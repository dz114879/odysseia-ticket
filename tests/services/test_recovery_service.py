from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.enums import TicketPriority, TicketStatus
from core.models import GuildConfigRecord, TicketRecord
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository
from services.recovery_service import RecoveryService


class FakeLoggingService:
    def __init__(self) -> None:
        self.ticket_logs: list[dict[str, object]] = []

    async def send_ticket_log(self, **payload):
        self.ticket_logs.append(payload)
        return True


def make_ticket(
    ticket_id: str,
    *,
    status: TicketStatus,
    channel_id: int,
    close_execute_at: str | None = None,
    archive_last_error: str | None = None,
    archive_attempts: int = 0,
) -> TicketRecord:
    return TicketRecord(
        ticket_id=ticket_id,
        guild_id=1,
        creator_id=100,
        category_key="support",
        channel_id=channel_id,
        status=status,
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T00:00:00+00:00",
        priority=TicketPriority.MEDIUM,
        close_execute_at=close_execute_at,
        archive_last_error=archive_last_error,
        archive_attempts=archive_attempts,
    )


@pytest.fixture
def seeded_recovery_state(migrated_database):
    guild_repository = GuildRepository(migrated_database)
    guild_repository.upsert_config(
        GuildConfigRecord(
            guild_id=1,
            is_initialized=True,
            log_channel_id=999,
            archive_channel_id=888,
            ticket_category_channel_id=777,
            admin_role_id=666,
            updated_at="2024-01-01T00:00:00+00:00",
        )
    )
    repository = TicketRepository(migrated_database)
    return migrated_database, repository


@pytest.mark.asyncio
async def test_sweep_recoverable_tickets_dispatches_due_and_recoverable_states(seeded_recovery_state) -> None:
    database, repository = seeded_recovery_state
    repository.create(
        make_ticket(
            "ticket-closing-due",
            status=TicketStatus.CLOSING,
            channel_id=101,
            close_execute_at="2024-01-01T00:00:00+00:00",
        )
    )
    repository.create(
        make_ticket(
            "ticket-closing-future",
            status=TicketStatus.CLOSING,
            channel_id=102,
            close_execute_at="2024-01-02T00:00:00+00:00",
        )
    )
    repository.create(make_ticket("ticket-archiving", status=TicketStatus.ARCHIVING, channel_id=103))
    repository.create(make_ticket("ticket-archive-sent", status=TicketStatus.ARCHIVE_SENT, channel_id=104))
    repository.create(make_ticket("ticket-channel-deleted", status=TicketStatus.CHANNEL_DELETED, channel_id=105))
    repository.create(
        make_ticket(
            "ticket-archive-failed",
            status=TicketStatus.ARCHIVE_FAILED,
            channel_id=106,
            archive_last_error="archive channel unavailable",
            archive_attempts=1,
        )
    )

    archive_service = SimpleNamespace(archive_ticket=AsyncMock(return_value=None), guild_repository=GuildRepository(database))
    logging_service = FakeLoggingService()
    service = RecoveryService(
        database,
        archive_service=archive_service,
        logging_service=logging_service,
    )

    await service.sweep_recoverable_tickets(
        reference_time=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    )

    called_ticket_ids = [call.args[0] for call in archive_service.archive_ticket.await_args_list]
    assert called_ticket_ids == [
        "ticket-closing-due",
        "ticket-archiving",
        "ticket-archive-sent",
        "ticket-channel-deleted",
    ]
    assert len(logging_service.ticket_logs) == 1
    assert logging_service.ticket_logs[0]["ticket_id"] == "ticket-archive-failed"


@pytest.mark.asyncio
async def test_sweep_recoverable_tickets_retries_transient_archive_failed_ticket(seeded_recovery_state) -> None:
    database, repository = seeded_recovery_state
    repository.create(
        make_ticket(
            "ticket-retry",
            status=TicketStatus.ARCHIVE_FAILED,
            channel_id=201,
            archive_last_error="temporary network timeout",
            archive_attempts=1,
        )
    )

    archive_service = SimpleNamespace(archive_ticket=AsyncMock(return_value=None), guild_repository=GuildRepository(database))
    service = RecoveryService(database, archive_service=archive_service, archive_retry_limit=3)

    await service.sweep_recoverable_tickets(reference_time=None)

    archive_service.archive_ticket.assert_awaited_once()
    assert archive_service.archive_ticket.await_args.kwargs["allow_retry_from_failed"] is True


@pytest.mark.asyncio
async def test_handle_channel_deleted_routes_closing_ticket_to_forced_fallback(seeded_recovery_state) -> None:
    database, repository = seeded_recovery_state
    repository.create(
        make_ticket(
            "ticket-deleted",
            status=TicketStatus.CLOSING,
            channel_id=301,
            close_execute_at="2024-01-02T00:00:00+00:00",
        )
    )

    archive_service = SimpleNamespace(archive_ticket=AsyncMock(return_value=None), guild_repository=GuildRepository(database))
    logging_service = FakeLoggingService()
    service = RecoveryService(database, archive_service=archive_service, logging_service=logging_service)

    await service.handle_channel_deleted(channel_id=301, guild_id=1)

    archive_service.archive_ticket.assert_awaited_once()
    assert archive_service.archive_ticket.await_args.args == ("ticket-deleted",)
    assert archive_service.archive_ticket.await_args.kwargs["ignore_due_time"] is True
    assert archive_service.archive_ticket.await_args.kwargs["force_fallback"] is True
    assert len(logging_service.ticket_logs) == 1
