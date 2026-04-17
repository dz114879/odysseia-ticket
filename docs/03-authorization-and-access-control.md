# Authorization and Access Control

This document explains who can do what in the bot. It sits above the low-level permission recomputation rules in `10-architecture/12-permission-model.md` and focuses on authorization behavior as seen by operators, maintainers, and feature authors.

## Scope

In scope:

- identity and authorization sources
- global management-command permissions
- ticket-level action guards
- the difference between action guards and Discord channel overwrites
- the most important authorization edge cases

Out of scope:

- the full overwrite recomputation algorithm
- Discord app creation and bot invite setup

## Core Principle

This bot does not implement a separate login or account system.

- Identity comes from Discord users, members, roles, and guild permissions.
- Authorization is enforced by bot code on top of that identity.
- Channel visibility and send access are then enforced through Discord permission overwrites.

A caller may pass an action guard but still be readonly in the channel, especially in strict claim mode.

## Authorization Inputs

| Input | Source | Meaning |
|------|--------|---------|
| Bot owner | `bot.is_owner(user)` | Global superuser bypass for command and ticket-action guards. |
| Discord administrator | `member.guild_permissions.administrator` | Global guild-level bypass used by setup and ticket-admin checks. |
| Ticket admin role | `GuildConfigRecord.admin_role_id` | Configured global ticket-admin role for this guild. |
| Category staff roles | `TicketCategoryConfig.staff_role_ids_json` | Category-scoped staff eligibility. |
| Category staff users | `TicketCategoryConfig.staff_user_ids_json` | Explicit category-scoped staff eligibility. |
| Ticket creator | `TicketRecord.creator_id` | Creator-only rights such as draft submit/rename/abandon and close-request initiation. |
| Claim mode | `GuildConfigRecord.claim_mode` | Controls whether visible staff are writable or readonly by default. |
| Active claimer | `TicketRecord.claimed_by` | May receive special write access in strict claim mode. |
| Muted participants | `ticket_mutes` rows | Preserve view access while removing send access. |

## Effective Roles

Current code effectively recognizes these role buckets:

| Role Bucket | How It Is Derived | Typical Capabilities |
|------------|-------------------|----------------------|
| Ticket admin | bot owner, Discord administrator, or member with `admin_role_id` | Guild-level ticket management and ticket-admin override inside any category. |
| Category staff | ticket admin, member listed in `staff_user_ids`, or member holding any `staff_role_ids` for the current category | Staff actions on tickets in that category. |
| Ticket creator | `ticket.creator_id == actor.id` | Draft ownership, submission, snapshot viewing, and close-request initiation. |
| Participant | Explicit runtime participant overwrite | Can view or send if a workflow grants it. |
| Ordinary guild member | None of the above | Can use the public panel, but cannot manage tickets unless they become creator or staff. |

Important distinction:

- Ticket admin is global to the guild.
- Category staff is category-scoped.
- Ticket admin automatically passes the staff guard.

## Guard Layers

Authorization is split into three layers:

1. Entry-point command guards in cogs.
2. Ticket-action guards in services.
3. Discord channel overwrites for visibility and send access.

The main owners are:

- global management commands: `cogs/admin_cog.py`, `cogs/panel_cog.py`, `cogs/config_cog.py`, `cogs/permission_cog.py`
- staff eligibility: `services/staff_guard_service.py`
- creator-only submit path: `services/submission_guard_service.py`
- snapshot and notes access: `services/ticket_access_service.py`
- channel overwrite recomputation: `services/staff_permission_service.py`

## Management Command Authorization

These commands are not category-scoped ticket actions. They are guild-level management surfaces.

| Surface | Who Can Use It | Notes |
|--------|----------------|-------|
| `/ticket setup` | Bot owner or Discord administrator | Initial setup and reconfiguration path. |
| `/ticket panel create` | Bot owner, Discord administrator, or configured ticket admin role | Same rule also applies to `refresh` and `remove`. |
| `/ticket panel refresh` | Bot owner, Discord administrator, or configured ticket admin role | Refresh rotates panel nonce and intentionally stales older messages. |
| `/ticket panel remove` | Bot owner, Discord administrator, or configured ticket admin role | Can retire the active panel record without deleting the Discord message. |
| `/ticket config` | Bot owner, Discord administrator, or configured ticket admin role | Opens the runtime config UI. |
| `/ticket permission` | Bot owner, Discord administrator, or configured ticket admin role | Updates stored category staff mappings from JSON. |
| `/ticket permission-help` | Bot owner, Discord administrator, or configured ticket admin role | Exports the current permission-config helper text. |

## Ticket-Level Authorization

Once a request is inside a ticket workflow, the rules become more specific.

| Workflow | Who Can Trigger | Main Guard |
|---------|-----------------|-----------|
| Draft create from public panel | Any guild member using the active panel | Panel nonce and duplicate-draft checks, not staff auth. |
| Draft rename / abandon | Current ticket creator only | `DraftService` creator checks. |
| Draft submit | Current ticket creator only | `SubmissionGuardService.inspect_submission()` |
| Snapshot view | Ticket creator, current-category staff, ticket admin, or bot owner | `TicketAccessService.assert_can_view_snapshots()` |
| Notes add / check | Current-category staff, ticket admin, or bot owner | `TicketAccessService.assert_can_manage_notes()` |
| Claim / sleep / rename / priority / mute / transfer | Current-category staff, ticket admin, or bot owner | `StaffGuardService.assert_staff_actor()` plus action-specific state checks |
| Unclaim / transfer-claim | Usually current claimer; ticket admin may override specific cases | Claim-specific rules in `ClaimService` |
| `/ticket close` by staff | Current-category staff, ticket admin, or bot owner | `CloseService.initiate_close()` enters `closing` directly |
| `/ticket close` by creator | Current ticket creator only, and only when not allowed to close directly as staff | `CloseRequestService.request_close()` creates a staff-review request |
| `/ticket close-cancel` | Current-category staff, ticket admin, or bot owner | `CloseService.revoke_close()` |

## Guard vs Overwrite

Passing a service guard is not the same thing as having writable channel access.

Current model:

- Draft channels are a special case. Staff and the configured admin role are hidden until submit or queued promotion.
- Active-ticket overwrites are normally recalculated by `StaffPermissionService.apply_ticket_permissions()`.
- In `relaxed` claim mode, visible staff can send messages.
- In `strict` claim mode, visible staff are readonly by default and only the active claimer gets write access.
- Creator and explicit participants are handled as participant-style overwrites, not staff overwrites.
- Mute keeps `view_channel=True` but forces `send_messages=False`.
- Close start temporarily freezes the whole ticket into a readonly state through close-specific support code.

## Important Edge Cases

- Draft visibility is intentionally different from submitted-ticket visibility. A category's staff config does not make staff visible during `draft`.
- `/ticket permission` updates stored category config immediately, but it does not bulk rewrite every live ticket channel. Existing tickets pick up the new mapping only when a later workflow recalculates overwrites.
- Category transfer does not hide old staff at request time. The old/new staff visibility switch happens only when transfer execution actually changes `category_key`.
- Snapshot access is broader than notes access. The creator may view snapshots, but notes remain staff-only.
- The close command is dual-path: staff close directly, creator requests review.
- Some older user-facing error text is narrower than the real code path. For example, the effective code path treats Discord administrators as ticket-admin bypass actors in multiple places.

## Troubleshooting Questions

When debugging "permission" reports, identify which layer failed:

1. Did the caller fail an entry-point or service guard?
2. Did the caller pass the guard but still lack the expected overwrite?
3. Did the guild config or category staff mapping change without a later recomputation trigger?

Common examples:

- Staff can see a ticket but cannot speak: likely strict claim mode with no active claimer.
- A creator can read snapshots but cannot read notes: expected behavior.
- Updating permission JSON does not fix old channels immediately: expected until a recomputation workflow runs.
- A guild admin can perform a ticket-admin action even if the error text only mentions the configured admin role: current code treats Discord administrator as a bypass.

## Related Docs

- `02-command-source-map.md`
- `10-architecture/12-permission-model.md`
- `20-modules/21-panel-and-draft.md`
- `20-modules/23-staff-actions.md`
- `30-operations/31-config-runbook.md`
