from __future__ import annotations

try:
    from enum import StrEnum
except ImportError:  # pragma: no cover - Python < 3.11 fallback
    from enum import Enum

    class StrEnum(str, Enum):
        pass


class TicketStatus(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    SUBMITTED = "submitted"
    SLEEP = "sleep"
    TRANSFERRING = "transferring"
    CLOSING = "closing"
    ARCHIVING = "archiving"
    ARCHIVE_SENT = "archive_sent"
    ARCHIVE_FAILED = "archive_failed"
    CHANNEL_DELETED = "channel_deleted"
    DONE = "done"
    ABANDONED = "abandoned"


class TicketPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EMERGENCY = "emergency"
    SLEEP = "sleep"


class ClaimMode(StrEnum):
    RELAXED = "relaxed"
    STRICT = "strict"
