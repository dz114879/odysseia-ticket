from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from collections.abc import AsyncIterator

from core.constants import TRANSFER_EXECUTION_DELAY_SECONDS
from core.enums import TicketStatus
from core.errors import PermissionDeniedError, ValidationError
from core.models import GuildConfigRecord, TicketCategoryConfig
from db.connection import DatabaseManager
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_mute_repository import TicketMuteRepository
from db.repositories.ticket_repository import TicketRepository
from runtime.locks import LockManager
from services.capacity_service import CapacityService
from services.logging_service import LoggingService
from services.queue_service import QueueService
from services.staff_guard_service import StaffGuardService, StaffTicketContext
from services.staff_permission_service import StaffPermissionService
from services.staff_panel_service import StaffPanelService


@dataclass(frozen=True, slots=True)
class TransferPreparationResult:
    context: StaffTicketContext
    target_categories: list[TicketCategoryConfig]
    current_claimer_id: int | None


@dataclass(frozen=True, slots=True)
class TransferMutationResult:
    ticket: Any
    previous_status: TicketStatus
    target_category: TicketCategoryConfig
    current_claimer_id: int | None
    reason: str | None
    execute_at: str | None
    changed: bool
    log_message: Any | None


@dataclass(frozen=True, slots=True)
class TransferCancellationResult:
    ticket: Any
    restored_status: TicketStatus
    previous_target_category_key: str | None
    reason: str | None
    changed: bool
    log_message: Any | None


@dataclass(frozen=True, slots=True)
class TransferExecutionResult:
    ticket: Any
    previous_category_key: str
    target_category: TicketCategoryConfig
    restored_status: TicketStatus
    previous_claimer_id: int | None
    changed: bool
    log_message: Any | None
    guild_log_sent: bool


class TransferService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        bot: Any | None = None,
        guard_service: StaffGuardService | None = None,
        guild_repository: GuildRepository | None = None,
        ticket_repository: TicketRepository | None = None,
        lock_manager: LockManager | None = None,
        ticket_mute_repository: TicketMuteRepository | None = None,
        staff_panel_service: StaffPanelService | None = None,
        logging_service: LoggingService | None = None,
        logger: logging.Logger | None = None,
        permission_service: StaffPermissionService | None = None,
        capacity_service: CapacityService | None = None,
        queue_service: QueueService | None = None,
    ) -> None:
        self.database = database
        self.bot = bot
        self.guild_repository = guild_repository or GuildRepository(database)
        self.ticket_repository = ticket_repository or TicketRepository(database)
        self.lock_manager = lock_manager
        self.ticket_mute_repository = ticket_mute_repository or TicketMuteRepository(database)
        self.staff_panel_service = staff_panel_service
        self.logging_service = logging_service
        self.logger = logger or logging.getLogger(__name__)
        self.permission_service = permission_service or StaffPermissionService()
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

    def inspect_transfer_request(
        self,
        channel: Any,
        *,
        actor: Any,
        is_bot_owner: bool = False,
    ) -> TransferPreparationResult:
        channel_id = getattr(channel, "id", None)
        actor_id = getattr(actor, "id", None)
        if channel_id is None:
            raise ValidationError("当前频道不支持 ticket transfer。")
        if actor_id is None:
            raise ValidationError("无法识别当前操作身份。")

        context = self.guard_service.load_ticket_context(
            channel_id,
            allowed_statuses=(TicketStatus.SUBMITTED, TicketStatus.SLEEP),
            invalid_state_message="当前 ticket 仅在 submitted / sleep 状态可发起转交。",
        )
        self.guard_service.assert_staff_actor(
            actor,
            config=context.config,
            category=context.category,
            is_bot_owner=is_bot_owner,
        )

        if context.ticket.claimed_by is not None and context.ticket.claimed_by != actor_id:
            raise PermissionDeniedError("当前 ticket 已被其他 staff 认领；请先由当前认领者发起转交，或先取消认领。")

        target_categories = [
            category
            for category in self.guild_repository.list_categories(context.ticket.guild_id, enabled_only=True)
            if category.category_key != context.ticket.category_key
        ]
        if not target_categories:
            raise ValidationError("当前服务器没有其他可转交的启用分类。")

        self._assert_sleep_capacity_available(preparation_context=context)
        return TransferPreparationResult(
            context=context,
            target_categories=target_categories,
            current_claimer_id=context.ticket.claimed_by,
        )

    async def transfer_ticket(
        self,
        channel: Any,
        *,
        actor: Any,
        target_category_key: str,
        reason: str | None = None,
        is_bot_owner: bool = False,
        now: datetime | str | None = None,
    ) -> TransferMutationResult:
        channel_id = getattr(channel, "id", None)
        actor_id = getattr(actor, "id", None)
        if channel_id is None:
            raise ValidationError("当前频道不支持 ticket transfer。")
        if actor_id is None:
            raise ValidationError("无法识别当前操作身份。")

        normalized_target_category_key = self._normalize_target_category_key(target_category_key)
        normalized_reason = self._normalize_reason(reason)

        async with self._acquire_transfer_lock(channel_id):
            preparation = self.inspect_transfer_request(
                channel,
                actor=actor,
                is_bot_owner=is_bot_owner,
            )
            target_category = self._select_target_category(
                preparation.target_categories,
                target_category_key=normalized_target_category_key,
            )
            # 从公会配置中读取转交延迟秒数，若无配置则使用默认值
            config_delay = preparation.context.config.transfer_delay_seconds if preparation.context.config else TRANSFER_EXECUTION_DELAY_SECONDS
            scheduled_execute_at = self._build_transfer_execute_at(now, delay_seconds=config_delay)
            ticket = preparation.context.ticket
            updated_ticket = (
                self.ticket_repository.update(
                    ticket.ticket_id,
                    status=TicketStatus.TRANSFERRING,
                    status_before=ticket.status,
                    transfer_target_category=target_category.category_key,
                    transfer_initiated_by=actor_id,
                    transfer_reason=normalized_reason,
                    transfer_execute_at=scheduled_execute_at,
                )
                or ticket
            )
            log_message = await self._send_channel_log(
                channel,
                content=self._build_transfer_log_content(
                    actor_id=actor_id,
                    ticket_id=ticket.ticket_id,
                    previous_status=ticket.status,
                    target_category=target_category,
                    reason=normalized_reason,
                    current_claimer_id=ticket.claimed_by,
                    execute_at=scheduled_execute_at,
                ),
            )
            if self.staff_panel_service is not None:
                self.staff_panel_service.request_refresh(ticket.ticket_id)

            return TransferMutationResult(
                ticket=updated_ticket,
                previous_status=ticket.status,
                target_category=target_category,
                current_claimer_id=ticket.claimed_by,
                reason=normalized_reason,
                execute_at=scheduled_execute_at,
                changed=True,
                log_message=log_message,
            )

    async def cancel_transfer(
        self,
        channel: Any,
        *,
        actor: Any,
        is_bot_owner: bool = False,
    ) -> TransferCancellationResult:
        channel_id = getattr(channel, "id", None)
        actor_id = getattr(actor, "id", None)
        if channel_id is None:
            raise ValidationError("当前频道不支持 ticket untransfer。")
        if actor_id is None:
            raise ValidationError("无法识别当前操作身份。")

        async with self._acquire_transfer_lock(channel_id):
            context = self.guard_service.load_ticket_context(
                channel_id,
                allowed_statuses=(TicketStatus.TRANSFERRING,),
                invalid_state_message="当前 ticket 不处于 transferring 状态，无法撤销转交。",
            )
            self.guard_service.assert_staff_actor(
                actor,
                config=context.config,
                category=context.category,
                is_bot_owner=is_bot_owner,
            )
            restored_status = self._resolve_restored_status(context.ticket)
            updated_ticket = (
                self.ticket_repository.update(
                    context.ticket.ticket_id,
                    status=restored_status,
                    status_before=None,
                    transfer_target_category=None,
                    transfer_initiated_by=None,
                    transfer_reason=None,
                    transfer_execute_at=None,
                )
                or context.ticket
            )
            log_message = await self._send_channel_log(
                channel,
                content=self._build_cancel_transfer_log_content(
                    actor_id=actor_id,
                    ticket_id=context.ticket.ticket_id,
                    restored_status=restored_status,
                    previous_target_category_key=context.ticket.transfer_target_category,
                    reason=context.ticket.transfer_reason,
                ),
            )
            if self.staff_panel_service is not None:
                self.staff_panel_service.request_refresh(context.ticket.ticket_id)

            if self.capacity_service.released_capacity(context.ticket.status, restored_status):
                await self._trigger_queue_fill(context.ticket.guild_id)

            return TransferCancellationResult(
                ticket=updated_ticket,
                restored_status=restored_status,
                previous_target_category_key=context.ticket.transfer_target_category,
                reason=context.ticket.transfer_reason,
                changed=True,
                log_message=log_message,
            )

    async def sweep_due_transfers(
        self,
        *,
        now: datetime | str | None = None,
    ) -> list[TransferExecutionResult]:
        reference_time = self._to_utc_datetime(now)
        outcomes: list[TransferExecutionResult] = []

        for ticket in self.ticket_repository.list_due_transfer_executions(reference_time.isoformat()):
            try:
                outcome = await self._execute_due_transfer(
                    ticket.ticket_id,
                    reference_time=reference_time,
                )
            except Exception as exc:
                self.logger.exception(
                    "Failed to execute due transfer. ticket_id=%s",
                    ticket.ticket_id,
                )
                if self.logging_service is not None:
                    config = self.guild_repository.get_config(ticket.guild_id)
                    await self.logging_service.send_ticket_log(
                        ticket_id=ticket.ticket_id,
                        guild_id=ticket.guild_id,
                        level="error",
                        title="转移执行失败",
                        description=f"定时 Transfer 执行失败：{exc}",
                        channel_id=getattr(config, "log_channel_id", None) if config else None,
                    )
                continue
            if outcome is not None:
                outcomes.append(outcome)

        return outcomes

    async def _execute_due_transfer(
        self,
        ticket_id: str,
        *,
        reference_time: datetime,
    ) -> TransferExecutionResult | None:
        current_ticket = self.ticket_repository.get_by_ticket_id(ticket_id)
        lock_key: str | int = ticket_id
        if current_ticket is not None and current_ticket.channel_id is not None:
            lock_key = current_ticket.channel_id

        async with self._acquire_transfer_lock(lock_key):
            ticket = self.ticket_repository.get_by_ticket_id(ticket_id)
            if ticket is None or ticket.status is not TicketStatus.TRANSFERRING:
                return None
            if not self._is_due_for_execution(ticket, reference_time):
                return None

            config = self._require_guild_config(ticket.guild_id)
            restored_status = self._resolve_restored_status(ticket)
            previous_category = self.guild_repository.get_category(ticket.guild_id, ticket.category_key)
            target_category = self._resolve_execution_target_category(ticket)
            history_json = self._append_transfer_history(
                ticket,
                executed_at=reference_time.isoformat(),
                restored_status=restored_status,
            )

            updated_ticket = (
                self.ticket_repository.update(
                    ticket.ticket_id,
                    category_key=target_category.category_key,
                    claimed_by=None,
                    status=restored_status,
                    status_before=None,
                    transfer_target_category=None,
                    transfer_initiated_by=None,
                    transfer_reason=None,
                    transfer_execute_at=None,
                    transfer_history_json=history_json,
                )
                or ticket
            )

            channel = await self._resolve_channel(updated_ticket.channel_id)
            if channel is not None:
                try:
                    await self._sync_transfer_permissions(
                        channel=channel,
                        config=config,
                        previous_category=previous_category,
                        target_category=target_category,
                        ticket=updated_ticket,
                        previous_claimer_id=ticket.claimed_by,
                    )
                except Exception as exc:
                    self.logger.exception(
                        "Failed to recalculate permissions after transfer execution. ticket_id=%s",
                        ticket.ticket_id,
                    )
                    if self.logging_service is not None:
                        await self.logging_service.send_ticket_log(
                            ticket_id=ticket.ticket_id,
                            guild_id=ticket.guild_id,
                            level="warning",
                            title="转移权限同步失败",
                            description=f"Transfer 执行后权限重算失败：{exc}",
                            channel_id=getattr(config, "log_channel_id", None),
                        )

            log_message = None
            if channel is not None:
                try:
                    log_message = await self._send_channel_log(
                        channel,
                        content=self._build_execute_transfer_log_content(
                            ticket_id=ticket.ticket_id,
                            previous_category_key=ticket.category_key,
                            previous_category=previous_category,
                            target_category=target_category,
                            restored_status=restored_status,
                            previous_claimer_id=ticket.claimed_by,
                            reason=ticket.transfer_reason,
                            executed_at=reference_time.isoformat(),
                        ),
                    )
                except Exception:
                    self.logger.exception(
                        "Failed to send channel transfer execution log. ticket_id=%s",
                        ticket.ticket_id,
                    )

            if self.staff_panel_service is not None:
                self.staff_panel_service.request_refresh(updated_ticket.ticket_id)

            guild_log_sent = await self._send_transfer_completion_log(
                ticket=updated_ticket,
                config=config,
                previous_category_key=ticket.category_key,
                previous_category=previous_category,
                target_category=target_category,
                restored_status=restored_status,
                previous_claimer_id=ticket.claimed_by,
                executed_at=reference_time.isoformat(),
                reason=ticket.transfer_reason,
            )

            if self.capacity_service.released_capacity(ticket.status, restored_status):
                await self._trigger_queue_fill(ticket.guild_id)

            return TransferExecutionResult(
                ticket=updated_ticket,
                previous_category_key=ticket.category_key,
                target_category=target_category,
                restored_status=restored_status,
                previous_claimer_id=ticket.claimed_by,
                changed=True,
                log_message=log_message,
                guild_log_sent=guild_log_sent,
            )

    @staticmethod
    def _normalize_target_category_key(target_category_key: str) -> str:
        normalized = target_category_key.strip()
        if not normalized:
            raise ValidationError("请提供目标分类的 category_key。")
        return normalized

    @staticmethod
    def _normalize_reason(reason: str | None) -> str | None:
        if reason is None:
            return None
        normalized = reason.strip()
        return normalized or None

    @staticmethod
    def _select_target_category(
        target_categories: list[TicketCategoryConfig],
        *,
        target_category_key: str,
    ) -> TicketCategoryConfig:
        for category in target_categories:
            if category.category_key == target_category_key:
                return category
        raise ValidationError("目标分类不存在、未启用，或与当前分类相同。")

    @staticmethod
    def _build_transfer_log_content(
        *,
        actor_id: int,
        ticket_id: str,
        previous_status: TicketStatus,
        target_category: TicketCategoryConfig,
        reason: str | None,
        current_claimer_id: int | None,
        execute_at: str | None,
    ) -> str:
        lines = [
            f"🔁 <@{actor_id}> 已发起 ticket `{ticket_id}` 的跨分类转交。",
            f"- 原状态：{TransferService.get_status_label(previous_status)}",
            f"- 目标分类：{target_category.display_name} (`{target_category.category_key}`)",
            f"- 当前认领者：<@{current_claimer_id}>" if current_claimer_id is not None else "- 当前认领者：未认领",
        ]
        if reason is not None:
            lines.append(f"- 转交理由：{reason}")
        if execute_at is not None:
            lines.append(f"- 计划执行时间：{execute_at}")
        return "\n".join(lines)

    @staticmethod
    def _build_cancel_transfer_log_content(
        *,
        actor_id: int,
        ticket_id: str,
        restored_status: TicketStatus,
        previous_target_category_key: str | None,
        reason: str | None,
    ) -> str:
        lines = [
            f"↩️ <@{actor_id}> 已撤销 ticket `{ticket_id}` 的跨分类转交。",
            f"- 恢复状态：{TransferService.get_status_label(restored_status)}",
            (f"- 原目标分类：`{previous_target_category_key}`" if previous_target_category_key is not None else "- 原目标分类：未知"),
        ]
        if reason is not None:
            lines.append(f"- 原转交理由：{reason}")
        return "\n".join(lines)

    @staticmethod
    def _build_execute_transfer_log_content(
        *,
        ticket_id: str,
        previous_category_key: str,
        previous_category: TicketCategoryConfig | None,
        target_category: TicketCategoryConfig,
        restored_status: TicketStatus,
        previous_claimer_id: int | None,
        reason: str | None,
        executed_at: str,
    ) -> str:
        previous_category_name = previous_category.display_name if previous_category is not None else previous_category_key
        lines = [
            f"✅ ticket `{ticket_id}` 的跨分类转交已执行。",
            f"- 原分类：{previous_category_name} (`{previous_category_key}`)",
            f"- 新分类：{target_category.display_name} (`{target_category.category_key}`)",
            f"- 恢复状态：{TransferService.get_status_label(restored_status)}",
            f"- 原认领者：<@{previous_claimer_id}>" if previous_claimer_id is not None else "- 原认领者：未认领",
            f"- 执行时间：{executed_at}",
        ]
        if reason is not None:
            lines.append(f"- 原转交理由：{reason}")
        return "\n".join(lines)

    @staticmethod
    def _resolve_restored_status(ticket: Any) -> TicketStatus:
        status_before = getattr(ticket, "status_before", None)
        if status_before in (TicketStatus.SUBMITTED, TicketStatus.SLEEP):
            return status_before
        raise ValidationError("当前 transferring ticket 缺少有效的 status_before，无法撤销转交。")

    def _resolve_execution_target_category(self, ticket: Any) -> TicketCategoryConfig:
        target_category_key = getattr(ticket, "transfer_target_category", None)
        if not target_category_key:
            raise ValidationError("当前 transferring ticket 缺少有效的 transfer_target_category，无法执行转交。")

        category = self.guild_repository.get_category(ticket.guild_id, target_category_key)
        if category is None or not category.is_enabled:
            raise ValidationError("当前 transferring ticket 的目标分类不存在或未启用，无法执行转交。")
        return category

    def _require_guild_config(self, guild_id: int) -> GuildConfigRecord:
        config = self.guild_repository.get_config(guild_id)
        if config is None or not config.is_initialized:
            raise ValidationError("当前服务器尚未完成 Ticket setup，无法执行 transfer。")
        return config

    def _assert_sleep_capacity_available(self, *, preparation_context: StaffTicketContext) -> None:
        ticket = preparation_context.ticket
        if ticket.status is not TicketStatus.SLEEP:
            return

        capacity = self.capacity_service.build_snapshot(
            guild_id=ticket.guild_id,
            max_open_tickets=preparation_context.config.max_open_tickets,
        )
        if capacity.has_capacity:
            return

        raise ValidationError(
            f"当前 active 容量已满（{capacity.active_count}/{preparation_context.config.max_open_tickets}），暂时无法从 sleep 发起 transfer。"
        )

    @staticmethod
    def get_status_label(status: TicketStatus) -> str:
        labels = {
            TicketStatus.SUBMITTED: "submitted 处理中",
            TicketStatus.SLEEP: "sleep 挂起中",
            TicketStatus.TRANSFERRING: "transferring 转交中",
        }
        return labels.get(status, status.value)

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

    async def _trigger_queue_fill(self, guild_id: int) -> None:
        if self.queue_service is None:
            return
        await self.queue_service.process_next_queued_ticket(guild_id)

    async def _sync_transfer_permissions(
        self,
        *,
        channel: Any,
        config: GuildConfigRecord,
        previous_category: TicketCategoryConfig | None,
        target_category: TicketCategoryConfig,
        ticket: Any,
        previous_claimer_id: int | None,
    ) -> None:
        creator = self._resolve_channel_member(channel, getattr(ticket, "creator_id", 0))
        muted_participants = self._resolve_muted_participants(channel, ticket.ticket_id)
        await self.permission_service.apply_ticket_permissions(
            channel,
            config=config,
            category=target_category,
            creator=creator,
            participants=muted_participants,
            muted_participants=muted_participants,
            previous_claimer_id=previous_claimer_id,
            hidden_categories=(previous_category,),
            visible_reason="Grant new category staff access after ticket transfer execution",
            hidden_reason="Hide previous category staff after ticket transfer execution",
        )

    def _resolve_muted_participants(self, channel: Any, ticket_id: str) -> list[Any]:
        return [
            member
            for member in (self._resolve_channel_member(channel, record.user_id) for record in self.ticket_mute_repository.list_by_ticket(ticket_id))
            if member is not None
        ]

    @staticmethod
    def _resolve_channel_member(channel: Any, user_id: int) -> Any | None:
        guild = getattr(channel, "guild", None)
        if guild is None:
            return None
        get_member = getattr(guild, "get_member", None)
        if not callable(get_member):
            return None
        return get_member(user_id)

    async def _send_transfer_completion_log(
        self,
        *,
        ticket: Any,
        config: GuildConfigRecord,
        previous_category_key: str,
        previous_category: TicketCategoryConfig | None,
        target_category: TicketCategoryConfig,
        restored_status: TicketStatus,
        previous_claimer_id: int | None,
        executed_at: str,
        reason: str | None,
    ) -> bool:
        if self.logging_service is None:
            return False

        previous_category_name = previous_category.display_name if previous_category is not None else previous_category_key
        description = (
            f"ticket `{ticket.ticket_id}` 已完成跨分类转交："
            f" `{previous_category_key}` -> `{target_category.category_key}`，"
            f"并恢复为 {restored_status.value}。"
        )
        extra: dict[str, object] = {
            "previous_category": previous_category_name,
            "target_category": target_category.display_name,
            "restored_status": restored_status.value,
            "executed_at": executed_at,
        }
        if previous_claimer_id is not None:
            extra["previous_claimer_id"] = previous_claimer_id
        if reason is not None:
            extra["transfer_reason"] = reason

        return await self.logging_service.send_ticket_log(
            ticket_id=ticket.ticket_id,
            guild_id=ticket.guild_id,
            level="success",
            title="工单转移已执行",
            description=description,
            channel_id=config.log_channel_id,
            extra=extra,
        )

    def _append_transfer_history(
        self,
        ticket: Any,
        *,
        executed_at: str,
        restored_status: TicketStatus,
    ) -> str:
        history = self._parse_transfer_history(getattr(ticket, "transfer_history_json", "[]"))
        history.append(
            {
                "type": "transfer_executed",
                "from_category_key": ticket.category_key,
                "to_category_key": getattr(ticket, "transfer_target_category", None),
                "status_before": getattr(getattr(ticket, "status_before", None), "value", None),
                "restored_status": restored_status.value,
                "initiated_by": getattr(ticket, "transfer_initiated_by", None),
                "reason": getattr(ticket, "transfer_reason", None),
                "previous_claimer_id": getattr(ticket, "claimed_by", None),
                "scheduled_execute_at": getattr(ticket, "transfer_execute_at", None),
                "executed_at": executed_at,
            }
        )
        return json.dumps(history, ensure_ascii=False)

    @staticmethod
    def _parse_transfer_history(raw_value: str) -> list[dict[str, Any]]:
        try:
            data = json.loads(raw_value or "[]")
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

    def _build_transfer_execute_at(self, now: datetime | str | None, *, delay_seconds: int = TRANSFER_EXECUTION_DELAY_SECONDS) -> str:
        reference_time = self._to_utc_datetime(now)
        return (reference_time + timedelta(seconds=delay_seconds)).isoformat()

    @staticmethod
    def _is_due_for_execution(ticket: Any, reference_time: datetime) -> bool:
        execute_at = getattr(ticket, "transfer_execute_at", None)
        if not execute_at:
            return False
        execute_at_datetime = TransferService._parse_iso_datetime(execute_at)
        return execute_at_datetime <= reference_time

    @staticmethod
    def _to_utc_datetime(value: datetime | str | None) -> datetime:
        if value is None:
            return datetime.now(timezone.utc)
        if isinstance(value, str):
            return TransferService._parse_iso_datetime(value)
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _parse_iso_datetime(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    async def _send_channel_log(channel: Any, *, content: str) -> Any | None:
        send = getattr(channel, "send", None)
        if send is None:
            return None
        return await send(content=content)

    @asynccontextmanager
    async def _acquire_transfer_lock(self, lock_key: str | int) -> AsyncIterator[None]:
        if self.lock_manager is None:
            yield
            return

        async with self.lock_manager.acquire(f"ticket-transfer:{lock_key}"):
            yield
