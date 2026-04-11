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
    priority_before_sleep: TicketPriority | None = None
    status_before: TicketStatus | None = None
    transfer_target_category: str | None = None
    transfer_initiated_by: int | None = None
    transfer_reason: str | None = None
    transfer_execute_at: str | None = None
    transfer_history_json: str = "[]"
    staff_panel_message_id: int | None = None
    close_reason: str | None = None
    close_initiated_by: int | None = None
    close_execute_at: str | None = None
    closed_at: str | None = None
    archive_message_id: int | None = None
    archive_last_error: str | None = None
    archive_attempts: int = 0
    archived_at: str | None = None
    message_count: int | None = None
    snapshot_bootstrapped_at: str | None = None
    queued_at: str | None = None


@dataclass(frozen=True, slots=True)
class TicketMuteRecord:
    ticket_id: str
    user_id: int
    muted_by: int
    reason: str | None = None
    expire_at: str | None = None
    created_at: str = ""
    updated_at: str = ""


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
