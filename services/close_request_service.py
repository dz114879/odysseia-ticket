from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.constants import CLOSE_REQUEST_TIMEOUT_SECONDS
from core.enums import TicketStatus
from core.errors import (
    PermissionDeniedError,
    StaleInteractionError,
    ValidationError,
)
from core.models import TicketRecord
from db.connection import DatabaseManager
from db.repositories.ticket_repository import TicketRepository
from services.close_service import CloseService, CloseMutationResult
from services.staff_guard_service import StaffGuardService
from discord_ui.close_embeds import (
    build_close_request_embed,
    build_close_request_status_embed,
)
from discord_ui.close_views import CloseRequestView


@dataclass(frozen=True, slots=True)
class CloseRequestCreationResult:
    ticket: TicketRecord
    request_message: Any | None
    replaced_message_id: int | None
    requested_by_id: int
    reason: str | None


class CloseRequestService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        close_service: CloseService,
        ticket_repository: TicketRepository | None = None,
        guard_service: StaffGuardService | None = None,
    ) -> None:
        self.database = database
        self.close_service = close_service
        self.ticket_repository = ticket_repository or TicketRepository(database)
        self.guard_service = guard_service or StaffGuardService(
            database,
            ticket_repository=self.ticket_repository,
        )
        self._pending_request_message_ids: dict[int, int] = {}

    async def request_close(
        self,
        channel: Any,
        *,
        actor: Any,
        reason: str | None = None,
    ) -> CloseRequestCreationResult:
        channel_id = getattr(channel, "id", None)
        actor_id = getattr(actor, "id", None)
        send = getattr(channel, "send", None)
        if channel_id is None or send is None:
            raise ValidationError("当前频道不支持发起关闭请求。")
        if actor_id is None:
            raise ValidationError("无法识别当前操作人的身份。")

        context = self.guard_service.load_ticket_context(
            channel_id,
            allowed_statuses=(TicketStatus.SUBMITTED, TicketStatus.SLEEP),
            invalid_state_message="当前 ticket 仅在 submitted / sleep 状态可请求关闭。",
        )
        if context.ticket.creator_id != actor_id:
            raise PermissionDeniedError("只有 ticket 创建者可以发起关闭请求；staff 请直接使用 `/ticket close`。")

        normalized_reason = self._normalize_reason(reason)
        replaced_message_id = self._pending_request_message_ids.get(channel_id)
        if replaced_message_id is not None:
            await self._mark_existing_request(
                channel,
                message_id=replaced_message_id,
                ticket=context.ticket,
                requester_id=actor_id,
                reason=normalized_reason,
                status_text="该关闭请求已被新的请求替换。",
            )

        view = CloseRequestView(
            service=self,
            requester_id=actor_id,
            request_reason=normalized_reason,
            channel_id=channel_id,
            timeout=CLOSE_REQUEST_TIMEOUT_SECONDS,
        )
        request_message = await send(
            embed=build_close_request_embed(
                context.ticket,
                requester_id=actor_id,
                reason=normalized_reason,
            ),
            view=view,
        )
        view.bind_message(request_message)
        self._pending_request_message_ids[channel_id] = getattr(request_message, "id", 0)
        return CloseRequestCreationResult(
            ticket=context.ticket,
            request_message=request_message,
            replaced_message_id=replaced_message_id,
            requested_by_id=actor_id,
            reason=normalized_reason,
        )

    async def approve_request(
        self,
        channel: Any,
        *,
        actor: Any,
        request_message: Any,
        requester_id: int,
        reason: str | None,
        is_bot_owner: bool = False,
    ) -> CloseMutationResult:
        channel_id = getattr(channel, "id", None)
        message_id = getattr(request_message, "id", None)
        if channel_id is None or message_id is None:
            raise ValidationError("无法识别当前关闭请求消息。")

        self.assert_current_request(channel_id, message_id)
        result = await self.close_service.initiate_close(
            channel,
            actor=actor,
            reason=reason,
            requested_by_id=requester_id,
            is_bot_owner=is_bot_owner,
        )
        self.clear_pending_request(channel_id, message_id=message_id)
        await self._edit_request_message(
            request_message,
            ticket=result.ticket,
            requester_id=requester_id,
            reason=reason,
            status_text=f"该关闭请求已被 <@{getattr(actor, 'id', 'unknown')}> 同意，并进入 closing 流程。",
        )
        return result

    async def reject_request(
        self,
        channel: Any,
        *,
        actor: Any,
        request_message: Any,
        requester_id: int,
        reason: str | None,
        is_bot_owner: bool = False,
    ) -> None:
        channel_id = getattr(channel, "id", None)
        message_id = getattr(request_message, "id", None)
        actor_id = getattr(actor, "id", None)
        if channel_id is None or message_id is None or actor_id is None:
            raise ValidationError("无法识别当前关闭请求消息。")

        self.assert_current_request(channel_id, message_id)
        context = self.guard_service.load_ticket_context(
            channel_id,
            allowed_statuses=(TicketStatus.SUBMITTED, TicketStatus.SLEEP),
            invalid_state_message="当前 ticket 已不再接受关闭请求操作。",
        )
        self.guard_service.assert_staff_actor(
            actor,
            config=context.config,
            category=context.category,
            is_bot_owner=is_bot_owner,
        )
        self.clear_pending_request(channel_id, message_id=message_id)
        await self._edit_request_message(
            request_message,
            ticket=context.ticket,
            requester_id=requester_id,
            reason=reason,
            status_text=f"该关闭请求已被 <@{actor_id}> 拒绝。",
        )
        send = getattr(channel, "send", None)
        if send is not None:
            await send(content=(f"🙅 <@{actor_id}> 已拒绝 <@{requester_id}> 对 ticket `{context.ticket.ticket_id}` 的关闭请求。"))

    async def expire_request_message(
        self,
        *,
        channel_id: int,
        message: Any,
        requester_id: int,
        reason: str | None,
    ) -> None:
        message_id = getattr(message, "id", None)
        if message_id is None:
            return
        if self._pending_request_message_ids.get(channel_id) != message_id:
            return
        ticket = self.ticket_repository.get_by_channel_id(channel_id)
        if ticket is None:
            self.clear_pending_request(channel_id, message_id=message_id)
            return
        self.clear_pending_request(channel_id, message_id=message_id)
        await self._edit_request_message(
            message,
            ticket=ticket,
            requester_id=requester_id,
            reason=reason,
            status_text="该关闭请求已超时失效。",
        )

    async def dismiss_pending_request(
        self,
        channel: Any,
        *,
        handled_by_id: int,
    ) -> None:
        channel_id = getattr(channel, "id", None)
        if channel_id is None:
            return
        pending_message_id = self._pending_request_message_ids.get(channel_id)
        if pending_message_id is None:
            return

        ticket = self.ticket_repository.get_by_channel_id(channel_id)
        if ticket is None:
            self.clear_pending_request(channel_id)
            return

        message = await self._resolve_message(channel, pending_message_id)
        self.clear_pending_request(channel_id, message_id=pending_message_id)
        if message is None:
            return
        await self._edit_request_message(
            message,
            ticket=ticket,
            requester_id=ticket.creator_id,
            reason=None,
            status_text=f"该关闭请求已由 <@{handled_by_id}> 直接通过 `/ticket close` 处理。",
        )

    def assert_current_request(self, channel_id: int, message_id: int) -> None:
        current_message_id = self._pending_request_message_ids.get(channel_id)
        if current_message_id is None or current_message_id != message_id:
            raise StaleInteractionError("该关闭请求已过期、被替换或已处理，请使用最新消息。")

    def clear_pending_request(self, channel_id: int, *, message_id: int | None = None) -> None:
        current_message_id = self._pending_request_message_ids.get(channel_id)
        if current_message_id is None:
            return
        if message_id is not None and current_message_id != message_id:
            return
        self._pending_request_message_ids.pop(channel_id, None)

    async def _mark_existing_request(
        self,
        channel: Any,
        *,
        message_id: int,
        ticket: TicketRecord,
        requester_id: int,
        reason: str | None,
        status_text: str,
    ) -> None:
        message = await self._resolve_message(channel, message_id)
        self.clear_pending_request(getattr(channel, "id", 0), message_id=message_id)
        if message is None:
            return
        await self._edit_request_message(
            message,
            ticket=ticket,
            requester_id=requester_id,
            reason=reason,
            status_text=status_text,
        )

    async def _edit_request_message(
        self,
        message: Any,
        *,
        ticket: TicketRecord,
        requester_id: int,
        reason: str | None,
        status_text: str,
    ) -> None:
        edit = getattr(message, "edit", None)
        if edit is None:
            return
        try:
            await edit(
                embed=build_close_request_status_embed(
                    ticket,
                    requester_id=requester_id,
                    reason=reason,
                    status_text=status_text,
                ),
                view=None,
            )
        except Exception:
            return

    async def _resolve_message(self, channel: Any, message_id: int) -> Any | None:
        fetch_message = getattr(channel, "fetch_message", None)
        if callable(fetch_message):
            try:
                return await fetch_message(message_id)
            except Exception:
                return None
        return None

    @staticmethod
    def _normalize_reason(reason: str | None) -> str | None:
        normalized = (reason or "").strip()
        return normalized or None
