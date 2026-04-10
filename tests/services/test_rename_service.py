from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from core.enums import ClaimMode, TicketPriority, TicketStatus
from core.errors import InvalidTicketStateError, PermissionDeniedError, ValidationError
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository
from runtime.locks import LockManager
from services.rename_service import RenameService


@dataclass(frozen=True)
class FakeRole:
    id: int


@dataclass
class FakeMember:
    id: int
    roles: list[FakeRole] = field(default_factory=list)
    administrator: bool = False

    @property
    def guild_permissions(self) -> SimpleNamespace:
        return SimpleNamespace(administrator=self.administrator)


@dataclass
class FakeMessage:
    content: str


class FakeChannel:
    def __init__(self, channel_id: int, *, name: str, fail_edit: bool = False) -> None:
        self.id = channel_id
        self.name = name
        self.fail_edit = fail_edit
        self.edit_calls: list[dict[str, str | None]] = []
        self.sent_messages: list[FakeMessage] = []

    async def edit(self, *, name: str, reason: str | None = None) -> None:
        if self.fail_edit:
            raise RuntimeError("channel rename failed")
        self.edit_calls.append({"name": name, "reason": reason})
        self.name = name

    async def send(self, *, content: str | None = None, embed=None, view=None):
        message = FakeMessage(content or "")
        self.sent_messages.append(message)
        return message


@pytest.fixture
def prepared_rename_context(migrated_database):
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
            emoji="🛠️",
            description="处理技术问题",
            staff_role_id=500,
            staff_user_ids_json="[]",
            extra_welcome_text="请说明具体错误。",
            is_enabled=True,
            allowlist_role_ids_json="[]",
            denylist_role_ids_json="[]",
            sort_order=1,
        )
    )

    staff_role = FakeRole(500)
    staff_member = FakeMember(301, roles=[staff_role])
    outsider = FakeMember(999)

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
            has_user_message=True,
            last_user_message_at="2024-01-01T01:00:00+00:00",
            priority=TicketPriority.HIGH,
        )
    )

    return {
        "database": migrated_database,
        "ticket_repository": ticket_repository,
        "ticket": ticket,
        "channel": FakeChannel(ticket.channel_id or 9001, name="🔴|ticket-0001-login-error"),
        "staff_member": staff_member,
        "outsider": outsider,
    }


@pytest.mark.asyncio
async def test_rename_ticket_updates_submitted_ticket_and_posts_log(prepared_rename_context) -> None:
    database = prepared_rename_context["database"]
    channel = prepared_rename_context["channel"]
    staff_member = prepared_rename_context["staff_member"]
    service = RenameService(database, lock_manager=LockManager())

    result = await service.rename_ticket(channel, actor=staff_member, requested_name="支付 异常")
    stored = prepared_rename_context["ticket_repository"].get_by_channel_id(channel.id)

    assert result.changed is True
    assert result.old_name == "🔴|ticket-0001-login-error"
    assert result.new_name == "🔴|ticket-0001-支付-异常"
    assert channel.name == "🔴|ticket-0001-支付-异常"
    assert channel.edit_calls[0]["reason"] == "Rename ticket 1-support-0001 in submitted state"
    assert stored is not None
    assert stored.updated_at != "2024-01-01T00:00:00+00:00"
    assert stored.status is TicketStatus.SUBMITTED
    assert channel.sent_messages
    assert "已修改 ticket" in channel.sent_messages[0].content


@pytest.mark.asyncio
async def test_rename_ticket_updates_sleep_ticket_and_preserves_sleep_prefix(prepared_rename_context) -> None:
    database = prepared_rename_context["database"]
    ticket_repository = prepared_rename_context["ticket_repository"]
    channel = prepared_rename_context["channel"]
    staff_member = prepared_rename_context["staff_member"]
    service = RenameService(database, lock_manager=LockManager())

    ticket_repository.update(
        prepared_rename_context["ticket"].ticket_id,
        status=TicketStatus.SLEEP,
        priority=TicketPriority.SLEEP,
        priority_before_sleep=TicketPriority.HIGH,
    )
    channel.name = "💤|ticket-0001-login-error"

    result = await service.rename_ticket(channel, actor=staff_member, requested_name="等待 用户 回复")

    assert result.changed is True
    assert result.new_name == "💤|ticket-0001-等待-用户-回复"
    assert channel.name == "💤|ticket-0001-等待-用户-回复"
    assert channel.edit_calls[0]["reason"] == "Rename ticket 1-support-0001 in sleep state"


@pytest.mark.asyncio
async def test_rename_ticket_rejects_non_staff_actor(prepared_rename_context) -> None:
    database = prepared_rename_context["database"]
    channel = prepared_rename_context["channel"]
    outsider = prepared_rename_context["outsider"]
    service = RenameService(database, lock_manager=LockManager())

    with pytest.raises(PermissionDeniedError, match="只有当前分类 staff"):
        await service.rename_ticket(channel, actor=outsider, requested_name="非法修改")


@pytest.mark.asyncio
async def test_rename_ticket_rejects_non_submitted_or_sleep_ticket(prepared_rename_context) -> None:
    database = prepared_rename_context["database"]
    channel = prepared_rename_context["channel"]
    staff_member = prepared_rename_context["staff_member"]
    ticket_repository = prepared_rename_context["ticket_repository"]
    service = RenameService(database, lock_manager=LockManager())

    ticket_repository.update(prepared_rename_context["ticket"].ticket_id, status=TicketStatus.TRANSFERRING)

    with pytest.raises(InvalidTicketStateError, match="submitted / sleep"):
        await service.rename_ticket(channel, actor=staff_member, requested_name="转交中改名")


@pytest.mark.asyncio
async def test_rename_ticket_rejects_invalid_requested_name(prepared_rename_context) -> None:
    database = prepared_rename_context["database"]
    channel = prepared_rename_context["channel"]
    staff_member = prepared_rename_context["staff_member"]
    service = RenameService(database, lock_manager=LockManager())

    with pytest.raises(ValidationError, match="不能为空"):
        await service.rename_ticket(channel, actor=staff_member, requested_name="  🔴  ")


@pytest.mark.asyncio
async def test_rename_ticket_is_noop_when_new_title_matches_current_name(prepared_rename_context) -> None:
    database = prepared_rename_context["database"]
    channel = prepared_rename_context["channel"]
    staff_member = prepared_rename_context["staff_member"]
    service = RenameService(database, lock_manager=LockManager())

    result = await service.rename_ticket(channel, actor=staff_member, requested_name="login error")

    assert result.changed is False
    assert result.new_name == "🔴|ticket-0001-login-error"
    assert channel.edit_calls == []
    assert channel.sent_messages == []
