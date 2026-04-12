from __future__ import annotations

from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from cogs.ticket_command_groups import panel_group
from core.errors import PermissionDeniedError, ValidationError
from services.guild_config_service import GuildConfigService
from services.panel_service import PanelPublishResult, PanelRemovalResult, PanelService


class PanelCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        resources = getattr(bot, "resources", None)
        if resources is None:
            raise RuntimeError("Bot resources 尚未初始化，无法加载 PanelCog。")

        self.bot = bot
        self.logging_service = resources.logging_service
        self.guild_config_service = GuildConfigService(resources.database)
        self.panel_service = PanelService(resources.database, bot=bot, logging_service=resources.logging_service)

    async def cog_load(self) -> None:
        self.create_panel_command.binding = self
        self.refresh_panel_command.binding = self
        self.remove_panel_command.binding = self

    @panel_group.command(name="create", description="在当前频道发送公开 Ticket 面板")
    @app_commands.guild_only()
    async def create_panel_command(self, interaction: discord.Interaction) -> None:
        await self.create_panel_in_channel(interaction)

    @panel_group.command(name="refresh", description="刷新当前服务器的 active panel")
    @app_commands.guild_only()
    async def refresh_panel_command(self, interaction: discord.Interaction) -> None:
        await self.refresh_panel(interaction)

    @panel_group.command(name="remove", description="移除当前服务器的 active panel")
    @app_commands.guild_only()
    @app_commands.describe(delete_message="是否同时删除 Discord 中的面板消息")
    async def remove_panel_command(
        self,
        interaction: discord.Interaction,
        delete_message: bool = False,
    ) -> None:
        await self.remove_panel(interaction, delete_message=delete_message)

    async def create_panel_in_channel(self, interaction: discord.Interaction) -> None:
        try:
            guild = self._require_guild(interaction)
            await self._ensure_panel_permission(interaction)
            channel = self._require_sendable_channel(interaction)
            result = await self.panel_service.create_panel(channel, created_by=interaction.user.id)
        except (PermissionDeniedError, ValidationError, discord.HTTPException) as exc:
            await self._send_ephemeral(interaction, str(exc))
            return

        self.logging_service.log_local_info(
            "Panel created. guild_id=%s panel_id=%s message_id=%s",
            guild.id,
            result.record.panel_id,
            result.record.message_id,
        )
        config = self.guild_config_service.get_config(guild.id)
        await self.logging_service.send_guild_log(
            guild.id, "info", "面板已创建",
            f"管理员 <@{interaction.user.id}> 创建了公开面板。",
            channel_id=getattr(config, "log_channel_id", None) if config else None,
            extra={"panel_id": str(result.record.panel_id)},
        )
        await self._send_ephemeral(interaction, self._build_create_success_message(result))

    async def refresh_panel(self, interaction: discord.Interaction) -> None:
        try:
            guild = self._require_guild(interaction)
            await self._ensure_panel_permission(interaction)
            result = await self.panel_service.refresh_active_panel(guild.id)
        except (PermissionDeniedError, ValidationError, discord.HTTPException) as exc:
            await self._send_ephemeral(interaction, str(exc))
            return

        self.logging_service.log_local_info(
            "Panel refreshed. guild_id=%s panel_id=%s message_id=%s",
            guild.id,
            result.record.panel_id,
            result.record.message_id,
        )
        config = self.guild_config_service.get_config(guild.id)
        await self.logging_service.send_guild_log(
            guild.id, "info", "面板已刷新",
            f"管理员 <@{interaction.user.id}> 刷新了公开面板。",
            channel_id=getattr(config, "log_channel_id", None) if config else None,
            extra={"panel_id": str(result.record.panel_id)},
        )
        await self._send_ephemeral(
            interaction,
            f"已刷新 active panel，消息 ID：{result.record.message_id}。",
        )

    async def remove_panel(
        self,
        interaction: discord.Interaction,
        *,
        delete_message: bool = False,
    ) -> None:
        try:
            guild = self._require_guild(interaction)
            await self._ensure_panel_permission(interaction)
            result = await self.panel_service.remove_active_panel(
                guild.id,
                delete_message=delete_message,
            )
        except (PermissionDeniedError, ValidationError, discord.HTTPException) as exc:
            await self._send_ephemeral(interaction, str(exc))
            return

        self.logging_service.log_local_info(
            "Panel removed. guild_id=%s panel_id=%s deleted=%s",
            guild.id,
            result.record.panel_id,
            result.message_deleted,
        )
        config = self.guild_config_service.get_config(guild.id)
        await self.logging_service.send_guild_log(
            guild.id, "info", "面板已移除",
            f"管理员 <@{interaction.user.id}> 移除了公开面板。",
            channel_id=getattr(config, "log_channel_id", None) if config else None,
            extra={"panel_id": str(result.record.panel_id), "message_deleted": str(result.message_deleted)},
        )
        await self._send_ephemeral(interaction, self._build_remove_success_message(result))

    async def _ensure_panel_permission(self, interaction: discord.Interaction) -> None:
        if await self.bot.is_owner(interaction.user):
            return

        guild = self._require_guild(interaction)
        member = interaction.user
        permissions = getattr(member, "guild_permissions", None)
        if permissions is not None and permissions.administrator:
            return

        config = self.guild_config_service.get_config(guild.id)
        if config is None or config.admin_role_id is None:
            raise PermissionDeniedError("当前服务器尚未完成 Ticket setup，无法管理面板。")

        role_ids = {role.id for role in getattr(member, "roles", [])}
        if config.admin_role_id in role_ids:
            return

        raise PermissionDeniedError("只有 Ticket 管理员角色或 Bot 所有者可以管理面板。")

    @staticmethod
    def _require_guild(interaction: discord.Interaction) -> Any:
        if interaction.guild is None:
            raise ValidationError("该命令只能在服务器中使用。")
        return interaction.guild

    @staticmethod
    def _require_sendable_channel(interaction: discord.Interaction) -> Any:
        channel = interaction.channel
        if channel is None or not hasattr(channel, "send") or getattr(channel, "guild", None) is None:
            raise ValidationError("当前频道不支持发送公开 Ticket 面板。")
        return channel

    @staticmethod
    def _build_create_success_message(result: PanelPublishResult) -> str:
        return (
            "公开 Ticket 面板已创建。\n"
            f"- panel_id：{result.record.panel_id}\n"
            f"- message_id：{result.record.message_id}\n"
            f"- nonce：{result.record.nonce}"
        )

    @staticmethod
    def _build_remove_success_message(result: PanelRemovalResult) -> str:
        deleted_text = "并已删除原消息。" if result.message_deleted else "原消息保留但已失效。"
        return f"已移除 active panel，{deleted_text}"

    @staticmethod
    async def _send_ephemeral(interaction: discord.Interaction, content: str) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
            return
        await interaction.response.send_message(content, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PanelCog(bot))
