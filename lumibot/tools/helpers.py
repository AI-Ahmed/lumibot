import os
import re
import sys
from decimal import Decimal, ROUND_HALF_EVEN

import pytz
import datetime as dt

import pandas as pd
import pandas_market_calendars as mcal
from pandas_market_calendars.market_calendar import MarketCalendar
from termcolor import colored

from ..constants import LUMIBOT_DEFAULT_PYTZ, LUMIBOT_DEFAULT_TIMEZONE


def get_chunks(l, chunk_size):
    chunks = []
    for i in range(0, len(l), chunk_size):
        chunks.append(l[i: i + chunk_size])
    return chunks


def deduplicate_sequence(seq, key=""):
    seen = set()
    pos = 0

    if key:
        get_ref = lambda item: getattr(item, key)
    else:
        get_ref = lambda item: item

    for item in seq:
        ref = get_ref(item)
        if ref not in seen:
            seen.add(ref)
            seq[pos] = item
            pos += 1
    del seq[pos:]
    return seq


class TwentyFourSevenCalendar(MarketCalendar):
    """
    Calendar for markets that trade 24/7, like crypto markets.
    Market open is set to midnight (00:00) and close to 23:59 for each day.
    """

    regular_market_times = {
        'market_open': [(None, dt.time(0, 0))],
        'market_close': [(None, dt.time(23, 59, 59, 999999))],
    }

    def __init__(self, tzinfo: str | pytz.BaseTzInfo = 'UTC'):
        self._tzinfo = pytz.timezone(tzinfo) if isinstance(tzinfo, str) else tzinfo
        super().__init__()

    @property
    def name(self):
        return "24/7"

    @property
    def tz(self):
        return self._tzinfo

    @property
    def open_time_default(self):
        return dt.time(0, 0)

    @property
    def close_time_default(self):
        return dt.time(23, 59)

    @property
    def regular_holidays(self):
        return []

    @property
    def special_closes(self):
        return []

    @property
    def special_closes_adherence(self):
        return []

    @property
    def special_opens(self):
        return []

    @property
    def special_opens_adherence(self):
        return []

    def valid_days(self, start_date, end_date, tz=None):
        return pd.date_range(start=start_date, end=end_date, freq='D')


def get_trading_days(
        market="NYSE",
        start_date="1950-01-01",
        end_date=None,
        tzinfo: pytz.tzinfo = pytz.timezone(LUMIBOT_DEFAULT_TIMEZONE)
) -> pd.DataFrame:
    """
    Gets a schedule of trading days and corresponding market open/close times
    for a specified market between given start and end dates, including proper
    timezone handling for datetime objects.

    Args:
        market (str, optional): Market identifier for which the trading days
            are to be retrieved. Defaults to "NYSE".
        start_date (str or datetime-like, optional): The start date for the
            range of trading days. Defaults to "1950-01-01".
        end_date (str or datetime-like, optional): The end date (exclusive) for
            the range of trading days. If not specified, the current date is used.
            Defaults to None.
        tzinfo (pytz.timezone, optional): Timezone information used for
            converting datetime objects. Defaults to pytz.timezone(LUMIBOT_DEFAULT_TIMEZONE).

    Returns:
        DataFrame: A pandas DataFrame containing the trading schedule with
            columns for 'market_open' and 'market_close', adjusted to the
            specified timezone.
    """
    if not isinstance(tzinfo, pytz.BaseTzInfo):
        raise TypeError('tzinfo must be a pytz.tzinfo object.')

    # More robust datetime conversion with explicit timezone handling
    def format_datetime(dtm):
        if pd.isna(dtm):
            return dtm
        # Convert to Python datetime and ensure proper timezone conversion
        return pd.Timestamp(dtm).tz_convert(tzinfo).to_pydatetime()

    def ensure_tz_aware(dtm, tzinfo):
        dtm = pd.to_datetime(dtm)
        return dtm.tz_convert(tzinfo) if dtm.tz is not None else dtm.tz_localize(tzinfo)

    start_date = ensure_tz_aware(start_date, tzinfo)
    if end_date is not None:
        end_date = ensure_tz_aware(end_date, tzinfo)
    else:
        end_date = ensure_tz_aware(get_lumibot_datetime(), tzinfo)

    if market == "24/7":
        cal = TwentyFourSevenCalendar(tzinfo=tzinfo)
    else:
        cal = mcal.get_calendar(market)

    # Make end_date exclusive by moving it one day earlier
    schedule_end = pd.Timestamp(end_date) - pd.Timedelta(days=1)
    days = cal.schedule(start_date=start_date, end_date=schedule_end, tz=tzinfo)
    days.market_open = days.market_open.apply(format_datetime)
    days.market_close = days.market_close.apply(format_datetime)
    return days


def get_trading_times(
        pcal: pd.DataFrame,
        timestep: str = 'day'
) -> pd.DatetimeIndex:
    """
    Generate a DatetimeIndex of trading times based on market calendar and timestep

    Parameters:
    -----------
    pcal : pd.DataFrame
        DataFrame with columns 'market_open' and 'market_close' containing datetime objects
    timestep : str
        'day' for daily bars or 'minute' for minute bars

    Returns:
    --------
    pd.DatetimeIndex : Index of all trading times
    """

    if timestep.lower() not in ['day', 'minute']:
        raise ValueError("timestep must be 'day' or 'minute'")

    if timestep.lower() == 'day':
        dates = pd.DatetimeIndex(pcal['market_open'])
        return dates

    # For minute bars, we need to generate minutes between open and close
    trading_minutes = []

    for _, row in pcal.iterrows():
        start = row['market_open']
        end = row['market_close']

        # Check if it's a 24/7 market by checking if close time is 23:59
        is_24_7 = end.hour == 23 and end.minute == 59

        # Generate minute bars between open and close
        minutes = pd.date_range(start=start, end=end, freq='min')

        # Only remove the last minute for non-24/7 markets
        if not is_24_7:
            minutes = minutes[:-1]

        trading_minutes.extend(minutes)

    return pd.DatetimeIndex(trading_minutes)


def date_n_trading_days_from_date(
        n_days: int,
        start_datetime: dt.datetime,
        market: str = "NYSE"
) -> dt.date:
    """
    Get the trading date n_days from start_datetime.
    Positive n_days means going backwards in time (earlier dates).
    Negative n_days means going forwards in time (later dates).
    """
    if n_days == 0:
        return start_datetime.date()
    if not isinstance(start_datetime, dt.datetime):
        raise ValueError("start_datetime must be datetime")

    if start_datetime.tzinfo is None:
        start_datetime = LUMIBOT_DEFAULT_PYTZ.localize(start_datetime)

    tzinfo = start_datetime.tzinfo

    # Special handling for 24/7 market
    if market == "24/7":
        return (start_datetime - dt.timedelta(days=n_days)).date()

    # Regular market handling
    buffer_bars = max(10, abs(n_days) + (abs(n_days) // 5) * 3)  # Padding for weekends/holidays

    # Calculate date range based on direction
    date_range = {
        'market': market,
        'tzinfo': tzinfo,
    }
    if n_days > 0:
        date_range.update({
            'start_date': (start_datetime - dt.timedelta(days=n_days + buffer_bars)).date().isoformat(),
            'end_date': (start_datetime + dt.timedelta(days=1)).date().isoformat(),  # Add one day to include end date
        })
    else:
        date_range.update({
            'start_date': start_datetime.date().isoformat(),
            'end_date': (start_datetime + dt.timedelta(days=abs(n_days) + buffer_bars + 1)).date().isoformat(),
            # Add one day
        })

    trading_days = get_trading_days(**date_range)
    start_datetime_naive = start_datetime.replace(tzinfo=None)

    # Find index and calculate result
    start_index = (trading_days.index.get_loc(start_datetime_naive)
                   if start_datetime_naive in trading_days.index
                   else trading_days.index.get_indexer([start_datetime_naive], method='bfill')[0])

    return trading_days.index[start_index - n_days].date()


def is_market_open(
        dtm: dt.datetime,
        market: str = "NYSE"
) -> bool:
    """
    Checks if the market is open at a given timezone-aware datetime.

    Args:
        dtm: A timezone-aware datetime object.
        market: A string representing the market (e.g., "NYSE").

    Returns:
        True if the market is open, False otherwise.
    """
    try:
        cal = mcal.get_calendar(market)
    except RuntimeError:
        print(f"Market calendar '{market}' not found.")
        return False

    try:
        schedule = cal.schedule(
            start_date=dtm - dt.timedelta(days=1),
            end_date=dtm,
            tz=dtm.tzinfo
        )
    except Exception as e:
        print(e)
        return False

    try:
        return cal.open_at_time(schedule, dtm)
    except ValueError:
        return False
    except Exception as e:
        print(e)


class ComparaisonMixin:
    COMPARAISON_PROP = "timestamp"

    def __eq__(self, other):
        return getattr(self, self.COMPARAISON_PROP) == getattr(other, self.COMPARAISON_PROP)

    def __ne__(self, other):
        return getattr(self, self.COMPARAISON_PROP) != getattr(other, self.COMPARAISON_PROP)

    def __gt__(self, other):
        return getattr(self, self.COMPARAISON_PROP) > getattr(other, self.COMPARAISON_PROP)

    def __ge__(self, other):
        return getattr(self, self.COMPARAISON_PROP) >= getattr(other, self.COMPARAISON_PROP)

    def __lt__(self, other):
        return getattr(self, self.COMPARAISON_PROP) < getattr(other, self.COMPARAISON_PROP)

    def __le__(self, other):
        return getattr(self, self.COMPARAISON_PROP) >= getattr(other, self.COMPARAISON_PROP)


import os
import sys
import datetime as dt
from termcolor import colored  # Ensure termcolor is installed with pip

# Global flag to track if a progress bar is currently displayed
_progress_bar_active = False
_progress_bar_completed = False

def print_progress_bar(
    value,
    start_value,
    end_value,
    backtesting_started,
    file=sys.stdout,
    length=None,
    prefix="Progress",
    suffix="",
    decimals=2,
    fill=chr(9608),
    cash=None,
    portfolio_value=None,
):
    """
    Prints a dynamically sized progress bar with time estimates and portfolio value.
    
    Args:
        value (float): Current iteration value
        start_value (float): Starting value
        end_value (float): Ending value
        backtesting_started (datetime): When backtesting started
        file (io.TextIOWrapper): Output stream (default: sys.stdout)
        length (int): Optional fixed bar length
        prefix (str): Text before bar
        suffix (str): Text after bar (unused)
        decimals (int): Decimal places for percentage
        fill (str): Bar fill character
        cash (float): Current cash (unused)
        portfolio_value (float): Current portfolio value
    """
    global _progress_bar_active, _progress_bar_completed
    
    # Ensure total_length is numeric
    total_length = end_value - start_value
    if isinstance(total_length, dt.timedelta):
        # If start_value and end_value are datetime objects, convert to total seconds
        total_length = total_length.total_seconds()
        current_length = (value - start_value).total_seconds()
    else:
        # Otherwise, treat as numeric
        current_length = value - start_value

    if total_length <= 0:
        return  # Prevent division by zero

    # Progress calculation
    progress = max(min(current_length / total_length, 1.0), 0.0)
    percent = progress * 100
    percent_str = f"{percent:.{decimals}f}%"

    # If progress is complete and we've already shown the final progress bar, don't show it again
    if progress >= 1.0 and _progress_bar_completed:
        return

    # Time calculations
    now = dt.datetime.now()
    elapsed = now - backtesting_started
    elapsed_str = str(elapsed).split('.')[0]

    # ETA calculation
    eta_str = ""
    if progress > 0:
        eta_seconds = elapsed.total_seconds() * (1/progress - 1)
        eta = dt.timedelta(seconds=int(eta_seconds))
        eta_str = str(eta).split('.')[0]
        time_info = f"[Elapsed: {elapsed_str} | ETA: {eta_str}]"
    else:
        time_info = f"[Elapsed: {elapsed_str}]"

    # Portfolio formatting
    portfolio_info = ""
    if portfolio_value is not None:
        portfolio_info = f"Portfolio: ${portfolio_value:,.2f}"

    # Dynamic bar sizing
    if length is None:
        try:
            term_width = os.get_terminal_size().columns
            # Calculate space needed for text elements
            text_components = [
                prefix, " |", "| ", percent_str,
                time_info, " " + portfolio_info if portfolio_info else ""
            ]
            reserved_space = sum(len(str(c)) for c in text_components)
            length = max(10, term_width - reserved_space - 4)  -9  # ANSI code buffer
        except:
            length = 40  # Fallback length

    # Bar construction
    filled_len = int(length * progress)
    bar = fill * filled_len + '-' * (length - filled_len)
    colored_bar = colored(bar, 'green')

    # Assembly - handle progress bar state
    if progress >= 1.0:
        # Progress is complete - write final bar and set completion flags
        progress_line = f"{prefix} |{colored_bar}| {percent_str} {time_info}"
        if portfolio_info:
            progress_line += f" {portfolio_info}"
        
        # Clear any existing progress line and write the final one with newline
        file.write(f"\r{' ' * 120}\r{progress_line}\n")
        _progress_bar_active = False
        _progress_bar_completed = True
    else:
        # Progress update - use carriage return to overwrite
        progress_line = f"\r{prefix} |{colored_bar}| {percent_str} {time_info}"
        if portfolio_info:
            progress_line += f" {portfolio_info}"
        
        # Truncate if over terminal width (ANSI codes aren't counted)
        try:
            max_allowed = os.get_terminal_size().columns if 'TERM' in os.environ else 120
        except OSError:
            # Environments without a real TTY (CI, some IDE terminals) can raise OSError
            max_allowed = 120
        
        progress_line = progress_line[:max_allowed]
        
        file.write(progress_line)
        _progress_bar_active = True
        _progress_bar_completed = False
    
    file.flush()

def is_progress_bar_active():
    """Check if a progress bar is currently displayed on the terminal."""
    global _progress_bar_active
    return _progress_bar_active

def clear_progress_bar_for_logging():
    """Clear the progress bar to make room for log messages."""
    global _progress_bar_active
    if _progress_bar_active:
        sys.stdout.write('\n')
        sys.stdout.flush()
        _progress_bar_active = False

def reset_progress_bar_state():
    """Reset the progress bar state for a new backtest session."""
    global _progress_bar_active, _progress_bar_completed
    _progress_bar_active = False
    _progress_bar_completed = False

def get_lumibot_datetime():
    return dt.datetime.now().astimezone(LUMIBOT_DEFAULT_PYTZ)


def to_datetime_aware(dt_in):
    """Convert naive time to datetime aware on default timezone."""
    if not dt_in:
        return dt_in
    elif isinstance(dt_in, dt.datetime) and (dt_in.tzinfo is None):
        return LUMIBOT_DEFAULT_PYTZ.localize(dt_in)
    elif isinstance(dt_in, dt.datetime) and (dt_in.tzinfo.utcoffset(dt_in) is None):
        # TODO: This will fail because an exception is thrown if tzinfo is not None.
        return LUMIBOT_DEFAULT_PYTZ.localize(dt_in)
    else:
        return dt_in


def parse_symbol(symbol):
    """
    Parse the given symbol and determine if it's an option or a stock.
    For options, extract and return the stock symbol, expiration date (as a datetime.date object),
    type (call or put), and strike price.
    For stocks, simply return the stock symbol.
    TODO: Crypto and Forex support
    """
    # Check that the symbol is a string
    if not isinstance(symbol, str):
        return {"type": None}
    
    # Pattern to match the option symbol format
    option_pattern = r"([A-Z]+)(\d{6})([CP])(\d+)"

    match = re.match(option_pattern, symbol)
    if match:
        stock_symbol, expiration, option_type, strike_price = match.groups()
        expiration_date = dt.datetime.strptime(expiration, "%y%m%d").date()
        option_type = "CALL" if option_type == "C" else "PUT"
        return {
            "type": "option",
            "stock_symbol": stock_symbol,
            "expiration_date": expiration_date,
            "option_type": option_type,
            "strike_price": round(float(strike_price) / 1000, 3),  # assuming strike price is in thousandths
        }
    else:
        return {"type": "stock", "stock_symbol": symbol}


def create_options_symbol(stock_symbol, expiration_date, option_type, strike_price):
    """
    Create an option symbol string from its components.

    Parameters
    ----------
    stock_symbol : str
        The stock symbol, e.g., 'AAPL'.
    expiration_date : dtm.date or datetime
        The expiration date of the option.
    option_type : str
        The type of the option, either 'Call' or 'Put'.
    strike_price : float
        The strike price of the option.

    Returns
    -------
    str
        The formatted option symbol.
    """
    # Format the expiration date
    if isinstance(expiration_date, str):
        expiration_date = dt.datetime.strptime(expiration_date, "%Y-%m-%d").date()
    expiration_str = expiration_date.strftime("%y%m%d")

    # Determine the option type character
    option_char = "C" if option_type.lower() == "call" else "P"

    # Format the strike price, assuming it needs to be in thousandths
    strike_price_str = f"{int(strike_price * 1000):08d}"

    return f"{stock_symbol}{expiration_str}{option_char}{strike_price_str}"


def parse_timestep_qty_and_unit(timestep):
    """
    Parse the timestep string and return the quantity and unit.

    Parameters
    ----------
    timestep : str
        The timestep string to parse.

    Returns
    -------
    tuple
        The quantity and unit.
    """

    quantity = 1
    unit = timestep
    m = re.search(r"(\d+)\s*(\w+)", timestep)
    if m:
        quantity = int(m.group(1))
        unit = m.group(2).rstrip("s")  # remove trailing 's' if any

    return quantity, unit


def get_decimals(number):
    return len(str(number).split('.')[-1]) if '.' in str(number) else 0


def quantize_to_num_decimals(num: float, num_decimals: int) -> float:
    if isinstance(num, Decimal):
        num = num
    elif isinstance(num, float):
        num = Decimal(str(num))
    else:
        raise ValueError(f"{num} is not a Decimal or float")

    # Create the proper decimal format (e.g., '0.01' for 2 decimals)
    decimal_format = Decimal('0.' + '0' * num_decimals)

    # quantize num using ROUND_HALF_EVEN
    quantized_num = num.quantize(decimal_format, rounding=ROUND_HALF_EVEN)
    return float(quantized_num)


def has_more_than_n_decimal_places(number: float, n: int) -> bool:
    """Return True if the number has more than n decimal places, False otherwise."""

    # Convert the number to a string
    number_str = str(number)

    # Split the string at the decimal point
    if '.' in number_str:
        decimal_part = number_str.split('.')[1]
        # Check if the length of the decimal part is greater than n
        return len(decimal_part) > n
    else:
        return False


def get_timezone_from_datetime(dtm: dt.datetime) -> pytz.timezone:
    """Convert datetime's timezone to pytz.timezone, handling both pytz and zoneinfo cases"""
    if dtm.tzinfo is None:
        return LUMIBOT_DEFAULT_PYTZ

    # If it's already a pytz timezone (checking both DstTzInfo and StaticTzInfo)
    if isinstance(dtm.tzinfo, (pytz.tzinfo.DstTzInfo, pytz.tzinfo.StaticTzInfo)):
        return dtm.tzinfo

    # Try different ways to get timezone name
    try:
        # Try key or zone attribute (works for both zoneinfo and pytz)
        if hasattr(dtm.tzinfo, 'key'):
            return pytz.timezone(dtm.tzinfo.key)
        elif hasattr(dtm.tzinfo, 'zone'):
            return pytz.timezone(dtm.tzinfo.zone)
        # Try getting string representation (fallback)
        timezone_name = str(dtm.tzinfo)
        return pytz.timezone(timezone_name)
    except (AttributeError, pytz.exceptions.UnknownTimeZoneError):
        return LUMIBOT_DEFAULT_PYTZ

