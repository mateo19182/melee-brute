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
- Solver changes: `pytest tests/test_brute.py`, then the position counts and answers of a fixed set
  of positions before and after.

## What the gate does not cover

Measured 2026-11-17 with `make validation-coverage`: the recordings execute 62% of source lines and
67% of functions; 69% of lines in `melee/ft`, 72% in `melee/it`. About 26,000 fighter and item lines
and 2,300 functions are never run. Until the release profile changed, that code was at `-O0`; now it
is optimized on the strength of flags, not recordings.

| Owner | Lines run | Note |
| --- | --- | --- |
| `ftKirby` | 53% | copy abilities: the largest reachable gap |
| `ftCommon` | 65% | includes item and single-player states the rules never reach |
| `ftDonkey`, `ftSeak`, `ftZelda` | 70 to 74% | |
| `ftNess` to `ftPikachu` | 79 to 86% | |
| `ftFox`, `ftMario`, `ftCaptain`, `ftLink` | 90 to 95% | |
| `it/items/itbombhei.c`, `itdosei.c` | 43%, 47% | Peach's rare pulls |
| `it/items/itnessbat.c` | 42% | |
| `gr/grpstadium.c` | 23% | transformations are out of scope |

Part of the uncovered code cannot run under the supported rules (items off, no CPU players, no
single-player modes): hammer, mushroom, barrel, warp star, `ftcpuattack.c`. That part needs listing
once so the reachable gap is known.

## Proposed next

### 1. A complete check before more risk

The gate compares against Slippi recordings, so its coverage is whatever people happened to record.
For build changes that is more than is needed: the `-O0` build is itself the reference.

1. **Differential gate.** Play the same input tape through the `-O0` runner and the release runner
   and compare every frame's full output. Any tape works: no recording, no Dolphin. Add it as a
   validation backend pair so the existing comparison and reporting are reused.
2. **Generated tapes, kept by coverage.** Scripted and random inputs for all 26 fighters on the six
   stages, in singles and teams; keep a tape when `validation-coverage` shows it reaches new lines.
   Aim at the reachable gaps above first: Kirby's copies against every fighter, Peach's pulls, Ness,
   grabs and throws between every weight class, shield breaks, ledge and wall interactions.
3. **A list of unreachable owners**, so the report can state coverage of reachable code and the
   target can be 95% of it.
4. **Solver checks across the roster.** `tests/test_brute.py` proves take-back and the hash on Fox
   against Fox only. Run the rollback-and-replay savestate comparison on generated tapes for every
   fighter, with items in play and with the Ice Climbers.
5. **A corpus of solved positions.** Answers and position counts for a few hundred positions, exact
   and look-alike, as the regression set for every search change below.

Order matters: 1 and 2 retire the open risk of the release profile, 4 and 5 are what any change to
state merging or take-back has to pass.

### 2. Speed, in order of expected return

1. **Fewer positions in exact mode.** The only place with factors of ten left. States that differ
   only in input history could share an answer when that history can no longer matter: the timers
   are past every threshold the game compares them with, and the held inputs are equal. That needs
   each field's readers found in the source, and item 5 above to catch a wrong merge.
2. **Rollback copy, a quarter of a position.** Find which fields put each of the 54 pages in the
   written set and whether the hot ones can share pages. The match struct alone spans sixteen
   pages (pad state, camera, output positions); how many of them are written is not yet measured.
3. **The frame.** Train the profile on solver positions as well as recordings; then look at whether
   pose work for bones no hitbox, hurtbox or collision point depends on can be skipped in a search.
   The second needs the differential gate first.
4. **Process scheduling.** Helpers recover idle time but repeat some work. Splitting a large job
   into its children, which the shared table already makes cheap, would scale better past 16
   processes.
5. **A quiet baseline.** Every number here was taken beside other load. One run of the suite and of
   a 14-frame solve on an idle machine, recorded here, would make later changes comparable.
