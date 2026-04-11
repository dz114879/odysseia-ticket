from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterable

from storage.file_store import TicketFileStore


class SnapshotStore:
    def __init__(
        self,
        *,
        file_store: TicketFileStore | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.file_store = file_store or TicketFileStore()
        self.logger = logger or logging.getLogger(__name__)

    def get_path(self, ticket_id: str) -> Path:
        return self.file_store.snapshot_path(ticket_id)

    def exists(self, ticket_id: str) -> bool:
        return self.get_path(ticket_id).exists()

    def append_record(self, ticket_id: str, record: dict[str, Any]) -> Path:
        path = self.get_path(ticket_id)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path

    def overwrite_records(
        self,
        ticket_id: str,
        records: Iterable[dict[str, Any]],
    ) -> Path:
        path = self.get_path(ticket_id)
        temporary_path = path.with_suffix(".jsonl.tmp")
        rendered_lines = [json.dumps(record, ensure_ascii=False) for record in records]
        content = "\n".join(rendered_lines)
        if content:
            content += "\n"
        temporary_path.write_text(content, encoding="utf-8")
        temporary_path.replace(path)
        return path

    def read_records(self, ticket_id: str) -> list[dict[str, Any]]:
        path = self.get_path(ticket_id)
        if not path.exists():
            return []

        records: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    self.logger.warning(
                        "Skipped corrupted snapshot line. ticket_id=%s line=%s",
                        ticket_id,
                        line_number,
                    )
                    continue
                if not isinstance(payload, dict):
                    self.logger.warning(
                        "Skipped non-object snapshot line. ticket_id=%s line=%s",
                        ticket_id,
                        line_number,
                    )
                    continue
                records.append(payload)
        return records

    def delete(self, ticket_id: str) -> bool:
        path = self.get_path(ticket_id)
        if not path.exists():
            return False
        path.unlink()
        return True
