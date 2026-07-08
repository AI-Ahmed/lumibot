import datetime
import io
import json
import math
import os
import random
import string
import time
import traceback
import uuid
from decimal import Decimal
from typing import Dict, List, Union

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import pandas as pd
import polars as pl
import requests
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError
from termcolor import colored

from lumibot.constants import LUMIBOT_DEFAULT_PYTZ
from lumibot.tools.helpers import to_datetime_aware
from lumibot.tools.lumibot_logger import get_logger, get_strategy_logger
from lumibot.tools.parquet_utils import (
    coerce_object_columns_to_json_strings,
    is_parquet_required,
    write_parquet_with_logging,
)

from ..backtesting import (
    AlpacaBacktesting,
    BacktestingBroker,
    InteractiveBrokersRESTBacktesting,
    YahooDataBacktesting,
)
from ..credentials import (
    ALPACA_CONFIG,
    ALPACA_MAX_MEMORY_BYTES,
    BACKTESTING_END,
    BACKTESTING_QUIET_LOGS,
    BACKTESTING_SHOW_PROGRESS_BAR,
    BACKTESTING_START,
    BROKER,
    DATA_SOURCE,
    DB_CONNECTION_STR,
    DISCORD_WEBHOOK_URL,
    HIDE_POSITIONS,
    HIDE_TRADES,
    LIVE_CONFIG,
    LOG_BACKTEST_PROGRESS_TO_FILE,
    LUMIWEALTH_API_KEY,
    MARKET,
    SHOW_INDICATORS,
    SHOW_PLOT,
    SHOW_TEARSHEET,
    STRATEGY_NAME,
)
from ..entities import Asset, Bars, Data, Order, Position
from ..tools import (
    create_tearsheet,
    day_deduplicate,
    get_symbol_returns,
    plot_indicators,
    plot_returns,
    stats_summary,
)
from ..traders import Trader
from .strategy_executor import StrategyExecutor

# Set the stats table name for when storing stats in a database, defined by db_connection_str
STATS_TABLE_NAME = "strategy_tracker"

class SafeJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder for Lumibot objects.
    
    Handles:
    - Objects with to_dict() method -> dictionary 
    - datetime.date and datetime.datetime -> ISO format string
    - Decimal -> float
    - Sets -> list
    """
    def default(self, obj):
        # Handle objects with to_dict method (Asset, Order, Position etc)
        if hasattr(obj, 'to_dict'):
            return obj.to_dict()

        # Handle dates and times
        if isinstance(obj, (datetime.date, datetime.datetime)):
            return obj.isoformat()

        # Handle Decimal
        if isinstance(obj, Decimal):
            return float(obj)

        # Handle sets
        if isinstance(obj, set):
            return list(obj)

        return super().default(obj)

class Vars:
    def __init__(self):
        super().__setattr__('_vars_dict', {})

    def __getattr__(self, name):
        try:
            return self._vars_dict[name]
        except KeyError:
            raise AttributeError(f"'Vars' object has no attribute '{name}'")

    def __setattr__(self, name, value):
        self._vars_dict[name] = value

    def set(self, name, value):
        self._vars_dict[name] = value

    def get(self, name, default=None):
        """Gets the value of a variable, returning a default value if it doesn't exist."""
        return self._vars_dict.get(name, default)

    def all(self):
        return self._vars_dict.copy()


class _Strategy:
    @staticmethod
    def _normalize_backtest_datetime(value):
        """Ensure backtest boundary datetimes are timezone-aware.

        Naive datetimes are localized to the LumiBot default timezone; timezone-aware
        inputs are returned unchanged so their original offsets are preserved.
        """
        if value is None:
            return None
        if isinstance(value, datetime.datetime):
            tzinfo = value.tzinfo
            if tzinfo is None or tzinfo.utcoffset(value) is None:
                return to_datetime_aware(value)
            if not hasattr(tzinfo, "zone"):
                return value.astimezone(LUMIBOT_DEFAULT_PYTZ)
        return value

    @property
    def is_backtesting(self) -> bool:
        """Boolean flag indicating whether the strategy is running in backtesting mode."""
        return getattr(self, "_is_backtesting", False)

    @is_backtesting.setter
    def is_backtesting(self, value: bool) -> None:
        self._is_backtesting = bool(value)

    IS_BACKTESTABLE = True
    _trader = None

    def __init__(
        self,
        broker=None,
        data_source=None,
        minutes_before_closing=1,
        minutes_before_opening=60,
        minutes_after_closing=0,
        sleeptime="1M",
        stats_file=None,
        risk_free_rate=None,
        benchmark_asset: str | Asset | None = "SPY",
        analyze_backtest: bool = True,
        backtesting_start=None,
        backtesting_end=None,
        quote_asset=Asset(symbol="USD", asset_type="forex"),
        starting_positions=None,
        filled_order_callback=None,
        name=None,
        budget=None,
        parameters={},
        buy_trading_fees=[],
        sell_trading_fees=[],
        buy_trading_slippages=[],
        sell_trading_slippages=[],
        force_start_immediately=False,
        discord_webhook_url=None,
        account_history_db_connection_str=None,
        db_connection_str=None,
        strategy_id=None,
        discord_account_summary_footer=None,
        should_backup_variables_to_database=True,
        should_send_summary_to_discord=True,
        save_logfile=False,
        lumiwealth_api_key=None,
        include_cash_positions=False,
        **kwargs,
    ):
        """Initializes a Strategy object.

        Parameters
        ----------
        broker : Broker
            The broker to use for the strategy. Required. For backtesting, use the BacktestingBroker class.
        data_source : DataSource
            The data source to use for the strategy. If not specified, uses the broker's default data source.
        minutes_before_closing : int
            The number of minutes before closing that the before_market_closes lifecycle method will be called and the
            strategy will be stopped.
        minutes_before_opening : int
            The number of minutes before opening that the before_market_opens lifecycle method will be called.
        sleeptime : str
            The number of seconds to sleep between the start of each iteration of the strategy (on_trading_iteration).
            For example "1S" for 1 second, "5M" for 5 minutes, "2H" for 2 hours, or "1D" for 1 day.
            Defaults to "1M" (1 minute).
        stats_file : str
            The file name to save the stats to.
        risk_free_rate : float
            The risk-free rate to use for calculating the Sharpe ratio.
        benchmark_asset : Asset or str or None
            The asset to use as the benchmark for the strategy. Defaults to "SPY". Strings are converted to
            Asset objects with an asset_type="stock". None, means don't benchmark the strategy.
        analyze_backtest: bool
            Run the backtest_analysis function at the end.
        backtesting_start : datetime.datetime
            The date and time to start backtesting from. Required for backtesting.
        backtesting_end : datetime.datetime
            The date and time to end backtesting. Required for backtesting.
        pandas_data : pd.DataFrame
            The pandas dataframe to use for backtesting. Required if using the PandasDataBacktesting data source.
        quote_asset : Asset
            The asset to use as the quote asset. Defaults to a USD forex Asset object.
        starting_positions : dict
            A dictionary of starting positions to use for backtesting. The keys are the symbols of the assets and the
            values are the quantities of the assets to start with.
        filled_order_callback : function
            A function to call when an order is filled. The function should take two parameters: the strategy object
            and the order object.
        name : str
            The name of the strategy. Defaults to the name of the class.
        budget : float
            The starting budget to use for backtesting. Defaults to $100,000.
        parameters : dict
            A dictionary of parameters to use for the strategy, this will override parameters set in the strategy
            class. The keys are the names of the parameters and the values are the values of the parameters.
            Defaults to an empty dictionary.
        buy_trading_fees : list
            A list of TradingFee objects to use for buying assets. Defaults to an empty list.
        sell_trading_fees : list
            A list of TradingFee objects to use for selling assets. Defaults to an empty list.
        buy_trading_slippages : list
            A list of TradingSlippage objects to use for buy fills in backtesting. Defaults to empty list.
        sell_trading_slippages : list
            A list of TradingSlippage objects to use for sell fills in backtesting. Defaults to empty list.
        force_start_immidiately : bool
            If True, the strategy will start immediately. If False, the strategy will wait until the market opens
            to start. Defaults to True.
        discord_webhook_url : str
            The discord webhook url to use for sending alerts from the strategy. You can send alerts to a discord
            channel by setting broadcast=True in the log_message method. The strategy will also by default send
            and account summary to the discord channel at the end of each day (db_connection_str
            must be set for this to work). Defaults to None (no discord alerts).
            For instructions on how to create a discord webhook url, see this link:
            https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks
        discord_account_summary_footer : str
            The footer to use for the account summary sent to the discord channel if discord_webhook_url is set and the
            db_connection_str is set.
            Defaults to None (no footer).
        db_connection_str : str
            The connection string to use for the account history database. This is used to store the account history
            for the strategy. The account history is sent to the discord channel at the end of each day. The connection
            string should be in the format: "sqlite:///path/to/database.db". The database should have a table named
            "strategy_tracker". If that table does not exist, it will be created. Defaults to None (no account history).
        strategy_id : str
            The id of the strategy that will be used to identify the strategy in the account history database.
            Defaults to None (lumibot will use the name of the strategy as the id).
        should_backup_variables_to_database : bool
            If True, the strategy will backup its variables to the account history database at the end of each day.
            Defaults to True.
        should_send_summary_to_discord : bool
            If True, the strategy will send an account summary to the discord channel at the end of each day.
            Defaults to True.
        save_logfile : bool
            Whether to save the logfile. Defaults to False. If True, the logfile will be saved to the logs directory.
            Turning on this option will slow down the backtest.
        include_cash_positions : bool
            If True, the strategy will include cash positions in the positions list returned by the get_positions
            method. Defaults to False.
        lumiwealth_api_key : str
            The API key to use for the LumiWealth data source. Defaults to None (saving to the cloud is off).
        kwargs : dict
            A dictionary of additional keyword arguments to pass to the strategy.

        """
        # TODO: Break up this function, too long!

        self.buy_trading_fees = buy_trading_fees
        self.sell_trading_fees = sell_trading_fees
        self.buy_trading_slippages = buy_trading_slippages
        self.sell_trading_slippages = sell_trading_slippages
        self.save_logfile = save_logfile
        self.broker = broker
        self.backtest_start = backtesting_start
        self.backtest_end = backtesting_end

        # initialize cash variables
        self._position_value = None
        self._portfolio_value = None

        # Only log one message about cloud API key being missing
        self._logged_missing_lumiwealth_api_key = False

        if name is not None:
            self._name = name

        elif STRATEGY_NAME is not None:
            self._name = STRATEGY_NAME

        else:
            self._name = self.__class__.__name__

        # Create an adapter with 'strategy_name' set to the instance's name
        if not hasattr(self, "logger") or self.logger is None:
            self.logger = get_strategy_logger(__name__, self._name)

        # Don't set log level here - let the logger hierarchy and quiet logs setting handle it
        # The StrategyLoggerAdapter will check BACKTESTING_QUIET_LOGS in its methods

        # Track which assets we've logged "Getting historical prices" for to reduce noise
        self._logged_get_historical_prices_assets = set()

        if self.broker == None:
            self.broker = BROKER

        # Handle data source initialization
        self._data_source = data_source
        if self._data_source is None:
            self._data_source = DATA_SOURCE

        # If we have a custom data source, attach it to the broker
        if self._data_source is not None and self.broker is not None:
            # Store the original data source for reference
            self._original_broker_data_source = self.broker.data_source

            # Set the custom data source
            self.broker.data_source = self._data_source

        self.hide_positions = HIDE_POSITIONS
        self.hide_trades = HIDE_TRADES
        self.include_cash_positions = include_cash_positions

        # If the MARKET env variable is set, use it as the market
        if MARKET:
            # Log the market being used
            colored_message = colored(f"Using market from environment variables: {MARKET}", "green")
            self.logger.info(colored_message)
            self.set_market(MARKET)

        self.live_config = LIVE_CONFIG
        self.discord_webhook_url = discord_webhook_url if discord_webhook_url is not None else DISCORD_WEBHOOK_URL

        if account_history_db_connection_str:
            self.db_connection_str = account_history_db_connection_str
            get_logger(__name__).warning("account_history_db_connection_str is deprecated and will be removed in future versions, please use db_connection_str instead")
        elif db_connection_str:
            self.db_connection_str = db_connection_str
        else:
            self.db_connection_str = DB_CONNECTION_STR if DB_CONNECTION_STR else None

        self.discord_account_summary_footer = discord_account_summary_footer
        self.backup_table_name="vars_backup"

        # Set the LumiWealth API key
        if lumiwealth_api_key:
            self.lumiwealth_api_key = lumiwealth_api_key
        else:
            self.lumiwealth_api_key = LUMIWEALTH_API_KEY

        if strategy_id is None:
            self.strategy_id = self._name
        else:
            self.strategy_id = strategy_id

        # Check if self.broker is set before accessing its attributes
        if self.broker is None:
            error_message = (
                "No broker is set. This typically happens when:\n"
                "1. IS_BACKTESTING is not set to 'true' (so it defaults to live trading)\n"
                "2. No broker credentials are configured in your environment variables\n\n"
                "To fix this, you need to:\n"
                "1. Create a .env file in your project root directory\n"
                "2. Set IS_BACKTESTING=true for backtesting, OR\n"
                "3. Configure a broker by setting the appropriate environment variables\n\n"
                "For example, add to your .env file:\n"
                "IS_BACKTESTING=true\n"
                "BACKTESTING_START=2023-01-01\n"
                "BACKTESTING_END=2023-12-31\n\n"
                "OR for live trading, set broker credentials like:\n"
                "ALPACA_API_KEY=your_api_key\n"
                "ALPACA_API_SECRET=your_api_secret\n"
                "ALPACA_IS_PAPER=true\n\n"
                "For more information, see: http://lumibot.lumiwealth.com/deployment.html#secrets-configuration"
            )
            self.logger.error(colored(error_message, "red"))
            raise ValueError(error_message)

        self._quote_asset = quote_asset if self.broker.name != "bitunix" else Asset("USDT", Asset.AssetType.CRYPTO)

        # Check if the quote_assets exists on the broker
        if not hasattr(self.broker, "quote_assets"):
            self.broker.quote_assets = set()

        self.broker.quote_assets.add(self._quote_asset)

        # Setting the broker object
        if self.broker == None:
            self.is_backtesting = True
        else:
            self.is_backtesting = self.broker.IS_BACKTESTING_BROKER

        self._benchmark_asset = benchmark_asset
        self._analyze_backtest = analyze_backtest

        # Get the backtesting start and end dates from the broker data source if we are backtesting
        if self.is_backtesting:
            if self.broker.data_source.datetime_start is not None and self.broker.data_source.datetime_end is not None:
                self._backtesting_start = self.broker.data_source.datetime_start
                self._backtesting_end = self.broker.data_source.datetime_end

        # Force start immediately if we are backtesting
        self.force_start_immediately = force_start_immediately

        # Initialize the chart markers list
        self._chart_markers_list = []

        # Initialize the chart lines list
        self._chart_lines_list = []

        # Initialize the chart OHLC list
        self._chart_ohlc_list = []

        # Hold the asset objects for strings for stocks only.
        self._asset_mapping = dict()

        # Setting the data provider
        if self.is_backtesting:
            if self.broker.data_source.SOURCE == "PANDAS":
                self.broker.data_source.load_data()

            # Create initial starting positions.
            self.starting_positions = starting_positions
            if self.starting_positions is not None and len(self.starting_positions) > 0:
                for asset, quantity in self.starting_positions.items():
                    position = Position(
                        self._name,
                        asset,
                        Decimal(quantity),
                        orders=None,
                        hold=0,
                        available=Decimal(quantity),
                    )
                    self.broker._filled_positions.append(position)

        # Set the the state of first iteration to True. This will later be updated to False by the strategy executor
        self._first_iteration = True

        # Setting execution parameters
        self._last_on_trading_iteration_datetime = None
        if not self.is_backtesting:
            self.update_broker_balances()

            # Set initial positions if live trading.
            self.broker._set_initial_positions(self)
        else:
            # Determine initial cash ("budget") for backtesting.
            # NOTE: In BotSpot/BotManager runs we often inject settings via environment variables.
            # If BACKTESTING_BUDGET is provided, prefer it (even if strategy code passed an explicit budget)
            # so the starting cash can be controlled per-run without forcing a code change.
            effective_budget = budget
            env_budget_raw = os.environ.get("BACKTESTING_BUDGET")
            if env_budget_raw is not None:
                trimmed = env_budget_raw.strip()
                if trimmed and trimmed.lower() not in ("none", "null"):
                    normalized = (
                        trimmed.replace("$", "")
                        .replace(",", "")
                        .replace("_", "")
                        .strip()
                    )
                    multiplier = 1.0
                    suffix = normalized[-1:].lower()
                    if suffix in ("k", "m", "b") and len(normalized) > 1:
                        normalized = normalized[:-1].strip()
                        if suffix == "k":
                            multiplier = 1_000.0
                        elif suffix == "m":
                            multiplier = 1_000_000.0
                        elif suffix == "b":
                            multiplier = 1_000_000_000.0
                    try:
                        parsed = float(normalized) * multiplier
                        if not math.isfinite(parsed) or parsed <= 0:
                            raise ValueError("budget must be a finite positive number")
                        effective_budget = parsed
                        self.logger.info(
                            colored(
                                f"Using BACKTESTING_BUDGET={effective_budget:g} as starting backtest cash",
                                "green",
                            )
                        )
                    except Exception:
                        self.logger.warning(
                            colored(
                                f"Invalid BACKTESTING_BUDGET value: {env_budget_raw!r}. "
                                "Expected a positive number like 500, 5000, 5k, 1_000_000, or $10,000. "
                                "Ignoring and falling back to budget/default.",
                                "yellow",
                            )
                        )

            if effective_budget is None:
                effective_budget = 100000  # Default budget

            self._set_cash_position(effective_budget)
            self._initial_budget = effective_budget # Store the budget used

            # ## TODO: Should all this just use _update_portfolio_value()?
            # ## START
            # Portfolio value should start with the cash set by the budget
            self._portfolio_value = self.cash # Calls property, should reflect effective_budget now

            store_assets = list(self.broker.data_source._data_store.keys())
            if len(store_assets) > 0:
                positions_value = 0
                for position in self.get_positions():
                    price = None
                    if position.asset == self._quote_asset:
                        # Don't include the quote asset since it's already included with cash
                        price = 0
                    else:
                        price = self.get_last_price(position.asset, quote=self._quote_asset)
                    value = float(position.quantity) * price
                    positions_value += value

                self._portfolio_value = self._portfolio_value + positions_value

            else:
                self._position_value = 0

            # END
            ##############################################

        self._minutes_before_closing = minutes_before_closing
        self._minutes_before_opening = minutes_before_opening
        self._minutes_after_closing = minutes_after_closing
        self._sleeptime = sleeptime
        self._risk_free_rate = risk_free_rate
        self._executor = StrategyExecutor(self)
        self.broker._add_subscriber(self._executor)

        # Stats related variables
        self._stats_file = stats_file
        self._stats = None
        self._stats_list = []
        self._stats_dirty = False
        self._analysis = {}

        # Variable backup related variables
        self.should_backup_variables_to_database = should_backup_variables_to_database
        self.should_send_summary_to_discord = should_send_summary_to_discord
        self._last_backup_state = None
        self.vars = Vars()

        # Storing parameters for the initialize method
        if not hasattr(self, "parameters") or not isinstance(self.parameters, dict) or self.parameters is None:
            self.parameters = {}
        self.parameters = {**self.parameters, **kwargs}
        if parameters is not None and isinstance(self.parameters, dict):
            self.parameters = {**self.parameters, **parameters}

        self._strategy_returns_df = None
        self._benchmark_returns_df = None

        self._filled_order_callback = filled_order_callback

    # =============Internal functions===================
    def _copy_dict(self):
        result = {}
        ignored_fields = ["broker", "data_source", "trading_pairs", "asset_gen"]
        for key in self.__dict__:
            if key[0] != "_" and key not in ignored_fields:
                try:
                    result[key] = self.__dict__[key]
                except KeyError:
                    pass
                    # self.logger.warning(
                    #     "Cannot perform deepcopy on %r" % self.__dict__[key]
                    # )
            elif key in [
                "_name",
                "_initial_budget",
                # "_cash",
                "_portfolio_value",
                "_minutes_before_closing",
                "_minutes_before_opening",
                "_sleeptime",
                "is_backtesting",
            ]:
                result[key[1:]] = self.__dict__[key]

        return result

    def _validate_order(self, order):
        """
        Validates an order to ensure it meets the necessary criteria before submission.

        Parameters:
        order (Order): The order to be validated.

        Returns:
        bool: True if the order is valid, False otherwise.

        Validation checks:
        - The order is not None.
        - The order is an instance of the Order class.
        - The order quantity is not zero.
        - For HFT strategies, check if sell order would result in negative position.
        """

        # Check if order is None
        if order is None:
            self.logger.error(
                "Cannot submit a None order, please check to make sure that you have actually created an order before submitting."
            )
            return False

        # Check if the order is an Order object
        if not isinstance(order, Order):
            self.logger.error(
                f"Order must be an Order object. You entered {order}."
            )
            return False

        # Check if the order quantity is None
        if order.quantity is None:
            self.logger.error(
                "Order quantity cannot be None. Please provide a valid quantity value."
            )
            return False

        # Check if the order does not have a quantity of zero
        if order.quantity == 0:
            self.logger.error(
                f"Order quantity cannot be zero. You entered {order.quantity}."
            )
            return False

        # Delegate position validation to broker for unified validation
        # This ensures single source of truth and proper error event dispatching
        if order.is_sell_order():
            # Use broker's position validation for consistency
            # The broker will handle position checks and error events properly
            validation_result = self.broker.validate_order_position(order, self.name)
            if not validation_result.is_valid:
                # Log the validation failure but let broker handle the error events
                self.logger.info(
                    f"Order validation deferred to broker: {validation_result.message} "
                    f"Order will be processed by broker validation system."
                )
                # Return True to allow broker to handle validation and error events
                # This ensures proper error dispatching and logging
                return True

        return True

    def _set_cash_position(self, cash: float):
        # Check if cash is in the list of positions yet
        for x in range(len(self.broker._filled_positions.get_list())):
            position = self.broker._filled_positions[x]
            if position is not None and position.asset == self._quote_asset:
                position.quantity = cash
                self.broker._filled_positions[x] = position
                return

        # If not in positions, create a new position for cash
        position = Position(
            self._name,
            self._quote_asset,
            Decimal(cash),
            orders=None,
            hold=0,
            available=Decimal(cash),
        )
        self.broker._filled_positions.append(position)

    def _sanitize_user_asset(self, asset):
        if isinstance(asset, Asset):
            return asset
        elif isinstance(asset, tuple):
            return asset
        elif isinstance(asset, list):
            return [self._sanitize_user_asset(a) for a in asset]
        elif isinstance(asset, str):
            # Make sure the asset is uppercase for consistency (and because some brokers require it)
            asset = asset.upper()
            return Asset(symbol=asset)
        else:
            if self.broker.data_source.SOURCE != "CCXT":
                raise ValueError(f"You must enter a symbol string or an asset object. You " f"entered {asset}")
            else:
                raise ValueError(
                    "You must enter symbol string or an asset object. If you "
                    "getting a quote, you may enter a string like `ETH/BTC` or "
                    "asset objects in a tuple like (Asset(ETH), Asset(BTC))."
                )

    def _log_strat_name(self):
        """Returns the name of the strategy as a string if not default"""
        return f"{self._name} " if self._name is not None else ""

    def update_broker_balances(self, force_update=True):
        """Updates the broker's balances, including cash and portfolio value

        Parameters
        ----------
        force_update : bool, optional
            If True, forces the broker to update the balances immediately.
            If False, the broker will only update the balances if the last
            update was more than 1 minute ago. The default is True.

        Returns
        -------
        bool
            True if the broker's balances were updated, False otherwise
        """
        if self.is_backtesting:
            return True

        if "last_broker_balances_update" not in self.__dict__:
            self.last_broker_balances_update = None

        UPDATE_INTERVAL = 59
        if (
            self.last_broker_balances_update is None
            or force_update
            or (
                self.last_broker_balances_update + datetime.timedelta(seconds=UPDATE_INTERVAL) < datetime.datetime.now()
            )
        ):
            try:
                broker_balances = self.broker._get_balances_at_broker(self._quote_asset, self)
            except Exception as e:
                self.logger.info(f"Error getting broker balances: {e}", exc_info=True)
                return False

            if broker_balances is not None:
                cash, position_value, portfolio_value = broker_balances

                # Update cash position instead of setting _cash directly
                self._set_cash_position(cash)
                self._position_value = position_value
                self._portfolio_value = portfolio_value

                self.last_broker_balances_update = datetime.datetime.now()
                return True

            else:
                self.logger.warning(
                    "Unable to get balances (cash, portfolio value, etc) from broker. "
                    "Please check your broker and your broker configuration."
                )
                return False
        else:
            self.logger.debug("Balances already updated recently. Skipping update.")

    # =============Auto updating functions=============

    def _update_portfolio_value(self):
        """updates self.portfolio_value"""
        # Live runs don't need to recalculate portfolio value here, as the broker sync should handle it
        if not self.is_backtesting:
            return

        with self._executor.lock:
            # Initialize last known prices tracker for forward-fill fallback.
            # This is used when OHLC data is missing (common for illiquid options like LEAPS).
            if not hasattr(self, '_last_known_prices'):
                self._last_known_prices = {}

            # Used for traditional brokers, for crypto this could be 0
            portfolio_value = self.cash

            positions = self.broker.get_tracked_positions(self._name)
            assets_original = [position.asset for position in positions]

            # Set the base currency for crypto valuations.

            prices = {}
            for asset in assets_original:
                if asset != self._quote_asset:
                    asset_is_option = False
                    if asset.asset_type == "crypto" or asset.asset_type == "forex":
                        asset = (asset, self._quote_asset)
                    elif asset.asset_type == "option":
                        asset_is_option = True

                    if self.broker.option_source is not None and asset_is_option:
                        source = self.broker.option_source
                    else:
                        source = self.broker.data_source
                    prices[asset] = self._get_price_from_source(source, asset)

            for position in positions:
                # Turn the asset into a tuple if it's a crypto asset
                asset = (
                    position.asset
                    if (position.asset.asset_type != "crypto") and (position.asset.asset_type != "forex")
                    else (position.asset, self._quote_asset)
                )
                quantity = position.quantity
                price = prices.get(asset)

                # If the asset is the quote asset, then we already have included it from cash
                # Eg. if we have a position of USDT and USDT is the quote_asset then we already consider it as cash
                if self._quote_asset is not None:
                    if isinstance(asset, tuple) and asset == (
                        self._quote_asset,
                        self._quote_asset,
                    ):
                        continue
                    elif isinstance(asset, Asset) and asset == self._quote_asset:
                        continue

                # Normalize "missing" prices to None so forward-fill fallback can apply.
                # Some data sources return 0 or NaN for "no price" (common on non-trading timestamps).
                if price is not None:
                    try:
                        price_float = float(price)
                    except (TypeError, ValueError):
                        price = None
                    else:
                        if (not math.isfinite(price_float)) or price_float == 0:
                            price = None
                        else:
                            price = price_float

                # Track valid prices for forward-fill fallback
                if price is not None:
                    self._last_known_prices[asset] = price

                if self.is_backtesting and price is None:
                    # Forward-fill fallback: use last known price when current price is unavailable.
                    # This is critical for illiquid options (LEAPS) that may not trade for days.
                    if asset in self._last_known_prices:
                        price = self._last_known_prices[asset]
                        base_asset = asset[0] if isinstance(asset, tuple) else asset
                        asset_symbol = getattr(base_asset, 'symbol', str(base_asset))
                        self.logger.warning(
                            "Using forward-filled price %.4f for %s at %s (no current price available).",
                            price, asset_symbol, self.broker.datetime,
                        )
                    else:
                        # No price history - must skip this position
                        if isinstance(asset, Asset):
                            asset_details = (
                                f"symbol: {asset.symbol}, type: {asset.asset_type}, right: {asset.right}, "
                                f"expiration: {asset.expiration}, strike: {asset.strike}"
                            )
                            self.logger.warning(
                                "Skipping valuation for asset (%s) because no price was available at %s.",
                                asset_details,
                                self.broker.datetime,
                            )
                        elif isinstance(asset, tuple):
                            base_asset = asset[0] if asset else None
                            if isinstance(base_asset, Asset):
                                asset_details = (
                                    f"symbol: {base_asset.symbol}, type: {base_asset.asset_type}, right: {base_asset.right}, "
                                    f"expiration: {base_asset.expiration}, strike: {base_asset.strike}"
                                )
                            else:
                                asset_details = str(asset)
                            self.logger.warning(
                                "Skipping valuation for pair (%s) because no price was available at %s.",
                                asset_details,
                                self.broker.datetime,
                            )
                        continue
                if isinstance(asset, tuple):
                    multiplier = 1
                else:
                    multiplier = asset.multiplier if asset.asset_type in ["option", "future", "cont_future"] else 1

                # BACKTESTING ONLY: Special handling for futures portfolio value
                # In backtesting, cash has margin deducted, so we need to add it back
                # In live trading, brokers handle this internally
                if (
                    self.is_backtesting
                    and not isinstance(asset, tuple)
                    and asset.asset_type in ["future", "cont_future"]
                ):
                    # Import here to avoid circular dependency
                    from lumibot.backtesting.backtesting_broker import get_futures_margin_requirement

                    # Add margin tied up in position (was deducted from cash)
                    margin_per_contract = get_futures_margin_requirement(asset)
                    total_margin = margin_per_contract * abs(float(quantity))
                    portfolio_value += total_margin

                    # Add unrealized P&L = (current_price - entry_price) × quantity × multiplier
                    entry_price = position.avg_fill_price if (hasattr(position, 'avg_fill_price') and position.avg_fill_price) else price
                    unrealized_pnl = (float(price) - float(entry_price)) * float(quantity) * multiplier
                    portfolio_value += unrealized_pnl
                else:
                    # All other cases (stocks, options, crypto, live trading)
                    position_value = float(quantity) * float(price) * multiplier
                    portfolio_value += position_value

            self._portfolio_value = portfolio_value
        return portfolio_value

    def _get_price_from_source(self, source, asset):
        """Return best available price from the provided data source."""
        if source is None:
            return None

        timestep_hint = None
        try:
            cadence_seconds = self._get_sleeptime_seconds()
            if cadence_seconds is not None and cadence_seconds >= 20 * 3600:
                timestep_hint = "day"
        except Exception:
            timestep_hint = None

        if hasattr(source, "get_price_snapshot"):
            try:
                if timestep_hint:
                    snapshot = source.get_price_snapshot(asset, timestep=timestep_hint)
                else:
                    snapshot = source.get_price_snapshot(asset)
            except Exception:
                self.logger.exception(
                    "Error retrieving price snapshot for %s from %s; falling back to last trade.",
                    asset,
                    type(source).__name__,
                )
            else:
                snapshot_price = self._pick_snapshot_price(asset, snapshot)
                if snapshot_price is not None:
                    return snapshot_price

        get_last_price = getattr(source, "get_last_price", None)
        if callable(get_last_price):
            price = get_last_price(asset)
            if price is not None:
                return price

        self.logger.warning(
            "Data source %s for asset %s does not provide get_last_price; returning None.",
            type(source).__name__,
            asset,
        )
        return None

    def _pick_snapshot_price(self, asset, snapshot):
        """Decide which figure to use from a Theta snapshot."""
        if not snapshot:
            return None

        close_price = self._coerce_snapshot_price(snapshot.get("close"))
        bid_price = self._coerce_snapshot_price(snapshot.get("bid"))
        ask_price = self._coerce_snapshot_price(snapshot.get("ask"))
        threshold = self._snapshot_stale_threshold_seconds()

        now = self._normalize_snapshot_datetime(getattr(self.broker, "datetime", None))
        if now is None:
            now = self._normalize_snapshot_datetime(datetime.datetime.now(LUMIBOT_DEFAULT_PYTZ))

        trade_time = self._normalize_snapshot_datetime(snapshot.get("last_trade_time"))
        bid_time = self._normalize_snapshot_datetime(snapshot.get("last_bid_time"))
        ask_time = self._normalize_snapshot_datetime(snapshot.get("last_ask_time"))

        def _is_fresh(ts):
            if ts is None or now is None:
                return False
            return (now - ts).total_seconds() <= threshold

        if close_price is not None and _is_fresh(trade_time):
            return close_price

        bid_fresh = bid_price is not None and _is_fresh(bid_time)
        ask_fresh = ask_price is not None and _is_fresh(ask_time)

        if bid_fresh and ask_fresh:
            mid_price = (bid_price + ask_price) / 2.0
            self.logger.debug(
                "Using bid/ask mid price for %s because last trade at %s is older than %ss.",
                asset,
                trade_time.isoformat() if trade_time else "unknown",
                threshold,
            )
            return mid_price
        if bid_fresh:
            self.logger.debug(
                "Using bid price for %s because last trade at %s is older than %ss.",
                asset,
                trade_time.isoformat() if trade_time else "unknown",
                threshold,
            )
            return bid_price
        if ask_fresh:
            self.logger.debug(
                "Using ask price for %s because last trade at %s is older than %ss.",
                asset,
                trade_time.isoformat() if trade_time else "unknown",
                threshold,
            )
            return ask_price

        if close_price is not None:
            # Use DEBUG - this is expected behavior in backtesting where historical data
            # may not have fresh bid/ask timestamps. WARNING here creates excessive noise.
            self.logger.debug(
                "Using stale trade price for %s; last trade=%s, last bid=%s, last ask=%s (threshold=%ss).",
                asset,
                trade_time.isoformat() if trade_time else "unknown",
                bid_time.isoformat() if bid_time else "unknown",
                ask_time.isoformat() if ask_time else "unknown",
                threshold,
            )
            return close_price

        return None

    @staticmethod
    def _coerce_snapshot_price(value):
        if value is None:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        if math.isnan(numeric):
            return None
        return numeric

    def _normalize_snapshot_datetime(self, dt_value):
        if dt_value is None:
            return None
        if isinstance(dt_value, pd.Timestamp):
            dt_value = dt_value.to_pydatetime()
        elif isinstance(dt_value, str):
            try:
                dt_value = pd.to_datetime(dt_value).to_pydatetime()
            except (TypeError, ValueError):
                return None
        if isinstance(dt_value, datetime.datetime):
            if dt_value.tzinfo is None:
                try:
                    return LUMIBOT_DEFAULT_PYTZ.localize(dt_value)
                except ValueError:
                    return dt_value.replace(tzinfo=LUMIBOT_DEFAULT_PYTZ)
            return dt_value.astimezone(LUMIBOT_DEFAULT_PYTZ)
        return None

    @staticmethod
    def _snapshot_stale_threshold_seconds():
        try:
            return int(os.environ.get("LUMIBOT_MTM_STALE_SECONDS", "120"))
        except (TypeError, ValueError):
            return 120

    @staticmethod
    def _is_buy_side(side):
        if side is None:
            return False
        if isinstance(side, Order.OrderSide):
            normalized = side.value.lower()
        else:
            normalized = str(side).lower()
        return normalized in ("buy", "buy_to_open", "buy_to_cover", "buy_to_close")

    @staticmethod
    def _is_sell_side(side):
        if side is None:
            return False
        if isinstance(side, Order.OrderSide):
            normalized = side.value.lower()
        else:
            normalized = str(side).lower()
        return normalized in ("sell", "sell_short", "sell_to_close", "sell_to_open")

    def _update_cash(self, order_or_side, quantity, price, multiplier):
        """update the self.cash"""
        with self._executor.lock:
            cash_val = self.cash # Calls property
            if cash_val is None: # Handle if property somehow still returns None despite the fix in its getter
                # self.logger.warning("_update_cash: self.cash (property) returned None. Defaulting to 0.0 for calculation.")
                cash_val = 0.0

            current_cash = Decimal(str(cash_val)) # Convert to Decimal robustly

            # Ensure all operands are Decimal for precision
            quantity_dec = Decimal(str(quantity))
            price_dec = Decimal(str(price))
            multiplier_dec = Decimal(str(multiplier))

            order_obj = order_or_side if isinstance(order_or_side, Order) else None
            side = getattr(order_obj, "side", order_or_side)

            is_buy = order_obj.is_buy_order() if order_obj is not None else self._is_buy_side(side)
            is_sell = order_obj.is_sell_order() if order_obj is not None else self._is_sell_side(side)

            if is_buy:
                current_cash -= quantity_dec * price_dec * multiplier_dec
            if is_sell:
                current_cash += quantity_dec * price_dec * multiplier_dec

            self._set_cash_position(float(current_cash)) # _set_cash_position expects float

            # Todo also update the cash asset in positions?

            return self.cash # Return the updated cash by calling the property again

    def _update_cash_with_dividends(self):
        with self._executor.lock:
            # IDEMPOTENCY CHECK: Track which (date, asset) combinations have already had dividends applied.
            # This prevents double/multiple dividend application when this method is called multiple times
            # per day from different locations in strategy_executor.py.
            if not hasattr(self, '_dividends_applied_tracker'):
                self._dividends_applied_tracker = set()

            current_date = self.get_datetime().date() if hasattr(self.get_datetime(), 'date') else self.get_datetime()

            positions = self.broker.get_tracked_positions(self._name)

            assets = []
            for position in positions:
                if position.asset != self._quote_asset and position.asset.asset_type != "option":
                    assets.append(position.asset)

            # Early return if no assets - avoid expensive dividend API calls
            if not assets:
                return self.cash

            dividends_per_share = self.get_yesterday_dividends(assets)

            for position in positions:
                asset = position.asset
                quantity = position.quantity
                dividend_per_share = 0 if dividends_per_share is None else dividends_per_share.get(asset, 0)

                # Skip if no dividend or already applied for this (date, asset) combination
                if dividend_per_share == 0:
                    continue

                tracker_key = (current_date, getattr(asset, 'symbol', str(asset)))
                if tracker_key in self._dividends_applied_tracker:
                    continue  # Already applied dividend for this asset on this date

                cash = self.cash
                if cash is None:
                    cash = 0
                cash += dividend_per_share * float(quantity)
                self._set_cash_position(cash)

                # Mark as applied
                self._dividends_applied_tracker.add(tracker_key)

            return self.cash

    # =============Stats functions=====================

    def _append_row(self, row):
        self._stats_list.append(row)
        self._stats_dirty = True

    def _format_stats(self):
        if not self._stats_dirty and self._stats is not None:
            return self._stats

        self._stats = pd.DataFrame(self._stats_list)
        if "datetime" in self._stats.columns:
            self._stats = self._stats.set_index("datetime")
            self._stats = self._stats.sort_index()
        
        # Calculate standard returns
        self._stats["return"] = self._stats["portfolio_value"].pct_change()
        self._stats_dirty = False
        
        # Add intraday metrics for HFT strategies
        # Add trading day column for grouping
        if self._is_hft_strategy():
            # Add intraday cumulative return column
            self._stats["intraday_cumulative_return"] = (1 + self._stats["return"]).cumprod() - 1
            
            # Add trading day column for grouping
            self._stats["trading_day"] = self._stats.index.date
            
            # Calculate intraday volatility (standard deviation of returns within each day)
            day_groups = self._stats.groupby("trading_day")
            
            # Create a dictionary to store intraday metrics
            intraday_metrics = {}
            
            # Calculate various intraday metrics
            intraday_metrics["max_intraday_return"] = day_groups["intraday_cumulative_return"].max()
            intraday_metrics["min_intraday_return"] = day_groups["intraday_cumulative_return"].min()
            intraday_metrics["intraday_volatility"] = day_groups["return"].std()
            
            # Calculate intraday Sharpe ratio (mean return / std of returns)
            # Handle division by zero by replacing NaN with 0
            mean_returns = day_groups["return"].mean()
            std_returns = day_groups["return"].std()
            intraday_metrics["intraday_sharpe"] = mean_returns / std_returns.replace(0, float('nan'))
            intraday_metrics["intraday_sharpe"] = intraday_metrics["intraday_sharpe"].fillna(0)
            
            # Calculate trade frequency (number of non-zero returns per day)
            intraday_metrics["trade_frequency"] = day_groups["return"].apply(lambda x: (x != 0).sum())
            
            # Store these metrics in the strategy for later use in tearsheet
            self._intraday_metrics = pd.DataFrame(intraday_metrics)
            
            # Add a summary of intraday metrics to the strategy parameters
            if not hasattr(self, "parameters"):
                self.parameters = {}
                
            if "HFT Metrics" not in self.parameters:
                self.parameters["HFT Metrics"] = {}
                
            # Add average metrics
            self.parameters["HFT Metrics"]["Avg Max Intraday Return"] = intraday_metrics["max_intraday_return"].mean()
            self.parameters["HFT Metrics"]["Avg Min Intraday Return"] = intraday_metrics["min_intraday_return"].mean()
            self.parameters["HFT Metrics"]["Avg Intraday Volatility"] = intraday_metrics["intraday_volatility"].mean()
            self.parameters["HFT Metrics"]["Avg Intraday Sharpe"] = intraday_metrics["intraday_sharpe"].mean()
            self.parameters["HFT Metrics"]["Avg Daily Trade Count"] = intraday_metrics["trade_frequency"].mean()
            
            # Add maximum values
            self.parameters["HFT Metrics"]["Max Intraday Return"] = intraday_metrics["max_intraday_return"].max()
            self.parameters["HFT Metrics"]["Max Intraday Drawdown"] = intraday_metrics["min_intraday_return"].min()
            self.parameters["HFT Metrics"]["Max Intraday Volatility"] = intraday_metrics["intraday_volatility"].max()
            self.parameters["HFT Metrics"]["Max Daily Trade Count"] = intraday_metrics["trade_frequency"].max()

        return self._stats

    def _dump_stats(self):
        # Don't change logger levels - respect the configured quiet logs setting
        if len(self._stats_list) > 0:
            self._format_stats()
            if self._stats_file:
                # Get the directory name from the stats file path
                stats_directory = os.path.dirname(self._stats_file)

                # Check if the directory exists
                if not os.path.exists(stats_directory):
                    os.makedirs(stats_directory)

                self._stats.to_csv(self._stats_file)
                stats_parquet_file = (
                    self._stats_file[:-4] + ".parquet" if self._stats_file.lower().endswith(".csv") else self._stats_file + ".parquet"
                )
                required = bool(self.is_backtesting) and is_parquet_required()
                write_parquet_with_logging(
                    df=self._stats,
                    path=stats_parquet_file,
                    artifact="stats",
                    logger=self.logger,
                    index=True,
                    required=required,
                    compression="zstd",
                    sanitizer=coerce_object_columns_to_json_strings,
                )

            self._strategy_returns_df = day_deduplicate(self._stats)

            self._analysis = stats_summary(self._strategy_returns_df, self.risk_free_rate)

            # Get performance for the benchmark asset
            self._dump_benchmark_stats()


    def _dump_benchmark_stats(self):
        if not self.is_backtesting or not self._benchmark_asset:
            return
        if self._backtesting_start is not None and self._backtesting_end is not None:
            # Need to adjust the backtesting end date because the data from Yahoo
            # is at the start of the day, so the graph cuts short. This may be needed
            # for other timeframes as well
            backtesting_end_adjusted = self._backtesting_end

            # IBKR backtests:
            # - For crypto benchmarks, prefer the IBKR data source (Yahoo crypto tickers are inconsistent).
            # - For equity benchmarks (e.g., SPY), prefer Yahoo to avoid IBKR history flakiness impacting
            #   tearsheet generation (benchmark is cosmetic; strategy stats are authoritative).
            if str(getattr(self.broker.data_source, "SOURCE", "") or "").upper() == "INTERACTIVEBROKERSREST":
                def _fallback_benchmark_from_strategy() -> None:
                    """Fallback: use the strategy equity curve as a benchmark so tearsheets remain available."""
                    try:
                        if self._strategy_returns_df is None or self._strategy_returns_df.empty:
                            return
                        if "portfolio_value" not in self._strategy_returns_df.columns:
                            return
                        series = self._strategy_returns_df["portfolio_value"].astype(float).copy()
                        first = float(series.dropna().iloc[0]) if not series.dropna().empty else None
                        if first is None or first == 0:
                            return
                        bench = pd.DataFrame(index=self._strategy_returns_df.index)
                        # Match the shape expected by plotting + tearsheet code:
                        # - `plot_returns()` expects a `return` column
                        # - tearsheets typically consume `symbol_cumprod`
                        bench["return"] = series.pct_change(fill_method=None)
                        bench["symbol_cumprod"] = (1 + bench["return"]).cumprod()
                        self._benchmark_returns_df = bench
                        self.logger.warning(
                            "IBKR benchmark bars unavailable; using strategy equity curve as benchmark for tearsheet generation."
                        )
                    except Exception:
                        return

                benchmark_asset = self._benchmark_asset
                if isinstance(benchmark_asset, str):
                    parts = [p.strip() for p in benchmark_asset.split("/") if p.strip()]
                    if len(parts) == 2:
                        benchmark_asset = (
                            Asset(symbol=parts[0], asset_type="crypto"),
                            Asset(symbol=parts[1], asset_type="forex"),
                        )
                    else:
                        try:
                            self._benchmark_returns_df = get_symbol_returns(
                                benchmark_asset,
                                self._backtesting_start,
                                backtesting_end_adjusted,
                            )
                        except Exception:
                            _fallback_benchmark_from_strategy()
                        return
                elif isinstance(benchmark_asset, Asset) and str(getattr(benchmark_asset, "asset_type", "")).lower() == "stock":
                    try:
                        self._benchmark_returns_df = get_symbol_returns(
                            benchmark_asset.symbol,
                            self._backtesting_start,
                            backtesting_end_adjusted,
                        )
                    except Exception:
                        _fallback_benchmark_from_strategy()
                    return

                timestep = "minute"
                if "D" in str(self._sleeptime):
                    timestep = "day"

                bars = self.broker.data_source.get_historical_prices_between_dates(
                    benchmark_asset,
                    timestep,
                    start_date=self._backtesting_start,
                    end_date=backtesting_end_adjusted,
                    quote=self._quote_asset,
                )
                if bars is None or getattr(bars, "df", None) is None:
                    self.logger.error(f"Couldn't get benchmark bars from IBKR data source: {benchmark_asset}")
                    _fallback_benchmark_from_strategy()
                    return
                df = bars.df
                if df is None or df.empty or "close" not in df.columns:
                    self.logger.error(f"IBKR benchmark bars empty/invalid: {benchmark_asset}")
                    _fallback_benchmark_from_strategy()
                    return
                df = df.copy()
                df["return"] = df["close"].pct_change(fill_method=None)
                df["symbol_cumprod"] = (1 + df["return"]).cumprod()
                self._benchmark_returns_df = df

            elif type(self.broker.data_source) == AlpacaBacktesting:
                benchmark_asset = self._benchmark_asset

                df = self.broker.data_source.get_historical_prices_between_dates(
                    base_asset=benchmark_asset
                )

                if df is None or df.empty:
                    self.logger.error(f"Couldn't get_historical_prices_between_dates: {benchmark_asset}")
                    return

                df = df.loc[self._backtesting_start:self._backtesting_end].copy()
                if hasattr(df, 'select'):  # Polars DataFrame
                    df = df.with_columns(pl.col("close").pct_change().alias("return"))
                    df = df.with_columns((1 + pl.col("return")).cumprod().alias("symbol_cumprod"))
                else:  # Pandas DataFrame
                    df["return"] = df["close"].pct_change(fill_method=None)
                    df["symbol_cumprod"] = (1 + df["return"]).cumprod()
                self._benchmark_returns_df = df

            # If we are using any other data source, then get the benchmark returns from yahoo
            else:
                benchmark_asset = self._benchmark_asset

                # If the benchmark asset is a string, then just use the string as the symbol
                if isinstance(benchmark_asset, str):
                    benchmark_symbol = benchmark_asset
                # If the benchmark asset is an Asset object, then use the symbol of the asset
                elif isinstance(benchmark_asset, Asset):
                    benchmark_symbol = benchmark_asset.symbol
                # If the benchmark asset is a tuple, then use the symbols of the assets in the tuple
                elif isinstance(benchmark_asset, tuple):
                    benchmark_symbol = f"{benchmark_asset[0].symbol}/{benchmark_asset[1].symbol}"

                self._benchmark_returns_df = get_symbol_returns(
                    benchmark_symbol,
                    self._backtesting_start,
                    backtesting_end_adjusted,
                )

    def plot_returns_vs_benchmark(
        self,
        plot_file_html="backtest_result.html",
        trades_df=None,
        show_plot=True,
    ):
        if not show_plot:
            return
        elif self._strategy_returns_df is None:
            self.logger.warning("Cannot plot returns because the strategy returns are missing")
        elif self._benchmark_returns_df is None:
            self.logger.warning("Cannot plot returns because the benchmark returns are missing")
        else:
            plot_returns(
                self._strategy_returns_df,
                f"{self._log_strat_name()}Strategy",
                self._benchmark_returns_df,
                str(self._benchmark_asset),
                plot_file_html,
                trades_df,
                show_plot,
                initial_budget=self._initial_budget,
            )

    def _is_hft_strategy(self):
        """
        Detect if this is a high-frequency trading strategy based on trading frequency
        or sleeptime configuration.
        
        Returns
        -------
        bool
            True if the strategy appears to be HFT, False otherwise
        """
        # First check: if sleeptime is very small (seconds or milliseconds), consider it HFT
        if hasattr(self, '_sleeptime'):
            sleeptime_str = str(self._sleeptime).lower()
            
            # Check for millisecond-level trading frequency
            if 'ms' in sleeptime_str or sleeptime_str.endswith('s') and float(sleeptime_str.rstrip('s')) < 5:
                return True
                
            # Check for very frequent minute-level trading (less than 5 minutes)
            if sleeptime_str.endswith('m') and float(sleeptime_str.rstrip('m')) < 5:
                return True
        
        # Second check: based on historical trading frequency
        if len(self._stats_list) == 0:
            return False
            
        # Calculate average iterations per day
        trading_days = set()
        for row in self._stats_list:
            if 'datetime' in row:
                if hasattr(row['datetime'], 'date'):
                    trading_days.add(row['datetime'].date())
                else:
                    # Try to convert to datetime if it's not already
                    try:
                        dt = pd.to_datetime(row['datetime'])
                        trading_days.add(dt.date())
                    except:
                        pass
        
        # If we couldn't extract dates, use a conservative estimate
        if not trading_days:
            return False
            
        avg_iterations_per_day = len(self._stats_list) / max(1, len(trading_days))
        
        # Consider it HFT if more than 100 iterations per day on average
        return avg_iterations_per_day > 100
    
    def _get_appropriate_resample_rule(self):
        """
        Determine the appropriate resampling rule based on strategy characteristics.
        
        Returns
        -------
        str
            The pandas resample rule to use
        """
        if self._is_hft_strategy():
            # For HFT strategies, use minute-level resampling
            # This can be adjusted based on specific needs
            return "1min"  # 1-minute intervals
        else:
            # For regular strategies, use daily resampling
            return "D"
            
    def tearsheet(
        self,
        save_tearsheet=True,
        tearsheet_file=None,
        show_tearsheet=True,
        resample_rule=None,  # Changed default to None for auto-detection
        bar_type="volume",  # Bar type for HFT strategies: 'volume', 'dollar', 'imbalance', 'runs', 'time', 'auto'
    ):
        if not save_tearsheet and not show_tearsheet:
            return None

        if show_tearsheet:
            save_tearsheet = True

        if self._strategy_returns_df is None:
            self.logger.warning("Cannot create a tearsheet because the strategy returns are missing")
        else:
            # Get the strategy parameters
            strategy_parameters = dict(self.parameters) if isinstance(self.parameters, dict) else {}

            # Remove pandas_data from the strategy parameters if it exists
            if "pandas_data" in strategy_parameters:
                del strategy_parameters["pandas_data"]

            # Always include backtest context in the QuantStats "Parameters Used" table.
            # This keeps reports self-describing (especially important when comparing sources).
            try:
                if self.is_backtesting:
                    strategy_parameters.setdefault(
                        "BACKTESTING_DATA_SOURCE",
                        os.environ.get("BACKTESTING_DATA_SOURCE") or type(self.broker.data_source).__name__,
                    )
                    if getattr(self.broker, "option_source", None) is not None:
                        strategy_parameters.setdefault(
                            "OPTION_DATA_SOURCE",
                            type(self.broker.option_source).__name__,
                        )
            except Exception:
                # Never fail tearsheet generation due to metadata/diagnostics.
                pass

            strat_name = self._name if self._name is not None else "Strategy"

            # Auto-detect appropriate resample rule if not specified
            if resample_rule is None:
                resample_rule = self._get_appropriate_resample_rule()
                
                # Add information about detected strategy type to parameters
                if "Strategy Info" not in strategy_parameters:
                    strategy_parameters["Strategy Info"] = {}
                    
                strategy_parameters["Strategy Info"]["Detected Type"] = "High-Frequency Trading" if resample_rule != "D" else "Standard"
                strategy_parameters["Strategy Info"]["Resample Rule"] = resample_rule
            
            # Check if bar_type is specified in strategy parameters (allows override from strategy)
            if "bar_type" in self.parameters:
                bar_type = self.parameters["bar_type"]
                
            # For HFT strategies, include trade metrics in the parameters
            if hasattr(self, '_trade_history') and self._trade_history:
                # Analyze trades and add metrics to parameters
                trade_metrics = self.analyze_trades()
                
                if "Trade Metrics" not in strategy_parameters:
                    strategy_parameters["Trade Metrics"] = {}
                
                # Format the metrics for display
                strategy_parameters["Trade Metrics"]["Trade Count"] = trade_metrics["trade_count"]
                strategy_parameters["Trade Metrics"]["Win Rate"] = f"{trade_metrics['win_rate']:.2%}"
                strategy_parameters["Trade Metrics"]["Profit Factor"] = f"{trade_metrics['profit_factor']:.2f}"
                strategy_parameters["Trade Metrics"]["Avg Profit/Trade"] = f"${trade_metrics['avg_profit_per_trade']:.2f}"
                strategy_parameters["Trade Metrics"]["Avg Profit %/Trade"] = f"{trade_metrics['avg_profit_pct_per_trade']:.2f}%"
                strategy_parameters["Trade Metrics"]["Total P&L"] = f"${trade_metrics['total_profit']:.2f}"
                strategy_parameters["Trade Metrics"]["Max Profit"] = f"${trade_metrics['max_profit']:.2f}"
                strategy_parameters["Trade Metrics"]["Max Loss"] = f"${trade_metrics['max_loss']:.2f}"
                strategy_parameters["Trade Metrics"]["Avg Win"] = f"${trade_metrics['avg_win']:.2f}"
                strategy_parameters["Trade Metrics"]["Avg Loss"] = f"${trade_metrics['avg_loss']:.2f}"

            lumibot_version = None
            backtesting_data_sources = None
            backtest_time_seconds = None

            try:
                if self.is_backtesting:
                    try:
                        import lumibot as _lumibot

                        lumibot_version = getattr(_lumibot, "__version__", None)
                    except Exception:
                        lumibot_version = None

                    try:
                        backtesting_data_sources = (
                            os.environ.get("BACKTESTING_DATA_SOURCES")
                            or os.environ.get("BACKTESTING_DATA_SOURCE")
                            or type(self.broker.data_source).__name__
                        )
                    except Exception:
                        backtesting_data_sources = os.environ.get("BACKTESTING_DATA_SOURCE")

                    backtest_time_seconds = getattr(self, "_backtest_time_seconds", None)
                    if backtest_time_seconds is None:
                        start_ts = getattr(self, "_backtest_time_start_monotonic", None)
                        if start_ts is not None:
                            backtest_time_seconds = time.monotonic() - float(start_ts)
            except Exception:
                pass

            result = create_tearsheet(
                self._strategy_returns_df,
                strat_name,
                tearsheet_file,
                self._benchmark_returns_df,
                self._benchmark_asset,
                show_tearsheet,
                save_tearsheet,
                risk_free_rate=self.risk_free_rate,
                strategy_parameters=strategy_parameters,
                resample_rule=resample_rule,  # Pass the resample_rule parameter
                bar_type=bar_type,  # Pass the bar_type parameter
                lumibot_version=lumibot_version,
                backtesting_data_sources=backtesting_data_sources,
                backtest_time_seconds=backtest_time_seconds,
            )

            return result

    @classmethod
    def run_backtest(
        self,
        datasource_class,
        backtesting_start: datetime = None,
        backtesting_end: datetime = None,
        minutes_before_closing = 5,
        minutes_before_opening = 60,
        sleeptime = 1,
        stats_file = None,
        risk_free_rate = None,
        logfile = None,
        config = None,
        auto_adjust = False,
        name = None,
        budget = None,
        benchmark_asset: str | Asset | None="SPY",
        analyze_backtest: bool = True,
        plot_file_html = None,
        trades_file = None,
        settings_file = None,
        pandas_data: Union[List, Dict[Asset, Data]] = None,
        quote_asset = Asset(symbol="USD", asset_type="forex"),
        starting_positions = None,
        show_plot = None,
        tearsheet_file = None,
        save_tearsheet = True,
        show_tearsheet = None,
        parameters = {},
        buy_trading_fees = [],
        sell_trading_fees = [],
        buy_trading_slippages = [],
        sell_trading_slippages = [],
        polygon_api_key = None,
        use_other_option_source = False,
        thetadata_username = None,
        thetadata_password = None,
        indicators_file = None,
        show_indicators = None,
        save_logfile = False,
        use_quote_data = False,
        show_progress_bar = True,
        quiet_logs = False,
        trader_class = Trader,
        include_cash_positions=False,
        save_stats_file = True,
        resample_rule = None,  # Add resample_rule parameter
        bar_type = "volume",  # Bar type for HFT tearsheet: 'volume', 'dollar', 'imbalance', 'runs', 'time', 'auto'
        **kwargs,
    ):
        """Backtest a strategy.

        Parameters
        ----------
        datasource_class : class
            The datasource class to use. For example, if you want to use the yahoo finance datasource,
            then you would pass YahooDataBacktesting as the datasource_class.
        backtesting_start : datetime
            The start date of the backtesting period.
        backtesting_end : datetime
            The end date of the backtesting period.
        minutes_before_closing : int
            The number of minutes before closing that the minutes_before_closing strategy method will be called.
        minutes_before_opening : int
            The number of minutes before opening that the minutes_before_opening strategy method will be called.
        sleeptime : int
            The number of seconds to sleep between each iteration of the backtest.
        stats_file : str
            The file to write the stats to.
        risk_free_rate : float
            The risk-free rate to use.
        logfile : str
            The file to write the log to.
        config : dict
            The config to use to set up the brokers in live trading.
        auto_adjust : bool
            Whether to automatically adjust the strategy.
        name : str
            The name of the strategy.
        budget : float
            The initial budget to use for the backtest.
        benchmark_asset : str or Asset or None
            The benchmark asset to use for the backtest to compare to. If it is a string then it will be converted
            to a stock Asset object. If it is None, no benchmarking will occur.
        analyze_backtest: bool = True
            Run the backtest_analysis method on the strategy.
        plot_file_html : str
            The file to write the plot html to.
        trades_file : str
            The file to write the trades to.
        pandas_data : list
            A list of Data objects that are used when the datasource_class object is set to PandasDataBacktesting.
            This contains all the data that will be used in backtesting.
        quote_asset : Asset (crypto)
            An Asset object for the cryptocurrency that will get used
            as a valuation asset for measuring overall porfolio values.
            Usually USDT, USD, USDC.
        starting_positions : dict
            A dictionary of starting positions for each asset. For example,
            if you want to start with $100 of SPY, and $200 of AAPL, then you
            would pass in starting_positions={'SPY': 100, 'AAPL': 200}.
        show_plot : bool
            Whether to show the plot.
        show_tearsheet : bool
            Whether to show the tearsheet.
        save_tearsheet : bool
            Whether to save the tearsheet.
        parameters : dict
            A dictionary of parameters to pass to the strategy. These parameters
            must be set up within the initialize() method.
        buy_trading_fees : list of TradingFee objects
            A list of TradingFee objects to apply to the buy orders during backtests.
        sell_trading_fees : list of TradingFee objects
            A list of TradingFee objects to apply to the sell orders during backtests.
        buy_trading_slippages : list of TradingSlippage objects
            Slippage amounts to apply to buy SMART_LIMIT fills when no per-order slippage is provided.
        sell_trading_slippages : list of TradingSlippage objects
            Slippage amounts to apply to sell SMART_LIMIT fills when no per-order slippage is provided.
        polygon_api_key : str
            The polygon api key to use for polygon data. Only required if you are using PolygonDataBacktesting as
            the datasource_class.
        indicators_file : str
            The file to write the indicators to.
        show_indicators : bool
            Whether to show the indicators plot.
        save_logfile : bool
            Whether to save the logfile. Defaults to False. If True, the logfile will be saved to the logs directory. Turning on this option will slow down the backtest.
        use_quote_data : bool
            Whether to use quote data for the backtest. Defaults to False. If True, the backtest will use quote data for the backtest. (Currently this is specific to ThetaData)
            When set to true this requests Quote data in addition to OHLC which adds time to backtests.
        show_progress_bar : bool
            Whether to show the progress bar during the backtest. Defaults to True.
        quiet_logs : bool
            Whether to quiet the logs during the backtest. Defaults to True.
        trader_class : class
            The class to use for the trader. Defaults to Trader.

        Returns
        -------
        tuple of (dict, Strategy)
            A tuple of the analysis dictionary and the strategy object. The analysis dictionary contains the
            analysis of the strategy returns. The strategy object is the strategy object that was backtested, where 
            you can access the strategy returns and other attributes.

        Examples
        --------

        >>> from datetime import datetime
        >>> from lumibot.backtesting import YahooDataBacktesting
        >>> from lumibot.strategies import Strategy
        >>>
        >>> # A simple strategy that buys AAPL on the first day
        >>> class MyStrategy(Strategy):
        >>>    def on_trading_iteration(self):
        >>>        if self.first_iteration:
        >>>            order = self.create_order("AAPL", quantity=1, side="buy")
        >>>            self.submit_order(order)
        >>>
        >>> # Create a backtest
        >>> backtesting_start = datetime(2018, 1, 1)
        >>> backtesting_end = datetime(2018, 1, 31)
        >>>
        >>> # The benchmark asset to use for the backtest to compare to
        >>> benchmark_asset = Asset(symbol="QQQ", asset_type="stock")
        >>>
        >>> backtest = MyStrategy.backtest(
        >>>     datasource_class=YahooDataBacktesting,
        >>>     backtesting_start=backtesting_start,
        >>>     backtesting_end=backtesting_end,
        >>>     benchmark_asset=benchmark_asset,
        >>> )
        """
        
        # Reset progress bar state at the start of each backtest
        try:
            from lumibot.tools.helpers import reset_progress_bar_state
            reset_progress_bar_state()
        except ImportError:
            # If helpers module isn't available, continue without resetting
            pass

        if name is None:
            name = self.__name__

        self._name = name
        self._analyze_backtest = analyze_backtest

        # Set backtesting_start: priority 1 - passed argument, 2 - BACKTESTING_START env var, 3 - default to 1 year ago
        if backtesting_start is not None:
            pass
        elif BACKTESTING_START is not None:
            backtesting_start = BACKTESTING_START
        else:
            backtesting_start = datetime.datetime.now() - datetime.timedelta(days=365)
            get_logger(__name__).warning(
            colored(
                "backtesting_start is set to one year ago by default. You can set it to a specific date by passing in the backtesting_start parameter or by setting the BACKTESTING_START environment variable.",
                "yellow"
            )
            )

        # Set backtesting_end: priority 1 - passed argument, 2 - BACKTESTING_END env var, 3 - default to yesterday
        if backtesting_end is not None:
            pass
        elif BACKTESTING_END is not None:
            backtesting_end = BACKTESTING_END
        else:
            backtesting_end = datetime.datetime.now() - datetime.timedelta(days=1)
            get_logger(__name__).warning(
            colored(
                "backtesting_end is set to the current date by default. You can set it to a specific date by passing in the backtesting_end parameter or by setting the BACKTESTING_END environment variable.",
                "yellow"
            )
            )

        # Create an adapter with 'strategy_name' set to the instance's name
        if not hasattr(self, "logger") or self.logger is None:
            self.logger = get_strategy_logger(__name__, self._name)

        # If show_plot is None, then set it to True
        if show_plot is None:
            show_plot = SHOW_PLOT

        # If show_tearsheet is None, then set it to True
        if show_tearsheet is None:
            show_tearsheet = SHOW_TEARSHEET

        # If show_indicators is None, then set it to True
        if show_indicators is None:
            show_indicators = SHOW_INDICATORS

        from lumibot.credentials import BACKTESTING_DATA_SOURCE as _DEFAULT_BACKTESTING_DATA_SOURCE

        # Determine whether an environment override exists. When BACKTESTING_DATA_SOURCE
        # is set (and not blank/\"none\"), it should take precedence even if a
        # datasource_class argument was provided.
        env_override_raw = os.environ.get("BACKTESTING_DATA_SOURCE")
        env_override_name = None

        if env_override_raw is not None:
            trimmed = env_override_raw.strip()
            if trimmed and trimmed.lower() != "none":
                if trimmed.startswith("{"):
                    raise ValueError(
                        "JSON routing maps are not supported in this equity-only build. "
                        "Set BACKTESTING_DATA_SOURCE to one of: 'yahoo', 'alpaca', 'ibkr'."
                    )
                env_override_name = trimmed.lower()
        elif datasource_class is None:
            env_override_name = _DEFAULT_BACKTESTING_DATA_SOURCE.lower()

        if env_override_name is not None:
            datasource_map = {
                "yahoo": YahooDataBacktesting,
                "alpaca": AlpacaBacktesting,
                "ibkr": InteractiveBrokersRESTBacktesting,
                "interactivebrokersrest": InteractiveBrokersRESTBacktesting,
                "interactive_brokers_rest": InteractiveBrokersRESTBacktesting,
            }

            if env_override_name not in datasource_map:
                label = env_override_raw or _DEFAULT_BACKTESTING_DATA_SOURCE
                raise ValueError(
                    f"Unknown BACKTESTING_DATA_SOURCE: '{label}'. "
                    f"Valid options: {list(datasource_map.keys())}"
                )

            datasource_class = datasource_map[env_override_name]

            label = env_override_raw or _DEFAULT_BACKTESTING_DATA_SOURCE
            get_logger(__name__).info(colored(
                f"Using BACKTESTING_DATA_SOURCE setting for backtest data: {label}",
                "green"
            ))
        elif datasource_class is None:
            raise ValueError(
                "No backtesting data source provided. Set BACKTESTING_DATA_SOURCE in the environment "
                "or pass datasource_class when calling backtest()."
            )

        # dict-based multi-provider routing (options/stock split) is not supported
        if isinstance(datasource_class, dict):
            raise NotImplementedError(
                "Multi-provider datasource routing (dict with 'STOCK'/'OPTION' keys) is not supported "
                "in this equity-only build. Pass a single datasource class directly."
            )
        optionsource_class = None
        use_other_option_source = False

        # Make a string with 6 random numbers/letters (upper and lowercase) to avoid overwriting
        random_string = "".join(random.choices(string.ascii_letters + string.digits, k=6))

        datestring = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M")
        base_filename = f"{name + '_' if name is not None else ''}{datestring}_{random_string}"

        logdir = "logs"
        env_save_logfile = os.environ.get("SAVE_LOGFILE")
        if env_save_logfile is not None:
            normalized = env_save_logfile.strip().lower()
            if normalized in ("true", "1", "yes", "y"):
                save_logfile = True
            elif normalized in ("false", "0", "no", "n"):
                save_logfile = False
        if logfile is None and save_logfile:
            logfile = f"{logdir}/{base_filename}_logs.csv"
        if stats_file is None and save_stats_file:
            stats_file = f"{logdir}/{base_filename}_stats.csv"

        # #############################################
        # Check the data types of the parameters
        # #############################################

        # Check datasource_class
        if not isinstance(datasource_class, type):
            raise ValueError(f"`datasource_class` must be a class. You passed in {datasource_class}")

        # Check optionsource_class
        if use_other_option_source and not isinstance(optionsource_class, type):
            raise ValueError(f"`optionsource_class` must be a class. You passed in {optionsource_class}")

        self.verify_backtest_inputs(backtesting_start, backtesting_end)

        alpaca_api_key = kwargs.get('alpaca_api_key', None) if kwargs.get('alpaca_api_key', None) is not None else ALPACA_CONFIG["API_KEY"]
        alpaca_secret_key = kwargs.get('alpaca_secret_key', None) if kwargs.get('alpaca_secret_key', None) is not None else ALPACA_CONFIG["API_SECRET"]
        if datasource_class == AlpacaBacktesting and (alpaca_api_key is None and alpaca_secret_key is None):
            raise ValueError(
                "Please set `ALPACA_API_KEY`, `ALPACA_API_SECRET`, and `ALPACA_IS_PAPER` to your API key from alpaca.markets "
                "as an environment variable if you are using AlpacaBacktesting. If you don't have one, you can get a free API key "
                "from https://alpaca.markets/."
            )            
        if not self.IS_BACKTESTABLE:
            get_logger(__name__).warning(f"Strategy {name + ' ' if name is not None else ''}cannot be " f"backtested at the moment")
            return None

        try:
            backtesting_start = to_datetime_aware(backtesting_start)
            backtesting_end = to_datetime_aware(backtesting_end)
        except AttributeError:
            get_logger(__name__).error(
                "`backtesting_start` and `backtesting_end` must be datetime objects. \n"
                "You are receiving this error most likely because you are using \n"
                "the original positional arguments for backtesting. \n\n"
            )
            return None

        backtesting_start, backtesting_end = self.verify_backtest_inputs(backtesting_start, backtesting_end)

        get_logger(__name__).info("Backtest start = %s", backtesting_start)
        get_logger(__name__).info("Backtest end = %s", backtesting_end)

        if not self.IS_BACKTESTABLE:
            get_logger(__name__).warning(f"Strategy {name + ' ' if name is not None else ''}cannot be " f"backtested at the moment")
            return None

        if BACKTESTING_QUIET_LOGS is not None:
            quiet_logs = BACKTESTING_QUIET_LOGS

        if BACKTESTING_SHOW_PROGRESS_BAR is not None:
            show_progress_bar = BACKTESTING_SHOW_PROGRESS_BAR

        self._trader = trader_class(logfile=logfile, backtest=True, quiet_logs=quiet_logs)

        if datasource_class == AlpacaBacktesting:
            if all(k in kwargs.keys() for k in ["alpaca_api_key", "alpaca_secret_key"]):
                api_key = kwargs.pop('alpaca_api_key')
                secret_key =  kwargs.pop("alpaca_secret_key")
            else:
                api_key = ALPACA_CONFIG['API_KEY']
                secret_key = ALPACA_CONFIG['API_SECRET']

            data_source = datasource_class(
                backtesting_start,
                backtesting_end,
                config=config,
                alpaca_api_key=api_key,
                alpaca_secret_key=secret_key,
                auto_adjust=auto_adjust,
                show_progress_bar=show_progress_bar,
                pandas_data=pandas_data,
                max_memory=ALPACA_MAX_MEMORY_BYTES,
                **kwargs
            )
        elif datasource_class == InteractiveBrokersRESTBacktesting:
            data_source = datasource_class(
                backtesting_start,
                backtesting_end,
                config=config,
                auto_adjust=auto_adjust,
                pandas_data=pandas_data,
                show_progress_bar=show_progress_bar,
                log_backtest_progress_to_file=LOG_BACKTEST_PROGRESS_TO_FILE,
                **kwargs,
            )
        else:
            data_source = datasource_class(
                datetime_start=backtesting_start,
                datetime_end=backtesting_end,
                config=config,
                auto_adjust=auto_adjust,
                pandas_data=pandas_data,
                show_progress_bar=show_progress_bar,
                log_backtest_progress_to_file=LOG_BACKTEST_PROGRESS_TO_FILE,
                **kwargs,
            )

        if not use_other_option_source:
            backtesting_broker = BacktestingBroker(data_source)
        else:
            options_source = optionsource_class(
                backtesting_start,
                backtesting_end,
                config=config,
                auto_adjust=auto_adjust,
                pandas_data=pandas_data,
                show_progress_bar=show_progress_bar,
                **kwargs,
            )
            backtesting_broker = BacktestingBroker(data_source, options_source)

        strategy = self(
            backtesting_broker,
            minutes_before_closing=minutes_before_closing,
            minutes_before_opening=minutes_before_opening,
            sleeptime=sleeptime,
            risk_free_rate=risk_free_rate,
            stats_file=stats_file,
            benchmark_asset=benchmark_asset,
            analyze_backtest=analyze_backtest,
            backtesting_start=backtesting_start,
            backtesting_end=backtesting_end,
            pandas_data=pandas_data,
            quote_asset=quote_asset,
            starting_positions=starting_positions,
            name=name,
            budget=budget,
            parameters=parameters,
            buy_trading_fees=buy_trading_fees,
            sell_trading_fees=sell_trading_fees,
            buy_trading_slippages=buy_trading_slippages,
            sell_trading_slippages=sell_trading_slippages,
            save_logfile=save_logfile,
            include_cash_positions=include_cash_positions,
            **kwargs,
        )
        self._trader.add_strategy(strategy)

        self.logger.info("Starting backtest...")
        try:
            strategy._backtest_time_start_monotonic = time.monotonic()
        except Exception:
            pass
        start = datetime.datetime.now()

        result = self._trader.run_all(
            show_plot=show_plot,
            show_tearsheet=show_tearsheet,
            save_tearsheet=save_tearsheet,
            show_indicators=show_indicators,
            tearsheet_file=tearsheet_file,
            base_filename=base_filename,
            resample_rule=resample_rule,  # Pass the resample_rule parameter
            bar_type=bar_type,  # Pass the bar_type parameter for HFT tearsheet
        )

        end = datetime.datetime.now()
        backtesting_length = backtesting_end - backtesting_start
        backtesting_run_time = end - start
        self.logger.info(
            f"Backtest took {backtesting_run_time} for a speed of {backtesting_run_time / backtesting_length:,.3f}"
        )

        return result[name], strategy

    def write_backtest_settings(self, settings_file):
        """
        Redefined in the Strategy class to that it has access to all the needed variables.
        """
        pass

    def backtest_analysis(
        self,
        logdir=None,
        show_plot=True,
        show_tearsheet=True,
        show_indicators=True,
        save_tearsheet=True,
        plot_file_html=None,
        tearsheet_file=None,
        trades_file=None,
        trade_events_file=None,
        settings_file=None,
        indicators_file=None,
        tearsheet_csv_file=None,
        base_filename=None,
        resample_rule=None,  # Add resample_rule parameter
        bar_type="volume",  # Bar type for HFT tearsheet: 'volume', 'dollar', 'imbalance', 'runs', 'time', 'auto'
    ):
        if not self._analyze_backtest:
            return

        if not base_filename:
            base_filename = self._name

        # Filename defaults
        if not logdir:
            logdir = "logs"

        if not plot_file_html:
            plot_file_html = f"{logdir}/{base_filename}_trades.html"
        if not trades_file:
            trades_file = f"{logdir}/{base_filename}_trades.csv"
        if not trade_events_file:
            # Full trade-event export (includes optional `audit.*` telemetry when LUMIBOT_BACKTEST_AUDIT=1).
            # `plot_returns()` intentionally writes a simplified `_trades.csv` for UI/quick review, so
            # we keep the full event stream in a separate artifact for investigations.
            trade_events_file = f"{logdir}/{base_filename}_trade_events.csv"
        if not tearsheet_file:
            tearsheet_file = f"{logdir}/{base_filename}_tearsheet.html"
        if not settings_file:
            settings_file = f"{logdir}/{base_filename}_settings.json"
        if not indicators_file:
            indicators_file = f"{logdir}/{base_filename}_indicators.html"
        if not tearsheet_csv_file:
            tearsheet_csv_file = f"{logdir}/{base_filename}_tearsheet.csv"

        # Create the directory if it doesn't exist
        if not os.path.exists(logdir):
            os.makedirs(logdir)

        # Write the backtest settings
        try:
            start_ts = getattr(self, "_backtest_time_start_monotonic", None)
            if start_ts is not None:
                self._backtest_time_seconds = time.monotonic() - float(start_ts)
        except Exception:
            # Never fail analysis due to timing metadata.
            pass

        self.write_backtest_settings(settings_file)

        backtesting_broker = self.broker
        backtesting_broker.export_trade_events_to_csv(trade_events_file)
        # Preserve legacy behavior: if plots are disabled, the simplified `_trades.csv` won't be
        # generated by `plot_returns()`, so export the events there too.
        if not show_plot:
            backtesting_broker.export_trade_events_to_csv(trades_file)
        self.plot_returns_vs_benchmark(
            plot_file_html,
            backtesting_broker._trade_event_log_df,
            show_plot=show_plot,
        )
        
        # Create chart lines dataframe
        chart_lines_df = pd.DataFrame(self._chart_lines_list)
        # Create chart OHLC dataframe
        chart_ohlc_df = pd.DataFrame(getattr(self, "_chart_ohlc_list", []))
        # Create chart markers dataframe
        chart_markers_df = pd.DataFrame(self._chart_markers_list)

        # Always call plot_indicators when show_indicators; it handles empty data via trade-derived markers + baseline
        if show_indicators:
            plot_indicators(
                plot_file_html=indicators_file,
                chart_markers_df=chart_markers_df,
                chart_lines_df=chart_lines_df,
                chart_ohlc_df=chart_ohlc_df,
                strategy_name=f"{self._log_strat_name()}Strategy Indicators",
                show_indicators=True,
                trades_df=backtesting_broker._trade_event_log_df,
                strategy_df=self._strategy_returns_df,
                initial_budget=getattr(self, "_initial_budget", 1.0),
                benchmark_df=getattr(self, "_benchmark_returns_df", None),
                benchmark_name=str(self._benchmark_asset) if getattr(self, "_benchmark_asset", None) else None,
            )

        tearsheet_result = self.tearsheet(
            save_tearsheet=save_tearsheet,
            tearsheet_file=tearsheet_file,
            show_tearsheet=show_tearsheet,
            resample_rule=resample_rule,  # Pass the resample_rule parameter
            bar_type=bar_type,  # Pass the bar_type parameter for HFT tearsheet
        )

        # Save the result to a csv file
        if tearsheet_result is not None:
            tearsheet_result.to_csv(tearsheet_csv_file)

        return tearsheet_result

    @classmethod
    def verify_backtest_inputs(cls, backtesting_start, backtesting_end):
        """
        Helper function to check that the inputs are set correctly for BackTest.
        Parameters
        ----------
        backtesting_start: datetime.datetime
            The start datetime of the backtesting period.
        backtesting_end: datetime.datetime
            The end datetime of the backtesting period.

        Raises
        -------
        ValueError
            If the inputs are not set correctly.

        Returns
        -------
        tuple[datetime.datetime, datetime.datetime]
            Normalized (timezone-aware) and validated start/end datetimes. If the provided
            end datetime is in the future, it is clamped to the current time.
        """
        # Check backtesting_start and backtesting_end
        if not isinstance(backtesting_start, datetime.datetime):
            raise ValueError(f"`backtesting_start` must be a datetime object. You passed in {backtesting_start}")

        if not isinstance(backtesting_end, datetime.datetime):
            raise ValueError(f"`backtesting_end` must be a datetime object. You passed in {backtesting_end}")

        start_dt = cls._normalize_backtest_datetime(backtesting_start)
        end_dt = cls._normalize_backtest_datetime(backtesting_end)

        # Check that backtesting end is after backtesting start
        if end_dt <= start_dt:
            raise ValueError(
                f"`backtesting_end` must be after `backtesting_start`. You passed in "
                f"{end_dt} and {start_dt}"
            )

        # If backtesting_end is in the future, clamp it to now. This avoids hard failures when
        # callers specify a "future" end date (e.g., tomorrow) and expect the backtest to stop
        # at the most recent available data.
        now = datetime.datetime.now(end_dt.tzinfo) if end_dt.tzinfo else datetime.datetime.now()
        if end_dt > now:
            get_logger(__name__).warning(
                "`backtesting_end` is in the future (%s > %s). Clamping to %s.",
                end_dt,
                now,
                now,
            )
            end_dt = now

        # After clamping, ensure end is still after start.
        if end_dt <= start_dt:
            raise ValueError(
                f"`backtesting_end` must be after `backtesting_start`. You passed in "
                f"{end_dt} and {start_dt}"
            )

        return start_dt, end_dt

    def send_update_to_cloud(self):
        """
        Sends an update to the LumiWealth cloud server with the current portfolio value, cash, positions, and any outstanding orders.
        There is an API Key that is required to send the update to the cloud.
        The API Key is stored in the environment variable LUMIWEALTH_API_KEY.
        """
        # Check if we are in backtesting mode, if so, don't send the message
        if self.is_backtesting:
            self.logger.debug("Skipping cloud update - in backtesting mode")
            return

        # Check if self.lumiwealth_api_key has been set, if not, return
        if not hasattr(self, "lumiwealth_api_key") or self.lumiwealth_api_key is None or self.lumiwealth_api_key == "":
            # Log that we are not sending the update to the cloud
            if not self._logged_missing_lumiwealth_api_key:
                self.logger.warning("LUMIWEALTH_API_KEY not set. Not sending an update to the cloud because "
                                    "lumiwealth_api_key is not set. If you would like to be able to track your bot "
                                    "performance on www.botspot.trade, please set the lumiwealth_api_key parameter "
                                    "in the strategy initialization or the LUMIWEALTH_API_KEY environment variable.")
                self._logged_missing_lumiwealth_api_key = True
            return

        # Log that we're starting to send data
        self.logger.debug(f"Starting cloud update for strategy '{self._name}' with API key: {self.lumiwealth_api_key[:10]}...")

        # Get the current portfolio value
        try:
            portfolio_value = self.get_portfolio_value()
            self.logger.debug(f"Portfolio value: {portfolio_value}")
        except Exception as e:
            self.logger.error(f"Failed to get portfolio value: {e}")
            self.logger.error(traceback.format_exc())
            return False

        # Get the current cash
        try:
            cash = self.get_cash()
            self.logger.debug(f"Cash: {cash}")
        except Exception as e:
            self.logger.error(f"Failed to get cash: {e}")
            self.logger.error(traceback.format_exc())
            return False

        # Get the current positions
        try:
            positions = self.get_positions()
            self.logger.debug(f"Number of positions: {len(positions)}")
            # DEBUG: Log position details
            for pos in positions:
                self.logger.debug(f"[DEBUG] Position: {pos.symbol}, qty: {pos.quantity}, has_price: {hasattr(pos, 'current_price')}")
                if hasattr(pos, '__dict__'):
                    attrs = {k: v for k, v in pos.__dict__.items() if not k.startswith('_')}
                    self.logger.debug(f"[DEBUG] Position attrs for {pos.symbol}: {list(attrs.keys())}")
        except Exception as e:
            self.logger.error(f"Failed to get positions: {e}")
            self.logger.error(traceback.format_exc())
            return False

        # Get the current orders
        try:
            orders = self.get_orders()
            self.logger.debug(f"Number of orders: {len(orders)}")
        except Exception as e:
            self.logger.error(f"Failed to get orders: {e}")
            self.logger.error(traceback.format_exc())
            return False

        LUMIWEALTH_URL = "https://listener.lumiwealth.com/portfolio_events"

        headers = {
            "x-api-key": f"{self.lumiwealth_api_key}",
            "Content-Type": "application/json",
        }

        # Create the data to send to the cloud
        positions_data = [position.to_dict() for position in positions]

        data = {
            "data_type": "portfolio_event",
            "portfolio_value": portfolio_value,
            "cash": cash,
            "positions": positions_data,
            "orders": [order.to_dict() for order in orders],
            "strategy_name": self._name,
            "broker_name": self.broker.name,
        }

        self.logger.debug(f"Preparing to send portfolio update: value={portfolio_value}, cash={cash}, positions={len(positions)}, orders={len(orders)}")

        # Helper function to recursively replace NaN in dictionaries
        def replace_nan(value):
            if isinstance(value, float) and math.isnan(value):
                return None  # or 0 if you prefer
            elif isinstance(value, dict):
                return {k: replace_nan(v) for k, v in value.items()}
            elif isinstance(value, list):
                return [replace_nan(v) for v in value]
            else:
                return value

        # Apply to your data dictionary
        data = replace_nan(data)

        try:
            # Send the data to the cloud
            json_data = json.dumps(data, default=str)
            data_size_kb = len(json_data.encode('utf-8')) / 1024
            self.logger.debug(f"Sending {data_size_kb:.2f} KB of data to {LUMIWEALTH_URL}")
            self.logger.debug(f"Request headers: {headers}")

            response = requests.post(LUMIWEALTH_URL, headers=headers, data=json_data)

            self.logger.debug(f"Cloud response: Status={response.status_code}, Headers={dict(response.headers)}")

        except requests.exceptions.ConnectionError as e:
            self.logger.info(f"Connection error when sending to cloud: {e}", exc_info=True)
            return False
        except requests.exceptions.Timeout as e:
            self.logger.info(f"Timeout error when sending to cloud: {e}", exc_info=True)
            return False
        except requests.exceptions.RequestException as e:
            self.logger.info(f"Request error when sending to cloud: {e}", exc_info=True)
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error when sending to cloud: {e}")
            self.logger.error(traceback.format_exc())
            return False

        # Check if the message was sent successfully
        if response.status_code == 200:
            self.logger.debug(f"Portfolio update sent successfully to cloud for strategy '{self._name}'")
            return True
        elif response.status_code == 401:
            self.logger.error(f"❌ Authentication failed - Invalid API key: {self.lumiwealth_api_key[:10]}...")
            self.logger.error(f"Response: {response.text}")
            return False
        elif response.status_code == 400:
            self.logger.error("❌ Bad request - Invalid data format")
            self.logger.error(f"Response: {response.text}")
            return False
        elif response.status_code == 413:
            self.logger.error(f"❌ Payload too large ({data_size_kb:.2f} KB)")
            self.logger.error(f"Response: {response.text}")
            return False
        else:
            self.logger.error(
                f"❌ Failed to send update to cloud. Status: {response.status_code}, Response: {response.text}"
            )
            return False

    def should_send_account_summary_to_discord(self):
        # Check if db_connection_str has been set, if not, return False
        if not hasattr(self, "db_connection_str"):
            # Log that we are not sending the account summary to Discord
            self.logger.info(
                "Not sending account summary to Discord because self does not have db_connection_str attribute")
            return False

        if self.db_connection_str is None or self.db_connection_str == "":
            # Log that we are not sending the account summary to Discord
            self.logger.debug("Not sending account summary to Discord because db_connection_str is not set")
            return False

        # Check if discord_webhook_url has been set, if not, return False
        if not self.discord_webhook_url or self.discord_webhook_url == "":
            # Log that we are not sending the account summary to Discord
            self.logger.info("Not sending account summary to Discord because discord_webhook_url is not set")
            return False

        # Check if should_send_summary_to_discord has been set, if not, return False
        if not self.should_send_summary_to_discord:
            # Log that we are not sending the account summary to Discord
            self.logger.info(
                f"Not sending account summary to Discord because should_send_summary_to_discord is False or not set. The value is: {self.should_send_summary_to_discord}")
            return False

        # Check if last_account_summary_dt has been set, if not, set it to None
        if not hasattr(self, "last_account_summary_dt"):
            self.last_account_summary_dt = None

        # Get the current datetime
        now = datetime.datetime.now()

        # Calculate the time since the last account summary if it has been set
        if self.last_account_summary_dt is not None:
            time_since_last_account_summary = now - self.last_account_summary_dt
        else:
            time_since_last_account_summary = None

        # Check if it has been at least 24 hours since the last account summary
        if self.last_account_summary_dt is None or time_since_last_account_summary.total_seconds() >= 86400: # 24 hours
            # Set the last account summary datetime to now
            self.last_account_summary_dt = now

            # Sleep for 5 seconds to make sure all the orders go through first
            time.sleep(5)

            # Return True because we should send the account summary to Discord
            return True

        else:
            # Log that we are not sending the account summary to Discord
            self.logger.info(f"Not sending account summary to Discord because it has not been at least 24 hours since the last account summary. It is currently {now} and the last account summary was at: {self.last_account_summary_dt}, which was {time_since_last_account_summary} ago.")

            # Return False because we should not send the account summary to Discord
            return False

    # ====== Messaging Methods ========================

    def send_discord_message(self, message, image_buf=None, silent=True):
        """
        Sends a message to Discord
        """

        # Check if we are in backtesting mode, if so, don't send the message
        if self.is_backtesting:
            return

        # Check if the message is empty
        if message == "" or message is None:
            # If the message is empty, log and return
            self.logger.debug("The discord message is empty. Please provide a message to send to Discord.")
            return

        # Check if the discord webhook URL is set
        if self.discord_webhook_url is None or self.discord_webhook_url == "":
            # If the webhook URL is not set, log and return
            self.logger.debug(
                "The discord webhook URL is not set. Please set the discord_webhook_url parameter in the strategy \
                initialization if you want to send messages to Discord."
            )
            return

        # Remove the extra spaces at the beginning of each line
        message = "\n".join(line.lstrip() for line in message.split("\n"))

        # Get the webhook URL from the environment variables
        webhook_url = self.discord_webhook_url

        # The payload for text content
        payload = {"content": message}

        # If silent is true, set the discord message to be silent
        if silent:
            payload["flags"] = [4096]

        # Check if we have an image
        if image_buf is not None:
            # The files that you want to send
            files = {"file": ("results.png", image_buf, "image/png")}

            # Make a POST request to the webhook URL with the payload and file
            response = requests.post(webhook_url, data=payload, files=files)
        else:
            # Make a POST request to the webhook URL with the payload
            response = requests.post(webhook_url, data=payload)

        # Check if the message was sent successfully
        if response.status_code == 200 or response.status_code == 204:
            self.logger.info("Discord message sent successfully.")
        else:
            self.logger.error(
                f"Failed to send message to Discord. Status code: {response.status_code}, message: {response.text}"
            )

    def send_spark_chart_to_discord(self, stats_df, portfolio_value, now, days=1095):
        # Check if we are in backtesting mode, if so, don't send the message
        if self.is_backtesting:
            return

        # Only keep the stats for the past X days
        stats_df = stats_df.loc[stats_df["datetime"] >= (now - pd.Timedelta(days=days))]

        # Set the default color
        color = "black"

        # Check what return we made over the past week
        if stats_df.shape[0] > 0:
            # Resanple the stats dataframe to daily but keep the datetime column
            stats_df = stats_df.resample("D", on="datetime").last().reset_index()

            # Drop the cash column because it's not needed
            stats_df = stats_df.drop(columns=["cash"])

            # Remove nan values
            stats_df = stats_df.dropna()

            # Get the portfolio value at the beginning of the dataframe
            portfolio_value_start = stats_df.iloc[0]["portfolio_value"]

            # Calculate the return over the past 7 days
            total_return = ((portfolio_value / portfolio_value_start) - 1) * 100

            # Check if we made a positive return, if so, set the color to green, otherwise set it to red
            if total_return > 0:
                color = "green"
            else:
                color = "red"

        # Plotting the DataFrame
        plt.figure()

        # Create an axes instance, setting the facecolor to white
        ax = plt.axes(facecolor="white")

        # Convert 'datetime' to Matplotlib's numeric format right after cleaning
        stats_df['mpl_datetime'] = mdates.date2num(stats_df['datetime'])

        # Plotting with a thicker line
        ax = stats_df.plot(
            x="mpl_datetime",
            y="portfolio_value",
            kind="line",
            linewidth=5,
            color=color,
            # label="Account Value",
            ax=ax,
            legend=False,
        )
        plt.title(f"{self._name} Account Value", fontsize=32, pad=60)
        plt.xlabel("")
        plt.ylabel("")

        # # Increase the font size of the tick labels
        # ax.tick_params(axis="both", which="major", labelsize=18)

        # Use a custom formatter for currency
        formatter = ticker.FuncFormatter(lambda x, pos: f"${int(x):1,}")
        ax.yaxis.set_major_formatter(formatter)

        # Custom formatter function
        def custom_date_formatter(x, pos):
            try:
                date = mdates.num2date(x)
                if pos % 2 == 0:  # Every second tick
                    return date.strftime("%d\n%b\n%Y")
                else:  # Other ticks
                    return date.strftime("%d")
            except Exception:
                return ""

        # Set the locator for the x-axis to automatically find the dates
        locator = mdates.AutoDateLocator(minticks=3, maxticks=7)
        ax.xaxis.set_major_locator(locator)

        # Use custom formatter for the x-axis
        ax.xaxis.set_major_formatter(ticker.FuncFormatter(custom_date_formatter))

        # Use the ConciseDateFormatter to format the x-axis dates
        formatter = mdates.ConciseDateFormatter(locator)

        # Increase the font size of the tick labels
        ax.tick_params(axis="x", which="major", labelsize=18, rotation=0)  # For x-axis
        ax.tick_params(axis="y", which="major", labelsize=18)  # For y-axis

        # Center align x-axis labels
        for label in ax.get_xticklabels():
            label.set_horizontalalignment("center")

        # Save the plot to an in-memory file
        buf = io.BytesIO()
        plt.savefig(buf, format="png", bbox_inches="tight", pad_inches=0.25)
        buf.seek(0)

        # Send the image to Discord
        self.send_discord_message("-----------\n", buf)

    def send_result_text_to_discord(self, returns_text, portfolio_value, cash):
        # Check if we are in backtesting mode, if so, don't send the message
        if self.is_backtesting:
            return

        # Check if we should hide positions
        if self.hide_positions:
            # Log that we are hiding positions in the account summary
            self.logger.info("Hiding positions because hide_positions is set to True")

            # Set the positions text to hidden
            positions_text = "Positions are hidden"
        else:
            # Get the current positions
            positions = self.get_positions()

            # Log the positions
            self.logger.info(f"Positions for send_result_text_to_discord: {positions}")

            # Create the positions text
            positions_details_list = []
            for position in positions:
                # Check if the position asset is the quote asset

                if position.asset == self._quote_asset:
                    last_price = 1
                else:
                    # Get the last price
                    last_price = self.get_last_price(position.asset)

                # Make sure last_price is a number
                if last_price is None or not isinstance(last_price, (int, float, Decimal)):
                    self.logger.info(f"Last price for {position.asset} is not a number: {last_price}")
                    continue

                # Calculate the value of the position
                position_value = position.quantity * last_price

                # If option, multiply % of portfolio by 100
                if position.asset.asset_type == "option":
                    position_value = position_value * 100

                if position_value > 0 and portfolio_value > 0:
                    # Calculate the percent of the portfolio that this position represents
                    percent_of_portfolio = position_value / portfolio_value
                else:
                    percent_of_portfolio = 0

                # Add the position details to the list
                positions_details_list.append(
                    {
                        "asset": position.asset,
                        "quantity": position.quantity,
                        "value": position_value,
                        "percent_of_portfolio": percent_of_portfolio,
                    }
                )

            # Sort the positions by the percent of the portfolio
            positions_details_list = sorted(positions_details_list, key=lambda x: x["percent_of_portfolio"], reverse=True)

            # Create the positions text
            positions_text = ""
            for position in positions_details_list:
                # positions_text += f"{position.quantity:,.2f} {position.asset} (${position.value:,.0f} or {position.percent_of_portfolio:,.0%})\n"
                positions_text += (
                    f"{position['quantity']:,.2f} {position['asset']} (${position['value']:,.0f} or {position['percent_of_portfolio']:,.0%})\n"
                )

        # Create a message to send to Discord (round the values to 2 decimal places)
        cash_str = f"{cash:,.2f}" if cash is not None else "N/A"
        portfolio_value_str = f"{portfolio_value:,.2f}" if portfolio_value is not None else "N/A"
        message = f"""
                **Update for {self._name}**
                **Account Value:** ${portfolio_value_str}
                **Cash:** ${cash_str}
                {returns_text}
                **Positions:**
                {positions_text}
                """

        # Remove any leading whitespace
        # Remove the extra spaces at the beginning of each line
        message = "\n".join(line.lstrip() for line in message.split("\n"))

        # Add self.discord_account_summary_footer to the message
        if hasattr(self, "discord_account_summary_footer") and self.discord_account_summary_footer is not None:
            message += f"{self.discord_account_summary_footer}\n\n"

        # Add powered by Lumiwealth to the message
        message += "[**Powered by 💡 Lumiwealth**](<https://lumiwealth.com>)\n-----------"

        # Send the message to Discord
        self.send_discord_message(message, None)

    def send_account_summary_to_discord(self):
        # Log that we are sending the account summary to Discord
        self.logger.debug("Considering sending account summary to Discord")

        # Check if we are in backtesting mode, if so, don't send the message
        if self.is_backtesting:
            # Log that we are not sending the account summary to Discord
            self.logger.debug("Not sending account summary to Discord because we are in backtesting mode")
            return

        # Check if last_account_summary_dt has been set, if not, set it to None
        if not hasattr(self, "last_account_summary_dt"):
            self.last_account_summary_dt = None

        # Check if we should send an account summary to Discord
        should_send_account_summary = self.should_send_account_summary_to_discord()
        if not should_send_account_summary:
            # Log that we are not sending the account summary to Discord
            return

        # Log that we are sending the account summary to Discord
        self.logger.info("Sending account summary to Discord")

        # Get the current portfolio value
        portfolio_value = self.get_portfolio_value()

        # Get the current cash
        cash = self.get_cash()

        # # Get the datetime
        now = pd.Timestamp(datetime.datetime.now()).tz_localize(LUMIBOT_DEFAULT_PYTZ)

        # Get the returns
        returns_text, stats_df = self.calculate_returns()

        # Send a spark chart to Discord
        self.send_spark_chart_to_discord(stats_df, portfolio_value, now)

        # Send the results text to Discord
        self.send_result_text_to_discord(returns_text, portfolio_value, cash)

    def get_stats_from_database(self, stats_table_name, retries=5, delay=5):
        attempt = 0
        while attempt < retries:
            try:
                # Create or verify the database connection
                if not hasattr(self, 'db_engine') or not self.db_engine:
                    self.db_engine = create_engine(self.db_connection_str)
                else:
                    # Verify the connection
                    with self.db_engine.connect() as conn:
                        conn.execute(text("SELECT 1"))

                # Check if the table exists
                if not inspect(self.db_engine).has_table(stats_table_name):
                    # Log that the table does not exist and we are creating it
                    self.logger.info(f"Table {stats_table_name} does not exist. Creating it now.")

                    # Get the current time in New York
                    ny_tz = LUMIBOT_DEFAULT_PYTZ
                    now = datetime.datetime.now(ny_tz)

                    # Create an empty stats dataframe
                    stats_new = pd.DataFrame(
                        {
                            "id": [str(uuid.uuid4())],
                            "datetime": [now],
                            "portfolio_value": [0.0],  # Default or initial value
                            "cash": [0.0],             # Default or initial value
                            "strategy_id": ["INITIAL VALUE"], # Default or initial value
                        }
                    )

                    # Set the index
                    stats_new.set_index("id", inplace=True)

                    # Create the table by saving this empty DataFrame to the database
                    self.to_sql(stats_new, stats_table_name, if_exists='replace', index=True)

                # Load the stats dataframe from the database
                stats_df = pd.read_sql_table(stats_table_name, self.db_engine)
                return stats_df

            except OperationalError as e:
                self.logger.error(f"OperationalError: {e}")
                attempt += 1
                if attempt < retries:
                    self.logger.info(f"Retrying in {delay} seconds and recreating db_engine...")
                    time.sleep(delay)
                    self.db_engine = create_engine(self.db_connection_str)  # Recreate the db_engine
                else:
                    self.logger.error("Max retries reached for get_stats_from_database. Failing operation.")
                    raise

    def to_sql(self, stats_df, stats_table_name, if_exists='replace', index=True, retries=5, delay=5):
        attempt = 0
        while attempt < retries:
            try:
                stats_df.to_sql(stats_table_name, self.db_engine, if_exists=if_exists, index=index)
                return
            except OperationalError as e:
                self.logger.error(f"OperationalError during to_sql: {e}")
                attempt += 1
                if attempt < retries:
                    self.logger.info(f"Retrying in {delay} seconds and recreating db_engine...")
                    time.sleep(delay)
                    self.db_engine = create_engine(self.db_connection_str)  # Recreate the db_engine
                else:
                    self.logger.error("Max retries reached for to_sql. Failing operation.")
                    raise

    def backup_variables_to_db(self):
        if self.is_backtesting:
            return

        if not hasattr(self, "db_connection_str") or self.db_connection_str is None or self.db_connection_str == "" or not self.should_backup_variables_to_database:
            return

        # Ensure we have a self.db_engine
        if not hasattr(self, 'db_engine') or not self.db_engine:
            self.db_engine = create_engine(self.db_connection_str)

        # Get the current time in New York
        ny_tz = LUMIBOT_DEFAULT_PYTZ
        now = datetime.datetime.now(ny_tz)

        if not inspect(self.db_engine).has_table(self.backup_table_name):
            # Log that the table does not exist and we are creating it
            self.logger.info(f"Table {self.backup_table_name} does not exist. Creating it now.")

            # Create an empty stats dataframe
            stats_new = pd.DataFrame(
                {
                    "id": [str(uuid.uuid4())],
                    "last_updated": [now],
                    "variables": ["INITIAL VALUE"],
                    "strategy_id": ["INITIAL VALUE"]
                }
            )

            # Set the index
            stats_new.set_index("id", inplace=True)

            # Create the table by saving this empty DataFrame to the database
            stats_new.to_sql(self.backup_table_name, self.db_engine, if_exists='replace', index=True)

        current_state = json.dumps(self.vars.all(), sort_keys=True, cls=SafeJSONEncoder)
        if current_state == self._last_backup_state:
            self.logger.info("No variables changed. Not backing up.")
            return

        try:
            data_to_save = self.vars.all()
            if data_to_save:
                json_data_to_save = json.dumps(data_to_save, cls=SafeJSONEncoder)
                with self.db_engine.connect() as connection:
                    with connection.begin():
                        # Check if the row exists
                        check_query = text(f"""
                            SELECT 1 FROM {self.backup_table_name} WHERE strategy_id = :strategy_id
                        """)
                        result = connection.execute(check_query, {'strategy_id': self._name}).fetchone()

                        if result:
                            # Update the existing row
                            update_query = text(f"""
                                UPDATE {self.backup_table_name}
                                SET last_updated = :last_updated, variables = :variables
                                WHERE strategy_id = :strategy_id
                            """)
                            connection.execute(update_query, {
                                'last_updated': now,
                                'variables': json_data_to_save,
                                'strategy_id': self._name
                            })
                        else:
                            # Insert a new row
                            insert_query = text(f"""
                                INSERT INTO {self.backup_table_name} (id, last_updated, variables, strategy_id)
                                VALUES (:id, :last_updated, :variables, :strategy_id)
                            """)
                            connection.execute(insert_query, {
                                'id': str(uuid.uuid4()),
                                'last_updated': now,
                                'variables': json_data_to_save,
                                'strategy_id': self._name
                            })

                self._last_backup_state = current_state
                self.logger.info("Variables backed up successfully")
            else:
                self.logger.info("No variables to back up")

        except Exception as e:
            self.logger.error(f"Error backing up variables to DB: {e}", exc_info=True)

    def load_variables_from_db(self):
        if self.is_backtesting:
            return
    
        if not hasattr(self, "db_connection_str") or self.db_connection_str is None or not self.should_backup_variables_to_database:
            return
    
        try:
            if not hasattr(self, 'db_engine') or not self.db_engine:
                self.db_engine = create_engine(self.db_connection_str)
    
            # Check if backup table exists
            inspector = inspect(self.db_engine)
            if not inspector.has_table(self.backup_table_name):
                self.logger.info(f"Backup for {self._name} does not exist in the database. Not restoring")
                return
    
            # Query the latest entry from the backup table
            query = text(
                f'SELECT * FROM {self.backup_table_name} WHERE strategy_id = :strategy_id ORDER BY last_updated DESC LIMIT 1'
            )
    
            params = {'strategy_id': self._name}
            df = pd.read_sql_query(query, self.db_engine, params=params)
    
            if df.empty:
                self.logger.debug("No data found in the backup")
                return
    
            json_data = df['variables'].iloc[0]
    
            import re
    
            iso_dt_re = re.compile(r"^\d{4}-\d{2}-\d{2}T")      # datetime prefix
            iso_date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")    # date only
    
            def _coerce_value(v):
                if not isinstance(v, str):
                    return v
    
                # ISO datetime (support trailing Z)
                if iso_dt_re.match(v):
                    try:
                        v2 = v.replace("Z", "+00:00") if v.endswith("Z") else v
                        return datetime.datetime.fromisoformat(v2)
                    except Exception:
                        return v
    
                # ISO date (YYYY-MM-DD)
                if iso_date_re.match(v):
                    try:
                        return datetime.datetime.strptime(v, "%Y-%m-%d").date()
                    except Exception:
                        return v
    
                return v
    
            # Decode any special types we stored using our SafeJSONEncoder,
            # but only parse strings that actually look like ISO dates/datetimes.
            data = json.loads(json_data, object_hook=lambda d: {k: _coerce_value(v) for k, v in d.items()})
    
            # Update self.vars dictionary
            for key, value in data.items():
                self.vars.set(key, value)
    
            current_state = json.dumps(self.vars.all(), sort_keys=True, cls=SafeJSONEncoder)
            self._last_backup_state = current_state
    
            self.logger.info("Variables loaded successfully from database")
    
        except Exception as e:
            self.logger.error(f"Error loading variables from database: {e}", exc_info=True)

    def calculate_returns(self):
        # Check if we are in backtesting mode, if so, don't send the message
        if self.is_backtesting:
            return

        # Calculate the return over the past 24 hours, 7 days, and 30 days using the stats dataframe

        # Get the current time in New York
        ny_tz = LUMIBOT_DEFAULT_PYTZ

        # Get the datetime
        now = datetime.datetime.now(ny_tz)

        # Load the stats dataframe from the database
        stats_df = self.get_stats_from_database(STATS_TABLE_NAME)

        # Only keep the stats for this strategy ID
        stats_df = stats_df.loc[stats_df["strategy_id"] == self.strategy_id]

        # Convert the datetime column to a datetime
        stats_df["datetime"] = pd.to_datetime(stats_df["datetime"])  # , utc=True)

        # Check if the datetime column is timezone-aware
        if stats_df['datetime'].dt.tz is None:
            # If the datetime is timezone-naive, directly localize it to "America/New_York"
            stats_df["datetime"] = stats_df["datetime"].dt.tz_localize(LUMIBOT_DEFAULT_PYTZ, ambiguous='infer')
        else:
            # If the datetime is already timezone-aware, first remove timezone and then localize
            stats_df["datetime"] = stats_df["datetime"].dt.tz_localize(None)
            stats_df["datetime"] = stats_df["datetime"].dt.tz_localize(LUMIBOT_DEFAULT_PYTZ, ambiguous='infer')

        # Get the stats
        stats_new = pd.DataFrame(
            {
                "id": str(uuid.uuid4()),
                "datetime": [now],
                "portfolio_value": [self.get_portfolio_value()],
                "cash": [self.get_cash()],
                "strategy_id": [self.strategy_id],
            }
        )

        # Set the index
        stats_new.set_index("id", inplace=True)

        # Add the new stats to the existing stats
        stats_df = pd.concat([stats_df, stats_new])

        # # Convert the datetime column to eastern time
        stats_df["datetime"] = stats_df["datetime"].dt.tz_convert(LUMIBOT_DEFAULT_PYTZ)

        # Remove any duplicate rows
        stats_df = stats_df[~stats_df["datetime"].duplicated(keep="last")]

        # Sort the stats by the datetime column
        stats_df = stats_df.sort_values("datetime")

        # Set the strategy ID column to be the strategy ID
        stats_df["strategy_id"] = self.strategy_id

        # Index should be a uuid, fill the index with uuids
        stats_df.loc[pd.isna(stats_df["id"]), "id"] = [
            str(uuid.uuid4()) for _ in range(len(stats_df.loc[pd.isna(stats_df["id"])]))
        ]

        # Set id as the index
        stats_df = stats_df.set_index("id")

        # Check that the stats dataframe has at least 1 row and contains the portfolio_value column
        if stats_df.shape[0] > 0 and "portfolio_value" in stats_df.columns:
            # Save the stats to the database
            self.to_sql(stats_new, STATS_TABLE_NAME, "append", index=True)

            # Get the current portfolio value
            portfolio_value = self.get_portfolio_value()

            # Initialize the results
            results_text = ""

            # Add results for the past 24 hours
            # Get the datetime 24 hours ago
            datetime_24_hours_ago = now - pd.Timedelta(days=1)
            # Get the df for the past 24 hours
            stats_past_24_hours = stats_df.loc[stats_df["datetime"] >= datetime_24_hours_ago]
            # Check if there are any stats for the past 24 hours
            if stats_past_24_hours.shape[0] > 0:
                # Get the portfolio value 24 hours ago
                portfolio_value_24_hours_ago = stats_past_24_hours.iloc[0]["portfolio_value"]
                if float(portfolio_value_24_hours_ago) != 0.0:
                    # Calculate the return over the past 24 hours
                    return_24_hours = ((portfolio_value / portfolio_value_24_hours_ago) - 1) * 100
                    # Add the return to the results
                    results_text += f"**24 hour Return:** {return_24_hours:,.2f}% (${(portfolio_value - portfolio_value_24_hours_ago):,.2f} change)\n"

            # Add results for the past 7 days
            # Get the datetime 7 days ago
            datetime_7_days_ago = now - pd.Timedelta(days=7)
            # First check if we have stats that are at least 7 days old
            if stats_df["datetime"].min() < datetime_7_days_ago:
                # Get the df for the past 7 days
                stats_past_7_days = stats_df.loc[stats_df["datetime"] >= datetime_7_days_ago]
                # Check if there are any stats for the past 7 days
                if stats_past_7_days.shape[0] > 0:
                    # Get the portfolio value 7 days ago
                    portfolio_value_7_days_ago = stats_past_7_days.iloc[0]["portfolio_value"]
                    return_7_days = None
                    if float(portfolio_value_7_days_ago) != 0.0:
                        # Calculate the return over the past 7 days
                        return_7_days = ((portfolio_value / portfolio_value_7_days_ago) - 1) * 100
                        # Add the return to the results
                        results_text += f"**7 day Return:** {return_7_days:,.2f}% (${(portfolio_value - portfolio_value_7_days_ago):,.2f} change)\n"

                    # If we are up more than pct_up_threshold over the past 7 days, send a message to Discord
                    PERCENT_UP_THRESHOLD = 3
                    if return_7_days and return_7_days > PERCENT_UP_THRESHOLD:
                        # Create a message to send to Discord
                        message = f"""
                                🚀 {self._name} is up {return_7_days:,.2f}% in 7 days.
                                """

                        # Remove any leading whitespace
                        # Remove the extra spaces at the beginning of each line
                        message = "\n".join(line.lstrip() for line in message.split("\n"))

                        # Send the message to Discord
                        self.send_discord_message(message, silent=False)

            # Add results for the past 30 days
            # Get the datetime 30 days ago
            datetime_30_days_ago = now - pd.Timedelta(days=30)
            # First check if we have stats that are at least 30 days old
            if stats_df["datetime"].min() < datetime_30_days_ago:
                # Get the df for the past 30 days
                stats_past_30_days = stats_df.loc[stats_df["datetime"] >= datetime_30_days_ago]
                # Check if there are any stats for the past 30 days
                if stats_past_30_days.shape[0] > 0:
                    # Get the portfolio value 30 days ago
                    portfolio_value_30_days_ago = stats_past_30_days.iloc[0]["portfolio_value"]
                    if float(portfolio_value_30_days_ago) != 0.0:
                        # Calculate the return over the past 30 days
                        return_30_days = ((portfolio_value / portfolio_value_30_days_ago) - 1) * 100
                        # Add the return to the results
                        results_text += f"**30 day Return:** {return_30_days:,.2f}% (${(portfolio_value - portfolio_value_30_days_ago):,.2f} change)\n"

            # Get inception date
            inception_date = stats_df["datetime"].min()

            # Inception date text
            inception_date_text = f"{inception_date.strftime('%b %d, %Y')}"

            # Add results since inception
            # Get the portfolio value at inception
            portfolio_value_inception = stats_df.iloc[0]["portfolio_value"]
            # Calculate the return since inception
            return_since_inception = ((portfolio_value / portfolio_value_inception) - 1) * 100
            # Add the return to the results
            results_text += f"**Since Inception ({inception_date_text}):** {return_since_inception:,.2f}% (started at ${portfolio_value_inception:,.2f}, now ${portfolio_value - portfolio_value_inception:,.2f} change)\n"

            return results_text, stats_df

        else:
            return "Not enough data to calculate returns", stats_df

    @property
    def cash(self):
        """Returns the current cash. This is the money that is not used for positions or
        orders (in other words, the money that is available to buy new assets, or cash).

        This property is updated whenever a transaction was filled by the broker or when dividends
        are paid.

        Crypto currencies are a form of cash. Therefore cash will always be zero.

        Returns
        -------
        cash : float
            The current cash.

        Example
        -------
        >>> # Get the current cash available in the account
        >>> self.log_message(self.cash)
        """

        self.update_broker_balances(force_update=False)

        cash_position = self.get_position(self._quote_asset)
        quantity = cash_position.quantity if cash_position else None

        # This is not really true:
        # if quantity is None:
        #     self._set_cash_position(0)
        #     quantity = 0

        if type(quantity) is Decimal:
            quantity = float(quantity)
        elif quantity is None: # Ensure we return a float if cash position doesn't exist
            quantity = 0.0

        return quantity

    def get_historical_trades(
        self,
        asset,
        quote=None,
        data_datetime_start=None,
        data_datetime_end=None,
    ):
        """Get historical trades data for an asset or list of assets.
        
        Parameters
        ----------
        asset : Asset, str, or list of Asset/str
            The asset(s) to get historical trades for. Pass a list for batch fetch
            (parallel downloading when supported by the data source).
        quote : Asset or str, optional
            The quote asset for pricing. If None, uses the default quote asset.
        data_datetime_start : datetime, optional
            Start date of the data to retrieve.
            If None, uses the default start date from the data source.
        data_datetime_end : datetime, optional
            End date of the data to retrieve (inclusive).
            If None, uses the default end date from the data source.
            
        Returns
        -------
        pandas.DataFrame
            - Single asset: DataFrame indexed by timestamp.
            - List of assets: DataFrame with MultiIndex (symbol, timestamp).
            
        Notes
        -----
        This method fetches raw trades data for high-frequency trading or custom
        feature engineering. It's the strategy's responsibility to process this data
        appropriately for analysis or custom bar construction.
        
        Currently only supported for stock assets with certain data sources like AlpacaBacktesting.
        """
        asset = self._sanitize_user_asset(asset)
        if quote is not None:
            quote = self._sanitize_user_asset(quote)
        
        if hasattr(self.broker.data_source, "get_historical_trades_between_dates"):
            # Auto-configure chunk size from strategy's sleeptime for efficient data fetching
            # This aligns data download chunks with strategy iteration frequency
            if hasattr(self.broker.data_source, "configure_from_sleeptime") and hasattr(self, "_sleeptime"):
                self.broker.data_source.configure_from_sleeptime(self._sleeptime)
            
            result = self.broker.data_source.get_historical_trades_between_dates(
                base_asset=asset,
                quote_asset=quote,
                data_datetime_start=data_datetime_start,
                data_datetime_end=data_datetime_end,
            )
            return result
        else:
            self.logger.error(
                "The data source does not support fetching historical trades data. "
                "This feature is currently only available with certain data sources like AlpacaBacktesting."
            )
            return None

    def get_realtime_trades(
        self,
        asset,
        minutes: int = 5,
    ):
        """Get real-time trades data for an asset (for paper/live trading).
        
        This method uses WebSocket streaming to get real-time market trades.
        Use this for paper trading and live trading scenarios.
        
        Parameters
        ----------
        asset : Asset or str
            The asset to get real-time trades for.
        minutes : int, default 5
            Get last N minutes of trades from the stream buffer.
            
        Returns
        -------
        pandas.DataFrame
            DataFrame containing real-time trades data indexed by timestamp.
            Columns: price, size, exchange, trade_id, conditions, tape
            
        Raises
        ------
        RuntimeError
            If real-time streaming is not enabled on the data source.
            
        Notes
        -----
        - This is for PAPER/LIVE trading only, not backtesting
        - For backtesting, use get_historical_trades() instead
        - Requires API key/secret (OAuth tokens don't support WebSocket)
        - The first call will automatically start streaming for the symbol
        
        Examples
        --------
        >>> # In paper/live trading mode
        >>> trades = self.get_realtime_trades("AAPL", minutes=5)
        >>> if not trades.empty:
        ...     latest_price = trades['price'].iloc[-1]
        ...     volume = trades['size'].sum()
        """
        asset = self._sanitize_user_asset(asset)
        symbol = asset.symbol if hasattr(asset, 'symbol') else str(asset)
        
        # Check if data source supports real-time streaming
        if hasattr(self.broker.data_source, "get_realtime_trades"):
            return self.broker.data_source.get_realtime_trades(symbol, minutes=minutes)
        else:
            self.logger.error(
                "The data source does not support real-time trades streaming. "
                "This feature requires AlpacaData with API key/secret authentication."
            )
            return None

    def start_realtime_trades_streaming(self, symbols: list = None):
        """Start real-time trades streaming for paper/live trading.
        
        This enables WebSocket-based real-time market data streaming.
        Call this in your initialize() method for HFT strategies.
        
        Parameters
        ----------
        symbols : list, optional
            List of symbols to stream. You can add more symbols later
            by calling get_realtime_trades() with new symbols.
            
        Returns
        -------
        object
            The streamer instance for advanced usage.
            
        Notes
        -----
        - Only works with AlpacaData using API key/secret
        - OAuth tokens do not support WebSocket streaming
        - The streamer runs in a background thread
        
        Examples
        --------
        >>> def initialize(self):
        ...     # Start streaming for HFT
        ...     self.start_realtime_trades_streaming(["AAPL", "TSLA"])
        ...
        >>> def on_trading_iteration(self):
        ...     # Get real-time trades
        ...     trades = self.get_realtime_trades("AAPL", minutes=1)
        """
        if hasattr(self.broker.data_source, "start_realtime_trades_streaming"):
            return self.broker.data_source.start_realtime_trades_streaming(symbols or [])
        else:
            self.logger.error(
                "The data source does not support real-time trades streaming. "
                "This feature requires AlpacaData with API key/secret authentication."
            )
            return None

    def stop_realtime_trades_streaming(self):
        """Stop real-time trades streaming.
        
        Call this in your on_strategy_end() or when you no longer need
        real-time data to free up resources.
        """
        if hasattr(self.broker.data_source, "stop_realtime_trades_streaming"):
            self.broker.data_source.stop_realtime_trades_streaming()
        else:
            self.logger.warning("Data source does not support real-time streaming")

    def get_trade_history(self):
        """
        Get the trade history for this strategy.
        
        Returns
        -------
        pandas.DataFrame or None
            DataFrame containing trade history with P&L information, or None if no trades have been made
        """
        if not hasattr(self, '_trade_history') or not self._trade_history:
            return None
            
        # Convert the trade history to a DataFrame
        trade_df = pd.DataFrame(self._trade_history)
        
        # Set the datetime as the index
        if 'datetime' in trade_df.columns:
            trade_df = trade_df.set_index('datetime')
            trade_df = trade_df.sort_index()
            
        return trade_df
        
    def analyze_trades(self):
        """
        Analyze the trade history and return trade performance metrics.
        
        Returns
        -------
        dict
            Dictionary containing trade performance metrics
        """
        trade_df = self.get_trade_history()
        
        if trade_df is None or trade_df.empty:
            return {
                "trade_count": 0,
                "win_rate": 0,
                "profit_factor": 0,
                "avg_profit_per_trade": 0,
                "avg_profit_pct_per_trade": 0,
                "total_profit": 0,
                "max_profit": 0,
                "max_loss": 0,
                "avg_win": 0,
                "avg_loss": 0
            }
            
        # Calculate trade metrics
        trade_count = len(trade_df)
        winning_trades = trade_df[trade_df['pl'] > 0]
        losing_trades = trade_df[trade_df['pl'] < 0]
        
        win_count = len(winning_trades)

        win_rate = win_count / trade_count if trade_count > 0 else 0
        
        total_profit = trade_df['pl'].sum()
        total_profit_winning = winning_trades['pl'].sum() if not winning_trades.empty else 0
        total_loss_losing = abs(losing_trades['pl'].sum()) if not losing_trades.empty else 0
        
        profit_factor = total_profit_winning / total_loss_losing if total_loss_losing > 0 else float('inf')
        
        avg_profit_per_trade = total_profit / trade_count if trade_count > 0 else 0
        avg_profit_pct_per_trade = trade_df['pl_pct'].mean() if 'pl_pct' in trade_df.columns else 0
        
        max_profit = trade_df['pl'].max() if not trade_df.empty else 0
        max_loss = trade_df['pl'].min() if not trade_df.empty else 0
        
        avg_win = winning_trades['pl'].mean() if not winning_trades.empty else 0
        avg_loss = losing_trades['pl'].mean() if not losing_trades.empty else 0
        
        # Create metrics dictionary
        metrics = {
            "trade_count": trade_count,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "avg_profit_per_trade": avg_profit_per_trade,
            "avg_profit_pct_per_trade": avg_profit_pct_per_trade,
            "total_profit": total_profit,
            "max_profit": max_profit,
            "max_loss": max_loss,
            "avg_win": avg_win,
            "avg_loss": avg_loss
        }
        
        return metrics
