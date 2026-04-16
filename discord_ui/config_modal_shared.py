from __future__ import annotations

from typing import Any

import discord

from config.defaults import (
    build_default_draft_welcome_text,
    build_default_snapshot_limit_text,
    build_default_snapshot_warning_text,
    build_public_panel_body,
)
from core.models import GuildConfigRecord
from db.repositories.guild_repository import GuildRepository
from discord_ui.interaction_helpers import safe_defer, send_ephemeral_text


def require_resources(interaction: discord.Interaction) -> Any:
    client = getattr(interaction, "client", None)
    resources = getattr(client, "resources", None)
    if resources is None:
        raise RuntimeError("Bot resources 尚未初始化。")
    return resources


def build_guild_repository(interaction: discord.Interaction) -> GuildRepository:
    return GuildRepository(require_resources(interaction).database)


async def refresh_panel_if_needed(interaction: discord.Interaction, guild_id: int) -> None:
    try:
        from services.panel_service import PanelService

        resources = require_resources(interaction)
        panel_service = PanelService(resources.database, bot=interaction.client, logging_service=resources.logging_service)
        await panel_service.refresh_active_panel(guild_id)
    except Exception:
        pass


async def load_config_for_submit(
    interaction: discord.Interaction,
    guild_id: int,
) -> tuple[GuildRepository, GuildConfigRecord] | None:
    await safe_defer(interaction)
    repo = build_guild_repository(interaction)
    current = repo.get_config(guild_id)
    if current is None:
        await send_ephemeral_text(interaction, "服务器配置不存在，请先执行 /ticket setup。")
        return None
    return repo, current


async def apply_config_updates(
    interaction: discord.Interaction,
    *,
    guild_id: int,
    repo: GuildRepository,
    updates: dict[str, Any],
    labels: dict[str, str],
    refresh_panel: bool = False,
) -> None:
    if not updates:
        await send_ephemeral_text(interaction, "未检测到变更。")
        return
    repo.update_config(guild_id, **updates)
    if refresh_panel:
        await refresh_panel_if_needed(interaction, guild_id)
    await send_ephemeral_text(interaction, format_changes(updates, labels))


def format_validation_errors(errors: list[str]) -> str:
    return "校验失败：\n" + "\n".join(f"- {error}" for error in errors)


def format_changes(parsed: dict[str, Any], labels: dict[str, str]) -> str:
    lines = []
    for key, value in parsed.items():
        label = labels.get(key, key)
        if value is None:
            lines.append(f"- {label}: 已恢复默认")
            continue
        lines.append(f"- {label}: `{format_change_value(value)}`")
    return "配置已更新：\n" + "\n".join(lines)


def format_change_value(value: Any) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return str(value)


def resolve_panel_body(config: GuildConfigRecord) -> str:
    return build_public_panel_body(
        description=config.panel_description,
        bullet_points=config.panel_bullet_points,
    )


def resolve_draft_welcome_text(config: GuildConfigRecord) -> str:
    return config.draft_welcome_text or build_default_draft_welcome_text(
        inactive_close_hours=config.draft_inactive_close_hours,
        abandon_timeout_hours=config.draft_abandon_timeout_hours,
    )


def resolve_snapshot_warning_text(config: GuildConfigRecord) -> str:
    return config.snapshot_warning_text or build_default_snapshot_warning_text(limit=config.snapshot_limit)


def resolve_snapshot_limit_text(config: GuildConfigRecord) -> str:
    return config.snapshot_limit_text or build_default_snapshot_limit_text(limit=config.snapshot_limit)


def normalize_default_backed_value(value: str | None, *, default_value: str) -> str | None:
    if value is None or value == default_value:
        return None
    return value


def resolve_default_backed_text_update(
    *,
    parsed: dict[str, str | None],
    field: str,
    current_value: str | None,
    default_value: str,
) -> dict[str, str | None]:
    if field not in parsed:
        return {}
    target_value = normalize_default_backed_value(parsed[field], default_value=default_value)
    if target_value == current_value:
        return {}
    return {field: target_value}
