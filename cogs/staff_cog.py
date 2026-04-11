from __future__ import annotations

from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from cogs.ticket_command_groups import ticket_group
from core.enums import TicketPriority
from core.errors import (
    InvalidTicketStateError,
    PermissionDeniedError,
    TicketNotFoundError,
    ValidationError,
)
from discord_ui.help_text import build_ticket_help_message
from discord_ui.staff_feedback import (
    build_claim_success_message,
    build_mute_success_message,
    build_priority_success_message,
    build_sleep_success_message,
    build_rename_success_message,
    build_transfer_claim_success_message,
    build_transfer_success_message,
    build_untransfer_success_message,
    build_unmute_success_message,
    build_unclaim_success_message,
)
from discord_ui.staff_panel_view import StaffPanelView
from services.claim_service import ClaimService
from services.moderation_service import ModerationService
from services.priority_service import PriorityService
from services.rename_service import RenameService
from services.sleep_service import SleepService
from services.staff_panel_service import StaffPanelService
from services.transfer_service import TransferService


class StaffCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        resources = getattr(bot, "resources", None)
        if resources is None:
            raise RuntimeError("Bot resources 尚未初始化，无法加载 StaffCog。")

        self.bot = bot
        self.logging_service = resources.logging_service
        self.staff_panel_service = StaffPanelService(
            resources.database,
            bot=bot,
            debounce_manager=getattr(resources, "debounce_manager", None),
        )
        self.claim_service = ClaimService(
            resources.database,
            lock_manager=getattr(resources, "lock_manager", None),
            staff_panel_service=self.staff_panel_service,
        )
        self.rename_service = RenameService(
            resources.database,
            lock_manager=getattr(resources, "lock_manager", None),
        )
        self.moderation_service = ModerationService(
            resources.database,
            lock_manager=getattr(resources, "lock_manager", None),
            staff_panel_service=self.staff_panel_service,
        )
        self.priority_service = PriorityService(
            resources.database,
            lock_manager=getattr(resources, "lock_manager", None),
            staff_panel_service=self.staff_panel_service,
        )
        self.sleep_service = SleepService(
            resources.database,
            lock_manager=getattr(resources, "lock_manager", None),
            staff_panel_service=self.staff_panel_service,
        )
        self.transfer_service = TransferService(
            resources.database,
            lock_manager=getattr(resources, "lock_manager", None),
            staff_panel_service=self.staff_panel_service,
        )

        if not getattr(bot, "_staff_panel_view_registered", False):
            bot.add_view(StaffPanelView())
            setattr(bot, "_staff_panel_view_registered", True)

    @ticket_group.command(name="claim", description="认领当前 submitted ticket")
    @app_commands.guild_only()
    async def claim_command(self, interaction: discord.Interaction) -> None:
        await self.claim_current_ticket(interaction)

    @ticket_group.command(name="unclaim", description="取消认领当前 submitted ticket")
    @app_commands.guild_only()
    async def unclaim_command(self, interaction: discord.Interaction) -> None:
        await self.unclaim_current_ticket(interaction)

    @ticket_group.command(name="transfer-claim", description="将当前 submitted ticket 的认领转交给另一位 staff")
    @app_commands.guild_only()
    @app_commands.describe(member="要接手当前 ticket 认领的 staff 成员")
    async def transfer_claim_command(self, interaction: discord.Interaction, member: discord.Member) -> None:
        await self.transfer_claim_current_ticket(interaction, member=member)

    @ticket_group.command(name="mute", description="临时禁言当前 ticket 中的目标参与成员")
    @app_commands.guild_only()
    @app_commands.describe(
        member="要禁言的目标成员",
        duration="禁言时长，例如 30m / 2h / 1d；留空则手动解除",
        reason="禁言原因（可选）",
    )
    async def mute_command(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        duration: str | None = None,
        reason: str | None = None,
    ) -> None:
        await self.mute_current_ticket(interaction, member=member, duration=duration, reason=reason)

    @ticket_group.command(name="unmute", description="解除当前 ticket 中目标参与成员的禁言")
    @app_commands.guild_only()
    @app_commands.describe(member="要解除禁言的目标成员")
    async def unmute_command(self, interaction: discord.Interaction, member: discord.Member) -> None:
        await self.unmute_current_ticket(interaction, member=member)

    @ticket_group.command(name="priority", description="修改当前 submitted ticket 的优先级")
    @app_commands.guild_only()
    @app_commands.describe(priority="要设置的 ticket 优先级")
    @app_commands.choices(
        priority=[
            app_commands.Choice(name="低 🟢", value=TicketPriority.LOW.value),
            app_commands.Choice(name="中 🟡", value=TicketPriority.MEDIUM.value),
            app_commands.Choice(name="高 🔴", value=TicketPriority.HIGH.value),
            app_commands.Choice(name="紧急 ‼️", value=TicketPriority.EMERGENCY.value),
        ]
    )
    async def priority_command(
        self,
        interaction: discord.Interaction,
        priority: app_commands.Choice[str],
    ) -> None:
        await self.set_current_ticket_priority(interaction, priority=TicketPriority(priority.value))

    @ticket_group.command(name="sleep", description="将当前 submitted ticket 挂起为 sleep 状态")
    @app_commands.guild_only()
    async def sleep_command(self, interaction: discord.Interaction) -> None:
        await self.sleep_current_ticket(interaction)

    @ticket_group.command(name="rename", description="修改当前 submitted / sleep ticket 的标题")
    @app_commands.guild_only()
    @app_commands.describe(title="新的 ticket 标题")
    async def rename_command(
        self,
        interaction: discord.Interaction,
        title: str,
    ) -> None:
        await self.rename_current_ticket(interaction, title=title)

    @ticket_group.command(name="transfer", description="发起当前 ticket 的跨分类转交")
    @app_commands.guild_only()
    @app_commands.describe(
        target_category_key="目标分类的 category_key",
        reason="转交理由（可选）",
    )
    async def transfer_command(
        self,
        interaction: discord.Interaction,
        target_category_key: str,
        reason: str | None = None,
    ) -> None:
        await self.transfer_current_ticket(interaction, target_category_key=target_category_key, reason=reason)

    @ticket_group.command(name="untransfer", description="撤销当前 ticket 的跨分类转交")
    @app_commands.guild_only()
    async def untransfer_command(self, interaction: discord.Interaction) -> None:
        await self.untransfer_current_ticket(interaction)

    @ticket_group.command(name="help", description="查看当前 ticket 工作流帮助")
    @app_commands.guild_only()
    async def help_command(self, interaction: discord.Interaction) -> None:
        await self.show_ticket_help(interaction)

    async def claim_current_ticket(self, interaction: discord.Interaction) -> None:
        try:
            channel = self._require_ticket_channel(interaction)
            await self._defer_ephemeral(interaction)
            result = await self.claim_service.claim_ticket(
                channel,
                actor=interaction.user,
                is_bot_owner=await self.bot.is_owner(interaction.user),
            )
        except (
            TicketNotFoundError,
            InvalidTicketStateError,
            PermissionDeniedError,
            ValidationError,
        ) as exc:
            await self._send_ephemeral(interaction, str(exc))
            return

        self.logging_service.log_local_info(
            "Ticket claimed. ticket_id=%s claimer_id=%s changed=%s strict_mode=%s",
            result.ticket.ticket_id,
            result.ticket.claimed_by,
            result.changed,
            result.strict_mode,
        )
        await self._send_ephemeral(interaction, build_claim_success_message(result))

    async def unclaim_current_ticket(self, interaction: discord.Interaction) -> None:
        try:
            channel = self._require_ticket_channel(interaction)
            await self._defer_ephemeral(interaction)
            result = await self.claim_service.unclaim_ticket(
                channel,
                actor=interaction.user,
                is_bot_owner=await self.bot.is_owner(interaction.user),
            )
        except (
            TicketNotFoundError,
            InvalidTicketStateError,
            PermissionDeniedError,
            ValidationError,
        ) as exc:
            await self._send_ephemeral(interaction, str(exc))
            return

        self.logging_service.log_local_info(
            "Ticket unclaimed. ticket_id=%s previous_claimer_id=%s changed=%s forced=%s strict_mode=%s",
            result.ticket.ticket_id,
            result.previous_claimer_id,
            result.changed,
            result.forced,
            result.strict_mode,
        )
        await self._send_ephemeral(interaction, build_unclaim_success_message(result))

    async def transfer_claim_current_ticket(
        self,
        interaction: discord.Interaction,
        *,
        member: discord.Member | Any,
    ) -> None:
        try:
            channel = self._require_ticket_channel(interaction)
            await self._defer_ephemeral(interaction)
            result = await self.claim_service.transfer_claim(
                channel,
                actor=interaction.user,
                target=member,
                is_bot_owner=await self.bot.is_owner(interaction.user),
            )
        except (
            TicketNotFoundError,
            InvalidTicketStateError,
            PermissionDeniedError,
            ValidationError,
        ) as exc:
            await self._send_ephemeral(interaction, str(exc))
            return

        self.logging_service.log_local_info(
            "Ticket claim transferred. ticket_id=%s previous_claimer_id=%s new_claimer_id=%s changed=%s forced=%s strict_mode=%s",
            result.ticket.ticket_id,
            result.previous_claimer_id,
            result.ticket.claimed_by,
            result.changed,
            result.forced,
            result.strict_mode,
        )
        await self._send_ephemeral(interaction, build_transfer_claim_success_message(result))

    async def mute_current_ticket(
        self,
        interaction: discord.Interaction,
        *,
        member: discord.Member | Any,
        duration: str | None = None,
        reason: str | None = None,
    ) -> None:
        try:
            channel = self._require_ticket_channel(interaction)
            await self._defer_ephemeral(interaction)
            result = await self.moderation_service.mute_member(
                channel,
                actor=interaction.user,
                target=member,
                duration=duration,
                reason=reason,
                is_bot_owner=await self.bot.is_owner(interaction.user),
            )
        except (
            TicketNotFoundError,
            InvalidTicketStateError,
            PermissionDeniedError,
            ValidationError,
        ) as exc:
            await self._send_ephemeral(interaction, str(exc))
            return

        self.logging_service.log_local_info(
            "Ticket participant muted. ticket_id=%s target_id=%s expire_at=%s changed=%s",
            result.ticket.ticket_id,
            result.target_id,
            result.expire_at,
            result.changed,
        )
        await self._send_ephemeral(interaction, build_mute_success_message(result))

    async def unmute_current_ticket(
        self,
        interaction: discord.Interaction,
        *,
        member: discord.Member | Any,
    ) -> None:
        try:
            channel = self._require_ticket_channel(interaction)
            await self._defer_ephemeral(interaction)
            result = await self.moderation_service.unmute_member(
                channel,
                actor=interaction.user,
                target=member,
                is_bot_owner=await self.bot.is_owner(interaction.user),
            )
        except (
            TicketNotFoundError,
            InvalidTicketStateError,
            PermissionDeniedError,
            ValidationError,
        ) as exc:
            await self._send_ephemeral(interaction, str(exc))
            return

        self.logging_service.log_local_info(
            "Ticket participant unmuted. ticket_id=%s target_id=%s changed=%s",
            result.ticket.ticket_id,
            result.target_id,
            result.changed,
        )
        await self._send_ephemeral(interaction, build_unmute_success_message(result))

    async def set_current_ticket_priority(
        self,
        interaction: discord.Interaction,
        *,
        priority: TicketPriority,
    ) -> None:
        try:
            channel = self._require_ticket_channel(interaction)
            await self._defer_ephemeral(interaction)
            result = await self.priority_service.set_priority(
                channel,
                actor=interaction.user,
                priority=priority,
                is_bot_owner=await self.bot.is_owner(interaction.user),
            )
        except (
            TicketNotFoundError,
            InvalidTicketStateError,
            PermissionDeniedError,
            ValidationError,
        ) as exc:
            await self._send_ephemeral(interaction, str(exc))
            return

        self.logging_service.log_local_info(
            "Ticket priority updated. ticket_id=%s old_priority=%s new_priority=%s changed=%s channel_name_changed=%s",
            result.ticket_id,
            result.old_priority.value,
            result.new_priority.value,
            result.changed,
            result.channel_name_changed,
        )
        await self._send_ephemeral(interaction, build_priority_success_message(result))

    async def sleep_current_ticket(self, interaction: discord.Interaction) -> None:
        try:
            channel = self._require_ticket_channel(interaction)
            await self._defer_ephemeral(interaction)
            result = await self.sleep_service.sleep_ticket(
                channel,
                actor=interaction.user,
                is_bot_owner=await self.bot.is_owner(interaction.user),
            )
        except (
            TicketNotFoundError,
            InvalidTicketStateError,
            PermissionDeniedError,
            ValidationError,
        ) as exc:
            await self._send_ephemeral(interaction, str(exc))
            return

        self.logging_service.log_local_info(
            "Ticket entered sleep. ticket_id=%s previous_priority=%s channel_name_changed=%s",
            result.ticket.ticket_id,
            result.previous_priority.value,
            result.channel_name_changed,
        )
        await self._send_ephemeral(interaction, build_sleep_success_message(result))

    async def rename_current_ticket(
        self,
        interaction: discord.Interaction,
        *,
        title: str,
    ) -> None:
        try:
            channel = self._require_ticket_channel(interaction)
            await self._defer_ephemeral(interaction)
            result = await self.rename_service.rename_ticket(
                channel,
                actor=interaction.user,
                requested_name=title,
                is_bot_owner=await self.bot.is_owner(interaction.user),
            )
        except (
            TicketNotFoundError,
            InvalidTicketStateError,
            PermissionDeniedError,
            ValidationError,
        ) as exc:
            await self._send_ephemeral(interaction, str(exc))
            return

        self.logging_service.log_local_info(
            "Ticket renamed. ticket_id=%s status=%s old_name=%s new_name=%s changed=%s",
            result.ticket.ticket_id,
            result.ticket.status.value,
            result.old_name,
            result.new_name,
            result.changed,
        )
        await self._send_ephemeral(interaction, build_rename_success_message(result))

    async def transfer_current_ticket(
        self,
        interaction: discord.Interaction,
        *,
        target_category_key: str,
        reason: str | None = None,
    ) -> None:
        try:
            channel = self._require_ticket_channel(interaction)
            await self._defer_ephemeral(interaction)
            result = await self.transfer_service.transfer_ticket(
                channel,
                actor=interaction.user,
                target_category_key=target_category_key,
                reason=reason,
                is_bot_owner=await self.bot.is_owner(interaction.user),
            )
        except (
            TicketNotFoundError,
            InvalidTicketStateError,
            PermissionDeniedError,
            ValidationError,
        ) as exc:
            await self._send_ephemeral(interaction, str(exc))
            return

        self.logging_service.log_local_info(
            "Ticket transfer initiated. ticket_id=%s previous_status=%s target_category=%s initiated_by=%s",
            result.ticket.ticket_id,
            result.previous_status.value,
            result.target_category.category_key,
            getattr(interaction.user, "id", None),
        )
        await self._send_ephemeral(interaction, build_transfer_success_message(result))

    async def untransfer_current_ticket(self, interaction: discord.Interaction) -> None:
        try:
            channel = self._require_ticket_channel(interaction)
            await self._defer_ephemeral(interaction)
            result = await self.transfer_service.cancel_transfer(
                channel,
                actor=interaction.user,
                is_bot_owner=await self.bot.is_owner(interaction.user),
            )
        except (
            TicketNotFoundError,
            InvalidTicketStateError,
            PermissionDeniedError,
            ValidationError,
        ) as exc:
            await self._send_ephemeral(interaction, str(exc))
            return

        self.logging_service.log_local_info(
            "Ticket transfer cancelled. ticket_id=%s restored_status=%s target_category=%s cancelled_by=%s",
            result.ticket.ticket_id,
            result.restored_status.value,
            result.previous_target_category_key,
            getattr(interaction.user, "id", None),
        )
        await self._send_ephemeral(interaction, build_untransfer_success_message(result))

    async def show_ticket_help(self, interaction: discord.Interaction) -> None:
        try:
            self._require_guild_context(interaction)
        except ValidationError as exc:
            await self._send_ephemeral(interaction, str(exc))
            return

        self.logging_service.log_local_info(
            "Ticket help requested. channel_id=%s user_id=%s",
            getattr(getattr(interaction, "channel", None), "id", None),
            getattr(getattr(interaction, "user", None), "id", None),
        )
        await self._send_ephemeral(interaction, build_ticket_help_message())

    @staticmethod
    def _require_ticket_channel(interaction: discord.Interaction) -> Any:
        if interaction.guild is None:
            raise ValidationError("该命令只能在服务器中使用。")
        channel = interaction.channel
        if channel is None or getattr(channel, "guild", None) is None:
            raise ValidationError("当前频道不支持 staff ticket 操作。")
        return channel

    @staticmethod
    def _require_guild_context(interaction: discord.Interaction) -> Any:
        if interaction.guild is None:
            raise ValidationError("该命令只能在服务器中使用。")
        return interaction.channel

    @staticmethod
    async def _defer_ephemeral(interaction: discord.Interaction) -> None:
        if interaction.response.is_done():
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

    @staticmethod
    async def _send_ephemeral(interaction: discord.Interaction, content: str) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
            return
        await interaction.response.send_message(content, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    try:
        bot.tree.add_command(ticket_group)
    except app_commands.CommandAlreadyRegistered:
        pass
    await bot.add_cog(StaffCog(bot))
