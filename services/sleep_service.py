from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.enums import ClaimMode, TicketPriority, TicketStatus
from core.errors import ValidationError
from db.connection import DatabaseManager
from services.staff_guard_service import StaffGuardService, StaffTicketContext


@dataclass(frozen=True, slots=True)
class SleepPreparationResult:
    context: StaffTicketContext
    previous_priority: TicketPriority
    strict_mode: bool


class SleepService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        guard_service: StaffGuardService | None = None,
    ) -> None:
        self.database = database
        self.guard_service = guard_service or StaffGuardService(database)

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
        return SleepPreparationResult(
            context=context,
            previous_priority=context.ticket.priority,
            strict_mode=context.config.claim_mode is ClaimMode.STRICT,
        )
