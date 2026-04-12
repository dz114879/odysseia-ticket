from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from config.defaults import DEFAULT_TICKET_CATEGORY_TEMPLATES
from core.models import GuildConfigRecord, TicketCategoryConfig
from db.connection import DatabaseManager
from db.repositories.guild_repository import GuildRepository


@dataclass(frozen=True, slots=True)
class GuildConfigSnapshot:
    config: GuildConfigRecord | None
    categories: list[TicketCategoryConfig]


class GuildConfigService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        repository: GuildRepository | None = None,
    ) -> None:
        self.database = database
        self.repository = repository or GuildRepository(database)

    def get_config(
        self,
        guild_id: int,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> GuildConfigRecord | None:
        return self.repository.get_config(guild_id, connection=connection)

    def list_categories(
        self,
        guild_id: int,
        *,
        enabled_only: bool = False,
        connection: sqlite3.Connection | None = None,
    ) -> list[TicketCategoryConfig]:
        return self.repository.list_categories(
            guild_id,
            enabled_only=enabled_only,
            connection=connection,
        )

    def get_snapshot(
        self,
        guild_id: int,
        *,
        enabled_only: bool = False,
        connection: sqlite3.Connection | None = None,
    ) -> GuildConfigSnapshot:
        return GuildConfigSnapshot(
            config=self.get_config(guild_id, connection=connection),
            categories=self.list_categories(
                guild_id,
                enabled_only=enabled_only,
                connection=connection,
            ),
        )

    def upsert_config(
        self,
        record: GuildConfigRecord,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> GuildConfigRecord:
        return self.repository.upsert_config(record, connection=connection)

    def replace_categories(
        self,
        guild_id: int,
        categories: list[TicketCategoryConfig],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[TicketCategoryConfig]:
        return self.repository.replace_categories(
            guild_id,
            categories,
            connection=connection,
        )

    def build_default_categories(self, guild_id: int) -> list[TicketCategoryConfig]:
        return [
            TicketCategoryConfig(
                guild_id=guild_id,
                category_key=template.category_key,
                display_name=template.display_name,
                emoji=template.emoji,
                description=template.description,
                staff_role_id=None,
                staff_user_ids_json="[]",
                is_enabled=template.is_enabled,
                allowlist_role_ids_json="[]",
                denylist_role_ids_json="[]",
                sort_order=template.sort_order,
            )
            for template in DEFAULT_TICKET_CATEGORY_TEMPLATES
        ]

    def ensure_default_categories(
        self,
        guild_id: int,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> list[TicketCategoryConfig]:
        existing_categories = self.list_categories(guild_id, connection=connection)
        if existing_categories:
            return existing_categories

        default_categories = self.build_default_categories(guild_id)
        return self.replace_categories(
            guild_id,
            default_categories,
            connection=connection,
        )
