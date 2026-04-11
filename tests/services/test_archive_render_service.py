from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from core.enums import TicketStatus
from core.models import TicketRecord
from services.archive_render_service import ArchiveRenderService
from services.snapshot_query_service import SnapshotQueryService
from storage.file_store import TicketFileStore
from storage.snapshot_store import SnapshotStore


@dataclass
class FakeAuthor:
    id: int
    name: str
    bot: bool = False


@dataclass
class FakeAttachment:
    filename: str


@dataclass
class FakeHistoryMessage:
    id: int
    author: FakeAuthor
    content: str
    created_at: datetime
    attachments: list[FakeAttachment] = field(default_factory=list)


class FakeChannel:
    def __init__(self, messages: list[FakeHistoryMessage]) -> None:
        self._messages = messages

    def history(self, *, limit=None, oldest_first: bool = True):
        async def iterator():
            messages = list(self._messages)
            if not oldest_first:
                messages.reverse()
            for item in messages:
                yield item

        return iterator()


@pytest.mark.asyncio
async def test_archive_render_service_injects_edit_and_delete_snapshot_annotations(tmp_path) -> None:
    snapshot_store = SnapshotStore(file_store=TicketFileStore(tmp_path))
    snapshot_store.overwrite_records(
        "ticket-1",
        [
            {
                "event": "create",
                "message_id": 1,
                "author_id": 201,
                "author_name": "creator",
                "timestamp": "2024-01-01T00:00:00+00:00",
                "content": "原始内容",
                "attachments": [],
            },
            {
                "event": "edit",
                "message_id": 1,
                "author_id": 201,
                "author_name": "creator",
                "timestamp": "2024-01-01T00:01:00+00:00",
                "old_content": "原始内容",
                "new_content": "更新后的内容",
                "old_attachments": [],
                "new_attachments": [],
            },
            {
                "event": "delete",
                "message_id": 2,
                "author_id": 301,
                "author_name": "staff",
                "timestamp": "2024-01-01T02:34:56+00:00",
                "deleted_content": "被删除的消息",
                "deleted_attachments": ["[文件: error.log, 1KB]"],
            },
        ],
    )
    render_service = ArchiveRenderService(
        exports_dir=tmp_path / "exports",
        snapshot_query_service=SnapshotQueryService(snapshot_store=snapshot_store),
    )
    ticket = TicketRecord(
        ticket_id="ticket-1",
        guild_id=1,
        creator_id=201,
        category_key="support",
        channel_id=9001,
        status=TicketStatus.CLOSING,
        close_reason="已处理",
        closed_at="2024-01-01T00:05:00+00:00",
    )
    channel = FakeChannel(
        [
            FakeHistoryMessage(
                id=1,
                author=FakeAuthor(201, "creator"),
                content="更新后的内容",
                created_at=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
            )
        ]
    )

    result = await render_service.render_ticket_transcript(ticket=ticket, channel=channel)
    html = result.transcript_path.read_text(encoding="utf-8")

    assert "编辑快照" in html
    assert "Deleted messages from snapshots" in html
    assert "被删除的消息" in html
    assert "2024-01-01T02:34:56+00:00" in html
    assert "2024-01-01T00:02:00+00:00" not in html
    assert result.message_count == 1
