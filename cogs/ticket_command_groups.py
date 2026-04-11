from __future__ import annotations

from discord import app_commands


ticket_group = app_commands.Group(
    name="ticket",
    description="Ticket 管理命令",
)

panel_group = app_commands.Group(
    name="panel",
    description="公开面板管理",
    parent=ticket_group,
)

draft_group = app_commands.Group(
    name="draft",
    description="draft ticket 操作",
    parent=ticket_group,
)

notes_group = app_commands.Group(
    name="notes",
    description="ticket 内部备注",
    parent=ticket_group,
)
