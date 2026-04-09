from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from core.enums import ClaimMode, TicketPriority, TicketStatus
from core.errors import InvalidTicketStateError, PermissionDeniedError
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository
from services.sleep_service import SleepService


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
def prepared_sleep_context(migrated_database):
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
            staff_role_id=500,
            staff_user_ids_json="[]",
            extra_welcome_text="请说明具体错误。",
            is_enabled=True,
            allowlist_role_ids_json="[]",
            denylist_role_ids_json="[]",
            sort_order=1,
        )
    )

    staff_role = FakeRole(500)
    staff_member = FakeMember(301, roles=[staff_role])
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
            priority=TicketPriority.HIGH,
        )
    )

    return {
        "database": migrated_database,
        "ticket_repository": ticket_repository,
        "ticket": ticket,
        "channel": FakeChannel(ticket.channel_id or 9001),
        "staff_member": staff_member,
        "outsider": outsider,
    }


def test_inspect_sleep_request_returns_previous_priority_and_strict_mode(prepared_sleep_context) -> None:
    database = prepared_sleep_context["database"]
    channel = prepared_sleep_context["channel"]
    staff_member = prepared_sleep_context["staff_member"]
    service = SleepService(database)

    result = service.inspect_sleep_request(channel, actor=staff_member)

    assert result.context.ticket.ticket_id == prepared_sleep_context["ticket"].ticket_id
    assert result.previous_priority is TicketPriority.HIGH
    assert result.strict_mode is True


def test_inspect_sleep_request_rejects_non_staff_actor(prepared_sleep_context) -> None:
    database = prepared_sleep_context["database"]
    channel = prepared_sleep_context["channel"]
    outsider = prepared_sleep_context["outsider"]
    service = SleepService(database)

    with pytest.raises(PermissionDeniedError, match="只有当前分类 staff"):
        service.inspect_sleep_request(channel, actor=outsider)


def test_inspect_sleep_request_rejects_non_submitted_ticket(prepared_sleep_context) -> None:
    database = prepared_sleep_context["database"]
    channel = prepared_sleep_context["channel"]
    staff_member = prepared_sleep_context["staff_member"]
    ticket_repository = prepared_sleep_context["ticket_repository"]
    service = SleepService(database)

    ticket_repository.update(prepared_sleep_context["ticket"].ticket_id, status=TicketStatus.SLEEP)

    with pytest.raises(InvalidTicketStateError, match="submitted"):
        service.inspect_sleep_request(channel, actor=staff_member)
