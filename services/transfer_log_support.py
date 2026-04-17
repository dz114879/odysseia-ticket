from __future__ import annotations

from typing import Any

from core.enums import TicketStatus
from core.models import GuildConfigRecord, TicketCategoryConfig


def get_transfer_status_label(status: TicketStatus) -> str:
    labels = {
        TicketStatus.SUBMITTED: "submitted 处理中",
        TicketStatus.SLEEP: "sleep 挂起中",
        TicketStatus.TRANSFERRING: "transferring 转交中",
    }
    return labels.get(status, status.value)


def build_transfer_log_content(
    *,
    actor_id: int,
    ticket_id: str,
    previous_status: TicketStatus,
    target_category: TicketCategoryConfig,
    reason: str | None,
    current_claimer_id: int | None,
    execute_at: str | None,
) -> str:
    lines = [
        f"🔁 <@{actor_id}> 已发起 ticket `{ticket_id}` 的跨分类转交。",
        f"- 原状态：{get_transfer_status_label(previous_status)}",
        f"- 目标分类：{target_category.display_name} (`{target_category.category_key}`)",
        f"- 当前认领者：<@{current_claimer_id}>" if current_claimer_id is not None else "- 当前认领者：未认领",
    ]
    if reason is not None:
        lines.append(f"- 转交理由：{reason}")
    if execute_at is not None:
        lines.append(f"- 计划执行时间：{execute_at}")
    return "\n".join(lines)


def build_cancel_transfer_log_content(
    *,
    actor_id: int,
    ticket_id: str,
    restored_status: TicketStatus,
    previous_target_category_key: str | None,
    reason: str | None,
) -> str:
    lines = [
        f"↩️ <@{actor_id}> 已撤销 ticket `{ticket_id}` 的跨分类转交。",
        f"- 恢复状态：{get_transfer_status_label(restored_status)}",
        (f"- 原目标分类：`{previous_target_category_key}`" if previous_target_category_key is not None else "- 原目标分类：未知"),
    ]
    if reason is not None:
        lines.append(f"- 原转交理由：{reason}")
    return "\n".join(lines)


def build_execute_transfer_log_content(
    *,
    ticket_id: str,
    previous_category_key: str,
    previous_category: TicketCategoryConfig | None,
    target_category: TicketCategoryConfig,
    restored_status: TicketStatus,
    previous_claimer_id: int | None,
    reason: str | None,
    executed_at: str,
) -> str:
    previous_category_name = previous_category.display_name if previous_category is not None else previous_category_key
    lines = [
        f"✅ ticket `{ticket_id}` 的跨分类转交已执行。",
        f"- 原分类：{previous_category_name} (`{previous_category_key}`)",
        f"- 新分类：{target_category.display_name} (`{target_category.category_key}`)",
        f"- 恢复状态：{get_transfer_status_label(restored_status)}",
        f"- 原认领者：<@{previous_claimer_id}>" if previous_claimer_id is not None else "- 原认领者：未认领",
        f"- 执行时间：{executed_at}",
    ]
    if reason is not None:
        lines.append(f"- 原转交理由：{reason}")
    return "\n".join(lines)


async def send_channel_log(channel: Any, *, content: str) -> Any | None:
    send = getattr(channel, "send", None)
    if send is None:
        return None
    return await send(content=content)


async def send_transfer_completion_log(
    logging_service: Any | None,
    *,
    ticket: Any,
    config: GuildConfigRecord,
    previous_category_key: str,
    previous_category: TicketCategoryConfig | None,
    target_category: TicketCategoryConfig,
    restored_status: TicketStatus,
    previous_claimer_id: int | None,
    executed_at: str,
    reason: str | None,
) -> bool:
    if logging_service is None:
        return False

    previous_category_name = previous_category.display_name if previous_category is not None else previous_category_key
    description = (
        f"ticket `{ticket.ticket_id}` 已完成跨分类转交："
        f" `{previous_category_key}` -> `{target_category.category_key}`，"
        f"并恢复为 {restored_status.value}。"
    )
    extra: dict[str, object] = {
        "previous_category": previous_category_name,
        "target_category": target_category.display_name,
        "restored_status": restored_status.value,
        "executed_at": executed_at,
    }
    if previous_claimer_id is not None:
        extra["previous_claimer_id"] = previous_claimer_id
    if reason is not None:
        extra["transfer_reason"] = reason

    return await logging_service.send_ticket_log(
        ticket_id=ticket.ticket_id,
        guild_id=ticket.guild_id,
        level="success",
        title="工单转移已执行",
        description=description,
        channel_id=config.log_channel_id,
        extra=extra,
    )
