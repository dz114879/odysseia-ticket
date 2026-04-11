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
    priority_before_sleep: TicketPriority | None = None,
    status_before: TicketStatus | None = None,
    transfer_target_category: str | None = None,
    transfer_initiated_by: int | None = None,
    transfer_reason: str | None = None,
    transfer_execute_at: str | None = None,
    transfer_history_json: str = "[]",
    staff_panel_message_id: int | None = None,
    close_reason: str | None = None,
    close_initiated_by: int | None = None,
    close_execute_at: str | None = None,
    closed_at: str | None = None,
    archive_message_id: int | None = None,
    archived_at: str | None = None,
    message_count: int | None = None,
    snapshot_bootstrapped_at: str | None = None,
    queued_at: str | None = None,
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
        priority_before_sleep=priority_before_sleep,
        status_before=status_before,
        transfer_target_category=transfer_target_category,
        transfer_initiated_by=transfer_initiated_by,
        transfer_reason=transfer_reason,
        transfer_execute_at=transfer_execute_at,
        transfer_history_json=transfer_history_json,
        staff_panel_message_id=staff_panel_message_id,
        close_reason=close_reason,
        close_initiated_by=close_initiated_by,
        close_execute_at=close_execute_at,
        closed_at=closed_at,
        archive_message_id=archive_message_id,
        archived_at=archived_at,
        message_count=message_count,
        snapshot_bootstrapped_at=snapshot_bootstrapped_at,
        queued_at=queued_at,
    )


def test_create_and_get_ticket_preserves_model_mapping(repository: TicketRepository) -> None:
    created = repository.create(
        make_ticket(
            "ticket-001",
            channel_id=501,
            status=TicketStatus.SLEEP,
            has_user_message=True,
            last_user_message_at="2024-01-01T01:00:00+00:00",
            claimed_by=999,
            staff_panel_message_id=3333,
            priority=TicketPriority.SLEEP,
            priority_before_sleep=TicketPriority.HIGH,
            status_before=TicketStatus.SUBMITTED,
            transfer_target_category="billing",
            transfer_initiated_by=301,
            transfer_reason="需要账单组处理",
            transfer_execute_at="2024-01-01T02:00:00+00:00",
            transfer_history_json='[{"target":"billing"}]',
            close_reason="问题已解决",
            close_initiated_by=401,
            close_execute_at="2024-01-01T03:00:00+00:00",
            closed_at="2024-01-01T03:00:00+00:00",
            archive_message_id=7777,
            archived_at="2024-01-01T03:02:00+00:00",
            message_count=42,
            snapshot_bootstrapped_at="2024-01-01T01:05:00+00:00",
            queued_at="2024-01-01T01:06:00+00:00",
        )
    )

    loaded = repository.get_by_ticket_id("ticket-001")

    assert created == loaded
    assert isinstance(loaded, TicketRecord)
    assert loaded is not None
    assert loaded.status is TicketStatus.SLEEP
    assert loaded.priority is TicketPriority.SLEEP
    assert loaded.priority_before_sleep is TicketPriority.HIGH
    assert loaded.status_before is TicketStatus.SUBMITTED
    assert loaded.transfer_target_category == "billing"
    assert loaded.transfer_initiated_by == 301
    assert loaded.transfer_reason == "需要账单组处理"
    assert loaded.transfer_execute_at == "2024-01-01T02:00:00+00:00"
    assert loaded.transfer_history_json == '[{"target":"billing"}]'
    assert loaded.has_user_message is True
    assert loaded.last_user_message_at == "2024-01-01T01:00:00+00:00"
    assert loaded.staff_panel_message_id == 3333
    assert loaded.close_reason == "问题已解决"
    assert loaded.close_initiated_by == 401
    assert loaded.close_execute_at == "2024-01-01T03:00:00+00:00"
    assert loaded.closed_at == "2024-01-01T03:00:00+00:00"
    assert loaded.archive_message_id == 7777
    assert loaded.archived_at == "2024-01-01T03:02:00+00:00"
    assert loaded.message_count == 42
    assert loaded.snapshot_bootstrapped_at == "2024-01-01T01:05:00+00:00"
    assert loaded.queued_at == "2024-01-01T01:06:00+00:00"
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


def test_list_queued_by_guild_orders_by_queued_at_then_created_at(repository: TicketRepository) -> None:
    repository.create(
        make_ticket(
            "ticket-queued-2",
            status=TicketStatus.QUEUED,
            created_at="2024-01-01T00:02:00+00:00",
            updated_at="2024-01-01T00:02:00+00:00",
            queued_at="2024-01-01T00:04:00+00:00",
        )
    )
    repository.create(
        make_ticket(
            "ticket-queued-1",
            status=TicketStatus.QUEUED,
            created_at="2024-01-01T00:01:00+00:00",
            updated_at="2024-01-01T00:01:00+00:00",
            queued_at="2024-01-01T00:03:00+00:00",
        )
    )
    repository.create(make_ticket("ticket-submitted", status=TicketStatus.SUBMITTED))

    queued = repository.list_queued_by_guild(1)

    assert [record.ticket_id for record in queued] == ["ticket-queued-1", "ticket-queued-2"]


def test_list_due_transfer_executions_returns_only_due_transferring_tickets(
    repository: TicketRepository,
) -> None:
    repository.create(
        make_ticket(
            "ticket-due",
            status=TicketStatus.TRANSFERRING,
            transfer_execute_at="2024-01-01T01:00:00+00:00",
        )
    )
    repository.create(
        make_ticket(
            "ticket-future",
            status=TicketStatus.TRANSFERRING,
            transfer_execute_at="2024-01-01T03:00:00+00:00",
        )
    )
    repository.create(
        make_ticket(
            "ticket-submitted",
            status=TicketStatus.SUBMITTED,
            transfer_execute_at="2024-01-01T00:30:00+00:00",
        )
    )

    assert [ticket.ticket_id for ticket in repository.list_due_transfer_executions("2024-01-01T02:00:00+00:00")] == ["ticket-due"]


def test_list_due_close_executions_returns_only_due_closing_tickets(
    repository: TicketRepository,
) -> None:
    repository.create(
        make_ticket(
            "ticket-due-close",
            status=TicketStatus.CLOSING,
            close_execute_at="2024-01-01T01:00:00+00:00",
        )
    )
    repository.create(
        make_ticket(
            "ticket-future-close",
            status=TicketStatus.CLOSING,
            close_execute_at="2024-01-01T03:00:00+00:00",
        )
    )
    repository.create(
        make_ticket(
            "ticket-archiving",
            status=TicketStatus.ARCHIVING,
            close_execute_at="2024-01-01T00:30:00+00:00",
        )
    )

    assert [ticket.ticket_id for ticket in repository.list_due_close_executions("2024-01-01T02:00:00+00:00")] == ["ticket-due-close"]


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
            priority_before_sleep=TicketPriority.HIGH,
            status_before=TicketStatus.SLEEP,
            transfer_target_category="billing",
            transfer_initiated_by=301,
            transfer_reason="sleep 状态下转交",
            transfer_execute_at="2024-02-01T02:00:00+00:00",
            transfer_history_json='[{"target":"billing","status_before":"sleep"}]',
            close_reason="已确认完结",
            close_initiated_by=401,
            close_execute_at="2024-02-01T03:00:00+00:00",
            closed_at="2024-02-01T03:00:00+00:00",
            archive_message_id=9001,
            archived_at="2024-02-01T03:02:00+00:00",
            message_count=11,
            snapshot_bootstrapped_at="2024-02-01T01:30:00+00:00",
            queued_at="2024-02-01T01:00:00+00:00",
        )
    )

    updated = repository.update(
        "ticket-100",
        claimed_by=None,
        priority=TicketPriority.LOW,
        updated_at="2024-03-01T00:00:00+00:00",
        staff_panel_message_id=8080,
        priority_before_sleep=None,
        last_user_message_at=None,
        status_before=None,
        transfer_target_category=None,
        transfer_initiated_by=None,
        transfer_reason=None,
        transfer_execute_at=None,
        transfer_history_json="[]",
        close_reason=None,
        close_initiated_by=None,
        close_execute_at=None,
        closed_at=None,
        archive_message_id=None,
        archived_at=None,
        message_count=None,
        snapshot_bootstrapped_at=None,
        queued_at=None,
    )

    assert upserted.created_at == "2024-01-01T00:00:00+00:00"
    assert upserted.channel_id == 701
    assert upserted.status is TicketStatus.SUBMITTED
    assert upserted.has_user_message is True
    assert upserted.last_user_message_at == "2024-02-01T01:00:00+00:00"
    assert upserted.claimed_by == 42
    assert upserted.priority is TicketPriority.EMERGENCY
    assert upserted.priority_before_sleep is TicketPriority.HIGH
    assert upserted.status_before is TicketStatus.SLEEP
    assert upserted.transfer_target_category == "billing"
    assert upserted.transfer_initiated_by == 301
    assert upserted.transfer_reason == "sleep 状态下转交"
    assert upserted.transfer_execute_at == "2024-02-01T02:00:00+00:00"
    assert upserted.transfer_history_json == '[{"target":"billing","status_before":"sleep"}]'
    assert upserted.close_reason == "已确认完结"
    assert upserted.close_initiated_by == 401
    assert upserted.close_execute_at == "2024-02-01T03:00:00+00:00"
    assert upserted.closed_at == "2024-02-01T03:00:00+00:00"
    assert upserted.archive_message_id == 9001
    assert upserted.archived_at == "2024-02-01T03:02:00+00:00"
    assert upserted.message_count == 11
    assert upserted.snapshot_bootstrapped_at == "2024-02-01T01:30:00+00:00"
    assert upserted.queued_at == "2024-02-01T01:00:00+00:00"

    assert updated is not None
    assert updated.created_at == "2024-01-01T00:00:00+00:00"
    assert updated.channel_id == 701
    assert updated.status is TicketStatus.SUBMITTED
    assert updated.claimed_by is None
    assert updated.priority is TicketPriority.LOW
    assert updated.last_user_message_at is None
    assert updated.staff_panel_message_id == 8080
    assert updated.priority_before_sleep is None
    assert updated.status_before is None
    assert updated.transfer_target_category is None
    assert updated.transfer_initiated_by is None
    assert updated.transfer_reason is None
    assert updated.transfer_execute_at is None
    assert updated.transfer_history_json == "[]"
    assert updated.close_reason is None
    assert updated.close_initiated_by is None
    assert updated.close_execute_at is None
    assert updated.closed_at is None
    assert updated.archive_message_id is None
    assert updated.archived_at is None
    assert updated.message_count is None
    assert updated.snapshot_bootstrapped_at is None
    assert updated.queued_at is None
    assert updated.updated_at == "2024-03-01T00:00:00+00:00"

    assert repository.delete("ticket-100") is True
    assert repository.delete("ticket-100") is False
    assert repository.get_by_ticket_id("ticket-100") is None
