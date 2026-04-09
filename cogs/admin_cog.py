from __future__ import annotations

from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from core.errors import PermissionDeniedError, ValidationError
from cogs.ticket_command_groups import ticket_group
from services.setup_service import SetupResult, SetupService


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        resources = getattr(bot, "resources", None)
        if resources is None:
            raise RuntimeError("Bot resources 尚未初始化，无法加载 AdminCog。")

        self.bot = bot
        self.logging_service = resources.logging_service
        self.setup_service = SetupService(resources.database)

    @ticket_group.command(name="setup", description="初始化当前服务器的 Ticket 配置")
    @app_commands.guild_only()
    @app_commands.describe(
        log_channel="用于记录 Ticket 事件的日志频道",
        archive_channel="用于发送归档记录的频道",
        ticket_category="用于承载 Ticket 子频道的分类",
        admin_role="Ticket 系统管理员角色",
    )
    async def setup_command(
        self,
        interaction: discord.Interaction,
        log_channel: discord.TextChannel,
        archive_channel: discord.TextChannel,
        ticket_category: discord.CategoryChannel,
        admin_role: discord.Role,
    ) -> None:
        await self.run_setup(
            interaction,
            log_channel=log_channel,
            archive_channel=archive_channel,
            ticket_category=ticket_category,
            admin_role=admin_role,
        )

    async def run_setup(
        self,
        interaction: discord.Interaction,
        *,
        log_channel: Any,
        archive_channel: Any,
        ticket_category: Any,
        admin_role: Any,
    ) -> None:
        try:
            guild = self._require_guild(interaction)
            await self._ensure_setup_permission(interaction)
            result = self.setup_service.setup_guild(
                guild,
                log_channel_id=log_channel.id,
                archive_channel_id=archive_channel.id,
                ticket_category_channel_id=ticket_category.id,
                admin_role_id=admin_role.id,
            )
        except (PermissionDeniedError, ValidationError) as exc:
            await self._send_ephemeral(interaction, str(exc))
            return

        self.logging_service.log_local_info(
            "Guild setup completed. guild_id=%s admin_role_id=%s default_categories=%s",
            guild.id,
            admin_role.id,
            len(result.categories),
        )
        await self._send_ephemeral(interaction, self._build_setup_success_message(result))

    async def _ensure_setup_permission(self, interaction: discord.Interaction) -> None:
        if await self.bot.is_owner(interaction.user):
            return

        permissions = getattr(interaction.user, "guild_permissions", None)
        if permissions is not None and permissions.administrator:
            return

        raise PermissionDeniedError("只有服务器管理员或 Bot 所有者可以执行 /ticket setup。")

    @staticmethod
    def _require_guild(interaction: discord.Interaction) -> Any:
        if interaction.guild is None:
            raise ValidationError("该命令只能在服务器中使用。")
        return interaction.guild

    @staticmethod
    def _build_setup_success_message(result: SetupResult) -> str:
        category_summary = "、".join(category.display_name for category in result.categories[:5])
        created_text = "已写入默认分类模板。" if result.created_default_categories else "保留了已有分类配置。"
        return (
            "Ticket setup 已完成。\n"
            f"- 管理员角色：<@&{result.config.admin_role_id}>\n"
            f"- 日志频道：<#{result.config.log_channel_id}>\n"
            f"- 归档频道：<#{result.config.archive_channel_id}>\n"
            f"- Ticket 分类：<#{result.config.ticket_category_channel_id}>\n"
            f"- 分类数量：{len(result.categories)}\n"
            f"- 分类预览：{category_summary}\n"
            f"- 结果：{created_text}"
        )

    @staticmethod
    async def _send_ephemeral(interaction: discord.Interaction, content: str) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
            return
        await interaction.response.send_message(content, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    try:
        bot.tree.add_command(ticket_group)
    except app_commands.CommandAlreadyRegistered:
        pass
    await bot.add_cog(AdminCog(bot))
