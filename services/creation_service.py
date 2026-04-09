from __future__ import annotations

import json
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any, AsyncIterator

import discord

from core.enums import TicketStatus
from core.errors import ValidationError
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.connection import DatabaseManager
from db.repositories.counter_repository import CounterRepository
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository
from runtime.locks import LockManager
from services.validation_service import ValidationService


@dataclass(frozen=True, slots=True)
class DraftCreationResult:
    ticket: TicketRecord
    channel: Any
    welcome_message: Any | None
    created: bool


class CreationService:
    def __init__(
        self,
        database: DatabaseManager,
        *,
        validation_service: ValidationService | None = None,
        guild_repository: GuildRepository | None = None,
        ticket_repository: TicketRepository | None = None,
        counter_repository: CounterRepository | None = None,
        lock_manager: LockManager | None = None,
    ) -> None:
        self.database = database
        self.validation_service = validation_service or ValidationService(database)
        self.guild_repository = guild_repository or GuildRepository(database)
        self.ticket_repository = ticket_repository or TicketRepository(database)
        self.counter_repository = counter_repository or CounterRepository(database)
        self.lock_manager = lock_manager

    async def create_draft_ticket(
        self,
        *,
        guild: Any,
        creator: Any,
        category_key: str,
        source_panel_message_id: int | None = None,
        source_panel_nonce: str | None = None,
    ) -> DraftCreationResult:
        async with self._acquire_creation_lock(guild.id, creator.id):
            existing_draft = self._find_existing_draft(guild, creator.id)
            if existing_draft is not None:
                existing_channel = guild.get_channel(existing_draft.channel_id)
                if existing_channel is not None:
                    return DraftCreationResult(
                        ticket=existing_draft,
                        channel=existing_channel,
                        welcome_message=None,
                        created=False,
                    )

            with self.database.session() as connection:
                config, category = self._resolve_creation_target(
                    guild_id=guild.id,
                    category_key=category_key,
                    source_panel_message_id=source_panel_message_id,
                    source_panel_nonce=source_panel_nonce,
                    connection=connection,
                )
                ticket_number = self._reserve_ticket_number(
                    guild_id=guild.id,
                    category_key=category.category_key,
                    connection=connection,
                )

            ticket_id = self.build_ticket_id(
                guild_id=guild.id,
                category_key=category.category_key,
                ticket_number=ticket_number,
            )
            channel_name = self.build_default_channel_name(
                category_key=category.category_key,
                ticket_number=ticket_number,
            )
            parent_channel = self._require_ticket_parent_channel(guild, config)
            overwrites = self._build_draft_overwrites(
                guild=guild,
                creator=creator,
                config=config,
                category=category,
            )

            channel = None
            welcome_message = None
            try:
                channel = await guild.create_text_channel(
                    channel_name,
                    category=parent_channel,
                    overwrites=overwrites,
                    topic=self._build_channel_topic(ticket_id=ticket_id, creator_id=creator.id),
                    reason=f"Create draft ticket {ticket_id}",
                )
                welcome_message = await channel.send(
                    content=self._build_welcome_message(
                        creator_id=creator.id,
                        ticket_id=ticket_id,
                        category=category,
                    )
                )
                await self._pin_welcome_message(welcome_message)
                ticket = self.ticket_repository.create(
                    TicketRecord(
                        ticket_id=ticket_id,
                        guild_id=guild.id,
                        creator_id=creator.id,
                        category_key=category.category_key,
                        channel_id=channel.id,
                        status=TicketStatus.DRAFT,
                        created_at="",
                        updated_at="",
                        has_user_message=False,
                    )
                )
            except Exception:
                if channel is not None:
                    with suppress(Exception):
                        await channel.delete(reason="Rollback failed draft ticket creation")
                raise

            return DraftCreationResult(
                ticket=ticket,
                channel=channel,
                welcome_message=welcome_message,
                created=True,
            )

    def _resolve_creation_target(
        self,
        *,
        guild_id: int,
        category_key: str,
        source_panel_message_id: int | None,
        source_panel_nonce: str | None,
        connection,
    ) -> tuple[GuildConfigRecord, TicketCategoryConfig]:
        if source_panel_message_id is not None or source_panel_nonce is not None:
            if source_panel_message_id is None or source_panel_nonce is None:
                raise ValidationError("缺少面板上下文，无法继续创建 draft ticket。")
            validation = self.validation_service.validate_panel_request(
                guild_id,
                nonce=source_panel_nonce,
                message_id=source_panel_message_id,
                category_key=category_key,
                connection=connection,
            )
            return validation.config, validation.category

        config, _ = self.validation_service.assert_panel_creation_ready(
            guild_id,
            connection=connection,
        )
        category = self.guild_repository.get_category(
            guild_id,
            category_key,
            connection=connection,
        )
        if category is None or not category.is_enabled:
            raise ValidationError("该分类当前不可用。")
        return config, category

    def _find_existing_draft(self, guild: Any, creator_id: int) -> TicketRecord | None:
        drafts = self.ticket_repository.list_by_guild(
            guild.id,
            statuses=[TicketStatus.DRAFT],
            creator_id=creator_id,
        )
        for draft in reversed(drafts):
            if draft.channel_id is None:
                continue
            if guild.get_channel(draft.channel_id) is not None:
                return draft
        return None

    def _reserve_ticket_number(
        self,
        *,
        guild_id: int,
        category_key: str,
        connection,
    ) -> int:
        counter = self.counter_repository.increment(
            guild_id,
            category_key,
            connection=connection,
        )
        return counter.next_number - 1

    def _require_ticket_parent_channel(self, guild: Any, config: GuildConfigRecord) -> Any:
        parent_channel = guild.get_channel(config.ticket_category_channel_id)
        if parent_channel is None:
            raise ValidationError("Ticket 承载分类不存在，请重新执行 /ticket setup。")
        return parent_channel

    def _build_draft_overwrites(
        self,
        *,
        guild: Any,
        creator: Any,
        config: GuildConfigRecord,
        category: TicketCategoryConfig,
    ) -> dict[Any, discord.PermissionOverwrite]:
        overwrites: dict[Any, discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            creator: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            ),
        }

        bot_member = getattr(guild, "me", None)
        if bot_member is not None:
            overwrites[bot_member] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_channels=True,
                manage_messages=True,
                attach_files=True,
                embed_links=True,
            )

        admin_role = guild.get_role(config.admin_role_id) if config.admin_role_id is not None else None
        if admin_role is not None:
            overwrites[admin_role] = discord.PermissionOverwrite(view_channel=False)

        staff_role = guild.get_role(category.staff_role_id) if category.staff_role_id is not None else None
        if staff_role is not None:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=False)

        get_member = getattr(guild, "get_member", None)
        if callable(get_member):
            for staff_user_id in self._parse_staff_user_ids(category.staff_user_ids_json):
                staff_member = get_member(staff_user_id)
                if staff_member is None or staff_member == creator:
                    continue
                overwrites[staff_member] = discord.PermissionOverwrite(view_channel=False)

        return overwrites

    @staticmethod
    def build_ticket_id(*, guild_id: int, category_key: str, ticket_number: int) -> str:
        slug = CreationService._slugify(category_key)
        return f"{guild_id}-{slug}-{ticket_number:04d}"

    @staticmethod
    def build_default_channel_name(*, category_key: str, ticket_number: int) -> str:
        slug = CreationService._slugify(category_key)
        channel_name = f"ticket-{slug}-{ticket_number:04d}"
        return channel_name[:95]

    @staticmethod
    def _build_channel_topic(*, ticket_id: str, creator_id: int) -> str:
        return f"ticket_id={ticket_id} creator_id={creator_id} status=draft"

    @staticmethod
    def _build_welcome_message(
        *,
        creator_id: int,
        ticket_id: str,
        category: TicketCategoryConfig,
    ) -> str:
        lines = [
            f"您好 <@{creator_id}>，您的 draft ticket 已创建。",
            f"- Ticket ID：`{ticket_id}`",
            f"- 分类：{category.display_name}",
            "- 当前阶段：draft（暂不对 staff 开放）",
            "请直接在此频道发送第一条消息描述问题，后续提交流程会在下一阶段接入。",
        ]
        if category.extra_welcome_text:
            lines.append(f"补充提示：{category.extra_welcome_text}")
        return "\n".join(lines)

    @staticmethod
    async def _pin_welcome_message(message: Any) -> None:
        pin = getattr(message, "pin", None)
        if pin is None:
            return
        with suppress(Exception):
            await pin(reason="Pin ticket draft welcome message")

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
    def _slugify(value: str) -> str:
        normalized = [character.lower() if character.isalnum() else "-" for character in value]
        slug = "".join(normalized).strip("-")
        while "--" in slug:
            slug = slug.replace("--", "-")
        return slug or "ticket"

    @asynccontextmanager
    async def _acquire_creation_lock(
        self,
        guild_id: int,
        creator_id: int,
    ) -> AsyncIterator[None]:
        if self.lock_manager is None:
            yield
            return

        lock_key = f"draft-create:{guild_id}:{creator_id}"
        async with self.lock_manager.acquire(lock_key):
            yield
