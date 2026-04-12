from __future__ import annotations

from typing import Any

import discord

from core.errors import (
    InvalidTicketStateError,
    PermissionDeniedError,
    StaleInteractionError,
    TicketNotFoundError,
    ValidationError,
)
from discord_ui.close_feedback import build_close_feedback_message, build_revoke_close_feedback_message


class ClosingNoticeView(discord.ui.View):
    def __init__(
        self,
        *,
        close_service,
        ticket_id: str,
        timeout: float,
    ) -> None:
        super().__init__(timeout=timeout)
        self.close_service = close_service
        self.ticket_id = ticket_id
        self.message: Any | None = None

    def bind_message(self, message: Any) -> None:
        self.message = message

    async def on_timeout(self) -> None:
        self.close_service._closing_notice_messages.pop(self.ticket_id, None)
        if self.message is None:
            return
        try:
            await self.message.edit(view=None)
        except Exception:
            pass

    @discord.ui.button(label="撤销关闭", style=discord.ButtonStyle.secondary, row=0)
    async def revoke_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        del button
        try:
            channel = _require_channel(interaction)
            await _defer_ephemeral(interaction)
            result = await self.close_service.revoke_close(
                channel,
                actor=interaction.user,
                is_bot_owner=await interaction.client.is_owner(interaction.user),
            )
        except (
            TicketNotFoundError,
            InvalidTicketStateError,
            StaleInteractionError,
            PermissionDeniedError,
            ValidationError,
            discord.HTTPException,
        ) as exc:
            await _send_ephemeral(interaction, str(exc))
            return

        await _send_ephemeral(interaction, build_revoke_close_feedback_message(result))


class CloseRequestView(discord.ui.View):
    def __init__(
        self,
        *,
        service,
        requester_id: int,
        request_reason: str | None,
        channel_id: int,
        timeout: float,
    ) -> None:
        super().__init__(timeout=timeout)
        self.service = service
        self.requester_id = requester_id
        self.request_reason = request_reason
        self.channel_id = channel_id
        self.message: Any | None = None

    def bind_message(self, message: Any) -> None:
        self.message = message

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        await self.service.expire_request_message(
            channel_id=self.channel_id,
            message=self.message,
            requester_id=self.requester_id,
            reason=self.request_reason,
        )

    @discord.ui.button(label="同意关闭", style=discord.ButtonStyle.danger, row=0)
    async def approve_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        del button
        try:
            channel = _require_channel(interaction)
            await _defer_ephemeral(interaction)
            result = await self.service.approve_request(
                channel,
                actor=interaction.user,
                request_message=interaction.message,
                requester_id=self.requester_id,
                reason=self.request_reason,
                is_bot_owner=await interaction.client.is_owner(interaction.user),
            )
        except (
            TicketNotFoundError,
            InvalidTicketStateError,
            StaleInteractionError,
            PermissionDeniedError,
            ValidationError,
            discord.HTTPException,
        ) as exc:
            await _send_ephemeral(interaction, str(exc))
            return

        await _send_ephemeral(interaction, build_close_feedback_message(result))

    @discord.ui.button(label="拒绝请求", style=discord.ButtonStyle.secondary, row=0)
    async def reject_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        del button
        try:
            channel = _require_channel(interaction)
            await _defer_ephemeral(interaction)
            await self.service.reject_request(
                channel,
                actor=interaction.user,
                request_message=interaction.message,
                requester_id=self.requester_id,
                reason=self.request_reason,
                is_bot_owner=await interaction.client.is_owner(interaction.user),
            )
        except (
            TicketNotFoundError,
            InvalidTicketStateError,
            StaleInteractionError,
            PermissionDeniedError,
            ValidationError,
            discord.HTTPException,
        ) as exc:
            await _send_ephemeral(interaction, str(exc))
            return

        await _send_ephemeral(interaction, "已拒绝该关闭请求，并在频道内发送公开说明。")


def _require_channel(interaction: discord.Interaction) -> Any:
    if interaction.guild is None:
        raise ValidationError("该交互只能在服务器中使用。")
    channel = interaction.channel
    if channel is None or getattr(channel, "guild", None) is None:
        raise ValidationError("当前频道不支持 ticket close request 操作。")
    return channel


async def _send_ephemeral(interaction: discord.Interaction, content: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=True)
        return
    await interaction.response.send_message(content, ephemeral=True)


async def _defer_ephemeral(interaction: discord.Interaction) -> None:
    if interaction.response.is_done():
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
