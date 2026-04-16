from __future__ import annotations

from typing import Any

from core.models import TicketRecord


class SubmitWelcomeService:
    async def resolve_welcome_message(
        self,
        channel: Any,
        *,
        ticket: TicketRecord,
        provided_message: Any | None,
    ) -> Any | None:
        stored_message_id = ticket.welcome_message_id
        if provided_message is not None:
            provided_message_id = getattr(provided_message, "id", None)
            if stored_message_id is None or provided_message_id == stored_message_id:
                return provided_message

        if stored_message_id is not None:
            resolved_message = await self._fetch_message_by_id(channel, stored_message_id)
            if resolved_message is not None:
                return resolved_message

        return await self._resolve_legacy_welcome_message(channel, ticket=ticket)

    async def remove_welcome_view(self, message: Any | None) -> bool:
        edit = getattr(message, "edit", None)
        if message is None or edit is None:
            return False
        try:
            await edit(view=None)
        except Exception:
            return False
        return True

    @staticmethod
    async def _fetch_message_by_id(channel: Any, message_id: int) -> Any | None:
        fetch_message = getattr(channel, "fetch_message", None)
        if not callable(fetch_message):
            return None
        try:
            return await fetch_message(message_id)
        except Exception:
            return None

    async def _resolve_legacy_welcome_message(self, channel: Any, *, ticket: TicketRecord) -> Any | None:
        pins = getattr(channel, "pins", None)
        if not callable(pins):
            return None
        try:
            pinned_messages = await pins()
        except Exception:
            return None

        for pinned_message in pinned_messages:
            if self._is_legacy_welcome_message(pinned_message, ticket=ticket):
                return pinned_message
        return None

    @staticmethod
    def _is_legacy_welcome_message(message: Any, *, ticket: TicketRecord) -> bool:
        content = str(getattr(message, "content", "") or "")
        creator_mention = f"<@{ticket.creator_id}>"
        creator_nick_mention = f"<@!{ticket.creator_id}>"
        if creator_mention not in content and creator_nick_mention not in content and ticket.ticket_id not in content:
            return False
        return SubmitWelcomeService._get_message_embed_title(message).startswith("📋 已创建")

    @staticmethod
    def _get_message_embed_title(message: Any) -> str:
        embed = getattr(message, "embed", None)
        if embed is None:
            embeds = getattr(message, "embeds", None) or []
            embed = embeds[0] if embeds else None
        return str(getattr(embed, "title", "") or "")
