from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from collections.abc import AsyncIterator

from core.enums import ClaimMode, TicketStatus
from core.errors import (
    PermissionDeniedError,
    ValidationError,
)
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.connection import DatabaseManager
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository
from runtime.locks import LockManager
from services.staff_guard_service import StaffGuardService, StaffTicketContext
from services.staff_permission_service import StaffPermissionService
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
        permission_service: StaffPermissionService | None = None,
    ) -> None:
        self.database = database
        self.guild_repository = guild_repository or GuildRepository(database)
        self.ticket_repository = ticket_repository or TicketRepository(database)
        self.lock_manager = lock_manager
        self.staff_panel_service = staff_panel_service
        self.permission_service = permission_service or StaffPermissionService()
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
                raise ValidationError(f"该 ticket 已被 <@{context.ticket.claimed_by}> 认领；如需转交，请使用 `/ticket transfer-claim`。")

            updated_ticket = (
                self.ticket_repository.update(
                    context.ticket.ticket_id,
                    claimed_by=actor_id,
                )
                or context.ticket
            )
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

    async def transfer_claim(
        self,
        channel: Any,
        *,
        actor: Any,
        target: Any,
        is_bot_owner: bool = False,
    ) -> ClaimMutationResult:
        channel_id = getattr(channel, "id", None)
        actor_id = getattr(actor, "id", None)
        target_id = getattr(target, "id", None)
        if channel_id is None:
            raise ValidationError("当前频道不支持 ticket transfer-claim。")
        if actor_id is None:
            raise ValidationError("无法识别当前操作人的身份。")
        if target_id is None:
            raise ValidationError("无法识别要转交给哪位 staff。")

        async with self._acquire_channel_lock(channel_id):
            context = self._load_ticket_context(channel_id)
            self._assert_staff_actor(
                actor,
                config=context.config,
                category=context.category,
                is_bot_owner=is_bot_owner,
            )

            if context.ticket.claimed_by is None:
                raise ValidationError("当前 ticket 尚未被认领，请先使用 `/ticket claim`。")

            forced = context.ticket.claimed_by != actor_id
            if forced and not self._is_ticket_admin(
                actor,
                config=context.config,
                is_bot_owner=is_bot_owner,
            ):
                raise PermissionDeniedError("只有当前认领者或 Ticket 管理员可以转交认领。")

            if not self._is_current_category_staff(target, category=context.category):
                raise ValidationError("认领只能转交给当前分类的合法 staff。")

            if context.ticket.claimed_by == target_id:
                return ClaimMutationResult(
                    ticket=context.ticket,
                    previous_claimer_id=context.ticket.claimed_by,
                    changed=False,
                    forced=forced,
                    strict_mode=context.config.claim_mode is ClaimMode.STRICT,
                    log_message=None,
                )

            previous_claimer_id = context.ticket.claimed_by
            updated_ticket = (
                self.ticket_repository.update(
                    context.ticket.ticket_id,
                    claimed_by=target_id,
                )
                or context.ticket
            )
            await self._sync_staff_permissions(
                channel=channel,
                config=context.config,
                category=context.category,
                previous_claimer_id=previous_claimer_id,
                active_claimer=target,
            )

            if forced:
                content = f"🔁 <@{actor_id}> 已将 ticket `{updated_ticket.ticket_id}` 的认领从 <@{previous_claimer_id}> 转交给 <@{target_id}>。"
            else:
                content = f"🔁 <@{actor_id}> 已将 ticket `{updated_ticket.ticket_id}` 的认领转交给 <@{target_id}>。"
            log_message = await self._send_channel_log(channel, content=content)
            if self.staff_panel_service is not None:
                self.staff_panel_service.request_refresh(updated_ticket.ticket_id)
            return ClaimMutationResult(
                ticket=updated_ticket,
                previous_claimer_id=previous_claimer_id,
                changed=True,
                forced=forced,
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

            updated_ticket = (
                self.ticket_repository.update(
                    context.ticket.ticket_id,
                    claimed_by=None,
                )
                or context.ticket
            )
            await self._sync_staff_permissions(
                channel=channel,
                config=context.config,
                category=context.category,
                previous_claimer_id=context.ticket.claimed_by,
                active_claimer=None,
            )

            if forced:
                content = f"👐 <@{actor_id}> 已取消 ticket `{updated_ticket.ticket_id}` 的认领。\n- 原认领者：<@{context.ticket.claimed_by}>"
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

    def _is_current_category_staff(
        self,
        actor: Any,
        *,
        category: TicketCategoryConfig,
    ) -> bool:
        actor_id = getattr(actor, "id", None)
        if actor_id is None:
            return False
        if actor_id in set(self.guard_service._parse_staff_user_ids(category.staff_user_ids_json)):
            return True
        role_ids = self.guard_service._extract_role_ids(getattr(actor, "roles", []))
        return category.staff_role_id is not None and category.staff_role_id in role_ids

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
        await self.permission_service.apply_ticket_permissions(
            channel,
            include_participants=False,
            config=config,
            category=category,
            active_claimer=active_claimer,
            previous_claimer_id=previous_claimer_id,
            visible_reason="Recalculate staff participation for ticket claim state",
            previous_claimer_reason="Normalize previous claimer override after claim state change",
            active_claimer_reason="Normalize current claimer override",
            strict_claimer_reason="Allow current claimer to speak in strict claim mode",
        )

    @staticmethod
    async def _send_channel_log(channel: Any, *, content: str) -> Any | None:
        send = getattr(channel, "send", None)
        if send is None:
            return None
        return await send(content=content)

    @asynccontextmanager
    async def _acquire_channel_lock(self, channel_id: int) -> AsyncIterator[None]:
        if self.lock_manager is None:
            yield
            return

        async with self.lock_manager.acquire(f"staff-claim:{channel_id}"):
            yield
