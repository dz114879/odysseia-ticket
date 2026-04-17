from __future__ import annotations

from collections.abc import Awaitable, Callable
from inspect import isawaitable
from typing import Any, TypeVar

import discord

from core.errors import (
    InvalidTicketStateError,
    PermissionDeniedError,
    StaleInteractionError,
    TicketNotFoundError,
    ValidationError,
)
from discord_ui.interaction_helpers import safe_defer, send_ephemeral_text


StaffActionResultT = TypeVar("StaffActionResultT")
PrecheckHook = Callable[[discord.Interaction, Any], object]
SuccessHook = Callable[[StaffActionResultT], object]

STAFF_ACTION_EXCEPTIONS = (
    TicketNotFoundError,
    InvalidTicketStateError,
    StaleInteractionError,
    PermissionDeniedError,
    ValidationError,
    discord.HTTPException,
)


async def execute_staff_action(
    interaction: discord.Interaction,
    *,
    action: Callable[[Any, bool], Awaitable[StaffActionResultT]],
    context_label: str,
    build_success_message: Callable[[StaffActionResultT], str] | None = None,
    precheck: PrecheckHook | None = None,
    on_success: Callable[[StaffActionResultT], object] | None = None,
    defer: bool = True,
    resolve_owner: bool = True,
    owner_resolver: Callable[[discord.Interaction], Awaitable[bool]] | None = None,
) -> StaffActionResultT | None:
    try:
        channel = require_staff_ticket_channel(interaction, context_label=context_label)
        if precheck is not None:
            await _maybe_await(precheck(interaction, channel))
        if defer:
            await safe_defer(interaction)
        is_bot_owner = await _resolve_is_bot_owner(interaction, owner_resolver=owner_resolver) if resolve_owner else False
        result = await action(channel, is_bot_owner)
    except STAFF_ACTION_EXCEPTIONS as exc:
        await send_ephemeral_text(interaction, str(exc))
        return None

    if on_success is not None:
        await _maybe_await(on_success(result))
    if build_success_message is not None:
        await send_ephemeral_text(interaction, build_success_message(result))
    return result


def require_staff_ticket_channel(
    interaction: discord.Interaction,
    *,
    context_label: str,
) -> Any:
    if interaction.guild is None:
        raise ValidationError(f"该{context_label}只能在服务器中使用。")
    channel = interaction.channel
    if channel is None or getattr(channel, "guild", None) is None:
        raise ValidationError("当前频道不支持 staff ticket 操作。")
    return channel


def require_guild_context(interaction: discord.Interaction, *, context_label: str) -> Any:
    if interaction.guild is None:
        raise ValidationError(f"该{context_label}只能在服务器中使用。")
    return interaction.channel


def assert_current_staff_panel(interaction: discord.Interaction, channel: Any) -> None:
    message = getattr(interaction, "message", None)
    message_id = getattr(message, "id", None)
    if message_id is None:
        raise ValidationError("无法识别当前 staff 控制面板消息，请稍后重试。")

    service = _build_staff_panel_service(interaction)
    service.assert_current_panel_interaction(
        channel_id=getattr(channel, "id", 0),
        message_id=message_id,
    )


def require_bot_resources(interaction: discord.Interaction) -> Any:
    client = getattr(interaction, "client", None)
    resources = getattr(client, "resources", None)
    if resources is None:
        raise RuntimeError("Bot resources 尚未初始化，无法处理 staff 交互。")
    return resources


async def _maybe_await(value: object) -> None:
    if isawaitable(value):
        await value


async def _resolve_is_bot_owner(
    interaction: discord.Interaction,
    *,
    owner_resolver: Callable[[discord.Interaction], Awaitable[bool]] | None = None,
) -> bool:
    if owner_resolver is not None:
        return await owner_resolver(interaction)
    client = getattr(interaction, "client", None)
    is_owner = getattr(client, "is_owner", None)
    if not callable(is_owner):
        raise RuntimeError("Interaction client 尚未就绪，无法校验 Bot owner 权限。")
    return await is_owner(interaction.user)


def _build_staff_panel_service(interaction: discord.Interaction):
    from services.staff_panel_service import StaffPanelService

    resources = require_bot_resources(interaction)
    return StaffPanelService(resources.database, bot=interaction.client)
