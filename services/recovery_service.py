from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from core.enums import TicketStatus
from core.models import TicketRecord
from db.connection import DatabaseManager
from db.repositories.ticket_repository import TicketRepository
from services.archive_service import ArchivePipelineResult, ArchiveService
from services.logging_service import LoggingService

_RETRYABLE_ARCHIVE_ERROR_TOKENS = (
    "temporary",
    "timeout",
    "timed out",
    "connection reset",
    "network",
    "rate limit",
    "503",
    "500",
)


class RecoveryService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        archive_service: ArchiveService | None = None,
        ticket_repository: TicketRepository | None = None,
        logging_service: LoggingService | None = None,
        logger: logging.Logger | None = None,
        archive_retry_limit: int = 3,
    ) -> None:
        self.database = database
        self.archive_service = archive_service or ArchiveService(database)
        self.ticket_repository = ticket_repository or TicketRepository(database)
        self.logging_service = logging_service
        self.logger = logger or logging.getLogger(__name__)
        self.archive_retry_limit = archive_retry_limit

    async def recover_incomplete_archive_flows(
        self,
        *,
        reference_time: datetime | None = None,
    ) -> list[ArchivePipelineResult | None]:
        return await self.sweep_recoverable_tickets(reference_time=reference_time)

    async def sweep_recoverable_tickets(
        self,
        *,
        reference_time: datetime | None = None,
    ) -> list[ArchivePipelineResult | None]:
        effective_reference_time = self._to_utc_datetime(reference_time)
        recoverable_tickets: list[tuple[TicketRecord, dict[str, Any]]] = []

        recoverable_tickets.extend(
            (ticket, {"reference_time": effective_reference_time})
            for ticket in self.ticket_repository.list_due_close_executions(effective_reference_time.isoformat())
        )

        recoverable_tickets.extend(
            (ticket, {"reference_time": effective_reference_time})
            for ticket in self.ticket_repository.list_by_statuses(
                [
                    TicketStatus.ARCHIVING,
                    TicketStatus.ARCHIVE_SENT,
                    TicketStatus.CHANNEL_DELETED,
                    TicketStatus.ARCHIVE_FAILED,
                ]
            )
        )

        outcomes: list[ArchivePipelineResult | None] = []
        seen_ticket_ids: set[str] = set()
        for ticket, kwargs in recoverable_tickets:
            if ticket.ticket_id in seen_ticket_ids:
                continue
            seen_ticket_ids.add(ticket.ticket_id)

            if ticket.status is TicketStatus.ARCHIVE_FAILED:
                if not self._should_retry_archive_failed(ticket):
                    self.logger.info(
                        "Skipping archive_failed recovery retry. ticket_id=%s attempts=%s reason=%s",
                        ticket.ticket_id,
                        ticket.archive_attempts,
                        ticket.archive_last_error,
                    )
                    await self._send_ticket_log(
                        ticket,
                        level="warning",
                        title="Archive recovery skipped",
                        description="archive_failed 未满足自动重试条件，本轮仅记录不重试。",
                        extra={
                            "archive_attempts": ticket.archive_attempts,
                            "reason": ticket.archive_last_error or "unknown",
                        },
                    )
                    continue
                kwargs["allow_retry_from_failed"] = True

            outcomes.append(await self.archive_service.archive_ticket(ticket.ticket_id, **kwargs))
        return outcomes

    async def handle_channel_deleted(
        self,
        *,
        channel_id: int | None,
        guild_id: int | None,
    ) -> ArchivePipelineResult | None:
        if channel_id is None or guild_id is None:
            return None

        ticket = self.ticket_repository.get_by_channel_id(channel_id)
        if ticket is None or ticket.guild_id != guild_id:
            return None

        if ticket.status in {TicketStatus.CLOSING, TicketStatus.ARCHIVING}:
            await self._send_ticket_log(
                ticket,
                level="warning",
                title="Missing channel recovery triggered",
                description="检测到 ticket 频道被删除，已尝试改用 snapshots fallback transcript 补偿。",
                extra={"channel_id": channel_id, "status": ticket.status.value},
            )
            return await self.archive_service.archive_ticket(
                ticket.ticket_id,
                reference_time=self._to_utc_datetime(None),
                ignore_due_time=True,
                force_fallback=True,
            )

        if ticket.status in {TicketStatus.ARCHIVE_SENT, TicketStatus.CHANNEL_DELETED}:
            return await self.archive_service.archive_ticket(ticket.ticket_id)

        return None

    def _should_retry_archive_failed(self, ticket: TicketRecord) -> bool:
        if (ticket.archive_attempts or 0) >= self.archive_retry_limit:
            return False
        if not ticket.archive_last_error:
            return False
        normalized_error = ticket.archive_last_error.lower()
        return any(token in normalized_error for token in _RETRYABLE_ARCHIVE_ERROR_TOKENS)

    async def _send_ticket_log(
        self,
        ticket: TicketRecord,
        *,
        level: str,
        title: str,
        description: str,
        extra: dict[str, Any] | None = None,
    ) -> bool:
        logging_service = self.logging_service
        if logging_service is None:
            logging_service = getattr(self.archive_service, "logging_service", None)
        if logging_service is None:
            return False

        config = self.archive_service.guild_repository.get_config(ticket.guild_id)
        channel_id = getattr(config, "log_channel_id", None) if config is not None else None
        return await logging_service.send_ticket_log(
            ticket_id=ticket.ticket_id,
            guild_id=ticket.guild_id,
            level=level,
            title=title,
            description=description,
            channel_id=channel_id,
            extra=extra,
        )

    @staticmethod
    def _to_utc_datetime(value: datetime | None) -> datetime:
        if value is None:
            return datetime.now(timezone.utc)
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
