import datetime
import logging
import re
from decimal import Decimal
from typing import Union

import pandas as pd
from lumibot import LUMIBOT_DEFAULT_PYTZ as DEFAULT_PYTZ
from lumibot.tools.helpers import parse_timestep_qty_and_unit, to_datetime_aware

from .asset import Asset
from .dataline import Dataline

# Set the option to raise an error if downcasting is not possible
pd.set_option('future.no_silent_downcasting', True)

class Data:
    """Input and manage Pandas dataframes for backtesting.

    Parameters
    ----------
    asset : Asset Object
        Asset to which this data is attached.
    df : dataframe
        Pandas dataframe containing OHLCV etc. trade data. Loaded by user
        from csv.
        Index is date and must be pandas datetime64.
        Columns are strictly ["open", "high", "low", "close", "volume"]
    quote : Asset Object
        The quote asset for this data. If not provided, then the quote asset will default to USD.
    date_start : Datetime or None
        Starting date for this data, if not provided then first date in
        the dataframe.
    date_end : Datetime or None
        Ending date for this data, if not provided then last date in
        the dataframe.
    trading_hours_start : datetime.time or None
        If not supplied, then default is 0001 hrs.
    trading_hours_end : datetime.time or None
        If not supplied, then default is 2359 hrs.
    timestep : str
        Either "minute" (default) or "day"
    localize_timezone : str or None
        If not None, then localize the timezone of the dataframe to the
        given timezone as a string. The values can be any supported by tz_localize,
        e.g. "US/Eastern", "UTC", etc.

    Attributes
    ----------
    asset : Asset Object
        Asset object to which this data is attached.
    sybmol : str
        The underlying or stock symbol as a string.
    df : pd.DataFrame
        Pandas dataframe containing OHLCV etc trade data. Loaded by user
        from csv.
        Index is date and must be pandas datetime64.
        Columns are strictly ["open", "high", "low", "close", "volume"]
    date_start : Datetime or None
        Starting date for this data, if not provided then first date in
        the dataframe.
    date_end : Datetime or None
        Ending date for this data, if not provided then last date in
        the dataframe.
    trading_hours_start : datetime.time or None
        If not supplied, then default is 0001 hrs.
    trading_hours_end : datetime.time or None
        If not supplied, then default is 2359 hrs.
    timestep : str
        Either "minute" (default) or "day"
    datalines : dict
        Keys are column names like `datetime` or `close`, values are
        numpy arrays.
    iter_index : Pandas Series
        Datetime in the index, range count in values. Used to retrieve
        the current df iteration for this data and datetime.

    Methods
    -------
    set_times
        Sets the start and end time for the data.
    repair_times_and_fill
        After all time series merged, adjust the local dataframe to reindex and fill nan's.
    columns
        Adjust date and column names to lower case.
    set_date_format
        Ensure datetime in local datetime64 format.
    set_dates
        Set start and end dates.
    trim_data
        Trim the dataframe to match the desired backtesting dates.
    to_datalines
        Create numpy datalines from existing date index and columns.
    get_iter_count
        Returns the current index number (len) given a date.
    check_data (wrapper)
        Validates if the provided date, length, timeshift, and timestep
        will return data. Runs function if data, returns None if no data.
    get_last_price
        Gets the last price from the current date.
    _get_bars_dict
        Returns bars in the form of a dict.
    get_bars
        Returns bars in the form of a dataframe.
    """

    MIN_TIMESTEP = "minute"
    TIMESTEP_MAPPING = [
        {"timestep": "day", "representations": ["1D", "day"]},
        {"timestep": "minute", "representations": ["1M", "minute"]},
    ]

    def __init__(
        self,
        asset: Asset,
        df: pd.DataFrame,
        date_start=None,
        date_end=None,
        trading_hours_start=datetime.time(0, 0),
        trading_hours_end=datetime.time(23, 59),
        timestep="minute",
        quote=None,
        timezone=None,
    ):
        self.asset = asset
        self.symbol = self.asset.symbol

        if self.asset.asset_type == "crypto" and quote is None:
            raise ValueError(
                f"A crypto asset {self.symbol} was added to data without a corresponding"
                f"`quote` asset. Please add the quote asset. For example, if trying to add "
                f"`BTCUSD` to data, you would need to add `USD` as the quote asset."
                f"Quote must be provided for crypto assets."
            )
        else:
            self.quote = quote

        # Throw an error if the quote is not an asset object
        if self.quote is not None and not isinstance(self.quote, Asset):
            raise ValueError(
                f"The quote asset for Data must be an Asset object. You provided a {type(self.quote)} object."
            )

        if timestep not in ["minute", "day"]:
            raise ValueError(
                f"Timestep must be either 'minute' or 'day', the value you enetered ({timestep}) is not currently supported."
            )

        self.timestep = timestep
        self.df: pd.DataFrame = self.columns(df)

        if isinstance(self.df.index, pd.MultiIndex):
            self.date_index = self.df.index.levels[1]
        else:
            self.date_index = self.df.index

        self.df = self.set_date_format(self.df, timezone=timezone)
        self.df = self.df.sort_index()

        self.trading_hours_start, self.trading_hours_end = self.set_times(trading_hours_start,
                                                                          trading_hours_end)
        self.date_start, self.date_end = self.set_dates(date_start, date_end)

        self.df = self.trim_data(
            self.df,
            self.date_start,
            self.date_end,
            self.trading_hours_start,
            self.trading_hours_end,
        )
        self.datetime_start = self.date_index[0]
        self.datetime_end = self.date_index[-1]        

    def set_times(self, trading_hours_start, trading_hours_end):
        """Set the start and end times for the data. The default is 0001 hrs to 2359 hrs.

        Parameters
        ----------
        trading_hours_start : datetime.time
            The start time of the trading hours.

        trading_hours_end : datetime.time
            The end time of the trading hours.

        Returns
        -------
        trading_hours_start : datetime.time
            The start time of the trading hours.

        trading_hours_end : datetime.time
            The end time of the trading hours.
        """
        # Set the trading hours start and end times.
        if self.timestep == "minute":
            ts = trading_hours_start
            te = trading_hours_end
        else:
            ts = datetime.time(0, 0)
            te = datetime.time(23, 59, 59, 999999)
        return ts, te

    def columns(self, df):
        # Select columns to use, change to lower case, rename `date` if necessary.
        df.columns = [
            col.lower() if col.lower() in ["open", "high", "low", "close", "volume"] else col for col in df.columns
        ]

        return df

    def set_date_format(self, df, timezone=None):
        """
        Ensure the DataFrame index (or timestamp level in MultiIndex) is in the correct datetime format and timezone.

        Parameters
        ----------
        df : pd.DataFrame
            The DataFrame to process. Can have a single index or a MultiIndex.
        timezone : str or pytz.timezone, optional
            The timezone to localize or convert the index to. If None, uses DEFAULT_PYTZ.

        Returns
        -------
        pd.DataFrame
            The DataFrame with the index (or timestamp level) properly formatted.
        """
        if timezone is None:
            timezone = DEFAULT_PYTZ

        if isinstance(df.index, pd.MultiIndex):
            date_index = df.index.levels[1]
        else:
            date_index = df.index

        # Check if the index is datetime (it has to be), and if it's not then try to find it in the columns
        if not str(date_index.dtype).startswith("datetime"):
            date_cols = [
                "Date",
                "date",
                "Time",
                "time",
                "Datetime",
                "datetime",
                "timestamp",
                "Timestamp",
            ]
            for date_col in date_cols:
                if date_col in df.columns:
                    df[date_col] = pd.to_datetime(df[date_col])
                    df = df.set_index(date_col)
                    break

        # Handle MultiIndex
        if isinstance(df.index, pd.MultiIndex):
            if "timestamp" not in df.index.names:
                raise ValueError("MultiIndex must have a 'timestamp' level")

            # Extract the timestamp level
            timestamp_level = df.index.get_level_values("timestamp")
            timestamp_level = pd.to_datetime(timestamp_level, utc=True)  # Convert to UTC

            # Handle timezone for the timestamp level
            if timestamp_level.tzinfo is None:
                timestamp_level = timestamp_level.tz_localize(timezone)
            elif timestamp_level.tzinfo != timezone:
                timestamp_level = timestamp_level.tz_convert(timezone)

            # Rebuild the MultiIndex with the updated timestamp level
            new_index = pd.MultiIndex.from_arrays(
                [df.index.get_level_values(level) for level in df.index.names if level != "timestamp"] + [timestamp_level],
                names=df.index.names
            )
            df = df.set_index(new_index)
        else:
            # Handle single index
            df.index.name = "datetime"
            df.index = pd.to_datetime(df.index)
            if df.index.tzinfo is None:
                df.index = df.index.tz_localize(timezone)
            elif df.index.tzinfo != timezone:
                df.index = df.index.tz_convert(timezone)

        return df

    def set_dates(self, date_start, date_end):
        # Set the start and end dates of the data.
        for dt in [date_start, date_end]:
            if dt and not isinstance(dt, datetime.datetime):
                raise TypeError(f"Start and End dates must be entries as full datetimes. {dt} " f"was entered")

        if not date_start:
            date_start = self.date_index.min()
        if not date_end:
            date_end = self.date_index.max()

        date_start = to_datetime_aware(date_start)
        date_end = to_datetime_aware(date_end)

        date_start = date_start.replace(hour=0, minute=0, second=0, microsecond=0)
        date_end = date_end.replace(hour=23, minute=59, second=59, microsecond=999999)

        return (
            date_start,
            date_end,
        )

    def trim_data(self, df, date_start, date_end, trading_hours_start, trading_hours_end):
        # Trim the dataframe to match the desired backtesting dates.
        if self.timestep == "minute":
            if isinstance(df.index, pd.MultiIndex):
                # Extract the timestamp level from the MultiIndex
                timestamp_level = df.index.get_level_values("timestamp")
                
                # Extract the time component from the timestamp level
                time_level = timestamp_level.time
                
                # Filter the DataFrame based on the time component
                mask = (time_level >= pd.to_datetime(trading_hours_start).time()) & \
                    (time_level <= pd.to_datetime(trading_hours_end).time())
                df = df[mask]
            else:
                # Handle single index
                df = df.between_time(trading_hours_start, trading_hours_end)

        if df.empty:
            raise ValueError(
                f"When attempting to load a dataframe for {self.asset}, "
                f"an empty dataframe was returned. This is likely due "
                f"to your backtesting start and end dates not being "
                f"within the start and end dates of the data provided. "
                f"\nPlease check that your at least one of your start "
                f"or end dates for backtesting is within the range of "
                f"your start and end dates for your data. "
            )
        return df

    # ./lumibot/build/__editable__.lumibot-3.1.14-py3-none-any/lumibot/entities/data.py:280:
    # FutureWarning: Downcasting object dtype arrays on .fillna, .ffill, .bfill is deprecated and will change in a future version.
    # Call result.infer_objects(copy=False) instead.
    # To opt-in to the future behavior, set `pd.set_option('future.no_silent_downcasting', True)`

    def repair_times_and_fill(self, idx):
        """
        Trim the global index to be within the local data range, reindex the DataFrame,
        and fill missing values. Supports both single-index and multi-index DataFrames.

        Parameters
        ----------
        idx : pd.DatetimeIndex
            The global index to trim and use for reindexing.
        """
        # Trim the global index so that it is within the local data range
        idx = idx[(idx >= self.datetime_start) & (idx <= self.datetime_end)]

        # Handle MultiIndex
        if isinstance(self.df.index, pd.MultiIndex):
            # Extract the timestamp level from the MultiIndex
            timestamp_level = self.df.index.get_level_values("timestamp")
            
            # Create a new index for reindexing
            new_index = pd.MultiIndex.from_arrays(
                [self.df.index.get_level_values(level) for level in self.df.index.names if level != "timestamp"] + [idx],
                names=self.df.index.names
            )
        else:
            # Handle single index
            new_index = idx

        # Reindex the DataFrame and forward-fill missing values
        df = self.df.reindex(new_index, method="ffill")

        # Fill missing volume with 0
        if "volume" in df.columns:
            df.loc[df["volume"].isna(), "volume"] = 0
        else:
            df["volume"] = 0

        # Forward-fill all columns except "open", "high", and "low"
        if any(col in df.columns for col in ["open", "high", "low", "close"]):
            df.loc[:, ~df.columns.isin(["open", "high", "low"])] = df.loc[
                :, ~df.columns.isin(["open", "high", "low"])
            ].ffill()

            # Fill missing "open", "high", and "low" with "close"
            for col in ["open", "high", "low"]:
                if col in df.columns:
                    df.loc[df[col].isna(), col] = df.loc[df[col].isna(), "close"]

        # Update the DataFrame and iterables
        self.df = df

        # Create iter_index and iter_index_dict for iteration
        # Update the `date_index`, too!
        if isinstance(self.df.index, pd.MultiIndex):
            self.date_index = self.df.index.levels[1]
            iter_index = pd.Series(self.date_index)
        else:
            self.date_index = df.index
            iter_index = pd.Series(self.date_index)

        self.iter_index = pd.Series(iter_index.index, index=iter_index)
        self.iter_index_dict = self.iter_index.to_dict()

        # Reset datalines and convert DataFrame to datalines
        self.datalines = dict()
        self.to_datalines()

    def to_datalines(self):
        self.datalines.update(
            {
                "datetime": Dataline(
                    self.asset,
                    "datetime",
                    self.date_index.to_numpy(),
                    self.date_index.dtype,
                )
            }
        )
        setattr(self, "datetime", self.datalines["datetime"].dataline)

        for column in self.df.columns:
            self.datalines.update(
                {
                    column: Dataline(
                        self.asset,
                        column,
                        self.df[column].to_numpy(),
                        self.df[column].dtype,
                    )
                }
            )
            setattr(self, column, self.datalines[column].dataline)

    def get_iter_count(self, dt, is_benchmark_asset):
        # Return the index location for a given datetime.

        # Check if the date is in the dataframe, if not then get the last
        # known data (this speeds up the process)
        i = None

        # Check if we have the iter_index_dict, if not then repair the times and fill (which will create the iter_index_dict)
        if getattr(self, "iter_index_dict", None) is None:
            self.repair_times_and_fill(self.date_index)

        # Search for dt in self.iter_index_dict
        if dt in self.iter_index_dict:
            i = self.iter_index_dict[dt]
        else:
            if is_benchmark_asset:
                i = self.custom_asof(self.iter_index, dt)
            else:
                i = self.iter_index.asof(dt)

        return i

    def custom_asof(self, index, label):
        # Use asof to get the previous label
        result = index.asof(label)
        
        # If the result is NaN, return the next label
        if pd.isna(result):
            # Find the index of the first label greater than the given label
            next_labels = index[index.index > label]
            if not next_labels.empty:
                return next_labels.iloc[0]
            else:
                return None  # No next label exists
        else:
            return result
        
    def check_data(func):
        # Validates if the provided date, length, timeshift, and timestep
        # will return data. Runs function if data, returns None if no data.
        def checker(self, *args, **kwargs):
            if type(kwargs.get("length", 1)) not in [int, float]:
                raise TypeError(f"Length must be an integer. {type(kwargs.get('length', 1))} was provided.")

            dt = args[0]

            # Check if the iter date is outside of this data's date range.
            if dt < self.datetime_start:
                raise ValueError(
                    f"The date you are looking for ({dt}) for ({self.asset}) is outside of the data's date range ({self.datetime_start} to {self.datetime_end}). This could be because the data for this asset does not exist for the date you are looking for, or something else."
                )

            # Search for dt in self.iter_index_dict
            if getattr(self, "iter_index_dict", None) is None:
                self.repair_times_and_fill(self.date_index)

            if dt in self.iter_index_dict:
                i = self.iter_index_dict[dt]
            else:
                # If not found, get the last known data
                i = self.iter_index.asof(dt)

            length = kwargs.get("length", 1)
            timeshift = kwargs.get("timeshift", 0)
            data_index = i + 1 - length - timeshift
            is_data = data_index >= 0
            if not is_data:
                # Log a warning
                logging.warning(
                    f"The date you are looking for ({dt}) is outside of the data's date range ({self.datetime_start} to {self.datetime_end}) after accounting for a length of {kwargs.get('length', 1)} and a timeshift of {kwargs.get('timeshift', 0)}. Keep in mind that the length you are requesting must also be available in your data, in this case we are {data_index} rows away from the data you need."
                )

            res = func(self, *args, **kwargs)
            # print(f"Results last price: {res}")
            return res

        return checker

    @check_data
    def get_last_price(self, dt, length=1, timeshift=0) -> Union[float, Decimal, None]:
        """Returns the last known price of the data.

        Parameters
        ----------
        dt : datetime.datetime
            The datetime to get the last price.
        length : int
            The number of periods to get the last price.
        timestep : str
            The frequency of the data to get the last price.
        timeshift : int
            The number of periods to shift the data.

        Returns
        -------
        float or Decimal or None
        """
        iter_count = self.get_iter_count(dt, False)
        open_price = self.datalines["open"].dataline[iter_count]
        close_price = self.datalines["close"].dataline[iter_count]
        price = close_price if dt > self.datalines["datetime"].dataline[iter_count] else open_price
        return price

    @check_data
    def get_quote(self, dt, length=1, timeshift=0):
        """Returns the last known price of the data.

        Parameters
        ----------
        dt : datetime.datetime
            The datetime to get the last price.
        length : int
            The number of periods to get the last price.
        timestep : str
            The frequency of the data to get the last price.
        timeshift : int
            The number of periods to shift the data.

        Returns
        -------
        dict
        """
        # Check if this data object at least has open, high, low, close, and volume columns
        if not all(
            [
                "open" in self.datalines,
                "high" in self.datalines,
                "low" in self.datalines,
                "close" in self.datalines,
                "volume" in self.datalines,
            ]
        ):
            raise ValueError(
                f"The data object for {self.asset} does not have the necessary columns to get the quote. "
                "Please make sure that the data object has at least the following columns: open, high, low, close, and volume. "
                "This could be an issue with the data source or the data itself, consider changing the data source you are using "
                "or check that the data you are looking for exists in the data source."
            )
        
        # Check if this data object has bid and ask
        if not all(
            [
                "bid" in self.datalines,
                "ask" in self.datalines,
                "bid_size" in self.datalines,
                "bid_condition" in self.datalines,
                "bid_exchange" in self.datalines,
                "ask_size" in self.datalines,
                "ask_condition" in self.datalines,
                "ask_exchange" in self.datalines,
            ]
        ):
            raise ValueError(
                f"The data object for {self.asset} does not have the necessary columns to get the quote. "
                "Please make sure that the data object has at least the following columns: bid and ask. "
                "This could be an issue with the data source or the data itself, consider changing the data source you are "
                "using or check that the data you are looking for exists in the data source. For example, "
                "Polygon does not provide bid and ask data."
            )

        iter_count = self.get_iter_count(dt)
        open = round(self.datalines["open"].dataline[iter_count], 2)
        high = round(self.datalines["high"].dataline[iter_count], 2)
        low = round(self.datalines["low"].dataline[iter_count], 2)
        close = round(self.datalines["close"].dataline[iter_count], 2)
        bid = round(self.datalines["bid"].dataline[iter_count], 2)
        ask = round(self.datalines["ask"].dataline[iter_count], 2)
        volume = round(self.datalines["volume"].dataline[iter_count], 0)
        bid_size = round(self.datalines["bid_size"].dataline[iter_count], 0)
        bid_condition = round(self.datalines["bid_condition"].dataline[iter_count], 0)
        bid_exchange = round(self.datalines["bid_exchange"].dataline[iter_count], 0)
        ask_size = round(self.datalines["ask_size"].dataline[iter_count], 0)
        ask_condition = round(self.datalines["ask_condition"].dataline[iter_count], 0)
        ask_exchange = round(self.datalines["ask_exchange"].dataline[iter_count], 0)

        return {
            "open": open,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "bid": bid,
            "ask": ask,
            "bid_size": bid_size,
            "bid_condition": bid_condition,
            "bid_exchange": bid_exchange,
            "ask_size": ask_size,
            "ask_condition": ask_condition,
            "ask_exchange": ask_exchange
        }

    @check_data
    def _get_bars_dict(self, dt, length=1, timestep=None, timeshift=0):
        """Returns a dictionary of the data.

        Parameters
        ----------
        dt : datetime.datetime
            The datetime to get the data.
        length : int
            The number of periods to get the data.
        timestep : str
            The frequency of the data to get the data.
        timeshift : int
            The number of periods to shift the data.

        Returns
        -------
        dict

        """

        # Get bars.
        end_row = self.get_iter_count(dt, False) - timeshift
        start_row = end_row - length

        if start_row < 0:
            start_row = 0

        # Cast both start_row and end_row to int
        start_row = int(start_row)
        end_row = int(end_row)

        dict = {}
        for dl_name, dl in self.datalines.items():
            dict[dl_name] = dl.dataline[start_row:end_row]

        return dict

    def _get_bars_between_dates_dict(self,
                                     timestep=None,
                                     start_date=None,
                                     end_date=None,
                                     is_benchmark_asset=False):
        """Returns a dictionary of all the data available between the start and end dates.

        Parameters
        ----------
        timestep : str
            The frequency of the data to get the data.
        start_date : datetime.datetime
            The start date to get the data for.
        end_date : datetime.datetime
            The end date to get the data for.
        is_benchmark_asset: bool
            If the given asset is a benchmark asset. Default False.

        Returns
        -------
        dict
        """
        end_row = self.get_iter_count(end_date, is_benchmark_asset=is_benchmark_asset)
        start_row = self.get_iter_count(start_date, is_benchmark_asset=is_benchmark_asset)

        if start_row < 0:
            start_row = 0

        # Cast both start_row and end_row to int
        start_row = int(start_row)
        end_row = int(end_row)

        dict = {}
        for dl_name, dl in self.datalines.items():
            dict[dl_name] = dl.dataline[start_row:end_row]

        return dict

    def get_bars(self, dt, length=1, timestep=MIN_TIMESTEP, timeshift=0):
        """Returns a dataframe of the data.

        Parameters
        ----------
        dt : datetime.datetime
            The datetime to get the data.
        length : int
            The number of periods to get the data.
        timestep : str
            The frequency of the data to get the data. Only minute and day are supported.
        timeshift : int
            The number of periods to shift the data.

        Returns
        -------
        pandas.DataFrame

        """
        # Parse the timestep
        quantity, timestep = parse_timestep_qty_and_unit(timestep)
        num_periods = length

        if timestep == "minute" and self.timestep == "day":
            raise ValueError("You are requesting minute data from a daily data source. This is not supported.")

        if timestep != "minute" and timestep != "day":
            raise ValueError(f"Only minute and day are supported for timestep. You provided: {timestep}")

        agg_column_map = {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
        if timestep == "day" and self.timestep == "minute":
            # If the data is minute data and we are requesting daily data then multiply the length by 1440
            length = length * 1440
            unit = "D"
            data = self._get_bars_dict(dt, length=length, timestep="minute", timeshift=timeshift)

        elif timestep == 'day' and self.timestep == 'day':
            unit = "D"
            data = self._get_bars_dict(dt, length=length, timestep=timestep, timeshift=timeshift)

        else:
            unit = "min"  # Guaranteed to be minute timestep at this point
            length = length * quantity
            data = self._get_bars_dict(dt, length=length, timestep=timestep, timeshift=timeshift)

        if data is None:
            return None

        df = pd.DataFrame(data).assign(datetime=lambda df: pd.to_datetime(df['datetime'])).set_index('datetime')
        if "dividend" in df.columns:
            agg_column_map["dividend"] = "sum"
        df_result = df.resample(f"{quantity}{unit}").agg(agg_column_map)

        # Drop any rows that have NaN values (this can happen if the data is not complete, eg. weekends)
        df_result = df_result.dropna()

        # Remove partial day data from the current day, which can happen if the data is in minute timestep.
        if timestep == "day" and self.timestep == "minute":
            df_result = df_result[df_result.index < dt.replace(hour=0, minute=0, second=0, microsecond=0)]

        # The original df_result may include more rows when timestep is day and self.timestep is minute.
        # In this case, we only want to return the last n rows.
        df_result = df_result.tail(n=int(num_periods))

        return df_result

    def get_bars_between_dates(self,
                               timestep=MIN_TIMESTEP,
                               exchange=None,
                               start_date=None,
                               end_date=None,
                               is_benchmark_asset=False):
        """Returns a dataframe of all the data available between the start and end dates.

        Parameters
        ----------
        timestep : str
            The frequency of the data to get the data. Only minute and day are supported.
        exchange : str
            The exchange to get the data for.
        start_date : datetime.datetime
            The start date to get the data for.
        end_date : datetime.datetime
            The end date to get the data for.
        is_benchmark_asset: bool
            If the given asset is a benchmark asset. Default False.

        Returns
        -------
        pandas.DataFrame
        """
        if timestep == "minute" and self.timestep == "day":
            raise ValueError("You are requesting minute data from a daily data source. This is not supported.")

        if timestep != "minute" and timestep != "day":
            raise ValueError(f"Only minute and day are supported for timestep. You provided: {timestep}")

        if timestep == "day" and self.timestep == "minute":
            dict = self._get_bars_between_dates_dict(timestep=timestep,
                                                     start_date=start_date,
                                                     end_date=end_date,
                                                     is_benchmark_asset=is_benchmark_asset)

            if dict is None:
                return None

            df = pd.DataFrame(dict).set_index("datetime")

            df_result = df.resample("D").agg(
                {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }
            )

            return df_result

        else:
            dict = self._get_bars_between_dates_dict(timestep=timestep,
                                                     start_date=start_date,
                                                     end_date=end_date,
                                                     is_benchmark_asset=is_benchmark_asset)

            if dict is None:
                return None

            df = pd.DataFrame(dict).set_index("datetime")
            return df
