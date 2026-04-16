from __future__ import annotations

from typing import Any

from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.repositories.ticket_repository import TicketRepository
from discord_ui.panel_embeds import build_staff_control_panel_embed
from discord_ui.staff_panel_view import StaffPanelView
from services.staff_permission_service import StaffPermissionService


class SubmitSideEffectsService:
    def __init__(
        self,
        *,
        ticket_repository: TicketRepository,
        permission_service: StaffPermissionService,
    ) -> None:
        self.ticket_repository = ticket_repository
        self.permission_service = permission_service

    async def rename_channel(self, *, channel: Any, current_name: str, next_name: str, reason: str, enabled: bool) -> str:
        if not enabled or next_name == current_name:
            return current_name
        await channel.edit(name=next_name, reason=reason)
        return next_name

    async def grant_staff_access(self, *, channel: Any, config: GuildConfigRecord, category: TicketCategoryConfig) -> None:
        await self.permission_service.apply_ticket_permissions(
            channel,
            include_participants=False,
            config=config,
            category=category,
            visible_reason=f"Open submitted ticket {getattr(channel, 'id', 'unknown')} to staff",
        )

    async def ensure_staff_control_panel(
        self,
        *,
        channel: Any,
        ticket: TicketRecord,
        category: TicketCategoryConfig,
        config: GuildConfigRecord,
    ) -> tuple[TicketRecord, Any | None]:
        if ticket.staff_panel_message_id is not None:
            return ticket, None
        send = getattr(channel, "send", None)
        if send is None:
            return ticket, None

        staff_panel_message = await send(
            embed=build_staff_control_panel_embed(ticket, category=category, config=config),
            view=StaffPanelView(),
        )
        updated_ticket = self.ticket_repository.update(ticket.ticket_id, staff_panel_message_id=getattr(staff_panel_message, "id", None)) or ticket
        return updated_ticket, staff_panel_message

    @staticmethod
    async def send_submission_divider(channel: Any, ticket: TicketRecord, *, from_queue: bool) -> Any | None:
        send = getattr(channel, "send", None)
        if send is None:
            return None
        return await send(
            content=(
                "━━━━━━━━━━━━━━━━━━\n"
                + (
                    "✅ 您的 Ticket 已从排队中自动提交 ！\n\n相关管理员现在可以查看并处理。\n\n=== 草稿期分界线 ===\n"
                    if from_queue
                    else "✅ 您的 Ticket 已成功提交 ！\n\n请稍候，相关管理员会前来处理。\n\n在此期间，请勿重复提交相同主题的Ticket。感谢您的理解和支持。\n\n=== 草稿期分界线 ===\n"
                )
                + "━━━━━━━━━━━━━━━━━━"
            )
        )
