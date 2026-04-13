from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.enums import ClaimMode
from core.errors import StaleInteractionError
from core.models import GuildConfigRecord, TicketCategoryConfig
from db.repositories.guild_repository import GuildRepository
from db.repositories.panel_repository import PanelRepository
from services.panel_service import PanelService


class FakeMessage:
    def __init__(self, message_id: int, channel: FakeChannel, *, embed, view) -> None:
        self.id = message_id
        self.channel = channel
        self.embed = embed
        self.view = view
        self.edited_payloads: list[dict] = []
        self.deleted = False

    async def edit(self, *, embed, view) -> None:
        self.embed = embed
        self.view = view
        self.edited_payloads.append({"embed": embed, "view": view})

    async def delete(self) -> None:
        self.deleted = True
        self.channel.messages.pop(self.id, None)


class FakeChannel:
    def __init__(self, channel_id: int, guild_id: int) -> None:
        self.id = channel_id
        self.guild = SimpleNamespace(id=guild_id)
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


class FakeBot:
    def __init__(self, channel: FakeChannel) -> None:
        self.channel = channel

    def get_channel(self, channel_id: int) -> FakeChannel | None:
        return self.channel if self.channel.id == channel_id else None

    async def fetch_channel(self, channel_id: int) -> FakeChannel:
        if self.channel.id != channel_id:
            raise LookupError("channel not found")
        return self.channel


@pytest.fixture
def prepared_guild(migrated_database) -> tuple[GuildRepository, FakeChannel]:
    repository = GuildRepository(migrated_database)
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
            staff_role_ids_json='[500]',
            staff_user_ids_json="[]",
            is_enabled=True,
            allowlist_role_ids_json="[]",
            denylist_role_ids_json="[]",
            sort_order=1,
        )
    )
    repository.upsert_category(
        TicketCategoryConfig(
            guild_id=1,
            category_key="feedback",
            display_name="运营建议",
            emoji="💡",
            description="处理反馈建议",
            staff_role_ids_json='[501]',
            staff_user_ids_json="[]",
            is_enabled=True,
            allowlist_role_ids_json="[]",
            denylist_role_ids_json="[]",
            sort_order=2,
        )
    )
    return repository, FakeChannel(777, 1)


@pytest.mark.asyncio
async def test_create_panel_publishes_message_and_persists_active_record(
    migrated_database,
    prepared_guild,
) -> None:
    _, channel = prepared_guild
    service = PanelService(migrated_database, bot=FakeBot(channel))

    result = await service.create_panel(channel, created_by=42)

    repository = PanelRepository(migrated_database)
    stored = repository.get_active_panel(1)

    assert stored == result.record
    assert result.message.embed.title == "🎫 Ticket 支持中心"
    assert len(result.message.view.children) == 1
    select = result.message.view.children[0]
    assert select.custom_id.startswith("panel:create:1:")
    assert [option.value for option in select.options] == ["support", "feedback"]


@pytest.mark.asyncio
async def test_create_panel_replaces_previous_active_panel(
    migrated_database,
    prepared_guild,
) -> None:
    _, channel = prepared_guild
    service = PanelService(migrated_database, bot=FakeBot(channel))

    first = await service.create_panel(channel, created_by=1)
    second = await service.create_panel(channel, created_by=2)

    repository = PanelRepository(migrated_database)
    old_record = repository.get_by_panel_id(first.record.panel_id)
    active_record = repository.get_active_panel(1)

    assert old_record is not None
    assert old_record.is_active is False
    assert active_record == second.record
    assert active_record is not None
    assert active_record.nonce != first.record.nonce


@pytest.mark.asyncio
async def test_refresh_active_panel_rotates_nonce_and_invalidates_old_context(
    migrated_database,
    prepared_guild,
) -> None:
    _, channel = prepared_guild
    service = PanelService(migrated_database, bot=FakeBot(channel))
    created = await service.create_panel(channel, created_by=42)
    previous_nonce = created.record.nonce

    refreshed = await service.refresh_active_panel(1)
    stored = PanelRepository(migrated_database).get_active_panel(1)

    assert refreshed.record.panel_id == created.record.panel_id
    assert refreshed.record.message_id == created.record.message_id
    assert refreshed.record.nonce != previous_nonce
    assert stored == refreshed.record
    assert refreshed.message.edited_payloads
    assert refreshed.message.view.children[0].custom_id.endswith(refreshed.record.nonce)

    with pytest.raises(StaleInteractionError, match="面板已过期"):
        service.preview_panel_request(
            guild_id=1,
            message_id=created.record.message_id,
            nonce=previous_nonce,
            category_key="support",
        )

    preview = service.preview_panel_request(
        guild_id=1,
        message_id=created.record.message_id,
        nonce=refreshed.record.nonce,
        category_key="support",
    )
    assert preview.panel.nonce == refreshed.record.nonce
    assert preview.category.category_key == "support"


@pytest.mark.asyncio
async def test_remove_active_panel_marks_inactive_and_optionally_deletes_message(
    migrated_database,
    prepared_guild,
) -> None:
    _, channel = prepared_guild
    service = PanelService(migrated_database, bot=FakeBot(channel))
    created = await service.create_panel(channel, created_by=42)

    removed = await service.remove_active_panel(1, delete_message=True)

    repository = PanelRepository(migrated_database)
    stored = repository.get_by_panel_id(created.record.panel_id)

    assert removed.record.is_active is False
    assert removed.message_deleted is True
    assert stored is not None
    assert stored.is_active is False
    assert repository.get_active_panel(1) is None


def test_preview_panel_request_requires_active_panel(migrated_database) -> None:
    service = PanelService(migrated_database)

    with pytest.raises(StaleInteractionError, match="面板已过期"):
        service.preview_panel_request(
            guild_id=1,
            message_id=1000,
            nonce="nonce",
            category_key="support",
        )
