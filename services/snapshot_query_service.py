from __future__ import annotations

from collections import defaultdict
from typing import Any

from storage.snapshot_store import SnapshotStore


class SnapshotQueryService:
    def __init__(self, *, snapshot_store: SnapshotStore | None = None) -> None:
        self.snapshot_store = snapshot_store or SnapshotStore()

    def get_message_timeline(self, ticket_id: str, message_id: int) -> list[dict[str, Any]]:
        return [
            record
            for record in self.snapshot_store.read_records(ticket_id)
            if self._coerce_message_id(record.get("message_id")) == message_id
        ]

    def format_message_timeline(self, ticket_id: str, message_id: int) -> str:
        timeline = self.get_message_timeline(ticket_id, message_id)
        if not timeline:
            return f"ticket `{ticket_id}` 中未找到 message `{message_id}` 的快照时间线。"

        author_name = timeline[0].get("author_name") or timeline[0].get("author_id") or "Unknown"
        lines = [
            f"Ticket `{ticket_id}` | Message `{message_id}`",
            f"作者：{author_name}",
            "",
        ]
        for record in timeline:
            event = str(record.get("event", "unknown"))
            if event == "create":
                lines.extend(
                    [
                        f"[create] {record.get('timestamp', 'unknown')}",
                        self._format_message_block(
                            content_key="content",
                            attachments_key="attachments",
                            record=record,
                        ),
                        "",
                    ]
                )
            elif event == "edit":
                lines.extend(
                    [
                        f"[edit] {record.get('timestamp', 'unknown')}",
                        f"旧内容：{record.get('old_content', '') or '(empty)'}",
                        self._format_attachment_list("旧附件", record.get("old_attachments") or []),
                        f"新内容：{record.get('new_content', '') or '(empty)'}",
                        self._format_attachment_list("新附件", record.get("new_attachments") or []),
                        "",
                    ]
                )
            elif event == "delete":
                lines.extend(
                    [
                        f"[delete] {record.get('timestamp', 'unknown')}",
                        f"删除前内容：{record.get('deleted_content', '') or '(empty)'}",
                        self._format_attachment_list(
                            "删除前附件",
                            record.get("deleted_attachments") or [],
                        ),
                        "",
                    ]
                )
        return "\n".join(lines).strip()

    def build_recycle_bin_text(self, ticket_id: str) -> str:
        grouped_records: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for record in self.snapshot_store.read_records(ticket_id):
            message_id = self._coerce_message_id(record.get("message_id"))
            if message_id is None:
                continue
            grouped_records[message_id].append(record)

        deleted_message_ids = [
            message_id
            for message_id, records in grouped_records.items()
            if any(str(record.get("event")) == "delete" for record in records)
        ]
        if not deleted_message_ids:
            return f"ticket `{ticket_id}` 当前没有已删除消息的快照记录。"

        lines = [f"Ticket `{ticket_id}` recycle bin", ""]
        for message_id in sorted(deleted_message_ids):
            timeline = grouped_records[message_id]
            latest_delete = next(
                (record for record in reversed(timeline) if str(record.get("event")) == "delete"),
                None,
            )
            if latest_delete is None:
                continue
            author_name = latest_delete.get("author_name") or latest_delete.get("author_id") or "Unknown"
            lines.extend(
                [
                    f"Message `{message_id}` | 作者：{author_name}",
                    f"删除时间：{latest_delete.get('timestamp', 'unknown')}",
                    f"删除前最后内容：{latest_delete.get('deleted_content', '') or '(empty)'}",
                    self._format_attachment_list(
                        "删除前附件",
                        latest_delete.get("deleted_attachments") or [],
                    ),
                ]
            )
            edit_records = [record for record in timeline if str(record.get("event")) == "edit"]
            if edit_records:
                lines.append("编辑历史：")
                for edit_record in edit_records:
                    lines.append(
                        f"- {edit_record.get('timestamp', 'unknown')}: "
                        f"{edit_record.get('old_content', '') or '(empty)'} -> "
                        f"{edit_record.get('new_content', '') or '(empty)'}"
                    )
            lines.append("")
        return "\n".join(lines).strip()

    def build_archive_annotations(self, ticket_id: str) -> dict[str, Any]:
        edits_by_message_id: dict[int, list[dict[str, Any]]] = defaultdict(list)
        deleted_messages: list[dict[str, Any]] = []
        grouped_records: dict[int, list[dict[str, Any]]] = defaultdict(list)

        for record in self.snapshot_store.read_records(ticket_id):
            message_id = self._coerce_message_id(record.get("message_id"))
            if message_id is None:
                continue
            grouped_records[message_id].append(record)
            event = str(record.get("event", ""))
            if event == "edit":
                edits_by_message_id[message_id].append(record)
            elif event == "delete":
                deleted_messages.append(record)

        deleted_sections: list[dict[str, Any]] = []
        for record in deleted_messages:
            message_id = self._coerce_message_id(record.get("message_id"))
            if message_id is None:
                continue
            deleted_sections.append(
                {
                    "message_id": message_id,
                    "author_name": record.get("author_name") or record.get("author_id") or "Unknown",
                    "author_id": record.get("author_id"),
                    "timestamp": record.get("timestamp"),
                    "content": record.get("deleted_content", ""),
                    "attachments": record.get("deleted_attachments") or [],
                    "edits": edits_by_message_id.get(message_id, []),
                    "timeline": grouped_records.get(message_id, []),
                }
            )

        return {
            "edits_by_message_id": dict(edits_by_message_id),
            "deleted_messages": deleted_sections,
        }

    @staticmethod
    def _format_message_block(*, content_key: str, attachments_key: str, record: dict[str, Any]) -> str:
        lines = [f"内容：{record.get(content_key, '') or '(empty)'}"]
        lines.append(
            SnapshotQueryService._format_attachment_list(
                "附件",
                record.get(attachments_key) or [],
            )
        )
        return "\n".join(lines)

    @staticmethod
    def _format_attachment_list(title: str, attachments: list[Any]) -> str:
        if not attachments:
            return f"{title}：无"
        return f"{title}：" + ", ".join(str(item) for item in attachments)

    @staticmethod
    def _coerce_message_id(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
