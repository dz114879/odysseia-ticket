from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest

from core.enums import ClaimMode
from core.models import GuildConfigRecord
from db.repositories.guild_repository import GuildRepository
from discord_ui.config_views import (
    BasicSettingsModal,
    DraftWelcomeTextModal,
    PanelTextModal,
    SnapshotTextModal,
    TextGroupSelect,
)


class FakeResponse:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.deferred: list[dict[str, object]] = []
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
        self.messages: list[dict[str, object]] = []

    async def send(self, content: str | None = None, *, embed=None, view=None, ephemeral: bool = False) -> None:
        self.messages.append({"content": content, "embed": embed, "view": view, "ephemeral": ephemeral})


class FakeClient:
    def __init__(self, migrated_database) -> None:
        self.resources = SimpleNamespace(database=migrated_database, logging_service=SimpleNamespace())


class FakeInteraction:
    def __init__(self, migrated_database) -> None:
        self.client = FakeClient(migrated_database)
        self.response = FakeResponse()
        self.followup = FakeFollowup()


def seed_config(migrated_database, **overrides) -> GuildConfigRecord:
    repository = GuildRepository(migrated_database)
    values = {
        "guild_id": 1,
        "is_initialized": True,
        "log_channel_id": 100,
        "archive_channel_id": 200,
        "ticket_category_channel_id": 300,
        "admin_role_id": 400,
        "claim_mode": ClaimMode.RELAXED,
        "max_open_tickets": 10,
        "timezone": "Asia/Hong_Kong",
        "enable_download_window": True,
        "updated_at": "2024-01-01T00:00:00+00:00",
    }
    values.update(overrides)
    repository.upsert_config(GuildConfigRecord(**values))
    return repository.get_config(1)


def test_text_group_select_hides_close_text_option(migrated_database) -> None:
    select = TextGroupSelect(guild_id=1, config=seed_config(migrated_database))

    assert [option.value for option in select.options] == ["panel", "draft_welcome", "snapshot_text"]


def test_basic_settings_modal_uses_selects_for_fixed_options(migrated_database) -> None:
    modal = BasicSettingsModal(
        guild_id=1,
        config=seed_config(
            migrated_database,
            claim_mode=ClaimMode.STRICT,
            enable_download_window=False,
        ),
    )

    assert isinstance(modal.claim_mode_select, discord.ui.Select)
    assert isinstance(modal.download_window_select, discord.ui.Select)
    assert [option.value for option in modal.claim_mode_select.options if option.default] == ["strict"]
    assert [option.value for option in modal.download_window_select.options if option.default] == ["false"]


def test_panel_text_modal_prefills_merged_legacy_body(migrated_database) -> None:
    modal = PanelTextModal(
        guild_id=1,
        config=seed_config(
            migrated_database,
            panel_description="在这里，您可以：",
            panel_bullet_points="- 第一项\n- 第二项",
        ),
    )

    assert modal.body_input.default == "在这里，您可以：\n- 第一项\n- 第二项"


@pytest.mark.asyncio
async def test_panel_text_modal_direct_save_canonicalizes_legacy_fields(migrated_database) -> None:
    config = seed_config(
        migrated_database,
        panel_description="在这里，您可以：",
        panel_bullet_points="- 第一项\n- 第二项",
    )
    modal = PanelTextModal(guild_id=1, config=config)
    modal.title_input._value = modal.title_input.default or ""
    modal.body_input._value = modal.body_input.default or ""
    modal.footer_input._value = modal.footer_input.default or ""
    interaction = FakeInteraction(migrated_database)

    await modal.on_submit(interaction)

    updated = GuildRepository(migrated_database).get_config(1)

    assert interaction.response.deferred
    assert interaction.followup.messages
    assert "配置已更新" in str(interaction.followup.messages[0]["content"])
    assert updated is not None
    assert updated.panel_description == "在这里，您可以：\n- 第一项\n- 第二项"
    assert updated.panel_bullet_points is None


@pytest.mark.asyncio
async def test_draft_welcome_modal_prefills_runtime_default_and_direct_save_is_noop(migrated_database) -> None:
    config = seed_config(
        migrated_database,
        draft_inactive_close_hours=8,
        draft_abandon_timeout_hours=36,
        draft_welcome_text=None,
    )
    modal = DraftWelcomeTextModal(guild_id=1, config=config)
    modal.welcome_input._value = modal.welcome_input.default or ""
    interaction = FakeInteraction(migrated_database)

    await modal.on_submit(interaction)

    updated = GuildRepository(migrated_database).get_config(1)

    assert "最多 36 小时" in (modal.welcome_input.default or "")
    assert interaction.response.deferred
    assert interaction.followup.messages == [{"content": "未检测到变更。", "embed": None, "view": None, "ephemeral": True}]
    assert updated is not None
    assert updated.draft_welcome_text is None


@pytest.mark.asyncio
async def test_snapshot_text_modal_prefills_runtime_defaults_and_direct_save_is_noop(migrated_database) -> None:
    config = seed_config(
        migrated_database,
        snapshot_warning_text=None,
        snapshot_limit_text=None,
        snapshot_limit=1200,
    )
    modal = SnapshotTextModal(guild_id=1, config=config)
    modal.warning_input._value = modal.warning_input.default or ""
    modal.limit_input._value = modal.limit_input.default or ""
    interaction = FakeInteraction(migrated_database)

    await modal.on_submit(interaction)

    updated = GuildRepository(migrated_database).get_config(1)

    assert "1200条" in (modal.warning_input.default or "")
    assert "1200条" in (modal.limit_input.default or "")
    assert interaction.followup.messages == [{"content": "未检测到变更。", "embed": None, "view": None, "ephemeral": True}]
    assert updated is not None
    assert updated.snapshot_warning_text is None
    assert updated.snapshot_limit_text is None
