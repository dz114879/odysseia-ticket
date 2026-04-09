from __future__ import annotations

import pytest

from core.models import PanelRecord
from db.repositories.panel_repository import PanelRepository


@pytest.fixture
def repository(migrated_database) -> PanelRepository:
    return PanelRepository(migrated_database)


def make_panel(
    panel_id: str,
    *,
    guild_id: int = 1,
    channel_id: int = 100,
    message_id: int = 1000,
    nonce: str = "nonce-1",
    is_active: bool = True,
    created_by: int = 42,
    created_at: str = "2024-01-01T00:00:00+00:00",
    updated_at: str = "2024-01-01T00:00:00+00:00",
) -> PanelRecord:
    return PanelRecord(
        panel_id=panel_id,
        guild_id=guild_id,
        channel_id=channel_id,
        message_id=message_id,
        nonce=nonce,
        is_active=is_active,
        created_by=created_by,
        created_at=created_at,
        updated_at=updated_at,
    )


def test_create_and_get_panel_preserves_model_mapping(repository: PanelRepository) -> None:
    created = repository.create(
        make_panel(
            "panel-001",
            channel_id=500,
            message_id=900,
            nonce="nonce-abc",
        )
    )

    loaded = repository.get_by_panel_id("panel-001")

    assert created == loaded
    assert isinstance(loaded, PanelRecord)
    assert loaded is not None
    assert loaded.is_active is True
    assert repository.get_by_message_id(900) == loaded


def test_replace_active_panel_deactivates_previous_panel(repository: PanelRepository) -> None:
    repository.create(make_panel("panel-old", message_id=1, nonce="old"))

    replacement = repository.replace_active_panel(
        make_panel(
            "panel-new",
            channel_id=101,
            message_id=2,
            nonce="new",
            updated_at="2024-02-01T00:00:00+00:00",
        )
    )

    old_panel = repository.get_by_panel_id("panel-old")
    active_panel = repository.get_active_panel(1)

    assert replacement.panel_id == "panel-new"
    assert old_panel is not None
    assert old_panel.is_active is False
    assert active_panel == replacement
    assert repository.list_by_guild(1, active_only=True) == [replacement]


def test_deactivate_upsert_update_and_delete_panel(repository: PanelRepository) -> None:
    repository.create(make_panel("panel-1", message_id=10, nonce="one", is_active=True))
    repository.create(
        make_panel(
            "panel-2",
            channel_id=200,
            message_id=20,
            nonce="two",
            is_active=False,
            created_at="2024-01-02T00:00:00+00:00",
            updated_at="2024-01-02T00:00:00+00:00",
        )
    )

    removed_count = repository.deactivate_guild_panels(1, except_panel_id="panel-2")
    upserted = repository.upsert(
        make_panel(
            "panel-2",
            channel_id=201,
            message_id=21,
            nonce="two-updated",
            is_active=True,
            created_by=99,
            created_at="2030-01-01T00:00:00+00:00",
            updated_at="2024-02-01T00:00:00+00:00",
        )
    )
    updated = repository.update(
        "panel-2",
        nonce="two-final",
        updated_at="2024-03-01T00:00:00+00:00",
    )

    assert removed_count == 1
    assert upserted.created_at == "2024-01-02T00:00:00+00:00"
    assert upserted.is_active is True

    assert updated is not None
    assert updated.channel_id == 201
    assert updated.message_id == 21
    assert updated.nonce == "two-final"
    assert updated.updated_at == "2024-03-01T00:00:00+00:00"
    assert repository.get_active_panel(1) == updated

    assert repository.delete("panel-1") is True
    assert repository.delete("panel-1") is False
