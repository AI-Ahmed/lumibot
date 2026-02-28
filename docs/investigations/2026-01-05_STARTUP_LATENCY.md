# Backtest Startup Latency (Submit → First Progress) — 2026-01-05
> Measured breakdown of “nothing happens for ~20–30s” in BotSpot backtests and the fix to reduce it.

**Last Updated:** 2026-01-06  
**Status:** Active  
**Audience:** Developers + AI Agents  

---

## What “startup latency” means here

For BotSpot users, the backtest “starts” when the UI receives the first `backtest_progress` row (from BotManager).

BotManager computes `backtest_progress` by tailing `logs/progress.csv` inside the backtest container and uploading the most recent row to DynamoDB.

So the practical metric is:

- **submit time** (API accepted) → **first progress row timestamp**

---

## Measurements (production, warm S3)

All runs below used warm S3 versions (no cold-cache hydration) and were launched via:

- `scripts/run_backtest_prod.py`

### NVDA (2025-01-20 → 2025-02-10)

- Bot ID: `nvda_prod_parity_warm-20250120-20250210-fku3p0ge`
- Submit: `2026-01-05T13:13:35.509300Z`
- First progress row:
  - `timestamp=2026-01-05T13:13:56.919559`
  - `elapsed=0:00:07`
- **Submit → first progress: ~21.4s**

ECS task timing (from `aws ecs describe-tasks`, task `ef1539da...`):
- `createdAt=2026-01-05T13:13:36.843Z`
- `startedAt=2026-01-05T13:13:37.839Z`
- `pullStoppedAt=2026-01-05T13:13:38.298Z`

Bootstrap timing (from `/api/logs` CloudWatch events):
- `Executing user code` at `2026-01-05T13:13:44.289Z`
- **User code → first progress: ~12.6s**

### SPX Copy2 (2025-01-21 → 2025-01-24)

- Bot ID: `spx_copy2_prod_parity_warm-20250121-20250124-5y3rzoih`
- Submit: `2026-01-05T13:14:34.203320Z`
- First progress row:
  - `timestamp=2026-01-05T13:15:05.518920`
  - `elapsed=0:00:17`
- **Submit → first progress: ~31.3s**

ECS task timing (task `b561b2a1...`):
- `createdAt=2026-01-05T13:14:35.418Z`
- `pullStoppedAt=2026-01-05T13:14:36.547Z`

Bootstrap timing:
- `Executing user code` at `2026-01-05T13:14:42.788Z`
- **User code → first progress: ~22.7s**

### SPX Copy3 (2025-01-21 → 2025-01-24)

- Bot ID: `spx_copy3_prod_parity_warm-20250121-20250124-msb86jww`
- Submit: `2026-01-05T13:15:32.516020Z`
- First progress row:
  - `timestamp=2026-01-05T13:16:06.470323`
  - `elapsed=0:00:20`
- **Submit → first progress: ~34.0s**

ECS task timing (task `17103c8a...`):
- `createdAt=2026-01-05T13:15:33.355Z`
- `pullStoppedAt=2026-01-05T13:15:34.881Z`

Bootstrap timing:
- `Executing user code` at `2026-01-05T13:15:40.945Z`
- **User code → first progress: ~25.5s**

### Additional measurements (cold node pulls + provisioning stalls)

These runs were launched via `scripts/run_backtest_prod.py` and illustrate that startup latency is not a single bucket.

#### NVDA (2025-01-11 → 2025-12-31): cold node pull dominates (first run after deploy)

- Bot ID: `nvda_prod_2025-20250111-20251231-pg4hd3f2`
- Submit: `2026-01-06T06:39:56.286085Z`

ECS task timing (from BotManager status payload):
- `createdAt=2026-01-06T06:39:57.167Z`
- `pullStartedAt=2026-01-06T06:39:58.171Z`
- `pullStoppedAt=2026-01-06T06:40:23.768Z` (**~25.6s pull**)
- `startedAt=2026-01-06T06:40:25.399Z`

Interpretation:
- This run landed on a node without the backtest image cached, so **image pull** was the dominant startup component.

#### NVDA (2025-12-30 → 2025-12-31): warm node pull is near-zero

- Bot ID: `startup_probe_nvda-20251230-20251231-wq3awmyx`
- Submit: `2026-01-06T07:05:48.199480Z`
- First progress row:
  - `timestamp=2026-01-06T07:06:08.446750`
- **Submit → first progress: ~20.2s**

ECS task timing (from BotManager status payload):
- `createdAt=2026-01-06T07:05:49.591Z`
- `pullStartedAt=2026-01-06T07:05:51.937Z`
- `pullStoppedAt=2026-01-06T07:05:51.989Z` (**~0.05s pull**)
- `startedAt=2026-01-06T07:05:51.735Z`

Interpretation:
- The image was already cached (or `image-pull-behavior=prefer-cached` was effective), so the remaining startup delay is mostly LumiBot boot + progress initialization.

#### NVDA (2013-01-10 → 2025-12-30): provisioning dominates

- Bot ID: `nvda_full_prod-20130110-20251230-zqxyqj49`

ECS task timing (from BotManager status payload):
- `createdAt=2026-01-05T16:15:05.955Z`
- `pullStartedAt=2026-01-05T16:23:16.225Z`
- `pullStoppedAt=2026-01-05T16:23:16.281Z` (**~0.06s pull**)
- `startedAt=2026-01-05T16:23:16.332Z`

Interpretation:
- The image was cached (near-zero pull), but the task spent ~8 minutes in **PROVISIONING** (capacity/scheduling).

---

## Conclusion (root cause bucket)

Startup latency is **multi-modal**:

- **Warm node:** ECS startup can be fast (1–2s), and then LumiBot boot/progress-init dominates.
- **Cold node:** ECS **image pull** can dominate (~30s).
- **Capacity constrained:** ECS **PROVISIONING** can dominate (minutes).

This is why fast backtests “feel slow”: the simulation is running, but **the UI has no progress row yet**.

---

## Fix (LumiBot): write an initial `progress.csv` row immediately

Change:
- In `lumibot/data_sources/data_source_backtesting.py`, when `LOG_BACKTEST_PROGRESS_TO_FILE` is truthy:
  - write an initial `logs/progress.csv` row immediately on datasource initialization
  - keep the existing download heartbeat behavior unchanged

Why it helps:
- BotManager will upload a progress record almost immediately (next 1s poll), cutting time-to-first-progress for short runs.

Follow-ups (optional):
- Infra (ECS) improvements are required to reduce cold-node pull/provisioning:
  - reduce backtest image size and layer churn (faster pulls),
  - add capacity headroom or a capacity provider (fewer provisioning stalls),
  - set `image-pull-behavior` to prefer cached images for the backtest cluster (BotManager `terraform/ecs.tf`; reduces ~30s pulls on warm nodes),
  - consider warming nodes/agents (keep the image cached).
- If we want even earlier progress (before datasource init), add a “progress bootstrap row” in:
  - strategy executor startup, and/or
  - BotSpot Node’s injected log patch (runs before importing LumiBot)
