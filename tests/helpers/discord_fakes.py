from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any


@dataclass(frozen=True)
class FakeRole:
    id: int


@dataclass
class FakeMember:
    id: int
    name: str = ""
    roles: list[FakeRole] = field(default_factory=list)
    administrator: bool = False
    bot: bool = False

    @property
    def guild_permissions(self) -> SimpleNamespace:
        return SimpleNamespace(administrator=self.administrator)


@dataclass
class FakeMessage:
    id: int | None = None
    content: str | None = None
    embed: Any | None = None
    view: Any | None = None
    file: Any | None = None
    pinned: bool = False
    edit_calls: list[dict[str, Any]] = field(default_factory=list)

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


class FakeResponse:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.deferred: list[dict[str, object]] = []
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def send_message(self, content: str | None = None, *, embed=None, view=None, ephemeral: bool = False) -> None:
        self._done = True
        self.messages.append({"content": content, "embed": embed, "view": view, "ephemeral": ephemeral})

    async def defer(self, *, ephemeral: bool, thinking: bool | None = None) -> None:
        self._done = True
        payload: dict[str, object] = {"ephemeral": ephemeral}
        if thinking is not None:
            payload["thinking"] = thinking
        self.deferred.append(payload)


class FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send(self, content: str | None = None, *, embed=None, view=None, ephemeral: bool = False) -> None:
        self.messages.append({"content": content, "embed": embed, "view": view, "ephemeral": ephemeral})


class FakeClient:
    def __init__(self, *, resources=None) -> None:
        self.resources = resources


class FakeInteraction:
    def __init__(
        self,
        guild: FakeGuild | None = None,
        channel: Any | None = None,
        user: FakeMember | None = None,
        *,
        client=None,
        message=None,
    ) -> None:
        self.guild = guild
        self.channel = channel
        self.user = user
        self.client = client
        self.message = message
        self.response = FakeResponse()
        self.followup = FakeFollowup()


def assert_deferred_ephemeral_followup(interaction: FakeInteraction) -> dict[str, object]:
    assert interaction.response.deferred
    assert interaction.response.deferred[0]["ephemeral"] is True
    assert interaction.followup.messages
    assert interaction.followup.messages[0]["ephemeral"] is True
    return interaction.followup.messages[0]
