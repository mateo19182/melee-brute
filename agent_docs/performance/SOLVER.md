# Solver speed: what makes it fast, what does not, what is next

How the forced search (`melee_brute`, `src/runtime/brute.c`) got from about 100 µs to about 17 µs a
searched position, what was measured and rejected along the way, and the work proposed next. The
user-facing description is [BRUTE.md](../../BRUTE.md); host setup and the gate are in
[ENVIRONMENT.md](../ENVIRONMENT.md) and [VALIDATION.md](../VALIDATION.md).

All timings are from a Ryzen 9 5950X with gcc 13.3 that carried other work, on one pinned core
unless stated. Compare position counts first and timings only as A/B pairs on the same core.

## Where a searched position goes

One position is one joint move: one to three frames, then a rollback.

| Part | Share | Now |
| --- | --- | --- |
| simulating the frame | about two thirds | 6.5 to 8 µs free-running, more right after a rollback |
| copying written pages back | about a quarter | 4.5 µs for 54 pages |
| hash, checkpoint, bookkeeping | the rest | 3.7 µs and 4.4 µs, once per expanded position |

The frame's profile is flat. The top entries are skeletal animation (`interpret_joint`,
`msl_sincosf_many`, matrix concatenation, `msl_fighter_pose_*`), then stage collision.

## What was done, by effect

| Change | Effect | Where |
| --- | --- | --- |
| Search loop in C instead of Python | 90 to 150 µs a position down to 25 to 30 µs under load | `src/runtime/brute.c` |
| Exact take-back by write protection | checkpoint and rollback 9 µs each, learned and only checked at the root, down to 4.5 µs, exact by construction, no learning phase | `brute.c`, BRUTE.md |
| Settled lines are not replayed | 2.07M to 1.60M positions on the 12-position suite | `settle` in `brute.c` |
| Pre-scan of answers only with more than 3 frames left | 1.60M to 0.93M positions | `BRUTE_SCAN_FRAMES` |
| Answers ordered by refutations per position spent | about 10% fewer positions than menu order | `record_score` |
| One table shared by forked processes, helpers on unfinished jobs | 16-process use from 68% to 95%; 14-frame solve 79 s to 65.5 s | `solve` in `melee_brute/search.py` |
| Processes split a search at every depth (busy lines, first answer by all, pointless searches given up); `solve` one job per root joint move | 13-frame solve on 16 processes, machine load about 70: "safe" 23.4 and 24.1 s to 10.3 and 10.5 s, 10.8 and 11.4 million positions to 4.6 and 4.3; "win" 16.0 s to 9.1 s, 6.9 million to 3.0 | `search`, `overtaken` in `src/runtime/brute.c`; `solve` |
| `Match.step` without the `EnvBatch.step` wrapper | 40 µs to 17 µs a frame from Python | `melee_brute/match.py` |
| Whole simulator optimized, link-time optimization | 19.9 µs to about 18 µs a position | `Makefile` release profile |
| Profile from the validation recordings (opt-in) | to about 16.5 µs | `make release-profile`, `RELEASE_PGO=use` |

Two findings behind those rows matter beyond the numbers.

**The first search was fast partly because it was unsound.** Its table key was the rounded look of
both fighters. States with the same look differ in input history: pad state, UCF buffers, held
buttons, the stick's previous position and the flick timers in each fighter's input block. The game
reads those to tell a press from a hold and a flick from a tilt. Keying on a hash of the whole state
searches 3 to 200 times more positions, most at point-blank range, and no move ordering recovers
that. The look-alike key remains as an opt-in (`exact=False`).

**Fighter and item code did not need `-O0`.** The release profile held `melee/ft` and `melee/it`
there because their outputs changed above it. The replay gate showed two causes, neither the
optimizer: the compiler replaced the game's own `sinf`, `cosf`, `sqrtf` with host built-ins (493 of
524 recordings off in the last bit), and `ftPe_SpecialLw.c::pickVeg` reads one entry past its table
(one crash). `RELEASE_MATH_FLAGS` and `RELEASE_SOURCE_FLAGS` handle them.

## Measured and rejected

Do not retry these without a new idea.

- **Finer rollback than pages.** The union of written 64-byte lines over random play is 135 KB
  against 340 KB for pages, and it keeps growing; it would have to be learned, so it is not exact.
- **Tracking the pages each step writes.** A fault costs about 2 µs and a frame writes about 26
  pages. `userfaultfd` write protection with `PAGEMAP_SCAN` still pays a kernel fault per page.
  Both cost more than the 2 µs of copying they would save.
- **Searching every answer on the spot, with no pre-scan.** Fewer positions on most problems and a
  40-fold blow-up at point-blank range, where an answer that hits at once waits behind a search.
- **Killer moves, history without cost.** Within 10% of menu order, or far worse.
- **Merging exact states that differ only in input history.** Tried as an experiment on 52 corpus
  cases by leaving bytes out of the state hash, which is unsound and was not kept. Exact searched
  322,504 positions and look-alike 80,372. Leaving out all input history (pad copies, UCF buffers,
  each fighter's stick, button and timer block, two per-player bytes) gave 104,057: input history
  is nearly the whole difference between the two tables. But it only pays when all of it goes.
  Leaving out the timers alone gave 262,689, the sticks, buttons and pad copies alone 269,435.
  The last inputs are live state (they decide press against hold next frame), so a sound version
  could at most canonicalize the timers: under 19% fewer positions, for a proof about every reader
  of 28 timer bytes.
- **Copying fewer pages on a rollback.** A Fox match writes 34 pages a frame on average and 67 over
  3,000 frames; a rollback copies the 54 or so a search has touched. 31 pages are written on nearly
  every frame, several of them for one to four bytes (frame counter, random seed, output
  positions). About 30 are written on fewer than one frame in ten. Copying only what a frame wrote
  would save at most 1.7 µs of an 18 µs position, and finding that out costs a fault per page.
  Moving the small hot fields onto one page would save about five pages, 0.4 µs.
- **`-march=native` on every file.** Passes the gate and is slower than tuning the listed owners.
- **`-O3` over `-O2` as the default level.** No measurable difference.

## How a build change is checked

```sh
git lfs pull --include="replays/validation/**" --exclude=""
make native validator MSL_REPLAY_ARITHMETIC=1
python -m tools.validation.validate_replay --suite replays/suites/melee_core_aggregate.json \
  --backend native --no-build --workers 12 --output-locks replays/suites/melee_core_output_locks.json
```

- 524 recordings, 4.84M frames, about 30 s on 12 workers. `MSL_CORE_NATIVE_BINARY=` points it at
  another runner; `make validation-release-supported-domain` builds and checks the release one.
- `make validation-coverage` reports which source lines those recordings execute.
- `make validation-differential` checks the release library against the `-O0` one on generated
  tapes, and the search's take-back on the same tapes.
- Solver changes: `pytest tests/test_brute.py`, then `python -m melee_brute corpus`. It solves 360
  recorded positions again with both tables, fails on any changed answer, and prints the positions
  searched against the recorded 2,499,012 (exact) and 553,128 (look-alike). About 20 s on 14
  processes. The two tables agree on all 360.

## What the gates cover

Measured 2026-11-17. `make validation-coverage` counts the lines the 524 recordings execute;
`make validation-tape-coverage` adds the 82 generated tapes of the differential gate
([VALIDATION.md](../VALIDATION.md)).

| Owner | Recordings | Tapes | Both |
| --- | --- | --- | --- |
| all source | 62.4% | 60.5% | 65.5% |
| `melee/ft/chara/ftKirby` | 52.9% | 53.9% | 67.9% |
| `melee/ft/chara/ftCommon` | 65.3% | 61.2% | 66.7% |
| `melee/it` | 72.4% | 68.5% | 74.2% |
| `melee/ft/chara/ftNess` | 79.1% | 56.7% | 82.4% |
| `melee/gr` | 45.8% | 39.2% | 45.8% |

- The release library matches the `-O0` reference on every frame of every tape (246,000 frames),
  and the search's take-back and state hash hold on 4,100 checks across the whole roster, teams,
  followers and items.
- About a third of the source is still run by neither. Most of that cannot run in a competitive
  match. Classifying every function of `melee/ft`, `melee/it`, `melee/gr` and `melee/mp` (99,000
  lines, 27,700 unrun) gives:

  | Class | Lines | Run |
  | --- | --- | --- |
  | cannot run under the rules: items, CPU types other than Nana's, 1P and demo, Stadium's transformations, display-only, code nothing in the build references | 10,211 | 1.6% |
  | competitive fighters' own code and shared gameplay code | 57,871 | 83.0% |
  | Nana's CPU AI, inside functions she runs | 5,397 | 65.3% |
  | only reachable by fighters outside competitive practice (Kirby, Ness, Link, Mewtwo, ...) | 24,907 | 78.9% |
  | never called, referenced only from tables or cycles; not attributed | 684 | 0% |

  **Of the code a competitive match can reach, the gates run 81.5%** (51,570 of 63,268 lines,
  Nana's AI included; 80.6% if the unattributed lines are reachable too). Per fighter, own code
  plus own projectiles: Donkey Kong 70.8%, Peach 72.5% (with her pulls), Yoshi 80.5%, Jigglypuff
  80.9%, Dr. Mario 84.7%, Marth 84.9%, Fox/Falco 87.7%, Pikachu 88.2%, Luigi 89.1%, Sheik 90.6%,
  Samus 90.9%, Ice Climbers 92.2%, Falcon/Ganondorf 93.0%.
- How: per-function lines from both coverage builds; rules by owner and file (fighter directory,
  item file, `ftCo_*` state); never-called shared functions take the class of whatever references
  them in the source (a running function makes them reachable, nothing makes them dead). Display
  code, Nana's CPU type (`xC = 6` in `ftCo_800A101C`) and Peach's pulls (`itpeachturnip.c`: Bob-omb,
  Mr. Saturn, Beam Sword) were read in the source; the rest of the CPU AI is assumed to belong to
  other CPU types. The scripts are in `reports/triage/coverage-classes/` (ignored).
- Seeded random controllers level off: 1,776 candidate matches reach 43,411 blocks and 82 of them
  reach 42,289. More seeds add little. A purpose-built Peach that pulls on every chance (17 pulls
  a tape, Mr. Saturn and Bob-omb in some) added 65 blocks and no line the recordings do not
  already run, so it was not kept.

## Proposed next

### 1. Closing the coverage gap

Competitive-reachable code is at 81.5%. What is left, most likely in a real match first, with what a
controller would have to do. Line counts are unrun lines of that class.

1. **Nana alone** (1,874 lines in her AI). Separate the climbers and keep them apart: Popo
   recovers or runs off while Nana is in hitstun or far away. Her AI also branches on the CPU
   level it is given; which of the 1,874 lines a versus match can reach is not verified.
2. **Collision corner cases** (1,388 in `mp/` and `ftcoll.c`): the grounded left/right wall and
   ceiling push of `mpColl_8004C534` never runs. Recover into the underside of Battlefield and
   Final Destination, up-special into ceilings and walls, wall jumps and wall techs.
3. **Item core** (1,211 in `it/*.c`) and held items (167): projectiles and turnips meeting walls,
   platforms and each other; catching, dropping and throwing in the air.
4. **Peach's rare pulls** (531): Bob-omb walking and exploding on its own, Mr. Saturn walking and
   picked up by the opponent, Beam Sword swings. Pulling alone is not enough (see above): drop the
   item and leave it, and have the other side pick it up.
5. **Donkey Kong** (319): carrying in cargo (walk, turn, jump, land, throw each way) and side
   special burying a grounded opponent.
6. **Yoshi** (253): egg lay on each weight, grounded and aerial, escaping from the egg, tongue
   at a ledge.
7. **Projectile interactions** (221, plus Samus 177 and Dr. Mario's pill 84): reflected, absorbed,
   clanked and shield-bounced shots. Fox or Falco shining when a laser, charge shot, pill or
   needle is near; shielding a charge shot.
8. **Ice Climbers' up special and freeze** (212): belay with Nana's rope throw, and the frozen
   state (`ftCo_DamageIce`) after it is entered, which the gates do reach but barely run.
9. **Specials at an edge**: Jigglypuff's rollout turned in the air (137), Sheik's chain (127),
   Peach's float, parasol and bomber end (111), Marth's dancing blade between ground and air
   (87), Luigi's green missile across a ledge (59). Start the special next to a ledge or
   platform edge and keep or reverse the stick.
10. **Knockdown bounces** (117): spiked onto the ground without a tech, sliding into a wall while
    down.
11. **Yoshi's Story Shy Guys** (111): hit one so its food drops, then pick it up.
12. **Rare common states**: throws that end in the air (31), shield break next to a ledge (22).

Stage code (1,044 unrun in `gr/`, mostly branches in `ground.c`) is not attributed further.
Kirby, Ness, Link, Mewtwo and the other non-competitive fighters (5,255 unrun) are left out on
purpose. The gates check none of the unrun lines on the release build.

### 2. Speed

The measurements above close the two ideas that looked largest: sound merging of exact states is
worth under 19%, and the rollback copy under 10%. With the frame at 6.5 to 8 µs after link-time and
profile-guided builds, a searched position is near its floor for this formulation. What is left:

1. **The frame in a search.** Profiled 2026-11-17 inside a search (Fox against Fox, Final
   Destination) with the simulator's subsystem profiler built into the Python library
   (`-DMSL_SUBSYSTEM_PROFILE`, and `msl_subsystem_profile_reset` / `_report` exported). A frame is
   about 40,000 cycles, roughly 60% of a position:

   | Part of the frame | Share |
   | --- | --- |
   | Tail physics solver (`lb_8001044C`, once a Fox) | 20% |
   | Fighter animation and pose | about 17% |
   | Stage collision | 16% |
   | Input and action logic | about 10% |
   | Stage objects | 7% |
   | Camera | about 7% |

   Skipping pose work that no gameplay reads has nothing left to take: upstream already leaves
   out presentation-only bones, the cloth, hair and cape solvers, and stage animation without a
   gameplay reader, each with a source audit. Fox's tail stays because it carries a hurtbox. What
   is left, in the order to try:
   - Train the compiler profile on solver positions as well as recordings, and make it the
     default if it holds. Mechanical; a guess of a few percent on the 17% it gives now.
   - Make the tail solver cheaper while keeping its bits. About 10% of a position at stake, and
     upstream has already tuned it by hand, so expect a few percent for careful work.
   - Not recommended: a search mode with the tail frozen. About 10% a position, but a hit that
     lands only on the tail would be missed, so answers stop being exact.
2. **Process scheduling.** Done 2026-11-17, in two parts.
   - One process a core. On a 13-frame solve (16 cores, 32 threads): 8 processes 28.3 s at
     19.5 µs a position, 16 processes 18.6 s at 23.8 µs, 30 processes 25.5 s at 42 µs. The second
     thread of a core nearly doubles the cost of a position.
   - Work shared inside the search. The old `solve` cut the work into fixed jobs, one per root
     joint move and next move of the first picker, and searched every one, though one next move
     that holds settles its root move: half the positions were of no use to the answer. Processes
     put on one question together did 2.6 times the positions of one process (2.35 million against
     0.91 on the 13-frame "safe" question), because each went on with searches another had already
     made pointless. With the busy lines, refuted moves in the table and giving up (the methods of
     parallel chess programs: ABDADA, young brothers wait, cutoff checks), 16 processes on that
     question do 1.15 million, and `solve` has one job per root joint move.
   - Tried and dropped on the way: leaving a move whose answers are all taken for the next move
     (4.3 million positions: the next move is a guess, the answers are not), and starting helpers
     on other moves (`spread`, 1.5 million with the rest in place).
   - Left: a position costs 22% more at 16 processes than at 8, which is shared cache and memory.
     On small questions (a few thousand positions) 16 processes still do 3 times the positions of
     one; those take milliseconds either way.
3. **The question, not the engine.** The remaining factors of ten are in what is asked: fewer or
   longer moves in the menus, zones of known result that end lines early, and the look-alike table
   for exploring before an exact run confirms. The two tables agree on all 360 corpus positions.
4. **A quiet baseline.** Every number here was taken beside other load. One run of
   `python -m melee_brute corpus` and of a 14-frame solve on an idle machine, recorded here, would
   make later changes comparable.
