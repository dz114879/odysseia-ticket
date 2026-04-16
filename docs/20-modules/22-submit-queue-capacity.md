# Submit, Queue, and Capacity

This document covers the handoff from draft to staff-facing ticket handling, including submission guards, queue behavior, and ticket capacity rules.

## Scope

In scope:

- Draft submission
- Submission validation and guard checks
- Queue placement and activation logic
- Capacity enforcement
- Any config or state that changes whether a ticket can be accepted

Out of scope:

- Draft creation before submit
- Staff actions after a ticket is active

## User Entry Points

| Entry Point | Owning File | Primary Service |
|------------|-------------|-----------------|
| `/ticket submit` | `cogs/submit_cog.py` | `SubmissionGuardService.inspect_submission()` -> `SubmitService.submit_draft_ticket()` |
| Draft submit button | `discord_ui/draft_views.py` | `SubmissionGuardService.inspect_submission()` -> `SubmitService.submit_draft_ticket()` |
| Draft submit title modal | `discord_ui/draft_views.py` | `SubmitService.submit_draft_ticket()` |

## Owning Source Files

- `cogs/submit_cog.py`
- `discord_ui/draft_views.py`
- `services/submission_guard_service.py`
- `services/submit_service.py`
- `services/queue_service.py`
- `services/capacity_service.py`
- `db/repositories/ticket_repository.py`
- `db/repositories/guild_repository.py`

## Workflow Notes

Current control flow:

1. Submission starts from `/ticket submit`, the draft submit button, or `DraftSubmitTitleModal`.
2. The entry point calls `SubmissionGuardService.inspect_submission()` before deferring if it must decide whether to open the title modal.
3. `SubmitService.submit_draft_ticket()` acquires two locks:
   - `draft-submit:{channel_id}`
   - `ticket-submit-guild:{guild_id}`
4. The channel lock stays around the whole submit/promotion flow for per-ticket idempotency, while the guild lock is held only for the state-decision critical section.
5. Under the guild lock, `SubmitService` re-runs the guard checks and opens an explicit database transaction to commit the minimal lifecycle decision first:
   - `already_submitted`: keep `status=submitted`, but build a reconcile plan for missing submitted-side effects.
   - `already_queued`: keep `status=queued` and return the current queue position.
   - `draft` with no capacity: persist `status=queued` and `queued_at` before any Discord-side changes.
   - `draft` with capacity: persist `status=submitted` and clear `queued_at` before any Discord-side changes.
6. After the transaction commits and the guild lock is released, the service runs post-commit side effects from that plan:
   - queued path: rename if needed, remove the welcome view by stored `welcome_message_id`, keep staff hidden.
   - submitted path: rename if needed, grant staff access, bootstrap snapshots, send the divider for fresh submit/promotion, ensure a staff panel exists, remove the welcome view by stored `welcome_message_id`.
   - `already_submitted` path: re-run submitted-side reconciliation so missing permission sync, snapshot bootstrap, staff panel creation, or welcome-view cleanup can be repaired.
7. `QueueService.process_next_queued_ticket()` later reuses `SubmitService.promote_queued_ticket()` to turn `queued` into `submitted` when capacity becomes available.

## Submission Guards

`SubmissionGuardService` is the gatekeeper for both direct submit and queued promotion.

| Guard | Behavior |
|------|----------|
| Ticket must exist for the current channel | Missing channel-to-ticket mapping raises `TicketNotFoundError`. |
| Only the creator can submit | `ticket.creator_id` must match the actor. |
| Guild setup must be initialized | Missing or uninitialized config blocks submission. |
| Category config must still exist | Deleted/missing category config blocks submission. |
| Only `draft` can newly submit | `submitted` and `queued` are treated as idempotent return cases; all other states are rejected. |
| Default-title drafts require a title | If the current channel name still equals `category.display_name`, `requested_title` is required. |
| Queued promotion must still match the original channel | `inspect_queued_promotion()` verifies `ticket_id`, `channel_id`, and `status=queued`. |

## Queue and Capacity Rules

### Capacity Model

- Capacity is guild-wide, not per category. The limit comes from `GuildConfigRecord.max_open_tickets`.
- `CapacityService` counts only these statuses as active:
  - `submitted`
  - `transferring`
  - `closing`
  - `archiving`
  - `archive_sent`
- `draft`, `queued`, `sleep`, `archive_failed`, `channel_deleted`, `done`, and `abandoned` do not consume active capacity.
- A sleep ticket does not reserve capacity. Waking it later must still pass a fresh capacity check.

### Queue Order

- Queue order is FIFO per guild using:
  - `COALESCE(queued_at, created_at) ASC`
  - then `created_at ASC`
  - then `ticket_id ASC`
- Neither priority nor claim state changes queue order.
- Queue position exists only while `status=queued`.

### Promotion Behavior

- Promotion happens one ticket at a time per guild per `process_next_queued_ticket()` call.
- If the first queued ticket has a permanent problem:
  - missing channel -> mark `abandoned`, continue scanning
  - missing creator -> try deleting the channel, mark `abandoned`, continue scanning
- If the first queued ticket has a temporary resolution problem:
  - channel fetch failure -> defer promotion for now
  - creator fetch failure -> defer promotion for now
- If capacity disappears between the initial check and promotion, `SubmitService.promote_queued_ticket()` returns `None` and the ticket stays queued.

## Queue Fill Triggers

Queued tickets are not promoted only by the scheduler. The current release paths are:

- `BootstrapService._run_queue_sweep()` scheduled handler
- `SleepService.sleep_ticket()` after a submitted ticket enters `sleep`
- `ArchiveService.archive_ticket()` when the pipeline lands in `archive_failed`
- `ArchiveService._ensure_channel_deleted()` because deleting the live channel releases capacity
- `TransferService.cancel_transfer()` or `TransferService._execute_due_transfer()` when the restored status is `sleep`
- `CloseService.revoke_close()` when the restored status is `sleep`

If you add a new path that releases active capacity, it should probably trigger queue fill too.

## Rules To Keep Explicit

- Staff visibility starts only on the `submitted` path, not on the `queued` path.
- Missing title is collected before deferring by opening `DraftSubmitTitleModal`.
- Submission checks are intentionally re-run inside locks to avoid double-submit races.
- Submit/promotion now commit lifecycle state before Discord side effects run.
- Slow post-commit work such as snapshot bootstrap must stay outside `ticket-submit-guild:{guild_id}` so one large ticket does not serialize all same-guild submits/promotions.
- Welcome-view cleanup should use persisted message identity. Only legacy rows without `welcome_message_id` may fall back to a stricter pinned-message heuristic.
- Recovery for partially completed submit work happens by replaying submitted-side reconciliation, not by rolling back a committed `submitted` / `queued` status.
- Queueing is guild-scoped FIFO, not category-scoped.
- Capacity is a guild-scoped count of active lifecycle statuses, not a count of all open channels.
- Priority does not change queue order.
- Claim state does not change queue order.
- User feedback differs for `queued` versus `submitted`, and queued feedback should include position plus current active usage when available.

## User Feedback Notes

- Normal submit returns a short success result and posts a public divider message telling the creator staff can now see the ticket.
- Queued submit returns queue position and current active usage, and tells the creator the system will auto-submit the ticket later.
- Auto-promoted queued tickets post a different divider message so the creator can tell the handoff happened automatically.
- Re-submitting an already queued ticket returns the current position instead of mutating state again.

## Extension Checklist

- Are you introducing a new submission guard?
- Does the feature change queue ordering or fairness rules?
- Does the feature change which statuses count against `max_open_tickets`?
- Does the feature release capacity from a new transition and therefore need to trigger queue fill?
- Does the feature change whether queued tickets should remain hidden from staff?
- Do new side effects need to happen when a draft becomes submitted?
- Does the feature require additional reconcile/backfill behavior if post-commit side effects fail?

## Tests To Review

- `tests/services/test_submission_guard_service.py`
- `tests/services/test_submit_service.py`
- `tests/services/test_queue_service.py`
- `tests/services/test_capacity_service.py`
- `tests/cogs/test_submit_cog.py`
- `tests/services/test_sleep_service.py`
- `tests/services/test_close_service.py`
- `tests/services/test_transfer_service.py`

## Related Docs

- `21-panel-and-draft.md`
- `23-staff-actions.md`
- `../10-architecture/11-ticket-lifecycle.md`
- `../10-architecture/13-config-model.md`
