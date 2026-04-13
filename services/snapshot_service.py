from __future__ import annotations

import inspect
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from collections.abc import AsyncIterator

from core.constants import SNAPSHOT_CREATE_LIMIT, SNAPSHOT_CREATE_WARNING_THRESHOLD
from core.enums import TicketStatus
from core.models import TicketRecord
from db.connection import DatabaseManager
from db.repositories.base import utc_now_iso
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository
from runtime.cache import RuntimeCacheStore, SnapshotLatestState
from runtime.locks import LockManager
from services.logging_service import LoggingService
from storage.snapshot_store import SnapshotStore

_ACTIVE_SNAPSHOT_STATUSES = (
    TicketStatus.SUBMITTED,
    TicketStatus.SLEEP,
    TicketStatus.TRANSFERRING,
    TicketStatus.CLOSING,
)
_UNKNOWN_OLD_CONTENT = "[未知，可能因快照上限或重启丢失]"


@dataclass(frozen=True, slots=True)
class SnapshotBootstrapResult:
    ticket: TicketRecord
    create_count: int
    skipped: bool


@dataclass(frozen=True, slots=True)
class SnapshotRestoreReport:
    tickets_scanned: int
    tickets_restored: int
    cached_messages: int


class SnapshotService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        snapshot_store: SnapshotStore | None = None,
        ticket_repository: TicketRepository | None = None,
        guild_repository: GuildRepository | None = None,
        lock_manager: LockManager | None = None,
        cache: RuntimeCacheStore | None = None,
        logging_service: LoggingService | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.database = database
        self.snapshot_store = snapshot_store or SnapshotStore()
        self.ticket_repository = ticket_repository or TicketRepository(database)
        self.guild_repository = guild_repository or GuildRepository(database)
        self.lock_manager = lock_manager
        self.cache = cache or RuntimeCacheStore()
        self.logging_service = logging_service
        self.logger = logger or logging.getLogger(__name__)

    async def bootstrap_from_channel_history(
        self,
        ticket: TicketRecord,
        channel: Any,
    ) -> SnapshotBootstrapResult:
        channel_id = getattr(channel, "id", None)
        if channel_id is None:
            return SnapshotBootstrapResult(ticket=ticket, create_count=0, skipped=True)

        async with self._acquire_snapshot_lock(ticket.ticket_id):
            refreshed_ticket = self.ticket_repository.get_by_ticket_id(ticket.ticket_id) or ticket
            if refreshed_ticket.snapshot_bootstrapped_at is not None:
                cached_count = self._ensure_message_count_cache(
                    refreshed_ticket,
                    default_channel_id=channel_id,
                )
                return SnapshotBootstrapResult(
                    ticket=refreshed_ticket,
                    create_count=cached_count,
                    skipped=True,
                )

            # 从公会配置获取快照阈值
            config = self.guild_repository.get_config(ticket.guild_id)
            warning_threshold = config.snapshot_warning_threshold if config else SNAPSHOT_CREATE_WARNING_THRESHOLD
            limit = config.snapshot_limit if config else SNAPSHOT_CREATE_LIMIT

            raw_messages = await self._collect_channel_history(channel)
            create_records: list[dict[str, Any]] = []
            latest_states: dict[int, SnapshotLatestState] = {}
            create_count = 0
            for message in raw_messages:
                if self._should_ignore_message(message):
                    continue
                if create_count >= limit:
                    break
                record = self._build_create_record(message)
                message_id = self._coerce_message_id(record.get("message_id"))
                if message_id is None:
                    continue
                create_records.append(record)
                latest_states[message_id] = self._latest_state_from_record(record)
                create_count += 1

            self.snapshot_store.overwrite_records(ticket.ticket_id, create_records)
            self.cache.clear_ticket_snapshot_state(channel_id)
            for message_id, latest_state in latest_states.items():
                self.cache.remember_snapshot_state(channel_id, message_id, latest_state)
            self.cache.set_snapshot_message_count(channel_id, create_count)
            self._restore_threshold_flags(channel_id, create_count, warning_threshold=warning_threshold, limit=limit)

            bootstrapped_at = utc_now_iso()
            updated_ticket = (
                self.ticket_repository.update(
                    ticket.ticket_id,
                    snapshot_bootstrapped_at=bootstrapped_at,
                    message_count=create_count,
                )
                or refreshed_ticket
            )
            return SnapshotBootstrapResult(
                ticket=updated_ticket,
                create_count=create_count,
                skipped=False,
            )

    async def handle_message(self, message: Any) -> bool:
        if self._should_ignore_message(message):
            return False

        channel_id = getattr(getattr(message, "channel", None), "id", None)
        if channel_id is None:
            return False
        ticket = self._get_active_ticket_by_channel(channel_id)
        if ticket is None:
            return False

        message_id = self._coerce_message_id(getattr(message, "id", None))
        if message_id is None:
            return False

        async with self._acquire_snapshot_lock(ticket.ticket_id):
            current_ticket = self.ticket_repository.get_by_ticket_id(ticket.ticket_id) or ticket
            if current_ticket.channel_id != channel_id or current_ticket.status not in _ACTIVE_SNAPSHOT_STATUSES:
                return False
            if self._resolve_latest_state(current_ticket, channel_id, message_id) is not None:
                return False

            # 从公会配置获取快照上限
            config = self.guild_repository.get_config(current_ticket.guild_id)
            limit = config.snapshot_limit if config else SNAPSHOT_CREATE_LIMIT

            current_count = self._ensure_message_count_cache(current_ticket)
            if current_count >= limit:
                await self._maybe_send_threshold_notifications(
                    channel=getattr(message, "channel", None),
                    ticket=current_ticket,
                    create_count=current_count,
                )
                return False

            record = self._build_create_record(message)
            self.snapshot_store.append_record(current_ticket.ticket_id, record)
            latest_state = self._latest_state_from_record(record)
            self.cache.remember_snapshot_state(channel_id, message_id, latest_state)
            next_count = self.cache.increment_snapshot_message_count(channel_id)
            self.ticket_repository.update(current_ticket.ticket_id, message_count=next_count)
            await self._maybe_send_threshold_notifications(
                channel=getattr(message, "channel", None),
                ticket=current_ticket,
                create_count=next_count,
            )
            return True

    async def handle_message_edit(self, before: Any, after: Any) -> bool:
        del before
        if self._should_ignore_message(after):
            return False

        channel_id = getattr(getattr(after, "channel", None), "id", None)
        if channel_id is None:
            return False
        ticket = self._get_active_ticket_by_channel(channel_id)
        if ticket is None:
            return False

        message_id = self._coerce_message_id(getattr(after, "id", None))
        if message_id is None:
            return False

        async with self._acquire_snapshot_lock(ticket.ticket_id):
            current_ticket = self.ticket_repository.get_by_ticket_id(ticket.ticket_id) or ticket
            if current_ticket.channel_id != channel_id or current_ticket.status not in _ACTIVE_SNAPSHOT_STATUSES:
                return False

            previous_state = self._resolve_latest_state(current_ticket, channel_id, message_id)
            next_content = self._normalize_content(getattr(after, "content", ""))
            next_attachments = self._format_attachments(getattr(after, "attachments", None) or [])
            if previous_state is not None and previous_state.content == next_content and list(previous_state.attachments) == next_attachments:
                return False

            old_content = previous_state.content if previous_state is not None else _UNKNOWN_OLD_CONTENT
            old_attachments = list(previous_state.attachments) if previous_state is not None else []
            record = {
                "event": "edit",
                "message_id": message_id,
                "author_id": getattr(getattr(after, "author", None), "id", None),
                "author_name": self._resolve_author_name(getattr(after, "author", None)),
                "timestamp": self._format_timestamp(getattr(after, "edited_at", None) or getattr(after, "created_at", None)),
                "old_content": old_content,
                "new_content": next_content,
                "old_attachments": old_attachments,
                "new_attachments": next_attachments,
            }
            self.snapshot_store.append_record(current_ticket.ticket_id, record)
            self.cache.remember_snapshot_state(
                channel_id,
                message_id,
                SnapshotLatestState(
                    author_id=getattr(getattr(after, "author", None), "id", None),
                    author_name=self._resolve_author_name(getattr(after, "author", None)),
                    content=next_content,
                    attachments=tuple(next_attachments),
                    timestamp=str(record["timestamp"]),
                ),
            )
            return True

    async def handle_raw_message_edit(self, payload: Any) -> bool:
        if getattr(payload, "cached_message", None) is not None:
            return False

        channel_id = self._coerce_message_id(getattr(payload, "channel_id", None))
        if channel_id is None:
            return False
        ticket = self._get_active_ticket_by_channel(channel_id)
        if ticket is None:
            return False

        message_id = self._coerce_message_id(getattr(payload, "message_id", None))
        if message_id is None:
            return False

        payload_data = getattr(payload, "data", None)
        if not isinstance(payload_data, dict):
            payload_data = {}

        async with self._acquire_snapshot_lock(ticket.ticket_id):
            current_ticket = self.ticket_repository.get_by_ticket_id(ticket.ticket_id) or ticket
            if current_ticket.channel_id != channel_id or current_ticket.status not in _ACTIVE_SNAPSHOT_STATUSES:
                return False

            previous_state = self._resolve_latest_state(current_ticket, channel_id, message_id)
            has_content_update = "content" in payload_data
            has_attachment_update = "attachments" in payload_data
            if not has_content_update and not has_attachment_update:
                return False

            next_content = (
                self._normalize_content(payload_data.get("content"))
                if has_content_update
                else (previous_state.content if previous_state is not None else "")
            )
            next_attachments = (
                self._format_attachments(payload_data.get("attachments") or [])
                if has_attachment_update
                else (list(previous_state.attachments) if previous_state is not None else [])
            )
            if previous_state is not None and previous_state.content == next_content and list(previous_state.attachments) == next_attachments:
                return False

            author_id, author_name = self._resolve_raw_author(payload_data.get("author"), previous_state)
            old_content = previous_state.content if previous_state is not None else _UNKNOWN_OLD_CONTENT
            old_attachments = list(previous_state.attachments) if previous_state is not None else []
            record = {
                "event": "edit",
                "message_id": message_id,
                "author_id": author_id,
                "author_name": author_name,
                "timestamp": self._format_timestamp(payload_data.get("edited_timestamp") or utc_now_iso()),
                "old_content": old_content,
                "new_content": next_content,
                "old_attachments": old_attachments,
                "new_attachments": next_attachments,
            }
            self.snapshot_store.append_record(current_ticket.ticket_id, record)
            self.cache.remember_snapshot_state(
                channel_id,
                message_id,
                SnapshotLatestState(
                    author_id=author_id,
                    author_name=author_name,
                    content=next_content,
                    attachments=tuple(next_attachments),
                    timestamp=str(record["timestamp"]),
                ),
            )
            return True

    async def handle_message_delete(self, message: Any) -> bool:
        if self._should_ignore_message(message):
            return False

        channel_id = getattr(getattr(message, "channel", None), "id", None)
        if channel_id is None:
            return False
        ticket = self._get_active_ticket_by_channel(channel_id)
        if ticket is None:
            return False

        message_id = self._coerce_message_id(getattr(message, "id", None))
        if message_id is None:
            return False

        async with self._acquire_snapshot_lock(ticket.ticket_id):
            current_ticket = self.ticket_repository.get_by_ticket_id(ticket.ticket_id) or ticket
            if current_ticket.channel_id != channel_id or current_ticket.status not in _ACTIVE_SNAPSHOT_STATUSES:
                return False

            previous_state = self._resolve_latest_state(current_ticket, channel_id, message_id)
            deleted_content = previous_state.content if previous_state is not None else _UNKNOWN_OLD_CONTENT
            deleted_attachments = list(previous_state.attachments) if previous_state is not None else []
            record = {
                "event": "delete",
                "message_id": message_id,
                "author_id": getattr(getattr(message, "author", None), "id", None),
                "author_name": self._resolve_author_name(getattr(message, "author", None)),
                "timestamp": utc_now_iso(),
                "deleted_content": deleted_content,
                "deleted_attachments": deleted_attachments,
            }
            self.snapshot_store.append_record(current_ticket.ticket_id, record)
            self.cache.forget_snapshot_state(channel_id, message_id)
            return True

    async def handle_raw_message_delete(self, payload: Any) -> bool:
        if getattr(payload, "cached_message", None) is not None:
            return False

        channel_id = self._coerce_message_id(getattr(payload, "channel_id", None))
        if channel_id is None:
            return False
        ticket = self._get_active_ticket_by_channel(channel_id)
        if ticket is None:
            return False

        message_id = self._coerce_message_id(getattr(payload, "message_id", None))
        if message_id is None:
            return False

        async with self._acquire_snapshot_lock(ticket.ticket_id):
            current_ticket = self.ticket_repository.get_by_ticket_id(ticket.ticket_id) or ticket
            if current_ticket.channel_id != channel_id or current_ticket.status not in _ACTIVE_SNAPSHOT_STATUSES:
                return False

            previous_state = self._resolve_latest_state(current_ticket, channel_id, message_id)
            record = {
                "event": "delete",
                "message_id": message_id,
                "author_id": previous_state.author_id if previous_state is not None else None,
                "author_name": previous_state.author_name if previous_state is not None else "Unknown",
                "timestamp": utc_now_iso(),
                "deleted_content": previous_state.content if previous_state is not None else _UNKNOWN_OLD_CONTENT,
                "deleted_attachments": list(previous_state.attachments) if previous_state is not None else [],
            }
            self.snapshot_store.append_record(current_ticket.ticket_id, record)
            self.cache.forget_snapshot_state(channel_id, message_id)
            return True

    async def restore_runtime_state(self) -> SnapshotRestoreReport:
        tickets = self.ticket_repository.list_by_statuses(_ACTIVE_SNAPSHOT_STATUSES)
        restored_tickets = 0
        cached_messages = 0
        # 按公会缓存配置，避免重复查询
        guild_config_cache: dict[int, Any] = {}
        for ticket in tickets:
            channel_id = ticket.channel_id
            if channel_id is None:
                continue

            # 获取公会快照阈值配置
            if ticket.guild_id not in guild_config_cache:
                guild_config_cache[ticket.guild_id] = self.guild_repository.get_config(ticket.guild_id)
            config = guild_config_cache[ticket.guild_id]
            warning_threshold = config.snapshot_warning_threshold if config else SNAPSHOT_CREATE_WARNING_THRESHOLD
            limit = config.snapshot_limit if config else SNAPSHOT_CREATE_LIMIT

            self.cache.clear_ticket_snapshot_state(channel_id)
            records = self.snapshot_store.read_records(ticket.ticket_id)
            if not records:
                if ticket.message_count is not None:
                    self.cache.set_snapshot_message_count(channel_id, ticket.message_count)
                continue

            latest_states: dict[int, SnapshotLatestState] = {}
            create_count = 0
            for record in records:
                message_id = self._coerce_message_id(record.get("message_id"))
                if message_id is None:
                    continue
                event = str(record.get("event", ""))
                if event == "create":
                    latest_states[message_id] = self._latest_state_from_record(record)
                    create_count += 1
                elif event == "edit":
                    latest_states[message_id] = SnapshotLatestState(
                        author_id=self._coerce_message_id(record.get("author_id")),
                        author_name=str(record.get("author_name") or record.get("author_id") or "Unknown"),
                        content=str(record.get("new_content", "") or ""),
                        attachments=tuple(str(item) for item in (record.get("new_attachments") or [])),
                        timestamp=str(record.get("timestamp", "unknown")),
                    )
                elif event == "delete":
                    latest_states.pop(message_id, None)

            for message_id, latest_state in latest_states.items():
                self.cache.remember_snapshot_state(channel_id, message_id, latest_state)
            self.cache.set_snapshot_message_count(channel_id, create_count)
            self._restore_threshold_flags(channel_id, create_count, warning_threshold=warning_threshold, limit=limit)
            cached_messages += len(latest_states)
            restored_tickets += 1
            if ticket.message_count != create_count:
                self.ticket_repository.update(ticket.ticket_id, message_count=create_count)

        return SnapshotRestoreReport(
            tickets_scanned=len(tickets),
            tickets_restored=restored_tickets,
            cached_messages=cached_messages,
        )

    def clear_ticket_runtime_state(self, ticket: TicketRecord) -> int:
        if ticket.channel_id is None:
            return 0
        return self.cache.clear_ticket_snapshot_state(ticket.channel_id)

    def _get_active_ticket_by_channel(self, channel_id: int) -> TicketRecord | None:
        ticket = self.ticket_repository.get_by_channel_id(channel_id)
        if ticket is None or ticket.status not in _ACTIVE_SNAPSHOT_STATUSES:
            return None
        return ticket

    def _ensure_message_count_cache(
        self,
        ticket: TicketRecord,
        *,
        default_channel_id: int | None = None,
    ) -> int:
        channel_id = ticket.channel_id if ticket.channel_id is not None else default_channel_id
        if channel_id is None:
            return ticket.message_count or 0
        count = self.cache.get_snapshot_message_count(channel_id, default=-1)
        if count >= 0:
            return count
        count = ticket.message_count or 0
        self.cache.set_snapshot_message_count(channel_id, count)
        return count

    def _resolve_latest_state(
        self,
        ticket: TicketRecord,
        channel_id: int,
        message_id: int,
    ) -> SnapshotLatestState | None:
        cached_state = self.cache.get_snapshot_state(channel_id, message_id)
        if cached_state is not None:
            return cached_state
        records = self.snapshot_store.read_records(ticket.ticket_id)
        for record in reversed(records):
            if self._coerce_message_id(record.get("message_id")) != message_id:
                continue
            event = str(record.get("event", ""))
            if event == "create":
                state = self._latest_state_from_record(record)
            elif event == "edit":
                state = SnapshotLatestState(
                    author_id=self._coerce_message_id(record.get("author_id")),
                    author_name=str(record.get("author_name") or record.get("author_id") or "Unknown"),
                    content=str(record.get("new_content", "") or ""),
                    attachments=tuple(str(item) for item in (record.get("new_attachments") or [])),
                    timestamp=str(record.get("timestamp", "unknown")),
                )
            elif event == "delete":
                state = SnapshotLatestState(
                    author_id=self._coerce_message_id(record.get("author_id")),
                    author_name=str(record.get("author_name") or record.get("author_id") or "Unknown"),
                    content=str(record.get("deleted_content", "") or ""),
                    attachments=tuple(str(item) for item in (record.get("deleted_attachments") or [])),
                    timestamp=str(record.get("timestamp", "unknown")),
                )
            else:
                continue
            self.cache.remember_snapshot_state(channel_id, message_id, state)
            return state
        return None

    async def _maybe_send_threshold_notifications(
        self,
        *,
        channel: Any,
        ticket: TicketRecord,
        create_count: int,
    ) -> None:
        channel_id = getattr(channel, "id", None)
        send = getattr(channel, "send", None)
        if channel_id is None or send is None:
            return

        # 从公会配置获取快照阈值与自定义提示文案
        config = self.guild_repository.get_config(ticket.guild_id)
        warning_threshold = config.snapshot_warning_threshold if config else SNAPSHOT_CREATE_WARNING_THRESHOLD
        limit = config.snapshot_limit if config else SNAPSHOT_CREATE_LIMIT

        if create_count >= warning_threshold and not self.cache.get_snapshot_threshold_flag(channel_id, "warn_900"):
            warning_text = (
                config.snapshot_warning_text
                if config and config.snapshot_warning_text
                else f"⚠️ 本 Ticket 内消息数接近BOT记录上限（{limit}条），建议总结后重开 Ticket 继续讨论。 ⚠️"
            )
            await send(content=warning_text)
            self.cache.set_snapshot_threshold_flag(channel_id, "warn_900")

        if create_count >= limit and not self.cache.get_snapshot_threshold_flag(channel_id, "warn_1000"):
            limit_text = (
                config.snapshot_limit_text
                if config and config.snapshot_limit_text
                else f"⚠️ 本 Ticket 消息数已达记录上限（{limit}条），新消息将不再被快照系统记录。 ⚠️"
            )
            await send(content=limit_text)
            self.cache.set_snapshot_threshold_flag(channel_id, "warn_1000")
            if self.logging_service is not None:
                await self.logging_service.send_ticket_log(
                    ticket_id=ticket.ticket_id,
                    guild_id=ticket.guild_id,
                    level="warning",
                    title="工单快照创建上限已达",
                    description=(f"Ticket `{ticket.ticket_id}` 已达到 {limit} 条 create 快照上限。"),
                    channel_id=getattr(config, "log_channel_id", None),
                    extra={"channel_id": channel_id, "create_count": create_count},
                )

    def _restore_threshold_flags(
        self,
        channel_id: int,
        create_count: int,
        *,
        warning_threshold: int = SNAPSHOT_CREATE_WARNING_THRESHOLD,
        limit: int = SNAPSHOT_CREATE_LIMIT,
    ) -> None:
        if create_count >= warning_threshold:
            self.cache.set_snapshot_threshold_flag(channel_id, "warn_900")
        if create_count >= limit:
            self.cache.set_snapshot_threshold_flag(channel_id, "warn_1000")

    @staticmethod
    def _should_ignore_message(message: Any) -> bool:
        author = getattr(message, "author", None)
        return author is None or bool(getattr(author, "bot", False))

    async def _collect_channel_history(self, channel: Any) -> list[Any]:
        history = getattr(channel, "history", None)
        if not callable(history):
            return []

        history_result = history(limit=None, oldest_first=True)
        if hasattr(history_result, "__aiter__"):
            return [item async for item in history_result]
        if inspect.isawaitable(history_result):
            history_result = await history_result
        return list(history_result or [])

    def _build_create_record(self, message: Any) -> dict[str, Any]:
        author = getattr(message, "author", None)
        return {
            "event": "create",
            "message_id": getattr(message, "id", None),
            "author_id": getattr(author, "id", None),
            "author_name": self._resolve_author_name(author),
            "timestamp": self._format_timestamp(getattr(message, "created_at", None)),
            "content": self._normalize_content(getattr(message, "content", "")),
            "attachments": self._format_attachments(getattr(message, "attachments", None) or []),
            "embeds_count": len(getattr(message, "embeds", None) or []),
            "reply_to": getattr(getattr(message, "reference", None), "message_id", None),
        }

    @staticmethod
    def _latest_state_from_record(record: dict[str, Any]) -> SnapshotLatestState:
        return SnapshotLatestState(
            author_id=SnapshotService._coerce_message_id(record.get("author_id")),
            author_name=str(record.get("author_name") or record.get("author_id") or "Unknown"),
            content=str(record.get("content", "") or ""),
            attachments=tuple(str(item) for item in (record.get("attachments") or [])),
            timestamp=str(record.get("timestamp", "unknown")),
        )

    @staticmethod
    def _resolve_author_name(author: Any) -> str:
        return str(getattr(author, "display_name", None) or getattr(author, "name", None) or getattr(author, "id", "Unknown"))

    @staticmethod
    def _resolve_raw_author(
        author: Any,
        previous_state: SnapshotLatestState | None,
    ) -> tuple[int | None, str]:
        if isinstance(author, dict):
            author_id = SnapshotService._coerce_message_id(author.get("id"))
            author_name = str(
                author.get("global_name")
                or author.get("username")
                or author.get("name")
                or author_id
                or (previous_state.author_name if previous_state is not None else "Unknown")
            )
            return author_id, author_name
        if author is not None:
            author_id = SnapshotService._coerce_message_id(getattr(author, "id", None))
            author_name = SnapshotService._resolve_author_name(author)
            return author_id, author_name
        if previous_state is not None:
            return previous_state.author_id, previous_state.author_name
        return None, "Unknown"

    @staticmethod
    def _normalize_content(value: Any) -> str:
        return str(value or "")

    @staticmethod
    def _format_timestamp(value: Any) -> str:
        if value is None:
            return "unknown"
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)

    @staticmethod
    def _format_attachments(attachments: list[Any]) -> list[str]:
        return [SnapshotService._format_attachment_placeholder(item) for item in attachments]

    @staticmethod
    def _format_attachment_placeholder(attachment: Any) -> str:
        if isinstance(attachment, dict):
            filename = attachment.get("filename") or attachment.get("url") or "attachment"
            size = attachment.get("size")
            content_type = str(attachment.get("content_type", "") or "").lower()
        else:
            filename = getattr(attachment, "filename", None) or getattr(attachment, "url", "attachment")
            size = getattr(attachment, "size", None)
            content_type = str(getattr(attachment, "content_type", "") or "").lower()
        if content_type.startswith("image/"):
            label = "图片"
        elif content_type.startswith("video/"):
            label = "视频"
        else:
            label = "文件"
        if size is None:
            size_text = "unknown size"
        elif size >= 1024 * 1024:
            size_text = f"{size / (1024 * 1024):.1f}MB"
        else:
            size_text = f"{max(size / 1024, 0):.1f}KB"
        return f"[{label}: {filename}, {size_text}]"

    @staticmethod
    def _coerce_message_id(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @asynccontextmanager
    async def _acquire_snapshot_lock(self, ticket_id: str) -> AsyncIterator[None]:
        if self.lock_manager is None:
            yield
            return

        async with self.lock_manager.acquire(f"ticket-snapshot:{ticket_id}"):
            yield
