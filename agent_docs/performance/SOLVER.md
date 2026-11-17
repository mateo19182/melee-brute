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
- About a third of the source is still run by neither. Part of it cannot run under the supported
  rules (items off, no CPU players, no single-player modes): hammer, mushroom, barrel, warp star,
  `ftcpuattack.c`, Stadium's transformations. That part has not been listed, so the reachable gap
  is not known yet.
- Seeded random controllers level off: 1,776 candidate matches reach 43,411 blocks and 82 of them
  reach 42,289. More seeds add little. What is left needs controllers that set things up on
  purpose.

## Proposed next

### 1. Closing the coverage gap

The differential gate, the roster-wide take-back check and the corpus of solved positions exist.
What is left of this step:

1. **A list of unreachable owners**, so the report states coverage of reachable code and the target
   can be 95% of it.
2. **Controllers with a purpose.** Per fighter: every special in every state it has (charged,
   held, cancelled, in the air, into a wall, out of a grab), Kirby's swallow of each fighter and
   each copied special, Peach's pulls until the rare ones come, every throw on every weight,
   shield breaks, ledge and wall options, deaths off each blast zone. Keep them by coverage as now.

### 2. Speed

The measurements above close the two ideas that looked largest: sound merging of exact states is
worth under 19%, and the rollback copy under 10%. With the frame at 6.5 to 8 µs after link-time and
profile-guided builds, a searched position is near its floor for this formulation. What is left:

1. **The frame in a search.** Train the profile on solver positions as well as recordings. Then
   see whether pose work for bones no hitbox, hurtbox or collision point depends on can be skipped;
   the differential gate can now check that.
2. **Process scheduling.** Helpers recover idle time but repeat some work. Splitting a large job
   into its children, which the shared table already makes cheap, would scale better past 16
   processes. On a busy machine a position cost 29.5 µs on one process and 57 µs on 28.
3. **The question, not the engine.** The remaining factors of ten are in what is asked: fewer or
   longer moves in the menus, zones of known result that end lines early, and the look-alike table
   for exploring before an exact run confirms. The two tables agree on all 360 corpus positions.
4. **A quiet baseline.** Every number here was taken beside other load. One run of
   `python -m melee_brute corpus` and of a 14-frame solve on an idle machine, recorded here, would
   make later changes comparable.
