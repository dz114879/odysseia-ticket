from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from core.enums import ClaimMode, TicketStatus
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository
from runtime.locks import LockManager
from services.close_request_service import CloseRequestService
from services.close_service import CloseService


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


class FakeMessage:
    def __init__(self, message_id: int, *, content=None, embed=None, view=None) -> None:
        self.id = message_id
        self.content = content
        self.embed = embed
        self.view = view
        self.edit_calls: list[dict[str, object]] = []

    async def edit(self, *, content=None, embed=None, view=None) -> None:
        if content is not None:
            self.content = content
        if embed is not None:
            self.embed = embed
        self.view = view
        self.edit_calls.append({"content": content, "embed": embed, "view": view})


class FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id
        self.roles: dict[int, FakeRole] = {}
        self.members: dict[int, FakeMember] = {}

    def add_role(self, role: FakeRole) -> None:
        self.roles[role.id] = role

    def add_member(self, member: FakeMember) -> None:
        self.members[member.id] = member

    def get_role(self, role_id: int) -> FakeRole | None:
        return self.roles.get(role_id)

    def get_member(self, member_id: int) -> FakeMember | None:
        return self.members.get(member_id)


class FakeChannel:
    def __init__(self, channel_id: int, guild: FakeGuild, *, name: str) -> None:
        self.id = channel_id
        self.guild = guild
        self.name = name
        self.next_message_id = 1000
        self.sent_messages: list[FakeMessage] = []
        self.permission_calls: list[dict[str, object]] = []
        self._messages: dict[int, FakeMessage] = {}

    async def send(self, *, content=None, embed=None, view=None, file=None):
        del file
        message = FakeMessage(self.next_message_id, content=content, embed=embed, view=view)
        self.next_message_id += 1
        self.sent_messages.append(message)
        self._messages[message.id] = message
        return message

    async def set_permissions(self, target, *, overwrite, reason: str | None = None) -> None:
        self.permission_calls.append({"target_id": getattr(target, "id", None), "overwrite": overwrite, "reason": reason})

    async def fetch_message(self, message_id: int) -> FakeMessage:
        return self._messages[message_id]


@pytest.fixture
def prepared_close_request_context(migrated_database):
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

    creator = FakeMember(201)
    staff_member = FakeMember(301, roles=[staff_role])
    outsider = FakeMember(999)
    for member in (creator, staff_member, outsider):
        guild.add_member(member)

    channel = FakeChannel(9001, guild, name="login-error")
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
        )
    )

    return {
        "database": migrated_database,
        "ticket_repository": ticket_repository,
        "ticket": ticket,
        "guild": guild,
        "channel": channel,
        "creator": creator,
        "staff_member": staff_member,
        "outsider": outsider,
    }


def build_request_service(context):
    close_service = CloseService(
        context["database"],
        lock_manager=LockManager(),
    )
    return CloseRequestService(
        context["database"],
        close_service=close_service,
    )


@pytest.mark.asyncio
async def test_request_close_creates_pending_message_with_buttons(prepared_close_request_context) -> None:
    service = build_request_service(prepared_close_request_context)
    channel = prepared_close_request_context["channel"]
    creator = prepared_close_request_context["creator"]

    result = await service.request_close(channel, actor=creator, reason="已解决")

    assert result.request_message is not None
    assert result.replaced_message_id is None
    assert result.request_message.embed.title == "📩 归档并关闭请求"
    assert result.request_message.view is not None
    assert service._pending_request_message_ids[channel.id] == result.request_message.id


@pytest.mark.asyncio
async def test_request_close_replaces_previous_pending_request(prepared_close_request_context) -> None:
    service = build_request_service(prepared_close_request_context)
    channel = prepared_close_request_context["channel"]
    creator = prepared_close_request_context["creator"]

    first = await service.request_close(channel, actor=creator, reason="第一次请求")
    second = await service.request_close(channel, actor=creator, reason="第二次请求")

    assert second.replaced_message_id == first.request_message.id
    assert first.request_message.edit_calls
    assert "被新的请求替换" in first.request_message.edit_calls[-1]["embed"].description
    assert first.request_message.view is None
    assert service._pending_request_message_ids[channel.id] == second.request_message.id


@pytest.mark.asyncio
async def test_approve_request_starts_closing_and_clears_pending_state(prepared_close_request_context) -> None:
    service = build_request_service(prepared_close_request_context)
    channel = prepared_close_request_context["channel"]
    creator = prepared_close_request_context["creator"]
    staff_member = prepared_close_request_context["staff_member"]

    request = await service.request_close(channel, actor=creator, reason="可以关单了")
    result = await service.approve_request(
        channel,
        actor=staff_member,
        request_message=request.request_message,
        requester_id=creator.id,
        reason=request.reason,
    )
    stored = prepared_close_request_context["ticket_repository"].get_by_channel_id(channel.id)

    assert result.changed is True
    assert stored is not None
    assert stored.status is TicketStatus.CLOSING
    assert channel.id not in service._pending_request_message_ids
    assert request.request_message.edit_calls
    assert "同意" in request.request_message.edit_calls[-1]["embed"].description


@pytest.mark.asyncio
async def test_reject_request_clears_pending_state_and_posts_public_notice(prepared_close_request_context) -> None:
    service = build_request_service(prepared_close_request_context)
    channel = prepared_close_request_context["channel"]
    creator = prepared_close_request_context["creator"]
    staff_member = prepared_close_request_context["staff_member"]

    request = await service.request_close(channel, actor=creator, reason="无需继续跟进")
    await service.reject_request(
        channel,
        actor=staff_member,
        request_message=request.request_message,
        requester_id=creator.id,
        reason=request.reason,
    )

    stored = prepared_close_request_context["ticket_repository"].get_by_channel_id(channel.id)
    assert stored is not None and stored.status is TicketStatus.SUBMITTED
    assert channel.id not in service._pending_request_message_ids
    assert request.request_message.edit_calls
    assert "拒绝" in request.request_message.edit_calls[-1]["embed"].description
    assert channel.sent_messages[-1].content is not None
    assert "已拒绝" in channel.sent_messages[-1].content
