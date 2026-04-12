from __future__ import annotations

from dataclasses import dataclass

DEFAULT_PANEL_TITLE = "🎫 Ticket 支持中心"
DEFAULT_PANEL_DESCRIPTION = "Ticket 是您与社区管理组的私密沟通频道。除了社区管理外，其他人无法看到 Ticket 频道的内容，保障您的隐私。\n\n在这里，您可以："
DEFAULT_PANEL_BULLET_POINTS = "- 提交对社区/BOT相关技术问题的反馈\n- 提交对社区运营的建议\n- 投诉您认为违规的行为\n- 对管理组的不合理判断申诉"
DEFAULT_PANEL_CAPACITY_TEXT = "当前容量状态：可用"
DEFAULT_PANEL_FOOTER_TEXT = "请在下方的下拉框选框中选择对应分类，提交ticket。"


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
