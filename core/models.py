from __future__ import annotations

from dataclasses import dataclass

from core.enums import ClaimMode, TicketPriority, TicketStatus


@dataclass(frozen=True, slots=True)
class TicketRecord:
    ticket_id: str
    guild_id: int
    creator_id: int
    category_key: str
    channel_id: int | None = None
    status: TicketStatus = TicketStatus.DRAFT
    created_at: str = ""
    updated_at: str = ""
    has_user_message: bool = False
    last_user_message_at: str | None = None
    claimed_by: int | None = None
    priority: TicketPriority = TicketPriority.MEDIUM


@dataclass(frozen=True, slots=True)
class GuildConfigRecord:
    guild_id: int
    is_initialized: bool = False
    log_channel_id: int | None = None
    archive_channel_id: int | None = None
    ticket_category_channel_id: int | None = None
    admin_role_id: int | None = None
    claim_mode: ClaimMode = ClaimMode.RELAXED
    max_open_tickets: int = 100
    timezone: str = "UTC"
    enable_download_window: bool = True
    updated_at: str = ""


@dataclass(frozen=True, slots=True)
class TicketCategoryConfig:
    guild_id: int
    category_key: str
    display_name: str
    emoji: str | None = None
    description: str | None = None
    staff_role_id: int | None = None
    staff_user_ids_json: str = "[]"
    extra_welcome_text: str | None = None
    is_enabled: bool = True
    allowlist_role_ids_json: str = "[]"
    denylist_role_ids_json: str = "[]"
    sort_order: int = 0


@dataclass(frozen=True, slots=True)
class PanelRecord:
    panel_id: str
    guild_id: int
    channel_id: int
    message_id: int
    nonce: str
    is_active: bool = True
    created_by: int = 0
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True, slots=True)
class TicketCounterRecord:
    guild_id: int
    category_key: str
    next_number: int = 1
