from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from config.env import EnvSettings, load_env_settings
from config.static import APP_NAME, BASE_DIR
from core.errors import ConfigurationError, ValidationError
from db.repositories.guild_repository import GuildRepository
from services.bootstrap_service import BootstrapResources, BootstrapService
from services.panel_service import PanelService


class TicketBot(commands.Bot):
    def __init__(self, settings: EnvSettings):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.messages = True
        intents.message_content = True

        bot_kwargs = self._build_bot_kwargs(settings=settings, intents=intents)
        super().__init__(**bot_kwargs)
        self.settings = settings
        self.bootstrap_service = BootstrapService(settings=settings, bot=self)
        self.resources: BootstrapResources | None = None

    @staticmethod
    def _build_bot_kwargs(*, settings: EnvSettings, intents: discord.Intents) -> dict[str, Any]:
        bot_kwargs: dict[str, Any] = {
            "command_prefix": settings.bot_prefix,
            "intents": intents,
        }
        if settings.application_id is not None:
            bot_kwargs["application_id"] = settings.application_id
        return bot_kwargs

    async def setup_hook(self) -> None:
        self.resources = await self.bootstrap_service.bootstrap()
        self.tree.on_error = self._on_tree_error
        await self._load_extensions()
        await self._restore_active_panel_views()

        if self.settings.auto_sync_commands:
            synced_commands = await self.tree.sync()

            self.resources.logging_service.log_local_info(
                "Application commands synced: %s",
                len(synced_commands),
            )

    async def close(self) -> None:
        await self.bootstrap_service.shutdown()
        await super().close()

    async def on_ready(self) -> None:
        if self.resources is None:
            return
        user_text = str(self.user) if self.user is not None else "unknown"
        user_id = self.user.id if self.user is not None else "unknown"
        self.resources.logging_service.log_local_info(
            "%s is ready as %s (%s).",
            APP_NAME,
            user_text,
            user_id,
        )

        outcomes = await self.resources.draft_timeout_service.sweep_expired_drafts()
        if outcomes:
            self.resources.logging_service.log_local_info(
                "Recovered %s expired draft ticket(s) on ready.",
                len(outcomes),
            )

    async def on_message(self, message: discord.Message) -> None:
        if self.resources is not None:
            for handler in (
                self.resources.sleep_service.handle_message,
                self.resources.draft_timeout_service.handle_message,
                self.resources.snapshot_service.handle_message,
            ):
                try:
                    await handler(message)
                except Exception as exc:  # noqa: PERF203
                    self.resources.logging_service.log_local_warning(
                        "on_message handler failed: %s",
                        handler.__qualname__,
                        exc_info=True,
                    )
                    guild_id = getattr(message.guild, "id", None)
                    await self.resources.logging_service.send_guild_log(
                        guild_id or 0,
                        "warning",
                        "消息处理器崩溃",
                        f"on_message 处理器 `{handler.__qualname__}` 崩溃：{exc}",
                        channel_id=self._get_log_channel_id(guild_id),
                    )

        await self.process_commands(message)

    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if self.resources is None:
            return
        await self.resources.snapshot_service.handle_message_edit(before, after)

    async def on_message_delete(self, message: discord.Message) -> None:
        if self.resources is None:
            return
        await self.resources.snapshot_service.handle_message_delete(message)

    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        if self.resources is None:
            return
        await self.resources.snapshot_service.handle_raw_message_edit(payload)

    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if self.resources is None:
            return
        await self.resources.snapshot_service.handle_raw_message_delete(payload)

    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        if self.resources is None:
            return
        recovery_service = getattr(self.resources, "recovery_service", None)
        if recovery_service is None:
            return
        await recovery_service.handle_channel_deleted(
            channel_id=getattr(channel, "id", None),
            guild_id=getattr(getattr(channel, "guild", None), "id", None),
        )

    async def _on_tree_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        original = error.__cause__ if isinstance(error, app_commands.CommandInvokeError) else error

        if isinstance(original, discord.Forbidden):
            user_message = f"Discord 权限不足：{original.text}"
        elif isinstance(original, discord.HTTPException):
            user_message = f"Discord API 错误（{original.status}）：{original.text}"
        else:
            user_message = "处理命令时发生未知错误，如问题持续出现，请联系开发者。"

        logger = logging.getLogger(__name__)
        logger.warning("Unhandled app command error: %s", original, exc_info=original)

        if self.resources is not None:
            guild_id = getattr(interaction.guild, "id", None) if interaction.guild else None
            command_name = getattr(interaction.command, "qualified_name", "unknown")
            await self.resources.logging_service.send_guild_log(
                guild_id or 0,
                "error",
                "未处理的命令错误",
                f"命令 `/{command_name}` 执行出错：{original}\n如持续出现，请联系开发者排查。",
                channel_id=self._get_log_channel_id(guild_id),
                extra={"user_id": str(interaction.user.id), "error_type": type(original).__name__},
            )

        await self._safe_send_error(interaction, user_message)

    @staticmethod
    async def _safe_send_error(interaction: discord.Interaction, content: str) -> None:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(content, ephemeral=True)
            else:
                await interaction.response.send_message(content, ephemeral=True)
        except discord.HTTPException:
            pass

    def _get_log_channel_id(self, guild_id: int | None) -> int | None:
        if self.resources is None or guild_id is None:
            return None
        config = GuildRepository(self.resources.database).get_config(guild_id)
        return getattr(config, "log_channel_id", None) if config else None

    async def _load_extensions(self) -> None:
        cogs_dir = BASE_DIR / "cogs"
        extension_names = sorted(f"cogs.{path.stem}" for path in cogs_dir.glob("*_cog.py") if path.is_file())

        if self.resources is not None:
            self.resources.logging_service.log_local_info(
                "Discovered %s extension(s).",
                len(extension_names),
            )

        for extension_name in extension_names:
            await self.load_extension(extension_name)
            if self.resources is not None:
                self.resources.logging_service.log_local_info(
                    "Loaded extension: %s",
                    extension_name,
                )

    async def _restore_active_panel_views(self) -> int:
        if self.resources is None:
            return 0

        panel_service = PanelService(self.resources.database, bot=self, logging_service=self.resources.logging_service)
        restored_count = 0

        for panel in panel_service.list_active_panels():
            try:
                view = panel_service.build_persistent_public_panel_view(panel)
            except ValidationError as exc:
                self.resources.logging_service.log_local_warning(
                    "Skipped restoring active panel view. guild_id=%s panel_id=%s reason=%s",
                    panel.guild_id,
                    panel.panel_id,
                    exc,
                )
                await self.resources.logging_service.send_guild_log(
                    panel.guild_id,
                    "warning",
                    "面板视图恢复失败",
                    f"启动时恢复面板视图失败：{exc}",
                    channel_id=self._get_log_channel_id(panel.guild_id),
                    extra={"panel_id": str(panel.panel_id)},
                )
                continue

            self.add_view(view, message_id=panel.message_id)
            restored_count += 1

        self.resources.logging_service.log_local_info(
            "Restored %s active panel persistent view(s).",
            restored_count,
        )
        return restored_count


async def main() -> None:
    settings = load_env_settings()
    if not settings.discord_bot_token:
        raise ConfigurationError("请在 .env 中配置 DISCORD_BOT_TOKEN。")

    bot = TicketBot(settings)
    async with bot:
        await bot.start(settings.discord_bot_token, reconnect=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ConfigurationError as exc:
        raise SystemExit(f"配置错误: {exc}") from exc
    except KeyboardInterrupt:
        pass
