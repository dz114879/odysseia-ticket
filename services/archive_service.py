from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, AsyncIterator

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
            if ticket.status is TicketStatus.ARCHIVE_FAILED:
                return self._build_result(ticket)

            if ticket.status is TicketStatus.CLOSING:
                if not self._is_due_for_archiving(ticket, effective_reference_time):
                    return None
                ticket = self.ticket_repository.update(ticket.ticket_id, status=TicketStatus.ARCHIVING) or ticket

            archive_sent = ticket.archive_message_id is not None
            channel_deleted = ticket.status in {TicketStatus.CHANNEL_DELETED, TicketStatus.DONE}
            cleaned_up = ticket.status is TicketStatus.DONE

            if ticket.status is TicketStatus.ARCHIVING:
                if ticket.archive_message_id is None:
                    archive_channel = await self._resolve_archive_channel(ticket)
                    source_channel = await self._resolve_channel(ticket.channel_id)
                    if archive_channel is None or source_channel is None:
                        failed_ticket = self._mark_archive_failed(
                            ticket,
                            reason="archive channel or source channel is unavailable",
                        )
                        await self._trigger_queue_fill(ticket.guild_id)
                        return self._build_result(failed_ticket)

                    archived_at = utc_now_iso()
                    try:
                        render_result = await self.render_service.render_ticket_transcript(
                            ticket=ticket,
                            channel=source_channel,
                        )
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
                        failed_ticket = self._mark_archive_failed(
                            ticket,
                            reason=f"archive export failed: {exc}",
                        )
                        await self._trigger_queue_fill(ticket.guild_id)
                        return self._build_result(failed_ticket)

                    ticket = self.ticket_repository.update(
                        ticket.ticket_id,
                        status=TicketStatus.ARCHIVE_SENT,
                        archive_message_id=getattr(archive_message, "id", None),
                        archived_at=archived_at,
                        message_count=render_result.message_count,
                    ) or ticket
                    archive_sent = True
                else:
                    ticket = self.ticket_repository.update(
                        ticket.ticket_id,
                        status=TicketStatus.ARCHIVE_SENT,
                    ) or ticket
                    archive_sent = True

            if ticket.status is TicketStatus.ARCHIVE_SENT:
                source_channel, channel_missing = await self._resolve_channel_for_deletion(ticket.channel_id)
                if source_channel is not None:
                    delete = getattr(source_channel, "delete", None)
                    if delete is None:
                        return self._build_result(ticket, archive_sent=archive_sent)
                    try:
                        await delete(reason=f"Archive completed for ticket {ticket.ticket_id}")
                    except Exception:
                        self.logger.warning(
                            "Failed to delete archived ticket channel. ticket_id=%s",
                            ticket.ticket_id,
                            exc_info=True,
                        )
                        return self._build_result(ticket, archive_sent=archive_sent)
                elif not channel_missing:
                    self.logger.warning(
                        "Skipping channel deletion because source channel could not be resolved. ticket_id=%s channel_id=%s",
                        ticket.ticket_id,
                        ticket.channel_id,
                    )
                    return self._build_result(ticket, archive_sent=archive_sent)
                ticket = self.ticket_repository.update(ticket.ticket_id, status=TicketStatus.CHANNEL_DELETED) or ticket
                await self._trigger_queue_fill(ticket.guild_id)
                channel_deleted = True

            if ticket.status is TicketStatus.CHANNEL_DELETED:
                try:
                    self.cleanup_service.cleanup_ticket(ticket)
                except Exception:
                    self.logger.warning(
                        "Cleanup failed after ticket channel deletion. ticket_id=%s",
                        ticket.ticket_id,
                        exc_info=True,
                    )
                    return self._build_result(
                        ticket,
                        archive_sent=archive_sent,
                        channel_deleted=channel_deleted,
                    )

                ticket = self.ticket_repository.update(
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
                ) or ticket
                cleaned_up = True

            return self._build_result(
                ticket,
                archive_sent=archive_sent,
                channel_deleted=channel_deleted,
                cleaned_up=cleaned_up,
            )

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

    def _mark_archive_failed(self, ticket: TicketRecord, *, reason: str) -> TicketRecord:
        self.logger.warning("Archive failed. ticket_id=%s reason=%s", ticket.ticket_id, reason)
        return self.ticket_repository.update(ticket.ticket_id, status=TicketStatus.ARCHIVE_FAILED) or ticket

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
            archive_sent=archive_sent or ticket.archive_message_id is not None,
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
