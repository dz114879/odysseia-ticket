from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from cogs.staff_cog import StaffCog
from core.enums import ClaimMode, TicketPriority, TicketStatus
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketMuteRecord, TicketRecord
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_mute_repository import TicketMuteRepository
from db.repositories.ticket_repository import TicketRepository
from discord_ui.staff_panel_view import StaffPanelView, build_staff_panel_custom_id
from runtime.locks import LockManager


@dataclass(frozen=True)
class FakeRole:
    id: int


@dataclass
class FakeUser:
    id: int
    roles: list[FakeRole] = field(default_factory=list)
    administrator: bool = False
    bot: bool = False

    @property
    def guild_permissions(self) -> SimpleNamespace:
        return SimpleNamespace(administrator=self.administrator)


@dataclass
class FakeGuild:
    id: int
    roles: dict[int, FakeRole] = field(default_factory=dict)
    members: dict[int, FakeUser] = field(default_factory=dict)

    def add_role(self, role: FakeRole) -> None:
        self.roles[role.id] = role

    def add_member(self, member: FakeUser) -> None:
        self.members[member.id] = member

    def get_role(self, role_id: int) -> FakeRole | None:
        return self.roles.get(role_id)

    def get_member(self, member_id: int) -> FakeUser | None:
        return self.members.get(member_id)


class FakeChannel:
    def __init__(self, channel_id: int, guild: FakeGuild, *, name: str) -> None:
        self.id = channel_id
        self.guild = guild
        self.name = name
        self.permission_calls: list[dict[str, object]] = []
        self.edit_calls: list[dict[str, str | None]] = []
        self.sent_messages: list[str] = []

    async def edit(self, *, name: str, reason: str | None = None) -> None:
        self.edit_calls.append({"name": name, "reason": reason})
        self.name = name

    async def set_permissions(self, target, *, overwrite, reason: str | None = None) -> None:
        self.permission_calls.append(
            {
                "target_id": getattr(target, "id", None),
                "overwrite": overwrite,
                "reason": reason,
            }
        )

    async def send(self, *, content: str | None = None, embed=None, view=None):
        self.sent_messages.append(content or "")
        return SimpleNamespace(content=content or "")


class FakeResponse:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.deferred: list[dict[str, object]] = []
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def send_message(self, content: str, *, ephemeral: bool) -> None:
        self._done = True
        self.messages.append({"content": content, "ephemeral": ephemeral})

    async def defer(self, *, ephemeral: bool, thinking: bool) -> None:
        self._done = True
        self.deferred.append({"ephemeral": ephemeral, "thinking": thinking})


class FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send(self, content: str, *, ephemeral: bool) -> None:
        self.messages.append({"content": content, "ephemeral": ephemeral})


class FakeLoggingService:
    def __init__(self) -> None:
        self.info_messages: list[str] = []

    def log_local_info(self, message: str, *args) -> None:
        self.info_messages.append(message % args if args else message)

    async def send_guild_log(self, *args, **kwargs) -> bool:
        return False

    async def send_ticket_log(self, *args, **kwargs) -> bool:
        return False


class FakeBot:
    def __init__(self, migrated_database, *, is_owner_result: bool = False) -> None:
        self.resources = SimpleNamespace(
            database=migrated_database,
            logging_service=FakeLoggingService(),
            lock_manager=LockManager(),
            debounce_manager=None,
        )
        self._is_owner_result = is_owner_result
        self.added_views: list[dict[str, object]] = []

    async def is_owner(self, user) -> bool:
        return self._is_owner_result

    def add_view(self, view, message_id: int | None = None) -> None:
        self.added_views.append({"view": view, "message_id": message_id})


class FakeInteraction:
    def __init__(self, guild: FakeGuild | None, channel: FakeChannel | None, user: FakeUser, *, bot=None, message=None) -> None:
        self.guild = guild
        self.channel = channel
        self.user = user
        self.client = bot
        self.message = message
        self.response = FakeResponse()
        self.followup = FakeFollowup()


def assert_deferred_ephemeral_followup(interaction: FakeInteraction) -> dict[str, object]:
    assert interaction.response.deferred == [{"ephemeral": True, "thinking": True}]
    assert interaction.followup.messages
    assert interaction.followup.messages[0]["ephemeral"] is True
    return interaction.followup.messages[0]


@pytest.fixture
def prepared_staff_cog_context(migrated_database):
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
            staff_role_ids_json='[500]',
            staff_user_ids_json="[302]",
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
            staff_role_ids_json='[600]',
            staff_user_ids_json="[]",
            is_enabled=True,
            allowlist_role_ids_json="[]",
            denylist_role_ids_json="[]",
            sort_order=2,
        )
    )

    admin_role = FakeRole(400)
    staff_role = FakeRole(500)
    billing_role = FakeRole(600)
    guild = FakeGuild(1)
    guild.add_role(admin_role)
    guild.add_role(staff_role)
    guild.add_role(billing_role)

    creator = FakeUser(201)
    staff_user = FakeUser(301, roles=[staff_role])
    explicit_staff_user = FakeUser(302)
    outsider = FakeUser(999)
    admin_user = FakeUser(401, roles=[admin_role])
    for member in (creator, staff_user, explicit_staff_user, outsider, admin_user):
        guild.add_member(member)

    channel = FakeChannel(9001, guild, name="login-error")
    ticket_repository.create(
        TicketRecord(
            ticket_id="1-support-0001",
            guild_id=1,
            creator_id=creator.id,
            category_key="support",
            channel_id=channel.id,
            status=TicketStatus.SUBMITTED,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
            has_user_message=True,
            last_user_message_at="2024-01-01T01:00:00+00:00",
            staff_panel_message_id=5001,
        )
    )

    return {
        "bot": FakeBot(migrated_database),
        "guild": guild,
        "channel": channel,
        "creator": creator,
        "staff_user": staff_user,
        "explicit_staff_user": explicit_staff_user,
        "outsider": outsider,
        "admin_user": admin_user,
        "ticket_repository": ticket_repository,
        "panel_message": SimpleNamespace(id=5001),
    }


@pytest.mark.asyncio
async def test_claim_current_ticket_updates_ticket_and_returns_feedback(
    prepared_staff_cog_context,
) -> None:
    bot = prepared_staff_cog_context["bot"]
    guild = prepared_staff_cog_context["guild"]
    channel = prepared_staff_cog_context["channel"]
    staff_user = prepared_staff_cog_context["staff_user"]
    ticket_repository = prepared_staff_cog_context["ticket_repository"]
    cog = StaffCog(bot)
    interaction = FakeInteraction(guild, channel, staff_user)

    await cog.claim_current_ticket(interaction)

    stored = ticket_repository.get_by_channel_id(channel.id)
    feedback = assert_deferred_ephemeral_followup(interaction)

    assert "ticket 已认领" in feedback["content"]
    assert f"<@{staff_user.id}>" in feedback["content"]
    assert stored is not None
    assert stored.claimed_by == staff_user.id
    assert bot.resources.logging_service.info_messages
    assert "Ticket claimed." in bot.resources.logging_service.info_messages[0]


@pytest.mark.asyncio
async def test_claim_current_ticket_rejects_non_staff_user(prepared_staff_cog_context) -> None:
    bot = prepared_staff_cog_context["bot"]
    guild = prepared_staff_cog_context["guild"]
    channel = prepared_staff_cog_context["channel"]
    outsider = prepared_staff_cog_context["outsider"]
    cog = StaffCog(bot)
    interaction = FakeInteraction(guild, channel, outsider)

    await cog.claim_current_ticket(interaction)

    feedback = assert_deferred_ephemeral_followup(interaction)
    assert "只有当前分类 staff" in feedback["content"]


@pytest.mark.asyncio
async def test_unclaim_current_ticket_returns_feedback_for_current_claimer(
    prepared_staff_cog_context,
) -> None:
    bot = prepared_staff_cog_context["bot"]
    guild = prepared_staff_cog_context["guild"]
    channel = prepared_staff_cog_context["channel"]
    staff_user = prepared_staff_cog_context["staff_user"]
    ticket_repository = prepared_staff_cog_context["ticket_repository"]
    ticket_repository.update("1-support-0001", claimed_by=staff_user.id)
    cog = StaffCog(bot)
    interaction = FakeInteraction(guild, channel, staff_user)

    await cog.unclaim_current_ticket(interaction)

    stored = ticket_repository.get_by_channel_id(channel.id)
    feedback = assert_deferred_ephemeral_followup(interaction)

    assert "ticket 已取消认领" in feedback["content"]
    assert stored is not None
    assert stored.claimed_by is None
    assert bot.resources.logging_service.info_messages
    assert "Ticket unclaimed." in bot.resources.logging_service.info_messages[0]


@pytest.mark.asyncio
async def test_transfer_claim_current_ticket_updates_ticket_and_returns_feedback(
    prepared_staff_cog_context,
) -> None:
    bot = prepared_staff_cog_context["bot"]
    guild = prepared_staff_cog_context["guild"]
    channel = prepared_staff_cog_context["channel"]
    staff_user = prepared_staff_cog_context["staff_user"]
    explicit_staff_user = prepared_staff_cog_context["explicit_staff_user"]
    ticket_repository = prepared_staff_cog_context["ticket_repository"]
    ticket_repository.update("1-support-0001", claimed_by=staff_user.id)
    cog = StaffCog(bot)
    interaction = FakeInteraction(guild, channel, staff_user)

    await cog.transfer_claim_current_ticket(interaction, member=explicit_staff_user)

    stored = ticket_repository.get_by_channel_id(channel.id)
    feedback = assert_deferred_ephemeral_followup(interaction)

    assert "ticket 认领已转交" in feedback["content"]
    assert f"<@{staff_user.id}>" in feedback["content"]
    assert f"<@{explicit_staff_user.id}>" in feedback["content"]
    assert stored is not None
    assert stored.claimed_by == explicit_staff_user.id
    assert channel.sent_messages
    assert "转交给" in channel.sent_messages[0]
    assert bot.resources.logging_service.info_messages
    assert "Ticket claim transferred." in bot.resources.logging_service.info_messages[0]


@pytest.mark.asyncio
async def test_rename_current_ticket_updates_channel_and_returns_feedback(
    prepared_staff_cog_context,
) -> None:
    bot = prepared_staff_cog_context["bot"]
    guild = prepared_staff_cog_context["guild"]
    channel = prepared_staff_cog_context["channel"]
    staff_user = prepared_staff_cog_context["staff_user"]
    ticket_repository = prepared_staff_cog_context["ticket_repository"]
    cog = StaffCog(bot)
    interaction = FakeInteraction(guild, channel, staff_user)

    await cog.rename_current_ticket(interaction, title="登录异常 复现")

    stored = ticket_repository.get_by_channel_id(channel.id)
    feedback = assert_deferred_ephemeral_followup(interaction)

    assert "ticket 标题已更新" in feedback["content"]
    assert "登录异常-复现" in feedback["content"]
    assert channel.name == "登录异常-复现"
    assert channel.edit_calls[0]["reason"] == "Rename ticket 1-support-0001 in submitted state"
    assert stored is not None
    assert stored.status is TicketStatus.SUBMITTED
    assert channel.sent_messages
    assert "已修改 ticket" in channel.sent_messages[0]
    assert bot.resources.logging_service.info_messages
    assert "Ticket renamed." in bot.resources.logging_service.info_messages[0]


@pytest.mark.asyncio
async def test_mute_current_ticket_updates_permissions_and_returns_feedback(
    prepared_staff_cog_context,
) -> None:
    bot = prepared_staff_cog_context["bot"]
    guild = prepared_staff_cog_context["guild"]
    channel = prepared_staff_cog_context["channel"]
    creator = prepared_staff_cog_context["creator"]
    staff_user = prepared_staff_cog_context["staff_user"]
    mute_repository = TicketMuteRepository(bot.resources.database)
    cog = StaffCog(bot)
    interaction = FakeInteraction(guild, channel, staff_user)

    await cog.mute_current_ticket(interaction, member=creator, duration="30m", reason="冷静期")

    stored = mute_repository.get_by_ticket_and_user("1-support-0001", creator.id)
    feedback = assert_deferred_ephemeral_followup(interaction)

    assert "ticket mute 已生效" in feedback["content"]
    assert "<@201>" in feedback["content"]
    assert stored is not None
    assert stored.reason == "冷静期"
    assert channel.permission_calls[-1]["target_id"] == creator.id
    assert channel.permission_calls[-1]["overwrite"].send_messages is False
    assert channel.sent_messages
    assert "执行禁言" in channel.sent_messages[0]
    assert bot.resources.logging_service.info_messages
    assert "Ticket participant muted." in bot.resources.logging_service.info_messages[0]


@pytest.mark.asyncio
async def test_unmute_current_ticket_restores_permissions_and_returns_feedback(
    prepared_staff_cog_context,
) -> None:
    bot = prepared_staff_cog_context["bot"]
    guild = prepared_staff_cog_context["guild"]
    channel = prepared_staff_cog_context["channel"]
    creator = prepared_staff_cog_context["creator"]
    staff_user = prepared_staff_cog_context["staff_user"]
    mute_repository = TicketMuteRepository(bot.resources.database)
    mute_repository.upsert(TicketMuteRecord(ticket_id="1-support-0001", user_id=creator.id, muted_by=staff_user.id))
    cog = StaffCog(bot)
    interaction = FakeInteraction(guild, channel, staff_user)

    await cog.unmute_current_ticket(interaction, member=creator)
    feedback = assert_deferred_ephemeral_followup(interaction)

    assert "ticket mute 已解除" in feedback["content"]
    assert mute_repository.get_by_ticket_and_user("1-support-0001", creator.id) is None
    assert channel.permission_calls[-1]["target_id"] == creator.id
    assert channel.permission_calls[-1]["overwrite"].send_messages is True
    assert channel.sent_messages
    assert "已解除 ticket" in channel.sent_messages[0]
    assert bot.resources.logging_service.info_messages
    assert "Ticket participant unmuted." in bot.resources.logging_service.info_messages[0]


@pytest.mark.asyncio
async def test_set_current_ticket_priority_updates_channel_name_and_feedback(
    prepared_staff_cog_context,
) -> None:
    bot = prepared_staff_cog_context["bot"]
    guild = prepared_staff_cog_context["guild"]
    channel = prepared_staff_cog_context["channel"]
    staff_user = prepared_staff_cog_context["staff_user"]
    ticket_repository = prepared_staff_cog_context["ticket_repository"]
    cog = StaffCog(bot)
    interaction = FakeInteraction(guild, channel, staff_user)

    await cog.set_current_ticket_priority(interaction, priority=TicketPriority.EMERGENCY)

    stored = ticket_repository.get_by_channel_id(channel.id)
    feedback = assert_deferred_ephemeral_followup(interaction)

    assert "ticket 优先级已更新" in feedback["content"]
    assert "紧急" in feedback["content"]
    assert channel.name == "‼️|login-error"
    assert channel.edit_calls[0]["reason"] == "Set ticket 1-support-0001 priority to emergency"
    assert stored is not None
    assert stored.priority is TicketPriority.EMERGENCY
    assert bot.resources.logging_service.info_messages
    assert "Ticket priority updated." in bot.resources.logging_service.info_messages[0]


@pytest.mark.asyncio
async def test_sleep_current_ticket_updates_status_and_returns_feedback(
    prepared_staff_cog_context,
) -> None:
    bot = prepared_staff_cog_context["bot"]
    guild = prepared_staff_cog_context["guild"]
    channel = prepared_staff_cog_context["channel"]
    staff_user = prepared_staff_cog_context["staff_user"]
    ticket_repository = prepared_staff_cog_context["ticket_repository"]
    cog = StaffCog(bot)
    interaction = FakeInteraction(guild, channel, staff_user)

    await cog.sleep_current_ticket(interaction)

    stored = ticket_repository.get_by_channel_id(channel.id)
    feedback = assert_deferred_ephemeral_followup(interaction)

    assert "ticket 已进入 sleep" in feedback["content"]
    assert "睡前优先级：未设定 ⚪" in feedback["content"]
    assert channel.name == "💤|login-error"
    assert channel.edit_calls[0]["reason"] == "Put ticket 1-support-0001 to sleep"
    assert stored is not None
    assert stored.status is TicketStatus.SLEEP
    assert stored.priority is TicketPriority.SLEEP
    assert stored.priority_before_sleep is TicketPriority.UNSET
    assert bot.resources.logging_service.info_messages
    assert "Ticket entered sleep." in bot.resources.logging_service.info_messages[0]


@pytest.mark.asyncio
async def test_transfer_current_ticket_updates_status_and_returns_feedback(
    prepared_staff_cog_context,
) -> None:
    bot = prepared_staff_cog_context["bot"]
    guild = prepared_staff_cog_context["guild"]
    channel = prepared_staff_cog_context["channel"]
    staff_user = prepared_staff_cog_context["staff_user"]
    ticket_repository = prepared_staff_cog_context["ticket_repository"]
    cog = StaffCog(bot)
    interaction = FakeInteraction(guild, channel, staff_user)

    await cog.transfer_current_ticket(
        interaction,
        target_category_key="billing",
        reason="需要账单组处理",
    )

    stored = ticket_repository.get_by_channel_id(channel.id)
    feedback = assert_deferred_ephemeral_followup(interaction)

    assert "ticket 已进入 transferring" in feedback["content"]
    assert "账单咨询 (`billing`)" in feedback["content"]
    assert "转交理由：需要账单组处理" in feedback["content"]
    assert "计划执行时间：" in feedback["content"]
    assert stored is not None
    assert stored.status is TicketStatus.TRANSFERRING
    assert stored.status_before is TicketStatus.SUBMITTED
    assert stored.transfer_target_category == "billing"
    assert stored.transfer_initiated_by == staff_user.id
    assert stored.transfer_reason == "需要账单组处理"
    assert stored.transfer_execute_at is not None
    assert bot.resources.logging_service.info_messages
    assert "Ticket transfer initiated." in bot.resources.logging_service.info_messages[0]


@pytest.mark.asyncio
async def test_untransfer_current_ticket_restores_previous_status_and_returns_feedback(
    prepared_staff_cog_context,
) -> None:
    bot = prepared_staff_cog_context["bot"]
    guild = prepared_staff_cog_context["guild"]
    channel = prepared_staff_cog_context["channel"]
    staff_user = prepared_staff_cog_context["staff_user"]
    ticket_repository = prepared_staff_cog_context["ticket_repository"]
    ticket_repository.update(
        "1-support-0001",
        status=TicketStatus.TRANSFERRING,
        status_before=TicketStatus.SUBMITTED,
        transfer_target_category="billing",
        transfer_initiated_by=staff_user.id,
        transfer_reason="需要账单组处理",
    )
    cog = StaffCog(bot)
    interaction = FakeInteraction(guild, channel, staff_user)

    await cog.untransfer_current_ticket(interaction)

    stored = ticket_repository.get_by_channel_id(channel.id)
    feedback = assert_deferred_ephemeral_followup(interaction)

    assert "ticket 已撤销 transferring" in feedback["content"]
    assert "恢复状态：submitted" in feedback["content"]
    assert "原目标分类：`billing`" in feedback["content"]
    assert stored is not None
    assert stored.status is TicketStatus.SUBMITTED
    assert stored.status_before is None
    assert stored.transfer_target_category is None
    assert stored.transfer_initiated_by is None
    assert stored.transfer_reason is None
    assert bot.resources.logging_service.info_messages
    assert "Ticket transfer cancelled." in bot.resources.logging_service.info_messages[0]


@pytest.mark.asyncio
async def test_set_current_ticket_priority_rejects_non_staff_user(prepared_staff_cog_context) -> None:
    bot = prepared_staff_cog_context["bot"]
    guild = prepared_staff_cog_context["guild"]
    channel = prepared_staff_cog_context["channel"]
    outsider = prepared_staff_cog_context["outsider"]
    cog = StaffCog(bot)
    interaction = FakeInteraction(guild, channel, outsider)

    await cog.set_current_ticket_priority(interaction, priority=TicketPriority.HIGH)

    feedback = assert_deferred_ephemeral_followup(interaction)
    assert "只有当前分类 staff" in feedback["content"]


@pytest.mark.asyncio
async def test_show_ticket_help_returns_command_summary_in_submitted_channel(
    prepared_staff_cog_context,
) -> None:
    bot = prepared_staff_cog_context["bot"]
    guild = prepared_staff_cog_context["guild"]
    channel = prepared_staff_cog_context["channel"]
    staff_user = prepared_staff_cog_context["staff_user"]
    cog = StaffCog(bot)
    interaction = FakeInteraction(guild, channel, staff_user)

    await cog.show_ticket_help(interaction)

    assert interaction.response.messages
    content = interaction.response.messages[0]["content"]
    assert "/ticket help" in content
    assert "/ticket claim" in content
    assert "/ticket priority" in content
    assert "/ticket sleep" in content
    assert "/ticket rename" in content
    assert "/ticket mute" in content
    assert "/ticket unmute" in content
    assert "/ticket transfer-claim" in content
    assert "/ticket transfer" in content
    assert "/ticket untransfer" in content
    assert "当前 ticket 频道" in content
    assert bot.resources.logging_service.info_messages
    assert "Ticket help requested." in bot.resources.logging_service.info_messages[0]


@pytest.mark.asyncio
async def test_show_ticket_help_is_available_in_draft_ticket(prepared_staff_cog_context) -> None:
    bot = prepared_staff_cog_context["bot"]
    guild = prepared_staff_cog_context["guild"]
    channel = prepared_staff_cog_context["channel"]
    creator = prepared_staff_cog_context["creator"]
    ticket_repository = prepared_staff_cog_context["ticket_repository"]
    ticket_repository.update("1-support-0001", status=TicketStatus.DRAFT)
    cog = StaffCog(bot)
    interaction = FakeInteraction(guild, channel, creator)

    await cog.show_ticket_help(interaction)

    assert interaction.response.messages
    assert "/ticket submit" in interaction.response.messages[0]["content"]


@pytest.mark.asyncio
async def test_staff_cog_registers_persistent_staff_panel_view(prepared_staff_cog_context) -> None:
    bot = prepared_staff_cog_context["bot"]

    StaffCog(bot)

    assert len(bot.added_views) == 1
    assert isinstance(bot.added_views[0]["view"], StaffPanelView)
    assert bot.added_views[0]["message_id"] is None


@pytest.mark.asyncio
async def test_staff_panel_claim_button_updates_ticket_and_returns_feedback(
    prepared_staff_cog_context,
) -> None:
    bot = prepared_staff_cog_context["bot"]
    guild = prepared_staff_cog_context["guild"]
    channel = prepared_staff_cog_context["channel"]
    staff_user = prepared_staff_cog_context["staff_user"]
    panel_message = prepared_staff_cog_context["panel_message"]
    ticket_repository = prepared_staff_cog_context["ticket_repository"]
    view = StaffPanelView()
    claim_button = next(child for child in view.children if getattr(child, "custom_id", None) == build_staff_panel_custom_id("claim"))
    interaction = FakeInteraction(guild, channel, staff_user, bot=bot, message=panel_message)

    await claim_button.callback(interaction)

    stored = ticket_repository.get_by_channel_id(channel.id)
    feedback = assert_deferred_ephemeral_followup(interaction)
    assert stored is not None
    assert stored.claimed_by == staff_user.id
    assert "ticket 已认领" in feedback["content"]


@pytest.mark.asyncio
async def test_staff_panel_priority_select_updates_ticket_and_returns_feedback(
    prepared_staff_cog_context,
) -> None:
    bot = prepared_staff_cog_context["bot"]
    guild = prepared_staff_cog_context["guild"]
    channel = prepared_staff_cog_context["channel"]
    staff_user = prepared_staff_cog_context["staff_user"]
    panel_message = prepared_staff_cog_context["panel_message"]
    ticket_repository = prepared_staff_cog_context["ticket_repository"]
    view = StaffPanelView()
    priority_select = next(child for child in view.children if getattr(child, "custom_id", None) == build_staff_panel_custom_id("priority"))
    priority_select._values = [TicketPriority.HIGH.value]
    interaction = FakeInteraction(guild, channel, staff_user, bot=bot, message=panel_message)

    await priority_select.callback(interaction)

    stored = ticket_repository.get_by_channel_id(channel.id)
    feedback = assert_deferred_ephemeral_followup(interaction)
    assert stored is not None
    assert stored.priority is TicketPriority.HIGH
    assert channel.name == "🔴|login-error"
    assert "ticket 优先级已更新" in feedback["content"]
    assert "高" in feedback["content"]


@pytest.mark.asyncio
async def test_staff_panel_claim_button_rejects_stale_panel_message(prepared_staff_cog_context) -> None:
    bot = prepared_staff_cog_context["bot"]
    guild = prepared_staff_cog_context["guild"]
    channel = prepared_staff_cog_context["channel"]
    staff_user = prepared_staff_cog_context["staff_user"]
    view = StaffPanelView()
    claim_button = next(child for child in view.children if getattr(child, "custom_id", None) == build_staff_panel_custom_id("claim"))
    stale_message = SimpleNamespace(id=4999)
    interaction = FakeInteraction(guild, channel, staff_user, bot=bot, message=stale_message)

    await claim_button.callback(interaction)

    assert interaction.response.messages
    assert interaction.response.messages[0]["ephemeral"] is True
    assert "控制面板已过期" in interaction.response.messages[0]["content"]
