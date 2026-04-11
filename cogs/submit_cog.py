from __future__ import annotations

from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from cogs.ticket_command_groups import ticket_group
from core.errors import (
    InvalidTicketStateError,
    PermissionDeniedError,
    TicketNotFoundError,
    ValidationError,
)
from discord_ui.draft_views import DraftSubmitTitleModal, DraftWelcomeView, build_submit_feedback_message
from services.submission_guard_service import SubmissionGuardService
from services.submit_service import SubmitService


class SubmitCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        resources = getattr(bot, "resources", None)
        if resources is None:
            raise RuntimeError("Bot resources 尚未初始化，无法加载 SubmitCog。")

        self.bot = bot
        self.logging_service = resources.logging_service
        self.guard_service = SubmissionGuardService(resources.database)
        self.submit_service = SubmitService(
            resources.database,
            lock_manager=getattr(resources, "lock_manager", None),
            snapshot_service=getattr(resources, "snapshot_service", None),
            capacity_service=getattr(resources, "capacity_service", None),
            queue_service=getattr(resources, "queue_service", None),
        )

        if not getattr(bot, "_draft_welcome_view_registered", False):
            bot.add_view(DraftWelcomeView())
            bot._draft_welcome_view_registered = True

    async def cog_load(self) -> None:
        self.submit_command.binding = self

    @ticket_group.command(name="submit", description="提交当前 draft ticket 给 staff 处理")
    @app_commands.guild_only()
    async def submit_command(self, interaction: discord.Interaction) -> None:
        await self.submit_current_draft(interaction)

    async def submit_current_draft(self, interaction: discord.Interaction) -> None:
        try:
            channel = self._require_channel(interaction)
            context = self.guard_service.inspect_submission(
                channel_id=channel.id,
                actor_id=interaction.user.id,
                channel_name=getattr(channel, "name", None),
            )
            if context.requires_title and not context.already_submitted:
                await interaction.response.send_modal(DraftSubmitTitleModal())
                return

            await self._defer_ephemeral(interaction)
            result = await self.submit_service.submit_draft_ticket(
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
            "Draft submit handled. ticket_id=%s outcome=%s renamed=%s",
            result.ticket.ticket_id,
            result.outcome,
            result.channel_name_changed,
        )
        await self._send_ephemeral(interaction, build_submit_feedback_message(result))

    @staticmethod
    def _require_channel(interaction: discord.Interaction) -> Any:
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


async def setup(bot: commands.Bot) -> None:
    try:
        bot.tree.add_command(ticket_group)
    except app_commands.CommandAlreadyRegistered:
        pass
    await bot.add_cog(SubmitCog(bot))
