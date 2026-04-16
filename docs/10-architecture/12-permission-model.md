# Permission Model

This document explains how ticket channel access is derived and recomputed. It is the source of truth for permission behavior, not for Discord UI details.

## Purpose

- Define all permission inputs and where they come from.
- Explain the order of overwrite calculation.
- Record when channel overwrites are recomputed and when they are intentionally not.
- Keep the "do not bypass this" rules explicit for future services.

## Hard Rules

These are the current architectural constraints:

- Active-ticket permission recomputation should go through `StaffPermissionService.apply_ticket_permissions()`.
- Ticket categories support multiple staff roles and multiple explicit staff users.
- Claim mode (`relaxed` or `strict`) is part of permission behavior, not just UI state.
- Services should ask the permission service to recalculate access instead of hand-building ad hoc staff/participant plans.

There are two intentional exceptions:

- Draft creation uses `CreationService._build_draft_overwrites()` to create the initial private channel before the ticket enters the active workflow.
- Close start uses `CloseService._freeze_ticket_permissions()` to force the entire channel into a temporary readonly window before archiving.

## Permission Inputs

| Input | Source | Effect |
|------|--------|--------|
| `admin_role_id` | `GuildConfigRecord` | Explicitly grants the configured admin role access in every active category plan. |
| Category staff roles | `TicketCategoryConfig.staff_role_ids_json` | Explicit category-scoped staff visibility. |
| Category staff users | `TicketCategoryConfig.staff_user_ids_json` | Explicit user-scoped staff visibility. |
| Discord administrator / bot owner | `StaffGuardService.is_ticket_admin()` | Authorizes actions even if not explicitly listed in category staff config. This is a guard-layer concept, not a dedicated overwrite target. |
| Claim mode | `GuildConfigRecord.claim_mode` | `RELAXED`: visible staff can write. `STRICT`: visible staff are readonly unless they are the active claimer. |
| Active claimer | `TicketRecord.claimed_by` | May receive explicit write access, especially in strict mode. |
| Previous claimer | workflow metadata passed to permission service | Normalizes or hides stale claim-specific overwrites after claim transfer or category transfer. |
| Current category | `TicketRecord.category_key` -> `GuildRepository.get_category()` | Determines which staff targets are currently visible. |
| Hidden categories | transfer execution path | Previous category staff are explicitly hidden after a category switch. |
| Ticket creator | `TicketRecord.creator_id` | Always gets participant-style access unless muted or temporarily frozen during closing. |
| Extra participants | service-supplied runtime targets | Optional explicit non-staff participant access; current code mainly passes muted participants or moderation targets. |
| Muted participants | `TicketMuteRepository.list_by_ticket()` | Preserve `view_channel=True` but force `send_messages=False` for creator/participants. |
| Closing window | `TicketStatus.CLOSING` via `CloseService` | Temporarily forces staff, creator, claimer, and muted participants to readonly. |

## Computation Order

`StaffPermissionService.build_ticket_permission_plan()` currently builds overwrites in this order:

1. Resolve visible staff targets:
   - configured admin role
   - current category staff roles
   - current category staff users
2. Resolve hidden staff targets from `hidden_categories` and hide them unless they are still visible in the new category.
3. Apply the base staff overwrite:
   - relaxed mode: visible staff get write access
   - strict mode: visible staff get readonly access
4. Normalize previous/current claimer overrides:
   - previous claimer may be hidden or normalized
   - current claimer may get an explicit writable overwrite
5. Apply creator and participant overwrites.
6. Re-apply muted creator/participant restrictions as readonly participant overwrites.

This order matters. A later participant or mute overwrite can intentionally narrow what an earlier generic staff/participant plan allowed.

## Recalculation Triggers

| Trigger | Owning Service or Entry Point | Expected Outcome |
|--------|-------------------------------|------------------|
| Draft channel creation | `CreationService.create_draft_ticket()` | Special-case initial overwrites: creator visible, bot visible, configured staff/admin hidden. |
| Draft submit accepted | `SubmitService._grant_staff_access()` | Open current-category staff visibility for the ticket; queued path does not do this. |
| Queued ticket promotion | `QueueService.process_next_queued_ticket()` -> `SubmitService.promote_queued_ticket()` | Same as normal submit, but executed later after capacity becomes available. |
| Claim / transfer-claim / unclaim | `ClaimService._sync_staff_permissions()` | Recompute staff write/read behavior from claim mode and claimer identity. |
| Sleep / wake | `SleepService._sync_ticket_permissions()` | Recompute staff + creator + muted participant access after the state change. |
| Mute / unmute / mute expiration | `ModerationService._sync_ticket_permissions()` | Preserve or remove participant send restrictions without rebuilding unrelated workflow state. |
| Transfer execution | `TransferService._sync_transfer_permissions()` | Show new category staff, hide previous category staff, clear stale claimer access, preserve creator/muted participants. |
| Close start | `CloseService._freeze_ticket_permissions()` | Force temporary readonly access during the revoke window. This is not a full recomputation. |
| Close revoke | `CloseService._restore_ticket_permissions()` | Restore the normal active-ticket permission model after leaving `closing`. |
| Config import via `/ticket permission` | `PermissionConfigService.apply_permission_config()` | Updates stored category staff config for future recalculations, but does not immediately push overwrites to already-open ticket channels. |

## Guard and Validation Layer

| Guard | Current Owner | Notes |
|------|---------------|-------|
| Staff eligibility and ticket-admin checks | `services/staff_guard_service.py` | Bot owner, Discord administrators, configured `admin_role_id`, category staff users, and category staff roles can pass action guards. |
| Snapshot and notes access | `services/ticket_access_service.py` | Creator may view snapshots; staff/ticket admins manage notes and broader staff-only actions. |
| Creator-only submit checks | `services/submission_guard_service.py` | Prevents non-creators from submitting and validates setup/category availability before submit. |
| Permission JSON validation | `services/permission_config_service.py` | Validates JSON structure and that referenced role IDs exist in the guild. |
| Runtime config validation | `services/config_validation.py` | Validates setup/config inputs that indirectly affect permission behavior, such as admin role and category configuration. |

## Current Behavioral Notes

- Draft channels start with staff hidden. Staff access is granted only when the ticket becomes `submitted`.
- Queued tickets keep the draft-style hidden-staff model until they are promoted.
- In strict claim mode, visible staff are readonly by default; only the active claimer receives write access.
- Category transfer does not immediately change overwrites when transfer is requested. Overwrites change only when the delayed execution actually switches `category_key`.
- Config changes made through `/ticket permission` do not fan out to existing channels. Existing open tickets pick up the new config only on the next permission-affecting workflow that recalculates them.

## Failure Modes

- Staff cannot see a newly submitted ticket:
  likely cause is submit/promotion never reached the permission-service grant step, or the category config no longer resolves.
- Staff can see a ticket but cannot speak unexpectedly:
  likely cause is strict claim mode with no active claimer.
- A muted user can still send messages:
  likely cause is moderation sync did not run, or a later overwrite unexpectedly re-opened send permission.
- Transfer finished but old staff still see the channel:
  likely cause is transfer execution skipped or failed during `hidden_categories` synchronization.
- Config import updated the database but live tickets did not change:
  this is expected with the current design; existing channels are not bulk-rewritten on config import.
- Closing made the whole channel readonly:
  this is expected during the revoke window and should clear only if close is revoked.

## Tests to Review

- `tests/services/test_staff_permission_service.py`
- `tests/services/test_staff_guard_service.py`
- `tests/services/test_ticket_access_service.py`
- `tests/services/test_submit_service.py`
- `tests/services/test_claim_service.py`
- `tests/services/test_sleep_service.py`
- `tests/services/test_transfer_service.py`
- `tests/services/test_moderation_service.py`
- `tests/services/test_close_service.py`

## When To Update

Update this document whenever:

- A new overwrite input is introduced.
- Claim mode semantics change.
- A workflow starts or stops triggering permission recomputation.
- Draft creation or close-freeze stops being a special case.
- Config changes begin to retroactively rewrite existing ticket channels.
