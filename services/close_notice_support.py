from __future__ import annotations

import logging
from typing import Any

from core.constants import CLOSE_REVOKE_WINDOW_SECONDS
from core.enums import TicketStatus
from core.models import TicketRecord
from discord_ui.close_embeds import build_closing_notice_embed, build_closing_revoked_embed
from discord_ui.close_views import ClosingNoticeView


class CloseNoticeSupport:
    def __init__(self, *, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(__name__)
        self._notice_messages: dict[str, Any] = {}

    async def send_closing_notice(
        self,
        channel: Any,
        *,
        close_service: Any,
        ticket: TicketRecord,
        initiated_by_id: int,
        requested_by_id: int | None,
        close_revoke_window_seconds: int = CLOSE_REVOKE_WINDOW_SECONDS,
    ) -> Any | None:
        send = getattr(channel, "send", None)
        if send is None:
            return None

        view = ClosingNoticeView(
            close_service=close_service,
            notice_support=self,
            ticket_id=ticket.ticket_id,
            timeout=float(close_revoke_window_seconds),
        )
        message = await send(
            embed=build_closing_notice_embed(
                ticket,
                initiated_by_id=initiated_by_id,
                reason=ticket.close_reason,
                close_execute_at=ticket.close_execute_at or "未知",
                requested_by_id=requested_by_id,
                close_revoke_window_seconds=close_revoke_window_seconds,
            ),
            view=view,
        )
        view.bind_message(message)
        self._notice_messages[ticket.ticket_id] = message
        return message

    async def edit_notice_as_revoked(
        self,
        ticket_id: str,
        *,
        ticket: TicketRecord,
        revoked_by_id: int,
        restored_status: TicketStatus,
    ) -> None:
        notice_msg = self._notice_messages.pop(ticket_id, None)
        if notice_msg is None:
            return
        try:
            await notice_msg.edit(
                embed=build_closing_revoked_embed(
                    ticket,
                    revoked_by_id=revoked_by_id,
                    restored_status=restored_status,
                ),
                view=None,
            )
        except Exception:
            self.logger.debug("Failed to edit closing notice for %s", ticket_id, exc_info=True)

    async def expire_notice(self, ticket_id: str, *, message: Any | None) -> None:
        self._notice_messages.pop(ticket_id, None)
        if message is None:
            return
        try:
            await message.edit(view=None)
        except Exception:
            self.logger.debug("Failed to expire closing notice for %s", ticket_id, exc_info=True)


async def send_channel_log(channel: Any, *, content: str) -> Any | None:
    send = getattr(channel, "send", None)
    if send is None:
        return None
    return await send(content=content)
