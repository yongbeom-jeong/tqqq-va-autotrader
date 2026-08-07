from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class StateStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Cannot read execution state safely: {self.path}") from exc
        if not isinstance(value, dict):
            raise RuntimeError("Execution state is invalid.")
        return value

    def save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        value = dict(state)
        value["updated_at"] = datetime.now(timezone.utc).isoformat()
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self.path)

    @contextmanager
    def execution_lock(self):
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RuntimeError(
                f"Another execution may be active ({lock_path}). New orders are blocked."
            ) from exc
        try:
            os.write(descriptor, str(os.getpid()).encode("ascii"))
            os.close(descriptor)
            descriptor = -1
            yield
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
