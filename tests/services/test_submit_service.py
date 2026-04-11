from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest

from core.enums import ClaimMode, TicketStatus
from core.errors import PermissionDeniedError, ValidationError
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository
from discord_ui.staff_panel_view import StaffPanelView
from services.snapshot_service import SnapshotBootstrapResult
from services.capacity_service import CapacityService
from services.queue_service import QueueService
from services.submit_service import SubmitService


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeMember:
    def __init__(self, member_id: int) -> None:
        self.id = member_id
        self.bot = False


class FakeMessage:
    def __init__(
        self,
        message_id: int,
        *,
        content: str | None = None,
        embed: discord.Embed | None = None,
        view=None,
        pinned: bool = False,
    ) -> None:
        self.id = message_id
        self.content = content
        self.embed = embed
        self.view = view
        self.pinned = pinned
        self.edit_calls: list[dict] = []

    async def edit(self, *, view=None) -> None:
        self.view = view
        self.edit_calls.append({"view": view})


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
    def __init__(self, channel_id: int, guild: FakeGuild, *, name: str, topic: str) -> None:
        self.id = channel_id
        self.guild = guild
        self.name = name
        self.topic = topic
        self.next_message_id = 1000
        self.sent_messages: list[FakeMessage] = []
        self.pinned_messages: list[FakeMessage] = []
        self.edit_calls: list[dict] = []
        self.permission_calls: list[dict] = []

    async def edit(self, *, name=None, topic=None, reason=None) -> None:
        if name is not None:
            self.name = name
        if topic is not None:
            self.topic = topic
        self.edit_calls.append({"name": name, "topic": topic, "reason": reason})

    async def send(self, *, content=None, embed=None, view=None) -> FakeMessage:
        message = FakeMessage(
            self.next_message_id,
            content=content,
            embed=embed,
            view=view,
        )
        self.next_message_id += 1
        self.sent_messages.append(message)
        return message

    async def set_permissions(self, target, *, overwrite, reason) -> None:
        self.permission_calls.append(
            {
                "target": target,
                "overwrite": overwrite,
                "reason": reason,
            }
        )

    async def pins(self) -> list[FakeMessage]:
        return list(self.pinned_messages)


class FakeSnapshotService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def bootstrap_from_channel_history(self, ticket: TicketRecord, channel: FakeChannel) -> SnapshotBootstrapResult:
        self.calls.append({"ticket": ticket, "channel": channel})
        bootstrapped_ticket = (
            TicketRepository(self.database).update(
                ticket.ticket_id,
                snapshot_bootstrapped_at="2024-01-01T01:10:00+00:00",
                message_count=1,
            )
            or ticket
        )
        return SnapshotBootstrapResult(ticket=bootstrapped_ticket, create_count=1, skipped=False)


@pytest.fixture
def prepared_submit_context(migrated_database):
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
    category = guild_repository.upsert_category(
        TicketCategoryConfig(
            guild_id=1,
            category_key="support",
            display_name="技术支持",
            emoji="🛠️",
            description="处理技术问题",
            staff_role_id=500,
            staff_user_ids_json="[301]",
            extra_welcome_text="请说明具体错误。",
            is_enabled=True,
            allowlist_role_ids_json="[]",
            denylist_role_ids_json="[]",
            sort_order=1,
        )
    )

    creator = FakeMember(201)
    staff_member = FakeMember(301)
    guild = FakeGuild(1)
    guild.add_member(creator)
    guild.add_member(staff_member)
    guild.add_role(FakeRole(400))
    guild.add_role(FakeRole(500))

    ticket = ticket_repository.create(
        TicketRecord(
            ticket_id="1-support-0001",
            guild_id=1,
            creator_id=creator.id,
            category_key="support",
            channel_id=9001,
            status=TicketStatus.DRAFT,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
            has_user_message=True,
            last_user_message_at="2024-01-01T01:00:00+00:00",
        )
    )
    channel = FakeChannel(
        9001,
        guild,
        name="ticket-support-0001",
        topic="ticket_id=1-support-0001 creator_id=201 status=draft",
    )
    welcome_message = FakeMessage(
        5001,
        content="您好 <@201>\n- Ticket ID：`1-support-0001`",
        view=SimpleNamespace(name="draft-welcome-view"),
        pinned=True,
    )
    channel.pinned_messages.append(welcome_message)

    return {
        "database": migrated_database,
        "ticket_repository": ticket_repository,
        "category": category,
        "creator": creator,
        "guild": guild,
        "channel": channel,
        "ticket": ticket,
        "welcome_message": welcome_message,
    }


@pytest.mark.asyncio
async def test_submit_draft_ticket_happy_path_updates_status_permissions_and_messages(
    prepared_submit_context,
) -> None:
    database = prepared_submit_context["database"]
    channel = prepared_submit_context["channel"]
    creator = prepared_submit_context["creator"]
    welcome_message = prepared_submit_context["welcome_message"]
    service = SubmitService(database)

    result = await service.submit_draft_ticket(
        channel,
        actor_id=creator.id,
        requested_title="Login fails badly",
        welcome_message=welcome_message,
    )

    stored = TicketRepository(database).get_by_ticket_id(result.ticket.ticket_id)

    assert result.outcome == "submitted"
    assert result.channel_name_changed is True
    assert result.old_channel_name == "ticket-support-0001"
    assert result.new_channel_name == "ticket-0001-login-fails-badly"
    assert stored is not None
    assert stored.status is TicketStatus.SUBMITTED
    assert stored.queued_at is None
    assert channel.name == "ticket-0001-login-fails-badly"
    assert channel.topic == "ticket_id=1-support-0001 creator_id=201 status=submitted"
    assert stored.staff_panel_message_id == result.staff_panel_message.id
    assert {call["target"].id for call in channel.permission_calls} == {400, 500, 301}
    assert result.welcome_message_updated is True
    assert welcome_message.view is None
    assert len(channel.sent_messages) == 2
    assert result.divider_message is channel.sent_messages[0]
    assert result.staff_panel_message is channel.sent_messages[1]
    assert "已提交" in (result.divider_message.content or "")
    assert result.staff_panel_message.embed is not None
    assert result.staff_panel_message.embed.title == "🛠️ Staff 控制面板"
    assert isinstance(result.staff_panel_message.view, StaffPanelView)
    assert len(result.staff_panel_message.view.children) == 4


@pytest.mark.asyncio
async def test_submit_draft_ticket_bootstraps_snapshot_history_when_snapshot_service_is_attached(
    prepared_submit_context,
) -> None:
    database = prepared_submit_context["database"]
    channel = prepared_submit_context["channel"]
    creator = prepared_submit_context["creator"]
    welcome_message = prepared_submit_context["welcome_message"]
    snapshot_service = FakeSnapshotService()
    snapshot_service.database = database
    service = SubmitService(database, snapshot_service=snapshot_service)

    result = await service.submit_draft_ticket(
        channel,
        actor_id=creator.id,
        requested_title="Login fails badly",
        welcome_message=welcome_message,
    )
    stored = TicketRepository(database).get_by_ticket_id(result.ticket.ticket_id)

    assert len(snapshot_service.calls) == 1
    assert snapshot_service.calls[0]["channel"] is channel
    assert stored is not None
    assert stored.snapshot_bootstrapped_at == "2024-01-01T01:10:00+00:00"
    assert stored.message_count == 1


@pytest.mark.asyncio
async def test_submit_draft_ticket_in_strict_mode_grants_staff_visibility_without_send_permission(
    prepared_submit_context,
) -> None:
    database = prepared_submit_context["database"]
    channel = prepared_submit_context["channel"]
    creator = prepared_submit_context["creator"]
    welcome_message = prepared_submit_context["welcome_message"]

    GuildRepository(database).update_config(1, claim_mode=ClaimMode.STRICT)
    service = SubmitService(database)

    result = await service.submit_draft_ticket(
        channel,
        actor_id=creator.id,
        requested_title="Strict mode title",
        welcome_message=welcome_message,
    )

    assert result.outcome == "submitted"
    assert {call["target"].id for call in channel.permission_calls} == {400, 500, 301}
    assert all(call["overwrite"].view_channel is True for call in channel.permission_calls)
    assert all(call["overwrite"].send_messages is False for call in channel.permission_calls)
    assert result.staff_panel_message is not None
    assert result.staff_panel_message.embed is not None
    fields = {field.name: field.value for field in result.staff_panel_message.embed.fields}
    assert fields["认领模式"] == "strict 严格认领"
    assert "仅可见不可发言" in (result.staff_panel_message.embed.description or "")


@pytest.mark.asyncio
async def test_submit_draft_ticket_requires_title_when_channel_name_still_default(
    prepared_submit_context,
) -> None:
    database = prepared_submit_context["database"]
    channel = prepared_submit_context["channel"]
    creator = prepared_submit_context["creator"]
    service = SubmitService(database)

    with pytest.raises(ValidationError, match="默认频道名"):
        await service.submit_draft_ticket(channel, actor_id=creator.id)


@pytest.mark.asyncio
async def test_submit_draft_ticket_is_idempotent_for_already_submitted_ticket(
    prepared_submit_context,
) -> None:
    database = prepared_submit_context["database"]
    ticket = prepared_submit_context["ticket"]
    channel = prepared_submit_context["channel"]
    creator = prepared_submit_context["creator"]
    welcome_message = prepared_submit_context["welcome_message"]
    repository = prepared_submit_context["ticket_repository"]
    repository.update(ticket.ticket_id, status=TicketStatus.SUBMITTED)
    channel.name = "ticket-0001-existing-title"
    service = SubmitService(database)

    result = await service.submit_draft_ticket(
        channel,
        actor_id=creator.id,
        welcome_message=welcome_message,
    )

    stored = TicketRepository(database).get_by_ticket_id(ticket.ticket_id)

    assert result.outcome == "already_submitted"
    assert result.channel_name_changed is False
    assert result.divider_message is None
    assert result.staff_panel_message is None
    assert result.welcome_message_updated is True
    assert stored is not None
    assert stored.status is TicketStatus.SUBMITTED
    assert not channel.sent_messages


@pytest.mark.asyncio
async def test_submit_draft_ticket_queues_when_active_capacity_is_full(prepared_submit_context) -> None:
    database = prepared_submit_context["database"]
    channel = prepared_submit_context["channel"]
    creator = prepared_submit_context["creator"]
    welcome_message = prepared_submit_context["welcome_message"]
    ticket_repository = prepared_submit_context["ticket_repository"]
    GuildRepository(database).update_config(1, max_open_tickets=1)
    ticket_repository.create(
        TicketRecord(
            ticket_id="1-support-0002",
            guild_id=1,
            creator_id=999,
            category_key="support",
            channel_id=9002,
            status=TicketStatus.SUBMITTED,
            created_at="2024-01-01T00:05:00+00:00",
            updated_at="2024-01-01T00:05:00+00:00",
        )
    )
    capacity_service = CapacityService(database)
    queue_service = QueueService(database, capacity_service=capacity_service)
    service = SubmitService(
        database,
        capacity_service=capacity_service,
        queue_service=queue_service,
    )

    result = await service.submit_draft_ticket(
        channel,
        actor_id=creator.id,
        requested_title="Need queue now",
        welcome_message=welcome_message,
    )

    stored = ticket_repository.get_by_ticket_id(prepared_submit_context["ticket"].ticket_id)

    assert result.outcome == "queued"
    assert result.queue_position == 1
    assert result.active_count == 1
    assert result.max_open_tickets == 1
    assert stored is not None
    assert stored.status is TicketStatus.QUEUED
    assert stored.queued_at is not None
    assert stored.staff_panel_message_id is None
    assert channel.name == "ticket-0001-need-queue-now"
    assert channel.topic == "ticket_id=1-support-0001 creator_id=201 status=queued"
    assert result.staff_panel_message is None
    assert result.divider_message is None
    assert result.welcome_message_updated is True
    assert welcome_message.view is None
    assert not channel.permission_calls
    assert not channel.sent_messages


@pytest.mark.asyncio
async def test_submit_draft_ticket_returns_current_queue_position_when_ticket_is_already_queued(prepared_submit_context) -> None:
    database = prepared_submit_context["database"]
    channel = prepared_submit_context["channel"]
    creator = prepared_submit_context["creator"]
    welcome_message = prepared_submit_context["welcome_message"]
    queue_service = QueueService(database)
    repository = prepared_submit_context["ticket_repository"]
    repository.update(prepared_submit_context["ticket"].ticket_id, status=TicketStatus.QUEUED, queued_at="2024-01-01T01:00:00+00:00")
    service = SubmitService(database, queue_service=queue_service)

    result = await service.submit_draft_ticket(channel, actor_id=creator.id, welcome_message=welcome_message)

    assert result.outcome == "already_queued"
    assert result.queue_position == 1
    assert result.staff_panel_message is None
    assert result.divider_message is None
    assert result.welcome_message_updated is True
    assert welcome_message.view is None


@pytest.mark.asyncio
async def test_submit_draft_ticket_rejects_non_creator(prepared_submit_context) -> None:
    database = prepared_submit_context["database"]
    channel = prepared_submit_context["channel"]
    service = SubmitService(database)

    with pytest.raises(PermissionDeniedError, match="创建者"):
        await service.submit_draft_ticket(channel, actor_id=999)
