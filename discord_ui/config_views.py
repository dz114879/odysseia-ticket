from __future__ import annotations

from typing import Any

import discord
from discord import TextStyle

from config.defaults import (
    DEFAULT_PANEL_BULLET_POINTS,
    DEFAULT_PANEL_DESCRIPTION,
    DEFAULT_PANEL_FOOTER_TEXT,
    DEFAULT_PANEL_TITLE,
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


def _format_changes(parsed: dict[str, Any], labels: dict[str, str]) -> str:
    if not parsed:
        return "未检测到变更。"
    lines = []
    for key, val in parsed.items():
        label = labels.get(key, key)
        if val is None:
            lines.append(f"- {label}: 已恢复默认")
        else:
            lines.append(f"- {label}: `{val}`")
    return "配置已更新：\n" + "\n".join(lines)


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
                discord.SelectOption(label="公开面板", value="panel", description="标题、描述、要点、页脚", emoji="📋"),
                discord.SelectOption(label="草稿欢迎", value="draft_welcome", description="创建 Ticket 时的欢迎信息", emoji="👋"),
                discord.SelectOption(label="快照提示", value="snapshot_text", description="消息数接近/达到上限的提示", emoji="⚠️"),
                discord.SelectOption(label="关闭流程", value="close_text", description="关闭请求、关闭通知、撤销通知", emoji="🔒"),
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        modal_map = {
            "panel": PanelTextModal,
            "draft_welcome": DraftWelcomeTextModal,
            "snapshot_text": SnapshotTextModal,
            "close_text": CloseTextModal,
        }
        modal_cls = modal_map.get(self.values[0])
        if modal_cls:
            await interaction.response.send_modal(modal_cls(guild_id=self.guild_id, config=self.config))


class TextGroupView(discord.ui.View):
    def __init__(self, *, guild_id: int, config: GuildConfigRecord, timeout: float = 300.0) -> None:
        super().__init__(timeout=timeout)
        self.add_item(TextGroupSelect(guild_id=guild_id, config=config))


# ── 数值设置 Modal ────────────────────────────────────────────


class BasicSettingsModal(discord.ui.Modal, title="基础设置"):
    timezone_input = discord.ui.TextInput(label="时区", placeholder="例如 Asia/Shanghai", max_length=50, required=False)
    max_tickets_input = discord.ui.TextInput(label="活跃工单上限", placeholder="1-1000", max_length=5, required=False)
    claim_mode_input = discord.ui.TextInput(label="认领模式", placeholder="relaxed / strict", max_length=10, required=False)
    download_window_input = discord.ui.TextInput(label="归档下载窗口", placeholder="是 / 否", max_length=10, required=False)

    def __init__(self, *, guild_id: int, config: GuildConfigRecord) -> None:
        super().__init__()
        self.guild_id = guild_id
        self.timezone_input.default = config.timezone
        self.max_tickets_input.default = str(config.max_open_tickets)
        self.claim_mode_input.default = config.claim_mode.value
        self.download_window_input.default = "是" if config.enable_download_window else "否"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _defer_ephemeral(interaction)
        raw = {
            "timezone": self.timezone_input.value,
            "max_open_tickets": self.max_tickets_input.value,
            "claim_mode": self.claim_mode_input.value,
            "enable_download_window": self.download_window_input.value,
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
        labels = {"timezone": "时区", "max_open_tickets": "活跃工单上限", "claim_mode": "认领模式", "enable_download_window": "下载窗口"}
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
    desc_input = discord.ui.TextInput(label="面板描述", style=TextStyle.paragraph, placeholder="留空恢复默认", max_length=4000, required=False)
    bullets_input = discord.ui.TextInput(label="面板要点", style=TextStyle.paragraph, placeholder="留空恢复默认", max_length=1024, required=False)
    footer_input = discord.ui.TextInput(label="面板页脚", style=TextStyle.paragraph, placeholder="留空恢复默认", max_length=2048, required=False)

    def __init__(self, *, guild_id: int, config: GuildConfigRecord) -> None:
        super().__init__()
        self.guild_id = guild_id
        self.title_input.default = config.panel_title or DEFAULT_PANEL_TITLE
        self.desc_input.default = config.panel_description or DEFAULT_PANEL_DESCRIPTION
        self.bullets_input.default = config.panel_bullet_points or DEFAULT_PANEL_BULLET_POINTS
        self.footer_input.default = config.panel_footer_text or DEFAULT_PANEL_FOOTER_TEXT

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _defer_ephemeral(interaction)
        raw = {
            "panel_title": self.title_input.value,
            "panel_description": self.desc_input.value,
            "panel_bullet_points": self.bullets_input.value,
            "panel_footer_text": self.footer_input.value,
        }
        parsed, errors = validate_text_fields(raw)
        if errors:
            await _send_ephemeral(interaction, "校验失败：\n" + "\n".join(f"- {e}" for e in errors))
            return
        if not parsed:
            await _send_ephemeral(interaction, "未检测到变更。")
            return
        repo = _build_guild_repository(interaction)
        repo.update_config(self.guild_id, **parsed)
        await _refresh_panel_if_needed(interaction, self.guild_id)
        labels = {"panel_title": "标题", "panel_description": "描述", "panel_bullet_points": "要点", "panel_footer_text": "页脚"}
        await _send_ephemeral(interaction, _format_changes(parsed, labels))


class DraftWelcomeTextModal(discord.ui.Modal, title="草稿欢迎文案"):
    welcome_input = discord.ui.TextInput(
        label="欢迎文案", style=TextStyle.paragraph, placeholder="留空恢复默认", max_length=4000, required=False,
    )

    def __init__(self, *, guild_id: int, config: GuildConfigRecord) -> None:
        super().__init__()
        self.guild_id = guild_id
        if config.draft_welcome_text:
            self.welcome_input.default = config.draft_welcome_text

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _defer_ephemeral(interaction)
        raw = {"draft_welcome_text": self.welcome_input.value}
        parsed, errors = validate_text_fields(raw)
        if errors:
            await _send_ephemeral(interaction, "校验失败：\n" + "\n".join(f"- {e}" for e in errors))
            return
        if not parsed:
            await _send_ephemeral(interaction, "未检测到变更。")
            return
        repo = _build_guild_repository(interaction)
        repo.update_config(self.guild_id, **parsed)
        labels = {"draft_welcome_text": "草稿欢迎文案"}
        await _send_ephemeral(interaction, _format_changes(parsed, labels))


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
        if config.snapshot_warning_text:
            self.warning_input.default = config.snapshot_warning_text
        if config.snapshot_limit_text:
            self.limit_input.default = config.snapshot_limit_text

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _defer_ephemeral(interaction)
        raw = {"snapshot_warning_text": self.warning_input.value, "snapshot_limit_text": self.limit_input.value}
        parsed, errors = validate_text_fields(raw)
        if errors:
            await _send_ephemeral(interaction, "校验失败：\n" + "\n".join(f"- {e}" for e in errors))
            return
        if not parsed:
            await _send_ephemeral(interaction, "未检测到变更。")
            return
        repo = _build_guild_repository(interaction)
        repo.update_config(self.guild_id, **parsed)
        labels = {"snapshot_warning_text": "接近上限提示", "snapshot_limit_text": "达到上限提示"}
        await _send_ephemeral(interaction, _format_changes(parsed, labels))


class CloseTextModal(discord.ui.Modal, title="关闭流程文案"):
    request_input = discord.ui.TextInput(
        label="关闭请求描述", style=TextStyle.paragraph, placeholder="留空恢复默认", max_length=1000, required=False,
    )
    notice_input = discord.ui.TextInput(
        label="关闭通知描述", style=TextStyle.paragraph, placeholder="留空恢复默认", max_length=1000, required=False,
    )
    revoke_input = discord.ui.TextInput(
        label="撤销通知描述", style=TextStyle.paragraph, placeholder="留空恢复默认", max_length=1000, required=False,
    )

    def __init__(self, *, guild_id: int, config: GuildConfigRecord) -> None:
        super().__init__()
        self.guild_id = guild_id
        if config.close_request_text:
            self.request_input.default = config.close_request_text
        if config.closing_notice_text:
            self.notice_input.default = config.closing_notice_text
        if config.close_revoke_text:
            self.revoke_input.default = config.close_revoke_text

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _defer_ephemeral(interaction)
        raw = {
            "close_request_text": self.request_input.value,
            "closing_notice_text": self.notice_input.value,
            "close_revoke_text": self.revoke_input.value,
        }
        parsed, errors = validate_text_fields(raw)
        if errors:
            await _send_ephemeral(interaction, "校验失败：\n" + "\n".join(f"- {e}" for e in errors))
            return
        if not parsed:
            await _send_ephemeral(interaction, "未检测到变更。")
            return
        repo = _build_guild_repository(interaction)
        repo.update_config(self.guild_id, **parsed)
        labels = {"close_request_text": "关闭请求", "closing_notice_text": "关闭通知", "close_revoke_text": "撤销通知"}
        await _send_ephemeral(interaction, _format_changes(parsed, labels))
