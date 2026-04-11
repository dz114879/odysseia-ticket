from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

from core.enums import TicketStatus
from core.errors import PermissionDeniedError, ValidationError
from core.models import TicketMuteRecord
from db.connection import DatabaseManager
from db.repositories.ticket_mute_repository import TicketMuteRepository
from db.repositories.ticket_repository import TicketRepository
from runtime.locks import LockManager
from services.staff_guard_service import StaffGuardService
from services.staff_permission_service import StaffPermissionService
from services.staff_panel_service import StaffPanelService

_DURATION_PATTERN = re.compile(r"^\s*(\d+)\s*(s|m|h|d|秒|分|分钟|小?时|天)\s*$", re.IGNORECASE)
_DURATION_SECONDS_BY_UNIT = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
    "秒": 1,
    "分": 60,
    "分钟": 60,
    "时": 3600,
    "小时": 3600,
    "天": 86400,
}


@dataclass(frozen=True, slots=True)
class MuteMutationResult:
    ticket: Any
    target_id: int
    muted_by_id: int
    reason: str | None
    expire_at: str | None
    changed: bool
    log_message: Any | None


@dataclass(frozen=True, slots=True)
class UnmuteMutationResult:
    ticket: Any
    target_id: int
    previous_expire_at: str | None
    changed: bool
    log_message: Any | None


@dataclass(frozen=True, slots=True)
class MuteExpirationResult:
    ticket: Any
    target_id: int
    expire_at: str | None
    changed: bool
    log_message: Any | None


class ModerationService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        bot: Any | None = None,
        ticket_repository: TicketRepository | None = None,
        ticket_mute_repository: TicketMuteRepository | None = None,
        lock_manager: LockManager | None = None,
        guard_service: StaffGuardService | None = None,
        permission_service: StaffPermissionService | None = None,
        staff_panel_service: StaffPanelService | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.database = database
        self.bot = bot
        self.ticket_repository = ticket_repository or TicketRepository(database)
        self.ticket_mute_repository = ticket_mute_repository or TicketMuteRepository(database)
        self.lock_manager = lock_manager
        self.permission_service = permission_service or StaffPermissionService()
        self.staff_panel_service = staff_panel_service
        self.logger = logger or logging.getLogger(__name__)
        self.guard_service = guard_service or StaffGuardService(
            database,
            ticket_repository=self.ticket_repository,
        )

    async def mute_member(
        self,
        channel: Any,
        *,
        actor: Any,
        target: Any,
        duration: str | None = None,
        reason: str | None = None,
        is_bot_owner: bool = False,
        now: datetime | str | None = None,
    ) -> MuteMutationResult:
        channel_id = getattr(channel, "id", None)
        target_id = getattr(target, "id", None)
        actor_id = getattr(actor, "id", None)
        if channel_id is None or getattr(channel, "set_permissions", None) is None:
            raise ValidationError("当前频道不支持 ticket mute。")
        if actor_id is None or target_id is None:
            raise ValidationError("无法识别当前操作对象。")

        reference_time = self._to_utc_datetime(now)
        expire_at = self._build_expire_at(duration, reference_time=reference_time)
        normalized_reason = self._normalize_reason(reason)

        async with self._acquire_ticket_lock(channel_id):
            context = self.guard_service.load_ticket_context(
                channel_id,
                allowed_statuses=(TicketStatus.SUBMITTED, TicketStatus.SLEEP),
                invalid_state_message="当前 ticket 仅在 submitted / sleep 状态可执行 mute。",
            )
            self.guard_service.assert_staff_actor(
                actor,
                config=context.config,
                category=context.category,
                is_bot_owner=is_bot_owner,
            )
            self._assert_mutable_target(
                channel,
                ticket=context.ticket,
                target=target,
                actor=actor,
                config=context.config,
                category=context.category,
            )

            previous_record = self.ticket_mute_repository.get_by_ticket_and_user(context.ticket.ticket_id, target_id)
            changed = not (
                previous_record is not None
                and previous_record.expire_at == expire_at
                and previous_record.reason == normalized_reason
                and previous_record.muted_by == actor_id
            )
            if changed:
                stored_record = self.ticket_mute_repository.upsert(
                    TicketMuteRecord(
                        ticket_id=context.ticket.ticket_id,
                        user_id=target_id,
                        muted_by=actor_id,
                        reason=normalized_reason,
                        expire_at=expire_at,
                        created_at=previous_record.created_at if previous_record is not None else reference_time.isoformat(),
                        updated_at=reference_time.isoformat(),
                    )
                )
            else:
                stored_record = previous_record or TicketMuteRecord(
                    ticket_id=context.ticket.ticket_id,
                    user_id=target_id,
                    muted_by=actor_id,
                    reason=normalized_reason,
                    expire_at=expire_at,
                    created_at=reference_time.isoformat(),
                    updated_at=reference_time.isoformat(),
                )

            try:
                await self._sync_ticket_permissions(
                    channel,
                    ticket=context.ticket,
                    config=context.config,
                    category=context.category,
                    extra_participants=(target,),
                    include_staff=False,
                )
            except Exception:
                if changed:
                    if previous_record is None:
                        self.ticket_mute_repository.delete(context.ticket.ticket_id, target_id)
                    else:
                        self.ticket_mute_repository.upsert(previous_record)
                raise

            log_message = None
            if changed:
                log_message = await self._send_channel_log(
                    channel,
                    content=self._build_mute_log_content(
                        actor_id=actor_id,
                        ticket_id=context.ticket.ticket_id,
                        target_id=target_id,
                        expire_at=expire_at,
                        reason=normalized_reason,
                    ),
                )
                if self.staff_panel_service is not None:
                    self.staff_panel_service.request_refresh(context.ticket.ticket_id)

            return MuteMutationResult(
                ticket=context.ticket,
                target_id=target_id,
                muted_by_id=stored_record.muted_by,
                reason=stored_record.reason,
                expire_at=stored_record.expire_at,
                changed=changed,
                log_message=log_message,
            )

    async def unmute_member(
        self,
        channel: Any,
        *,
        actor: Any,
        target: Any,
        is_bot_owner: bool = False,
    ) -> UnmuteMutationResult:
        channel_id = getattr(channel, "id", None)
        target_id = getattr(target, "id", None)
        if channel_id is None or getattr(channel, "set_permissions", None) is None:
            raise ValidationError("当前频道不支持 ticket unmute。")
        if getattr(actor, "id", None) is None or target_id is None:
            raise ValidationError("无法识别当前操作对象。")

        async with self._acquire_ticket_lock(channel_id):
            context = self.guard_service.load_ticket_context(
                channel_id,
                allowed_statuses=(TicketStatus.SUBMITTED, TicketStatus.SLEEP),
                invalid_state_message="当前 ticket 仅在 submitted / sleep 状态可执行 unmute。",
            )
            self.guard_service.assert_staff_actor(
                actor,
                config=context.config,
                category=context.category,
                is_bot_owner=is_bot_owner,
            )

            previous_record = self.ticket_mute_repository.get_by_ticket_and_user(context.ticket.ticket_id, target_id)
            if previous_record is None:
                return UnmuteMutationResult(
                    ticket=context.ticket,
                    target_id=target_id,
                    previous_expire_at=None,
                    changed=False,
                    log_message=None,
                )

            self.ticket_mute_repository.delete(context.ticket.ticket_id, target_id)
            include_staff = target_id == getattr(context.ticket, "creator_id", None)
            try:
                await self._sync_ticket_permissions(
                    channel,
                    ticket=context.ticket,
                    config=context.config,
                    category=context.category,
                    extra_participants=(target,),
                    include_staff=include_staff,
                )
            except Exception:
                self.ticket_mute_repository.upsert(previous_record)
                raise

            log_message = await self._send_channel_log(
                channel,
                content=(f"🔊 <@{getattr(actor, 'id', None)}> 已解除 ticket `{context.ticket.ticket_id}` 对 <@{target_id}> 的禁言。"),
            )
            if self.staff_panel_service is not None:
                self.staff_panel_service.request_refresh(context.ticket.ticket_id)

            return UnmuteMutationResult(
                ticket=context.ticket,
                target_id=target_id,
                previous_expire_at=previous_record.expire_at,
                changed=True,
                log_message=log_message,
            )

    async def sweep_expired_mutes(
        self,
        *,
        now: datetime | str | None = None,
    ) -> list[MuteExpirationResult]:
        reference_time = self._to_utc_datetime(now)
        outcomes: list[MuteExpirationResult] = []

        for record in self.ticket_mute_repository.list_due_expirations(reference_time.isoformat()):
            try:
                outcome = await self._expire_due_mute(
                    ticket_id=record.ticket_id,
                    user_id=record.user_id,
                    reference_time=reference_time,
                )
            except Exception:
                self.logger.exception(
                    "Failed to expire ticket mute. ticket_id=%s user_id=%s",
                    record.ticket_id,
                    record.user_id,
                )
                continue
            if outcome is not None:
                outcomes.append(outcome)

        return outcomes

    async def _expire_due_mute(
        self,
        *,
        ticket_id: str,
        user_id: int,
        reference_time: datetime,
    ) -> MuteExpirationResult | None:
        ticket = self.ticket_repository.get_by_ticket_id(ticket_id)
        lock_key: str | int = ticket.channel_id if ticket is not None and ticket.channel_id is not None else ticket_id

        async with self._acquire_ticket_lock(lock_key):
            mute_record = self.ticket_mute_repository.get_by_ticket_and_user(ticket_id, user_id)
            if mute_record is None or not self._is_due(mute_record, reference_time):
                return None

            ticket = self.ticket_repository.get_by_ticket_id(ticket_id)
            if ticket is None:
                self.ticket_mute_repository.delete(ticket_id, user_id)
                return None

            channel = await self._resolve_channel(ticket.channel_id)
            target = self._resolve_channel_member(channel, user_id)
            config = self.guard_service.guild_repository.get_config(ticket.guild_id)
            category = self.guard_service.guild_repository.get_category(ticket.guild_id, ticket.category_key)
            self.ticket_mute_repository.delete(ticket.ticket_id, user_id)

            if channel is None or target is None or config is None or not config.is_initialized or category is None:
                return None

            include_staff = user_id == getattr(ticket, "creator_id", None)
            try:
                await self._sync_ticket_permissions(
                    channel,
                    ticket=ticket,
                    config=config,
                    category=category,
                    extra_participants=(target,),
                    include_staff=include_staff,
                )
            except Exception:
                self.ticket_mute_repository.upsert(mute_record)
                raise

            log_message = await self._send_channel_log(
                channel,
                content=(f"⏰ ticket `{ticket.ticket_id}` 对 <@{user_id}> 的临时禁言已自动解除。"),
            )
            if self.staff_panel_service is not None:
                self.staff_panel_service.request_refresh(ticket.ticket_id)

            return MuteExpirationResult(
                ticket=ticket,
                target_id=user_id,
                expire_at=mute_record.expire_at,
                changed=True,
                log_message=log_message,
            )

    def _assert_mutable_target(
        self,
        channel: Any,
        *,
        ticket: Any,
        target: Any,
        actor: Any,
        config: Any,
        category: Any,
    ) -> None:
        target_id = getattr(target, "id", None)
        actor_id = getattr(actor, "id", None)
        if target_id is None:
            raise ValidationError("无法识别要禁言的成员。")
        if actor_id == target_id:
            raise ValidationError("不能对自己执行 ticket mute。")
        if getattr(target, "bot", False):
            raise ValidationError("不能对 bot 执行 ticket mute。")
        if self.guard_service.is_staff_actor(
            target,
            config=config,
            category=category,
            is_bot_owner=False,
        ):
            raise PermissionDeniedError("不能对当前分类合法 staff、Ticket 管理员或 Bot 执行 mute。")
        if target_id == getattr(ticket, "creator_id", None):
            return
        if self._has_explicit_participant_access(channel, target):
            return
        raise ValidationError("当前仅支持 mute ticket 创建者，或已被显式允许发言的非 staff 参与者。")

    @staticmethod
    def _has_explicit_participant_access(channel: Any, target: Any) -> bool:
        overwrites_for = getattr(channel, "overwrites_for", None)
        if not callable(overwrites_for):
            return False
        overwrite = overwrites_for(target)
        return any(getattr(overwrite, field, None) is True for field in ("view_channel", "send_messages", "read_message_history"))

    async def _resolve_channel(self, channel_id: int | None) -> Any | None:
        if self.bot is None or channel_id is None:
            return None

        channel = getattr(self.bot, "get_channel", lambda _: None)(channel_id)
        if channel is not None:
            return channel

        fetch_channel = getattr(self.bot, "fetch_channel", None)
        if fetch_channel is None:
            return None

        try:
            return await fetch_channel(channel_id)
        except Exception:
            return None

    async def _sync_ticket_permissions(
        self,
        channel: Any,
        *,
        ticket: Any,
        config: Any,
        category: Any,
        extra_participants: tuple[Any, ...] = (),
        include_staff: bool = False,
    ) -> None:
        creator = self._resolve_channel_member(channel, getattr(ticket, "creator_id", 0))
        muted_participants = self._resolve_muted_participants(channel, ticket.ticket_id)
        await self.permission_service.apply_ticket_permissions(
            channel,
            config=config,
            category=category,
            creator=creator,
            participants=extra_participants,
            muted_participants=muted_participants,
            include_staff=include_staff,
            include_participants=True,
        )

    def _resolve_muted_participants(self, channel: Any, ticket_id: str) -> list[Any]:
        return [
            member
            for member in (self._resolve_channel_member(channel, record.user_id) for record in self.ticket_mute_repository.list_by_ticket(ticket_id))
            if member is not None
        ]

    @staticmethod
    def _resolve_channel_member(channel: Any | None, user_id: int) -> Any | None:
        if channel is None:
            return None
        guild = getattr(channel, "guild", None)
        if guild is None:
            return None
        get_member = getattr(guild, "get_member", None)
        if not callable(get_member):
            return None
        return get_member(user_id)

    @staticmethod
    def _normalize_reason(reason: str | None) -> str | None:
        if reason is None:
            return None
        normalized = reason.strip()
        return normalized or None

    @staticmethod
    def _build_expire_at(duration: str | None, *, reference_time: datetime) -> str | None:
        if duration is None:
            return None
        normalized_duration = duration.strip()
        if not normalized_duration:
            return None

        matched = _DURATION_PATTERN.fullmatch(normalized_duration)
        if matched is None:
            raise ValidationError("mute duration 仅支持 `30m`、`2h`、`1d`、`45分钟` 这类格式。")

        amount = int(matched.group(1))
        unit = matched.group(2).lower()
        seconds = amount * _DURATION_SECONDS_BY_UNIT[unit]
        if seconds < 60:
            raise ValidationError("mute duration 最短为 60 秒。")
        return (reference_time + timedelta(seconds=seconds)).isoformat()

    @staticmethod
    def _is_due(record: TicketMuteRecord, reference_time: datetime) -> bool:
        if record.expire_at is None:
            return False
        return ModerationService._parse_iso_datetime(record.expire_at) <= reference_time

    @staticmethod
    def _to_utc_datetime(value: datetime | str | None) -> datetime:
        if value is None:
            return datetime.now(timezone.utc)
        if isinstance(value, str):
            return ModerationService._parse_iso_datetime(value)
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _parse_iso_datetime(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _build_mute_log_content(
        *,
        actor_id: int,
        ticket_id: str,
        target_id: int,
        expire_at: str | None,
        reason: str | None,
    ) -> str:
        lines = [
            f"🔇 <@{actor_id}> 已对 ticket `{ticket_id}` 中的 <@{target_id}> 执行禁言。",
            f"- 到期时间：{expire_at or '手动解除'}",
        ]
        if reason is not None:
            lines.append(f"- 原因：{reason}")
        return "\n".join(lines)

    @staticmethod
    async def _send_channel_log(channel: Any, *, content: str) -> Any | None:
        send = getattr(channel, "send", None)
        if send is None:
            return None
        return await send(content=content)

    @asynccontextmanager
    async def _acquire_ticket_lock(self, key: str | int) -> AsyncIterator[None]:
        if self.lock_manager is None:
            yield
            return

        async with self.lock_manager.acquire(f"ticket-moderation:{key}"):
            yield
