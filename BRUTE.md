# melee-brute: brute-force search for Melee

A fork of [melee-sim-light](https://github.com/kyhavlov/melee-sim-light), the exact Melee gameplay simulator built from the decompilation, with a search core added to its C code and a small Python package on top:

- **Exact take-back in about 5 microseconds.** Checkpoint a match, play some frames, roll back. The simulator's own state copy takes about 300.
- **A forced-win search inside the simulator**, about 25 microseconds a position on one core, with one table of decided positions shared by every process.
- **`melee_brute`**: a `Match` you drive with raw controller inputs, and three searches: every line for one player (`best`), random improvement of a long line (`improve`), and the two-player search for forced wins (`Forced`, `solve`).

Tools like this exist for other games: [TMInterface](https://donadigo.com/tminterface/what-is-bf)'s bruteforce for Trackmania, the [SM64 bruteforcers](https://github.com/FramePerfection/sm64_bruteforcers) built on that game's decompilation, [Kinoko](https://github.com/vabold/Kinoko) for Mario Kart Wii. We found none for Melee, where TASes are made by hand with Dolphin's Lua tools.

## Install

You need Python 3.11+, GCC, GNU Make and binutils, and your own NTSC 1.02 ISO.

```bash
uv add "melee-brute @ git+https://github.com/mateo19182/melee-brute.git"
```

Extract the game data once, as in the simulator's [README](README.md#quick-start), and point `MSL_DATA_DIR` at it (or run from the folder that holds `data/`). The package installs `melee_sim` (the simulator with the search calls added) and `melee_brute`. The search calls need Linux. Don't install it next to upstream's `melee-sim-light`: both provide `melee_sim`.

## Quick start

```python
from melee_brute import Match, pad, best, B, X

m = Match("FOX", "MARTH", "BATTLEFIELD")
m.start()                                 # play out the countdown

with m.branch():                          # everything inside is undone on exit
    m.step(pad(B, y=-80))                 # P1: down-B for one frame
    m.step(frames=10)
    print(m.players()[1]["percent"])

# The 4-frame P1 line that ends furthest right, out of all 3^4:
choices = [pad(), pad(x=80), pad(X)]
score, line = best(m, choices, 4, lambda m: float(m.players()[0]["pos_x"]))
```

- An input is `pad(buttons, x, y, cx, cy, l, r)`: button bits OR-ed together (`A B X Y Z R L`), sticks from -80 to 80, triggers from 0 to 255.
- `m.players()` returns the two fighters' records: `pos_x`, `pos_y`, `percent`, `stocks`, `action_id`, `action_frame`, `hitlag`, `shield_hp` and the rest of [API.md](API.md).
- `m.save()` and `m.load(bytes)` are full savestates (about 1 MB, 2.5 ms): use them to keep a position, not inside a search.
- `m.hash()` is 128 bits that are equal for equal states.

## The searches

| Call | What it does |
| --- | --- |
| `best(match, choices, depth, score)` | Tries every sequence of `depth` choices and returns the highest `score(match)` with its line. |
| `improve(match, line, choices, score, tries)` | For lines too long to enumerate: changes one choice at random, replays from the nearest checkpoint, keeps the change if the score improved. |
| `Forced(match, rules).win(frames)` | Can P1 land the first hit whatever P2 does, with P2 seeing each P1 move before answering? Yes means a forced win. |
| `Forced(match, rules).safe(frames)` | Can P2 avoid the first hit whatever P1 does, with P1 seeing each P2 move first? Yes means a guaranteed defence. If both are no, the position is decided by guessing. |
| `solve(root, rules, frames, mode)` | `Forced` from a savestate on every core. |

`Rules` are data: named moves (one input per frame), menus of moves, and which action ids get which menu. An optional `Trap` names a position the attacker is known to win; `Zone`s do the same for either side (below). `melee_brute/fox.py` is a worked example for Fox against Fox, and the command line runs it:

```bash
python -m melee_brute bench                                    # time a frame, a checkpoint, a rollback
python -m melee_brute fox --gap 9 --frames 10                  # attacker vs a Fox cornered at the ledge
python -m melee_brute fox --gap 11.5 --hop 9 --frames 16 --safe --trap
```

The last one starts with the attacker nine frames into a short hop above a cornered Fox and asks which defences hold without guessing. At 6, 11 and 14 frames the only one is shield: his shine, safe when both stand, is not safe against a Fox in the air. The 16-frame run published earlier used the old search and menus and has not been repeated.

### Rules for chaining searches

- **A decision every frame.** `fox.frame_rules()` gives both Foxes one input a frame, as two players or bots play it, with the jumpsquat cancels, the spotdodge from standing and the cut-short shield drop that `fox.RULES` lacks. `defender={"ground": (...)}` gives P2 menus of his own.
- **`caught`**: action ids that count as having been hit. A grab moves no percent, so without it a grab is never a first hit. `frame_rules` sets it.
- **`Zone`**: a set of positions whose result is already known (actions of both and how far into them, the gap between them, where P2 stands). A line that reaches one stops there. What one search proved about a set of positions becomes a zone of the next, which is how a search of 10 frames can say something about a longer game.
- **`timeout="attacker"`**: a line whose frames run out goes to the attacker. The defender then has to reach one of his zones in time, not just last.

On five positions solved earlier as the simultaneous game, the one-frame rules give the same answers (`tests/test_brute.py`).

### Exact or look-alike

The search remembers the positions it has decided. By default a remembered answer is used only for the very same state, byte for byte (`Match.hash`). `Forced(..., exact=False)`, `solve(..., exact=False)` or `--look` on the command line instead share an answer between states that look the same from outside: place and speeds to 0.01, action and its frame, hitlag, shield, facing, jumps left. That is the table the first version of this package used.

- Look-alike searches 3 to 200 times fewer positions, most of all at point-blank range.
- It can be wrong. States that look the same differ in what each fighter was holding: the buttons still down, the stick's last position and how long it has been there. The game reads those to tell a press from a hold and a flick from a tilt. Every difference we found between merged states was of this kind.
- On the 12 positions we compare on, both give the same answers. Use look-alike to explore and exact to confirm.

## How fast

On a Ryzen 9 5950X (16 cores) that was busy with other work (load 15 to 35), so every number is on the slow side. "Before" is the Python search this replaces, on the same optimised simulator build.

| | Before | Now, look-alike | Now, exact |
| --- | --- | --- | --- |
| 12 positions of 8 to 10 frames, one core | 39.8 s | 3.8 s | 27.2 s |
| positions searched for them | 328,000 | 130,000 | 933,000 |
| one position | 90 to 150 µs | 25 to 30 µs | 25 to 30 µs |
| 11 frames, "is the defender safe", 16 processes | 16.2 s | 5.1 s | 9.7 s |
| 11 frames, "can the attacker force it", 16 processes | 20.0 s | 4.6 s | 7.1 s |
| 14 frames, "is the defender safe", 16 processes | 77.3 s | 12.5 s | 67.5 s |
| positions searched for it | 6.2 million | 2.8 million | 26 million |

- Before and look-alike use the same table key, so that column is the like-for-like speed-up: about 10 times on one core and 3 to 6 times on 16 processes, where a second and a half of each run is start-up.
- Exact is a stricter question with 3 to 200 times the positions. It is faster than before on most of these and slower at point-blank range, where one position took 0.06 s before and takes 1.8 s exactly.
- Every answer in the table above is the same in all three columns.

| Operation | Time |
| --- | --- |
| one frame inside the search | 12 to 15 µs |
| checkpoint / rollback | 4.4 / 4.5 µs |
| state hash | 3.7 µs |
| one frame from Python, with its observation | 17 µs |
| savestate save and restore | about 4,500 µs |

- `python -m melee_brute bench` prints these for your machine.
- Where a searched position goes: about two thirds simulating the frame, a quarter copying pages back, the rest hashing and bookkeeping.
- Long searches are bounded: the table is `table_mb` in all (default 2 GB) and replaces its cheapest entries when full, the process count is cut to the free memory, and with `out=` (`--out`) finished jobs are saved so a rerun continues where it stopped.

## How take-back works, and why it is exact

- A frame changes about 3 KB of a match's 3.2 MB of memory, on 25 or so 4 KiB pages; over a search, 50 to 60 pages are ever touched.
- A `Match` write-protects its memory. The first write to a page faults; the handler keeps the page's old content, lifts the protection for that page and lets the write through. This happens once for each page.
- A checkpoint copies the pages written so far. A rollback copies them back, and gives a page written after the checkpoint the content the handler kept, which is what it held when the checkpoint was taken.
- A page that never faulted cannot have changed. So nothing is learned or guessed, and there is nothing to repair: a rollback is exact by construction.
- The state hash is built the same way: each written page adds the difference between its hash now and its hash before its first write, so the result does not depend on which pages happen to have been written. Processes forked from one match share its addresses, so their hashes agree and they can share one table.
- `tests/test_brute.py` checks that a rollback followed by the same inputs gives a byte-identical savestate, including for pages first written after the checkpoint, and that a forked process computes the same hash.

## How the forced search works

- At a position, the side asked about tries its moves; a move holds if every answer of the other side leads to a position that is still good.
- A line that is settled when its joint move ends (someone was hit, the frames ran out, the trap closed) is never played twice.
- With more than three frames left, all answers to a move are played once before any is searched, so that an answer that hits at once is found for a few positions. Nearer the end, each answer is played and searched on the spot.
- Answers are tried in falling order of how often they have refuted per position they cost, counted separately against each move.
- `solve` forks its processes from one that holds the root. They share the game data and the table, so a position decided by one is known to all. A process with no job left helps the unfinished job with the fewest helpers: it searches the same position starting from other moves, and the two meet through the table.

## What changed in the simulator

- `src/runtime/brute.c`, `brute.h` (new): take-back, the state hash, the table and the forced search. `src/api.c`, `api.h`: the `msl_brute_*` calls over them.
- `src/runtime/batch.c`, `batch.h`: `msl_core_batch_match`. `src/runtime/observation.c`, `observation.h`: the few fields the search reads after a frame. `Makefile`: `brute.o` joins the files built at `-O3`.
- `msl_batch_checkpoint`, `msl_batch_rollback`, `msl_batch_diff_pages` and `EnvBatch.checkpoint`, `rollback`, `changed_pages` from the first version are still there and unchanged; `melee_brute` no longer uses them. The branch `upstream-checkpoint` holds them alone on top of upstream's `main`.
- `setup.py` and `pyproject.toml`: the package list and the project name.
- Everything else of ours is in `melee_brute/`, `tests/test_brute.py` and this page.

To take in upstream's changes:

```bash
git remote add upstream https://github.com/kyhavlov/melee-sim-light.git
git fetch upstream && git merge upstream/main
```

## Limits

- Versus mode only, with the simulator's rules: UCF, no items, frozen Pokémon Stadium, no CPU opponents.
- The game's random numbers run as in the simulator; a line found here replays on Dolphin only while no random event differs.
- `Forced` proves things about the moves you give it and the frames you allow, no more. `fox.py`'s menus are a starting point, not the whole game.
- Take-back and the search need Linux: they use page protection and `fork`.
- The simulator has no license file. Its author agreed to this fork being published on GitHub.
