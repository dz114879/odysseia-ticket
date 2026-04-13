from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from cogs.config_cog import ConfigCog
from core.enums import ClaimMode
from core.models import GuildConfigRecord
from db.repositories.guild_repository import GuildRepository


class FakeResponse:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.deferred: list[dict] = []
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def send_message(self, content: str | None = None, *, embed=None, view=None, ephemeral: bool = False) -> None:
        self._done = True
        self.messages.append({"content": content, "embed": embed, "view": view, "ephemeral": ephemeral})

    async def defer(self, *, ephemeral: bool, thinking: bool) -> None:
        self._done = True
        self.deferred.append({"ephemeral": ephemeral, "thinking": thinking})


class FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send(self, content: str | None = None, *, embed=None, view=None, ephemeral: bool = False) -> None:
        self.messages.append({"content": content, "embed": embed, "view": view, "ephemeral": ephemeral})


@dataclass
class FakeRole:
    id: int


class FakeUser:
    def __init__(self, user_id: int, *, administrator: bool = False, role_ids: list[int] | None = None) -> None:
        self.id = user_id
        self.guild_permissions = SimpleNamespace(administrator=administrator)
        self.roles = [FakeRole(rid) for rid in (role_ids or [])]


class FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id


class FakeBot:
    def __init__(self, migrated_database, *, is_owner: bool = False) -> None:
        self.resources = SimpleNamespace(database=migrated_database)
        self._is_owner = is_owner

    async def is_owner(self, user) -> bool:
        return self._is_owner


class FakeInteraction:
    def __init__(self, guild: FakeGuild | None, user: FakeUser) -> None:
        self.guild = guild
        self.user = user
        self.response = FakeResponse()
        self.followup = FakeFollowup()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ADMIN_ROLE_ID = 400


def _make_initialized_config(guild_id: int = 1) -> GuildConfigRecord:
    return GuildConfigRecord(
        guild_id=guild_id,
        is_initialized=True,
        log_channel_id=100,
        archive_channel_id=200,
        ticket_category_channel_id=300,
        admin_role_id=ADMIN_ROLE_ID,
        claim_mode=ClaimMode.RELAXED,
        max_open_tickets=10,
        timezone="Asia/Hong_Kong",
        enable_download_window=True,
        updated_at="2024-01-01T00:00:00+00:00",
    )


def _seed_config(migrated_database, config: GuildConfigRecord) -> None:
    GuildRepository(migrated_database).upsert_config(config)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_config_no_guild(migrated_database) -> None:
    """guild is None -> ephemeral error containing '服务器'."""
    cog = ConfigCog(FakeBot(migrated_database))
    interaction = FakeInteraction(guild=None, user=FakeUser(1))

    await cog.run_config(interaction)

    assert interaction.response.deferred
    assert interaction.followup.messages
    msg = interaction.followup.messages[0]
    assert msg["ephemeral"] is True
    assert "服务器" in msg["content"]


@pytest.mark.asyncio
async def test_run_config_no_permission(migrated_database) -> None:
    """User is not owner, not administrator, and lacks admin_role -> ephemeral permission error."""
    config = _make_initialized_config()
    _seed_config(migrated_database, config)

    cog = ConfigCog(FakeBot(migrated_database, is_owner=False))
    user = FakeUser(42, administrator=False, role_ids=[999])
    interaction = FakeInteraction(guild=FakeGuild(1), user=user)

    await cog.run_config(interaction)

    assert interaction.response.deferred
    assert interaction.followup.messages
    msg = interaction.followup.messages[0]
    assert msg["ephemeral"] is True
    # The permission error mentions admin role / administrator / Bot owner
    assert "管理" in msg["content"] or "权限" in msg["content"] or "所有者" in msg["content"]


@pytest.mark.asyncio
async def test_run_config_not_initialized(migrated_database) -> None:
    """Has permission but config.is_initialized is False -> ephemeral error about setup."""
    # Seed a non-initialized config so the permission check can find admin_role_id
    config = GuildConfigRecord(
        guild_id=1,
        is_initialized=False,
        admin_role_id=ADMIN_ROLE_ID,
        updated_at="2024-01-01T00:00:00+00:00",
    )
    _seed_config(migrated_database, config)

    cog = ConfigCog(FakeBot(migrated_database, is_owner=False))
    user = FakeUser(42, administrator=True)
    interaction = FakeInteraction(guild=FakeGuild(1), user=user)

    await cog.run_config(interaction)

    assert interaction.response.deferred
    assert interaction.followup.messages
    msg = interaction.followup.messages[0]
    assert msg["ephemeral"] is True
    assert "setup" in msg["content"].lower() or "初始化" in msg["content"] or "尚未完成" in msg["content"]


@pytest.mark.asyncio
async def test_run_config_success(migrated_database) -> None:
    """Valid admin with initialized config -> sends embed + ConfigPanelView, ephemeral=True."""
    config = _make_initialized_config()
    _seed_config(migrated_database, config)

    cog = ConfigCog(FakeBot(migrated_database, is_owner=False))
    user = FakeUser(42, administrator=True)
    interaction = FakeInteraction(guild=FakeGuild(1), user=user)

    await cog.run_config(interaction)

    assert interaction.response.deferred
    assert interaction.followup.messages
    msg = interaction.followup.messages[0]
    assert msg["ephemeral"] is True
    # Success path sends embed + view, not a text content string
    assert msg["embed"] is not None
    assert msg["view"] is not None
    assert msg["content"] is None
