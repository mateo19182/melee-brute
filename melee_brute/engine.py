"""ctypes bindings for the simulator's search calls (`msl_brute_*`, see src/api.h)."""
from __future__ import annotations

import ctypes
from functools import lru_cache

MAX_MOVES, MAX_MOVE_FRAMES, MAX_MENUS, MAX_MENU_MOVES, ACTIONS, MAX_ZONES = 64, 512, 16, 32, 1024, 8
NO_MOVE = -1


class Pad(ctypes.Structure):
    _fields_ = [("buttons", ctypes.c_uint16), ("main_x", ctypes.c_int8), ("main_y", ctypes.c_int8),
                ("c_x", ctypes.c_int8), ("c_y", ctypes.c_int8), ("l", ctypes.c_uint8), ("r", ctypes.c_uint8)]


class ZoneData(ctypes.Structure):
    _fields_ = [("attacker_wins", ctypes.c_uint8), ("same_y", ctypes.c_uint8), ("either_side", ctypes.c_uint8),
                ("_reserved", ctypes.c_uint8), ("gap_min", ctypes.c_float), ("gap_max", ctypes.c_float),
                ("x_min", ctypes.c_float), ("x_max", ctypes.c_float), ("attacker", ctypes.c_uint8 * ACTIONS),
                ("defender", ctypes.c_uint8 * ACTIONS)]


class RulesData(ctypes.Structure):
    _fields_ = [("move_count", ctypes.c_uint32), ("menu_count", ctypes.c_uint32),
                ("move_start", ctypes.c_uint16 * (MAX_MOVES + 1)), ("frames", Pad * MAX_MOVE_FRAMES),
                ("menu_size", ctypes.c_uint8 * MAX_MENUS),
                ("menu_moves", (ctypes.c_uint8 * MAX_MENU_MOVES) * MAX_MENUS),
                ("action_menu", ctypes.c_uint8 * ACTIONS), ("trap", ctypes.c_uint8),
                ("trap_same_y", ctypes.c_uint8), ("_reserved", ctypes.c_uint8 * 2), ("trap_reach", ctypes.c_float),
                ("trap_attacker", ctypes.c_uint8 * ACTIONS), ("trap_defender", ctypes.c_uint8 * ACTIONS),
                ("caught", ctypes.c_uint8 * ACTIONS), ("defender_menu", ctypes.c_uint8 * ACTIONS),
                ("timeout_attacker", ctypes.c_uint8), ("zone_count", ctypes.c_uint8),
                ("defender_menus", ctypes.c_uint8), ("_reserved2", ctypes.c_uint8), ("zones", ZoneData * MAX_ZONES)]


class Play(ctypes.Structure):
    _fields_ = [("outcome", ctypes.c_int32), ("frames", ctypes.c_int32), ("attacker", ctypes.c_int32),
                ("defender", ctypes.c_int32)]


class Stats(ctypes.Structure):
    _fields_ = [("positions", ctypes.c_uint64), ("frames", ctypes.c_uint64), ("nodes", ctypes.c_uint64),
                ("table_hits", ctypes.c_uint64), ("table_stores", ctypes.c_uint64),
                ("dirty_pages", ctypes.c_uint32), ("_reserved", ctypes.c_uint32)]


@lru_cache(maxsize=1)
def library() -> ctypes.CDLL:
    from melee_sim import _native
    lib = _native.library()
    p, i32, u32 = ctypes.c_void_p, ctypes.c_int32, ctypes.c_uint32
    for name, args in (
            ("create", [p, u32, u32, ctypes.POINTER(p)]), ("reset", [p]), ("checkpoint", [p, u32]),
            ("rollback", [p, u32]), ("hash", [p, ctypes.POINTER(ctypes.c_uint64 * 2)]), ("step", [p, p]),
            ("begin", [p, ctypes.POINTER(RulesData), p, ctypes.c_size_t, ctypes.c_int]),
            ("play", [p, i32, i32, ctypes.POINTER(Play)]),
            ("options", [p, i32, i32, ctypes.POINTER(i32 * MAX_MENU_MOVES), ctypes.POINTER(u32),
                         ctypes.POINTER(i32 * MAX_MENU_MOVES), ctypes.POINTER(u32)]),
            ("trapped", [p, ctypes.POINTER(i32)]), ("ruled", [p, i32, i32, i32, ctypes.POINTER(i32)]),
            ("search", [p, i32, i32, i32, u32, ctypes.c_int, u32, ctypes.POINTER(i32)]),
            ("stats", [p, ctypes.POINTER(Stats)])):
        fn = getattr(lib, "msl_brute_" + name)
        fn.argtypes, fn.restype = args, ctypes.c_int
    lib.msl_brute_destroy.argtypes, lib.msl_brute_destroy.restype = [p], None
    return lib


def ok(code: int) -> None:
    if code != 0:
        raise RuntimeError(f"simulator search call failed ({code})")
