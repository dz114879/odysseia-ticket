from __future__ import annotations

import pytest

from core.enums import ClaimMode
from core.models import GuildConfigRecord, TicketCategoryConfig
from db.repositories.guild_repository import GuildRepository


@pytest.fixture
def repository(migrated_database) -> GuildRepository:
    return GuildRepository(migrated_database)


def make_config(
    guild_id: int = 1,
    *,
    is_initialized: bool = True,
    log_channel_id: int | None = 100,
    archive_channel_id: int | None = 200,
    ticket_category_channel_id: int | None = 300,
    admin_role_id: int | None = 400,
    claim_mode: ClaimMode = ClaimMode.RELAXED,
    max_open_tickets: int = 25,
    timezone: str = "Asia/Hong_Kong",
    enable_download_window: bool = True,
    updated_at: str = "2024-01-01T00:00:00+00:00",
) -> GuildConfigRecord:
    return GuildConfigRecord(
        guild_id=guild_id,
        is_initialized=is_initialized,
        log_channel_id=log_channel_id,
        archive_channel_id=archive_channel_id,
        ticket_category_channel_id=ticket_category_channel_id,
        admin_role_id=admin_role_id,
        claim_mode=claim_mode,
        max_open_tickets=max_open_tickets,
        timezone=timezone,
        enable_download_window=enable_download_window,
        updated_at=updated_at,
    )


def make_category(
    category_key: str,
    *,
    guild_id: int = 1,
    display_name: str | None = None,
    sort_order: int = 0,
    is_enabled: bool = True,
) -> TicketCategoryConfig:
    return TicketCategoryConfig(
        guild_id=guild_id,
        category_key=category_key,
        display_name=display_name or category_key.title(),
        emoji="🎫",
        description=f"{category_key} description",
        staff_role_ids_json='[500]',
        staff_user_ids_json="[1, 2]",
        is_enabled=is_enabled,
        allowlist_role_ids_json="[10]",
        denylist_role_ids_json="[20]",
        sort_order=sort_order,
    )


def test_upsert_and_get_config_returns_dataclass(repository: GuildRepository) -> None:
    stored = repository.upsert_config(
        make_config(
            claim_mode=ClaimMode.STRICT,
            enable_download_window=False,
        )
    )

    loaded = repository.get_config(1)

    assert stored == loaded
    assert isinstance(loaded, GuildConfigRecord)
    assert loaded is not None
    assert loaded.claim_mode is ClaimMode.STRICT
    assert loaded.enable_download_window is False
    assert loaded.is_initialized is True


def test_update_config_only_changes_requested_fields(repository: GuildRepository) -> None:
    repository.upsert_config(make_config())

    updated = repository.update_config(
        1,
        ticket_category_channel_id=None,
        max_open_tickets=50,
        claim_mode=ClaimMode.STRICT,
        updated_at="2024-02-01T00:00:00+00:00",
    )

    assert updated is not None
    assert updated.log_channel_id == 100
    assert updated.archive_channel_id == 200
    assert updated.ticket_category_channel_id is None
    assert updated.max_open_tickets == 50
    assert updated.claim_mode is ClaimMode.STRICT
    assert updated.updated_at == "2024-02-01T00:00:00+00:00"
    assert repository.update_config(999, timezone="UTC") is None


def test_category_crud_and_enabled_filtering(repository: GuildRepository) -> None:
    bug = repository.upsert_category(make_category("bug", sort_order=2, is_enabled=True))
    repository.upsert_category(make_category("billing", sort_order=1, is_enabled=False))

    loaded = repository.get_category(1, "bug")
    enabled_categories = repository.list_categories(1, enabled_only=True)

    assert bug == loaded
    assert isinstance(loaded, TicketCategoryConfig)
    assert loaded is not None
    assert loaded.is_enabled is True
    assert [category.category_key for category in enabled_categories] == ["bug"]

    assert repository.delete_category(1, "billing") is True
    assert repository.delete_category(1, "billing") is False


def test_replace_categories_replaces_previous_set(repository: GuildRepository) -> None:
    repository.upsert_category(make_category("legacy", sort_order=0))

    replaced = repository.replace_categories(
        1,
        [
            make_category("support", sort_order=2),
            make_category("appeal", sort_order=1),
        ],
    )

    assert [category.category_key for category in replaced] == ["appeal", "support"]
    assert repository.get_category(1, "legacy") is None
