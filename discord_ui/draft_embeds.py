from __future__ import annotations

import discord


def build_draft_welcome_embed(
    *,
    category_name: str,
    inactive_close_hours: int = 6,
    abandon_timeout_hours: int = 24,
    custom_welcome_text: str | None = None,
) -> discord.Embed:
    if custom_welcome_text:
        description = custom_welcome_text
    else:
        description = (
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
    return discord.Embed(
        title=f"📋 已创建 {category_name} Ticket",
        description=description,
        color=discord.Color.blue(),
    )
