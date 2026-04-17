from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.models import GuildConfigRecord, TicketCategoryConfig
from db.repositories.ticket_mute_repository import TicketMuteRepository
from services.staff_permission_service import StaffPermissionService


@dataclass(frozen=True, slots=True)
class TransferPermissionContext:
    config: GuildConfigRecord
    previous_category: TicketCategoryConfig | None
    target_category: TicketCategoryConfig
    previous_claimer_id: int | None


async def resolve_channel(bot: Any | None, channel_id: int | None) -> Any | None:
    if bot is None or channel_id is None:
        return None

    channel = getattr(bot, "get_channel", lambda _: None)(channel_id)
    if channel is not None:
        return channel

    fetch_channel = getattr(bot, "fetch_channel", None)
    if fetch_channel is None:
        return None

    try:
        return await fetch_channel(channel_id)
    except Exception:
        return None


async def sync_transfer_permissions(
    permission_service: StaffPermissionService,
    ticket_mute_repository: TicketMuteRepository,
    *,
    channel: Any,
    context: TransferPermissionContext,
    ticket: Any,
) -> None:
    creator = resolve_channel_member(channel, getattr(ticket, "creator_id", 0))
    muted_participants = resolve_muted_participants(
        channel,
        ticket_mute_repository=ticket_mute_repository,
        ticket_id=ticket.ticket_id,
    )
    await permission_service.apply_ticket_permissions(
        channel,
        config=context.config,
        category=context.target_category,
        creator=creator,
        participants=muted_participants,
        muted_participants=muted_participants,
        previous_claimer_id=context.previous_claimer_id,
        hidden_categories=(context.previous_category,),
        visible_reason="Grant new category staff access after ticket transfer execution",
        hidden_reason="Hide previous category staff after ticket transfer execution",
    )


def resolve_muted_participants(
    channel: Any,
    *,
    ticket_mute_repository: TicketMuteRepository,
    ticket_id: str,
) -> list[Any]:
    return [
        member
        for member in (resolve_channel_member(channel, record.user_id) for record in ticket_mute_repository.list_by_ticket(ticket_id))
        if member is not None
    ]


def resolve_channel_member(channel: Any, user_id: int | None) -> Any | None:
    if user_id is None:
        return None
    guild = getattr(channel, "guild", None)
    if guild is None:
        return None
    get_member = getattr(guild, "get_member", None)
    if not callable(get_member):
        return None
    return get_member(user_id)
