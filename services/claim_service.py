from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Iterable

import discord

from core.enums import ClaimMode, TicketStatus
from core.errors import (
    InvalidTicketStateError,
    PermissionDeniedError,
    TicketNotFoundError,
    ValidationError,
)
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.connection import DatabaseManager
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository
from runtime.locks import LockManager
from services.staff_guard_service import StaffGuardService, StaffTicketContext
from services.staff_panel_service import StaffPanelService


@dataclass(frozen=True, slots=True)
class ClaimMutationResult:
    ticket: TicketRecord
    previous_claimer_id: int | None
    changed: bool
    forced: bool
    strict_mode: bool
    log_message: Any | None


class ClaimService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        guild_repository: GuildRepository | None = None,
        ticket_repository: TicketRepository | None = None,
        lock_manager: LockManager | None = None,
        guard_service: StaffGuardService | None = None,
        staff_panel_service: StaffPanelService | None = None,
    ) -> None:
        self.database = database
        self.guild_repository = guild_repository or GuildRepository(database)
        self.ticket_repository = ticket_repository or TicketRepository(database)
        self.lock_manager = lock_manager
        self.staff_panel_service = staff_panel_service
        self.guard_service = guard_service or StaffGuardService(
            database,
            guild_repository=self.guild_repository,
            ticket_repository=self.ticket_repository,
        )

    async def claim_ticket(
        self,
        channel: Any,
        *,
        actor: Any,
        is_bot_owner: bool = False,
    ) -> ClaimMutationResult:
        channel_id = getattr(channel, "id", None)
        actor_id = getattr(actor, "id", None)
        if channel_id is None:
            raise ValidationError("当前频道不支持 ticket claim。")
        if actor_id is None:
            raise ValidationError("无法识别当前操作人的身份。")

        async with self._acquire_channel_lock(channel_id):
            context = self._load_ticket_context(channel_id)
            self._assert_staff_actor(
                actor,
                config=context.config,
                category=context.category,
                is_bot_owner=is_bot_owner,
            )

            if context.ticket.claimed_by == actor_id:
                return ClaimMutationResult(
                    ticket=context.ticket,
                    previous_claimer_id=context.ticket.claimed_by,
                    changed=False,
                    forced=False,
                    strict_mode=context.config.claim_mode is ClaimMode.STRICT,
                    log_message=None,
                )

            if context.ticket.claimed_by is not None:
                raise ValidationError(
                    f"该 ticket 已被 <@{context.ticket.claimed_by}> 认领；如需转交，请等待后续 `/ticket transfer-claim`。"
                )

            updated_ticket = self.ticket_repository.update(
                context.ticket.ticket_id,
                claimed_by=actor_id,
            ) or context.ticket
            await self._sync_staff_permissions(
                channel=channel,
                config=context.config,
                category=context.category,
                previous_claimer_id=context.ticket.claimed_by,
                active_claimer=actor,
            )
            log_message = await self._send_channel_log(
                channel,
                content=f"✋ <@{actor_id}> 已认领 ticket `{updated_ticket.ticket_id}`。",
            )
            if self.staff_panel_service is not None:
                self.staff_panel_service.request_refresh(updated_ticket.ticket_id)
            return ClaimMutationResult(
                ticket=updated_ticket,
                previous_claimer_id=context.ticket.claimed_by,
                changed=True,
                forced=False,
                strict_mode=context.config.claim_mode is ClaimMode.STRICT,
                log_message=log_message,
            )

    async def unclaim_ticket(
        self,
        channel: Any,
        *,
        actor: Any,
        is_bot_owner: bool = False,
    ) -> ClaimMutationResult:
        channel_id = getattr(channel, "id", None)
        actor_id = getattr(actor, "id", None)
        if channel_id is None:
            raise ValidationError("当前频道不支持 ticket unclaim。")
        if actor_id is None:
            raise ValidationError("无法识别当前操作人的身份。")

        async with self._acquire_channel_lock(channel_id):
            context = self._load_ticket_context(channel_id)
            self._assert_staff_actor(
                actor,
                config=context.config,
                category=context.category,
                is_bot_owner=is_bot_owner,
            )

            if context.ticket.claimed_by is None:
                return ClaimMutationResult(
                    ticket=context.ticket,
                    previous_claimer_id=None,
                    changed=False,
                    forced=False,
                    strict_mode=context.config.claim_mode is ClaimMode.STRICT,
                    log_message=None,
                )

            forced = context.ticket.claimed_by != actor_id
            if forced and not self._is_ticket_admin(
                actor,
                config=context.config,
                is_bot_owner=is_bot_owner,
            ):
                raise PermissionDeniedError("只有当前认领者或 Ticket 管理员可以取消认领。")

            updated_ticket = self.ticket_repository.update(
                context.ticket.ticket_id,
                claimed_by=None,
            ) or context.ticket
            await self._sync_staff_permissions(
                channel=channel,
                config=context.config,
                category=context.category,
                previous_claimer_id=context.ticket.claimed_by,
                active_claimer=None,
            )

            if forced:
                content = (
                    f"👐 <@{actor_id}> 已取消 ticket `{updated_ticket.ticket_id}` 的认领。\n"
                    f"- 原认领者：<@{context.ticket.claimed_by}>"
                )
            else:
                content = f"👐 <@{actor_id}> 已放弃认领 ticket `{updated_ticket.ticket_id}`。"
            log_message = await self._send_channel_log(channel, content=content)
            if self.staff_panel_service is not None:
                self.staff_panel_service.request_refresh(updated_ticket.ticket_id)
            return ClaimMutationResult(
                ticket=updated_ticket,
                previous_claimer_id=context.ticket.claimed_by,
                changed=True,
                forced=forced,
                strict_mode=context.config.claim_mode is ClaimMode.STRICT,
                log_message=log_message,
            )

    def _load_ticket_context(self, channel_id: int) -> StaffTicketContext:
        return self.guard_service.load_ticket_context(
            channel_id,
            allowed_statuses=(TicketStatus.SUBMITTED,),
            invalid_state_message="当前 ticket 不处于 submitted 状态，无法执行此操作。",
        )

    def _assert_staff_actor(
        self,
        actor: Any,
        *,
        config: GuildConfigRecord,
        category: TicketCategoryConfig,
        is_bot_owner: bool,
    ) -> None:
        self.guard_service.assert_staff_actor(
            actor,
            config=config,
            category=category,
            is_bot_owner=is_bot_owner,
        )

    def _is_staff_actor(
        self,
        actor: Any,
        *,
        config: GuildConfigRecord,
        category: TicketCategoryConfig,
        is_bot_owner: bool,
    ) -> bool:
        return self.guard_service.is_staff_actor(
            actor,
            config=config,
            category=category,
            is_bot_owner=is_bot_owner,
        )

    def _is_ticket_admin(
        self,
        actor: Any,
        *,
        config: GuildConfigRecord,
        is_bot_owner: bool,
    ) -> bool:
        return self.guard_service.is_ticket_admin(actor, config=config, is_bot_owner=is_bot_owner)

    async def _sync_staff_permissions(
        self,
        *,
        channel: Any,
        config: GuildConfigRecord,
        previous_claimer_id: int | None,
        category: TicketCategoryConfig,
        active_claimer: Any | None,
    ) -> None:
        guild = getattr(channel, "guild", None)
        set_permissions = getattr(channel, "set_permissions", None)
        if guild is None or set_permissions is None:
            return

        readable_overwrite = self._build_staff_overwrite(can_send=False)
        writable_overwrite = self._build_staff_overwrite(can_send=True)
        strict_mode = config.claim_mode is ClaimMode.STRICT

        role_targets, member_targets = self._resolve_staff_targets(
            guild,
            config=config,
            category=category,
        )

        explicit_member_ids = {getattr(member, "id", None) for member in member_targets}
        base_overwrite = readable_overwrite if strict_mode else writable_overwrite
        for target in [*role_targets, *member_targets]:
            await set_permissions(
                target,
                overwrite=base_overwrite,
                reason="Recalculate staff participation for ticket claim state",
            )

        previous_claimer = None
        if previous_claimer_id is not None:
            get_member = getattr(guild, "get_member", None)
            if callable(get_member):
                previous_claimer = get_member(previous_claimer_id)

        if previous_claimer is not None and previous_claimer_id not in explicit_member_ids:
            await set_permissions(
                previous_claimer,
                overwrite=readable_overwrite if strict_mode else writable_overwrite,
                reason="Normalize previous claimer override after claim state change",
            )

        active_claimer_id = getattr(active_claimer, "id", None)
        if (
            active_claimer is not None
            and active_claimer_id is not None
            and not strict_mode
            and active_claimer_id not in explicit_member_ids
            and active_claimer_id != previous_claimer_id
        ):
            await set_permissions(active_claimer, overwrite=base_overwrite, reason="Normalize current claimer override")

        if strict_mode and active_claimer is not None:
            await set_permissions(
                active_claimer,
                overwrite=writable_overwrite,
                reason="Allow current claimer to speak in strict claim mode",
            )

    @staticmethod
    def _resolve_staff_targets(
        guild: Any,
        *,
        config: GuildConfigRecord,
        category: TicketCategoryConfig,
    ) -> tuple[list[Any], list[Any]]:
        role_targets: list[Any] = []
        member_targets: list[Any] = []

        if config.admin_role_id is not None:
            admin_role = guild.get_role(config.admin_role_id)
            if admin_role is not None:
                role_targets.append(admin_role)

        if category.staff_role_id is not None:
            staff_role = guild.get_role(category.staff_role_id)
            if staff_role is not None:
                role_targets.append(staff_role)

        get_member = getattr(guild, "get_member", None)
        if callable(get_member):
            for staff_user_id in ClaimService._parse_staff_user_ids(category.staff_user_ids_json):
                member = get_member(staff_user_id)
                if member is not None:
                    member_targets.append(member)

        unique_roles: list[Any] = []
        seen_role_ids: set[int] = set()
        for role in role_targets:
            role_id = getattr(role, "id", None)
            if role_id is None or role_id in seen_role_ids:
                continue
            seen_role_ids.add(role_id)
            unique_roles.append(role)

        unique_members: list[Any] = []
        seen_member_ids: set[int] = set()
        for member in member_targets:
            member_id = getattr(member, "id", None)
            if member_id is None or member_id in seen_member_ids:
                continue
            seen_member_ids.add(member_id)
            unique_members.append(member)

        return unique_roles, unique_members

    @staticmethod
    def _build_staff_overwrite(*, can_send: bool) -> discord.PermissionOverwrite:
        return discord.PermissionOverwrite(
            view_channel=True,
            send_messages=can_send,
            read_message_history=True,
            attach_files=can_send,
            embed_links=can_send,
        )

    @staticmethod
    async def _send_channel_log(channel: Any, *, content: str) -> Any | None:
        send = getattr(channel, "send", None)
        if send is None:
            return None
        return await send(content=content)

    @staticmethod
    def _parse_staff_user_ids(raw_value: str) -> list[int]:
        try:
            data = json.loads(raw_value or "[]")
        except json.JSONDecodeError:
            return []

        values: list[int] = []
        for item in data if isinstance(data, list) else []:
            try:
                values.append(int(item))
            except (TypeError, ValueError):
                continue
        return values

    @staticmethod
    def _extract_role_ids(roles: Iterable[Any]) -> set[int]:
        role_ids: set[int] = set()
        for role in roles:
            role_id = getattr(role, "id", None)
            if role_id is not None:
                role_ids.add(role_id)
        return role_ids

    @asynccontextmanager
    async def _acquire_channel_lock(self, channel_id: int) -> AsyncIterator[None]:
        if self.lock_manager is None:
            yield
            return

        async with self.lock_manager.acquire(f"staff-claim:{channel_id}"):
            yield
