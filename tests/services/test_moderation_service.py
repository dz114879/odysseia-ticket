from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from core.enums import ClaimMode, TicketPriority, TicketStatus
from core.errors import InvalidTicketStateError, PermissionDeniedError, ValidationError
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketMuteRecord, TicketRecord
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_mute_repository import TicketMuteRepository
from db.repositories.ticket_repository import TicketRepository
from runtime.locks import LockManager
from services.moderation_service import ModerationService


@dataclass(frozen=True)
class FakeRole:
    id: int


@dataclass
class FakeMember:
    id: int
    roles: list[FakeRole] = field(default_factory=list)
    administrator: bool = False
    bot: bool = False

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


class FakeOverwrite:
    def __init__(self, *, view_channel=None, send_messages=None, read_message_history=None) -> None:
        self.view_channel = view_channel
        self.send_messages = send_messages
        self.read_message_history = read_message_history


class FakeChannel:
    def __init__(self, channel_id: int, guild: FakeGuild, *, name: str) -> None:
        self.id = channel_id
        self.guild = guild
        self.name = name
        self.permission_calls: list[dict[str, object]] = []
        self.sent_messages: list[FakeMessage] = []
        self._overwrites_by_user_id: dict[int, FakeOverwrite] = {}

    async def set_permissions(self, target, *, overwrite=None, reason: str | None = None):
        self.permission_calls.append(
            {
                "target_id": getattr(target, "id", None),
                "overwrite": overwrite,
                "reason": reason,
            }
        )
        target_id = getattr(target, "id", None)
        if target_id is not None and overwrite is not None:
            self._overwrites_by_user_id[target_id] = FakeOverwrite(
                view_channel=getattr(overwrite, "view_channel", None),
                send_messages=getattr(overwrite, "send_messages", None),
                read_message_history=getattr(overwrite, "read_message_history", None),
            )

    async def send(self, *, content: str | None = None, embed=None, view=None):
        message = FakeMessage(content or "")
        self.sent_messages.append(message)
        return message

    def overwrites_for(self, target) -> FakeOverwrite:
        return self._overwrites_by_user_id.get(getattr(target, "id", None), FakeOverwrite())

    def seed_participant_access(self, member_id: int) -> None:
        self._overwrites_by_user_id[member_id] = FakeOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
        )


class FakeBot:
    def __init__(self, channel: FakeChannel) -> None:
        self.channel = channel

    def get_channel(self, channel_id: int) -> FakeChannel | None:
        return self.channel if self.channel.id == channel_id else None

    async def fetch_channel(self, channel_id: int) -> FakeChannel:
        if self.channel.id != channel_id:
            raise LookupError("channel not found")
        return self.channel


class FakeStaffPanelService:
    def __init__(self) -> None:
        self.requested_ticket_ids: list[str] = []

    def request_refresh(self, ticket_id: str) -> None:
        self.requested_ticket_ids.append(ticket_id)


@pytest.fixture
def prepared_moderation_context(migrated_database):
    guild_repository = GuildRepository(migrated_database)
    ticket_repository = TicketRepository(migrated_database)
    ticket_mute_repository = TicketMuteRepository(migrated_database)

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
    participant = FakeMember(202)
    staff_user = FakeMember(301, roles=[staff_role])
    explicit_staff_user = FakeMember(302)
    admin_user = FakeMember(401, roles=[admin_role])
    bot_user = FakeMember(9999, bot=True)
    for member in (creator, participant, staff_user, explicit_staff_user, admin_user, bot_user):
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
            priority=TicketPriority.MEDIUM,
        )
    )
    channel = FakeChannel(ticket.channel_id or 9001, guild, name="login-error")
    channel.seed_participant_access(participant.id)

    return {
        "database": migrated_database,
        "ticket": ticket,
        "ticket_repository": ticket_repository,
        "ticket_mute_repository": ticket_mute_repository,
        "guild": guild,
        "channel": channel,
        "creator": creator,
        "participant": participant,
        "staff_user": staff_user,
        "explicit_staff_user": explicit_staff_user,
        "admin_user": admin_user,
        "bot_user": bot_user,
    }


@pytest.mark.asyncio
async def test_mute_creator_succeeds_and_persists_expiration(prepared_moderation_context) -> None:
    database = prepared_moderation_context["database"]
    channel = prepared_moderation_context["channel"]
    creator = prepared_moderation_context["creator"]
    staff_user = prepared_moderation_context["staff_user"]
    ticket_mute_repository = prepared_moderation_context["ticket_mute_repository"]
    staff_panel_service = FakeStaffPanelService()
    service = ModerationService(
        database,
        lock_manager=LockManager(),
        staff_panel_service=staff_panel_service,
    )

    result = await service.mute_member(
        channel,
        actor=staff_user,
        target=creator,
        duration="2h",
        reason="情绪激动",
        now="2024-01-01T00:00:00+00:00",
    )
    stored = ticket_mute_repository.get_by_ticket_and_user(result.ticket.ticket_id, creator.id)

    assert result.changed is True
    assert result.target_id == creator.id
    assert result.expire_at == "2024-01-01T02:00:00+00:00"
    assert stored is not None
    assert stored.reason == "情绪激动"
    assert stored.expire_at == "2024-01-01T02:00:00+00:00"
    assert channel.permission_calls[-1]["target_id"] == creator.id
    assert channel.permission_calls[-1]["overwrite"].send_messages is False
    assert channel.sent_messages
    assert "已对 ticket" in channel.sent_messages[0].content
    assert staff_panel_service.requested_ticket_ids == [result.ticket.ticket_id]


@pytest.mark.asyncio
async def test_mute_explicit_participant_preserves_creator_access_while_restricting_target(
    prepared_moderation_context,
) -> None:
    database = prepared_moderation_context["database"]
    channel = prepared_moderation_context["channel"]
    participant = prepared_moderation_context["participant"]
    creator = prepared_moderation_context["creator"]
    staff_user = prepared_moderation_context["staff_user"]
    service = ModerationService(database, lock_manager=LockManager())

    result = await service.mute_member(
        channel,
        actor=staff_user,
        target=participant,
        duration="30m",
    )

    permission_targets = {call["target_id"]: call["overwrite"] for call in channel.permission_calls}
    assert result.changed is True
    assert permission_targets[creator.id].send_messages is True
    assert permission_targets[participant.id].send_messages is False


@pytest.mark.asyncio
async def test_unmute_creator_restores_send_permission_and_removes_record(prepared_moderation_context) -> None:
    database = prepared_moderation_context["database"]
    channel = prepared_moderation_context["channel"]
    creator = prepared_moderation_context["creator"]
    staff_user = prepared_moderation_context["staff_user"]
    ticket = prepared_moderation_context["ticket"]
    ticket_mute_repository = prepared_moderation_context["ticket_mute_repository"]
    staff_panel_service = FakeStaffPanelService()
    service = ModerationService(
        database,
        lock_manager=LockManager(),
        staff_panel_service=staff_panel_service,
    )

    ticket_mute_repository.upsert(
        TicketMuteRecord(
            ticket_id=ticket.ticket_id,
            user_id=creator.id,
            muted_by=staff_user.id,
            expire_at="2024-01-01T02:00:00+00:00",
        )
    )

    result = await service.unmute_member(channel, actor=staff_user, target=creator)

    assert result.changed is True
    assert ticket_mute_repository.get_by_ticket_and_user(ticket.ticket_id, creator.id) is None
    assert channel.permission_calls[-1]["target_id"] == creator.id
    assert channel.permission_calls[-1]["overwrite"].send_messages is True
    assert channel.sent_messages
    assert "已解除 ticket" in channel.sent_messages[0].content
    assert staff_panel_service.requested_ticket_ids == [ticket.ticket_id]


@pytest.mark.asyncio
async def test_mute_rejects_staff_target(prepared_moderation_context) -> None:
    database = prepared_moderation_context["database"]
    channel = prepared_moderation_context["channel"]
    staff_user = prepared_moderation_context["staff_user"]
    explicit_staff_user = prepared_moderation_context["explicit_staff_user"]
    service = ModerationService(database, lock_manager=LockManager())

    with pytest.raises(PermissionDeniedError, match="合法 staff"):
        await service.mute_member(channel, actor=staff_user, target=explicit_staff_user, duration="30m")


@pytest.mark.asyncio
async def test_mute_rejects_self_and_bot_targets(prepared_moderation_context) -> None:
    database = prepared_moderation_context["database"]
    channel = prepared_moderation_context["channel"]
    staff_user = prepared_moderation_context["staff_user"]
    bot_user = prepared_moderation_context["bot_user"]
    service = ModerationService(database, lock_manager=LockManager())

    with pytest.raises(ValidationError, match="不能对自己"):
        await service.mute_member(channel, actor=staff_user, target=staff_user, duration="30m")

    with pytest.raises(ValidationError, match="不能对 bot"):
        await service.mute_member(channel, actor=staff_user, target=bot_user, duration="30m")


@pytest.mark.asyncio
async def test_mute_allows_sleep_ticket(prepared_moderation_context) -> None:
    database = prepared_moderation_context["database"]
    ticket_repository = prepared_moderation_context["ticket_repository"]
    channel = prepared_moderation_context["channel"]
    creator = prepared_moderation_context["creator"]
    staff_user = prepared_moderation_context["staff_user"]
    service = ModerationService(database, lock_manager=LockManager())

    ticket_repository.update(
        prepared_moderation_context["ticket"].ticket_id,
        status=TicketStatus.SLEEP,
        priority=TicketPriority.SLEEP,
        priority_before_sleep=TicketPriority.HIGH,
    )

    result = await service.mute_member(channel, actor=staff_user, target=creator, duration="30m")

    assert result.changed is True
    assert channel.permission_calls[-1]["overwrite"].send_messages is False


@pytest.mark.asyncio
async def test_mute_rejects_invalid_ticket_state(prepared_moderation_context) -> None:
    database = prepared_moderation_context["database"]
    ticket_repository = prepared_moderation_context["ticket_repository"]
    channel = prepared_moderation_context["channel"]
    creator = prepared_moderation_context["creator"]
    staff_user = prepared_moderation_context["staff_user"]
    service = ModerationService(database, lock_manager=LockManager())

    ticket_repository.update(prepared_moderation_context["ticket"].ticket_id, status=TicketStatus.TRANSFERRING)

    with pytest.raises(InvalidTicketStateError, match="submitted / sleep"):
        await service.mute_member(channel, actor=staff_user, target=creator, duration="30m")


@pytest.mark.asyncio
async def test_sweep_expired_mutes_restores_permission_and_clears_record(prepared_moderation_context) -> None:
    database = prepared_moderation_context["database"]
    channel = prepared_moderation_context["channel"]
    creator = prepared_moderation_context["creator"]
    ticket = prepared_moderation_context["ticket"]
    ticket_mute_repository = prepared_moderation_context["ticket_mute_repository"]
    staff_panel_service = FakeStaffPanelService()
    service = ModerationService(
        database,
        bot=FakeBot(channel),
        lock_manager=LockManager(),
        staff_panel_service=staff_panel_service,
    )

    ticket_mute_repository.upsert(
        TicketMuteRecord(
            ticket_id=ticket.ticket_id,
            user_id=creator.id,
            muted_by=301,
            expire_at="2024-01-01T00:30:00+00:00",
        )
    )

    outcomes = await service.sweep_expired_mutes(now="2024-01-01T01:00:00+00:00")

    assert len(outcomes) == 1
    assert outcomes[0].target_id == creator.id
    assert ticket_mute_repository.get_by_ticket_and_user(ticket.ticket_id, creator.id) is None
    assert channel.permission_calls[-1]["overwrite"].send_messages is True
    assert channel.sent_messages
    assert "自动解除" in channel.sent_messages[0].content
    assert staff_panel_service.requested_ticket_ids == [ticket.ticket_id]
