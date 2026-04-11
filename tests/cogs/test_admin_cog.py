from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from cogs.admin_cog import AdminCog
from db.repositories.guild_repository import GuildRepository


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
class FakeChannel:
    id: int


@dataclass
class FakeRole:
    id: int


class FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id
        self._channels = {
            100: FakeChannel(100),
            200: FakeChannel(200),
            300: FakeChannel(300),
        }
        self._roles = {400: FakeRole(400)}

    def get_channel(self, channel_id: int) -> FakeChannel | None:
        return self._channels.get(channel_id)

    def get_role(self, role_id: int) -> FakeRole | None:
        return self._roles.get(role_id)


class FakeUser:
    def __init__(self, user_id: int, *, administrator: bool) -> None:
        self.id = user_id
        self.guild_permissions = SimpleNamespace(administrator=administrator)
        self.roles: list[FakeRole] = []


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
    def __init__(self, migrated_database, *, is_owner: bool = False) -> None:
        self.resources = SimpleNamespace(
            database=migrated_database,
            logging_service=FakeLoggingService(),
        )
        self._is_owner = is_owner

    async def is_owner(self, user: FakeUser) -> bool:
        return self._is_owner


class FakeInteraction:
    def __init__(self, guild: FakeGuild | None, user: FakeUser) -> None:
        self.guild = guild
        self.user = user
        self.response = FakeResponse()
        self.followup = FakeFollowup()


@pytest.mark.asyncio
async def test_run_setup_rejects_non_admin_user(migrated_database) -> None:
    cog = AdminCog(FakeBot(migrated_database))
    interaction = FakeInteraction(FakeGuild(1), FakeUser(42, administrator=False))

    await cog.run_setup(
        interaction,
        log_channel=FakeChannel(100),
        archive_channel=FakeChannel(200),
        ticket_category=FakeChannel(300),
        admin_role=FakeRole(400),
    )

    assert interaction.response.messages
    assert "只有服务器管理员或 Bot 所有者" in interaction.response.messages[0]["content"]


@pytest.mark.asyncio
async def test_run_setup_persists_config_and_categories(migrated_database) -> None:
    bot = FakeBot(migrated_database)
    cog = AdminCog(bot)
    interaction = FakeInteraction(FakeGuild(1), FakeUser(42, administrator=True))

    await cog.run_setup(
        interaction,
        log_channel=FakeChannel(100),
        archive_channel=FakeChannel(200),
        ticket_category=FakeChannel(300),
        admin_role=FakeRole(400),
    )

    repository = GuildRepository(migrated_database)
    config = repository.get_config(1)
    categories = repository.list_categories(1)

    assert config is not None
    assert config.is_initialized is True
    assert config.admin_role_id == 400
    assert len(categories) == 5
    assert interaction.response.messages
    assert "Ticket setup 已完成" in interaction.response.messages[0]["content"]
