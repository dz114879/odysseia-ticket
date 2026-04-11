from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from collections.abc import Iterable

import discord

from core.enums import ClaimMode
from core.models import GuildConfigRecord, TicketCategoryConfig


@dataclass(frozen=True, slots=True)
class StaffPermissionUpdate:
    target: Any
    overwrite: discord.PermissionOverwrite
    reason: str


class StaffPermissionService:
    async def apply_ticket_permissions(
        self,
        channel: Any,
        *,
        config: GuildConfigRecord,
        category: TicketCategoryConfig,
        active_claimer: Any | None = None,
        previous_claimer_id: int | None = None,
        hidden_categories: Iterable[TicketCategoryConfig | None] = (),
        creator: Any | None = None,
        participants: Iterable[Any] = (),
        muted_participants: Iterable[Any] = (),
        include_staff: bool = True,
        include_participants: bool = True,
        visible_reason: str = "Recalculate current category staff participation",
        hidden_reason: str = "Hide stale staff access after category change",
        previous_claimer_reason: str = "Normalize previous claimer override after claim state change",
        active_claimer_reason: str = "Normalize current claimer override",
        strict_claimer_reason: str = "Allow current claimer to speak in strict claim mode",
        creator_reason: str = "Normalize ticket creator access",
        participant_reason: str = "Normalize participant access",
        muted_reason: str = "Preserve muted participant restriction",
    ) -> None:
        guild = getattr(channel, "guild", None)
        set_permissions = getattr(channel, "set_permissions", None)
        if guild is None or set_permissions is None:
            return

        updates = self.build_ticket_permission_plan(
            guild,
            config=config,
            category=category,
            active_claimer=active_claimer,
            previous_claimer_id=previous_claimer_id,
            hidden_categories=hidden_categories,
            creator=creator,
            participants=participants,
            muted_participants=muted_participants,
            include_staff=include_staff,
            include_participants=include_participants,
            visible_reason=visible_reason,
            hidden_reason=hidden_reason,
            previous_claimer_reason=previous_claimer_reason,
            active_claimer_reason=active_claimer_reason,
            strict_claimer_reason=strict_claimer_reason,
            creator_reason=creator_reason,
            participant_reason=participant_reason,
            muted_reason=muted_reason,
        )
        for update in updates:
            await set_permissions(
                update.target,
                overwrite=update.overwrite,
                reason=update.reason,
            )

    async def apply_staff_overwrite_plan(
        self,
        channel: Any,
        *,
        config: GuildConfigRecord,
        category: TicketCategoryConfig,
        active_claimer: Any | None = None,
        previous_claimer_id: int | None = None,
        hidden_categories: Iterable[TicketCategoryConfig | None] = (),
        visible_reason: str = "Recalculate current category staff participation",
        hidden_reason: str = "Hide stale staff access after category change",
        previous_claimer_reason: str = "Normalize previous claimer override after claim state change",
        active_claimer_reason: str = "Normalize current claimer override",
        strict_claimer_reason: str = "Allow current claimer to speak in strict claim mode",
    ) -> None:
        guild = getattr(channel, "guild", None)
        set_permissions = getattr(channel, "set_permissions", None)
        if guild is None or set_permissions is None:
            return

        updates = self.build_staff_overwrite_plan(
            guild,
            config=config,
            category=category,
            active_claimer=active_claimer,
            previous_claimer_id=previous_claimer_id,
            hidden_categories=hidden_categories,
            visible_reason=visible_reason,
            hidden_reason=hidden_reason,
            previous_claimer_reason=previous_claimer_reason,
            active_claimer_reason=active_claimer_reason,
            strict_claimer_reason=strict_claimer_reason,
        )
        for update in updates:
            await set_permissions(
                update.target,
                overwrite=update.overwrite,
                reason=update.reason,
            )

    async def apply_participant_overwrite(
        self,
        channel: Any,
        *,
        target: Any,
        can_send: bool,
        reason: str,
    ) -> None:
        set_permissions = getattr(channel, "set_permissions", None)
        if set_permissions is None:
            return
        await set_permissions(
            target,
            overwrite=self.build_participant_overwrite(can_send=can_send),
            reason=reason,
        )

    def build_ticket_permission_plan(
        self,
        guild: Any,
        *,
        config: GuildConfigRecord,
        category: TicketCategoryConfig,
        active_claimer: Any | None = None,
        previous_claimer_id: int | None = None,
        hidden_categories: Iterable[TicketCategoryConfig | None] = (),
        creator: Any | None = None,
        participants: Iterable[Any] = (),
        muted_participants: Iterable[Any] = (),
        include_staff: bool = True,
        include_participants: bool = True,
        visible_reason: str = "Recalculate current category staff participation",
        hidden_reason: str = "Hide stale staff access after category change",
        previous_claimer_reason: str = "Normalize previous claimer override after claim state change",
        active_claimer_reason: str = "Normalize current claimer override",
        strict_claimer_reason: str = "Allow current claimer to speak in strict claim mode",
        creator_reason: str = "Normalize ticket creator access",
        participant_reason: str = "Normalize participant access",
        muted_reason: str = "Preserve muted participant restriction",
    ) -> list[StaffPermissionUpdate]:
        updates: list[StaffPermissionUpdate] = []

        if include_staff:
            updates.extend(
                self.build_staff_overwrite_plan(
                    guild,
                    config=config,
                    category=category,
                    active_claimer=active_claimer,
                    previous_claimer_id=previous_claimer_id,
                    hidden_categories=hidden_categories,
                    visible_reason=visible_reason,
                    hidden_reason=hidden_reason,
                    previous_claimer_reason=previous_claimer_reason,
                    active_claimer_reason=active_claimer_reason,
                    strict_claimer_reason=strict_claimer_reason,
                )
            )

        if include_participants:
            updates.extend(
                self.build_participant_permission_plan(
                    creator=creator,
                    participants=participants,
                    muted_participants=muted_participants,
                    creator_reason=creator_reason,
                    participant_reason=participant_reason,
                    muted_reason=muted_reason,
                )
            )

        return updates

    def build_staff_overwrite_plan(
        self,
        guild: Any,
        *,
        config: GuildConfigRecord,
        category: TicketCategoryConfig,
        active_claimer: Any | None = None,
        previous_claimer_id: int | None = None,
        hidden_categories: Iterable[TicketCategoryConfig | None] = (),
        visible_reason: str = "Recalculate current category staff participation",
        hidden_reason: str = "Hide stale staff access after category change",
        previous_claimer_reason: str = "Normalize previous claimer override after claim state change",
        active_claimer_reason: str = "Normalize current claimer override",
        strict_claimer_reason: str = "Allow current claimer to speak in strict claim mode",
    ) -> list[StaffPermissionUpdate]:
        strict_mode = config.claim_mode is ClaimMode.STRICT
        readable_overwrite = self._build_staff_overwrite(can_send=False)
        writable_overwrite = self._build_staff_overwrite(can_send=True)
        hidden_overwrite = self._build_hidden_staff_overwrite()
        base_overwrite = readable_overwrite if strict_mode else writable_overwrite

        visible_targets = self.resolve_staff_targets(guild, config=config, category=category)
        visible_target_ids = self._extract_target_ids(visible_targets)
        has_hidden_categories = any(hidden_category is not None for hidden_category in hidden_categories)

        updates: list[StaffPermissionUpdate] = []
        hidden_target_ids: set[int] = set()
        for hidden_category in hidden_categories:
            if hidden_category is None:
                continue
            for target in self.resolve_staff_targets(guild, config=config, category=hidden_category):
                target_id = getattr(target, "id", None)
                if target_id is None or target_id in visible_target_ids or target_id in hidden_target_ids:
                    continue
                hidden_target_ids.add(target_id)
                updates.append(
                    StaffPermissionUpdate(
                        target=target,
                        overwrite=hidden_overwrite,
                        reason=hidden_reason,
                    )
                )

        updates.extend(
            StaffPermissionUpdate(
                target=target,
                overwrite=base_overwrite,
                reason=visible_reason,
            )
            for target in visible_targets
        )

        previous_claimer = self._resolve_member(guild, previous_claimer_id)
        if previous_claimer is not None and previous_claimer_id not in visible_target_ids:
            if has_hidden_categories:
                if previous_claimer_id not in hidden_target_ids:
                    updates.append(
                        StaffPermissionUpdate(
                            target=previous_claimer,
                            overwrite=hidden_overwrite,
                            reason=hidden_reason,
                        )
                    )
                    hidden_target_ids.add(previous_claimer_id)
            else:
                updates.append(
                    StaffPermissionUpdate(
                        target=previous_claimer,
                        overwrite=base_overwrite,
                        reason=previous_claimer_reason,
                    )
                )

        active_claimer_id = getattr(active_claimer, "id", None)
        if (
            active_claimer is not None
            and active_claimer_id is not None
            and active_claimer_id not in visible_target_ids
            and active_claimer_id != previous_claimer_id
            and not strict_mode
        ):
            updates.append(
                StaffPermissionUpdate(
                    target=active_claimer,
                    overwrite=base_overwrite,
                    reason=active_claimer_reason,
                )
            )

        if strict_mode and active_claimer is not None:
            updates.append(
                StaffPermissionUpdate(
                    target=active_claimer,
                    overwrite=writable_overwrite,
                    reason=strict_claimer_reason,
                )
            )

        return updates

    def resolve_staff_targets(
        self,
        guild: Any,
        *,
        config: GuildConfigRecord,
        category: TicketCategoryConfig,
    ) -> list[Any]:
        targets: list[Any] = []

        if config.admin_role_id is not None:
            admin_role = getattr(guild, "get_role", lambda _role_id: None)(config.admin_role_id)
            if admin_role is not None:
                targets.append(admin_role)

        if category.staff_role_id is not None:
            staff_role = getattr(guild, "get_role", lambda _role_id: None)(category.staff_role_id)
            if staff_role is not None:
                targets.append(staff_role)

        get_member = getattr(guild, "get_member", None)
        if callable(get_member):
            for staff_user_id in self._parse_staff_user_ids(category.staff_user_ids_json):
                member = get_member(staff_user_id)
                if member is not None:
                    targets.append(member)

        return self._unique_targets(targets)

    @staticmethod
    def _resolve_member(guild: Any, member_id: int | None) -> Any | None:
        if member_id is None:
            return None
        get_member = getattr(guild, "get_member", None)
        if not callable(get_member):
            return None
        return get_member(member_id)

    @staticmethod
    def _unique_targets(targets: Iterable[Any]) -> list[Any]:
        unique_targets: list[Any] = []
        seen_target_ids: set[int] = set()
        for target in targets:
            target_id = getattr(target, "id", None)
            if target_id is None or target_id in seen_target_ids:
                continue
            seen_target_ids.add(target_id)
            unique_targets.append(target)
        return unique_targets

    @staticmethod
    def _extract_target_ids(targets: Iterable[Any]) -> set[int]:
        target_ids: set[int] = set()
        for target in targets:
            target_id = getattr(target, "id", None)
            if target_id is not None:
                target_ids.add(target_id)
        return target_ids

    @staticmethod
    def _build_staff_overwrite(*, can_send: bool) -> discord.PermissionOverwrite:
        return discord.PermissionOverwrite(
            view_channel=True,
            send_messages=can_send,
            read_message_history=True,
            attach_files=can_send,
            embed_links=can_send,
        )

    @staticmethod
    def build_participant_overwrite(*, can_send: bool) -> discord.PermissionOverwrite:
        return discord.PermissionOverwrite(
            view_channel=True,
            send_messages=can_send,
            read_message_history=True,
            attach_files=can_send,
            embed_links=can_send,
        )

    def build_participant_permission_plan(
        self,
        *,
        creator: Any | None = None,
        participants: Iterable[Any] = (),
        muted_participants: Iterable[Any] = (),
        creator_reason: str = "Normalize ticket creator access",
        participant_reason: str = "Normalize participant access",
        muted_reason: str = "Preserve muted participant restriction",
    ) -> list[StaffPermissionUpdate]:
        updates: list[StaffPermissionUpdate] = []
        muted_target_ids = self._extract_target_ids(muted_participants)

        creator_id = getattr(creator, "id", None)
        if creator is not None and creator_id is not None:
            updates.append(
                StaffPermissionUpdate(
                    target=creator,
                    overwrite=self.build_participant_overwrite(can_send=creator_id not in muted_target_ids),
                    reason=muted_reason if creator_id in muted_target_ids else creator_reason,
                )
            )

        for participant in self._unique_targets(participants):
            participant_id = getattr(participant, "id", None)
            if participant_id is None or participant_id == creator_id:
                continue
            updates.append(
                StaffPermissionUpdate(
                    target=participant,
                    overwrite=self.build_participant_overwrite(can_send=participant_id not in muted_target_ids),
                    reason=muted_reason if participant_id in muted_target_ids else participant_reason,
                )
            )

        return updates

    @staticmethod
    def _build_hidden_staff_overwrite() -> discord.PermissionOverwrite:
        return discord.PermissionOverwrite(
            view_channel=False,
            send_messages=False,
            read_message_history=False,
            attach_files=False,
            embed_links=False,
        )

    @staticmethod
    def _parse_staff_user_ids(raw_value: str) -> list[int]:
        try:
            data = json.loads(raw_value or "[]")
        except json.JSONDecodeError:
            return []

        items = data if isinstance(data, list) else []
        return [
            value
            for value in (StaffPermissionService._coerce_staff_user_id(item) for item in items)
            if value is not None
        ]

    @staticmethod
    def _coerce_staff_user_id(value: object) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
