"""python -m melee_brute bench | fox"""
from __future__ import annotations

import argparse
import os
import sys
import time

from melee_brute import Forced, Match, corpus, fox, pad, solve
from melee_brute.match import B


def bench(args) -> None:
    m = Match(data_dir=args.data_dir)
    m.load(fox.corner(m, 11.5, hop=9))
    f = Forced(m, fox.RULES)
    t = time.perf_counter()
    f.win(9)
    seconds, s = time.perf_counter() - t, f.stats()
    print(f"forced search        {seconds / s.positions * 1e6:7.1f} us a position "
          f"({s.positions:,} positions, {s.nodes:,} expanded, {s.table_hits:,} found in the table)")
    print(f"pages a rollback copies: {s.dirty_pages}")

    def timed(fn, n):
        t = time.perf_counter()
        for _ in range(n):
            fn()
        return (time.perf_counter() - t) / n * 1e6
    lib, brute = m.lib, m.brute
    print(f"one frame            {timed(m.step, 5000):7.1f} us  (from Python, with its observation)")
    print(f"checkpoint           {timed(lambda: lib.msl_brute_checkpoint(brute, 0), 5000):7.1f} us")
    print(f"rollback             {timed(lambda: lib.msl_brute_rollback(brute, 0), 5000):7.1f} us")
    print(f"state hash           {timed(m.hash, 5000):7.1f} us")
    print(f"savestate save+load  {timed(lambda: m.load(m.save()), 100):7.1f} us")

    def position():
        with m.branch():
            m.step(pad(B, y=-80))
    print(f"branch, step, undo   {timed(position, 5000):7.1f} us  (one position searched from Python)")


def run_fox(args) -> None:
    m = Match(data_dir=args.data_dir)
    root = fox.corner(m, args.gap, args.ledge, args.hop)
    a, b = m.players()
    print(f"P1 at x={float(a['pos_x']):.1f} y={float(a['pos_y']):.1f}, P2 at x={float(b['pos_x']):.1f}, "
          f"{float(b['pos_x'] - a['pos_x']):.1f} apart, {args.frames} frames")
    m.close()
    rules = fox.RULES_TRAP if args.trap else fox.RULES
    r = solve(root, rules, args.frames, "safe" if args.safe else "win", args.workers, data_dir=args.data_dir,
              table_mb=args.table_mb, out=args.out, exact=not args.look, tag=f"gap {args.gap} ledge {args.ledge} hop {args.hop} trap {args.trap}")
    if args.safe:
        print(f"defender safe with {r['holds']}" if r["holds"] else "NO SAFE DEFENCE: the defender must guess")
    else:
        print(f"FORCED WIN with {r['holds']}" if r["holds"] else "no forced win")
    for move, by in r["beaten_by"].items():
        print(f"   {move:8s} " + ("holds against everything" if not by else "beaten by: " + ", ".join(by)))
    print(f"{r['positions']:,} positions in {r['seconds']} s on {r['workers']} processes; "
          f"{r['resumed']} of {r['jobs']} jobs came from an earlier run")


def main() -> None:
    ap = argparse.ArgumentParser(prog="melee_brute", description=__doc__)
    ap.add_argument("--data-dir", help="extracted game data (default: $MSL_DATA_DIR or ./data)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("bench", help="time a frame, a checkpoint and a rollback").set_defaults(fn=bench)
    f = sub.add_parser("fox", help="Fox vs cornered Fox: can the attacker force the first hit?")
    f.add_argument("--gap", type=float, default=9)
    f.add_argument("--ledge", type=float, default=12, help="how far the defender stands from the ledge behind him")
    f.add_argument("--hop", type=int, default=0, help="start with the attacker this many frames into a short hop")
    f.add_argument("--frames", type=int, default=10)
    f.add_argument("--safe", action="store_true", help="ask whether the defender has a defence that needs no guess")
    f.add_argument("--trap", action="store_true", help="count the measured shield trap as a win for the attacker")
    f.add_argument("--look", action="store_true",
                   help="let positions that look the same share an answer: much faster, can be wrong")
    f.add_argument("--workers", type=int)
    f.add_argument("--table-mb", type=int, default=2048, help="memory for decided positions, shared by all processes")
    f.add_argument("--out", help="file that collects finished jobs; a rerun with it continues where it stopped")
    f.set_defaults(fn=run_fox)
    c = sub.add_parser("corpus", help="solve the fixed set of positions again and compare with the recorded answers")
    c.add_argument("--write", action="store_true", help="record the answers and position counts")
    c.add_argument("--every", type=int, default=1, help="only every Nth case")
    c.add_argument("--workers", type=int, default=min(16, os.cpu_count() or 1))
    c.set_defaults(fn=lambda args: sys.exit(corpus.run(args)))
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
