from __future__ import annotations

from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from cogs.ticket_command_groups import draft_group, ticket_group
from core.errors import (
    InvalidTicketStateError,
    PermissionDeniedError,
    TicketNotFoundError,
    ValidationError,
)
from discord_ui.draft_views import DraftAbandonConfirmView
from discord_ui.interaction_helpers import safe_defer, send_ephemeral_message, send_ephemeral_text
from services.draft_service import DraftRenameResult, DraftService


class DraftCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        resources = getattr(bot, "resources", None)
        if resources is None:
            raise RuntimeError("Bot resources 尚未初始化，无法加载 DraftCog。")

        self.bot = bot
        self.logging_service = resources.logging_service
        self.draft_service = DraftService(
            resources.database,
            lock_manager=getattr(resources, "lock_manager", None),
        )

    async def cog_load(self) -> None:
        self.rename_draft_command.binding = self
        self.abandon_draft_command.binding = self

    @draft_group.command(name="rename", description="修改当前 draft ticket 的频道标题")
    @app_commands.guild_only()
    @app_commands.describe(title="新的 draft 标题")
    async def rename_draft_command(
        self,
        interaction: discord.Interaction,
        title: str,
    ) -> None:
        await self.rename_current_draft(interaction, title=title)

    @draft_group.command(name="abandon", description="废弃当前 draft ticket 并删除频道")
    @app_commands.guild_only()
    async def abandon_draft_command(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await self.abandon_current_draft(interaction)

    async def rename_current_draft(
        self,
        interaction: discord.Interaction,
        *,
        title: str,
    ) -> None:
        try:
            channel = self._require_editable_channel(interaction)
            await safe_defer(interaction)
            result = await self.draft_service.rename_draft_ticket(
                channel,
                actor_id=interaction.user.id,
                requested_name=title,
            )
        except (
            TicketNotFoundError,
            InvalidTicketStateError,
            PermissionDeniedError,
            ValidationError,
            discord.HTTPException,
        ) as exc:
            await send_ephemeral_text(interaction, str(exc))
            return

        self.logging_service.log_local_info(
            "Draft renamed. ticket_id=%s old_name=%s new_name=%s changed=%s",
            result.ticket.ticket_id,
            result.old_name,
            result.new_name,
            result.changed,
        )
        await send_ephemeral_text(interaction, self._build_rename_success_message(result))

    async def abandon_current_draft(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if interaction.guild is None or interaction.channel is None:
            await send_ephemeral_text(interaction, "该命令只能在服务器频道中使用。")
            return

        view = DraftAbandonConfirmView()
        await send_ephemeral_message(
            interaction,
            content="⚠️ 警告：此操作会废弃当前 draft ticket ，并永久删除当前频道，无法撤销。\n\n 请确认。",
            view=view,
        )

    @staticmethod
    def _require_editable_channel(interaction: discord.Interaction) -> Any:
        if interaction.guild is None:
            raise ValidationError("该命令只能在服务器中使用。")
        channel = interaction.channel
        if channel is None or not hasattr(channel, "edit"):
            raise ValidationError("当前频道不支持 draft rename。")
        return channel

    @staticmethod
    def _build_rename_success_message(result: DraftRenameResult) -> str:
        if not result.changed:
            return f"draft 标题未变化。\n- Ticket ID：`{result.ticket.ticket_id}`\n- 当前频道名：`{result.new_name}`"
        return f"draft 标题已更新。\n- Ticket ID：`{result.ticket.ticket_id}`\n- 旧频道名：`{result.old_name}`\n- 新频道名：`{result.new_name}`"

async def setup(bot: commands.Bot) -> None:
    try:
        bot.tree.add_command(ticket_group)
    except app_commands.CommandAlreadyRegistered:
        pass
    await bot.add_cog(DraftCog(bot))
