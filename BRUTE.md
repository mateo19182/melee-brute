# melee-brute: brute-force search for Melee

A fork of [melee-sim-light](https://github.com/kyhavlov/melee-sim-light), the exact Melee gameplay simulator built from the decompilation, with one addition to its C core and a small Python package on top:

- **Take-back in 9 microseconds.** Checkpoint a match, play some frames, roll back. The simulator's own state copy takes about 300.
- **`melee_brute`**: a `Match` you drive with raw controller inputs, and three searches: every line for one player (`best`), random improvement of a long line (`improve`), and a two-player search for forced wins (`Forced`, `solve`).

Tools like this exist for other games: [TMInterface](https://donadigo.com/tminterface/what-is-bf)'s bruteforce for Trackmania, the [SM64 bruteforcers](https://github.com/FramePerfection/sm64_bruteforcers) built on that game's decompilation, [Kinoko](https://github.com/vabold/Kinoko) for Mario Kart Wii. We found none for Melee, where TASes are made by hand with Dolphin's Lua tools.

## Install

You need Python 3.11+, GCC, GNU Make and binutils, and your own NTSC 1.02 ISO.

```bash
uv add "melee-brute @ git+https://github.com/mateo19182/melee-brute.git"
```

Extract the game data once, as in the simulator's [README](README.md#quick-start), and point `MSL_DATA_DIR` at it (or run from the folder that holds `data/`). The package installs `melee_sim` (the simulator, unchanged apart from four new C calls) and `melee_brute`. Don't install it next to upstream's `melee-sim-light`: both provide `melee_sim`.

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
- `m.save()` and `m.load(bytes)` are full savestates (about 1 MB, 2.5 ms): use them to send a position to another process, not inside a search.

## The searches

| Call | What it does |
| --- | --- |
| `best(match, choices, depth, score)` | Tries every sequence of `depth` choices and returns the highest `score(match)` with its line. |
| `improve(match, line, choices, score, tries)` | For lines too long to enumerate: changes one choice at random, replays from the nearest checkpoint, keeps the change if the score improved. |
| `Forced(match, rules).win(frames)` | Can P1 land the first hit whatever P2 does, with P2 seeing each P1 move before answering? Yes means a forced win. |
| `Forced(match, rules).safe(frames)` | Can P2 avoid the first hit whatever P1 does, with P1 seeing each P2 move first? Yes means a guaranteed defence. If both are no, the position is decided by guessing. |
| `solve(root, "module:RULES", frames, mode)` | `Forced` from a savestate on every core. |

`Rules` say which moves a fighter may pick in each state. `melee_brute/fox.py` is a worked example for Fox against Fox, and the command line runs it:

```bash
python -m melee_brute bench                                    # time a frame, a checkpoint, a rollback
python -m melee_brute fox --gap 9 --frames 10                  # attacker vs a Fox cornered at the ledge
python -m melee_brute fox --gap 11.5 --hop 9 --frames 16 --safe --trap
```

The last one starts with the attacker nine frames into a short hop above a cornered Fox and finds that the defender's only move that holds without guessing is shield: his shine, safe when both stand, is not safe against a Fox in the air.

## How fast

On a Ryzen 9 5950X (16 cores), measured on an otherwise quiet machine with the same code inside [melee-llm](https://github.com/mateo19182/melee-llm):

| Operation | Time |
| --- | --- |
| one frame, one match | about 47 µs |
| checkpoint / rollback | 8.3 / 8.7 µs |
| `EnvBatch.copy_matches_from`, one match | about 300 µs |
| savestate save or restore | about 2,500 µs |

- The two-player search runs at 67,000 to 113,000 positions per second on 28 processes. With lane copies it could not pass 10,500: every copy moves the whole state and saturates memory bandwidth.
- `python -m melee_brute bench` prints these numbers for your machine.

## How take-back works, and why it is exact

- A frame changes about 3 KB of a match's 3.2 MB of memory, in a few dozen 4 KiB pages, always the same ones.
- `Match.learn()` plays 6,000 frames of random inputs to find those pages. A checkpoint copies them out; a rollback copies them back to the same address, so no pointers need fixing.
- Random play can miss a rarely written page, so this is checked. The root of a search (checkpoint level 0, the outermost `branch()`) keeps a full copy of the state. Every rollback to the root compares all other pages with it, puts back any that changed, adds them to the set and counts the miss in `match.missed`.
- So the root is always restored exactly. `match.exact(fn)` repeats `fn` if a miss happened inside it, because deeper rollbacks may then have been inexact; `best`, `improve` and `solve` do this themselves.
- `tests/test_brute.py` checks that a rollback followed by the same inputs gives a byte-identical savestate.

## What changed in the simulator

- `src/runtime/batch.c`, `batch.h`, `src/api.c`, `api.h`: four new calls, `msl_batch_checkpoint_size`, `msl_batch_checkpoint`, `msl_batch_rollback` and `msl_batch_diff_pages`. Nothing existing was modified.
- `melee_sim/env_batch.py`, `_native.py`, `API.md`, `tests/test_melee_sim_api.py`: `EnvBatch.checkpoint`, `rollback` and `changed_pages` over those calls, their docs and a test. This and the C change are the part meant for upstream; the branch `upstream-checkpoint` holds it alone on top of upstream's `main`.
- `setup.py` and `pyproject.toml`: the package list and the project name.
- Everything else of ours is in new files: `melee_brute/`, `tests/test_brute.py`, this page.

To take in upstream's changes:

```bash
git remote add upstream https://github.com/kyhavlov/melee-sim-light.git
git fetch upstream && git merge upstream/main
```

## Limits

- Versus mode only, with the simulator's rules: UCF, no items, frozen Pokémon Stadium, no CPU opponents.
- The game's random numbers run as in the simulator; a line found here replays on Dolphin only while no random event differs.
- `Forced` proves things about the moves you give it and the frames you allow, no more. `fox.py`'s menus are a starting point, not the whole game.
- The simulator has no license file. Its author agreed to this fork being published on GitHub.
