from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from core.enums import ClaimMode
from core.models import GuildConfigRecord, TicketCategoryConfig
from services.staff_permission_service import StaffPermissionService


@dataclass(frozen=True)
class FakeRole:
    id: int


@dataclass
class FakeMember:
    id: int
    roles: list[FakeRole] = field(default_factory=list)


class FakeGuild:
    def __init__(self, *, roles: list[FakeRole], members: list[FakeMember]) -> None:
        self._roles = {role.id: role for role in roles}
        self._members = {member.id: member for member in members}

    def get_role(self, role_id: int):
        return self._roles.get(role_id)

    def get_member(self, member_id: int):
        return self._members.get(member_id)


class FakeChannel:
    def __init__(self, *, guild: FakeGuild) -> None:
        self.guild = guild
        self.permission_calls: list[dict[str, object]] = []

    async def set_permissions(self, target, *, overwrite=None, reason: str | None = None):
        self.permission_calls.append(
            {
                "target_id": getattr(target, "id", None),
                "overwrite": overwrite,
                "reason": reason,
            }
        )


@pytest.mark.asyncio
async def test_apply_staff_overwrite_plan_in_strict_mode_only_allows_current_claimer_to_speak() -> None:
    admin_role = FakeRole(400)
    staff_role = FakeRole(500)
    current_claimer = FakeMember(301, roles=[staff_role])
    explicit_staff = FakeMember(302)
    guild = FakeGuild(roles=[admin_role, staff_role], members=[current_claimer, explicit_staff])
    channel = FakeChannel(guild=guild)
    config = GuildConfigRecord(guild_id=1, admin_role_id=400, claim_mode=ClaimMode.STRICT)
    category = TicketCategoryConfig(
        guild_id=1,
        category_key="support",
        display_name="技术支持",
        staff_role_id=500,
        staff_user_ids_json="[302]",
    )
    service = StaffPermissionService()

    await service.apply_staff_overwrite_plan(
        channel,
        config=config,
        category=category,
        active_claimer=current_claimer,
        visible_reason="Recalculate staff participation for ticket claim state",
        strict_claimer_reason="Allow current claimer to speak in strict claim mode",
    )

    assert [call["target_id"] for call in channel.permission_calls] == [400, 500, 302, 301]
    assert [call["overwrite"].send_messages for call in channel.permission_calls] == [False, False, False, True]
    assert channel.permission_calls[-1]["reason"] == "Allow current claimer to speak in strict claim mode"


@pytest.mark.asyncio
async def test_apply_staff_overwrite_plan_hides_previous_category_targets_during_transfer_execution() -> None:
    admin_role = FakeRole(400)
    support_role = FakeRole(500)
    billing_role = FakeRole(600)
    previous_claimer = FakeMember(301, roles=[support_role])
    explicit_support_staff = FakeMember(302)
    guild = FakeGuild(
        roles=[admin_role, support_role, billing_role],
        members=[previous_claimer, explicit_support_staff],
    )
    channel = FakeChannel(guild=guild)
    config = GuildConfigRecord(guild_id=1, admin_role_id=400, claim_mode=ClaimMode.RELAXED)
    support_category = TicketCategoryConfig(
        guild_id=1,
        category_key="support",
        display_name="技术支持",
        staff_role_id=500,
        staff_user_ids_json="[302]",
    )
    billing_category = TicketCategoryConfig(
        guild_id=1,
        category_key="billing",
        display_name="账单咨询",
        staff_role_id=600,
        staff_user_ids_json="[]",
    )
    service = StaffPermissionService()

    await service.apply_staff_overwrite_plan(
        channel,
        config=config,
        category=billing_category,
        previous_claimer_id=previous_claimer.id,
        hidden_categories=(support_category,),
        visible_reason="Grant new category staff access after ticket transfer execution",
        hidden_reason="Hide previous category staff after ticket transfer execution",
    )

    permission_targets = {call["target_id"]: call["overwrite"] for call in channel.permission_calls}
    assert permission_targets[500].view_channel is False
    assert permission_targets[302].view_channel is False
    assert permission_targets[301].view_channel is False
    assert permission_targets[400].view_channel is True
    assert permission_targets[600].view_channel is True
    assert permission_targets[600].send_messages is True


@pytest.mark.asyncio
async def test_apply_ticket_permissions_includes_creator_and_preserves_muted_participant_restriction() -> None:
    admin_role = FakeRole(400)
    staff_role = FakeRole(500)
    creator = FakeMember(201)
    muted_participant = FakeMember(202)
    guild = FakeGuild(
        roles=[admin_role, staff_role],
        members=[creator, muted_participant],
    )
    channel = FakeChannel(guild=guild)
    config = GuildConfigRecord(guild_id=1, admin_role_id=400, claim_mode=ClaimMode.RELAXED)
    category = TicketCategoryConfig(
        guild_id=1,
        category_key="support",
        display_name="技术支持",
        staff_role_id=500,
        staff_user_ids_json="[]",
    )
    service = StaffPermissionService()

    await service.apply_ticket_permissions(
        channel,
        config=config,
        category=category,
        creator=creator,
        participants=(muted_participant,),
        muted_participants=(muted_participant,),
        creator_reason="Normalize ticket creator access",
        participant_reason="Normalize participant access",
        muted_reason="Preserve muted participant restriction",
    )

    assert [call["target_id"] for call in channel.permission_calls] == [400, 500, 201, 202]
    permission_targets = {call["target_id"]: call for call in channel.permission_calls}
    assert permission_targets[201]["overwrite"].send_messages is True
    assert permission_targets[201]["reason"] == "Normalize ticket creator access"
    assert permission_targets[202]["overwrite"].send_messages is False
    assert permission_targets[202]["reason"] == "Preserve muted participant restriction"
