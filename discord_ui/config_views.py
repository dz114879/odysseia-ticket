from __future__ import annotations

from typing import Any

import discord
from discord import TextStyle

from config.defaults import (
    DEFAULT_PANEL_BODY,
    DEFAULT_PANEL_FOOTER_TEXT,
    DEFAULT_PANEL_TITLE,
    build_default_draft_welcome_text,
    build_default_snapshot_limit_text,
    build_default_snapshot_warning_text,
    build_public_panel_body,
)
from core.models import GuildConfigRecord
from db.repositories.guild_repository import GuildRepository
from services.config_validation import (
    validate_basic_settings,
    validate_close_transfer,
    validate_draft_timeouts,
    validate_snapshot_limits,
    validate_text_fields,
)


# ── 工具函数 ──────────────────────────────────────────────────


def _require_resources(interaction: discord.Interaction) -> Any:
    client = getattr(interaction, "client", None)
    resources = getattr(client, "resources", None)
    if resources is None:
        raise RuntimeError("Bot resources 尚未初始化。")
    return resources


def _build_guild_repository(interaction: discord.Interaction) -> GuildRepository:
    return GuildRepository(_require_resources(interaction).database)


async def _refresh_panel_if_needed(interaction: discord.Interaction, guild_id: int) -> None:
    try:
        from services.panel_service import PanelService

        resources = _require_resources(interaction)
        panel_service = PanelService(resources.database, bot=interaction.client, logging_service=resources.logging_service)
        await panel_service.refresh_active_panel(guild_id)
    except Exception:
        pass


async def _defer_ephemeral(interaction: discord.Interaction) -> None:
    if interaction.response.is_done():
        return
    await interaction.response.defer(ephemeral=True, thinking=True)


async def _send_ephemeral(interaction: discord.Interaction, content: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=True)
        return
    await interaction.response.send_message(content, ephemeral=True)


def _format_change_value(value: Any) -> str:
    if isinstance(value, bool):
        return "是" if value else "否"
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return str(value)


def _format_changes(parsed: dict[str, Any], labels: dict[str, str]) -> str:
    if not parsed:
        return "未检测到变更。"
    lines = []
    for key, val in parsed.items():
        label = labels.get(key, key)
        if val is None:
            lines.append(f"- {label}: 已恢复默认")
        else:
            lines.append(f"- {label}: `{_format_change_value(val)}`")
    return "配置已更新：\n" + "\n".join(lines)


def _resolve_panel_body(config: GuildConfigRecord) -> str:
    return build_public_panel_body(
        description=config.panel_description,
        bullet_points=config.panel_bullet_points,
    )


def _resolve_draft_welcome_text(config: GuildConfigRecord) -> str:
    if config.draft_welcome_text:
        return config.draft_welcome_text
    return _build_default_draft_welcome_text(config)


def _build_default_draft_welcome_text(config: GuildConfigRecord) -> str:
    return build_default_draft_welcome_text(
        inactive_close_hours=config.draft_inactive_close_hours,
        abandon_timeout_hours=config.draft_abandon_timeout_hours,
    )


def _resolve_snapshot_warning_text(config: GuildConfigRecord) -> str:
    if config.snapshot_warning_text:
        return config.snapshot_warning_text
    return _build_default_snapshot_warning_text(config)


def _build_default_snapshot_warning_text(config: GuildConfigRecord) -> str:
    return build_default_snapshot_warning_text(limit=config.snapshot_limit)


def _resolve_snapshot_limit_text(config: GuildConfigRecord) -> str:
    if config.snapshot_limit_text:
        return config.snapshot_limit_text
    return _build_default_snapshot_limit_text(config)


def _build_default_snapshot_limit_text(config: GuildConfigRecord) -> str:
    return build_default_snapshot_limit_text(limit=config.snapshot_limit)


def _normalize_default_backed_value(value: str | None, *, default_value: str) -> str | None:
    if value is None or value == default_value:
        return None
    return value


def _resolve_default_backed_text_update(
    *,
    parsed: dict[str, str | None],
    field: str,
    current_value: str | None,
    default_value: str,
) -> dict[str, str | None]:
    if field not in parsed:
        return {}

    target_value = _normalize_default_backed_value(parsed[field], default_value=default_value)
    if target_value == current_value:
        return {}
    return {field: target_value}


# ── 顶层类别选择 ─────────────────────────────────────────────


class ConfigCategorySelect(discord.ui.Select):
    def __init__(self, *, guild_id: int, config: GuildConfigRecord) -> None:
        self.guild_id = guild_id
        self.config = config
        super().__init__(
            placeholder="选择要修改的配置类别",
            options=[
                discord.SelectOption(label="基础设置", value="basic", description="时区、容量、认领模式", emoji="⚙️"),
                discord.SelectOption(label="草稿超时", value="draft_timeout", description="不活跃关闭、无消息废弃", emoji="⏱️"),
                discord.SelectOption(label="关闭与转交", value="close_transfer", description="转交延迟、撤销窗口", emoji="🔄"),
                discord.SelectOption(label="快照限制", value="snapshot", description="消息记录警告和上限", emoji="📸"),
                discord.SelectOption(label="文案设置", value="text", description="自定义面板、欢迎等文案", emoji="✏️"),
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        choice = self.values[0]
        modal_map = {
            "basic": BasicSettingsModal,
            "draft_timeout": DraftTimeoutModal,
            "close_transfer": CloseTransferModal,
            "snapshot": SnapshotLimitsModal,
        }
        if choice in modal_map:
            await interaction.response.send_modal(modal_map[choice](guild_id=self.guild_id, config=self.config))
        elif choice == "text":
            embed = discord.Embed(
                title="✏️ 文案设置",
                description="请选择要修改的文案类别。提交后留空的字段将恢复为默认值。",
                color=discord.Color.blue(),
            )
            await interaction.response.send_message(
                embed=embed,
                view=TextGroupView(guild_id=self.guild_id, config=self.config),
                ephemeral=True,
            )


class ConfigPanelView(discord.ui.View):
    def __init__(self, *, guild_id: int, config: GuildConfigRecord, timeout: float = 300.0) -> None:
        super().__init__(timeout=timeout)
        self.add_item(ConfigCategorySelect(guild_id=guild_id, config=config))


# ── 文案子类别选择 ────────────────────────────────────────────


class TextGroupSelect(discord.ui.Select):
    def __init__(self, *, guild_id: int, config: GuildConfigRecord) -> None:
        self.guild_id = guild_id
        self.config = config
        super().__init__(
            placeholder="选择要修改的文案组",
            options=[
                discord.SelectOption(label="公开面板", value="panel", description="标题、正文、页脚", emoji="📋"),
                discord.SelectOption(label="草稿欢迎", value="draft_welcome", description="创建 Ticket 时的欢迎信息", emoji="👋"),
                discord.SelectOption(label="快照提示", value="snapshot_text", description="消息数接近/达到上限的提示", emoji="⚠️"),
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        modal_map = {
            "panel": PanelTextModal,
            "draft_welcome": DraftWelcomeTextModal,
            "snapshot_text": SnapshotTextModal,
        }
        modal_cls = modal_map.get(self.values[0])
        if modal_cls:
            await interaction.response.send_modal(modal_cls(guild_id=self.guild_id, config=self.config))


class TextGroupView(discord.ui.View):
    def __init__(self, *, guild_id: int, config: GuildConfigRecord, timeout: float = 300.0) -> None:
        super().__init__(timeout=timeout)
        self.add_item(TextGroupSelect(guild_id=guild_id, config=config))


# ── 数值设置 Modal ────────────────────────────────────────────


class _SettingsSelect(discord.ui.Select):
    def __init__(
        self,
        *,
        placeholder: str,
        current_value: str,
        options: list[discord.SelectOption],
        row: int,
    ) -> None:
        self.current_value = current_value
        normalized_options = [
            discord.SelectOption(
                label=option.label,
                value=option.value,
                description=option.description,
                emoji=option.emoji,
                default=option.value == current_value,
            )
            for option in options
        ]
        super().__init__(
            placeholder=placeholder,
            options=normalized_options,
            min_values=1,
            max_values=1,
            row=row,
        )

    def resolve_value(self) -> str:
        if self.values:
            return self.values[0]
        return self.current_value


class BasicSettingsModal(discord.ui.Modal, title="基础设置"):
    def __init__(self, *, guild_id: int, config: GuildConfigRecord) -> None:
        super().__init__()
        self.guild_id = guild_id
        self.timezone_input = discord.ui.TextInput(
            label="时区",
            placeholder="例如 Asia/Shanghai",
            default=config.timezone,
            max_length=50,
            required=False,
            row=0,
        )
        self.max_tickets_input = discord.ui.TextInput(
            label="活跃工单上限",
            placeholder="1-1000",
            default=str(config.max_open_tickets),
            max_length=5,
            required=False,
            row=1,
        )
        self.claim_mode_select = _SettingsSelect(
            placeholder="认领模式",
            current_value=config.claim_mode.value,
            options=[
                discord.SelectOption(label="relaxed 协作模式", value="relaxed", description="staff 可协作处理"),
                discord.SelectOption(label="strict 严格认领", value="strict", description="未认领前 staff 默认不可发言"),
            ],
            row=2,
        )
        self.download_window_select = _SettingsSelect(
            placeholder="归档下载窗口",
            current_value="true" if config.enable_download_window else "false",
            options=[
                discord.SelectOption(label="开启", value="true", description="归档完成后显示下载窗口"),
                discord.SelectOption(label="关闭", value="false", description="归档完成后不显示下载窗口"),
            ],
            row=3,
        )
        self.add_item(self.timezone_input)
        self.add_item(self.max_tickets_input)
        self.add_item(self.claim_mode_select)
        self.add_item(self.download_window_select)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _defer_ephemeral(interaction)
        raw = {
            "timezone": self.timezone_input.value,
            "max_open_tickets": self.max_tickets_input.value,
            "claim_mode": self.claim_mode_select.resolve_value(),
            "enable_download_window": self.download_window_select.resolve_value(),
        }
        repo = _build_guild_repository(interaction)
        current = repo.get_config(self.guild_id)
        if current is None:
            await _send_ephemeral(interaction, "服务器配置不存在，请先执行 /ticket setup。")
            return
        parsed, errors = validate_basic_settings(raw, current)
        if errors:
            await _send_ephemeral(interaction, "校验失败：\n" + "\n".join(f"- {e}" for e in errors))
            return
        if not parsed:
            await _send_ephemeral(interaction, "未检测到变更。")
            return
        repo.update_config(self.guild_id, **parsed)
        labels = {"timezone": "时区", "max_open_tickets": "活跃工单上限", "claim_mode": "认领模式", "enable_download_window": "归档下载窗口"}
        await _send_ephemeral(interaction, _format_changes(parsed, labels))


class DraftTimeoutModal(discord.ui.Modal, title="草稿超时设置"):
    inactive_input = discord.ui.TextInput(label="不活跃关闭（小时）", placeholder="2-168，建议 6", max_length=4, required=False)
    abandon_input = discord.ui.TextInput(label="无消息废弃（小时）", placeholder="2-720，建议 24", max_length=4, required=False)

    def __init__(self, *, guild_id: int, config: GuildConfigRecord) -> None:
        super().__init__()
        self.guild_id = guild_id
        self.inactive_input.default = str(config.draft_inactive_close_hours)
        self.abandon_input.default = str(config.draft_abandon_timeout_hours)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _defer_ephemeral(interaction)
        raw = {
            "draft_inactive_close_hours": self.inactive_input.value,
            "draft_abandon_timeout_hours": self.abandon_input.value,
        }
        repo = _build_guild_repository(interaction)
        current = repo.get_config(self.guild_id)
        if current is None:
            await _send_ephemeral(interaction, "服务器配置不存在，请先执行 /ticket setup。")
            return
        parsed, errors = validate_draft_timeouts(raw, current)
        if errors:
            await _send_ephemeral(interaction, "校验失败：\n" + "\n".join(f"- {e}" for e in errors))
            return
        if not parsed:
            await _send_ephemeral(interaction, "未检测到变更。")
            return
        repo.update_config(self.guild_id, **parsed)
        labels = {"draft_inactive_close_hours": "不活跃关闭", "draft_abandon_timeout_hours": "无消息废弃"}
        await _send_ephemeral(interaction, _format_changes(parsed, labels))


class CloseTransferModal(discord.ui.Modal, title="关闭与转交设置"):
    transfer_input = discord.ui.TextInput(label="转交延迟（秒）", placeholder="10-86400，建议 300", max_length=6, required=False)
    revoke_input = discord.ui.TextInput(label="关闭撤销窗口（秒）", placeholder="10-3600，建议 120", max_length=5, required=False)
    request_input = discord.ui.TextInput(label="关闭请求超时（秒）", placeholder="10-3600，建议 300", max_length=5, required=False)

    def __init__(self, *, guild_id: int, config: GuildConfigRecord) -> None:
        super().__init__()
        self.guild_id = guild_id
        self.transfer_input.default = str(config.transfer_delay_seconds)
        self.revoke_input.default = str(config.close_revoke_window_seconds)
        self.request_input.default = str(config.close_request_timeout_seconds)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _defer_ephemeral(interaction)
        raw = {
            "transfer_delay_seconds": self.transfer_input.value,
            "close_revoke_window_seconds": self.revoke_input.value,
            "close_request_timeout_seconds": self.request_input.value,
        }
        repo = _build_guild_repository(interaction)
        current = repo.get_config(self.guild_id)
        if current is None:
            await _send_ephemeral(interaction, "服务器配置不存在，请先执行 /ticket setup。")
            return
        parsed, errors = validate_close_transfer(raw, current)
        if errors:
            await _send_ephemeral(interaction, "校验失败：\n" + "\n".join(f"- {e}" for e in errors))
            return
        if not parsed:
            await _send_ephemeral(interaction, "未检测到变更。")
            return
        repo.update_config(self.guild_id, **parsed)
        labels = {"transfer_delay_seconds": "转交延迟", "close_revoke_window_seconds": "撤销窗口", "close_request_timeout_seconds": "请求超时"}
        await _send_ephemeral(interaction, _format_changes(parsed, labels))


class SnapshotLimitsModal(discord.ui.Modal, title="快照限制设置"):
    threshold_input = discord.ui.TextInput(label="警告阈值", placeholder="100-10000，建议 900", max_length=6, required=False)
    limit_input = discord.ui.TextInput(label="记录上限", placeholder="100-10000，需大于警告阈值", max_length=6, required=False)

    def __init__(self, *, guild_id: int, config: GuildConfigRecord) -> None:
        super().__init__()
        self.guild_id = guild_id
        self.threshold_input.default = str(config.snapshot_warning_threshold)
        self.limit_input.default = str(config.snapshot_limit)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _defer_ephemeral(interaction)
        raw = {
            "snapshot_warning_threshold": self.threshold_input.value,
            "snapshot_limit": self.limit_input.value,
        }
        repo = _build_guild_repository(interaction)
        current = repo.get_config(self.guild_id)
        if current is None:
            await _send_ephemeral(interaction, "服务器配置不存在，请先执行 /ticket setup。")
            return
        parsed, errors = validate_snapshot_limits(raw, current)
        if errors:
            await _send_ephemeral(interaction, "校验失败：\n" + "\n".join(f"- {e}" for e in errors))
            return
        if not parsed:
            await _send_ephemeral(interaction, "未检测到变更。")
            return
        repo.update_config(self.guild_id, **parsed)
        labels = {"snapshot_warning_threshold": "警告阈值", "snapshot_limit": "记录上限"}
        await _send_ephemeral(interaction, _format_changes(parsed, labels))


# ── 文案 Modal ────────────────────────────────────────────────


class PanelTextModal(discord.ui.Modal, title="公开面板文案"):
    title_input = discord.ui.TextInput(label="面板标题", placeholder="留空恢复默认", max_length=256, required=False)
    body_input = discord.ui.TextInput(label="面板正文", style=TextStyle.paragraph, placeholder="留空恢复默认", max_length=4000, required=False)
    footer_input = discord.ui.TextInput(label="面板页脚", style=TextStyle.paragraph, placeholder="留空恢复默认", max_length=2048, required=False)

    def __init__(self, *, guild_id: int, config: GuildConfigRecord) -> None:
        super().__init__()
        self.guild_id = guild_id
        self.title_input.default = config.panel_title or DEFAULT_PANEL_TITLE
        self.body_input.default = _resolve_panel_body(config)
        self.footer_input.default = config.panel_footer_text or DEFAULT_PANEL_FOOTER_TEXT

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _defer_ephemeral(interaction)
        repo = _build_guild_repository(interaction)
        current = repo.get_config(self.guild_id)
        if current is None:
            await _send_ephemeral(interaction, "服务器配置不存在，请先执行 /ticket setup。")
            return
        raw = {
            "panel_title": self.title_input.value,
            "panel_description": self.body_input.value,
            "panel_footer_text": self.footer_input.value,
        }
        parsed, errors = validate_text_fields(raw)
        if errors:
            await _send_ephemeral(interaction, "校验失败：\n" + "\n".join(f"- {e}" for e in errors))
            return
        updates: dict[str, str | None] = {}
        updates.update(
            _resolve_default_backed_text_update(
                parsed=parsed,
                field="panel_title",
                current_value=current.panel_title,
                default_value=DEFAULT_PANEL_TITLE,
            )
        )
        if "panel_description" in parsed:
            target_description = _normalize_default_backed_value(parsed["panel_description"], default_value=DEFAULT_PANEL_BODY)
            if target_description != current.panel_description or current.panel_bullet_points is not None:
                updates["panel_description"] = target_description
                updates["panel_bullet_points"] = None
        updates.update(
            _resolve_default_backed_text_update(
                parsed=parsed,
                field="panel_footer_text",
                current_value=current.panel_footer_text,
                default_value=DEFAULT_PANEL_FOOTER_TEXT,
            )
        )
        if not updates:
            await _send_ephemeral(interaction, "未检测到变更。")
            return
        repo.update_config(self.guild_id, **updates)
        await _refresh_panel_if_needed(interaction, self.guild_id)
        labels = {"panel_title": "标题", "panel_description": "正文", "panel_bullet_points": "旧要点", "panel_footer_text": "页脚"}
        await _send_ephemeral(interaction, _format_changes(updates, labels))


class DraftWelcomeTextModal(discord.ui.Modal, title="草稿欢迎文案"):
    welcome_input = discord.ui.TextInput(
        label="欢迎文案", style=TextStyle.paragraph, placeholder="留空恢复默认", max_length=4000, required=False,
    )

    def __init__(self, *, guild_id: int, config: GuildConfigRecord) -> None:
        super().__init__()
        self.guild_id = guild_id
        self.welcome_input.default = _resolve_draft_welcome_text(config)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _defer_ephemeral(interaction)
        repo = _build_guild_repository(interaction)
        current = repo.get_config(self.guild_id)
        if current is None:
            await _send_ephemeral(interaction, "服务器配置不存在，请先执行 /ticket setup。")
            return
        raw = {"draft_welcome_text": self.welcome_input.value}
        parsed, errors = validate_text_fields(raw)
        if errors:
            await _send_ephemeral(interaction, "校验失败：\n" + "\n".join(f"- {e}" for e in errors))
            return
        updates = _resolve_default_backed_text_update(
            parsed=parsed,
            field="draft_welcome_text",
            current_value=current.draft_welcome_text,
            default_value=_build_default_draft_welcome_text(current),
        )
        if not updates:
            await _send_ephemeral(interaction, "未检测到变更。")
            return
        repo.update_config(self.guild_id, **updates)
        labels = {"draft_welcome_text": "草稿欢迎文案"}
        await _send_ephemeral(interaction, _format_changes(updates, labels))


class SnapshotTextModal(discord.ui.Modal, title="快照提示文案"):
    warning_input = discord.ui.TextInput(
        label="接近上限提示", style=TextStyle.paragraph, placeholder="留空恢复默认", max_length=1000, required=False,
    )
    limit_input = discord.ui.TextInput(
        label="达到上限提示", style=TextStyle.paragraph, placeholder="留空恢复默认", max_length=1000, required=False,
    )

    def __init__(self, *, guild_id: int, config: GuildConfigRecord) -> None:
        super().__init__()
        self.guild_id = guild_id
        self.warning_input.default = _resolve_snapshot_warning_text(config)
        self.limit_input.default = _resolve_snapshot_limit_text(config)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _defer_ephemeral(interaction)
        repo = _build_guild_repository(interaction)
        current = repo.get_config(self.guild_id)
        if current is None:
            await _send_ephemeral(interaction, "服务器配置不存在，请先执行 /ticket setup。")
            return
        raw = {"snapshot_warning_text": self.warning_input.value, "snapshot_limit_text": self.limit_input.value}
        parsed, errors = validate_text_fields(raw)
        if errors:
            await _send_ephemeral(interaction, "校验失败：\n" + "\n".join(f"- {e}" for e in errors))
            return
        updates: dict[str, str | None] = {}
        updates.update(
            _resolve_default_backed_text_update(
                parsed=parsed,
                field="snapshot_warning_text",
                current_value=current.snapshot_warning_text,
                default_value=_build_default_snapshot_warning_text(current),
            )
        )
        updates.update(
            _resolve_default_backed_text_update(
                parsed=parsed,
                field="snapshot_limit_text",
                current_value=current.snapshot_limit_text,
                default_value=_build_default_snapshot_limit_text(current),
            )
        )
        if not updates:
            await _send_ephemeral(interaction, "未检测到变更。")
            return
        repo.update_config(self.guild_id, **updates)
        labels = {"snapshot_warning_text": "接近上限提示", "snapshot_limit_text": "达到上限提示"}
        await _send_ephemeral(interaction, _format_changes(updates, labels))
