# PROD SPEED BENCHMARK PROTOCOL

> Canonical benchmark windows, measurement template, and how to attribute production slowness (downloader vs compute vs startup).

**Last Updated:** 2026-01-09  
**Status:** Active  
**Audience:** Developers + AI Agents

---

## Canonical Benchmarks (1-week windows)

These windows are used for cold→warm comparisons. They are intentionally short to iterate quickly, but must contain trades.

1) **SPX Short Straddle (intraday)**
   - Strategy: `SPX Short Straddle Intraday (Copy 2).py`
   - Window (1-week): `2025-04-21 → 2025-04-28`

2) **Alpha Picks Options**
   - Strategy: `Alpha Picks Options.py`
   - Window (1-week): `2025-05-05 → 2025-05-12`

Notes:
- If a window produces no trades after a future strategy revision, pick a nearby week with trades and update this doc.

---

## Measurement Rules

For each benchmark, run **twice back-to-back** with the same S3 cache version:

- Run #1: cold local disk + target S3 version (expected downloader activity if S3 isn’t warm for that keyspace)
- Run #2: same S3 version (expected near-zero downloader submits if cache coverage is correct)

Always record:
- `LUMIBOT_CACHE_S3_VERSION`
- wall time
- `Submitted to queue` count by `path=...` (CloudWatch Insights in prod)
- download_status snapshots (per-asset `current/total`)
- startup buckets:
  - submit → task started → first log → first progress row

---

## Benchmark Record Template (copy/paste)

```markdown
## <BENCHMARK NAME> — <ENV> — <DATE>

- LumiBot version: <X.Y.Z>
- manager_bot_id: <uuid>
- window: <YYYY-MM-DD → YYYY-MM-DD>
- cache: S3 version=<...>, local cache=<cold|warm>

### Timings
- submit→task-start: <...s>
- task-start→first-log: <...s>
- submit→first-progress-row: <...s>
- total wall time: <...s>

### Downloader / Queue
- Submitted to queue total: <N>
- Top endpoints:
  - <path>: <count>
  - ...

### Observed download_status
- <asset> <data_type>/<timespan>: <current>/<total>, queue_position=<...> (if present)

### Notes
- <anything surprising>
```

