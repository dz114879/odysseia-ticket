# Runtime, Bootstrap, and Scheduler

This module doc covers process startup, shared runtime primitives, bot-level event delegation, persistent view registration, and the single background scheduler loop.

## Scope

In scope:

- `.env` loading and validation
- Bootstrap order and dependency wiring
- Runtime helper ownership
- Which services are prebuilt at startup versus created by cogs
- Bot-level event delegation
- Persistent view registration and restore
- Scheduler design and registered handlers

Out of scope:

- Detailed business rules inside each feature module

## Boot Ownership

| Responsibility | Owning File |
|---------------|-------------|
| Process entry point | `bot.py` |
| Environment loading and validation | `config/env.py` |
| Static paths and defaults | `config/static.py`, `config/defaults.py` |
| Runtime bootstrap orchestration | `services/bootstrap_service.py` |
| Logging setup | `services/logging_service.py` |
| Database connection and migrations | `db/connection.py`, `db/migrations.py` |
| Shared runtime primitives | `runtime/` |
| Bot event delegation | `bot.py` |

## Environment Load Contract

`bot.py:main()` is the hard startup boundary.

Current flow:

1. `load_env_settings()` reads `.env`
2. it validates:
   - `DISCORD_APP_ID` as optional integer
   - `SCHEDULER_INTERVAL_SECONDS` as integer greater than `0`
   - `AUTO_SYNC_COMMANDS` as a strict boolean-like string
3. it resolves relative `SQLITE_PATH` and `LOG_FILE` against `BASE_DIR`
4. `main()` requires `DISCORD_BOT_TOKEN`
5. `TicketBot` is created with the resolved settings

If env parsing fails, startup exits before Discord login and before bootstrap.

## Bootstrap Order

`TicketBot.setup_hook()` runs before `on_ready()` and before the bot starts handling normal Discord traffic.

Current order is:

1. `BootstrapService.bootstrap()`
2. `self.tree.on_error = self._on_tree_error`
3. `_load_extensions()`
4. `_restore_active_panel_views()`
5. optional command sync when `AUTO_SYNC_COMMANDS=true`

Inside `BootstrapService.bootstrap()`, the concrete sequence is:

1. ensure runtime directories exist:
   - database parent
   - log parent
   - `storage/`
   - `storage/snapshots`
   - `storage/notes`
   - `storage/archives`
   - `storage/exports`
2. create `LoggingService`
3. create `DatabaseManager`
4. run migrations with `apply_migrations()`
5. construct runtime primitives:
   - `LockManager`
   - `CooldownManager`
   - `DebounceManager`
   - `RuntimeCacheStore`
6. construct file-backed stores:
   - `SnapshotStore`
   - `NotesStore`
7. construct snapshot/note runtime services:
   - `SnapshotQueryService`
   - `SnapshotService`
   - `NotesService`
8. restore in-memory snapshot cache from snapshot files
9. construct long-lived event/scheduler-facing services:
   - `DraftTimeoutService`
   - `CapacityService`
   - `QueueService`
   - `SleepService`
   - `ModerationService`
   - `TransferService`
   - `CloseService`
   - `RecoveryService`
10. run one bootstrap recovery pass for incomplete archive flows
11. construct `BackgroundScheduler`
12. register scheduler handlers
13. start scheduler
14. freeze everything into `BootstrapResources`

If `bootstrap()` is called again without shutdown, it returns the already-built `BootstrapResources` instead of rebuilding the runtime.

## Prebuilt Services vs Command-Scoped Services

Not every service is a bootstrap singleton.

### Prebuilt at bootstrap

These are prebuilt because the bot or scheduler needs them immediately:

- `DraftTimeoutService`
- `CapacityService`
- `QueueService`
- `SleepService`
- `ModerationService`
- `TransferService`
- `CloseService`
- `RecoveryService`
- `SnapshotService`
- `SnapshotQueryService`
- `NotesService`

### Built later by cogs or views

Many command-facing services are constructed in cog constructors or interaction helpers using shared resources from `bot.resources`, for example:

- `ClaimService`
- `PriorityService`
- `RenameService`
- `SubmitService`
- `CloseRequestService`
- `PanelService`
- `StaffPanelService`

Practical rule:

- shared state lives in the database and runtime helpers
- do not rely on Python object identity of a service instance unless that instance is explicitly stored in `BootstrapResources`

## Runtime Primitives

These helpers are the shared stateful building blocks under `runtime/`.

| Component | File | Why It Exists | Typical Callers |
|----------|------|---------------|-----------------|
| Cache | `runtime/cache.py` | keeps short-lived in-memory runtime state without touching SQLite or JSONL on every event | primarily `SnapshotService`, plus `CleanupService` during teardown |
| Locks | `runtime/locks.py` | serializes multi-step mutations on tickets, channels, users, or feature-specific keys | submit, draft timeout, claim, rename, sleep, transfer, close, archive, moderation, notes, snapshots |
| Cooldowns | `runtime/cooldowns.py` | generic monotonic-time cooldown map for future rate limiting | currently provisioned and swept by bootstrap, but not actively consumed by a feature service |
| Debounce | `runtime/debounce.py` | collapses rapid repeated refresh requests into one callback | mainly `StaffPanelService.request_refresh()` |
| Scheduler | `runtime/scheduler.py` | runs all background handlers on one fixed-interval loop with failure isolation | `BootstrapService` owns registration and lifecycle |

### Cache

`RuntimeCacheStore` is currently snapshot-heavy.

Current practical usage:

- latest snapshot state per message
- snapshot create counts
- snapshot threshold flags

The generic `latest_messages` and `flags` caches exist for broader runtime use, but snapshot tracking is the current primary consumer.

### Locks

`LockManager` keys are free-form strings such as:

- `draft-submit:{channel_id}`
- `ticket-submit-guild:{guild_id}`
- `ticket-snapshot:{ticket_id}`
- `ticket-sleep:{channel_id}`
- `ticket-close:{ticket_id}`

Cleanup removes only unlocked stale entries, so active operations are never force-evicted.

### Debounce

`DebounceManager.schedule()` replaces any existing task with the same key.

That is why repeated panel refresh requests during a burst of actions become one delayed message edit instead of many.

### Scheduler

`BackgroundScheduler`:

- owns one `asyncio` task
- runs handlers sequentially in registration order
- supports both sync and async callbacks
- logs per-handler duration
- catches normal exceptions per handler and keeps the loop alive
- sleeps `max(0, interval - elapsed)` between cycles

So one slow handler delays the next cycle, but one failing handler does not stop the scheduler.

## Scheduler Handlers

Bootstrap currently registers exactly these handlers:

| Handler Name | Bootstrap Method | Underlying Owner | Purpose |
|-------------|------------------|------------------|---------|
| `runtime.cleanup_locks` | `_cleanup_locks()` | `LockManager.cleanup()` | drop stale unlocked lock entries |
| `runtime.cleanup_cooldowns` | `_cleanup_cooldowns()` | `CooldownManager.sweep()` | drop expired cooldown entries |
| `runtime.cleanup_cache` | `_cleanup_cache()` | `RuntimeCacheStore.sweep()` | drop expired TTL cache entries |
| `ticket.draft_timeout_sweep` | `_run_draft_timeout_sweep()` | `DraftTimeoutService.sweep_expired_drafts()` | auto-abandon overdue drafts |
| `ticket.draft_warning_sweep` | `_run_draft_warning_sweep()` | `DraftTimeoutService.sweep_draft_warnings()` | send one-hour draft warnings |
| `ticket.transfer_execute_sweep` | `_run_transfer_execute_sweep()` | `TransferService.sweep_due_transfers()` | execute delayed category transfers |
| `ticket.mute_expire_sweep` | `_run_mute_expire_sweep()` | `ModerationService.sweep_expired_mutes()` | lift expired ticket mutes |
| `ticket.archive_recovery_sweep` | `_run_archive_recovery_sweep()` | `RecoveryService.sweep_recoverable_tickets()` | resume due closing/archive flows and retry allowed archive failures |
| `ticket.queue_sweep` | `_run_queue_sweep()` | `QueueService.sweep_queued_tickets()` | promote or abandon queued tickets as capacity allows |

Important note:

- there is no dedicated registered close-expiry handler
- due `closing` tickets currently advance through `RecoveryService.sweep_recoverable_tickets()`

That coupling is intentional in the current implementation and must stay documented.

## Bot-Level Event Delegation

`TicketBot` keeps event routing explicit at the bot layer.

### `setup_hook()`

Responsibilities:

- bootstrap runtime resources
- load all `cogs/*_cog.py`
- restore active public panel views
- optionally sync slash commands

### `on_ready()`

Responsibilities:

- log bot identity
- immediately run `DraftTimeoutService.sweep_expired_drafts()`

The one-shot `on_ready()` sweep exists so overdue drafts are processed as soon as the bot comes online, without waiting for the first scheduler tick.

### `on_message()`

Delegation order is fixed:

1. `SleepService.handle_message()`
2. `DraftTimeoutService.handle_message()`
3. `SnapshotService.handle_message()`
4. `process_commands(message)`

Each pre-command handler is wrapped in its own `try/except`, so one broken runtime handler:

- logs a local warning
- emits a guild warning log
- does not stop the remaining handlers or command processing

### Edit/delete message events

Delegation is direct:

- `on_message_edit()` -> `SnapshotService.handle_message_edit()`
- `on_message_delete()` -> `SnapshotService.handle_message_delete()`
- `on_raw_message_edit()` -> `SnapshotService.handle_raw_message_edit()`
- `on_raw_message_delete()` -> `SnapshotService.handle_raw_message_delete()`

### Channel deletion event

`on_guild_channel_delete()` delegates to `RecoveryService.handle_channel_deleted()` so unexpected live-channel deletion can still resume the close/archive pipeline.

### App command errors

`_on_tree_error()` is the shared slash-command fallback:

- classifies Discord permission and HTTP errors for the user
- logs the full exception locally
- sends a guild error log when resources are available
- uses `_safe_send_error()` to avoid double-response crashes

## Persistent View Strategy

Persistent component recovery currently uses two different patterns.

### Pattern 1: global stateless views registered once

These views are added once with `bot.add_view(...)` and do not need a message ID restore pass:

- `DraftWelcomeView` in `SubmitCog`
- `StaffPanelView` in `StaffCog`

This works because their custom IDs are stable and the runtime logic can validate current ticket state at interaction time.

### Pattern 2: message-scoped public panel restore

Public ticket-creation panels are message-specific. `TicketBot._restore_active_panel_views()`:

- loads active panel records through `PanelService.list_active_panels()`
- rebuilds each view from current guild config/category data
- calls `add_view(view, message_id=panel.message_id)`
- skips invalid panel records and logs a warning instead of failing startup

### What is not restored this way

Timed views such as close-request and closing-notice views are not part of the startup persistent-view restore path. Their lifecycle is owned by their feature services and timeout/recovery flows.

## Snapshot and Recovery Startup Hooks

Two startup hooks are especially important operationally.

### Snapshot runtime restore

Before the scheduler starts, bootstrap calls `SnapshotService.restore_runtime_state()`.

That repopulates:

- latest snapshot cache
- snapshot create counts
- threshold flags

without needing Discord message cache to still exist after restart.

### Incomplete archive recovery

Before the scheduler starts, bootstrap also calls `RecoveryService.recover_incomplete_archive_flows()`.

So the bot does one immediate pass over stuck close/archive flows before periodic sweeps begin.

## Shutdown Behavior

`TicketBot.close()` delegates to `BootstrapService.shutdown()`.

Current shutdown behavior:

- stop scheduler if present
- shut down `DebounceManager` and cancel pending debounced callbacks
- clear `resources`

Shutdown is designed to be idempotent.

## Failure Model

- env/config parsing failure: process exits before bot startup
- bootstrap failure: setup fails hard rather than starting with partial runtime
- per-message handler failure in `on_message`: isolated and logged
- scheduler handler failure: isolated and logged; loop continues
- invalid active public panel restore: skipped and logged; startup continues

## Guardrails

- Keep bootstrap order explicit. Snapshot restore and archive recovery should happen before the scheduler starts.
- Do not add a new long-lived runtime concern only inside a cog if bot events or scheduler jobs also depend on it.
- Reuse `LockManager`, `DebounceManager`, `RuntimeCacheStore`, and `BackgroundScheduler` before inventing new stateful helpers.
- If a feature needs periodic work, decide whether it belongs in the shared scheduler or should be event-driven instead.
- If you add a new persistent view, also decide whether it is:
  - globally stateless and `add_view()` once is enough
  - message-scoped and needs explicit restore from persisted records
- If you change how due closes advance, update this doc and `24-close-archive-recovery.md` together.

## Extension Checklist

- Does the feature need startup initialization or restart recovery?
- Does it need a new field on `BootstrapResources`, or can a cog construct it on demand?
- Does it need one scheduler handler or just an event listener?
- Does it require a new runtime primitive, or can it reuse locks/cache/debounce/cooldowns?
- Does it introduce a persistent view that must survive restart?
- Does failure need to stop startup, or can it be isolated and logged?

## Tests To Review

- `tests/test_bot.py`
- `tests/test_env.py`
- `tests/services/test_bootstrap_service.py`
- `tests/services/test_bootstrap_service_restore.py`
- `tests/runtime/test_scheduler.py`
- `tests/runtime/test_cache.py`
- `tests/runtime/test_locks.py`
- `tests/runtime/test_cooldowns.py`
- `tests/runtime/test_debounce.py`

## Related Docs

- `24-close-archive-recovery.md`
- `25-snapshot-and-notes.md`
- `../10-architecture/10-overview.md`
- `../10-architecture/11-ticket-lifecycle.md`
- `../10-architecture/14-data-model-and-migrations.md`
- `../30-operations/32-troubleshooting.md`
