# Close, Archive, and Recovery

This module doc covers how tickets are closed, how transcripts and cleanup run, and what recovery paths exist when channels or close flows do not complete normally.

## Scope

In scope:

- Direct close by staff
- Creator close request flow
- Close request approval, rejection, timeout, replacement, and dismissal
- Closing revoke window
- Transcript rendering and archive delivery
- Post-close cleanup
- Channel deletion recovery and interrupted archive recovery

Out of scope:

- General staff actions before close
- Snapshot browsing UI details unless close behavior depends on them

## User Entry Points

| Entry Point | Owning File | Primary Service |
|------------|-------------|-----------------|
| `/ticket close` by staff | `cogs/close_cog.py` | `CloseService.initiate_close()` |
| `/ticket close` by creator | `cogs/close_cog.py` | `CloseRequestService.request_close()` after direct-close path rejects |
| `/ticket close-cancel` | `cogs/close_cog.py` | `CloseService.revoke_close()` |
| Staff panel close button | `discord_ui/staff_panel_view.py` | `CloseService.initiate_close()` |
| Close request approve button | `discord_ui/close_views.py` | `CloseRequestService.approve_request()` |
| Close request reject button | `discord_ui/close_views.py` | `CloseRequestService.reject_request()` |
| Close request timeout | `discord_ui/close_views.py` | `CloseRequestService.expire_request_message()` |
| Startup recovery | `BootstrapService.bootstrap()` | `RecoveryService.recover_incomplete_archive_flows()` |
| Scheduled archive/close recovery | `BootstrapService._run_archive_recovery_sweep()` | `RecoveryService.sweep_recoverable_tickets()` |
| Channel deleted event | `bot.py:on_guild_channel_delete()` | `RecoveryService.handle_channel_deleted()` |

## Owning Source Files

- `cogs/close_cog.py`
- `discord_ui/close_views.py`
- `discord_ui/close_embeds.py`
- `discord_ui/close_feedback.py`
- `services/close_service.py`
- `services/close_notice_support.py`
- `services/close_permission_support.py`
- `services/close_request_service.py`
- `services/archive_service.py`
- `services/archive_render_service.py`
- `services/archive_send_service.py`
- `services/cleanup_service.py`
- `services/recovery_service.py`

## Close Flow Split

`/ticket close` has two very different behaviors behind one command:

- Staff / ticket admin / bot owner:
  - enters the direct close flow immediately through `CloseService.initiate_close()`
- Ticket creator:
  - direct close fails the staff guard
  - `CloseCog` catches that `PermissionDeniedError`
  - the same command then falls back to `CloseRequestService.request_close()`

This means the creator and staff share one slash command surface, but not one close path.

## Close Request Flow

Close requests are intentionally message-scoped, not persisted in `tickets.status`.

### Rules

- Only the creator may create a close request.
- The ticket must be in `submitted` or `sleep`.
- A new close request replaces the previous pending request for that channel.
- Approve/reject actions are validated against the current pending message ID to reject stale button clicks.
- If staff directly use `/ticket close`, any pending request message is dismissed as already handled.

### Outcomes

| Action | Result |
|--------|--------|
| Creator requests close | sends a timed `CloseRequestView` message in-channel |
| Staff approves | starts the normal direct close flow and edits the request message to an approved state |
| Staff rejects | leaves the ticket in `submitted` or `sleep`, edits the request message, and posts a public rejection notice |
| Request times out | edits the request message to expired and clears it from the in-memory pending map |
| Creator submits another request | old request message is edited to "replaced", new one becomes current |

## Closing Window

Once direct close starts, the ticket enters `closing`.

### What `CloseService.initiate_close()` does

1. Validates that the ticket is `submitted` or `sleep`.
2. Applies staff guard checks.
3. If the source state is `sleep`, checks active capacity first because `closing` consumes capacity.
4. Stores:
   - `status=closing`
   - `status_before`
   - `close_reason`
   - `close_initiated_by`
   - `close_execute_at`
   - `closed_at`
5. Freezes the channel into readonly mode through `services/close_permission_support.py`.
6. Posts a closing notice embed with `ClosingNoticeView`.
7. Requests staff panel refresh.

### Revoke Rules

- `CloseService.revoke_close()` is valid only while the ticket is still `closing`.
- It also requires the revoke window to still be open.
- On revoke, the service restores `status_before`, clears close metadata, recalculates permissions, edits the closing notice into a revoked state, and refreshes the panel.
- If the restored state is `sleep`, the service triggers queue fill because capacity is released.

## Runtime Scheduling Note

There is an important implementation detail here:

- `CloseService.sweep_due_closing_tickets()` exists, but it is not wired into the bootstrap scheduler.
- Actual runtime progression from due `closing` tickets into archiving currently happens through `RecoveryService.sweep_recoverable_tickets()`.
- That recovery sweep runs:
  - once during bootstrap
  - repeatedly through `ticket.archive_recovery_sweep`

When maintaining this area, treat close-expiry and archive-recovery as one runtime pipeline unless the scheduler design changes.

## Archive Pipeline

The archive pipeline is owned by `ArchiveService.archive_ticket()`.

### Current Order

1. Load the current ticket under lock.
2. If already `done`, return idempotently.
3. If `archive_failed` and retry is not allowed, return current state.
4. If retry is allowed from `archive_failed`, move back to `archiving`.
5. If the ticket is `closing` and due, advance it to `archiving`.
6. Materialize archive output:
   - try live transcript render from channel history
   - fall back to snapshots transcript if live render/channel is unavailable
   - send archive embed + transcript file to the configured archive channel
   - persist `archive_message_id`, `archived_at`, and `message_count`
7. Delete the live ticket channel, producing `channel_deleted`.
8. Run cleanup, producing `done`.

### Archive Artifacts

- `ArchiveRenderService` writes an HTML transcript into `storage/exports/` during rendering.
- `ArchiveSendService` uploads that transcript to the archive channel with:
  - `build_archive_record_embed(ticket)`
  - filename `{ticket_id}-transcript.html`
- Live transcripts include:
  - message content
  - attachments
  - embed content
  - snapshot-derived edit/deleted-message annotations when available
- Fallback transcripts include:
  - visible reconstructed messages from snapshots
  - a snapshot timeline section
  - a notice that the transcript was generated from fallback data

## Cleanup

`CleanupService.cleanup_ticket()` currently removes:

- all mute records for the ticket
- `storage/snapshots/{ticket_id}.jsonl`
- `storage/snapshots/{ticket_id}.jsonl.tmp`
- `storage/notes/{ticket_id}.jsonl`
- archive/export HTML files matching the ticket boundary
- in-memory snapshot cache for the ticket channel, if present

After successful cleanup, `ArchiveService` also clears transient workflow metadata in the database such as `status_before`, transfer metadata, `close_execute_at`, `staff_panel_message_id`, `priority_before_sleep`, and `archive_last_error`.

## Recovery Behavior

`RecoveryService` is the operational safety net for interrupted close/archive flows.

### Sweep Targets

The recovery sweep currently revisits:

- due `closing` tickets
- `archiving`
- `archive_sent`
- `channel_deleted`
- retryable `archive_failed`

### Automatic Retry of `archive_failed`

Automatic retry is intentionally narrow:

- retry count must be below `archive_retry_limit`
- `archive_last_error` must contain a retryable token such as:
  - `temporary`
  - `timeout`
  - `timed out`
  - `connection reset`
  - `network`
  - `rate limit`
  - `503`
  - `500`

If the failure is not retryable, recovery only logs that it skipped the ticket.

### Channel Deleted Event

`bot.py:on_guild_channel_delete()` routes deleted ticket channels into `RecoveryService.handle_channel_deleted()`:

- if the ticket is `closing` or `archiving`:
  - force archive immediately
  - ignore due time
  - force snapshots fallback transcript
- if the ticket is already `archive_sent` or `channel_deleted`:
  - resume the remaining pipeline idempotently

This is the main protection against operators or Discord-side events deleting the live channel before archive completion.

## Failure Modes

- Creator runs `/ticket close` and nothing closes:
  expected if they are not staff; the command should create a close request instead.
- Close request buttons stop working:
  likely cause is the request was replaced, timed out, or already handled, so the interaction is stale.
- Ticket is stuck in `closing` past the displayed execution time:
  likely cause is the recovery sweep is not running or has failed.
- Ticket reaches `archive_failed`:
  transcript generation or archive send failed; auto-retry depends on retryable error classification.
- Transcript was sent but channel still exists:
  likely cause is channel deletion failed or channel resolution temporarily failed after `archive_sent`.
- Channel was deleted before archive send:
  recovery should fall back to snapshots, but only if enough snapshot data exists.

## Guardrails

- Do not turn close requests into a second persisted lifecycle state without updating the lifecycle and permission docs.
- Do not bypass `CloseService` when starting `closing`, because the readonly freeze and revoke metadata are part of the contract.
- Do not bypass `ArchiveService` when resuming partial archive flows; its idempotent branching is the safety boundary.
- If you add new archive artifacts, update `CleanupService` so `done` actually means cleaned up.
- If you change how due closing tickets advance, update both this doc and `26-runtime-bootstrap-scheduler.md`.

## Extension Checklist

- Does the feature affect who can close directly versus who can only request close?
- Does it change the request replacement, timeout, or stale-interaction behavior?
- Does it add a new partial state that recovery must resume?
- Does it change transcript contents, render mode choice, archive destination, or file naming?
- Does cleanup need to preserve or delete additional files or records?
- Does the runtime scheduler still advance due closes and archive recovery correctly?

## Tests To Review

- `tests/cogs/test_close_cog.py`
- `tests/services/test_close_request_service.py`
- `tests/services/test_close_service.py`
- `tests/services/test_archive_render_service.py`
- `tests/services/test_cleanup_service.py`
- `tests/services/test_recovery_service.py`

## Related Docs

- `23-staff-actions.md`
- `25-snapshot-and-notes.md`
- `../10-architecture/11-ticket-lifecycle.md`
- `../30-operations/32-troubleshooting.md`
