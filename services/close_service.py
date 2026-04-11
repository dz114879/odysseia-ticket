from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

from core.constants import CLOSE_REVOKE_WINDOW_SECONDS
from core.enums import TicketStatus
from core.errors import InvalidTicketStateError, TicketNotFoundError, ValidationError
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.connection import DatabaseManager
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_mute_repository import TicketMuteRepository
from db.repositories.ticket_repository import TicketRepository
from runtime.locks import LockManager
from services.archive_service import ArchivePipelineResult, ArchiveService
from services.capacity_service import CapacityService
from services.queue_service import QueueService
from services.staff_guard_service import StaffGuardService, StaffTicketContext
from services.staff_permission_service import StaffPermissionService
from services.staff_panel_service import StaffPanelService
from discord_ui.close_embeds import build_closing_notice_embed


@dataclass(frozen=True, slots=True)
class CloseMutationResult:
    ticket: TicketRecord
    previous_status: TicketStatus
    close_execute_at: str | None
    close_reason: str | None
    requested_by_id: int | None
    changed: bool
    log_message: Any | None


@dataclass(frozen=True, slots=True)
class CloseRevokeResult:
    ticket: TicketRecord
    restored_status: TicketStatus
    close_reason: str | None
    changed: bool
    log_message: Any | None


class CloseService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        bot: Any | None = None,
        guild_repository: GuildRepository | None = None,
        ticket_repository: TicketRepository | None = None,
        ticket_mute_repository: TicketMuteRepository | None = None,
        lock_manager: LockManager | None = None,
        guard_service: StaffGuardService | None = None,
        permission_service: StaffPermissionService | None = None,
        staff_panel_service: StaffPanelService | None = None,
        archive_service: ArchiveService | None = None,
        logger: logging.Logger | None = None,
        capacity_service: CapacityService | None = None,
        queue_service: QueueService | None = None,
    ) -> None:
        self.database = database
        self.bot = bot
        self.guild_repository = guild_repository or GuildRepository(database)
        self.ticket_repository = ticket_repository or TicketRepository(database)
        self.ticket_mute_repository = ticket_mute_repository or TicketMuteRepository(database)
        self.lock_manager = lock_manager
        self.permission_service = permission_service or StaffPermissionService()
        self.staff_panel_service = staff_panel_service
        self.logger = logger or logging.getLogger(__name__)
        self.capacity_service = capacity_service or CapacityService(
            database,
            ticket_repository=self.ticket_repository,
        )
        self.queue_service = queue_service
        self.guard_service = guard_service or StaffGuardService(
            database,
            guild_repository=self.guild_repository,
            ticket_repository=self.ticket_repository,
        )
        self.archive_service = archive_service or ArchiveService(
            database,
            bot=bot,
            guild_repository=self.guild_repository,
            capacity_service=self.capacity_service,
            queue_service=self.queue_service,
            ticket_repository=self.ticket_repository,
            lock_manager=lock_manager,
            logger=self.logger.getChild("archive"),
        )

    async def initiate_close(
        self,
        channel: Any,
        *,
        actor: Any,
        reason: str | None = None,
        requested_by_id: int | None = None,
        is_bot_owner: bool = False,
    ) -> CloseMutationResult:
        channel_id = getattr(channel, "id", None)
        actor_id = getattr(actor, "id", None)
        if channel_id is None:
            raise ValidationError("当前频道不支持 ticket close。")
        if actor_id is None:
            raise ValidationError("无法识别当前操作人的身份。")

        ticket = self.ticket_repository.get_by_channel_id(channel_id)
        if ticket is None:
            raise TicketNotFoundError("当前频道不是已登记的 ticket。")

        async with self._acquire_ticket_lock(ticket.ticket_id):
            context = self._load_close_context(channel_id)
            self.guard_service.assert_staff_actor(
                actor,
                config=context.config,
                category=context.category,
                is_bot_owner=is_bot_owner,
            )

            if context.ticket.status is TicketStatus.CLOSING:
                return CloseMutationResult(
                    ticket=context.ticket,
                    previous_status=context.ticket.status_before or TicketStatus.SUBMITTED,
                    close_execute_at=context.ticket.close_execute_at,
                    close_reason=context.ticket.close_reason,
                    requested_by_id=requested_by_id,
                    changed=False,
                    log_message=None,
                )

            if context.ticket.status not in {TicketStatus.SUBMITTED, TicketStatus.SLEEP}:
                raise InvalidTicketStateError("当前 ticket 仅在 submitted / sleep 状态可关闭。")

            self._assert_sleep_capacity_available(context=context)

            normalized_reason = self._normalize_reason(reason)
            close_started_at = datetime.now(timezone.utc)
            close_execute_at = (close_started_at + timedelta(seconds=CLOSE_REVOKE_WINDOW_SECONDS)).isoformat()
            updated_ticket = (
                self.ticket_repository.update(
                    context.ticket.ticket_id,
                    status=TicketStatus.CLOSING,
                    status_before=context.ticket.status,
                    close_reason=normalized_reason,
                    close_initiated_by=actor_id,
                    close_execute_at=close_execute_at,
                    closed_at=close_started_at.isoformat(),
                )
                or context.ticket
            )

            try:
                await self._freeze_ticket_permissions(channel, context=context)
                log_message = await self._send_closing_notice(
                    channel,
                    ticket=updated_ticket,
                    initiated_by_id=actor_id,
                    requested_by_id=requested_by_id,
                )
            except Exception:
                await self._rollback_close_start(channel, context=context)
                raise

            if self.staff_panel_service is not None:
                self.staff_panel_service.request_refresh(updated_ticket.ticket_id)

            return CloseMutationResult(
                ticket=updated_ticket,
                previous_status=context.ticket.status,
                close_execute_at=close_execute_at,
                close_reason=normalized_reason,
                requested_by_id=requested_by_id,
                changed=True,
                log_message=log_message,
            )

    async def revoke_close(
        self,
        channel: Any,
        *,
        actor: Any,
        is_bot_owner: bool = False,
    ) -> CloseRevokeResult:
        channel_id = getattr(channel, "id", None)
        actor_id = getattr(actor, "id", None)
        if channel_id is None:
            raise ValidationError("当前频道不支持撤销 ticket close。")
        if actor_id is None:
            raise ValidationError("无法识别当前操作人的身份。")

        ticket = self.ticket_repository.get_by_channel_id(channel_id)
        if ticket is None:
            raise TicketNotFoundError("当前频道不是已登记的 ticket。")

        async with self._acquire_ticket_lock(ticket.ticket_id):
            context = self._load_close_context(channel_id)
            self.guard_service.assert_staff_actor(
                actor,
                config=context.config,
                category=context.category,
                is_bot_owner=is_bot_owner,
            )

            if context.ticket.status is not TicketStatus.CLOSING:
                raise InvalidTicketStateError("当前 ticket 不处于 closing 状态，无法撤销关闭。")
            if self._close_window_expired(context.ticket):
                raise InvalidTicketStateError("关闭撤销窗口已结束，当前 ticket 正在进入归档。")

            restored_status = context.ticket.status_before or TicketStatus.SUBMITTED
            updated_ticket = (
                self.ticket_repository.update(
                    context.ticket.ticket_id,
                    status=restored_status,
                    status_before=None,
                    close_reason=None,
                    close_initiated_by=None,
                    close_execute_at=None,
                    closed_at=None,
                )
                or context.ticket
            )
            await self._restore_ticket_permissions(
                channel,
                context=context,
                ticket=updated_ticket,
            )
            log_message = await self._send_channel_log(
                channel,
                content=(f"↩️ <@{actor_id}> 已撤销 ticket `{updated_ticket.ticket_id}` 的关闭流程。\n- 恢复状态：`{restored_status.value}`"),
            )
            if self.staff_panel_service is not None:
                self.staff_panel_service.request_refresh(updated_ticket.ticket_id)
            if self.capacity_service.released_capacity(context.ticket.status, restored_status):
                await self._trigger_queue_fill(context.ticket.guild_id)

            return CloseRevokeResult(
                ticket=updated_ticket,
                restored_status=restored_status,
                close_reason=context.ticket.close_reason,
                changed=True,
                log_message=log_message,
            )

    async def sweep_due_closing_tickets(
        self,
        *,
        now: datetime | None = None,
    ) -> list[ArchivePipelineResult]:
        reference_time = self._to_utc_datetime(now)
        outcomes: list[ArchivePipelineResult] = []
        for ticket in self.ticket_repository.list_due_close_executions(reference_time.isoformat()):
            outcome = await self.archive_service.archive_ticket(
                ticket.ticket_id,
                reference_time=reference_time,
            )
            if outcome is not None:
                outcomes.append(outcome)
        return outcomes

    def _load_close_context(self, channel_id: int) -> StaffTicketContext:
        ticket = self.ticket_repository.get_by_channel_id(channel_id)
        if ticket is None:
            raise TicketNotFoundError("当前频道不是已登记的 ticket。")
        if ticket.status not in {TicketStatus.SUBMITTED, TicketStatus.SLEEP, TicketStatus.CLOSING}:
            raise InvalidTicketStateError("当前 ticket 状态不允许执行关闭相关操作。")

        config = self.guild_repository.get_config(ticket.guild_id)
        if config is None or not config.is_initialized:
            raise ValidationError("当前服务器尚未完成 Ticket setup，无法执行 close 操作。")

        category = self.guild_repository.get_category(ticket.guild_id, ticket.category_key)
        if category is None:
            raise ValidationError("当前 ticket 所属分类配置不存在，请先修复服务器配置。")

        return StaffTicketContext(ticket=ticket, config=config, category=category)

    async def _rollback_close_start(self, channel: Any, *, context: StaffTicketContext) -> None:
        self.ticket_repository.update(
            context.ticket.ticket_id,
            status=context.ticket.status,
            status_before=context.ticket.status_before,
            close_reason=context.ticket.close_reason,
            close_initiated_by=context.ticket.close_initiated_by,
            close_execute_at=context.ticket.close_execute_at,
            closed_at=context.ticket.closed_at,
            updated_at=context.ticket.updated_at,
        )
        try:
            await self._restore_ticket_permissions(
                channel,
                context=context,
                ticket=context.ticket,
            )
        except Exception:
            self.logger.warning(
                "Failed to restore ticket permissions after close-start rollback. ticket_id=%s",
                context.ticket.ticket_id,
                exc_info=True,
            )

    async def _freeze_ticket_permissions(
        self,
        channel: Any,
        *,
        context: StaffTicketContext,
    ) -> None:
        guild = getattr(channel, "guild", None)
        set_permissions = getattr(channel, "set_permissions", None)
        if guild is None or set_permissions is None:
            return

        readonly_overwrite = self.permission_service.build_participant_overwrite(can_send=False)
        targets = self.permission_service.resolve_staff_targets(
            guild,
            config=context.config,
            category=context.category,
        )
        creator = self._resolve_channel_member(channel, context.ticket.creator_id)
        active_claimer = self._resolve_channel_member(channel, context.ticket.claimed_by)
        muted_participants = self._resolve_muted_participants(
            channel,
            ticket_id=context.ticket.ticket_id,
        )

        unique_targets: list[Any] = []
        seen_ids: set[int] = set()
        for target in [*targets, creator, active_claimer, *muted_participants]:
            target_id = getattr(target, "id", None)
            if target is None or target_id is None or target_id in seen_ids:
                continue
            seen_ids.add(target_id)
            unique_targets.append(target)

        for target in unique_targets:
            await set_permissions(
                target,
                overwrite=readonly_overwrite,
                reason=f"Lock ticket {context.ticket.ticket_id} during closing window",
            )

    async def _restore_ticket_permissions(
        self,
        channel: Any,
        *,
        context: StaffTicketContext,
        ticket: TicketRecord,
    ) -> None:
        creator = self._resolve_channel_member(channel, ticket.creator_id)
        active_claimer = self._resolve_channel_member(channel, ticket.claimed_by)
        muted_participants = self._resolve_muted_participants(
            channel,
            ticket_id=ticket.ticket_id,
        )
        await self.permission_service.apply_ticket_permissions(
            channel,
            config=context.config,
            category=context.category,
            active_claimer=active_claimer,
            creator=creator,
            muted_participants=muted_participants,
            visible_reason=f"Restore staff access after closing revoke for {ticket.ticket_id}",
            creator_reason=f"Restore creator access after closing revoke for {ticket.ticket_id}",
            muted_reason=f"Preserve muted participant state after closing revoke for {ticket.ticket_id}",
        )

    async def _send_closing_notice(
        self,
        channel: Any,
        *,
        ticket: TicketRecord,
        initiated_by_id: int,
        requested_by_id: int | None,
    ) -> Any | None:
        send = getattr(channel, "send", None)
        if send is None:
            return None
        return await send(
            embed=build_closing_notice_embed(
                ticket,
                initiated_by_id=initiated_by_id,
                reason=ticket.close_reason,
                close_execute_at=ticket.close_execute_at or "未知",
                requested_by_id=requested_by_id,
            )
        )

    @staticmethod
    async def _send_channel_log(channel: Any, *, content: str) -> Any | None:
        send = getattr(channel, "send", None)
        if send is None:
            return None
        return await send(content=content)

    def _resolve_muted_participants(self, channel: Any, *, ticket_id: str) -> list[Any]:
        return [
            member
            for record in self.ticket_mute_repository.list_by_ticket(ticket_id)
            if (member := self._resolve_channel_member(channel, record.user_id)) is not None
        ]

    @staticmethod
    def _resolve_channel_member(channel: Any, user_id: int | None) -> Any | None:
        if user_id is None:
            return None
        guild = getattr(channel, "guild", None)
        get_member = getattr(guild, "get_member", None)
        if not callable(get_member):
            return None
        return get_member(user_id)

    async def _trigger_queue_fill(self, guild_id: int) -> None:
        if self.queue_service is None:
            return
        await self.queue_service.process_next_queued_ticket(guild_id)

    def _assert_sleep_capacity_available(self, *, context: StaffTicketContext) -> None:
        ticket = context.ticket
        if ticket.status is not TicketStatus.SLEEP:
            return

        capacity = self.capacity_service.build_snapshot(
            guild_id=ticket.guild_id,
            max_open_tickets=context.config.max_open_tickets,
        )
        if capacity.has_capacity:
            return

        raise ValidationError(f"当前 active 容量已满（{capacity.active_count}/{context.config.max_open_tickets}），暂时无法从 sleep 发起 close。")

    @staticmethod
    def _normalize_reason(reason: str | None) -> str | None:
        normalized = (reason or "").strip()
        return normalized or None

    @staticmethod
    def _close_window_expired(ticket: TicketRecord) -> bool:
        if ticket.close_execute_at is None:
            return False
        execute_at = CloseService._parse_iso_datetime(ticket.close_execute_at)
        return datetime.now(timezone.utc) >= execute_at

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

    @asynccontextmanager
    async def _acquire_ticket_lock(self, ticket_id: str) -> AsyncIterator[None]:
        if self.lock_manager is None:
            yield
            return

        async with self.lock_manager.acquire(f"ticket-close:{ticket_id}"):
            yield
