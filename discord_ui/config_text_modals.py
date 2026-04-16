from __future__ import annotations

import discord
from discord import TextStyle

from config.defaults import DEFAULT_PANEL_BODY, DEFAULT_PANEL_FOOTER_TEXT, DEFAULT_PANEL_TITLE
from core.models import GuildConfigRecord
from discord_ui.config_modal_shared import (
    apply_config_updates,
    format_validation_errors,
    load_config_for_submit,
    normalize_default_backed_value,
    resolve_default_backed_text_update,
    resolve_draft_welcome_text,
    resolve_panel_body,
    resolve_snapshot_limit_text,
    resolve_snapshot_warning_text,
)
from discord_ui.interaction_helpers import send_ephemeral_text
from services.config_validation import validate_text_fields


class PanelTextModal(discord.ui.Modal, title="公开面板文案"):
    title_input = discord.ui.TextInput(label="面板标题", placeholder="留空恢复默认", max_length=256, required=False)
    body_input = discord.ui.TextInput(label="面板正文", style=TextStyle.paragraph, placeholder="留空恢复默认", max_length=4000, required=False)
    footer_input = discord.ui.TextInput(label="面板页脚", style=TextStyle.paragraph, placeholder="留空恢复默认", max_length=2048, required=False)

    def __init__(self, *, guild_id: int, config: GuildConfigRecord) -> None:
        super().__init__()
        self.guild_id = guild_id
        self.title_input.default = config.panel_title or DEFAULT_PANEL_TITLE
        self.body_input.default = resolve_panel_body(config)
        self.footer_input.default = config.panel_footer_text or DEFAULT_PANEL_FOOTER_TEXT

    async def on_submit(self, interaction: discord.Interaction) -> None:
        loaded = await load_config_for_submit(interaction, self.guild_id)
        if loaded is None:
            return
        repo, current = loaded
        parsed, errors = validate_text_fields(
            {
                "panel_title": self.title_input.value,
                "panel_description": self.body_input.value,
                "panel_footer_text": self.footer_input.value,
            }
        )
        if errors:
            await send_ephemeral_text(interaction, format_validation_errors(errors))
            return

        updates: dict[str, str | None] = {}
        updates.update(resolve_default_backed_text_update(parsed=parsed, field="panel_title", current_value=current.panel_title, default_value=DEFAULT_PANEL_TITLE))
        if "panel_description" in parsed:
            target_description = normalize_default_backed_value(parsed["panel_description"], default_value=DEFAULT_PANEL_BODY)
            if target_description != current.panel_description or current.panel_bullet_points is not None:
                updates["panel_description"] = target_description
                updates["panel_bullet_points"] = None
        updates.update(resolve_default_backed_text_update(parsed=parsed, field="panel_footer_text", current_value=current.panel_footer_text, default_value=DEFAULT_PANEL_FOOTER_TEXT))

        await apply_config_updates(
            interaction,
            guild_id=self.guild_id,
            repo=repo,
            updates=updates,
            labels={"panel_title": "标题", "panel_description": "正文", "panel_bullet_points": "旧要点", "panel_footer_text": "页脚"},
            refresh_panel=True,
        )


class DraftWelcomeTextModal(discord.ui.Modal, title="草稿欢迎文案"):
    welcome_input = discord.ui.TextInput(label="欢迎文案", style=TextStyle.paragraph, placeholder="留空恢复默认", max_length=4000, required=False)

    def __init__(self, *, guild_id: int, config: GuildConfigRecord) -> None:
        super().__init__()
        self.guild_id = guild_id
        self.welcome_input.default = resolve_draft_welcome_text(config)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        loaded = await load_config_for_submit(interaction, self.guild_id)
        if loaded is None:
            return
        repo, current = loaded
        parsed, errors = validate_text_fields({"draft_welcome_text": self.welcome_input.value})
        if errors:
            await send_ephemeral_text(interaction, format_validation_errors(errors))
            return
        await apply_config_updates(
            interaction,
            guild_id=self.guild_id,
            repo=repo,
            updates=resolve_default_backed_text_update(
                parsed=parsed,
                field="draft_welcome_text",
                current_value=current.draft_welcome_text,
                default_value=resolve_draft_welcome_text(current),
            ),
            labels={"draft_welcome_text": "草稿欢迎文案"},
        )


class SnapshotTextModal(discord.ui.Modal, title="快照提示文案"):
    warning_input = discord.ui.TextInput(label="接近上限提示", style=TextStyle.paragraph, placeholder="留空恢复默认", max_length=1000, required=False)
    limit_input = discord.ui.TextInput(label="达到上限提示", style=TextStyle.paragraph, placeholder="留空恢复默认", max_length=1000, required=False)

    def __init__(self, *, guild_id: int, config: GuildConfigRecord) -> None:
        super().__init__()
        self.guild_id = guild_id
        self.warning_input.default = resolve_snapshot_warning_text(config)
        self.limit_input.default = resolve_snapshot_limit_text(config)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        loaded = await load_config_for_submit(interaction, self.guild_id)
        if loaded is None:
            return
        repo, current = loaded
        parsed, errors = validate_text_fields(
            {
                "snapshot_warning_text": self.warning_input.value,
                "snapshot_limit_text": self.limit_input.value,
            }
        )
        if errors:
            await send_ephemeral_text(interaction, format_validation_errors(errors))
            return

        updates: dict[str, str | None] = {}
        updates.update(
            resolve_default_backed_text_update(
                parsed=parsed,
                field="snapshot_warning_text",
                current_value=current.snapshot_warning_text,
                default_value=resolve_snapshot_warning_text(current),
            )
        )
        updates.update(
            resolve_default_backed_text_update(
                parsed=parsed,
                field="snapshot_limit_text",
                current_value=current.snapshot_limit_text,
                default_value=resolve_snapshot_limit_text(current),
            )
        )
        await apply_config_updates(
            interaction,
            guild_id=self.guild_id,
            repo=repo,
            updates=updates,
            labels={"snapshot_warning_text": "接近上限提示", "snapshot_limit_text": "达到上限提示"},
        )
