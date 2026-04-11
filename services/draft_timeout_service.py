from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from typing import Any
from collections.abc import AsyncIterator

from core.constants import DRAFT_ABANDON_TIMEOUT_HOURS, DRAFT_INACTIVE_CLOSE_HOURS
from core.enums import TicketStatus
from core.models import TicketRecord
from db.connection import DatabaseManager
from db.repositories.base import utc_now_iso
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
        lock_manager: LockManager | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.database = database
        self.bot = bot
        self.ticket_repository = ticket_repository or TicketRepository(database)
        self.lock_manager = lock_manager
        self.logger = logger or logging.getLogger(__name__)

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

            reason = self._get_timeout_reason(ticket, reference_time)
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
    def _get_timeout_reason(ticket: TicketRecord, reference_time: datetime) -> str | None:
        created_at = DraftTimeoutService._parse_iso_datetime(ticket.created_at)
        if not ticket.has_user_message:
            if reference_time - created_at >= timedelta(hours=DRAFT_ABANDON_TIMEOUT_HOURS):
                return "draft_expired"
            return None

        last_user_message_at = DraftTimeoutService._parse_iso_datetime(ticket.last_user_message_at or ticket.created_at)
        if reference_time - last_user_message_at >= timedelta(hours=DRAFT_INACTIVE_CLOSE_HOURS):
            return "inactive_close"
        return None

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
