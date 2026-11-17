"""Generated input tapes: matches played by seeded random controllers.

A recipe names a match (stage, fighters, start percents) and a seed. Playing it on the reference build
gives a tape, one raw controller row per player per frame, and a digest of every frame's observation.
Replaying the tape on another build must give the same digests. Nothing is recorded from a console, so
a tape can reach code no Slippi recording does.

The controllers look at the match they are playing: they walk toward each other and steer back when
off stage, so that fighters meet. A tape is therefore a product of the recipe and the reference build.
"""
from __future__ import annotations

import ctypes
import hashlib
import random
import struct
from dataclasses import dataclass

import numpy as np

A, B, X, Y, Z, R, L, DPAD_UP = 0x0100, 0x0200, 0x0400, 0x0800, 0x0010, 0x0020, 0x0040, 0x0008
_PAD = struct.Struct("<HbbbbBB")
Row = tuple[int, int, int, int, int, int, int]  # buttons, x, y, c-stick x, c-stick y, l, r
NEUTRAL: Row = (0, 0, 0, 0, 0, 0, 0)


@dataclass(frozen=True)
class Recipe:
    stage: str
    fighters: tuple[str, ...]
    seed: int
    frames: int = 3000
    percents: tuple[int, ...] = ()
    teams: tuple[int, ...] = ()

    @property
    def name(self) -> str:
        return f"{self.stage.lower()}-{'-'.join(f.lower() for f in self.fighters)}-{self.seed}"

    def to_json(self) -> dict:
        row = {"stage": self.stage, "fighters": list(self.fighters), "seed": self.seed, "frames": self.frames}
        if self.percents:
            row["percents"] = list(self.percents)
        if self.teams:
            row["teams"] = list(self.teams)
        return row

    @classmethod
    def from_json(cls, row: dict) -> "Recipe":
        return cls(row["stage"], tuple(row["fighters"]), row["seed"], row.get("frames", 3000),
                   tuple(row.get("percents", ())), tuple(row.get("teams", ())))


class Controller:
    """One seeded player. Every few frames it picks a plan, a short run of rows, from the kinds below."""

    def __init__(self, seed: int):
        self.r = random.Random(seed)
        self.plan: list[Row] = []

    def _pick(self, n: int) -> int:
        return int(self.r.random() * n)

    def _stick(self) -> int:
        return (-80, -80, -45, -25, 0, 25, 45, 80, 80)[self._pick(9)]

    def row(self, me, other) -> Row:
        if not self.plan:
            self.plan = self._plan(me, other)
        return self.plan.pop(0)

    def _plan(self, me, other) -> list[Row]:
        r, pick = self.r, self._pick
        toward = 80 if float(other["pos_x"]) >= float(me["pos_x"]) else -80
        x, y = float(me["pos_x"]), float(me["pos_y"])
        if (abs(x) > 75 or y < -8) and r.random() < 0.85:  # off stage: come back
            home = 80 if x < 0 else -80
            return ([(X, home, 0, 0, 0, 0, 0)] + [(0, home, 0, 0, 0, 0, 0)] * (2 + pick(10))
                    + [(B, home // 2, 80, 0, 0, 0, 0)] + [(0, home, 40, 0, 0, 0, 0)] * (4 + pick(20)))
        aim = toward if r.random() < 0.7 else self._stick()
        kind = pick(22)
        hold = lambda row, n: [row] * n
        if kind == 0:
            return hold(NEUTRAL, 1 + pick(12))
        if kind in (1, 2, 3):  # walk or run, mostly at the other fighter
            return hold((0, aim, 0, 0, 0, 0, 0), 4 + pick(30))
        if kind == 4:  # jump, then drift and maybe attack or jump again
            air = [(0, aim, 0, 0, 0, 0, 0)] * (2 + pick(14))
            end = [((A, B, X, 0)[pick(4)], self._stick(), self._stick(), 0, 0, 0, 0)]
            return hold((X, aim, 0, 0, 0, 0, 0), 1 + pick(4)) + air + end + hold((0, aim, -80 * pick(2), 0, 0, 0, 0), 2 + pick(10))
        if kind in (5, 6):  # jab, tilt, smash or aerial with A
            return [(A, self._stick(), self._stick(), 0, 0, 0, 0)] + hold(NEUTRAL, 1 + pick(6))
        if kind == 7:  # the same with the C-stick
            return [(0, 0, 0, self._stick(), self._stick(), 0, 0)] + hold(NEUTRAL, 1 + pick(6))
        if kind in (8, 9, 10, 11):  # a special, tapped or held, in one of four directions
            sx, sy = ((0, 0), (aim, 0), (0, 80), (0, -80))[kind - 8]
            held = 1 + (pick(70) if r.random() < 0.5 else pick(4))
            after = [(0, 0, -80, 0, 0, 0, 0)] * pick(3) + [(B, self._stick(), self._stick(), 0, 0, 0, 0)] * pick(2)
            return [(B, sx, sy, 0, 0, 0, 0)] + hold((B, sx // 2, sy // 2, 0, 0, 0, 0), held) + after
        if kind in (12, 13):  # grab, then pummel or throw
            throw = [(A, 0, 0, 0, 0, 0, 0)] * pick(3) + [(0, self._stick(), self._stick(), 0, 0, 0, 0)]
            return [(Z, 0, 0, 0, 0, 0, 0)] + hold(NEUTRAL, 6 + pick(10)) + throw + hold(NEUTRAL, pick(8))
        if kind in (14, 15):  # shield, and maybe roll, dodge, grab or jump out of it
            out = [((R, R, R | A, R | X)[pick(4)], self._stick(), self._stick(), 0, 0, 0, 255)]
            return hold((R, 0, 0, 0, 0, 0, 255), 2 + pick(40)) + out * pick(2) + hold(NEUTRAL, pick(4))
        if kind == 16:  # light shield or an air dodge
            return hold((0, self._stick(), self._stick(), 0, 0, 60 + pick(160), 0), 1 + pick(20))
        if kind == 17:  # dash back and forth
            return [(0, 80 if i // (2 + pick(3)) % 2 else -80, 0, 0, 0, 0, 0) for i in range(6 + pick(16))]
        if kind == 18:  # crouch, fast fall or drop through
            return hold((0, 0, -80, 0, 0, 0, 0), 1 + pick(14))
        if kind == 19:  # tap up: jump, or get up from the ledge
            return [(0, 0, 80, 0, 0, 0, 0)] + hold(NEUTRAL, pick(6))
        if kind == 20:
            return [(DPAD_UP, 0, 0, 0, 0, 0, 0)] + hold(NEUTRAL, pick(30))
        return [((A | B, X | A, Y, L, Z | X)[pick(5)], self._stick(), self._stick(), self._stick(), self._stick(), 0, 0)]


_HELD = []  # one idle batch: the game data stays loaded between matches instead of being read again


class Player:
    """One match on the simulator library this process loaded."""

    def __init__(self, recipe: Recipe, data_dir=None):
        import melee_sim as msl
        from melee_sim import _native
        if not _HELD:
            _HELD.append(msl.EnvBatch(batch_size=1, length=1, data_dir=data_dir))
        self.recipe, n = recipe, len(recipe.fighters)
        self.env = env = msl.EnvBatch(batch_size=1, length=64, num_players=n, action_format="raw",
                                      data_dir=data_dir)
        players = tuple(msl.PlayerConfig(int(msl.Character[c]), controller_port=i,
                                         team_id=recipe.teams[i] if recipe.teams else None,
                                         start_percent=recipe.percents[i] if recipe.percents else 0)
                        for i, c in enumerate(recipe.fighters))
        env.configure_match(config=msl.MatchConfig(stage=int(msl.Stage[recipe.stage]), players=players,
                                                   seed=recipe.seed, max_frame=recipe.frames + 200,
                                                   is_teams=bool(recipe.teams)))
        env.reset_all()
        b = env.buffers
        self._step = _native.library().msl_batch_step_masked
        self._actions = (ctypes.c_char * b.action.nbytes).from_address(b.action.ctypes.data)
        self._rows = [(t * b.action.strides[0], _native.pointer(b.action[t]), _native.pointer(b.gamestate[t + 1]),
                       _native.pointer(b.terminal[t])) for t in range(env.length)]
        self._state = [b.gamestate[t, 0] for t in range(env.length + 1)]
        self._done = b.done
        self._view = env.gamestate_view
        self.players = n

    def frame(self, rows) -> bool:
        """Play one frame. False once the match has ended."""
        env = self.env
        if env.t >= env.length:
            env.reset_cursor()
        offset, inputs, state, terminal = self._rows[env.t]
        for i, row in enumerate(rows):
            _PAD.pack_into(self._actions, offset + 8 * i, *row)
        if self._step(env._handle, inputs, None, state, terminal):
            raise RuntimeError("the simulator could not play the frame")
        env.t += 1
        return not self._done[env.t - 1, 0]

    def observation(self) -> bytes:
        return self._state[self.env.t].tobytes()

    def slots(self):
        return self._view[self.env.t, 0]["slots"]


def digest(observation: bytes) -> int:
    return int.from_bytes(hashlib.blake2b(observation, digest_size=8).digest(), "little")


def record(recipe: Recipe, data_dir=None) -> tuple[np.ndarray, np.ndarray]:
    """Play the recipe with its controllers. Returns the tape, int16[frames, players, 7], and the digest of
    each frame's observation."""
    match = Player(recipe, data_dir)
    pads = [Controller(recipe.seed * 1000 + i) for i in range(match.players)]
    tape, digests = [], []
    for _ in range(recipe.frames):
        slots = match.slots()
        rows = [pads[i].row(slots[i], slots[(i + 1) % match.players]) for i in range(match.players)]
        alive = match.frame(rows)
        tape.append(rows)
        digests.append(digest(match.observation()))
        if not alive:
            break
    match.env.close()
    return np.array(tape, dtype=np.int16), np.array(digests, dtype=np.uint64)


def replay(recipe: Recipe, tape: np.ndarray, data_dir=None, keep: int | None = None):
    """Play a recorded tape. Returns the digests, and with `keep` the observation after that frame."""
    match = Player(recipe, data_dir)
    digests, kept = [], None
    for f, rows in enumerate(tape.tolist()):
        alive = match.frame(rows)
        observation = match.observation()
        digests.append(digest(observation))
        if f == keep:
            kept = observation
        if not alive:
            break
    match.env.close()
    return np.array(digests, dtype=np.uint64), kept


def takeback(recipe: Recipe, tape: np.ndarray, data_dir=None, every: int = 61) -> tuple[int, list[str]]:
    """Play a tape and, every `every` frames, check the search's take-back on it: checkpoint, play ahead,
    roll back, and require the savestate and the state hash from before; then play the same frames again and
    require the same observations and the same hash at the end. Returns the checks made and what failed."""
    from melee_brute import engine
    match = Player(recipe, data_dir)
    lib, brute = engine.library(), ctypes.c_void_p()
    engine.ok(lib.msl_brute_create(match.env._handle, 0, 4, ctypes.byref(brute)))
    word = (ctypes.c_uint64 * 2)()

    def state_hash() -> tuple[int, int]:
        engine.ok(lib.msl_brute_hash(brute, ctypes.byref(word)))
        return word[0], word[1]

    rows, r = tape.tolist(), random.Random(recipe.seed)
    checks, failures, f = 0, [], 0
    try:
        while f < len(rows):
            ahead = rows[f:f + 1 + int(r.random() * 40)] if f % every == 0 else []
            if ahead:
                engine.ok(lib.msl_brute_checkpoint(brute, 0))
                before, saved = state_hash(), match.env.save(0)
                first = []
                for row in ahead:
                    match.frame(row)
                    first.append(digest(match.observation()))
                after = state_hash()
                engine.ok(lib.msl_brute_rollback(brute, 0))
                checks += 1
                if state_hash() != before or match.env.save(0) != saved:
                    failures.append(f"frame {f}: the state after a rollback of {len(ahead)} frames is not the one before")
                again = []
                for row in ahead:
                    alive = match.frame(row)
                    again.append(digest(match.observation()))
                if again != first or state_hash() != after:
                    failures.append(f"frame {f}: {len(ahead)} frames played again after a rollback came out different")
                f += len(ahead)
            else:
                alive = match.frame(rows[f])
                f += 1
            if not alive:
                break
    finally:
        lib.msl_brute_destroy(brute)
        match.env.close()
    return checks, failures
