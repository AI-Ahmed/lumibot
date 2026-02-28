import inspect

import pandas as pd


def test_strategy_backtest_ui_defaults_are_none() -> None:
    """Guardrail: Strategy.backtest must honor existing SHOW_* env vars by default.

    `_Strategy.run_backtest()` already resolves `show_plot/show_indicators/show_tearsheet` from
    `SHOW_PLOT/SHOW_INDICATORS/SHOW_TEARSHEET` when they are None. This is what acceptance backtests
    rely on to prevent UI popups.
    """
    from lumibot.strategies.strategy import Strategy

    sig = inspect.signature(Strategy.backtest)
    assert sig.parameters["show_plot"].default is None
    assert sig.parameters["show_tearsheet"].default is None
    assert sig.parameters["show_indicators"].default is None


def test_create_tearsheet_does_not_open_browser_when_show_tearsheet_false(monkeypatch, tmp_path) -> None:
    """Guardrail: callers can disable tearsheet browser auto-open via `show_tearsheet=False`."""
    from lumibot.tools import indicators as indicators_mod

    def _should_not_open(_: str) -> None:
        raise AssertionError("webbrowser.open() must not be called when show_tearsheet=False")

    monkeypatch.setattr(indicators_mod.webbrowser, "open", _should_not_open)

    def _fake_qs_html(*args, **kwargs):
        output = kwargs.get("output")
        if output:
            with open(output, "w", encoding="utf-8") as f:
                f.write("<html><body>stub</body></html>")
        return {"ok": True}

    monkeypatch.setattr(indicators_mod.qs.reports, "html", _fake_qs_html)

    idx = pd.to_datetime(["2025-01-02", "2025-01-03", "2025-01-06"])
    strategy_df = pd.DataFrame({"portfolio_value": [100.0, 101.0, 99.0]}, index=idx)
    benchmark_df = pd.DataFrame({"symbol_cumprod": [1.0, 1.01, 1.02]}, index=idx)

    indicators_mod.create_tearsheet(
        strategy_df=strategy_df,
        strat_name="TestStrategy",
        tearsheet_file=str(tmp_path / "tearsheet.html"),
        benchmark_df=benchmark_df,
        benchmark_asset="SPY",
        show_tearsheet=False,
        save_tearsheet=True,
        risk_free_rate=0.0,
    )
