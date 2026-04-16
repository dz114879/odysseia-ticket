from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.errors import ValidationError
from core.enums import ClaimMode, TicketStatus
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketMuteRecord, TicketRecord
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_mute_repository import TicketMuteRepository
from db.repositories.ticket_repository import TicketRepository
from runtime.locks import LockManager
from services.archive_render_service import ArchiveRenderService
from services.archive_service import ArchiveService
from services.cleanup_service import CleanupService
from services.close_service import CloseService
from services.snapshot_query_service import SnapshotQueryService
from storage.file_store import TicketFileStore
from storage.snapshot_store import SnapshotStore
from tests.helpers.discord_fakes import FakeGuild, FakeMember, FakeMessage, FakeRole


@dataclass
class FakeAttachment:
    filename: str
    url: str | None = None


@dataclass
class FakeHistoryMessage:
    id: int
    author: FakeMember
    content: str
    created_at: datetime
    attachments: list[FakeAttachment] = field(default_factory=list)




class FakeTextChannel:
    def __init__(self, channel_id: int, guild: FakeGuild, *, name: str) -> None:
        self.id = channel_id
        self.guild = guild
        self.name = name
        self.next_message_id = 1000
        self.sent_messages: list[FakeMessage] = []
        self.permission_calls: list[dict[str, object]] = []
        self.delete_calls: list[str | None] = []
        self.deleted = False
        self.history_messages: list[FakeHistoryMessage] = []
        self._messages: dict[int, FakeMessage] = {}

    async def send(self, *, content=None, embed=None, view=None, file=None):
        message = FakeMessage(
            self.next_message_id,
            content=content,
            embed=embed,
            view=view,
            file=file,
        )
        self.next_message_id += 1
        self.sent_messages.append(message)
        self._messages[message.id] = message
        return message

    async def set_permissions(self, target, *, overwrite, reason: str | None = None) -> None:
        self.permission_calls.append(
            {
                "target_id": getattr(target, "id", None),
                "overwrite": overwrite,
                "reason": reason,
            }
        )

    async def delete(self, *, reason: str | None = None) -> None:
        self.delete_calls.append(reason)
        self.deleted = True

    async def fetch_message(self, message_id: int) -> FakeMessage:
        return self._messages[message_id]

    def history(self, *, limit=None, oldest_first: bool = True):
        async def iterator():
            messages = list(self.history_messages)
            if not oldest_first:
                messages.reverse()
            for message in messages:
                yield message

        return iterator()


class FakeDiscordNotFound(Exception):
    def __init__(self, message: str = "channel not found") -> None:
        super().__init__(message)
        self.status = 404


class FakeBot:
    def __init__(self, *channels: FakeTextChannel) -> None:
        self.channels = {channel.id: channel for channel in channels}

    def get_channel(self, channel_id: int):
        return self.channels.get(channel_id)

    async def fetch_channel(self, channel_id: int):
        channel = self.channels.get(channel_id)
        if channel is None:
            raise FakeDiscordNotFound()
        return channel


class FakeStaffPanelService:
    def __init__(self) -> None:
        self.requested_ticket_ids: list[str] = []

    def request_refresh(self, ticket_id: str) -> None:
        self.requested_ticket_ids.append(ticket_id)


@pytest.fixture
def prepared_close_context(migrated_database):
    guild_repository = GuildRepository(migrated_database)
    ticket_repository = TicketRepository(migrated_database)

    guild_repository.upsert_config(
        GuildConfigRecord(
            guild_id=1,
            is_initialized=True,
            log_channel_id=100,
            archive_channel_id=2001,
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
            emoji="🛠️",
            description="处理技术问题",
            staff_role_ids_json="[500]",
            staff_user_ids_json="[]",
            is_enabled=True,
            allowlist_role_ids_json="[]",
            denylist_role_ids_json="[]",
            sort_order=1,
        )
    )

    admin_role = FakeRole(400)
    staff_role = FakeRole(500)
    guild = FakeGuild(1)
    guild.add_role(admin_role)
    guild.add_role(staff_role)

    creator = FakeMember(201, "creator")
    staff_member = FakeMember(301, "staff", roles=[staff_role])
    admin_member = FakeMember(401, "admin", roles=[admin_role])
    for member in (creator, staff_member, admin_member):
        guild.add_member(member)

    channel = FakeTextChannel(9001, guild, name="login-error")
    archive_channel = FakeTextChannel(2001, guild, name="ticket-archives")
    ticket = ticket_repository.create(
        TicketRecord(
            ticket_id="1-support-0001",
            guild_id=1,
            creator_id=creator.id,
            category_key="support",
            channel_id=channel.id,
            status=TicketStatus.SUBMITTED,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
            has_user_message=True,
            last_user_message_at="2024-01-01T01:00:00+00:00",
        )
    )

    return {
        "database": migrated_database,
        "guild": guild,
        "channel": channel,
        "archive_channel": archive_channel,
        "ticket_repository": ticket_repository,
        "mute_repository": TicketMuteRepository(migrated_database),
        "ticket": ticket,
        "creator": creator,
        "staff_member": staff_member,
        "admin_member": admin_member,
    }


def build_close_service(context, tmp_path: Path):
    lock_manager = LockManager()
    bot = FakeBot(context["channel"], context["archive_channel"])
    archive_service = ArchiveService(
        context["database"],
        bot=bot,
        lock_manager=lock_manager,
        render_service=ArchiveRenderService(exports_dir=tmp_path / "exports"),
        cleanup_service=CleanupService(context["database"], storage_dir=tmp_path),
    )
    staff_panel_service = FakeStaffPanelService()
    close_service = CloseService(
        context["database"],
        bot=bot,
        lock_manager=lock_manager,
        archive_service=archive_service,
        staff_panel_service=staff_panel_service,
    )
    return close_service, archive_service, staff_panel_service, bot


@pytest.mark.asyncio
async def test_initiate_close_updates_ticket_locks_permissions_and_posts_notice(
    prepared_close_context,
    tmp_path,
) -> None:
    close_service, _, staff_panel_service, _ = build_close_service(prepared_close_context, tmp_path)
    channel = prepared_close_context["channel"]
    staff_member = prepared_close_context["staff_member"]

    result = await close_service.initiate_close(
        channel,
        actor=staff_member,
        reason="问题已解决",
    )
    stored = prepared_close_context["ticket_repository"].get_by_channel_id(channel.id)

    assert result.changed is True
    assert result.previous_status is TicketStatus.SUBMITTED
    assert stored is not None
    assert stored.status is TicketStatus.CLOSING
    assert stored.status_before is TicketStatus.SUBMITTED
    assert stored.close_reason == "问题已解决"
    assert stored.close_initiated_by == staff_member.id
    assert stored.close_execute_at is not None
    assert stored.closed_at is not None
    assert {call["target_id"] for call in channel.permission_calls} == {201, 400, 500}
    assert channel.sent_messages
    assert channel.sent_messages[0].embed.title == "🔒 Ticket 即将归档并关闭"
    assert staff_panel_service.requested_ticket_ids == [stored.ticket_id]


@pytest.mark.asyncio
async def test_initiate_close_from_sleep_rejects_when_active_capacity_is_full(
    prepared_close_context,
    tmp_path,
) -> None:
    close_service, _, staff_panel_service, _ = build_close_service(prepared_close_context, tmp_path)
    database = prepared_close_context["database"]
    channel = prepared_close_context["channel"]
    staff_member = prepared_close_context["staff_member"]
    ticket_repository = prepared_close_context["ticket_repository"]
    GuildRepository(database).update_config(1, max_open_tickets=1)
    ticket_repository.update(prepared_close_context["ticket"].ticket_id, status=TicketStatus.SLEEP)
    ticket_repository.create(
        TicketRecord(
            ticket_id="1-support-0002",
            guild_id=1,
            creator_id=202,
            category_key="support",
            channel_id=9002,
            status=TicketStatus.SUBMITTED,
        )
    )

    with pytest.raises(ValidationError, match="active 容量已满（1/1）"):
        await close_service.initiate_close(channel, actor=staff_member, reason="准备直接关闭")

    stored = ticket_repository.get_by_channel_id(channel.id)
    assert stored is not None and stored.status is TicketStatus.SLEEP
    assert channel.sent_messages == []
    assert channel.permission_calls == []
    assert staff_panel_service.requested_ticket_ids == []


@pytest.mark.asyncio
async def test_revoke_close_restores_ticket_state_and_permissions(
    prepared_close_context,
    tmp_path,
) -> None:
    close_service, _, staff_panel_service, _ = build_close_service(prepared_close_context, tmp_path)
    channel = prepared_close_context["channel"]
    staff_member = prepared_close_context["staff_member"]

    await close_service.initiate_close(channel, actor=staff_member, reason="误操作")
    result = await close_service.revoke_close(channel, actor=staff_member)
    stored = prepared_close_context["ticket_repository"].get_by_channel_id(channel.id)

    assert stored is not None
    assert result.restored_status is TicketStatus.SUBMITTED
    assert stored.status is TicketStatus.SUBMITTED
    assert stored.status_before is None
    assert stored.close_reason is None
    assert stored.close_execute_at is None
    assert stored.closed_at is None
    assert channel.sent_messages[-1].content is not None
    assert "已撤销 ticket" in channel.sent_messages[-1].content
    assert staff_panel_service.requested_ticket_ids == [stored.ticket_id, stored.ticket_id]


@pytest.mark.asyncio
async def test_sweep_due_closing_tickets_archives_deletes_channel_and_cleans_up(
    prepared_close_context,
    tmp_path,
) -> None:
    close_service, _, _, _ = build_close_service(prepared_close_context, tmp_path)
    channel = prepared_close_context["channel"]
    archive_channel = prepared_close_context["archive_channel"]
    ticket = prepared_close_context["ticket"]
    mute_repository = prepared_close_context["mute_repository"]
    creator = prepared_close_context["creator"]
    staff_member = prepared_close_context["staff_member"]
    ticket_repository = prepared_close_context["ticket_repository"]

    channel.history_messages.extend(
        [
            FakeHistoryMessage(
                id=1,
                author=creator,
                content="我这里已经正常了",
                created_at=datetime(2024, 1, 1, 1, 0, tzinfo=timezone.utc),
            ),
            FakeHistoryMessage(
                id=2,
                author=staff_member,
                content="好的，我来归档",
                created_at=datetime(2024, 1, 1, 1, 5, tzinfo=timezone.utc),
                attachments=[FakeAttachment(filename="debug.log")],
            ),
        ]
    )
    mute_repository.upsert(
        TicketMuteRecord(
            ticket_id=ticket.ticket_id,
            user_id=creator.id,
            muted_by=staff_member.id,
        )
    )
    ticket_repository.update(
        ticket.ticket_id,
        status=TicketStatus.CLOSING,
        status_before=TicketStatus.SUBMITTED,
        close_reason="已确认处理完成",
        close_execute_at=(datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
        closed_at=(datetime.now(timezone.utc) - timedelta(minutes=7)).isoformat(),
        claimed_by=staff_member.id,
    )

    outcomes = await close_service.sweep_due_closing_tickets(now=datetime.now(timezone.utc))
    stored = ticket_repository.get_by_channel_id(channel.id)

    assert len(outcomes) == 1
    assert outcomes[0].final_status is TicketStatus.DONE
    assert stored is not None
    assert stored.status is TicketStatus.DONE
    assert stored.archive_message_id == archive_channel.sent_messages[0].id
    assert stored.message_count == 2
    assert stored.close_execute_at is None
    assert channel.deleted is True
    assert archive_channel.sent_messages
    assert archive_channel.sent_messages[0].embed.title == "🗂️ Ticket 归档记录"
    assert archive_channel.sent_messages[0].file is not None
    assert mute_repository.list_by_ticket(ticket.ticket_id) == []
    assert list((tmp_path / "exports").glob("*")) == []


@pytest.mark.asyncio
async def test_archive_service_is_idempotent_after_archive_sent_without_resending_transcript(
    prepared_close_context,
    tmp_path,
) -> None:
    close_service, archive_service, _, _ = build_close_service(prepared_close_context, tmp_path)
    ticket_repository = prepared_close_context["ticket_repository"]
    archive_channel = prepared_close_context["archive_channel"]

    second_ticket = ticket_repository.create(
        TicketRecord(
            ticket_id="1-support-0002",
            guild_id=1,
            creator_id=prepared_close_context["creator"].id,
            category_key="support",
            channel_id=9002,
            status=TicketStatus.ARCHIVE_SENT,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
            close_reason="重复恢复测试",
            closed_at="2024-01-01T02:00:00+00:00",
            archive_message_id=5555,
            archived_at="2024-01-01T02:01:00+00:00",
            message_count=1,
        )
    )
    exports_dir = tmp_path / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    (exports_dir / f"{second_ticket.ticket_id}-stale.html").write_text("stale", encoding="utf-8")

    outcome = await archive_service.archive_ticket(second_ticket.ticket_id)
    stored = ticket_repository.get_by_ticket_id(second_ticket.ticket_id)

    assert outcome is not None
    assert outcome.final_status is TicketStatus.DONE
    assert stored is not None
    assert stored.status is TicketStatus.DONE
    assert stored.archive_message_id == 5555
    assert archive_channel.sent_messages == []
    assert list(exports_dir.glob("*")) == []


@pytest.mark.asyncio
async def test_archive_service_keeps_archive_sent_when_channel_resolution_temporarily_fails(
    prepared_close_context,
    tmp_path,
) -> None:
    _, archive_service, _, bot = build_close_service(prepared_close_context, tmp_path)
    ticket_repository = prepared_close_context["ticket_repository"]
    channel = prepared_close_context["channel"]
    ticket = prepared_close_context["ticket"]

    ticket_repository.update(
        ticket.ticket_id,
        status=TicketStatus.ARCHIVE_SENT,
        archive_message_id=7777,
        archived_at="2024-01-01T02:01:00+00:00",
        message_count=3,
    )
    bot.channels.pop(channel.id)

    async def failing_fetch_channel(channel_id: int):
        raise RuntimeError(f"temporary fetch failure for {channel_id}")

    bot.fetch_channel = failing_fetch_channel

    outcome = await archive_service.archive_ticket(ticket.ticket_id)
    stored = ticket_repository.get_by_ticket_id(ticket.ticket_id)

    assert outcome is not None
    assert outcome.final_status is TicketStatus.ARCHIVE_SENT
    assert outcome.channel_deleted is False
    assert stored is not None and stored.status is TicketStatus.ARCHIVE_SENT
    assert channel.deleted is False


@pytest.mark.asyncio
async def test_archive_service_uses_snapshot_fallback_when_source_channel_is_missing(
    prepared_close_context,
    tmp_path,
) -> None:
    database = prepared_close_context["database"]
    ticket_repository = prepared_close_context["ticket_repository"]
    archive_channel = prepared_close_context["archive_channel"]
    ticket = prepared_close_context["ticket"]

    snapshot_store = SnapshotStore(file_store=TicketFileStore(tmp_path))
    snapshot_store.overwrite_records(
        ticket.ticket_id,
        [
            {
                "event": "create",
                "message_id": 1,
                "author_id": ticket.creator_id,
                "author_name": "creator",
                "timestamp": "2024-01-01T00:00:00+00:00",
                "content": "fallback create",
                "attachments": [],
            },
            {
                "event": "edit",
                "message_id": 1,
                "author_id": ticket.creator_id,
                "author_name": "creator",
                "timestamp": "2024-01-01T00:01:00+00:00",
                "old_content": "fallback create",
                "new_content": "fallback edited",
                "old_attachments": [],
                "new_attachments": [],
            },
        ],
    )

    updated_ticket = ticket_repository.update(ticket.ticket_id, status=TicketStatus.ARCHIVING) or ticket
    archive_service = ArchiveService(
        database,
        bot=FakeBot(archive_channel),
        lock_manager=LockManager(),
        render_service=ArchiveRenderService(
            exports_dir=tmp_path / "exports",
            snapshot_query_service=SnapshotQueryService(snapshot_store=snapshot_store),
        ),
        cleanup_service=CleanupService(database, storage_dir=tmp_path),
    )

    outcome = await archive_service.archive_ticket(updated_ticket.ticket_id)
    stored = ticket_repository.get_by_ticket_id(updated_ticket.ticket_id)

    assert outcome is not None
    assert outcome.final_status is TicketStatus.DONE
    assert archive_channel.sent_messages
    assert stored is not None
    assert stored.status is TicketStatus.DONE
    assert stored.archive_last_error is None
    assert stored.archive_message_id is not None


@pytest.mark.asyncio
async def test_archive_service_records_archive_failure_metadata_when_fallback_also_fails(
    prepared_close_context,
    tmp_path,
) -> None:
    database = prepared_close_context["database"]
    ticket_repository = prepared_close_context["ticket_repository"]
    archive_channel = prepared_close_context["archive_channel"]
    ticket = prepared_close_context["ticket"]

    ticket_repository.update(ticket.ticket_id, status=TicketStatus.ARCHIVING)
    archive_service = ArchiveService(
        database,
        bot=FakeBot(archive_channel),
        lock_manager=LockManager(),
        render_service=ArchiveRenderService(exports_dir=tmp_path / "exports"),
        cleanup_service=CleanupService(database, storage_dir=tmp_path),
    )

    outcome = await archive_service.archive_ticket(ticket.ticket_id)
    stored = ticket_repository.get_by_ticket_id(ticket.ticket_id)

    assert outcome is not None
    assert outcome.final_status is TicketStatus.ARCHIVE_FAILED
    assert stored is not None
    assert stored.status is TicketStatus.ARCHIVE_FAILED
    assert stored.archive_attempts == 1
    assert stored.archive_last_error is not None
    assert "fallback render failed" in stored.archive_last_error
