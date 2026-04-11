from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.close_request_service import CloseRequestCreationResult
    from services.close_service import CloseMutationResult, CloseRevokeResult


def build_close_feedback_message(result: CloseMutationResult) -> str:
    if not result.changed:
        return f"当前 ticket 已处于 closing 流程中。\n- Ticket ID：`{result.ticket.ticket_id}`\n- 计划归档时间：{result.close_execute_at or '未知'}"

    lines = [
        "ticket 已进入 closing。",
        f"- Ticket ID：`{result.ticket.ticket_id}`",
        f"- 原状态：`{result.previous_status.value}`",
        f"- 计划归档时间：{result.close_execute_at or '未知'}",
        "- 如需撤销，请在窗口结束前使用 `/ticket close-cancel`",
    ]
    if result.close_reason is not None:
        lines.append(f"- 关闭理由：{result.close_reason}")
    if result.requested_by_id is not None:
        lines.append(f"- 来源请求：<@{result.requested_by_id}>")
    return "\n".join(lines)


def build_close_request_feedback_message(result: CloseRequestCreationResult) -> str:
    lines = [
        "已向 staff 发出关闭请求。",
        f"- Ticket ID：`{result.ticket.ticket_id}`",
        f"- 请求发起人：<@{result.requested_by_id}>",
        "- staff 可在频道内点击请求消息上的按钮同意或拒绝",
    ]
    if result.reason is not None:
        lines.append(f"- 请求理由：{result.reason}")
    if result.replaced_message_id is not None:
        lines.append(f"- 已替换旧请求消息：`{result.replaced_message_id}`")
    return "\n".join(lines)


def build_revoke_close_feedback_message(result: CloseRevokeResult) -> str:
    return f"ticket closing 已撤销。\n- Ticket ID：`{result.ticket.ticket_id}`\n- 恢复状态：`{result.restored_status.value}`"
