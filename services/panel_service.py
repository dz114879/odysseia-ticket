from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from discord.ext import commands

from core.errors import ValidationError
from core.models import PanelRecord, TicketCategoryConfig
from db.connection import DatabaseManager
from db.repositories.base import utc_now_iso
from db.repositories.panel_repository import PanelRepository
from discord_ui.panel_embeds import build_public_panel_embed
from discord_ui.public_panel_view import PublicPanelView
from services.creation_service import CreationService, DraftCreationResult
from services.validation_service import ValidationService


@dataclass(frozen=True, slots=True)
class PanelPublishResult:
    record: PanelRecord
    message: Any


@dataclass(frozen=True, slots=True)
class PanelRemovalResult:
    record: PanelRecord
    message_deleted: bool


@dataclass(frozen=True, slots=True)
class PanelSelectionPreview:
    category: TicketCategoryConfig
    panel: PanelRecord


class PanelService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        bot: commands.Bot | None = None,
        panel_repository: PanelRepository | None = None,
        validation_service: ValidationService | None = None,
        creation_service: CreationService | None = None,
    ) -> None:
        self.database = database
        self.bot = bot
        self.panel_repository = panel_repository or PanelRepository(database)
        self.validation_service = validation_service or ValidationService(database)
        bot_resources = getattr(bot, "resources", None) if bot is not None else None
        lock_manager = getattr(bot_resources, "lock_manager", None)
        self.creation_service = creation_service or CreationService(
            database,
            validation_service=self.validation_service,
            lock_manager=lock_manager,
        )

    def get_active_panel(self, guild_id: int) -> PanelRecord | None:
        return self.panel_repository.get_active_panel(guild_id)

    def list_active_panels(self) -> list[PanelRecord]:
        return self.panel_repository.list_active_panels()

    def build_public_panel_view(
        self,
        *,
        guild_id: int,
        nonce: str,
        categories: list[TicketCategoryConfig],
    ) -> PublicPanelView:
        return PublicPanelView(
            guild_id=guild_id,
            nonce=nonce,
            categories=categories,
            panel_service=self,
        )

    def build_persistent_public_panel_view(self, panel: PanelRecord) -> PublicPanelView:
        _, categories = self.validation_service.assert_panel_creation_ready(panel.guild_id)
        return self.build_public_panel_view(guild_id=panel.guild_id, nonce=panel.nonce, categories=categories)

    def preview_panel_request(
        self,
        *,
        guild_id: int,
        message_id: int,
        nonce: str,
        category_key: str,
    ) -> PanelSelectionPreview:
        validation = self.validation_service.validate_panel_request(
            guild_id,
            message_id=message_id,
            nonce=nonce,
            category_key=category_key,
        )
        return PanelSelectionPreview(
            category=validation.category,
            panel=validation.panel,
        )

    async def create_draft_from_panel_request(
        self,
        *,
        guild: Any,
        creator: Any,
        message_id: int,
        nonce: str,
        category_key: str,
    ) -> DraftCreationResult:
        return await self.creation_service.create_draft_ticket(
            guild=guild,
            creator=creator,
            category_key=category_key,
            source_panel_message_id=message_id,
            source_panel_nonce=nonce,
        )

    async def create_panel(
        self,
        channel: Any,
        *,
        created_by: int,
    ) -> PanelPublishResult:
        guild_id = channel.guild.id
        _, categories = self.validation_service.assert_panel_creation_ready(guild_id)
        nonce = self.generate_panel_nonce()

        message = await channel.send(
            embed=build_public_panel_embed(categories),
            view=self.build_public_panel_view(guild_id=guild_id, nonce=nonce, categories=categories),
        )
        record = self.panel_repository.replace_active_panel(
            PanelRecord(
                panel_id=self.generate_panel_id(),
                guild_id=guild_id,
                channel_id=channel.id,
                message_id=message.id,
                nonce=nonce,
                is_active=True,
                created_by=created_by,
                created_at="",
                updated_at="",
            )
        )
        return PanelPublishResult(record=record, message=message)

    async def refresh_active_panel(self, guild_id: int) -> PanelPublishResult:
        active_panel = self._require_active_panel(guild_id)
        _, categories = self.validation_service.assert_panel_creation_ready(guild_id)
        message = await self._resolve_message(active_panel)
        refreshed_nonce = self.generate_panel_nonce()

        await message.edit(
            embed=build_public_panel_embed(categories),
            view=self.build_public_panel_view(
                guild_id=guild_id,
                nonce=refreshed_nonce,
                categories=categories,
            ),
        )
        refreshed_at = utc_now_iso()
        updated_record = self.panel_repository.update(
            active_panel.panel_id,
            nonce=refreshed_nonce,
            updated_at=refreshed_at,
        ) or PanelRecord(
            panel_id=active_panel.panel_id,
            guild_id=active_panel.guild_id,
            channel_id=active_panel.channel_id,
            message_id=active_panel.message_id,
            nonce=refreshed_nonce,
            is_active=active_panel.is_active,
            created_by=active_panel.created_by,
            created_at=active_panel.created_at,
            updated_at=refreshed_at,
        )
        return PanelPublishResult(record=updated_record, message=message)

    async def remove_active_panel(
        self,
        guild_id: int,
        *,
        delete_message: bool = False,
    ) -> PanelRemovalResult:
        active_panel = self._require_active_panel(guild_id)
        updated_record = self.panel_repository.update(
            active_panel.panel_id,
            is_active=False,
            updated_at=utc_now_iso(),
        ) or PanelRecord(
            panel_id=active_panel.panel_id,
            guild_id=active_panel.guild_id,
            channel_id=active_panel.channel_id,
            message_id=active_panel.message_id,
            nonce=active_panel.nonce,
            is_active=False,
            created_by=active_panel.created_by,
            created_at=active_panel.created_at,
            updated_at=active_panel.updated_at,
        )

        message_deleted = False
        if delete_message:
            try:
                message = await self._resolve_message(active_panel)
            except ValidationError:
                message = None
            if message is not None:
                try:
                    await message.delete()
                    message_deleted = True
                except Exception:
                    message_deleted = False

        return PanelRemovalResult(record=updated_record, message_deleted=message_deleted)

    def _require_active_panel(self, guild_id: int) -> PanelRecord:
        active_panel = self.panel_repository.get_active_panel(guild_id)
        if active_panel is None:
            raise ValidationError("当前服务器还没有 active panel，请先创建面板。")
        return active_panel

    async def _resolve_message(self, panel: PanelRecord) -> Any:
        channel = await self._resolve_channel(panel.channel_id)
        try:
            return await channel.fetch_message(panel.message_id)
        except Exception as exc:
            raise ValidationError("当前 active panel 消息不存在，请改用 /ticket panel create 重新发送。") from exc

    async def _resolve_channel(self, channel_id: int) -> Any:
        if self.bot is None:
            raise ValidationError("PanelService 未绑定 bot，无法定位已发布的面板消息。")

        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            return channel

        try:
            return await self.bot.fetch_channel(channel_id)
        except Exception as exc:
            raise ValidationError("无法定位当前 active panel 所在频道。") from exc

    @staticmethod
    def generate_panel_id() -> str:
        return secrets.token_hex(12)

    @staticmethod
    def generate_panel_nonce() -> str:
        return secrets.token_urlsafe(9)
