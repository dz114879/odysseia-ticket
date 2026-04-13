from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Any
from collections.abc import AsyncIterator

from core.constants import (
    DRAFT_ABANDON_TIMEOUT_HOURS,
    DRAFT_INACTIVE_CLOSE_HOURS,
)
from core.enums import TicketStatus
from core.models import GuildConfigRecord, TicketRecord
from db.connection import DatabaseManager
from db.repositories.base import utc_now_iso
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository
from runtime.locks import LockManager


@dataclass(frozen=True, slots=True)
class DraftTimeoutOutcome:
    ticket: TicketRecord
    reason: str
    channel_deleted: bool


class DraftTimeoutService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        bot: Any | None = None,
        ticket_repository: TicketRepository | None = None,
        guild_repository: GuildRepository | None = None,
        lock_manager: LockManager | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.database = database
        self.bot = bot
        self.ticket_repository = ticket_repository or TicketRepository(database)
        self.guild_repository = guild_repository or GuildRepository(database)
        self.lock_manager = lock_manager
        self.logger = logger or logging.getLogger(__name__)
        self._warned_ticket_ids: set[str] = set()
        self._guild_config_cache: dict[int, GuildConfigRecord | None] = {}

    async def handle_message(self, message: Any) -> TicketRecord | None:
        author = getattr(message, "author", None)
        if author is None or getattr(author, "bot", False):
            return None
        if getattr(message, "guild", None) is None or getattr(message, "channel", None) is None:
            return None

        return await self.record_user_message(
            channel_id=message.channel.id,
            author_id=author.id,
            occurred_at=getattr(message, "created_at", None),
        )

    async def record_user_message(
        self,
        *,
        channel_id: int,
        author_id: int,
        occurred_at: datetime | str | None = None,
    ) -> TicketRecord | None:
        async with self._acquire_ticket_lock(f"channel:{channel_id}"):
            ticket = self.ticket_repository.get_by_channel_id(channel_id)
            if ticket is None or ticket.status != TicketStatus.DRAFT:
                return None
            if ticket.creator_id != author_id:
                return None

            occurred_at_iso = self._normalize_timestamp(occurred_at)
            return self.ticket_repository.update(
                ticket.ticket_id,
                has_user_message=True,
                last_user_message_at=occurred_at_iso,
            )

    async def sweep_expired_drafts(
        self,
        *,
        now: datetime | str | None = None,
    ) -> list[DraftTimeoutOutcome]:
        reference_time = self._to_utc_datetime(now)
        outcomes: list[DraftTimeoutOutcome] = []
        self._guild_config_cache.clear()

        for ticket in self.ticket_repository.list_by_statuses([TicketStatus.DRAFT]):
            try:
                outcome = await self._apply_timeout_if_needed(
                    ticket.ticket_id,
                    reference_time=reference_time,
                )
            except Exception:
                self.logger.exception("Failed to process draft timeout. ticket_id=%s", ticket.ticket_id)
                continue
            if outcome is not None:
                outcomes.append(outcome)

        return outcomes

    async def sweep_draft_warnings(
        self,
        *,
        now: datetime | str | None = None,
    ) -> list[str]:
        reference_time = self._to_utc_datetime(now)
        warned: list[str] = []

        self._guild_config_cache.clear()
        for ticket in self.ticket_repository.list_by_statuses([TicketStatus.DRAFT]):
            if ticket.ticket_id in self._warned_ticket_ids:
                continue
            try:
                config = self._load_guild_config(ticket.guild_id)
                warning_msg = self._get_warning_message(ticket, reference_time, config=config)
                if warning_msg is None:
                    continue
                channel = await self._resolve_channel(ticket.channel_id)
                if channel is None:
                    continue
                send = getattr(channel, "send", None)
                if send is not None:
                    await send(content=warning_msg)
                self._warned_ticket_ids.add(ticket.ticket_id)
                warned.append(ticket.ticket_id)
            except Exception:
                self.logger.exception("Failed to send draft warning. ticket_id=%s", ticket.ticket_id)

        return warned

    @staticmethod
    def _get_warning_message(
        ticket: TicketRecord,
        reference_time: datetime,
        *,
        config: GuildConfigRecord | None = None,
    ) -> str | None:
        abandon_hours = config.draft_abandon_timeout_hours if config else DRAFT_ABANDON_TIMEOUT_HOURS
        abandon_warning_hours = abandon_hours - 1
        inactive_hours = config.draft_inactive_close_hours if config else DRAFT_INACTIVE_CLOSE_HOURS
        inactive_warning_hours = inactive_hours - 1

        created_at = DraftTimeoutService._parse_iso_datetime(ticket.created_at)
        if not ticket.has_user_message:
            elapsed = reference_time - created_at
            if timedelta(hours=abandon_warning_hours) <= elapsed < timedelta(hours=abandon_hours):
                return (
                    f"<@{ticket.creator_id}> 由于处于草稿期过久，您的 Ticket 将在 1 小时后被自动废弃。\n\n"
                    "如果您已经完成草稿，但忘记提交，请使用标注消息下的按钮，尽快提交 Ticket; "
                    "若按钮失效，也可以用 `/ticket submit` 斜杠命令提交。"
                )
            return None

        last_msg_at = DraftTimeoutService._parse_iso_datetime(ticket.last_user_message_at or ticket.created_at)
        elapsed = reference_time - last_msg_at
        if timedelta(hours=inactive_warning_hours) <= elapsed < timedelta(hours=inactive_hours):
            return f"<@{ticket.creator_id}> 由于您未发言，本 Ticket 将在 1 小时后被自动废弃。"
        return None

    async def _apply_timeout_if_needed(
        self,
        ticket_id: str,
        *,
        reference_time: datetime,
    ) -> DraftTimeoutOutcome | None:
        async with self._acquire_ticket_lock(f"ticket:{ticket_id}"):
            ticket = self.ticket_repository.get_by_ticket_id(ticket_id)
            if ticket is None or ticket.status != TicketStatus.DRAFT:
                return None

            config = self._load_guild_config(ticket.guild_id)
            reason = self._get_timeout_reason(ticket, reference_time, config=config)
            if reason is None:
                return None

            updated_ticket = (
                self.ticket_repository.update(
                    ticket.ticket_id,
                    status=TicketStatus.ABANDONED,
                )
                or ticket
            )
            channel = await self._resolve_channel(updated_ticket.channel_id)
            if channel is None:
                return DraftTimeoutOutcome(
                    ticket=updated_ticket,
                    reason=reason,
                    channel_deleted=False,
                )

            try:
                await channel.delete(reason=self._build_delete_reason(updated_ticket, reason))
            except Exception:
                self.ticket_repository.update(
                    ticket.ticket_id,
                    status=TicketStatus.DRAFT,
                    updated_at=utc_now_iso(),
                )
                raise

            return DraftTimeoutOutcome(
                ticket=updated_ticket,
                reason=reason,
                channel_deleted=True,
            )

    @staticmethod
    def _get_timeout_reason(
        ticket: TicketRecord,
        reference_time: datetime,
        *,
        config: GuildConfigRecord | None = None,
    ) -> str | None:
        abandon_hours = config.draft_abandon_timeout_hours if config else DRAFT_ABANDON_TIMEOUT_HOURS
        inactive_hours = config.draft_inactive_close_hours if config else DRAFT_INACTIVE_CLOSE_HOURS

        created_at = DraftTimeoutService._parse_iso_datetime(ticket.created_at)
        if not ticket.has_user_message:
            if reference_time - created_at >= timedelta(hours=abandon_hours):
                return "draft_expired"
            return None

        last_user_message_at = DraftTimeoutService._parse_iso_datetime(ticket.last_user_message_at or ticket.created_at)
        if reference_time - last_user_message_at >= timedelta(hours=inactive_hours):
            return "inactive_close"
        return None

    def _load_guild_config(self, guild_id: int) -> GuildConfigRecord | None:
        if guild_id in self._guild_config_cache:
            return self._guild_config_cache[guild_id]
        config = self.guild_repository.get_config(guild_id)
        self._guild_config_cache[guild_id] = config
        return config

    async def _resolve_channel(self, channel_id: int | None) -> Any | None:
        if self.bot is None or channel_id is None:
            return None

        channel = getattr(self.bot, "get_channel", lambda _: None)(channel_id)
        if channel is not None:
            return channel

        fetch_channel = getattr(self.bot, "fetch_channel", None)
        if fetch_channel is None:
            return None

        try:
            return await fetch_channel(channel_id)
        except Exception:
            return None

    @staticmethod
    def _build_delete_reason(ticket: TicketRecord, reason: str) -> str:
        if reason == "inactive_close":
            return f"Close inactive draft ticket {ticket.ticket_id}"
        return f"Expire draft ticket {ticket.ticket_id}"

    @staticmethod
    def _normalize_timestamp(value: datetime | str | None) -> str:
        if value is None:
            return utc_now_iso()
        if isinstance(value, str):
            return DraftTimeoutService._parse_iso_datetime(value).isoformat()

        normalized = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return normalized.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _to_utc_datetime(value: datetime | str | None) -> datetime:
        if value is None:
            return datetime.now(timezone.utc)
        if isinstance(value, str):
            return DraftTimeoutService._parse_iso_datetime(value)
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _parse_iso_datetime(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @asynccontextmanager
    async def _acquire_ticket_lock(self, key: str) -> AsyncIterator[None]:
        if self.lock_manager is None:
            yield
            return

        async with self.lock_manager.acquire(f"draft-timeout:{key}"):
            yield
