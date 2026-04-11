from __future__ import annotations

from dataclasses import dataclass
import sqlite3

from core.enums import TicketStatus
from core.errors import InvalidTicketStateError, PermissionDeniedError, TicketNotFoundError, ValidationError
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.connection import DatabaseManager
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository


@dataclass(frozen=True, slots=True)
class SubmissionContext:
    ticket: TicketRecord
    config: GuildConfigRecord
    category: TicketCategoryConfig
    requires_title: bool
    already_submitted: bool = False
    already_queued: bool = False


class SubmissionGuardService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        ticket_repository: TicketRepository | None = None,
        guild_repository: GuildRepository | None = None,
    ) -> None:
        self.database = database
        self.ticket_repository = ticket_repository or TicketRepository(database)
        self.guild_repository = guild_repository or GuildRepository(database)

    def inspect_submission(
        self,
        *,
        channel_id: int,
        actor_id: int,
        channel_name: str | None,
        connection: sqlite3.Connection | None = None,
    ) -> SubmissionContext:
        ticket = self.ticket_repository.get_by_channel_id(channel_id, connection=connection)
        if ticket is None:
            raise TicketNotFoundError("当前频道未关联 ticket 记录。")
        if ticket.creator_id != actor_id:
            raise PermissionDeniedError("只有当前 ticket 的创建者可以执行提交。")

        config, category = self._load_submission_target(ticket, connection=connection)

        if ticket.status is TicketStatus.SUBMITTED:
            return SubmissionContext(
                ticket=ticket,
                config=config,
                category=category,
                requires_title=False,
                already_submitted=True,
            )
        if ticket.status is TicketStatus.QUEUED:
            return SubmissionContext(
                ticket=ticket,
                config=config,
                category=category,
                requires_title=False,
                already_queued=True,
            )
        if ticket.status is not TicketStatus.DRAFT:
            raise InvalidTicketStateError("当前 ticket 已不处于 draft 状态，无法执行提交。")

        return SubmissionContext(
            ticket=ticket,
            config=config,
            category=category,
            requires_title=self.requires_title_completion(ticket=ticket, channel_name=channel_name),
        )

    def inspect_queued_promotion(
        self,
        *,
        ticket_id: str,
        channel_id: int,
        connection: sqlite3.Connection | None = None,
    ) -> SubmissionContext:
        ticket = self.ticket_repository.get_by_ticket_id(ticket_id, connection=connection)
        if ticket is None:
            raise TicketNotFoundError("当前 ticket 不存在，无法执行排队出队。")
        if ticket.channel_id != channel_id:
            raise ValidationError("当前 queued ticket 的频道上下文已失效，无法执行自动出队。")
        if ticket.status is not TicketStatus.QUEUED:
            raise InvalidTicketStateError("当前 ticket 不处于 queued 状态，无法执行自动出队。")
        config, category = self._load_submission_target(ticket, connection=connection)
        return SubmissionContext(
            ticket=ticket,
            config=config,
            category=category,
            requires_title=False,
        )

    def _load_submission_target(
        self,
        ticket: TicketRecord,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> tuple[GuildConfigRecord, TicketCategoryConfig]:
        config = self.guild_repository.get_config(ticket.guild_id, connection=connection)
        if config is None or not config.is_initialized:
            raise ValidationError("当前服务器尚未完成 ticket setup，无法提交。")

        category = self.guild_repository.get_category(
            ticket.guild_id,
            ticket.category_key,
            connection=connection,
        )
        if category is None:
            raise ValidationError("当前 ticket 的分类配置已缺失，无法继续提交。")
        return config, category

    @staticmethod
    def requires_title_completion(*, ticket: TicketRecord, channel_name: str | None) -> bool:
        if not channel_name:
            return False

        ticket_number = SubmissionGuardService._extract_ticket_number(ticket.ticket_id)
        if ticket_number is None:
            return False

        expected_name = SubmissionGuardService._build_default_channel_name(
            category_key=ticket.category_key,
            ticket_number=ticket_number,
        )
        return channel_name == expected_name

    @staticmethod
    def _extract_ticket_number(ticket_id: str) -> int | None:
        _, _, suffix = ticket_id.rpartition("-")
        if not suffix.isdigit():
            return None
        return int(suffix)

    @staticmethod
    def _build_default_channel_name(*, category_key: str, ticket_number: int) -> str:
        slug = SubmissionGuardService._slugify(category_key)
        return f"ticket-{slug}-{ticket_number:04d}"[:95]

    @staticmethod
    def _slugify(value: str) -> str:
        normalized = [character.lower() if character.isalnum() else "-" for character in value]
        slug = "".join(normalized).strip("-")
        while "--" in slug:
            slug = slug.replace("--", "-")
        return slug or "ticket"
