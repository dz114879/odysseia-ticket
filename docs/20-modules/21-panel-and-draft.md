# Panel and Draft

This module doc covers the user-facing entry into the system before staff can see the ticket: public panel publishing, category selection, draft creation, draft editing, and draft abandonment.

## Scope

In scope:

- active public panel publishing, refresh, and removal
- category selection and preview-confirm flow
- draft channel creation and duplicate-draft prevention
- draft rename and abandon actions
- draft welcome embed and persistent action buttons
- draft-stage privacy and timeout hooks

Out of scope:

- submit, queue, and capacity decisions after draft handoff
- staff-side ticket workflow after `submitted`

## User Entry Points

| Entry Point | Owning File | Primary Service |
|------------|-------------|-----------------|
| `/ticket panel create` | `cogs/panel_cog.py` | `PanelService.create_panel()` |
| `/ticket panel refresh` | `cogs/panel_cog.py` | `PanelService.refresh_active_panel()` |
| `/ticket panel remove` | `cogs/panel_cog.py` | `PanelService.remove_active_panel()` |
| Public panel category select | `discord_ui/public_panel_view.py` | `PanelService.preview_panel_request()` |
| Draft create confirm button | `discord_ui/public_panel_view.py` | `PanelService.create_draft_from_panel_request()` |
| `/ticket draft rename` | `cogs/draft_cog.py` | `DraftService.rename_draft_ticket()` |
| `/ticket draft abandon` | `cogs/draft_cog.py` | `DraftService.abandon_draft_ticket()` |
| Draft welcome buttons | `discord_ui/draft_views.py` | `SubmitService.submit_draft_ticket()`, `DraftService.rename_draft_ticket()`, `DraftService.abandon_draft_ticket()` |

## Owning Source Files

- `cogs/panel_cog.py`
- `cogs/draft_cog.py`
- `cogs/submit_cog.py`
- `discord_ui/public_panel_view.py`
- `discord_ui/draft_views.py`
- `discord_ui/panel_embeds.py`
- `discord_ui/draft_embeds.py`
- `services/panel_service.py`
- `services/creation_service.py`
- `services/draft_service.py`
- `services/validation_service.py`
- `services/draft_timeout_service.py`
- `db/repositories/panel_repository.py`

## Public Panel Flow

Current happy path:

1. `/ticket panel create` or `/ticket panel refresh` validates that guild setup is complete and that at least one category is enabled.
2. `PanelService` builds the public embed from current guild config and enabled categories.
3. The active panel is stored in `panels` with a fresh nonce.
4. A user picks a category in `PublicPanelView`.
5. `PanelService.preview_panel_request()` validates that the clicked message and nonce still match the active panel record.
6. The bot sends an ephemeral preview embed plus `DraftCreateConfirmView`.
7. The user confirms, and `CreationService.create_draft_ticket()` creates or reuses a draft.

## Panel Rules

- Public panel category options come from enabled categories only.
- Category ordering follows `GuildRepository.list_categories(..., enabled_only=True)`, which sorts by `sort_order ASC`, then `category_key ASC`.
- The select menu shows at most the first 25 enabled categories because of Discord select limits.
- The public embed text lists at most the first 10 categories in its visible summary field.
- Panel create, refresh, and remove are limited to the configured ticket admin role, guild administrators, or the bot owner.
- Only one panel per guild is considered active; `replace_active_panel()` deactivates older active records.
- Refresh rotates the stored nonce, so older panel messages intentionally become stale even if the Discord message still exists.
- Removing the active panel only deactivates the stored record by default; the old Discord message stays visible but should no longer be trusted.
- `PublicPanelView` is persistent (`timeout=None`) and restored on startup from active panel records.

## Draft Creation Flow

`CreationService.create_draft_ticket()` currently does this:

1. acquires `draft-create:{guild_id}:{creator_id}` to serialize per-user creation
2. checks whether the creator already has a live `draft` channel in this guild
3. validates the selected category either through active-panel context or direct category lookup
4. reserves the next category-scoped ticket number through `CounterRepository.increment()`
5. builds `ticket_id` as `{guild_id}-{category_slug}-{number:04d}`
6. creates the Discord channel under `config.ticket_category_channel_id`
7. sends and pins the draft welcome embed with `DraftWelcomeView`
8. persists the `TicketRecord` with `status=draft`

If channel creation or welcome-message send fails, the service deletes the just-created channel and aborts.

## Draft Privacy And Visibility

Draft creation is intentionally a permission exception outside the normal active-ticket permission service.

Initial overwrites are built by `CreationService._build_draft_overwrites()`:

- `@everyone` hidden
- creator visible and writable
- bot visible and manageable
- configured `admin_role_id` hidden
- current category staff roles hidden
- current category staff users hidden

Operational rule:

- staff visibility starts only after submit or queued promotion, never during `draft`

## Duplicate-Draft Rule

- A creator can have only one live draft per guild at a time if the existing `draft` row still points to a resolvable Discord channel.
- If an older `draft` row exists but its channel is already gone, creation ignores that stale row and allows a new draft.
- Re-clicking the panel for an already-live draft returns the existing channel instead of creating a second one.

## Draft Naming Rules

- New draft channels start with the category display name, truncated to 95 characters.
- `/ticket draft rename` and the draft rename button both call `DraftService.rename_draft_ticket()`.
- Renaming slugifies the requested text into lowercase alphanumeric-plus-hyphen form and truncates to 95 characters.
- Blank or whitespace-only rename input is rejected.

Important submit interaction:

- `SubmissionGuardService` treats a draft as "still missing a title" when the current channel name still equals `category.display_name`.
- That is why default-named drafts open `DraftSubmitTitleModal` during submit, while already-renamed drafts do not.

## Draft Welcome UI

The pinned welcome message uses `build_draft_welcome_embed()` and `DraftWelcomeView`.

Current behavior:

- welcome text uses `config.draft_welcome_text` when present
- otherwise it uses `build_default_draft_welcome_text()` with the guild's current draft timeout hours
- `DraftWelcomeView` is persistent (`timeout=None`) and registered globally once in `SubmitCog`
- the view exposes three actions: submit, abandon, rename
- abandon uses a second ephemeral confirm view with a 60-second timeout

## Draft Actions And Guardrails

- rename and abandon are creator-only
- both actions require the ticket to still be in `draft`
- abandon first writes `status=abandoned`, then deletes the channel
- if channel deletion fails during abandon, the service rolls the database status back to `draft`
- draft submit guard also requires the actor to be the creator and the guild/category config to still exist

## Draft Activity And Timeout Hooks

Draft-timeout behavior is split between message events and sweeps:

- `DraftTimeoutService.handle_message()` records the creator's first and subsequent non-bot messages while the ticket is still `draft`
- `sweep_draft_warnings()` sends the one-hour warning before the relevant timeout
- `sweep_expired_drafts()` abandons overdue drafts
- `TicketBot.on_ready()` also runs one immediate expired-draft sweep after startup

Important user-facing rule:

- draft messages are not live-captured by the snapshot system
- snapshot bootstrap happens only once the ticket first becomes `submitted`

## Failure Modes

- panel select says it is stale: the stored active panel changed or the clicked message is old
- panel refresh fails: the active panel message or hosting channel is gone; recreate instead of retrying indefinitely
- draft create silently returns the old channel: the user already has a live draft
- draft create fails after recent config changes: the guild setup target or selected category no longer validates
- draft abandon leaves the row in `draft`: channel deletion failed and the rollback path restored state

## Extension Checklist

- Does the feature change how many categories can be presented or how they are sorted?
- Does it change the stale-panel contract for old messages?
- Does it change draft privacy before submit?
- Does it allow more than one live draft per creator or per guild?
- Does it change the default-name or title-completion rule used by submit?
- Does it require the welcome view to add or remove a persistent component?

## Tests To Review

- `tests/cogs/test_panel_cog.py`
- `tests/cogs/test_draft_cog.py`
- `tests/services/test_panel_service.py`
- `tests/services/test_creation_service.py`
- `tests/services/test_draft_service.py`
- `tests/services/test_draft_timeout_service.py`
- `tests/services/test_submission_guard_service.py`

## Related Docs

- `../10-architecture/11-ticket-lifecycle.md`
- `../10-architecture/12-permission-model.md`
- `../10-architecture/13-config-model.md`
- `22-submit-queue-capacity.md`
