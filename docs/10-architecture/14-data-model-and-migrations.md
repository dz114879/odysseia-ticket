# Data Model and Migrations

This document describes what the bot persists today, which layer owns each persisted shape, and how schema changes must be introduced without breaking startup or recovery.

## Scope

In scope:

- SQLite-backed domain records
- file-backed ticket artifacts under `storage/`
- migration ordering, atomicity, and review rules

Out of scope:

- runtime-only state such as `LockManager`, `RuntimeCacheStore`, `CooldownManager`, `DebounceManager`, and close-request in-memory message tracking

## Persistence Surfaces

| Surface | Path / Table Area | Primary Owner | What Lives There |
|--------|--------------------|---------------|------------------|
| SQLite | `db/connection.py`, `db/migrations.py`, `db/repositories/` | repositories + services | ticket rows, guild config, category config, panel records, mutes, counters, schema version |
| Snapshot JSONL | `storage/snapshots/{ticket_id}.jsonl` | `SnapshotStore`, `SnapshotService` | append-only `create` / `edit` / `delete` message history |
| Notes JSONL | `storage/notes/{ticket_id}.jsonl` | `NotesStore`, `NotesService` | append-only internal staff notes |
| Transcript HTML | `storage/exports/{ticket_id}.html` | `ArchiveRenderService` | rendered archive transcript before upload |
| Reserved archive dir | `storage/archives/` | bootstrap + cleanup only | directory exists and cleanup removes ticket-boundary files, but current archive rendering does not primarily write here |

## Typed Record Inventory

`core/models.py` is the canonical typed boundary for persisted SQL records.

| Dataclass | Backing Table | Main Repository | Purpose |
|----------|---------------|-----------------|---------|
| `TicketRecord` | `tickets` | `TicketRepository` | ticket lifecycle row and all long-lived workflow metadata |
| `TicketMuteRecord` | `ticket_mutes` | `TicketMuteRepository` | per-ticket moderation mute records |
| `GuildConfigRecord` | `guild_configs` | `GuildRepository` | per-guild setup targets and runtime config |
| `TicketCategoryConfig` | `ticket_categories` | `GuildRepository` | per-category display and staff mapping config |
| `PanelRecord` | `panels` | `PanelRepository` | active/stale public panel message records |
| `TicketCounterRecord` | `ticket_counters` | `CounterRepository` | next sequence number per guild/category |

All of these dataclasses are `frozen=True`; repositories return typed records, not raw SQLite rows.

## SQLite Schema Ownership

| Repository | Owns | Main Responsibilities | Important Notes |
|-----------|------|-----------------------|-----------------|
| `TicketRepository` | `tickets` | create/update/get/list lifecycle rows, due close/transfer lookup, queue ordering lookup, lightweight count/position aggregation | accepts `connection=` for multi-step service transactions and persists message identity fields such as `welcome_message_id` / `staff_panel_message_id` |
| `TicketMuteRepository` | `ticket_mutes` | upsert, expire sweep lookup, delete | composite primary key is `(ticket_id, user_id)` |
| `GuildRepository` | `guild_configs`, `ticket_categories` | config upsert/update, category replace/list/get | one repo owns both guild-level and category-level config surfaces |
| `PanelRepository` | `panels` | replace active panel, active panel lookup, list restore candidates | one active panel per guild is enforced both in code and by a partial unique index |
| `CounterRepository` | `ticket_counters` | read, upsert, increment, delete per-category counters | used to generate stable ticket numbers per guild/category |

The `schema_version` table is infrastructure metadata owned by `db/migrations.py`, not by a normal repository.

## Current Tables And Invariants

| Table | Key Columns | Why It Exists | Important Invariant |
|------|-------------|---------------|---------------------|
| `tickets` | `ticket_id` PK, nullable unique `channel_id` | core ticket lifecycle and workflow state | one live Discord channel maps to at most one ticket row |
| `guild_configs` | `guild_id` PK | per-guild setup + runtime settings | one row per guild |
| `ticket_categories` | `(guild_id, category_key)` PK | category presentation + staff mapping | categories are guild-scoped, not global |
| `panels` | `panel_id` PK, unique `message_id` | persistent public panel records | partial unique index allows only one active panel per guild |
| `ticket_counters` | `(guild_id, category_key)` PK | numbering state | independent sequence per guild/category |
| `ticket_mutes` | `(ticket_id, user_id)` PK | temporary moderation state | expire sweep uses indexed `expire_at` |
| `schema_version` | fixed row `id=1` | current applied migration version | database must never be ahead of program support |

Additional indexes worth keeping explicit:

- `tickets(guild_id, status)` supports lifecycle/status sweeps
- `tickets(guild_id, status)` also supports SQL-side active-capacity counts
- `tickets(close_execute_at)` supports due-close lookup
- `tickets(guild_id, queued_at, created_at, ticket_id)` supports queue promotion order and SQL-side queue-position lookup
- `panels(message_id)` supports message-scoped view restore

## File-Backed Artifacts

| Area | Path | Writer | Reader | Cleanup |
|------|------|--------|--------|---------|
| Snapshots | `storage/snapshots/{ticket_id}.jsonl` | `SnapshotService` via `SnapshotStore.append_record()` / `overwrite_records()` | `SnapshotService`, `SnapshotQueryService`, archive fallback render | `CleanupService.cleanup_ticket()` |
| Snapshot temp file | `storage/snapshots/{ticket_id}.jsonl.tmp` | `SnapshotStore.overwrite_records()` | internal replace flow only | `CleanupService.cleanup_ticket()` |
| Notes | `storage/notes/{ticket_id}.jsonl` | `NotesService` via `NotesStore.append_record()` | `NotesService` | `CleanupService.cleanup_ticket()` |
| Transcript export | `storage/exports/{ticket_id}.html` and `{ticket_id}-*` | `ArchiveRenderService` | `ArchiveSendService` upload path, operator inspection if needed | `CleanupService.cleanup_ticket()` |
| Reserved archive artifacts | `storage/archives/{ticket_id}.html` and `{ticket_id}-*` | no primary writer in current code path | cleanup/legacy compatibility | `CleanupService.cleanup_ticket()` |

## Migration History

| Version | Change |
|--------|--------|
| `1` | base schema: `tickets`, `guild_configs`, `ticket_categories`, `panels`, `ticket_counters`, core indexes |
| `2-5` | add draft activity, staff panel tracking, sleep priority restore, transfer workflow fields |
| `6` | add `ticket_mutes` table and expiration index |
| `7-10` | add close/archive fields, snapshot bootstrap marker, queue tracking, archive failure tracking |
| `11` | change default priority to `unset` at application layer only; no SQL statement runs |
| `12` | add `staff_role_ids_json` and backfill from legacy single `staff_role_id` |
| `13` | add runtime config fields for timeouts, snapshot limits, and text overrides |
| `14` | add `tickets.welcome_message_id` so draft welcome view cleanup can target the exact message |

`core/constants.py` currently declares `CURRENT_SCHEMA_VERSION = 14`, and the last migration in `MIGRATIONS` must stay aligned with that value.

## Hard Rules

- SQLite must run in WAL mode; `DatabaseManager.connect()` sets `PRAGMA journal_mode = WAL`.
- Migrations must execute inside one `DatabaseManager.session()` transaction.
- Do not use `executescript()` in migrations; issue ordered `connection.execute(...)` calls instead.
- `apply_migrations()` must fail if the database version is higher than `CURRENT_SCHEMA_VERSION`.
- Repositories should keep SQL and row-to-dataclass mapping only; business repair/backfill belongs in services or explicit migration code.
- If a service needs one transaction across several repositories, pass the same `connection=` through every repository call.

## Safe Schema Change Workflow

1. Decide whether the data belongs in SQLite, JSONL storage, or only runtime memory.
2. Extend the owning dataclass in `core/models.py` if the shape is SQL-backed.
3. Add the new migration in `db/migrations.py` and keep it atomic.
4. Bump `CURRENT_SCHEMA_VERSION` in `core/constants.py`.
5. Update all repository read/write paths for the new columns or table.
6. Update service logic that consumes the field and document any non-retroactive behavior.
7. Run migration and repository tests before release.

## Rollback Constraint

There is one operational rule that matters during deployment:

- once a newer schema version is applied, an older binary will refuse to start because `apply_migrations()` raises when `current_version > CURRENT_SCHEMA_VERSION`

So schema bumps require either:

- a database backup and restore plan, or
- a forward fix on top of the new schema

Do not treat schema-changing releases as a simple code rollback.

## Tests To Run

- `uv run pytest tests/test_migrations.py tests/services/test_bootstrap_service.py`
- `uv run pytest tests/repositories/`
- `uv run pytest tests/services/test_bootstrap_service_restore.py`
- `uv run pytest -q`

## Related Docs

- `11-ticket-lifecycle.md`
- `12-permission-model.md`
- `13-config-model.md`
- `../20-modules/24-close-archive-recovery.md`
- `../20-modules/25-snapshot-and-notes.md`
- `../30-operations/33-release-checklist.md`
