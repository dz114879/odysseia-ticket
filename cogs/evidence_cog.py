from __future__ import annotations

import io
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from cogs.ticket_command_groups import notes_group, ticket_group
from core.errors import InvalidTicketStateError, PermissionDeniedError, TicketNotFoundError, ValidationError
from services.ticket_access_service import TicketAccessService


class EvidenceCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        resources = getattr(bot, "resources", None)
        if resources is None:
            raise RuntimeError("Bot resources 尚未初始化，无法加载 EvidenceCog。")

        self.bot = bot
        self.logging_service = resources.logging_service
        self.snapshot_query_service = resources.snapshot_query_service
        self.notes_service = resources.notes_service
        self.access_service = TicketAccessService(resources.database)

    async def cog_load(self) -> None:
        self.history_command.binding = self
        self.recycle_bin_command.binding = self
        self.notes_add_command.binding = self
        self.notes_check_command.binding = self

    @ticket_group.command(name="history", description="查看当前 ticket 中指定消息的快照时间线")
    @app_commands.guild_only()
    @app_commands.describe(message_id="要查询的 Discord 消息 ID")
    async def history_command(self, interaction: discord.Interaction, message_id: int) -> None:
        await self.show_message_history(interaction, message_id=message_id)

    @ticket_group.command(name="recycle-bin", description="导出当前 ticket 的已删除消息快照摘要")
    @app_commands.guild_only()
    async def recycle_bin_command(self, interaction: discord.Interaction) -> None:
        await self.show_recycle_bin(interaction)

    @notes_group.command(name="add", description="为当前 ticket 添加一条内部备注")
    @app_commands.guild_only()
    @app_commands.describe(content="备注内容")
    async def notes_add_command(self, interaction: discord.Interaction, content: str) -> None:
        await self.add_note(interaction, content=content)

    @notes_group.command(name="check", description="查看当前 ticket 的内部备注")
    @app_commands.guild_only()
    async def notes_check_command(self, interaction: discord.Interaction) -> None:
        await self.check_notes(interaction)

    async def show_message_history(self, interaction: discord.Interaction, *, message_id: int) -> None:
        try:
            channel = self._require_ticket_channel(interaction)
            context = self.access_service.load_snapshot_context(channel.id)
            self.access_service.assert_can_view_snapshots(
                interaction.user,
                context=context,
                is_bot_owner=await self.bot.is_owner(interaction.user),
            )
            await self._defer_ephemeral(interaction)
            rendered = self.snapshot_query_service.format_message_timeline(
                context.ticket.ticket_id,
                message_id,
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
            "Ticket snapshot history requested. ticket_id=%s message_id=%s user_id=%s",
            context.ticket.ticket_id,
            message_id,
            interaction.user.id,
        )
        await self._send_text_payload(
            interaction,
            content=rendered,
            filename=f"{context.ticket.ticket_id}-message-{message_id}-history.txt",
        )

    async def show_recycle_bin(self, interaction: discord.Interaction) -> None:
        try:
            channel = self._require_ticket_channel(interaction)
            context = self.access_service.load_snapshot_context(channel.id)
            self.access_service.assert_can_view_snapshots(
                interaction.user,
                context=context,
                is_bot_owner=await self.bot.is_owner(interaction.user),
            )
            await self._defer_ephemeral(interaction)
            rendered = self.snapshot_query_service.build_recycle_bin_text(context.ticket.ticket_id)
        except (
            TicketNotFoundError,
            InvalidTicketStateError,
            PermissionDeniedError,
            ValidationError,
        ) as exc:
            await self._send_ephemeral(interaction, str(exc))
            return

        self.logging_service.log_local_info(
            "Ticket recycle bin requested. ticket_id=%s user_id=%s",
            context.ticket.ticket_id,
            interaction.user.id,
        )
        await self._send_text_payload(
            interaction,
            content=rendered,
            filename=f"{context.ticket.ticket_id}-recycle-bin.txt",
            prefer_file=True,
        )

    async def add_note(self, interaction: discord.Interaction, *, content: str) -> None:
        try:
            channel = self._require_ticket_channel(interaction)
            context = self.access_service.load_snapshot_context(channel.id)
            self.access_service.assert_can_manage_notes(
                interaction.user,
                context=context,
                is_bot_owner=await self.bot.is_owner(interaction.user),
            )
            await self._defer_ephemeral(interaction)
            result = await self.notes_service.add_note(
                context.ticket,
                actor=interaction.user,
                content=content,
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
            "Ticket note added. ticket_id=%s author_id=%s note_count=%s",
            result.ticket.ticket_id,
            interaction.user.id,
            result.note_count,
        )
        await self._send_ephemeral(
            interaction,
            f"已为 ticket `{result.ticket.ticket_id}` 新增内部备注（当前共 {result.note_count} 条）。",
        )

    async def check_notes(self, interaction: discord.Interaction) -> None:
        try:
            channel = self._require_ticket_channel(interaction)
            context = self.access_service.load_snapshot_context(channel.id)
            self.access_service.assert_can_manage_notes(
                interaction.user,
                context=context,
                is_bot_owner=await self.bot.is_owner(interaction.user),
            )
            await self._defer_ephemeral(interaction)
            rendered = self.notes_service.format_notes(context.ticket)
        except (
            TicketNotFoundError,
            InvalidTicketStateError,
            PermissionDeniedError,
            ValidationError,
        ) as exc:
            await self._send_ephemeral(interaction, str(exc))
            return

        self.logging_service.log_local_info(
            "Ticket notes checked. ticket_id=%s user_id=%s",
            context.ticket.ticket_id,
            interaction.user.id,
        )
        await self._send_text_payload(
            interaction,
            content=rendered,
            filename=f"{context.ticket.ticket_id}-notes.txt",
        )

    @staticmethod
    def _require_ticket_channel(interaction: discord.Interaction) -> Any:
        if interaction.guild is None:
            raise ValidationError("该命令只能在服务器中使用。")
        channel = interaction.channel
        if channel is None:
            raise ValidationError("无法识别当前 ticket 频道。")
        return channel

    @staticmethod
    async def _defer_ephemeral(interaction: discord.Interaction) -> None:
        if interaction.response.is_done():
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

    @staticmethod
    async def _send_ephemeral(interaction: discord.Interaction, content: str) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
            return
        await interaction.response.send_message(content, ephemeral=True)

    @staticmethod
    async def _send_text_payload(
        interaction: discord.Interaction,
        *,
        content: str,
        filename: str,
        prefer_file: bool = False,
    ) -> None:
        if not prefer_file and len(content) <= 1800:
            await interaction.followup.send(content, ephemeral=True)
            return

        file = discord.File(io.BytesIO(content.encode("utf-8")), filename=filename)
        await interaction.followup.send(
            "内容较长，已附带文本文件。",
            ephemeral=True,
            file=file,
        )


async def setup(bot: commands.Bot) -> None:
    try:
        bot.tree.add_command(ticket_group)
    except app_commands.CommandAlreadyRegistered:
        pass
    await bot.add_cog(EvidenceCog(bot))
