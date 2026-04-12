from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.enums import TicketStatus
from core.errors import PermissionDeniedError
from core.models import TicketRecord
from db.repositories.ticket_repository import TicketRepository
from runtime.locks import LockManager
from services.draft_service import DraftService


@dataclass(frozen=True)
class FakeMember:
    id: int


class FakeChannel:
    def __init__(self, channel_id: int, *, name: str) -> None:
        self.id = channel_id
        self.name = name
        self.deleted = False
        self.edit_calls: list[dict[str, str | None]] = []
        self.delete_calls: list[str | None] = []

    async def edit(self, *, name: str, reason: str | None = None) -> None:
        self.edit_calls.append({"name": name, "reason": reason})
        self.name = name

    async def delete(self, *, reason: str | None = None) -> None:
        self.delete_calls.append(reason)
        self.deleted = True


@pytest.fixture
def prepared_draft_context(migrated_database):
    repository = TicketRepository(migrated_database)
    repository.create(
        TicketRecord(
            ticket_id="1-support-0001",
            guild_id=1,
            creator_id=42,
            category_key="support",
            channel_id=2000,
            status=TicketStatus.DRAFT,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
            has_user_message=False,
        )
    )
    return FakeChannel(2000, name="ticket-support-0001")


@pytest.mark.asyncio
async def test_rename_draft_ticket_updates_channel_name_and_ticket_timestamp(
    migrated_database,
    prepared_draft_context: FakeChannel,
) -> None:
    service = DraftService(migrated_database, lock_manager=LockManager())

    result = await service.rename_draft_ticket(
        prepared_draft_context,
        actor_id=42,
        requested_name="登录异常 复现",
    )

    stored = TicketRepository(migrated_database).get_by_channel_id(prepared_draft_context.id)

    assert result.changed is True
    assert result.old_name == "ticket-support-0001"
    assert result.new_name == "登录异常-复现"
    assert prepared_draft_context.name == "登录异常-复现"
    assert prepared_draft_context.edit_calls[0]["reason"] == "Rename draft ticket 1-support-0001"
    assert stored is not None
    assert stored.updated_at != "2024-01-01T00:00:00+00:00"
    assert stored.status is TicketStatus.DRAFT


@pytest.mark.asyncio
async def test_rename_draft_ticket_is_noop_when_name_already_matches(
    migrated_database,
    prepared_draft_context: FakeChannel,
) -> None:
    service = DraftService(migrated_database, lock_manager=LockManager())

    first = await service.rename_draft_ticket(
        prepared_draft_context,
        actor_id=42,
        requested_name="登录异常 复现",
    )
    second = await service.rename_draft_ticket(
        prepared_draft_context,
        actor_id=42,
        requested_name="登录异常 复现",
    )

    assert first.changed is True
    assert second.changed is False
    assert second.new_name == "登录异常-复现"
    assert len(prepared_draft_context.edit_calls) == 1


@pytest.mark.asyncio
async def test_abandon_draft_ticket_marks_ticket_abandoned_and_deletes_channel(
    migrated_database,
    prepared_draft_context: FakeChannel,
) -> None:
    service = DraftService(migrated_database, lock_manager=LockManager())

    result = await service.abandon_draft_ticket(prepared_draft_context, actor_id=42)

    stored = TicketRepository(migrated_database).get_by_channel_id(prepared_draft_context.id)

    assert result.channel_deleted is True
    assert prepared_draft_context.deleted is True
    assert prepared_draft_context.delete_calls[0] == "Abandon draft ticket 1-support-0001"
    assert stored is not None
    assert stored.status is TicketStatus.ABANDONED


@pytest.mark.asyncio
async def test_abandon_draft_ticket_rejects_non_creator(
    migrated_database,
    prepared_draft_context: FakeChannel,
) -> None:
    service = DraftService(migrated_database, lock_manager=LockManager())

    with pytest.raises(PermissionDeniedError, match="只有 ticket 创建者"):
        await service.abandon_draft_ticket(prepared_draft_context, actor_id=99)
