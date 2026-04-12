from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import Mock

import discord
import pytest

from core.enums import ClaimMode, TicketStatus
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository
from runtime.locks import LockManager
from services.capacity_service import CapacityService
from services.queue_service import QueueService


@dataclass(frozen=True)
class FakeRole:
    id: int


@dataclass
class FakeMember:
    id: int
    bot: bool = False


class FakeMessage:
    def __init__(self, message_id: int, *, content: str | None = None, embed: discord.Embed | None = None, view=None) -> None:
        self.id = message_id
        self.content = content
        self.embed = embed
        self.view = view

    async def edit(self, *, view=None) -> None:
        self.view = view


def make_discord_http_exception(
    exception_type: type[discord.HTTPException],
    *,
    status: int,
    reason: str,
    message: str,
) -> discord.HTTPException:
    response = Mock(status=status, reason=reason, headers={})
    return exception_type(response, message)


class FakeGuild:
    def __init__(self, guild_id: int) -> None:
        self.id = guild_id
        self.roles: dict[int, FakeRole] = {}
        self.members: dict[int, FakeMember] = {}
        self.fetched_members: dict[int, FakeMember] = {}
        self.member_fetch_errors: dict[int, Exception] = {}

    def add_role(self, role: FakeRole) -> None:
        self.roles[role.id] = role

    def add_member(self, member: FakeMember) -> None:
        self.members[member.id] = member

    def get_role(self, role_id: int) -> FakeRole | None:
        return self.roles.get(role_id)

    def get_member(self, member_id: int) -> FakeMember | None:
        return self.members.get(member_id)

    def set_fetched_member(self, member: FakeMember) -> None:
        self.fetched_members[member.id] = member

    def set_member_fetch_error(self, member_id: int, error: Exception) -> None:
        self.member_fetch_errors[member_id] = error

    async def fetch_member(self, member_id: int) -> FakeMember:
        error = self.member_fetch_errors.get(member_id)
        if error is not None:
            raise error
        fetched_member = self.fetched_members.get(member_id)
        if fetched_member is not None:
            return fetched_member
        cached_member = self.members.get(member_id)
        if cached_member is not None:
            return cached_member
        raise make_discord_http_exception(discord.NotFound, status=404, reason="Not Found", message="Unknown Member")


class FakeChannel:
    def __init__(self, channel_id: int, guild: FakeGuild, *, name: str) -> None:
        self.id = channel_id
        self.guild = guild
        self.name = name
        self.next_message_id = 1000
        self.sent_messages: list[FakeMessage] = []
        self.permission_calls: list[dict] = []
        self.deleted = False

    async def edit(self, *, name=None, reason=None, **kwargs) -> None:
        if name is not None:
            self.name = name

    async def send(self, *, content=None, embed=None, view=None) -> FakeMessage:
        message = FakeMessage(self.next_message_id, content=content, embed=embed, view=view)
        self.next_message_id += 1
        self.sent_messages.append(message)
        return message

    async def set_permissions(self, target, *, overwrite, reason) -> None:
        self.permission_calls.append({"target": target, "overwrite": overwrite, "reason": reason})

    async def pins(self) -> list[FakeMessage]:
        return []

    async def delete(self, *, reason: str | None = None) -> None:
        self.deleted = True


class FakeBot:
    def __init__(
        self,
        channels: dict[int, FakeChannel],
        *,
        cached_channel_ids: set[int] | None = None,
        fetch_errors: dict[int, Exception] | None = None,
    ) -> None:
        self.channels = channels
        self.cached_channel_ids = set(channels) if cached_channel_ids is None else set(cached_channel_ids)
        self.fetch_errors = fetch_errors or {}

    def get_channel(self, channel_id: int):
        if channel_id not in self.cached_channel_ids:
            return None
        return self.channels.get(channel_id)

    async def fetch_channel(self, channel_id: int):
        error = self.fetch_errors.get(channel_id)
        if error is not None:
            raise error
        channel = self.channels.get(channel_id)
        if channel is None:
            raise make_discord_http_exception(discord.NotFound, status=404, reason="Not Found", message="Unknown Channel")
        return channel


@pytest.fixture
def prepared_queue_context(migrated_database):
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
            max_open_tickets=1,
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
            staff_user_ids_json="[301]",
            is_enabled=True,
            allowlist_role_ids_json="[]",
            denylist_role_ids_json="[]",
            sort_order=1,
        )
    )

    guild = FakeGuild(1)
    guild.add_role(FakeRole(400))
    guild.add_role(FakeRole(500))
    guild.add_member(FakeMember(201))
    guild.add_member(FakeMember(202))
    guild.add_member(FakeMember(301))

    first_channel = FakeChannel(9001, guild, name="first")
    second_channel = FakeChannel(9002, guild, name="second")
    ticket_repository.create(
        TicketRecord(
            ticket_id="1-support-0001",
            guild_id=1,
            creator_id=201,
            category_key="support",
            channel_id=9001,
            status=TicketStatus.QUEUED,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
            has_user_message=True,
            queued_at="2024-01-01T01:00:00+00:00",
        )
    )
    ticket_repository.create(
        TicketRecord(
            ticket_id="1-support-0002",
            guild_id=1,
            creator_id=202,
            category_key="support",
            channel_id=9002,
            status=TicketStatus.QUEUED,
            created_at="2024-01-01T00:01:00+00:00",
            updated_at="2024-01-01T00:01:00+00:00",
            has_user_message=True,
            queued_at="2024-01-01T01:05:00+00:00",
        )
    )

    return {
        "database": migrated_database,
        "ticket_repository": ticket_repository,
        "channels": {9001: first_channel, 9002: second_channel},
    }


@pytest.mark.asyncio
async def test_process_next_queued_ticket_promotes_fifo_ticket_when_capacity_is_available(prepared_queue_context) -> None:
    database = prepared_queue_context["database"]
    channels = prepared_queue_context["channels"]
    repository = prepared_queue_context["ticket_repository"]
    service = QueueService(
        database,
        bot=FakeBot(channels),
        capacity_service=CapacityService(database),
        lock_manager=LockManager(),
    )

    outcome = await service.process_next_queued_ticket(1)

    first_ticket = repository.get_by_ticket_id("1-support-0001")
    second_ticket = repository.get_by_ticket_id("1-support-0002")
    assert outcome is not None
    assert outcome.action == "promoted"
    assert outcome.ticket.ticket_id == "1-support-0001"
    assert first_ticket is not None and first_ticket.status is TicketStatus.SUBMITTED
    assert first_ticket.queued_at is None
    assert second_ticket is not None and second_ticket.status is TicketStatus.QUEUED
    assert channels[9001].sent_messages
    assert {call["target"].id for call in channels[9001].permission_calls} == {400, 500, 301}


@pytest.mark.asyncio
async def test_process_next_queued_ticket_abandons_missing_channel_and_keeps_sweeping(prepared_queue_context) -> None:
    database = prepared_queue_context["database"]
    repository = prepared_queue_context["ticket_repository"]
    channels = {9002: prepared_queue_context["channels"][9002]}
    service = QueueService(
        database,
        bot=FakeBot(channels),
        capacity_service=CapacityService(database),
        lock_manager=LockManager(),
    )

    outcome = await service.process_next_queued_ticket(1)

    first_ticket = repository.get_by_ticket_id("1-support-0001")
    second_ticket = repository.get_by_ticket_id("1-support-0002")
    assert outcome is not None
    assert outcome.action == "promoted"
    assert outcome.ticket.ticket_id == "1-support-0002"
    assert first_ticket is not None and first_ticket.status is TicketStatus.ABANDONED
    assert second_ticket is not None and second_ticket.status is TicketStatus.SUBMITTED


@pytest.mark.asyncio
async def test_process_next_queued_ticket_defers_when_channel_fetch_hits_temporary_error(prepared_queue_context) -> None:
    database = prepared_queue_context["database"]
    repository = prepared_queue_context["ticket_repository"]
    channels = prepared_queue_context["channels"]
    temporary_error = make_discord_http_exception(
        discord.HTTPException,
        status=503,
        reason="Service Unavailable",
        message="temporary outage",
    )
    service = QueueService(
        database,
        bot=FakeBot(
            channels,
            cached_channel_ids={9002},
            fetch_errors={9001: temporary_error},
        ),
        capacity_service=CapacityService(database),
        lock_manager=LockManager(),
    )

    outcome = await service.process_next_queued_ticket(1)

    first_ticket = repository.get_by_ticket_id("1-support-0001")
    second_ticket = repository.get_by_ticket_id("1-support-0002")
    assert outcome is None
    assert first_ticket is not None and first_ticket.status is TicketStatus.QUEUED
    assert second_ticket is not None and second_ticket.status is TicketStatus.QUEUED
    assert not channels[9002].sent_messages


@pytest.mark.asyncio
async def test_process_next_queued_ticket_fetches_creator_when_member_is_not_cached(prepared_queue_context) -> None:
    database = prepared_queue_context["database"]
    repository = prepared_queue_context["ticket_repository"]
    channels = prepared_queue_context["channels"]
    guild = channels[9001].guild
    creator = guild.members.pop(201)
    guild.set_fetched_member(creator)
    service = QueueService(
        database,
        bot=FakeBot(channels),
        capacity_service=CapacityService(database),
        lock_manager=LockManager(),
    )

    outcome = await service.process_next_queued_ticket(1)

    first_ticket = repository.get_by_ticket_id("1-support-0001")
    assert outcome is not None
    assert outcome.action == "promoted"
    assert outcome.ticket.ticket_id == "1-support-0001"
    assert first_ticket is not None and first_ticket.status is TicketStatus.SUBMITTED


@pytest.mark.asyncio
async def test_process_next_queued_ticket_defers_when_creator_fetch_hits_temporary_error(prepared_queue_context) -> None:
    database = prepared_queue_context["database"]
    repository = prepared_queue_context["ticket_repository"]
    channels = prepared_queue_context["channels"]
    guild = channels[9001].guild
    guild.members.pop(201)
    guild.set_member_fetch_error(
        201,
        make_discord_http_exception(discord.HTTPException, status=503, reason="Service Unavailable", message="temporary outage"),
    )
    service = QueueService(
        database,
        bot=FakeBot(channels),
        capacity_service=CapacityService(database),
        lock_manager=LockManager(),
    )

    outcome = await service.process_next_queued_ticket(1)

    first_ticket = repository.get_by_ticket_id("1-support-0001")
    second_ticket = repository.get_by_ticket_id("1-support-0002")
    assert outcome is None
    assert first_ticket is not None and first_ticket.status is TicketStatus.QUEUED
    assert second_ticket is not None and second_ticket.status is TicketStatus.QUEUED
    assert channels[9001].deleted is False
    assert not channels[9002].sent_messages
