from __future__ import annotations

import discord

from core.enums import TicketStatus
from core.models import TicketRecord


def build_close_request_embed(
    ticket: TicketRecord,
    *,
    requester_id: int,
    reason: str | None,
) -> discord.Embed:
    embed = discord.Embed(
        title="📩 用户关闭请求",
        description=(f"<@{requester_id}> 请求关闭当前 ticket。\n请由合法 staff 点击下方按钮同意或拒绝。"),
        color=discord.Color.orange(),
    )
    embed.add_field(name="Ticket ID", value=f"`{ticket.ticket_id}`", inline=False)
    embed.add_field(name="当前状态", value=_format_status_label(ticket.status), inline=True)
    embed.add_field(name="发起人", value=f"<@{requester_id}>", inline=True)
    embed.add_field(name="请求理由", value=reason or "未提供", inline=False)
    embed.set_footer(text="请求有效期 5 分钟；若 staff 直接执行 /ticket close，旧请求会自动失效。")
    return embed


def build_close_request_status_embed(
    ticket: TicketRecord,
    *,
    requester_id: int,
    reason: str | None,
    status_text: str,
) -> discord.Embed:
    embed = discord.Embed(
        title="📩 用户关闭请求",
        description=status_text,
        color=discord.Color.dark_grey(),
    )
    embed.add_field(name="Ticket ID", value=f"`{ticket.ticket_id}`", inline=False)
    embed.add_field(name="原发起人", value=f"<@{requester_id}>", inline=True)
    embed.add_field(name="原请求理由", value=reason or "未提供", inline=False)
    return embed


def build_closing_notice_embed(
    ticket: TicketRecord,
    *,
    initiated_by_id: int,
    reason: str | None,
    close_execute_at: str,
    requested_by_id: int | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title="🔒 Ticket 正在关闭中",
        description=(
            "当前频道已进入关闭撤销窗口，期间会锁定所有非 Bot 用户发言。\n若需撤销，请点击下方「撤销关闭」按钮或使用 `/ticket close-cancel`。"
        ),
        color=discord.Color.red(),
    )
    embed.add_field(name="Ticket ID", value=f"`{ticket.ticket_id}`", inline=False)
    embed.add_field(name="发起 staff", value=f"<@{initiated_by_id}>", inline=True)
    embed.add_field(name="原状态", value=_format_status_label(ticket.status_before or ticket.status), inline=True)
    if requested_by_id is not None:
        embed.add_field(name="来源请求", value=f"来自 <@{requested_by_id}> 的关闭请求", inline=False)
    embed.add_field(name="关闭理由", value=reason or "未提供", inline=False)
    embed.add_field(name="归档开始时间", value=close_execute_at, inline=False)
    embed.set_footer(text="窗口结束后将自动进入 archiving，并在归档成功后删除频道。")
    return embed


def build_archive_record_embed(ticket: TicketRecord) -> discord.Embed:
    embed = discord.Embed(
        title="🗂️ Ticket 归档记录",
        description="当前 ticket 已完成归档并进入删除/完成流程。",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Ticket ID", value=f"`{ticket.ticket_id}`", inline=False)
    embed.add_field(name="创建者", value=f"<@{ticket.creator_id}>", inline=True)
    embed.add_field(name="分类", value=ticket.category_key, inline=True)
    embed.add_field(name="关闭时间", value=ticket.closed_at or "未知", inline=True)
    embed.add_field(name="归档时间", value=ticket.archived_at or "未知", inline=True)
    embed.add_field(name="消息数", value=str(ticket.message_count or 0), inline=True)
    embed.add_field(name="关闭理由", value=ticket.close_reason or "未提供", inline=False)
    if ticket.claimed_by is not None:
        embed.add_field(name="最终认领者", value=f"<@{ticket.claimed_by}>", inline=True)
    embed.add_field(name="最终状态", value=_format_status_label(ticket.status), inline=True)
    return embed


def build_closing_revoked_embed(
    ticket: TicketRecord,
    *,
    revoked_by_id: int,
    restored_status: TicketStatus,
) -> discord.Embed:
    embed = discord.Embed(
        title="↩️ Ticket 关闭已撤销",
        description="关闭流程已被撤销，频道权限已恢复。",
        color=discord.Color.dark_grey(),
    )
    embed.add_field(name="Ticket ID", value=f"`{ticket.ticket_id}`", inline=False)
    embed.add_field(name="撤销者", value=f"<@{revoked_by_id}>", inline=True)
    embed.add_field(name="恢复状态", value=_format_status_label(restored_status), inline=True)
    return embed


def _format_status_label(status: TicketStatus) -> str:
    labels = {
        TicketStatus.SUBMITTED: "submitted 处理中",
        TicketStatus.SLEEP: "sleep 挂起中",
        TicketStatus.CLOSING: "closing 关闭中",
        TicketStatus.ARCHIVING: "archiving 归档中",
        TicketStatus.ARCHIVE_SENT: "archive_sent 已发送",
        TicketStatus.ARCHIVE_FAILED: "archive_failed 发送失败",
        TicketStatus.CHANNEL_DELETED: "channel_deleted 频道已删除",
        TicketStatus.DONE: "done 已完成",
    }
    return labels.get(status, status.value)
