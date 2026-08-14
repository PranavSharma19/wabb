from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class JsonSearchCache:
    """Small persistent cache keyed by search provider and normalized query."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "entries": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"version": 1, "entries": {}}
        if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
            return {"version": 1, "entries": {}}
        return data

    def get(self, key: str) -> list[dict[str, Any]] | None:
        value = self._read()["entries"].get(key)
        if not isinstance(value, list):
            return None
        return value

    def set(self, key: str, candidates: list[dict[str, Any]]) -> None:
        data = self._read()
        data["entries"][key] = candidates
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(
            prefix=f"{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=2, ensure_ascii=False)
                file.write("\n")
            os.replace(temporary_name, self.path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
