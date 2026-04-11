from __future__ import annotations

from typing import Any

from core.enums import TicketStatus
from core.errors import PermissionDeniedError
from services.staff_guard_service import StaffGuardService, StaffTicketContext
from db.connection import DatabaseManager


_ACTIVE_SNAPSHOT_CONTEXT_STATUSES = (
    TicketStatus.SUBMITTED,
    TicketStatus.SLEEP,
    TicketStatus.TRANSFERRING,
    TicketStatus.CLOSING,
)


class TicketAccessService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        staff_guard_service: StaffGuardService | None = None,
    ) -> None:
        self.database = database
        self.staff_guard_service = staff_guard_service or StaffGuardService(database)

    def load_snapshot_context(self, channel_id: int) -> StaffTicketContext:
        return self.staff_guard_service.load_ticket_context(
            channel_id,
            allowed_statuses=_ACTIVE_SNAPSHOT_CONTEXT_STATUSES,
            invalid_state_message="当前 ticket 状态不支持查看快照或备注记录。",
        )

    def assert_can_view_snapshots(
        self,
        actor: Any,
        *,
        context: StaffTicketContext,
        is_bot_owner: bool,
    ) -> None:
        actor_id = getattr(actor, "id", None)
        if actor_id is not None and actor_id == context.ticket.creator_id:
            return
        if self.staff_guard_service.is_staff_actor(
            actor,
            config=context.config,
            category=context.category,
            is_bot_owner=is_bot_owner,
        ):
            return
        raise PermissionDeniedError(
            "只有当前 ticket 创建者、当前分类 staff、Ticket 管理员或 Bot 所有者可以查看快照记录。"
        )

    def assert_can_manage_notes(
        self,
        actor: Any,
        *,
        context: StaffTicketContext,
        is_bot_owner: bool,
    ) -> None:
        self.staff_guard_service.assert_staff_actor(
            actor,
            config=context.config,
            category=context.category,
            is_bot_owner=is_bot_owner,
        )
