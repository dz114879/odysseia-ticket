from __future__ import annotations

from typing import Any

from core.models import TicketRecord
from db.repositories.ticket_mute_repository import TicketMuteRepository
from services.staff_guard_service import StaffTicketContext
from services.staff_permission_service import StaffPermissionService


class ClosePermissionSupport:
    def __init__(
        self,
        *,
        permission_service: StaffPermissionService,
        ticket_mute_repository: TicketMuteRepository,
    ) -> None:
        self.permission_service = permission_service
        self.ticket_mute_repository = ticket_mute_repository

    async def freeze_ticket_permissions(self, channel: Any, *, context: StaffTicketContext) -> None:
        guild = getattr(channel, "guild", None)
        set_permissions = getattr(channel, "set_permissions", None)
        if guild is None or set_permissions is None:
            return

        readonly_overwrite = self.permission_service.build_participant_overwrite(can_send=False)
        targets = self.permission_service.resolve_staff_targets(
            guild,
            config=context.config,
            category=context.category,
        )
        creator = self._resolve_channel_member(channel, context.ticket.creator_id)
        active_claimer = self._resolve_channel_member(channel, context.ticket.claimed_by)
        muted_participants = self._resolve_muted_participants(channel, ticket_id=context.ticket.ticket_id)

        unique_targets: list[Any] = []
        seen_ids: set[int] = set()
        for target in [*targets, creator, active_claimer, *muted_participants]:
            target_id = getattr(target, "id", None)
            if target is None or target_id is None or target_id in seen_ids:
                continue
            seen_ids.add(target_id)
            unique_targets.append(target)

        for target in unique_targets:
            await set_permissions(
                target,
                overwrite=readonly_overwrite,
                reason=f"Lock ticket {context.ticket.ticket_id} during closing window",
            )

    async def restore_ticket_permissions(
        self,
        channel: Any,
        *,
        context: StaffTicketContext,
        ticket: TicketRecord,
    ) -> None:
        creator = self._resolve_channel_member(channel, ticket.creator_id)
        active_claimer = self._resolve_channel_member(channel, ticket.claimed_by)
        muted_participants = self._resolve_muted_participants(channel, ticket_id=ticket.ticket_id)
        await self.permission_service.apply_ticket_permissions(
            channel,
            config=context.config,
            category=context.category,
            active_claimer=active_claimer,
            creator=creator,
            muted_participants=muted_participants,
            visible_reason=f"Restore staff access after closing revoke for {ticket.ticket_id}",
            creator_reason=f"Restore creator access after closing revoke for {ticket.ticket_id}",
            muted_reason=f"Preserve muted participant state after closing revoke for {ticket.ticket_id}",
        )

    def _resolve_muted_participants(self, channel: Any, *, ticket_id: str) -> list[Any]:
        return [
            member
            for record in self.ticket_mute_repository.list_by_ticket(ticket_id)
            if (member := self._resolve_channel_member(channel, record.user_id)) is not None
        ]

    @staticmethod
    def _resolve_channel_member(channel: Any, user_id: int | None) -> Any | None:
        if user_id is None:
            return None
        guild = getattr(channel, "guild", None)
        get_member = getattr(guild, "get_member", None)
        if not callable(get_member):
            return None
        return get_member(user_id)
