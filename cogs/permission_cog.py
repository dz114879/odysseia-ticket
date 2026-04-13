from __future__ import annotations

import io
import json
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from cogs.ticket_command_groups import ticket_group
from core.errors import PermissionDeniedError, ValidationError
from services.guild_config_service import GuildConfigService
from services.panel_service import PanelService
from services.permission_config_service import PermissionConfigService


class PermissionCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        resources = getattr(bot, "resources", None)
        if resources is None:
            raise RuntimeError("Bot resources 尚未初始化，无法加载 PermissionCog。")

        self.bot = bot
        self.logging_service = resources.logging_service
        self.guild_config_service = GuildConfigService(resources.database)
        self.permission_config_service = PermissionConfigService(resources.database)
        self.panel_service = PanelService(resources.database, bot=bot, logging_service=resources.logging_service)

    async def cog_load(self) -> None:
        self.permission_command.binding = self
        self.permission_help_command.binding = self

    @ticket_group.command(name="permission", description="通过上传 JSON 文件配置各分类的 staff 权限")
    @app_commands.guild_only()
    @app_commands.describe(file="包含权限配置的 JSON 文件")
    async def permission_command(
        self,
        interaction: discord.Interaction,
        file: discord.Attachment,
    ) -> None:
        await self.run_permission(interaction, file=file)

    @ticket_group.command(name="permission-help", description="获取权限配置的帮助文档和 JSON 格式说明")
    @app_commands.guild_only()
    async def permission_help_command(self, interaction: discord.Interaction) -> None:
        await self.run_permission_help(interaction)

    async def run_permission(self, interaction: discord.Interaction, *, file: Any) -> None:
        try:
            guild = self._require_guild(interaction)
            await self._ensure_permission(interaction)

            raw_bytes = await file.read()
            try:
                data = json.loads(raw_bytes.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ValidationError(f"JSON 解析失败：{exc}") from exc

            categories = self.guild_config_service.list_categories(guild.id)
            if not categories:
                raise ValidationError("当前服务器没有任何 Ticket 分类，请先执行 /ticket setup。")

            errors = self.permission_config_service.validate_permission_json(
                data, guild=guild, categories=categories,
            )
            if errors:
                error_text = "\n".join(f"- {e}" for e in errors[:15])
                raise ValidationError(f"JSON 校验失败：\n{error_text}")

            result = self.permission_config_service.apply_permission_config(guild.id, data)
        except (PermissionDeniedError, ValidationError, discord.HTTPException) as exc:
            await self._send_ephemeral(interaction, str(exc))
            return

        summary = "\n".join(result.summary_lines) if result.summary_lines else "无变更"
        response = f"权限配置已更新，共修改 {len(result.updated_categories)} 个分类：\n{summary}"

        self.logging_service.log_local_info(
            "Permission config applied. guild_id=%s updated=%s",
            guild.id,
            result.updated_categories,
        )
        config = self.guild_config_service.get_config(guild.id)
        await self.logging_service.send_guild_log(
            guild.id,
            "info",
            "权限配置已更新",
            f"管理员 <@{interaction.user.id}> 通过 JSON 更新了 staff 权限配置。",
            channel_id=getattr(config, "log_channel_id", None) if config else None,
            extra={"updated_categories": ", ".join(result.updated_categories)},
        )

        try:
            await self.panel_service.refresh_active_panel(guild.id)
        except Exception:
            pass

        await self._send_ephemeral(interaction, response)

    async def run_permission_help(self, interaction: discord.Interaction) -> None:
        try:
            guild = self._require_guild(interaction)
            await self._ensure_permission(interaction)

            config = self.guild_config_service.get_config(guild.id)
            if config is None or not config.is_initialized:
                raise ValidationError("当前服务器尚未完成 Ticket setup。")

            categories = self.guild_config_service.list_categories(guild.id)
            help_text = PermissionConfigService.build_permission_help_text(config, categories)
        except (PermissionDeniedError, ValidationError) as exc:
            await self._send_ephemeral(interaction, str(exc))
            return

        file = discord.File(
            io.BytesIO(help_text.encode("utf-8")),
            filename="permission-help.txt",
        )
        if interaction.response.is_done():
            await interaction.followup.send(file=file, ephemeral=True)
        else:
            await interaction.response.send_message(file=file, ephemeral=True)

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
            raise PermissionDeniedError("当前服务器尚未完成 Ticket setup，无法管理权限配置。")

        role_ids = {role.id for role in getattr(member, "roles", [])}
        if config.admin_role_id in role_ids:
            return

        raise PermissionDeniedError("只有 Ticket 管理员角色或 Bot 所有者可以管理权限配置。")

    @staticmethod
    def _require_guild(interaction: discord.Interaction) -> Any:
        if interaction.guild is None:
            raise ValidationError("该命令只能在服务器中使用。")
        return interaction.guild

    @staticmethod
    async def _send_ephemeral(interaction: discord.Interaction, content: str) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
            return
        await interaction.response.send_message(content, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PermissionCog(bot))
