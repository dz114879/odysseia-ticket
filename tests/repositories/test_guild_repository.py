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
    draft_inactive_close_hours: int = 6,
    draft_abandon_timeout_hours: int = 24,
    transfer_delay_seconds: int = 300,
    close_revoke_window_seconds: int = 120,
    close_request_timeout_seconds: int = 300,
    snapshot_warning_threshold: int = 900,
    snapshot_limit: int = 1000,
    panel_title: str | None = None,
    panel_description: str | None = None,
    panel_bullet_points: str | None = None,
    panel_footer_text: str | None = None,
    draft_welcome_text: str | None = None,
    snapshot_warning_text: str | None = None,
    snapshot_limit_text: str | None = None,
    close_request_text: str | None = None,
    closing_notice_text: str | None = None,
    close_revoke_text: str | None = None,
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
        draft_inactive_close_hours=draft_inactive_close_hours,
        draft_abandon_timeout_hours=draft_abandon_timeout_hours,
        transfer_delay_seconds=transfer_delay_seconds,
        close_revoke_window_seconds=close_revoke_window_seconds,
        close_request_timeout_seconds=close_request_timeout_seconds,
        snapshot_warning_threshold=snapshot_warning_threshold,
        snapshot_limit=snapshot_limit,
        panel_title=panel_title,
        panel_description=panel_description,
        panel_bullet_points=panel_bullet_points,
        panel_footer_text=panel_footer_text,
        draft_welcome_text=draft_welcome_text,
        snapshot_warning_text=snapshot_warning_text,
        snapshot_limit_text=snapshot_limit_text,
        close_request_text=close_request_text,
        closing_notice_text=closing_notice_text,
        close_revoke_text=close_revoke_text,
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


def test_update_config_runtime_numeric_fields(repository: GuildRepository) -> None:
    repository.upsert_config(make_config())

    updated = repository.update_config(
        1,
        draft_inactive_close_hours=12,
        transfer_delay_seconds=600,
        snapshot_limit=2000,
        updated_at="2024-03-01T00:00:00+00:00",
    )

    assert updated is not None
    assert updated.draft_inactive_close_hours == 12
    assert updated.transfer_delay_seconds == 600
    assert updated.snapshot_limit == 2000
    # Unchanged fields retain original values
    assert updated.log_channel_id == 100
    assert updated.panel_title is None
    assert updated.draft_abandon_timeout_hours == 24
    assert updated.close_revoke_window_seconds == 120
    assert updated.close_request_timeout_seconds == 300
    assert updated.snapshot_warning_threshold == 900


def test_update_config_text_override_fields(repository: GuildRepository) -> None:
    repository.upsert_config(make_config())

    updated = repository.update_config(
        1,
        panel_title="Custom Title",
        draft_welcome_text="Welcome!",
        updated_at="2024-03-01T00:00:00+00:00",
    )

    assert updated is not None
    assert updated.panel_title == "Custom Title"
    assert updated.draft_welcome_text == "Welcome!"

    # Clear panel_title by setting it to None, draft_welcome_text should remain
    cleared = repository.update_config(
        1,
        panel_title=None,
        updated_at="2024-04-01T00:00:00+00:00",
    )

    assert cleared is not None
    assert cleared.panel_title is None
    assert cleared.draft_welcome_text == "Welcome!"


def test_upsert_config_roundtrips_all_new_fields(repository: GuildRepository) -> None:
    config = make_config(
        draft_inactive_close_hours=10,
        draft_abandon_timeout_hours=48,
        transfer_delay_seconds=600,
        close_revoke_window_seconds=240,
        close_request_timeout_seconds=600,
        snapshot_warning_threshold=800,
        snapshot_limit=1500,
        panel_title="My Panel",
        panel_description="Panel description text",
        panel_bullet_points="bullet one\nbullet two",
        panel_footer_text="Footer here",
        draft_welcome_text="Welcome to your ticket",
        snapshot_warning_text="Snapshot warning!",
        snapshot_limit_text="Snapshot limit reached",
        close_request_text="Close request sent",
        closing_notice_text="Ticket is closing",
        close_revoke_text="Close was revoked",
    )

    repository.upsert_config(config)
    loaded = repository.get_config(1)

    assert loaded is not None
    assert loaded.draft_inactive_close_hours == 10
    assert loaded.draft_abandon_timeout_hours == 48
    assert loaded.transfer_delay_seconds == 600
    assert loaded.close_revoke_window_seconds == 240
    assert loaded.close_request_timeout_seconds == 600
    assert loaded.snapshot_warning_threshold == 800
    assert loaded.snapshot_limit == 1500
    assert loaded.panel_title == "My Panel"
    assert loaded.panel_description == "Panel description text"
    assert loaded.panel_bullet_points == "bullet one\nbullet two"
    assert loaded.panel_footer_text == "Footer here"
    assert loaded.draft_welcome_text == "Welcome to your ticket"
    assert loaded.snapshot_warning_text == "Snapshot warning!"
    assert loaded.snapshot_limit_text == "Snapshot limit reached"
    assert loaded.close_request_text == "Close request sent"
    assert loaded.closing_notice_text == "Ticket is closing"
    assert loaded.close_revoke_text == "Close was revoked"
