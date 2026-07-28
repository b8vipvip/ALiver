from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import BinaryIO


class BridgeInstanceLock:
    def __init__(self, handle: BinaryIO, path: Path) -> None:
        self._handle = handle
        self.path = path
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            self._handle.close()
        except Exception:
            pass

    def __enter__(self) -> "BridgeInstanceLock":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def _read_owner(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def try_acquire_bridge_lock(path: Path) -> tuple[BridgeInstanceLock | None, dict]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        handle.close()
        return None, _read_owner(path)

    owner = {
        "pid": os.getpid(),
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "cwd": str(Path.cwd()),
    }
    encoded = json.dumps(owner, ensure_ascii=False, indent=2).encode("utf-8")
    handle.seek(0)
    handle.truncate()
    handle.write(encoded)
    handle.flush()
    os.fsync(handle.fileno())
    return BridgeInstanceLock(handle, path), owner
