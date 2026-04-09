from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from cogs.draft_cog import DraftCog
from core.enums import TicketStatus
from core.models import TicketRecord
from db.repositories.ticket_repository import TicketRepository
from runtime.locks import LockManager


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
class FakeGuild:
    id: int


@dataclass
class FakeUser:
    id: int


class FakeChannel:
    def __init__(self, channel_id: int, guild: FakeGuild, *, name: str) -> None:
        self.id = channel_id
        self.guild = guild
        self.name = name
        self.deleted = False
        self.edit_calls: list[dict[str, str | None]] = []
        self.delete_calls: list[str | None] = []

    async def edit(self, *, name: str, reason: str | None = None) -> None:
        self.edit_calls.append({"name": name, "reason": reason})
        self.name = name

    async def delete(self, *, reason: str | None = None) -> None:
        self.delete_calls.append(reason)
        self.deleted = True


class FakeLoggingService:
    def __init__(self) -> None:
        self.info_messages: list[str] = []

    def log_local_info(self, message: str, *args) -> None:
        self.info_messages.append(message % args if args else message)


class FakeBot:
    def __init__(self, migrated_database) -> None:
        self.resources = SimpleNamespace(
            database=migrated_database,
            logging_service=FakeLoggingService(),
            lock_manager=LockManager(),
        )


class FakeInteraction:
    def __init__(self, guild: FakeGuild | None, channel: FakeChannel | None, user: FakeUser) -> None:
        self.guild = guild
        self.channel = channel
        self.user = user
        self.response = FakeResponse()
        self.followup = FakeFollowup()


@pytest.fixture
def prepared_draft_cog_context(migrated_database):
    guild = FakeGuild(1)
    channel = FakeChannel(2000, guild, name="ticket-support-0001")
    repository = TicketRepository(migrated_database)
    repository.create(
        TicketRecord(
            ticket_id="1-support-0001",
            guild_id=1,
            creator_id=42,
            category_key="support",
            channel_id=channel.id,
            status=TicketStatus.DRAFT,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
            has_user_message=False,
        )
    )
    return FakeBot(migrated_database), guild, channel


@pytest.mark.asyncio
async def test_rename_current_draft_updates_channel_and_returns_feedback(
    prepared_draft_cog_context,
    migrated_database,
) -> None:
    bot, guild, channel = prepared_draft_cog_context
    cog = DraftCog(bot)
    interaction = FakeInteraction(guild, channel, FakeUser(42))

    await cog.rename_current_draft(interaction, title="登录异常 复现")

    stored = TicketRepository(migrated_database).get_by_channel_id(channel.id)

    assert interaction.response.messages
    assert "draft 标题已更新" in interaction.response.messages[0]["content"]
    assert "ticket-0001-登录异常-复现" in interaction.response.messages[0]["content"]
    assert channel.name == "ticket-0001-登录异常-复现"
    assert stored is not None
    assert stored.status is TicketStatus.DRAFT


@pytest.mark.asyncio
async def test_abandon_current_draft_requires_confirm_flag(prepared_draft_cog_context) -> None:
    bot, guild, channel = prepared_draft_cog_context
    cog = DraftCog(bot)
    interaction = FakeInteraction(guild, channel, FakeUser(42))

    await cog.abandon_current_draft(interaction, confirm=False)

    assert interaction.response.messages
    assert "请将 confirm 设为 true" in interaction.response.messages[0]["content"]
    assert channel.deleted is False


@pytest.mark.asyncio
async def test_abandon_current_draft_deletes_channel_and_updates_ticket(
    prepared_draft_cog_context,
    migrated_database,
) -> None:
    bot, guild, channel = prepared_draft_cog_context
    cog = DraftCog(bot)
    interaction = FakeInteraction(guild, channel, FakeUser(42))

    await cog.abandon_current_draft(interaction, confirm=True)

    stored = TicketRepository(migrated_database).get_by_channel_id(channel.id)

    assert interaction.response.messages
    assert "draft ticket 已废弃" in interaction.response.messages[0]["content"]
    assert channel.deleted is True
    assert stored is not None
    assert stored.status is TicketStatus.ABANDONED


@pytest.mark.asyncio
async def test_rename_current_draft_rejects_non_creator(prepared_draft_cog_context) -> None:
    bot, guild, channel = prepared_draft_cog_context
    cog = DraftCog(bot)
    interaction = FakeInteraction(guild, channel, FakeUser(99))

    await cog.rename_current_draft(interaction, title="别人的工单")

    assert interaction.response.messages
    assert "只有 ticket 创建者" in interaction.response.messages[0]["content"]
