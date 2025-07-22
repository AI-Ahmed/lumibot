import os
import sys
import warnings
import importlib

from lumibot.tools.lumibot_logger import get_logger

logger = get_logger(__name__)

# Get and display the version
try:
    from importlib.metadata import version
    __version__ = version("lumibot")
except ImportError:
    # Fallback for Python < 3.8
    try:
        import pkg_resources
        __version__ = pkg_resources.get_distribution("lumibot").version
    except:
        __version__ = "unknown"
except:
    __version__ = "unknown"

print(f"Lumibot version {__version__}")

# Get the major and minor Python version
major, minor = sys.version_info[:2]

# Check if Python version is less than 3.10
if (major, minor) < (3, 10):
    warnings.warn("Lumibot requires Python 3.10 or higher.", RuntimeWarning)

from loguru import logger as log

# SOURCE PATH
# Import constants from constants module
from .constants import (
    LUMIBOT_SOURCE_PATH,
    LUMIBOT_DEFAULT_TIMEZONE,
    LUMIBOT_DEFAULT_PYTZ,
    LUMIBOT_DEFAULT_QUOTE_ASSET_SYMBOL,
    LUMIBOT_DEFAULT_QUOTE_ASSET_TYPE,
    LUMIBOT_CACHE_FOLDER
)

# Import main submodules
from . import strategies
from . import brokers
from . import backtesting
from . import entities
from . import data_sources
from . import traders

# Ensure cache folder exists
if not os.path.exists(LUMIBOT_CACHE_FOLDER):
    try:
        os.makedirs(LUMIBOT_CACHE_FOLDER)
    except Exception as e:
        logger.critical(
            f"""Could not create cache folder because of the following error:
            {e}. Please fix the issue to use data caching."""
        )

# ================================
# LOGGING CONFIGURATIONS
# ================================

# Remove default logger first
log.remove()
# Define custom levels with colors
log.level("BUY", no=25, color="<bold><green>")
log.level("SELL", no=35, color="<bold><red>")

# Dynamic format function for terminal with granular color control
def dynamic_format(record):
    level = record["level"].name
    
    if level == "BUY":
        return "\n<fg #00ff00><bold> {time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | 🟢 {message}</bold></fg #00ff00>\n"
    elif level == "SELL":
        return "\n<fg #ff0000><bold> {time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | 🔴 {message}</bold></fg #ff0000>\n"
    else:
        # For standard log levels, use individual coloring for each component
        # Let loguru handle the level coloring automatically
        return "\n<white>{time:YYYY-MM-DD HH:mm:ss.SSS}</white> | <level>{level: <8}</level> | <cyan>{name}:{function}:{line}</cyan> - {message}\n"

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


# ================================
# Backward Compatibility Aliases
# ================================

# Some older code and documentation may still refer to the top-level
# package name `entities` (e.g. `entities.asset`) that existed in
# earlier Lumibot versions.  To avoid breaking those references we
# expose `lumibot.entities` and its sub-modules under the legacy name
# when the library is imported.

# Map the root package alias.
import lumibot.entities as _lb_entities
sys.modules.setdefault("entities", _lb_entities)

# Expose common sub-modules (asset, bars, data, order, position, trading_fee)
# so that e.g. `import entities.asset` keeps working.
for _sub in ("asset", "bars", "data", "order", "position", "trading_fee"):
    _full = f"lumibot.entities.{_sub}"
    _alias = f"entities.{_sub}"
    try:
        _mod = importlib.import_module(_full)
        sys.modules[_alias] = _mod
    except ModuleNotFoundError as e:
        # If a particular sub-module was removed or fails to import (e.g., due to deeper issues like 'fp')
        # log a warning and skip. Sphinx will simply not find this specific alias.
        logger.warning(f"[lumibot/__init__.py] Could not create alias '{_alias}' for '{_full}': {e}")
        # Ensure the problematic alias isn't lingering if it was partially set or if it's a mock
        if _alias in sys.modules and isinstance(sys.modules[_alias], importlib.util.LazyLoader):
            pass # Don't remove if it's a lazy loader that might resolve later or differently
        elif _alias in sys.modules:
            # If it's a real module or a simple mock that failed, best to remove its alias attempt
            # to avoid Sphinx confusion with a potentially broken module object.
            # However, if Sphinx/autodoc is running, it might be safer to leave mocks as they are.
            # For now, we'll log and continue, letting Sphinx handle missing modules.
            # Reconsidering removal: could interfere with Sphinx's own mock handling.
            pass # Reconsidering removal: could interfere with Sphinx's own mock handling
    except ImportError as e: # Catch other import-related errors
        logger.warning(f"[lumibot/__init__.py] ImportError while creating alias '{_alias}' for '{_full}': {e}")
    except Exception as e: # Catch any other unexpected errors during aliasing
        logger.warning(f"[lumibot/__init__.py] Unexpected error creating alias '{_alias}' for '{_full}': {e}")

# Export the default timezone constants so they can be imported by other modules
__all__ = [
    '__version__',
    'LUMIBOT_DEFAULT_TIMEZONE',
    'LUMIBOT_DEFAULT_PYTZ',
    'LUMIBOT_DEFAULT_QUOTE_ASSET_SYMBOL',
    'LUMIBOT_DEFAULT_QUOTE_ASSET_TYPE',
    'LUMIBOT_SOURCE_PATH',
    'LUMIBOT_CACHE_FOLDER',
    'strategies',
    'brokers',
    'backtesting',
    'entities',
    'data_sources',
    'traders',
    'log',
    'buy_logger',
    'sell_logger'
]
