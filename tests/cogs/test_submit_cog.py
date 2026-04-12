from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest

from cogs.submit_cog import SubmitCog
from core.enums import ClaimMode, TicketStatus
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository
from discord_ui.draft_views import DraftSubmitTitleModal, DraftWelcomeView


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, member_id: int) -> None:
        self.id = member_id
        self.bot = False


class FakeMessage:
    def __init__(
        self,
        message_id: int,
        *,
        content: str | None = None,
        embed: discord.Embed | None = None,
        view=None,
        pinned: bool = False,
    ) -> None:
        self.id = message_id
        self.content = content
        self.embed = embed
        self.view = view
        self.pinned = pinned
        self.edit_calls: list[dict] = []

    async def edit(self, *, view=None) -> None:
        self.view = view
        self.edit_calls.append({"view": view})


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
        self.next_message_id = 1000
        self.sent_messages: list[FakeMessage] = []
        self.pinned_messages: list[FakeMessage] = []
        self.permission_calls: list[dict] = []

    async def edit(self, *, name=None, reason=None, **kwargs) -> None:
        if name is not None:
            self.name = name

    async def send(self, *, content=None, embed=None, view=None) -> FakeMessage:
        message = FakeMessage(self.next_message_id, content=content, embed=embed, view=view)
        self.next_message_id += 1
        self.sent_messages.append(message)
        return message

    async def set_permissions(self, target, *, overwrite, reason) -> None:
        self.permission_calls.append({"target": target, "overwrite": overwrite, "reason": reason})

    async def pins(self) -> list[FakeMessage]:
        return list(self.pinned_messages)


class FakeResponse:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.deferred: list[dict] = []
        self.modal = None
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def send_message(self, content: str, *, ephemeral: bool) -> None:
        self.messages.append({"content": content, "ephemeral": ephemeral})
        self._done = True

    async def defer(self, *, ephemeral: bool, thinking: bool) -> None:
        self.deferred.append({"ephemeral": ephemeral, "thinking": thinking})
        self._done = True

    async def send_modal(self, modal) -> None:
        self.modal = modal
        self._done = True


class FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[dict] = []

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
    def __init__(self, migrated_database) -> None:
        self.resources = SimpleNamespace(
            database=migrated_database,
            logging_service=FakeLoggingService(),
            lock_manager=None,
        )
        self.added_views: list[discord.ui.View] = []

    def add_view(self, view: discord.ui.View, message_id: int | None = None) -> None:
        self.added_views.append(view)


class FakeInteraction:
    def __init__(self, *, bot, guild, channel, user, message=None) -> None:
        self.client = bot
        self.guild = guild
        self.channel = channel
        self.user = user
        self.message = message
        self.response = FakeResponse()
        self.followup = FakeFollowup()


@pytest.fixture
def prepared_submit_cog_context(migrated_database):
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
            is_enabled=True,
            allowlist_role_ids_json="[]",
            denylist_role_ids_json="[]",
            sort_order=1,
        )
    )

    creator = FakeMember(201)
    guild = FakeGuild(1)
    guild.add_member(creator)
    guild.add_role(FakeRole(400))
    guild.add_role(FakeRole(500))

    ticket = ticket_repository.create(
        TicketRecord(
            ticket_id="1-support-0001",
            guild_id=1,
            creator_id=creator.id,
            category_key="support",
            channel_id=9001,
            status=TicketStatus.DRAFT,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
            has_user_message=True,
            last_user_message_at="2024-01-01T01:00:00+00:00",
        )
    )
    channel = FakeChannel(
        9001,
        guild,
        name="技术支持",
    )
    welcome_message = FakeMessage(
        5001,
        content="您好 <@201>\n- Ticket ID：`1-support-0001`",
        view=DraftWelcomeView(),
        pinned=True,
    )
    channel.pinned_messages.append(welcome_message)

    return {
        "database": migrated_database,
        "ticket_repository": ticket_repository,
        "creator": creator,
        "guild": guild,
        "channel": channel,
        "ticket": ticket,
        "welcome_message": welcome_message,
    }


@pytest.mark.asyncio
async def test_submit_cog_registers_persistent_welcome_view(prepared_submit_cog_context) -> None:
    bot = FakeBot(prepared_submit_cog_context["database"])

    SubmitCog(bot)

    assert len(bot.added_views) == 1
    assert isinstance(bot.added_views[0], DraftWelcomeView)


@pytest.mark.asyncio
async def test_submit_current_draft_sends_modal_when_channel_name_is_still_default(
    prepared_submit_cog_context,
) -> None:
    bot = FakeBot(prepared_submit_cog_context["database"])
    cog = SubmitCog(bot)
    interaction = FakeInteraction(
        bot=bot,
        guild=prepared_submit_cog_context["guild"],
        channel=prepared_submit_cog_context["channel"],
        user=prepared_submit_cog_context["creator"],
    )

    await cog.submit_current_draft(interaction)

    assert isinstance(interaction.response.modal, DraftSubmitTitleModal)
    assert not interaction.followup.messages


@pytest.mark.asyncio
async def test_submit_current_draft_submits_after_channel_has_custom_name(
    prepared_submit_cog_context,
) -> None:
    database = prepared_submit_cog_context["database"]
    bot = FakeBot(database)
    cog = SubmitCog(bot)
    channel = prepared_submit_cog_context["channel"]
    channel.name = "existing-title"
    interaction = FakeInteraction(
        bot=bot,
        guild=prepared_submit_cog_context["guild"],
        channel=channel,
        user=prepared_submit_cog_context["creator"],
    )

    await cog.submit_current_draft(interaction)

    stored = TicketRepository(database).get_by_ticket_id(prepared_submit_cog_context["ticket"].ticket_id)
    assert interaction.response.deferred == [{"ephemeral": True, "thinking": True}]
    assert interaction.followup.messages
    assert "Ticket 已提交" in interaction.followup.messages[0]["content"]
    assert stored is not None
    assert stored.status is TicketStatus.SUBMITTED


@pytest.mark.asyncio
async def test_draft_welcome_submit_button_opens_modal_for_default_name(
    prepared_submit_cog_context,
) -> None:
    bot = FakeBot(prepared_submit_cog_context["database"])
    view = DraftWelcomeView()
    button = view.children[0]
    interaction = FakeInteraction(
        bot=bot,
        guild=prepared_submit_cog_context["guild"],
        channel=prepared_submit_cog_context["channel"],
        user=prepared_submit_cog_context["creator"],
        message=prepared_submit_cog_context["welcome_message"],
    )

    await button.callback(interaction)

    assert isinstance(interaction.response.modal, DraftSubmitTitleModal)
