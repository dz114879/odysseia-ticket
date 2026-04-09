from __future__ import annotations

import asyncio
from pathlib import Path

import discord
from discord.ext import commands

from config.env import EnvSettings, load_env_settings
from config.static import APP_NAME, BASE_DIR
from core.errors import ConfigurationError
from services.bootstrap_service import BootstrapResources, BootstrapService


class TicketBot(commands.Bot):
    def __init__(self, settings: EnvSettings):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.members = True
        intents.messages = True
        intents.message_content = True

        super().__init__(
            command_prefix=settings.bot_prefix,
            intents=intents,
            application_id=settings.application_id,
        )
        self.settings = settings
        self.bootstrap_service = BootstrapService(settings=settings, bot=self)
        self.resources: BootstrapResources | None = None

    async def setup_hook(self) -> None:
        self.resources = await self.bootstrap_service.bootstrap()
        await self._load_extensions()

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

    async def _load_extensions(self) -> None:
        cogs_dir = BASE_DIR / "cogs"
        extension_names = sorted(
            f"cogs.{path.stem}"
            for path in cogs_dir.glob("*_cog.py")
            if path.is_file()
        )

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
