from __future__ import annotations

import pytest

from core.enums import TicketPriority, TicketStatus
from core.models import TicketRecord
from db.repositories.ticket_repository import TicketRepository


@pytest.fixture
def repository(migrated_database) -> TicketRepository:
    return TicketRepository(migrated_database)


def make_ticket(
    ticket_id: str,
    *,
    guild_id: int = 1,
    creator_id: int = 100,
    category_key: str = "general",
    channel_id: int | None = None,
    status: TicketStatus = TicketStatus.DRAFT,
    created_at: str = "2024-01-01T00:00:00+00:00",
    updated_at: str = "2024-01-01T00:00:00+00:00",
    has_user_message: bool = False,
    last_user_message_at: str | None = None,
    claimed_by: int | None = None,
    priority: TicketPriority = TicketPriority.MEDIUM,
) -> TicketRecord:
    return TicketRecord(
        ticket_id=ticket_id,
        guild_id=guild_id,
        creator_id=creator_id,
        category_key=category_key,
        channel_id=channel_id,
        status=status,
        created_at=created_at,
        updated_at=updated_at,
        has_user_message=has_user_message,
        last_user_message_at=last_user_message_at,
        claimed_by=claimed_by,
        priority=priority,
    )


def test_create_and_get_ticket_preserves_model_mapping(repository: TicketRepository) -> None:
    created = repository.create(
        make_ticket(
            "ticket-001",
            channel_id=501,
            status=TicketStatus.SUBMITTED,
            has_user_message=True,
            last_user_message_at="2024-01-01T01:00:00+00:00",
            claimed_by=999,
            priority=TicketPriority.HIGH,
        )
    )

    loaded = repository.get_by_ticket_id("ticket-001")

    assert created == loaded
    assert isinstance(loaded, TicketRecord)
    assert loaded is not None
    assert loaded.status is TicketStatus.SUBMITTED
    assert loaded.priority is TicketPriority.HIGH
    assert loaded.has_user_message is True
    assert loaded.last_user_message_at == "2024-01-01T01:00:00+00:00"
    assert repository.get_by_channel_id(501) == loaded


def test_list_by_guild_supports_status_and_creator_filters(repository: TicketRepository) -> None:
    repository.create(
        make_ticket(
            "ticket-001",
            creator_id=1,
            status=TicketStatus.DRAFT,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )
    )
    repository.create(
        make_ticket(
            "ticket-002",
            creator_id=1,
            status=TicketStatus.SUBMITTED,
            created_at="2024-01-02T00:00:00+00:00",
            updated_at="2024-01-02T00:00:00+00:00",
        )
    )
    repository.create(
        make_ticket(
            "ticket-003",
            guild_id=2,
            creator_id=2,
            status=TicketStatus.DRAFT,
            created_at="2024-01-03T00:00:00+00:00",
            updated_at="2024-01-03T00:00:00+00:00",
        )
    )

    all_in_guild = repository.list_by_guild(1)
    drafts_by_creator = repository.list_by_guild(
        1,
        statuses=[TicketStatus.DRAFT],
        creator_id=1,
    )

    assert [record.ticket_id for record in all_in_guild] == ["ticket-001", "ticket-002"]
    assert [record.ticket_id for record in drafts_by_creator] == ["ticket-001"]


def test_upsert_update_and_delete_ticket_without_overwriting_unspecified_fields(
    repository: TicketRepository,
) -> None:
    repository.create(
        make_ticket(
            "ticket-100",
            channel_id=700,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )
    )

    upserted = repository.upsert(
        make_ticket(
            "ticket-100",
            channel_id=701,
            status=TicketStatus.SUBMITTED,
            created_at="2030-01-01T00:00:00+00:00",
            updated_at="2024-02-01T00:00:00+00:00",
            has_user_message=True,
            last_user_message_at="2024-02-01T01:00:00+00:00",
            claimed_by=42,
            priority=TicketPriority.EMERGENCY,
        )
    )

    updated = repository.update(
        "ticket-100",
        claimed_by=None,
        priority=TicketPriority.LOW,
        updated_at="2024-03-01T00:00:00+00:00",
        last_user_message_at=None,
    )

    assert upserted.created_at == "2024-01-01T00:00:00+00:00"
    assert upserted.channel_id == 701
    assert upserted.status is TicketStatus.SUBMITTED
    assert upserted.has_user_message is True
    assert upserted.last_user_message_at == "2024-02-01T01:00:00+00:00"
    assert upserted.claimed_by == 42
    assert upserted.priority is TicketPriority.EMERGENCY

    assert updated is not None
    assert updated.created_at == "2024-01-01T00:00:00+00:00"
    assert updated.channel_id == 701
    assert updated.status is TicketStatus.SUBMITTED
    assert updated.claimed_by is None
    assert updated.priority is TicketPriority.LOW
    assert updated.last_user_message_at is None
    assert updated.updated_at == "2024-03-01T00:00:00+00:00"

    assert repository.delete("ticket-100") is True
    assert repository.delete("ticket-100") is False
    assert repository.get_by_ticket_id("ticket-100") is None
