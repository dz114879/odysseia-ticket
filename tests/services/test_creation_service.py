from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.enums import ClaimMode, TicketStatus
from core.errors import StaleInteractionError
from core.models import GuildConfigRecord, PanelRecord, TicketCategoryConfig
from db.repositories.counter_repository import CounterRepository
from db.repositories.guild_repository import GuildRepository
from db.repositories.panel_repository import PanelRepository
from db.repositories.ticket_repository import TicketRepository
from runtime.locks import LockManager
from services.creation_service import CreationService


@dataclass(frozen=True)
class FakeRole:
    id: int


@dataclass(frozen=True)
class FakeMember:
    id: int

    @property
    def mention(self) -> str:
        return f"<@{self.id}>"


@dataclass(frozen=True)
class FakeCategoryChannel:
    id: int
    name: str = "Tickets"


class FakeMessage:
    def __init__(self, message_id: int, channel: FakeTextChannel, *, content: str, embed=None, view=None) -> None:
        self.id = message_id
        self.channel = channel
        self.content = content
        self.embed = embed
        self.view = view
        self.pinned = False

    async def pin(self, *, reason: str | None = None) -> None:
        self.pinned = True


class FakeTextChannel:
    def __init__(
        self,
        channel_id: int,
        guild: FakeGuild,
        *,
        name: str,
        category: FakeCategoryChannel,
        overwrites,
    ) -> None:
        self.id = channel_id
        self.guild = guild
        self.name = name
        self.category = category
        self.overwrites = overwrites
        self.messages: list[FakeMessage] = []
        self.deleted = False

    @property
    def mention(self) -> str:
        return f"<#{self.id}>"

    async def send(self, *, content: str, embed=None, view=None) -> FakeMessage:
        message = FakeMessage(1000 + len(self.messages), self, content=content, embed=embed, view=view)
        self.messages.append(message)
        return message

    async def delete(self, *, reason: str | None = None) -> None:
        self.deleted = True
        self.guild.channels.pop(self.id, None)


class FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id
        self.default_role = FakeRole(0)
        self.me = FakeMember(9999)
        self.channels: dict[int, object] = {300: FakeCategoryChannel(300)}
        self.roles: dict[int, FakeRole] = {
            400: FakeRole(400),
            500: FakeRole(500),
        }
        self.members: dict[int, FakeMember] = {self.me.id: self.me}
        self.next_channel_id = 2000
        self.created_channels: list[FakeTextChannel] = []

    def add_member(self, member: FakeMember) -> None:
        self.members[member.id] = member

    def get_channel(self, channel_id: int):
        return self.channels.get(channel_id)

    def get_role(self, role_id: int) -> FakeRole | None:
        return self.roles.get(role_id)

    def get_member(self, member_id: int) -> FakeMember | None:
        return self.members.get(member_id)

    async def create_text_channel(
        self,
        name: str,
        *,
        category: FakeCategoryChannel,
        overwrites,
        reason: str | None = None,
    ) -> FakeTextChannel:
        channel = FakeTextChannel(
            self.next_channel_id,
            self,
            name=name,
            category=category,
            overwrites=overwrites,
        )
        self.channels[channel.id] = channel
        self.created_channels.append(channel)
        self.next_channel_id += 1
        return channel


@pytest.fixture
def prepared_creation_context(migrated_database):
    guild = FakeGuild(1)
    creator = FakeMember(42)
    guild.add_member(creator)

    guild_repository = GuildRepository(migrated_database)
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
            staff_role_ids_json="[500]",
            staff_user_ids_json="[]",
            is_enabled=True,
            allowlist_role_ids_json="[]",
            denylist_role_ids_json="[]",
            sort_order=1,
        )
    )

    panel_repository = PanelRepository(migrated_database)
    panel_repository.replace_active_panel(
        PanelRecord(
            panel_id="panel-1",
            guild_id=1,
            channel_id=777,
            message_id=888,
            nonce="nonce-123",
            is_active=True,
            created_by=9000,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )
    )

    return guild, creator


@pytest.mark.asyncio
async def test_create_draft_ticket_happy_path_persists_ticket_and_private_channel(
    migrated_database,
    prepared_creation_context,
) -> None:
    guild, creator = prepared_creation_context
    service = CreationService(migrated_database, lock_manager=LockManager())

    result = await service.create_draft_ticket(
        guild=guild,
        creator=creator,
        category_key="support",
        source_panel_message_id=888,
        source_panel_nonce="nonce-123",
    )

    ticket_repository = TicketRepository(migrated_database)
    counter_repository = CounterRepository(migrated_database)
    stored_ticket = ticket_repository.get_by_ticket_id(result.ticket.ticket_id)
    counter = counter_repository.get_counter(1, "support")
    channel = result.channel

    assert result.created is True
    assert stored_ticket == result.ticket
    assert result.ticket.status is TicketStatus.DRAFT
    assert result.ticket.channel_id == channel.id
    assert result.ticket.ticket_id == "1-support-0001"
    assert channel.name == "技术支持"
    assert channel.category.id == 300
    assert channel.overwrites[guild.default_role].view_channel is False
    assert channel.overwrites[guild.get_role(400)].view_channel is False
    assert channel.overwrites[guild.get_role(500)].view_channel is False
    assert channel.overwrites[creator].view_channel is True
    assert channel.overwrites[guild.me].view_channel is True
    assert result.welcome_message is not None
    assert result.welcome_message.pinned is True
    assert result.welcome_message.view is not None
    assert result.welcome_message.embed is not None
    assert "技术支持" in result.welcome_message.embed.title
    assert counter is not None
    assert counter.next_number == 2


@pytest.mark.asyncio
async def test_create_draft_ticket_returns_existing_draft_for_duplicate_request(
    migrated_database,
    prepared_creation_context,
) -> None:
    guild, creator = prepared_creation_context
    service = CreationService(migrated_database, lock_manager=LockManager())

    first = await service.create_draft_ticket(
        guild=guild,
        creator=creator,
        category_key="support",
        source_panel_message_id=888,
        source_panel_nonce="nonce-123",
    )
    second = await service.create_draft_ticket(
        guild=guild,
        creator=creator,
        category_key="support",
        source_panel_message_id=888,
        source_panel_nonce="nonce-123",
    )

    drafts = TicketRepository(migrated_database).list_by_guild(
        1,
        statuses=[TicketStatus.DRAFT],
        creator_id=creator.id,
    )

    assert first.created is True
    assert second.created is False
    assert second.ticket.ticket_id == first.ticket.ticket_id
    assert second.channel.id == first.channel.id
    assert len(drafts) == 1


@pytest.mark.asyncio
async def test_create_draft_ticket_rejects_stale_panel_nonce(
    migrated_database,
    prepared_creation_context,
) -> None:
    guild, creator = prepared_creation_context
    service = CreationService(migrated_database, lock_manager=LockManager())

    with pytest.raises(StaleInteractionError, match="面板已过期"):
        await service.create_draft_ticket(
            guild=guild,
            creator=creator,
            category_key="support",
            source_panel_message_id=888,
            source_panel_nonce="expired-nonce",
        )
