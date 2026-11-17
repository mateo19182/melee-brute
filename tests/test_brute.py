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


def test_a_rollback_restores_pages_first_written_after_the_checkpoint():
    """A checkpoint taken before any page was written holds nothing: the rollback rebuilds the state from
    what each page held before its first write."""
    from melee_brute import Match
    m = Match()
    m.start()
    m.load(m.save())  # forgets which pages were written
    before = m.save()
    m.checkpoint(0)
    rng = np.random.default_rng(2)
    for a, b in _random_inputs(rng, 40):
        m.step(a, b)
    m.checkpoint(1)
    middle = m.save()
    for a, b in _random_inputs(rng, 40):
        m.step(a, b)
    assert m.save() not in (before, middle)
    m.rollback(1)
    assert m.save() == middle
    m.rollback(0)
    assert m.save() == before


def test_the_hash_tells_states_apart_and_survives_a_fork(match):
    import os
    from melee_brute import B, pad
    here = match.hash()
    with match.branch():
        match.step(pad(B, y=-80))
        shine = match.hash()
    with match.branch():
        match.step(pad(x=80))
        walk = match.hash()
    assert len({here, shine, walk}) == 3 and match.hash() == here
    read, write = os.pipe()
    pid = os.fork()
    if pid == 0:  # the child plays the same frame from the same state
        match.step(pad(B, y=-80))
        os.write(write, match.hash().to_bytes(16, "little"))
        os._exit(0)
    os.waitpid(pid, 0)
    assert int.from_bytes(os.read(read, 16), "little") == shine


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


def test_the_exact_and_the_look_alike_tables_agree_here():
    from melee_brute import Forced, Match, fox
    m, n = Match(), Match()
    root = fox.corner(m, 11.5, hop=9)
    n.load(root)
    exact, look = Forced(m, fox.RULES), Forced(n, fox.RULES, exact=False)
    for frames in (3, 5, 7):
        assert exact.win(frames) == look.win(frames)
        assert exact.safe(frames) == look.safe(frames)
    assert look.positions < exact.positions
    with pytest.raises(RuntimeError):
        Forced(m, fox.RULES_TRAP) and exact.win(3)


def test_solve_on_forked_processes_matches_one_process():
    from melee_brute import Forced, Match, fox, solve
    m = Match()
    root = fox.corner(m, 11.5, hop=9)
    r = solve(root, fox.RULES, 6, "safe", workers=2, table_mb=16)
    assert r["holds"] == ["shield2"] and r["positions"] > 0
    f = Forced(m, fox.RULES)
    assert not f.win(6)
    assert solve(root, fox.RULES, 6, "win", workers=2, table_mb=16)["holds"] == []


def _corner(gap, hop=0):
    from melee_brute import Match, fox
    m = Match()
    m.load(fox.corner(m, gap, hop=hop))
    return m


def test_a_grab_counts_as_a_hit_only_where_the_rules_say_so():
    from melee_brute import Forced, fox
    m = _corner(11.5)  # 10.2 apart: the standing grab reaches

    def grabbed(rules):
        f = Forced(m, rules)
        m.checkpoint(0)
        out = 0
        for i in range(12):
            out = f.play(("grab" if i == 0 else "wait", 0, 1), ("wait", 0, -1))[0]
            if out:
                break
        m.rollback(0)
        return out
    plain = fox.Rules(fox.MOVES1, fox.MENUS1, fox.ACTIONS1)
    assert grabbed(fox.frame_rules()) == 1 and grabbed(plain) == 0


def test_the_defender_can_have_menus_of_his_own():
    from melee_brute import Forced, fox
    m = _corner(14)
    att, dfn = Forced(m, fox.frame_rules(defender={"ground": ("wait", "shine")})).options()
    assert [d[0] for d in dfn] == ["wait", "shine"] and [a[0] for a in att] == list(fox.MENUS1["ground"])


def test_zones_and_the_timeout_decide_a_position_without_search():
    from melee_brute import Forced, Zone, fox
    m = _corner(14)  # both standing 12.59 apart: nothing lands in two frames
    everywhere = dict(attacker=frozenset(fox.GROUND), defender=frozenset(fox.GROUND))
    f = Forced(m, fox.frame_rules())
    assert f.ruled(2) is None and f.ruled(0) is False and f.safe(2) and not f.win(2)
    f = Forced(m, fox.frame_rules(timeout="attacker"))
    assert f.ruled(0) is True and f.win(2) and not f.safe(2)
    f = Forced(m, fox.frame_rules(zones=(Zone("attacker", **everywhere),)))
    assert f.ruled(2) is True and f.win(2) and not f.safe(2)
    # the defender has to reach his zone before the frames run out: the one on his side of the stage holds,
    # the one on the far side cannot be reached
    here = Zone("defender", gap=(12, 13), x=(0, 90), **everywhere)
    there = Zone("defender", gap=(12, 13), x=(-90, 0), **everywhere)
    assert Forced(m, fox.frame_rules(zones=(here,), timeout="attacker")).safe(2)
    f = Forced(m, fox.frame_rules(zones=(there,), timeout="attacker"))
    assert f.ruled(2) is None and not f.safe(2)
    # either_side reads the gap as a distance
    back = Zone("attacker", gap=(-13, -12), **everywhere)
    assert Forced(m, fox.frame_rules(zones=(back,))).ruled(2) is None
    assert Forced(m, fox.frame_rules(zones=(Zone("attacker", gap=(12, 13), either_side=True, **everywhere),))).ruled(2)


def test_one_input_a_frame_gives_the_known_answers():
    """Solved before as the simultaneous game (melee-llm, docs/optimal-play.md section 13): five frames into
    the attacker's short hop the air shine is forced unless the defender shields; nine frames in it is a guess."""
    from melee_brute import Forced, fox
    bare = fox.frame_rules(defender={"ground": tuple(x for x in fox.MENUS1["ground"] if x != "shield")})
    m = _corner(11.5, hop=5)
    f = Forced(m, bare)
    assert f.win(10) and not f.safe(10)
    m = _corner(11.5, hop=9)
    f = Forced(m, bare)
    assert not f.win(6) and not f.safe(6)
    assert Forced(m, fox.frame_rules()).safe(6)


def test_a_zone_can_stop_at_an_action_frame():
    from melee_brute import Forced, Zone, fox
    m = _corner(14)
    early = Zone("attacker", frozenset({14}), frozenset({235}), defender_frame=3)  # 235: spotdodge
    f = Forced(m, fox.frame_rules(zones=(early,)))
    m.checkpoint(0)
    seen = []
    for i in range(5):
        f.play(("wait", 0, 1), ("spotdodge" if i == 0 else "wait", 0, -1))
        seen.append(f.ruled(5))
    m.rollback(0)
    assert seen == [True, True, True, None, None]
