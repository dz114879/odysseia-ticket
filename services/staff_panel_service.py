from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from discord.ext import commands

from core.errors import StaleInteractionError, TicketNotFoundError, ValidationError
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.connection import DatabaseManager
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository
from discord_ui.panel_embeds import build_staff_control_panel_embed
from runtime.debounce import DebounceManager


@dataclass(frozen=True, slots=True)
class StaffPanelRefreshResult:
    ticket: TicketRecord
    category: TicketCategoryConfig
    message: Any | None
    refreshed: bool
    recovered: bool = False


class StaffPanelService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        bot: commands.Bot | None = None,
        ticket_repository: TicketRepository | None = None,
        guild_repository: GuildRepository | None = None,
        debounce_manager: DebounceManager | None = None,
        debounce_delay_seconds: float = 2.0,
    ) -> None:
        self.database = database
        self.bot = bot
        self.ticket_repository = ticket_repository or TicketRepository(database)
        self.guild_repository = guild_repository or GuildRepository(database)
        self.debounce_manager = debounce_manager
        self.debounce_delay_seconds = debounce_delay_seconds

    def request_refresh(self, ticket_id: str) -> None:
        if self.debounce_manager is None:
            return
        self.debounce_manager.schedule(
            f"staff-panel:{ticket_id}",
            delay_seconds=self.debounce_delay_seconds,
            callback=self.refresh_now,
            ticket_id=ticket_id,
        )

    def assert_current_panel_interaction(self, *, channel_id: int, message_id: int) -> TicketRecord:
        ticket = self.ticket_repository.get_by_channel_id(channel_id)
        if ticket is None:
            raise TicketNotFoundError("当前 ticket 不存在，无法验证 staff 控制面板。")
        if ticket.staff_panel_message_id is None or ticket.staff_panel_message_id != message_id:
            raise StaleInteractionError("此控制面板已过期，请使用最新面板。")
        return ticket

    async def refresh_now(self, *, ticket_id: str) -> StaffPanelRefreshResult:
        ticket = self.ticket_repository.get_by_ticket_id(ticket_id)
        if ticket is None:
            raise TicketNotFoundError("当前 ticket 不存在，无法刷新 staff 控制面板。")

        config = self.guild_repository.get_config(ticket.guild_id)
        if config is None:
            raise ValidationError("当前服务器 Ticket 配置不存在，无法刷新 staff 控制面板。")

        category = self.guild_repository.get_category(ticket.guild_id, ticket.category_key)
        if category is None:
            raise ValidationError("当前 ticket 所属分类配置不存在，无法刷新 staff 控制面板。")

        message = await self._try_resolve_panel_message(ticket)
        if message is None:
            recovered_ticket, recovered_message = await self._recover_panel_message(
                ticket,
                category=category,
                config=config,
            )
            return StaffPanelRefreshResult(
                ticket=recovered_ticket,
                category=category,
                message=recovered_message,
                refreshed=True,
                recovered=True,
            )

        await message.edit(
            embed=build_staff_control_panel_embed(ticket, category=category, config=config),
            view=self._build_panel_view(ticket),
        )
        return StaffPanelRefreshResult(
            ticket=ticket,
            category=category,
            message=message,
            refreshed=True,
            recovered=False,
        )

    async def _try_resolve_panel_message(self, ticket: TicketRecord) -> Any | None:
        channel_id = ticket.channel_id
        message_id = ticket.staff_panel_message_id
        if channel_id is None:
            raise ValidationError("当前 ticket 尚未绑定频道，无法刷新 staff 控制面板。")
        if message_id is None:
            return None

        channel = await self._resolve_channel(channel_id)
        fetch_message = getattr(channel, "fetch_message", None)
        if fetch_message is None:
            return None
        try:
            return await fetch_message(message_id)
        except Exception:
            return None

    async def _recover_panel_message(
        self,
        ticket: TicketRecord,
        *,
        category: TicketCategoryConfig,
        config: GuildConfigRecord,
    ) -> tuple[TicketRecord, Any]:
        channel_id = ticket.channel_id
        if channel_id is None:
            raise ValidationError("当前 ticket 尚未绑定频道，无法补发 staff 控制面板。")

        channel = await self._resolve_channel(channel_id)
        send = getattr(channel, "send", None)
        if send is None:
            raise ValidationError("当前 ticket 频道不支持补发 staff 控制面板。")

        message = await send(
            embed=build_staff_control_panel_embed(ticket, category=category, config=config),
            view=self._build_panel_view(ticket),
        )
        updated_ticket = (
            self.ticket_repository.update(
                ticket.ticket_id,
                staff_panel_message_id=getattr(message, "id", None),
            )
            or ticket
        )
        return updated_ticket, message

    async def _resolve_channel(self, channel_id: int) -> Any:
        if self.bot is None:
            raise ValidationError("StaffPanelService 未绑定 bot，无法定位 ticket 频道。")

        channel = self.bot.get_channel(channel_id)
        if channel is not None:
            return channel

        try:
            return await self.bot.fetch_channel(channel_id)
        except Exception as exc:
            raise ValidationError("无法定位当前 ticket 所在频道。") from exc

    @staticmethod
    def _build_panel_view(ticket: TicketRecord):
        from discord_ui.staff_panel_view import StaffPanelView

        return StaffPanelView(ticket_status=ticket.status)
