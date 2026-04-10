from __future__ import annotations

from pathlib import Path

from config.static import STORAGE_DIR
from core.models import TicketRecord
from db.connection import DatabaseManager
from db.repositories.ticket_mute_repository import TicketMuteRepository


class CleanupService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        ticket_mute_repository: TicketMuteRepository | None = None,
        storage_dir: Path | None = None,
    ) -> None:
        self.database = database
        self.ticket_mute_repository = ticket_mute_repository or TicketMuteRepository(database)
        self.storage_dir = storage_dir or STORAGE_DIR

    def cleanup_ticket(self, ticket: TicketRecord) -> None:
        for mute_record in self.ticket_mute_repository.list_by_ticket(ticket.ticket_id):
            self.ticket_mute_repository.delete(ticket.ticket_id, mute_record.user_id)

        for file_path in self._iter_cleanup_files(ticket.ticket_id):
            if file_path.exists() and file_path.is_file():
                file_path.unlink()

    def _iter_cleanup_files(self, ticket_id: str) -> list[Path]:
        base_directories = (
            self.storage_dir / "snapshots",
            self.storage_dir / "notes",
            self.storage_dir / "archives",
            self.storage_dir / "exports",
        )

        cleanup_files: list[Path] = []
        for directory in base_directories:
            if not directory.exists():
                continue
            cleanup_files.extend(sorted(directory.glob(f"{ticket_id}*")))
        return cleanup_files
