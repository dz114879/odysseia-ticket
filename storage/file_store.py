from __future__ import annotations

from pathlib import Path

from config.static import STORAGE_DIR


class TicketFileStore:
    def __init__(self, storage_dir: Path | None = None) -> None:
        self.storage_dir = storage_dir or STORAGE_DIR
        self.snapshots_dir = self.storage_dir / "snapshots"
        self.notes_dir = self.storage_dir / "notes"
        self.archives_dir = self.storage_dir / "archives"
        self.exports_dir = self.storage_dir / "exports"
        self.ensure_directories()

    def ensure_directories(self) -> None:
        for directory in (
            self.storage_dir,
            self.snapshots_dir,
            self.notes_dir,
            self.archives_dir,
            self.exports_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def snapshot_path(self, ticket_id: str) -> Path:
        self.ensure_directories()
        return self.snapshots_dir / f"{ticket_id}.jsonl"

    def notes_path(self, ticket_id: str) -> Path:
        self.ensure_directories()
        return self.notes_dir / f"{ticket_id}.jsonl"
