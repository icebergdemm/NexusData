"""Small cross-platform advisory file-lock API used by background workers."""

import os


if os.name == "nt":
    import msvcrt

    LOCK_EX = 1
    LOCK_NB = 2
    LOCK_UN = 8

    def flock(fd: int, operation: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
            os.fsync(fd)
            os.lseek(fd, 0, os.SEEK_SET)
        if operation & LOCK_UN:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            return
        mode = msvcrt.LK_NBLCK if operation & LOCK_NB else msvcrt.LK_LOCK
        try:
            msvcrt.locking(fd, mode, 1)
        except PermissionError as exc:
            if operation & LOCK_NB:
                raise BlockingIOError from exc
            raise
else:
    import fcntl as _fcntl

    LOCK_EX = _fcntl.LOCK_EX
    LOCK_NB = _fcntl.LOCK_NB
    LOCK_UN = _fcntl.LOCK_UN

    def flock(fd: int, operation: int) -> None:
        _fcntl.flock(fd, operation)


def restrict_mode(path, mode: int) -> None:
    """Apply Unix permission bits where they exist; Windows uses ACLs."""
    if os.name == "posix":
        os.chmod(path, mode)
