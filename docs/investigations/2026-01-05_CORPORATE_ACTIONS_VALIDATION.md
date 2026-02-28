# Corporate Actions Validation (NVDA splits, TQQQ dividends) — 2026-01-05

This investigation exists because we observed **options strike normalization** breaking an NVDA options strategy
(no valid strikes found), and we also have an open question about **ThetaData corporate action quality**
(phantom dividends / duplicates).

**Goal:** validate what is *real* vs *vendor noise* using primary/reputable sources, and avoid hardcoding
"phantom corporate actions" in LumiBot.

---

## 1) NVDA stock splits (relevant to 2013 → 2025 backtests)

### Finding
NVDA **did** have a **10-for-1** stock split with split-adjusted trading starting **2024-06-10**.

This means a ThetaData split event with:
- `event_date = 2024-06-10`
- `ratio = 10.0`

...is **not** "phantom" and should not be filtered/denied.

### Evidence (primary)
- SEC Form 8‑K / Current Report (filed 2024‑06‑07) documenting the 10‑for‑1 split and split‑adjusted trading date:
  - https://www.sec.gov/Archives/edgar/data/1045810/000104581024000144/nvda-20240607.htm
- NVIDIA Investor Relations / Press Release (Q1 FY2025 results; includes split details and dates):
  - https://investor.nvidia.com/news/press-release-details/2024/NVIDIA-Announces-Financial-Results-for-First-Quarter-Fiscal-2025/default.aspx

### Key dates (as stated in filings/IR)
- Record date: **2024‑06‑06** (close)
- Distribution: **2024‑06‑07** (after close)
- Split‑adjusted trading begins: **2024‑06‑10** (market open)

### Other NVDA split in range
NVDA also had a **4‑for‑1** split effective **2021‑07‑20** (split‑adjusted trading on/after that date).
- NVIDIA IR press release (May 2021; 4‑for‑1 split, pending approval; contains the effective details):
  - https://nvidianews.nvidia.com/news/nvidia-announces-four-for-one-stock-split-pending-stockholder-approval-at-annual-meeting-set-for-june-3

---

## 2) TQQQ dividend/distribution discrepancies (ThetaData vs Nasdaq vs Yahoo)

### Context
ThetaData support reported two conflicting things:
1) "Known issues with the feed; no flags; wait for provider switch" (Bill)
2) "Dividends are on the Nasdaq website; yfinance is wrong" (Sam)

We need something machine-checkable.

### What Nasdaq shows (machine-checkable API)
Nasdaq's site uses a JSON endpoint. For TQQQ:

- URL:
  - https://api.nasdaq.com/api/quote/tqqq/dividends?assetclass=etf

The API currently returns **21** rows total and does **not** include:
- **2014‑09‑18** (the alleged $0.41 "phantom")
- **2015‑07‑02** (the alleged $1.22 "phantom")

It **does** include the tiny year-end rows:
- **2020‑12‑23** amount `$0.0002`
- **2021‑12‑23** amount `$0.000119`

and a single row for:
- **2019‑03‑20** amount `$0.020268` (not duplicated)

This strongly suggests:
- ThetaData may have **extra dividend rows** (possibly phantom),
- and ThetaData may return **duplicate rows** for the same ex-date in its raw feed.

### Why Yahoo/yfinance may differ
Plausible (not proven) reasons Yahoo/yfinance can differ from Nasdaq/API/vendor feeds:
- Some vendors drop **micro-distributions** below a threshold or merge them into neighboring rows.
- Vendor pipelines may de-duplicate/normalize differently (especially if upstream has duplicates).
- ETFs can have distributions that are reclassified; vendors may update history differently over time.

### What we do in LumiBot (principle)
We should not hardcode symbol/date deny-lists for corporate actions.
Instead:
- normalize and de-duplicate deterministically when the feed provides duplicates
- keep corporate action adjustment logic **consistent across timeframes** (minute vs day) so options strikes and
  underlying prices live in the same "split-adjusted space"
- document any heuristics we apply (and keep them conservative)

