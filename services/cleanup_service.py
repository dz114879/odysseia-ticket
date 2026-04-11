from __future__ import annotations

from pathlib import Path

from config.static import STORAGE_DIR
from core.models import TicketRecord
from db.connection import DatabaseManager
from db.repositories.ticket_mute_repository import TicketMuteRepository
from runtime.cache import RuntimeCacheStore


class CleanupService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        ticket_mute_repository: TicketMuteRepository | None = None,
        storage_dir: Path | None = None,
        cache: RuntimeCacheStore | None = None,
    ) -> None:
        self.database = database
        self.ticket_mute_repository = ticket_mute_repository or TicketMuteRepository(database)
        self.storage_dir = storage_dir or STORAGE_DIR
        self.cache = cache

    def cleanup_ticket(self, ticket: TicketRecord) -> None:
        for mute_record in self.ticket_mute_repository.list_by_ticket(ticket.ticket_id):
            self.ticket_mute_repository.delete(ticket.ticket_id, mute_record.user_id)

        for file_path in self._iter_cleanup_files(ticket.ticket_id):
            if file_path.exists() and file_path.is_file():
                file_path.unlink()

        if self.cache is not None and ticket.channel_id is not None:
            self.cache.clear_ticket_snapshot_state(ticket.channel_id)

    def _iter_cleanup_files(self, ticket_id: str) -> list[Path]:
        cleanup_files = [
            self.storage_dir / "snapshots" / f"{ticket_id}.jsonl",
            self.storage_dir / "snapshots" / f"{ticket_id}.jsonl.tmp",
            self.storage_dir / "notes" / f"{ticket_id}.jsonl",
            self.storage_dir / "archives" / f"{ticket_id}.html",
            self.storage_dir / "exports" / f"{ticket_id}.html",
        ]
        cleanup_files.extend(self._iter_ticket_boundary_files(self.storage_dir / "archives", ticket_id))
        cleanup_files.extend(self._iter_ticket_boundary_files(self.storage_dir / "exports", ticket_id))

        deduplicated_files: list[Path] = []
        seen_paths: set[Path] = set()
        for file_path in cleanup_files:
            if file_path in seen_paths:
                continue
            seen_paths.add(file_path)
            deduplicated_files.append(file_path)
        return deduplicated_files

    @staticmethod
    def _iter_ticket_boundary_files(directory: Path, ticket_id: str) -> list[Path]:
        if not directory.exists():
            return []

        return sorted(path for path in directory.glob(f"{ticket_id}-*") if path.is_file())
