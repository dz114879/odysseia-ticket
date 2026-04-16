# Staff Actions

This document covers staff-side ticket workflows after a ticket is available to staff, including slash commands and the persistent staff panel.

## Scope

In scope:

- Claiming and unclaiming
- Claim transfer
- Mute and unmute
- Priority changes
- Sleep and wake-related behavior
- Ticket rename
- Category transfer and cancellation
- Staff help and workflow guidance
- Staff panel refresh/staleness behavior

Out of scope:

- Close, archive, and recovery behavior
- Creator-side close requests before staff action

## User Entry Points

| Entry Point | Owning File | Primary Service |
|------------|-------------|-----------------|
| `/ticket claim` | `cogs/staff_cog.py` | `ClaimService.claim_ticket()` |
| `/ticket unclaim` | `cogs/staff_cog.py` | `ClaimService.unclaim_ticket()` |
| `/ticket transfer-claim` | `cogs/staff_cog.py` | `ClaimService.transfer_claim()` |
| `/ticket mute` | `cogs/staff_cog.py` | `ModerationService.mute_member()` |
| `/ticket unmute` | `cogs/staff_cog.py` | `ModerationService.unmute_member()` |
| `/ticket priority` | `cogs/staff_cog.py` | `PriorityService.set_priority()` |
| `/ticket sleep` | `cogs/staff_cog.py` | `SleepService.sleep_ticket()` |
| `/ticket rename` | `cogs/staff_cog.py` | `RenameService.rename_ticket()` |
| `/ticket transfer` | `cogs/staff_cog.py` | `TransferService.transfer_ticket()` |
| `/ticket untransfer` | `cogs/staff_cog.py` | `TransferService.cancel_transfer()` |
| `/ticket help` | `cogs/staff_cog.py` | `build_ticket_help_message()` |
| Staff panel claim button | `discord_ui/staff_panel_view.py` | `ClaimService.claim_ticket()` |
| Staff panel unclaim button | `discord_ui/staff_panel_view.py` | `ClaimService.unclaim_ticket()` |
| Staff panel sleep button | `discord_ui/staff_panel_view.py` | `SleepService.sleep_ticket()` |
| Staff panel close button | `discord_ui/staff_panel_view.py` | `CloseService.initiate_close()` |
| Staff panel rename modal | `discord_ui/staff_panel_view.py` | `RenameService.rename_ticket()` |
| Staff panel priority select | `discord_ui/staff_panel_view.py` | `PriorityService.set_priority()` |
| Sleep wake-up on message | `bot.py:on_message()` | `SleepService.handle_message()` |

## Owning Source Files

- `cogs/staff_cog.py`
- `discord_ui/staff_panel_view.py`
- `discord_ui/staff_feedback.py`
- `discord_ui/help_text.py`
- `discord_ui/panel_embeds.py`
- `services/claim_service.py`
- `services/moderation_service.py`
- `services/priority_service.py`
- `services/sleep_service.py`
- `services/rename_service.py`
- `services/transfer_service.py`
- `services/staff_guard_service.py`
- `services/staff_panel_service.py`
- `services/staff_permission_service.py`

## Surface Split

Current UI split is deliberate:

- Staff panel includes:
  - claim
  - unclaim
  - sleep
  - close
  - rename
  - priority
- Slash commands are still required for:
  - transfer-claim
  - mute
  - unmute
  - transfer
  - untransfer
  - help

The panel is a convenience surface for the most common in-channel actions, not the complete staff control surface.

## Action Matrix

| Action | Allowed Status | Who Can Trigger | Main Effects | Permission / Panel Effects |
|--------|----------------|-----------------|--------------|----------------------------|
| Claim | `submitted` | current category staff, ticket admin, bot owner | set `claimed_by` | recompute staff overwrites, refresh panel |
| Unclaim | `submitted` | current claimer; ticket admin can force cancel | clear `claimed_by` | recompute staff overwrites, refresh panel |
| Transfer claim | `submitted` | current claimer; ticket admin can force transfer | replace `claimed_by` with another current-category staff member | recompute staff overwrites, refresh panel |
| Priority | `submitted` | staff / admin / bot owner | update `priority`, rewrite channel prefix | refresh panel; no permission recomputation |
| Sleep | `submitted` | staff / admin / bot owner | `status=submitted -> sleep`, set `priority=SLEEP`, store `priority_before_sleep`, rename channel | recompute permissions, refresh panel, trigger queue fill |
| Wake | `sleep` | implicit on any non-bot message in the channel, if capacity available | `status=sleep -> submitted`, restore priority, rename channel | recompute permissions, refresh panel |
| Rename | `submitted`, `sleep` | staff / admin / bot owner | rewrite channel name while preserving current prefix | no panel refresh, no permission recomputation |
| Mute | `submitted`, `sleep` | staff / admin / bot owner | create/update `ticket_mutes` record | recompute participant access, refresh panel |
| Unmute | `submitted`, `sleep` | staff / admin / bot owner | delete `ticket_mutes` record | recompute participant access, refresh panel |
| Transfer category | `submitted`, `sleep` | staff / admin / bot owner, but if claimed only current claimer may initiate | `status -> transferring`, store delayed transfer metadata | refresh panel immediately; permission changes happen later on execution |
| Cancel transfer | `transferring` | staff / admin / bot owner | restore `submitted` or `sleep`, clear transfer metadata | refresh panel; may trigger queue fill if restored to `sleep` |
| Help | any | anyone in guild context | no state change | no panel refresh |

## Workflow Notes

### Claim Family

- `ClaimService` is strictly `submitted`-only.
- Claim is idempotent for the current claimer.
- Claim transfer and unclaim allow a ticket-admin override, but normal staff cannot take over another staff member's claim without that override.
- In strict claim mode, visible staff become readonly and only the active claimer receives write access.

### Priority

- `PriorityService` only works in `submitted`.
- `UNSET` and `SLEEP` are not valid manual targets.
- Priority changes are reflected in the channel name via prefixes such as `🟢|`, `🟡|`, `🔴|`, and `‼️|`.
- If both the stored priority and the channel prefix are already current, the update is a no-op.

### Sleep / Wake

- `SleepService.sleep_ticket()` moves `submitted` into `sleep`, preserves `priority_before_sleep`, and rewrites the channel name with the sleep prefix.
- Sleeping releases active capacity and immediately tries to promote the next queued ticket.
- Wake-up is not a slash command. It is triggered by `bot.py:on_message()` calling `SleepService.handle_message()`.
- Any non-bot message in the sleep channel can trigger wake-up.
- Wake-up only succeeds if the guild still has active capacity at that moment.

### Rename

- `RenameService` only allows `submitted` and `sleep`.
- It preserves the current prefix:
  - priority prefix in `submitted`
  - sleep prefix in `sleep`
- The requested name is slugified and cannot be empty or symbol-only.
- Rename updates `updated_at` and posts a channel log message, but does not refresh the staff panel because the panel does not display the channel name.

### Mute / Unmute

- `ModerationService` only allows `submitted` and `sleep`.
- Valid mute targets are:
  - the ticket creator
  - non-staff participants who already have explicit participant access
- Invalid mute targets include:
  - the acting staff member
  - bots
  - current-category staff
  - ticket admins
- Duration parsing supports values such as `30m`, `2h`, `1d`, and `45分钟`.
- Minimum duration is 60 seconds.
- Expired mutes are lifted by the scheduled moderation sweep and also refresh the panel.

### Category Transfer

- `TransferService` allows `submitted` and `sleep`.
- The target category must be enabled and different from the current category.
- If the ticket is already claimed, only the current claimer may initiate the category transfer. Unlike claim-transfer, there is no ticket-admin force-transfer path here.
- Transfer is delayed: the ticket enters `transferring`, stores target metadata, and executes later.
- Canceling transfer restores `status_before`.
- Executing transfer changes `category_key`, clears `claimed_by`, appends `transfer_history_json`, and then recalculates permissions so new-category staff can see the channel and old-category staff are hidden.

## Staff Panel Behavior

`StaffPanelService` is the runtime owner for the persistent control panel message.

### Staleness

- Panel interactions validate `ticket.staff_panel_message_id` against the clicked message ID via `StaffPanelService.assert_current_panel_interaction()`.
- If the message ID does not match, the interaction is rejected as stale.
- This matters because panel refresh can recover a missing panel by reposting a new message and updating `staff_panel_message_id`.

### Refresh

- Most stateful staff actions call `request_refresh(ticket_id)`.
- Refresh is debounced through `DebounceManager` with a short delay, so multiple rapid updates collapse into one message edit.
- If the panel message is missing, refresh reposts it instead of failing hard.

### Control Availability by Status

- `submitted`:
  - panel claim/unclaim/priority/sleep/close/rename controls are enabled
- `sleep`:
  - claim/unclaim/priority/sleep are disabled
  - close/rename remain enabled
- `transferring`:
  - claim/unclaim/priority/sleep/close/rename are all disabled

The embed text also changes by status to point staff toward the correct slash commands for the disabled actions.

## Guardrails

- State gating is centralized in `StaffGuardService.load_ticket_context()` or service-local wrappers around it.
- Staff eligibility goes through `StaffGuardService.assert_staff_actor()`.
- Panel clicks must pass staleness validation before invoking the underlying service.
- Actions that change who can talk or who can see the ticket should call `StaffPermissionService`.
- Actions that change high-level ticket status should usually request a staff panel refresh.
- Multi-step actions such as priority rename, sleep rename, and moderation overwrite updates include rollback/compensation logic when the second step fails.

## Extension Checklist

- Does the new action belong on the staff panel, slash command surface, or both?
- Does it mutate `status`, or only side metadata such as claim, mute, or priority?
- Does it require staleness checks because it is triggered from the persistent panel?
- Does it need panel refresh after completion?
- Does it require permission recomputation?
- Does it release or consume active capacity?
- Does it need a scheduled follow-up path like wake-up, mute expiry, or delayed transfer execution?

## Tests To Review

- `tests/services/test_claim_service.py`
- `tests/services/test_moderation_service.py`
- `tests/services/test_priority_service.py`
- `tests/services/test_sleep_service.py`
- `tests/services/test_rename_service.py`
- `tests/services/test_transfer_service.py`
- `tests/services/test_staff_guard_service.py`
- `tests/services/test_staff_panel_service.py`
- `tests/services/test_staff_permission_service.py`

## Related Docs

- `22-submit-queue-capacity.md`
- `24-close-archive-recovery.md`
- `../10-architecture/12-permission-model.md`
