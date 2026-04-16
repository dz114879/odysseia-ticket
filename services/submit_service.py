from __future__ import annotations

import logging
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from collections.abc import AsyncIterator

from core.enums import TicketStatus
from core.errors import ValidationError
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.connection import DatabaseManager
from db.repositories.base import utc_now_iso
from db.repositories.ticket_repository import TicketRepository
from discord_ui.panel_embeds import build_staff_control_panel_embed
from discord_ui.staff_panel_view import StaffPanelView
from runtime.locks import LockManager
from services.capacity_service import CapacityService
from services.draft_service import DraftService
from services.snapshot_service import SnapshotService
from services.staff_permission_service import StaffPermissionService
from services.submission_guard_service import SubmissionContext, SubmissionGuardService

if TYPE_CHECKING:
    from services.queue_service import QueueService, QueueTicketResult


@dataclass(frozen=True, slots=True)
class SubmitDraftResult:
    ticket: TicketRecord
    old_channel_name: str
    new_channel_name: str
    divider_message: Any | None
    staff_panel_message: Any | None
    welcome_message_updated: bool
    channel_name_changed: bool
    outcome: str
    queue_position: int | None = None
    active_count: int | None = None
    max_open_tickets: int | None = None


@dataclass(frozen=True, slots=True)
class SubmissionPlan:
    context: SubmissionContext
    ticket: TicketRecord
    target_channel_name: str
    outcome: str
    from_queue: bool
    queue_position: int | None = None
    active_count: int | None = None
    max_open_tickets: int | None = None


class SubmitService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        guard_service: SubmissionGuardService | None = None,
        ticket_repository: TicketRepository | None = None,
        lock_manager: LockManager | None = None,
        permission_service: StaffPermissionService | None = None,
        snapshot_service: SnapshotService | None = None,
        capacity_service: CapacityService | None = None,
        queue_service: QueueService | None = None,
    ) -> None:
        self.database = database
        self.ticket_repository = ticket_repository or TicketRepository(database)
        self.guard_service = guard_service or SubmissionGuardService(
            database,
            ticket_repository=self.ticket_repository,
        )
        self.lock_manager = lock_manager
        self.permission_service = permission_service or StaffPermissionService()
        self.snapshot_service = snapshot_service
        self.capacity_service = capacity_service or CapacityService(
            database,
            ticket_repository=self.ticket_repository,
        )
        self.queue_service = queue_service
        self.logger = logging.getLogger(__name__)

    async def submit_draft_ticket(
        self,
        channel: Any,
        *,
        actor_id: int,
        requested_title: str | None = None,
        welcome_message: Any | None = None,
    ) -> SubmitDraftResult:
        channel_id = getattr(channel, "id", None)
        if channel_id is None:
            raise ValidationError("当前频道不支持 ticket submit。")

        async with self._acquire_channel_lock(channel_id):
            initial_context = self.guard_service.inspect_submission(
                channel_id=channel_id,
                actor_id=actor_id,
                channel_name=getattr(channel, "name", None),
            )
            async with self._acquire_guild_submission_lock(initial_context.ticket.guild_id):
                old_channel_name = str(getattr(channel, "name", ""))
                plan = self._plan_submit_transition(
                    channel_id=channel_id,
                    actor_id=actor_id,
                    channel_name=old_channel_name,
                    requested_title=requested_title,
                )
                return await self._apply_submission_plan(
                    channel=channel,
                    plan=plan,
                    old_channel_name=old_channel_name,
                    requested_title=requested_title,
                    welcome_message=welcome_message,
                )

    async def promote_queued_ticket(
        self,
        channel: Any,
        *,
        ticket_id: str,
    ) -> SubmitDraftResult | None:
        channel_id = getattr(channel, "id", None)
        if channel_id is None:
            return None

        async with self._acquire_channel_lock(channel_id):
            initial_context = self.guard_service.inspect_queued_promotion(
                ticket_id=ticket_id,
                channel_id=channel_id,
            )
            async with self._acquire_guild_submission_lock(initial_context.ticket.guild_id):
                old_channel_name = str(getattr(channel, "name", ""))
                plan = self._plan_queue_promotion(
                    channel_id=channel_id,
                    channel_name=old_channel_name,
                    ticket_id=ticket_id,
                )
                if plan is None:
                    return None

                return await self._apply_submission_plan(
                    channel=channel,
                    plan=plan,
                    old_channel_name=old_channel_name,
                    requested_title=None,
                    welcome_message=None,
                )

    def _plan_submit_transition(
        self,
        *,
        channel_id: int,
        actor_id: int,
        channel_name: str,
        requested_title: str | None,
    ) -> SubmissionPlan:
        with self.database.session() as connection:
            context = self.guard_service.inspect_submission(
                channel_id=channel_id,
                actor_id=actor_id,
                channel_name=channel_name,
                connection=connection,
            )
            target_channel_name = self._build_target_channel_name(channel_name=channel_name, requested_title=requested_title)
            if context.already_submitted:
                return SubmissionPlan(context=context, ticket=context.ticket, target_channel_name=target_channel_name, outcome="already_submitted", from_queue=False)
            if context.already_queued:
                return SubmissionPlan(
                    context=context,
                    ticket=context.ticket,
                    target_channel_name=target_channel_name,
                    outcome="already_queued",
                    from_queue=False,
                    queue_position=self._get_queue_position(context.ticket.ticket_id, connection=connection),
                )
            if context.requires_title and not requested_title:
                raise ValidationError("当前 draft 仍使用默认频道名，请先补充一个简短标题后再提交。")
            capacity = self.capacity_service.build_snapshot(
                guild_id=context.ticket.guild_id,
                max_open_tickets=context.config.max_open_tickets,
                connection=connection,
            )
            if not capacity.has_capacity:
                queue_result = self._enqueue_ticket(context.ticket.ticket_id, connection=connection)
                return SubmissionPlan(
                    context=context,
                    ticket=queue_result.ticket,
                    target_channel_name=target_channel_name,
                    outcome="queued",
                    from_queue=False,
                    queue_position=queue_result.position,
                    active_count=capacity.active_count,
                    max_open_tickets=context.config.max_open_tickets,
                )
            updated_ticket = self._commit_submitted_ticket(context.ticket.ticket_id, connection=connection) or context.ticket
            return SubmissionPlan(context=context, ticket=updated_ticket, target_channel_name=target_channel_name, outcome="submitted", from_queue=False)

    def _plan_queue_promotion(
        self,
        *,
        channel_id: int,
        channel_name: str,
        ticket_id: str,
    ) -> SubmissionPlan | None:
        with self.database.session() as connection:
            context = self.guard_service.inspect_queued_promotion(ticket_id=ticket_id, channel_id=channel_id, connection=connection)
            capacity = self.capacity_service.build_snapshot(
                guild_id=context.ticket.guild_id,
                max_open_tickets=context.config.max_open_tickets,
                connection=connection,
            )
            if not capacity.has_capacity:
                return None
            updated_ticket = self._commit_submitted_ticket(context.ticket.ticket_id, connection=connection) or context.ticket
            return SubmissionPlan(context=context, ticket=updated_ticket, target_channel_name=channel_name, outcome="submitted", from_queue=True)

    async def _apply_submission_plan(
        self,
        *,
        channel: Any,
        plan: SubmissionPlan,
        old_channel_name: str,
        requested_title: str | None,
        welcome_message: Any | None,
    ) -> SubmitDraftResult:
        if plan.outcome in {"queued", "already_queued"}:
            return await self._run_queued_side_effects(
                channel=channel,
                plan=plan,
                old_channel_name=old_channel_name,
                requested_title=requested_title,
                welcome_message=welcome_message,
            )
        return await self._run_submitted_side_effects(
            channel=channel,
            plan=plan,
            old_channel_name=old_channel_name,
            requested_title=requested_title,
            welcome_message=welcome_message,
        )

    async def _run_submitted_side_effects(
        self,
        *,
        channel: Any,
        plan: SubmissionPlan,
        old_channel_name: str,
        requested_title: str | None,
        welcome_message: Any | None,
    ) -> SubmitDraftResult:
        should_retry_rename = plan.outcome == "submitted" or requested_title is not None
        new_channel_name = await self._rename_channel(
            channel=channel,
            current_name=old_channel_name,
            next_name=plan.target_channel_name,
            reason=(f"Promote queued ticket {plan.ticket.ticket_id}" if plan.from_queue else f"Submit draft ticket {plan.ticket.ticket_id}"),
            enabled=should_retry_rename,
        )
        await self._grant_staff_access(channel=channel, config=plan.context.config, category=plan.context.category)
        updated_ticket = plan.ticket
        if self.snapshot_service is not None:
            bootstrap_result = await self.snapshot_service.bootstrap_from_channel_history(updated_ticket, channel)
            updated_ticket = bootstrap_result.ticket
        divider_message = None
        if plan.outcome == "submitted":
            divider_message = await self._send_submission_divider(channel, updated_ticket, from_queue=plan.from_queue)
        updated_ticket, staff_panel_message = await self._ensure_staff_control_panel(
            channel=channel,
            ticket=updated_ticket,
            category=plan.context.category,
            config=plan.context.config,
        )
        resolved_welcome_message = welcome_message or await self._resolve_welcome_message(channel, ticket_id=updated_ticket.ticket_id)
        welcome_message_updated = await self._remove_welcome_view(resolved_welcome_message)
        return SubmitDraftResult(
            ticket=updated_ticket,
            old_channel_name=old_channel_name,
            new_channel_name=new_channel_name,
            divider_message=divider_message,
            staff_panel_message=staff_panel_message,
            welcome_message_updated=welcome_message_updated,
            channel_name_changed=new_channel_name != old_channel_name,
            outcome=plan.outcome,
        )

    async def _run_queued_side_effects(
        self,
        *,
        channel: Any,
        plan: SubmissionPlan,
        old_channel_name: str,
        requested_title: str | None,
        welcome_message: Any | None,
    ) -> SubmitDraftResult:
        new_channel_name = await self._rename_channel(
            channel=channel,
            current_name=old_channel_name,
            next_name=plan.target_channel_name,
            reason=f"Queue ticket {plan.ticket.ticket_id} after submit request",
            enabled=(plan.outcome == "queued" or requested_title is not None),
        )
        resolved_welcome_message = welcome_message or await self._resolve_welcome_message(channel, ticket_id=plan.ticket.ticket_id)
        welcome_message_updated = await self._remove_welcome_view(resolved_welcome_message)
        return SubmitDraftResult(
            ticket=plan.ticket,
            old_channel_name=old_channel_name,
            new_channel_name=new_channel_name,
            divider_message=None,
            staff_panel_message=None,
            welcome_message_updated=welcome_message_updated,
            channel_name_changed=new_channel_name != old_channel_name,
            outcome=plan.outcome,
            queue_position=plan.queue_position,
            active_count=plan.active_count,
            max_open_tickets=plan.max_open_tickets,
        )

    async def _rename_channel(
        self,
        *,
        channel: Any,
        current_name: str,
        next_name: str,
        reason: str,
        enabled: bool,
    ) -> str:
        if not enabled or next_name == current_name:
            return current_name
        await channel.edit(name=next_name, reason=reason)
        return next_name

    async def _ensure_staff_control_panel(
        self,
        *,
        channel: Any,
        ticket: TicketRecord,
        category: TicketCategoryConfig,
        config: GuildConfigRecord,
    ) -> tuple[TicketRecord, Any | None]:
        if ticket.staff_panel_message_id is not None:
            return ticket, None
        staff_panel_message = await self._send_staff_control_panel(channel, ticket=ticket, category=category, config=config)
        if staff_panel_message is None:
            return ticket, None
        updated_ticket = self.ticket_repository.update(ticket.ticket_id, staff_panel_message_id=getattr(staff_panel_message, "id", None)) or ticket
        return updated_ticket, staff_panel_message

    def _commit_submitted_ticket(
        self,
        ticket_id: str,
        *,
        connection: sqlite3.Connection,
    ) -> TicketRecord | None:
        return self.ticket_repository.update(ticket_id, status=TicketStatus.SUBMITTED, queued_at=None, connection=connection)

    @staticmethod
    def _build_target_channel_name(*, channel_name: str, requested_title: str | None) -> str:
        if not requested_title:
            return channel_name
        return DraftService.build_renamed_channel_name(requested_name=requested_title)

    async def _grant_staff_access(
        self,
        *,
        channel: Any,
        config: GuildConfigRecord,
        category: TicketCategoryConfig,
    ) -> None:
        await self.permission_service.apply_ticket_permissions(
            channel,
            include_participants=False,
            config=config,
            category=category,
            visible_reason=f"Open submitted ticket {getattr(channel, 'id', 'unknown')} to staff",
        )

    async def _send_submission_divider(
        self,
        channel: Any,
        ticket: TicketRecord,
        *,
        from_queue: bool,
    ) -> Any | None:
        send = getattr(channel, "send", None)
        if send is None:
            return None
        return await send(
            content=(
                "━━━━━━━━━━━━━━━━━━\n"
                + (
                    "✅ 您的 Ticket 已从排队中自动提交 ！\n\n相关管理员现在可以查看并处理。\n\n=== 草稿期分界线 ===\n"
                    if from_queue
                    else "✅ 您的 Ticket 已成功提交 ！\n\n请稍候，相关管理员会前来处理。\n\n在此期间，请勿重复提交相同主题的Ticket。感谢您的理解和支持。\n\n=== 草稿期分界线 ===\n"
                )
                + "━━━━━━━━━━━━━━━━━━"
            )
        )

    async def _send_staff_control_panel(
        self,
        channel: Any,
        *,
        ticket: TicketRecord,
        category: TicketCategoryConfig,
        config: GuildConfigRecord,
    ) -> Any | None:
        send = getattr(channel, "send", None)
        if send is None:
            return None

        return await send(
            embed=build_staff_control_panel_embed(ticket, category=category, config=config),
            view=StaffPanelView(),
        )

    async def _resolve_welcome_message(self, channel: Any, *, ticket_id: str) -> Any | None:
        pins = getattr(channel, "pins", None)
        if pins is None:
            return None

        try:
            pinned_messages = await pins()
        except Exception:
            return None

        if not pinned_messages:
            return None

        return pinned_messages[0]

    @staticmethod
    async def _remove_welcome_view(message: Any | None) -> bool:
        if message is None:
            return False

        edit = getattr(message, "edit", None)
        if edit is None:
            return False

        try:
            await edit(view=None)
        except Exception:
            return False
        return True

    def _enqueue_ticket(
        self,
        ticket_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> QueueTicketResult:
        if self.queue_service is not None:
            return self.queue_service.enqueue_ticket(ticket_id, connection=connection)

        from services.queue_service import QueueTicketResult

        queued_ticket = self.ticket_repository.update(
            ticket_id,
            status=TicketStatus.QUEUED,
            queued_at=utc_now_iso(),
            connection=connection,
        )
        if queued_ticket is None:
            raise ValidationError("当前 ticket 已不存在，无法加入排队。")
        return QueueTicketResult(ticket=queued_ticket, position=1)

    def _get_queue_position(
        self,
        ticket_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> int | None:
        if self.queue_service is not None:
            return self.queue_service.get_queue_position(ticket_id, connection=connection)
        return None

    @asynccontextmanager
    async def _acquire_channel_lock(self, channel_id: int) -> AsyncIterator[None]:
        if self.lock_manager is None:
            yield
            return

        async with self.lock_manager.acquire(f"draft-submit:{channel_id}"):
            yield

    @asynccontextmanager
    async def _acquire_guild_submission_lock(self, guild_id: int) -> AsyncIterator[None]:
        if self.lock_manager is None:
            yield
            return

        async with self.lock_manager.acquire(f"ticket-submit-guild:{guild_id}"):
            yield
