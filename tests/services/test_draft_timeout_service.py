from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from core.enums import TicketStatus
from core.models import TicketRecord
from db.repositories.ticket_repository import TicketRepository
from runtime.locks import LockManager
from services.draft_timeout_service import DraftTimeoutService


@dataclass(frozen=True)
class FakeAuthor:
    id: int
    bot: bool = False


class FakeChannel:
    def __init__(self, channel_id: int, *, name: str = "draft-channel") -> None:
        self.id = channel_id
        self.name = name
        self.deleted = False
        self.delete_reasons: list[str | None] = []

    async def delete(self, *, reason: str | None = None) -> None:
        self.deleted = True
        self.delete_reasons.append(reason)


class FakeBot:
    def __init__(self, channels: list[FakeChannel]) -> None:
        self.channels = {channel.id: channel for channel in channels}

    def get_channel(self, channel_id: int) -> FakeChannel | None:
        return self.channels.get(channel_id)

    async def fetch_channel(self, channel_id: int) -> FakeChannel:
        channel = self.channels.get(channel_id)
        if channel is None:
            raise LookupError("channel not found")
        return channel


class FakeMessage:
    def __init__(
        self,
        *,
        author: FakeAuthor,
        channel: FakeChannel,
        created_at: datetime,
    ) -> None:
        self.author = author
        self.channel = channel
        self.guild = object()
        self.created_at = created_at


@pytest.fixture
def repository(migrated_database) -> TicketRepository:
    return TicketRepository(migrated_database)


def make_draft_ticket(
    ticket_id: str,
    *,
    channel_id: int,
    creator_id: int = 42,
    created_at: str = "2024-01-01T00:00:00+00:00",
    has_user_message: bool = False,
    last_user_message_at: str | None = None,
) -> TicketRecord:
    return TicketRecord(
        ticket_id=ticket_id,
        guild_id=1,
        creator_id=creator_id,
        category_key="support",
        channel_id=channel_id,
        status=TicketStatus.DRAFT,
        created_at=created_at,
        updated_at=created_at,
        has_user_message=has_user_message,
        last_user_message_at=last_user_message_at,
    )


@pytest.mark.asyncio
async def test_handle_message_marks_first_user_message_and_updates_last_activity(
    migrated_database,
    repository: TicketRepository,
) -> None:
    channel = FakeChannel(2000)
    repository.create(make_draft_ticket("1-support-0001", channel_id=channel.id))
    service = DraftTimeoutService(
        migrated_database,
        bot=FakeBot([channel]),
        lock_manager=LockManager(),
    )

    result = await service.handle_message(
        FakeMessage(
            author=FakeAuthor(42),
            channel=channel,
            created_at=datetime(2024, 1, 1, 1, 0, tzinfo=timezone.utc),
        )
    )

    stored = repository.get_by_ticket_id("1-support-0001")

    assert result is not None
    assert result.has_user_message is True
    assert result.last_user_message_at == "2024-01-01T01:00:00+00:00"
    assert stored is not None
    assert stored.has_user_message is True
    assert stored.last_user_message_at == "2024-01-01T01:00:00+00:00"


@pytest.mark.asyncio
async def test_sweep_expired_drafts_abandons_silent_24h_draft_and_deletes_channel(
    migrated_database,
    repository: TicketRepository,
) -> None:
    channel = FakeChannel(2001)
    repository.create(
        make_draft_ticket(
            "1-support-0002",
            channel_id=channel.id,
            created_at="2024-01-01T00:00:00+00:00",
        )
    )
    service = DraftTimeoutService(
        migrated_database,
        bot=FakeBot([channel]),
        lock_manager=LockManager(),
    )

    outcomes = await service.sweep_expired_drafts(now="2024-01-02T00:01:00+00:00")
    stored = repository.get_by_ticket_id("1-support-0002")

    assert len(outcomes) == 1
    assert outcomes[0].reason == "draft_expired"
    assert outcomes[0].channel_deleted is True
    assert channel.deleted is True
    assert channel.delete_reasons == ["Expire draft ticket 1-support-0002"]
    assert stored is not None
    assert stored.status is TicketStatus.ABANDONED


@pytest.mark.asyncio
async def test_sweep_expired_drafts_closes_inactive_6h_draft_after_first_message(
    migrated_database,
    repository: TicketRepository,
) -> None:
    channel = FakeChannel(2002)
    repository.create(
        make_draft_ticket(
            "1-support-0003",
            channel_id=channel.id,
            created_at="2024-01-01T00:00:00+00:00",
            has_user_message=True,
            last_user_message_at="2024-01-01T01:00:00+00:00",
        )
    )
    service = DraftTimeoutService(
        migrated_database,
        bot=FakeBot([channel]),
        lock_manager=LockManager(),
    )

    outcomes = await service.sweep_expired_drafts(now="2024-01-01T07:30:00+00:00")
    stored = repository.get_by_ticket_id("1-support-0003")

    assert len(outcomes) == 1
    assert outcomes[0].reason == "inactive_close"
    assert outcomes[0].channel_deleted is True
    assert channel.delete_reasons == ["Close inactive draft ticket 1-support-0003"]
    assert stored is not None
    assert stored.status is TicketStatus.ABANDONED


@pytest.mark.asyncio
async def test_sweep_expired_drafts_is_idempotent_and_recovers_when_channel_missing(
    migrated_database,
    repository: TicketRepository,
) -> None:
    repository.create(
        make_draft_ticket(
            "1-support-0004",
            channel_id=2999,
            created_at="2024-01-01T00:00:00+00:00",
        )
    )
    service = DraftTimeoutService(
        migrated_database,
        bot=FakeBot([]),
        lock_manager=LockManager(),
    )

    first = await service.sweep_expired_drafts(now="2024-01-02T00:01:00+00:00")
    second = await service.sweep_expired_drafts(now="2024-01-02T00:02:00+00:00")
    stored = repository.get_by_ticket_id("1-support-0004")

    assert len(first) == 1
    assert first[0].channel_deleted is False
    assert first[0].reason == "draft_expired"
    assert second == []
    assert stored is not None
    assert stored.status is TicketStatus.ABANDONED
