# Architecture Overview

This document is the stable map of the codebase. It should explain how the bot is put together and where new work belongs before readers drop into feature-specific docs.

## Purpose

- Explain the startup path and runtime composition.
- Keep layer ownership explicit.
- Provide a durable map of where major responsibilities live.
- Give maintainers a short checklist for placing new code and updating docs.

## Boot Sequence

Current startup flow:

1. `bot.py:main()`
2. `load_env_settings()`
3. `TicketBot.setup_hook()`
4. `BootstrapService.bootstrap()`
5. Logging, database, migrations, runtime infrastructure, services, scheduler handlers
6. Cog loading from `cogs/*_cog.py`
7. Restoration of persistent panel views

If the startup order changes, update this section and the runtime module doc.

## Layer Boundaries

The repository follows these ownership rules:

| Layer | Responsibility | Should Not Do |
|------|----------------|---------------|
| `cogs/` | Discord commands, interaction wiring, top-level listeners | Business rules, SQL, data shaping |
| `discord_ui/` | Views, embeds, modals, and interaction UI composition | Business orchestration, persistence |
| `services/` | Business workflows, transactions, orchestration, coordination | Raw SQL, Discord-only presentation details |
| `db/repositories/` | SQL access and row-to-model mapping | Workflow decisions, permission policy |
| `core/` | Shared enums, constants, models, and domain errors | Runtime orchestration |
| `runtime/` | Scheduler, cache, locks, cooldowns, debounce helpers | Business-domain decisions |
| `config/` | Environment loading and static/default settings | Ticket workflow logic |
| `storage/` | File-based persistence for notes, snapshots, archives, exports | SQL-backed domain logic |

## Cross-Cutting Rules

These rules are important enough to keep at architecture level:

- Repository methods accept optional `connection=` parameters for transaction composition.
- Repositories return domain models from `core/models.py`, never raw SQLite rows.
- Permission recalculation goes through `StaffPermissionService.apply_ticket_permissions()`.
- Migrations stay atomic inside one `DatabaseManager.session()` transaction.
- `CURRENT_SCHEMA_VERSION` in `core/constants.py` must match the newest migration.
- Interaction callbacks should call shared `safe_defer(interaction)` first unless a modal must be sent immediately.

## Source Map by Responsibility

Use this as the high-level ownership map:

| Responsibility | Primary Files |
|----------------|---------------|
| Bot entry and event dispatch | `bot.py` |
| Bootstrap and wiring | `services/bootstrap_service.py` |
| Ticket creation and panel flow | `services/panel_service.py`, `discord_ui/public_panel_view.py`, `cogs/panel_cog.py` |
| Draft workflow | `services/draft_service.py`, `discord_ui/draft_views.py`, `cogs/draft_cog.py` |
| Submission and queueing | `services/submit_service.py`, `services/submit_side_effects.py`, `services/submit_welcome_service.py`, `services/queue_service.py`, `services/capacity_service.py`, `cogs/submit_cog.py` |
| Staff controls | `services/claim_service.py`, `services/sleep_service.py`, `services/rename_service.py`, `services/transfer_service.py`, `discord_ui/staff_panel_view.py`, `cogs/staff_cog.py` |
| Close and archive | `services/close_service.py`, `services/close_request_service.py`, `services/archive_service.py`, `services/archive_render_service.py`, `services/archive_send_service.py` |
| Snapshots and notes | `services/snapshot_service.py`, `services/snapshot_query_service.py`, `services/notes_service.py`, `storage/snapshot_store.py`, `storage/notes_store.py` |
| Permissions and config | `services/staff_permission_service.py`, `services/permission_config_service.py`, `services/config_validation.py`, `discord_ui/config_views.py`, `discord_ui/config_modal_shared.py`, `discord_ui/config_setting_modals.py`, `discord_ui/config_text_modals.py`, `cogs/config_cog.py` |
| Persistence | `db/repositories/`, `db/connection.py`, `db/migrations.py` |

## New Feature Placement Checklist

When adding a new feature, document the decisions here:

| Question | Notes |
|----------|-------|
| What user entry point starts the flow? | Slash command, button, modal, event, or scheduler |
| Which service owns orchestration? | Keep orchestration out of cogs and repositories |
| Does it need persistence? | If yes, decide repository and model ownership |
| Does it affect permissions? | Route changes through centralized permission recomputation |
| Does it change lifecycle rules? | Update `11-ticket-lifecycle.md` |
| Does it add config or operational impact? | Update `13-config-model.md` and an operations doc |

## Documentation Maintenance

Update this page when:

- A new top-level layer or directory is introduced.
- Startup sequencing changes.
- The project changes how responsibilities are divided between layers.
- A new cross-cutting rule becomes important to future maintainers.
