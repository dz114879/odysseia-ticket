from __future__ import annotations

import html
import inspect
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from config.static import STORAGE_DIR
from core.models import TicketRecord
from services.snapshot_query_service import SnapshotQueryService


@dataclass(frozen=True, slots=True)
class ArchiveRenderResult:
    transcript_path: Path
    transcript_filename: str
    message_count: int


class ArchiveRenderService:
    def __init__(
        self,
        *,
        exports_dir: Path | None = None,
        snapshot_query_service: SnapshotQueryService | None = None,
    ) -> None:
        self.exports_dir = exports_dir or STORAGE_DIR / "exports"
        self.snapshot_query_service = snapshot_query_service
        self.exports_dir.mkdir(parents=True, exist_ok=True)

    async def render_ticket_transcript(
        self,
        *,
        ticket: TicketRecord,
        channel: Any,
    ) -> ArchiveRenderResult:
        messages = await self._collect_messages(channel)
        annotations = (
            self.snapshot_query_service.build_archive_annotations(ticket.ticket_id)
            if self.snapshot_query_service is not None
            else {"edits_by_message_id": {}, "deleted_messages": []}
        )
        transcript_path = self.exports_dir / f"{ticket.ticket_id}.html"
        transcript_path.write_text(
            self._build_html(ticket, messages, annotations=annotations),
            encoding="utf-8",
        )
        return ArchiveRenderResult(
            transcript_path=transcript_path,
            transcript_filename=f"{ticket.ticket_id}-transcript.html",
            message_count=len(messages),
        )

    async def _collect_messages(self, channel: Any) -> list[dict[str, object]]:
        history = getattr(channel, "history", None)
        if not callable(history):
            return []

        history_result = history(limit=None, oldest_first=True)
        raw_messages: list[Any] = []
        if hasattr(history_result, "__aiter__"):
            async for message in history_result:
                raw_messages.append(message)
        else:
            if inspect.isawaitable(history_result):
                history_result = await history_result
            raw_messages = list(history_result or [])

        normalized_messages: list[dict[str, object]] = []
        for message in raw_messages:
            author = getattr(message, "author", None)
            attachments = getattr(message, "attachments", None) or []
            normalized_messages.append(
                {
                    "message_id": getattr(message, "id", None),
                    "author_name": self._resolve_author_name(author),
                    "author_id": getattr(author, "id", None),
                    "created_at": self._format_timestamp(getattr(message, "created_at", None)),
                    "content": str(getattr(message, "content", "") or ""),
                    "attachments": [
                        getattr(attachment, "filename", None) or getattr(attachment, "url", "attachment")
                        for attachment in attachments
                    ],
                }
            )
        return normalized_messages

    @staticmethod
    def _build_html(
        ticket: TicketRecord,
        messages: list[dict[str, object]],
        *,
        annotations: dict[str, Any],
    ) -> str:
        header = (
            f"<h1>Transcript for {html.escape(ticket.ticket_id)}</h1>"
            f"<p><strong>Creator:</strong> {ticket.creator_id}</p>"
            f"<p><strong>Category:</strong> {html.escape(ticket.category_key)}</p>"
            f"<p><strong>Closed At:</strong> {html.escape(ticket.closed_at or 'unknown')}</p>"
            f"<p><strong>Close Reason:</strong> {html.escape(ticket.close_reason or 'not provided')}</p>"
            f"<p><strong>Message Count:</strong> {len(messages)}</p>"
        )

        edits_by_message_id = annotations.get("edits_by_message_id", {}) if isinstance(annotations, dict) else {}
        deleted_messages = annotations.get("deleted_messages", []) if isinstance(annotations, dict) else []

        rows: list[str] = []
        for message in messages:
            attachment_lines = ""
            attachments = message["attachments"]
            if attachments:
                rendered_attachments = "".join(
                    f"<li>{html.escape(str(item))}</li>" for item in attachments
                )
                attachment_lines = f"<ul>{rendered_attachments}</ul>"

            edit_records = edits_by_message_id.get(message.get("message_id"), [])
            edit_lines = ""
            if edit_records:
                rendered_edits = "".join(
                    "<li>"
                    f"<strong>{html.escape(str(record.get('timestamp', 'unknown')))}</strong>"
                    f"<div>old: {html.escape(str(record.get('old_content', '') or '(empty)'))}</div>"
                    f"<div>new: {html.escape(str(record.get('new_content', '') or '(empty)'))}</div>"
                    "</li>"
                    for record in edit_records
                )
                edit_lines = (
                    "<section class='snapshot-annotation'>"
                    "<h4>编辑快照</h4>"
                    f"<ul>{rendered_edits}</ul>"
                    "</section>"
                )

            rows.append(
                "<article class='message'>"
                f"<div class='meta'><span class='author'>{html.escape(str(message['author_name']))}</span> "
                f"<span class='author-id'>({html.escape(str(message['author_id'] or 'unknown'))})</span> "
                f"<span class='timestamp'>{html.escape(str(message['created_at']))}</span></div>"
                f"<pre>{html.escape(str(message['content'])) or '(empty message)'}</pre>"
                f"{attachment_lines}"
                f"{edit_lines}"
                "</article>"
            )

        if not rows:
            rows.append("<p>No transcript messages were available.</p>")

        deleted_section = ""
        if deleted_messages:
            rendered_deleted = []
            for deleted_message in deleted_messages:
                attachment_lines = ""
                attachments = deleted_message.get("attachments") or []
                if attachments:
                    attachment_lines = "<ul>" + "".join(
                        f"<li>{html.escape(str(item))}</li>" for item in attachments
                    ) + "</ul>"
                timeline_lines = ""
                edits = deleted_message.get("edits") or []
                if edits:
                    timeline_lines = "<ul>" + "".join(
                        "<li>"
                        f"{html.escape(str(edit.get('timestamp', 'unknown')))} | "
                        f"{html.escape(str(edit.get('old_content', '') or '(empty)'))} -> "
                        f"{html.escape(str(edit.get('new_content', '') or '(empty)'))}"
                        "</li>"
                        for edit in edits
                    ) + "</ul>"
                rendered_deleted.append(
                    "<article class='message deleted'>"
                    f"<div class='meta'><span class='author'>{html.escape(str(deleted_message.get('author_name', 'Unknown')))}</span> "
                    f"<span class='author-id'>({html.escape(str(deleted_message.get('author_id') or 'unknown'))})</span> "
                    f"<span class='timestamp'>{html.escape(str(deleted_message.get('timestamp') or 'unknown'))}</span></div>"
                    f"<pre>{html.escape(str(deleted_message.get('content', '') or '(empty message)'))}</pre>"
                    f"{attachment_lines}"
                    f"{timeline_lines}"
                    "</article>"
                )
            deleted_section = (
                "<section class='deleted-messages'>"
                "<h2>Deleted messages from snapshots</h2>"
                f"{''.join(rendered_deleted)}"
                "</section>"
            )

        return (
            "<!DOCTYPE html>"
            "<html lang='en'><head><meta charset='utf-8'>"
            "<title>Ticket Transcript</title>"
            "<style>body{font-family:Segoe UI,Arial,sans-serif;background:#111827;color:#f3f4f6;padding:24px;}"
            ".message{border:1px solid #374151;border-radius:8px;padding:12px;margin:12px 0;background:#1f2937;}"
            ".message.deleted{border-color:#7c2d12;background:#2b1d1b;}"
            ".meta{color:#9ca3af;font-size:12px;margin-bottom:8px;}pre{white-space:pre-wrap;word-break:break-word;}"
            ".snapshot-annotation{margin-top:12px;padding:8px;border-top:1px dashed #4b5563;color:#d1d5db;}"
            ".deleted-messages{margin-top:32px;}a{color:#60a5fa;}</style></head><body>"
            f"{header}"
            f"{''.join(rows)}"
            f"{deleted_section}"
            "</body></html>"
        )

    @staticmethod
    def _resolve_author_name(author: Any) -> str:
        if author is None:
            return "Unknown"
        return str(
            getattr(author, "display_name", None)
            or getattr(author, "name", None)
            or getattr(author, "id", "Unknown")
        )

    @staticmethod
    def _format_timestamp(value: Any) -> str:
        if value is None:
            return "unknown"
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)
