from __future__ import annotations

from typing import TYPE_CHECKING

from core.enums import TicketPriority

if TYPE_CHECKING:
    from services.claim_service import ClaimMutationResult
    from services.priority_service import PriorityUpdateResult
    from services.sleep_service import SleepMutationResult


def build_claim_success_message(result: ClaimMutationResult) -> str:
    if not result.changed:
        return (
            "当前 ticket 已由您认领，无需重复操作。\n"
            f"- Ticket ID：`{result.ticket.ticket_id}`\n"
            f"- 当前认领者：<@{result.ticket.claimed_by}>"
        )

    mode_text = "strict" if result.strict_mode else "relaxed"
    return (
        "ticket 已认领。\n"
        f"- Ticket ID：`{result.ticket.ticket_id}`\n"
        f"- 当前认领者：<@{result.ticket.claimed_by}>\n"
        f"- claim mode：`{mode_text}`"
    )


def build_unclaim_success_message(result: ClaimMutationResult) -> str:
    if not result.changed:
        return (
            "当前 ticket 尚未被认领。\n"
            f"- Ticket ID：`{result.ticket.ticket_id}`"
        )

    forced_text = "（由管理员强制取消）" if result.forced else ""
    return (
        "ticket 已取消认领。\n"
        f"- Ticket ID：`{result.ticket.ticket_id}`\n"
        f"- 原认领者：<@{result.previous_claimer_id}> {forced_text}".rstrip()
    )


def build_priority_success_message(result: PriorityUpdateResult) -> str:
    new_priority_label = _get_priority_label(result.new_priority)
    if not result.changed:
        return (
            "当前 ticket 已是该优先级，频道名前缀也已是最新状态。\n"
            f"- Ticket ID：`{result.ticket_id}`\n"
            f"- 当前优先级：{new_priority_label}"
        )

    old_priority_label = _get_priority_label(result.old_priority)
    return (
        "ticket 优先级已更新。\n"
        f"- Ticket ID：`{result.ticket_id}`\n"
        f"- 原优先级：{old_priority_label}\n"
        f"- 新优先级：{new_priority_label}\n"
        f"- 旧频道名：`{result.old_channel_name}`\n"
        f"- 新频道名：`{result.new_channel_name}`"
    )


def build_sleep_success_message(result: SleepMutationResult) -> str:
    return (
        "ticket 已进入 sleep。\n"
        f"- Ticket ID：`{result.ticket.ticket_id}`\n"
        f"- 睡前优先级：{_get_priority_label(result.previous_priority)}\n"
        f"- 旧频道名：`{result.old_channel_name}`\n"
        f"- 新频道名：`{result.new_channel_name}`"
    )


def _get_priority_label(priority: TicketPriority) -> str:
    labels = {
        TicketPriority.LOW: "低 🟢",
        TicketPriority.MEDIUM: "中 🟡",
        TicketPriority.HIGH: "高 🔴",
        TicketPriority.EMERGENCY: "紧急 ‼️",
        TicketPriority.SLEEP: "挂起 💤",
    }
    return labels.get(priority, priority.value)
