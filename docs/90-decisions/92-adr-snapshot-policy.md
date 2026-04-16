# ADR 92 - Snapshot Policy

## Status

Proposed

## Date

2026-04-16

## Context

The bot records ticket message history for evidence and operator review. Snapshot behavior affects:

- storage growth
- operator expectations during disputes
- recycle-bin style exports
- what data remains available after edits or deletes
- how close and archive workflows interact with message history

The codebase already has snapshot capture, query, and storage components, but the long-term policy should be explicit rather than inferred from implementation details.

## Decision

To be finalized. Use this ADR to record:

- which message events must be captured
- what payload is stored per snapshot
- whether retention or size limits are soft or hard
- what operators should expect after close
- how missing cached events are handled via raw event fallbacks

## Consequences

Expected benefits once finalized:

- clearer operator expectations
- better control over storage growth
- easier regression review for snapshot-related changes

Potential costs:

- stricter policy may require more migration or cleanup work
- stronger retention guarantees may increase storage usage

## Open Questions

- Are snapshots intended to be a best-effort audit trail or a stronger evidence record?
- What retention guarantees are promised to operators?
- Should closed tickets retain the same snapshot accessibility as active ones?
- Which limits are configurable per guild versus global defaults?

## Related Files

- `services/snapshot_service.py`
- `services/snapshot_query_service.py`
- `storage/snapshot_store.py`
- `services/notes_service.py`

## Follow-Up

Once the policy is decided:

1. Mark this ADR as Accepted.
2. Update `../10-architecture/13-config-model.md` if limits or retention are configurable.
3. Update `../20-modules/25-snapshot-and-notes.md`.
4. Add or update tests for the documented guarantees.
