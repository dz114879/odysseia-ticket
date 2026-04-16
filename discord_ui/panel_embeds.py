from __future__ import annotations

import discord

from config.defaults import (
    DEFAULT_PANEL_BODY,
    DEFAULT_PANEL_CAPACITY_TEXT,
    DEFAULT_PANEL_FOOTER_TEXT,
    DEFAULT_PANEL_TITLE,
    build_public_panel_body,
)
from core.constants import TRANSFER_EXECUTION_DELAY_SECONDS
from core.enums import ClaimMode, TicketPriority, TicketStatus
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord


def build_public_panel_embed(categories: list[TicketCategoryConfig], *, config: GuildConfigRecord | None = None) -> discord.Embed:
    title = (config.panel_title if config and config.panel_title else DEFAULT_PANEL_TITLE)
    description = (
        build_public_panel_body(
            description=config.panel_description if config else None,
            bullet_points=config.panel_bullet_points if config else None,
        )
        if config
        else DEFAULT_PANEL_BODY
    )
    footer = (config.panel_footer_text if config and config.panel_footer_text else DEFAULT_PANEL_FOOTER_TEXT)
    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="可选分类",
        value=_format_category_lines(categories),
        inline=False,
    )
    embed.add_field(name="容量状态", value=DEFAULT_PANEL_CAPACITY_TEXT, inline=False)
    embed.set_footer(text=footer)
    return embed


def build_panel_request_preview_embed(category: TicketCategoryConfig) -> discord.Embed:
    embed = discord.Embed(
        title=f"已选择分类：{category.display_name}",
        description=("当前请求已通过入口预校验。\n请点击下方按钮创建一个仅您与机器人可见的私密 draft ticket 频道。"),
        color=discord.Color.green(),
    )
    if category.description:
        embed.add_field(name="分类说明", value=category.description, inline=False)
    embed.add_field(name="下一步", value="确认创建后，请在 draft 频道发送第一条消息。", inline=False)
    return embed


def build_staff_control_panel_embed(
    ticket: TicketRecord,
    *,
    category: TicketCategoryConfig,
    config: GuildConfigRecord | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title="🛠️ Staff 控制面板",
        description=_build_staff_panel_description(ticket, config=config),
        color=discord.Color.gold(),
    )
    embed.add_field(name="Ticket ID", value=f"`{ticket.ticket_id}`", inline=False)
    embed.add_field(name="状态", value=_format_status_label(ticket.status), inline=True)
    embed.add_field(name="优先级", value=_format_ticket_priority_label(ticket), inline=True)
    embed.add_field(name="认领模式", value=_format_claim_mode_label(config.claim_mode if config else None), inline=True)
    embed.add_field(name="分类", value=category.display_name, inline=True)
    embed.add_field(name="创建者", value=f"<@{ticket.creator_id}>", inline=True)
    embed.add_field(name="当前认领者", value=_format_claimer(ticket.claimed_by), inline=True)
    embed.add_field(name="最近用户消息", value=ticket.last_user_message_at or "暂无", inline=False)
    embed.add_field(name="创建时间", value=ticket.created_at or "未知", inline=False)
    transfer_summary = _build_transfer_summary(ticket)
    if transfer_summary is not None:
        embed.add_field(name="转交信息", value=transfer_summary, inline=False)
    embed.set_footer(
        text="claim / unclaim / transfer-claim / rename / mute / unmute / priority / sleep / transfer / untransfer / help 已接入；transfer 当前为延迟执行并可在窗口内撤销。"
    )
    return embed


def _build_staff_panel_description(
    ticket: TicketRecord,
    *,
    config: GuildConfigRecord | None,
) -> str:
    if ticket.status is TicketStatus.SLEEP:
        description = "当前 ticket 已进入 sleep 挂起状态，不占 active 容量；现有参与者权限保持不变。"
    elif ticket.status is TicketStatus.SUBMITTED:
        description = "当前 ticket 已提交，可通过 staff 命令继续处理。"
    elif ticket.status is TicketStatus.TRANSFERRING:
        target_text = f"`{ticket.transfer_target_category}`" if ticket.transfer_target_category else "目标分类"
        execute_at_text = ticket.transfer_execute_at or "未设置"
        restored_status_text = _format_status_label(ticket.status_before) if ticket.status_before is not None else "未知"
        description = (
            f"当前 ticket 正在发起跨分类转交，目标为 {target_text}。"
            f"默认会在{_format_transfer_delay_text()}后自动执行；执行前可使用 `/ticket untransfer` 撤销。\n"
            f"计划执行时间：{execute_at_text}\n"
            f"执行完成后将恢复为：{restored_status_text}"
        )
        if ticket.transfer_reason:
            description = f"{description}\n转交理由：{ticket.transfer_reason}"
    else:
        description = f"当前 ticket 状态为 {_format_status_label(ticket.status)}。"

    if config is not None:
        if ticket.status is TicketStatus.SUBMITTED and config.claim_mode is ClaimMode.STRICT and ticket.claimed_by is None:
            description = f"{description}\n当前为 strict claim mode，未认领前 staff 默认仅可见不可发言。"
        else:
            description = f"{description}\n当前 claim mode：{_format_claim_mode_label(config.claim_mode)}。"

    action_hint = _build_staff_panel_action_hint(ticket)
    if action_hint is None:
        return description
    return f"{description}\n{action_hint}"


def _build_staff_panel_action_hint(ticket: TicketRecord) -> str | None:
    if ticket.status is TicketStatus.SUBMITTED:
        return "面板中的认领 / 取消认领 / 优先级控件当前可用；其他动作请继续使用 slash 命令。"
    if ticket.status is TicketStatus.SLEEP:
        return (
            "当前面板已禁用认领 / 取消认领 / 优先级控件；如需继续处理，可改用 "
            "`/ticket rename`、`/ticket mute`、`/ticket unmute`、`/ticket transfer` 或 `/ticket help`。"
        )
    if ticket.status is TicketStatus.TRANSFERRING:
        return "当前面板已禁用认领 / 取消认领 / 优先级控件；如需撤销本次转交，请使用 `/ticket untransfer`，其他说明可通过 `/ticket help` 查看。"
    return None


def _build_transfer_summary(ticket: TicketRecord) -> str | None:
    if ticket.status is not TicketStatus.TRANSFERRING:
        return None

    lines = [
        f"- 目标分类：`{ticket.transfer_target_category or '未设置'}`",
        f"- 计划执行时间：{ticket.transfer_execute_at or '未设置'}",
        f"- 执行后恢复状态：{_format_status_label(ticket.status_before) if ticket.status_before is not None else '未知'}",
    ]
    if ticket.transfer_reason:
        lines.append(f"- 转交理由：{ticket.transfer_reason}")
    return "\n".join(lines)


def _format_transfer_delay_text(delay_seconds: int = TRANSFER_EXECUTION_DELAY_SECONDS) -> str:
    if delay_seconds % 60 == 0:
        minutes = delay_seconds // 60
        return "1 分钟" if minutes == 1 else f"{minutes} 分钟"
    return f"{delay_seconds} 秒"


def _format_category_lines(categories: list[TicketCategoryConfig]) -> str:
    if not categories:
        return "当前没有可用分类。"

    lines = []
    for category in categories[:10]:
        prefix = f"{category.emoji} " if category.emoji else ""
        description = category.description or "暂无描述"
        lines.append(f"{prefix}**{category.display_name}**：{description}")
    return "\n".join(lines)


def _format_ticket_priority_label(ticket: TicketRecord) -> str:
    if ticket.priority is TicketPriority.SLEEP:
        sleep_label = _format_priority_label(TicketPriority.SLEEP)
        if ticket.priority_before_sleep is None:
            return sleep_label
        return f"{sleep_label}（睡前：{_format_priority_label(ticket.priority_before_sleep)}）"
    return _format_priority_label(ticket.priority)


def _format_priority_label(priority: TicketPriority) -> str:
    labels = {
        TicketPriority.UNSET: "未设定 ⚪",
        TicketPriority.LOW: "低 🟢",
        TicketPriority.MEDIUM: "中 🟡",
        TicketPriority.HIGH: "高 🔴",
        TicketPriority.EMERGENCY: "紧急 ‼️",
        TicketPriority.SLEEP: "挂起 💤",
    }
    return labels.get(priority, priority.value)


def _format_claim_mode_label(claim_mode: ClaimMode | None) -> str:
    labels = {
        ClaimMode.RELAXED: "relaxed 协作模式",
        ClaimMode.STRICT: "strict 严格认领",
        None: "未知",
    }
    return labels.get(claim_mode, getattr(claim_mode, "value", "未知"))


def _format_status_label(status: TicketStatus) -> str:
    labels = {
        TicketStatus.DRAFT: "draft 草稿",
        TicketStatus.QUEUED: "queued 排队中",
        TicketStatus.SUBMITTED: "submitted 处理中",
        TicketStatus.SLEEP: "sleep 挂起中",
        TicketStatus.TRANSFERRING: "transferring 转交中",
        TicketStatus.CLOSING: "closing 关闭中",
        TicketStatus.ARCHIVING: "archiving 归档中",
        TicketStatus.ARCHIVE_SENT: "archive_sent 已发送",
        TicketStatus.ARCHIVE_FAILED: "archive_failed 发送失败",
        TicketStatus.CHANNEL_DELETED: "channel_deleted 频道已删除",
        TicketStatus.DONE: "done 已完成",
        TicketStatus.ABANDONED: "abandoned 已放弃",
    }
    return labels.get(status, status.value)


def _format_claimer(claimer_id: int | None) -> str:
    if claimer_id is None:
        return "未认领"
    return f"<@{claimer_id}>"
