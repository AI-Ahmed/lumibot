import pandas as pd
import pytest
from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock

from lumibot.backtesting import BacktestingBroker, PandasDataBacktesting
from lumibot.entities import Asset, Data, SmartLimitConfig, SmartLimitPreset, TradingFee, TradingSlippage
from lumibot.tools.smart_limit_utils import build_price_ladder, round_to_tick
from lumibot.entities.order import Order
from lumibot.strategies.strategy import Strategy


DEFAULT_START = "2025-01-13 09:30"
DEFAULT_FREQ = "1min"


def make_ohlcv(
    bars,
    start: str = DEFAULT_START,
    freq: str = DEFAULT_FREQ,
    tz: str = "America/New_York",
    volume: int = 1_000,
    bid: float | None = None,
    ask: float | None = None,
):
    index = pd.date_range(start, periods=len(bars), freq=freq, tz=tz)
    opens, highs, lows, closes, volumes = [], [], [], [], []

    for bar in bars:
        if isinstance(bar, dict):
            open_ = bar.get("open")
            high = bar.get("high", open_)
            low = bar.get("low", open_)
            close = bar.get("close", open_)
            vol = bar.get("volume", volume)
        elif isinstance(bar, (tuple, list)) and len(bar) == 4:
            open_, high, low, close = bar
            vol = volume
        else:
            open_ = high = low = close = bar
            vol = volume

        opens.append(open_)
        highs.append(high)
        lows.append(low)
        closes.append(close)
        volumes.append(vol)

    df = pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        },
        index=index,
    )
    if bid is not None:
        df["bid"] = [bid for _ in range(len(bars))]
    if ask is not None:
        df["ask"] = [ask for _ in range(len(bars))]
    return df


class DummyStrategy(Strategy):
    def initialize(self, parameters=None):
        self.sleeptime = "1M"
        self.include_cash_positions = True

    def on_trading_iteration(self):
        return


def build_data_source(asset: Asset, quote: Asset, df: pd.DataFrame) -> PandasDataBacktesting:
    dt_start = df.index[0]
    dt_end = df.index[-1] + pd.Timedelta(minutes=1)

    df_local = df.copy()
    if df_local.index.tz is not None:
        df_local = df_local.tz_convert("America/New_York").tz_localize(None)

    data = Data(
        asset=asset,
        df=df_local,
        quote=quote,
        timestep="minute",
        timezone="America/New_York",
    )
    pandas_data = {(asset, quote): data}
    data_source = PandasDataBacktesting(
        pandas_data=pandas_data,
        datetime_start=dt_start,
        datetime_end=dt_end,
        show_progress_bar=False,
        market="24/7",
        auto_adjust=True,
    )
    data_source.load_data()
    return data_source


def build_data_source_with_market(
    asset: Asset,
    quote: Asset,
    df: pd.DataFrame,
    *,
    market: str,
    datetime_end: pd.Timestamp | None = None,
) -> PandasDataBacktesting:
    dt_start = df.index[0]
    dt_end = datetime_end if datetime_end is not None else (df.index[-1] + pd.Timedelta(minutes=1))

    df_local = df.copy()
    if df_local.index.tz is not None:
        df_local = df_local.tz_convert("America/New_York").tz_localize(None)

    data = Data(
        asset=asset,
        df=df_local,
        quote=quote,
        timestep="minute",
        timezone="America/New_York",
    )
    pandas_data = {(asset, quote): data}
    data_source = PandasDataBacktesting(
        pandas_data=pandas_data,
        datetime_start=dt_start,
        datetime_end=dt_end,
        show_progress_bar=False,
        market=market,
        auto_adjust=True,
    )
    data_source.load_data()
    return data_source


def build_strategy(broker, buy_fee=None, sell_fee=None, buy_slippage=None, sell_slippage=None, budget=100000.0):
    buy_fees = [buy_fee] if buy_fee else []
    sell_fees = [sell_fee] if sell_fee else []
    buy_slippages = [buy_slippage] if buy_slippage else []
    sell_slippages = [sell_slippage] if sell_slippage else []
    return DummyStrategy(
        broker=broker,
        budget=budget,
        buy_trading_fees=buy_fees,
        sell_trading_fees=sell_fees,
        buy_trading_slippages=buy_slippages,
        sell_trading_slippages=sell_slippages,
        analyze_backtest=False,
        parameters={},
    )


def setup_strategy_with_prices(
    asset: Asset,
    quote: Asset,
    bars,
    *,
    start: str = DEFAULT_START,
    freq: str = DEFAULT_FREQ,
    budget: float = 100000.0,
    buy_fee: TradingFee | None = None,
    sell_fee: TradingFee | None = None,
):
    df = make_ohlcv(bars, start=start, freq=freq)
    data_source = build_data_source(asset, quote, df)
    broker = BacktestingBroker(data_source=data_source)
    broker.initialize_market_calendars(data_source.get_trading_days_pandas())
    broker._first_iteration = False

    strategy = build_strategy(
        broker,
        buy_fee=buy_fee,
        sell_fee=sell_fee,
        budget=budget,
    )
    strategy._first_iteration = False
    return strategy, broker, data_source


def submit_and_fill(strategy, broker, order):
    strategy.submit_order(order)
    broker.process_pending_orders(strategy)
    strategy._executor.process_queue()
    return order


def position_quantity(broker, strategy, asset):
    position = broker.get_tracked_position(strategy.name, asset)
    return float(position.quantity) if position else 0.0


def test_process_pending_orders_equity_appies_fee_once():
    asset = Asset("AAPL", asset_type=Asset.AssetType.STOCK)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)
    fee = TradingFee(percent_fee=Decimal("0.001"), taker=True)
    strategy, broker, _ = setup_strategy_with_prices(
        asset,
        quote,
        bars=[(100.0, 101.0, 99.0, 101.0), (101.0, 102.0, 100.0, 102.0), (102.0, 103.0, 101.0, 103.0)],
        buy_fee=fee,
        sell_fee=fee,
    )

    order = strategy.create_order(
        asset,
        10,
        Order.OrderSide.BUY,
        order_type=Order.OrderType.MARKET,
    )
    submit_and_fill(strategy, broker, order)

    expected_cash = 100000.0 - (10 * 100.0) - (10 * 100.0 * 0.001)
    assert strategy.cash == pytest.approx(expected_cash, rel=1e-9)

    position = broker.get_tracked_position(strategy.name, asset)
    assert position is not None
    assert position.quantity == pytest.approx(10.0)


def test_bracket_order_entry_and_exit_cash_consistency():
    asset = Asset("AAPL", asset_type=Asset.AssetType.STOCK)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)
    fee = TradingFee(percent_fee=Decimal("0.001"), taker=True)
    strategy, broker, _ = setup_strategy_with_prices(
        asset,
        quote,
        bars=[(100.0, 110.0, 99.0, 105.0), (110.0, 112.0, 109.0, 111.0)],
        buy_fee=fee,
        sell_fee=fee,
    )

    order = strategy.create_order(
        asset,
        10,
        Order.OrderSide.BUY,
        order_type=Order.OrderType.MARKET,
        order_class=Order.OrderClass.BRACKET,
        secondary_limit_price=110.0,
        secondary_stop_price=95.0,
    )
    submit_and_fill(strategy, broker, order)

    # Advance to next bar so the child order can fill
    broker._update_datetime(broker.datetime + timedelta(minutes=1))
    broker.process_pending_orders(strategy)
    strategy._executor.process_queue()

    expected_cash = 100000.0 - (10 * 100.0) - (10 * 100.0 * 0.001)
    expected_cash += (10 * 110.0) - (10 * 110.0 * 0.001)
    assert strategy.cash == pytest.approx(expected_cash, rel=1e-9)

    assert broker.get_tracked_position(strategy.name, asset) is None


def test_oto_order_entry_and_exit_cash_consistency():
    asset = Asset("AAPL", asset_type=Asset.AssetType.STOCK)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)
    fee = TradingFee(percent_fee=Decimal("0.001"), taker=True)
    strategy, broker, _ = setup_strategy_with_prices(
        asset,
        quote,
        bars=[(100.0, 109.0, 99.0, 105.0), (110.0, 112.0, 109.0, 111.0)],
        buy_fee=fee,
        sell_fee=fee,
    )

    order = strategy.create_order(
        asset,
        10,
        Order.OrderSide.BUY,
        order_type=Order.OrderType.MARKET,
        order_class=Order.OrderClass.OTO,
        secondary_limit_price=110.0,
    )
    submit_and_fill(strategy, broker, order)

    # Advance to next bar so the child order can fill (avoid same-bar lookahead).
    broker._update_datetime(broker.datetime + timedelta(minutes=1))
    broker.process_pending_orders(strategy)
    strategy._executor.process_queue()

    expected_cash = 100000.0 - (10 * 100.0) - (10 * 100.0 * 0.001)
    expected_cash += (10 * 110.0) - (10 * 110.0 * 0.001)
    assert strategy.cash == pytest.approx(expected_cash, rel=1e-9)

    assert broker.get_tracked_position(strategy.name, asset) is None

    assert order.order_class == Order.OrderClass.OTO
    assert len(order.child_orders) == 1
    assert order.child_orders[0].is_filled()


def test_oco_order_exit_cancels_other_child():
    asset = Asset("AAPL", asset_type=Asset.AssetType.STOCK)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)
    strategy, broker, _ = setup_strategy_with_prices(
        asset,
        quote,
        bars=[(100.0, 109.0, 99.0, 105.0), (110.0, 112.0, 109.0, 111.0)],
    )

    entry = strategy.create_order(asset, 10, Order.OrderSide.BUY, order_type=Order.OrderType.MARKET)
    submit_and_fill(strategy, broker, entry)

    broker._update_datetime(broker.datetime + timedelta(minutes=1))

    oco = strategy.create_order(
        asset,
        10,
        Order.OrderSide.SELL,
        order_class=Order.OrderClass.OCO,
        limit_price=110.0,
        stop_price=95.0,
    )
    strategy.submit_order(oco)
    broker.process_pending_orders(strategy)
    strategy._executor.process_queue()

    assert oco.order_class == Order.OrderClass.OCO
    assert len(oco.child_orders) == 2

    filled = [child for child in oco.child_orders if child.is_filled()]
    canceled = [child for child in oco.child_orders if child.is_canceled()]
    assert len(filled) == 1
    assert len(canceled) == 1

@pytest.mark.parametrize(
    "order_type, bars, quantity, order_kwargs, expected_price",
    [
        (
            Order.OrderType.MARKET,
            [(100.0, 101.0, 99.0, 100.0)],
            10,
            {},
            100.0,
        ),
        (
            Order.OrderType.LIMIT,
            [(100.0, 101.0, 98.5, 100.5)],
            10,
            {"limit_price": 99.0},
            99.0,
        ),
        (
            Order.OrderType.LIMIT,
            [(95.0, 100.0, 94.5, 99.0)],
            10,
            {"limit_price": 99.5},
            95.0,
        ),
        (
            Order.OrderType.STOP,
            [(100.0, 105.0, 99.0, 104.0)],
            10,
            {"stop_price": 104.0},
            104.0,
        ),
        (
            Order.OrderType.STOP_LIMIT,
            [(100.0, 105.0, 99.0, 103.0)],
            10,
            {"stop_price": 103.0, "limit_price": 103.0},
            103.0,
        ),
        (
            Order.OrderType.MARKET,
            [(150.0, 151.0, 149.0, 150.5)],
            Decimal("2.5"),
            {},
            150.0,
        ),
        (
            Order.OrderType.STOP,
            [(90.0, 92.0, 88.0, 91.0)],
            Decimal("5"),
            {"stop_price": 91.0},
            91.0,
        ),
    ],
    ids=[
        "market_buy",
        "limit_between_range",
        "limit_gap_down",
        "stop_gap_up",
        "stop_limit_trigger",
        "market_fractional",
        "stop_fractional",
    ],
)
def test_buy_orders_fill_at_expected_prices(order_type, bars, quantity, order_kwargs, expected_price):
    asset = Asset("TEST", asset_type=Asset.AssetType.STOCK)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)
    strategy, broker, _ = setup_strategy_with_prices(asset, quote, bars)

    order = strategy.create_order(
        asset,
        quantity,
        Order.OrderSide.BUY,
        order_type=order_type,
        **order_kwargs,
    )
    submit_and_fill(strategy, broker, order)

    expected_cash = 100000.0 - (float(quantity) * expected_price)
    assert strategy.cash == pytest.approx(expected_cash, rel=1e-9)
    assert position_quantity(broker, strategy, asset) == pytest.approx(float(quantity))
    assert order.trade_cost == pytest.approx(0.0)


def test_limit_order_remains_open_when_price_not_reached():
    asset = Asset("LIMIT", asset_type=Asset.AssetType.STOCK)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)
    strategy, broker, _ = setup_strategy_with_prices(
        asset,
        quote,
        bars=[(100.0, 100.5, 100.0, 100.25)],
    )

    order = strategy.create_order(
        asset,
        10,
        Order.OrderSide.BUY,
        order_type=Order.OrderType.LIMIT,
        limit_price=98.0,
    )
    submit_and_fill(strategy, broker, order)

    assert not order.is_filled()
    assert strategy.cash == pytest.approx(100000.0, rel=1e-9)
    assert position_quantity(broker, strategy, asset) == pytest.approx(0.0)


@pytest.mark.parametrize(
    "order_type, bars, quantity, order_kwargs, expected_price",
    [
        (
            Order.OrderType.MARKET,
            [(101.0, 102.0, 100.5, 101.5)],
            10,
            {},
            101.0,
        ),
        (
            Order.OrderType.LIMIT,
            [(100.0, 105.0, 99.0, 103.0)],
            10,
            {"limit_price": 104.0},
            104.0,
        ),
        (
            Order.OrderType.STOP,
            [(100.0, 101.5, 98.5, 99.5)],
            10,
            {"stop_price": 99.0},
            99.0,
        ),
        (
            Order.OrderType.STOP_LIMIT,
            [(100.0, 101.5, 98.5, 99.5)],
            10,
            {"stop_price": 99.0, "limit_price": 99.0},
            99.0,
        ),
        (
            Order.OrderType.LIMIT,
            [(110.0, 112.0, 109.0, 111.0)],
            Decimal("2.5"),
            {"limit_price": 111.5},
            111.5,
        ),
        (
            Order.OrderType.STOP,
            [(95.0, 96.0, 90.0, 91.0)],
            Decimal("4"),
            {"stop_price": 94.0},
            94.0,
        ),
    ],
    ids=[
        "market_sell",
        "limit_sell",
        "stop_sell",
        "stop_limit_sell",
        "limit_fractional",
        "stop_fractional",
    ],
)
def test_sell_orders_fill_at_expected_prices(order_type, bars, quantity, order_kwargs, expected_price):
    asset = Asset("SELL", asset_type=Asset.AssetType.STOCK)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)
    initial_bars = [(100.0, 101.0, 99.0, 100.0)] + bars
    strategy, broker, _ = setup_strategy_with_prices(asset, quote, initial_bars)

    entry_order = strategy.create_order(
        asset,
        quantity,
        Order.OrderSide.BUY,
        order_type=Order.OrderType.MARKET,
    )
    submit_and_fill(strategy, broker, entry_order)

    broker._update_datetime(broker.datetime + timedelta(minutes=1))

    exit_order = strategy.create_order(
        asset,
        quantity,
        Order.OrderSide.SELL,
        order_type=order_type,
        **order_kwargs,
    )
    submit_and_fill(strategy, broker, exit_order)

    expected_cash = 100000.0 - (float(quantity) * 100.0) + (float(quantity) * expected_price)
    assert strategy.cash == pytest.approx(expected_cash, rel=1e-9)
    assert position_quantity(broker, strategy, asset) == pytest.approx(0.0)


def test_trailing_stop_sell_triggers_after_price_drop():
    asset = Asset("TRAIL", asset_type=Asset.AssetType.STOCK)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)
    bars = [
        (100.0, 105.0, 99.0, 104.0),
        (104.0, 106.0, 103.0, 105.0),
        (105.0, 105.5, 100.0, 101.0),
    ]
    strategy, broker, _ = setup_strategy_with_prices(asset, quote, bars)

    entry = strategy.create_order(
        asset,
        10,
        Order.OrderSide.BUY,
        order_type=Order.OrderType.MARKET,
    )
    submit_and_fill(strategy, broker, entry)

    trail_order = strategy.create_order(
        asset,
        10,
        Order.OrderSide.SELL,
        order_type=Order.OrderType.TRAIL,
        trail_price=2.0,
    )
    submit_and_fill(strategy, broker, trail_order)
    assert not trail_order.is_filled()

    broker._update_datetime(broker.datetime + timedelta(minutes=1))
    broker.process_pending_orders(strategy)
    strategy._executor.process_queue()
    assert trail_order.is_filled()

    expected_cash = 100000.0 - (10 * 100.0) + (10 * 103.0)
    assert strategy.cash == pytest.approx(expected_cash, rel=1e-9)
    assert position_quantity(broker, strategy, asset) == pytest.approx(0.0)


def test_bracket_order_stop_exit_executes():
    asset = Asset("BRKSTOP", asset_type=Asset.AssetType.STOCK)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)
    strategy, broker, _ = setup_strategy_with_prices(
        asset,
        quote,
        bars=[(100.0, 101.0, 99.0, 100.5), (99.0, 100.0, 94.0, 95.0)],
    )

    order = strategy.create_order(
        asset,
        10,
        Order.OrderSide.BUY,
        order_type=Order.OrderType.MARKET,
        order_class=Order.OrderClass.BRACKET,
        secondary_stop_price=96.0,
    )
    submit_and_fill(strategy, broker, order)

    broker._update_datetime(broker.datetime + timedelta(minutes=1))
    broker.process_pending_orders(strategy)
    strategy._executor.process_queue()

    expected_cash = 100000.0 - (10 * 100.0) + (10 * 96.0)
    assert strategy.cash == pytest.approx(expected_cash, rel=1e-9)
    assert position_quantity(broker, strategy, asset) == pytest.approx(0.0)


def test_bracket_order_trailing_exit_executes():
    asset = Asset("BRKTRAIL", asset_type=Asset.AssetType.STOCK)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)
    strategy, broker, _ = setup_strategy_with_prices(
        asset,
        quote,
        bars=[
            (100.0, 104.0, 99.0, 103.0),
            (103.0, 107.0, 102.0, 106.0),
            (106.0, 106.5, 101.0, 102.0),
        ],
    )

    order = strategy.create_order(
        asset,
        10,
        Order.OrderSide.BUY,
        order_type=Order.OrderType.MARKET,
        order_class=Order.OrderClass.BRACKET,
        secondary_stop_price=102.0,
        secondary_trail_price=2.0,
    )
    submit_and_fill(strategy, broker, order)

    # First child evaluation
    broker._update_datetime(broker.datetime + timedelta(minutes=1))
    broker.process_pending_orders(strategy)
    strategy._executor.process_queue()

    # Second child evaluation triggers trailing exit
    broker._update_datetime(broker.datetime + timedelta(minutes=1))
    broker.process_pending_orders(strategy)
    strategy._executor.process_queue()

    expected_cash = 100000.0 - (10 * 100.0) + (10 * 105.0)
    assert strategy.cash == pytest.approx(expected_cash, rel=1e-9)
    assert position_quantity(broker, strategy, asset) == pytest.approx(0.0)


def test_percent_fee_is_applied_once():
    asset = Asset("FEEPCT", asset_type=Asset.AssetType.STOCK)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)
    fee = TradingFee(percent_fee=Decimal("0.002"), taker=True)
    strategy, broker, _ = setup_strategy_with_prices(
        asset,
        quote,
        bars=[(100.0, 101.0, 99.0, 100.5)],
        buy_fee=fee,
    )

    order = strategy.create_order(
        asset,
        10,
        Order.OrderSide.BUY,
        order_type=Order.OrderType.MARKET,
    )
    submit_and_fill(strategy, broker, order)

    expected_fee = 10 * 100.0 * 0.002
    expected_cash = 100000.0 - (10 * 100.0) - expected_fee

    assert order.trade_cost == pytest.approx(expected_fee, rel=1e-9)
    assert strategy.cash == pytest.approx(expected_cash, rel=1e-9)


def test_flat_fee_is_applied_once():
    asset = Asset("FEEFLAT", asset_type=Asset.AssetType.STOCK)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)
    fee = TradingFee(flat_fee=Decimal("5.00"), taker=True)
    strategy, broker, _ = setup_strategy_with_prices(
        asset,
        quote,
        bars=[(50.0, 50.5, 49.5, 50.25)],
        buy_fee=fee,
    )

    order = strategy.create_order(
        asset,
        20,
        Order.OrderSide.BUY,
        order_type=Order.OrderType.MARKET,
    )
    submit_and_fill(strategy, broker, order)

    expected_fee = 5.0
    expected_cash = 100000.0 - (20 * 50.0) - expected_fee

    assert order.trade_cost == pytest.approx(expected_fee, rel=1e-9)
    assert strategy.cash == pytest.approx(expected_cash, rel=1e-9)


def test_multiple_orders_in_single_cycle_all_fill():
    asset = Asset("MULTI", asset_type=Asset.AssetType.STOCK)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)
    strategy, broker, _ = setup_strategy_with_prices(
        asset,
        quote,
        bars=[(75.0, 76.0, 74.5, 75.5)],
    )

    order_one = strategy.create_order(
        asset,
        5,
        Order.OrderSide.BUY,
        order_type=Order.OrderType.MARKET,
    )
    order_two = strategy.create_order(
        asset,
        7,
        Order.OrderSide.BUY,
        order_type=Order.OrderType.MARKET,
    )

    strategy.submit_order(order_one)
    strategy.submit_order(order_two)

    broker.process_pending_orders(strategy)
    strategy._executor.process_queue()

    expected_cash = 100000.0 - ((5 + 7) * 75.0)
    assert strategy.cash == pytest.approx(expected_cash, rel=1e-9)
    assert position_quantity(broker, strategy, asset) == pytest.approx(12.0)


def test_missing_bar_falls_back_to_last_available_price():
    asset = Asset("FALLBACK", asset_type=Asset.AssetType.STOCK)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)
    strategy, broker, _ = setup_strategy_with_prices(
        asset,
        quote,
        bars=[(200.0, 202.0, 198.0, 201.0)],
    )

    broker._update_datetime(broker.datetime + timedelta(minutes=5))

    order = strategy.create_order(
        asset,
        3,
        Order.OrderSide.BUY,
        order_type=Order.OrderType.MARKET,
    )

    strategy.submit_order(order)
    broker.process_pending_orders(strategy)
    strategy._executor.process_queue()

    expected_cash = 100000.0 - (3 * 200.0)
    assert strategy.cash == pytest.approx(expected_cash, rel=1e-9)
    assert position_quantity(broker, strategy, asset) == pytest.approx(3.0)


def test_smart_limit_fills_mid_plus_slippage_and_logs_slippage():
    asset = Asset("SMART", asset_type=Asset.AssetType.STOCK)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)
    df = make_ohlcv(
        [(100.0, 101.0, 99.0, 100.0)],
        bid=99.0,
        ask=101.0,
    )
    data_source = build_data_source(asset, quote, df)
    broker = BacktestingBroker(data_source=data_source)
    broker.initialize_market_calendars(data_source.get_trading_days_pandas())
    broker._first_iteration = False

    strategy = build_strategy(broker)
    strategy._first_iteration = False

    config = SmartLimitConfig(
        preset=SmartLimitPreset.FAST,
        slippage=TradingSlippage(amount=0.25),
    )
    order = strategy.create_order(
        asset,
        Decimal("2"),
        Order.OrderSide.BUY,
        order_type=Order.OrderType.SMART_LIMIT,
        smart_limit=config,
    )
    strategy.submit_order(order)
    order._date_created = broker.datetime - timedelta(seconds=60)

    broker.process_pending_orders(strategy)
    strategy._executor.process_queue()

    assert order.is_filled()
    assert order.get_fill_price() == pytest.approx(100.25)
    assert order.trade_slippage == pytest.approx(0.5)  # 0.25 * 2

    fills = broker._trade_event_log_df
    fill_row = fills[fills["status"] == "fill"].iloc[0]
    assert float(fill_row["trade_slippage"]) == pytest.approx(0.5)


def test_smart_limit_downgrades_to_market_when_quotes_missing():
    asset = Asset("NOBID", asset_type=Asset.AssetType.STOCK)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)
    df = make_ohlcv(
        [(50.0, 51.0, 49.0, 50.0)],
        bid=None,
        ask=None,
    )
    data_source = build_data_source(asset, quote, df)
    broker = BacktestingBroker(data_source=data_source)
    broker.initialize_market_calendars(data_source.get_trading_days_pandas())
    broker._first_iteration = False

    strategy = build_strategy(broker)
    strategy._first_iteration = False

    config = SmartLimitConfig(preset=SmartLimitPreset.FAST, slippage=0.1)
    order = strategy.create_order(
        asset,
        Decimal("1"),
        Order.OrderSide.BUY,
        order_type=Order.OrderType.SMART_LIMIT,
        smart_limit=config,
    )
    strategy.submit_order(order)
    order._date_created = broker.datetime - timedelta(seconds=60)

    broker.process_pending_orders(strategy)
    strategy._executor.process_queue()

    assert order.is_filled()
    assert order.get_fill_price() == pytest.approx(50.0)
    assert order.trade_slippage == pytest.approx(0.0)

def test_smart_limit_sell_applies_slippage_below_mid():
    asset = Asset("SMARTSELL", asset_type=Asset.AssetType.STOCK)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)
    df = make_ohlcv(
        [(200.0, 201.0, 199.0, 200.0)],
        bid=199.0,
        ask=201.0,
    )
    data_source = build_data_source(asset, quote, df)
    broker = BacktestingBroker(data_source=data_source)
    broker.initialize_market_calendars(data_source.get_trading_days_pandas())
    broker._first_iteration = False

    strategy = build_strategy(broker)
    strategy._first_iteration = False

    buy_order = strategy.create_order(
        asset,
        Decimal("1"),
        Order.OrderSide.BUY,
        order_type=Order.OrderType.MARKET,
    )
    strategy.submit_order(buy_order)
    buy_order._date_created = broker.datetime - timedelta(seconds=120)
    broker.process_pending_orders(strategy)
    strategy._executor.process_queue()
    assert buy_order.is_filled()

    config = SmartLimitConfig(
        preset=SmartLimitPreset.FAST,
        slippage=TradingSlippage(amount=0.4),
    )
    order = strategy.create_order(
        asset,
        Decimal("1"),
        Order.OrderSide.SELL,
        order_type=Order.OrderType.SMART_LIMIT,
        smart_limit=config,
    )
    strategy.submit_order(order)
    order._date_created = broker.datetime - timedelta(seconds=60)

    broker.process_pending_orders(strategy)
    strategy._executor.process_queue()

    assert order.is_filled()
    assert order.get_fill_price() == pytest.approx(199.6)  # mid 200 minus slippage 0.4
    assert order.trade_slippage == pytest.approx(0.4)


def test_smart_limit_cancels_after_final_hold():
    asset = Asset("CANCEL", asset_type=Asset.AssetType.STOCK)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)
    df = make_ohlcv(
        [(10.0, 11.0, 9.0, 10.0)],
        bid=9.0,
        ask=11.0,
    )
    data_source = build_data_source(asset, quote, df)
    broker = BacktestingBroker(data_source=data_source)
    broker.initialize_market_calendars(data_source.get_trading_days_pandas())
    broker._first_iteration = False

    strategy = build_strategy(broker)
    strategy._first_iteration = False

    config = SmartLimitConfig(preset=SmartLimitPreset.FAST, slippage=0.1)
    order = strategy.create_order(
        asset,
        Decimal("1"),
        Order.OrderSide.BUY,
        order_type=Order.OrderType.SMART_LIMIT,
        smart_limit=config,
    )
    strategy.submit_order(order)
    order._date_created = broker.datetime - timedelta(seconds=200)

    broker.process_pending_orders(strategy)
    strategy._executor.process_queue()

    assert order.is_canceled()


def test_smart_limit_respects_final_price_guard():
    asset = Asset("GUARD", asset_type=Asset.AssetType.STOCK)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)
    df = make_ohlcv(
        [(100.0, 101.0, 99.0, 100.0)],
        bid=99.0,
        ask=101.0,
    )
    data_source = build_data_source(asset, quote, df)
    broker = BacktestingBroker(data_source=data_source)
    broker.initialize_market_calendars(data_source.get_trading_days_pandas())
    broker._first_iteration = False

    strategy = build_strategy(broker)
    strategy._first_iteration = False

    config = SmartLimitConfig(
        preset=SmartLimitPreset.FAST,
        slippage=2.0,
        final_price_pct=0.1,
    )
    order = strategy.create_order(
        asset,
        Decimal("1"),
        Order.OrderSide.BUY,
        order_type=Order.OrderType.SMART_LIMIT,
        smart_limit=config,
    )
    strategy.submit_order(order)
    order._date_created = broker.datetime - timedelta(seconds=60)

    broker.process_pending_orders(strategy)
    strategy._executor.process_queue()

    assert not order.is_filled()


def test_smart_limit_uses_strategy_slippage_when_config_missing():
    asset = Asset("STRATSLIP", asset_type=Asset.AssetType.STOCK)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)
    df = make_ohlcv(
        [(100.0, 101.0, 99.0, 100.0)],
        bid=99.0,
        ask=101.0,
    )
    data_source = build_data_source(asset, quote, df)
    broker = BacktestingBroker(data_source=data_source)
    broker.initialize_market_calendars(data_source.get_trading_days_pandas())
    broker._first_iteration = False

    strategy = build_strategy(broker, buy_slippage=TradingSlippage(amount=0.3))
    strategy._first_iteration = False

    config = SmartLimitConfig(preset=SmartLimitPreset.FAST)
    order = strategy.create_order(
        asset,
        Decimal("1"),
        Order.OrderSide.BUY,
        order_type=Order.OrderType.SMART_LIMIT,
        smart_limit=config,
    )
    strategy.submit_order(order)
    order._date_created = broker.datetime - timedelta(seconds=60)

    broker.process_pending_orders(strategy)
    strategy._executor.process_queue()

    assert order.is_filled()
    assert order.get_fill_price() == pytest.approx(100.3)


def test_market_order_prefers_quote_when_bid_ask_available():
    asset = Asset("QUOTED", asset_type=Asset.AssetType.STOCK)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)
    df = make_ohlcv(
        [(100.0, 101.0, 99.0, 100.0)],
        bid=99.0,
        ask=101.0,
    )
    data_source = build_data_source(asset, quote, df)
    broker = BacktestingBroker(data_source=data_source)
    broker.initialize_market_calendars(data_source.get_trading_days_pandas())
    broker._first_iteration = False

    strategy = build_strategy(broker)
    strategy._first_iteration = False

    order = strategy.create_order(
        asset,
        Decimal("1"),
        Order.OrderSide.BUY,
        order_type=Order.OrderType.MARKET,
    )
    submit_and_fill(strategy, broker, order)

    assert order.is_filled()
    assert order.get_fill_price() == pytest.approx(101.0)


def test_market_order_falls_back_to_open_when_quotes_missing():
    asset = Asset("NOQUOTE", asset_type=Asset.AssetType.STOCK)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)
    df = make_ohlcv(
        [(100.0, 101.0, 99.0, 100.0)],
        bid=None,
        ask=None,
    )
    data_source = build_data_source(asset, quote, df)
    broker = BacktestingBroker(data_source=data_source)
    broker.initialize_market_calendars(data_source.get_trading_days_pandas())
    broker._first_iteration = False

    strategy = build_strategy(broker)
    strategy._first_iteration = False

    order = strategy.create_order(
        asset,
        Decimal("1"),
        Order.OrderSide.BUY,
        order_type=Order.OrderType.MARKET,
    )
    submit_and_fill(strategy, broker, order)

    assert order.is_filled()
    assert order.get_fill_price() == pytest.approx(100.0)


def test_limit_order_uses_quote_when_ohlc_missing():
    asset = Asset("MISSINGBAR", asset_type=Asset.AssetType.STOCK)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)
    df = make_ohlcv(
        [(100.0, 101.0, 99.0, 100.0)],
        bid=99.0,
        ask=101.0,
    )
    data_source = build_data_source(asset, quote, df)
    data_source.get_historical_prices = MagicMock(return_value=None)
    broker = BacktestingBroker(data_source=data_source)
    broker.initialize_market_calendars(data_source.get_trading_days_pandas())
    broker._first_iteration = False

    strategy = build_strategy(broker)
    strategy._first_iteration = False

    order = strategy.create_order(
        asset,
        Decimal("1"),
        Order.OrderSide.BUY,
        order_type=Order.OrderType.LIMIT,
        limit_price=105.0,
    )
    submit_and_fill(strategy, broker, order)

    assert order.is_filled()
    assert order.get_fill_price() == pytest.approx(101.0)


def test_smart_limit_ladder_and_rounding():
    ladder = build_price_ladder(100.0, 102.0, 3)
    assert ladder == pytest.approx([100.0, 101.0, 102.0])

    assert round_to_tick(100.03, 0.05, side="buy") == pytest.approx(100.05)
    assert round_to_tick(100.03, 0.05, side="sell") == pytest.approx(100.0)


def test_future_end_date_stops_backtest_cleanly():
    asset = Asset("AAPL", asset_type=Asset.AssetType.STOCK)
    quote = Asset("USD", asset_type=Asset.AssetType.FOREX)
    df = make_ohlcv(
        bars=[(100.0, 101.0, 99.0, 100.0), (101.0, 102.0, 100.0, 101.0)],
        start="2025-01-02 09:30",
        freq="1min",
    )
    future_end = df.index[-1] + pd.Timedelta(days=10)
    data_source = build_data_source_with_market(
        asset,
        quote,
        df,
        market="NYSE",
        datetime_end=future_end,
    )
    broker = BacktestingBroker(data_source=data_source)
    broker.initialize_market_calendars(data_source.get_trading_days_pandas())

    broker._update_datetime(df.index[-1] + pd.Timedelta(days=5))
    strategy = build_strategy(broker)
    broker._await_market_to_open(timedelta=0, strategy=strategy)

    assert broker._end_of_trading_days_reached is True
    assert broker.should_continue() is False
    assert broker.data_source.datetime_end <= broker.datetime
