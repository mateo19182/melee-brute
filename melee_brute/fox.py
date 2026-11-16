"""A worked example: Fox against Fox on Final Destination, with the defender cornered at the ledge.

`RULES` gives each Fox a menu that depends on his state: 11 moves standing, 6 in the air, 7 in shield, 3 in the
shine, 3 in jumpsquat, and only "wait" while he cannot act. Most moves last one or two frames, so both sides
decide almost every frame.
"""
from __future__ import annotations

from melee_brute.match import A, B, NEUTRAL, R, X, Z, Match, pad
from melee_brute.search import Rules

_IN, _OUT, _SHIELD = pad(x=80), pad(x=-80), pad(R, r=255)
MOVES = {
    "wait": (NEUTRAL,), "wait2": (NEUTRAL, NEUTRAL), "in": (_IN,), "out": (_OUT,), "in2": (_IN, _IN),
    "out2": (_OUT, _OUT), "shine": (pad(B, y=-80),), "hold_b": (pad(B, y=-80),), "jab": (pad(A),),
    "grab": (pad(Z),), "usmash": (pad(A, y=80),), "hop": (pad(X),), "hop_in": (pad(X, x=80),),
    "fullhop": (pad(X),) * 3, "shield2": (_SHIELD, _SHIELD), "hold_shield": (_SHIELD, _SHIELD),
    "roll_in": (pad(R, x=80, r=255),), "roll_out": (pad(R, x=-80, r=255),), "spotdodge": (pad(R, y=-80, r=255),),
    "shield_grab": (pad(R | A, r=255),), "jump": (pad(X),), "nair": (pad(A),),
}
MENUS = {
    "ground": ("wait2", "in2", "out2", "shine", "jab", "hop", "hop_in", "fullhop", "shield2", "grab", "usmash"),
    "air": ("wait2", "in2", "out2", "shine", "jump", "nair"),
    "squat": ("wait", "in", "out"),
    "shine": ("wait", "hold_b", "jump"),
    "shield": ("hold_shield", "wait", "jump", "roll_in", "roll_out", "spotdodge", "shield_grab"),
    "locked": ("wait",),
}
# Action ids (the game's own, see API.md): standing, walking, turning, dashing, running, crouching, landing
GROUND = {14, 15, 16, 17, 18, 19, 20, 21, 23, 39, 40, 41, 42}
FREE = GROUND - {19, 23, 42}  # not in a run turn, a run brake or landing lag
AIR = set(range(25, 35))  # jumps and falls
SQUAT, SHIELD, SHINE = {24}, {178, 179, 181, 182}, {360, 361, 364, 365, 366, 368}
LEDGE = 85.57  # Final Destination's ledges are at x = +-85.57


def menu(slot) -> tuple[str, ...]:
    a = int(slot["action_id"])
    for kind, ids in (("ground", GROUND), ("air", AIR), ("squat", SQUAT), ("shield", SHIELD), ("shine", SHINE)):
        if a in ids:
            return MENUS[kind]
    return MENUS["locked"]


def shield_trap(a, b) -> bool:
    """P2 is in shield and P1 stands within 10 units, free to act. Measured separately: every way out of shield
    then loses 5-18% to a reaction, and a shield held untouched breaks after 196 frames."""
    return (int(b["action_id"]) in (178, 179) and int(a["action_id"]) in FREE
            and abs(float(b["pos_x"]) - float(a["pos_x"])) <= 10.0 and float(a["pos_y"]) == float(b["pos_y"]))


RULES = Rules(MOVES, menu)
RULES_TRAP = Rules(MOVES, menu, shield_trap)


def corner(match: Match, gap: float, ledge: float = 12, hop: int = 0) -> bytes:
    """P2 stands `ledge` units from the right ledge facing P1, who stands about `gap` away. With `hop`, P1 has
    then short-hopped toward P2 and is `hop` frames into it, P2 not having moved. Returns the savestate."""
    match.start()
    x = lambda i: float(match.players()[i]["pos_x"])
    for _ in range(600):  # P2 walks right
        if x(1) >= LEDGE - ledge:
            break
        match.step(NEUTRAL, pad(x=30))
    match.step(frames=20)
    match.step(NEUTRAL, pad(x=-25), frames=3)  # and turns back to face the stage
    match.step(frames=25)
    for _ in range(900):  # P1 walks in
        if x(1) - x(0) <= gap + 1.0:
            break
        match.step(pad(x=30))
    match.step(frames=30)
    for f in range(hop):
        match.step(pad(X, x=40) if f == 0 else pad(x=40))
    return match.save()
