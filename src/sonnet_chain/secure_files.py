from __future__ import annotations

import os
import stat
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
