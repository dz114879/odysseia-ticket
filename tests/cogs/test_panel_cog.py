from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from cogs.panel_cog import PanelCog
from core.enums import ClaimMode
from core.models import GuildConfigRecord, TicketCategoryConfig
from db.repositories.panel_repository import PanelRepository


class FakeResponse:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def send_message(self, content: str, *, ephemeral: bool) -> None:
        self._done = True
        self.messages.append({"content": content, "ephemeral": ephemeral})


class FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send(self, content: str, *, ephemeral: bool) -> None:
        self.messages.append({"content": content, "ephemeral": ephemeral})


@dataclass
class FakeRole:
    id: int


class FakeUser:
    def __init__(self, user_id: int, *, role_ids: list[int] | None = None, administrator: bool = False) -> None:
        self.id = user_id
        self.roles = [FakeRole(role_id) for role_id in (role_ids or [])]
        self.guild_permissions = SimpleNamespace(administrator=administrator)


class FakeMessage:
    def __init__(self, message_id: int, channel: FakeChannel, *, embed, view) -> None:
        self.id = message_id
        self.channel = channel
        self.embed = embed
        self.view = view
        self.deleted = False

    async def edit(self, *, embed, view) -> None:
        self.embed = embed
        self.view = view

    async def delete(self) -> None:
        self.deleted = True
        self.channel.messages.pop(self.id, None)


class FakeChannel:
    def __init__(self, channel_id: int, guild: FakeGuild) -> None:
        self.id = channel_id
        self.guild = guild
        self.messages: dict[int, FakeMessage] = {}
        self.next_message_id = 1000

    async def send(self, *, embed, view) -> FakeMessage:
        message = FakeMessage(self.next_message_id, self, embed=embed, view=view)
        self.messages[message.id] = message
        self.next_message_id += 1
        return message

    async def fetch_message(self, message_id: int) -> FakeMessage:
        if message_id not in self.messages:
            raise LookupError("message not found")
        return self.messages[message_id]


class FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id


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
    def __init__(self, migrated_database, channel: FakeChannel, *, is_owner: bool = False) -> None:
        self.resources = SimpleNamespace(
            database=migrated_database,
            logging_service=FakeLoggingService(),
        )
        self.channel = channel
        self._is_owner = is_owner

    async def is_owner(self, user: FakeUser) -> bool:
        return self._is_owner

    def get_channel(self, channel_id: int) -> FakeChannel | None:
        return self.channel if self.channel.id == channel_id else None

    async def fetch_channel(self, channel_id: int) -> FakeChannel:
        if self.channel.id != channel_id:
            raise LookupError("channel not found")
        return self.channel


class FakeInteraction:
    def __init__(self, guild: FakeGuild | None, channel: FakeChannel | None, user: FakeUser) -> None:
        self.guild = guild
        self.channel = channel
        self.user = user
        self.response = FakeResponse()
        self.followup = FakeFollowup()


@pytest.fixture
def prepared_panel_context(migrated_database):
    guild = FakeGuild(1)
    channel = FakeChannel(777, guild)
    bot = FakeBot(migrated_database, channel)

    connection = migrated_database
    from db.repositories.guild_repository import GuildRepository

    repository = GuildRepository(connection)
    repository.upsert_config(
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
    repository.upsert_category(
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
    return bot, guild, channel


@pytest.mark.asyncio
async def test_create_panel_in_channel_requires_ticket_admin_role(prepared_panel_context, migrated_database) -> None:
    bot, guild, channel = prepared_panel_context
    cog = PanelCog(bot)
    interaction = FakeInteraction(guild, channel, FakeUser(42, role_ids=[999]))

    await cog.create_panel_in_channel(interaction)

    assert interaction.response.messages
    assert "只有 Ticket 管理员角色或 Bot 所有者" in interaction.response.messages[0]["content"]


@pytest.mark.asyncio
async def test_create_refresh_and_remove_panel_flow(prepared_panel_context, migrated_database) -> None:
    bot, guild, channel = prepared_panel_context
    cog = PanelCog(bot)
    interaction = FakeInteraction(guild, channel, FakeUser(42, role_ids=[400]))

    await cog.create_panel_in_channel(interaction)
    assert "公开 Ticket 面板已创建" in interaction.response.messages[0]["content"]

    refresh_interaction = FakeInteraction(guild, channel, FakeUser(42, role_ids=[400]))
    await cog.refresh_panel(refresh_interaction)
    assert "已刷新 active panel" in refresh_interaction.response.messages[0]["content"]

    remove_interaction = FakeInteraction(guild, channel, FakeUser(42, role_ids=[400]))
    await cog.remove_panel(remove_interaction, delete_message=False)
    assert "已移除 active panel" in remove_interaction.response.messages[0]["content"]

    repository = PanelRepository(migrated_database)
    assert repository.get_active_panel(1) is None
