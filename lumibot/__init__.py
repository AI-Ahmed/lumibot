import logging
import os
import sys

import appdirs
import pytz

from loguru import logger as log

# SOURCE PATH
LUMIBOT_SOURCE_PATH = os.path.abspath(os.path.dirname(__file__))

# GLOBAL PARAMETERS
LUMIBOT_DEFAULT_TIMEZONE = "America/New_York"
LUMIBOT_DEFAULT_PYTZ = pytz.timezone(LUMIBOT_DEFAULT_TIMEZONE)

# CACHING CONFIGURATIONS
LUMIBOT_CACHE_FOLDER = appdirs.user_cache_dir(appauthor="LumiWealth", appname="lumibot", version="1.0")

if not os.path.exists(LUMIBOT_CACHE_FOLDER):
    try:
        os.makedirs(LUMIBOT_CACHE_FOLDER)
    except Exception as e:
        log.critical(
            f"""Could not create cache folder because of the following error:
            {e}. Please fix the issue to use data caching."""
        )


# LOGGING CONFIGURATIONS
# Remove default logger first
log.remove()

# Define custom levels with colors
log.level("BUY", no=25, color="<green>")
log.level("SELL", no=35, color="<red>")

# Dynamic format function for terminal with granular color control
def dynamic_format(record):
    level = record["level"].name
    
    if level == "BUY":
        return "\n<green>{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {message}</green>\n"
    elif level == "SELL":
        return "\n<red>{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {message}</red>\n"
    else:
        # For standard log levels, use individual coloring for each component
        # Let loguru handle the level coloring automatically
        return "\n<white>{time:YYYY-MM-DD HH:mm:ss.SSS}</white> | <level>{level: <8}</level> | <light-blue>{name}:{function}:{line}</light-blue> - {message}\n"

# Add sink for buy.log (file output)
log.add(
    "logs/buy.log",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {message}",
    filter=lambda record: record["level"].name == "BUY"
)

# Add sink for sell.log (file output)
log.add(
    "logs/sell.log",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {message}",
    filter=lambda record: record["level"].name == "SELL"
)

# Add terminal sink with dynamic coloring
log.add(
    sink=sys.stderr,
    format=dynamic_format,
    level="DEBUG"  # Captures all levels including BUY/SELL
)

# Create bound loggers
buy_logger = log.bind(buy=True)
sell_logger = log.bind(sell=True)

# Export the loggers
__all__ = ["log", "buy_logger", "sell_logger"]