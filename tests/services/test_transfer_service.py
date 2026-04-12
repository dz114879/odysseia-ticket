from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from core.enums import ClaimMode, TicketPriority, TicketStatus
from core.errors import InvalidTicketStateError, PermissionDeniedError, ValidationError
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository
from runtime.locks import LockManager
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
class FakeMessage:
    content: str


class FakeChannel:
    def __init__(self, channel_id: int, *, guild=None) -> None:
        self.id = channel_id
        self.guild = guild
        self.sent_messages: list[FakeMessage] = []
        self.permission_calls: list[dict[str, object]] = []

    async def send(self, *, content: str | None = None, embed=None, view=None):
        message = FakeMessage(content or "")
        self.sent_messages.append(message)
        return message

    async def set_permissions(self, target, *, overwrite=None, reason: str | None = None):
        self.permission_calls.append(
            {
                "target": target,
                "overwrite": overwrite,
                "reason": reason,
            }
        )


class FakeGuild:
    def __init__(self, *, roles: list[FakeRole], members: list[FakeMember]) -> None:
        self._roles = {role.id: role for role in roles}
        self._members = {member.id: member for member in members}

    def get_role(self, role_id: int):
        return self._roles.get(role_id)

    def get_member(self, member_id: int):
        return self._members.get(member_id)


class FakeBot:
    def __init__(self, channels: list[FakeChannel]) -> None:
        self._channels = {channel.id: channel for channel in channels}

    def get_channel(self, channel_id: int):
        return self._channels.get(channel_id)

    async def fetch_channel(self, channel_id: int):
        return self._channels.get(channel_id)


class FakeLoggingService:
    def __init__(self) -> None:
        self.ticket_logs: list[dict[str, object]] = []

    async def send_guild_log(self, *args, **kwargs) -> bool:
        return False

    async def send_ticket_log(self, **kwargs) -> bool:
        self.ticket_logs.append(kwargs)
        return True


class FakeStaffPanelService:
    def __init__(self) -> None:
        self.requested_ticket_ids: list[str] = []

    def request_refresh(self, ticket_id: str) -> None:
        self.requested_ticket_ids.append(ticket_id)


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
            is_enabled=True,
            allowlist_role_ids_json="[]",
            denylist_role_ids_json="[]",
            sort_order=1,
        )
    )
    billing_category = guild_repository.upsert_category(
        TicketCategoryConfig(
            guild_id=1,
            category_key="billing",
            display_name="账单咨询",
            emoji="💳",
            description="处理账单问题",
            staff_role_id=600,
            staff_user_ids_json="[]",
            is_enabled=True,
            allowlist_role_ids_json="[]",
            denylist_role_ids_json="[]",
            sort_order=2,
        )
    )

    staff_role = FakeRole(500)
    billing_role = FakeRole(600)
    admin_role = FakeRole(400)
    staff_member = FakeMember(301, roles=[staff_role])
    explicit_staff_member = FakeMember(302)
    billing_staff_member = FakeMember(303, roles=[billing_role])
    outsider = FakeMember(999)
    guild = FakeGuild(roles=[admin_role, staff_role, billing_role], members=[staff_member, explicit_staff_member, billing_staff_member, outsider])

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

    context = {
        "database": migrated_database,
        "guild_repository": guild_repository,
        "ticket_repository": ticket_repository,
        "ticket": ticket,
        "billing_category": billing_category,
        "channel": FakeChannel(ticket.channel_id or 9001, guild=guild),
        "staff_member": staff_member,
        "explicit_staff_member": explicit_staff_member,
        "billing_staff_member": billing_staff_member,
        "outsider": outsider,
        "guild": guild,
    }
    context["bot"] = FakeBot([context["channel"]])
    return context


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
            is_enabled=False,
            allowlist_role_ids_json="[]",
            denylist_role_ids_json="[]",
            sort_order=2,
        )
    )

    with pytest.raises(ValidationError, match="没有其他可转交的启用分类"):
        service.inspect_transfer_request(channel, actor=staff_member)


@pytest.mark.asyncio
async def test_transfer_ticket_updates_status_and_transfer_fields(prepared_transfer_context) -> None:
    database = prepared_transfer_context["database"]
    channel = prepared_transfer_context["channel"]
    staff_member = prepared_transfer_context["staff_member"]
    ticket_repository = prepared_transfer_context["ticket_repository"]
    staff_panel_service = FakeStaffPanelService()
    service = TransferService(
        database,
        lock_manager=LockManager(),
        staff_panel_service=staff_panel_service,
    )

    result = await service.transfer_ticket(
        channel,
        actor=staff_member,
        target_category_key="billing",
        reason="需要账单组处理",
        now="2024-01-01T00:00:00+00:00",
    )
    stored = ticket_repository.get_by_channel_id(channel.id)

    assert result.changed is True
    assert result.previous_status is TicketStatus.SUBMITTED
    assert result.target_category.category_key == "billing"
    assert result.target_category.display_name == "账单咨询"
    assert result.current_claimer_id is None
    assert result.reason == "需要账单组处理"
    assert result.execute_at == "2024-01-01T00:05:00+00:00"
    assert stored is not None
    assert stored.status is TicketStatus.TRANSFERRING
    assert stored.status_before is TicketStatus.SUBMITTED
    assert stored.transfer_target_category == "billing"
    assert stored.transfer_initiated_by == staff_member.id
    assert stored.transfer_reason == "需要账单组处理"
    assert stored.transfer_execute_at == "2024-01-01T00:05:00+00:00"
    assert channel.sent_messages
    assert "已发起 ticket `1-support-0001` 的跨分类转交" in channel.sent_messages[0].content
    assert "目标分类：账单咨询 (`billing`)" in channel.sent_messages[0].content
    assert "转交理由：需要账单组处理" in channel.sent_messages[0].content
    assert "计划执行时间：2024-01-01T00:05:00+00:00" in channel.sent_messages[0].content
    assert staff_panel_service.requested_ticket_ids == [prepared_transfer_context["ticket"].ticket_id]


@pytest.mark.asyncio
async def test_transfer_ticket_from_sleep_preserves_sleep_priority_metadata(prepared_transfer_context) -> None:
    database = prepared_transfer_context["database"]
    channel = prepared_transfer_context["channel"]
    staff_member = prepared_transfer_context["staff_member"]
    ticket_repository = prepared_transfer_context["ticket_repository"]
    service = TransferService(database, lock_manager=LockManager())
    ticket_repository.update(
        prepared_transfer_context["ticket"].ticket_id,
        status=TicketStatus.SLEEP,
        priority=TicketPriority.SLEEP,
        priority_before_sleep=TicketPriority.HIGH,
    )

    result = await service.transfer_ticket(
        channel,
        actor=staff_member,
        target_category_key="billing",
        now="2024-01-01T00:00:00+00:00",
    )
    stored = ticket_repository.get_by_channel_id(channel.id)

    assert result.previous_status is TicketStatus.SLEEP
    assert result.execute_at == "2024-01-01T00:05:00+00:00"
    assert stored is not None
    assert stored.status is TicketStatus.TRANSFERRING
    assert stored.status_before is TicketStatus.SLEEP
    assert stored.priority is TicketPriority.SLEEP
    assert stored.priority_before_sleep is TicketPriority.HIGH
    assert stored.transfer_execute_at == "2024-01-01T00:05:00+00:00"


@pytest.mark.asyncio
async def test_transfer_ticket_from_sleep_rejects_when_active_capacity_is_full(prepared_transfer_context) -> None:
    database = prepared_transfer_context["database"]
    channel = prepared_transfer_context["channel"]
    staff_member = prepared_transfer_context["staff_member"]
    ticket_repository = prepared_transfer_context["ticket_repository"]
    GuildRepository(database).update_config(1, max_open_tickets=1)
    ticket_repository.update(
        prepared_transfer_context["ticket"].ticket_id,
        status=TicketStatus.SLEEP,
        priority=TicketPriority.SLEEP,
        priority_before_sleep=TicketPriority.HIGH,
    )
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
    service = TransferService(database, lock_manager=LockManager())

    with pytest.raises(ValidationError, match="active 容量已满（1/1）"):
        await service.transfer_ticket(channel, actor=staff_member, target_category_key="billing")

    stored = ticket_repository.get_by_channel_id(channel.id)
    assert stored is not None and stored.status is TicketStatus.SLEEP
    assert channel.sent_messages == []


@pytest.mark.asyncio
async def test_transfer_ticket_rejects_unknown_target_category(prepared_transfer_context) -> None:
    service = TransferService(prepared_transfer_context["database"], lock_manager=LockManager())

    with pytest.raises(ValidationError, match="目标分类不存在"):
        await service.transfer_ticket(
            prepared_transfer_context["channel"],
            actor=prepared_transfer_context["staff_member"],
            target_category_key="unknown",
        )


@pytest.mark.asyncio
async def test_cancel_transfer_restores_submitted_and_clears_transfer_fields(prepared_transfer_context) -> None:
    database = prepared_transfer_context["database"]
    channel = prepared_transfer_context["channel"]
    staff_member = prepared_transfer_context["staff_member"]
    ticket_repository = prepared_transfer_context["ticket_repository"]
    staff_panel_service = FakeStaffPanelService()
    service = TransferService(
        database,
        lock_manager=LockManager(),
        staff_panel_service=staff_panel_service,
    )
    ticket_repository.update(
        prepared_transfer_context["ticket"].ticket_id,
        status=TicketStatus.TRANSFERRING,
        status_before=TicketStatus.SUBMITTED,
        transfer_target_category="billing",
        transfer_initiated_by=staff_member.id,
        transfer_reason="需要账单组处理",
    )

    result = await service.cancel_transfer(channel, actor=staff_member)
    stored = ticket_repository.get_by_channel_id(channel.id)

    assert result.changed is True
    assert result.restored_status is TicketStatus.SUBMITTED
    assert result.previous_target_category_key == "billing"
    assert result.reason == "需要账单组处理"
    assert stored is not None
    assert stored.status is TicketStatus.SUBMITTED
    assert stored.status_before is None
    assert stored.transfer_target_category is None
    assert stored.transfer_initiated_by is None
    assert stored.transfer_reason is None
    assert stored.transfer_execute_at is None
    assert channel.sent_messages
    assert "已撤销 ticket `1-support-0001` 的跨分类转交" in channel.sent_messages[0].content
    assert "恢复状态：submitted 处理中" in channel.sent_messages[0].content
    assert "原目标分类：`billing`" in channel.sent_messages[0].content
    assert staff_panel_service.requested_ticket_ids == [prepared_transfer_context["ticket"].ticket_id]


@pytest.mark.asyncio
async def test_cancel_transfer_restores_sleep_and_preserves_sleep_priority_metadata(prepared_transfer_context) -> None:
    database = prepared_transfer_context["database"]
    channel = prepared_transfer_context["channel"]
    staff_member = prepared_transfer_context["staff_member"]
    ticket_repository = prepared_transfer_context["ticket_repository"]
    service = TransferService(database, lock_manager=LockManager())
    ticket_repository.update(
        prepared_transfer_context["ticket"].ticket_id,
        status=TicketStatus.TRANSFERRING,
        status_before=TicketStatus.SLEEP,
        priority=TicketPriority.SLEEP,
        priority_before_sleep=TicketPriority.HIGH,
        transfer_target_category="billing",
        transfer_initiated_by=staff_member.id,
    )

    result = await service.cancel_transfer(channel, actor=staff_member)
    stored = ticket_repository.get_by_channel_id(channel.id)

    assert result.restored_status is TicketStatus.SLEEP
    assert stored is not None
    assert stored.status is TicketStatus.SLEEP
    assert stored.priority is TicketPriority.SLEEP
    assert stored.priority_before_sleep is TicketPriority.HIGH


@pytest.mark.asyncio
async def test_cancel_transfer_rejects_invalid_missing_status_before(prepared_transfer_context) -> None:
    database = prepared_transfer_context["database"]
    ticket_repository = prepared_transfer_context["ticket_repository"]
    service = TransferService(database, lock_manager=LockManager())
    ticket_repository.update(
        prepared_transfer_context["ticket"].ticket_id,
        status=TicketStatus.TRANSFERRING,
        status_before=None,
        transfer_target_category="billing",
    )

    with pytest.raises(ValidationError, match="status_before"):
        await service.cancel_transfer(
            prepared_transfer_context["channel"],
            actor=prepared_transfer_context["staff_member"],
        )


@pytest.mark.asyncio
async def test_sweep_due_transfers_executes_transfer_updates_category_and_resets_permissions(
    prepared_transfer_context,
) -> None:
    database = prepared_transfer_context["database"]
    channel = prepared_transfer_context["channel"]
    bot = prepared_transfer_context["bot"]
    staff_member = prepared_transfer_context["staff_member"]
    billing_staff_member = prepared_transfer_context["billing_staff_member"]
    ticket_repository = prepared_transfer_context["ticket_repository"]
    staff_panel_service = FakeStaffPanelService()
    logging_service = FakeLoggingService()
    service = TransferService(
        database,
        bot=bot,
        lock_manager=LockManager(),
        staff_panel_service=staff_panel_service,
        logging_service=logging_service,
    )
    ticket_repository.update(
        prepared_transfer_context["ticket"].ticket_id,
        status=TicketStatus.TRANSFERRING,
        status_before=TicketStatus.SUBMITTED,
        claimed_by=staff_member.id,
        transfer_target_category="billing",
        transfer_initiated_by=staff_member.id,
        transfer_reason="需要账单组处理",
        transfer_execute_at="2024-01-01T00:05:00+00:00",
        transfer_history_json='[{"type":"transfer_requested"}]',
    )

    outcomes = await service.sweep_due_transfers(now="2024-01-01T00:05:00+00:00")
    stored = ticket_repository.get_by_channel_id(channel.id)

    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.previous_category_key == "support"
    assert outcome.target_category.category_key == "billing"
    assert outcome.restored_status is TicketStatus.SUBMITTED
    assert outcome.previous_claimer_id == staff_member.id
    assert outcome.guild_log_sent is True
    assert stored is not None
    assert stored.category_key == "billing"
    assert stored.claimed_by is None
    assert stored.status is TicketStatus.SUBMITTED
    assert stored.status_before is None
    assert stored.transfer_target_category is None
    assert stored.transfer_initiated_by is None
    assert stored.transfer_reason is None
    assert stored.transfer_execute_at is None
    history = json.loads(stored.transfer_history_json)
    assert len(history) == 2
    assert history[-1]["type"] == "transfer_executed"
    assert history[-1]["from_category_key"] == "support"
    assert history[-1]["to_category_key"] == "billing"
    assert history[-1]["restored_status"] == "submitted"
    assert channel.sent_messages
    assert "ticket `1-support-0001` 的跨分类转交已执行" in channel.sent_messages[0].content
    assert staff_panel_service.requested_ticket_ids == [prepared_transfer_context["ticket"].ticket_id]
    assert logging_service.ticket_logs
    assert logging_service.ticket_logs[0]["channel_id"] == 100

    permission_targets = {call["target"].id: call["overwrite"] for call in channel.permission_calls}
    assert permission_targets[500].view_channel is False
    assert permission_targets[302].view_channel is False
    assert permission_targets[600].view_channel is True
    assert permission_targets[600].send_messages is True
    assert 303 not in permission_targets or permission_targets[303].view_channel is not False
    assert permission_targets[staff_member.id].view_channel is False
    assert billing_staff_member.id not in permission_targets


@pytest.mark.asyncio
async def test_sweep_due_transfers_restores_sleep_and_preserves_sleep_priority_metadata(prepared_transfer_context) -> None:
    database = prepared_transfer_context["database"]
    channel = prepared_transfer_context["channel"]
    ticket_repository = prepared_transfer_context["ticket_repository"]
    service = TransferService(database, bot=prepared_transfer_context["bot"], lock_manager=LockManager())
    ticket_repository.update(
        prepared_transfer_context["ticket"].ticket_id,
        status=TicketStatus.TRANSFERRING,
        status_before=TicketStatus.SLEEP,
        priority=TicketPriority.SLEEP,
        priority_before_sleep=TicketPriority.HIGH,
        transfer_target_category="billing",
        transfer_execute_at="2024-01-01T00:05:00+00:00",
    )

    outcomes = await service.sweep_due_transfers(now="2024-01-01T00:05:00+00:00")
    stored = ticket_repository.get_by_channel_id(channel.id)

    assert len(outcomes) == 1
    assert stored is not None
    assert stored.category_key == "billing"
    assert stored.status is TicketStatus.SLEEP
    assert stored.priority is TicketPriority.SLEEP
    assert stored.priority_before_sleep is TicketPriority.HIGH
