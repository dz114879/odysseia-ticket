from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from core.enums import ClaimMode, TicketStatus
from core.errors import InvalidTicketStateError, PermissionDeniedError, ValidationError
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository
from services.transfer_service import TransferService


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


@dataclass
class FakeChannel:
    id: int


@pytest.fixture
def prepared_transfer_context(migrated_database):
    guild_repository = GuildRepository(migrated_database)
    ticket_repository = TicketRepository(migrated_database)

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
            emoji="🛠️",
            description="处理技术问题",
            staff_role_id=500,
            staff_user_ids_json="[302]",
            extra_welcome_text="请说明具体错误。",
            is_enabled=True,
            allowlist_role_ids_json="[]",
            denylist_role_ids_json="[]",
            sort_order=1,
        )
    )
    guild_repository.upsert_category(
        TicketCategoryConfig(
            guild_id=1,
            category_key="billing",
            display_name="账单咨询",
            emoji="💳",
            description="处理账单问题",
            staff_role_id=600,
            staff_user_ids_json="[]",
            extra_welcome_text="请提供账单编号。",
            is_enabled=True,
            allowlist_role_ids_json="[]",
            denylist_role_ids_json="[]",
            sort_order=2,
        )
    )

    staff_role = FakeRole(500)
    staff_member = FakeMember(301, roles=[staff_role])
    explicit_staff_member = FakeMember(302)
    outsider = FakeMember(999)

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

    return {
        "database": migrated_database,
        "guild_repository": guild_repository,
        "ticket_repository": ticket_repository,
        "ticket": ticket,
        "channel": FakeChannel(ticket.channel_id or 9001),
        "staff_member": staff_member,
        "explicit_staff_member": explicit_staff_member,
        "outsider": outsider,
    }


def test_inspect_transfer_request_returns_enabled_target_categories_and_claimer(
    prepared_transfer_context,
) -> None:
    database = prepared_transfer_context["database"]
    channel = prepared_transfer_context["channel"]
    staff_member = prepared_transfer_context["staff_member"]
    ticket_repository = prepared_transfer_context["ticket_repository"]
    service = TransferService(database)

    ticket_repository.update(prepared_transfer_context["ticket"].ticket_id, claimed_by=staff_member.id)

    result = service.inspect_transfer_request(channel, actor=staff_member)

    assert result.context.ticket.ticket_id == prepared_transfer_context["ticket"].ticket_id
    assert [category.category_key for category in result.target_categories] == ["billing"]
    assert result.current_claimer_id == staff_member.id


def test_inspect_transfer_request_allows_sleep_status(prepared_transfer_context) -> None:
    database = prepared_transfer_context["database"]
    channel = prepared_transfer_context["channel"]
    staff_member = prepared_transfer_context["staff_member"]
    ticket_repository = prepared_transfer_context["ticket_repository"]
    service = TransferService(database)

    ticket_repository.update(prepared_transfer_context["ticket"].ticket_id, status=TicketStatus.SLEEP)

    result = service.inspect_transfer_request(channel, actor=staff_member)

    assert result.context.ticket.status is TicketStatus.SLEEP
    assert [category.category_key for category in result.target_categories] == ["billing"]


def test_inspect_transfer_request_rejects_non_staff_actor(prepared_transfer_context) -> None:
    database = prepared_transfer_context["database"]
    channel = prepared_transfer_context["channel"]
    outsider = prepared_transfer_context["outsider"]
    service = TransferService(database)

    with pytest.raises(PermissionDeniedError, match="只有当前分类 staff"):
        service.inspect_transfer_request(channel, actor=outsider)


def test_inspect_transfer_request_rejects_invalid_status(prepared_transfer_context) -> None:
    database = prepared_transfer_context["database"]
    channel = prepared_transfer_context["channel"]
    staff_member = prepared_transfer_context["staff_member"]
    ticket_repository = prepared_transfer_context["ticket_repository"]
    service = TransferService(database)

    ticket_repository.update(prepared_transfer_context["ticket"].ticket_id, status=TicketStatus.TRANSFERRING)

    with pytest.raises(InvalidTicketStateError, match="submitted / sleep"):
        service.inspect_transfer_request(channel, actor=staff_member)


def test_inspect_transfer_request_rejects_non_claimer_when_already_claimed(prepared_transfer_context) -> None:
    database = prepared_transfer_context["database"]
    channel = prepared_transfer_context["channel"]
    staff_member = prepared_transfer_context["staff_member"]
    explicit_staff_member = prepared_transfer_context["explicit_staff_member"]
    ticket_repository = prepared_transfer_context["ticket_repository"]
    service = TransferService(database)

    ticket_repository.update(prepared_transfer_context["ticket"].ticket_id, claimed_by=staff_member.id)

    with pytest.raises(PermissionDeniedError, match="已被其他 staff 认领"):
        service.inspect_transfer_request(channel, actor=explicit_staff_member)


def test_inspect_transfer_request_rejects_when_no_other_enabled_category(prepared_transfer_context) -> None:
    database = prepared_transfer_context["database"]
    channel = prepared_transfer_context["channel"]
    staff_member = prepared_transfer_context["staff_member"]
    guild_repository = prepared_transfer_context["guild_repository"]
    service = TransferService(database)

    guild_repository.upsert_category(
        TicketCategoryConfig(
            guild_id=1,
            category_key="billing",
            display_name="账单咨询",
            emoji="💳",
            description="处理账单问题",
            staff_role_id=600,
            staff_user_ids_json="[]",
            extra_welcome_text="请提供账单编号。",
            is_enabled=False,
            allowlist_role_ids_json="[]",
            denylist_role_ids_json="[]",
            sort_order=2,
        )
    )

    with pytest.raises(ValidationError, match="没有其他可转交的启用分类"):
        service.inspect_transfer_request(channel, actor=staff_member)
