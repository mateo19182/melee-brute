"""melee_brute: rollback exactness and the searches. Needs the built library and extracted game data."""
import os
from pathlib import Path

import numpy as np
import pytest

DATA = Path(os.environ.get("MSL_DATA_DIR", "data"))
pytestmark = pytest.mark.skipif(not DATA.is_dir(), reason="game data not extracted (set MSL_DATA_DIR)")


@pytest.fixture(scope="module")
def match():
    from melee_brute import Match
    m = Match()
    m.start()
    m.learn()
    return m


def _random_inputs(rng, n):
    from melee_brute import A, B, X, R, pad
    options = [pad(), pad(B, y=-80), pad(X), pad(A), pad(x=80), pad(x=-80), pad(R, r=255), pad(B), pad(A, y=80)]
    return [(options[i], options[j]) for i, j in rng.integers(len(options), size=(n, 2))]


def test_a_rollback_and_replay_gives_the_same_savestate(match):
    rng = np.random.default_rng(1)
    for _ in range(30):
        for a, b in _random_inputs(rng, int(rng.integers(1, 20))):
            match.step(a, b)
        seq = _random_inputs(rng, int(rng.integers(1, 8)))
        match.checkpoint(0)
        for a, b in seq:
            match.step(a, b)
        first = match.save()
        match.rollback(0)
        for a, b in seq:
            match.step(a, b)
        assert match.save() == first


def test_branch_undoes_what_happens_inside(match):
    from melee_brute import B, pad
    before = match.save()
    with match.branch():
        match.step(pad(B, y=-80), frames=5)
        with match.branch():
            match.step(pad(x=80), frames=5)
        assert match.save() != before
    assert match.save() == before


def test_best_and_improve_find_the_line_that_moves_furthest_right():
    from melee_brute import Match, best, improve, pad
    m = Match()
    m.start()
    choices = [pad(), pad(x=80), pad(x=-80)]
    x = lambda mm: float(mm.players()[0]["pos_x"])
    value, line = best(m, choices, 4, x)
    assert line == [pad(x=80)] * 4 and value > -60
    better, _ = improve(m, [pad()] * 12, choices, x, tries=150)
    assert better > x(m)


def test_point_blank_shine_is_answered_only_by_shine_or_shield():
    from melee_brute import Forced, Match, fox
    m = Match()
    m.load(fox.corner(m, 9))
    f = Forced(m, fox.RULES)
    assert not f.win(4) and f.safe(4)
    att, dfn = f.options()
    shine = next(a for a in att if a[0] == "shine")
    m.checkpoint(0)
    survive = set()
    for d in dfn:
        if f.play(shine, d)[0] <= 0:
            survive.add(d[0])
        m.rollback(0)
    assert survive == {"shine", "shield2"}


def test_against_a_fox_in_the_air_only_shield_is_safe():
    """The published result: attacker nine frames into a short hop above a cornered Fox."""
    from melee_brute import Forced, Match, fox
    m = Match()
    m.load(fox.corner(m, 11.5, hop=9))
    f = Forced(m, fox.RULES)
    att, dfn = f.options()
    m.checkpoint(0)
    holds = set()
    for d in dfn:
        ok = True
        for a in att:
            o, u, ra, rd = f.play(a, d)
            ok = o < 0 or (o == 0 and f.safe(6 - u, ra, rd, depth=1))
            m.rollback(0)
            if not ok:
                break
        if ok:
            holds.add(d[0])
    assert holds == {"shield2"}
    assert not f.win(6)


def test_jump_out_of_shield_needs_the_shield_button_held():
    from melee_brute import Match, X, fox, pad
    m = Match()
    m.start()
    m.step(pad(), fox.MOVES["shield2"][0], frames=12)
    assert int(m.players()[1]["action_id"]) in fox.SHIELD

    def reaches_squat(move):
        with m.branch():
            for p in fox.MOVES[move]:
                m.step(pad(), p)
            for _ in range(3):
                if int(m.players()[1]["action_id"]) in fox.SQUAT:
                    return True
                m.step(pad(), fox.MOVES["hold_shield"][0] if move == "jump_oos" else pad())
        return False

    assert fox.MOVES["jump"] == (pad(X),)
    assert reaches_squat("jump_oos") and not reaches_squat("jump")
    assert "jump_oos" in fox.MENUS["shield"] and "jump" not in fox.MENUS["shield"]
    assert "shine" in fox.MENUS["squat"]
