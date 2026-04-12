from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from collections.abc import AsyncIterator

from core.errors import ValidationError
from core.models import TicketRecord
from db.repositories.base import utc_now_iso
from runtime.locks import LockManager
from storage.notes_store import NotesStore


@dataclass(frozen=True, slots=True)
class NoteAddResult:
    ticket: TicketRecord
    record: dict[str, Any]
    note_count: int
    is_claimer: bool


class NotesService:
    def __init__(
        self,
        *,
        notes_store: NotesStore | None = None,
        lock_manager: LockManager | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.notes_store = notes_store or NotesStore()
        self.lock_manager = lock_manager
        self.logger = logger or logging.getLogger(__name__)

    async def add_note(
        self,
        ticket: TicketRecord,
        *,
        actor: Any,
        content: str,
    ) -> NoteAddResult:
        actor_id = getattr(actor, "id", None)
        if actor_id is None:
            raise ValidationError("无法识别当前备注作者。")

        normalized_content = str(content or "").strip()
        if not normalized_content:
            raise ValidationError("备注内容不能为空。")

        record = {
            "author_id": actor_id,
            "author_name": self._resolve_author_name(actor),
            "is_claimer": ticket.claimed_by is not None and ticket.claimed_by == actor_id,
            "timestamp": utc_now_iso(),
            "content": normalized_content,
        }
        async with self._acquire_notes_lock(ticket.ticket_id):
            self.notes_store.append_record(ticket.ticket_id, record)
            note_count = len(self.notes_store.read_records(ticket.ticket_id))

        return NoteAddResult(
            ticket=ticket,
            record=record,
            note_count=note_count,
            is_claimer=bool(record["is_claimer"]),
        )

    def list_notes(self, ticket: TicketRecord) -> list[dict[str, Any]]:
        return self.notes_store.read_records(ticket.ticket_id)

    def format_notes(self, ticket: TicketRecord) -> str:
        notes = self.list_notes(ticket)
        if not notes:
            return f"ticket `{ticket.ticket_id}` 暂无内部备注。"

        lines = [f"Ticket `{ticket.ticket_id}` 内部备注（共 {len(notes)} 条）", ""]
        for index, note in enumerate(notes, start=1):
            claimer_marker = " ⭐" if note.get("is_claimer") else ""
            lines.extend(
                [
                    f"[{index}] {note.get('author_name', 'Unknown')} ({note.get('author_id', 'unknown')}){claimer_marker}",
                    f"时间：{note.get('timestamp', 'unknown')}",
                    str(note.get("content", "")),
                    "",
                ]
            )
        return "\n".join(lines).strip()

    @staticmethod
    def _resolve_author_name(actor: Any) -> str:
        return str(getattr(actor, "display_name", None) or getattr(actor, "name", None) or getattr(actor, "id", "Unknown"))

    @asynccontextmanager
    async def _acquire_notes_lock(self, ticket_id: str) -> AsyncIterator[None]:
        if self.lock_manager is None:
            yield
            return

        async with self.lock_manager.acquire(f"ticket-notes:{ticket_id}"):
            yield
