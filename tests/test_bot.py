from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot import TicketBot
from core.enums import ClaimMode
from core.models import GuildConfigRecord, PanelRecord, TicketCategoryConfig
from db.repositories.guild_repository import GuildRepository
from db.repositories.panel_repository import PanelRepository
from discord_ui.public_panel_view import build_public_panel_custom_id


class FakeLoggingService:
    def __init__(self) -> None:
        self.info_messages: list[str] = []
        self.warning_messages: list[str] = []

    def log_local_info(self, message: str, *args) -> None:
        self.info_messages.append(message % args if args else message)

    def log_local_warning(self, message: str, *args) -> None:
        self.warning_messages.append(message % args if args else message)


def seed_active_panel_state(migrated_database) -> PanelRecord:
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
            staff_role_id=500,
            staff_user_ids_json="[]",
            extra_welcome_text="请描述复现步骤。",
            is_enabled=True,
            allowlist_role_ids_json="[]",
            denylist_role_ids_json="[]",
            sort_order=1,
        )
    )

    return PanelRepository(migrated_database).replace_active_panel(
        PanelRecord(
            panel_id="panel-active",
            guild_id=1,
            channel_id=777,
            message_id=1000,
            nonce="nonce-active",
            is_active=True,
            created_by=42,
            created_at="",
            updated_at="",
        )
    )


@pytest.mark.asyncio
async def test_ticket_bot_allows_missing_application_id(make_settings) -> None:
    bot = TicketBot(make_settings(application_id=None))

    try:
        assert bot.settings.application_id is None
    finally:
        await bot.close()



@pytest.mark.asyncio
async def test_setup_hook_restores_active_panel_persistent_views(
    make_settings,
    migrated_database,
) -> None:
    active_panel = seed_active_panel_state(migrated_database)
    logging_service = FakeLoggingService()
    bot = TicketBot(make_settings())
    bot.bootstrap_service.bootstrap = AsyncMock(
        return_value=SimpleNamespace(
            database=migrated_database,
            logging_service=logging_service,
            lock_manager=None,
        )
    )
    bot._load_extensions = AsyncMock()
    bot.add_view = MagicMock()

    try:
        await bot.setup_hook()

        bot._load_extensions.assert_awaited_once()
        bot.add_view.assert_called_once()
        restored_view = bot.add_view.call_args.args[0]

        assert bot.add_view.call_args.kwargs == {"message_id": active_panel.message_id}
        assert len(restored_view.children) == 1

        select = restored_view.children[0]
        assert select.custom_id == build_public_panel_custom_id(1, active_panel.nonce)
        assert [option.value for option in select.options] == ["support"]
        assert select.panel_service is not None

        preview = select.panel_service.preview_panel_request(
            guild_id=1,
            message_id=active_panel.message_id,
            nonce=active_panel.nonce,
            category_key="support",
        )
        assert preview.panel == active_panel
        assert preview.category.category_key == "support"
        assert logging_service.info_messages[-1] == "Restored 1 active panel persistent view(s)."
    finally:
        await bot.close()


@pytest.mark.asyncio
async def test_setup_hook_skips_invalid_active_panel_recovery(
    make_settings,
    migrated_database,
) -> None:
    PanelRepository(migrated_database).replace_active_panel(
        PanelRecord(
            panel_id="panel-invalid",
            guild_id=999,
            channel_id=888,
            message_id=1234,
            nonce="nonce-invalid",
            is_active=True,
            created_by=42,
            created_at="",
            updated_at="",
        )
    )

    logging_service = FakeLoggingService()
    bot = TicketBot(make_settings())
    bot.bootstrap_service.bootstrap = AsyncMock(
        return_value=SimpleNamespace(
            database=migrated_database,
            logging_service=logging_service,
            lock_manager=None,
        )
    )
    bot._load_extensions = AsyncMock()
    bot.add_view = MagicMock()

    try:
        await bot.setup_hook()

        bot.add_view.assert_not_called()
        assert logging_service.info_messages[-1] == "Restored 0 active panel persistent view(s)."
        assert len(logging_service.warning_messages) == 1
        assert "Skipped restoring active panel view." in logging_service.warning_messages[0]
        assert "guild_id=999" in logging_service.warning_messages[0]
    finally:
        await bot.close()
