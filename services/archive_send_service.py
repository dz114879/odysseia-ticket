from __future__ import annotations

from pathlib import Path
from typing import Any

import discord

from core.errors import ValidationError
from core.models import TicketRecord
from discord_ui.close_embeds import build_archive_record_embed


class ArchiveSendService:
    async def send_archive(
        self,
        archive_channel: Any,
        *,
        ticket: TicketRecord,
        transcript_path: Path,
        transcript_filename: str,
    ) -> Any:
        send = getattr(archive_channel, "send", None)
        if send is None:
            raise ValidationError("当前归档频道不可发送消息。")
        if not transcript_path.exists():
            raise ValidationError("归档 transcript 文件不存在。")

        with transcript_path.open("rb") as file_stream:
            discord_file = discord.File(file_stream, filename=transcript_filename)
            try:
                return await send(
                    embed=build_archive_record_embed(ticket),
                    file=discord_file,
                )
            finally:
                close = getattr(discord_file, "close", None)
                if callable(close):
                    close()
