"""Brute-force search on melee-sim-light: make a move, look, take it back.

    from melee_brute import Match, pad, B
    m = Match()            # Fox vs Fox on Final Destination
    m.start()              # skip the countdown
    with m.branch():       # everything inside is undone on exit, in about 9 microseconds
        m.step(pad(B, y=-80))          # P1 shines
        print(m.players()[1]["percent"])
"""
from melee_brute.match import A, B, L, NEUTRAL, R, X, Y, Z, Match, pad
from melee_brute.rollback import Rollback
from melee_brute.search import Forced, Rules, best, first_hit, improve, solve

__all__ = ["A", "B", "L", "NEUTRAL", "R", "X", "Y", "Z", "Match", "pad", "Rollback", "Forced", "Rules", "best",
           "first_hit", "improve", "solve"]
