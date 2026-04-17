# Odysseia Ticket Bot - Documentation

This documentation set is organized for long-term maintenance. It separates stable architecture concepts, feature-domain docs, operational runbooks, and design decisions so new features can extend the docs without turning every page into a catch-all.

## Reading Order

Start here when you need orientation:

| # | Document | Description |
|---|----------|-------------|
| 01 | [Deployment Guide](01-deployment.md) | How to create a Discord app/bot, configure intents and permissions, and run the bot |
| 02 | [Command and Source Map](02-command-source-map.md) | Quick reference for slash commands, UI entry points, event listeners, and their owning files |
| 03 | [Authorization and Access Control](03-authorization-and-access-control.md) | Identity sources, command authorization, and ticket access rules |
| 10 | [Architecture Overview](10-architecture/10-overview.md) | Startup path, layer boundaries, source ownership, and documentation rules |
| 11 | [Ticket Lifecycle](10-architecture/11-ticket-lifecycle.md) | State transitions, entry and exit paths, and lifecycle invariants |
| 12 | [Permission Model](10-architecture/12-permission-model.md) | How ticket access is derived, recalculated, and guarded |
| 13 | [Config Model](10-architecture/13-config-model.md) | Runtime configuration domains, validation flow, and storage model |
| 14 | [Data Model and Migrations](10-architecture/14-data-model-and-migrations.md) | Database schema ownership, migration rules, and schema change workflow |

## Architecture

Stable concepts that should change slowly and explain how the system is structured:

| # | Document | Description |
|---|----------|-------------|
| 10 | [Architecture Overview](10-architecture/10-overview.md) | Boot flow, layers, dependency boundaries, and cross-cutting rules |
| 11 | [Ticket Lifecycle](10-architecture/11-ticket-lifecycle.md) | Ticket state map and lifecycle checkpoints |
| 12 | [Permission Model](10-architecture/12-permission-model.md) | Permission sources, recomputation flow, and guardrails |
| 13 | [Config Model](10-architecture/13-config-model.md) | Config groups, validation, defaults, and runtime editing |
| 14 | [Data Model and Migrations](10-architecture/14-data-model-and-migrations.md) | Tables, repositories, schema versioning, and migration expectations |

## Modules

Feature-domain docs. Update these when behavior changes inside a user-facing flow:

| # | Document | Description |
|---|----------|-------------|
| 21 | [Panel and Draft](20-modules/21-panel-and-draft.md) | Public panel flow, draft creation, draft editing, and draft UI |
| 22 | [Submit, Queue, and Capacity](20-modules/22-submit-queue-capacity.md) | Submission path, queueing decisions, and ticket capacity controls |
| 23 | [Staff Actions](20-modules/23-staff-actions.md) | Claiming, sleeping, renaming, muting, priority changes, and transfers |
| 24 | [Close, Archive, and Recovery](20-modules/24-close-archive-recovery.md) | Closing flows, transcript generation, cleanup, and channel recovery |
| 25 | [Snapshot and Notes](20-modules/25-snapshot-and-notes.md) | Message snapshots, recycle-bin exports, and staff notes |
| 26 | [Runtime, Bootstrap, and Scheduler](20-modules/26-runtime-bootstrap-scheduler.md) | Startup orchestration, runtime helpers, persistent views, and scheduled tasks |

## Operations

Docs for bot operators and maintainers:

| # | Document | Description |
|---|----------|-------------|
| 31 | [Config Runbook](30-operations/31-config-runbook.md) | Operational guide for changing config, permissions, and panel text safely |
| 32 | [Troubleshooting](30-operations/32-troubleshooting.md) | Symptom-driven debugging notes and verification steps |
| 33 | [Release Checklist](30-operations/33-release-checklist.md) | Pre-release checks for schema, commands, permissions, and docs |

## Decisions

Architecture decision records. Use these when a change needs a durable "why", not just a "what":

| # | Document | Description |
|---|----------|-------------|
| 91 | [ADR - Permission Recalculation](90-decisions/91-adr-permission-recalc.md) | Records why permission recomputation is centralized |
| 92 | [ADR - Snapshot Policy](90-decisions/92-adr-snapshot-policy.md) | Records snapshot scope, retention, and operator expectations |

## Documentation Rules

- Update [02-command-source-map.md](02-command-source-map.md) whenever you add or remove a slash command, button, modal, or listener entry point.
- Update one of the `20-modules/` docs whenever a user-facing workflow changes.
- Update one of the `10-architecture/` docs when the underlying rules or data model change.
- Add or update an ADR in `90-decisions/` when the implementation changes for reasons future maintainers will not be able to infer from code alone.
- Prefer linking to the owning file and service chain instead of duplicating large code excerpts in docs.
