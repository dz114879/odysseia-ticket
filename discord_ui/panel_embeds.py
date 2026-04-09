from __future__ import annotations

import discord

from config.defaults import (
    DEFAULT_PANEL_BULLET_POINTS,
    DEFAULT_PANEL_CAPACITY_TEXT,
    DEFAULT_PANEL_DESCRIPTION,
    DEFAULT_PANEL_FOOTER_TEXT,
    DEFAULT_PANEL_TITLE,
)
from core.models import TicketCategoryConfig


def build_public_panel_embed(categories: list[TicketCategoryConfig]) -> discord.Embed:
    embed = discord.Embed(
        title=DEFAULT_PANEL_TITLE,
        description=DEFAULT_PANEL_DESCRIPTION,
        color=discord.Color.blurple(),
    )
    embed.add_field(name="支持事项", value=DEFAULT_PANEL_BULLET_POINTS, inline=False)
    embed.add_field(
        name="可选分类",
        value=_format_category_lines(categories),
        inline=False,
    )
    embed.add_field(name="容量状态", value=DEFAULT_PANEL_CAPACITY_TEXT, inline=False)
    embed.set_footer(text=DEFAULT_PANEL_FOOTER_TEXT)
    return embed


def build_panel_request_preview_embed(category: TicketCategoryConfig) -> discord.Embed:
    embed = discord.Embed(
        title=f"已选择分类：{category.display_name}",
        description=(
            "当前请求已通过入口预校验。\n"
            "请点击下方按钮创建一个仅您与机器人可见的私密 draft ticket 频道。"
        ),
        color=discord.Color.green(),
    )
    if category.description:
        embed.add_field(name="分类说明", value=category.description, inline=False)
    if category.extra_welcome_text:
        embed.add_field(name="提交前提示", value=category.extra_welcome_text, inline=False)
    embed.add_field(name="下一步", value="确认创建后，请在 draft 频道发送第一条消息。", inline=False)
    return embed


def _format_category_lines(categories: list[TicketCategoryConfig]) -> str:
    if not categories:
        return "当前没有可用分类。"

    lines = []
    for category in categories[:10]:
        prefix = f"{category.emoji} " if category.emoji else ""
        description = category.description or "暂无描述"
        lines.append(f"{prefix}**{category.display_name}**：{description}")
    return "\n".join(lines)
