from __future__ import annotations

from dataclasses import dataclass

from config.static import DEFAULT_GUILD_TIMEZONE
from core.enums import ClaimMode
from core.models import GuildConfigRecord, TicketCategoryConfig
from db.connection import DatabaseManager
from services.guild_config_service import GuildConfigService
from services.validation_service import GuildLike, ValidationService


@dataclass(frozen=True, slots=True)
class SetupResult:
    config: GuildConfigRecord
    categories: list[TicketCategoryConfig]
    created_default_categories: bool


class SetupService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        guild_config_service: GuildConfigService | None = None,
        validation_service: ValidationService | None = None,
    ) -> None:
        self.database = database
        self.guild_config_service = guild_config_service or GuildConfigService(database)
        self.validation_service = validation_service or ValidationService(database)

    def setup_guild(
        self,
        guild: GuildLike,
        *,
        log_channel_id: int,
        archive_channel_id: int,
        ticket_category_channel_id: int,
        admin_role_id: int,
        claim_mode: ClaimMode = ClaimMode.RELAXED,
        max_open_tickets: int = 100,
        timezone: str = DEFAULT_GUILD_TIMEZONE,
        enable_download_window: bool = True,
    ) -> SetupResult:
        self.validation_service.validate_setup_targets(
            guild,
            log_channel_id=log_channel_id,
            archive_channel_id=archive_channel_id,
            ticket_category_channel_id=ticket_category_channel_id,
            admin_role_id=admin_role_id,
        )

        with self.database.session() as connection:
            existing_categories = self.guild_config_service.list_categories(
                guild.id,
                connection=connection,
            )
            if existing_categories:
                categories = existing_categories
                created_default_categories = False
            else:
                categories = self.guild_config_service.ensure_default_categories(
                    guild.id,
                    connection=connection,
                )
                created_default_categories = True

            config = self.guild_config_service.upsert_config(
                GuildConfigRecord(
                    guild_id=guild.id,
                    is_initialized=True,
                    log_channel_id=log_channel_id,
                    archive_channel_id=archive_channel_id,
                    ticket_category_channel_id=ticket_category_channel_id,
                    admin_role_id=admin_role_id,
                    claim_mode=claim_mode,
                    max_open_tickets=max_open_tickets,
                    timezone=timezone or DEFAULT_GUILD_TIMEZONE,
                    enable_download_window=enable_download_window,
                    updated_at="",
                ),
                connection=connection,
            )

        return SetupResult(
            config=config,
            categories=categories,
            created_default_categories=created_default_categories,
        )
