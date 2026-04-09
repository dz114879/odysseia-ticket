from __future__ import annotations


def build_ticket_help_message() -> str:
    return (
        "📘 Ticket 命令帮助\n\n"
        "当前可用命令：\n"
        "- `/ticket help`：查看这份帮助说明。\n"
        "- `/ticket submit`：提交当前 draft ticket 给 staff 处理。\n"
        "- `/ticket claim`：认领当前 submitted ticket。\n"
        "- `/ticket unclaim`：取消当前认领。\n"
        "- `/ticket priority <low|medium|high|emergency>`：调整当前 submitted ticket 的优先级。\n"
        "- `/ticket sleep`：将当前 submitted ticket 挂起为 sleep 状态。\n\n"
        "使用说明：\n"
        "- `submit` 仅适用于 draft ticket；提交前请先发送至少一条用户消息。\n"
        "- `claim / unclaim / priority / sleep` 仅适用于 submitted ticket，且需要当前分类 staff、Ticket 管理员或 Bot 所有者权限。\n"
        "- strict claim mode 下，未认领 ticket 可能对 staff 仅可见不可发言。\n"
        "- `sleep` 会保留睡前优先级，并将频道名前缀切换为 `💤|`。\n"
        "- sleep 频道收到任意非 bot 新消息后，会自动尝试恢复为 submitted 并还原睡前优先级。\n"
        "- 这份帮助可在 draft / submitted 频道中查看，后续控制面板的帮助入口也会复用同一份内容。"
    )
