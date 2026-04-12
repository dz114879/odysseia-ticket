from __future__ import annotations

import html
import inspect
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from config.static import STORAGE_DIR
from core.errors import ValidationError
from core.models import TicketRecord
from services.snapshot_query_service import SnapshotQueryService


@dataclass(frozen=True, slots=True)
class ArchiveRenderResult:
    transcript_path: Path
    transcript_filename: str
    message_count: int
    render_mode: str = "live"
    source_message_count: int | None = None
    diagnostics: dict[str, Any] | None = None


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
        return await self.render_live_transcript(ticket=ticket, channel=channel)

    async def render_live_transcript(
        self,
        *,
        ticket: TicketRecord,
        channel: Any,
    ) -> ArchiveRenderResult:
        messages = await self._collect_messages(channel)
        annotations = self._build_annotations(ticket.ticket_id)
        transcript_path = self._write_transcript(
            ticket.ticket_id,
            self._build_html(
                ticket,
                messages,
                annotations=annotations,
                render_mode="live",
                rendered_message_count=len(messages),
            ),
        )
        return ArchiveRenderResult(
            transcript_path=transcript_path,
            transcript_filename=f"{ticket.ticket_id}-transcript.html",
            message_count=len(messages),
            render_mode="live",
            source_message_count=len(messages),
            diagnostics={"annotation_deleted_count": len(annotations.get("deleted_messages", []))},
        )

    async def render_fallback_transcript(
        self,
        *,
        ticket: TicketRecord,
    ) -> ArchiveRenderResult:
        if self.snapshot_query_service is None:
            raise ValidationError("未配置 snapshot_query_service，无法生成 fallback transcript。")

        records = self.snapshot_query_service.get_archive_snapshot_records(ticket.ticket_id)
        if not records:
            raise ValidationError("当前 ticket 没有可用 snapshots，无法生成 fallback transcript。")

        payload = self._build_fallback_payload(records)
        annotations = self._build_annotations(ticket.ticket_id)
        transcript_path = self._write_transcript(
            ticket.ticket_id,
            self._build_html(
                ticket,
                payload["visible_messages"],
                annotations=annotations,
                render_mode="fallback",
                rendered_message_count=int(payload["visible_message_count"]),
                notices=[
                    "本归档由 snapshots fallback 生成；live channel history 不可用或渲染失败。",
                    (
                        f"快照记录数：{payload['record_count']}，"
                        f"可恢复消息数：{payload['visible_message_count']}，"
                        f"已删除消息数：{payload['deleted_message_count']}。"
                    ),
                ],
                extra_sections=[self._build_fallback_timeline_section(payload["timeline_sections"])],
            ),
        )
        return ArchiveRenderResult(
            transcript_path=transcript_path,
            transcript_filename=f"{ticket.ticket_id}-transcript.html",
            message_count=int(payload["visible_message_count"]),
            render_mode="fallback",
            source_message_count=int(payload["record_count"]),
            diagnostics={
                "deleted_message_count": int(payload["deleted_message_count"]),
                "timeline_message_count": len(payload["timeline_sections"]),
            },
        )

    async def _collect_messages(self, channel: Any) -> list[dict[str, object]]:
        history = getattr(channel, "history", None)
        if not callable(history):
            return []

        history_result = history(limit=None, oldest_first=True)
        raw_messages: list[Any] = []
        if hasattr(history_result, "__aiter__"):
            raw_messages.extend([message async for message in history_result])
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
                    "attachments": [getattr(attachment, "filename", None) or getattr(attachment, "url", "attachment") for attachment in attachments],
                    "embeds": self._extract_embeds(getattr(message, "embeds", None) or []),
                }
            )
        return normalized_messages

    def _build_annotations(self, ticket_id: str) -> dict[str, Any]:
        if self.snapshot_query_service is None:
            return {"edits_by_message_id": {}, "deleted_messages": []}
        return self.snapshot_query_service.build_archive_annotations(ticket_id)

    def _write_transcript(self, ticket_id: str, content: str) -> Path:
        transcript_path = self.exports_dir / f"{ticket_id}.html"
        transcript_path.write_text(content, encoding="utf-8")
        return transcript_path

    def _build_fallback_payload(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        grouped_records: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            message_id = self._coerce_message_id(record.get("message_id"))
            if message_id is None:
                continue
            grouped_records[message_id].append(record)

        timeline_sections: list[dict[str, Any]] = []
        visible_messages: list[dict[str, Any]] = []
        deleted_count = 0

        for message_id, message_records in sorted(
            grouped_records.items(),
            key=lambda item: self._sort_message_records(item[1]),
        ):
            timeline_records = sorted(
                message_records,
                key=lambda record: self._timeline_sort_key(record.get("timestamp")),
            )
            latest_state = self._derive_latest_snapshot_state(message_id, timeline_records)
            timeline_sections.append(
                {
                    "message_id": message_id,
                    "author_name": latest_state["author_name"],
                    "author_id": latest_state["author_id"],
                    "events": [self._normalize_timeline_event(record) for record in timeline_records],
                    "deleted": latest_state["deleted"],
                }
            )
            if latest_state["deleted"]:
                deleted_count += 1
                continue
            visible_messages.append(
                {
                    "message_id": message_id,
                    "author_name": latest_state["author_name"],
                    "author_id": latest_state["author_id"],
                    "created_at": latest_state["timestamp"],
                    "content": latest_state["content"],
                    "attachments": latest_state["attachments"],
                }
            )

        return {
            "record_count": len(records),
            "visible_message_count": len(visible_messages),
            "deleted_message_count": deleted_count,
            "visible_messages": visible_messages,
            "timeline_sections": timeline_sections,
        }

    @staticmethod
    def _derive_latest_snapshot_state(
        message_id: int,
        timeline_records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        latest_state = {
            "message_id": message_id,
            "author_name": "Unknown",
            "author_id": None,
            "timestamp": "unknown",
            "content": "",
            "attachments": [],
            "deleted": False,
        }
        for record in timeline_records:
            event = str(record.get("event", "unknown"))
            latest_state["author_name"] = str(record.get("author_name") or record.get("author_id") or latest_state["author_name"])
            latest_state["author_id"] = record.get("author_id", latest_state["author_id"])
            latest_state["timestamp"] = str(record.get("timestamp") or latest_state["timestamp"])
            if event == "create":
                latest_state["content"] = str(record.get("content", "") or "")
                latest_state["attachments"] = list(record.get("attachments") or [])
                latest_state["deleted"] = False
            elif event == "edit":
                latest_state["content"] = str(record.get("new_content", "") or "")
                latest_state["attachments"] = list(record.get("new_attachments") or [])
                latest_state["deleted"] = False
            elif event == "delete":
                latest_state["content"] = str(record.get("deleted_content", "") or latest_state["content"])
                latest_state["attachments"] = list(record.get("deleted_attachments") or latest_state["attachments"])
                latest_state["deleted"] = True
        return latest_state

    @staticmethod
    def _normalize_timeline_event(record: dict[str, Any]) -> dict[str, Any]:
        event = str(record.get("event", "unknown"))
        if event == "create":
            return {
                "event": "create",
                "timestamp": str(record.get("timestamp") or "unknown"),
                "content": str(record.get("content", "") or ""),
                "attachments": list(record.get("attachments") or []),
            }
        if event == "edit":
            return {
                "event": "edit",
                "timestamp": str(record.get("timestamp") or "unknown"),
                "old_content": str(record.get("old_content", "") or ""),
                "new_content": str(record.get("new_content", "") or ""),
                "old_attachments": list(record.get("old_attachments") or []),
                "new_attachments": list(record.get("new_attachments") or []),
            }
        if event == "delete":
            return {
                "event": "delete",
                "timestamp": str(record.get("timestamp") or "unknown"),
                "content": str(record.get("deleted_content", "") or ""),
                "attachments": list(record.get("deleted_attachments") or []),
            }
        return {
            "event": event,
            "timestamp": str(record.get("timestamp") or "unknown"),
            "content": str(record.get("content", "") or ""),
            "attachments": list(record.get("attachments") or []),
        }

    @staticmethod
    def _build_html(
        ticket: TicketRecord,
        messages: list[dict[str, object]],
        *,
        annotations: dict[str, Any],
        render_mode: str,
        rendered_message_count: int,
        notices: list[str] | None = None,
        extra_sections: list[str] | None = None,
    ) -> str:
        header = (
            f"<h1>Transcript for {html.escape(ticket.ticket_id)}</h1>"
            f"<p><strong>Creator:</strong> {ticket.creator_id}</p>"
            f"<p><strong>Category:</strong> {html.escape(ticket.category_key)}</p>"
            f"<p><strong>Closed At:</strong> {html.escape(ticket.closed_at or 'unknown')}</p>"
            f"<p><strong>Close Reason:</strong> {html.escape(ticket.close_reason or 'not provided')}</p>"
            f"<p><strong>Render Mode:</strong> {html.escape(render_mode)}</p>"
            f"<p><strong>Message Count:</strong> {rendered_message_count}</p>"
        )

        edits_by_message_id = annotations.get("edits_by_message_id", {}) if isinstance(annotations, dict) else {}
        deleted_messages = annotations.get("deleted_messages", []) if isinstance(annotations, dict) else []

        timeline: list[dict[str, Any]] = []
        for msg in messages:
            timeline.append({**msg, "_deleted": False, "_edits": []})
        for del_msg in deleted_messages:
            timeline.append(
                {
                    "message_id": del_msg.get("message_id"),
                    "author_name": del_msg.get("author_name", "Unknown"),
                    "author_id": del_msg.get("author_id"),
                    "created_at": del_msg.get("timestamp", "unknown"),
                    "content": del_msg.get("content", ""),
                    "attachments": del_msg.get("attachments") or [],
                    "embeds": [],
                    "_deleted": True,
                    "_edits": del_msg.get("edits") or [],
                }
            )

        def _mid_sort_key(entry: dict[str, Any]) -> int:
            try:
                return int(entry.get("message_id") or 0)
            except (TypeError, ValueError):
                return 0

        timeline.sort(key=_mid_sort_key)

        rows: list[str] = []
        for entry in timeline:
            is_deleted = entry.get("_deleted", False)
            css_class = "message deleted" if is_deleted else "message"

            attachment_lines = ""
            attachments = entry.get("attachments") or []
            if attachments:
                rendered_attachments = "".join(f"<li>{html.escape(str(item))}</li>" for item in attachments)
                attachment_lines = f"<ul>{rendered_attachments}</ul>"

            embed_lines = ""
            for embed_data in entry.get("embeds") or []:
                embed_lines += ArchiveRenderService._render_embed_html(embed_data)

            edit_records = entry.get("_edits") or [] if is_deleted else edits_by_message_id.get(entry.get("message_id"), [])
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
                edit_lines = f"<section class='snapshot-annotation'><h4>编辑快照</h4><ul>{rendered_edits}</ul></section>"

            deleted_label = "<span class='deleted-label'>[已删除]</span>" if is_deleted else ""

            rows.append(
                f"<article class='{css_class}'>"
                f"<div class='meta'><span class='author'>{html.escape(str(entry['author_name']))}</span> "
                f"<span class='author-id'>({html.escape(str(entry.get('author_id') or 'unknown'))})</span> "
                f"<span class='timestamp'>{html.escape(str(entry['created_at']))}</span>"
                f"{deleted_label}</div>"
                f"<pre>{html.escape(str(entry.get('content', ''))) or '(empty message)'}</pre>"
                f"{embed_lines}"
                f"{attachment_lines}"
                f"{edit_lines}"
                "</article>"
            )

        if not rows:
            rows.append("<p>No transcript messages were available.</p>")

        rendered_notices = ""
        if notices:
            rendered_notices = (
                "<section class='fallback-notice'>" + "".join(f"<p>{html.escape(notice)}</p>" for notice in notices if notice) + "</section>"
            )

        rendered_extra_sections = "".join(section for section in (extra_sections or []) if section)

        return (
            "<!DOCTYPE html>"
            "<html lang='en'><head><meta charset='utf-8'>"
            "<title>Ticket Transcript</title>"
            "<style>body{font-family:Segoe UI,Arial,sans-serif;background:#111827;color:#f3f4f6;padding:24px;}"
            ".message{border:1px solid #374151;border-radius:8px;padding:12px;margin:12px 0;background:#1f2937;}"
            ".message.deleted{border-color:#7c2d12;background:#2b1d1b;}"
            ".message.deleted pre{text-decoration:line-through;opacity:0.7;}"
            ".deleted-label{color:#ef4444;font-size:12px;font-weight:600;margin-left:8px;}"
            ".embed{border-left:4px solid #5865f2;background:#2f3136;padding:8px 12px;margin:8px 0;border-radius:4px;}"
            ".embed-author{font-size:12px;color:#b9bbbe;}"
            ".embed-title{font-weight:600;color:#00b0f4;margin:4px 0;}"
            ".embed-description{color:#dcddde;white-space:pre-wrap;margin:4px 0;}"
            ".embed-field{margin:4px 0;}"
            ".embed-field-name{font-weight:600;font-size:13px;}"
            ".embed-field-value{font-size:13px;color:#dcddde;}"
            ".embed-footer{font-size:11px;color:#72767d;margin-top:4px;}"
            ".meta{color:#9ca3af;font-size:12px;margin-bottom:8px;}pre{white-space:pre-wrap;word-break:break-word;}"
            ".snapshot-annotation{margin-top:12px;padding:8px;border-top:1px dashed #4b5563;color:#d1d5db;}"
            ".deleted-messages,.timeline-section{margin-top:32px;}"
            ".fallback-notice{margin:20px 0;padding:16px;border:1px solid #92400e;border-radius:8px;background:#3f2b12;color:#fde68a;}"
            ".timeline-message{border-left:3px solid #4b5563;padding-left:12px;margin:16px 0;}"
            "a{color:#60a5fa;}</style></head><body>"
            f"{header}"
            f"{rendered_notices}"
            f"{''.join(rows)}"
            f"{rendered_extra_sections}"
            "</body></html>"
        )

    @staticmethod
    def _build_fallback_timeline_section(timeline_sections: list[dict[str, Any]]) -> str:
        if not timeline_sections:
            return ""

        rendered_messages: list[str] = []
        for section in timeline_sections:
            rendered_events: list[str] = []
            for event in section.get("events", []):
                event_type = str(event.get("event", "unknown"))
                attachments = event.get("attachments") or event.get("new_attachments") or []
                attachment_lines = ""
                if attachments:
                    attachment_lines = "<ul>" + "".join(f"<li>{html.escape(str(item))}</li>" for item in attachments) + "</ul>"
                if event_type == "edit":
                    rendered_events.append(
                        "<li>"
                        f"<strong>{html.escape(str(event.get('timestamp', 'unknown')))}</strong> "
                        "[edit]"
                        f"<div>old: {html.escape(str(event.get('old_content', '') or '(empty)'))}</div>"
                        f"<div>new: {html.escape(str(event.get('new_content', '') or '(empty)'))}</div>"
                        "</li>"
                    )
                    continue
                rendered_events.append(
                    "<li>"
                    f"<strong>{html.escape(str(event.get('timestamp', 'unknown')))}</strong> "
                    f"[{html.escape(event_type)}]"
                    f"<div>{html.escape(str(event.get('content', '') or '(empty)'))}</div>"
                    f"{attachment_lines}"
                    "</li>"
                )

            rendered_messages.append(
                "<article class='timeline-message'>"
                f"<h3>Message {html.escape(str(section.get('message_id', 'unknown')))}</h3>"
                f"<p>{html.escape(str(section.get('author_name', 'Unknown')))} "
                f"({html.escape(str(section.get('author_id') or 'unknown'))})</p>"
                f"<p><strong>Deleted:</strong> {html.escape(str(section.get('deleted', False)))}</p>"
                f"<ul>{''.join(rendered_events)}</ul>"
                "</article>"
            )

        return f"<section class='timeline-section'><h2>Snapshot timeline</h2>{''.join(rendered_messages)}</section>"

    @staticmethod
    def _sort_message_records(records: list[dict[str, Any]]) -> tuple[str, int]:
        first_timestamp = min(
            (ArchiveRenderService._timeline_sort_key(record.get("timestamp")) for record in records),
            default="",
        )
        first_message_id = ArchiveRenderService._coerce_message_id(records[0].get("message_id")) or 0
        return first_timestamp, first_message_id

    @staticmethod
    def _timeline_sort_key(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    @staticmethod
    def _coerce_message_id(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_embeds(embeds: list[Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for embed_obj in embeds:
            data: dict[str, Any] = {}
            embed_author = getattr(embed_obj, "author", None)
            if embed_author and getattr(embed_author, "name", None):
                data["author_name"] = str(embed_author.name)
            title = getattr(embed_obj, "title", None)
            if title:
                data["title"] = str(title)
            description = getattr(embed_obj, "description", None)
            if description:
                data["description"] = str(description)
            raw_fields = getattr(embed_obj, "fields", None) or []
            if raw_fields:
                data["fields"] = [
                    {"name": str(getattr(f, "name", "")), "value": str(getattr(f, "value", ""))}
                    for f in raw_fields
                ]
            footer = getattr(embed_obj, "footer", None)
            if footer and getattr(footer, "text", None):
                data["footer"] = str(footer.text)
            color = getattr(embed_obj, "color", None) or getattr(embed_obj, "colour", None)
            if color is not None:
                color_value = getattr(color, "value", color)
                if isinstance(color_value, int):
                    data["color"] = f"#{color_value:06x}"
            if data:
                result.append(data)
        return result

    @staticmethod
    def _render_embed_html(embed_data: dict[str, Any]) -> str:
        color = html.escape(embed_data.get("color", "#5865f2"))
        parts = [f"<div class='embed' style='border-left-color:{color}'>"]
        if "author_name" in embed_data:
            parts.append(f"<div class='embed-author'>{html.escape(embed_data['author_name'])}</div>")
        if "title" in embed_data:
            parts.append(f"<div class='embed-title'>{html.escape(embed_data['title'])}</div>")
        if "description" in embed_data:
            parts.append(f"<div class='embed-description'>{html.escape(embed_data['description'])}</div>")
        for field in embed_data.get("fields", []):
            parts.append(
                f"<div class='embed-field'>"
                f"<div class='embed-field-name'>{html.escape(field.get('name', ''))}</div>"
                f"<div class='embed-field-value'>{html.escape(field.get('value', ''))}</div>"
                f"</div>"
            )
        if "footer" in embed_data:
            parts.append(f"<div class='embed-footer'>{html.escape(embed_data['footer'])}</div>")
        parts.append("</div>")
        return "".join(parts)

    @staticmethod
    def _resolve_author_name(author: Any) -> str:
        if author is None:
            return "Unknown"
        return str(getattr(author, "display_name", None) or getattr(author, "name", None) or getattr(author, "id", "Unknown"))

    @staticmethod
    def _format_timestamp(value: Any) -> str:
        if value is None:
            return "unknown"
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)
