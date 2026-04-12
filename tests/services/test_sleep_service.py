from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from core.enums import ClaimMode, TicketPriority, TicketStatus
from core.errors import InvalidTicketStateError, PermissionDeniedError
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository
from runtime.locks import LockManager
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
class FakeMessage:
    content: str


class FakeChannel:
    def __init__(self, channel_id: int, *, name: str, fail_edit: bool = False) -> None:
        self.id = channel_id
        self.name = name
        self.fail_edit = fail_edit
        self.edit_calls: list[dict[str, str | None]] = []
        self.sent_messages: list[FakeMessage] = []

    async def edit(self, *, name: str, reason: str | None = None) -> None:
        if self.fail_edit:
            raise RuntimeError("channel rename failed")
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
        "channel": FakeChannel(ticket.channel_id or 9001, name="🔴|login-error"),
        "staff_member": staff_member,
        "outsider": outsider,
    }


class FakeQueueService:
    def __init__(self) -> None:
        self.requested_guild_ids: list[int] = []

    async def process_next_queued_ticket(self, guild_id: int):
        self.requested_guild_ids.append(guild_id)
        return None


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


@pytest.mark.asyncio
async def test_sleep_ticket_updates_status_priority_channel_name_and_panel_refresh(prepared_sleep_context) -> None:
    database = prepared_sleep_context["database"]
    channel = prepared_sleep_context["channel"]
    staff_member = prepared_sleep_context["staff_member"]
    ticket_repository = prepared_sleep_context["ticket_repository"]
    staff_panel_service = FakeStaffPanelService()
    service = SleepService(
        database,
        lock_manager=LockManager(),
        staff_panel_service=staff_panel_service,
    )

    result = await service.sleep_ticket(channel, actor=staff_member)
    stored = ticket_repository.get_by_channel_id(channel.id)

    assert result.changed is True
    assert result.previous_priority is TicketPriority.HIGH
    assert result.old_channel_name == "🔴|login-error"
    assert result.new_channel_name == "💤|login-error"
    assert result.channel_name_changed is True
    assert stored is not None
    assert stored.status is TicketStatus.SLEEP
    assert stored.priority is TicketPriority.SLEEP
    assert stored.priority_before_sleep is TicketPriority.HIGH
    assert result.ticket.status is TicketStatus.SLEEP
    assert result.ticket.priority is TicketPriority.SLEEP
    assert result.ticket.priority_before_sleep is TicketPriority.HIGH
    assert channel.name == "💤|login-error"
    assert channel.edit_calls[0]["reason"] == "Put ticket 1-support-0001 to sleep"
    assert channel.sent_messages
    assert "已将 ticket `1-support-0001` 挂起" in channel.sent_messages[0].content
    assert "睡前优先级：高 🔴" in channel.sent_messages[0].content
    assert staff_panel_service.requested_ticket_ids == [prepared_sleep_context["ticket"].ticket_id]


@pytest.mark.asyncio
async def test_sleep_ticket_triggers_queue_fill_after_releasing_capacity(prepared_sleep_context) -> None:
    database = prepared_sleep_context["database"]
    channel = prepared_sleep_context["channel"]
    staff_member = prepared_sleep_context["staff_member"]
    queue_service = FakeQueueService()
    service = SleepService(
        database,
        lock_manager=LockManager(),
        queue_service=queue_service,
    )

    await service.sleep_ticket(channel, actor=staff_member)

    assert queue_service.requested_guild_ids == [1]


@pytest.mark.asyncio
async def test_handle_message_wakes_sleep_ticket_and_restores_previous_priority(prepared_sleep_context) -> None:
    database = prepared_sleep_context["database"]
    channel = prepared_sleep_context["channel"]
    ticket_repository = prepared_sleep_context["ticket_repository"]
    staff_panel_service = FakeStaffPanelService()
    service = SleepService(
        database,
        lock_manager=LockManager(),
        staff_panel_service=staff_panel_service,
    )
    ticket_repository.update(
        prepared_sleep_context["ticket"].ticket_id,
        status=TicketStatus.SLEEP,
        priority=TicketPriority.SLEEP,
        priority_before_sleep=TicketPriority.HIGH,
    )
    channel.name = "💤|login-error"
    message = SimpleNamespace(
        author=SimpleNamespace(id=777, bot=False),
        guild=SimpleNamespace(id=1),
        channel=channel,
    )

    result = await service.handle_message(message)
    stored = ticket_repository.get_by_channel_id(channel.id)

    assert result is not None
    assert result.restored_priority is TicketPriority.HIGH
    assert result.old_channel_name == "💤|login-error"
    assert result.new_channel_name == "🔴|login-error"
    assert stored is not None
    assert stored.status is TicketStatus.SUBMITTED
    assert stored.priority is TicketPriority.HIGH
    assert stored.priority_before_sleep is None
    assert channel.name == "🔴|login-error"
    assert channel.edit_calls[0]["reason"] == "Wake ticket 1-support-0001 from sleep"
    assert channel.sent_messages
    assert "已唤醒 ticket `1-support-0001`" in channel.sent_messages[0].content
    assert "恢复优先级：高 🔴" in channel.sent_messages[0].content
    assert staff_panel_service.requested_ticket_ids == [prepared_sleep_context["ticket"].ticket_id]


@pytest.mark.asyncio
async def test_handle_message_keeps_sleep_ticket_when_active_capacity_is_full(prepared_sleep_context) -> None:
    database = prepared_sleep_context["database"]
    channel = prepared_sleep_context["channel"]
    ticket_repository = prepared_sleep_context["ticket_repository"]
    service = SleepService(
        database,
        lock_manager=LockManager(),
    )
    ticket_repository.update(
        prepared_sleep_context["ticket"].ticket_id,
        status=TicketStatus.SLEEP,
        priority=TicketPriority.SLEEP,
        priority_before_sleep=TicketPriority.HIGH,
    )
    GuildRepository(database).update_config(1, max_open_tickets=1)
    ticket_repository.create(
        TicketRecord(
            ticket_id="1-support-0002",
            guild_id=1,
            creator_id=202,
            category_key="support",
            channel_id=9002,
            status=TicketStatus.SUBMITTED,
        )
    )

    result = await service.handle_message(SimpleNamespace(author=SimpleNamespace(id=777, bot=False), guild=SimpleNamespace(id=1), channel=channel))

    stored = ticket_repository.get_by_channel_id(channel.id)
    assert result is None
    assert stored is not None and stored.status is TicketStatus.SLEEP
    assert channel.sent_messages
    assert "active 容量已满（1/1）" in channel.sent_messages[0].content


@pytest.mark.asyncio
async def test_handle_message_returns_none_for_non_sleep_ticket(prepared_sleep_context) -> None:
    service = SleepService(prepared_sleep_context["database"], lock_manager=LockManager())
    message = SimpleNamespace(
        author=SimpleNamespace(id=777, bot=False),
        guild=SimpleNamespace(id=1),
        channel=prepared_sleep_context["channel"],
    )

    assert await service.handle_message(message) is None


@pytest.mark.asyncio
async def test_sleep_ticket_rolls_back_ticket_state_when_channel_rename_fails(prepared_sleep_context) -> None:
    database = prepared_sleep_context["database"]
    staff_member = prepared_sleep_context["staff_member"]
    ticket_repository = prepared_sleep_context["ticket_repository"]
    ticket = prepared_sleep_context["ticket"]
    failing_channel = FakeChannel(ticket.channel_id or 9001, name="login-error", fail_edit=True)
    service = SleepService(database, lock_manager=LockManager())

    with pytest.raises(RuntimeError, match="rename failed"):
        await service.sleep_ticket(failing_channel, actor=staff_member)

    stored = ticket_repository.get_by_channel_id(failing_channel.id)
    assert stored is not None
    assert stored.status is TicketStatus.SUBMITTED
    assert stored.priority is TicketPriority.HIGH
    assert stored.priority_before_sleep is None
    assert not failing_channel.sent_messages


@pytest.mark.asyncio
async def test_wake_ticket_rolls_back_state_when_channel_rename_fails(prepared_sleep_context) -> None:
    database = prepared_sleep_context["database"]
    ticket_repository = prepared_sleep_context["ticket_repository"]
    ticket = prepared_sleep_context["ticket"]
    staff_panel_service = FakeStaffPanelService()
    failing_channel = FakeChannel(ticket.channel_id or 9001, name="💤|login-error", fail_edit=True)
    service = SleepService(
        database,
        lock_manager=LockManager(),
        staff_panel_service=staff_panel_service,
    )
    ticket_repository.update(
        ticket.ticket_id,
        status=TicketStatus.SLEEP,
        priority=TicketPriority.SLEEP,
        priority_before_sleep=TicketPriority.HIGH,
    )

    with pytest.raises(RuntimeError, match="rename failed"):
        await service.wake_ticket(failing_channel, actor=SimpleNamespace(id=777))

    stored = ticket_repository.get_by_channel_id(failing_channel.id)
    assert stored is not None
    assert stored.status is TicketStatus.SLEEP
    assert stored.priority is TicketPriority.SLEEP
    assert stored.priority_before_sleep is TicketPriority.HIGH
    assert not failing_channel.sent_messages
    assert staff_panel_service.requested_ticket_ids == []
