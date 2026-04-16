from __future__ import annotations

import discord

from core.models import GuildConfigRecord
from discord_ui.config_setting_modals import (
    BasicSettingsModal,
    CloseTransferModal,
    DraftTimeoutModal,
    SnapshotLimitsModal,
)
from discord_ui.config_text_modals import (
    DraftWelcomeTextModal,
    PanelTextModal,
    SnapshotTextModal,
)
from discord_ui.interaction_helpers import send_ephemeral_message


class ConfigCategorySelect(discord.ui.Select):
    def __init__(self, *, guild_id: int, config: GuildConfigRecord) -> None:
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
        self.guild_id = guild_id
        self.config = config

    async def callback(self, interaction: discord.Interaction) -> None:
        modal_map = {
            "basic": BasicSettingsModal,
            "draft_timeout": DraftTimeoutModal,
            "close_transfer": CloseTransferModal,
            "snapshot": SnapshotLimitsModal,
        }
        choice = self.values[0]
        if choice in modal_map:
            await interaction.response.send_modal(modal_map[choice](guild_id=self.guild_id, config=self.config))
            return

        await send_ephemeral_message(
            interaction,
            embed=discord.Embed(
                title="✏️ 文案设置",
                description="请选择要修改的文案类别。提交后留空的字段将恢复为默认值。",
                color=discord.Color.blue(),
            ),
            view=TextGroupView(guild_id=self.guild_id, config=self.config),
        )


class ConfigPanelView(discord.ui.View):
    def __init__(self, *, guild_id: int, config: GuildConfigRecord, timeout: float = 300.0) -> None:
        super().__init__(timeout=timeout)
        self.add_item(ConfigCategorySelect(guild_id=guild_id, config=config))


class TextGroupSelect(discord.ui.Select):
    def __init__(self, *, guild_id: int, config: GuildConfigRecord) -> None:
        super().__init__(
            placeholder="选择要修改的文案组",
            options=[
                discord.SelectOption(label="公开面板", value="panel", description="标题、正文、页脚", emoji="📋"),
                discord.SelectOption(label="草稿欢迎", value="draft_welcome", description="创建 Ticket 时的欢迎信息", emoji="👋"),
                discord.SelectOption(label="快照提示", value="snapshot_text", description="消息数接近/达到上限的提示", emoji="⚠️"),
            ],
        )
        self.guild_id = guild_id
        self.config = config

    async def callback(self, interaction: discord.Interaction) -> None:
        modal_map = {
            "panel": PanelTextModal,
            "draft_welcome": DraftWelcomeTextModal,
            "snapshot_text": SnapshotTextModal,
        }
        modal_cls = modal_map.get(self.values[0])
        if modal_cls is not None:
            await interaction.response.send_modal(modal_cls(guild_id=self.guild_id, config=self.config))


class TextGroupView(discord.ui.View):
    def __init__(self, *, guild_id: int, config: GuildConfigRecord, timeout: float = 300.0) -> None:
        super().__init__(timeout=timeout)
        self.add_item(TextGroupSelect(guild_id=guild_id, config=config))
