from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from core.enums import ClaimMode, TicketStatus
from core.errors import InvalidTicketStateError, PermissionDeniedError, ValidationError
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository
from runtime.locks import LockManager
from services.claim_service import ClaimService


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
        self.permission_calls: list[dict[str, object]] = []
        self.sent_messages: list[FakeMessage] = []

    async def set_permissions(self, target, *, overwrite, reason: str | None = None) -> None:
        self.permission_calls.append(
            {
                "target_id": getattr(target, "id", None),
                "overwrite": overwrite,
                "reason": reason,
            }
        )

    async def send(self, *, content: str | None = None, embed=None, view=None):
        message = FakeMessage(content or "")
        self.sent_messages.append(message)
        return message


class FakeStaffPanelService:
    def __init__(self) -> None:
        self.requested_ticket_ids: list[str] = []

    def request_refresh(self, ticket_id: str) -> None:
        self.requested_ticket_ids.append(ticket_id)


@pytest.fixture
def prepared_claim_context(migrated_database):
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
            staff_role_ids_json="[500]",
            staff_user_ids_json="[302]",
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
    explicit_staff_member = FakeMember(302)
    admin_member = FakeMember(401, roles=[admin_role])
    outsider = FakeMember(999)

    for member in (creator, staff_member, explicit_staff_member, admin_member, outsider):
        guild.add_member(member)

    ticket = ticket_repository.create(
        TicketRecord(
            ticket_id="1-support-0001",
            guild_id=1,
            creator_id=creator.id,
            category_key="support",
            channel_id=9001,
            status=TicketStatus.SUBMITTED,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
            has_user_message=True,
            last_user_message_at="2024-01-01T01:00:00+00:00",
        )
    )
    channel = FakeChannel(ticket.channel_id or 9001, guild, name="login-error")

    return {
        "database": migrated_database,
        "ticket_repository": ticket_repository,
        "guild_repository": guild_repository,
        "channel": channel,
        "ticket": ticket,
        "creator": creator,
        "staff_member": staff_member,
        "explicit_staff_member": explicit_staff_member,
        "admin_member": admin_member,
        "outsider": outsider,
    }


@pytest.mark.asyncio
async def test_claim_ticket_relaxed_mode_updates_claimed_by_and_posts_log(prepared_claim_context) -> None:
    database = prepared_claim_context["database"]
    channel = prepared_claim_context["channel"]
    staff_member = prepared_claim_context["staff_member"]
    staff_panel_service = FakeStaffPanelService()
    service = ClaimService(database, lock_manager=LockManager(), staff_panel_service=staff_panel_service)

    result = await service.claim_ticket(channel, actor=staff_member)

    stored = prepared_claim_context["ticket_repository"].get_by_channel_id(channel.id)

    assert result.changed is True
    assert result.strict_mode is False
    assert result.ticket.claimed_by == staff_member.id
    assert stored is not None
    assert stored.claimed_by == staff_member.id
    assert channel.sent_messages
    assert "已认领 ticket" in channel.sent_messages[0].content
    assert {call["target_id"] for call in channel.permission_calls} == {301, 400, 500, 302}
    assert staff_panel_service.requested_ticket_ids == [result.ticket.ticket_id]
    assert all(call["overwrite"].send_messages is True for call in channel.permission_calls)


@pytest.mark.asyncio
async def test_claim_ticket_strict_mode_recalculates_permissions_for_only_claimer(prepared_claim_context) -> None:
    database = prepared_claim_context["database"]
    channel = prepared_claim_context["channel"]
    staff_member = prepared_claim_context["staff_member"]
    guild_repository = prepared_claim_context["guild_repository"]
    service = ClaimService(database, lock_manager=LockManager())

    guild_repository.update_config(1, claim_mode=ClaimMode.STRICT)

    result = await service.claim_ticket(channel, actor=staff_member)

    assert result.changed is True
    assert result.strict_mode is True
    assert [call["target_id"] for call in channel.permission_calls] == [400, 500, 302, 301]
    assert [call["overwrite"].send_messages for call in channel.permission_calls] == [False, False, False, True]
    assert all(call["overwrite"].view_channel is True for call in channel.permission_calls)


@pytest.mark.asyncio
async def test_claim_ticket_rejects_non_staff_actor(prepared_claim_context) -> None:
    database = prepared_claim_context["database"]
    channel = prepared_claim_context["channel"]
    outsider = prepared_claim_context["outsider"]
    service = ClaimService(database, lock_manager=LockManager())

    with pytest.raises(PermissionDeniedError, match="只有当前分类 staff"):
        await service.claim_ticket(channel, actor=outsider)


@pytest.mark.asyncio
async def test_claim_ticket_rejects_non_submitted_ticket(prepared_claim_context) -> None:
    database = prepared_claim_context["database"]
    channel = prepared_claim_context["channel"]
    staff_member = prepared_claim_context["staff_member"]
    ticket_repository = prepared_claim_context["ticket_repository"]
    service = ClaimService(database, lock_manager=LockManager())

    ticket_repository.update(prepared_claim_context["ticket"].ticket_id, status=TicketStatus.DRAFT)

    with pytest.raises(InvalidTicketStateError, match="submitted"):
        await service.claim_ticket(channel, actor=staff_member)


@pytest.mark.asyncio
async def test_unclaim_ticket_requires_current_claimer_or_admin(prepared_claim_context) -> None:
    database = prepared_claim_context["database"]
    channel = prepared_claim_context["channel"]
    staff_member = prepared_claim_context["staff_member"]
    explicit_staff_member = prepared_claim_context["explicit_staff_member"]
    ticket_repository = prepared_claim_context["ticket_repository"]
    service = ClaimService(database, lock_manager=LockManager())

    ticket_repository.update(prepared_claim_context["ticket"].ticket_id, claimed_by=staff_member.id)

    with pytest.raises(PermissionDeniedError, match="只有当前认领者或 Ticket 管理员"):
        await service.unclaim_ticket(channel, actor=explicit_staff_member)


@pytest.mark.asyncio
async def test_unclaim_ticket_allows_admin_force_cancel_in_strict_mode(prepared_claim_context) -> None:
    database = prepared_claim_context["database"]
    channel = prepared_claim_context["channel"]
    staff_member = prepared_claim_context["staff_member"]
    admin_member = prepared_claim_context["admin_member"]
    guild_repository = prepared_claim_context["guild_repository"]
    ticket_repository = prepared_claim_context["ticket_repository"]
    staff_panel_service = FakeStaffPanelService()
    service = ClaimService(database, lock_manager=LockManager(), staff_panel_service=staff_panel_service)

    guild_repository.update_config(1, claim_mode=ClaimMode.STRICT)
    ticket_repository.update(prepared_claim_context["ticket"].ticket_id, claimed_by=staff_member.id)

    result = await service.unclaim_ticket(channel, actor=admin_member)
    stored = ticket_repository.get_by_channel_id(channel.id)

    assert result.changed is True
    assert result.forced is True
    assert result.previous_claimer_id == staff_member.id
    assert stored is not None
    assert stored.claimed_by is None
    assert [call["target_id"] for call in channel.permission_calls] == [400, 500, 302, 301]
    assert staff_panel_service.requested_ticket_ids == [result.ticket.ticket_id]
    assert all(call["overwrite"].send_messages is False for call in channel.permission_calls)
    assert "原认领者" in channel.sent_messages[0].content


@pytest.mark.asyncio
async def test_transfer_claim_allows_current_claimer_to_transfer_to_another_staff(prepared_claim_context) -> None:
    database = prepared_claim_context["database"]
    channel = prepared_claim_context["channel"]
    staff_member = prepared_claim_context["staff_member"]
    explicit_staff_member = prepared_claim_context["explicit_staff_member"]
    ticket_repository = prepared_claim_context["ticket_repository"]
    staff_panel_service = FakeStaffPanelService()
    service = ClaimService(database, lock_manager=LockManager(), staff_panel_service=staff_panel_service)

    ticket_repository.update(prepared_claim_context["ticket"].ticket_id, claimed_by=staff_member.id)

    result = await service.transfer_claim(channel, actor=staff_member, target=explicit_staff_member)
    stored = ticket_repository.get_by_channel_id(channel.id)

    assert result.changed is True
    assert result.forced is False
    assert result.previous_claimer_id == staff_member.id
    assert stored is not None
    assert stored.claimed_by == explicit_staff_member.id
    assert [call["target_id"] for call in channel.permission_calls] == [400, 500, 302, 301]
    assert all(call["overwrite"].send_messages is True for call in channel.permission_calls)
    assert channel.sent_messages
    assert "转交给" in channel.sent_messages[0].content
    assert f"<@{explicit_staff_member.id}>" in channel.sent_messages[0].content
    assert staff_panel_service.requested_ticket_ids == [result.ticket.ticket_id]


@pytest.mark.asyncio
async def test_transfer_claim_allows_admin_force_transfer_in_strict_mode(prepared_claim_context) -> None:
    database = prepared_claim_context["database"]
    channel = prepared_claim_context["channel"]
    staff_member = prepared_claim_context["staff_member"]
    explicit_staff_member = prepared_claim_context["explicit_staff_member"]
    admin_member = prepared_claim_context["admin_member"]
    guild_repository = prepared_claim_context["guild_repository"]
    ticket_repository = prepared_claim_context["ticket_repository"]
    service = ClaimService(database, lock_manager=LockManager())

    guild_repository.update_config(1, claim_mode=ClaimMode.STRICT)
    ticket_repository.update(prepared_claim_context["ticket"].ticket_id, claimed_by=staff_member.id)

    result = await service.transfer_claim(channel, actor=admin_member, target=explicit_staff_member)
    stored = ticket_repository.get_by_channel_id(channel.id)

    assert result.changed is True
    assert result.forced is True
    assert result.strict_mode is True
    assert stored is not None
    assert stored.claimed_by == explicit_staff_member.id
    assert [call["target_id"] for call in channel.permission_calls] == [400, 500, 302, 301, 302]
    assert [call["overwrite"].send_messages for call in channel.permission_calls] == [False, False, False, False, True]
    assert channel.sent_messages
    assert f"<@{admin_member.id}>" in channel.sent_messages[0].content


@pytest.mark.asyncio
async def test_transfer_claim_rejects_non_current_claimer_without_admin_override(prepared_claim_context) -> None:
    database = prepared_claim_context["database"]
    channel = prepared_claim_context["channel"]
    staff_member = prepared_claim_context["staff_member"]
    explicit_staff_member = prepared_claim_context["explicit_staff_member"]
    ticket_repository = prepared_claim_context["ticket_repository"]
    service = ClaimService(database, lock_manager=LockManager())

    ticket_repository.update(prepared_claim_context["ticket"].ticket_id, claimed_by=staff_member.id)

    with pytest.raises(PermissionDeniedError, match="只有当前认领者或 Ticket 管理员"):
        await service.transfer_claim(channel, actor=explicit_staff_member, target=staff_member)


@pytest.mark.asyncio
async def test_transfer_claim_rejects_target_that_is_not_current_category_staff(prepared_claim_context) -> None:
    database = prepared_claim_context["database"]
    channel = prepared_claim_context["channel"]
    staff_member = prepared_claim_context["staff_member"]
    outsider = prepared_claim_context["outsider"]
    ticket_repository = prepared_claim_context["ticket_repository"]
    service = ClaimService(database, lock_manager=LockManager())

    ticket_repository.update(prepared_claim_context["ticket"].ticket_id, claimed_by=staff_member.id)

    with pytest.raises(ValidationError, match="合法 staff"):
        await service.transfer_claim(channel, actor=staff_member, target=outsider)
