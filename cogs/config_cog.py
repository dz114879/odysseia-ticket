from __future__ import annotations

from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from cogs.ticket_command_groups import ticket_group
from core.errors import PermissionDeniedError, ValidationError
from discord_ui.interaction_helpers import safe_defer, send_ephemeral_message, send_ephemeral_text
from discord_ui.config_views import ConfigPanelView
from services.guild_config_service import GuildConfigService


class ConfigCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        resources = getattr(bot, "resources", None)
        if resources is None:
            raise RuntimeError("Bot resources 尚未初始化，无法加载 ConfigCog。")

        self.bot = bot
        self.guild_config_service = GuildConfigService(resources.database)

    async def cog_load(self) -> None:
        self.config_command.binding = self

    @ticket_group.command(name="config", description="打开当前服务器的 Ticket 运行时配置面板")
    @app_commands.guild_only()
    async def config_command(self, interaction: discord.Interaction) -> None:
        await self.run_config(interaction)

    async def run_config(self, interaction: discord.Interaction) -> None:
        await safe_defer(interaction)
        try:
            guild = self._require_guild(interaction)
            await self._ensure_permission(interaction)

            config = self.guild_config_service.get_config(guild.id)
            if config is None or not config.is_initialized:
                raise ValidationError("当前服务器尚未完成 Ticket setup，请先执行 /ticket setup。")
        except (PermissionDeniedError, ValidationError) as exc:
            await send_ephemeral_text(interaction, str(exc))
            return

        embed = discord.Embed(
            title="⚙️ Ticket 运行时配置",
            description=(
                "请在下方选择要修改的配置类别。修改后立即生效，无需重启。\n\n"
                "- **基础设置**：时区、活跃工单上限、认领模式\n"
                "- **草稿超时**：不活跃关闭时间、无消息废弃时间\n"
                "- **关闭与转交**：转交延迟、关闭撤销窗口\n"
                "- **快照限制**：消息快照警告和上限阈值\n"
                "- **文案设置**：自定义面板、欢迎、关闭等文案"
            ),
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"当前时区: {config.timezone} | 活跃上限: {config.max_open_tickets} | 认领模式: {config.claim_mode.value}")

        view = ConfigPanelView(guild_id=guild.id, config=config)
        await send_ephemeral_message(interaction, embed=embed, view=view)

    async def _ensure_permission(self, interaction: discord.Interaction) -> None:
        if await self.bot.is_owner(interaction.user):
            return

        guild = self._require_guild(interaction)
        member = interaction.user
        permissions = getattr(member, "guild_permissions", None)
        if permissions is not None and permissions.administrator:
            return

        config = self.guild_config_service.get_config(guild.id)
        if config is None or config.admin_role_id is None:
            raise PermissionDeniedError("当前服务器尚未完成 Ticket setup，无法管理配置。")

        role_ids = {role.id for role in getattr(member, "roles", [])}
        if config.admin_role_id in role_ids:
            return

        raise PermissionDeniedError("只有 Ticket 管理员角色、服务器管理员或 Bot 所有者可以管理配置。")

    @staticmethod
    def _require_guild(interaction: discord.Interaction) -> Any:
        if interaction.guild is None:
            raise ValidationError("该命令只能在服务器中使用。")
        return interaction.guild

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ConfigCog(bot))
