from __future__ import annotations

import logging
from collections.abc import Mapping
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands

from core.constants import APP_NAMESPACE


class LoggingService:
    def __init__(self, bot: commands.Bot | None, logger: logging.Logger):
        self.bot = bot
        self.logger = logger

    @classmethod
    def create(
        cls,
        *,
        bot: commands.Bot | None,
        log_file: Path,
        log_level: str,
    ) -> "LoggingService":
        logger = cls._build_logger(log_file=log_file, log_level=log_level)
        return cls(bot=bot, logger=logger)

    @staticmethod
    def _build_logger(*, log_file: Path, log_level: str) -> logging.Logger:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger(APP_NAMESPACE)
        resolved_level = getattr(logging, log_level.upper(), logging.INFO)
        logger.setLevel(resolved_level)

        formatter = logging.Formatter(
            fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        if not getattr(logger, "_ticket_bot_configured", False):
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            console_handler.setLevel(resolved_level)

            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=2 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            file_handler.setLevel(resolved_level)

            logger.addHandler(console_handler)
            logger.addHandler(file_handler)
            logger.propagate = False
            setattr(logger, "_ticket_bot_configured", True)
        else:
            for handler in logger.handlers:
                handler.setLevel(resolved_level)

        return logger

    def child(self, name: str) -> logging.Logger:
        return self.logger.getChild(name)

    def log_local_debug(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.logger.debug(message, *args, **kwargs)

    def log_local_info(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.logger.info(message, *args, **kwargs)

    def log_local_warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.logger.warning(message, *args, **kwargs)

    def log_local_exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.logger.exception(message, *args, **kwargs)

    async def send_guild_log(
        self,
        guild_id: int,
        level: str,
        title: str,
        description: str,
        *,
        channel_id: int | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> bool:
        if self.bot is None:
            self.log_local_warning("Guild log skipped because bot is not attached.")
            return False

        if channel_id is None:
            self.log_local_warning(
                "Guild log skipped because no log channel id is available yet. guild_id=%s title=%s",
                guild_id,
                title,
            )
            return False

        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception:
                self.log_local_warning(
                    "Failed to resolve guild log channel. guild_id=%s channel_id=%s",
                    guild_id,
                    channel_id,
                    exc_info=True,
                )
                return False

        if not hasattr(channel, "send"):
            self.log_local_warning(
                "Resolved guild log channel is not sendable. guild_id=%s channel_id=%s",
                guild_id,
                channel_id,
            )
            return False

        embed = discord.Embed(
            title=title,
            description=description,
            color=self._color_for_level(level),
        )
        embed.add_field(name="Level", value=level.upper(), inline=True)
        embed.add_field(name="Guild", value=str(guild_id), inline=True)

        if extra:
            for key, value in extra.items():
                embed.add_field(
                    name=str(key),
                    value=str(value)[:1024],
                    inline=False,
                )

        try:
            await channel.send(embed=embed)
            return True
        except Exception:
            self.log_local_warning(
                "Failed to send guild log. guild_id=%s channel_id=%s",
                guild_id,
                channel_id,
                exc_info=True,
            )
            return False

    async def send_ticket_log(
        self,
        ticket_id: str,
        guild_id: int,
        level: str,
        title: str,
        description: str,
        *,
        channel_id: int | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> bool:
        merged_extra = dict(extra or {})
        merged_extra.setdefault("ticket_id", ticket_id)
        return await self.send_guild_log(
            guild_id=guild_id,
            level=level,
            title=title,
            description=description,
            channel_id=channel_id,
            extra=merged_extra,
        )

    @staticmethod
    def _color_for_level(level: str) -> discord.Color:
        normalized = level.strip().lower()
        if normalized in {"error", "critical"}:
            return discord.Color.red()
        if normalized == "warning":
            return discord.Color.orange()
        if normalized == "success":
            return discord.Color.green()
        return discord.Color.blurple()
