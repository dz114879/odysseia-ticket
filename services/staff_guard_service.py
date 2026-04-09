from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from core.enums import TicketStatus
from core.errors import InvalidTicketStateError, PermissionDeniedError, TicketNotFoundError, ValidationError
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.connection import DatabaseManager
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository


@dataclass(frozen=True, slots=True)
class StaffTicketContext:
    ticket: TicketRecord
    config: GuildConfigRecord
    category: TicketCategoryConfig


class StaffGuardService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        guild_repository: GuildRepository | None = None,
        ticket_repository: TicketRepository | None = None,
    ) -> None:
        self.database = database
        self.guild_repository = guild_repository or GuildRepository(database)
        self.ticket_repository = ticket_repository or TicketRepository(database)

    def load_ticket_context(
        self,
        channel_id: int,
        *,
        allowed_statuses: Sequence[TicketStatus],
        invalid_state_message: str,
    ) -> StaffTicketContext:
        ticket = self.ticket_repository.get_by_channel_id(channel_id)
        if ticket is None:
            raise TicketNotFoundError("当前频道不是已登记的 ticket。")
        if ticket.status not in tuple(allowed_statuses):
            raise InvalidTicketStateError(invalid_state_message)

        config = self.guild_repository.get_config(ticket.guild_id)
        if config is None or not config.is_initialized:
            raise ValidationError("当前服务器尚未完成 Ticket setup，无法执行 staff 操作。")

        category = self.guild_repository.get_category(ticket.guild_id, ticket.category_key)
        if category is None:
            raise ValidationError("当前 ticket 所属分类配置不存在，请先修复服务器配置。")

        return StaffTicketContext(ticket=ticket, config=config, category=category)

    def assert_staff_actor(
        self,
        actor: Any,
        *,
        config: GuildConfigRecord,
        category: TicketCategoryConfig,
        is_bot_owner: bool,
    ) -> None:
        if self.is_staff_actor(
            actor,
            config=config,
            category=category,
            is_bot_owner=is_bot_owner,
        ):
            return
        raise PermissionDeniedError("只有当前分类 staff、Ticket 管理员或 Bot 所有者可以执行此操作。")

    def is_staff_actor(
        self,
        actor: Any,
        *,
        config: GuildConfigRecord,
        category: TicketCategoryConfig,
        is_bot_owner: bool,
    ) -> bool:
        if self.is_ticket_admin(actor, config=config, is_bot_owner=is_bot_owner):
            return True

        actor_id = getattr(actor, "id", None)
        if actor_id is None:
            return False
        if actor_id in set(self._parse_staff_user_ids(category.staff_user_ids_json)):
            return True

        actor_role_ids = self._extract_role_ids(getattr(actor, "roles", []))
        return category.staff_role_id is not None and category.staff_role_id in actor_role_ids

    @staticmethod
    def is_ticket_admin(
        actor: Any,
        *,
        config: GuildConfigRecord,
        is_bot_owner: bool,
    ) -> bool:
        if is_bot_owner:
            return True

        permissions = getattr(actor, "guild_permissions", None)
        if permissions is not None and getattr(permissions, "administrator", False):
            return True

        if config.admin_role_id is None:
            return False
        return config.admin_role_id in StaffGuardService._extract_role_ids(getattr(actor, "roles", []))

    @staticmethod
    def _parse_staff_user_ids(raw_value: str) -> list[int]:
        try:
            data = json.loads(raw_value or "[]")
        except json.JSONDecodeError:
            return []

        values: list[int] = []
        for item in data if isinstance(data, list) else []:
            try:
                values.append(int(item))
            except (TypeError, ValueError):
                continue
        return values

    @staticmethod
    def _extract_role_ids(roles: Iterable[Any]) -> set[int]:
        role_ids: set[int] = set()
        for role in roles:
            role_id = getattr(role, "id", None)
            if role_id is not None:
                role_ids.add(role_id)
        return role_ids
