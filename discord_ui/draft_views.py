from __future__ import annotations

from typing import Any

import discord

from core.constants import CUSTOM_ID_SEPARATOR, TICKET_CUSTOM_ID_PREFIX
from core.errors import (
    InvalidTicketStateError,
    PermissionDeniedError,
    TicketNotFoundError,
    ValidationError,
)
from services.draft_service import DraftAbandonResult, DraftService
from services.submission_guard_service import SubmissionGuardService
from services.submit_service import SubmitDraftResult, SubmitService


DRAFT_SUBMIT_ACTION = "draft-submit"


def build_draft_submit_custom_id() -> str:
    return f"{TICKET_CUSTOM_ID_PREFIX}{CUSTOM_ID_SEPARATOR}{DRAFT_SUBMIT_ACTION}"


def build_submit_feedback_message(result: SubmitDraftResult) -> str:
    if result.outcome == "already_submitted":
        return f"该 ticket 已经提交，无需重复操作。\n- Ticket ID：`{result.ticket.ticket_id}`\n- 当前频道名：`{result.new_channel_name}`"

    if result.outcome in {"queued", "already_queued"}:
        position = result.queue_position or "未知"
        lines = [
            "该 ticket 已进入排队，staff 会在空位释放后自动接手。" if result.outcome == "queued" else "该 ticket 当前仍在排队中，无需重复提交。",
            f"- Ticket ID：`{result.ticket.ticket_id}`",
            f"- 当前频道名：`{result.new_channel_name}`",
            f"- 当前排队位置：`#{position}`",
        ]
        if result.active_count is not None and result.max_open_tickets is not None:
            lines.append(f"- 当前 active 容量：`{result.active_count}/{result.max_open_tickets}`")
        lines.append("- 系统会在有空位时自动将此 ticket 正式提交给 staff。")
        return "\n".join(lines)

    lines = [
        "draft ticket 已提交。",
        f"- Ticket ID：`{result.ticket.ticket_id}`",
        f"- 当前频道名：`{result.new_channel_name}`",
        "- staff 现在已经可以查看当前频道。" if result.outcome == "submitted" else "- 提交流程已完成。",
    ]
    if result.channel_name_changed:
        lines.insert(2, f"- 原频道名：`{result.old_channel_name}`")
    return "\n".join(lines)


class DraftSubmitTitleModal(discord.ui.Modal, title="提交前补充标题"):
    title_input = discord.ui.TextInput(
        label="Ticket 标题",
        placeholder="请简要描述本次问题或诉求",
        min_length=1,
        max_length=80,
    )

    def __init__(self, *, welcome_message: Any | None = None) -> None:
        super().__init__()
        self.welcome_message = welcome_message

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            channel = _require_channel(interaction)
            submit_service = _build_submit_service(interaction)
            await _defer_ephemeral(interaction)
            result = await submit_service.submit_draft_ticket(
                channel,
                actor_id=interaction.user.id,
                requested_title=self.title_input.value,
                welcome_message=self.welcome_message,
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

        await _send_ephemeral(interaction, build_submit_feedback_message(result))


class DraftSubmitButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="提交给 Staff",
            style=discord.ButtonStyle.primary,
            custom_id=build_draft_submit_custom_id(),
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            channel = _require_channel(interaction)
            guard_service = _build_guard_service(interaction)
            context = guard_service.inspect_submission(
                channel_id=channel.id,
                actor_id=interaction.user.id,
                channel_name=getattr(channel, "name", None),
            )
            if context.requires_title and not context.already_submitted:
                await interaction.response.send_modal(DraftSubmitTitleModal(welcome_message=interaction.message))
                return

            submit_service = _build_submit_service(interaction)
            await _defer_ephemeral(interaction)
            result = await submit_service.submit_draft_ticket(
                channel,
                actor_id=interaction.user.id,
                welcome_message=interaction.message,
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

        await _send_ephemeral(interaction, build_submit_feedback_message(result))


class DraftWelcomeView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(DraftSubmitButton())


def build_abandon_feedback_message(result: DraftAbandonResult) -> str:
    deleted_text = "频道已删除。" if result.channel_deleted else "频道删除失败，请手动处理。"
    return f"draft ticket 已废弃。\n- Ticket ID：`{result.ticket.ticket_id}`\n- 结果：{deleted_text}"


class DraftAbandonConfirmView(discord.ui.View):
    def __init__(self, *, timeout: float = 60.0) -> None:
        super().__init__(timeout=timeout)

    @discord.ui.button(label="确认废弃", style=discord.ButtonStyle.danger)
    async def confirm_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        del button
        try:
            channel = _require_channel(interaction)
            draft_service = _build_draft_service(interaction)
            await _defer_ephemeral(interaction)
            result = await draft_service.abandon_draft_ticket(
                channel,
                actor_id=interaction.user.id,
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

        try:
            await _send_ephemeral(interaction, build_abandon_feedback_message(result))
        except discord.HTTPException:
            pass


def _build_draft_service(interaction: discord.Interaction) -> DraftService:
    resources = _require_resources(interaction)
    return DraftService(
        resources.database,
        lock_manager=getattr(resources, "lock_manager", None),
    )


def _build_guard_service(interaction: discord.Interaction) -> SubmissionGuardService:
    resources = _require_resources(interaction)
    return SubmissionGuardService(resources.database)


def _build_submit_service(interaction: discord.Interaction) -> SubmitService:
    resources = _require_resources(interaction)
    return SubmitService(
        resources.database,
        lock_manager=getattr(resources, "lock_manager", None),
        snapshot_service=getattr(resources, "snapshot_service", None),
        capacity_service=getattr(resources, "capacity_service", None),
        queue_service=getattr(resources, "queue_service", None),
    )


def _require_resources(interaction: discord.Interaction) -> Any:
    client = getattr(interaction, "client", None)
    resources = getattr(client, "resources", None)
    if resources is None:
        raise RuntimeError("Bot resources 尚未初始化，无法处理 draft submit 交互。")
    return resources


def _require_channel(interaction: discord.Interaction) -> Any:
    if interaction.guild is None:
        raise ValidationError("该交互只能在服务器中使用。")
    channel = interaction.channel
    if channel is None:
        raise ValidationError("无法识别当前 ticket 频道。")
    return channel


async def _defer_ephemeral(interaction: discord.Interaction) -> None:
    if interaction.response.is_done():
        return
    await interaction.response.defer(ephemeral=True, thinking=True)


async def _send_ephemeral(interaction: discord.Interaction, content: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=True)
        return
    await interaction.response.send_message(content, ephemeral=True)
