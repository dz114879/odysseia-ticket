from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

import discord

from core.enums import ClaimMode, TicketStatus
from core.errors import ValidationError
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.connection import DatabaseManager
from db.repositories.ticket_repository import TicketRepository
from runtime.locks import LockManager
from discord_ui.panel_embeds import build_staff_control_panel_embed
from discord_ui.staff_panel_view import StaffPanelView
from services.draft_service import DraftService
from services.submission_guard_service import SubmissionContext, SubmissionGuardService


@dataclass(frozen=True, slots=True)
class SubmitDraftResult:
    ticket: TicketRecord
    old_channel_name: str
    new_channel_name: str
    divider_message: Any | None
    staff_panel_message: Any | None
    welcome_message_updated: bool
    channel_name_changed: bool
    submitted: bool


class SubmitService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        guard_service: SubmissionGuardService | None = None,
        ticket_repository: TicketRepository | None = None,
        lock_manager: LockManager | None = None,
    ) -> None:
        self.database = database
        self.guard_service = guard_service or SubmissionGuardService(database)
        self.ticket_repository = ticket_repository or TicketRepository(database)
        self.lock_manager = lock_manager

    async def submit_draft_ticket(
        self,
        channel: Any,
        *,
        actor_id: int,
        requested_title: str | None = None,
        welcome_message: Any | None = None,
    ) -> SubmitDraftResult:
        channel_id = getattr(channel, "id", None)
        if channel_id is None:
            raise ValidationError("当前频道不支持 ticket submit。")

        async with self._acquire_channel_lock(channel_id):
            context = self.guard_service.inspect_submission(
                channel_id=channel_id,
                actor_id=actor_id,
                channel_name=getattr(channel, "name", None),
            )
            old_channel_name = str(getattr(channel, "name", ""))

            if context.already_submitted:
                resolved_welcome_message = welcome_message or await self._resolve_welcome_message(
                    channel,
                    ticket_id=context.ticket.ticket_id,
                )
                welcome_message_updated = await self._remove_welcome_view(resolved_welcome_message)
                return SubmitDraftResult(
                    ticket=context.ticket,
                    old_channel_name=old_channel_name,
                    new_channel_name=old_channel_name,
                    divider_message=None,
                    staff_panel_message=None,
                    welcome_message_updated=welcome_message_updated,
                    channel_name_changed=False,
                    submitted=False,
                )

            if context.requires_title and not requested_title:
                raise ValidationError("当前 draft 仍使用默认频道名，请先补充一个简短标题后再提交。")

            new_channel_name = await self._apply_submission_channel_state(
                channel=channel,
                context=context,
                requested_title=requested_title,
            )
            updated_ticket = self.ticket_repository.update(
                context.ticket.ticket_id,
                status=TicketStatus.SUBMITTED,
            ) or context.ticket
            divider_message = await self._send_submission_divider(channel, updated_ticket)
            staff_panel_message = await self._send_staff_control_panel(
                channel,
                ticket=updated_ticket,
                category=context.category,
                config=context.config,
            )
            if staff_panel_message is not None:
                updated_ticket = self.ticket_repository.update(
                    updated_ticket.ticket_id,
                    staff_panel_message_id=getattr(staff_panel_message, "id", None),
                ) or updated_ticket
            resolved_welcome_message = welcome_message or await self._resolve_welcome_message(
                channel,
                ticket_id=updated_ticket.ticket_id,
            )
            welcome_message_updated = await self._remove_welcome_view(resolved_welcome_message)

            return SubmitDraftResult(
                ticket=updated_ticket,
                old_channel_name=old_channel_name,
                new_channel_name=new_channel_name,
                divider_message=divider_message,
                staff_panel_message=staff_panel_message,
                welcome_message_updated=welcome_message_updated,
                channel_name_changed=new_channel_name != old_channel_name,
                submitted=True,
            )

    async def _apply_submission_channel_state(
        self,
        *,
        channel: Any,
        context: SubmissionContext,
        requested_title: str | None,
    ) -> str:
        current_name = str(getattr(channel, "name", ""))
        next_name = current_name
        if requested_title:
            next_name = DraftService.build_renamed_channel_name(
                ticket=context.ticket,
                requested_name=requested_title,
            )

        edit_kwargs: dict[str, object] = {
            "topic": self._build_channel_topic(context.ticket),
            "reason": f"Submit draft ticket {context.ticket.ticket_id}",
        }
        if next_name != current_name:
            edit_kwargs["name"] = next_name
        await channel.edit(**edit_kwargs)

        await self._grant_staff_access(
            channel=channel,
            config=context.config,
            category=context.category,
        )
        return next_name

    async def _grant_staff_access(
        self,
        *,
        channel: Any,
        config: GuildConfigRecord,
        category: TicketCategoryConfig,
    ) -> None:
        set_permissions = getattr(channel, "set_permissions", None)
        guild = getattr(channel, "guild", None)
        if guild is None or set_permissions is None:
            return

        can_staff_send = config.claim_mode is not ClaimMode.STRICT
        visible_permissions = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=can_staff_send,
            read_message_history=True,
            attach_files=can_staff_send,
            embed_links=can_staff_send,
        )

        targets: list[Any] = []
        if config.admin_role_id is not None:
            admin_role = guild.get_role(config.admin_role_id)
            if admin_role is not None:
                targets.append(admin_role)

        if category.staff_role_id is not None:
            staff_role = guild.get_role(category.staff_role_id)
            if staff_role is not None:
                targets.append(staff_role)

        get_member = getattr(guild, "get_member", None)
        if callable(get_member):
            for staff_user_id in self._parse_staff_user_ids(category.staff_user_ids_json):
                staff_member = get_member(staff_user_id)
                if staff_member is not None:
                    targets.append(staff_member)

        seen_target_ids: set[int] = set()
        for target in targets:
            target_id = getattr(target, "id", None)
            if target_id is None or target_id in seen_target_ids:
                continue
            seen_target_ids.add(target_id)
            await set_permissions(
                target,
                overwrite=visible_permissions,
                reason=f"Open submitted ticket {getattr(channel, 'id', 'unknown')} to staff",
            )

    async def _send_submission_divider(self, channel: Any, ticket: TicketRecord) -> Any | None:
        send = getattr(channel, "send", None)
        if send is None:
            return None
        return await send(
            content=(
                "━━━━━━━━━━━━━━━━━━\n"
                f"draft ticket `{ticket.ticket_id}` 已提交，staff 现在可以查看并接手处理。\n"
                "━━━━━━━━━━━━━━━━━━"
            )
        )

    async def _send_staff_control_panel(
        self,
        channel: Any,
        *,
        ticket: TicketRecord,
        category: TicketCategoryConfig,
        config: GuildConfigRecord,
    ) -> Any | None:
        send = getattr(channel, "send", None)
        if send is None:
            return None

        return await send(
            embed=build_staff_control_panel_embed(ticket, category=category, config=config),
            view=StaffPanelView(),
        )

    async def _resolve_welcome_message(self, channel: Any, *, ticket_id: str) -> Any | None:
        pins = getattr(channel, "pins", None)
        if pins is None:
            return None

        try:
            pinned_messages = await pins()
        except Exception:
            return None

        if not pinned_messages:
            return None

        for message in pinned_messages:
            content = getattr(message, "content", "") or ""
            if ticket_id in content:
                return message
        return pinned_messages[0]

    @staticmethod
    async def _remove_welcome_view(message: Any | None) -> bool:
        if message is None:
            return False

        edit = getattr(message, "edit", None)
        if edit is None:
            return False

        try:
            await edit(view=None)
        except Exception:
            return False
        return True

    @staticmethod
    def _build_channel_topic(ticket: TicketRecord) -> str:
        return f"ticket_id={ticket.ticket_id} creator_id={ticket.creator_id} status=submitted"

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

    @asynccontextmanager
    async def _acquire_channel_lock(self, channel_id: int) -> AsyncIterator[None]:
        if self.lock_manager is None:
            yield
            return

        async with self.lock_manager.acquire(f"draft-submit:{channel_id}"):
            yield
