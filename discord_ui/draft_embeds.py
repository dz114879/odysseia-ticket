from __future__ import annotations

import discord

from config.defaults import build_default_draft_welcome_text


def build_draft_welcome_embed(
    *,
    category_name: str,
    inactive_close_hours: int = 6,
    abandon_timeout_hours: int = 24,
    custom_welcome_text: str | None = None,
) -> discord.Embed:
    if custom_welcome_text:
        description = custom_welcome_text
    else:
        description = build_default_draft_welcome_text(
            inactive_close_hours=inactive_close_hours,
            abandon_timeout_hours=abandon_timeout_hours,
        )
    return discord.Embed(
        title=f"📋 已创建 {category_name} Ticket",
        description=description,
        color=discord.Color.blue(),
    )
