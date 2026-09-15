"""Filesystem storage used by the Blender bridge, assets, logs and references."""
from pathlib import Path


class RuntimeStorage:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ("blender_jobs", "sources", "assets"):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def clear(self):
        # Old semantic-model databases are removed during general-purpose cleanup.
        for pattern in ("projects.sqlite3", "projects.sqlite3-shm", "projects.sqlite3-wal"):
            (self.root / pattern).unlink(missing_ok=True)

    def close(self):
        pass
