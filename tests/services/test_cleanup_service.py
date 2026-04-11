from __future__ import annotations

from pathlib import Path

from core.enums import TicketStatus
from core.models import TicketMuteRecord, TicketRecord
from db.repositories.ticket_mute_repository import TicketMuteRepository
from db.repositories.ticket_repository import TicketRepository
from runtime.cache import RuntimeCacheStore, SnapshotLatestState
from services.cleanup_service import CleanupService


def test_cleanup_service_removes_ticket_files_mutes_and_runtime_snapshot_cache(
    migrated_database,
    tmp_path: Path,
) -> None:
    ticket_repository = TicketRepository(migrated_database)
    mute_repository = TicketMuteRepository(migrated_database)
    ticket = ticket_repository.create(
        TicketRecord(
            ticket_id="1-support-0001",
            guild_id=1,
            creator_id=201,
            category_key="support",
            channel_id=9001,
            status=TicketStatus.CLOSING,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )
    )
    mute_repository.upsert(
        TicketMuteRecord(
            ticket_id=ticket.ticket_id,
            user_id=201,
            muted_by=301,
        )
    )

    expected_files = [
        tmp_path / "snapshots" / f"{ticket.ticket_id}.jsonl",
        tmp_path / "snapshots" / f"{ticket.ticket_id}.jsonl.tmp",
        tmp_path / "notes" / f"{ticket.ticket_id}.jsonl",
        tmp_path / "archives" / f"{ticket.ticket_id}.html",
        tmp_path / "exports" / f"{ticket.ticket_id}-history.txt",
    ]
    for file_path in expected_files:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("payload", encoding="utf-8")

    unrelated_file = tmp_path / "snapshots" / "other-ticket.jsonl"
    unrelated_file.write_text("keep", encoding="utf-8")

    cache = RuntimeCacheStore()
    cache.remember_snapshot_state(
        ticket.channel_id or 0,
        123,
        SnapshotLatestState(
            author_id=201,
            author_name="creator",
            content="hello",
            attachments=(),
            timestamp="2024-01-01T00:00:00+00:00",
        ),
    )
    cache.set_snapshot_message_count(ticket.channel_id or 0, 3)
    cache.set_snapshot_threshold_flag(ticket.channel_id or 0, "warn_900")

    service = CleanupService(
        migrated_database,
        storage_dir=tmp_path,
        cache=cache,
    )

    service.cleanup_ticket(ticket)

    assert mute_repository.list_by_ticket(ticket.ticket_id) == []
    assert unrelated_file.exists() is True
    assert all(file_path.exists() is False for file_path in expected_files)
    assert cache.get_snapshot_state(ticket.channel_id or 0, 123) is None
    assert cache.get_snapshot_message_count(ticket.channel_id or 0, default=-1) == -1
    assert cache.get_snapshot_threshold_flag(ticket.channel_id or 0, "warn_900") is False


def test_cleanup_service_does_not_delete_other_ticket_files_with_same_prefix(
    migrated_database,
    tmp_path: Path,
) -> None:
    ticket_repository = TicketRepository(migrated_database)
    target_ticket = ticket_repository.create(
        TicketRecord(
            ticket_id="1-support-0001",
            guild_id=1,
            creator_id=201,
            category_key="support",
            channel_id=9001,
            status=TicketStatus.CHANNEL_DELETED,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )
    )

    target_files = [
        tmp_path / "snapshots" / f"{target_ticket.ticket_id}.jsonl",
        tmp_path / "notes" / f"{target_ticket.ticket_id}.jsonl",
        tmp_path / "archives" / f"{target_ticket.ticket_id}.html",
        tmp_path / "exports" / f"{target_ticket.ticket_id}-history.txt",
    ]
    colliding_files = [path.parent / path.name.replace(target_ticket.ticket_id, "1-support-00010", 1) for path in target_files]
    for file_path in [*target_files, *colliding_files]:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(file_path.name, encoding="utf-8")

    CleanupService(migrated_database, storage_dir=tmp_path).cleanup_ticket(target_ticket)

    assert all(file_path.exists() is False for file_path in target_files)
    assert all(file_path.exists() is True for file_path in colliding_files)
