from __future__ import annotations

import pytest

from core.enums import ClaimMode, TicketPriority, TicketStatus
from core.models import GuildConfigRecord, TicketMuteRecord, TicketRecord
from db.repositories.counter_repository import CounterRepository
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_mute_repository import TicketMuteRepository
from db.repositories.ticket_repository import TicketRepository


def make_config(guild_id: int) -> GuildConfigRecord:
    return GuildConfigRecord(
        guild_id=guild_id,
        is_initialized=True,
        log_channel_id=100,
        archive_channel_id=200,
        ticket_category_channel_id=300,
        admin_role_id=400,
        claim_mode=ClaimMode.RELAXED,
        max_open_tickets=25,
        timezone="Asia/Hong_Kong",
        enable_download_window=True,
        updated_at="2024-01-01T00:00:00+00:00",
    )


def make_ticket(ticket_id: str, *, guild_id: int, category_key: str) -> TicketRecord:
    return TicketRecord(
        ticket_id=ticket_id,
        guild_id=guild_id,
        creator_id=123,
        category_key=category_key,
        channel_id=456,
        status=TicketStatus.SUBMITTED,
        created_at="2024-01-01T00:00:00+00:00",
        updated_at="2024-01-01T00:00:00+00:00",
        has_user_message=True,
        claimed_by=789,
        priority=TicketPriority.HIGH,
    )


def test_repositories_share_connection_for_multi_repository_unit_of_work(
    migrated_database,
) -> None:
    guild_repository = GuildRepository(migrated_database)
    ticket_repository = TicketRepository(migrated_database)
    counter_repository = CounterRepository(migrated_database)
    ticket_mute_repository = TicketMuteRepository(migrated_database)

    with migrated_database.session() as connection:
        stored_config = guild_repository.upsert_config(
            make_config(guild_id=42),
            connection=connection,
        )
        stored_ticket = ticket_repository.create(
            make_ticket("ticket-connection", guild_id=42, category_key="support"),
            connection=connection,
        )
        incremented_counter = counter_repository.increment(
            42,
            "support",
            step=2,
            connection=connection,
        )
        stored_mute = ticket_mute_repository.upsert(
            TicketMuteRecord(
                ticket_id=stored_ticket.ticket_id,
                user_id=123,
                muted_by=789,
            ),
            connection=connection,
        )

        assert guild_repository.get_config(42, connection=connection) == stored_config
        assert (
            ticket_repository.get_by_ticket_id(
                "ticket-connection",
                connection=connection,
            )
            == stored_ticket
        )
        assert (
            counter_repository.get_counter(
                42,
                "support",
                connection=connection,
            )
            == incremented_counter
        )
        assert ticket_mute_repository.get_by_ticket_and_user(stored_ticket.ticket_id, 123, connection=connection) == stored_mute

    assert guild_repository.get_config(42) == stored_config
    assert ticket_repository.get_by_ticket_id("ticket-connection") == stored_ticket
    assert counter_repository.get_counter(42, "support") == incremented_counter
    assert ticket_mute_repository.get_by_ticket_and_user(stored_ticket.ticket_id, 123) == stored_mute


def test_repositories_rollback_changes_when_outer_transaction_fails(
    migrated_database,
) -> None:
    guild_repository = GuildRepository(migrated_database)
    ticket_repository = TicketRepository(migrated_database)
    counter_repository = CounterRepository(migrated_database)
    ticket_mute_repository = TicketMuteRepository(migrated_database)

    with pytest.raises(RuntimeError, match="rollback marker"):
        with migrated_database.session() as connection:
            guild_repository.upsert_config(
                make_config(guild_id=7),
                connection=connection,
            )
            ticket_repository.create(
                make_ticket("ticket-rollback", guild_id=7, category_key="billing"),
                connection=connection,
            )
            counter_repository.increment(
                7,
                "billing",
                connection=connection,
            )
            ticket_mute_repository.upsert(
                TicketMuteRecord(
                    ticket_id="ticket-rollback",
                    user_id=456,
                    muted_by=789,
                ),
                connection=connection,
            )

            assert guild_repository.get_config(7, connection=connection) is not None
            assert (
                ticket_repository.get_by_ticket_id(
                    "ticket-rollback",
                    connection=connection,
                )
                is not None
            )
            assert (
                counter_repository.get_counter(
                    7,
                    "billing",
                    connection=connection,
                )
                is not None
            )
            assert (
                ticket_mute_repository.get_by_ticket_and_user(
                    "ticket-rollback",
                    456,
                    connection=connection,
                )
                is not None
            )

            raise RuntimeError("rollback marker")

    assert guild_repository.get_config(7) is None
    assert ticket_repository.get_by_ticket_id("ticket-rollback") is None
    assert counter_repository.get_counter(7, "billing") is None
    assert ticket_mute_repository.get_by_ticket_and_user("ticket-rollback", 456) is None
