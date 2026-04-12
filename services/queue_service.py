from __future__ import annotations

import logging
import sqlite3
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

import discord

from core.enums import TicketStatus
from core.errors import TicketNotFoundError, ValidationError
from core.models import TicketRecord
from db.connection import DatabaseManager
from db.repositories.base import utc_now_iso
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository
from runtime.locks import LockManager
from services.capacity_service import CapacityService
from services.snapshot_service import SnapshotService


@dataclass(frozen=True, slots=True)
class QueueTicketResult:
    ticket: TicketRecord
    position: int


@dataclass(frozen=True, slots=True)
class QueueProcessResult:
    ticket: TicketRecord
    action: str
    position: int | None = None


@dataclass(frozen=True, slots=True)
class _QueueResolutionResult:
    value: Any | None
    outcome: str


class QueueService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        bot: Any | None = None,
        guild_repository: GuildRepository | None = None,
        ticket_repository: TicketRepository | None = None,
        capacity_service: CapacityService | None = None,
        lock_manager: LockManager | None = None,
        snapshot_service: SnapshotService | None = None,
        logging_service: Any | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.database = database
        self.bot = bot
        self.logging_service = logging_service
        self.guild_repository = guild_repository or GuildRepository(database)
        self.ticket_repository = ticket_repository or TicketRepository(database)
        self.capacity_service = capacity_service or CapacityService(
            database,
            ticket_repository=self.ticket_repository,
        )
        self.lock_manager = lock_manager
        self.snapshot_service = snapshot_service
        self.logger = logger or logging.getLogger(__name__)

    def enqueue_ticket(
        self,
        ticket_id: str,
        *,
        queued_at: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> QueueTicketResult:
        connection_context = nullcontext(connection) if connection is not None else self.database.session()
        with connection_context as current_connection:
            ticket = self.ticket_repository.get_by_ticket_id(
                ticket_id,
                connection=current_connection,
            )
            if ticket is None:
                raise TicketNotFoundError("当前 ticket 不存在，无法加入队列。")

            if ticket.status is TicketStatus.QUEUED:
                position = self.get_queue_position(
                    ticket.ticket_id,
                    connection=current_connection,
                )
                return QueueTicketResult(ticket=ticket, position=position or 1)

            queued_timestamp = queued_at or utc_now_iso()
            queued_ticket = (
                self.ticket_repository.update(
                    ticket.ticket_id,
                    status=TicketStatus.QUEUED,
                    queued_at=queued_timestamp,
                    connection=current_connection,
                )
                or ticket
            )
            position = self.get_queue_position(
                queued_ticket.ticket_id,
                connection=current_connection,
            )
            return QueueTicketResult(ticket=queued_ticket, position=position or 1)

    def get_queue_position(
        self,
        ticket_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> int | None:
        ticket = self.ticket_repository.get_by_ticket_id(
            ticket_id,
            connection=connection,
        )
        if ticket is None or ticket.status is not TicketStatus.QUEUED:
            return None

        queued_tickets = self.ticket_repository.list_queued_by_guild(
            ticket.guild_id,
            connection=connection,
        )
        for index, queued_ticket in enumerate(queued_tickets, start=1):
            if queued_ticket.ticket_id == ticket_id:
                return index
        return None

    async def sweep_queued_tickets(self) -> list[QueueProcessResult]:
        queued_tickets = self.ticket_repository.list_by_statuses([TicketStatus.QUEUED])
        outcomes: list[QueueProcessResult] = []
        processed_guild_ids: set[int] = set()

        for ticket in queued_tickets:
            if ticket.guild_id in processed_guild_ids:
                continue
            processed_guild_ids.add(ticket.guild_id)
            outcome = await self.process_next_queued_ticket(ticket.guild_id)
            if outcome is not None:
                outcomes.append(outcome)

        return outcomes

    async def process_next_queued_ticket(self, guild_id: int) -> QueueProcessResult | None:
        if self.bot is None:
            return None

        config = self.guild_repository.get_config(guild_id)
        if config is None or not config.is_initialized:
            return None

        capacity = self.capacity_service.build_snapshot(
            guild_id=guild_id,
            max_open_tickets=config.max_open_tickets,
        )
        if not capacity.has_capacity:
            return None

        queued_tickets = self.ticket_repository.list_queued_by_guild(guild_id)
        abandoned_ticket: TicketRecord | None = None
        abandoned_position: int | None = None

        for position, ticket in enumerate(queued_tickets, start=1):
            channel_resolution = await self._resolve_channel(ticket.channel_id)
            if channel_resolution.outcome == "deferred":
                self.logger.warning(
                    "Queued ticket promotion deferred because source channel could not be resolved yet. ticket_id=%s channel_id=%s",
                    ticket.ticket_id,
                    ticket.channel_id,
                )
                await self._send_ticket_log(
                    ticket.ticket_id, guild_id, "warning", "队列提升已延迟",
                    f"排队 ticket 推进延迟：无法解析频道 {ticket.channel_id}",
                    log_channel_id=getattr(config, "log_channel_id", None),
                )
                return self._build_abandoned_result(abandoned_ticket, abandoned_position)

            channel = channel_resolution.value
            if channel is None:
                abandoned_ticket = self._mark_abandoned(
                    ticket,
                    reason="source channel is unavailable while promoting queued ticket",
                )
                abandoned_position = position
                await self._send_ticket_log(
                    ticket.ticket_id, guild_id, "warning", "排队工单已废弃",
                    f"排队 ticket 已废弃：频道不存在 (channel_id={ticket.channel_id})",
                    log_channel_id=getattr(config, "log_channel_id", None),
                )
                continue

            guild = getattr(channel, "guild", None)
            creator_resolution = await self._resolve_guild_member(guild, ticket.creator_id)
            if creator_resolution.outcome == "deferred":
                self.logger.warning(
                    "Queued ticket promotion deferred because creator could not be resolved yet. ticket_id=%s creator_id=%s",
                    ticket.ticket_id,
                    ticket.creator_id,
                )
                await self._send_ticket_log(
                    ticket.ticket_id, guild_id, "warning", "队列提升已延迟",
                    f"排队 ticket 推进延迟：无法解析创建者 (creator_id={ticket.creator_id})",
                    log_channel_id=getattr(config, "log_channel_id", None),
                )
                return self._build_abandoned_result(abandoned_ticket, abandoned_position)

            if creator_resolution.value is None:
                await self._try_delete_channel(
                    channel,
                    reason=f"Abandon queued ticket {ticket.ticket_id} because creator is unavailable",
                )
                abandoned_ticket = self._mark_abandoned(
                    ticket,
                    reason="creator is unavailable while promoting queued ticket",
                )
                abandoned_position = position
                await self._send_ticket_log(
                    ticket.ticket_id, guild_id, "warning", "排队工单已废弃",
                    f"排队 ticket 已废弃：创建者不可用 (creator_id={ticket.creator_id})",
                    log_channel_id=getattr(config, "log_channel_id", None),
                )
                continue

            submit_service = self._build_submit_service()
            try:
                result = await submit_service.promote_queued_ticket(
                    channel,
                    ticket_id=ticket.ticket_id,
                )
            except ValidationError:
                self.logger.warning(
                    "Queued ticket promotion is deferred because validation failed. ticket_id=%s",
                    ticket.ticket_id,
                    exc_info=True,
                )
                await self._send_ticket_log(
                    ticket.ticket_id, guild_id, "warning", "队列提升已延迟",
                    "排队 ticket 推进延迟：验证失败。",
                    log_channel_id=getattr(config, "log_channel_id", None),
                )
                return None
            except Exception as exc:
                self.logger.exception(
                    "Failed to promote queued ticket. ticket_id=%s",
                    ticket.ticket_id,
                )
                await self._send_ticket_log(
                    ticket.ticket_id, guild_id, "warning", "队列提升失败",
                    f"排队 ticket 推进失败：{exc}",
                    log_channel_id=getattr(config, "log_channel_id", None),
                )
                return None

            if result is None:
                return None

            return QueueProcessResult(
                ticket=result.ticket,
                action="promoted",
                position=position,
            )

        if abandoned_ticket is not None:
            return QueueProcessResult(
                ticket=abandoned_ticket,
                action="abandoned",
                position=abandoned_position,
            )
        return None

    @staticmethod
    def _build_abandoned_result(
        ticket: TicketRecord | None,
        position: int | None,
    ) -> QueueProcessResult | None:
        if ticket is None:
            return None
        return QueueProcessResult(ticket=ticket, action="abandoned", position=position)

    def _build_submit_service(self):
        from services.submit_service import SubmitService

        return SubmitService(
            self.database,
            ticket_repository=self.ticket_repository,
            lock_manager=self.lock_manager,
            capacity_service=self.capacity_service,
            queue_service=self,
            snapshot_service=self.snapshot_service,
        )

    async def _resolve_channel(self, channel_id: int | None) -> _QueueResolutionResult:
        if channel_id is None:
            return _QueueResolutionResult(value=None, outcome="missing")
        if self.bot is None:
            return _QueueResolutionResult(value=None, outcome="deferred")

        get_channel = getattr(self.bot, "get_channel", None)
        if callable(get_channel):
            channel = get_channel(channel_id)
            if channel is not None:
                return _QueueResolutionResult(value=channel, outcome="resolved")

        fetch_channel = getattr(self.bot, "fetch_channel", None)
        if not callable(fetch_channel):
            return _QueueResolutionResult(value=None, outcome="deferred")

        try:
            channel = await fetch_channel(channel_id)
        except discord.NotFound:
            return _QueueResolutionResult(value=None, outcome="missing")
        except discord.HTTPException:
            self.logger.warning(
                "Failed to fetch queued ticket channel due to a temporary Discord error. channel_id=%s",
                channel_id,
                exc_info=True,
            )
            return _QueueResolutionResult(value=None, outcome="deferred")
        except Exception:
            self.logger.exception(
                "Failed to fetch queued ticket channel due to an unexpected error. channel_id=%s",
                channel_id,
            )
            return _QueueResolutionResult(value=None, outcome="deferred")

        if channel is None:
            return _QueueResolutionResult(value=None, outcome="missing")
        return _QueueResolutionResult(value=channel, outcome="resolved")

    async def _resolve_guild_member(self, guild: Any, user_id: int) -> _QueueResolutionResult:
        if guild is None:
            return _QueueResolutionResult(value=None, outcome="deferred")

        get_member = getattr(guild, "get_member", None)
        if callable(get_member):
            member = get_member(user_id)
            if member is not None:
                return _QueueResolutionResult(value=member, outcome="resolved")

        fetch_member = getattr(guild, "fetch_member", None)
        if not callable(fetch_member):
            return _QueueResolutionResult(value=None, outcome="deferred")

        try:
            member = await fetch_member(user_id)
        except discord.NotFound:
            return _QueueResolutionResult(value=None, outcome="missing")
        except discord.HTTPException:
            self.logger.warning(
                "Failed to fetch queued ticket creator due to a temporary Discord error. guild_id=%s user_id=%s",
                getattr(guild, "id", None),
                user_id,
                exc_info=True,
            )
            return _QueueResolutionResult(value=None, outcome="deferred")
        except Exception:
            self.logger.exception(
                "Failed to fetch queued ticket creator due to an unexpected error. guild_id=%s user_id=%s",
                getattr(guild, "id", None),
                user_id,
            )
            return _QueueResolutionResult(value=None, outcome="deferred")

        if member is None:
            return _QueueResolutionResult(value=None, outcome="missing")
        return _QueueResolutionResult(value=member, outcome="resolved")

    async def _try_delete_channel(self, channel: Any, *, reason: str) -> None:
        delete = getattr(channel, "delete", None)
        if delete is None:
            return
        try:
            await delete(reason=reason)
        except Exception as exc:
            channel_id = getattr(channel, "id", None)
            self.logger.warning(
                "Failed to delete abandoned queued ticket channel. channel_id=%s",
                channel_id,
                exc_info=True,
            )
            guild = getattr(channel, "guild", None)
            guild_id = getattr(guild, "id", None)
            if self.logging_service is not None and guild_id is not None:
                config = self.guild_repository.get_config(guild_id)
                await self.logging_service.send_guild_log(
                    guild_id, "warning", "排队工单频道删除失败",
                    f"删除废弃排队 ticket 的频道失败：{exc}",
                    channel_id=getattr(config, "log_channel_id", None) if config else None,
                    extra={"channel_id": str(channel_id)},
                )

    def _mark_abandoned(self, ticket: TicketRecord, *, reason: str) -> TicketRecord:
        self.logger.warning("Queued ticket abandoned. ticket_id=%s reason=%s", ticket.ticket_id, reason)
        return (
            self.ticket_repository.update(
                ticket.ticket_id,
                status=TicketStatus.ABANDONED,
                queued_at=None,
            )
            or ticket
        )

    async def _send_ticket_log(
        self,
        ticket_id: str,
        guild_id: int,
        level: str,
        title: str,
        description: str,
        *,
        log_channel_id: int | None = None,
        extra: dict | None = None,
    ) -> None:
        if self.logging_service is None:
            return
        await self.logging_service.send_ticket_log(
            ticket_id=ticket_id,
            guild_id=guild_id,
            level=level,
            title=title,
            description=description,
            channel_id=log_channel_id,
            extra=extra,
        )
