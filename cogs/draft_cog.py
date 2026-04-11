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
from services.draft_service import DraftAbandonResult, DraftRenameResult, DraftService


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
    @app_commands.describe(confirm="请设为 true 以确认废弃并删除当前频道")
    async def abandon_draft_command(
        self,
        interaction: discord.Interaction,
        confirm: bool = False,
    ) -> None:
        await self.abandon_current_draft(interaction, confirm=confirm)

    async def rename_current_draft(
        self,
        interaction: discord.Interaction,
        *,
        title: str,
    ) -> None:
        try:
            channel = self._require_editable_channel(interaction)
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
        ) as exc:
            await self._send_ephemeral(interaction, str(exc))
            return

        self.logging_service.log_local_info(
            "Draft renamed. ticket_id=%s old_name=%s new_name=%s changed=%s",
            result.ticket.ticket_id,
            result.old_name,
            result.new_name,
            result.changed,
        )
        await self._send_ephemeral(interaction, self._build_rename_success_message(result))

    async def abandon_current_draft(
        self,
        interaction: discord.Interaction,
        *,
        confirm: bool,
    ) -> None:
        if not confirm:
            await self._send_ephemeral(
                interaction,
                "此操作会废弃当前 draft ticket 并删除频道；如确认执行，请将 confirm 设为 true。",
            )
            return

        try:
            channel = self._require_deletable_channel(interaction)
            result = await self.draft_service.abandon_draft_ticket(
                channel,
                actor_id=interaction.user.id,
            )
        except (
            TicketNotFoundError,
            InvalidTicketStateError,
            PermissionDeniedError,
            ValidationError,
        ) as exc:
            await self._send_ephemeral(interaction, str(exc))
            return

        self.logging_service.log_local_info(
            "Draft abandoned. ticket_id=%s channel_deleted=%s",
            result.ticket.ticket_id,
            result.channel_deleted,
        )
        await self._send_ephemeral(interaction, self._build_abandon_success_message(result))

    @staticmethod
    def _require_editable_channel(interaction: discord.Interaction) -> Any:
        if interaction.guild is None:
            raise ValidationError("该命令只能在服务器中使用。")
        channel = interaction.channel
        if channel is None or not hasattr(channel, "edit"):
            raise ValidationError("当前频道不支持 draft rename。")
        return channel

    @staticmethod
    def _require_deletable_channel(interaction: discord.Interaction) -> Any:
        if interaction.guild is None:
            raise ValidationError("该命令只能在服务器中使用。")
        channel = interaction.channel
        if channel is None or not hasattr(channel, "delete"):
            raise ValidationError("当前频道不支持 draft abandon。")
        return channel

    @staticmethod
    def _build_rename_success_message(result: DraftRenameResult) -> str:
        if not result.changed:
            return f"draft 标题未变化。\n- Ticket ID：`{result.ticket.ticket_id}`\n- 当前频道名：`{result.new_name}`"
        return f"draft 标题已更新。\n- Ticket ID：`{result.ticket.ticket_id}`\n- 旧频道名：`{result.old_name}`\n- 新频道名：`{result.new_name}`"

    @staticmethod
    def _build_abandon_success_message(result: DraftAbandonResult) -> str:
        deleted_text = "频道已删除。" if result.channel_deleted else "频道删除失败，请手动处理。"
        return f"draft ticket 已废弃。\n- Ticket ID：`{result.ticket.ticket_id}`\n- 结果：{deleted_text}"

    @staticmethod
    async def _send_ephemeral(interaction: discord.Interaction, content: str) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
            return
        await interaction.response.send_message(content, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    try:
        bot.tree.add_command(ticket_group)
    except app_commands.CommandAlreadyRegistered:
        pass
    await bot.add_cog(DraftCog(bot))
