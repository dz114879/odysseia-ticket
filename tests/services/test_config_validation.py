from __future__ import annotations

from core.enums import ClaimMode
from core.models import GuildConfigRecord
from services.config_validation import (
    validate_basic_settings,
    validate_close_transfer,
    validate_draft_timeouts,
    validate_snapshot_limits,
    validate_text_fields,
)

GUILD_ID = 100


def _default_config(**overrides) -> GuildConfigRecord:
    return GuildConfigRecord(guild_id=GUILD_ID, **overrides)


# ── validate_basic_settings ────────────────────────────────────


class TestValidateBasicSettings:
    def test_valid_timezone(self):
        parsed, errors = validate_basic_settings({"timezone": "Asia/Shanghai"}, _default_config())
        assert not errors
        assert parsed["timezone"] == "Asia/Shanghai"

    def test_invalid_timezone(self):
        parsed, errors = validate_basic_settings({"timezone": "Fake/Zone"}, _default_config())
        assert len(errors) == 1
        assert "无效的时区" in errors[0]

    def test_valid_max_open_tickets(self):
        parsed, errors = validate_basic_settings({"max_open_tickets": "50"}, _default_config())
        assert not errors
        assert parsed["max_open_tickets"] == 50

    def test_max_open_tickets_out_of_range(self):
        _, errors = validate_basic_settings({"max_open_tickets": "0"}, _default_config())
        assert len(errors) == 1

    def test_max_open_tickets_not_integer(self):
        _, errors = validate_basic_settings({"max_open_tickets": "abc"}, _default_config())
        assert len(errors) == 1

    def test_valid_claim_mode(self):
        parsed, errors = validate_basic_settings({"claim_mode": "strict"}, _default_config())
        assert not errors
        assert parsed["claim_mode"] == ClaimMode.STRICT

    def test_invalid_claim_mode(self):
        _, errors = validate_basic_settings({"claim_mode": "unknown"}, _default_config())
        assert len(errors) == 1

    def test_download_window_true_variants(self):
        for val in ("true", "1", "是", "yes", "on"):
            parsed, errors = validate_basic_settings({"enable_download_window": val}, _default_config())
            assert not errors
            assert parsed["enable_download_window"] is True

    def test_download_window_false_variants(self):
        for val in ("false", "0", "否", "no", "off"):
            parsed, errors = validate_basic_settings({"enable_download_window": val}, _default_config())
            assert not errors
            assert parsed["enable_download_window"] is False

    def test_download_window_invalid(self):
        _, errors = validate_basic_settings({"enable_download_window": "maybe"}, _default_config())
        assert len(errors) == 1

    def test_empty_fields_no_change(self):
        parsed, errors = validate_basic_settings(
            {"timezone": "", "max_open_tickets": "", "claim_mode": "", "enable_download_window": ""},
            _default_config(),
        )
        assert not errors
        assert not parsed or "timezone" in parsed


# ── validate_draft_timeouts ────────────────────────────────────


class TestValidateDraftTimeouts:
    def test_valid_values(self):
        parsed, errors = validate_draft_timeouts(
            {"draft_inactive_close_hours": "12", "draft_abandon_timeout_hours": "48"},
            _default_config(),
        )
        assert not errors
        assert parsed["draft_inactive_close_hours"] == 12
        assert parsed["draft_abandon_timeout_hours"] == 48

    def test_below_minimum(self):
        _, errors = validate_draft_timeouts({"draft_inactive_close_hours": "1"}, _default_config())
        assert len(errors) == 1

    def test_above_maximum(self):
        _, errors = validate_draft_timeouts({"draft_abandon_timeout_hours": "999"}, _default_config())
        assert len(errors) == 1

    def test_not_integer(self):
        _, errors = validate_draft_timeouts({"draft_inactive_close_hours": "abc"}, _default_config())
        assert len(errors) == 1


# ── validate_close_transfer ────────────────────────────────────


class TestValidateCloseTransfer:
    def test_valid_values(self):
        parsed, errors = validate_close_transfer(
            {"transfer_delay_seconds": "600", "close_revoke_window_seconds": "60", "close_request_timeout_seconds": "120"},
            _default_config(),
        )
        assert not errors
        assert parsed["transfer_delay_seconds"] == 600

    def test_out_of_range(self):
        _, errors = validate_close_transfer({"transfer_delay_seconds": "5"}, _default_config())
        assert len(errors) == 1


# ── validate_snapshot_limits ───────────────────────────────────


class TestValidateSnapshotLimits:
    def test_valid_values(self):
        parsed, errors = validate_snapshot_limits(
            {"snapshot_warning_threshold": "500", "snapshot_limit": "600"},
            _default_config(),
        )
        assert not errors
        assert parsed["snapshot_warning_threshold"] == 500
        assert parsed["snapshot_limit"] == 600

    def test_limit_must_exceed_threshold(self):
        _, errors = validate_snapshot_limits(
            {"snapshot_warning_threshold": "500", "snapshot_limit": "500"},
            _default_config(),
        )
        assert len(errors) == 1
        assert "大于" in errors[0]

    def test_uses_current_config_for_cross_validation(self):
        config = _default_config(snapshot_warning_threshold=800)
        _, errors = validate_snapshot_limits(
            {"snapshot_limit": "700"},
            config,
        )
        assert len(errors) == 1

    def test_out_of_range(self):
        _, errors = validate_snapshot_limits({"snapshot_warning_threshold": "50"}, _default_config())
        assert len(errors) == 1


# ── validate_text_fields ───────────────────────────────────────


class TestValidateTextFields:
    def test_empty_string_becomes_none(self):
        parsed, errors = validate_text_fields({"panel_title": "  "})
        assert not errors
        assert parsed["panel_title"] is None

    def test_valid_text(self):
        parsed, errors = validate_text_fields({"panel_title": "My Title"})
        assert not errors
        assert parsed["panel_title"] == "My Title"

    def test_exceeds_max_length(self):
        _, errors = validate_text_fields({"panel_title": "x" * 257})
        assert len(errors) == 1
        assert "256" in errors[0]

    def test_missing_fields_ignored(self):
        parsed, errors = validate_text_fields({})
        assert not errors
        assert not parsed

    def test_multiple_fields(self):
        parsed, errors = validate_text_fields({
            "panel_title": "Title",
            "panel_description": "Desc",
            "draft_welcome_text": "",
        })
        assert not errors
        assert parsed["panel_title"] == "Title"
        assert parsed["panel_description"] == "Desc"
        assert parsed["draft_welcome_text"] is None
