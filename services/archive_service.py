from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any
from collections.abc import AsyncIterator

from core.enums import TicketStatus
from core.errors import TicketNotFoundError
from core.models import TicketRecord
from db.connection import DatabaseManager
from db.repositories.base import utc_now_iso
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository
from runtime.locks import LockManager
from services.archive_render_service import ArchiveRenderResult, ArchiveRenderService
from services.archive_send_service import ArchiveSendService
from services.capacity_service import CapacityService
from services.cleanup_service import CleanupService
from services.logging_service import LoggingService
from services.queue_service import QueueService


@dataclass(frozen=True, slots=True)
class ArchivePipelineResult:
    ticket: TicketRecord
    archive_message_id: int | None
    message_count: int
    archive_sent: bool
    channel_deleted: bool
    cleaned_up: bool
    final_status: TicketStatus


class ArchiveService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        bot: Any | None = None,
        guild_repository: GuildRepository | None = None,
        ticket_repository: TicketRepository | None = None,
        lock_manager: LockManager | None = None,
        render_service: ArchiveRenderService | None = None,
        send_service: ArchiveSendService | None = None,
        cleanup_service: CleanupService | None = None,
        logging_service: LoggingService | None = None,
        logger: logging.Logger | None = None,
        capacity_service: CapacityService | None = None,
        queue_service: QueueService | None = None,
    ) -> None:
        self.database = database
        self.bot = bot
        self.guild_repository = guild_repository or GuildRepository(database)
        self.ticket_repository = ticket_repository or TicketRepository(database)
        self.lock_manager = lock_manager
        self.render_service = render_service or ArchiveRenderService()
        self.send_service = send_service or ArchiveSendService()
        self.cleanup_service = cleanup_service or CleanupService(database)
        self.logging_service = logging_service
        self.logger = logger or logging.getLogger(__name__)
        self.capacity_service = capacity_service or CapacityService(
            database,
            ticket_repository=self.ticket_repository,
        )
        self.queue_service = queue_service

    async def archive_ticket(
        self,
        ticket_id: str,
        *,
        reference_time: datetime | None = None,
        allow_retry_from_failed: bool = False,
        ignore_due_time: bool = False,
        force_fallback: bool = False,
    ) -> ArchivePipelineResult | None:
        current_ticket = self.ticket_repository.get_by_ticket_id(ticket_id)
        if current_ticket is None:
            raise TicketNotFoundError("当前 ticket 不存在，无法继续归档。")

        async with self._acquire_ticket_lock(ticket_id):
            ticket = self.ticket_repository.get_by_ticket_id(ticket_id)
            if ticket is None:
                raise TicketNotFoundError("当前 ticket 不存在，无法继续归档。")

            effective_reference_time = self._to_utc_datetime(reference_time)
            if ticket.status is TicketStatus.DONE:
                return self._build_result(ticket, archive_sent=True, channel_deleted=True, cleaned_up=True)
            if ticket.status is TicketStatus.ARCHIVE_FAILED and not allow_retry_from_failed:
                return self._build_result(ticket)

            if ticket.status is TicketStatus.ARCHIVE_FAILED and allow_retry_from_failed:
                ticket = (
                    self.ticket_repository.update(
                        ticket.ticket_id,
                        status=TicketStatus.ARCHIVING,
                        archive_last_error=None,
                    )
                    or ticket
                )

            if ticket.status is TicketStatus.CLOSING:
                ticket = self._advance_closing_to_archiving_if_due(
                    ticket,
                    reference_time=effective_reference_time,
                    ignore_due_time=ignore_due_time,
                )
                if ticket is None:
                    return None

            archive_sent = ticket.archive_message_id is not None
            channel_deleted = ticket.status in {TicketStatus.CHANNEL_DELETED, TicketStatus.DONE}
            cleaned_up = ticket.status is TicketStatus.DONE

            if ticket.status is TicketStatus.ARCHIVING:
                ticket = await self._ensure_archive_materialized(ticket, force_fallback=force_fallback)
                archive_sent = archive_sent or ticket.archive_message_id is not None
                if ticket.status is TicketStatus.ARCHIVE_FAILED:
                    await self._trigger_queue_fill(ticket.guild_id)
                    return self._build_result(ticket, archive_sent=archive_sent)

            if ticket.status is TicketStatus.ARCHIVE_SENT:
                ticket, deleted_now = await self._ensure_channel_deleted(ticket)
                channel_deleted = channel_deleted or deleted_now

            if ticket.status is TicketStatus.CHANNEL_DELETED:
                ticket, cleaned_now = self._ensure_cleanup_completed(ticket)
                cleaned_up = cleaned_up or cleaned_now

            return self._build_result(
                ticket,
                archive_sent=archive_sent,
                channel_deleted=channel_deleted,
                cleaned_up=cleaned_up,
            )

    def _advance_closing_to_archiving_if_due(
        self,
        ticket: TicketRecord,
        *,
        reference_time: datetime,
        ignore_due_time: bool,
    ) -> TicketRecord | None:
        if not ignore_due_time and not self._is_due_for_archiving(ticket, reference_time):
            return None
        return (
            self.ticket_repository.update(
                ticket.ticket_id,
                status=TicketStatus.ARCHIVING,
                archive_last_error=None,
            )
            or ticket
        )

    async def _ensure_archive_materialized(
        self,
        ticket: TicketRecord,
        *,
        force_fallback: bool,
    ) -> TicketRecord:
        if ticket.archive_message_id is not None:
            return (
                self.ticket_repository.update(
                    ticket.ticket_id,
                    status=TicketStatus.ARCHIVE_SENT,
                    archive_last_error=None,
                )
                or ticket
            )

        archive_channel = await self._resolve_archive_channel(ticket)
        if archive_channel is None:
            return await self._mark_archive_failed(ticket, reason="archive channel is unavailable")

        source_channel = None if force_fallback else await self._resolve_channel(ticket.channel_id)
        render_result = await self._render_archive_material(
            ticket,
            source_channel=source_channel,
            force_fallback=force_fallback,
        )
        if render_result is None:
            return self.ticket_repository.get_by_ticket_id(ticket.ticket_id) or ticket

        archived_at = utc_now_iso()
        try:
            archive_message = await self.send_service.send_archive(
                archive_channel,
                ticket=replace(
                    ticket,
                    status=TicketStatus.ARCHIVE_SENT,
                    archived_at=archived_at,
                    message_count=render_result.message_count,
                ),
                transcript_path=render_result.transcript_path,
                transcript_filename=render_result.transcript_filename,
            )
        except Exception as exc:
            return await self._mark_archive_failed(ticket, reason=f"archive send failed: {exc}")

        return (
            self.ticket_repository.update(
                ticket.ticket_id,
                status=TicketStatus.ARCHIVE_SENT,
                archive_message_id=getattr(archive_message, "id", None),
                archive_last_error=None,
                archived_at=archived_at,
                message_count=render_result.message_count,
            )
            or ticket
        )

    async def _render_archive_material(
        self,
        ticket: TicketRecord,
        *,
        source_channel: Any | None,
        force_fallback: bool,
    ) -> ArchiveRenderResult | None:
        live_failure_reason: str | None = None
        if source_channel is not None and not force_fallback:
            try:
                return await self.render_service.render_live_transcript(
                    ticket=ticket,
                    channel=source_channel,
                )
            except Exception as exc:
                live_failure_reason = f"live render failed: {exc}"
                self.logger.warning(
                    "Live archive render failed; fallback will be attempted. ticket_id=%s",
                    ticket.ticket_id,
                    exc_info=True,
                )
        else:
            live_failure_reason = "source channel unavailable" if not force_fallback else "forced snapshot fallback"

        try:
            render_result = await self.render_service.render_fallback_transcript(ticket=ticket)
        except Exception as exc:
            reason = f"{live_failure_reason}; fallback render failed: {exc}" if live_failure_reason else f"fallback render failed: {exc}"
            await self._send_ticket_log(
                ticket,
                level="warning",
                title="Archive fallback attempt failed",
                description="无法使用 snapshots fallback transcript 补偿当前归档。",
                extra={"reason": reason},
            )
            await self._mark_archive_failed(ticket, reason=reason)
            return None

        await self._send_ticket_log(
            ticket,
            level="warning",
            title="Archive fallback transcript used",
            description="当前归档已降级为 snapshots fallback transcript。",
            extra={
                "reason": live_failure_reason or "live channel unavailable",
                "render_mode": render_result.render_mode,
                "message_count": render_result.message_count,
            },
        )
        return render_result

    async def _ensure_channel_deleted(self, ticket: TicketRecord) -> tuple[TicketRecord, bool]:
        source_channel, channel_missing = await self._resolve_channel_for_deletion(ticket.channel_id)
        if source_channel is not None:
            delete = getattr(source_channel, "delete", None)
            if delete is None:
                return ticket, False
            try:
                await delete(reason=f"Archive completed for ticket {ticket.ticket_id}")
            except Exception as exc:
                if self._is_channel_not_found(exc):
                    channel_missing = True
                else:
                    self.logger.warning(
                        "Failed to delete archived ticket channel. ticket_id=%s",
                        ticket.ticket_id,
                        exc_info=True,
                    )
                    return ticket, False
        elif not channel_missing:
            self.logger.warning(
                "Skipping channel deletion because source channel could not be resolved. ticket_id=%s channel_id=%s",
                ticket.ticket_id,
                ticket.channel_id,
            )
            return ticket, False

        updated_ticket = self.ticket_repository.update(ticket.ticket_id, status=TicketStatus.CHANNEL_DELETED) or ticket
        await self._trigger_queue_fill(ticket.guild_id)
        return updated_ticket, True

    def _ensure_cleanup_completed(self, ticket: TicketRecord) -> tuple[TicketRecord, bool]:
        try:
            self.cleanup_service.cleanup_ticket(ticket)
        except Exception:
            self.logger.warning(
                "Cleanup failed after ticket channel deletion. ticket_id=%s",
                ticket.ticket_id,
                exc_info=True,
            )
            return ticket, False

        updated_ticket = (
            self.ticket_repository.update(
                ticket.ticket_id,
                status=TicketStatus.DONE,
                status_before=None,
                close_execute_at=None,
                transfer_target_category=None,
                transfer_initiated_by=None,
                transfer_reason=None,
                transfer_execute_at=None,
                transfer_history_json="[]",
                staff_panel_message_id=None,
                priority_before_sleep=None,
                archive_last_error=None,
            )
            or ticket
        )
        return updated_ticket, True

    async def _resolve_archive_channel(self, ticket: TicketRecord) -> Any | None:
        config = self.guild_repository.get_config(ticket.guild_id)
        archive_channel_id = getattr(config, "archive_channel_id", None) if config is not None else None
        return await self._resolve_channel(archive_channel_id)

    async def _resolve_channel(self, channel_id: int | None) -> Any | None:
        if channel_id is None or self.bot is None:
            return None

        channel = getattr(self.bot, "get_channel", lambda _channel_id: None)(channel_id)
        if channel is not None:
            return channel

        fetch_channel = getattr(self.bot, "fetch_channel", None)
        if fetch_channel is None:
            return None

        try:
            return await fetch_channel(channel_id)
        except Exception:
            self.logger.warning(
                "Failed to resolve channel. channel_id=%s",
                channel_id,
                exc_info=True,
            )
            return None

    async def _resolve_channel_for_deletion(self, channel_id: int | None) -> tuple[Any | None, bool]:
        if channel_id is None or self.bot is None:
            return None, False

        channel = getattr(self.bot, "get_channel", lambda _channel_id: None)(channel_id)
        if channel is not None:
            return channel, False

        fetch_channel = getattr(self.bot, "fetch_channel", None)
        if fetch_channel is None:
            return None, False

        try:
            return await fetch_channel(channel_id), False
        except Exception as exc:
            if self._is_channel_not_found(exc):
                return None, True
            self.logger.warning(
                "Failed to resolve channel before deletion. channel_id=%s",
                channel_id,
                exc_info=True,
            )
            return None, False

    @staticmethod
    def _is_channel_not_found(exc: Exception) -> bool:
        return getattr(exc, "status", None) == 404 or exc.__class__.__name__ == "NotFound"

    async def _mark_archive_failed(self, ticket: TicketRecord, *, reason: str) -> TicketRecord:
        attempts = int(ticket.archive_attempts or 0) + 1
        self.logger.warning("Archive failed. ticket_id=%s reason=%s", ticket.ticket_id, reason)
        updated_ticket = (
            self.ticket_repository.update(
                ticket.ticket_id,
                status=TicketStatus.ARCHIVE_FAILED,
                archive_last_error=reason,
                archive_attempts=attempts,
            )
            or ticket
        )
        await self._send_ticket_log(
            updated_ticket,
            level="error",
            title="Archive flow failed",
            description="当前 ticket 进入 archive_failed，需要恢复或人工介入。",
            extra={"reason": reason, "archive_attempts": attempts},
        )
        return updated_ticket

    async def _send_ticket_log(
        self,
        ticket: TicketRecord,
        *,
        level: str,
        title: str,
        description: str,
        extra: dict[str, Any] | None = None,
    ) -> bool:
        if self.logging_service is None:
            return False
        config = self.guild_repository.get_config(ticket.guild_id)
        channel_id = getattr(config, "log_channel_id", None) if config is not None else None
        return await self.logging_service.send_ticket_log(
            ticket_id=ticket.ticket_id,
            guild_id=ticket.guild_id,
            level=level,
            title=title,
            description=description,
            channel_id=channel_id,
            extra=extra,
        )

    @staticmethod
    def _is_due_for_archiving(ticket: TicketRecord, reference_time: datetime) -> bool:
        if ticket.close_execute_at is None:
            return False
        execute_at = ArchiveService._parse_iso_datetime(ticket.close_execute_at)
        return execute_at <= reference_time

    @staticmethod
    def _parse_iso_datetime(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _to_utc_datetime(value: datetime | None) -> datetime:
        if value is None:
            return datetime.now(timezone.utc)
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _build_result(
        self,
        ticket: TicketRecord,
        *,
        archive_sent: bool = False,
        channel_deleted: bool = False,
        cleaned_up: bool = False,
    ) -> ArchivePipelineResult:
        return ArchivePipelineResult(
            ticket=ticket,
            archive_message_id=ticket.archive_message_id,
            message_count=ticket.message_count or 0,
            archive_sent=archive_sent
            or ticket.status in {TicketStatus.ARCHIVE_SENT, TicketStatus.CHANNEL_DELETED, TicketStatus.DONE}
            or ticket.archive_message_id is not None,
            channel_deleted=channel_deleted or ticket.status in {TicketStatus.CHANNEL_DELETED, TicketStatus.DONE},
            cleaned_up=cleaned_up or ticket.status is TicketStatus.DONE,
            final_status=ticket.status,
        )

    async def _trigger_queue_fill(self, guild_id: int) -> None:
        if self.queue_service is None:
            return
        await self.queue_service.process_next_queued_ticket(guild_id)

    @asynccontextmanager
    async def _acquire_ticket_lock(self, ticket_id: str) -> AsyncIterator[None]:
        if self.lock_manager is None:
            yield
            return

        async with self.lock_manager.acquire(f"ticket-close:{ticket_id}"):
            yield
