# Upstream Cherry-Pick TODO — Tier 1 & Tier 2

One-line description: Actionable checklist and commit manifest for porting equity-safe upstream `Lumiwealth/lumibot` patches into this fork.

Last Updated: 2026-07-08  
Status: **TIER 1 DONE** (with fork adaptations) — 0/17 Tier 2 applied  
Audience: Contributors syncing from upstream  

## Overview

Audit on 2026-07-08 found the fork **735 commits behind** `upstream/dev` (`b618afa6`, v4.5.74) at merge-base `d5945bdc`. Of 604 unmerged patches (`git cherry`):

| Bucket | Count | Action |
|--------|-------|--------|
| Tier 1 ACCEPT | 15 | Cherry-pick as-is (oldest → newest) |
| Tier 2 REVIEW | 17 | Port safe hunks; drop forbidden files |
| REJECT | 129 | Skip (forbidden brokers/assets) |
| Other REVIEW | ~388 | Classify per batch |

**Tool:** `scripts/upstream_cherry_pick_todo.sh` — status, list, cherry-pick helpers.

**Rules:** `BROKER_DATA_SOURCE_INVENTORY.md`, `docs/UPSTREAM_MERGE_PROTOCOL.md`, `.cursor/rules/equity-only-fork.mdc`

### Preflight (every session)

```bash
git fetch upstream dev
scripts/upstream_cherry_pick_todo.sh status
git status --porcelain=v1
```

### Post-batch validation

```bash
uv run pytest tests/test_alpaca.py tests/backtest/test_yahoo.py tests/test_interactive_brokers.py \
  tests/test_backtesting_broker.py tests/test_data_entity.py -q -m "not apitest and not downloader"
graphify update .
```

---

## Tier 1 — ACCEPT (cherry-pick as-is)

Apply in **chronological order** (oldest first). All files are equity-safe.

| # | SHA | Subject | Files | Status |
|---|-----|---------|-------|--------|
| 1 | `82aa9bea` | fix: scope daily last-price optimization by datasource | `strategy.py` | ☑ (IBKR-only gate) |
| 2 | `85f5b1db` | fix: sort imports in `_strategy.py` for ruff CI compliance | `_strategy.py` | ⊘ N/A (no AgentManager in fork) |
| 3 | `f61c19a5` | fix: add `replay_cache.py` (was excluded by `*cache*` gitignore) | `components/agents/replay_cache.py` | ☑ |
| 4 | `f4ce122f` | fix: ruff import sort order for agents import in `_strategy.py` | `_strategy.py` | ⊘ N/A |
| 5 | `0cf578fd` | Fix CI lint regression on hotfix branch | `_strategy.py` | ⊘ N/A |
| 6 | `e3cb48fe` | fix(order): emit identifier and avg_fill_price in `to_dict()` | `entities/order.py` | ☑ |
| 7 | `847fcbd9` | fix(strategy): warn loudly on BACKTESTING_START/END env override | `strategy.py` | ☑ |
| 8 | `6375ad5f` | fix(ibkr): coerce bare-string asset to Asset in `get_price_data` | `tools/ibkr_helper.py` | ☑ |
| 9 | `37469128` | feat(agents): suppress LiteLLM cosmetic provider-lookup banner | `components/agents/runtime.py` | ☑ |
| 10 | `2917361e` | resolve subscriber | `brokers/broker.py` | ☑ |
| 11 | `eeb7eb54` | fix: support asyncio timeout errors on py310 | `components/agents/runtime.py` | ☑ |
| 12 | `8d67a579` | Tighten DeepSeek context pruning | `components/agents/runtime.py` | ☑ |
| 13 | `2e37023c` | Collapse older DeepSeek tool history | `components/agents/runtime.py` | ☑ |
| 14 | `8c6d6c08` | Remove unused scheduled timing adapter | `strategy_executor.py` | ⊘ N/A (method absent in fork) |
| 15 | `588bb03c` | perf: reduce lazy class proxy overhead | `_lazy_imports.py`, `_strategy.py` | ☑ partial (`_lazy_imports.py` only; fork uses direct imports) |

### Tier 1 blockers

- **`588bb03c`** requires `lumibot/_lazy_imports.py`, which upstream has but this fork does not. Cherry-pick may need to add the file from upstream first (`git show upstream/dev:lumibot/_lazy_imports.py`).
- Run Tier 1 before Tier 2 — several Tier 2 commits depend on `replay_cache.py` (`f61c19a5`) and lazy-import infra.

### Tier 1 batch command

```bash
scripts/upstream_cherry_pick_todo.sh cherry-pick tier1
```

---

## Tier 2 — REVIEW (manual port, equity hunks only)

Port safe `lumibot/` hunks. **Drop** forbidden tests (crypto/options), upstream-only deps, and forbidden broker references.

### P0 — correctness & performance (do first)

| # | SHA | Subject | Port | Skip | Status |
|---|-----|---------|------|------|--------|
| 1 | `3c5cbb5c` | ibkr: keep paged history chunks when later page is empty | `tools/ibkr_helper.py` | `test_ibkr_crypto_daily_series.py` | ☐ |
| 2 | `721fab17` | fix: apply per-contract trading fees in backtesting | `backtesting_broker.py`, `trading_fee.py`, `test_tradingfee.py` | upstream CHANGELOG only | ☐ |
| 3 | `5a13897a` | Optimize safe backtesting hot paths | all `lumibot/` files | equity-safe tests only | ☐ |
| 4 | `7dc7697f` | fix: prevent backtesting broker fabricating fills from future bars | `backtesting_broker.py` | `setup.py` version hunks | ☐ |
| 5 | `009443c0` | fix(strategy): get_last_price look-ahead bias (Alpha Picks incident) | `strategy.py`, safety tests | — | ☐ |
| 6 | `fee632b1` | fix(strategy): forward-fill get_last_price when length=1 slice empty | `strategy.py`, safety tests | — | ☐ |
| 7 | `bc12c76b` | fix(ibkr): chunk-preserving empty mid-walk pagination | `tools/ibkr_helper.py` | crypto test file | ☐ |
| 8 | `fb9fc658` | perf: reduce pandas backtesting data overhead | `entities/data.py`, `test_data_entity.py` | — | ☐ |
| 9 | `9497a875` | perf: speed up pandas bars between dates | `entities/data.py`, `test_data_entity.py` | — | ☐ |
| 10 | `609d7e88` | perf: index orders during broker sync | `strategy_executor.py`, order-index test | — | ☐ |
| 11 | `76a58cb5` | Fix stats external flow base for duplicate timestamps | `_strategy.py`, stats regression test | `setup.py` version hunks | ☐ |
| 12 | `c5a4ffc8` | Fix stats base handling for missing portfolio values | `_strategy.py`, stats regression test | `setup.py` version hunks | ☐ |

### P1 — features & observability (after P0)

| # | SHA | Subject | Port | Skip | Status |
|---|-----|---------|------|------|--------|
| 13 | `8d7c3a11` | feat(strategy): BACKTESTING_START/END env override | `strategy.py`, env override test | — | ☐ |
| 14 | `c6459221` | Add BACKTESTING_PARAMETERS env var | `credentials.py`, `_strategy.py`, `docsrc/environment_variables.rst` | — | ☐ |
| 15 | `a9985330` | feat: wire metrics_json into tearsheet pipeline | `_strategy.py`, `tools/indicators.py` | — | ☐ |
| 16 | `73b874bf` | tearsheet: custom metrics hook + summary artifact | `_strategy.py`, `strategy.py`, `indicators.py`, `trader.py`, docsrc, tests | — | ☐ |
| 17 | `f52882a3` | Lazy-load import startup paths | all `lumibot/__init__.py` lazy exports | equity-safe tests only | ☐ |

### Tier 2 manual port workflow

```bash
# Inspect before porting
git show <sha> --stat
git show <sha> -- lumibot/path/to/file.py

# Option A: cherry-pick then revert forbidden files
git cherry-pick <sha>
git checkout HEAD -- tests/test_ibkr_crypto_daily_series.py   # example: drop crypto test

# Option B: checkout single file from upstream commit
git checkout <sha> -- lumibot/tools/ibkr_helper.py
```

---

## Progress tracker

| Milestone | Target | Done |
|-----------|--------|------|
| Tier 1 complete | 15/15 cherry-picks | 11 applied, 4 N/A/partial (fork layout) |
| Tier 2 P0 complete | 12/12 ports | 0/12 |
| Tier 2 P1 complete | 5/5 ports | 0/5 |
| Equity pytest subset green | 0 failures | 40 passed (subset) |
| Handoff status → DONE | all boxes checked | Tier 1 only |

---

## Cross-references

- [`2026-07-08_UPSTREAM_MERGE_EQUITY_FILTER.md`](2026-07-08_UPSTREAM_MERGE_EQUITY_FILTER.md) — full classification matrix
- [`2026-07-08_FINAL_CLEANUP_STATE.md`](2026-07-08_FINAL_CLEANUP_STATE.md) — equity-only deletion state
- [`../UPSTREAM_MERGE_PROTOCOL.md`](../UPSTREAM_MERGE_PROTOCOL.md) — merge runbook
- [`../../BROKER_DATA_SOURCE_INVENTORY.md`](../../BROKER_DATA_SOURCE_INVENTORY.md) — allowed integrations
