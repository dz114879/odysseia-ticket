from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Protocol

from core.errors import StaleInteractionError, ValidationError
from core.models import GuildConfigRecord, PanelRecord, TicketCategoryConfig
from db.connection import DatabaseManager
from db.repositories.guild_repository import GuildRepository
from db.repositories.panel_repository import PanelRepository


class GuildLike(Protocol):
    id: int

    def get_channel(self, channel_id: int) -> object | None:
        ...

    def get_role(self, role_id: int) -> object | None:
        ...


@dataclass(frozen=True, slots=True)
class PanelSelectionValidation:
    config: GuildConfigRecord
    panel: PanelRecord
    category: TicketCategoryConfig


class ValidationService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        guild_repository: GuildRepository | None = None,
        panel_repository: PanelRepository | None = None,
    ) -> None:
        self.database = database
        self.guild_repository = guild_repository or GuildRepository(database)
        self.panel_repository = panel_repository or PanelRepository(database)

    def validate_setup_targets(
        self,
        guild: GuildLike | None,
        *,
        log_channel_id: int,
        archive_channel_id: int,
        ticket_category_channel_id: int,
        admin_role_id: int,
    ) -> None:
        if guild is None:
            raise ValidationError("该命令只能在服务器中使用。")

        required_channels = {
            "日志频道": log_channel_id,
            "归档频道": archive_channel_id,
            "Ticket 承载分类": ticket_category_channel_id,
        }
        for field_name, channel_id in required_channels.items():
            if guild.get_channel(channel_id) is None:
                raise ValidationError(f"{field_name}不存在，请重新选择。")

        if guild.get_role(admin_role_id) is None:
            raise ValidationError("管理员角色不存在，请重新选择。")

    def assert_panel_creation_ready(
        self,
        guild_id: int,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> tuple[GuildConfigRecord, list[TicketCategoryConfig]]:
        config = self.guild_repository.get_config(guild_id, connection=connection)
        validated_config = self._assert_minimum_config(config)

        categories = self.guild_repository.list_categories(
            guild_id,
            enabled_only=True,
            connection=connection,
        )
        if not categories:
            raise ValidationError("当前服务器还没有可用的 Ticket 分类。")
        return validated_config, categories

    def validate_panel_request(
        self,
        guild_id: int,
        *,
        nonce: str,
        message_id: int,
        category_key: str,
        connection: sqlite3.Connection | None = None,
    ) -> PanelSelectionValidation:
        active_panel = self.panel_repository.get_active_panel(
            guild_id,
            connection=connection,
        )
        if active_panel is None:
            raise StaleInteractionError("此面板已过期，请使用最新的 Ticket 面板。")
        if active_panel.nonce != nonce or active_panel.message_id != message_id:
            raise StaleInteractionError("此面板已过期，请使用最新的 Ticket 面板。")

        config, _ = self.assert_panel_creation_ready(guild_id, connection=connection)
        category = self.guild_repository.get_category(
            guild_id,
            category_key,
            connection=connection,
        )
        if category is None or not category.is_enabled:
            raise ValidationError("该分类当前不可用。")

        return PanelSelectionValidation(
            config=config,
            panel=active_panel,
            category=category,
        )

    def _assert_minimum_config(
        self,
        config: GuildConfigRecord | None,
    ) -> GuildConfigRecord:
        if config is None or not config.is_initialized:
            raise ValidationError("服务器尚未完成 Ticket 初始化。")

        missing_fields = [
            field_name
            for field_name, value in (
                ("日志频道", config.log_channel_id),
                ("归档频道", config.archive_channel_id),
                ("Ticket 承载分类", config.ticket_category_channel_id),
                ("管理员角色", config.admin_role_id),
            )
            if value is None
        ]
        if missing_fields:
            raise ValidationError(
                "当前服务器的 Ticket 配置尚未完成：缺少"
                + "、".join(missing_fields)
                + "。"
            )

        return config
