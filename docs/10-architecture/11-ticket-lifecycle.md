# Ticket Lifecycle

This document is the canonical state map for tickets from draft creation to final archival or abandonment. Keep UI details in the module docs; keep durable state rules here.

## Purpose

- Define the real `TicketStatus` state machine.
- Record which service owns each transition.
- Separate true persisted states from message-level or runtime-only branches.
- Capture invariants that queueing, permissions, and recovery must preserve.

## Non-State Branches

Two important workflows are intentionally not additional `TicketStatus` values:

- "Close request" is a message/view workflow owned by `CloseRequestService` and `CloseRequestView`. It is tracked in memory by `_pending_request_message_ids`, not in `tickets.status`.
- Claim, unclaim, mute, unmute, rename, and priority changes do not change lifecycle status. They mutate side fields around the current status.

There is also no terminal `closed` status in the database. The close path is represented as:

`submitted/sleep` -> `closing` -> `archiving` -> `archive_sent` -> `channel_deleted` -> `done`

## Lifecycle Map

Current end-to-end flow:

1. `CreationService.create_draft_ticket()` creates a `draft` channel and `tickets` row.
2. The creator may rename the draft, write messages, or abandon it manually. `DraftTimeoutService` may also warn and auto-abandon it.
3. `SubmitService.submit_draft_ticket()` evaluates the draft:
   - If capacity is available, the ticket becomes `submitted`.
   - If capacity is full, the ticket becomes `queued`.
4. `QueueService.process_next_queued_ticket()` promotes queued tickets to `submitted` when capacity is available, or marks broken queued tickets as `abandoned`.
5. From `submitted`, staff-side flows can mutate claim/priority/mute metadata, move the ticket to `sleep`, start a delayed category transfer, or start closing.
6. `sleep` can wake back to `submitted` on any new non-bot message in the channel if the guild still has active capacity.
7. `transferring` is a scheduled intermediate state. It restores to `submitted` or `sleep` when canceled or when the delayed execution completes.
8. `closing` is a revoke window. It restores to `submitted` or `sleep` if revoked; otherwise it advances into archiving when due.
9. `archiving` generates and sends the transcript. It either becomes `archive_sent` or `archive_failed`.
10. `archive_sent` deletes the live channel, producing `channel_deleted`, then cleanup produces `done`.
11. `archive_failed`, `channel_deleted`, and interrupted close/archive flows may be resumed by `RecoveryService`.

## State Inventory

All persisted lifecycle states come from `core/enums.py:TicketStatus` and are stored in `tickets.status`.

| State | Entered By | Exited By | Important Stored Fields | Notes |
|-------|------------|-----------|--------------------------|-------|
| `draft` | `CreationService.create_draft_ticket()` | `SubmitService.submit_draft_ticket()`, `DraftService.abandon_draft_ticket()`, `DraftTimeoutService._apply_timeout_if_needed()` | `channel_id`, `has_user_message`, `last_user_message_at` | Creator-only pre-submit state. |
| `queued` | `SubmitService._enqueue_ticket()`, `QueueService.enqueue_ticket()` | `SubmitService.promote_queued_ticket()`, `QueueService._mark_abandoned()` | `queued_at` | Staff still stay hidden; does not consume active capacity. |
| `submitted` | `SubmitService._execute_submission()`, `SleepService.wake_ticket()`, `TransferService.cancel_transfer()`, `TransferService._execute_due_transfer()`, `CloseService.revoke_close()` | `SleepService.sleep_ticket()`, `TransferService.transfer_ticket()`, `CloseService.initiate_close()` | `staff_panel_message_id`, `claimed_by`, `priority`, `snapshot_bootstrapped_at` | Main active working state. |
| `sleep` | `SleepService.sleep_ticket()`, `TransferService.cancel_transfer()`, `TransferService._execute_due_transfer()`, `CloseService.revoke_close()` | `SleepService.wake_ticket()`, `TransferService.transfer_ticket()`, `CloseService.initiate_close()` | `priority=TicketPriority.SLEEP`, `priority_before_sleep` | Inactive from the capacity model. |
| `transferring` | `TransferService.transfer_ticket()` | `TransferService.cancel_transfer()`, `TransferService._execute_due_transfer()` | `status_before`, `transfer_target_category`, `transfer_initiated_by`, `transfer_reason`, `transfer_execute_at`, `transfer_history_json` | Category has not changed yet; delayed execution pending. |
| `closing` | `CloseService.initiate_close()` | `CloseService.revoke_close()`, `ArchiveService._advance_closing_to_archiving_if_due()` | `status_before`, `close_reason`, `close_initiated_by`, `close_execute_at`, `closed_at` | Read-only revoke window before archive starts. |
| `archiving` | `ArchiveService._advance_closing_to_archiving_if_due()`, `RecoveryService` retry path from `archive_failed` | `ArchiveService._ensure_archive_materialized()` | `archive_last_error` cleared before retry | Transcript render/send is in progress. |
| `archive_sent` | `ArchiveService._ensure_archive_materialized()` | `ArchiveService._ensure_channel_deleted()` | `archive_message_id`, `archived_at`, `message_count` | Transcript exists; live channel may still exist briefly. |
| `archive_failed` | `ArchiveService._mark_archive_failed()` | `RecoveryService.sweep_recoverable_tickets()` retry path to `archiving`, or manual operator intervention | `archive_last_error`, `archive_attempts` | Not active for capacity counting. |
| `channel_deleted` | `ArchiveService._ensure_channel_deleted()` | `ArchiveService._ensure_cleanup_completed()` | `archive_message_id`, `archived_at` | Transcript sent and live channel gone; cleanup still pending. |
| `done` | `ArchiveService._ensure_cleanup_completed()` | Terminal | workflow fields are cleared: `status_before`, transfer metadata, `close_execute_at`, `staff_panel_message_id`, `priority_before_sleep`, `archive_last_error` | Final archived state. |
| `abandoned` | `DraftService.abandon_draft_ticket()`, `DraftTimeoutService._apply_timeout_if_needed()`, `QueueService._mark_abandoned()` | Terminal | `queued_at` cleared if abandoned from queue | Final non-archived state for discarded drafts/queued tickets. |

## Transition Ownership

| Transition | Owning Service | Guards | Important Side Effects |
|------------|----------------|--------|------------------------|
| `draft` -> `submitted` | `SubmitService` | `SubmissionGuardService.inspect_submission()`, `CapacityService.build_snapshot()` | rename channel if needed, grant current-category staff access, bootstrap snapshots, send divider, send staff panel, clear welcome view |
| `draft` -> `queued` | `SubmitService` | `SubmissionGuardService.inspect_submission()`, no capacity available | rename channel if needed, set `queued_at`, keep staff hidden, clear welcome view |
| `draft` -> `abandoned` | `DraftService`, `DraftTimeoutService` | creator-only or timeout check | delete channel; roll back to `draft` if delete fails |
| `queued` -> `submitted` | `QueueService` -> `SubmitService.promote_queued_ticket()` | channel and creator must still resolve, `SubmissionGuardService.inspect_queued_promotion()`, capacity available | same side effects as normal submit, but divider text indicates auto-promotion |
| `queued` -> `abandoned` | `QueueService` | permanent missing channel or creator | optionally delete orphaned channel, clear `queued_at`, continue scanning later queued tickets |
| `submitted` -> `sleep` | `SleepService` | `StaffGuardService`, submitted-only | rename channel with sleep prefix, store `priority_before_sleep`, sync permissions, trigger queue fill |
| `sleep` -> `submitted` | `SleepService` | new non-bot message, capacity available | restore previous priority, rename channel, sync permissions |
| `submitted/sleep` -> `transferring` | `TransferService` | `StaffGuardService`, claimer rules, enabled target category, extra capacity check when source state is `sleep` | store `status_before`, target category, reason, delayed execute time |
| `transferring` -> `submitted/sleep` | `TransferService.cancel_transfer()` | current ticket must still be `transferring` | clear transfer metadata, maybe trigger queue fill if restored to `sleep` |
| `transferring` -> `submitted/sleep` with category switch | `TransferService._execute_due_transfer()` | due time reached and target category still valid | change `category_key`, clear claimer, append `transfer_history_json`, sync permissions with old category hidden |
| `submitted/sleep` -> `closing` | `CloseService` directly, or `CloseRequestService.approve_request()` -> `CloseService` | `StaffGuardService`, extra capacity check when source state is `sleep` | store close metadata, freeze channel to readonly, post closing notice, refresh staff panel |
| `closing` -> `submitted/sleep` | `CloseService.revoke_close()` | still inside revoke window | clear close metadata, restore permissions, edit closing notice, maybe trigger queue fill if restored to `sleep` |
| `closing` -> `archiving` | `ArchiveService` | close window due, or recovery forces archive after missing-channel detection | clear archive error, start transcript pipeline |
| `archiving` -> `archive_sent` | `ArchiveService` | archive channel resolvable and transcript render/send succeeds | store `archive_message_id`, `archived_at`, `message_count` |
| `archiving` -> `archive_failed` | `ArchiveService` | live render and fallback both fail, or archive channel unavailable | increment `archive_attempts`, store `archive_last_error`, send failure log, trigger queue fill |
| `archive_failed` -> `archiving` | `RecoveryService` | retryable error token and retry limit not exceeded | clear last error and retry pipeline |
| `archive_sent` -> `channel_deleted` | `ArchiveService` | source channel can be deleted or is already missing | delete live channel, trigger queue fill |
| `channel_deleted` -> `done` | `ArchiveService` | cleanup succeeds | remove mute/cache/file leftovers and clear transient workflow fields |

## Lifecycle Invariants

- `tickets.status` is the only authoritative lifecycle state source. Close requests are intentionally outside this state machine.
- `queued`, `sleep`, `archive_failed`, `channel_deleted`, `done`, and `abandoned` do not consume active capacity.
- `submitted`, `transferring`, `closing`, `archiving`, and `archive_sent` do consume active capacity.
- `status_before` is rollback metadata, not a second source of truth. It is meaningful only while a ticket is `transferring` or `closing`.
- Queue filling should happen when capacity is released, not on unrelated ticket mutations.
- Permission changes must stay aligned with lifecycle transitions. In particular, queued tickets must not silently gain staff visibility.
- The archive pipeline must be idempotent enough for bootstrap recovery, scheduled recovery sweeps, and channel-delete recovery to resume partially completed flows.
- `abandoned` and `done` are terminal in the current design. There is no built-in reopen path.

## Edge Cases

- Draft warnings are sent one hour before timeout, but only while the ticket is still `draft`.
- A queued ticket is deferred, not abandoned, when channel/member resolution fails due to temporary Discord errors.
- A queued ticket is abandoned when its source channel is gone or its creator is no longer resolvable.
- Transfer and close initiated from `sleep` require spare active capacity because they move the ticket back into a capacity-consuming state.
- `archive_failed` can be auto-retried only for a limited set of retryable error tokens and only up to the retry limit.
- `on_guild_channel_delete` routes missing-channel cases into `RecoveryService.handle_channel_deleted()`, which may force fallback transcript generation.
- `archive_sent` is not the end of the lifecycle. The live channel can still exist until deletion succeeds.

## Tests to Review

- `tests/services/test_creation_service.py`
- `tests/services/test_draft_service.py`
- `tests/services/test_draft_timeout_service.py`
- `tests/services/test_submit_service.py`
- `tests/services/test_queue_service.py`
- `tests/services/test_capacity_service.py`
- `tests/services/test_sleep_service.py`
- `tests/services/test_transfer_service.py`
- `tests/services/test_close_request_service.py`
- `tests/services/test_close_service.py`
- `tests/services/test_recovery_service.py`
- `tests/services/test_cleanup_service.py`

## When To Update

Update this document whenever:

- A `TicketStatus` value is added, removed, or renamed.
- A lifecycle transition changes owners or timing.
- Capacity counting changes for any state.
- A non-persistent branch becomes persisted, or the reverse.
- Recovery starts resuming a new partial state.
