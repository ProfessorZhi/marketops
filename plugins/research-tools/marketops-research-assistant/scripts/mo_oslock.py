"""One file lock for every computer: fcntl on Linux and macOS, msvcrt on Windows.

Each agent runs its own server process, so the project files (tasks.csv above all) are shared
between processes. Before 0.8.1 the lock was fcntl only: on Windows it fell back to threads, and
two agents could claim the same package or lose each other's writes.
"""
from __future__ import annotations

import time

try:
    import fcntl
except ImportError:   # Windows
    fcntl = None
try:
    import msvcrt
except ImportError:   # Linux, macOS
    msvcrt = None


class Busy(RuntimeError):
    """Another call holds the file longer than this one may wait."""


def acquire(path, block: bool = True, timeout: float | None = None):
    """Open <path> and lock it for this process; OSError when block=False and it is taken, Busy
    when it is still taken after `timeout` seconds."""
    fh = open(path, "a+")
    try:
        if fcntl is not None and block and timeout is None:
            fcntl.flock(fh, fcntl.LOCK_EX)
            return fh
        end = None if timeout is None else time.time() + timeout
        while True:
            try:
                if fcntl is not None:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                elif msvcrt is not None:
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                return fh
            except OSError:
                if not block:
                    raise
                if end is not None and time.time() >= end:
                    raise Busy(f"{path} is held by another call for over {timeout:.0f} s") from None
                time.sleep(0.05)
    except BaseException:
        fh.close()
        raise


def release(fh) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(fh, fcntl.LOCK_UN)
        elif msvcrt is not None:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    finally:
        fh.close()
