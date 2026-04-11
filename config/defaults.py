from __future__ import annotations

from dataclasses import dataclass

DEFAULT_PANEL_TITLE = "🎫 Ticket 支持中心"
DEFAULT_PANEL_DESCRIPTION = "欢迎使用 Odysseia Ticket 系统。\n请选择最符合您问题的分类，系统会在下一阶段进入私密处理流程。"
DEFAULT_PANEL_BULLET_POINTS = "• 技术问题反馈\n• 社区运营建议\n• 违规行为投诉\n• 处罚申诉与答题申诉"
DEFAULT_PANEL_CAPACITY_TEXT = "当前容量状态：可用"
DEFAULT_PANEL_FOOTER_TEXT = "请选择下方分类开始。"


@dataclass(frozen=True, slots=True)
class DefaultTicketCategoryTemplate:
    category_key: str
    display_name: str
    emoji: str | None = None
    description: str | None = None
    extra_welcome_text: str | None = None
    is_enabled: bool = True
    sort_order: int = 0


DEFAULT_TICKET_CATEGORY_TEMPLATES = (
    DefaultTicketCategoryTemplate(
        category_key="answer-appeal",
        display_name="答题处罚申诉专用",
        emoji="🧾",
        description="适用于答题处罚、考试异常与相关申诉。",
        extra_welcome_text="请尽量附上题目截图、处罚时间与申诉理由。",
        sort_order=10,
    ),
    DefaultTicketCategoryTemplate(
        category_key="technical-support",
        display_name="技术问题反馈",
        emoji="🛠️",
        description="适用于机器人异常、功能问题与使用故障。",
        extra_welcome_text="请描述复现步骤、时间点与错误现象。",
        sort_order=20,
    ),
    DefaultTicketCategoryTemplate(
        category_key="community-feedback",
        display_name="社区运营建议",
        emoji="💡",
        description="适用于活动建议、流程优化与体验反馈。",
        extra_welcome_text="欢迎提供背景、目标和可执行建议。",
        sort_order=30,
    ),
    DefaultTicketCategoryTemplate(
        category_key="report-abuse",
        display_name="违规行为投诉",
        emoji="🚨",
        description="适用于举报骚扰、违规内容与恶意行为。",
        extra_welcome_text="请附上截图、链接、时间和相关成员信息。",
        sort_order=40,
    ),
    DefaultTicketCategoryTemplate(
        category_key="punishment-appeal",
        display_name="处罚申诉",
        emoji="⚖️",
        description="适用于封禁、禁言与其他处罚的正式申诉。",
        extra_welcome_text="请说明处罚原因、时间和您的申诉依据。",
        sort_order=50,
    ),
)
