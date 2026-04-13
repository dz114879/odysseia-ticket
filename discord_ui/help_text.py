from __future__ import annotations

from core.constants import TRANSFER_EXECUTION_DELAY_SECONDS


def _format_transfer_delay_text(delay_seconds: int = TRANSFER_EXECUTION_DELAY_SECONDS) -> str:
    if delay_seconds % 60 == 0:
        minutes = delay_seconds // 60
        if minutes == 1:
            return "1 分钟"
        return f"{minutes} 分钟"
    return f"{delay_seconds} 秒"


def build_ticket_help_message(*, transfer_delay_seconds: int = TRANSFER_EXECUTION_DELAY_SECONDS) -> str:
    transfer_delay_text = _format_transfer_delay_text(transfer_delay_seconds)
    return (
        "📘 Ticket 命令帮助\n\n"
        "当前可用命令：\n"
        "- `/ticket help`：查看这份帮助说明。\n"
        "- `/ticket submit`：提交当前 draft ticket 给 staff 处理。\n"
        "- `/ticket claim`：认领当前 submitted ticket。\n"
        "- `/ticket unclaim`：取消当前认领。\n"
        "- `/ticket transfer-claim <member>`：将当前 submitted ticket 的认领转交给当前分类内另一位 staff。\n"
        "- `/ticket rename <title>`：修改当前 submitted / sleep ticket 的标题，并保留现有前缀。\n"
        "- `/ticket mute <member> [duration] [reason]`：临时或手动禁言当前 ticket 内的目标参与者。\n"
        "- `/ticket unmute <member>`：解除当前 ticket 内目标参与者的禁言。\n"
        "- `/ticket priority <low|medium|high|emergency>`：调整当前 submitted ticket 的优先级。\n"
        "- `/ticket sleep`：将当前 submitted ticket 挂起为 sleep 状态。\n"
        f"- `/ticket transfer <category_key> [reason]`：发起跨分类转交，并将 ticket 置为 transferring；默认会在{transfer_delay_text}后自动执行。\n\n"
        "- `/ticket untransfer`：在转交正式执行前撤销当前 transferring ticket，并恢复原状态。\n\n"
        "使用说明：\n"
        "- `submit` 仅适用于 draft ticket；提交前请先发送至少一条用户消息。\n"
        "- `claim / unclaim / transfer-claim / priority / sleep` 仅适用于 submitted ticket，且需要当前分类 staff、Ticket 管理员或 Bot 所有者权限。\n"
        "- `transfer-claim` 需要当前 ticket 已被认领；只有当前认领者、Ticket 管理员或 Bot 所有者可以执行，且目标成员必须属于当前分类 staff。\n"
        "- `rename` 仅适用于 submitted / sleep；会保留当前优先级前缀或 `💤|` 前缀，且标题不能为空、不能只包含 emoji 或符号。\n"
        "- `mute / unmute` 仅适用于 submitted / sleep；不能对自己、bot、合法 staff 或 Ticket 管理员执行 mute。\n"
        "- `mute` 目前主要用于创建者或已显式允许发言的非 staff 参与者；duration 支持 `30m`、`2h`、`1d`、`45分钟`，留空则需手动 `/ticket unmute`。\n"
        "- `transfer` 适用于 submitted / sleep，并要求目标分类是当前服务器中另一个已启用分类。\n"
        f"- `transfer` 发起后会保留约{transfer_delay_text}的撤销窗口；期间面板与频道日志会展示计划执行时间。\n"
        "- `untransfer` 仅适用于 transferring，并会恢复到发起转交前的 submitted / sleep 状态。\n"
        "- strict claim mode 下，未认领 ticket 可能对 staff 仅可见不可发言。\n"
        "- `sleep` 会保留睡前优先级，并将频道名前缀切换为 `💤|`。\n"
        "- sleep 频道收到任意非 bot 新消息后，会自动尝试恢复为 submitted 并还原睡前优先级。\n"
        "- 这份帮助可在当前 ticket 频道中查看，后续控制面板的帮助入口也会复用同一份内容。"
    )
