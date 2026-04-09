from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from core.enums import TicketPriority
from core.errors import ValidationError
from db.connection import DatabaseManager
from db.repositories.ticket_repository import TicketRepository
from runtime.locks import LockManager
from services.claim_service import ClaimService
from services.staff_panel_service import StaffPanelService


PRIORITY_CHANNEL_PREFIXES: dict[TicketPriority, str] = {
    TicketPriority.LOW: "🟢|",
    TicketPriority.MEDIUM: "🟡|",
    TicketPriority.HIGH: "🔴|",
    TicketPriority.EMERGENCY: "‼️|",
}


@dataclass(frozen=True, slots=True)
class PriorityUpdateResult:
    ticket_id: str
    ticket: Any
    old_priority: TicketPriority
    new_priority: TicketPriority
    old_channel_name: str
    new_channel_name: str
    priority_changed: bool
    channel_name_changed: bool
    changed: bool
    log_message: Any | None


class PriorityService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        claim_service: ClaimService | None = None,
        ticket_repository: TicketRepository | None = None,
        lock_manager: LockManager | None = None,
        staff_panel_service: StaffPanelService | None = None,
    ) -> None:
        self.database = database
        shared_ticket_repository = ticket_repository or getattr(claim_service, "ticket_repository", None)
        self.ticket_repository = shared_ticket_repository or TicketRepository(database)
        self.claim_service = claim_service or ClaimService(
            database,
            ticket_repository=self.ticket_repository,
            lock_manager=lock_manager,
        )
        self.lock_manager = lock_manager or getattr(self.claim_service, "lock_manager", None)
        self.staff_panel_service = staff_panel_service

    async def set_priority(
        self,
        channel: Any,
        *,
        actor: Any,
        priority: TicketPriority | str,
        is_bot_owner: bool = False,
    ) -> PriorityUpdateResult:
        channel_id = getattr(channel, "id", None)
        edit = getattr(channel, "edit", None)
        if channel_id is None or edit is None:
            raise ValidationError("当前频道不支持 ticket priority。")

        actor_id = getattr(actor, "id", None)
        if actor_id is None:
            raise ValidationError("无法识别当前操作人的身份。")

        target_priority = self._coerce_priority(priority)
        async with self._acquire_channel_lock(channel_id):
            context = self.claim_service._load_ticket_context(channel_id)
            self.claim_service._assert_staff_actor(
                actor,
                config=context.config,
                category=context.category,
                is_bot_owner=is_bot_owner,
            )

            old_channel_name = str(getattr(channel, "name", "") or "")
            new_channel_name = self.build_priority_channel_name(
                old_channel_name,
                priority=target_priority,
            )
            priority_changed = context.ticket.priority is not target_priority
            channel_name_changed = new_channel_name != old_channel_name

            updated_ticket = context.ticket
            if priority_changed:
                updated_ticket = self.ticket_repository.update(
                    context.ticket.ticket_id,
                    priority=target_priority,
                ) or context.ticket

            try:
                if channel_name_changed:
                    await edit(
                        name=new_channel_name,
                        reason=f"Set ticket {context.ticket.ticket_id} priority to {target_priority.value}",
                    )
            except Exception:
                if priority_changed:
                    self.ticket_repository.update(
                        context.ticket.ticket_id,
                        priority=context.ticket.priority,
                    )
                raise

            changed = priority_changed or channel_name_changed
            log_message = None
            if changed:
                priority_label = self.get_priority_label(target_priority)
                log_message = await self._send_channel_log(
                    channel,
                    content=(
                        f"🏷️ <@{actor_id}> 已将 ticket `{context.ticket.ticket_id}` 的优先级调整为 "
                        f"{priority_label}。"
                    ),
                )
                if self.staff_panel_service is not None:
                    self.staff_panel_service.request_refresh(context.ticket.ticket_id)

            return PriorityUpdateResult(
                ticket_id=context.ticket.ticket_id,
                ticket=updated_ticket,
                old_priority=context.ticket.priority,
                new_priority=target_priority,
                old_channel_name=old_channel_name,
                new_channel_name=new_channel_name,
                priority_changed=priority_changed,
                channel_name_changed=channel_name_changed,
                changed=changed,
                log_message=log_message,
            )

    @staticmethod
    def build_priority_channel_name(channel_name: str, *, priority: TicketPriority) -> str:
        prefix = PriorityService.get_priority_prefix(priority)
        base_name = PriorityService.strip_priority_prefix(channel_name) or "ticket"
        max_base_length = max(1, 95 - len(prefix))
        trimmed_base_name = base_name[:max_base_length]
        return f"{prefix}{trimmed_base_name}"

    @staticmethod
    def strip_priority_prefix(channel_name: str) -> str:
        for prefix in sorted(PRIORITY_CHANNEL_PREFIXES.values(), key=len, reverse=True):
            if channel_name.startswith(prefix):
                return channel_name[len(prefix) :]
        return channel_name

    @staticmethod
    def get_priority_prefix(priority: TicketPriority) -> str:
        if priority is TicketPriority.SLEEP:
            raise ValidationError("sleep 不是可手动设置的 ticket 优先级，请改用后续 sleep 功能。")
        prefix = PRIORITY_CHANNEL_PREFIXES.get(priority)
        if prefix is None:
            raise ValidationError("不支持的 ticket 优先级。")
        return prefix

    @staticmethod
    def get_priority_label(priority: TicketPriority) -> str:
        labels = {
            TicketPriority.LOW: "低 🟢",
            TicketPriority.MEDIUM: "中 🟡",
            TicketPriority.HIGH: "高 🔴",
            TicketPriority.EMERGENCY: "紧急 ‼️",
        }
        return labels.get(priority, priority.value)

    @staticmethod
    async def _send_channel_log(channel: Any, *, content: str) -> Any | None:
        send = getattr(channel, "send", None)
        if send is None:
            return None
        return await send(content=content)

    @staticmethod
    def _coerce_priority(priority: TicketPriority | str) -> TicketPriority:
        if isinstance(priority, TicketPriority):
            return priority
        try:
            return TicketPriority(priority)
        except ValueError as exc:
            raise ValidationError("不支持的 ticket 优先级。") from exc

    @asynccontextmanager
    async def _acquire_channel_lock(self, channel_id: int) -> AsyncIterator[None]:
        if self.lock_manager is None:
            yield
            return

        async with self.lock_manager.acquire(f"ticket-priority:{channel_id}"):
            yield
