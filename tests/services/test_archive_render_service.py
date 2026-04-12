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
class FakeEmbedAuthor:
    name: str


@dataclass
class FakeEmbedFooter:
    text: str


@dataclass
class FakeEmbedField:
    name: str
    value: str
    inline: bool = False


@dataclass
class FakeEmbed:
    title: str | None = None
    description: str | None = None
    color: int | None = None
    colour: int | None = None
    author: FakeEmbedAuthor | None = None
    footer: FakeEmbedFooter | None = None
    fields: list[FakeEmbedField] = field(default_factory=list)


@dataclass
class FakeHistoryMessage:
    id: int
    author: FakeAuthor
    content: str
    created_at: datetime
    attachments: list[FakeAttachment] = field(default_factory=list)
    embeds: list[FakeEmbed] = field(default_factory=list)


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
    assert "被删除的消息" in html
    assert "已删除" in html
    assert "message deleted" in html
    assert "2024-01-01T02:34:56+00:00" in html
    assert "Deleted messages from snapshots" not in html
    assert result.render_mode == "live"
    assert result.message_count == 1


@pytest.mark.asyncio
async def test_archive_render_service_can_render_snapshot_only_fallback_transcript(tmp_path) -> None:
    snapshot_store = SnapshotStore(file_store=TicketFileStore(tmp_path))
    snapshot_store.overwrite_records(
        "ticket-fallback",
        [
            {
                "event": "create",
                "message_id": 10,
                "author_id": 201,
                "author_name": "creator",
                "timestamp": "2024-01-01T00:00:00+00:00",
                "content": "初始内容",
                "attachments": [],
            },
            {
                "event": "edit",
                "message_id": 10,
                "author_id": 201,
                "author_name": "creator",
                "timestamp": "2024-01-01T00:01:00+00:00",
                "old_content": "初始内容",
                "new_content": "编辑后的内容",
                "old_attachments": [],
                "new_attachments": ["trace.txt"],
            },
            {
                "event": "delete",
                "message_id": 11,
                "author_id": 301,
                "author_name": "staff",
                "timestamp": "2024-01-01T00:02:00+00:00",
                "deleted_content": "已删除的提示",
                "deleted_attachments": [],
            },
        ],
    )
    render_service = ArchiveRenderService(
        exports_dir=tmp_path / "exports",
        snapshot_query_service=SnapshotQueryService(snapshot_store=snapshot_store),
    )
    ticket = TicketRecord(ticket_id="ticket-fallback", guild_id=1, creator_id=201, category_key="support")

    result = await render_service.render_fallback_transcript(ticket=ticket)
    html = result.transcript_path.read_text(encoding="utf-8")

    assert result.render_mode == "fallback"
    assert result.message_count == 1
    assert "snapshots fallback" in html
    assert "Snapshot timeline" in html
    assert "编辑后的内容" in html
    assert "已删除的提示" in html


@pytest.mark.asyncio
async def test_archive_render_service_renders_embed_content_in_html(tmp_path) -> None:
    render_service = ArchiveRenderService(exports_dir=tmp_path / "exports")
    ticket = TicketRecord(
        ticket_id="ticket-embed",
        guild_id=1,
        creator_id=201,
        category_key="support",
        channel_id=9001,
        status=TicketStatus.CLOSING,
        closed_at="2024-01-01T00:05:00+00:00",
    )
    channel = FakeChannel(
        [
            FakeHistoryMessage(
                id=1,
                author=FakeAuthor(100, "BotUser", bot=True),
                content="",
                created_at=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
                embeds=[
                    FakeEmbed(
                        title="Welcome",
                        description="Please describe your issue.",
                        color=0x5865F2,
                        author=FakeEmbedAuthor(name="Support Bot"),
                        footer=FakeEmbedFooter(text="Ticket System"),
                        fields=[
                            FakeEmbedField(name="Priority", value="High"),
                            FakeEmbedField(name="Category", value="Bug Report"),
                        ],
                    )
                ],
            ),
            FakeHistoryMessage(
                id=2,
                author=FakeAuthor(201, "creator"),
                content="I have a bug to report",
                created_at=datetime(2024, 1, 1, 0, 1, tzinfo=timezone.utc),
            ),
        ]
    )

    result = await render_service.render_ticket_transcript(ticket=ticket, channel=channel)
    rendered_html = result.transcript_path.read_text(encoding="utf-8")

    assert result.message_count == 2
    assert "Welcome" in rendered_html
    assert "Please describe your issue." in rendered_html
    assert "Support Bot" in rendered_html
    assert "Ticket System" in rendered_html
    assert "Priority" in rendered_html
    assert "High" in rendered_html
    assert "Bug Report" in rendered_html
    assert "embed-title" in rendered_html
    assert "embed-description" in rendered_html
    assert "embed-field" in rendered_html
    assert "#5865f2" in rendered_html


@pytest.mark.asyncio
async def test_archive_render_deleted_messages_appear_inline_at_correct_position(tmp_path) -> None:
    snapshot_store = SnapshotStore(file_store=TicketFileStore(tmp_path))
    snapshot_store.overwrite_records(
        "ticket-inline",
        [
            {
                "event": "create",
                "message_id": 5,
                "author_id": 301,
                "author_name": "staff",
                "timestamp": "2024-01-01T00:00:30+00:00",
                "content": "deleted between",
                "attachments": [],
            },
            {
                "event": "delete",
                "message_id": 5,
                "author_id": 301,
                "author_name": "staff",
                "timestamp": "2024-01-01T00:01:00+00:00",
                "deleted_content": "deleted between",
                "deleted_attachments": [],
            },
        ],
    )
    render_service = ArchiveRenderService(
        exports_dir=tmp_path / "exports",
        snapshot_query_service=SnapshotQueryService(snapshot_store=snapshot_store),
    )
    ticket = TicketRecord(
        ticket_id="ticket-inline",
        guild_id=1,
        creator_id=201,
        category_key="support",
        channel_id=9002,
        status=TicketStatus.CLOSING,
        closed_at="2024-01-01T00:05:00+00:00",
    )
    channel = FakeChannel(
        [
            FakeHistoryMessage(
                id=3,
                author=FakeAuthor(201, "creator"),
                content="first message",
                created_at=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
            ),
            FakeHistoryMessage(
                id=10,
                author=FakeAuthor(201, "creator"),
                content="third message",
                created_at=datetime(2024, 1, 1, 0, 2, tzinfo=timezone.utc),
            ),
        ]
    )

    result = await render_service.render_ticket_transcript(ticket=ticket, channel=channel)
    rendered_html = result.transcript_path.read_text(encoding="utf-8")

    assert "message deleted" in rendered_html
    assert "已删除" in rendered_html
    first_pos = rendered_html.index("first message")
    deleted_pos = rendered_html.index("deleted between")
    third_pos = rendered_html.index("third message")
    assert first_pos < deleted_pos < third_pos, "Deleted message should appear between first and third in timeline"
