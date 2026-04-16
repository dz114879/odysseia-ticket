from __future__ import annotations

from dataclasses import dataclass

DEFAULT_PANEL_TITLE = "🎫 Ticket 支持中心"
DEFAULT_PANEL_BODY = (
    "Ticket 是您与社区管理组的私密沟通频道。除了社区管理外，其他人无法看到 Ticket 频道的内容，保障您的隐私。\n\n"
    "在这里，您可以：\n"
    "- 提交对社区/BOT相关技术问题的反馈\n"
    "- 提交对社区运营的建议\n"
    "- 投诉您认为违规的行为\n"
    "- 对管理组的不合理判断申诉"
)
DEFAULT_PANEL_CAPACITY_TEXT = "当前容量状态：可用"
DEFAULT_PANEL_FOOTER_TEXT = "请在下方的下拉框选框中选择对应分类，提交ticket。"


def build_public_panel_body(*, description: str | None = None, bullet_points: str | None = None) -> str:
    parts = [part.strip() for part in (description, bullet_points) if part and part.strip()]
    if not parts:
        return DEFAULT_PANEL_BODY
    if len(parts) == 1:
        return parts[0]

    first_part, second_part = parts[0], parts[1]
    separator = "\n" if first_part.endswith(("：", ":")) else "\n\n"
    return f"{first_part}{separator}{second_part}"


def build_default_draft_welcome_text(
    *,
    inactive_close_hours: int = 6,
    abandon_timeout_hours: int = 24,
) -> str:
    return (
        f"目前 Ticket 处于草稿阶段，您有**最多 {abandon_timeout_hours} 小时**来详细描述情况。"
        "在此期间，管理员**无法看到本频道**，机器人也**不会记录您输入的任何内容**。\n\n"
        "此外，您还可以为本Ticket频道取个名字，让管理员快速了解情况。"
        "请注意，由于 Discord 设计缺陷，任何用户都可以使用第三方插件看到您给Ticket取的标题（无法看到内容）。\n\n"
        "当您准备好后，请**点击下方的提交按钮**，正式提交您的 Ticket；"
        "提交后，管理员才能查看您的 Ticket 内容。\n\n"
        "机器人会记录您和管理员在Ticket内发送的所有消息，附件，以及编辑和删除操作。"
        "记录在 Ticket 处理完毕后将被归档保存。\n\n"
        "如果您误操作打开了本 Ticket，可以在下方按钮中废弃。被废弃的 Ticket 内容不会被记录。\n\n"
        f"⚠️ 如果您在{inactive_close_hours}小时内，没有在本Ticket频道发送任何消息，本 Ticket 将被自动废弃；\n"
        f"⚠️ 如果您在{abandon_timeout_hours}小时内未将草稿正式提交，本 Ticket 将被自动废弃。"
    )


def build_default_snapshot_warning_text(*, limit: int) -> str:
    return f"⚠️ 本 Ticket 内消息数接近BOT记录上限（{limit}条），建议总结后重开 Ticket 继续讨论。 ⚠️"


def build_default_snapshot_limit_text(*, limit: int) -> str:
    return f"⚠️ 本 Ticket 消息数已达记录上限（{limit}条），新消息将不再被快照系统记录。 ⚠️"


@dataclass(frozen=True, slots=True)
class DefaultTicketCategoryTemplate:
    category_key: str
    display_name: str
    emoji: str | None = None
    description: str | None = None
    is_enabled: bool = True
    sort_order: int = 0


DEFAULT_TICKET_CATEGORY_TEMPLATES = (
    DefaultTicketCategoryTemplate(
        category_key="answer-appeal",
        display_name="答题处罚申诉专用",
        emoji="🧾",
        description="适用于答题处罚、考试异常与相关申诉。",
        sort_order=10,
    ),
    DefaultTicketCategoryTemplate(
        category_key="technical-support",
        display_name="技术问题反馈",
        emoji="🛠️",
        description="适用于机器人异常、功能问题与使用故障。",
        sort_order=20,
    ),
    DefaultTicketCategoryTemplate(
        category_key="community-feedback",
        display_name="社区运营建议",
        emoji="💡",
        description="适用于活动建议、流程优化与体验反馈。",
        sort_order=30,
    ),
    DefaultTicketCategoryTemplate(
        category_key="report-abuse",
        display_name="违规行为投诉",
        emoji="🚨",
        description="适用于举报骚扰、违规内容与恶意行为。",
        sort_order=40,
    ),
    DefaultTicketCategoryTemplate(
        category_key="punishment-appeal",
        display_name="处罚申诉",
        emoji="⚖️",
        description="适用于封禁、禁言与其他处罚的正式申诉。",
        sort_order=50,
    ),
)
