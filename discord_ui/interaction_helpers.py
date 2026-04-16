from __future__ import annotations

from typing import Any

import discord


async def safe_defer(interaction: discord.Interaction) -> None:
    response = getattr(interaction, "response", None)
    if response is None or response.is_done():
        return
    await response.defer(ephemeral=True)


async def send_ephemeral_text(interaction: discord.Interaction, content: str) -> None:
    await send_ephemeral_message(interaction, content=content)


async def send_ephemeral_message(
    interaction: discord.Interaction,
    *,
    content: str | None = None,
    embed: Any | None = None,
    view: discord.ui.View | None = None,
) -> None:
    response = getattr(interaction, "response", None)
    if response is None:
        raise RuntimeError("Interaction response 不可用。")

    kwargs: dict[str, Any] = {"ephemeral": True}
    if content is not None:
        kwargs["content"] = content
    if embed is not None:
        kwargs["embed"] = embed
    if view is not None:
        kwargs["view"] = view

    if response.is_done():
        await interaction.followup.send(**kwargs)
        return

    await response.send_message(**kwargs)
