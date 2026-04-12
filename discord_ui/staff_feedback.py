from __future__ import annotations

from typing import TYPE_CHECKING

from core.enums import TicketPriority

if TYPE_CHECKING:
    from services.claim_service import ClaimMutationResult
    from services.moderation_service import MuteMutationResult, UnmuteMutationResult
    from services.priority_service import PriorityUpdateResult
    from services.rename_service import TicketRenameResult
    from services.sleep_service import SleepMutationResult
    from services.transfer_service import TransferCancellationResult, TransferMutationResult


def build_claim_success_message(result: ClaimMutationResult) -> str:
    if not result.changed:
        return f"当前 ticket 已由您认领，无需重复操作。\n- Ticket ID：`{result.ticket.ticket_id}`\n- 当前认领者：<@{result.ticket.claimed_by}>"

    mode_text = "strict" if result.strict_mode else "relaxed"
    return f"ticket 已认领。\n- Ticket ID：`{result.ticket.ticket_id}`\n- 当前认领者：<@{result.ticket.claimed_by}>\n- claim mode：`{mode_text}`"


def build_transfer_claim_success_message(result: ClaimMutationResult) -> str:
    if not result.changed:
        return (
            f"当前 ticket 已由目标 staff 认领，无需重复转交。\n- Ticket ID：`{result.ticket.ticket_id}`\n- 当前认领者：<@{result.ticket.claimed_by}>"
        )

    mode_text = "strict" if result.strict_mode else "relaxed"
    transfer_text = "管理员强制转交" if result.forced else "当前认领者主动转交"
    return (
        "ticket 认领已转交。\n"
        f"- Ticket ID：`{result.ticket.ticket_id}`\n"
        f"- 原认领者：<@{result.previous_claimer_id}>\n"
        f"- 新认领者：<@{result.ticket.claimed_by}>\n"
        f"- 转交方式：{transfer_text}\n"
        f"- claim mode：`{mode_text}`"
    )


def build_unclaim_success_message(result: ClaimMutationResult) -> str:
    if not result.changed:
        return f"当前 ticket 尚未被认领。\n- Ticket ID：`{result.ticket.ticket_id}`"

    forced_text = "（由管理员强制取消）" if result.forced else ""
    return f"ticket 已取消认领。\n- Ticket ID：`{result.ticket.ticket_id}`\n- 原认领者：<@{result.previous_claimer_id}> {forced_text}".rstrip()


def build_rename_success_message(result: TicketRenameResult) -> str:
    if not result.changed:
        return f"当前 ticket 标题未变化。\n- Ticket ID：`{result.ticket.ticket_id}`\n- 当前频道名：`{result.new_name}`"

    return f"ticket 标题已更新。\n- Ticket ID：`{result.ticket.ticket_id}`\n- 旧频道名：`{result.old_name}`\n- 新频道名：`{result.new_name}`"


def build_mute_success_message(result: MuteMutationResult) -> str:
    if not result.changed:
        return (
            "目标成员当前已处于相同的 ticket mute 状态。\n"
            f"- Ticket ID：`{result.ticket.ticket_id}`\n"
            f"- 目标成员：<@{result.target_id}>\n"
            f"- 到期时间：{result.expire_at or '手动解除'}"
        )

    message = (
        "ticket mute 已生效。\n"
        f"- Ticket ID：`{result.ticket.ticket_id}`\n"
        f"- 目标成员：<@{result.target_id}>\n"
        f"- 到期时间：{result.expire_at or '手动解除'}"
    )
    if result.reason is None:
        return message
    return f"{message}\n- 原因：{result.reason}"


def build_unmute_success_message(result: UnmuteMutationResult) -> str:
    if not result.changed:
        return f"目标成员当前未处于 ticket mute 状态。\n- Ticket ID：`{result.ticket.ticket_id}`\n- 目标成员：<@{result.target_id}>"
    return f"ticket mute 已解除。\n- Ticket ID：`{result.ticket.ticket_id}`\n- 目标成员：<@{result.target_id}>"


def build_priority_success_message(result: PriorityUpdateResult) -> str:
    new_priority_label = _get_priority_label(result.new_priority)
    if not result.changed:
        return f"当前 ticket 已是该优先级，频道名前缀也已是最新状态。\n- Ticket ID：`{result.ticket_id}`\n- 当前优先级：{new_priority_label}"

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


def build_transfer_success_message(result: TransferMutationResult) -> str:
    message = (
        "ticket 已进入 transferring。\n"
        f"- Ticket ID：`{result.ticket.ticket_id}`\n"
        f"- 原状态：{result.previous_status.value}\n"
        f"- 目标分类：{result.target_category.display_name} (`{result.target_category.category_key}`)\n"
        f"- 计划执行时间：{result.execute_at or '未设置'}\n"
        "- 如需撤销，请在执行前使用 `/ticket untransfer`"
    )
    if result.reason is None:
        return message
    return f"{message}\n- 转交理由：{result.reason}"


def build_untransfer_success_message(result: TransferCancellationResult) -> str:
    message = f"ticket 已撤销 transferring。\n- Ticket ID：`{result.ticket.ticket_id}`\n- 恢复状态：{result.restored_status.value}"
    if result.previous_target_category_key is not None:
        message = f"{message}\n- 原目标分类：`{result.previous_target_category_key}`"
    if result.reason is None:
        return message
    return f"{message}\n- 原转交理由：{result.reason}"


def _get_priority_label(priority: TicketPriority) -> str:
    labels = {
        TicketPriority.UNSET: "未设定 ⚪",
        TicketPriority.LOW: "低 🟢",
        TicketPriority.MEDIUM: "中 🟡",
        TicketPriority.HIGH: "高 🔴",
        TicketPriority.EMERGENCY: "紧急 ‼️",
        TicketPriority.SLEEP: "挂起 💤",
    }
    return labels.get(priority, priority.value)
