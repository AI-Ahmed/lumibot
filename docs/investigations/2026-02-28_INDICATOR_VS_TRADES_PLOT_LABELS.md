# Indicator vs Trades Plot Label Quality Investigation

**Title:** Label/Tooltip Quality Gap: plot_indicators() vs plot_returns() for Trade-Derived Markers

**One-line description:** Root cause analysis and recommendations to align indicator plot tooltips with trades plot when markers are derived from trades (TradesDataStrategy pattern).

**Last Updated:** 2026-02-28

**Status:** Fix implemented — trade-derived markers now use detail_text as-is (no redundant "Value:" prefix); header labels aligned to "Bought"/"Sold" in `_row_to_marker`. `*_indicators.html` is disabled by default (`LUMIBOT_WRITE_INDICATORS_HTML` default=False) since it duplicates the trades plot; CSV/parquet are still emitted for downstream.

**Audience:** AI agents, contributors working on LumiBot plotting/UX

---

## Overview

For strategies that do **not** call `add_chart_marker` / `add_line` (e.g., TradesDataStrategy), `plot_indicators()` falls back to `_trades_to_chart_markers()` to derive buy/sell markers from the trades DataFrame. Both `plot_indicators()` and `plot_returns()` display the same trade events, but with different tooltip quality. Users report the trades plot as "more professional and clear" than the indicator plot.

This document identifies root causes, compares formatting paths, and recommends concrete changes to align quality when markers are trade-derived.

---

## 1. Data Flow Summary

### Indicator Plot (plot_indicators) — Trade-Derived Path

| Step | Location | What Happens |
|------|----------|--------------|
| 1 | `_trades_to_chart_markers()` (lines 601–672) | Merges trades with strategy returns to get `portfolio_value` at trade time. Calls `_build_trade_marker_tooltip(row)` for each row → `detail_text`. |
| 2 | `plot_indicators()` (lines 701–707) | Sets `used_trade_derived_markers = True` when using derived markers. |
| 3 | `generate_marker_plotly_text()` (lines 857–858) | **Overwrites** `detail_text` with `_format_indicator_plotly_text(value, detail_text)`. |
| 4 | `_format_indicator_plotly_text()` (lines 49–75) | Returns `"Value: " + str(value) + "<br>" + detail_str`. No formatting for `value` (raw float). |
| 5 | Hovertemplate (line 911) | `"{marker_name}<br>%{text}<br>%{x|...}"` with `marker_name` = "Buy" or "Sell". |

**Resulting tooltip example:**
```
Buy
Value: 100092.30999999998
fill
58.00 AAPL
Price: 170.28
Order Type: market
Amount Transacted: 9,876.24
Trade Cost: 0.00
Slippage: 0.00
```

### Trades Plot (plot_returns) — Trade Markers

| Step | Location | What Happens |
|------|----------|--------------|
| 1 | `df_final` | Merged strategy + trades by time. Each row has `side`, `status`, `filled_quantity`, `price`, etc. |
| 2 | `generate_buysell_plotly_text()` (lines 1392–1425) | Calls `_build_trade_marker_tooltip(row)` **directly**; no "Value:" prefix. |
| 3 | Hovertemplate (lines 1451, 1493) | `"Bought<br>%{text}<br>%{x|...}"` or `"Sold<br>%{text}<br>%{x|...}"`. |

**Resulting tooltip example:**
```
Bought
fill
58.00 AAPL
Price: 170.28
Order Type: market
Amount Transacted: 9,876.24
Trade Cost: 0.00
Slippage: 0.00
```

---

## 2. Root Causes of Quality Gap

### 2.1 Redundant "Value:" Prefix

- **Location:** `indicators.py`, lines 49–75, 857–861.
- **Cause:** `_format_indicator_plotly_text()` always prepends `"Value: " + str(value)` for all markers. For **custom** indicators (e.g., RSI, add_chart_marker with `value=50`), this is meaningful. For **trade-derived** markers, `value` is `portfolio_value`, which:
  1. Is already visible on the y-axis at the marker point.
  2. Pushes trade-specific information below the fold.
  3. Adds no actionable information for trade analysis.

- **Evidence:** Artifact `logs/TradesDataStrategy_2026-02-28_22-13_WhQFa2_indicators.csv` shows every `detail_text` starting with `"Value: ...<br>"`.

### 2.2 Raw Float Display (No Formatting)

- **Location:** `_format_indicator_plotly_text()` line 57: `base = "Value: " + str(value)`.
- **Cause:** Uses `str(value)` with no formatting. Portfolio values like `100092.30999999998`, `99922.63499999997`, `100321.18789999995` are displayed raw.
- **Evidence:** CSV rows 6–7: `Value: 100092.30999999998`, row 16: `Value: 99922.63499999997`.
- **Contrast:** `_build_trade_marker_tooltip()` uses `Decimal.quantize()` and `__format__(",f")` for amounts, producing `9,876.24`, `170.2800`, etc.

### 2.3 One-Size-Fits-All Formatting

- **Location:** `plot_indicators()` lines 857–861. `generate_marker_plotly_text` applies `_format_indicator_plotly_text` to **all** markers, including trade-derived ones.
- **Cause:** No distinction between:
  - **Custom indicators:** `value` is semantic (e.g., RSI 70, price level) and "Value:" is useful.
  - **Trade-derived markers:** `value` is portfolio_value; tooltip should prioritize trade details.

### 2.4 Minor: Header Label Difference (Buy/Sell vs Bought/Sold)

- **Indicator plot:** `marker_name` = "Buy" or "Sell" (from `_row_to_marker()` in `_trades_to_chart_markers`).
- **Trades plot:** "Bought" or "Sold" in hovertemplate.
- **Impact:** "Bought"/"Sold" (past tense) better reflects completed fills. Minor UX preference.

---

## 3. Comparison: _format_indicator_plotly_text vs _build_trade_marker_tooltip

| Aspect | _format_indicator_plotly_text | _build_trade_marker_tooltip |
|--------|-------------------------------|-----------------------------|
| **Purpose** | Generic indicator/lines hover | Trade-specific marker tooltip |
| **Value display** | `str(value)` — raw float | N/A (no portfolio value) |
| **Numeric formatting** | None | Decimal quantize + ",f" for amounts |
| **Structure** | `"Value: X<br>" + detail_str` | `status<br>qty symbol<br>Price: ...<br>Order Type: ...<br>Amount Transacted: ...<br>Trade Cost: ...<br>Slippage: ...<br>` |
| **When used** | All markers/lines in plot_indicators | Trades plot markers; also feeds detail_text in _trades_to_chart_markers |

For trade-derived markers, `_build_trade_marker_tooltip` output is already complete and well-formatted. `_format_indicator_plotly_text` adds a low-value, unformatted prefix and degrades UX.

---

## 4. Recommendations (with File:Line References)

### Recommendation 1 [Primary] — Bypass _format_indicator_plotly_text for Trade-Derived Markers

**Objective:** When markers come from `_trades_to_chart_markers()`, use `detail_text` as-is (i.e., `_build_trade_marker_tooltip` output) instead of wrapping it with "Value: ...".

**Implementation:**

1. **`indicators.py` lines 856–861** — Make `generate_marker_plotly_text` conditional on `used_trade_derived_markers`:

   ```python
   def generate_marker_plotly_text(row):
       if used_trade_derived_markers and row.get("detail_text"):
           return row.get("detail_text")  # trade tooltip already complete
       return _format_indicator_plotly_text(row.get("value"), row.get("detail_text"))
   ```

2. **Scope:** `used_trade_derived_markers` is already in scope at this point (set at line 706).

**Effort:** Low  
**Risk:** Low — only affects trade-derived path; custom markers unchanged  
**Impact:** Removes redundant "Value:" and raw floats for trade-derived markers

---

### Recommendation 2 [Optional] — Align Header: "Bought"/"Sold" for Trade-Derived Markers

**Objective:** Match the trades plot’s header style for consistency.

**Implementation:**

1. **`indicators.py` line 911** — For trade-derived markers, use "Bought"/"Sold" instead of "Buy"/"Sell":
   - Either: add `plot_df["marker_header"]` when `used_trade_derived_markers` is True, mapping "Buy"→"Bought", "Sell"→"Sold".
   - Or: pass `used_trade_derived_markers` and adjust `marker_name` in the hovertemplate for trade-derived groups only.

2. **Alternative:** Change `_row_to_marker()` in `_trades_to_chart_markers()` (lines 648–653) to return "Bought"/"Sold" for the `name` field. This would require confirming no other consumers depend on "Buy"/"Sell".

**Effort:** Low  
**Risk:** Low — cosmetic  
**Impact:** Consistent verb tense across both plots

---

### Recommendation 3 [Defensive] — Format Portfolio Value When Retained

**Objective:** If "Value:" is ever kept for trade-derived markers (e.g., for debugging), format it like other monetary values.

**Implementation:**

- In `_format_indicator_plotly_text()` (or a trade-specific helper), use:

  ```python
  value_str = f"{float(value):,.2f}" if isinstance(value, (int, float)) else str(value)
  ```

  instead of `str(value)` for the value portion.

**Effort:** Low  
**Risk:** Low  
**Impact:** Only relevant if Recommendation 1 is not applied or is partially reverted

---

### Recommendation 4 [Future] — Explicit Marker Origin Flag

**Objective:** Make trade-derived markers explicit so future changes don’t regress behavior.

**Implementation:**

1. **`_trades_to_chart_markers()`** (lines 670–669) — Add column:

   ```python
   "is_trade_derived": True
   ```

2. **`generate_marker_plotly_text()`** — Check `row.get("is_trade_derived")` instead of relying on `used_trade_derived_markers` (which is function-level). This supports mixed marker sources if that pattern is ever introduced.

**Effort:** Low  
**Risk:** Low  
**Impact:** Clearer intent, easier maintenance

---

## 5. Validation Plan

1. Run TradesDataStrategy backtest; inspect `*_indicators.csv` — `detail_text` should no longer start with `"Value: ..."` for trade-derived rows.
2. Open indicator plot HTML; verify tooltips match trades plot (trade-focused, no raw floats).
3. Run a strategy that uses `add_chart_marker` with custom `value`; confirm "Value: X" still appears for those markers.
4. Compare indicator and trades plots side-by-side; confirm consistent look and content for trade markers.

---

## 6. References

| Reference | Location |
|-----------|----------|
| `_format_indicator_plotly_text` | `lumibot/tools/indicators.py:49-75` |
| `_build_trade_marker_tooltip` | `lumibot/tools/indicators.py:77-217` |
| `_trades_to_chart_markers` | `lumibot/tools/indicators.py:601-672` |
| Marker plotting block | `lumibot/tools/indicators.py:856-916` |
| `generate_buysell_plotly_text` (trades plot) | `lumibot/tools/indicators.py:1392-1425` |
| Artifact: indicator markers CSV | `logs/TradesDataStrategy_2026-02-28_22-13_WhQFa2_indicators.csv` |

---

## 7. Confidence

**High.** The behavior is fully traced through the codebase and artifact data. Recommendation 1 directly targets the reported quality gap with minimal scope and risk.
