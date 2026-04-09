from __future__ import annotations


def build_ticket_help_message() -> str:
    return (
        "📘 Ticket 命令帮助\n\n"
        "当前可用命令：\n"
        "- `/ticket help`：查看这份帮助说明。\n"
        "- `/ticket submit`：提交当前 draft ticket 给 staff 处理。\n"
        "- `/ticket claim`：认领当前 submitted ticket。\n"
        "- `/ticket unclaim`：取消当前认领。\n"
        "- `/ticket priority <low|medium|high|emergency>`：调整当前 submitted ticket 的优先级。\n\n"
        "使用说明：\n"
        "- `submit` 仅适用于 draft ticket；提交前请先发送至少一条用户消息。\n"
        "- `claim / unclaim / priority` 仅适用于 submitted ticket，且需要当前分类 staff、Ticket 管理员或 Bot 所有者权限。\n"
        "- strict claim mode 下，未认领 ticket 可能对 staff 仅可见不可发言。\n"
        "- `priority` 目前只支持 `low` / `medium` / `high` / `emergency`；`sleep` 需要等待后续专用命令。\n"
        "- 这份帮助可在 draft / submitted 频道中查看，后续控制面板的帮助入口也会复用同一份内容。"
    )
