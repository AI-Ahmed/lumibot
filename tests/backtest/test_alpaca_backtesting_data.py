from pytz import UTC
from datetime import datetime
from collections import Counter

import pandas as pd
import numpy as np

from .config import ALPACA_CONFIG, IS_BACKTESTING, POLYGON_KEY

from lumibot.backtesting import AlpacaBacktesting
from lumibot.backtesting import PolygonDataBacktesting, YahooDataBacktesting
from lumibot.brokers import Alpaca
from lumibot.strategies import Strategy
from lumibot.entities import Asset
from lumibot.traders import Trader

from lumibot import log

class TAStrategy(Strategy):
    parameters = {
        "tickers": "AAPL",
        "start_date": datetime(2020, 1, 1),
        "end_date": datetime(2025, 1, 1)
    }

    def initialize(self):
        tickers = self.parameters["tickers"]
        start_date = self.parameters["start_date"]
        end_date = self.parameters["end_date"]
        
        if tickers is None:
            tickers = ["AAPL", "MSFT", "GOOGL"]
        elif isinstance(tickers, str):
            tickers = [s.strip() for s in tickers.split(",")]
        self.tickers = [Asset(symbol=s) for s in tickers]
        
        self.sleeptime = "1d"
        self.start_date = pd.Timestamp(start_date).tz_localize(UTC) if start_date.tzinfo is None else start_date
        self.end_date = pd.Timestamp(end_date).tz_localize(UTC) if end_date.tzinfo is None else end_date
        self.stop_cash = self.cash * 0.85
        
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
        """
        Execute trading logic for each iteration.
        
        Trading Rules:
        - Buy: Price > SMA, ADX > 20 (lowered), +DI > -DI, RSI < 75 (raised)
        - Sell: Price < SMA, ADX > 20 (lowered), -DI > +DI, OR RSI > 75 (lowered)
        """
        self.log_message("Trading iteration started")
        current_dt = self.get_datetime()
        
        # Check if strategy should end
        if current_dt >= self.end_date:
            self.log_message("End date reached, selling all positions")
            self.sell_all()
            return

        # Get data and indicators
        if not self._get_historical_data() or self.data is None or self.data.empty:
            self.log_message("No data available, skipping trading iteration", show_in_terminal=True, type_of_log='warning')
            return

        sma, rsi, adx_df = self._get_technical_indicators()
        if sma is None or rsi is None or adx_df is None:
            self.log_message("Failed to calculate indicators, skipping trading iteration", show_in_terminal=True, type_of_log='error')
            return

        buy_signals = []
        sell_signals = []
        active_tickers = 0
        
        for ticker in self.tickers:
            ticker_symbol = ticker.symbol
            try:
                # Validate data availability
                ticker_data = self.data.xs(ticker_symbol, level="symbol")
                if ticker_data.empty:
                    continue
                    
                # Check if indicators are available for this ticker
                sma_available = ticker_symbol in sma.index.get_level_values('symbol') if hasattr(sma.index, 'get_level_values') else False
                rsi_available = ticker_symbol in rsi.index.get_level_values('symbol') if hasattr(rsi.index, 'get_level_values') else False
                adx_available = ticker_symbol in adx_df.index.get_level_values('symbol') if hasattr(adx_df.index, 'get_level_values') else False
                
                if not (sma_available and rsi_available and adx_available):
                    continue

                # Get indicator values with error handling
                try:
                    sma_val = float(sma.xs(ticker_symbol, level="symbol").iloc[-1])
                    rsi_val = float(rsi.xs(ticker_symbol, level="symbol").iloc[-1])
                    adx_val = float(adx_df.xs(ticker_symbol, level="symbol")['adx'].iloc[-1])
                    plus_di = float(adx_df.xs(ticker_symbol, level="symbol")['plus_di'].iloc[-1])
                    minus_di = float(adx_df.xs(ticker_symbol, level="symbol")['minus_di'].iloc[-1])
                except Exception as e:
                    continue

                last_price = self.get_last_price(ticker)
                current_position = self._get_position_for_asset(ticker)
                
                # Trading conditions
                price_above_sma = last_price > sma_val
                adx_strong = adx_val > 15  # Further lowered from 20
                bullish_momentum = plus_di > minus_di
                not_overbought = rsi_val < 80  # Further raised from 75
                has_cash = self.cash > self.stop_cash
                
                active_tickers += 1

                # BUY LOGIC
                buy_conditions_met = all([price_above_sma, adx_strong, bullish_momentum, not_overbought, has_cash])
                
                if buy_conditions_met:
                    # Position sizing
                    max_allocation_per_position = min(0.15, 1.0 / len(self.tickers))
                    position_value = self.cash * max_allocation_per_position
                    buy_quantity = int(position_value / last_price)
                    
                    if buy_quantity > 0:
                        buy_signals.append(f"{ticker_symbol}: Buy {buy_quantity} @ {last_price:.2f}")
                        log.log("BUY", f"BUY SIGNAL [{current_dt}]: {ticker_symbol} - {buy_quantity} shares at {last_price:.2f} (RSI: {rsi_val:.1f})")
                        order = self.create_order(ticker, buy_quantity, "buy")
                        self.submit_order(order)

                # SELL LOGIC
                elif current_position > 0 and (
                    (last_price < sma_val and adx_val > 15 and minus_di > plus_di) or  # Trend reversal
                    rsi_val > 80  # Overbought threshold
                ):
                    if rsi_val > 80:
                        sell_percent = min(40, max(25, (rsi_val - 80) * 3))
                    else:
                        signal_strength = minus_di - plus_di
                        sell_percent = min(60, max(20, signal_strength * 2))
                    
                    sell_quantity = max(1, int(current_position * sell_percent / 100))
                    sell_quantity = min(sell_quantity, current_position)
                    
                    if sell_quantity > 0:
                        sell_reason = "Overbought (RSI)" if rsi_val > 80 else "Trend Reversal"
                        sell_signals.append(f"{ticker_symbol}: Sell {sell_quantity} @ {last_price:.2f} ({sell_reason})")
                        log.log("SELL", f"SELL SIGNAL [{current_dt}]: {ticker_symbol} - {sell_quantity} shares at {last_price:.2f} (RSI: {rsi_val:.1f}, Reason: {sell_reason})")
                        order = self.create_order(ticker, sell_quantity, "sell")
                        self.submit_order(order)

            except Exception as e:
                self.log_message(f"Error processing {ticker_symbol}: {str(e)}", show_in_terminal=True, type_of_log='critical')

        # FALLBACK STRATEGY: Simple momentum when TA conditions are too restrictive
        if not buy_signals and not sell_signals and active_tickers > 0:
            # Simple momentum strategy: buy if price > 20-day average, sell if below
            for ticker in self.tickers[:2]:  # Limit to first 2 tickers to avoid overallocation
                try:
                    current_position = self._get_position_for_asset(ticker)
                    last_price = self.get_last_price(ticker)
                    ticker_symbol = ticker.symbol
                    
                    # Get 20-day average from our data
                    ticker_data = self.data.xs(ticker_symbol, level="symbol")
                    if len(ticker_data) >= 20:
                        avg_20 = ticker_data['close'].tail(20).mean()
                        
                        # Simple buy condition: price 2% above 20-day average and no position
                        if last_price > avg_20 * 1.02 and current_position == 0 and self.cash > self.stop_cash:
                            position_value = self.cash * 0.1  # 10% allocation
                            buy_quantity = int(position_value / last_price)
                            
                            if buy_quantity > 0:
                                log.log("BUY", f"MOMENTUM BUY [{current_dt}]: {ticker_symbol} - {buy_quantity} shares at {last_price:.2f} (20-day avg: ${avg_20:.2f})")
                                order = self.create_order(ticker, buy_quantity, "buy")
                                self.submit_order(order)
                                break  # Only one buy per iteration
                        
                        # Simple sell condition: price 2% below 20-day average and have position
                        elif last_price < avg_20 * 0.98 and current_position > 0:
                            sell_quantity = int(current_position * 0.3)  # Sell 30%
                            
                            if sell_quantity > 0:
                                log.log("SELL", f"MOMENTUM SELL [{current_dt}]: {ticker_symbol} - {sell_quantity} shares at {last_price:.2f} (20-day avg: ${avg_20:.2f})")
                                order = self.create_order(ticker, sell_quantity, "sell")
                                self.submit_order(order)
                                break  # Only one sell per iteration
                                
                except Exception as e:
                    continue

    def on_filled_order(self, position, order, price, quantity, multiplier):
        self.log_message(f"Filled order: {order}, {position}, {price}, {quantity}, {multiplier}", show_in_terminal=False)

    def on_new_order(self, order):
        self.log_message(f"New order: {order}", show_in_terminal=False)

if __name__ == "__main__":
    if IS_BACKTESTING:
        start = datetime(2023, 1, 30)
        end = datetime(2024, 10, 31)

        TAStrategy.run_backtest(
            AlpacaBacktesting,
            start,
            end,
            benchmark_asset="SPY",
            risk_free_rate=0.025,
            parameters={
                "tickers": ["AAPL", "GOOGL", "NVDA", "TSLA", "TSM"],
                "start_date": start,
                "end_date": end
            },
            show_progress_bar=True,
            quiet_logs=False,
            save_logfile=False
        )
    else:
        trader = Trader()
        broker = Alpaca(ALPACA_CONFIG)
        strategy = TAStrategy(broker=broker,
                              parameters={
                                  "tickers": "AAPL",
                                  "start_date": datetime(2023, 10, 31),
                                  "end_date": datetime.now()
                              })
        trader.add_strategy(strategy)
        trader.run_all()
        
