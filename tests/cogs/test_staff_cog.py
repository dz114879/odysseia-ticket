from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from cogs.staff_cog import StaffCog
from core.enums import ClaimMode, TicketPriority, TicketStatus
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository
from discord_ui.help_text import build_ticket_help_message
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
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def send_message(self, content: str, *, ephemeral: bool) -> None:
        self._done = True
        self.messages.append({"content": content, "ephemeral": ephemeral})


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
            staff_role_id=500,
            staff_user_ids_json="[]",
            extra_welcome_text="请说明具体错误。",
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

    creator = FakeUser(201)
    staff_user = FakeUser(301, roles=[staff_role])
    outsider = FakeUser(999)
    admin_user = FakeUser(401, roles=[admin_role])
    for member in (creator, staff_user, outsider, admin_user):
        guild.add_member(member)

    channel = FakeChannel(9001, guild, name="ticket-0001-login-error")
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

    assert interaction.response.messages
    assert "ticket 已认领" in interaction.response.messages[0]["content"]
    assert f"<@{staff_user.id}>" in interaction.response.messages[0]["content"]
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

    assert interaction.response.messages
    assert "只有当前分类 staff" in interaction.response.messages[0]["content"]


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

    assert interaction.response.messages
    assert "ticket 已取消认领" in interaction.response.messages[0]["content"]
    assert stored is not None
    assert stored.claimed_by is None
    assert bot.resources.logging_service.info_messages
    assert "Ticket unclaimed." in bot.resources.logging_service.info_messages[0]


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

    assert interaction.response.messages
    assert "ticket 优先级已更新" in interaction.response.messages[0]["content"]
    assert "紧急" in interaction.response.messages[0]["content"]
    assert channel.name == "‼️|ticket-0001-login-error"
    assert channel.edit_calls[0]["reason"] == "Set ticket 1-support-0001 priority to emergency"
    assert stored is not None
    assert stored.priority is TicketPriority.EMERGENCY
    assert bot.resources.logging_service.info_messages
    assert "Ticket priority updated." in bot.resources.logging_service.info_messages[0]


@pytest.mark.asyncio
async def test_set_current_ticket_priority_rejects_non_staff_user(prepared_staff_cog_context) -> None:
    bot = prepared_staff_cog_context["bot"]
    guild = prepared_staff_cog_context["guild"]
    channel = prepared_staff_cog_context["channel"]
    outsider = prepared_staff_cog_context["outsider"]
    cog = StaffCog(bot)
    interaction = FakeInteraction(guild, channel, outsider)

    await cog.set_current_ticket_priority(interaction, priority=TicketPriority.HIGH)

    assert interaction.response.messages
    assert "只有当前分类 staff" in interaction.response.messages[0]["content"]


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
    assert "draft / submitted" in content
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
    claim_button = next(
        child for child in view.children if getattr(child, "custom_id", None) == build_staff_panel_custom_id("claim")
    )
    interaction = FakeInteraction(guild, channel, staff_user, bot=bot, message=panel_message)

    await claim_button.callback(interaction)

    stored = ticket_repository.get_by_channel_id(channel.id)
    assert stored is not None
    assert stored.claimed_by == staff_user.id
    assert interaction.response.messages
    assert "ticket 已认领" in interaction.response.messages[0]["content"]


@pytest.mark.asyncio
async def test_staff_panel_help_button_returns_shared_help_text(prepared_staff_cog_context) -> None:
    bot = prepared_staff_cog_context["bot"]
    guild = prepared_staff_cog_context["guild"]
    channel = prepared_staff_cog_context["channel"]
    creator = prepared_staff_cog_context["creator"]
    panel_message = prepared_staff_cog_context["panel_message"]
    view = StaffPanelView()
    help_button = next(
        child for child in view.children if getattr(child, "custom_id", None) == build_staff_panel_custom_id("help")
    )
    interaction = FakeInteraction(guild, channel, creator, bot=bot, message=panel_message)

    await help_button.callback(interaction)

    assert interaction.response.messages
    assert interaction.response.messages[0]["content"] == build_ticket_help_message()


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
    priority_select = next(
        child for child in view.children if getattr(child, "custom_id", None) == build_staff_panel_custom_id("priority")
    )
    priority_select._values = [TicketPriority.HIGH.value]
    interaction = FakeInteraction(guild, channel, staff_user, bot=bot, message=panel_message)

    await priority_select.callback(interaction)

    stored = ticket_repository.get_by_channel_id(channel.id)
    assert stored is not None
    assert stored.priority is TicketPriority.HIGH
    assert channel.name == "🔴|ticket-0001-login-error"
    assert interaction.response.messages
    assert "ticket 优先级已更新" in interaction.response.messages[0]["content"]
    assert "高" in interaction.response.messages[0]["content"]


@pytest.mark.asyncio
async def test_staff_panel_claim_button_rejects_stale_panel_message(prepared_staff_cog_context) -> None:
    bot = prepared_staff_cog_context["bot"]
    guild = prepared_staff_cog_context["guild"]
    channel = prepared_staff_cog_context["channel"]
    staff_user = prepared_staff_cog_context["staff_user"]
    view = StaffPanelView()
    claim_button = next(
        child for child in view.children if getattr(child, "custom_id", None) == build_staff_panel_custom_id("claim")
    )
    stale_message = SimpleNamespace(id=4999)
    interaction = FakeInteraction(guild, channel, staff_user, bot=bot, message=stale_message)

    await claim_button.callback(interaction)

    assert interaction.response.messages
    assert interaction.response.messages[0]["ephemeral"] is True
    assert "控制面板已过期" in interaction.response.messages[0]["content"]
