from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from core.enums import ClaimMode, TicketPriority, TicketStatus
from core.errors import ValidationError
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository
from runtime.locks import LockManager
from services.priority_service import PriorityService


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
class FakeMessage:
    content: str


class FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id
        self.roles: dict[int, FakeRole] = {}
        self.members: dict[int, FakeMember] = {}

    def add_role(self, role: FakeRole) -> None:
        self.roles[role.id] = role

    def add_member(self, member: FakeMember) -> None:
        self.members[member.id] = member

    def get_role(self, role_id: int) -> FakeRole | None:
        return self.roles.get(role_id)

    def get_member(self, member_id: int) -> FakeMember | None:
        return self.members.get(member_id)


class FakeChannel:
    def __init__(self, channel_id: int, guild: FakeGuild, *, name: str) -> None:
        self.id = channel_id
        self.guild = guild
        self.name = name
        self.edit_calls: list[dict[str, str | None]] = []
        self.sent_messages: list[FakeMessage] = []

    async def edit(self, *, name: str, reason: str | None = None) -> None:
        self.edit_calls.append({"name": name, "reason": reason})
        self.name = name

    async def send(self, *, content: str | None = None, embed=None, view=None):
        message = FakeMessage(content or "")
        self.sent_messages.append(message)
        return message


class FakeStaffPanelService:
    def __init__(self) -> None:
        self.requested_ticket_ids: list[str] = []

    def request_refresh(self, ticket_id: str) -> None:
        self.requested_ticket_ids.append(ticket_id)


@pytest.fixture
def prepared_priority_context(migrated_database):
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
            staff_role_ids_json="[500]",
            staff_user_ids_json="[]",
            is_enabled=True,
            allowlist_role_ids_json="[]",
            denylist_role_ids_json="[]",
            sort_order=1,
        )
    )

    admin_role = FakeRole(400)
    staff_role = FakeRole(500)
    guild = FakeGuild(1)
    guild.add_role(admin_role)
    guild.add_role(staff_role)

    creator = FakeMember(201)
    staff_member = FakeMember(301, roles=[staff_role])
    guild.add_member(creator)
    guild.add_member(staff_member)

    ticket = ticket_repository.create(
        TicketRecord(
            ticket_id="1-support-0001",
            guild_id=1,
            creator_id=creator.id,
            category_key="support",
            channel_id=9001,
            status=TicketStatus.SUBMITTED,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
            has_user_message=True,
            last_user_message_at="2024-01-01T01:00:00+00:00",
            priority=TicketPriority.MEDIUM,
        )
    )
    channel = FakeChannel(ticket.channel_id or 9001, guild, name="login-error")

    return {
        "database": migrated_database,
        "ticket_repository": ticket_repository,
        "channel": channel,
        "ticket": ticket,
        "staff_member": staff_member,
    }


@pytest.mark.asyncio
async def test_set_priority_updates_ticket_and_channel_prefix(prepared_priority_context) -> None:
    database = prepared_priority_context["database"]
    channel = prepared_priority_context["channel"]
    staff_member = prepared_priority_context["staff_member"]
    staff_panel_service = FakeStaffPanelService()
    service = PriorityService(database, lock_manager=LockManager(), staff_panel_service=staff_panel_service)

    result = await service.set_priority(
        channel,
        actor=staff_member,
        priority=TicketPriority.HIGH,
    )

    stored = prepared_priority_context["ticket_repository"].get_by_channel_id(channel.id)

    assert result.changed is True
    assert result.priority_changed is True
    assert result.channel_name_changed is True
    assert result.old_priority is TicketPriority.MEDIUM
    assert result.new_priority is TicketPriority.HIGH
    assert result.new_channel_name == "🔴|login-error"
    assert channel.name == "🔴|login-error"
    assert channel.edit_calls[0]["reason"] == "Set ticket 1-support-0001 priority to high"
    assert stored is not None
    assert stored.priority is TicketPriority.HIGH
    assert channel.sent_messages
    assert "优先级" in channel.sent_messages[0].content
    assert staff_panel_service.requested_ticket_ids == [result.ticket_id]
    assert "高" in channel.sent_messages[0].content


@pytest.mark.asyncio
async def test_set_priority_replaces_existing_priority_prefix(prepared_priority_context) -> None:
    database = prepared_priority_context["database"]
    channel = prepared_priority_context["channel"]
    staff_member = prepared_priority_context["staff_member"]
    channel.name = "🟡|login-error"
    service = PriorityService(database, lock_manager=LockManager())

    result = await service.set_priority(
        channel,
        actor=staff_member,
        priority=TicketPriority.LOW,
    )

    assert result.changed is True
    assert result.new_channel_name == "🟢|login-error"
    assert channel.name == "🟢|login-error"


@pytest.mark.asyncio
async def test_set_priority_is_noop_when_priority_and_prefix_are_already_current(
    prepared_priority_context,
) -> None:
    database = prepared_priority_context["database"]
    channel = prepared_priority_context["channel"]
    staff_member = prepared_priority_context["staff_member"]
    channel.name = "🟡|login-error"
    staff_panel_service = FakeStaffPanelService()
    service = PriorityService(database, lock_manager=LockManager(), staff_panel_service=staff_panel_service)

    result = await service.set_priority(
        channel,
        actor=staff_member,
        priority=TicketPriority.MEDIUM,
    )

    assert result.changed is False
    assert result.priority_changed is False
    assert result.channel_name_changed is False
    assert staff_panel_service.requested_ticket_ids == []
    assert not channel.edit_calls
    assert not channel.sent_messages


@pytest.mark.asyncio
async def test_set_priority_rejects_sleep_display_priority(prepared_priority_context) -> None:
    database = prepared_priority_context["database"]
    channel = prepared_priority_context["channel"]
    staff_member = prepared_priority_context["staff_member"]
    service = PriorityService(database, lock_manager=LockManager())

    with pytest.raises(ValidationError, match="sleep"):
        await service.set_priority(
            channel,
            actor=staff_member,
            priority=TicketPriority.SLEEP,
        )
