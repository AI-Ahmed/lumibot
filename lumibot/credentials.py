# NOTE: 
# This file is not meant to be modified. This file loads the credentials from the ".env" file or secrets and sets them as environment variables.
# If you want to set the environment variables on your computer, you can do so by creating a ".env" file in the root directory of the project
# and adding the variables described in the "Secrets Configuration" section of the README.md file like this (but without the "# " at the front):
# IS_BACKTESTING=True
# POLYGON_API_KEY=p0izKxeskywlLjKi82NLrQPUvSzvlYVT
# etc.

import os
import sys

from .brokers import Alpaca, InteractiveBrokers, InteractiveBrokersREST
from dotenv import load_dotenv
import termcolor
from dateutil import parser

# Configure logging
from lumibot.tools.lumibot_logger import get_logger
logger = get_logger(__name__)


def find_and_load_dotenv(base_dir) -> bool:
    for root, dirs, files in os.walk(base_dir):
        logger.debug(f"Checking {root} for .env file")
        if '.env' in files:
            dotenv_path = os.path.join(root, '.env')
            load_dotenv(dotenv_path)

            # Create a colored message for the log using termcolor
            colored_message = termcolor.colored(f".env file loaded from: {dotenv_path}", "green")
            logger.info(colored_message)

            # Optional local override file. This is intentionally loaded *after* `.env` so it can
            # override settings without requiring edits to the primary file (which may contain
            # shared or sensitive values).
            dotenv_local_path = os.path.join(root, ".env.local")
            if os.path.exists(dotenv_local_path):
                load_dotenv(dotenv_local_path, override=True)
                colored_message = termcolor.colored(f".env.local file loaded from: {dotenv_local_path}", "green")
                logger.info(colored_message)
            return True

    return False


# Get the directory of the original script being run
script_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
logger.debug(f"script_dir: {script_dir}")
_disable_dotenv = os.environ.get("LUMIBOT_DISABLE_DOTENV", "").lower() in ("1", "true", "yes")

if _disable_dotenv:
    # In production backtests we should rely on injected environment variables rather than scanning
    # large directory trees for `.env` files. Recursive scanning can add seconds of startup latency and,
    # worse, can accidentally load an unrelated `.env` if the working directory contains nested repos.
    logger.debug("Skipping .env discovery because LUMIBOT_DISABLE_DOTENV is set.")
    found_dotenv = False
else:
    found_dotenv = find_and_load_dotenv(script_dir)

if not found_dotenv and not _disable_dotenv:
    # Get the root directory of the project
    cwd_dir = os.getcwd()
    logger.debug(f"cwd_dir: {cwd_dir}")
    found_dotenv = find_and_load_dotenv(cwd_dir)

# If no .env file was found, print a warning message
if not found_dotenv:
    # Create a colored message for the log using termcolor
    colored_message = termcolor.colored(
        "No .env file found. This is expected when relying on environment variables or external secrets.",
        "blue",
    )
    logger.debug(colored_message)

# dotenv.load_dotenv()
broker=None

# Check if we are backtesting or not
is_backtesting = os.environ.get("IS_BACKTESTING")
if not is_backtesting or is_backtesting.lower() == "false":
    IS_BACKTESTING = False
elif is_backtesting.lower() == "true":
    IS_BACKTESTING = True
else:
    # Log a warning if the value is not a boolean
    colored_message = termcolor.colored(f"IS_BACKTESTING must be set to 'true' or 'false'. Got '{is_backtesting}'. Defaulting to False.", "yellow")
    logger.warning(colored_message)
    IS_BACKTESTING = False

# Get the backtesting start and end dates
backtesting_start = os.environ.get("BACKTESTING_START")
backtesting_end = os.environ.get("BACKTESTING_END")

# Check if the dates are not None and not empty strings before parsing
BACKTESTING_START = None
if backtesting_start:
    BACKTESTING_START = parser.parse(backtesting_start)
BACKTESTING_END = None
if backtesting_end:
    BACKTESTING_END = parser.parse(backtesting_end)

# Get the backtesting data source
BACKTESTING_DATA_SOURCE = os.environ.get("BACKTESTING_DATA_SOURCE", "yahoo")

# Check if we should hide trades
hide_trades = os.environ.get("HIDE_TRADES")
if not hide_trades or hide_trades.lower() == "false":
    HIDE_TRADES = False
elif hide_trades.lower() == "true":
    HIDE_TRADES = True
else:
    # Log a warning if the value is not a boolean
    colored_message = termcolor.colored(f"HIDE_TRADES must be set to 'true' or 'false'. Got '{hide_trades}'. Defaulting to False.", "yellow")
    logger.warning(colored_message)
    HIDE_TRADES = False

# Check if we should hide positions
hide_positions = os.environ.get("HIDE_POSITIONS")
if not hide_positions or hide_positions.lower() == "false":
    HIDE_POSITIONS = False
elif hide_positions.lower() == "true":
    HIDE_POSITIONS = True
else:
    # Log a warning if the value is not a boolean
    colored_message = termcolor.colored(f"HIDE_POSITIONS must be set to 'true' or 'false'. Got '{hide_positions}'. Defaulting to False.", "yellow")
    logger.warning(colored_message)
    HIDE_POSITIONS = False

# Name for the strategy to be used in the database
STRATEGY_NAME = os.environ.get("STRATEGY_NAME")

# Market to be traded
MARKET = os.environ.get("MARKET")

# Live trading configuration (if applicable)
LIVE_CONFIG = os.environ.get("LIVE_CONFIG")

# Discord credentials
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

# Get SHOW_PLOT and SHOW_INDICATORS from the environment variables, default to True
SHOW_PLOT = os.environ.get("SHOW_PLOT", "True") == "True"
SHOW_INDICATORS = os.environ.get("SHOW_INDICATORS", "True") == "True"
SHOW_TEARSHEET = os.environ.get("SHOW_TEARSHEET", "True") == "True"

# Set DB_CONNECTION_STR to None by default
DB_CONNECTION_STR = None

# Add a warning if ACCOUNT_HISTORY_DB_CONNECTION_STR is set because it is now replaced by DB_CONNECTION_STR
if os.environ.get("ACCOUNT_HISTORY_DB_CONNECTION_STR"):
    print("ACCOUNT_HISTORY_DB_CONNECTION_STR is deprecated and will be removed in a future version. Please use DB_CONNECTION_STR instead.")
    DB_CONNECTION_STR = os.environ.get("ACCOUNT_HISTORY_DB_CONNECTION_STR")

# Database connection string
if os.environ.get("DB_CONNECTION_STR"):
    DB_CONNECTION_STR = os.environ.get("DB_CONNECTION_STR")

# Name for the strategy to be used in the database
STRATEGY_NAME = os.environ.get("STRATEGY_NAME")

# Flag to determine if backtest progress should be logged to a file (True/False)
LOG_BACKTEST_PROGRESS_TO_FILE = os.environ.get("LOG_BACKTEST_PROGRESS_TO_FILE")

BACKTESTING_SHOW_PROGRESS_BAR = os.environ.get("BACKTESTING_SHOW_PROGRESS_BAR", "true").lower() == "true"

# Determine if backtesting logs should be quiet via env variable (default None means not set)
_btl = os.environ.get("BACKTESTING_QUIET_LOGS", None)
if _btl is not None:
    if _btl.lower() == "true":
        BACKTESTING_QUIET_LOGS = True
    elif _btl.lower() == "false":
        BACKTESTING_QUIET_LOGS = False
    else:
        colored_message = termcolor.colored(f"BACKTESTING_QUIET_LOGS must be set to 'true' or 'false'. Got '{_btl}'. Defaulting to None.", "yellow")
        logger.warning(colored_message)
        BACKTESTING_QUIET_LOGS = None
else:
    BACKTESTING_QUIET_LOGS = True  # Default to quiet logs for better performance

# Set a hard limit on the memory polygon uses
POLYGON_MAX_MEMORY_BYTES = os.environ.get("POLYGON_MAX_MEMORY_BYTES")
ALPACA_MAX_MEMORY_BYTES = os.environ.get("ALPACA_MAX_MEMORY_BYTES")

POLYGON_CONFIG = {
    # Add POLYGON_API_KEY to your .env file or set it as secrets
    "API_KEY": os.environ.get("POLYGON_API_KEY"),
}

# Polygon API Key
POLYGON_API_KEY = POLYGON_CONFIG['API_KEY']

# Thetadata Configuration
THETADATA_CONFIG = {
    # Get the ThetaData API key from the .env file or secrets
    "THETADATA_USERNAME": os.environ.get("THETADATA_USERNAME"),
    "THETADATA_PASSWORD": os.environ.get("THETADATA_PASSWORD")
}

# DataBento Configuration
DATABENTO_CONFIG = {
    # Add DATABENTO_API_KEY to your .env file or set them as secrets
    "API_KEY": os.environ.get("DATABENTO_API_KEY"),
    "TIMEOUT": int(os.environ.get("DATABENTO_TIMEOUT", "30")),
    "MAX_RETRIES": int(os.environ.get("DATABENTO_MAX_RETRIES", "3")),
}

# Remote cache configuration (disabled by default)
CACHE_REMOTE_CONFIG = {
    "backend": os.environ.get("LUMIBOT_CACHE_BACKEND", "local"),
    "mode": os.environ.get("LUMIBOT_CACHE_MODE", "disabled"),
    "s3_bucket": os.environ.get("LUMIBOT_CACHE_S3_BUCKET"),
    "s3_prefix": os.environ.get("LUMIBOT_CACHE_S3_PREFIX", ""),
    "s3_region": os.environ.get("LUMIBOT_CACHE_S3_REGION"),
    "s3_access_key_id": os.environ.get("LUMIBOT_CACHE_S3_ACCESS_KEY_ID"),
    "s3_secret_access_key": os.environ.get("LUMIBOT_CACHE_S3_SECRET_ACCESS_KEY"),
    "s3_session_token": os.environ.get("LUMIBOT_CACHE_S3_SESSION_TOKEN"),
    "s3_version": os.environ.get("LUMIBOT_CACHE_S3_VERSION", "v1"),
}

# Alpaca Configuration
ALPACA_CONFIG = {
    # Add ALPACA_API_KEY, ALPACA_API_SECRET, ALPACA_OAUTH_TOKEN, and ALPACA_IS_PAPER to your .env file or set them as secrets
    "API_KEY": os.environ.get("ALPACA_API_KEY"),
    "API_SECRET": os.environ.get("ALPACA_API_SECRET"),
    "OAUTH_TOKEN": os.environ.get("ALPACA_OAUTH_TOKEN"),
    "PAPER": os.environ.get("ALPACA_IS_PAPER").lower() == "true" if os.environ.get("ALPACA_IS_PAPER") else True,
}

# Alpaca OAuth Configuration Constants
ALPACA_OAUTH_CONFIG = {
    "CALLBACK_URL": "https://api.botspot.trade/broker_oauth/alpaca",
    "CLIENT_ID": "6625abd29ce3f95285dfa4405934de83",
    "REDIRECT_URL": "https://botspot.trade/oauth/alpaca/success",
}

# Alpaca test configuration for unit tests
ALPACA_TEST_CONFIG = {  # Paper trading!
    # Add ALPACA_TEST_API_KEY, ALPACA_TEST_API_SECRET, ALPACA_TEST_OAUTH_TOKEN to your .env file or set them as secrets
    "API_KEY": os.environ.get("ALPACA_TEST_API_KEY"),
    "API_SECRET": os.environ.get("ALPACA_TEST_API_SECRET"),
    "OAUTH_TOKEN": os.environ.get("ALPACA_TEST_OAUTH_TOKEN"),
    "PAPER": True
}

# Interactive Brokers Configuration
INTERACTIVE_BROKERS_CONFIG = {
    "SOCKET_PORT": int(os.environ.get("INTERACTIVE_BROKERS_PORT")) if os.environ.get("INTERACTIVE_BROKERS_PORT") else None,
    "CLIENT_ID": int(os.environ.get("INTERACTIVE_BROKERS_CLIENT_ID")) if os.environ.get("INTERACTIVE_BROKERS_CLIENT_ID") else None,
    "IP": os.environ.get("INTERACTIVE_BROKERS_IP", "127.0.0.1"),
    "IB_SUBACCOUNT": os.environ.get("IB_SUBACCOUNT", None)
}

# Interactive Brokers REST Configuration
INTERACTIVE_BROKERS_REST_CONFIG = {
    "IB_USERNAME": os.environ.get("IB_USERNAME"),
    "IB_PASSWORD": os.environ.get("IB_PASSWORD"),
    "IB_ACCOUNT_ID": os.environ.get("IB_ACCOUNT_ID"),
    "API_URL": os.environ.get("IB_API_URL"),
    "RUNNING_ON_SERVER": os.environ.get("RUNNING_ON_SERVER")
}

LUMIWEALTH_API_KEY = os.environ.get("LUMIWEALTH_API_KEY")

# Get TRADING_BROKER and DATA_SOURCE from environment variables
trading_broker_name = os.environ.get("TRADING_BROKER")
data_source_name = os.environ.get("DATA_SOURCE")

broker = None
data_source = None

# Check if we are backtesting or not
is_backtesting = os.environ.get("IS_BACKTESTING")
if not is_backtesting or is_backtesting.lower() == "false":
    IS_BACKTESTING = False
    
    # Determine which trading broker to use based on TRADING_BROKER environment variable or available configs
    if trading_broker_name:
        name = trading_broker_name.lower()
        if name == "alpaca":
            broker = Alpaca(ALPACA_CONFIG)
        elif name in ("ib", "interactivebrokers"):
            broker = InteractiveBrokers(INTERACTIVE_BROKERS_CONFIG)
        elif name in ("ibrest", "interactivebrokersrest"):
            broker = InteractiveBrokersREST(INTERACTIVE_BROKERS_REST_CONFIG)
        else:
            colored_message = termcolor.colored(
                f"Unknown or unsupported trading broker: {trading_broker_name}. "
                "This fork supports Alpaca and Interactive Brokers only.",
                "red",
            )
            logger.error(colored_message)
    else:
        if ALPACA_CONFIG["API_KEY"] or ALPACA_CONFIG["OAUTH_TOKEN"]:
            try:
                broker = Alpaca(ALPACA_CONFIG)
            except ValueError as e:
                if "Either OAuth token or API key/secret must be provided" not in str(e):
                    raise
        elif INTERACTIVE_BROKERS_CONFIG["CLIENT_ID"]:
            broker = InteractiveBrokers(INTERACTIVE_BROKERS_CONFIG)
        elif INTERACTIVE_BROKERS_REST_CONFIG["IB_USERNAME"]:
            broker = InteractiveBrokersREST(INTERACTIVE_BROKERS_REST_CONFIG)
    
    # Determine if we should use a custom data source based on DATA_SOURCE environment variable
    if data_source_name:
        try:
            name = data_source_name.lower()
            if name == "alpaca":
                from .data_sources import AlpacaData
                data_source = AlpacaData(ALPACA_CONFIG)
            elif name in ("ib", "interactivebrokers"):
                from .data_sources import InteractiveBrokersData
                data_source = InteractiveBrokersData(INTERACTIVE_BROKERS_CONFIG)
            elif name in ("ibrest", "interactivebrokersrest"):
                from .data_sources import InteractiveBrokersRESTData
                data_source = InteractiveBrokersRESTData(INTERACTIVE_BROKERS_REST_CONFIG)
            elif name == "yahoo":
                from .data_sources import YahooData
                data_source = YahooData()
                if BACKTESTING_START and BACKTESTING_END:
                    data_source._update_datetime_limits(BACKTESTING_START, BACKTESTING_END)
            elif name in ("alpha_vantage", "alphavantage"):
                from .data_sources import AlphaVantageData
                data_source = AlphaVantageData()
            else:
                colored_message = termcolor.colored(
                    f"Unknown or unsupported data source: {data_source_name}. "
                    "This fork supports yahoo, alpaca, ib, ibrest, alpha_vantage.",
                    "red",
                )
                logger.error(colored_message)
        except ImportError as e:
            colored_message = termcolor.colored(f"Could not import data source {data_source_name}: {str(e)}", "red")
            logger.error(colored_message)
    
    # If we have both a broker and a custom data source, set the broker's data source
    if broker and data_source:
        logger.info(termcolor.colored(f"Using {data_source_name} as data source for {broker.name} broker", "green"))
        # Store the original data source for reference
        original_broker_data_source = broker.data_source
        
        # Set the custom data source
        broker.data_source = data_source

elif is_backtesting.lower() == "true":
    IS_BACKTESTING = True
else:
    # Log a warning if the value is not a boolean
    colored_message = termcolor.colored(f"IS_BACKTESTING must be set to 'true' or 'false'. Got '{is_backtesting}'. Defaulting to False.", "yellow")
    logger.warning(colored_message)
    IS_BACKTESTING = False

# Export variables for use in strategies
BROKER = broker
DATA_SOURCE = data_source

# Alpaca Backtesting Configuration
## Constants for magic numbers and configuration
DEFAULT_END_SHIFT_DAYS = 3
DEFAULT_END_SHIFT_MINUTES = 5
MIN_TRADING_DAYS_FOR_SHIFT = 3
HFT_END_TIME_BUFFER_MINUTES = 5
DEFAULT_CACHE_MAX_MEMORY_MB = 1000
DEFAULT_API_RETRY_ATTEMPTS = 3
DEFAULT_API_RETRY_MIN_WAIT = 4
DEFAULT_API_RETRY_MAX_WAIT = 10
DATA_QUALITY_DROP_THRESHOLD = 0.05  # 5% threshold for data quality warnings
CACHE_VERSION = "v1"  # For cache invalidation when format changes

# HFT Trades Progressive Loading Configuration
# Note: For HFT strategies, we work with hours/minutes, not days (days would be millions of records)
DEFAULT_TRADES_LOOKAHEAD_HOURS = 24  # Download 24 hours ahead of current backtest iteration
DEFAULT_TRADES_MEMORY_WINDOW_HOURS = 48  # Keep last 48 hours of trades in memory
DEFAULT_TRADES_CHUNK_SIZE_HOURS = 1  # Download 1 hour of trades at a time (legacy, use MINUTES for HFT)
ALPACA_HISTORICAL_RATE_LIMIT = 200  # 200 requests per minute for historical data
# Optional override: LUMIBOT_ALPACA_TRADES_RATE_LIMIT (int; 0 or negative = unlimited)
try:
    _at = os.environ.get("LUMIBOT_ALPACA_TRADES_RATE_LIMIT")
    ALPACA_TRADES_RATE_LIMIT_ENV = int(_at) if _at is not None and str(_at).strip() else None
except (ValueError, TypeError):
    ALPACA_TRADES_RATE_LIMIT_ENV = None
