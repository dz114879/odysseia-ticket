from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from core.enums import ClaimMode, TicketStatus
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository
from runtime.cache import RuntimeCacheStore
from runtime.locks import LockManager
import services.snapshot_service as snapshot_service_module
from services.snapshot_service import SnapshotService
from storage.file_store import TicketFileStore
from storage.snapshot_store import SnapshotStore


@dataclass
class FakeAuthor:
    id: int
    name: str
    bot: bool = False

    @property
    def display_name(self) -> str:
        return self.name


@dataclass
class FakeAttachment:
    filename: str
    size: int = 1024
    content_type: str = "text/plain"


@dataclass
class FakeMessage:
    id: int
    author: FakeAuthor
    channel: FakeChannel
    content: str
    created_at: datetime
    attachments: list[FakeAttachment] = field(default_factory=list)
    embeds: list[object] = field(default_factory=list)
    edited_at: datetime | None = None


@dataclass
class FakeRawMessageUpdatePayload:
    channel_id: int
    message_id: int
    data: dict[str, object]
    cached_message: FakeMessage | None = None


@dataclass
class FakeRawMessageDeletePayload:
    channel_id: int
    message_id: int
    cached_message: FakeMessage | None = None


class FakeChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id
        self.sent_messages: list[str] = []
        self.history_messages: list[FakeMessage] = []

    async def send(self, *, content=None, embed=None, view=None):
        del embed, view
        self.sent_messages.append(content or "")
        return content

    def history(self, *, limit=None, oldest_first: bool = True):
        async def iterator():
            messages = list(self.history_messages)
            if not oldest_first:
                messages.reverse()
            for message in messages:
                yield message

        return iterator()


class FakeLoggingService:
    def __init__(self) -> None:
        self.ticket_logs: list[dict[str, object]] = []

    async def send_guild_log(self, *args, **kwargs) -> bool:
        return False

    async def send_ticket_log(self, **payload) -> bool:
        self.ticket_logs.append(payload)
        return True


@pytest.fixture
def prepared_snapshot_context(migrated_database):
    guild_repository = GuildRepository(migrated_database)
    ticket_repository = TicketRepository(migrated_database)
    guild_repository.upsert_config(
        GuildConfigRecord(
            guild_id=1,
            is_initialized=True,
            log_channel_id=100,
            archive_channel_id=200,
            ticket_category_channel_id=300,
            admin_role_id=400,
            claim_mode=ClaimMode.RELAXED,
            max_open_tickets=10,
            timezone="Asia/Hong_Kong",
            enable_download_window=True,
            updated_at="2024-01-01T00:00:00+00:00",
        )
    )
    guild_repository.upsert_category(
        TicketCategoryConfig(
            guild_id=1,
            category_key="support",
            display_name="技术支持",
            staff_role_id=500,
        )
    )
    ticket = ticket_repository.create(
        TicketRecord(
            ticket_id="1-support-0001",
            guild_id=1,
            creator_id=201,
            category_key="support",
            channel_id=9001,
            status=TicketStatus.SUBMITTED,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )
    )
    channel = FakeChannel(9001)
    creator = FakeAuthor(201, "creator")
    staff = FakeAuthor(301, "staff")
    return {
        "database": migrated_database,
        "ticket_repository": ticket_repository,
        "ticket": ticket,
        "channel": channel,
        "creator": creator,
        "staff": staff,
    }


@pytest.mark.asyncio
async def test_snapshot_service_bootstrap_create_edit_and_delete_flow(prepared_snapshot_context, tmp_path) -> None:
    database = prepared_snapshot_context["database"]
    ticket_repository = prepared_snapshot_context["ticket_repository"]
    ticket = prepared_snapshot_context["ticket"]
    channel = prepared_snapshot_context["channel"]
    creator = prepared_snapshot_context["creator"]
    staff = prepared_snapshot_context["staff"]
    snapshot_store = SnapshotStore(file_store=TicketFileStore(tmp_path))
    cache = RuntimeCacheStore()
    service = SnapshotService(
        database,
        snapshot_store=snapshot_store,
        lock_manager=LockManager(),
        cache=cache,
    )

    first_message = FakeMessage(
        id=1,
        author=creator,
        channel=channel,
        content="第一条消息",
        created_at=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
    )
    second_message = FakeMessage(
        id=2,
        author=staff,
        channel=channel,
        content="第二条消息",
        created_at=datetime(2024, 1, 1, 0, 1, tzinfo=timezone.utc),
        attachments=[FakeAttachment(filename="debug.log")],
    )
    channel.history_messages.extend([first_message, second_message])

    bootstrap_result = await service.bootstrap_from_channel_history(ticket, channel)
    stored_ticket = ticket_repository.get_by_ticket_id(ticket.ticket_id)

    assert bootstrap_result.skipped is False
    assert bootstrap_result.create_count == 2
    assert stored_ticket is not None
    assert stored_ticket.snapshot_bootstrapped_at is not None
    assert stored_ticket.message_count == 2
    assert len(snapshot_store.read_records(ticket.ticket_id)) == 2
    assert cache.get_snapshot_message_count(channel.id) == 2
    assert cache.get_snapshot_state(channel.id, first_message.id) is not None
    assert cache.get_snapshot_state(channel.id, second_message.id) is not None

    third_message = FakeMessage(
        id=3,
        author=creator,
        channel=channel,
        content="第三条消息",
        created_at=datetime(2024, 1, 1, 0, 2, tzinfo=timezone.utc),
    )
    assert await service.handle_message(third_message) is True
    assert ticket_repository.get_by_ticket_id(ticket.ticket_id).message_count == 3

    edited_message = FakeMessage(
        id=3,
        author=creator,
        channel=channel,
        content="第三条消息（已编辑）",
        created_at=third_message.created_at,
        edited_at=datetime(2024, 1, 1, 0, 3, tzinfo=timezone.utc),
    )
    assert await service.handle_message_edit(third_message, edited_message) is True
    latest_state = cache.get_snapshot_state(channel.id, 3)
    assert latest_state is not None
    assert latest_state.content == "第三条消息（已编辑）"

    assert await service.handle_message_delete(edited_message) is True
    assert cache.get_snapshot_state(channel.id, 3) is None
    assert [record["event"] for record in snapshot_store.read_records(ticket.ticket_id)] == [
        "create",
        "create",
        "create",
        "edit",
        "delete",
    ]


@pytest.mark.asyncio
async def test_snapshot_service_handles_uncached_raw_edit_and_delete_events(
    prepared_snapshot_context,
    tmp_path,
) -> None:
    database = prepared_snapshot_context["database"]
    ticket = prepared_snapshot_context["ticket"]
    channel = prepared_snapshot_context["channel"]
    creator = prepared_snapshot_context["creator"]
    snapshot_store = SnapshotStore(file_store=TicketFileStore(tmp_path))

    initial_service = SnapshotService(
        database,
        snapshot_store=snapshot_store,
        cache=RuntimeCacheStore(),
        lock_manager=LockManager(),
    )
    original_message = FakeMessage(
        id=7,
        author=creator,
        channel=channel,
        content="重启前消息",
        created_at=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
    )
    assert await initial_service.handle_message(original_message) is True

    restarted_service = SnapshotService(
        database,
        snapshot_store=snapshot_store,
        cache=RuntimeCacheStore(),
        lock_manager=LockManager(),
    )
    raw_edit_payload = FakeRawMessageUpdatePayload(
        channel_id=channel.id,
        message_id=original_message.id,
        data={
            "content": "重启后已编辑",
            "edited_timestamp": "2024-01-01T00:05:00+00:00",
            "author": {"id": str(creator.id), "username": creator.name},
        },
    )
    assert await restarted_service.handle_raw_message_edit(raw_edit_payload) is True
    latest_state = restarted_service.cache.get_snapshot_state(channel.id, original_message.id)
    assert latest_state is not None
    assert latest_state.content == "重启后已编辑"
    assert (
        await restarted_service.handle_raw_message_edit(
            FakeRawMessageUpdatePayload(
                channel_id=channel.id,
                message_id=original_message.id,
                data={"content": "会被缓存事件接管"},
                cached_message=original_message,
            )
        )
        is False
    )

    assert (
        await restarted_service.handle_raw_message_delete(FakeRawMessageDeletePayload(channel_id=channel.id, message_id=original_message.id)) is True
    )
    assert restarted_service.cache.get_snapshot_state(channel.id, original_message.id) is None
    assert (
        await restarted_service.handle_raw_message_delete(
            FakeRawMessageDeletePayload(channel_id=channel.id, message_id=original_message.id, cached_message=original_message)
        )
        is False
    )
    assert [record["event"] for record in snapshot_store.read_records(ticket.ticket_id)] == ["create", "edit", "delete"]


@pytest.mark.asyncio
async def test_snapshot_service_records_delete_timestamp_at_deletion_time(
    prepared_snapshot_context,
    tmp_path,
    monkeypatch,
) -> None:
    database = prepared_snapshot_context["database"]
    ticket = prepared_snapshot_context["ticket"]
    channel = prepared_snapshot_context["channel"]
    creator = prepared_snapshot_context["creator"]
    snapshot_store = SnapshotStore(file_store=TicketFileStore(tmp_path))
    service = SnapshotService(
        database,
        snapshot_store=snapshot_store,
        cache=RuntimeCacheStore(),
        lock_manager=LockManager(),
    )
    original_message = FakeMessage(
        id=8,
        author=creator,
        channel=channel,
        content="稍后删除的消息",
        created_at=datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc),
    )
    fixed_deleted_at = "2024-01-01T01:23:45+00:00"
    monkeypatch.setattr(snapshot_service_module, "utc_now_iso", lambda: fixed_deleted_at)

    assert await service.handle_message(original_message) is True
    assert await service.handle_message_delete(original_message) is True

    delete_record = snapshot_store.read_records(ticket.ticket_id)[-1]
    assert delete_record["event"] == "delete"
    assert delete_record["timestamp"] == fixed_deleted_at
    assert delete_record["timestamp"] != original_message.created_at.isoformat()


@pytest.mark.asyncio
async def test_snapshot_service_rebuilds_runtime_cache_from_snapshot_file(prepared_snapshot_context, tmp_path) -> None:
    database = prepared_snapshot_context["database"]
    ticket_repository = prepared_snapshot_context["ticket_repository"]
    ticket = prepared_snapshot_context["ticket"]
    snapshot_store = SnapshotStore(file_store=TicketFileStore(tmp_path))
    path = snapshot_store.get_path(ticket.ticket_id)
    path.write_text(
        '{"event":"create","message_id":1,"author_id":201,"author_name":"creator","timestamp":"2024-01-01T00:00:00+00:00","content":"hello","attachments":[]}\n'
        "{broken-json\n"
        '{"event":"edit","message_id":1,"author_id":201,"author_name":"creator","timestamp":"2024-01-01T00:01:00+00:00","old_content":"hello","new_content":"hello!!!","old_attachments":[],"new_attachments":[]}\n'
        '{"event":"delete","message_id":2,"author_id":301,"author_name":"staff","timestamp":"2024-01-01T00:02:00+00:00","deleted_content":"gone","deleted_attachments":[]}\n',
        encoding="utf-8",
    )
    cache = RuntimeCacheStore()
    service = SnapshotService(
        database,
        snapshot_store=snapshot_store,
        cache=cache,
        lock_manager=LockManager(),
    )

    report = await service.restore_runtime_state()
    restored_ticket = ticket_repository.get_by_ticket_id(ticket.ticket_id)

    assert report.tickets_scanned == 1
    assert report.tickets_restored == 1
    assert report.cached_messages == 1
    latest_state = cache.get_snapshot_state(ticket.channel_id or 9001, 1)
    assert latest_state is not None
    assert latest_state.content == "hello!!!"
    assert cache.get_snapshot_message_count(ticket.channel_id or 9001) == 1
    assert restored_ticket is not None
    assert restored_ticket.message_count == 1


@pytest.mark.asyncio
async def test_snapshot_service_emits_threshold_warnings_and_stops_create_after_limit(
    prepared_snapshot_context,
    tmp_path,
) -> None:
    database = prepared_snapshot_context["database"]
    ticket = prepared_snapshot_context["ticket"]
    channel = prepared_snapshot_context["channel"]
    creator = prepared_snapshot_context["creator"]
    logging_service = FakeLoggingService()
    service = SnapshotService(
        database,
        snapshot_store=SnapshotStore(file_store=TicketFileStore(tmp_path)),
        cache=RuntimeCacheStore(),
        lock_manager=LockManager(),
        logging_service=logging_service,
        create_warning_threshold=2,
        create_limit=3,
    )
    TicketRepository(database).update(ticket.ticket_id, snapshot_bootstrapped_at="2024-01-01T00:00:00+00:00")

    for index in range(1, 5):
        message = FakeMessage(
            id=index,
            author=creator,
            channel=channel,
            content=f"message-{index}",
            created_at=datetime(2024, 1, 1, 0, index, tzinfo=timezone.utc),
        )
        await service.handle_message(message)

    records = service.snapshot_store.read_records(ticket.ticket_id)

    assert len([record for record in records if record["event"] == "create"]) == 3
    assert len(channel.sent_messages) == 2
    assert "接近BOT记录上限" in channel.sent_messages[0]
    assert "已达记录上限" in channel.sent_messages[1]
    assert len(logging_service.ticket_logs) == 1
