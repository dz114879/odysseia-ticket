# Config Runbook

This runbook is for operators changing live-server configuration. It focuses on safe change order, verification, and rollback.

## Config Surfaces

| Need | Use | Takes Effect | Important Note |
|------|-----|--------------|----------------|
| Log channel, archive channel, ticket category, admin role | `/ticket setup` | Immediately after command success | Existing category rows are kept if they already exist. |
| Runtime thresholds and text | `/ticket config` | Immediately | Only panel text edits auto-refresh the active public panel. |
| Per-category staff mapping | `/ticket permission` | Immediately | Active panel is refreshed, but existing ticket channel overwrites are not bulk rewritten. |
| Public panel message state | `/ticket panel create`, `/ticket panel refresh`, `/ticket panel remove` | Immediately | Refresh rotates the panel nonce; older buttons become stale by design. |
| Process settings and paths | `.env` + restart | Next process start | `DISCORD_BOT_TOKEN`, `DISCORD_APP_ID`, `LOG_FILE`, `SQLITE_PATH`, `SCHEDULER_INTERVAL_SECONDS`, and `AUTO_SYNC_COMMANDS` live here. |

## Before Changing Anything

- Confirm the target guild and environment.
- Record the current values or keep the previous JSON / `.env` nearby for rollback.
- Change one logical area at a time: setup, runtime config, permission JSON, or env.
- If the change affects capacity, timeout, or permissions, note that in-flight tickets may behave differently from newly created tickets.

## Re-run `/ticket setup`

Use this when the server-level targets changed:

- log channel
- archive channel
- ticket category container
- ticket admin role

Procedure:

1. Run `/ticket setup` with the new channels and role.
2. Verify the success message shows the expected IDs.
3. Check the configured log channel receives the setup update log.
4. Create one low-risk draft and confirm it lands under the new ticket category container.
5. If archive destination changed, verify the next closed ticket goes to the new archive channel.

Operational notes:

- `SetupService` validates that the selected channels and role still exist in the guild.
- Existing category config is preserved on reconfiguration; default categories are created only when no category rows exist yet.
- Re-running setup does not rewrite old ticket-channel permissions or move existing ticket channels.

## Edit Runtime Settings With `/ticket config`

Current runtime groups and guardrails:

| Group | Fields |
|------|--------|
| Basic | `timezone` (IANA name), `max_open_tickets` (`1-1000`), `claim_mode` (`relaxed/strict`), `enable_download_window` |
| Draft timeout | `draft_inactive_close_hours` (`2-168`), `draft_abandon_timeout_hours` (`2-720`) |
| Close and transfer | `transfer_delay_seconds` (`10-86400`), `close_revoke_window_seconds` (`10-3600`), `close_request_timeout_seconds` (`10-3600`) |
| Snapshot limits | `snapshot_warning_threshold`, `snapshot_limit` (`100-10000`), and `snapshot_limit > snapshot_warning_threshold` |
| Text | panel title/body/footer, draft welcome text, snapshot warning text, snapshot limit text; blank restores default |

Procedure:

1. Open `/ticket config`.
2. Change one group only.
3. If validation fails, fix the input instead of stacking more changes.
4. Re-open the affected UI or workflow and verify the new behavior.

Operational notes:

- Numeric and enum validation is enforced before persistence.
- Text fields backed by defaults are stored as `NULL` when you submit the current default or leave them blank.
- Panel text updates call `PanelService.refresh_active_panel()` automatically.
- Timeout, capacity, close-window, and snapshot-limit changes do not need restart; the next service check or scheduler sweep uses the new value.

## Upload Permission JSON With `/ticket permission`

Procedure:

1. Prepare JSON with the exact category keys under `categories`.
2. Upload it with `/ticket permission`.
3. Confirm validation passes before treating the change as applied.
4. Verify the active public panel still renders correctly after the automatic refresh.
5. Verify staff access on a representative new ticket in the affected category.

Operational notes:

- Validation checks root shape, category existence, integer arrays, and guild role existence for `staff_role_ids`.
- Only categories present in the uploaded JSON are updated. Omitted categories stay unchanged.
- This command updates stored category config, not every existing live ticket channel overwrite.
- Existing `submitted` / `sleep` tickets keep their current overwrites until a later lifecycle action recalculates them through `apply_ticket_permissions()` or a new ticket is created.

## Maintain The Public Panel

Use these commands deliberately:

- `/ticket panel refresh`: use after panel text changes, category presentation changes, or stale panel reports.
- `/ticket panel create`: use when there is no active panel or the old message/host channel is gone.
- `/ticket panel remove`: retire the stored active panel record; `delete_message=true` also attempts to delete the Discord message.

Checks:

- If refresh says the active panel message is missing, recreate it instead of retrying refresh.
- After refresh, older panel messages should fail with a stale-panel error; that is expected because the nonce changed.

## Change `.env` And Restart

Use `.env` only for process-level settings:

- `DISCORD_BOT_TOKEN`
- `DISCORD_APP_ID`
- `BOT_PREFIX`
- `SQLITE_PATH`
- `LOG_LEVEL`
- `LOG_FILE`
- `SCHEDULER_INTERVAL_SECONDS`
- `AUTO_SYNC_COMMANDS`

Procedure:

1. Edit `.env`.
2. Restart the bot process.
3. Check startup logs for:
   - `Bootstrapping ...`
   - `Bootstrap finished.`
   - `... is ready as ...`
   - `Application commands synced:` when `AUTO_SYNC_COMMANDS=true`

Operational notes:

- Relative `SQLITE_PATH` and `LOG_FILE` are resolved against `BASE_DIR`.
- If `AUTO_SYNC_COMMANDS` is disabled, restart alone will not push slash-command updates.

## Rollback

| Change Type | Rollback |
|------------|----------|
| `/ticket setup` | Re-run `/ticket setup` with the previous channels and admin role. |
| `/ticket config` numeric / enum values | Reapply the previous values through `/ticket config`. |
| `/ticket config` text values | Submit blank to restore defaults, or re-enter the previous custom text. |
| `/ticket permission` | Re-upload the previous known-good JSON. |
| Panel state | Recreate or refresh the panel. |
| `.env` | Restore the previous values and restart the process. |

## Operator Handoff

After any non-trivial change, record:

- what changed
- when it changed
- which guild or environment it changed in
- what you verified manually
- whether in-flight tickets need special attention because the change is not retroactive

## Related Docs

- `../10-architecture/13-config-model.md`
- `../20-modules/21-panel-and-draft.md`
- `../20-modules/22-submit-queue-capacity.md`
- `../20-modules/26-runtime-bootstrap-scheduler.md`
