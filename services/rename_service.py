from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from core.constants import SLEEP_CHANNEL_PREFIX
from core.enums import TicketStatus
from core.errors import ValidationError
from db.connection import DatabaseManager
from db.repositories.base import utc_now_iso
from db.repositories.ticket_repository import TicketRepository
from runtime.locks import LockManager
from services.draft_service import DraftService
from services.priority_service import PRIORITY_CHANNEL_PREFIXES
from services.staff_guard_service import StaffGuardService


@dataclass(frozen=True, slots=True)
class TicketRenameResult:
    ticket: Any
    old_name: str
    new_name: str
    changed: bool
    log_message: Any | None


class RenameService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        ticket_repository: TicketRepository | None = None,
        lock_manager: LockManager | None = None,
        guard_service: StaffGuardService | None = None,
    ) -> None:
        self.database = database
        self.ticket_repository = ticket_repository or TicketRepository(database)
        self.lock_manager = lock_manager
        self.guard_service = guard_service or StaffGuardService(
            database,
            ticket_repository=self.ticket_repository,
        )

    async def rename_ticket(
        self,
        channel: Any,
        *,
        actor: Any,
        requested_name: str,
        is_bot_owner: bool = False,
    ) -> TicketRenameResult:
        channel_id = getattr(channel, "id", None)
        edit = getattr(channel, "edit", None)
        if channel_id is None or edit is None:
            raise ValidationError("当前频道不支持 ticket rename。")

        actor_id = getattr(actor, "id", None)
        if actor_id is None:
            raise ValidationError("无法识别当前操作人的身份。")

        async with self._acquire_channel_lock(channel_id):
            context = self.guard_service.load_ticket_context(
                channel_id,
                allowed_statuses=(TicketStatus.SUBMITTED, TicketStatus.SLEEP),
                invalid_state_message="当前 ticket 仅在 submitted / sleep 状态可修改标题。",
            )
            self.guard_service.assert_staff_actor(
                actor,
                config=context.config,
                category=context.category,
                is_bot_owner=is_bot_owner,
            )

            old_name = str(getattr(channel, "name", "") or "")
            new_name = self.build_renamed_channel_name(
                ticket=context.ticket,
                current_channel_name=old_name,
                requested_name=requested_name,
            )
            if new_name == old_name:
                return TicketRenameResult(
                    ticket=context.ticket,
                    old_name=old_name,
                    new_name=new_name,
                    changed=False,
                    log_message=None,
                )

            await edit(
                name=new_name,
                reason=f"Rename ticket {context.ticket.ticket_id} in {context.ticket.status.value} state",
            )
            updated_ticket = (
                self.ticket_repository.update(
                    context.ticket.ticket_id,
                    updated_at=utc_now_iso(),
                )
                or context.ticket
            )
            log_message = await self._send_channel_log(
                channel,
                content=(f"✏️ <@{actor_id}> 已修改 ticket `{context.ticket.ticket_id}` 的标题。\n- 旧频道名：`{old_name}`\n- 新频道名：`{new_name}`"),
            )
            return TicketRenameResult(
                ticket=updated_ticket,
                old_name=old_name,
                new_name=new_name,
                changed=True,
                log_message=log_message,
            )

    @staticmethod
    def build_renamed_channel_name(
        *,
        ticket: Any,
        current_channel_name: str,
        requested_name: str,
    ) -> str:
        prefix = RenameService._detect_preserved_prefix(current_channel_name)
        normalized = DraftService._slugify(requested_name)
        if not normalized:
            raise ValidationError("新的 ticket 标题不能为空，也不能只包含 emoji 或符号。")

        ticket_number = DraftService._extract_ticket_number(ticket.ticket_id)
        ticket_prefix = f"ticket-{ticket_number}-" if ticket_number else "ticket-"
        max_slug_length = max(1, 95 - len(prefix) - len(ticket_prefix))
        trimmed_slug = normalized[:max_slug_length].strip("-") or "ticket"
        return f"{prefix}{ticket_prefix}{trimmed_slug}"[:95]

    @staticmethod
    def _detect_preserved_prefix(channel_name: str) -> str:
        if channel_name.startswith(SLEEP_CHANNEL_PREFIX):
            return SLEEP_CHANNEL_PREFIX
        for prefix in sorted(PRIORITY_CHANNEL_PREFIXES.values(), key=len, reverse=True):
            if channel_name.startswith(prefix):
                return prefix
        return ""

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

        async with self.lock_manager.acquire(f"ticket-rename:{channel_id}"):
            yield
