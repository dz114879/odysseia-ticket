from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from core.constants import TRANSFER_EXECUTION_DELAY_SECONDS
from core.enums import TicketStatus


def append_transfer_history(
    ticket: Any,
    *,
    executed_at: str,
    restored_status: TicketStatus,
) -> str:
    history = parse_transfer_history(getattr(ticket, "transfer_history_json", "[]"))
    history.append(
        {
            "type": "transfer_executed",
            "from_category_key": ticket.category_key,
            "to_category_key": getattr(ticket, "transfer_target_category", None),
            "status_before": getattr(getattr(ticket, "status_before", None), "value", None),
            "restored_status": restored_status.value,
            "initiated_by": getattr(ticket, "transfer_initiated_by", None),
            "reason": getattr(ticket, "transfer_reason", None),
            "previous_claimer_id": getattr(ticket, "claimed_by", None),
            "scheduled_execute_at": getattr(ticket, "transfer_execute_at", None),
            "executed_at": executed_at,
        }
    )
    return json.dumps(history, ensure_ascii=False)


def parse_transfer_history(raw_value: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(raw_value or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def build_transfer_execute_at(
    now: datetime | str | None,
    *,
    delay_seconds: int = TRANSFER_EXECUTION_DELAY_SECONDS,
) -> str:
    reference_time = to_utc_datetime(now)
    return (reference_time + timedelta(seconds=delay_seconds)).isoformat()


def is_due_for_execution(ticket: Any, reference_time: datetime) -> bool:
    execute_at = getattr(ticket, "transfer_execute_at", None)
    if not execute_at:
        return False
    execute_at_datetime = parse_iso_datetime(execute_at)
    return execute_at_datetime <= reference_time


def to_utc_datetime(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        return parse_iso_datetime(value)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_iso_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
