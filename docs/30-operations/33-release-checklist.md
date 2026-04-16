# Release Checklist

Use this checklist before shipping any release that changes bot behavior, slash-command surfaces, schema, config, permissions, queueing, archive flow, or operator workflow.

## Scope

This runbook is for real releases, not pure doc-only edits. It is optimized for this bot's actual startup path:

1. `bot.py:main()`
2. `load_env_settings()`
3. `TicketBot.setup_hook()`
4. `BootstrapService.bootstrap()`
5. extension load
6. active public panel view restore
7. optional slash-command sync

## Before Cutting A Release

- Review the affected module docs under `../20-modules/`.
- Update `../02-command-source-map.md` if any slash command, button, select, modal, or event listener changed.
- Update architecture docs if lifecycle, permission, config, or persistence invariants changed.
- Update `31-config-runbook.md` or `32-troubleshooting.md` if operators need new rollback or diagnosis steps.
- Add or update an ADR under `../90-decisions/` if the behavior change is not obvious from code alone.
- Decide the slash-command sync plan:
  - if `AUTO_SYNC_COMMANDS=true`, restart will sync automatically
  - if `AUTO_SYNC_COMMANDS=false`, release is not complete until you have a separate sync plan

## Required Automated Checks

Run the smallest relevant set first, then finish with the broad checks.

| Change Area | Minimum Commands |
|------------|------------------|
| Any code change | `uv run ruff check .` |
| Schema / migrations | `uv run pytest tests/test_migrations.py tests/services/test_bootstrap_service.py` |
| Repository changes | `uv run pytest tests/repositories/` |
| Runtime / bootstrap / scheduler changes | `uv run pytest tests/runtime/ tests/services/test_bootstrap_service.py tests/services/test_bootstrap_service_restore.py` |
| Service-layer changes | `uv run pytest tests/services/test_<affected_service>.py` |
| Final sweep | `uv run pytest -q` |

If the change touches permissions, queueing, or lifecycle transitions, include both happy-path and blocked-path tests before release.

## Pre-Deploy Checks

- Confirm the target environment and target guild.
- Confirm `.env` contains the correct `DISCORD_BOT_TOKEN` and `DISCORD_APP_ID`.
- Confirm the resolved `SQLITE_PATH` and `LOG_FILE` match the deployment environment.
- If `.env` changed, keep the previous file or values ready for rollback.
- If `/ticket permission` JSON or setup targets changed, keep the last known-good values ready for rollback.
- If the release changes schema or file formats, back up the resolved SQLite database and `storage/` first.

## Schema Release Guardrail

Schema changes have a stricter rollback rule than normal code changes.

- `CURRENT_SCHEMA_VERSION` in `core/constants.py` must match the last declared migration in `db/migrations.py`
- migration code must stay atomic inside one `DatabaseManager.session()`
- once a newer schema version has been applied, an older binary will refuse to start with `数据库 schema 版本高于当前程序支持的版本`

So for schema releases, rollback means:

- restore the database backup, or
- deploy a newer compatible fix

Do not assume you can simply redeploy the previous commit after migrations have run.

## Deploy Order

1. Stop the running bot.
2. Deploy the new code.
3. Run `uv sync` if dependencies changed.
4. Start the bot with `uv run python bot.py`.
5. Watch startup logs until the bot is fully ready.

## Startup Logs To Confirm

At minimum, confirm these log patterns in the target environment:

- `Bootstrapping ...`
- `Bootstrap finished. schema=... applied=... scheduler_handlers=...`
- `Restored snapshot runtime cache for ...` when snapshot restore had work to do
- `Recovered ... incomplete archive flow(s) during bootstrap.` when recovery had work to do
- `Restored ... active panel persistent view(s).`
- `Application commands synced: ...` when `AUTO_SYNC_COMMANDS=true`
- `... is ready as ...`

If startup stops before the ready log, treat the release as failed even if the process is still running.

## Manual Verification Matrix

| Area | Verify | Expected Result |
|------|--------|-----------------|
| Slash commands | invoke one changed `/ticket ...` command | command runs without tree error or timeout |
| Public panel | refresh or recreate the active panel | panel renders, buttons work, old refreshed panel becomes stale by design |
| Draft -> submit | create one draft and submit it | ticket reaches `submitted` or `queued` according to capacity rules |
| Queue / capacity | only if queue logic changed | over-capacity submission becomes `queued`, and queued ticket is not exposed to staff until promoted |
| Staff actions | only if staff flows changed | verify at least one representative action from slash command and one from the staff panel |
| Close / archive | only if close, archive, or recovery changed | creator close-request and staff direct close behave correctly; archive message reaches archive channel; live channel is deleted |
| Snapshot / notes | only if evidence flows changed | `/ticket message-history`, `/ticket recycle-bin`, or `/ticket notes ...` still read expected data |
| Config UI | only if config changed | `/ticket config`, `/ticket setup`, and `/ticket permission` show and persist the expected values |

## Release-Specific Notes

Use these extra checks when the touched area matches:

- panel/category presentation change:
  verify the active panel reflects new text or category metadata after refresh
- permission model change:
  verify a representative new ticket, not only an existing ticket channel, because `/ticket permission` does not bulk rewrite all old overwrites
- close timing change:
  verify both the displayed closing window and the recovery sweep path
- snapshot limit change:
  verify warning and hard-limit behavior separately, because hard limit stops new `create` snapshots but not later `edit` / `delete` records

## Rollback

| Change Type | Rollback Path |
|------------|---------------|
| Code only, no schema bump | redeploy previous code and restart |
| `.env` change | restore previous values and restart |
| `/ticket setup` change | re-run `/ticket setup` with previous channels/role |
| `/ticket config` change | reapply previous values or submit blank text to restore defaults |
| `/ticket permission` change | re-upload previous known-good JSON |
| Schema bump already applied | restore DB backup or roll forward with a compatible fix |

## Post-Release

- Verify one live ticket flow end-to-end in the target environment.
- Check the archive channel and log channel for unexpected warnings.
- If operators needed manual intervention, record it in `31-config-runbook.md` or `32-troubleshooting.md`.
- If the release changed an invariant, update the matching architecture/module doc immediately instead of leaving it to a later cleanup pass.

## Related Docs

- `../02-command-source-map.md`
- `../10-architecture/14-data-model-and-migrations.md`
- `31-config-runbook.md`
- `32-troubleshooting.md`
