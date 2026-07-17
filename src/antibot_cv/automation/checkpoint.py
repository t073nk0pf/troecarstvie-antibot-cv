from __future__ import annotations

import json
from contextlib import contextmanager
import fcntl
import os
import tempfile
from pathlib import Path
from typing import Any


@contextmanager
def checkpoint_lock(path: str | Path):
    """Serialize readers/CAS writers and every ordinary checkpoint write."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(f".{target.name}.lock")
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def write_json_checkpoint(
    path: str | Path, payload: dict[str, Any], *, lock_held: bool = False,
) -> None:
    """Atomically replace a small controller checkpoint."""
    target = Path(path)
    if not lock_held:
        with checkpoint_lock(target):
            return write_json_checkpoint(target, payload, lock_held=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
