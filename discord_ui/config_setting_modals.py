from __future__ import annotations

import discord

from core.models import GuildConfigRecord
from discord_ui.config_modal_shared import (
    apply_config_updates,
    format_validation_errors,
    load_config_for_submit,
    log_config_warning,
)
from discord_ui.interaction_helpers import send_ephemeral_text
from services.config_validation import (
    validate_basic_settings,
    validate_close_transfer,
    validate_draft_timeouts,
    validate_snapshot_limits,
)


class _SettingsSelect(discord.ui.Select):
    def __init__(
        self,
        *,
        placeholder: str,
        current_value: str,
        options: list[discord.SelectOption],
    ) -> None:
        super().__init__(
            placeholder=placeholder,
            options=[
                discord.SelectOption(
                    label=option.label,
                    value=option.value,
                    description=option.description,
                    emoji=option.emoji,
                    default=option.value == current_value,
                )
                for option in options
            ],
            min_values=1,
            max_values=1,
        )
        self.current_value = current_value

    def resolve_value(self) -> str:
        return self.values[0] if self.values else self.current_value


class _ConfigModal(discord.ui.Modal):
    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log_config_warning(
            interaction,
            "Ticket config modal failed. guild_id=%s user_id=%s modal=%s",
            getattr(self, "guild_id", None),
            getattr(getattr(interaction, "user", None), "id", None),
            type(self).__name__,
            exc_info=error,
        )
        await super().on_error(interaction, error)


class BasicSettingsModal(_ConfigModal, title="基础设置"):
    def __init__(self, *, guild_id: int, config: GuildConfigRecord) -> None:
        super().__init__()
        self.guild_id = guild_id
        self.timezone_input = discord.ui.TextInput(label="时区", placeholder="例如 Asia/Shanghai", default=config.timezone, max_length=50, required=False)
        self.max_tickets_input = discord.ui.TextInput(label="活跃工单上限", placeholder="1-1000", default=str(config.max_open_tickets), max_length=5, required=False)
        self.claim_mode_select = _SettingsSelect(
            placeholder="认领模式",
            current_value=config.claim_mode.value,
            options=[
                discord.SelectOption(label="relaxed 协作模式", value="relaxed", description="staff 可协作处理"),
                discord.SelectOption(label="strict 严格认领", value="strict", description="未认领前 staff 默认不可发言"),
            ],
        )
        self.download_window_select = _SettingsSelect(
            placeholder="归档下载窗口",
            current_value="true" if config.enable_download_window else "false",
            options=[
                discord.SelectOption(label="开启", value="true", description="归档完成后显示下载窗口"),
                discord.SelectOption(label="关闭", value="false", description="归档完成后不显示下载窗口"),
            ],
        )
        self.add_item(self.timezone_input)
        self.add_item(self.max_tickets_input)
        self.add_item(discord.ui.Label(text="认领模式", component=self.claim_mode_select))
        self.add_item(discord.ui.Label(text="归档下载窗口", component=self.download_window_select))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        loaded = await load_config_for_submit(interaction, self.guild_id)
        if loaded is None:
            return
        repo, current = loaded
        parsed, errors = validate_basic_settings(
            {
                "timezone": self.timezone_input.value,
                "max_open_tickets": self.max_tickets_input.value,
                "claim_mode": self.claim_mode_select.resolve_value(),
                "enable_download_window": self.download_window_select.resolve_value(),
            },
            current,
        )
        if errors:
            await send_ephemeral_text(interaction, format_validation_errors(errors))
            return
        await apply_config_updates(
            interaction,
            guild_id=self.guild_id,
            repo=repo,
            updates=parsed,
            labels={"timezone": "时区", "max_open_tickets": "活跃工单上限", "claim_mode": "认领模式", "enable_download_window": "归档下载窗口"},
        )


class DraftTimeoutModal(_ConfigModal, title="草稿超时设置"):
    inactive_input = discord.ui.TextInput(label="不活跃关闭（小时）", placeholder="2-168，建议 6", max_length=4, required=False)
    abandon_input = discord.ui.TextInput(label="无消息废弃（小时）", placeholder="2-720，建议 24", max_length=4, required=False)

    def __init__(self, *, guild_id: int, config: GuildConfigRecord) -> None:
        super().__init__()
        self.guild_id = guild_id
        self.inactive_input.default = str(config.draft_inactive_close_hours)
        self.abandon_input.default = str(config.draft_abandon_timeout_hours)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        loaded = await load_config_for_submit(interaction, self.guild_id)
        if loaded is None:
            return
        repo, current = loaded
        parsed, errors = validate_draft_timeouts(
            {
                "draft_inactive_close_hours": self.inactive_input.value,
                "draft_abandon_timeout_hours": self.abandon_input.value,
            },
            current,
        )
        if errors:
            await send_ephemeral_text(interaction, format_validation_errors(errors))
            return
        await apply_config_updates(
            interaction,
            guild_id=self.guild_id,
            repo=repo,
            updates=parsed,
            labels={"draft_inactive_close_hours": "不活跃关闭", "draft_abandon_timeout_hours": "无消息废弃"},
        )


class CloseTransferModal(_ConfigModal, title="关闭与转交设置"):
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
        loaded = await load_config_for_submit(interaction, self.guild_id)
        if loaded is None:
            return
        repo, current = loaded
        parsed, errors = validate_close_transfer(
            {
                "transfer_delay_seconds": self.transfer_input.value,
                "close_revoke_window_seconds": self.revoke_input.value,
                "close_request_timeout_seconds": self.request_input.value,
            },
            current,
        )
        if errors:
            await send_ephemeral_text(interaction, format_validation_errors(errors))
            return
        await apply_config_updates(
            interaction,
            guild_id=self.guild_id,
            repo=repo,
            updates=parsed,
            labels={"transfer_delay_seconds": "转交延迟", "close_revoke_window_seconds": "撤销窗口", "close_request_timeout_seconds": "请求超时"},
        )


class SnapshotLimitsModal(_ConfigModal, title="快照限制设置"):
    threshold_input = discord.ui.TextInput(label="警告阈值", placeholder="100-10000，建议 900", max_length=6, required=False)
    limit_input = discord.ui.TextInput(label="记录上限", placeholder="100-10000，需大于警告阈值", max_length=6, required=False)

    def __init__(self, *, guild_id: int, config: GuildConfigRecord) -> None:
        super().__init__()
        self.guild_id = guild_id
        self.threshold_input.default = str(config.snapshot_warning_threshold)
        self.limit_input.default = str(config.snapshot_limit)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        loaded = await load_config_for_submit(interaction, self.guild_id)
        if loaded is None:
            return
        repo, current = loaded
        parsed, errors = validate_snapshot_limits(
            {
                "snapshot_warning_threshold": self.threshold_input.value,
                "snapshot_limit": self.limit_input.value,
            },
            current,
        )
        if errors:
            await send_ephemeral_text(interaction, format_validation_errors(errors))
            return
        await apply_config_updates(
            interaction,
            guild_id=self.guild_id,
            repo=repo,
            updates=parsed,
            labels={"snapshot_warning_threshold": "警告阈值", "snapshot_limit": "记录上限"},
        )
