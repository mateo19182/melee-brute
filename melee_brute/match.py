"""One simulator match driven by raw controller inputs, with exact take-back.

An input is `pad(buttons, x, y, cx, cy, l, r)`: OR-ed button bits, sticks in -80..80, triggers in 0..255.
`players()` gives the two fighters' observation records (pos_x, percent, action_id, ...: see API.md).

Take-back copies nothing up front. The match's memory is write-protected; the first write to a page is caught,
the page's old content is kept, and from then on a checkpoint copies just the pages that have been written and a
rollback copies them back. A page that was never written cannot have changed, so a rollback is exact.
"""
from __future__ import annotations

import ctypes
import struct
from contextlib import contextmanager

from melee_brute import engine

A, B, X, Y, Z, R, L = 0x0100, 0x0200, 0x0400, 0x0800, 0x0010, 0x0020, 0x0040
Input = tuple[int, int, int, int, int, int, int]
STANDING = 14  # the action id of a fighter standing still
_PADS = struct.Struct("<HbbbbBBHbbbbBB")  # two players of the simulator's 8-byte input


def pad(buttons: int = 0, x: int = 0, y: int = 0, cx: int = 0, cy: int = 0, l: int = 0, r: int = 0) -> Input:
    return buttons, x, y, cx, cy, l, r


NEUTRAL = pad()


class Match:
    def __init__(self, p1="FOX", p2="FOX", stage="FINAL_DESTINATION", seed: int = 0,
                 percents: tuple[int, int] = (0, 0), data_dir=None, levels: int = 1024):
        import melee_sim as msl
        from melee_sim import _native
        char = lambda c: int(msl.Character[c] if isinstance(c, str) else c)
        self.env = env = msl.EnvBatch(batch_size=1, length=64, action_format="raw", data_dir=data_dir)
        players = tuple(msl.PlayerConfig(char(c), controller_port=i, start_percent=percents[i])
                        for i, c in enumerate((p1, p2)))
        env.configure_match(stage=int(msl.Stage[stage] if isinstance(stage, str) else stage), seed=seed,
                            max_frame=8 * 60 * 60, players=players)
        env.reset_all()
        self._level = 0
        self.forced = None  # the Forced search this match is set up for
        # The frame call, with its three buffers resolved once for each row of the ring.
        b = env.buffers
        self._step = _native.library().msl_batch_step_masked
        self._actions = (ctypes.c_char * b.action.nbytes).from_address(b.action.ctypes.data)
        self._rows = [(t * b.action.strides[0], _native.pointer(b.action[t]), _native.pointer(b.gamestate[t + 1]),
                       _native.pointer(b.terminal[t])) for t in range(env.length)]
        self.lib = engine.library()
        self.brute = ctypes.c_void_p()
        engine.ok(self.lib.msl_brute_create(env._handle, 0, levels, ctypes.byref(self.brute)))

    def close(self) -> None:
        if self.brute:
            self.lib.msl_brute_destroy(self.brute)  # before the simulator frees the memory it protects
            self.brute = ctypes.c_void_p()
        self.env.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    # --- playing
    def players(self):
        s = self.env.current_frame[0]["slots"]
        return s[0], s[1]

    @property
    def frame(self) -> int:
        return int(self.env.current_frame[0]["frame_id"])

    def step(self, p1: Input = NEUTRAL, p2: Input = NEUTRAL, frames: int = 1) -> None:
        env = self.env
        for _ in range(frames):
            if env.t >= env.length:
                env.reset_cursor()
            offset, inputs, state, terminal = self._rows[env.t]
            _PADS.pack_into(self._actions, offset, *p1, *p2)
            if self._step(env._handle, inputs, None, state, terminal):
                raise RuntimeError("the simulator could not play the frame")
            env.t += 1

    def start(self) -> None:
        """Play out the countdown: both fighters standing and free to act."""
        for _ in range(600):
            a, b = self.players()
            if self.frame >= 0 and int(a["action_id"]) == STANDING and int(b["action_id"]) == STANDING:
                return
            self.step()
        raise RuntimeError("the match did not reach its first free frame")

    def observe(self) -> None:
        """Refresh `players()` after the state changed without a `step`."""
        if self.env.t >= self.env.length:
            self.env.reset_cursor()
        self.env.observe()

    def save(self) -> bytes:
        """A portable savestate (about 1 MB, 2.5 ms): for keeping a position or sending it elsewhere."""
        return self.env.save(0)

    def load(self, state: bytes) -> None:
        """Replace the state with a savestate. Every checkpoint is forgotten."""
        self.env.restore(0, state)
        engine.ok(self.lib.msl_brute_reset(self.brute))
        self.observe()

    # --- taking moves back
    def checkpoint(self, level: int) -> None:
        """Remember the current state as `level`."""
        engine.ok(self.lib.msl_brute_checkpoint(self.brute, level))

    def rollback(self, level: int) -> None:
        """Back to the state remembered as `level`, exactly."""
        engine.ok(self.lib.msl_brute_rollback(self.brute, level))
        self.observe()

    def hash(self) -> int:
        """128 bits that are equal for equal states, in this process and the ones forked from it."""
        out = (ctypes.c_uint64 * 2)()
        engine.ok(self.lib.msl_brute_hash(self.brute, ctypes.byref(out)))
        return out[0] | out[1] << 64

    @contextmanager
    def branch(self):
        """Everything played inside the block is undone when it ends. Blocks nest."""
        level = self._level
        self.checkpoint(level)
        self._level += 1
        try:
            yield self
        finally:
            self._level = level
            self.rollback(level)
