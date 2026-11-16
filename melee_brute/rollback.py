"""In-place checkpoint and rollback of one simulator match.

A frame changes about 3 KB of a match's 3.2 MB of memory, spread over a few dozen 4 KiB pages, always the same
ones. A checkpoint copies only the pages known to change ("dirty" pages) and a rollback copies them back: about
9 microseconds, against 300 for `EnvBatch.copy_matches_from`, which moves everything and re-links every pointer.
A rollback goes back to the same address, so no pointers need fixing.

The dirty pages are learned by playing random inputs (`Match.learn`), which can miss a rarely written page. So
exactness is checked, not assumed. The reference is a full copy of the state at the root of the search. A rollback
never restores the pages outside the dirty set, so one that changed stays changed until `verify()` finds it, and
`restore()` puts it back from the reference. `Match` does this on every rollback to the root: the root state is
always exact, and a miss is counted so the search can be repeated with the page added.
"""
from __future__ import annotations

import ctypes

import numpy as np

PAGE = 4096
_U32P = ctypes.POINTER(ctypes.c_uint32)


class Rollback:
    def __init__(self, env, lane: int = 0, depth: int = 256):
        from melee_sim import _native
        self.lib, self.h, self.lane = _native.library(), env._handle, lane
        for name in ("checkpoint_size", "checkpoint", "rollback", "diff_pages"):
            getattr(self.lib, f"msl_batch_{name}").restype = ctypes.c_int
        size = ctypes.c_size_t()
        self._ok(self.lib.msl_batch_checkpoint_size(self.h, lane, ctypes.byref(size)))
        self.size, self.npages = size.value, size.value // PAGE + 1
        self.base = (ctypes.c_uint8 * self.size)()
        self.stack = [None] * depth  # buffers are made on first use; only their dirty pages are ever touched
        self.pages = np.zeros(0, dtype=np.uint32)
        self._ptr = self.pages.ctypes.data_as(_U32P)
        self.rebase()

    @staticmethod
    def _ok(code: int) -> None:
        if code != 0:
            raise RuntimeError(f"simulator checkpoint call failed ({code})")

    def rebase(self) -> None:
        """Take the current state as the reference `verify()` and `reset()` use."""
        self._ok(self.lib.msl_batch_checkpoint(self.h, self.lane, self.base, self.size, None, 0))

    def changed(self) -> np.ndarray:
        """Pages that differ from the reference state right now."""
        flags = (ctypes.c_uint8 * self.npages)()
        self._ok(self.lib.msl_batch_diff_pages(self.h, self.lane, self.base, self.size, flags, self.npages))
        return np.flatnonzero(np.frombuffer(flags, dtype=np.uint8)).astype(np.uint32)

    def add(self, pages: np.ndarray) -> None:
        self.pages = np.union1d(self.pages, pages).astype(np.uint32)
        self._ptr = self.pages.ctypes.data_as(_U32P)

    def reset(self) -> None:
        """Back to the reference state, every page."""
        self._ok(self.lib.msl_batch_rollback(self.h, self.lane, self.base, self.size, None, 0))

    def checkpoint(self, level: int) -> None:
        if self.stack[level] is None:
            self.stack[level] = (ctypes.c_uint8 * self.size)()
        self._ok(self.lib.msl_batch_checkpoint(self.h, self.lane, self.stack[level], self.size, self._ptr,
                                               len(self.pages)))

    def rollback(self, level: int) -> None:
        self._ok(self.lib.msl_batch_rollback(self.h, self.lane, self.stack[level], self.size, self._ptr,
                                             len(self.pages)))

    def restore(self, pages: np.ndarray) -> None:
        """Copy these pages back from the reference state."""
        pages = np.ascontiguousarray(pages, dtype=np.uint32)
        self._ok(self.lib.msl_batch_rollback(self.h, self.lane, self.base, self.size,
                                             pages.ctypes.data_as(_U32P), len(pages)))

    def verify(self) -> np.ndarray:
        """Pages outside the dirty set that no longer match the reference: empty when every rollback so far
        was exact."""
        return np.setdiff1d(self.changed(), self.pages)
