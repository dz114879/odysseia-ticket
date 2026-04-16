from __future__ import annotations

from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from cogs.ticket_command_groups import ticket_group
from core.errors import PermissionDeniedError, ValidationError
from discord_ui.interaction_helpers import safe_defer, send_ephemeral_text
from services.setup_service import SetupResult, SetupService


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        resources = getattr(bot, "resources", None)
        if resources is None:
            raise RuntimeError("Bot resources 尚未初始化，无法加载 AdminCog。")

        self.bot = bot
        self.logging_service = resources.logging_service
        self.setup_service = SetupService(resources.database)

    async def cog_load(self) -> None:
        self.setup_command.binding = self

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
            await safe_defer(interaction)
            guild = self._require_guild(interaction)
            await self._ensure_setup_permission(interaction)
            result = self.setup_service.setup_guild(
                guild,
                log_channel_id=log_channel.id,
                archive_channel_id=archive_channel.id,
                ticket_category_channel_id=ticket_category.id,
                admin_role_id=admin_role.id,
            )
        except (PermissionDeniedError, ValidationError, discord.HTTPException) as exc:
            await send_ephemeral_text(interaction, str(exc))
            return

        log_title = "服务器配置已更新" if result.is_reconfiguration else "服务器初始化完成"
        log_description = (
            f"管理员 <@{interaction.user.id}> 更新了服务器配置。"
            if result.is_reconfiguration
            else f"管理员 <@{interaction.user.id}> 完成了服务器设置。"
        )
        self.logging_service.log_local_info(
            "Guild setup completed. guild_id=%s admin_role_id=%s default_categories=%s reconfiguration=%s",
            guild.id,
            admin_role.id,
            len(result.categories),
            result.is_reconfiguration,
        )
        await self.logging_service.send_guild_log(
            guild.id,
            "info",
            log_title,
            log_description,
            channel_id=result.config.log_channel_id,
            extra={"admin_role_id": str(admin_role.id), "categories": str(len(result.categories))},
        )
        await send_ephemeral_text(interaction, self._build_setup_success_message(result))

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
        heading = "Ticket 配置已更新。" if result.is_reconfiguration else "Ticket setup 已完成。"
        return (
            f"{heading}\n"
            f"- 管理员角色：<@&{result.config.admin_role_id}>\n"
            f"- 日志频道：<#{result.config.log_channel_id}>\n"
            f"- 归档频道：<#{result.config.archive_channel_id}>\n"
            f"- Ticket 分类：<#{result.config.ticket_category_channel_id}>\n"
            f"- 分类数量：{len(result.categories)}\n"
            f"- 分类预览：{category_summary}\n"
            f"- 结果：{created_text}"
        )

async def setup(bot: commands.Bot) -> None:
    try:
        bot.tree.add_command(ticket_group)
    except app_commands.CommandAlreadyRegistered:
        pass
    await bot.add_cog(AdminCog(bot))
