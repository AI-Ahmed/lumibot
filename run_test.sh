#!/bin/bash
# Run the HFT trades backtesting test
# This uses the venv Python directly, so no reinstall needed
source .venv/bin/activate
python -m tests.backtest.st_alpaca_hft_trades_backtesting "$@"
