Backtesting
************************

Lumibot has multiple modes for backtesting US equities and ETFs:

1. **Yahoo Backtesting:** Daily stock/ETF backtesting with data from Yahoo Finance (default).
2. **Alpaca Backtesting:** Intraday or daily US equity data via Alpaca.
3. **Pandas Backtesting:** Intra-day and inter-day testing using CSV or DataFrame data you supply.
4. **Interactive Brokers (REST) Backtesting:** Backtesting with IBKR Client Portal Gateway (via the LumiBot Data Downloader).

For most daily equity strategies, start with **Yahoo**. Use **Alpaca** or **IBKR** when you need broker-aligned intraday bars. **Pandas** is an advanced option when you already have custom datasets.

Files Generated from Backtesting
================================

When you run a backtest, several important files are generated, each prefixed by the strategy name and the date. These files provide detailed insights into the performance and behavior of the strategy.

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   backtesting.how_to_backtest
   backtesting.backtesting_function
   backtesting.performance
   backtesting.yahoo
   backtesting.pandas
   backtesting.ibkr
   backtesting.tearsheet_html
   backtesting.trades_files
   backtesting.indicators_files
   backtesting.logs_csv
