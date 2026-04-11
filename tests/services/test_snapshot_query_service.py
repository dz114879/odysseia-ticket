from __future__ import annotations

from services.snapshot_query_service import SnapshotQueryService
from storage.file_store import TicketFileStore
from storage.snapshot_store import SnapshotStore


def build_query_service(tmp_path) -> SnapshotQueryService:
    snapshot_store = SnapshotStore(file_store=TicketFileStore(tmp_path))
    snapshot_store.overwrite_records(
        "ticket-1",
        [
            {
                "event": "create",
                "message_id": 1001,
                "author_id": 201,
                "author_name": "creator",
                "timestamp": "2024-01-01T00:00:00+00:00",
                "content": "原始内容",
                "attachments": ["[文件: debug.log, 1KB]"],
            },
            {
                "event": "edit",
                "message_id": 1001,
                "author_id": 201,
                "author_name": "creator",
                "timestamp": "2024-01-01T00:01:00+00:00",
                "old_content": "原始内容",
                "new_content": "更新后的内容",
                "old_attachments": ["[文件: debug.log, 1KB]"],
                "new_attachments": [],
            },
            {
                "event": "delete",
                "message_id": 1001,
                "author_id": 201,
                "author_name": "creator",
                "timestamp": "2024-01-01T01:30:00+00:00",
                "deleted_content": "更新后的内容",
                "deleted_attachments": [],
            },
        ],
    )
    return SnapshotQueryService(snapshot_store=snapshot_store)


def test_snapshot_query_service_formats_message_timeline(tmp_path) -> None:
    service = build_query_service(tmp_path)

    rendered = service.format_message_timeline("ticket-1", 1001)

    assert "Message `1001`" in rendered
    assert "[create]" in rendered
    assert "[edit]" in rendered
    assert "[delete]" in rendered
    assert "更新后的内容" in rendered


def test_snapshot_query_service_builds_recycle_bin_text(tmp_path) -> None:
    service = build_query_service(tmp_path)

    rendered = service.build_recycle_bin_text("ticket-1")

    assert "recycle bin" in rendered
    assert "删除时间" in rendered
    assert "删除时间：2024-01-01T01:30:00+00:00" in rendered
    assert "删除时间：2024-01-01T00:00:00+00:00" not in rendered
    assert "编辑历史" in rendered
    assert "更新后的内容" in rendered
