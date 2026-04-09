from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from core.constants import CUSTOM_ID_SEPARATOR, PANEL_CUSTOM_ID_PREFIX
from core.errors import StaleInteractionError, ValidationError
from core.models import TicketCategoryConfig
from discord_ui.panel_embeds import build_panel_request_preview_embed

if TYPE_CHECKING:
    from services.panel_service import PanelSelectionPreview, PanelService


def build_public_panel_custom_id(guild_id: int, nonce: str) -> str:
    return (
        f"{PANEL_CUSTOM_ID_PREFIX}"
        f"{CUSTOM_ID_SEPARATOR}create"
        f"{CUSTOM_ID_SEPARATOR}{guild_id}"
        f"{CUSTOM_ID_SEPARATOR}{nonce}"
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
            ephemeral=True,
        )


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
