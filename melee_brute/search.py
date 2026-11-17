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

The forced search itself runs inside the simulator (src/runtime/brute.c). Positions it has decided are kept in a
table keyed by a hash of the whole match state, so two positions share an answer only when they are the same.
"""
from __future__ import annotations

import ctypes
import json
import mmap
import multiprocessing as mp
import os
import queue
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping, Sequence

import numpy as np

from melee_brute import engine
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
    return go(depth)


def improve(match: Match, line: Sequence, choices: Sequence, score: Callable[[Match], float],
            tries: int = 2000, seed: int = 0, play: Callable[[Match, object], None] | None = None,
            every: int = 8) -> tuple[float, list]:
    """Improve an existing line by random changes, the way TMInterface and the SM64 bruteforcers do: change
    one choice, replay from the nearest checkpoint, keep the change if `score(match)` at the end got better.
    Use it when `best` is out of reach: a line of 300 frames has too many variations to try them all.
    Leaves the match where it started; returns the best score and line."""
    play = play or (lambda m, c: m.step(c))
    line = list(line)
    marks = range(0, len(line), every)  # checkpoint levels 1.. hold the state before these positions

    def run(first: int) -> float:
        for i in range(first, len(line)):
            if i % every == 0:
                match.checkpoint(1 + i // every)
            play(match, line[i])
        return score(match)

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


@dataclass(frozen=True)
class Trap:
    """A position the attacker is known to win, used as a shortcut: the defender's action is in `defender`,
    the attacker's is in `attacker` and he is free to pick, and they stand within `reach` of each other
    (`same_y`: at the same height)."""
    attacker: frozenset[int]
    defender: frozenset[int]
    reach: float
    same_y: bool = True


@dataclass(frozen=True)
class Zone:
    """A set of positions whose result is known, so a line that reaches one stops there: both fighters free to
    pick, their actions in `attacker` and `defender`, P2's x minus P1's within `gap` (with `either_side`, its
    size alone), P2's x within `x`, and (`same_y`) at the same height. `winner` is "attacker" or "defender". Use it to chain searches: what one
    search proved about a set of positions is a zone of the next."""
    winner: str
    attacker: frozenset[int]
    defender: frozenset[int]
    gap: tuple[float, float] = (-1e9, 1e9)
    x: tuple[float, float] = (-1e9, 1e9)
    same_y: bool = True
    either_side: bool = False


@dataclass(frozen=True)
class Rules:
    """`moves`: name -> one Input per frame, stick x written as if the opponent were to the right.
    `menus`: menu name -> the moves a fighter with that menu may pick. `actions`: menu name -> the action ids
    that get it (the first menu listing an id wins); every other action gets the menu `otherwise`.
    `defender`: menus by name that P2 gets instead of the ones in `menus`, when the two sides may not do the same.
    `caught`: action ids that count as having been hit (a grab moves no percent). `zones`: positions whose
    result is known. `timeout`: who gets a line whose frames run out, "defender" (he only has to last) or
    "attacker" (the defender has to reach one of his zones in time)."""
    moves: Mapping[str, tuple[Input, ...]]
    menus: Mapping[str, tuple[str, ...]]
    actions: Mapping[str, Iterable[int]]
    otherwise: str = "locked"
    trap: Trap | None = None
    defender: Mapping[str, tuple[str, ...]] | None = None
    caught: frozenset[int] = frozenset()
    zones: tuple[Zone, ...] = ()
    timeout: str = "defender"
    names: tuple[str, ...] = field(init=False, compare=False)
    data: engine.RulesData = field(init=False, compare=False, repr=False)

    def __post_init__(self):
        names = tuple(self.moves)
        theirs = dict(self.defender or {})  # P2's own menus are stored after the shared ones
        menus = tuple(self.menus)
        picks_of = list(self.menus.values()) + list(theirs.values())
        d = engine.RulesData()
        if (len(names) > engine.MAX_MOVES or len(picks_of) > engine.MAX_MENUS
                or sum(map(len, self.moves.values())) > engine.MAX_MOVE_FRAMES
                or max(map(len, picks_of)) > engine.MAX_MENU_MOVES or set(theirs) - set(menus)):
            raise ValueError("too many moves, frames or menus for the search, or a defender menu with a new name")
        d.move_count, d.menu_count, d.defender_menus = len(names), len(picks_of), bool(theirs)
        at = 0
        for i, rows in enumerate(self.moves.values()):
            d.move_start[i] = at
            for row in rows:
                d.frames[at] = engine.Pad(*row)
                at += 1
        d.move_start[len(names)] = at
        for i, picks in enumerate(picks_of):
            d.menu_size[i] = len(picks)
            for j, name in enumerate(picks):
                d.menu_moves[i][j] = names.index(name)
        second = {name: len(menus) + i for i, name in enumerate(theirs)}
        for a in range(engine.ACTIONS):
            d.action_menu[a] = menus.index(self.otherwise)
            d.defender_menu[a] = second.get(self.otherwise, d.action_menu[a])
        for name in reversed(tuple(self.actions)):
            for a in self.actions[name]:
                d.action_menu[a] = menus.index(name)
                d.defender_menu[a] = second.get(name, d.action_menu[a])
        if len(self.zones) > engine.MAX_ZONES or self.timeout not in ("defender", "attacker"):
            raise ValueError("at most 8 zones, and timeout is 'defender' or 'attacker'")
        for a in self.caught:
            d.caught[a] = 1
        d.timeout_attacker, d.zone_count = self.timeout == "attacker", len(self.zones)
        for z, zone in zip(d.zones, self.zones):
            z.attacker_wins, z.same_y, z.either_side = zone.winner == "attacker", zone.same_y, zone.either_side
            (z.gap_min, z.gap_max), (z.x_min, z.x_max) = zone.gap, zone.x
            for a in zone.attacker:
                z.attacker[a] = 1
            for a in zone.defender:
                z.defender[a] = 1
        if self.trap:
            d.trap, d.trap_same_y, d.trap_reach = 1, self.trap.same_y, self.trap.reach
            for a in self.trap.attacker:
                d.trap_attacker[a] = 1
            for a in self.trap.defender:
                d.trap_defender[a] = 1
        object.__setattr__(self, "names", names)
        object.__setattr__(self, "data", d)


def first_hit(base: tuple, a, b) -> int:
    """+1 P1 landed the first hit, -1 P1 was hit or it was a trade, 0 nobody hit yet. `base` is
    (percent 1, percent 2, stocks 1, stocks 2) at the root."""
    d1 = float(a["percent"]) - base[0] + 100 * (base[2] - int(a["stocks"]))
    d2 = float(b["percent"]) - base[1] + 100 * (base[3] - int(b["stocks"]))
    return 0 if d1 == 0 and d2 == 0 else 1 if d2 > d1 else -1


WORKER_MB = 200  # a forked search process: its checkpoints and the pages it has written
RESERVE_MB = 4_000  # memory left for everything else on the machine


def fit_workers(workers: int, table_mb: int) -> int:
    """As many processes as the free memory holds next to the table (Linux; elsewhere `workers`)."""
    try:
        with open("/proc/meminfo") as f:
            avail = next(int(x.split()[1]) for x in f if x.startswith("MemAvailable")) // 1024
    except OSError:
        return workers
    return max(1, min(workers, (avail - RESERVE_MB - table_mb) // WORKER_MB))


class Forced:
    """Forced-first-hit search from the match's current state, whose percents and stocks are the ones a hit is
    measured against. A move in flight is (name, frame, toward). Decided positions are kept in `table`, a
    shared `mmap` that forked processes search with together, or in a private one of `table_mb`.

    With `exact` a decided position answers only for the very same state. Without, it answers for every state
    that looks the same from outside (place, speeds, action and its frame, shield): many times fewer positions,
    but what a fighter was holding is not part of that look, so an answer can be wrong.

    A match searches for one `Forced` at a time: making another for the same match retires this one."""

    def __init__(self, match: Match, rules: Rules, table_mb: int = 256, table: mmap.mmap | None = None,
                 exact: bool = True):
        self.m, self.rules = match, rules
        self.table = table if table is not None else mmap.mmap(-1, table_mb << 20)
        self._memory = (ctypes.c_char * len(self.table)).from_buffer(self.table)
        self._play, self._count = engine.Play(), ctypes.c_uint32()
        self._att = (ctypes.c_int32 * engine.MAX_MENU_MOVES)()
        self._dfn = (ctypes.c_int32 * engine.MAX_MENU_MOVES)()
        self._natt, self._ndfn, self._holds = ctypes.c_uint32(), ctypes.c_uint32(), ctypes.c_int32()
        engine.ok(match.lib.msl_brute_begin(match.brute, ctypes.byref(rules.data), self._memory, len(self.table),
                                            exact))
        match.forced = self

    def _mine(self) -> None:
        if self.m.forced is not self:
            raise RuntimeError("this match has since been given another Forced")

    def _pack(self, move) -> int:
        if move is None:
            return engine.NO_MOVE
        name, frame, toward = move
        return self.rules.names.index(name) | frame << 8 | (1 << 24 if toward < 0 else 0)

    def _unpack(self, move: int):
        return None if move < 0 else (self.rules.names[move & 0xFF], move >> 8 & 0xFFFF, -1 if move >> 24 else 1)

    def stats(self) -> engine.Stats:
        self._mine()
        s = engine.Stats()
        engine.ok(self.m.lib.msl_brute_stats(self.m.brute, ctypes.byref(s)))
        return s

    @property
    def positions(self) -> int:
        return self.stats().positions

    def play(self, pa, pd):
        """Play P1 move `pa` against P2 move `pd` until one of them ends or someone is hit. Returns
        (outcome, frames used, P1's unfinished move, P2's unfinished move)."""
        self._mine()
        p = self._play
        engine.ok(self.m.lib.msl_brute_play(self.m.brute, self._pack(pa), self._pack(pd), ctypes.byref(p)))
        return p.outcome, p.frames, self._unpack(p.attacker), self._unpack(p.defender)

    def options(self, pa=None, pd=None):
        self._mine()
        engine.ok(self.m.lib.msl_brute_options(
            self.m.brute, self._pack(pa), self._pack(pd), ctypes.byref(self._att), ctypes.byref(self._natt),
            ctypes.byref(self._dfn), ctypes.byref(self._ndfn)))
        return ([self._unpack(x) for x in self._att[:self._natt.value]],
                [self._unpack(x) for x in self._dfn[:self._ndfn.value]])

    def ruled(self, frames: int, pa=None, pd=None) -> bool | None:
        """Whose this position is by the rules alone (zones, frames run out, trap) with nobody hit: True the
        attacker's, False the defender's, None it has to be played on."""
        self._mine()
        engine.ok(self.m.lib.msl_brute_ruled(self.m.brute, frames, self._pack(pa), self._pack(pd),
                                             ctypes.byref(self._holds)))
        return None if self._holds.value < 0 else bool(self._holds.value)

    def win(self, frames: int, pa=None, pd=None, depth: int = 0) -> bool:
        return self._search(frames, pa, pd, depth, True)

    def safe(self, frames: int, pa=None, pd=None, depth: int = 0) -> bool:
        return self._search(frames, pa, pd, depth, False)

    def _search(self, frames: int, pa, pd, depth: int, attacking: bool, spread: int = 0) -> bool:
        """With `attacking`, is the first hit forced for P1; without, can P2 always avoid it? The side asked
        about picks first and the other answers. Uses checkpoint levels from `depth` up and leaves the state
        as it found it. Processes searching the same position over one table pass different `spread`."""
        self._mine()
        engine.ok(self.m.lib.msl_brute_search(self.m.brute, frames, self._pack(pa), self._pack(pd), depth,
                                              attacking, spread, ctypes.byref(self._holds)))
        return bool(self._holds.value)


# --- many processes, forked from one that holds the root: one job per (root joint move, first picker's next move)
_F: Forced | None = None
_CFG: dict = {}


def _open(job):
    """Play a root joint move: is the line decided at once, and if not, what may the first picker play next?"""
    a, d = job
    attacking, frames = _CFG["attacking"], _CFG["frames"]
    _F.m.checkpoint(0)
    o, u, ra, rd = _F.play(a, d)
    whose = None if o else _F.ruled(frames - u, ra, rd)
    if o:
        done, nxt = (o > 0) == attacking, []
    elif whose is not None:
        done, nxt = whose == attacking, []
    else:
        done, nxt = None, _F.options(ra, rd)[0 if attacking else 1]
    _F.m.rollback(0)
    return a, d, done, nxt


def _task(job):
    """After the root joint move (a, d), does the first picker's move `m` hold against every answer? `spread`
    is 0 for the process that owns the job and counts up for the ones that help it."""
    a, d, m, spread = job
    attacking, frames = _CFG["attacking"], _CFG["frames"]
    before = _F.positions
    _F.m.checkpoint(0)
    o, u, ra, rd = _F.play(a, d)
    att, dfn = _F.options(m if attacking else ra, rd if attacking else m)
    pairs = [(a2, d2) for a2 in att for d2 in dfn]
    turn = spread * (len(pairs) // 2 + 1) % len(pairs)
    _F.m.checkpoint(1)
    ok = True
    for a2, d2 in pairs[turn:] + pairs[:turn]:
        o2, u2, ra2, rd2 = _F.play(a2, d2)
        ok = (o2 > 0) == attacking if o2 else _F._search(frames - u - u2, ra2, rd2, 2, attacking, spread)
        _F.m.rollback(1)
        if not ok:
            break
    _F.m.rollback(0)
    return a, d, m, ok, _F.positions - before


def solve(root: bytes, rules: Rules, frames: int, mode: str = "win", workers: int | None = None,
          match: tuple = ("FOX", "FOX", "FINAL_DESTINATION"), data_dir=None, table_mb: int = 2048,
          out: str | None = None, tag: str = "", exact: bool = True) -> dict:
    """Run `Forced` from savestate `root` on many processes. `mode` is "win" or "safe". Returns which first
    moves hold and what beats the rest.

    The processes are forked from one that holds the root, so they share the game data and one table of
    decided positions, `table_mb` in all. The process count is cut to what the free memory holds. Finished
    jobs are appended to the file `out`; a later call with the same `out`, question and `tag` (name the root
    position and the rules with it) skips them. `exact` is as in `Forced`."""
    global _F, _CFG
    t0 = time.time()
    attacking = mode == "win"
    workers = fit_workers(workers or mp.cpu_count(), table_mb)
    ask = {"frames": frames, "mode": mode, "match": list(match), "tag": tag, "exact": exact}
    old: dict[str, tuple[bool, int]] = {}
    if out and os.path.exists(out):
        with open(out) as f:
            for row in map(json.loads, f):
                if row["ask"] == ask:
                    old[json.dumps(row["job"])] = (row["ok"], row["n"])
    log = open(out, "a") if out else None
    m = Match(*match, data_dir=data_dir)
    m.load(root)
    _F = Forced(m, rules, table=mmap.mmap(-1, table_mb << 20), exact=exact)
    _CFG = {"attacking": attacking, "frames": frames}
    att, dfn = _F.options()
    result: dict[tuple, bool] = {}
    total = 0
    try:
        with mp.get_context("fork").Pool(workers) as pool:
            jobs = []
            for a, d, done, nxt in pool.imap_unordered(_open, [(a, d) for a in att for d in dfn]):
                result[(a, d)] = bool(done)
                jobs += [(a, d, x) for x in nxt]
            np.random.default_rng(0).shuffle(jobs)
            todo = []
            for a, d, x in jobs:
                if (seen := old.get(json.dumps([a, d, x]))) is None:
                    todo.append((a, d, x))
                else:
                    result[(a, d)] = result[(a, d)] or seen[0]
                    total += seen[1]
            # Each job goes to one process. A process with nothing left to start helps the unfinished job
            # that has the fewest: it searches the same position from other moves, through the same table.
            done: queue.SimpleQueue = queue.SimpleQueue()
            fresh, helpers, flying = list(reversed(todo)), {job: 0 for job in todo}, 0
            while helpers:
                while flying < workers and (fresh or helpers):
                    job = fresh.pop() if fresh else min(helpers, key=helpers.get)
                    pool.apply_async(_task, ((*job, helpers[job]),), callback=done.put, error_callback=done.put)
                    helpers[job] += 1
                    flying += 1
                got = done.get()
                flying -= 1
                if isinstance(got, BaseException):
                    raise got
                a, d, x, ok, n = got
                total += n
                if helpers.pop((a, d, x), None) is not None:
                    result[(a, d)] = result[(a, d)] or ok
                    if log:
                        log.write(json.dumps({"ask": ask, "job": [a, d, x], "ok": ok, "n": n}) + "\n")
                        log.flush()
    finally:
        _F = None
        m.close()
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
