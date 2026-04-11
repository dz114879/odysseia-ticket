from __future__ import annotations

import pytest

from core.models import TicketCounterRecord
from db.repositories.counter_repository import CounterRepository


@pytest.fixture
def repository(migrated_database) -> CounterRepository:
    return CounterRepository(migrated_database)


def test_upsert_get_and_delete_counter(repository: CounterRepository) -> None:
    stored = repository.upsert_counter(TicketCounterRecord(guild_id=1, category_key="support", next_number=8))

    loaded = repository.get_counter(1, "support")

    assert stored == loaded
    assert isinstance(loaded, TicketCounterRecord)
    assert loaded is not None
    assert loaded.next_number == 8
    assert repository.delete_counter(1, "support") is True
    assert repository.delete_counter(1, "support") is False


def test_increment_initializes_missing_counter_and_supports_custom_steps(
    repository: CounterRepository,
) -> None:
    first = repository.increment(1, "billing")
    second = repository.increment(1, "billing", step=3)

    assert first.next_number == 2
    assert second.next_number == 5
    assert repository.get_counter(1, "billing") == second


def test_increment_rejects_non_positive_steps(repository: CounterRepository) -> None:
    with pytest.raises(ValueError, match="step 必须大于 0"):
        repository.increment(1, "billing", step=0)
