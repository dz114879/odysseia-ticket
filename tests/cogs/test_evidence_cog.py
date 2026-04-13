from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from cogs.evidence_cog import EvidenceCog
from core.enums import ClaimMode, TicketStatus
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository
from runtime.locks import LockManager
from services.notes_service import NotesService
from services.snapshot_query_service import SnapshotQueryService
from storage.file_store import TicketFileStore
from storage.notes_store import NotesStore
from storage.snapshot_store import SnapshotStore


@dataclass(frozen=True)
class FakeRole:
    id: int


@dataclass
class FakeUser:
    id: int
    display_name: str
    roles: list[FakeRole] = field(default_factory=list)
    administrator: bool = False
    bot: bool = False

    @property
    def guild_permissions(self) -> SimpleNamespace:
        return SimpleNamespace(administrator=self.administrator)


@dataclass(frozen=True)
class FakeChannel:
    id: int


class FakeResponse:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.deferred: list[dict[str, object]] = []
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def send_message(self, content: str, *, ephemeral: bool) -> None:
        self._done = True
        self.messages.append({"content": content, "ephemeral": ephemeral})

    async def defer(self, *, ephemeral: bool, thinking: bool) -> None:
        self._done = True
        self.deferred.append({"ephemeral": ephemeral, "thinking": thinking})


class FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send(self, content: str, *, ephemeral: bool, file=None) -> None:
        self.messages.append({"content": content, "ephemeral": ephemeral, "file": file})


class FakeLoggingService:
    def __init__(self) -> None:
        self.info_messages: list[str] = []

    def log_local_info(self, message: str, *args) -> None:
        self.info_messages.append(message % args if args else message)

    async def send_guild_log(self, *args, **kwargs) -> bool:
        return False

    async def send_ticket_log(self, *args, **kwargs) -> bool:
        return False


class FakeBot:
    def __init__(self, resources, *, is_owner_result: bool = False) -> None:
        self.resources = resources
        self._is_owner_result = is_owner_result

    async def is_owner(self, user) -> bool:
        del user
        return self._is_owner_result


class FakeInteraction:
    def __init__(self, *, guild, channel, user, bot) -> None:
        self.guild = guild
        self.channel = channel
        self.user = user
        self.client = bot
        self.response = FakeResponse()
        self.followup = FakeFollowup()


def read_uploaded_text(uploaded_file) -> str:
    uploaded_file.fp.seek(0)
    payload = uploaded_file.fp.read()
    if isinstance(payload, bytes):
        return payload.decode("utf-8")
    return str(payload)


@pytest.fixture
def prepared_evidence_cog_context(migrated_database, tmp_path):
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
            staff_role_ids_json='[500]',
            staff_user_ids_json="[]",
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
            claimed_by=301,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
            has_user_message=True,
            last_user_message_at="2024-01-01T01:00:00+00:00",
        )
    )

    file_store = TicketFileStore(tmp_path)
    snapshot_store = SnapshotStore(file_store=file_store)
    notes_store = NotesStore(file_store=file_store)
    snapshot_store.overwrite_records(
        ticket.ticket_id,
        [
            {
                "event": "create",
                "message_id": 101,
                "author_id": 201,
                "author_name": "creator",
                "timestamp": "2024-01-01T00:00:00+00:00",
                "content": "原始消息",
                "attachments": [],
            },
            {
                "event": "edit",
                "message_id": 101,
                "author_id": 201,
                "author_name": "creator",
                "timestamp": "2024-01-01T00:02:00+00:00",
                "old_content": "原始消息",
                "new_content": "原始消息（已编辑）",
                "old_attachments": [],
                "new_attachments": [],
            },
            {
                "event": "create",
                "message_id": 102,
                "author_id": 201,
                "author_name": "creator",
                "timestamp": "2024-01-01T00:03:00+00:00",
                "content": "已经删掉的消息",
                "attachments": [],
            },
            {
                "event": "delete",
                "message_id": 102,
                "author_id": 201,
                "author_name": "creator",
                "timestamp": "2024-01-01T00:04:00+00:00",
                "deleted_content": "已经删掉的消息",
                "deleted_attachments": [],
            },
        ],
    )

    resources = SimpleNamespace(
        database=migrated_database,
        logging_service=FakeLoggingService(),
        snapshot_query_service=SnapshotQueryService(snapshot_store=snapshot_store),
        notes_service=NotesService(notes_store=notes_store, lock_manager=LockManager()),
    )
    staff_role = FakeRole(500)

    return {
        "bot": FakeBot(resources),
        "guild": SimpleNamespace(id=1),
        "channel": FakeChannel(ticket.channel_id or 0),
        "creator": FakeUser(201, "creator"),
        "staff_user": FakeUser(301, "helper", roles=[staff_role]),
        "outsider": FakeUser(999, "outsider"),
        "ticket": ticket,
    }


@pytest.mark.asyncio
async def test_show_message_history_returns_timeline_text_for_creator(
    prepared_evidence_cog_context,
) -> None:
    bot = prepared_evidence_cog_context["bot"]
    cog = EvidenceCog(bot)
    interaction = FakeInteraction(
        guild=prepared_evidence_cog_context["guild"],
        channel=prepared_evidence_cog_context["channel"],
        user=prepared_evidence_cog_context["creator"],
        bot=bot,
    )

    await cog.show_message_history(interaction, message_id=101)

    assert interaction.response.deferred == [{"ephemeral": True, "thinking": True}]
    assert interaction.followup.messages
    payload = interaction.followup.messages[0]
    assert payload["ephemeral"] is True
    assert payload["file"] is None
    assert "Ticket `1-support-0001` | Message `101`" in str(payload["content"])
    assert "[edit] 2024-01-01T00:02:00+00:00" in str(payload["content"])


@pytest.mark.asyncio
async def test_show_recycle_bin_prefers_file_payload(prepared_evidence_cog_context) -> None:
    bot = prepared_evidence_cog_context["bot"]
    cog = EvidenceCog(bot)
    interaction = FakeInteraction(
        guild=prepared_evidence_cog_context["guild"],
        channel=prepared_evidence_cog_context["channel"],
        user=prepared_evidence_cog_context["creator"],
        bot=bot,
    )

    await cog.show_recycle_bin(interaction)

    assert interaction.response.deferred == [{"ephemeral": True, "thinking": True}]
    payload = interaction.followup.messages[0]
    assert payload["content"] == "已生成本 ticket 内所有被删除消息快照，请尽快下载"
    assert payload["ephemeral"] is True
    assert payload["file"] is not None
    assert payload["file"].filename == "1-support-0001-recycle-bin.txt"
    assert "删除前最后内容：已经删掉的消息" in read_uploaded_text(payload["file"])


@pytest.mark.asyncio
async def test_add_note_rejects_creator_without_staff_permission(prepared_evidence_cog_context) -> None:
    bot = prepared_evidence_cog_context["bot"]
    cog = EvidenceCog(bot)
    interaction = FakeInteraction(
        guild=prepared_evidence_cog_context["guild"],
        channel=prepared_evidence_cog_context["channel"],
        user=prepared_evidence_cog_context["creator"],
        bot=bot,
    )

    await cog.add_note(interaction, content="内部备注")

    assert interaction.response.deferred == []
    assert interaction.response.messages == [
        {
            "content": "只有当前分类 staff、Ticket 管理员或 Bot 所有者可以执行此操作。",
            "ephemeral": True,
        }
    ]
    assert interaction.followup.messages == []


@pytest.mark.asyncio
async def test_add_note_and_check_notes_for_staff(prepared_evidence_cog_context) -> None:
    bot = prepared_evidence_cog_context["bot"]
    cog = EvidenceCog(bot)
    add_interaction = FakeInteraction(
        guild=prepared_evidence_cog_context["guild"],
        channel=prepared_evidence_cog_context["channel"],
        user=prepared_evidence_cog_context["staff_user"],
        bot=bot,
    )

    await cog.add_note(add_interaction, content="  需要继续观察  ")

    assert add_interaction.response.deferred == [{"ephemeral": True, "thinking": True}]
    assert add_interaction.followup.messages == [
        {
            "content": "已为 ticket `1-support-0001` 新增内部备注（当前共 1 条）。",
            "ephemeral": True,
            "file": None,
        }
    ]

    check_interaction = FakeInteraction(
        guild=prepared_evidence_cog_context["guild"],
        channel=prepared_evidence_cog_context["channel"],
        user=prepared_evidence_cog_context["staff_user"],
        bot=bot,
    )

    await cog.check_notes(check_interaction)

    assert check_interaction.response.deferred == [{"ephemeral": True, "thinking": True}]
    payload = check_interaction.followup.messages[0]
    assert payload["ephemeral"] is True
    assert payload["file"] is None
    assert "Ticket `1-support-0001` 内部备注（共 1 条）" in str(payload["content"])
    assert "helper (301) ⭐" in str(payload["content"])
    assert "需要继续观察" in str(payload["content"])
