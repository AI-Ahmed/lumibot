from pytz import UTC
from datetime import datetime
from collections import Counter

import pandas as pd
import numpy as np

from .config import ALPACA_CONFIG, IS_BACKTESTING, POLYGON_KEY

from lumibot.backtesting import AlpacaDataBacktesting
from lumibot.backtesting import PolygonDataBacktesting, YahooDataBacktesting
from lumibot.brokers import Alpaca
from lumibot.strategies import Strategy
from lumibot.entities import Asset

from lumibot import log

class TAStrategy(Strategy):
    def initialize(self, tickers=None, start_date=datetime(2020, 1, 1), end_date=datetime(2025, 1, 1)):
        if tickers is None:
            tickers = ["AAPL", "MSFT", "GOOGL"]
        elif isinstance(tickers, str):
            tickers = [s.strip() for s in tickers.split(",")]
        self.tickers = [Asset(symbol=s) for s in tickers]
        
        self.sleeptime = "1d"
        self.start_date = pd.Timestamp(start_date).tz_localize(UTC) if start_date.tzinfo is None else start_date
        self.end_date = pd.Timestamp(end_date).tz_localize(UTC) if end_date.tzinfo is None else end_date
        self.stop_cash = self.cash * 0.85
        self.timestep = "day"
        
        self.data = None
        self.sma_period = 50
        self.rsi_period = 14
        self.adx_period = 14
        
        self.log_message(f"Strategy initialized with tickers: {[t.symbol for t in self.tickers]}")
        self.log_message(f"Date range: {self.start_date} to {self.end_date}", type_of_log='debug')

    def _get_historical_data(self):
        try:
            current_date = self.get_datetime()
            lookback_days = max(self.sma_period, self.rsi_period, self.adx_period) + 10
            start_date = current_date - pd.Timedelta(days=lookback_days)
            if start_date < self.start_date:
                start_date = self.start_date
            
            timeshift = current_date - start_date
            bars_len = timeshift.days + 1

            if len(self.tickers) == 1:
                asset = self.tickers[0]
                bars = self.get_historical_prices(asset, length=bars_len, timestep="day")
            else:
                bars = self.get_historical_prices_for_assets(self.tickers, length=bars_len, timestep="day")

            frames = []
            if isinstance(bars, dict):
                for asset, asset_bars in bars.items():
                    df = asset_bars.df.reset_index(drop=True)
                    df["date"] = df.index
                    df = df.set_index("date")
                    df["symbol"] = asset.symbol
                    frames.append(df.set_index("symbol", append=True))
            else:
                df = bars.df.rename_axis("date")
                df["symbol"] = self.tickers[0].symbol
                frames.append(df.set_index("symbol", append=True))
            
            self.data = pd.concat(frames).swaplevel().sort_index()
            return True
        except Exception as e:
            self.log_message(f"Error fetching historical data: {str(e)}", show_in_terminal=True, type_of_log='error')
            return False

    def _sma(self, period):
        try:
            sma = self.data.groupby(level='symbol', group_keys=False)['close'].rolling(window=period).mean()
            idx_count = Counter(sma.index.names)
            if idx_count['symbol'] > 1:
                sma = sma.droplevel(0)
            return sma.dropna()
        except Exception as e:
            self.log_message(f"Error calculating SMA: {str(e)}", show_in_terminal=True, type_of_log='error')
            return pd.Series()

    def _rsi(self, period=14):
        try:
            grouped = self.data.groupby(level='symbol', group_keys=False)
            delta = grouped['close'].diff()
            gain = delta.where(delta > 0, 0.0)
            loss = -delta.where(delta < 0, 0.0)
            avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
            avg_loss = avg_loss.replace(0, 1e-10)
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            return rsi.dropna()
        except Exception as e:
            self.log_message(f"Error calculating RSI: {str(e)}", show_in_terminal=True, type_of_log='error')
            return pd.Series()

    def _adx(self, period=14):
        try:
            grouped = self.data.groupby(level='symbol', group_keys=False)
            high = grouped['high'].transform(lambda x: x)
            low = grouped['low'].transform(lambda x: x)
            close = grouped['close'].transform(lambda x: x)
            tr1 = high - low
            tr2 = abs(high - close.shift())
            tr3 = abs(low - close.shift())
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            up_move = high - high.shift()
            down_move = low.shift() - low
            pos_dm = up_move.where((up_move > down_move) & (up_move > 0), 0)
            neg_dm = down_move.where((down_move > up_move) & (down_move > 0), 0)
            alpha = 1 / period
            smoothed_tr = tr.ewm(alpha=alpha, adjust=False).mean()
            smoothed_pos_dm = pos_dm.ewm(alpha=alpha, adjust=False).mean()
            smoothed_neg_dm = neg_dm.ewm(alpha=alpha, adjust=False).mean()
            pos_di = 100 * (smoothed_pos_dm / smoothed_tr)
            neg_di = 100 * (smoothed_neg_dm / smoothed_tr)
            pos_di = pos_di.replace([np.inf, -np.inf], 0).fillna(0)
            neg_di = neg_di.replace([np.inf, -np.inf], 0).fillna(0)
            dx = 100 * abs(pos_di - neg_di) / (pos_di + neg_di)
            dx = dx.replace([np.inf, -np.inf], 0).fillna(0)
            adx = dx.ewm(alpha=alpha, adjust=False).mean()
            return pd.DataFrame({'adx': adx, 'plus_di': pos_di, 'minus_di': neg_di}).dropna()
        except Exception as e:
            self.log_message(f"Error calculating ADX: {str(e)}", show_in_terminal=True, type_of_log='error')
            return pd.DataFrame(columns=['adx', 'plus_di', 'minus_di'])

    def _get_technical_indicators(self):
        try:
            return (self._sma(self.sma_period), self._rsi(self.rsi_period), self._adx(self.adx_period))
        except Exception as e:
            self.log_message(f"Error calculating indicators: {str(e)}", show_in_terminal=True, type_of_log='critical')
            return (None, None, None)

    def _get_position_for_asset(self, asset):
        for position in self.get_positions():
            if position.asset.symbol == asset.symbol:
                return position.quantity
        return 0

    def on_trading_iteration(self):
        self.log_message("Trading iteration started")
        current_dt = self.get_datetime()
        if current_dt >= self.end_date:
            self.log_message("End date reached, selling all positions")
            self.sell_all()
            return

        if not self._get_historical_data() or self.data is None or self.data.empty:
            log.warning("No data available, skipping trading iteration")
            return

        sma, rsi, adx_df = self._get_technical_indicators()
        if sma is None or adx_df is None:
            self.log_message("Failed to calculate indicators, skipping trading iteration", show_in_terminal=True, type_of_log='error')
            return

        buy_signals = []
        sell_signals = []
        
        for ticker in self.tickers:
            ticker_symbol = ticker.symbol
            try:
                ticker_data = self.data.xs(ticker_symbol, level="symbol")
                if ticker_data.empty:
                    continue
                if ticker_symbol not in sma.index.get_level_values('symbol'):
                    continue
                if ticker_symbol not in adx_df.index.get_level_values('symbol'):
                    continue

                sma_val = float(sma.xs(ticker_symbol, level="symbol").iloc[-1])
                rsi_val = float(rsi.xs(ticker_symbol, level="symbol").iloc[-1])
                adx_val = float(adx_df.xs(ticker_symbol, level="symbol")['adx'].iloc[-1])
                plus_di = float(adx_df.xs(ticker_symbol, level="symbol")['plus_di'].iloc[-1])
                minus_di = float(adx_df.xs(ticker_symbol, level="symbol")['minus_di'].iloc[-1])

                last_price = self.get_last_price(ticker, timestep=self.timestep)
                self.log_message(
                    f"{ticker_symbol}: Price: {last_price:.2f}, SMA: {sma_val:.2f}, RSI: {rsi_val:.2f}, ADX: {adx_val:.2f}, +DI: {plus_di:.2f}, -DI: {minus_di:.2f}",
                    show_in_terminal=True,
                    type_of_log='debug'
                )

                current_position = self._get_position_for_asset(ticker)

                if last_price > sma_val and adx_val > 25 and plus_di > minus_di:
                    if self.cash > self.stop_cash:
                        signal_strength = plus_di - minus_di
                        buy_quantity = max(1, int(signal_strength))
                        max_quantity = int(self.cash * 0.1 / last_price)  # Corrected typo: self.cash instead of self.c.ash
                        buy_quantity = min(buy_quantity, max_quantity)
                        if buy_quantity > 0:
                            buy_signals.append(f"{ticker_symbol}: Buy {buy_quantity} @ {last_price:.2f}")
                            log.log("BUY", f"BUY SIGNAL [{current_dt}]: {ticker_symbol} - {buy_quantity} shares at {last_price:.2f}", show_in_terminal=True)
                            order = self.create_order(ticker, buy_quantity, "buy")
                            self.submit_order(order)

                elif last_price < sma_val and adx_val > 25 and minus_di > plus_di:
                    if current_position > 0:
                        signal_strength = minus_di - plus_di
                        sell_percent = min(100, signal_strength * 10)
                        sell_quantity = max(1, int(current_position * sell_percent / 100))
                        sell_quantity = min(sell_quantity, current_position)
                        if sell_quantity > 0:
                            sell_signals.append(f"{ticker_symbol}: Sell {sell_quantity} @ {last_price:.2f}")
                            log.log("SELL", f"SELL SIGNAL [{current_dt}]: {ticker_symbol} - {sell_quantity} shares at {last_price:.2f}", show_in_terminal=True)
                            order = self.create_order(ticker, sell_quantity, "sell")
                            self.submit_order(order)

            except Exception as e:
                self.log_message(f"Error processing {ticker_symbol}: {str(e)}", show_in_terminal=True, type_of_log='critical')

        if buy_signals:
            self.log_message(f"Buy signals: {', '.join(buy_signals)}", show_in_terminal=False)
        if sell_signals:
            self.log_message(f"Sell signals: {', '.join(sell_signals)}", show_in_terminal=False)

        if current_dt >= self.end_date:
            self.log_message("Strategy end date reached. Closing all positions.")
            self.sell_all()

    def on_filled_order(self, position, order, price, quantity, multiplier):
        self.log_message(f"Filled order: {order}, {position}, {price}, {quantity}, {multiplier}", show_in_terminal=False)

    def on_new_order(self, order):
        self.log_message(f"New order: {order}", show_in_terminal=False)

if __name__ == "__main__":
    if IS_BACKTESTING:
        start = datetime(2024, 1, 30)
        end = datetime(2024, 10, 31)
        TAStrategy.run_backtest(
            AlpacaDataBacktesting,
            start,
            end,
            benchmark_asset="SPY",
            risk_free_rate=0.025,
            alpaca_api_key=ALPACA_CONFIG["API_KEY"],
            alpaca_secret_key=ALPACA_CONFIG["API_SECRET"],
            parameters={
                # "tickers": "AAPL",
                "start_date": start,
                "end_date": end
            },
            show_progress_bar=True,
            quiet_logs=False,
            save_logfile=False
        )
    else:
        broker = Alpaca(ALPACA_CONFIG)
        strategy = TAStrategy(broker=broker, start_date=datetime(2023, 10, 31), end_date=datetime.now())
        strategy.run_live()
