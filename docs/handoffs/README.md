# Handoffs (local/private)

This folder is for **local/private** session handoffs intended to let a new contributor (human or agent) pick up work without re-discovering context.

Going forward, new handoff files should **not** be committed to git (see `docs/handoffs/.gitignore`). If something is worth sharing publicly, convert it into:
- a canonical doc under `docs/`, or
- an investigation under `docs/investigations/`, or
- a test, script, or code comment where it belongs.

## Naming convention (required)

Use **date-first** filenames so they sort chronologically:

`YYYY-MM-DD_<SHORT_TITLE>.md`

Examples:
- `2026-01-04_THETADATA_CI_ACCEPTANCE_GATE_HANDOFF.md`
- `2026-01-01_THETADATA_SESSION_HANDOFF.md`

## What belongs here

- Session notes / investigation status
- Concrete runbooks (how to reproduce, how to validate)
- Links to key files and “map” docs
- Open questions / next actions / owner handoffs

## What does *not* belong here

- Long-term canonical docs (put those in `docs/`, e.g., `docs/BACKTESTING_ARCHITECTURE.md`)
- One-off experiment logs (store those under `Strategy Library/logs` or an investigation folder)

## Index (newest first)

- `2026-01-04_NVDA_SPX_PROD_PARITY_STARTUP_HANDOFF.md`
- `2026-01-04_THETADATA_CI_ACCEPTANCE_GATE_HANDOFF.md`
- `2026-01-01_THETADATA_SESSION_HANDOFF.md`
- `2025-12-26_THETADATA_SESSION_HANDOFF.md`
- `2025-12-18_THETADATA_MERGE_HANDOFF.md`
- `2025-12-17_THETADATA_BACKTESTING_HANDOFF.md`
