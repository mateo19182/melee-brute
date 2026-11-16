"""One simulator match driven by raw controller inputs, with checkpoints.

An input is `pad(buttons, x, y, cx, cy, l, r)`: OR-ed button bits, sticks in -80..80, triggers in 0..255.
`players()` gives the two fighters' observation records (pos_x, percent, action_id, ...: see API.md).
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Callable

import numpy as np

A, B, X, Y, Z, R, L = 0x0100, 0x0200, 0x0400, 0x0800, 0x0010, 0x0020, 0x0040
Input = tuple[int, int, int, int, int, int, int]
STANDING = 14  # the action id of a fighter standing still


def pad(buttons: int = 0, x: int = 0, y: int = 0, cx: int = 0, cy: int = 0, l: int = 0, r: int = 0) -> Input:
    return buttons, x, y, cx, cy, l, r


NEUTRAL = pad()


class Match:
    def __init__(self, p1="FOX", p2="FOX", stage="FINAL_DESTINATION", seed: int = 0,
                 percents: tuple[int, int] = (0, 0), data_dir=None, depth: int = 256):
        import melee_sim as msl
        char = lambda c: int(msl.Character[c] if isinstance(c, str) else c)
        self.env = msl.EnvBatch(batch_size=1, length=64, action_format="raw", data_dir=data_dir)
        players = tuple(msl.PlayerConfig(char(c), controller_port=i, start_percent=percents[i])
                        for i, c in enumerate((p1, p2)))
        self.env.configure_match(stage=int(msl.Stage[stage] if isinstance(stage, str) else stage), seed=seed,
                                 max_frame=8 * 60 * 60, players=players)
        self.env.reset_all()
        self._depth, self._level, self.rb = depth, 0, None
        self._at_root = False  # is the state the one the rollback reference was taken at?
        self.missed = 0  # times a rollback to the root found a page outside the dirty set changed

    # --- playing
    def players(self):
        s = self.env.current_frame[0]["slots"]
        return s[0], s[1]

    @property
    def frame(self) -> int:
        return int(self.env.current_frame[0]["frame_id"])

    def step(self, p1: Input = NEUTRAL, p2: Input = NEUTRAL, frames: int = 1) -> None:
        env = self.env
        self._at_root = False
        for _ in range(frames):
            if env.t >= env.length:
                env.reset_cursor()
            row = env.raw_action_view[env.t, 0]["players"]
            for i, (b, x, y, cx, cy, l, r) in enumerate((p1, p2)):
                row[i]["buttons"], row[i]["main_x"], row[i]["main_y"] = b, x, y
                row[i]["c_x"], row[i]["c_y"], row[i]["l"], row[i]["r"] = cx, cy, l, r
            env.step()

    def start(self) -> None:
        """Play out the countdown: both fighters standing and free to act."""
        for _ in range(600):
            a, b = self.players()
            if self.frame >= 0 and int(a["action_id"]) == STANDING and int(b["action_id"]) == STANDING:
                return
            self.step()
        raise RuntimeError("the match did not reach its first free frame")

    def observe(self) -> None:
        if self.env.t >= self.env.length:
            self.env.reset_cursor()
        self.env.observe()

    def save(self) -> bytes:
        """A portable savestate (about 1 MB, 2.5 ms): for sending a position to another process."""
        return self.env.save(0)

    def load(self, state: bytes) -> None:
        self.env.restore(0, state)
        self.observe()
        self._at_root = False

    # --- taking moves back
    def learn(self, frames: int = 6000, seed: int = 0) -> int:
        """Find the memory pages a frame can change by playing random inputs from here, then come back.
        Called by the first checkpoint; returns how many pages were found."""
        from melee_brute.rollback import Rollback
        if self.rb is None:
            self.rb = Rollback(self.env, depth=self._depth)
        else:
            self.rb.rebase()
        rng = np.random.default_rng(seed)
        buttons = np.array([A, B, X, Y, Z, R, L])
        left, rows = [0, 0], [NEUTRAL, NEUTRAL]
        for f in range(frames):
            for i in (0, 1):  # each player holds a random input for 1 to 20 frames
                if left[i] == 0:
                    left[i] = int(rng.integers(1, 21))
                    rows[i] = pad(int(buttons[rng.random(7) < 0.15].sum()),
                                  *(int(v) for v in rng.choice((-80, -40, 0, 40, 80), 4, p=(.2, .05, .5, .05, .2))),
                                  *(int(v) for v in rng.choice((0, 255), 2, p=(0.85, 0.15))))
                left[i] -= 1
            self.step(*rows)
            if f % 100 == 99:
                self.rb.add(self.rb.changed())
        self.rb.add(self.rb.changed())
        self.rb.reset()
        self.observe()
        self._at_root = True
        return len(self.rb.pages)

    def checkpoint(self, level: int) -> None:
        """Remember the current state as `level`. Level 0 is the root of a search: deeper levels are only valid
        until the next checkpoint at level 0."""
        if self.rb is None:
            self.learn()
        if level == 0 and not self._at_root:
            self.rb.rebase()
            self._at_root = True
        self.rb.checkpoint(level)

    def rollback(self, level: int) -> None:
        """Back to the state remembered as `level`. A rollback to level 0 is always exact: it also puts back
        any page outside the dirty set, adds it to the set and counts the miss in `missed`."""
        self.rb.rollback(level)
        if level == 0:
            stray = self.rb.verify()
            if len(stray):
                self.rb.restore(stray)
                self.rb.add(stray)
                self.missed += 1
            self._at_root = True
        self.observe()

    def exact(self, run: Callable):
        """`run()`, repeated while a rollback inside it missed a page: deeper rollbacks may then have been
        inexact. Each repeat knows the page, so this settles after a few rounds at most."""
        while True:
            before = self.missed
            out = run()
            if self.missed == before:
                return out

    @contextmanager
    def branch(self):
        """Everything played inside the block is undone when it ends. Blocks nest; the outermost one is the
        root (level 0) and always ends exactly where it began."""
        level = self._level
        self.checkpoint(level)
        self._level += 1
        try:
            yield self
        finally:
            self._level = level
            self.rollback(level)
