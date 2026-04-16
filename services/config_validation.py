from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo

from core.enums import ClaimMode
from core.models import GuildConfigRecord


def validate_basic_settings(
    raw: dict[str, str],
    current: GuildConfigRecord,
) -> tuple[dict[str, Any], list[str]]:
    parsed: dict[str, Any] = {}
    errors: list[str] = []

    tz_raw = raw.get("timezone", "").strip()
    if tz_raw:
        try:
            ZoneInfo(tz_raw)
            if tz_raw != current.timezone:
                parsed["timezone"] = tz_raw
        except (KeyError, ValueError):
            errors.append(f"无效的时区：{tz_raw!r}，请使用 IANA 时区名（如 Asia/Shanghai）。")

    max_raw = raw.get("max_open_tickets", "").strip()
    if max_raw:
        try:
            val = int(max_raw)
            if not 1 <= val <= 1000:
                errors.append("活跃工单上限必须在 1-1000 之间。")
            elif val != current.max_open_tickets:
                parsed["max_open_tickets"] = val
        except ValueError:
            errors.append("活跃工单上限必须是整数。")

    mode_raw = raw.get("claim_mode", "").strip().lower()
    if mode_raw:
        if mode_raw in ("relaxed", "strict"):
            mode = ClaimMode(mode_raw)
            if mode is not current.claim_mode:
                parsed["claim_mode"] = mode
        else:
            errors.append("认领模式必须是 relaxed 或 strict。")

    dw_raw = raw.get("enable_download_window", "").strip().lower()
    if dw_raw:
        if dw_raw in ("true", "1", "是", "yes", "on"):
            if current.enable_download_window is not True:
                parsed["enable_download_window"] = True
        elif dw_raw in ("false", "0", "否", "no", "off"):
            if current.enable_download_window is not False:
                parsed["enable_download_window"] = False
        else:
            errors.append("下载窗口必须是 是/否 或 true/false。")

    return parsed, errors


def validate_draft_timeouts(
    raw: dict[str, str],
    current: GuildConfigRecord,
) -> tuple[dict[str, Any], list[str]]:
    parsed: dict[str, Any] = {}
    errors: list[str] = []

    for field, label, lo, hi in (
        ("draft_inactive_close_hours", "不活跃关闭时间", 2, 168),
        ("draft_abandon_timeout_hours", "无消息废弃时间", 2, 720),
    ):
        val_raw = raw.get(field, "").strip()
        if val_raw:
            try:
                val = int(val_raw)
                if not lo <= val <= hi:
                    errors.append(f"{label}必须在 {lo}-{hi} 之间。")
                elif val != getattr(current, field):
                    parsed[field] = val
            except ValueError:
                errors.append(f"{label}必须是整数。")

    return parsed, errors


def validate_close_transfer(
    raw: dict[str, str],
    current: GuildConfigRecord,
) -> tuple[dict[str, Any], list[str]]:
    parsed: dict[str, Any] = {}
    errors: list[str] = []

    for field, label, lo, hi in (
        ("transfer_delay_seconds", "转交延迟", 10, 86400),
        ("close_revoke_window_seconds", "关闭撤销窗口", 10, 3600),
        ("close_request_timeout_seconds", "关闭请求超时", 10, 3600),
    ):
        val_raw = raw.get(field, "").strip()
        if val_raw:
            try:
                val = int(val_raw)
                if not lo <= val <= hi:
                    errors.append(f"{label}必须在 {lo}-{hi} 秒之间。")
                elif val != getattr(current, field):
                    parsed[field] = val
            except ValueError:
                errors.append(f"{label}必须是整数。")

    return parsed, errors


def validate_snapshot_limits(
    raw: dict[str, str],
    current: GuildConfigRecord,
) -> tuple[dict[str, Any], list[str]]:
    parsed: dict[str, Any] = {}
    errors: list[str] = []

    for field, label in (
        ("snapshot_warning_threshold", "快照警告阈值"),
        ("snapshot_limit", "快照上限"),
    ):
        val_raw = raw.get(field, "").strip()
        if val_raw:
            try:
                val = int(val_raw)
                if not 100 <= val <= 10000:
                    errors.append(f"{label}必须在 100-10000 之间。")
                elif val != getattr(current, field):
                    parsed[field] = val
            except ValueError:
                errors.append(f"{label}必须是整数。")

    threshold = parsed.get("snapshot_warning_threshold", current.snapshot_warning_threshold)
    limit = parsed.get("snapshot_limit", current.snapshot_limit)
    if not errors and limit <= threshold:
        errors.append(f"快照上限（{limit}）必须大于警告阈值（{threshold}）。")

    return parsed, errors


_TEXT_FIELD_LIMITS: dict[str, tuple[str, int]] = {
    "panel_title": ("面板标题", 256),
    "panel_description": ("面板正文", 4096),
    "panel_bullet_points": ("面板要点", 1024),
    "panel_footer_text": ("面板页脚", 2048),
    "draft_welcome_text": ("草稿欢迎文案", 4000),
    "snapshot_warning_text": ("快照警告文案", 1000),
    "snapshot_limit_text": ("快照上限文案", 1000),
    "close_request_text": ("关闭请求文案", 1000),
    "closing_notice_text": ("关闭通知文案", 1000),
    "close_revoke_text": ("关闭撤销文案", 1000),
}


def validate_text_fields(
    raw: dict[str, str],
) -> tuple[dict[str, str | None], list[str]]:
    parsed: dict[str, str | None] = {}
    errors: list[str] = []

    for field, (label, max_len) in _TEXT_FIELD_LIMITS.items():
        if field not in raw:
            continue
        val = raw[field].strip()
        if not val:
            parsed[field] = None
        elif len(val) > max_len:
            errors.append(f"{label}不能超过 {max_len} 个字符（当前 {len(val)}）。")
        else:
            parsed[field] = val

    return parsed, errors
