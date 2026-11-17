"""A fixed set of solved positions: the regression set and the benchmark for changes to the search.

Each case is a Fox against a cornered Fox (`fox.corner`) with a question, a number of frames and a set of
rules. `tests/fixtures/solved_positions.json` holds the answer of each, found with the exact table, and
the positions the exact and the look-alike table needed for it at the time. Answers must not change; the
position counts and the time are what a change to the search is judged by.

    python -m melee_brute corpus            # solve them all again and compare
    python -m melee_brute corpus --write    # record the answers and counts
"""
from __future__ import annotations

import json
import multiprocessing as mp
import time
from pathlib import Path

SOLVED = Path(__file__).resolve().parents[1] / "tests/fixtures/solved_positions.json"


def cases() -> list[dict]:
    out = []
    for gap in (6, 9, 11.5, 14, 20, 30):
        for hop in (0, 3, 6, 9, 12):
            for ledge in (12, 40):
                for mode in ("win", "safe"):
                    for frames in (4, 6, 8):
                        trap = ledge == 12 and hop in (0, 6) and frames == 6  # a sample with the shield trap
                        out.append({"gap": gap, "hop": hop, "ledge": ledge, "mode": mode, "frames": frames,
                                    "rules": "RULES_TRAP" if trap else "RULES"})
    return out


def name(case: dict) -> str:
    return (f"gap {case['gap']} hop {case['hop']} ledge {case['ledge']} {case['mode']} {case['frames']} "
            f"{case['rules']}")


_HELD = []  # one idle match: the game data stays loaded between cases


def solve_case(job) -> dict:
    """The answer with each table, the positions each needed, and the seconds the exact one took."""
    from melee_brute import Forced, Match, fox
    case, data_dir = job
    if not _HELD:
        _HELD.append(Match(data_dir=data_dir))
    out = dict(case)
    for key, exact in (("exact", True), ("look", False)):
        m = Match(data_dir=data_dir)
        m.load(fox.corner(m, case["gap"], case["ledge"], case["hop"]))
        f = Forced(m, getattr(fox, case["rules"]), table_mb=8, exact=exact)
        started = time.perf_counter()
        answer = getattr(f, case["mode"])(case["frames"])
        if exact:
            out["seconds"] = time.perf_counter() - started
        out[key], out[key + "_positions"] = answer, f.positions
        m.close()
    return out


def run(args) -> int:
    todo = cases()[::args.every]
    started = time.time()
    with mp.get_context("spawn").Pool(args.workers) as pool:
        solved = pool.map(solve_case, [(case, args.data_dir) for case in todo], chunksize=1)
    wall = time.time() - started
    total = {k: sum(row[k] for row in solved) for k in ("exact_positions", "look_positions", "seconds")}
    if args.write:
        rows = [{k: v for k, v in row.items() if k != "seconds"} for row in solved]
        SOLVED.write_text(json.dumps({"notes": "python -m melee_brute corpus --write", "cases": rows}, indent=1) + "\n")
        print(f"wrote {len(rows)} cases to {SOLVED}")
    else:
        known = {name(row): row for row in json.loads(SOLVED.read_text())["cases"]}
        wrong = [row for row in solved if row["exact"] != known[name(row)]["exact"]]
        apart = [row for row in solved if row["look"] != row["exact"]]
        before = {k: sum(known[name(row)][k] for row in solved) for k in ("exact_positions", "look_positions")}
        for row in wrong:
            print(f"WRONG {name(row)}: {row['exact']}, recorded {known[name(row)]['exact']}")
        for row in apart:
            print(f"look-alike disagrees: {name(row)}: exact {row['exact']}, look-alike {row['look']}")
        print(f"{len(solved)} cases, {len(wrong)} wrong, look-alike disagrees on {len(apart)}")
        for k in before:
            print(f"{k.replace('_', ' ')}: {total[k]:,} (recorded {before[k]:,}, {total[k] / before[k]:.2f}x)")
    rate = total["seconds"] / max(total["exact_positions"], 1) * 1e6
    print(f"exact: {total['seconds']:.1f} s of search, {rate:.1f} us a position; {wall:.1f} s on {args.workers} processes")
    return 1 if not args.write and wrong else 0
