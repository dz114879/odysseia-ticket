# Troubleshooting

Use this page from the symptom outward. Start with the user-visible failure, then confirm the owning command, current ticket state, runtime health, and recent config changes.

## Triage Order

1. Locate the entry point in `../02-command-source-map.md`.
2. Decide which layer is failing:
   - process startup / `.env`
   - guild setup or runtime config
   - public panel / persistent view
   - scheduler / recovery
   - ticket state / permission guard
3. Check both:
   - the local log file configured by `LOG_FILE`
   - the guild log channel configured by `/ticket setup`
4. If a ticket is involved, check its current lifecycle status before assuming the command is broken.

## Common Symptoms

### Bot does not start or exits immediately

First checks:

- `DISCORD_BOT_TOKEN` is present.
- `DISCORD_APP_ID` is an integer if set.
- `SCHEDULER_INTERVAL_SECONDS` is an integer greater than `0`.
- `AUTO_SYNC_COMMANDS` is a valid boolean-like string.
- `SQLITE_PATH` and `LOG_FILE` resolve to writable locations.

What this usually means:

- startup failed in `load_env_settings()` before Discord login
- or bootstrap failed while running migrations / constructing runtime services

### Slash commands are missing or outdated

First checks:

- the bot was invited with the `applications.commands` scope
- `DISCORD_APP_ID` points at the intended application
- `AUTO_SYNC_COMMANDS` is enabled in the environment where you expect auto-sync
- startup logs contain `Application commands synced:`

Typical fix:

- set `AUTO_SYNC_COMMANDS=true`, restart once, confirm sync, then decide whether to keep it enabled

### `/ticket setup`, `/ticket config`, `/ticket permission`, or panel commands say setup is incomplete

First checks:

- confirm `/ticket setup` was run successfully in this guild
- verify the stored log channel, archive channel, ticket category, and admin role still exist
- if channels or role were deleted or replaced, rerun `/ticket setup`

Typical fix:

- rerun `/ticket setup` with valid targets instead of trying to patch around missing guild objects

### Public panel select or button says the panel is stale

First checks:

- was `/ticket panel refresh` or `/ticket panel create` run recently
- is the user clicking an older copied panel message
- does the active panel message still exist in Discord

What is expected:

- refresh rotates the panel nonce, so old buttons becoming stale is normal

Typical fix:

- point users to the newest panel message
- if the active panel message is gone, use `/ticket panel create`

### Public panel is visible but draft creation fails

First checks:

- the selected category still exists and is enabled
- the guild still has valid setup config
- the panel message belongs to the current active panel record

Typical fix:

- refresh or recreate the panel after category/config changes

### Draft warnings or auto-abandon do not happen

First checks:

- the ticket is still in `draft`
- the creator, not a bot, sent the tracked messages
- current timeout values are what you expect in `/ticket config`
- scheduler startup succeeded and the draft sweep handlers are registered

Important behavior:

- warning sends one hour before timeout and is scheduler-driven
- expired draft cleanup runs on the scheduler and once on `on_ready()`
- inactivity is based on the creator's last non-bot message in the draft

### Queued tickets never promote

First checks:

- active capacity is actually free; `submitted`, `transferring`, `closing`, `archiving`, and `archive_sent` still count
- `ticket.queue_sweep` is running
- the first queued ticket still has a resolvable channel and creator

Important behavior:

- queue promotion is FIFO per guild
- temporary Discord resolution failures defer promotion
- missing channel or missing creator can cause the queued ticket to be abandoned

Useful guild-log titles:

- `队列提升已延迟`
- `排队工单已废弃`
- `队列提升失败`

### A sleep ticket does not wake, or wakes unexpectedly

First checks:

- the ticket is actually in `sleep`
- a new non-bot message was sent in the ticket channel
- guild capacity has room for the ticket to return to an active state

Important behavior:

- wake is triggered by any non-bot message in the channel, not only by staff commands
- if capacity is full, the ticket stays asleep and the channel gets a notice

### Staff cannot see a ticket or a staff action is rejected

First checks:

- the actor is current-category staff, ticket admin, guild admin, or bot owner as required
- the ticket's category config still exists
- the action is valid for the ticket's current status
- a recent `/ticket permission` change only updated stored config, not every existing channel overwrite

Typical fix:

- verify the category's `staff_role_ids` / `staff_user_ids`
- do not assume permission JSON upload retroactively repaired old ticket channels

### Close flow is stuck, archive is missing, or the live channel still exists

First checks:

- is the ticket stuck in `closing`, `archiving`, `archive_sent`, or `archive_failed`
- is `ticket.archive_recovery_sweep` running
- does the configured archive channel still exist and allow sends
- did the guild log channel record archive failure or recovery skip messages

Important behavior:

- due `closing` tickets are advanced by recovery sweep, not a dedicated close scheduler
- `archive_failed` is auto-retried only when the error token is retryable and retry limit is not exhausted
- if a live ticket channel is deleted early during `closing` or `archiving`, recovery should force snapshot fallback

Useful guild-log titles:

- `缺失频道恢复已触发`
- `归档恢复已跳过`

### Snapshot history, recycle-bin output, or transcript fallback is incomplete

First checks:

- the messages were from an active snapshot status: `submitted`, `sleep`, `transferring`, or `closing`
- the ticket hit the configured create-record limit
- the missing content belongs to draft or queued time before first submit

Important behavior:

- draft and queued messages are not live-captured
- on first successful submit, channel history is bootstrapped into create snapshots
- once the create limit is reached, new create records stop, but later edit/delete records may still append
- unknown old content in edit/delete records can happen when the prior create state was unavailable

Useful guild-log titles:

- `工单快照创建上限已达`
- `消息处理器崩溃`

## Useful Local Log Markers

Look for these markers before assuming the problem is in Discord UI:

- `Bootstrap finished.`
- `Restored snapshot runtime cache`
- `Recovered ... incomplete archive flow(s) during bootstrap.`
- `Application commands synced:`
- `Restored ... active panel persistent view(s).`

## What To Collect Before Escalating

Collect these details together:

- exact command, button, or modal the user triggered
- guild ID, channel ID, and ticket ID if available
- current ticket status
- relevant local log lines and guild-log embeds
- any config or permission JSON change made shortly before the failure

## Related Docs

- `../02-command-source-map.md`
- `../20-modules/21-panel-and-draft.md`
- `../20-modules/24-close-archive-recovery.md`
- `../20-modules/25-snapshot-and-notes.md`
- `../20-modules/26-runtime-bootstrap-scheduler.md`
