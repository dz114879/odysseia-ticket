from __future__ import annotations

import tempfile
from pathlib import Path

from bootstrap_path import ensure_project_root_on_path

ensure_project_root_on_path()

from core.enums import ClaimMode, TicketPriority, TicketStatus
from core.models import (
    GuildConfigRecord,
    PanelRecord,
    TicketCategoryConfig,
    TicketCounterRecord,
    TicketRecord,
)
from db.connection import DatabaseManager
from db.migrations import apply_migrations
from db.repositories import CounterRepository, GuildRepository, PanelRepository, TicketRepository


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="ticket-bot-m1-") as temp_dir:
        database_path = Path(temp_dir) / "m1-check.sqlite3"
        database = DatabaseManager(database_path)
        migration_report = apply_migrations(database)

        guild_repository = GuildRepository(database)
        ticket_repository = TicketRepository(database)
        panel_repository = PanelRepository(database)
        counter_repository = CounterRepository(database)

        guild = guild_repository.upsert_config(
            GuildConfigRecord(
                guild_id=123456789,
                is_initialized=True,
                log_channel_id=1001,
                archive_channel_id=1002,
                ticket_category_channel_id=1003,
                admin_role_id=2001,
                claim_mode=ClaimMode.STRICT,
                max_open_tickets=8,
                timezone="Asia/Hong_Kong",
                enable_download_window=False,
            )
        )
        assert guild.claim_mode is ClaimMode.STRICT
        guild = guild_repository.update_config(guild.guild_id, max_open_tickets=12)
        assert guild is not None
        assert guild.max_open_tickets == 12

        category = guild_repository.upsert_category(
            TicketCategoryConfig(
                guild_id=guild.guild_id,
                category_key="general",
                display_name="General Support",
                emoji="🎫",
                description="默认工单分类",
                sort_order=1,
            )
        )
        assert category.category_key == "general"
        assert len(guild_repository.list_categories(guild.guild_id)) == 1

        ticket = ticket_repository.create(
            TicketRecord(
                ticket_id="T-0001",
                guild_id=guild.guild_id,
                creator_id=3001,
                category_key=category.category_key,
                channel_id=4001,
                status=TicketStatus.DRAFT,
                priority=TicketPriority.MEDIUM,
            )
        )
        assert ticket.ticket_id == "T-0001"
        ticket = ticket_repository.update(
            ticket.ticket_id,
            status=TicketStatus.SUBMITTED,
            has_user_message=True,
            claimed_by=5001,
            priority=TicketPriority.HIGH,
        )
        assert ticket is not None
        assert ticket.status is TicketStatus.SUBMITTED
        assert ticket.claimed_by == 5001
        assert ticket_repository.get_by_channel_id(4001) is not None

        panel_repository.replace_active_panel(
            PanelRecord(
                panel_id="panel-1",
                guild_id=guild.guild_id,
                channel_id=6001,
                message_id=7001,
                nonce="nonce-a",
                is_active=True,
                created_by=8001,
            )
        )
        panel_repository.replace_active_panel(
            PanelRecord(
                panel_id="panel-2",
                guild_id=guild.guild_id,
                channel_id=6001,
                message_id=7002,
                nonce="nonce-b",
                is_active=True,
                created_by=8001,
            )
        )
        active_panel = panel_repository.get_active_panel(guild.guild_id)
        assert active_panel is not None
        assert active_panel.panel_id == "panel-2"

        counter = counter_repository.upsert_counter(
            TicketCounterRecord(
                guild_id=guild.guild_id,
                category_key=category.category_key,
                next_number=10,
            )
        )
        assert counter.next_number == 10
        counter = counter_repository.increment(guild.guild_id, category.category_key)
        assert counter.next_number == 11

        print(
            {
                "schema_version": migration_report.final_version,
                "guild_id": guild.guild_id,
                "ticket_status": ticket.status.value,
                "active_panel_id": active_panel.panel_id,
                "counter_next_number": counter.next_number,
            }
        )


if __name__ == "__main__":
    main()
