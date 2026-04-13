from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from core.enums import ClaimMode, TicketStatus
from core.errors import InvalidTicketStateError, PermissionDeniedError
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository
from services.ticket_access_service import TicketAccessService


@dataclass(frozen=True)
class FakeRole:
    id: int


@dataclass
class FakeMember:
    id: int
    roles: list[FakeRole] = field(default_factory=list)
    administrator: bool = False

    @property
    def guild_permissions(self) -> SimpleNamespace:
        return SimpleNamespace(administrator=self.administrator)


@pytest.fixture
def prepared_ticket_access_context(migrated_database):
    guild_repository = GuildRepository(migrated_database)
    ticket_repository = TicketRepository(migrated_database)
    access_service = TicketAccessService(migrated_database)

    guild_repository.upsert_config(
        GuildConfigRecord(
            guild_id=1,
            is_initialized=True,
            log_channel_id=100,
            archive_channel_id=200,
            ticket_category_channel_id=300,
            admin_role_id=400,
            claim_mode=ClaimMode.RELAXED,
            max_open_tickets=10,
            timezone="Asia/Hong_Kong",
            enable_download_window=True,
            updated_at="2024-01-01T00:00:00+00:00",
        )
    )
    guild_repository.upsert_category(
        TicketCategoryConfig(
            guild_id=1,
            category_key="support",
            display_name="技术支持",
            staff_role_ids_json='[500]',
            staff_user_ids_json="[302]",
        )
    )

    ticket = ticket_repository.create(
        TicketRecord(
            ticket_id="1-support-0001",
            guild_id=1,
            creator_id=201,
            category_key="support",
            channel_id=9001,
            status=TicketStatus.SUBMITTED,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
            has_user_message=True,
            last_user_message_at="2024-01-01T01:00:00+00:00",
        )
    )

    admin_role = FakeRole(400)
    staff_role = FakeRole(500)
    creator = FakeMember(201)
    staff_member = FakeMember(301, roles=[staff_role])
    explicit_staff_member = FakeMember(302)
    admin_member = FakeMember(401, roles=[admin_role])
    outsider = FakeMember(999)

    return {
        "access_service": access_service,
        "ticket_repository": ticket_repository,
        "ticket": ticket,
        "creator": creator,
        "staff_member": staff_member,
        "explicit_staff_member": explicit_staff_member,
        "admin_member": admin_member,
        "outsider": outsider,
    }


@pytest.mark.parametrize(
    "status",
    (
        TicketStatus.SUBMITTED,
        TicketStatus.SLEEP,
        TicketStatus.TRANSFERRING,
        TicketStatus.CLOSING,
    ),
)
def test_load_snapshot_context_accepts_active_snapshot_statuses(
    prepared_ticket_access_context,
    status: TicketStatus,
) -> None:
    access_service = prepared_ticket_access_context["access_service"]
    ticket_repository = prepared_ticket_access_context["ticket_repository"]
    ticket = prepared_ticket_access_context["ticket"]

    ticket_repository.update(ticket.ticket_id, status=status)
    context = access_service.load_snapshot_context(ticket.channel_id or 0)

    assert context.ticket.status is status
    assert context.ticket.ticket_id == ticket.ticket_id
    assert context.category.category_key == ticket.category_key


def test_load_snapshot_context_rejects_inactive_status(prepared_ticket_access_context) -> None:
    access_service = prepared_ticket_access_context["access_service"]
    ticket_repository = prepared_ticket_access_context["ticket_repository"]
    ticket = prepared_ticket_access_context["ticket"]

    ticket_repository.update(ticket.ticket_id, status=TicketStatus.DRAFT)

    with pytest.raises(InvalidTicketStateError, match="不支持查看快照或备注记录"):
        access_service.load_snapshot_context(ticket.channel_id or 0)


def test_assert_can_view_snapshots_accepts_creator_staff_admin_and_owner(
    prepared_ticket_access_context,
) -> None:
    access_service = prepared_ticket_access_context["access_service"]
    ticket = prepared_ticket_access_context["ticket"]
    creator = prepared_ticket_access_context["creator"]
    staff_member = prepared_ticket_access_context["staff_member"]
    explicit_staff_member = prepared_ticket_access_context["explicit_staff_member"]
    admin_member = prepared_ticket_access_context["admin_member"]
    outsider = prepared_ticket_access_context["outsider"]
    context = access_service.load_snapshot_context(ticket.channel_id or 0)

    access_service.assert_can_view_snapshots(creator, context=context, is_bot_owner=False)
    access_service.assert_can_view_snapshots(staff_member, context=context, is_bot_owner=False)
    access_service.assert_can_view_snapshots(explicit_staff_member, context=context, is_bot_owner=False)
    access_service.assert_can_view_snapshots(admin_member, context=context, is_bot_owner=False)
    access_service.assert_can_view_snapshots(outsider, context=context, is_bot_owner=True)


def test_assert_can_view_snapshots_rejects_outsider(prepared_ticket_access_context) -> None:
    access_service = prepared_ticket_access_context["access_service"]
    ticket = prepared_ticket_access_context["ticket"]
    outsider = prepared_ticket_access_context["outsider"]
    context = access_service.load_snapshot_context(ticket.channel_id or 0)

    with pytest.raises(PermissionDeniedError, match="ticket 创建者"):
        access_service.assert_can_view_snapshots(outsider, context=context, is_bot_owner=False)


def test_assert_can_manage_notes_accepts_staff_admin_and_owner(prepared_ticket_access_context) -> None:
    access_service = prepared_ticket_access_context["access_service"]
    ticket = prepared_ticket_access_context["ticket"]
    staff_member = prepared_ticket_access_context["staff_member"]
    explicit_staff_member = prepared_ticket_access_context["explicit_staff_member"]
    admin_member = prepared_ticket_access_context["admin_member"]
    outsider = prepared_ticket_access_context["outsider"]
    context = access_service.load_snapshot_context(ticket.channel_id or 0)

    access_service.assert_can_manage_notes(staff_member, context=context, is_bot_owner=False)
    access_service.assert_can_manage_notes(explicit_staff_member, context=context, is_bot_owner=False)
    access_service.assert_can_manage_notes(admin_member, context=context, is_bot_owner=False)
    access_service.assert_can_manage_notes(outsider, context=context, is_bot_owner=True)


def test_assert_can_manage_notes_rejects_creator(prepared_ticket_access_context) -> None:
    access_service = prepared_ticket_access_context["access_service"]
    ticket = prepared_ticket_access_context["ticket"]
    creator = prepared_ticket_access_context["creator"]
    context = access_service.load_snapshot_context(ticket.channel_id or 0)

    with pytest.raises(PermissionDeniedError, match="只有当前分类 staff"):
        access_service.assert_can_manage_notes(creator, context=context, is_bot_owner=False)
