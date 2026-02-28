# Backtesting Tests (Unit + Integration + Acceptance)

This project relies on a layered test strategy:

1. **Unit tests** (fast, deterministic): validate core entities and backtest engine rules.
2. **Backtest integration tests** (slower, high value): run real strategies against backtesting data sources.
3. **Acceptance backtests** (manual, end-to-end): run from `Strategy Library/` and inspect artifacts
   (`*_trades.html`, `*_tearsheet.html`, `*_stats.csv`) for realism and regressions.

## Recommended workflow (local + GitHub CI)

Local runs are great for fast feedback, but the full suite is often faster and more reliable on GitHub because CI runs tests in parallel (sharded).

Recommended approach on `version/X.Y.Z` branches:

- Run targeted tests locally for quick iteration.
- Push early/often so GitHub CI can run the full sharded suite for release confidence.

Treat **green GitHub CI** as the “release-ready” signal since it matches the release workflow environment more closely than a single local machine.

## Test authority (“Legacy tests win”)

When tests fail, **how you fix them depends on how old the test is**:

- **> 1 year old:** treat as **LEGACY / high-authority**. Fix the **code**, not the test.
- **6–12 months:** investigate carefully; usually fix the code.
- **< 6 months:** the test may still be evolving; confirm intent before changing.

This prevents “performance fixes” from silently changing broker-like semantics.

## Acceptance backtests (ThetaData)

The acceptance suite lives in:

- `Strategy Library/Demos/*` (do not edit demo strategy files)
- `Strategy Library/logs/*` (artifacts)

The canonical acceptance suite definition (strategies, windows, and what to validate) lives in:

- `docs/ACCEPTANCE_BACKTESTS.md`

CI runs these same demos (as normal `pytest` tests under `tests/backtest/`) via:
- `tests/backtest/test_acceptance_backtests_ci.py`

Acceptance CI has one extra invariant beyond “metrics match”: the S3 cache is expected to be warm for the canonical windows,
so any Data Downloader / queue usage is treated as a failure signal (cache regression).

The latest “why are we changing this?” investigation context (stalls/perf history) lives in the session handoffs:

- `docs/handoffs/2026-01-01_THETADATA_SESSION_HANDOFF.md`

### When acceptance backtests fail due to downloader usage

If you see acceptance failures like:

- `exit=86` and `[ACCEPTANCE][TRIPWIRE] Attempted to call Data Downloader …`

that means one of:

1) S3 is missing a required cache object for the canonical window, or
2) cache keying/schema changed and the code is looking under a new key/version, or
3) a placeholder-only cache object is suppressing a needed refresh.

The intended workflow is:

- **Warm/fill S3 outside CI** (tripwire OFF) using:
  - `scripts/warm_acceptance_backtests_cache.py`
- Then re-run acceptance (tripwire ON) and confirm:
  - no downloader calls,
  - `thetadata_queue_telemetry.submit_requests == 0` in `*_settings.json`.

See the detailed handoff:
- `docs/handoffs/2026-01-06_ACCEPTANCE_BACKTESTS_HANDOFF.md`

## Backtest profiling (opt-in)

Backtests can produce a profiling artifact to help explain production-vs-local speed differences.

- Enable: `BACKTESTING_PROFILE=yappi`
- Output artifact: `*_profile_yappi.csv` (written alongside other backtest artifacts like `*_trades.csv`).
- Settings metadata: written into `*_settings.json` under `profiling_*` keys when enabled.

## Performance regressions

Performance changes are only accepted when:

- Unit tests stay green
- Backtest integration tests stay green
- Acceptance backtests remain **broker-like** (no lookahead bias, stable option MTM, realistic fills)

If you’re unsure whether a behaviour change is “more accurate” vs “just faster”, prefer accuracy and add a regression
test to lock in the correct semantics.
