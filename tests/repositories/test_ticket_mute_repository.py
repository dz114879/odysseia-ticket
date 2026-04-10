from __future__ import annotations

import pytest

from core.models import TicketMuteRecord
from db.repositories.ticket_mute_repository import TicketMuteRepository


@pytest.fixture
def repository(migrated_database) -> TicketMuteRepository:
    return TicketMuteRepository(migrated_database)


def make_mute(
    ticket_id: str,
    user_id: int,
    *,
    muted_by: int = 301,
    reason: str | None = None,
    expire_at: str | None = None,
    created_at: str = "2024-01-01T00:00:00+00:00",
    updated_at: str = "2024-01-01T00:00:00+00:00",
) -> TicketMuteRecord:
    return TicketMuteRecord(
        ticket_id=ticket_id,
        user_id=user_id,
        muted_by=muted_by,
        reason=reason,
        expire_at=expire_at,
        created_at=created_at,
        updated_at=updated_at,
    )


def test_upsert_get_and_list_due_expirations_preserve_model_mapping(repository: TicketMuteRepository) -> None:
    repository.upsert(
        make_mute(
            "ticket-001",
            201,
            muted_by=301,
            reason="需要先冷静一下",
            expire_at="2024-01-01T01:00:00+00:00",
        )
    )
    repository.upsert(
        make_mute(
            "ticket-001",
            202,
            muted_by=302,
            expire_at="2024-01-02T01:00:00+00:00",
        )
    )

    loaded = repository.get_by_ticket_and_user("ticket-001", 201)
    due = repository.list_due_expirations("2024-01-01T12:00:00+00:00")
    by_ticket = repository.list_by_ticket("ticket-001")

    assert loaded is not None
    assert loaded.ticket_id == "ticket-001"
    assert loaded.user_id == 201
    assert loaded.muted_by == 301
    assert loaded.reason == "需要先冷静一下"
    assert loaded.expire_at == "2024-01-01T01:00:00+00:00"
    assert [(record.ticket_id, record.user_id) for record in by_ticket] == [
        ("ticket-001", 201),
        ("ticket-001", 202),
    ]
    assert [(record.ticket_id, record.user_id) for record in due] == [("ticket-001", 201)]


def test_upsert_updates_existing_mute_and_delete_removes_it(repository: TicketMuteRepository) -> None:
    repository.upsert(
        make_mute(
            "ticket-002",
            201,
            muted_by=301,
            reason="初次禁言",
            expire_at="2024-01-01T01:00:00+00:00",
        )
    )

    updated = repository.upsert(
        make_mute(
            "ticket-002",
            201,
            muted_by=401,
            reason="延长禁言",
            expire_at="2024-01-01T03:00:00+00:00",
            created_at="2030-01-01T00:00:00+00:00",
            updated_at="2024-01-01T02:00:00+00:00",
        )
    )

    assert updated.muted_by == 401
    assert updated.reason == "延长禁言"
    assert updated.expire_at == "2024-01-01T03:00:00+00:00"
    assert updated.created_at == "2024-01-01T00:00:00+00:00"
    assert updated.updated_at == "2024-01-01T02:00:00+00:00"

    assert repository.delete("ticket-002", 201) is True
    assert repository.delete("ticket-002", 201) is False
    assert repository.get_by_ticket_and_user("ticket-002", 201) is None
