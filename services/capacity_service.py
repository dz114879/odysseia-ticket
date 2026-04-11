from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from core.enums import TicketStatus
from db.connection import DatabaseManager
from db.repositories.ticket_repository import TicketRepository


ACTIVE_CAPACITY_STATUSES = (
    TicketStatus.SUBMITTED,
    TicketStatus.TRANSFERRING,
    TicketStatus.CLOSING,
    TicketStatus.ARCHIVING,
    TicketStatus.ARCHIVE_SENT,
)


@dataclass(frozen=True, slots=True)
class CapacitySnapshot:
    guild_id: int
    max_open_tickets: int
    active_count: int
    available_slots: int
    has_capacity: bool


class CapacityService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        ticket_repository: TicketRepository | None = None,
    ) -> None:
        self.database = database
        self.ticket_repository = ticket_repository or TicketRepository(database)

    def build_snapshot(
        self,
        *,
        guild_id: int,
        max_open_tickets: int,
        exclude_ticket_id: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> CapacitySnapshot:
        active_count = self.count_active_tickets(
            guild_id,
            exclude_ticket_id=exclude_ticket_id,
            connection=connection,
        )
        available_slots = max(0, max_open_tickets - active_count)
        return CapacitySnapshot(
            guild_id=guild_id,
            max_open_tickets=max_open_tickets,
            active_count=active_count,
            available_slots=available_slots,
            has_capacity=available_slots > 0,
        )

    def count_active_tickets(
        self,
        guild_id: int,
        *,
        exclude_ticket_id: str | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> int:
        active_tickets = self.ticket_repository.list_by_guild(
            guild_id,
            statuses=ACTIVE_CAPACITY_STATUSES,
            connection=connection,
        )
        return sum(1 for ticket in active_tickets if exclude_ticket_id is None or ticket.ticket_id != exclude_ticket_id)

    @staticmethod
    def is_capacity_consuming_status(status: TicketStatus | None) -> bool:
        return status in ACTIVE_CAPACITY_STATUSES

    @classmethod
    def released_capacity(
        cls,
        previous_status: TicketStatus | None,
        next_status: TicketStatus | None,
    ) -> bool:
        return cls.is_capacity_consuming_status(previous_status) and not cls.is_capacity_consuming_status(next_status)
