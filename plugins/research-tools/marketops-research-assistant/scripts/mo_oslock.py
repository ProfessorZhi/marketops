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


def acquire(path, block: bool = True):
    """Open <path> and lock it for this process; OSError when block=False and it is taken."""
    fh = open(path, "a+")
    try:
        if fcntl is not None:
            fcntl.flock(fh, fcntl.LOCK_EX | (0 if block else fcntl.LOCK_NB))
        elif msvcrt is not None:
            fh.seek(0)
            while True:   # LK_LOCK gives up after ten seconds; a writer may hold it longer
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if not block:
                        raise
                    time.sleep(0.05)
        return fh
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
