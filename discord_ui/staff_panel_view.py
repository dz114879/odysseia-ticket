from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from core.constants import CUSTOM_ID_SEPARATOR, STAFF_CUSTOM_ID_PREFIX
from core.enums import TicketPriority, TicketStatus
from discord_ui.close_feedback import build_close_feedback_message
from discord_ui.staff_action_executor import assert_current_staff_panel, execute_staff_action, require_bot_resources
from discord_ui.staff_feedback import (
    build_claim_success_message,
    build_priority_success_message,
    build_rename_success_message,
    build_sleep_success_message,
    build_unclaim_success_message,
)

if TYPE_CHECKING:
    from services.claim_service import ClaimService
    from services.close_service import CloseService
    from services.priority_service import PriorityService
    from services.rename_service import RenameService
    from services.sleep_service import SleepService


CLAIM_ACTION = "claim"
UNCLAIM_ACTION = "unclaim"
SLEEP_ACTION = "sleep"
CLOSE_ACTION = "close"
RENAME_ACTION = "rename"
PRIORITY_ACTION = "priority"
ACTIVE_PANEL_ACTION_STATUSES = frozenset({TicketStatus.SUBMITTED})
CLOSE_RENAME_ACTIVE_STATUSES = frozenset({TicketStatus.SUBMITTED, TicketStatus.SLEEP})


PRIORITY_SELECT_OPTIONS = [
    discord.SelectOption(label="低 🟢", value=TicketPriority.LOW.value, description="设置为低优先级"),
    discord.SelectOption(label="中 🟡", value=TicketPriority.MEDIUM.value, description="设置为中优先级"),
    discord.SelectOption(label="高 🔴", value=TicketPriority.HIGH.value, description="设置为高优先级"),
    discord.SelectOption(label="紧急 ‼️", value=TicketPriority.EMERGENCY.value, description="设置为紧急优先级"),
]


def build_staff_panel_custom_id(action: str) -> str:
    return f"{STAFF_CUSTOM_ID_PREFIX}{CUSTOM_ID_SEPARATOR}{action}"


class StaffClaimButton(discord.ui.Button):
    def __init__(self, *, disabled: bool = False) -> None:
        super().__init__(
            label="认领",
            style=discord.ButtonStyle.primary,
            custom_id=build_staff_panel_custom_id(CLAIM_ACTION),
            disabled=disabled,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await execute_staff_action(
            interaction,
            context_label="交互",
            precheck=assert_current_staff_panel,
            action=lambda channel, is_bot_owner: _build_claim_service(interaction).claim_ticket(
                channel,
                actor=interaction.user,
                is_bot_owner=is_bot_owner,
            ),
            build_success_message=build_claim_success_message,
        )


class StaffUnclaimButton(discord.ui.Button):
    def __init__(self, *, disabled: bool = False) -> None:
        super().__init__(
            label="取消认领",
            style=discord.ButtonStyle.secondary,
            custom_id=build_staff_panel_custom_id(UNCLAIM_ACTION),
            disabled=disabled,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await execute_staff_action(
            interaction,
            context_label="交互",
            precheck=assert_current_staff_panel,
            action=lambda channel, is_bot_owner: _build_claim_service(interaction).unclaim_ticket(
                channel,
                actor=interaction.user,
                is_bot_owner=is_bot_owner,
            ),
            build_success_message=build_unclaim_success_message,
        )


class StaffSleepButton(discord.ui.Button):
    def __init__(self, *, disabled: bool = False) -> None:
        super().__init__(
            label="挂起",
            style=discord.ButtonStyle.secondary,
            custom_id=build_staff_panel_custom_id(SLEEP_ACTION),
            disabled=disabled,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await execute_staff_action(
            interaction,
            context_label="交互",
            precheck=assert_current_staff_panel,
            action=lambda channel, is_bot_owner: _build_sleep_service(interaction).sleep_ticket(
                channel,
                actor=interaction.user,
                is_bot_owner=is_bot_owner,
            ),
            build_success_message=build_sleep_success_message,
        )


class StaffCloseButton(discord.ui.Button):
    def __init__(self, *, disabled: bool = False) -> None:
        super().__init__(
            label="关闭",
            style=discord.ButtonStyle.danger,
            custom_id=build_staff_panel_custom_id(CLOSE_ACTION),
            disabled=disabled,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await execute_staff_action(
            interaction,
            context_label="交互",
            precheck=assert_current_staff_panel,
            action=lambda channel, is_bot_owner: _build_close_service(interaction).initiate_close(
                channel,
                actor=interaction.user,
                is_bot_owner=is_bot_owner,
            ),
            build_success_message=build_close_feedback_message,
        )


class StaffRenameModal(discord.ui.Modal, title="修改 Ticket 标题"):
    name_input = discord.ui.TextInput(
        label="新标题",
        placeholder="请输入新的 ticket 标题",
        min_length=1,
        max_length=80,
    )

    def __init__(self) -> None:
        super().__init__()

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await execute_staff_action(
            interaction,
            context_label="交互",
            action=lambda channel, is_bot_owner: _build_rename_service(interaction).rename_ticket(
                channel,
                actor=interaction.user,
                requested_name=self.name_input.value,
                is_bot_owner=is_bot_owner,
            ),
            build_success_message=build_rename_success_message,
        )


class StaffRenameButton(discord.ui.Button):
    def __init__(self, *, disabled: bool = False) -> None:
        super().__init__(
            label="重命名",
            style=discord.ButtonStyle.secondary,
            custom_id=build_staff_panel_custom_id(RENAME_ACTION),
            disabled=disabled,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await execute_staff_action(
            interaction,
            context_label="交互",
            precheck=assert_current_staff_panel,
            defer=False,
            resolve_owner=False,
            action=lambda _channel, _is_bot_owner: interaction.response.send_modal(StaffRenameModal()),
        )


class StaffPrioritySelect(discord.ui.Select):
    def __init__(self, *, disabled: bool = False, placeholder: str = "设置当前 ticket 优先级") -> None:
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=PRIORITY_SELECT_OPTIONS,
            custom_id=build_staff_panel_custom_id(PRIORITY_ACTION),
            disabled=disabled,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await execute_staff_action(
            interaction,
            context_label="交互",
            precheck=assert_current_staff_panel,
            action=lambda channel, is_bot_owner: _build_priority_service(interaction).set_priority(
                channel,
                actor=interaction.user,
                priority=TicketPriority(self.values[0]),
                is_bot_owner=is_bot_owner,
            ),
            build_success_message=build_priority_success_message,
        )


class StaffPanelView(discord.ui.View):
    def __init__(self, *, ticket_status: TicketStatus = TicketStatus.SUBMITTED) -> None:
        super().__init__(timeout=None)
        submitted_only_disabled = ticket_status not in ACTIVE_PANEL_ACTION_STATUSES
        close_rename_disabled = ticket_status not in CLOSE_RENAME_ACTIVE_STATUSES
        # Row 0: Claim / Unclaim
        self.add_item(StaffClaimButton(disabled=submitted_only_disabled))
        self.add_item(StaffUnclaimButton(disabled=submitted_only_disabled))
        # Row 1: Sleep / Close / Rename
        self.add_item(StaffSleepButton(disabled=submitted_only_disabled))
        self.add_item(StaffCloseButton(disabled=close_rename_disabled))
        self.add_item(StaffRenameButton(disabled=close_rename_disabled))
        # Row 2: Priority Select
        self.add_item(
            StaffPrioritySelect(
                disabled=submitted_only_disabled,
                placeholder=_build_priority_placeholder(ticket_status),
            )
        )


def _build_priority_placeholder(ticket_status: TicketStatus) -> str:
    if ticket_status in ACTIVE_PANEL_ACTION_STATUSES:
        return "设置当前 ticket 优先级"
    if ticket_status is TicketStatus.SLEEP:
        return "sleep 状态下暂不可修改优先级"
    if ticket_status is TicketStatus.TRANSFERRING:
        return "transferring 状态下暂不可修改优先级"
    return f"{ticket_status.value} 状态下暂不可修改优先级"


def _build_claim_service(interaction: discord.Interaction) -> ClaimService:
    from services.claim_service import ClaimService
    from services.staff_panel_service import StaffPanelService

    resources = require_bot_resources(interaction)
    staff_panel_service = StaffPanelService(
        resources.database,
        bot=interaction.client,
        debounce_manager=getattr(resources, "debounce_manager", None),
    )
    return ClaimService(
        resources.database,
        lock_manager=getattr(resources, "lock_manager", None),
        staff_panel_service=staff_panel_service,
    )


def _build_priority_service(interaction: discord.Interaction) -> PriorityService:
    from services.priority_service import PriorityService
    from services.staff_panel_service import StaffPanelService

    resources = require_bot_resources(interaction)
    staff_panel_service = StaffPanelService(
        resources.database,
        bot=interaction.client,
        debounce_manager=getattr(resources, "debounce_manager", None),
    )
    return PriorityService(
        resources.database,
        lock_manager=getattr(resources, "lock_manager", None),
        staff_panel_service=staff_panel_service,
    )


def _build_sleep_service(interaction: discord.Interaction) -> SleepService:
    resources = require_bot_resources(interaction)
    existing = getattr(resources, "sleep_service", None)
    if existing is not None:
        return existing
    from services.sleep_service import SleepService
    from services.staff_panel_service import StaffPanelService

    staff_panel_service = StaffPanelService(
        resources.database,
        bot=interaction.client,
        debounce_manager=getattr(resources, "debounce_manager", None),
    )
    return SleepService(
        resources.database,
        lock_manager=getattr(resources, "lock_manager", None),
        staff_panel_service=staff_panel_service,
    )


def _build_close_service(interaction: discord.Interaction) -> CloseService:
    resources = require_bot_resources(interaction)
    existing = getattr(resources, "close_service", None)
    if existing is not None:
        return existing
    from services.close_service import CloseService

    return CloseService(
        resources.database,
        bot=interaction.client,
        lock_manager=getattr(resources, "lock_manager", None),
    )


def _build_rename_service(interaction: discord.Interaction) -> RenameService:
    from services.rename_service import RenameService

    resources = require_bot_resources(interaction)
    return RenameService(
        resources.database,
        lock_manager=getattr(resources, "lock_manager", None),
    )
