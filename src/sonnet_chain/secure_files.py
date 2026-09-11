from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path


class SecureFileError(RuntimeError):
    pass


def read_private_file(path: Path, max_bytes: int) -> bytes:
    """Read an owner-only regular file without following a final symlink."""
    if not path.is_absolute():
        raise SecureFileError("secret file path must be absolute")
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise SecureFileError("secret file is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise SecureFileError("secret path is not a regular file")
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise SecureFileError("secret file owner or permissions are unsafe")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        try:
            opened = os.fstat(fd)
            if opened.st_dev != info.st_dev or opened.st_ino != info.st_ino:
                raise SecureFileError("secret file changed while opening")
            data = os.read(fd, max_bytes + 1)
        finally:
            os.close(fd)
    except OSError as exc:
        raise SecureFileError("secret file could not be read safely") from exc
    if len(data) > max_bytes:
        raise SecureFileError("secret file exceeds size limit")
    return data


def require_private_directory(path: Path, create: bool = False) -> None:
    if create:
        path.mkdir(mode=0o700, parents=False, exist_ok=True)
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SecureFileError("private directory is unsafe")
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise SecureFileError("private directory owner or permissions are unsafe")


def atomic_write_private(path: Path, data: bytes, max_bytes: int = 65_536) -> None:
    if not path.is_absolute() or len(data) > max_bytes:
        raise SecureFileError("private file target or size is invalid")
    require_private_directory(path.parent)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise SecureFileError("private file write did not complete")
            view = view[written:]
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(temporary, path)
        temporary = None
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        if temporary is not None:
            temporary.unlink(missing_ok=True)
