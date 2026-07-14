import errno
import fcntl
import os
from contextlib import contextmanager
from pathlib import Path


class RunAlreadyActive(RuntimeError):
    pass


class InvalidInheritedLock(RuntimeError):
    pass


def _validate_inherited_fd(path: Path, inherited_fd: int) -> int:
    try:
        fd = int(inherited_fd)
        fd_stat = os.fstat(fd)
        path_stat = path.stat()
    except (OSError, TypeError, ValueError) as exc:
        raise InvalidInheritedLock(f"继承发布锁无效: {path}") from exc
    if (fd_stat.st_dev, fd_stat.st_ino) != (path_stat.st_dev, path_stat.st_ino):
        raise InvalidInheritedLock(f"继承发布锁无效: {path}")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise InvalidInheritedLock(f"继承发布锁无效: {path}") from exc
    return fd


@contextmanager
def run_lock(path: Path, *, inherited_fd: int | None = None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if inherited_fd is not None:
        yield _validate_inherited_fd(path, inherited_fd)
        return
    handle = path.open("a+")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            raise RunAlreadyActive(f"publish run already active: {path}") from exc
        try:
            yield handle.fileno()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()
