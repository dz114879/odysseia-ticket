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
from discord_ui.close_feedback import (
    build_close_feedback_message,
    build_close_request_feedback_message,
    build_revoke_close_feedback_message,
)
from discord_ui.interaction_helpers import safe_defer, send_ephemeral_text
from services.close_request_service import CloseRequestService
from services.close_service import CloseService


class CloseCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        resources = getattr(bot, "resources", None)
        if resources is None:
            raise RuntimeError("Bot resources 尚未初始化，无法加载 CloseCog。")

        self.bot = bot
        self.logging_service = resources.logging_service
        self.close_service = getattr(resources, "close_service", None) or CloseService(
            resources.database,
            bot=bot,
            lock_manager=getattr(resources, "lock_manager", None),
        )
        self.close_request_service = CloseRequestService(
            resources.database,
            close_service=self.close_service,
        )

    async def cog_load(self) -> None:
        self.close_command.binding = self
        self.close_cancel_command.binding = self

    @ticket_group.command(name="close", description="关闭当前 ticket；staff 会直接进入 closing，创建者会发起关闭请求")
    @app_commands.guild_only()
    @app_commands.describe(reason="关闭或请求关闭的原因（可选）")
    async def close_command(
        self,
        interaction: discord.Interaction,
        reason: str | None = None,
    ) -> None:
        await self.close_current_ticket(interaction, reason=reason)

    @ticket_group.command(name="close-cancel", description="撤销当前 closing ticket 的关闭流程")
    @app_commands.guild_only()
    async def close_cancel_command(self, interaction: discord.Interaction) -> None:
        await self.revoke_current_ticket_close(interaction)

    async def close_current_ticket(
        self,
        interaction: discord.Interaction,
        *,
        reason: str | None = None,
    ) -> None:
        try:
            channel = self._require_ticket_channel(interaction)
            await safe_defer(interaction)
            try:
                result = await self.close_service.initiate_close(
                    channel,
                    actor=interaction.user,
                    reason=reason,
                    is_bot_owner=await self.bot.is_owner(interaction.user),
                )
            except PermissionDeniedError:
                request_result = await self.close_request_service.request_close(
                    channel,
                    actor=interaction.user,
                    reason=reason,
                )
            else:
                await self.close_request_service.dismiss_pending_request(
                    channel,
                    handled_by_id=getattr(interaction.user, "id", 0),
                )
                self.logging_service.log_local_info(
                    "Ticket close initiated. ticket_id=%s previous_status=%s changed=%s",
                    result.ticket.ticket_id,
                    result.previous_status.value,
                    result.changed,
                )
                await send_ephemeral_text(interaction, build_close_feedback_message(result))
                return
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
            "Ticket close request created. ticket_id=%s requester_id=%s replaced_message_id=%s",
            request_result.ticket.ticket_id,
            request_result.requested_by_id,
            request_result.replaced_message_id,
        )
        await send_ephemeral_text(interaction, build_close_request_feedback_message(request_result))

    async def revoke_current_ticket_close(self, interaction: discord.Interaction) -> None:
        try:
            channel = self._require_ticket_channel(interaction)
            await safe_defer(interaction)
            result = await self.close_service.revoke_close(
                channel,
                actor=interaction.user,
                is_bot_owner=await self.bot.is_owner(interaction.user),
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
            "Ticket close revoked. ticket_id=%s restored_status=%s",
            result.ticket.ticket_id,
            result.restored_status.value,
        )
        await send_ephemeral_text(interaction, build_revoke_close_feedback_message(result))

    @staticmethod
    def _require_ticket_channel(interaction: discord.Interaction) -> Any:
        if interaction.guild is None:
            raise ValidationError("该命令只能在服务器中使用。")
        channel = interaction.channel
        if channel is None or getattr(channel, "guild", None) is None:
            raise ValidationError("当前频道不支持 ticket close 操作。")
        return channel

async def setup(bot: commands.Bot) -> None:
    try:
        bot.tree.add_command(ticket_group)
    except app_commands.CommandAlreadyRegistered:
        pass
    await bot.add_cog(CloseCog(bot))
