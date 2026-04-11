from __future__ import annotations

from types import SimpleNamespace

from core.enums import TicketStatus
from core.models import TicketRecord
from runtime.locks import LockManager
from services.notes_service import NotesService
from storage.file_store import TicketFileStore
from storage.notes_store import NotesStore


def build_ticket() -> TicketRecord:
    return TicketRecord(
        ticket_id="1-support-0001",
        guild_id=1,
        creator_id=201,
        category_key="support",
        channel_id=9001,
        status=TicketStatus.SUBMITTED,
        claimed_by=301,
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T00:00:00+00:00",
    )


async def test_notes_service_adds_and_formats_notes(tmp_path) -> None:
    store = NotesStore(file_store=TicketFileStore(tmp_path))
    service = NotesService(notes_store=store, lock_manager=LockManager())
    ticket = build_ticket()

    result = await service.add_note(
        ticket,
        actor=SimpleNamespace(id=301, display_name="helper"),
        content="  需要后续转交账单组  ",
    )
    rendered = service.format_notes(ticket)

    assert result.note_count == 1
    assert result.record["content"] == "需要后续转交账单组"
    assert result.is_claimer is True
    assert "helper" in rendered
    assert "⭐ claimer" in rendered
    assert "需要后续转交账单组" in rendered
