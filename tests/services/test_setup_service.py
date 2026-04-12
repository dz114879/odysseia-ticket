from __future__ import annotations

import pytest

from config.defaults import DEFAULT_TICKET_CATEGORY_TEMPLATES
from core.enums import ClaimMode
from core.errors import ValidationError
from core.models import TicketCategoryConfig
from db.repositories.guild_repository import GuildRepository
from services.setup_service import SetupService


class FakeChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeGuild:
    def __init__(
        self,
        guild_id: int,
        *,
        channels: list[FakeChannel],
        roles: list[FakeRole],
    ) -> None:
        self.id = guild_id
        self._channels = {channel.id: channel for channel in channels}
        self._roles = {role.id: role for role in roles}

    def get_channel(self, channel_id: int) -> FakeChannel | None:
        return self._channels.get(channel_id)

    def get_role(self, role_id: int) -> FakeRole | None:
        return self._roles.get(role_id)


@pytest.fixture
def fake_guild() -> FakeGuild:
    return FakeGuild(
        1,
        channels=[FakeChannel(100), FakeChannel(200), FakeChannel(300)],
        roles=[FakeRole(400)],
    )


def test_setup_guild_creates_initialized_config_and_default_categories(
    migrated_database,
    fake_guild: FakeGuild,
) -> None:
    service = SetupService(migrated_database)

    result = service.setup_guild(
        fake_guild,
        log_channel_id=100,
        archive_channel_id=200,
        ticket_category_channel_id=300,
        admin_role_id=400,
        claim_mode=ClaimMode.STRICT,
        max_open_tickets=24,
        timezone="Asia/Hong_Kong",
        enable_download_window=False,
    )

    assert result.created_default_categories is True
    assert result.config.guild_id == 1
    assert result.config.is_initialized is True
    assert result.config.log_channel_id == 100
    assert result.config.archive_channel_id == 200
    assert result.config.ticket_category_channel_id == 300
    assert result.config.admin_role_id == 400
    assert result.config.claim_mode is ClaimMode.STRICT
    assert result.config.max_open_tickets == 24
    assert result.config.timezone == "Asia/Hong_Kong"
    assert result.config.enable_download_window is False
    assert len(result.categories) == len(DEFAULT_TICKET_CATEGORY_TEMPLATES)
    assert [category.category_key for category in result.categories] == [template.category_key for template in DEFAULT_TICKET_CATEGORY_TEMPLATES]


def test_setup_guild_keeps_existing_categories(
    migrated_database,
    fake_guild: FakeGuild,
) -> None:
    repository = GuildRepository(migrated_database)
    repository.upsert_category(
        TicketCategoryConfig(
            guild_id=1,
            category_key="custom",
            display_name="自定义分类",
            emoji="✨",
            description="custom category",
            staff_role_id=999,
            staff_user_ids_json="[]",
            is_enabled=True,
            allowlist_role_ids_json="[]",
            denylist_role_ids_json="[]",
            sort_order=1,
        )
    )

    service = SetupService(migrated_database)
    result = service.setup_guild(
        fake_guild,
        log_channel_id=100,
        archive_channel_id=200,
        ticket_category_channel_id=300,
        admin_role_id=400,
    )

    assert result.created_default_categories is False
    assert [category.category_key for category in result.categories] == ["custom"]


def test_setup_guild_rejects_missing_setup_targets(migrated_database) -> None:
    service = SetupService(migrated_database)
    guild = FakeGuild(
        1,
        channels=[FakeChannel(100), FakeChannel(300)],
        roles=[FakeRole(400)],
    )

    with pytest.raises(ValidationError, match="归档频道不存在"):
        service.setup_guild(
            guild,
            log_channel_id=100,
            archive_channel_id=200,
            ticket_category_channel_id=300,
            admin_role_id=400,
        )
