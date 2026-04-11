from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from cogs.close_cog import CloseCog
from core.enums import ClaimMode, TicketStatus
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository
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


class FakeMessage:
    def __init__(self, message_id: int, *, content=None, embed=None, view=None, file=None) -> None:
        self.id = message_id
        self.content = content
        self.embed = embed
        self.view = view
        self.file = file
        self.edit_calls: list[dict[str, object]] = []

    async def edit(self, *, content=None, embed=None, view=None) -> None:
        if content is not None:
            self.content = content
        if embed is not None:
            self.embed = embed
        self.view = view
        self.edit_calls.append({"content": content, "embed": embed, "view": view})


class FakeChannel:
    def __init__(self, channel_id: int, guild: FakeGuild, *, name: str) -> None:
        self.id = channel_id
        self.guild = guild
        self.name = name
        self.next_message_id = 1000
        self.sent_messages: list[FakeMessage] = []
        self.permission_calls: list[dict[str, object]] = []
        self._messages: dict[int, FakeMessage] = {}

    async def send(self, *, content=None, embed=None, view=None, file=None):
        message = FakeMessage(self.next_message_id, content=content, embed=embed, view=view, file=file)
        self.next_message_id += 1
        self.sent_messages.append(message)
        self._messages[message.id] = message
        return message

    async def set_permissions(self, target, *, overwrite, reason: str | None = None) -> None:
        self.permission_calls.append({"target_id": getattr(target, "id", None), "overwrite": overwrite, "reason": reason})

    async def fetch_message(self, message_id: int) -> FakeMessage:
        return self._messages[message_id]


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
    def __init__(self, migrated_database) -> None:
        self.resources = SimpleNamespace(
            database=migrated_database,
            logging_service=FakeLoggingService(),
            lock_manager=LockManager(),
            close_service=None,
        )

    async def is_owner(self, user) -> bool:
        del user
        return False


class FakeInteraction:
    def __init__(self, guild: FakeGuild, channel: FakeChannel, user: FakeUser, *, bot) -> None:
        self.guild = guild
        self.channel = channel
        self.user = user
        self.client = bot
        self.response = FakeResponse()
        self.followup = FakeFollowup()


@pytest.fixture
def prepared_close_cog_context(migrated_database):
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

    guild = FakeGuild(1)
    admin_role = FakeRole(400)
    staff_role = FakeRole(500)
    guild.add_role(admin_role)
    guild.add_role(staff_role)

    creator = FakeUser(201)
    staff_user = FakeUser(301, roles=[staff_role])
    guild.add_member(creator)
    guild.add_member(staff_user)

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
        )
    )

    return {
        "guild": guild,
        "channel": channel,
        "creator": creator,
        "staff_user": staff_user,
        "ticket_repository": ticket_repository,
        "database": migrated_database,
    }


def assert_deferred_followup(interaction: FakeInteraction) -> dict[str, object]:
    assert interaction.response.deferred == [{"ephemeral": True, "thinking": True}]
    assert interaction.followup.messages
    assert interaction.followup.messages[0]["ephemeral"] is True
    return interaction.followup.messages[0]


@pytest.mark.asyncio
async def test_close_current_ticket_by_staff_enters_closing_and_returns_feedback(
    prepared_close_cog_context,
) -> None:
    bot = FakeBot(prepared_close_cog_context["database"])
    cog = CloseCog(bot)
    interaction = FakeInteraction(
        prepared_close_cog_context["guild"],
        prepared_close_cog_context["channel"],
        prepared_close_cog_context["staff_user"],
        bot=bot,
    )

    await cog.close_current_ticket(interaction, reason="问题已解决")

    stored = prepared_close_cog_context["ticket_repository"].get_by_channel_id(prepared_close_cog_context["channel"].id)
    feedback = assert_deferred_followup(interaction)

    assert stored is not None
    assert stored.status is TicketStatus.CLOSING
    assert "ticket 已进入 closing" in feedback["content"]
    assert bot.resources.logging_service.info_messages
    assert "Ticket close initiated." in bot.resources.logging_service.info_messages[0]


@pytest.mark.asyncio
async def test_close_current_ticket_by_creator_creates_request_and_returns_feedback(
    prepared_close_cog_context,
) -> None:
    bot = FakeBot(prepared_close_cog_context["database"])
    cog = CloseCog(bot)
    interaction = FakeInteraction(
        prepared_close_cog_context["guild"],
        prepared_close_cog_context["channel"],
        prepared_close_cog_context["creator"],
        bot=bot,
    )

    await cog.close_current_ticket(interaction, reason="可以结案了")

    stored = prepared_close_cog_context["ticket_repository"].get_by_channel_id(prepared_close_cog_context["channel"].id)
    feedback = assert_deferred_followup(interaction)

    assert stored is not None
    assert stored.status is TicketStatus.SUBMITTED
    assert "已向 staff 发出关闭请求" in feedback["content"]
    assert prepared_close_cog_context["channel"].sent_messages
    assert prepared_close_cog_context["channel"].sent_messages[0].embed.title == "📩 用户关闭请求"
    assert bot.resources.logging_service.info_messages
    assert "Ticket close request created." in bot.resources.logging_service.info_messages[0]


@pytest.mark.asyncio
async def test_revoke_current_ticket_close_restores_status_and_returns_feedback(
    prepared_close_cog_context,
) -> None:
    bot = FakeBot(prepared_close_cog_context["database"])
    ticket_repository = prepared_close_cog_context["ticket_repository"]
    ticket_repository.update(
        "1-support-0001",
        status=TicketStatus.CLOSING,
        status_before=TicketStatus.SUBMITTED,
        close_execute_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
        close_reason="误操作",
        closed_at=datetime.now(timezone.utc).isoformat(),
    )
    cog = CloseCog(bot)
    interaction = FakeInteraction(
        prepared_close_cog_context["guild"],
        prepared_close_cog_context["channel"],
        prepared_close_cog_context["staff_user"],
        bot=bot,
    )

    await cog.revoke_current_ticket_close(interaction)

    stored = ticket_repository.get_by_channel_id(prepared_close_cog_context["channel"].id)
    feedback = assert_deferred_followup(interaction)

    assert stored is not None
    assert stored.status is TicketStatus.SUBMITTED
    assert "ticket closing 已撤销" in feedback["content"]
    assert bot.resources.logging_service.info_messages
    assert "Ticket close revoked." in bot.resources.logging_service.info_messages[0]
