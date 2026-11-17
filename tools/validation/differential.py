"""The differential gate: one build of the simulator against another, on generated input tapes.

    python -m tools.validation.differential check --reference build/melee_core/python/libmelee_core.so \\
        --candidate build/melee_core/python-release/libmelee_core.so
    python -m tools.validation.differential select --library build/melee_core/python-coverage/libmelee_core.so
    python -m tools.validation.differential play --library build/melee_core/python-coverage/libmelee_core.so

`check` plays every recipe of the corpus on the reference build, replays the resulting tape on each
candidate and compares the observation of every frame. `select` builds the corpus: it plays many
candidate recipes on a build with block coverage (`make python-coverage`) and keeps, greedily, the ones
that reach blocks the kept ones do not.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import multiprocessing as mp
import os
import random
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "replays/suites/generated_tapes.json"
FIGHTERS = ("MARIO", "FOX", "FALCON", "DONKEY_KONG", "KIRBY", "BOWSER", "LINK", "SHEIK", "NESS", "PEACH",
            "ICE_CLIMBERS", "PIKACHU", "SAMUS", "YOSHI", "JIGGLYPUFF", "MEWTWO", "LUIGI", "MARTH", "ZELDA",
            "YOUNG_LINK", "DRMARIO", "FALCO", "PICHU", "GAMEWATCH", "GANONDORF", "ROY")
STAGES = ("FINAL_DESTINATION", "BATTLEFIELD", "YOSHIS_STORY", "FOUNTAIN_OF_DREAMS", "DREAM_LAND_N64",
          "POKEMON_STADIUM")


def _use(library: str, data_dir: str | None) -> None:
    """Runs in each worker before the simulator is imported: the library is chosen once per process."""
    os.environ["MSL_CORE_LIBRARY"] = str(Path(library).resolve())
    if data_dir:
        os.environ["MSL_DATA_DIR"] = data_dir
    sys.path.insert(0, str(ROOT))


def _pool(library: str, data_dir: str | None, workers: int):
    return mp.get_context("spawn").Pool(workers, initializer=_use, initargs=(library, data_dir))


def _record(row: dict):
    from tools.validation import tapes
    recipe = tapes.Recipe.from_json(row)
    tape, digests = tapes.record(recipe)
    return row, tape, digests


def _replay(job):
    from tools.validation import tapes
    row, tape, keep = job
    digests, kept = tapes.replay(tapes.Recipe.from_json(row), tape, keep=keep)
    return row, digests, kept


def _blocks(row: dict):
    """Record one recipe on the coverage build: frames played and the blocks it ran."""
    from melee_sim import _native
    from tools.validation import tapes
    lib = _native.library()
    size = ctypes.c_size_t()
    lib.msl_trace_map.restype = ctypes.POINTER(ctypes.c_uint8)
    start = lib.msl_trace_map(ctypes.byref(size))
    lib.msl_trace_reset()
    tape, _ = tapes.record(tapes.Recipe.from_json(row))
    ran = np.flatnonzero(np.ctypeslib.as_array(start, (size.value,))).astype(np.uint32)
    return row, len(tape), ran


def differing_fields(a: bytes, b: bytes) -> list[str]:
    """Names of the observation fields that differ, for the report of a mismatch."""
    from melee_sim import dtypes
    left, right = (np.frombuffer(x, dtype=dtypes.gamestate_dtype())[0] for x in (a, b))
    names: list[str] = []

    def walk(x, y, path: str) -> None:
        if x.dtype.names and getattr(x, "ndim", 0):
            for i in range(len(x)):
                walk(x[i], y[i], f"{path}[{i}]")
        elif x.dtype.names:
            for name in x.dtype.names:
                if not name.startswith("_"):
                    walk(x[name], y[name], f"{path}.{name}" if path else name)
        elif not np.array_equal(x, y):
            names.append(f"{path}: {np.asarray(x).tolist()} != {np.asarray(y).tolist()}")
    walk(left, right, "")
    return names


def check(args) -> int:
    from tools.validation import tapes
    corpus = json.loads(Path(args.corpus).read_text())["tapes"]
    if args.limit:
        corpus = corpus[:args.limit]
    started = time.time()
    with _pool(args.reference, args.data_dir, args.workers) as pool:
        recorded = pool.map(_record, corpus, chunksize=1)
    frames = sum(len(tape) for _, tape, _ in recorded)
    print(f"reference {args.reference}: {len(recorded)} tapes, {frames:,} frames in {time.time() - started:.1f} s")
    failed = 0
    for candidate in args.candidate:
        started = time.time()
        with _pool(candidate, args.data_dir, args.workers) as pool:
            replayed = pool.map(_replay, [(row, tape, None) for row, tape, _ in recorded], chunksize=1)
            bad = []
            for (row, tape, expected), (_, got, _) in zip(recorded, replayed):
                if len(expected) != len(got) or not np.array_equal(expected, got):
                    same = np.flatnonzero(expected[:len(got)] != got[:len(expected)])
                    bad.append((row, tape, int(same[0]) if len(same) else min(len(expected), len(got))))
            # Play the first few again on both builds, up to the frame that differs, and name the fields.
            shown = bad[:args.show]
            if shown:
                with _pool(args.reference, args.data_dir, min(args.workers, len(shown))) as reference:
                    wanted = reference.map(_replay, [(row, tape[:frame + 1], frame) for row, tape, frame in shown])
                had = pool.map(_replay, [(row, tape[:frame + 1], frame) for row, tape, frame in shown])
                for (row, tape, frame), (_, _, want), (_, _, have) in zip(shown, wanted, had):
                    print(f"FAIL {tapes.Recipe.from_json(row).name}: first differs at frame {frame} of {len(tape)}")
                    fields = differing_fields(want, have) if want and have else ["the match ended on one build only"]
                    for line in fields[:6]:
                        print(f"     {line}")
            if len(bad) > len(shown):
                print(f"     and {len(bad) - len(shown)} more tapes")
        failed += len(bad)
        print(f"candidate {candidate}: pass={len(recorded) - len(bad)} fail={len(bad)} "
              f"frames={frames:,} in {time.time() - started:.1f} s")
    return 1 if failed else 0


def _takeback(job):
    from tools.validation import tapes
    row, tape = job
    checks, failures = tapes.takeback(tapes.Recipe.from_json(row), tape)
    return row, checks, failures


def takeback(args) -> int:
    """The search's take-back and state hash, on every tape: see tapes.takeback."""
    from tools.validation import tapes
    corpus = json.loads(Path(args.corpus).read_text())["tapes"]
    started = time.time()
    with _pool(args.library, args.data_dir, args.workers) as pool:
        recorded = pool.map(_record, corpus, chunksize=1)
        results = pool.map(_takeback, [(row, tape) for row, tape, _ in recorded], chunksize=1)
    bad = [(row, failures) for row, _, failures in results if failures]
    for row, failures in bad:
        print(f"FAIL {tapes.Recipe.from_json(row).name}: {failures[0]}" + (f" (+{len(failures) - 1})" if failures[1:] else ""))
    print(f"take-back on {args.library}: tapes pass={len(results) - len(bad)} fail={len(bad)} "
          f"checks={sum(n for _, n, _ in results):,} in {time.time() - started:.1f} s")
    return 1 if bad else 0


def _count(row: dict):
    from melee_sim import _native
    out = _record(row)
    _native.library().msl_trace_flush()
    return out


def play(args) -> int:
    """Play the corpus on one build and compare nothing: for a coverage build, to count what the tapes run."""
    corpus = json.loads(Path(args.corpus).read_text())["tapes"]
    with _pool(args.library, args.data_dir, args.workers) as pool:
        recorded = pool.map(_count if args.counts else _record, corpus, chunksize=1)
    print(f"{len(recorded)} tapes, {sum(len(tape) for _, tape, _ in recorded):,} frames")
    return 0


def candidates(seeds: int) -> list[dict]:
    """The recipes `select` chooses from: every fighter against several others and against Kirby from both
    sides, on every stage, fresh and already damaged, and some team matches."""
    from tools.validation.tapes import Recipe
    r = random.Random(20261117)
    pick = lambda xs: xs[int(r.random() * len(xs))]
    out: list[Recipe] = []
    for seed in range(seeds):
        n = 0
        for fighter in FIGHTERS:
            others = [pick(FIGHTERS) for _ in range(4)] + ["KIRBY"]
            for other in others:
                pair = (fighter, other) if n % 2 == 0 else (other, fighter)
                hurt = () if n % 3 == 0 else (int(r.random() * 101), int(r.random() * 101))
                out.append(Recipe(STAGES[n % len(STAGES)], pair, 1 + seed * 10_000 + n, percents=hurt))
                n += 1
        for i in range(12):
            four = tuple(pick(FIGHTERS) for _ in range(4))
            out.append(Recipe(STAGES[i % len(STAGES)], four, 5_000 + seed * 10_000 + i, teams=(0, 0, 1, 1)))
        for i in range(6):
            three = tuple(pick(FIGHTERS) for _ in range(3))
            out.append(Recipe(STAGES[i % len(STAGES)], three, 6_000 + seed * 10_000 + i, teams=(0, 1, 1)))
    return [x.to_json() for x in out]


def select(args) -> int:
    rows = candidates(args.seeds)
    started = time.time()
    with _pool(args.library, args.data_dir, args.workers) as pool:
        played = pool.map(_blocks, rows, chunksize=1)
    print(f"{len(played)} candidates, {sum(n for _, n, _ in played):,} frames in {time.time() - started:.1f} s")
    size = 1 + max(int(ran[-1]) for _, _, ran in played)
    seen = np.zeros(size, dtype=bool)
    left = list(played)
    kept: list[dict] = []
    while left:
        gains = [int(np.count_nonzero(~seen[ran])) for _, _, ran in left]
        best = int(np.argmax(gains))
        if gains[best] < args.min_blocks:
            break
        row, _, ran = left.pop(best)
        seen[ran] = True
        kept.append(row)
    everything = np.zeros(size, dtype=bool)
    for _, _, ran in played:
        everything[ran] = True
    print(f"kept {len(kept)} tapes reaching {int(seen.sum()):,} of the {int(everything.sum()):,} blocks all "
          f"candidates reach")
    Path(args.corpus).write_text(json.dumps({
        "name": "generated_tapes",
        "notes": "Recipes for the differential gate, chosen by tools/validation/differential.py select: each "
                 "reaches at least min_blocks basic blocks the ones before it do not.",
        "min_blocks": args.min_blocks, "tapes": kept}, indent=1) + "\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", help="extracted game data (default: $MSL_DATA_DIR or ./data)")
    ap.add_argument("--corpus", default=str(CORPUS))
    ap.add_argument("--workers", type=int, default=min(16, os.cpu_count() or 1))
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="compare candidate builds with the reference build")
    c.add_argument("--reference", required=True)
    c.add_argument("--candidate", required=True, action="append")
    c.add_argument("--limit", type=int, default=0, help="only the first N tapes")
    c.add_argument("--show", type=int, default=5, help="mismatching tapes to describe field by field")
    c.set_defaults(fn=check)
    s = sub.add_parser("select", help="choose the corpus by block coverage")
    s.add_argument("--library", required=True, help="a build from `make python-coverage`")
    s.add_argument("--seeds", type=int, default=3, help="candidate recipes per pairing")
    s.add_argument("--min-blocks", type=int, default=20)
    s.set_defaults(fn=select)
    t = sub.add_parser("takeback", help="check the search's take-back and state hash on every tape")
    t.add_argument("--library", required=True)
    t.set_defaults(fn=takeback)
    p = sub.add_parser("play", help="play the corpus on one build, comparing nothing")
    p.add_argument("--library", required=True)
    p.add_argument("--counts", action="store_true", help="write the line counts of a `make python-coverage` build")
    p.set_defaults(fn=play)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
