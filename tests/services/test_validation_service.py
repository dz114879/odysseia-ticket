from __future__ import annotations

import pytest

from core.enums import ClaimMode
from core.errors import StaleInteractionError, ValidationError
from core.models import GuildConfigRecord, PanelRecord, TicketCategoryConfig
from db.repositories.guild_repository import GuildRepository
from db.repositories.panel_repository import PanelRepository
from services.validation_service import ValidationService


def make_config(
    guild_id: int = 1,
    *,
    is_initialized: bool = True,
    log_channel_id: int | None = 100,
    archive_channel_id: int | None = 200,
    ticket_category_channel_id: int | None = 300,
    admin_role_id: int | None = 400,
) -> GuildConfigRecord:
    return GuildConfigRecord(
        guild_id=guild_id,
        is_initialized=is_initialized,
        log_channel_id=log_channel_id,
        archive_channel_id=archive_channel_id,
        ticket_category_channel_id=ticket_category_channel_id,
        admin_role_id=admin_role_id,
        claim_mode=ClaimMode.RELAXED,
        max_open_tickets=10,
        timezone="Asia/Hong_Kong",
        enable_download_window=True,
        updated_at="2024-01-01T00:00:00+00:00",
    )


def make_category(
    category_key: str,
    *,
    is_enabled: bool = True,
) -> TicketCategoryConfig:
    return TicketCategoryConfig(
        guild_id=1,
        category_key=category_key,
        display_name=category_key.title(),
        emoji="🎫",
        description=f"{category_key} description",
        staff_role_ids_json='[500]',
        staff_user_ids_json="[]",
        is_enabled=is_enabled,
        allowlist_role_ids_json="[]",
        denylist_role_ids_json="[]",
        sort_order=1,
    )


def make_panel(
    *,
    nonce: str = "nonce-1",
    message_id: int = 1000,
) -> PanelRecord:
    return PanelRecord(
        panel_id="panel-1",
        guild_id=1,
        channel_id=123,
        message_id=message_id,
        nonce=nonce,
        is_active=True,
        created_by=42,
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T00:00:00+00:00",
    )


@pytest.fixture
def validation_service(migrated_database) -> ValidationService:
    return ValidationService(migrated_database)


def test_assert_panel_creation_ready_requires_initialized_complete_config(
    migrated_database,
    validation_service: ValidationService,
) -> None:
    repository = GuildRepository(migrated_database)
    repository.upsert_config(make_config(archive_channel_id=None))
    repository.upsert_category(make_category("support"))

    with pytest.raises(ValidationError, match="配置尚未完成"):
        validation_service.assert_panel_creation_ready(1)


def test_assert_panel_creation_ready_returns_enabled_categories(
    migrated_database,
    validation_service: ValidationService,
) -> None:
    repository = GuildRepository(migrated_database)
    repository.upsert_config(make_config())
    repository.upsert_category(make_category("support", is_enabled=True))
    repository.upsert_category(make_category("billing", is_enabled=False))

    config, categories = validation_service.assert_panel_creation_ready(1)

    assert config.guild_id == 1
    assert [category.category_key for category in categories] == ["support"]


def test_validate_panel_request_rejects_stale_nonce_and_disabled_category(
    migrated_database,
    validation_service: ValidationService,
) -> None:
    guild_repository = GuildRepository(migrated_database)
    panel_repository = PanelRepository(migrated_database)
    guild_repository.upsert_config(make_config())
    guild_repository.upsert_category(make_category("support", is_enabled=True))
    guild_repository.upsert_category(make_category("hidden", is_enabled=False))
    panel_repository.replace_active_panel(make_panel(nonce="nonce-current", message_id=9001))

    with pytest.raises(StaleInteractionError, match="面板已过期"):
        validation_service.validate_panel_request(
            1,
            nonce="nonce-old",
            message_id=9001,
            category_key="support",
        )

    with pytest.raises(ValidationError, match="分类当前不可用"):
        validation_service.validate_panel_request(
            1,
            nonce="nonce-current",
            message_id=9001,
            category_key="hidden",
        )


def test_validate_panel_request_accepts_rotated_nonce_and_rejects_previous_nonce(
    migrated_database,
    validation_service: ValidationService,
) -> None:
    guild_repository = GuildRepository(migrated_database)
    panel_repository = PanelRepository(migrated_database)
    guild_repository.upsert_config(make_config())
    guild_repository.upsert_category(make_category("support", is_enabled=True))
    panel_repository.replace_active_panel(make_panel(nonce="nonce-before-refresh", message_id=9001))
    panel_repository.update(
        "panel-1",
        nonce="nonce-after-refresh",
        updated_at="2024-01-02T00:00:00+00:00",
    )

    with pytest.raises(StaleInteractionError, match="面板已过期"):
        validation_service.validate_panel_request(
            1,
            nonce="nonce-before-refresh",
            message_id=9001,
            category_key="support",
        )

    validation = validation_service.validate_panel_request(
        1,
        nonce="nonce-after-refresh",
        message_id=9001,
        category_key="support",
    )
    assert validation.panel.nonce == "nonce-after-refresh"
