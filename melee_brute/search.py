"""Searches built on take-back: best line for one player (exhaustive or by random changes), and two-player
forced-win search.

`best` tries every sequence of choices and keeps the highest score: the tool for "what is the most damage,
the furthest launch, the fastest kill from here". `improve` does the same for long lines by random changes.

`Forced` answers two questions about a two-player position, for a set of `Rules`:
- `win`: can the attacker (P1) land the first hit whatever the defender (P2) does, when the defender sees each
  attacker move before answering? A yes is a forced win with no guessing.
- `safe`: can the defender avoid being hit first whatever the attacker does, when the attacker sees each
  defender move before answering? A yes is a guaranteed defence.
If both are no, the defender has to guess: the position is decided by mixed strategies.
"""
from __future__ import annotations

import importlib
import json
import multiprocessing as mp
import os
import time
from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np

from melee_brute.match import Input, Match


def best(match: Match, choices: Sequence, depth: int, score: Callable[[Match], float],
         play: Callable[[Match, object], None] | None = None) -> tuple[float, list]:
    """The sequence of `depth` choices with the highest `score(match)` at its end, by trying all of them.
    `play(match, choice)` makes one choice; by default a choice is a P1 input held for one frame."""
    play = play or (lambda m, c: m.step(c))

    def go(left: int) -> tuple[float, list]:
        if left == 0:
            return score(match), []
        top = (-np.inf, [])
        for c in choices:
            with match.branch():
                play(match, c)
                value, line = go(left - 1)
            if value > top[0]:
                top = (value, [c] + line)
        return top
    return match.exact(lambda: go(depth))


def improve(match: Match, line: Sequence, choices: Sequence, score: Callable[[Match], float],
            tries: int = 2000, seed: int = 0, play: Callable[[Match, object], None] | None = None,
            every: int = 8) -> tuple[float, list]:
    """Improve an existing line by random changes, the way TMInterface and the SM64 bruteforcers do: change
    one choice, replay from the nearest checkpoint, keep the change if `score(match)` at the end got better.
    Use it when `best` is out of reach: a line of 300 frames has too many variations to try them all.
    Leaves the match where it started; returns the best score and line."""
    play = play or (lambda m, c: m.step(c))
    start, line = list(line), list(line)
    marks = range(0, len(line), every)  # checkpoint levels 1.. hold the state before these positions

    def run(first: int) -> float:
        for i in range(first, len(line)):
            if i % every == 0:
                match.checkpoint(1 + i // every)
            play(match, line[i])
        return score(match)

    def search() -> tuple[float, list]:
        nonlocal line
        line = list(start)
        rng = np.random.default_rng(seed)
        match.checkpoint(0)
        top = run(0)
        for _ in range(tries):
            i = int(rng.integers(len(line)))
            old, line[i] = line[i], choices[int(rng.integers(len(choices)))]
            if line[i] == old:
                continue
            match.rollback(1 + i // every)
            value = run(marks[i // every])
            if value > top:
                top = value
            else:  # undo the change; checkpoints after it now hold the rejected line, so lay them again
                line[i] = old
                match.rollback(1 + i // every)
                run(marks[i // every])
        match.rollback(0)
        return top, line
    return match.exact(search)


@dataclass(frozen=True)
class Rules:
    """`moves`: name -> one Input per frame, stick x written as if the opponent were to the right.
    `menu(slot)`: the move names a fighter in that state may pick. `won(p1, p2)`: an optional position the
    attacker is known to win, used as a shortcut."""
    moves: dict[str, tuple[Input, ...]]
    menu: Callable[[object], Sequence[str]]
    won: Callable[[object, object], bool] | None = None


def first_hit(base: tuple, a, b) -> int:
    """+1 P1 landed the first hit, -1 P1 was hit or it was a trade, 0 nobody hit yet. `base` is
    (percent 1, percent 2, stocks 1, stocks 2) at the root."""
    d1 = float(a["percent"]) - base[0] + 100 * (base[2] - int(a["stocks"]))
    d2 = float(b["percent"]) - base[1] + 100 * (base[3] - int(b["stocks"]))
    return 0 if d1 == 0 and d2 == 0 else 1 if d2 > d1 else -1


def _key(s) -> tuple:
    return (round(float(s["pos_x"]), 2), round(float(s["pos_y"]), 2), round(float(s["speed_ground_x_self"]), 2),
            round(float(s["speed_air_x_self"]), 2), round(float(s["speed_y_self"]), 2), int(s["action_id"]),
            int(s["action_frame"]), int(s["hitlag"]), round(float(s["shield_hp"]), 1), int(s["facing"]),
            int(s["jumps_left"]))


TABLE_BYTES = 140  # measured memory per remembered position
WORKER_MB = 600  # a search process before its table grows
RESERVE_MB = 10_000  # memory left for everything else on the machine


def fit_workers(workers: int, table_mb: int) -> int:
    """As many processes as the free memory holds with their tables full (Linux; elsewhere `workers`)."""
    try:
        with open("/proc/meminfo") as f:
            avail = next(int(x.split()[1]) for x in f if x.startswith("MemAvailable")) // 1024
    except OSError:
        return workers
    return max(1, min(workers, (avail - RESERVE_MB) // (WORKER_MB + table_mb)))


class Forced:
    """Depth-first forced-win search from the match's current state. A move in flight is (name, frame, toward).
    Decided positions are remembered by hash, in at most `table_mb` of memory; the table is emptied when full."""

    def __init__(self, match: Match, rules: Rules, table_mb: int = 400):
        self.m, self.rules = match, rules
        a, b = match.players()
        self.base = (float(a["percent"]), float(b["percent"]), int(a["stocks"]), int(b["stocks"]))
        self.for_attacker: dict[int, int] = {}  # fewest frames in which the position is proven good for P1
        self.for_defender: dict[int, int] = {}  # most frames for which it is proven good for P2
        self.cap = table_mb * 1_000_000 // TABLE_BYTES
        self.positions = 0

    def play(self, pa, pd):
        """Play P1 move `pa` against P2 move `pd` until one of them ends or someone is hit. Returns
        (outcome, frames used, P1's unfinished move, P2's unfinished move)."""
        self.positions += 1
        arows, drows = self.rules.moves[pa[0]], self.rules.moves[pd[0]]
        out = used = 0
        for f in range(min(len(arows) - pa[1], len(drows) - pd[1])):
            b, x, y, cx, cy, l, r = arows[pa[1] + f]
            db, dx, dy, dcx, dcy, dl, dr = drows[pd[1] + f]
            self.m.step((b, x * pa[2], y, cx * pa[2], cy, l, r), (db, dx * pd[2], dy, dcx * pd[2], dcy, dl, dr))
            used += 1
            out = first_hit(self.base, *self.m.players())
            if out:
                break
        ra = (pa[0], pa[1] + used, pa[2]) if pa[1] + used < len(arows) else None
        rd = (pd[0], pd[1] + used, pd[2]) if pd[1] + used < len(drows) else None
        return out, used, ra, rd

    def options(self, pa=None, pd=None):
        a, b = self.m.players()
        toward = 1 if float(b["pos_x"]) >= float(a["pos_x"]) else -1
        att = [pa] if pa else [(n, 0, toward) for n in self.rules.menu(a)]
        dfn = [pd] if pd else [(n, 0, -toward) for n in self.rules.menu(b)]
        return att, dfn

    def forget(self) -> None:
        self.for_attacker.clear()
        self.for_defender.clear()

    def _remember(self, key: int, frames: int, attacker: bool) -> None:
        if len(self.for_attacker) + len(self.for_defender) >= self.cap:
            self.forget()
        (self.for_attacker if attacker else self.for_defender)[key] = frames

    def known_win(self, pa) -> bool:
        return self.rules.won is not None and pa is None and self.rules.won(*self.m.players())

    def win(self, frames: int, pa=None, pd=None, depth: int = 0) -> bool:
        return self._search(frames, pa, pd, depth, True)

    def safe(self, frames: int, pa=None, pd=None, depth: int = 0) -> bool:
        return self._search(frames, pa, pd, depth, False)

    def _search(self, frames: int, pa, pd, depth: int, attacking: bool) -> bool:
        """The answer to the question: with `attacking`, is the first hit forced for P1; without, can P2 always
        avoid it? The side asked about picks first and the other answers. Leaves the state as it found it."""
        if frames <= 0:
            return not attacking
        if self.known_win(pa):
            return attacking
        a, b = self.m.players()
        key = hash(_key(a) + _key(b) + (pa, pd, attacking))
        if frames >= self.for_attacker.get(key, 10 ** 6):  # more frames in hand only help the attacker
            return attacking
        if frames <= self.for_defender.get(key, -1):
            return not attacking
        att, dfn = self.options(pa, pd)
        mine, theirs = (att, dfn) if attacking else (dfn, att)
        good = 1 if attacking else -1  # the outcome that settles a line for the side asked about
        pair = (lambda x, y: (x, y)) if attacking else (lambda x, y: (y, x))
        self.m.checkpoint(depth)
        for x in mine:
            kids = []
            for y in theirs:  # look at every answer first: one that wins at once for them refutes the move
                kids.append(self.play(*pair(x, y)))
                self.m.rollback(depth)
                if kids[-1][0] == -good:
                    break
            if kids[-1][0] == -good:
                continue
            ok = True
            for y, (o, u, ra, rd) in zip(theirs, kids):
                if o == good:
                    continue
                self.play(*pair(x, y))
                ok = self._search(frames - u, ra, rd, depth + 1, attacking)
                self.m.rollback(depth)
                if not ok:
                    break
            if ok:
                self._remember(key, frames, attacking)
                return True
        self._remember(key, frames, not attacking)
        return False


# --- many processes: one job per (root joint move, first picker's next move)
_F: Forced | None = None
_CFG: dict = {}


def _load(path: str):
    module, name = path.split(":")
    return getattr(importlib.import_module(module), name)


def _init(root: bytes, cfg: dict) -> None:
    global _F, _CFG
    m = Match(*cfg["match"], data_dir=cfg["data_dir"])
    m.load(root)
    m.learn()
    _F, _CFG = Forced(m, _load(cfg["rules"]), cfg["table_mb"]), cfg


def _clean(run):
    """Run, and repeat if a rollback missed a page: the table of proven positions may then hold wrong entries."""
    while True:
        before = _F.m.missed
        out = run()
        if _F.m.missed == before:
            return out
        _F.forget()


def _open(job):
    def run():
        a, d = job
        attacking = _CFG["attacking"]
        _F.m.checkpoint(0)
        o, u, ra, rd = _F.play(a, d)
        known = o == 0 and _F.known_win(ra)
        hit = 1 if (o > 0 or known) else o
        done = None if hit == 0 else (hit > 0) == attacking
        nxt = [] if done is not None else _F.options(ra, rd)[0 if attacking else 1]
        _F.m.rollback(0)
        return a, d, done, nxt
    return _clean(run)


def _task(job):
    def run():
        a, d, m = job
        attacking, frames = _CFG["attacking"], _CFG["frames"]
        _F.m.checkpoint(0)
        o, u, ra, rd = _F.play(a, d)
        att, dfn = _F.options(m if attacking else ra, rd if attacking else m)
        _F.m.checkpoint(1)
        ok = True
        for a2 in att:
            for d2 in dfn:
                o2, u2, ra2, rd2 = _F.play(a2, d2)
                if o2 == 0:
                    ok = _F._search(frames - u - u2, ra2, rd2, 2, attacking)
                else:
                    ok = (o2 > 0) == attacking
                _F.m.rollback(1)
                if not ok:
                    break
            if not ok:
                break
        _F.m.rollback(0)
        n, _F.positions = _F.positions, 0
        return a, d, m, ok, n
    return _clean(run)


def solve(root: bytes, rules: str, frames: int, mode: str = "win", workers: int | None = None,
          match: tuple = ("FOX", "FOX", "FINAL_DESTINATION"), data_dir=None, table_mb: int = 400,
          out: str | None = None, tag: str = "") -> dict:
    """Run `Forced` from savestate `root` on many processes. `rules` is "module:NAME" of a `Rules` object, so
    each worker can import it. `mode` is "win" or "safe". Returns which first moves hold and what beats the rest.

    Memory is bounded: each process keeps at most `table_mb` of decided positions, and the process count is cut
    to what the free memory holds. Finished jobs are appended to the file `out`; a later call with the same
    `out`, question and `tag` (name the root position with it) skips them."""
    t0 = time.time()
    attacking = mode == "win"
    workers = fit_workers(workers or mp.cpu_count(), table_mb)
    cfg = {"match": match, "data_dir": data_dir, "rules": rules, "frames": frames, "attacking": attacking,
           "table_mb": table_mb}
    ask = {"rules": rules, "frames": frames, "mode": mode, "match": list(match), "tag": tag}
    old: dict[str, tuple[bool, int]] = {}
    if out and os.path.exists(out):
        with open(out) as f:
            for row in map(json.loads, f):
                if row["ask"] == ask:
                    old[json.dumps(row["job"])] = (row["ok"], row["n"])
    log = open(out, "a") if out else None
    m = Match(*match, data_dir=data_dir)
    m.load(root)
    att, dfn = Forced(m, _load(rules)).options()
    del m
    result: dict[tuple, bool] = {}
    total = 0
    with mp.get_context("spawn").Pool(workers, initializer=_init, initargs=(root, cfg)) as pool:
        jobs = []
        for a, d, done, nxt in pool.imap_unordered(_open, [(a, d) for a in att for d in dfn]):
            result[(a, d)] = bool(done)
            jobs += [(a, d, x) for x in nxt] if done is None else []
        np.random.default_rng(0).shuffle(jobs)
        todo = []
        for a, d, x in jobs:
            if (seen := old.get(json.dumps([a, d, x]))) is None:
                todo.append((a, d, x))
            else:
                result[(a, d)] = result[(a, d)] or seen[0]
                total += seen[1]
        for a, d, x, ok, n in pool.imap_unordered(_task, todo):
            result[(a, d)] = result[(a, d)] or ok
            total += n
            if log:
                log.write(json.dumps({"ask": ask, "job": [a, d, x], "ok": ok, "n": n}) + "\n")
                log.flush()
    if log:
        log.close()
    first, second = (0, 1) if attacking else (1, 0)
    table: dict[str, dict[str, bool]] = {}
    for pair, ok in result.items():
        table.setdefault(pair[first][0], {})[pair[second][0]] = ok
    return {"mode": mode, "frames": frames, "holds": [x for x, row in table.items() if all(row.values())],
            "beaten_by": {x: [y for y, ok in row.items() if not ok] for x, row in table.items()},
            "positions": total, "seconds": round(time.time() - t0, 1), "workers": workers,
            "jobs": len(jobs), "resumed": len(jobs) - len(todo)}
