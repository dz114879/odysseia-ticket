from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from core.constants import SLEEP_CHANNEL_PREFIX
from core.enums import ClaimMode, TicketPriority, TicketStatus
from core.errors import ValidationError
from db.connection import DatabaseManager
from db.repositories.ticket_repository import TicketRepository
from runtime.locks import LockManager
from services.priority_service import PRIORITY_CHANNEL_PREFIXES, PriorityService
from services.staff_guard_service import StaffGuardService, StaffTicketContext
from services.staff_panel_service import StaffPanelService


@dataclass(frozen=True, slots=True)
class SleepPreparationResult:
    context: StaffTicketContext
    previous_priority: TicketPriority
    strict_mode: bool


@dataclass(frozen=True, slots=True)
class SleepMutationResult:
    ticket: Any
    previous_priority: TicketPriority
    old_channel_name: str
    new_channel_name: str
    channel_name_changed: bool
    changed: bool
    log_message: Any | None


@dataclass(frozen=True, slots=True)
class WakeMutationResult:
    ticket: Any
    restored_priority: TicketPriority
    old_channel_name: str
    new_channel_name: str
    channel_name_changed: bool
    changed: bool
    log_message: Any | None


class SleepService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        ticket_repository: TicketRepository | None = None,
        lock_manager: LockManager | None = None,
        guard_service: StaffGuardService | None = None,
        staff_panel_service: StaffPanelService | None = None,
    ) -> None:
        self.database = database
        self.ticket_repository = ticket_repository or TicketRepository(database)
        self.lock_manager = lock_manager
        self.staff_panel_service = staff_panel_service
        self.guard_service = guard_service or StaffGuardService(
            database,
            ticket_repository=self.ticket_repository,
        )

    def inspect_sleep_request(
        self,
        channel: Any,
        *,
        actor: Any,
        is_bot_owner: bool = False,
    ) -> SleepPreparationResult:
        channel_id = getattr(channel, "id", None)
        actor_id = getattr(actor, "id", None)
        if channel_id is None:
            raise ValidationError("当前频道不支持 ticket sleep。")
        if actor_id is None:
            raise ValidationError("无法识别当前操作人的身份。")

        context = self.guard_service.load_ticket_context(
            channel_id,
            allowed_statuses=(TicketStatus.SUBMITTED,),
            invalid_state_message="当前 ticket 仅在 submitted 状态可进入 sleep。",
        )
        self.guard_service.assert_staff_actor(
            actor,
            config=context.config,
            category=context.category,
            is_bot_owner=is_bot_owner,
        )
        if context.ticket.priority is TicketPriority.SLEEP:
            raise ValidationError("当前 ticket 的优先级状态异常，无法再次进入 sleep。")
        return SleepPreparationResult(
            context=context,
            previous_priority=context.ticket.priority,
            strict_mode=context.config.claim_mode is ClaimMode.STRICT,
        )

    async def sleep_ticket(
        self,
        channel: Any,
        *,
        actor: Any,
        is_bot_owner: bool = False,
    ) -> SleepMutationResult:
        channel_id = getattr(channel, "id", None)
        edit = getattr(channel, "edit", None)
        if channel_id is None or edit is None:
            raise ValidationError("当前频道不支持 ticket sleep。")

        actor_id = getattr(actor, "id", None)
        if actor_id is None:
            raise ValidationError("无法识别当前操作人的身份。")

        async with self._acquire_channel_lock(channel_id):
            preparation = self.inspect_sleep_request(
                channel,
                actor=actor,
                is_bot_owner=is_bot_owner,
            )
            ticket = preparation.context.ticket
            old_channel_name = str(getattr(channel, "name", "") or "")
            new_channel_name = self.build_sleep_channel_name(old_channel_name)
            channel_name_changed = new_channel_name != old_channel_name

            updated_ticket = self.ticket_repository.update(
                ticket.ticket_id,
                status=TicketStatus.SLEEP,
                priority=TicketPriority.SLEEP,
                priority_before_sleep=preparation.previous_priority,
            ) or ticket

            try:
                if channel_name_changed:
                    await edit(
                        name=new_channel_name,
                        reason=f"Put ticket {ticket.ticket_id} to sleep",
                    )
            except Exception:
                self.ticket_repository.update(
                    ticket.ticket_id,
                    status=ticket.status,
                    priority=ticket.priority,
                    priority_before_sleep=ticket.priority_before_sleep,
                )
                raise

            log_message = await self._send_channel_log(
                channel,
                content=(
                    f"💤 <@{actor_id}> 已将 ticket `{ticket.ticket_id}` 挂起。\n"
                    f"- 睡前优先级：{self.get_priority_label(preparation.previous_priority)}"
                ),
            )
            if self.staff_panel_service is not None:
                self.staff_panel_service.request_refresh(ticket.ticket_id)

            return SleepMutationResult(
                ticket=updated_ticket,
                previous_priority=preparation.previous_priority,
                old_channel_name=old_channel_name,
                new_channel_name=new_channel_name,
                channel_name_changed=channel_name_changed,
                changed=True,
                log_message=log_message,
            )

    async def handle_message(self, message: Any) -> WakeMutationResult | None:
        author = getattr(message, "author", None)
        if author is None or getattr(author, "bot", False):
            return None
        if getattr(message, "guild", None) is None or getattr(message, "channel", None) is None:
            return None

        return await self.wake_ticket(message.channel, actor=author)

    async def wake_ticket(
        self,
        channel: Any,
        *,
        actor: Any,
    ) -> WakeMutationResult | None:
        channel_id = getattr(channel, "id", None)
        edit = getattr(channel, "edit", None)
        actor_id = getattr(actor, "id", None)
        if channel_id is None or edit is None or actor_id is None:
            return None

        async with self._acquire_channel_lock(channel_id):
            ticket = self.ticket_repository.get_by_channel_id(channel_id)
            if ticket is None or ticket.status is not TicketStatus.SLEEP:
                return None

            restored_priority = self._resolve_wake_priority(ticket)
            old_channel_name = str(getattr(channel, "name", "") or "")
            new_channel_name = self.build_wake_channel_name(old_channel_name, priority=restored_priority)
            channel_name_changed = new_channel_name != old_channel_name

            updated_ticket = self.ticket_repository.update(
                ticket.ticket_id,
                status=TicketStatus.SUBMITTED,
                priority=restored_priority,
                priority_before_sleep=None,
            ) or ticket

            try:
                if channel_name_changed:
                    await edit(
                        name=new_channel_name,
                        reason=f"Wake ticket {ticket.ticket_id} from sleep",
                    )
            except Exception:
                self.ticket_repository.update(
                    ticket.ticket_id,
                    status=ticket.status,
                    priority=ticket.priority,
                    priority_before_sleep=ticket.priority_before_sleep,
                )
                raise

            log_message = await self._send_channel_log(
                channel,
                content=(
                    f"🌅 <@{actor_id}> 的新消息已唤醒 ticket `{ticket.ticket_id}`。\n"
                    f"- 恢复优先级：{self.get_priority_label(restored_priority)}"
                ),
            )
            if self.staff_panel_service is not None:
                self.staff_panel_service.request_refresh(ticket.ticket_id)

            return WakeMutationResult(
                ticket=updated_ticket,
                restored_priority=restored_priority,
                old_channel_name=old_channel_name,
                new_channel_name=new_channel_name,
                channel_name_changed=channel_name_changed,
                changed=True,
                log_message=log_message,
            )

    @staticmethod
    def build_sleep_channel_name(channel_name: str) -> str:
        base_name = SleepService.strip_sleep_prefix(channel_name)
        base_name = SleepService.strip_priority_prefix(base_name) or "ticket"
        max_base_length = max(1, 95 - len(SLEEP_CHANNEL_PREFIX))
        return f"{SLEEP_CHANNEL_PREFIX}{base_name[:max_base_length]}"

    @staticmethod
    def build_wake_channel_name(channel_name: str, *, priority: TicketPriority) -> str:
        base_name = SleepService.strip_sleep_prefix(channel_name)
        base_name = SleepService.strip_priority_prefix(base_name) or "ticket"
        if priority is TicketPriority.SLEEP:
            return SleepService.build_sleep_channel_name(base_name)
        return PriorityService.build_priority_channel_name(base_name, priority=priority)

    @staticmethod
    def strip_sleep_prefix(channel_name: str) -> str:
        if channel_name.startswith(SLEEP_CHANNEL_PREFIX):
            return channel_name[len(SLEEP_CHANNEL_PREFIX) :]
        return channel_name

    @staticmethod
    def strip_priority_prefix(channel_name: str) -> str:
        for prefix in sorted(PRIORITY_CHANNEL_PREFIXES.values(), key=len, reverse=True):
            if channel_name.startswith(prefix):
                return channel_name[len(prefix) :]
        return channel_name

    @staticmethod
    def _resolve_wake_priority(ticket: Any) -> TicketPriority:
        previous_priority = getattr(ticket, "priority_before_sleep", None)
        if isinstance(previous_priority, TicketPriority):
            return previous_priority
        current_priority = getattr(ticket, "priority", None)
        if isinstance(current_priority, TicketPriority) and current_priority is not TicketPriority.SLEEP:
            return current_priority
        return TicketPriority.MEDIUM

    @staticmethod
    def get_priority_label(priority: TicketPriority) -> str:
        labels = {
            TicketPriority.LOW: "低 🟢",
            TicketPriority.MEDIUM: "中 🟡",
            TicketPriority.HIGH: "高 🔴",
            TicketPriority.EMERGENCY: "紧急 ‼️",
            TicketPriority.SLEEP: "挂起 💤",
        }
        return labels.get(priority, priority.value)

    @staticmethod
    async def _send_channel_log(channel: Any, *, content: str) -> Any | None:
        send = getattr(channel, "send", None)
        if send is None:
            return None
        return await send(content=content)

    @asynccontextmanager
    async def _acquire_channel_lock(self, channel_id: int) -> AsyncIterator[None]:
        if self.lock_manager is None:
            yield
            return

        async with self.lock_manager.acquire(f"ticket-sleep:{channel_id}"):
            yield
