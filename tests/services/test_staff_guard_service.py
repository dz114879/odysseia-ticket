from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from core.enums import ClaimMode, TicketStatus
from core.errors import InvalidTicketStateError, PermissionDeniedError
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository
from services.staff_guard_service import StaffGuardService


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
def prepared_staff_guard_context(migrated_database):
    guild_repository = GuildRepository(migrated_database)
    ticket_repository = TicketRepository(migrated_database)
    guard_service = StaffGuardService(
        migrated_database,
        guild_repository=guild_repository,
        ticket_repository=ticket_repository,
    )

    guild_repository.upsert_config(
        GuildConfigRecord(
            guild_id=1,
            is_initialized=True,
            log_channel_id=100,
            archive_channel_id=200,
            ticket_category_channel_id=300,
            admin_role_id=400,
            claim_mode=ClaimMode.STRICT,
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
            emoji="🛠️",
            description="处理技术问题",
            staff_role_ids_json='[500]',
            staff_user_ids_json="[302]",
            is_enabled=True,
            allowlist_role_ids_json="[]",
            denylist_role_ids_json="[]",
            sort_order=1,
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
    staff_member = FakeMember(301, roles=[staff_role])
    explicit_staff_member = FakeMember(302)
    admin_member = FakeMember(401, roles=[admin_role])
    outsider = FakeMember(999)

    return {
        "guard_service": guard_service,
        "ticket_repository": ticket_repository,
        "ticket": ticket,
        "staff_member": staff_member,
        "explicit_staff_member": explicit_staff_member,
        "admin_member": admin_member,
        "outsider": outsider,
    }


def test_load_ticket_context_returns_ticket_config_and_category(prepared_staff_guard_context) -> None:
    guard_service = prepared_staff_guard_context["guard_service"]
    ticket = prepared_staff_guard_context["ticket"]

    context = guard_service.load_ticket_context(
        ticket.channel_id or 0,
        allowed_statuses=(TicketStatus.SUBMITTED,),
        invalid_state_message="当前 ticket 不处于 submitted 状态，无法执行此操作。",
    )

    assert context.ticket.ticket_id == ticket.ticket_id
    assert context.config.guild_id == ticket.guild_id
    assert context.category.category_key == ticket.category_key


def test_load_ticket_context_rejects_invalid_status(prepared_staff_guard_context) -> None:
    guard_service = prepared_staff_guard_context["guard_service"]
    ticket_repository = prepared_staff_guard_context["ticket_repository"]
    ticket = prepared_staff_guard_context["ticket"]

    ticket_repository.update(ticket.ticket_id, status=TicketStatus.DRAFT)

    with pytest.raises(InvalidTicketStateError, match="submitted"):
        guard_service.load_ticket_context(
            ticket.channel_id or 0,
            allowed_statuses=(TicketStatus.SUBMITTED,),
            invalid_state_message="当前 ticket 不处于 submitted 状态，无法执行此操作。",
        )


def test_assert_staff_actor_accepts_role_user_admin_and_owner(prepared_staff_guard_context) -> None:
    guard_service = prepared_staff_guard_context["guard_service"]
    ticket = prepared_staff_guard_context["ticket"]
    staff_member = prepared_staff_guard_context["staff_member"]
    explicit_staff_member = prepared_staff_guard_context["explicit_staff_member"]
    admin_member = prepared_staff_guard_context["admin_member"]
    outsider = prepared_staff_guard_context["outsider"]
    context = guard_service.load_ticket_context(
        ticket.channel_id or 0,
        allowed_statuses=(TicketStatus.SUBMITTED,),
        invalid_state_message="当前 ticket 不处于 submitted 状态，无法执行此操作。",
    )

    guard_service.assert_staff_actor(
        staff_member,
        config=context.config,
        category=context.category,
        is_bot_owner=False,
    )
    guard_service.assert_staff_actor(
        explicit_staff_member,
        config=context.config,
        category=context.category,
        is_bot_owner=False,
    )
    guard_service.assert_staff_actor(
        admin_member,
        config=context.config,
        category=context.category,
        is_bot_owner=False,
    )
    guard_service.assert_staff_actor(
        outsider,
        config=context.config,
        category=context.category,
        is_bot_owner=True,
    )


def test_assert_staff_actor_rejects_outsider(prepared_staff_guard_context) -> None:
    guard_service = prepared_staff_guard_context["guard_service"]
    ticket = prepared_staff_guard_context["ticket"]
    outsider = prepared_staff_guard_context["outsider"]
    context = guard_service.load_ticket_context(
        ticket.channel_id or 0,
        allowed_statuses=(TicketStatus.SUBMITTED,),
        invalid_state_message="当前 ticket 不处于 submitted 状态，无法执行此操作。",
    )

    with pytest.raises(PermissionDeniedError, match="只有当前分类 staff"):
        guard_service.assert_staff_actor(
            outsider,
            config=context.config,
            category=context.category,
            is_bot_owner=False,
        )
