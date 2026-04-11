from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from collections.abc import AsyncIterator

from core.enums import TicketStatus
from core.errors import (
    InvalidTicketStateError,
    PermissionDeniedError,
    TicketNotFoundError,
    ValidationError,
)
from core.models import TicketRecord
from db.connection import DatabaseManager
from db.repositories.base import utc_now_iso
from db.repositories.ticket_repository import TicketRepository
from runtime.locks import LockManager


@dataclass(frozen=True, slots=True)
class DraftRenameResult:
    ticket: TicketRecord
    old_name: str
    new_name: str
    changed: bool


@dataclass(frozen=True, slots=True)
class DraftAbandonResult:
    ticket: TicketRecord
    channel_deleted: bool


class DraftService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        ticket_repository: TicketRepository | None = None,
        lock_manager: LockManager | None = None,
    ) -> None:
        self.database = database
        self.ticket_repository = ticket_repository or TicketRepository(database)
        self.lock_manager = lock_manager

    async def rename_draft_ticket(
        self,
        channel: Any,
        *,
        actor_id: int,
        requested_name: str,
    ) -> DraftRenameResult:
        async with self._acquire_channel_lock(channel.id):
            ticket = self._require_draft_ticket(channel.id)
            self._assert_draft_owner(ticket, actor_id)

            old_name = getattr(channel, "name", "") or ""
            new_name = self.build_renamed_channel_name(ticket=ticket, requested_name=requested_name)
            if new_name == old_name:
                return DraftRenameResult(
                    ticket=ticket,
                    old_name=old_name,
                    new_name=new_name,
                    changed=False,
                )

            await channel.edit(name=new_name, reason=f"Rename draft ticket {ticket.ticket_id}")
            updated_ticket = (
                self.ticket_repository.update(
                    ticket.ticket_id,
                    updated_at=utc_now_iso(),
                )
                or ticket
            )
            return DraftRenameResult(
                ticket=updated_ticket,
                old_name=old_name,
                new_name=new_name,
                changed=True,
            )

    async def abandon_draft_ticket(
        self,
        channel: Any,
        *,
        actor_id: int,
    ) -> DraftAbandonResult:
        async with self._acquire_channel_lock(channel.id):
            ticket = self._require_draft_ticket(channel.id)
            self._assert_draft_owner(ticket, actor_id)

            updated_ticket = (
                self.ticket_repository.update(
                    ticket.ticket_id,
                    status=TicketStatus.ABANDONED,
                )
                or ticket
            )
            try:
                await channel.delete(reason=f"Abandon draft ticket {ticket.ticket_id}")
            except Exception:
                self.ticket_repository.update(
                    ticket.ticket_id,
                    status=TicketStatus.DRAFT,
                    updated_at=utc_now_iso(),
                )
                raise

            return DraftAbandonResult(ticket=updated_ticket, channel_deleted=True)

    def _require_draft_ticket(self, channel_id: int) -> TicketRecord:
        ticket = self.ticket_repository.get_by_channel_id(channel_id)
        if ticket is None:
            raise TicketNotFoundError("当前频道不是已登记的 ticket。")
        if ticket.status != TicketStatus.DRAFT:
            raise InvalidTicketStateError("当前 ticket 不处于 draft 状态，无法执行此操作。")
        return ticket

    @staticmethod
    def _assert_draft_owner(ticket: TicketRecord, actor_id: int) -> None:
        if ticket.creator_id != actor_id:
            raise PermissionDeniedError("只有 ticket 创建者可以在 draft 阶段执行此操作。")

    @staticmethod
    def build_renamed_channel_name(*, ticket: TicketRecord, requested_name: str) -> str:
        normalized = DraftService._slugify(requested_name)
        if not normalized:
            raise ValidationError("新的 draft 标题不能为空。")

        ticket_number = DraftService._extract_ticket_number(ticket.ticket_id)
        prefix = f"ticket-{ticket_number}-" if ticket_number else "ticket-"
        max_slug_length = max(1, 95 - len(prefix))
        trimmed_slug = normalized[:max_slug_length].strip("-") or "draft"
        return f"{prefix}{trimmed_slug}"[:95]

    @staticmethod
    def _extract_ticket_number(ticket_id: str) -> str:
        candidate = ticket_id.rsplit("-", 1)[-1]
        return candidate if candidate.isdigit() else ""

    @staticmethod
    def _slugify(value: str) -> str:
        stripped = value.strip()
        if not stripped:
            return ""

        normalized = [character.lower() if character.isalnum() else "-" for character in stripped]
        slug = "".join(normalized).strip("-")
        while "--" in slug:
            slug = slug.replace("--", "-")
        return slug

    @asynccontextmanager
    async def _acquire_channel_lock(self, channel_id: int) -> AsyncIterator[None]:
        if self.lock_manager is None:
            yield
            return

        async with self.lock_manager.acquire(f"draft-channel:{channel_id}"):
            yield
