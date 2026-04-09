from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.enums import TicketStatus
from core.errors import PermissionDeniedError, ValidationError
from core.models import TicketCategoryConfig
from db.connection import DatabaseManager
from db.repositories.guild_repository import GuildRepository
from services.staff_guard_service import StaffGuardService, StaffTicketContext


@dataclass(frozen=True, slots=True)
class TransferPreparationResult:
    context: StaffTicketContext
    target_categories: list[TicketCategoryConfig]
    current_claimer_id: int | None


class TransferService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        guard_service: StaffGuardService | None = None,
        guild_repository: GuildRepository | None = None,
    ) -> None:
        self.database = database
        self.guild_repository = guild_repository or GuildRepository(database)
        self.guard_service = guard_service or StaffGuardService(
            database,
            guild_repository=self.guild_repository,
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
            raise PermissionDeniedError(
                "当前 ticket 已被其他 staff 认领；请先由当前认领者发起转交，或先取消认领。"
            )

        target_categories = [
            category
            for category in self.guild_repository.list_categories(context.ticket.guild_id, enabled_only=True)
            if category.category_key != context.ticket.category_key
        ]
        if not target_categories:
            raise ValidationError("当前服务器没有其他可转交的启用分类。")

        return TransferPreparationResult(
            context=context,
            target_categories=target_categories,
            current_claimer_id=context.ticket.claimed_by,
        )
