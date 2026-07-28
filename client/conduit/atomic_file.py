"""Atomic private JSON writes for the persisted authorization token."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def write_json_atomic(path: Path, payload: dict[str, Any], *, mode: int | None = None, indent: int | None = 2) -> None:
    """Write `payload` as JSON to `path` atomically.

    Writes to a uniquely-named temp file in the same directory (so
    os.replace() is guaranteed to be an atomic rename on POSIX, never a
    cross-filesystem copy), fsyncs before replacing, and best-effort cleans
    up the temp file afterward (the unlink is a no-op on the success path,
    since os.replace() already consumed it; it matters on the exception
    path, where the temp file would otherwise be orphaned).

    Raises whatever the underlying file operations raise; callers decide
    whether to swallow/log (auth.py currently lets failures propagate as a
    return-False from save_token's caller context; session.py similarly).

    `mode`, when provided, is applied before the atomic rename so a
    credential-bearing file never briefly uses broader permissions.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=indent)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)
