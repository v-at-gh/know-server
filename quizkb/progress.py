from pathlib import Path

from .config import OUTPUT_ROOT
from .io_utils import load_json, save_json


class Progress:
    def __init__(self, path: Path):
        self._path = path
        self._data = load_json(path, {}) or {}

    @staticmethod
    def key(*parts) -> str:
        return ":".join(str(p) for p in parts)

    def is_done(self, *parts) -> bool:
        return self._data.get(self.key(*parts), False)

    def mark(self, *parts) -> None:
        self._data[self.key(*parts)] = True
        save_json(self._path, self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: str) -> bool:
        return key in self._data


def default_progress() -> Progress:
    return Progress(OUTPUT_ROOT / "progress.json")