import os
import re
import sys
import datetime as dt

import pandas_market_calendars as mcal
from termcolor import colored

from lumibot import LUMIBOT_DEFAULT_PYTZ
import pandas as pd


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


def get_trading_days(market="NYSE", start_date="1950-01-01", end_date=None):
    format_datetime = lambda dt: dt.to_pydatetime().astimezone(LUMIBOT_DEFAULT_PYTZ)
    start_date = to_datetime_aware(pd.to_datetime(start_date))
    today = get_lumibot_datetime()
    # macl's "24/7" calendar doesn't return consecutive days, so need to be generated manually.
    if market == "24/7":
        market_open = pd.date_range(
            start=start_date, end=end_date or today).to_frame(index=False,name="market_open")
        market_close = pd.date_range(
            start=start_date.replace(hour=23,minute=59,second=59,microsecond=999999), 
            end=end_date or today.replace(hour=23,minute=59,second=59,microsecond=999999)).to_frame(index=False,name="market_close")
        index = pd.date_range(
            start=start_date.replace(tzinfo=None), end=end_date or today.replace(tzinfo=None))
        days = pd.concat([market_open, market_close], axis=1)
        days.index = index
    else:
        nyse = mcal.get_calendar(market)
        days = nyse.schedule(start_date=start_date, end_date=end_date or today)
        days.market_open = days.market_open.apply(format_datetime)
        days.market_close = days.market_close.apply(format_datetime)
    return days


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

    # Assembly
    progress_line = f"\r{prefix} |{colored_bar}| {percent_str} {time_info}"
    if portfolio_info:
        progress_line += f" {portfolio_info}"
        
    # Truncate if over terminal width (ANSI codes aren't counted)
    max_allowed = os.get_terminal_size().columns if 'TERM' in os.environ else 120
    progress_line = progress_line[:max_allowed]

    file.write(progress_line)
    file.flush()

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
    expiration_date : dt.date or dt.datetime
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