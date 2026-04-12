from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord
import pytest

from core.enums import ClaimMode, TicketPriority, TicketStatus
from core.errors import StaleInteractionError
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository
from discord_ui.staff_panel_view import StaffPanelView, build_staff_panel_custom_id
from runtime.debounce import DebounceManager
from services.staff_panel_service import StaffPanelService


class FakeMessage:
    def __init__(
        self,
        message_id: int,
        channel: FakeChannel,
        *,
        embed: discord.Embed | None = None,
        view=None,
    ) -> None:
        self.id = message_id
        self.channel = channel
        self.embed = embed
        self.view = view
        self.edit_calls: list[dict[str, object]] = []

    async def edit(self, *, embed=None, view=None) -> None:
        self.embed = embed
        self.view = view
        self.edit_calls.append({"embed": embed, "view": view})


class FakeChannel:
    def __init__(self, channel_id: int, guild_id: int) -> None:
        self.id = channel_id
        self.guild = SimpleNamespace(id=guild_id)
        self.messages: dict[int, FakeMessage] = {}
        self.next_message_id = 8000
        self.sent_messages: list[FakeMessage] = []

    def add_message(self, message: FakeMessage) -> None:
        self.messages[message.id] = message

    async def send(self, *, content=None, embed=None, view=None) -> FakeMessage:
        message = FakeMessage(self.next_message_id, self, embed=embed, view=view)
        self.next_message_id += 1
        self.sent_messages.append(message)
        self.add_message(message)
        return message

    async def fetch_message(self, message_id: int) -> FakeMessage:
        if message_id not in self.messages:
            raise LookupError("message not found")
        return self.messages[message_id]


class FakeBot:
    def __init__(self, channel: FakeChannel) -> None:
        self.channel = channel

    def get_channel(self, channel_id: int) -> FakeChannel | None:
        return self.channel if self.channel.id == channel_id else None

    async def fetch_channel(self, channel_id: int) -> FakeChannel:
        if self.channel.id != channel_id:
            raise LookupError("channel not found")
        return self.channel


def get_panel_child(view: StaffPanelView, action: str):
    return next(child for child in view.children if getattr(child, "custom_id", None) == build_staff_panel_custom_id(action))


@pytest.fixture
def prepared_staff_panel_context(migrated_database):
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
    category = guild_repository.upsert_category(
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
            priority=TicketPriority.MEDIUM,
            staff_panel_message_id=5001,
        )
    )

    channel = FakeChannel(ticket.channel_id or 9001, ticket.guild_id)
    message = FakeMessage(ticket.staff_panel_message_id or 5001, channel)
    channel.add_message(message)

    return {
        "database": migrated_database,
        "ticket_repository": ticket_repository,
        "category": category,
        "ticket": ticket,
        "channel": channel,
        "message": message,
    }


@pytest.mark.asyncio
async def test_refresh_now_updates_staff_panel_embed_with_latest_ticket_state(
    prepared_staff_panel_context,
) -> None:
    database = prepared_staff_panel_context["database"]
    ticket_repository = prepared_staff_panel_context["ticket_repository"]
    ticket = prepared_staff_panel_context["ticket"]
    message = prepared_staff_panel_context["message"]
    service = StaffPanelService(database, bot=FakeBot(prepared_staff_panel_context["channel"]))

    ticket_repository.update(
        ticket.ticket_id,
        priority=TicketPriority.HIGH,
        claimed_by=301,
    )

    result = await service.refresh_now(ticket_id=ticket.ticket_id)

    assert result.refreshed is True
    assert result.recovered is False
    assert result.message is message
    assert message.edit_calls
    assert message.embed is not None
    assert isinstance(message.view, StaffPanelView)
    fields = {field.name: field.value for field in message.embed.fields}
    assert message.embed.title == "🛠️ Staff 控制面板"
    assert fields["认领模式"] == "relaxed 协作模式"
    assert fields["优先级"] == "高 🔴"
    assert fields["当前认领者"] == "<@301>"
    assert fields["最近用户消息"] == "2024-01-01T01:00:00+00:00"
    assert fields["分类"] == "技术支持"
    assert get_panel_child(message.view, "claim").disabled is False
    assert get_panel_child(message.view, "unclaim").disabled is False
    assert get_panel_child(message.view, "priority").disabled is False
    assert get_panel_child(message.view, "sleep").disabled is False
    assert get_panel_child(message.view, "close").disabled is False
    assert get_panel_child(message.view, "rename").disabled is False


@pytest.mark.asyncio
async def test_refresh_now_renders_sleep_status_and_previous_priority(
    prepared_staff_panel_context,
) -> None:
    database = prepared_staff_panel_context["database"]
    ticket_repository = prepared_staff_panel_context["ticket_repository"]
    ticket = prepared_staff_panel_context["ticket"]
    message = prepared_staff_panel_context["message"]
    service = StaffPanelService(database, bot=FakeBot(prepared_staff_panel_context["channel"]))

    ticket_repository.update(
        ticket.ticket_id,
        status=TicketStatus.SLEEP,
        priority=TicketPriority.SLEEP,
        priority_before_sleep=TicketPriority.HIGH,
    )

    await service.refresh_now(ticket_id=ticket.ticket_id)

    assert message.embed is not None
    fields = {field.name: field.value for field in message.embed.fields}
    assert fields["状态"] == "sleep 挂起中"
    assert fields["优先级"] == "挂起 💤（睡前：高 🔴）"
    assert "sleep 挂起状态" in (message.embed.description or "")
    assert "已禁用认领 / 取消认领 / 优先级控件" in (message.embed.description or "")
    assert get_panel_child(message.view, "claim").disabled is True
    assert get_panel_child(message.view, "unclaim").disabled is True
    assert get_panel_child(message.view, "priority").disabled is True
    assert get_panel_child(message.view, "sleep").disabled is True
    assert get_panel_child(message.view, "close").disabled is False
    assert get_panel_child(message.view, "rename").disabled is False


@pytest.mark.asyncio
async def test_refresh_now_renders_transferring_status_with_target_category(
    prepared_staff_panel_context,
) -> None:
    database = prepared_staff_panel_context["database"]
    ticket_repository = prepared_staff_panel_context["ticket_repository"]
    ticket = prepared_staff_panel_context["ticket"]
    message = prepared_staff_panel_context["message"]
    service = StaffPanelService(database, bot=FakeBot(prepared_staff_panel_context["channel"]))

    ticket_repository.update(
        ticket.ticket_id,
        status=TicketStatus.TRANSFERRING,
        status_before=TicketStatus.SUBMITTED,
        transfer_target_category="billing",
        transfer_initiated_by=301,
        transfer_execute_at="2024-01-01T00:05:00+00:00",
        transfer_reason="需要账单组处理",
    )

    await service.refresh_now(ticket_id=ticket.ticket_id)

    assert message.embed is not None
    fields = {field.name: field.value for field in message.embed.fields}
    assert fields["状态"] == "transferring 转交中"
    assert "跨分类转交" in (message.embed.description or "")
    assert "5 分钟后自动执行" in (message.embed.description or "")
    assert "计划执行时间：2024-01-01T00:05:00+00:00" in (message.embed.description or "")
    assert "`billing`" in (message.embed.description or "")
    assert "已禁用认领 / 取消认领 / 优先级控件" in (message.embed.description or "")
    assert "计划执行时间：2024-01-01T00:05:00+00:00" in fields["转交信息"]
    assert "/ticket untransfer" in (message.embed.description or "")
    assert "untransfer" in (message.embed.footer.text or "")
    assert get_panel_child(message.view, "claim").disabled is True
    assert get_panel_child(message.view, "unclaim").disabled is True
    assert get_panel_child(message.view, "priority").disabled is True
    assert get_panel_child(message.view, "sleep").disabled is True
    assert get_panel_child(message.view, "close").disabled is True
    assert get_panel_child(message.view, "rename").disabled is True


@pytest.mark.asyncio
async def test_request_refresh_debounces_multiple_updates_into_single_message_edit(
    prepared_staff_panel_context,
) -> None:
    database = prepared_staff_panel_context["database"]
    ticket_repository = prepared_staff_panel_context["ticket_repository"]
    ticket = prepared_staff_panel_context["ticket"]
    message = prepared_staff_panel_context["message"]
    debounce_manager = DebounceManager()
    service = StaffPanelService(
        database,
        bot=FakeBot(prepared_staff_panel_context["channel"]),
        debounce_manager=debounce_manager,
        debounce_delay_seconds=0.03,
    )

    service.request_refresh(ticket.ticket_id)
    await asyncio.sleep(0)
    ticket_repository.update(ticket.ticket_id, priority=TicketPriority.EMERGENCY, claimed_by=302)
    service.request_refresh(ticket.ticket_id)

    await asyncio.sleep(0.08)
    await debounce_manager.shutdown()

    assert len(message.edit_calls) == 1
    assert message.embed is not None
    fields = {field.name: field.value for field in message.embed.fields}
    assert fields["优先级"] == "紧急 ‼️"
    assert fields["当前认领者"] == "<@302>"


@pytest.mark.asyncio
async def test_refresh_now_recovers_missing_staff_panel_message_by_reposting(
    prepared_staff_panel_context,
) -> None:
    database = prepared_staff_panel_context["database"]
    ticket_repository = prepared_staff_panel_context["ticket_repository"]
    ticket = prepared_staff_panel_context["ticket"]
    channel = prepared_staff_panel_context["channel"]
    channel.messages.clear()
    service = StaffPanelService(database, bot=FakeBot(channel))

    result = await service.refresh_now(ticket_id=ticket.ticket_id)
    stored = ticket_repository.get_by_ticket_id(ticket.ticket_id)

    assert result.refreshed is True
    assert result.recovered is True
    assert result.message is not None
    assert isinstance(result.message.view, StaffPanelView)
    assert channel.sent_messages
    assert stored is not None
    assert stored.staff_panel_message_id == result.message.id


def test_assert_current_panel_interaction_rejects_stale_message_id(prepared_staff_panel_context) -> None:
    database = prepared_staff_panel_context["database"]
    channel = prepared_staff_panel_context["channel"]
    service = StaffPanelService(database, bot=FakeBot(channel))

    with pytest.raises(StaleInteractionError, match="控制面板已过期"):
        service.assert_current_panel_interaction(channel_id=channel.id, message_id=4999)
