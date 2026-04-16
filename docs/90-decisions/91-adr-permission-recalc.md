# ADR 91 - Permission Recalculation

## Status

Accepted

## Date

2026-04-16

## Context

Ticket access is affected by multiple moving parts:

- creator identity
- category staff roles and staff users
- ticket lifecycle state
- claim, mute, transfer, and close behaviors
- runtime config and permission JSON changes

If multiple services build Discord permission overwrites independently, the project risks drift, stale access, and hard-to-debug behavioral mismatches between workflows.

## Decision

Permission recomputation is centralized through `StaffPermissionService.apply_ticket_permissions()`.

Services may decide *when* permissions need to be refreshed, but they should not hand-build overwrite payloads as their own final source of truth.

## Consequences

Positive:

- Permission behavior is easier to reason about and test.
- Workflow services can focus on state changes instead of Discord overwrite details.
- Regression risk is lower when permission rules evolve.

Tradeoffs:

- More workflows depend on a single service boundary.
- Permission changes may require broader regression testing across unrelated flows.

## What Must Be Updated When This Decision Changes

- `../10-architecture/12-permission-model.md`
- Any module docs that trigger permission recalculation
- Tests covering staff access, transfer, mute, close, and recovery

## Alternatives Considered

### Alternative 1: Let each workflow service build its own overwrites

Rejected because it spreads permission policy across many files and makes drift likely.

### Alternative 2: Keep policy in UI or cog layer

Rejected because permission behavior is business logic, not interaction wiring.

## Notes

If a future redesign introduces a different permission engine, add a follow-up ADR instead of silently replacing this decision in code.
