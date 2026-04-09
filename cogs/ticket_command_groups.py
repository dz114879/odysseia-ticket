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
