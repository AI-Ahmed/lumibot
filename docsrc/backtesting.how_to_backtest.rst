How To Backtest
===================================

Backtesting is a vital step in validating your trading strategies using historical data. With LumiBot, you can backtest **US equity and ETF** strategies using **Yahoo Finance** (default), **Alpaca**, **Interactive Brokers (REST)**, or your own **Pandas/CSV** datasets. This guide walks through setup, data-source selection, and the artifacts LumiBot generates during a backtest.

.. note::

   **Why Backtest?**

   Backtesting lets you see how a strategy would have behaved historically before deploying it live.

Installing LumiBot
-----------------------------------

Install or upgrade LumiBot:

.. code-block:: bash

    pip install lumibot --upgrade

Use any Python IDE (VS Code, PyCharm, etc.) to author strategies.

Choosing a Data Source
-----------------------------------

This fork focuses on **equity backtesting**. Supported providers:

**1. Yahoo Finance (Default)**

- Free daily OHLCV for US stocks and ETFs.
- Best for longer-horizon daily strategies.
- Set ``BACKTESTING_DATA_SOURCE=yahoo`` or pass ``YahooDataBacktesting`` in code.

See :ref:`Yahoo Backtesting <backtesting.yahoo>`.

**2. Alpaca**

- Intraday and daily US equity data (requires Alpaca API credentials).
- Useful when you already trade or paper-trade with Alpaca.

**3. Interactive Brokers (REST)**

- IBKR Client Portal REST backtesting via the LumiBot data downloader.
- Set ``BACKTESTING_DATA_SOURCE=ibkr`` (aliases: ``interactivebrokersrest``).

See :ref:`Interactive Brokers Backtesting <backtesting.ibkr>`.

**4. Pandas (CSV or Custom Data)**

- Bring your own minute/daily equity bars in CSV or DataFrame form.
- Advanced setup; see :ref:`Pandas Backtesting <backtesting.pandas>`.

Running a Backtest with Yahoo
-----------------------------------

``run_backtest`` needs a datasource class (or ``BACKTESTING_DATA_SOURCE``), a date range, and optional parameters:

.. code-block:: python

    from datetime import datetime
    from lumibot.backtesting import YahooDataBacktesting
    from lumibot.strategies import Strategy

    class MyStrategy(Strategy):
        parameters = {"symbol": "SPY"}

        def initialize(self):
            self.sleeptime = "1D"

        def on_trading_iteration(self):
            if self.first_iteration:
                symbol = self.parameters["symbol"]
                price = self.get_last_price(symbol)
                qty = self.portfolio_value / price
                order = self.create_order(symbol, quantity=qty, side="buy")
                self.submit_order(order)

    if __name__ == "__main__":
        result = MyStrategy.run_backtest(
            YahooDataBacktesting,
            datetime(2020, 1, 1),
            datetime(2024, 12, 31),
            benchmark_asset="SPY",
        )

Optional: Environment Variables for Backtest Configuration
-----------------------------------------------------------------

Instead of hard-coding dates or datasources, set:

- ``IS_BACKTESTING``
- ``BACKTESTING_START``
- ``BACKTESTING_END``
- ``BACKTESTING_DATA_SOURCE``

.. list-table::
   :header-rows: 1
   :widths: 20 60 20

   * - **Variable**
     - **Description**
     - **Example**
   * - IS_BACKTESTING
     - ``True`` for backtest mode; ``False`` for live.
     - True
   * - BACKTESTING_START
     - Start date ``YYYY-MM-DD``.
     - 2020-01-01
   * - BACKTESTING_END
     - End date ``YYYY-MM-DD``.
     - 2024-12-31
   * - BACKTESTING_DATA_SOURCE
     - Overrides code datasource. Valid values: ``yahoo``, ``alpaca``, ``ibkr`` (default: ``yahoo``). Use ``none`` to rely on code.
     - yahoo

Example relying on environment variables:

.. code-block:: python

    from lumibot.strategies import Strategy

    class MyStrategy(Strategy):
        def initialize(self):
            self.sleeptime = "1D"

        def on_trading_iteration(self):
            if self.first_iteration:
                order = self.create_order("SPY", quantity=10, side="buy")
                self.submit_order(order)

    if __name__ == "__main__":
        # BACKTESTING_DATA_SOURCE=yahoo
        # BACKTESTING_START=2020-01-01
        # BACKTESTING_END=2024-12-31
        MyStrategy.run_backtest(None)

See :ref:`Environment Variables <environment_variables>` for the full list.

Files Generated from Backtesting
===================================

LumiBot writes tearsheets, trade logs, and indicator artifacts under your strategy log directory. See:

- :ref:`Tearsheet HTML <backtesting.tearsheet_html>`
- :ref:`Trades Files <backtesting.trades_files>`
- :ref:`Indicators Files <backtesting.indicators_files>`

Conclusion
-----------------------------------

Pick **Yahoo** for daily equity research, **Alpaca** or **IBKR** when you need broker-aligned intraday data, and **Pandas** when you supply custom CSVs. Use ``BACKTESTING_DATA_SOURCE`` to switch providers without changing strategy code.
