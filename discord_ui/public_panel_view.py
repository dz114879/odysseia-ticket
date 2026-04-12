from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from core.constants import (
    CUSTOM_ID_SEPARATOR,
    PANEL_CUSTOM_ID_PREFIX,
    TICKET_CUSTOM_ID_PREFIX,
)
from core.errors import StaleInteractionError, ValidationError
from core.models import TicketCategoryConfig
from discord_ui.panel_embeds import build_panel_request_preview_embed

if TYPE_CHECKING:
    from services.creation_service import DraftCreationResult
    from services.panel_service import PanelService

_logger = logging.getLogger(__name__)


def build_public_panel_custom_id(guild_id: int, nonce: str) -> str:
    return f"{PANEL_CUSTOM_ID_PREFIX}{CUSTOM_ID_SEPARATOR}create{CUSTOM_ID_SEPARATOR}{guild_id}{CUSTOM_ID_SEPARATOR}{nonce}"


def build_draft_confirm_custom_id(guild_id: int, nonce: str, category_key: str) -> str:
    return (
        f"{TICKET_CUSTOM_ID_PREFIX}"
        f"{CUSTOM_ID_SEPARATOR}draft-confirm"
        f"{CUSTOM_ID_SEPARATOR}{guild_id}"
        f"{CUSTOM_ID_SEPARATOR}{nonce}"
        f"{CUSTOM_ID_SEPARATOR}{category_key}"
    )


class DraftCreateConfirmButton(discord.ui.Button):
    def __init__(
        self,
        *,
        guild_id: int,
        nonce: str,
        category: TicketCategoryConfig,
        source_message_id: int,
        panel_service: PanelService | None,
    ) -> None:
        super().__init__(
            label="确认创建私密 Ticket",
            style=discord.ButtonStyle.green,
            custom_id=build_draft_confirm_custom_id(guild_id, nonce, category.category_key),
        )
        self.guild_id = guild_id
        self.nonce = nonce
        self.category = category
        self.source_message_id = source_message_id
        self.panel_service = panel_service

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.panel_service is None:
            await interaction.response.send_message("Ticket 创建流程尚未完成接线。", ephemeral=True)
            return

        if interaction.guild is None:
            await interaction.response.send_message("该交互只能在服务器中使用。", ephemeral=True)
            return

        try:
            result = await self.panel_service.create_draft_from_panel_request(
                guild=interaction.guild,
                creator=interaction.user,
                message_id=self.source_message_id,
                nonce=self.nonce,
                category_key=self.category.category_key,
            )
        except (StaleInteractionError, ValidationError) as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        except Exception as exc:
            _logger.exception(
                "Draft creation failed silently. guild_id=%s user_id=%s category=%s",
                self.guild_id,
                interaction.user.id,
                self.category.category_key,
            )
            if self.panel_service is not None and getattr(self.panel_service, "logging_service", None) is not None:
                guild_repo = getattr(self.panel_service, "guild_repository", None)
                config = guild_repo.get_config(self.guild_id) if guild_repo else None
                await self.panel_service.logging_service.send_guild_log(
                    self.guild_id,
                    "error",
                    "草稿工单创建失败",
                    f"用户 <@{interaction.user.id}> 创建 draft ticket 失败：{exc}",
                    channel_id=getattr(config, "log_channel_id", None) if config else None,
                    extra={"category": self.category.category_key, "error_type": type(exc).__name__},
                )
            await interaction.response.send_message(
                "创建草稿工单失败，如问题持续出现，请联系开发者。",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            _build_draft_creation_feedback(result),
            ephemeral=True,
        )


class DraftCreateConfirmView(discord.ui.View):
    def __init__(
        self,
        *,
        guild_id: int,
        nonce: str,
        category: TicketCategoryConfig,
        source_message_id: int,
        panel_service: PanelService | None,
    ) -> None:
        super().__init__(timeout=300)
        self.add_item(
            DraftCreateConfirmButton(
                guild_id=guild_id,
                nonce=nonce,
                category=category,
                source_message_id=source_message_id,
                panel_service=panel_service,
            )
        )


class PanelCategorySelect(discord.ui.Select):
    def __init__(
        self,
        *,
        guild_id: int,
        nonce: str,
        categories: list[TicketCategoryConfig],
        panel_service: PanelService | None,
    ) -> None:
        options = [
            discord.SelectOption(
                label=category.display_name[:100],
                value=category.category_key,
                description=(category.description or "请选择该分类继续")[:100],
                emoji=category.emoji,
            )
            for category in categories[:25]
        ]
        super().__init__(
            placeholder="请选择您需要的 Ticket 分类",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=build_public_panel_custom_id(guild_id, nonce),
        )
        self.guild_id = guild_id
        self.nonce = nonce
        self.panel_service = panel_service

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.panel_service is None:
            await interaction.response.send_message(
                "Ticket 面板交互尚未完成接线。",
                ephemeral=True,
            )
            return

        if interaction.message is None:
            await interaction.response.send_message(
                "无法识别当前面板消息，请稍后重试。",
                ephemeral=True,
            )
            return

        try:
            preview = self.panel_service.preview_panel_request(
                guild_id=self.guild_id,
                message_id=interaction.message.id,
                nonce=self.nonce,
                category_key=self.values[0],
            )
        except (StaleInteractionError, ValidationError) as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await interaction.response.send_message(
            embed=build_panel_request_preview_embed(preview.category),
            view=DraftCreateConfirmView(
                guild_id=self.guild_id,
                nonce=self.nonce,
                category=preview.category,
                source_message_id=interaction.message.id,
                panel_service=self.panel_service,
            ),
            ephemeral=True,
        )


def _build_draft_creation_feedback(result: DraftCreationResult) -> str:
    channel = result.channel
    channel_reference = getattr(channel, "mention", f"<#{getattr(channel, 'id', 'unknown')}>")

    if result.created:
        return (
            "已为您创建私密 draft ticket。\n"
            f"- 频道：{channel_reference}\n"
            f"- Ticket ID：`{result.ticket.ticket_id}`\n"
            "请进入频道发送第一条消息描述问题。"
        )

    return f"您已有进行中的 draft ticket。\n- 频道：{channel_reference}\n- Ticket ID：`{result.ticket.ticket_id}`"


class PublicPanelView(discord.ui.View):
    def __init__(
        self,
        *,
        guild_id: int,
        nonce: str,
        categories: list[TicketCategoryConfig],
        panel_service: PanelService | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self.add_item(
            PanelCategorySelect(
                guild_id=guild_id,
                nonce=nonce,
                categories=categories,
                panel_service=panel_service,
            )
        )
