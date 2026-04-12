from __future__ import annotations

from typing import TYPE_CHECKING, Any

import discord

from core.constants import CUSTOM_ID_SEPARATOR, STAFF_CUSTOM_ID_PREFIX
from core.enums import TicketPriority, TicketStatus
from core.errors import (
    InvalidTicketStateError,
    PermissionDeniedError,
    StaleInteractionError,
    TicketNotFoundError,
    ValidationError,
)
from discord_ui.close_feedback import build_close_feedback_message
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
        try:
            channel = _require_channel(interaction)
            _assert_current_staff_panel(interaction, channel=channel)
            await _defer_ephemeral(interaction)
            claim_service = _build_claim_service(interaction)
            result = await claim_service.claim_ticket(
                channel,
                actor=interaction.user,
                is_bot_owner=await interaction.client.is_owner(interaction.user),
            )
        except (
            TicketNotFoundError,
            InvalidTicketStateError,
            StaleInteractionError,
            PermissionDeniedError,
            ValidationError,
            discord.HTTPException,
        ) as exc:
            await _send_ephemeral(interaction, str(exc))
            return

        await _send_ephemeral(interaction, build_claim_success_message(result))


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
        try:
            channel = _require_channel(interaction)
            _assert_current_staff_panel(interaction, channel=channel)
            await _defer_ephemeral(interaction)
            claim_service = _build_claim_service(interaction)
            result = await claim_service.unclaim_ticket(
                channel,
                actor=interaction.user,
                is_bot_owner=await interaction.client.is_owner(interaction.user),
            )
        except (
            TicketNotFoundError,
            InvalidTicketStateError,
            StaleInteractionError,
            PermissionDeniedError,
            ValidationError,
            discord.HTTPException,
        ) as exc:
            await _send_ephemeral(interaction, str(exc))
            return

        await _send_ephemeral(interaction, build_unclaim_success_message(result))


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
        try:
            channel = _require_channel(interaction)
            _assert_current_staff_panel(interaction, channel=channel)
            await _defer_ephemeral(interaction)
            sleep_service = _build_sleep_service(interaction)
            result = await sleep_service.sleep_ticket(
                channel,
                actor=interaction.user,
                is_bot_owner=await interaction.client.is_owner(interaction.user),
            )
        except (
            TicketNotFoundError,
            InvalidTicketStateError,
            StaleInteractionError,
            PermissionDeniedError,
            ValidationError,
            discord.HTTPException,
        ) as exc:
            await _send_ephemeral(interaction, str(exc))
            return

        await _send_ephemeral(interaction, build_sleep_success_message(result))


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
        try:
            channel = _require_channel(interaction)
            _assert_current_staff_panel(interaction, channel=channel)
            await _defer_ephemeral(interaction)
            close_service = _build_close_service(interaction)
            result = await close_service.initiate_close(
                channel,
                actor=interaction.user,
                is_bot_owner=await interaction.client.is_owner(interaction.user),
            )
        except (
            TicketNotFoundError,
            InvalidTicketStateError,
            StaleInteractionError,
            PermissionDeniedError,
            ValidationError,
            discord.HTTPException,
        ) as exc:
            await _send_ephemeral(interaction, str(exc))
            return

        await _send_ephemeral(interaction, build_close_feedback_message(result))


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
        try:
            channel = _require_channel(interaction)
            await _defer_ephemeral(interaction)
            rename_service = _build_rename_service(interaction)
            result = await rename_service.rename_ticket(
                channel,
                actor=interaction.user,
                requested_name=self.name_input.value,
                is_bot_owner=await interaction.client.is_owner(interaction.user),
            )
        except (
            TicketNotFoundError,
            InvalidTicketStateError,
            PermissionDeniedError,
            ValidationError,
            discord.HTTPException,
        ) as exc:
            await _send_ephemeral(interaction, str(exc))
            return

        await _send_ephemeral(interaction, build_rename_success_message(result))


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
        try:
            _require_channel(interaction)
            _assert_current_staff_panel(interaction)
        except (StaleInteractionError, ValidationError) as exc:
            await _send_ephemeral(interaction, str(exc))
            return

        await interaction.response.send_modal(StaffRenameModal())


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
        try:
            channel = _require_channel(interaction)
            _assert_current_staff_panel(interaction, channel=channel)
            await _defer_ephemeral(interaction)
            priority_service = _build_priority_service(interaction)
            result = await priority_service.set_priority(
                channel,
                actor=interaction.user,
                priority=TicketPriority(self.values[0]),
                is_bot_owner=await interaction.client.is_owner(interaction.user),
            )
        except (
            TicketNotFoundError,
            InvalidTicketStateError,
            StaleInteractionError,
            PermissionDeniedError,
            ValidationError,
            discord.HTTPException,
        ) as exc:
            await _send_ephemeral(interaction, str(exc))
            return

        await _send_ephemeral(interaction, build_priority_success_message(result))


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

    resources = _require_resources(interaction)
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

    resources = _require_resources(interaction)
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
    resources = _require_resources(interaction)
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
    resources = _require_resources(interaction)
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

    resources = _require_resources(interaction)
    return RenameService(
        resources.database,
        lock_manager=getattr(resources, "lock_manager", None),
    )


def _require_resources(interaction: discord.Interaction) -> Any:
    client = getattr(interaction, "client", None)
    resources = getattr(client, "resources", None)
    if resources is None:
        raise RuntimeError("Bot resources 尚未初始化，无法处理 staff panel 交互。")
    return resources


def _require_channel(interaction: discord.Interaction) -> Any:
    if interaction.guild is None:
        raise ValidationError("该交互只能在服务器中使用。")
    channel = interaction.channel
    if channel is None or getattr(channel, "guild", None) is None:
        raise ValidationError("当前频道不支持 staff ticket 操作。")
    return channel


def _assert_current_staff_panel(interaction: discord.Interaction, *, channel: Any | None = None) -> None:
    resolved_channel = channel or _require_channel(interaction)
    message = getattr(interaction, "message", None)
    message_id = getattr(message, "id", None)
    if message_id is None:
        raise ValidationError("无法识别当前 staff 控制面板消息，请稍后重试。")

    service = _build_staff_panel_service(interaction)
    service.assert_current_panel_interaction(
        channel_id=getattr(resolved_channel, "id", 0),
        message_id=message_id,
    )


def _build_staff_panel_service(interaction: discord.Interaction):
    from services.staff_panel_service import StaffPanelService

    resources = _require_resources(interaction)
    return StaffPanelService(resources.database, bot=interaction.client)


async def _send_ephemeral(interaction: discord.Interaction, content: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=True)
        return
    await interaction.response.send_message(content, ephemeral=True)


async def _defer_ephemeral(interaction: discord.Interaction) -> None:
    if interaction.response.is_done():
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
