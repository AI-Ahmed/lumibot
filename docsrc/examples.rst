Code Examples
=============

This page contains practical code examples for common Lumibot tasks. These examples cover stocks, options, crypto, futures, and advanced features like the PerplexityHelper for AI-powered trading decisions.

Stocks
------

Get Historical Prices for a Stock
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Retrieve historical price data for a stock asset:

.. code-block:: python

    asset = Asset("SPY", asset_type=Asset.AssetType.STOCK)
    bars = self.get_historical_prices(asset, 2, "day")
    if bars is not None:
        df = bars.df  # DatetimeIndex (tz-aware) with open/high/low/close/volume/return columns
        last_ohlc = df.iloc[-1]  # Most recent bar
        self.log_message(f"Last price of SPY: {last_ohlc['close']}, open: {last_ohlc['open']}")

Get Multiple Assets' Historical Prices
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Retrieve historical prices for multiple assets at once:

.. code-block:: python

    assets = [
        Asset("AAPL", asset_type=Asset.AssetType.STOCK),
        Asset("MSFT", asset_type=Asset.AssetType.STOCK),
        Asset("GOOGL", asset_type=Asset.AssetType.STOCK),
    ]
    historical_prices = self.get_historical_prices_for_assets(assets, 30, "minute")
    for asset_obj, bars in historical_prices.items():
        if bars is None:
            self.log_message(f"No data available for {asset_obj}")
            continue
        df = bars.df
        last_bar = df.iloc[-1]

Get Quote with Bid/Ask Spread
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Get detailed market data including bid/ask spreads:

.. code-block:: python

    asset = Asset("SPY", asset_type=Asset.AssetType.STOCK)
    quote = self.get_quote(asset)
    if quote is not None:
        self.log_message(f"Bid: {quote.bid}, Ask: {quote.ask}, Mid: {quote.mid_price}")

Compare get_last_price vs Historical Bars
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

In live trading, ``get_last_price`` returns the broker's latest tick while historical bars may lag:

.. code-block:: python

    spy_stock = Asset("SPY", asset_type=Asset.AssetType.STOCK)
    latest_price = self.get_last_price(spy_stock)
    minute_bar = self.get_historical_prices(spy_stock, 1, "minute")

Calculate Moving Average with Up-to-Date Data
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Compute a moving average using both historical data and the latest available price:

.. code-block:: python

    asset = Asset("SPY", asset_type=Asset.AssetType.STOCK)
    df = self.get_historical_prices(asset, 20, "minute").df
    last = self.get_last_price(asset)
    sma20 = (df["close"].iloc[-19:].sum() + last) / 20
    self.log_message(f"SMA-20 (live): {sma20:.4f}")

Calculate Technical Indicators
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Calculate indicators using historical price data:

.. code-block:: python

    asset = Asset("SPY", asset_type=Asset.AssetType.STOCK)
    bars = self.get_historical_prices(asset, 100, "day")  # Get more data than needed for indicators

    if bars is not None:
        df = bars.df
        df["SMA_50"] = df["close"].rolling(window=50).mean()  # 50-day moving average
        last_ohlc = df.iloc[-1]

Handle Missing Data
~~~~~~~~~~~~~~~~~~~

Always handle the case when data is unavailable:

.. code-block:: python

    missing_asset = Asset("XYZ", asset_type=Asset.AssetType.STOCK)
    bars = self.get_historical_prices(missing_asset, 30, "minute")
    if bars is None:
        self.log_message(f"No data available for {missing_asset.symbol}")
    else:
        df = bars.df

Positions and Orders
--------------------

Get Position Details
~~~~~~~~~~~~~~~~~~~~

Retrieve information about a specific position:

.. code-block:: python

    position = self.get_position(Asset("AAPL", asset_type=Asset.AssetType.STOCK))

    if position is not None:
        self.log_message(f"Position for AAPL: {position.quantity} shares")
        quantity = position.quantity

Sell a Position
~~~~~~~~~~~~~~~

Liquidate an existing stock position:

.. code-block:: python

    position = self.get_position(Asset("AAPL", asset_type=Asset.AssetType.STOCK))
    if position is not None:
        asset = position.asset
        quantity = position.quantity
        order = self.create_order(asset, quantity, Order.OrderSide.SELL)
        self.submit_order(order)

Filter Out USD Cash Position
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When processing positions, filter out the USD cash position:

.. code-block:: python

    positions = self.get_positions()

    for position in positions:
        if position.asset.symbol == "USD" and position.asset.asset_type == Asset.AssetType.FOREX:
            continue
        # Process real positions here

Persistent Variables
--------------------

Using self.vars for State
~~~~~~~~~~~~~~~~~~~~~~~~~

Use ``self.vars`` for variables that persist between trading iterations:

.. code-block:: python

    def initialize(self):
        self.vars.my_variable = 10

    def on_trading_iteration(self):
        self.log_message(f"My variable is {self.vars.my_variable}")
        self.vars.my_variable += 1

Check if Variable Exists
~~~~~~~~~~~~~~~~~~~~~~~~

Safely check if a persistent variable exists before using it:

.. code-block:: python

    def on_trading_iteration(self):
        if not hasattr(self.vars, "filled_count"):
            self.vars.filled_count = 0

        self.log_message(f"The number of filled orders is {self.vars.filled_count}")

    def on_filled_order(self, position, order, price, quantity, multiplier):
        if not hasattr(self.vars, "filled_count"):
            self.vars.filled_count = 0

        self.vars.filled_count += 1

Dictionary-Style Access
~~~~~~~~~~~~~~~~~~~~~~~

Use dictionary-style access for signal counts:

.. code-block:: python

    self.vars.signal_counts = self.vars.get("signal_counts", {})
    self.vars.signal_counts.setdefault("SPY", 0)
    self.vars.signal_counts["SPY"] += 1

Logging and Debugging
---------------------

Log What Triggered a Decision
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Always log the reasoning behind trading decisions:

.. code-block:: python

    rsi = self.get_indicator("RSI", symbol="SPY", period=14)
    self.log_message(f"RSI gate check: value {rsi:.2f} vs sell > 70")
    if rsi > 70:
        self.log_message("RSI gate passed, preparing to sell SPY", color="yellow")
        # submit_order(...) here
    else:
        self.log_message("RSI gate failed, holding position")

Visualization with Markers and Lines
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use ``add_ohlc`` for price bars, ``add_line`` for continuous indicators, and ``add_marker`` for infrequent events:

.. code-block:: python

    asset = Asset("SPY", asset_type=Asset.AssetType.STOCK)
    bars = self.get_historical_prices(asset, 100, "day")

    if bars is not None:
        df = bars.df
        last_bar = df.iloc[-1]

        # Plot SPY price as OHLC candles (pass asset parameter for proper charting)
        self.add_ohlc(
            "SPY",
            open=last_bar["open"],
            high=last_bar["high"],
            low=last_bar["low"],
            close=last_bar["close"],
            detail_text="SPY Price",
            asset=asset,
        )

        df["SMA_50"] = df["close"].rolling(window=50).mean()

        # Add a line for the moving average (pass asset to overlay on price chart)
        self.add_line("SMA_50", df["SMA_50"].iloc[-1], color="blue", width=2,
                      detail_text="50-day SMA", asset=asset)

        # Markers only for significant events (not every iteration!)
        if last_bar["close"] > last_bar["SMA_50"]:
            self.add_marker("Buy Signal", last_bar["close"], color="green",
                          symbol="arrow-up", size=10, detail_text="Buy Signal", asset=asset)
        else:
            self.add_marker("Sell Signal", last_bar["close"], color="red",
                          symbol="arrow-down", size=10, detail_text="Sell Signal", asset=asset)

.. warning::

    Never add markers every iteration - this crashes the chart! Only use markers for significant events.
    Use ``add_line`` for continuous data like indicators, and ``add_ohlc`` for price bars.

Cryptocurrency
--------------

Get Crypto Historical Prices
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    asset = Asset("BTC", asset_type=Asset.AssetType.CRYPTO)
    bars = self.get_historical_prices(asset, 30, "minute")

    if bars is not None:
        df = bars.df

Get Crypto Last Price
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    asset = Asset("BTC", asset_type=Asset.AssetType.CRYPTO)
    last_price = self.get_last_price(asset)
    if last_price is not None:
        self.log_message(f"Last price of BTC in USD: {last_price}")

Create Crypto Order
~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    from lumibot.entities import Asset

    base = Asset("BTC", asset_type=Asset.AssetType.CRYPTO)
    quote = Asset("USD", asset_type=Asset.AssetType.CRYPTO)
    order = self.create_order(base, 0.05, "buy", quote=quote)
    self.submit_order(order)

Set 24/7 Market Hours for Crypto
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    def initialize(self):
        self.set_market("24/7")  # REQUIRED for crypto
        self.sleeptime = "15S"  # Run every 15 seconds

    def on_trading_iteration(self):
        dt = self.get_datetime()

        if dt.weekday() < 5:
            self.log_message(f"Current datetime: {dt}")
        else:
            self.log_message("It's the weekend!")

Crypto Futures (Bitunix)
------------------------

Trade Crypto Futures
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    asset = Asset("BTC", asset_type=Asset.AssetType.CRYPTO)
    last_price = self.get_last_price(asset)

    if last_price is not None:
        futures_asset = Asset("BTCUSDT", asset_type=Asset.AssetType.CRYPTO_FUTURE)
        order = self.create_order(futures_asset, 0.1, "buy", order_type="market")
        self.submit_order(order)
    else:
        self.log_message("BTC price unavailable", color="red")

Close Crypto Futures Position
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For crypto futures, you **must** use ``close_position()`` instead of ``submit_order()``:

.. code-block:: python

    positions = self.get_positions()

    for position in positions:
        if position.asset.asset_type == Asset.AssetType.CRYPTO_FUTURE:
            # CORRECT - use close_position for futures
            self.close_position(position.asset)
            self.log_message(f"Closed position for {position.asset.symbol}", color="green")

.. warning::

    Using ``submit_order()`` to "sell" a crypto future will open another position instead of closing!

Futures (DataBento)
-------------------

Trade Futures Contracts
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    futures_asset = Asset("MES", asset_type=Asset.AssetType.CONT_FUTURE)  # Micro E-mini S&P 500

    bars = self.get_historical_prices(futures_asset, 100, "minute")
    if bars and not bars.df.empty:
        df = bars.df
        df["sma_20"] = df["close"].rolling(window=20).mean()

        current_price = df["close"].iloc[-1]
        current_sma = df["sma_20"].iloc[-1]

        if current_price > current_sma:
            order = self.create_order(futures_asset, 5, "buy")
            self.submit_order(order)
        elif current_price < current_sma:
            order = self.create_order(futures_asset, 5, "sell")
            self.submit_order(order)

Futures Backtesting
~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    from lumibot.backtesting import DataBentoDataBacktesting
    from lumibot.entities import TradingFee

    class FuturesStrategy(Strategy):
        def initialize(self):
            self.asset = Asset("ES", asset_type=Asset.AssetType.CONT_FUTURE)

        def on_trading_iteration(self):
            # Your futures trading logic here
            pass

    if __name__ == "__main__":
        if IS_BACKTESTING:
            # Use flat fees for futures (typical: $0.50 per contract)
            trading_fee = TradingFee(flat_fee=0.50)

            results = FuturesStrategy.backtest(
                DataBentoDataBacktesting,
                benchmark_asset=Asset("SPY", Asset.AssetType.STOCK),
                buy_trading_fees=[trading_fee],
                sell_trading_fees=[trading_fee]
            )

Multiple Futures Contracts
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    def initialize(self):
        self.futures_assets = [
            Asset("ES", asset_type=Asset.AssetType.CONT_FUTURE),   # S&P 500
            Asset("MES", asset_type=Asset.AssetType.CONT_FUTURE),  # Micro S&P 500
            Asset("NQ", asset_type=Asset.AssetType.CONT_FUTURE),   # NASDAQ 100
            Asset("CL", asset_type=Asset.AssetType.CONT_FUTURE),   # Crude Oil
        ]

    def on_trading_iteration(self):
        for asset in self.futures_assets:
            bars = self.get_historical_prices(asset, 50, "day")
            if bars and not bars.df.empty:
                # Your trading logic for each contract
                pass

FOREX
-----

Create FOREX Order
~~~~~~~~~~~~~~~~~~

.. code-block:: python

    from lumibot.entities import Asset

    asset = Asset(
       symbol="CHF",
       currency="EUR",
       asset_type=Asset.AssetType.FOREX)
    order = self.create_order(asset, 100, "buy", limit_price=100.00)
    self.submit_order(order)

AI-Powered Trading (PerplexityHelper)
-------------------------------------

Trade Based on Earnings News
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use AI analysis to make trading decisions based on earnings reports:

.. code-block:: python

    news_query = "What are the latest earnings reports for major tech companies?"
    news_data = self.perplexity_helper.execute_financial_news_query(news_query)

    for item in news_data.get("items", []):
        sentiment = item.get("sentiment_score", 0)
        popularity = item.get("popularity_metric", 0)
        if sentiment >= 5 and popularity > 100:
            symbol = item.get("symbol")
            asset = Asset(symbol, asset_type=Asset.AssetType.STOCK)
            order = self.create_order(asset, 100, "buy")
            self.submit_order(order)
            self.log_message(f"Bought {symbol} based on positive earnings", color="green")
            break

Trade Volatile Stocks
~~~~~~~~~~~~~~~~~~~~~

Identify and trade volatile stocks:

.. code-block:: python

    general_query = "List stocks that are showing unusually high volatility."
    general_data = self.perplexity_helper.execute_general_query(general_query)

    if "symbols" in general_data and len(general_data["symbols"]) > 0:
        for symbol in general_data["symbols"]:
            asset = Asset(symbol, asset_type=Asset.AssetType.STOCK)
            current_price = self.get_last_price(asset)
            if current_price and current_price < 50:
                order = self.create_order(asset, 200, "buy")
                self.submit_order(order)
                self.log_message(f"Bought {symbol} (volatile, under $50)", color="green")
                break

Use Custom Schema for Analysis
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Query with a custom JSON schema for structured results:

.. code-block:: python

    custom_schema = {
        "query": "<string, echo the user's query>",
        "stocks": [
            {
                "symbol": "<string, ticker symbol>",
                "earnings_growth": "<float, earnings growth percentage>",
                "analyst_rating": "<float, average analyst rating from 1 to 5>",
                "price_target": "<float, consensus price target in USD>"
            }
        ],
        "summary": "<string, overall summary of findings>"
    }

    general_query = "List stocks with high earnings growth and strong analyst ratings."
    import os
    perplexity_model = os.getenv("PERPLEXITY_MODEL", "sonar-pro")
    custom_data = self.perplexity_helper.execute_general_query(
        general_query, custom_schema, model=perplexity_model
    )

    for stock in custom_data.get("stocks", []):
        earnings_growth = stock.get("earnings_growth", 0)
        analyst_rating = stock.get("analyst_rating", 0)
        if earnings_growth > 50 and analyst_rating >= 4.5:
            symbol = stock.get("symbol")
            asset = Asset(symbol, asset_type=Asset.AssetType.STOCK)
            current_price = self.get_last_price(asset)
            avg_target = stock.get("price_target")
            if current_price and avg_target and current_price < avg_target:
                order = self.create_order(asset, 150, "buy")
                self.submit_order(order)
                break
