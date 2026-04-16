# Snapshot and Notes

This module doc covers ticket message snapshots, operator-facing history queries, recycle-bin style exports, and internal staff notes.

## Scope

In scope:

- When snapshot capture starts
- Which ticket states keep recording
- Cached and raw edit/delete fallback behavior
- Snapshot query surfaces and permissions
- Notes write/read behavior
- File-backed persistence, limits, and cleanup

Out of scope:

- Full close/archive workflow except where archive depends on snapshot data
- General staff permission rules outside snapshot/note access

## User Entry Points

| Entry Point | Owning File | Primary Service |
|------------|-------------|-----------------|
| `/ticket message-history` | `cogs/evidence_cog.py` | `TicketAccessService.load_snapshot_context()` -> `SnapshotQueryService.format_message_timeline()` |
| `/ticket recycle-bin` | `cogs/evidence_cog.py` | `TicketAccessService.load_snapshot_context()` -> `SnapshotQueryService.build_recycle_bin_text()` |
| `/ticket notes add` | `cogs/evidence_cog.py` | `TicketAccessService.load_snapshot_context()` -> `NotesService.add_note()` |
| `/ticket notes check` | `cogs/evidence_cog.py` | `TicketAccessService.load_snapshot_context()` -> `NotesService.format_notes()` |
| Draft or queued ticket first enters `submitted` | `services/submit_service.py` | `SnapshotService.bootstrap_from_channel_history()` |
| `on_message` | `bot.py` | `SnapshotService.handle_message()` |
| `on_message_edit` / `on_raw_message_edit` | `bot.py` | `SnapshotService.handle_message_edit()` / `handle_raw_message_edit()` |
| `on_message_delete` / `on_raw_message_delete` | `bot.py` | `SnapshotService.handle_message_delete()` / `handle_raw_message_delete()` |

## Owning Source Files

- `cogs/evidence_cog.py`
- `services/submit_service.py`
- `services/snapshot_service.py`
- `services/snapshot_query_service.py`
- `services/notes_service.py`
- `services/ticket_access_service.py`
- `storage/snapshot_store.py`
- `storage/notes_store.py`
- `services/archive_render_service.py`
- `services/cleanup_service.py`

## Capture Model

Snapshot capture has two phases.

### Phase 1: bootstrap after the ticket reaches `submitted`

`SubmitService` calls `SnapshotService.bootstrap_from_channel_history()` after the `submitted` state has already been committed.

This happens for both:

- direct draft submission
- queued ticket promotion into `submitted`

If a ticket is already `submitted` but its submit-side initialization was left incomplete, a later re-submit can trigger the same bootstrap path again. `SnapshotService` remains idempotent and skips work once `snapshot_bootstrapped_at` is already set.

Bootstrap behavior:

- reads full channel history oldest-first
- ignores bot messages
- converts each message into a `create` snapshot record
- stops at the configured hard limit
- overwrites the ticket snapshot file with the bootstrapped `create` records
- stores `snapshot_bootstrapped_at` and `message_count` on the ticket row
- seeds runtime cache with latest message state, create count, and threshold flags

Important implication:

- draft and queued messages are not live-captured while the ticket is still `draft` or `queued`
- if the ticket later reaches `submitted`, those existing non-bot messages are bootstrapped into snapshots retroactively
- if that first bootstrap attempt fails after the `submitted` commit, a later submitted-path reconciliation can retry it

### Phase 2: live capture after bootstrap

After the ticket is active, `bot.py` delegates message events into `SnapshotService`.

Live capture applies only while ticket status is one of:

- `submitted`
- `sleep`
- `transferring`
- `closing`

It does not live-capture while the ticket is:

- `draft`
- `queued`
- `archiving`
- `archive_sent`
- `channel_deleted`
- `done`
- `abandoned`
- `archive_failed`

Bot-authored messages are ignored in all snapshot event handlers.

## Snapshot Record Model

Snapshots are append-only JSONL records under `storage/snapshots/{ticket_id}.jsonl`.

### `create` record

Stored fields currently include:

- `event=create`
- `message_id`
- `author_id`
- `author_name`
- `timestamp`
- `content`
- `attachments`
- `embeds_count`
- `reply_to`

Attachment entries are normalized into human-readable placeholders such as:

- `[文件: debug.log, 1.0KB]`
- `[图片: image.png, 2.4MB]`

### `edit` record

Stored fields currently include:

- `event=edit`
- `message_id`
- `author_id`
- `author_name`
- `timestamp`
- `old_content`
- `new_content`
- `old_attachments`
- `new_attachments`

Edit records do not change `message_count`.

### `delete` record

Stored fields currently include:

- `event=delete`
- `message_id`
- `author_id`
- `author_name`
- `timestamp`
- `deleted_content`
- `deleted_attachments`

Delete records also do not change `message_count`.

`message_count` means create-snapshot count, not visible-message count and not total timeline-record count.

## Cached Event and Raw Event Split

Discord may deliver edit/delete events either with a cached message object or as raw payload only. The service handles both.

### Cached edit/delete path

- `on_message_edit` uses the current message object and compares against runtime cache or stored snapshot history
- `on_message_delete` records deletion time at delete time, not message creation time

### Raw fallback path

- `on_raw_message_edit` and `on_raw_message_delete` are used only when `payload.cached_message is None`
- if Discord also supplies a cached message, the raw handler exits to avoid double-recording
- when previous state is missing, the service falls back to placeholder content:
  - `[未知，可能因快照上限或重启丢失]`

This design is what allows edit/delete trails to survive restarts and cache misses reasonably well.

## Runtime Cache Model

`RuntimeCacheStore` is the in-memory acceleration layer for snapshots.

Per active ticket channel it tracks:

- latest known state per message ID
- create-snapshot count
- one-shot threshold flags:
  - `warn_900`
  - `warn_1000`

Why it exists:

- avoids re-reading the JSONL file for every edit/delete
- lets the service compare "before" and "after" content
- suppresses duplicate threshold warnings in the same runtime

On bootstrap restart recovery, `SnapshotService.restore_runtime_state()`:

- scans tickets in `submitted`, `sleep`, `transferring`, and `closing`
- rebuilds latest message state from snapshot files
- recomputes create count from `create` records only
- restores threshold flags based on the recomputed count
- corrects `tickets.message_count` if the stored count drifted from file reality

Corrupted or non-object JSONL lines are skipped instead of failing the whole restore.

## Limits and Warning Text

Snapshot create limits are guild-configurable.

Current config fields:

- `snapshot_warning_threshold`
- `snapshot_limit`
- `snapshot_warning_text`
- `snapshot_limit_text`

Defaults come from:

- `core/constants.py`
  - warning threshold: `900`
  - limit: `1000`
- `config/defaults.py`
  - default warning text
  - default hard-limit text

Validation rules in `services/config_validation.py`:

- both numeric values must be between `100` and `10000`
- `snapshot_limit` must be greater than `snapshot_warning_threshold`

Runtime behavior:

- warning text is sent once when create count reaches the warning threshold
- limit text is sent once when create count reaches the hard limit
- hitting the hard limit stops future `create` records only
- edit and delete events can still be appended after the hard limit
- reaching the hard limit also emits a ticket warning log through `LoggingService.send_ticket_log()`

## Query Surfaces

`SnapshotQueryService` has three main read shapes.

### Message timeline

`format_message_timeline(ticket_id, message_id)` renders the full timeline for one message:

- `create`
- zero or more `edit`
- optional `delete`

This is used by `/ticket message-history`.

### Recycle bin

`build_recycle_bin_text(ticket_id)` groups records by message ID and includes only messages that have at least one `delete` event.

For each deleted message it shows:

- author
- latest delete timestamp
- deleted content and attachments
- edit history summary when present

This is used by `/ticket recycle-bin`.

### Archive annotations and fallback source

`build_archive_annotations(ticket_id)` and `get_archive_snapshot_records(ticket_id)` feed `ArchiveRenderService`.

That is why snapshots matter even after close starts:

- live transcripts can be annotated with edit and deleted-message history
- fallback transcripts can be reconstructed from snapshot records when live channel history is unavailable

## Notes Model

Notes are a separate append-only JSONL stream under `storage/notes/{ticket_id}.jsonl`.

Each note record stores:

- `author_id`
- `author_name`
- `is_claimer`
- `timestamp`
- `content`

`is_claimer` is captured at write time. It marks whether the note author was the active claimer when the note was added.

Current note behavior:

- blank or whitespace-only content is rejected
- notes are not editable in place
- note formatting adds a `⭐` marker for claimer-authored notes
- corrupted note lines are skipped on read

## Access Rules

Evidence commands do not bypass normal ticket guards.

### Status gate

`TicketAccessService.load_snapshot_context()` only allows:

- `submitted`
- `sleep`
- `transferring`
- `closing`

So snapshots and notes are intentionally an active-ticket surface, not a post-archive browsing surface.

### Snapshot visibility

`assert_can_view_snapshots()` allows:

- ticket creator
- current-category staff
- ticket admin
- bot owner

### Notes visibility and writes

`assert_can_manage_notes()` allows:

- current-category staff
- ticket admin
- bot owner

The ticket creator may view their own snapshot history but may not read or add internal staff notes.

## Command Response Behavior

`EvidenceCog` returns snapshot and note output privately.

Current response policy:

- `/ticket message-history`
  - inline ephemeral text if short enough
  - file attachment if too long
- `/ticket recycle-bin`
  - always prefers file output
- `/ticket notes check`
  - inline ephemeral text if short enough
  - file attachment if too long
- `/ticket notes add`
  - confirmation only

## Retention and Cleanup

Snapshots and notes are not permanent operator storage after the ticket is fully done.

Current cleanup behavior in `CleanupService.cleanup_ticket()` removes:

- snapshot JSONL
- snapshot temp file
- notes JSONL
- related export/archive HTML files
- in-memory snapshot cache

This means:

- active tickets can query snapshots and notes in-channel
- archive generation can use snapshots during close/recovery
- after successful cleanup, the permanent operator record is the archive output, not the raw snapshot/note files

## Guardrails

- Do not treat `message_count` as total timeline rows; it tracks only `create` snapshots.
- Do not add new snapshot writers outside `SnapshotService`; raw/cached deduping and runtime cache coherence live there.
- Do not add note writes outside `NotesService`; the note lock and formatting assumptions live there.
- If you change snapshot payload fields, keep archive fallback and query formatting in sync.
- If you change retention or cleanup, update both this doc and `24-close-archive-recovery.md`.
- If you change access rules, update this doc and `../10-architecture/12-permission-model.md`.

## Extension Checklist

- Does the feature need draft-history bootstrap, live event capture, or both?
- Does it change which statuses are considered snapshot-active?
- Does it change the JSONL schema for `create`, `edit`, `delete`, or note records?
- Does it change warning/limit semantics or the meaning of `message_count`?
- Do timeline, recycle-bin, and archive fallback rendering still agree on the data model?
- Are notes still append-only and backward readable after the change?
- Does cleanup still remove the right files at `done`?

## Tests To Review

- `tests/services/test_snapshot_service.py`
- `tests/services/test_snapshot_query_service.py`
- `tests/services/test_notes_service.py`
- `tests/services/test_ticket_access_service.py`
- `tests/services/test_submit_service.py`
- `tests/storage/test_snapshot_store.py`
- `tests/services/test_archive_render_service.py`
- `tests/services/test_cleanup_service.py`

## Related Docs

- `24-close-archive-recovery.md`
- `../10-architecture/12-permission-model.md`
- `../10-architecture/13-config-model.md`
- `../10-architecture/14-data-model-and-migrations.md`
- `../90-decisions/92-adr-snapshot-policy.md`
