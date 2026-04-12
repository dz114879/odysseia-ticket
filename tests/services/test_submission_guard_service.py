from __future__ import annotations

import pytest

from core.enums import ClaimMode, TicketStatus
from core.errors import InvalidTicketStateError, PermissionDeniedError, TicketNotFoundError, ValidationError
from core.models import GuildConfigRecord, TicketCategoryConfig, TicketRecord
from db.repositories.guild_repository import GuildRepository
from db.repositories.ticket_repository import TicketRepository
from services.submission_guard_service import SubmissionGuardService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def prepared_submission_guard_context(migrated_database):
    guild_repository = GuildRepository(migrated_database)
    ticket_repository = TicketRepository(migrated_database)
    guard_service = SubmissionGuardService(
        migrated_database,
        ticket_repository=ticket_repository,
        guild_repository=guild_repository,
    )

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
            staff_role_id=500,
            staff_user_ids_json="[301]",
            is_enabled=True,
            allowlist_role_ids_json="[]",
            denylist_role_ids_json="[]",
            sort_order=1,
        )
    )

    ticket = ticket_repository.create(
        TicketRecord(
            ticket_id="1-support-0001",
            guild_id=1,
            creator_id=201,
            category_key="support",
            channel_id=9001,
            status=TicketStatus.DRAFT,
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
            has_user_message=True,
            last_user_message_at="2024-01-01T01:00:00+00:00",
        )
    )

    return {
        "database": migrated_database,
        "guard_service": guard_service,
        "guild_repository": guild_repository,
        "ticket_repository": ticket_repository,
        "ticket": ticket,
    }


# ---------------------------------------------------------------------------
# requires_title_completion (static method)
# ---------------------------------------------------------------------------


class TestRequiresTitleCompletion:
    def test_returns_true_when_channel_name_equals_category_display_name(self) -> None:
        assert SubmissionGuardService.requires_title_completion(
            channel_name="技术支持",
            category_display_name="技术支持",
        ) is True

    def test_returns_false_when_channel_name_differs_from_category_display_name(self) -> None:
        assert SubmissionGuardService.requires_title_completion(
            channel_name="login-fails-badly",
            category_display_name="技术支持",
        ) is False

    def test_returns_false_when_channel_name_is_none(self) -> None:
        assert SubmissionGuardService.requires_title_completion(
            channel_name=None,
            category_display_name="技术支持",
        ) is False

    def test_returns_false_when_channel_name_is_empty_string(self) -> None:
        assert SubmissionGuardService.requires_title_completion(
            channel_name="",
            category_display_name="技术支持",
        ) is False

    def test_case_sensitive_comparison(self) -> None:
        # Different case should not match
        assert SubmissionGuardService.requires_title_completion(
            channel_name="Support",
            category_display_name="support",
        ) is False


# ---------------------------------------------------------------------------
# inspect_submission
# ---------------------------------------------------------------------------


class TestInspectSubmission:
    def test_draft_ticket_with_default_channel_name_requires_title(self, prepared_submission_guard_context) -> None:
        guard_service = prepared_submission_guard_context["guard_service"]

        context = guard_service.inspect_submission(
            channel_id=9001,
            actor_id=201,
            channel_name="技术支持",
        )

        assert context.ticket.ticket_id == "1-support-0001"
        assert context.requires_title is True
        assert context.already_submitted is False
        assert context.already_queued is False
        assert context.category.category_key == "support"
        assert context.config.guild_id == 1

    def test_draft_ticket_with_custom_channel_name_does_not_require_title(self, prepared_submission_guard_context) -> None:
        guard_service = prepared_submission_guard_context["guard_service"]

        context = guard_service.inspect_submission(
            channel_id=9001,
            actor_id=201,
            channel_name="login-issue",
        )

        assert context.requires_title is False
        assert context.already_submitted is False
        assert context.already_queued is False

    def test_draft_ticket_with_none_channel_name_does_not_require_title(self, prepared_submission_guard_context) -> None:
        guard_service = prepared_submission_guard_context["guard_service"]

        context = guard_service.inspect_submission(
            channel_id=9001,
            actor_id=201,
            channel_name=None,
        )

        assert context.requires_title is False

    def test_already_submitted_ticket_returns_idempotent_context(self, prepared_submission_guard_context) -> None:
        guard_service = prepared_submission_guard_context["guard_service"]
        ticket_repository = prepared_submission_guard_context["ticket_repository"]
        ticket_repository.update("1-support-0001", status=TicketStatus.SUBMITTED)

        context = guard_service.inspect_submission(
            channel_id=9001,
            actor_id=201,
            channel_name="技术支持",
        )

        assert context.already_submitted is True
        assert context.requires_title is False

    def test_already_queued_ticket_returns_queued_context(self, prepared_submission_guard_context) -> None:
        guard_service = prepared_submission_guard_context["guard_service"]
        ticket_repository = prepared_submission_guard_context["ticket_repository"]
        ticket_repository.update("1-support-0001", status=TicketStatus.QUEUED, queued_at="2024-01-01T02:00:00+00:00")

        context = guard_service.inspect_submission(
            channel_id=9001,
            actor_id=201,
            channel_name="技术支持",
        )

        assert context.already_queued is True
        assert context.requires_title is False

    def test_rejects_non_draft_status_other_than_submitted_and_queued(self, prepared_submission_guard_context) -> None:
        guard_service = prepared_submission_guard_context["guard_service"]
        ticket_repository = prepared_submission_guard_context["ticket_repository"]
        ticket_repository.update("1-support-0001", status=TicketStatus.CLOSING)

        with pytest.raises(InvalidTicketStateError, match="draft"):
            guard_service.inspect_submission(
                channel_id=9001,
                actor_id=201,
                channel_name="技术支持",
            )

    def test_rejects_non_creator_actor(self, prepared_submission_guard_context) -> None:
        guard_service = prepared_submission_guard_context["guard_service"]

        with pytest.raises(PermissionDeniedError, match="创建者"):
            guard_service.inspect_submission(
                channel_id=9001,
                actor_id=999,
                channel_name="技术支持",
            )

    def test_raises_ticket_not_found_for_unknown_channel(self, prepared_submission_guard_context) -> None:
        guard_service = prepared_submission_guard_context["guard_service"]

        with pytest.raises(TicketNotFoundError, match="ticket"):
            guard_service.inspect_submission(
                channel_id=99999,
                actor_id=201,
                channel_name="技术支持",
            )

    def test_raises_validation_error_when_guild_not_initialized(self, prepared_submission_guard_context) -> None:
        guard_service = prepared_submission_guard_context["guard_service"]
        guild_repository = prepared_submission_guard_context["guild_repository"]
        guild_repository.update_config(1, is_initialized=False)

        with pytest.raises(ValidationError, match="setup"):
            guard_service.inspect_submission(
                channel_id=9001,
                actor_id=201,
                channel_name="技术支持",
            )

    def test_raises_validation_error_when_category_missing(self, prepared_submission_guard_context) -> None:
        guard_service = prepared_submission_guard_context["guard_service"]
        ticket_repository = prepared_submission_guard_context["ticket_repository"]
        # Point ticket at a nonexistent category
        ticket_repository.update("1-support-0001", category_key="nonexistent")

        with pytest.raises(ValidationError, match="分类配置已缺失"):
            guard_service.inspect_submission(
                channel_id=9001,
                actor_id=201,
                channel_name="技术支持",
            )


# ---------------------------------------------------------------------------
# inspect_queued_promotion
# ---------------------------------------------------------------------------


class TestInspectQueuedPromotion:
    def test_happy_path_returns_context_for_queued_ticket(self, prepared_submission_guard_context) -> None:
        guard_service = prepared_submission_guard_context["guard_service"]
        ticket_repository = prepared_submission_guard_context["ticket_repository"]
        ticket_repository.update("1-support-0001", status=TicketStatus.QUEUED, queued_at="2024-01-01T02:00:00+00:00")

        context = guard_service.inspect_queued_promotion(
            ticket_id="1-support-0001",
            channel_id=9001,
        )

        assert context.ticket.ticket_id == "1-support-0001"
        assert context.requires_title is False
        assert context.config.guild_id == 1
        assert context.category.category_key == "support"

    def test_raises_ticket_not_found_for_unknown_ticket_id(self, prepared_submission_guard_context) -> None:
        guard_service = prepared_submission_guard_context["guard_service"]

        with pytest.raises(TicketNotFoundError, match="ticket"):
            guard_service.inspect_queued_promotion(
                ticket_id="1-support-9999",
                channel_id=9001,
            )

    def test_raises_validation_error_when_channel_id_mismatches(self, prepared_submission_guard_context) -> None:
        guard_service = prepared_submission_guard_context["guard_service"]
        ticket_repository = prepared_submission_guard_context["ticket_repository"]
        ticket_repository.update("1-support-0001", status=TicketStatus.QUEUED, queued_at="2024-01-01T02:00:00+00:00")

        with pytest.raises(ValidationError, match="频道上下文已失效"):
            guard_service.inspect_queued_promotion(
                ticket_id="1-support-0001",
                channel_id=99999,
            )

    def test_raises_invalid_state_error_when_not_queued(self, prepared_submission_guard_context) -> None:
        guard_service = prepared_submission_guard_context["guard_service"]
        # Ticket is still in DRAFT status

        with pytest.raises(InvalidTicketStateError, match="queued"):
            guard_service.inspect_queued_promotion(
                ticket_id="1-support-0001",
                channel_id=9001,
            )

    def test_raises_validation_error_when_guild_not_initialized(self, prepared_submission_guard_context) -> None:
        guard_service = prepared_submission_guard_context["guard_service"]
        ticket_repository = prepared_submission_guard_context["ticket_repository"]
        guild_repository = prepared_submission_guard_context["guild_repository"]
        ticket_repository.update("1-support-0001", status=TicketStatus.QUEUED, queued_at="2024-01-01T02:00:00+00:00")
        guild_repository.update_config(1, is_initialized=False)

        with pytest.raises(ValidationError, match="setup"):
            guard_service.inspect_queued_promotion(
                ticket_id="1-support-0001",
                channel_id=9001,
            )
