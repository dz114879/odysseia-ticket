from __future__ import annotations

from core.enums import TicketStatus
from core.models import TicketRecord
from db.repositories.ticket_repository import TicketRepository
from services.capacity_service import CapacityService


def test_capacity_service_counts_only_active_capacity_statuses(migrated_database) -> None:
    repository = TicketRepository(migrated_database)
    repository.create(
        TicketRecord(
            ticket_id="ticket-submitted",
            guild_id=1,
            creator_id=101,
            category_key="support",
            channel_id=9001,
            status=TicketStatus.SUBMITTED,
        )
    )
    repository.create(
        TicketRecord(
            ticket_id="ticket-closing",
            guild_id=1,
            creator_id=102,
            category_key="support",
            channel_id=9002,
            status=TicketStatus.CLOSING,
        )
    )
    repository.create(
        TicketRecord(
            ticket_id="ticket-queued",
            guild_id=1,
            creator_id=103,
            category_key="support",
            channel_id=9003,
            status=TicketStatus.QUEUED,
            queued_at="2024-01-01T00:10:00+00:00",
        )
    )
    repository.create(
        TicketRecord(
            ticket_id="ticket-sleep",
            guild_id=1,
            creator_id=104,
            category_key="support",
            channel_id=9004,
            status=TicketStatus.SLEEP,
        )
    )

    service = CapacityService(migrated_database)
    snapshot = service.build_snapshot(guild_id=1, max_open_tickets=3)

    assert snapshot.active_count == 2
    assert snapshot.available_slots == 1
    assert snapshot.has_capacity is True


def test_capacity_service_released_capacity_detects_active_to_inactive_transitions() -> None:
    assert CapacityService.released_capacity(TicketStatus.SUBMITTED, TicketStatus.SLEEP) is True
    assert CapacityService.released_capacity(TicketStatus.ARCHIVE_SENT, TicketStatus.CHANNEL_DELETED) is True
    assert CapacityService.released_capacity(TicketStatus.TRANSFERRING, TicketStatus.SUBMITTED) is False
    assert CapacityService.released_capacity(TicketStatus.QUEUED, TicketStatus.SUBMITTED) is False


def test_capacity_service_count_active_tickets_supports_exclude_ticket_id(migrated_database) -> None:
    repository = TicketRepository(migrated_database)
    first_ticket = TicketRecord(
        ticket_id="ticket-submitted-1",
        guild_id=1,
        creator_id=101,
        category_key="support",
        channel_id=9101,
        status=TicketStatus.SUBMITTED,
    )
    repository.create(first_ticket)
    repository.create(
        TicketRecord(
            ticket_id="ticket-submitted-2",
            guild_id=1,
            creator_id=102,
            category_key="support",
            channel_id=9102,
            status=TicketStatus.SUBMITTED,
        )
    )

    service = CapacityService(migrated_database)

    assert service.count_active_tickets(1) == 2
    assert service.count_active_tickets(1, exclude_ticket_id=first_ticket.ticket_id) == 1
