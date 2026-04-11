from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator

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
                context = self.guard_service.inspect_submission(
                    channel_id=channel_id,
                    actor_id=actor_id,
                    channel_name=getattr(channel, "name", None),
                )
                old_channel_name = str(getattr(channel, "name", ""))

                if context.already_submitted:
                    resolved_welcome_message = welcome_message or await self._resolve_welcome_message(
                        channel,
                        ticket_id=context.ticket.ticket_id,
                    )
                    welcome_message_updated = await self._remove_welcome_view(resolved_welcome_message)
                    return SubmitDraftResult(
                        ticket=context.ticket,
                        old_channel_name=old_channel_name,
                        new_channel_name=old_channel_name,
                        divider_message=None,
                        staff_panel_message=None,
                        welcome_message_updated=welcome_message_updated,
                        channel_name_changed=False,
                        outcome="already_submitted",
                    )

                if context.already_queued:
                    resolved_welcome_message = welcome_message or await self._resolve_welcome_message(
                        channel,
                        ticket_id=context.ticket.ticket_id,
                    )
                    welcome_message_updated = await self._remove_welcome_view(resolved_welcome_message)
                    queue_position = self._get_queue_position(context.ticket.ticket_id)
                    return SubmitDraftResult(
                        ticket=context.ticket,
                        old_channel_name=old_channel_name,
                        new_channel_name=old_channel_name,
                        divider_message=None,
                        staff_panel_message=None,
                        welcome_message_updated=welcome_message_updated,
                        channel_name_changed=False,
                        outcome="already_queued",
                        queue_position=queue_position,
                    )

                if context.requires_title and not requested_title:
                    raise ValidationError("当前 draft 仍使用默认频道名，请先补充一个简短标题后再提交。")

                capacity = self.capacity_service.build_snapshot(
                    guild_id=context.ticket.guild_id,
                    max_open_tickets=context.config.max_open_tickets,
                )
                if not capacity.has_capacity:
                    new_channel_name = await self._apply_queue_channel_state(
                        channel=channel,
                        context=context,
                        requested_title=requested_title,
                    )
                    queue_result = self._enqueue_ticket(context.ticket.ticket_id)
                    resolved_welcome_message = welcome_message or await self._resolve_welcome_message(
                        channel,
                        ticket_id=context.ticket.ticket_id,
                    )
                    welcome_message_updated = await self._remove_welcome_view(resolved_welcome_message)
                    return SubmitDraftResult(
                        ticket=queue_result.ticket,
                        old_channel_name=old_channel_name,
                        new_channel_name=new_channel_name,
                        divider_message=None,
                        staff_panel_message=None,
                        welcome_message_updated=welcome_message_updated,
                        channel_name_changed=new_channel_name != old_channel_name,
                        outcome="queued",
                        queue_position=queue_result.position,
                        active_count=capacity.active_count,
                        max_open_tickets=context.config.max_open_tickets,
                    )

                return await self._execute_submission(
                    channel=channel,
                    context=context,
                    old_channel_name=old_channel_name,
                    requested_title=requested_title,
                    welcome_message=welcome_message,
                    from_queue=False,
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
                context = self.guard_service.inspect_queued_promotion(
                    ticket_id=ticket_id,
                    channel_id=channel_id,
                )
                capacity = self.capacity_service.build_snapshot(
                    guild_id=context.ticket.guild_id,
                    max_open_tickets=context.config.max_open_tickets,
                )
                if not capacity.has_capacity:
                    return None

                return await self._execute_submission(
                    channel=channel,
                    context=context,
                    old_channel_name=str(getattr(channel, "name", "")),
                    requested_title=None,
                    welcome_message=None,
                    from_queue=True,
                )

    async def _execute_submission(
        self,
        *,
        channel: Any,
        context: SubmissionContext,
        old_channel_name: str,
        requested_title: str | None,
        welcome_message: Any | None,
        from_queue: bool,
    ) -> SubmitDraftResult:
        new_channel_name = await self._apply_submission_channel_state(
            channel=channel,
            context=context,
            requested_title=requested_title,
            from_queue=from_queue,
        )
        updated_ticket = self.ticket_repository.update(
            context.ticket.ticket_id,
            status=TicketStatus.SUBMITTED,
            queued_at=None,
        ) or context.ticket
        if self.snapshot_service is not None:
            bootstrap_result = await self.snapshot_service.bootstrap_from_channel_history(
                updated_ticket,
                channel,
            )
            updated_ticket = bootstrap_result.ticket
        divider_message = await self._send_submission_divider(
            channel,
            updated_ticket,
            from_queue=from_queue,
        )
        staff_panel_message = await self._send_staff_control_panel(
            channel,
            ticket=updated_ticket,
            category=context.category,
            config=context.config,
        )
        if staff_panel_message is not None:
            updated_ticket = self.ticket_repository.update(
                updated_ticket.ticket_id,
                staff_panel_message_id=getattr(staff_panel_message, "id", None),
            ) or updated_ticket
        resolved_welcome_message = welcome_message or await self._resolve_welcome_message(
            channel,
            ticket_id=updated_ticket.ticket_id,
        )
        welcome_message_updated = await self._remove_welcome_view(resolved_welcome_message)

        return SubmitDraftResult(
            ticket=updated_ticket,
            old_channel_name=old_channel_name,
            new_channel_name=new_channel_name,
            divider_message=divider_message,
            staff_panel_message=staff_panel_message,
            welcome_message_updated=welcome_message_updated,
            channel_name_changed=new_channel_name != old_channel_name,
            outcome="submitted",
        )

    async def _apply_submission_channel_state(
        self,
        *,
        channel: Any,
        context: SubmissionContext,
        requested_title: str | None,
        from_queue: bool,
    ) -> str:
        current_name = str(getattr(channel, "name", ""))
        next_name = current_name
        if requested_title:
            next_name = DraftService.build_renamed_channel_name(
                ticket=context.ticket,
                requested_name=requested_title,
            )

        edit_kwargs: dict[str, object] = {
            "topic": self._build_channel_topic(context.ticket, status=TicketStatus.SUBMITTED),
            "reason": (
                f"Promote queued ticket {context.ticket.ticket_id}"
                if from_queue
                else f"Submit draft ticket {context.ticket.ticket_id}"
            ),
        }
        if next_name != current_name:
            edit_kwargs["name"] = next_name
        await channel.edit(**edit_kwargs)

        await self._grant_staff_access(
            channel=channel,
            config=context.config,
            category=context.category,
        )
        return next_name

    async def _apply_queue_channel_state(
        self,
        *,
        channel: Any,
        context: SubmissionContext,
        requested_title: str | None,
    ) -> str:
        current_name = str(getattr(channel, "name", ""))
        next_name = current_name
        if requested_title:
            next_name = DraftService.build_renamed_channel_name(
                ticket=context.ticket,
                requested_name=requested_title,
            )

        edit_kwargs: dict[str, object] = {
            "topic": self._build_channel_topic(context.ticket, status=TicketStatus.QUEUED),
            "reason": f"Queue ticket {context.ticket.ticket_id} after submit request",
        }
        if next_name != current_name:
            edit_kwargs["name"] = next_name
        await channel.edit(**edit_kwargs)
        return next_name

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
                    f"queued ticket `{ticket.ticket_id}` 已自动出队并正式提交，staff 现在可以查看并接手处理。\n"
                    if from_queue
                    else f"draft ticket `{ticket.ticket_id}` 已提交，staff 现在可以查看并接手处理。\n"
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

        for message in pinned_messages:
            content = getattr(message, "content", "") or ""
            if ticket_id in content:
                return message
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

    @staticmethod
    def _build_channel_topic(ticket: TicketRecord, *, status: TicketStatus) -> str:
        return f"ticket_id={ticket.ticket_id} creator_id={ticket.creator_id} status={status.value}"

    def _enqueue_ticket(self, ticket_id: str) -> QueueTicketResult:
        if self.queue_service is not None:
            return self.queue_service.enqueue_ticket(ticket_id)

        from services.queue_service import QueueTicketResult
        queued_ticket = self.ticket_repository.update(
            ticket_id,
            status=TicketStatus.QUEUED,
            queued_at=utc_now_iso(),
        )
        if queued_ticket is None:
            raise ValidationError("当前 ticket 已不存在，无法加入排队。")
        return QueueTicketResult(ticket=queued_ticket, position=1)

    def _get_queue_position(self, ticket_id: str) -> int | None:
        if self.queue_service is not None:
            return self.queue_service.get_queue_position(ticket_id)
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
