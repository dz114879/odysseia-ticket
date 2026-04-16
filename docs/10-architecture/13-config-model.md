# Config Model

This document describes configuration ownership in the current bot: which values exist, where they are edited, how they are validated, and when a config change actually affects live behavior.

## Purpose

- Separate guild runtime config, category config, and process env config.
- Record the real editable surfaces instead of the full dataclass shape only.
- Keep defaulting and side effects explicit so operators know what is immediate and what is not retroactive.

## Config Surfaces

There are four different configuration surfaces in the current system:

| Surface | Entry Point | Stored In | Main Owner |
|--------|-------------|-----------|------------|
| Guild runtime config | `/ticket config` | `guild_configs` | `cogs/config_cog.py`, `discord_ui/config_views.py`, `services/config_validation.py` |
| Guild setup targets | `/ticket setup` | `guild_configs` | `cogs/admin_cog.py`, `services/setup_service.py` |
| Category staff mapping | `/ticket permission` | `ticket_categories` | `cogs/permission_cog.py`, `services/permission_config_service.py` |
| Process/runtime settings | `.env` | process env | `config/env.py` |

Important distinction:

- `/ticket config` edits thresholds and text.
- `/ticket setup` edits guild wiring such as log/archive/category/admin-role targets.
- `/ticket permission` edits category-level staff assignments, not numeric thresholds or text.

## Storage Model

### `guild_configs`

`GuildConfigRecord` is the per-guild runtime config model. It currently stores:

- setup targets: log channel, archive channel, ticket category, admin role
- behavior settings: claim mode, max open tickets, timezone, download-window toggle
- timeout settings: draft inactivity/abandon, transfer delay, close revoke window, close request timeout
- snapshot thresholds: warning threshold and hard limit
- UI text overrides: panel text, draft welcome text, snapshot warning text, snapshot limit text
- extra persisted text fields not currently exposed in `/ticket config`: `close_request_text`, `closing_notice_text`, `close_revoke_text`

### `ticket_categories`

`TicketCategoryConfig` stores per-category presentation and staff mapping:

- `category_key`, `display_name`, `emoji`, `description`, `sort_order`
- `staff_role_ids_json`, `staff_user_ids_json`
- `is_enabled`
- `allowlist_role_ids_json`, `denylist_role_ids_json`

Current behavioral note:

- `allowlist_role_ids_json` and `denylist_role_ids_json` are persisted, but the current workflow does not enforce them.

## Editable Runtime Domains

These are the settings actually exposed today through `/ticket config`.

| Domain | Edited Via | Validation | Persistence | Operational Impact |
|-------|------------|------------|-------------|--------------------|
| Basic settings | `BasicSettingsModal` | `validate_basic_settings()` | `GuildRepository.update_config()` | changes claim mode, capacity limit, timezone, and archive download-window behavior immediately |
| Draft timeouts | `DraftTimeoutModal` | `validate_draft_timeouts()` | `GuildRepository.update_config()` | next draft message check / warning sweep / timeout sweep uses the new hours |
| Close and transfer | `CloseTransferModal` | `validate_close_transfer()` | `GuildRepository.update_config()` | next transfer scheduling, close request timeout, and revoke-window checks use the new values |
| Snapshot limits | `SnapshotLimitsModal` | `validate_snapshot_limits()` | `GuildRepository.update_config()` | next snapshot create/warning check and runtime-cache restore use the new thresholds |
| Panel text | `PanelTextModal` | `validate_text_fields()` | `GuildRepository.update_config()` | active public panel is refreshed immediately |
| Draft welcome text | `DraftWelcomeTextModal` | `validate_text_fields()` | `GuildRepository.update_config()` | affects newly created draft welcome embeds; existing pinned welcome messages stay as-is |
| Snapshot text | `SnapshotTextModal` | `validate_text_fields()` | `GuildRepository.update_config()` | affects future threshold warnings; previously sent warning messages stay as-is |

## Defaulting And Resolution

The current resolution order is:

1. `.env` is parsed by `config/env.py` for process-level settings.
2. Static defaults come from `config/static.py` and `config/defaults.py`.
3. Per-guild rows in `guild_configs` override those defaults where the feature reads guild config.
4. UI builders and services resolve the effective value at call time.

Examples:

- public panel title/body/footer fall back to `DEFAULT_PANEL_TITLE`, `DEFAULT_PANEL_BODY`, and `DEFAULT_PANEL_FOOTER_TEXT`
- draft welcome text falls back to `build_default_draft_welcome_text()` using the guild's current draft timeout hours
- snapshot warning/limit text falls back to `build_default_snapshot_warning_text()` and `build_default_snapshot_limit_text()` using the guild's current snapshot limit
- snapshot thresholds fall back to `SNAPSHOT_CREATE_WARNING_THRESHOLD` and `SNAPSHOT_CREATE_LIMIT` when guild config is unavailable

One compatibility detail stays important:

- `panel_bullet_points` is still stored and still understood by `build_public_panel_body()`, but the current config UI edits panel body as one merged text field and clears legacy bullet-point storage on rewrite.

## Validation Rules

Current validation behavior in `services/config_validation.py`:

| Category | Rules |
|----------|-------|
| Timezone | must be a valid IANA zone name accepted by `ZoneInfo`, for example `Asia/Shanghai` |
| Integers | all numeric runtime settings are bounded; zero and negative values are rejected |
| Booleans | `enable_download_window` accepts only explicit true/false-like strings |
| Enums | `claim_mode` accepts only `relaxed` or `strict` |
| Text fields | length-limited per field; blank means restore default by storing `None` |
| Cross-field | `snapshot_limit` must be greater than `snapshot_warning_threshold` |

Setup and permission config are validated separately:

- `/ticket setup` verifies the selected channels and admin role still exist in the guild
- `/ticket permission` verifies JSON shape, category keys, integer arrays, and referenced guild roles

## Side Effects And Non-Retroactive Behavior

The most important operational rules are:

- `/ticket config` changes are persisted immediately and do not require restart.
- Panel text changes auto-call `PanelService.refresh_active_panel()`.
- Permission JSON upload also refreshes the active public panel after apply.
- Timeout and threshold changes are picked up on the next relevant event or scheduler sweep, not by rewriting old messages.
- `/ticket permission` updates stored category config only; it does not bulk rewrite overwrites on already-open ticket channels.
- `/ticket setup` rewires future operations to new channels/roles, but does not move or rewrite existing ticket channels.
- Only `.env` changes require process restart.

## Current Gaps To Keep Explicit

These config-shaped fields exist in storage but are not fully exposed as operator-facing runtime config today:

- `close_request_text`, `closing_notice_text`, and `close_revoke_text` exist on `GuildConfigRecord` and in migrations, but the current `/ticket config` UI does not edit them
- `allowlist_role_ids_json` and `denylist_role_ids_json` exist on `TicketCategoryConfig`, but the current permission model does not consume them

Do not document those fields as live features unless their read paths are added.

## Related Docs

- `12-permission-model.md`
- `../20-modules/21-panel-and-draft.md`
- `../20-modules/22-submit-queue-capacity.md`
- `../20-modules/25-snapshot-and-notes.md`
- `../30-operations/31-config-runbook.md`

## When To Update

Update this document whenever:

- a new runtime setting is added or removed
- a persisted config field becomes operator-editable
- default-resolution rules change
- a config change becomes retroactive, or stops being retroactive
- category allowlist/denylist or close-text fields become truly wired into runtime behavior
