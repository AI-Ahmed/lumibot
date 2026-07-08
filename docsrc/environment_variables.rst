.. _environment_variables:

Environment Variables
=====================

LumiBot supports configuring many behaviors via environment variables. This page documents the variables most commonly used for **backtesting**, **broker credentials**, and **remote caching**.

.. important::

   **Never commit secrets** (API keys, passwords, AWS secret keys) into any repo or docs. Document variable names and semantics only.

Backtesting configuration
-------------------------

LUMIBOT_DISABLE_DOTENV
^^^^^^^^^^^^^^^^^^^^^^

- Purpose: Disable recursive ``.env`` discovery (directory scanning) at startup.
- Values: truthy enables (``1``, ``true``, ``yes``); unset/``0`` disables.
- Default: disabled.
- Notes:
  - Recursive ``.env`` scanning can add startup latency and can accidentally load the wrong ``.env`` when running in a directory with nested repos.
  - In production/BotManager backtests we rely on injected environment variables, so ``.env`` discovery should be off.

IS_BACKTESTING
^^^^^^^^^^^^^^

- Purpose: Signals backtesting mode for certain code paths.
- Values: ``True`` / ``False`` (string).

BACKTESTING_START / BACKTESTING_END
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- Purpose: Default date range used when dates are not passed in code.
- Format: ``YYYY-MM-DD``

BACKTESTING_BUDGET
^^^^^^^^^^^^^^^^^^

- Purpose: Override the starting cash used for backtests (initial portfolio cash).
- Format: Positive number. Accepted examples: ``500``, ``5000``, ``5k``, ``1_000_000``, ``$10,000``.
- Notes:
  - When set, this value is preferred over any ``budget=`` passed in strategy code, so it can be controlled per-run via injected environment variables.
  - Default (when unset and no code budget is provided): ``100000``.

BACKTESTING_DATA_SOURCE
^^^^^^^^^^^^^^^^^^^^^^^

- Purpose: Select the backtesting datasource **even if your code passes a `datasource_class`**.
- Values (case-insensitive):
  - ``yahoo`` (default)
  - ``alpaca``
  - ``ibkr`` / ``interactivebrokersrest`` / ``interactive_brokers_rest`` (IBKR Client Portal REST)
  - ``none`` to disable the env override and rely on code.

Testing / CI guardrails
-----------------------

LUMIBOT_ACCEPTANCE_TRIPWIRE
^^^^^^^^^^^^^^^^^^^^^^^^^^^

- Purpose: **Acceptance backtests only** — when truthy, a Python startup hook aborts the subprocess the moment it attempts to call a remote data service.
- Values: truthy enables (``1``, ``true``, ``yes``); unset/``0`` disables.
- Notes:
  - This is an engineering/CI guardrail to enforce “warm-cache” acceptance backtests. It should not be used for normal production backtests.
  - When triggered, it prints a marker and exits the subprocess with a non-zero code so the test fails reliably.

Backtest artifacts + UX flags
-----------------------------

SHOW_PLOT / SHOW_INDICATORS / SHOW_TEARSHEET
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- Purpose: Enable/disable artifact generation.
- Values: ``True`` / ``False`` (string).

LUMIBOT_WRITE_INDICATORS_HTML
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- Purpose: When truthy, write ``*_indicators.html`` chart files during backtests. The indicators HTML duplicates trade markers shown in the trades plot; by default it is not generated to avoid redundancy.
- Values: truthy enables (``1``, ``true``, ``yes``); unset/``0`` disables.
- Default: disabled. Set to enable if you need the indicators HTML chart.
- Notes: ``*_indicators.csv`` and ``*_indicators.parquet`` are always emitted for downstream tools regardless of this flag.

LUMIBOT_BACKTEST_PARQUET_MODE
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- Purpose: Control parquet export semantics for backtest artifacts (indicators/trades/stats/trade events).
- Values:
  - ``best_effort`` (default): parquet failures log warnings; CSV remains the compatibility layer.
  - ``required``: parquet export failures raise and should fail the backtest (artifact contract mode).
- Notes:
  - This is primarily intended for BotManager/BotSpot backtests where downstream tools depend on Parquet for performance.
  - When set to ``required``, a parquet export error should fail the backtest so missing artifacts are never silently ignored.

BACKTESTING_QUIET_LOGS
^^^^^^^^^^^^^^^^^^^^^^

- Purpose: Reduce log noise during backtests.
- Values: ``true`` / ``false`` (string).

BACKTESTING_SHOW_PROGRESS_BAR
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- Purpose: Enable progress bar updates.
- Values: ``true`` / ``false`` (string).

Backtest progress file (BotSpot/BotManager UI)
----------------------------------------------

LOG_BACKTEST_PROGRESS_TO_FILE
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- Purpose: When truthy, write ``logs/progress.csv`` during backtests so BotManager/BotSpot can show live progress.
- Values: truthy enables (``1``, ``true``, ``yes``); unset/``0`` disables.
- Notes:
  - On startup, LumiBot writes an initial ``progress.csv`` row immediately to reduce “time-to-first-progress” latency for short backtests.
  - In BotManager, a background thread watches ``/app/logs/*progress.csv`` and uploads the most recent row to DynamoDB.

BACKTESTING_PROGRESS_HEARTBEAT
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- Purpose: Enable periodic ``progress.csv`` updates while a ThetaData download is active (prevents the UI appearing stuck when simulation datetime is not advancing).
- Values: ``true`` / ``false`` (string).
- Default: enabled (``true``).

BACKTESTING_PROGRESS_HEARTBEAT_SECONDS
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- Purpose: Heartbeat interval (seconds) for writing ``progress.csv`` while downloading.
- Values: float seconds (string).
- Default: ``2.0``

Trade audit telemetry (accuracy investigations)
-----------------------------------------------

LUMIBOT_BACKTEST_AUDIT
^^^^^^^^^^^^^^^^^^^^^^

- Purpose: Emit **per-fill audit telemetry** into the trade-event CSV as ``audit.*`` columns.
- Values: ``1`` enables (any truthy value); unset/``0`` disables.
- Output:
  - Writes a full trade-event export ``*_trade_events.csv`` with ``audit.*`` columns (for example, quote bid/ask snapshots, bar OHLC, SMART_LIMIT inputs, and multileg linkage).
- Notes:
  - This increases CSV width and can add overhead; keep it enabled only when you need a full audit trail.

Profiling (performance + parity investigations)
------------------------------------------------

BACKTESTING_PROFILE
^^^^^^^^^^^^^^^^^^^

- Purpose: Enable profiling during backtests to attribute runtime (S3 IO vs compute vs artifacts).
- Values:
  - ``yappi`` (supported)
- Output:
  - Produces a ``*_profile_yappi.csv`` artifact alongside other backtest artifacts.

Remote cache (S3)
-----------------

LUMIBOT_CACHE_BACKEND / LUMIBOT_CACHE_MODE
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- Purpose: Enable remote cache mirroring (for example, mirroring parquet cache files to S3).
- Common values:
  - ``LUMIBOT_CACHE_BACKEND=s3``
  - ``LUMIBOT_CACHE_MODE=readwrite`` (or ``readonly``)

LUMIBOT_CACHE_FOLDER
^^^^^^^^^^^^^^^^^^^^

- Purpose: Override the local cache folder (useful to simulate a fresh container/task).

LUMIBOT_CACHE_S3_BUCKET / LUMIBOT_CACHE_S3_PREFIX / LUMIBOT_CACHE_S3_REGION
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- Purpose: S3 target configuration.

LUMIBOT_CACHE_S3_VERSION
^^^^^^^^^^^^^^^^^^^^^^^^

- Purpose: Namespace/version the remote cache without deleting anything.
- Practical use: set a unique version to simulate a “cold S3” run safely.

LUMIBOT_CACHE_S3_ACCESS_KEY_ID / LUMIBOT_CACHE_S3_SECRET_ACCESS_KEY / LUMIBOT_CACHE_S3_SESSION_TOKEN
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- Purpose: Credentials for S3 access when not using an instance/task role.
- Values: provided by your runtime environment (**do not hardcode**).

For cache key layout and validation workflow, see :doc:`Backtesting <backtesting>` and the engineering notes in ``docs/remote_cache.md``.

Strategy configuration
----------------------

STRATEGY_NAME
^^^^^^^^^^^^^

- Purpose: Name for the strategy to be used in database logging and identification.
- Values: Any string.

MARKET
^^^^^^

- Purpose: Market to be traded (used for market calendar selection).
- Values: ``NYSE``, ``NASDAQ``, ``24/7`` (crypto), etc.

HIDE_TRADES / HIDE_POSITIONS
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- Purpose: Hide trade and position information in logs/output.
- Values: ``true`` / ``false`` (string).
- Default: ``false``.

DISCORD_WEBHOOK_URL
^^^^^^^^^^^^^^^^^^^

- Purpose: Discord webhook URL for notifications.
- Values: Full Discord webhook URL (**do not hardcode in public repos**).

Database configuration
----------------------

DB_CONNECTION_STR
^^^^^^^^^^^^^^^^^

- Purpose: PostgreSQL connection string for account history and strategy persistence.
- Values: ``postgresql://user:password@host:port/database`` (**do not hardcode**).
- Note: Replaces deprecated ``ACCOUNT_HISTORY_DB_CONNECTION_STR``.

LOG_BACKTEST_PROGRESS_TO_FILE
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- Purpose: Log backtest progress to a file instead of stdout.
- Values: ``true`` / ``false`` (string).

Broker selection
----------------

TRADING_BROKER
^^^^^^^^^^^^^^

- Purpose: Explicitly specify which broker to use for live trading.
- Values (case-insensitive):
  - ``alpaca``
  - ``ib``, ``interactivebrokers``, ``ibrest``, ``interactivebrokersrest``
- Note: If not set, broker is auto-detected based on available credentials.

DATA_SOURCE
^^^^^^^^^^^

- Purpose: Explicitly specify which data source to use.
- Values (case-insensitive):
  - ``alpaca``, ``yahoo``, ``ibkr``, ``interactivebrokersrest``
- Note: If not set, uses broker's default data source.

Alpaca broker
-------------

ALPACA_API_KEY / ALPACA_API_SECRET
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- Purpose: Alpaca API credentials for trading.
- Values: Obtain from Alpaca dashboard (**do not hardcode**).

ALPACA_OAUTH_TOKEN
^^^^^^^^^^^^^^^^^^

- Purpose: OAuth token (alternative to API key/secret).
- Values: OAuth token (**do not hardcode**).
- Note: Either OAuth token OR API key/secret must be provided, not both.

ALPACA_IS_PAPER
^^^^^^^^^^^^^^^

- Purpose: Toggle between paper and live trading.
- Values: ``true`` (paper) / ``false`` (live).
- Default: ``true`` (paper trading).

LUMIBOT_ALPACA_TRADES_RATE_LIMIT
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- Purpose: Override Alpaca historical trades API rate limit for HFT backtests.
- Values: Integer (requests per minute). Use ``0`` or negative for unlimited (premium/custom Alpaca agreements).
- Default: unset (uses 200 req/min per Alpaca standard plan).
- Notes:
  - When using ``AlpacaBacktesting`` with trades data (HFT strategies), parallel downloads share a global rate limiter.
  - Users with premium/custom Alpaca agreements may set ``0`` to disable rate limiting.
  - Reference: `Alpaca API rate limits <https://alpaca.markets/support/usage-limit-api-calls>`_.
Interactive Brokers
-------------------

INTERACTIVE_BROKERS_PORT
^^^^^^^^^^^^^^^^^^^^^^^^

- Purpose: Socket port for IB Gateway/TWS connection.
- Values: Integer (e.g., ``7497`` for paper, ``7496`` for live).

INTERACTIVE_BROKERS_CLIENT_ID
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- Purpose: Client ID for IB connection (must be unique per connection).
- Values: Integer.

INTERACTIVE_BROKERS_IP
^^^^^^^^^^^^^^^^^^^^^^

- Purpose: IP address of IB Gateway/TWS.
- Values: IP address string.
- Default: ``127.0.0.1``.

IB_SUBACCOUNT
^^^^^^^^^^^^^

- Purpose: Sub-account identifier for IB multi-account setups.
- Values: Account identifier string.

Interactive Brokers REST
------------------------

IB_USERNAME / IB_PASSWORD
^^^^^^^^^^^^^^^^^^^^^^^^^

- Purpose: Credentials for IB REST API authentication.
- Values: IB credentials (**do not hardcode**).

IB_ACCOUNT_ID
^^^^^^^^^^^^^

- Purpose: Account ID for IB REST API.
- Values: Account identifier string.

IB_API_URL
^^^^^^^^^^

- Purpose: Base URL for IB REST API endpoint.
- Values: URL string.

IBKR_HISTORY_SOURCE
^^^^^^^^^^^^^^^^^^^

- Purpose: Select which IBKR Client Portal history source to use for OHLC bars in IBKR REST backtests.
- Values: ``Trades`` / ``Midpoint`` / ``Bid_Ask`` (case-insensitive; hyphen/underscore variants accepted).
- Default: ``Trades``.

IBKR_FUTURES_EXCHANGE
^^^^^^^^^^^^^^^^^^^^^

- Purpose: Fallback futures exchange for IBKR REST when ``exchange=`` is not provided and automatic exchange routing cannot resolve a unique venue.
- Values: Exchange code string (for example: ``CME``, ``CBOT``, ``COMEX``, ``NYMEX``).
- Default: ``CME``.

IBKR_CRYPTO_VENUE
^^^^^^^^^^^^^^^^^

- Purpose: Default IBKR crypto venue when backtesting spot crypto via IBKR REST.
- Values: Venue/exchange string (for example: ``ZEROHASH``).
- Default: ``ZEROHASH``.

LUMIBOT_IBKR_ENABLE_FUTURES_BID_ASK
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- Purpose: Opt-in derivation of per-bar futures bid/ask quotes using IBKR ``Bid_Ask`` + ``Midpoint`` history sources.
- Values: ``true``/``false`` (or ``1``/``0``).
- Default: disabled.

Schwab broker
-------------

SCHWAB_ACCOUNT_NUMBER
^^^^^^^^^^^^^^^^^^^^^

- Purpose: Schwab account number (required).
- Values: Account number string.

SCHWAB_APP_KEY / SCHWAB_APP_SECRET
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- Purpose: Schwab API application credentials.
- Values: Obtain from Schwab developer portal (**do not hardcode**).

SCHWAB_TOKEN
^^^^^^^^^^^^

- Purpose: Optional pre-existing OAuth token.
- Values: Token string (**do not hardcode**).

SCHWAB_BACKEND_CALLBACK_URL
^^^^^^^^^^^^^^^^^^^^^^^^^^^

- Purpose: OAuth callback URL for authentication flow.
- Values: URL string.

Tradovate broker
----------------

TRADOVATE_USERNAME / TRADOVATE_DEDICATED_PASSWORD
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- Purpose: Tradovate credentials.
- Values: Tradovate credentials (**do not hardcode**).

TRADOVATE_APP_ID / TRADOVATE_APP_VERSION
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- Purpose: Application identification for Tradovate API.
- Values: String identifiers.
- Default: ``Lumibot`` / ``1.0``.

TRADOVATE_CID / TRADOVATE_SECRET
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- Purpose: Client credentials for Tradovate OAuth.
- Values: Obtain from Tradovate (**do not hardcode**).

TRADOVATE_IS_PAPER
^^^^^^^^^^^^^^^^^^

- Purpose: Toggle between paper and live trading.
- Values: ``true`` (paper) / ``false`` (live).
- Default: ``true``.

TRADOVATE_MD_URL
^^^^^^^^^^^^^^^^

- Purpose: Market data URL override.
- Values: URL string.
- Default: ``https://md.tradovateapi.com/v1``.
LUMIWEALTH_API_KEY
^^^^^^^^^^^^^^^^^^

- Purpose: LumiWealth platform API key (for enterprise features).
- Values: Obtain from LumiWealth (**do not hardcode**).

Runtime telemetry (memory/health)
---------------------------------

LUMIBOT_TELEMETRY
^^^^^^^^^^^^^^^^^

- Purpose: Enable/disable runtime telemetry emission (single-line JSON to stdout prefixed with ``LUMIBOT_TELEMETRY``).
- Values: truthy enables (``1``, ``true``, ``yes``); falsy disables (``0``, ``false``).
- Default: enabled for live runs; disabled for backtests and pytest.

LUMIBOT_TELEMETRY_INTERVAL_SECONDS
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

- Purpose: Base telemetry cadence.
- Values: seconds (float).
- Default: ``300``.

LUMIBOT_TELEMETRY_DEEP
^^^^^^^^^^^^^^^^^^^^^^

- Purpose: Enable deep snapshot mode for diagnosing unknown memory sources.
- Values: truthy enables; falsy disables.
- Default: disabled.

Notes:

- Burst mode (more frequent telemetry logs) turns on automatically above ~80% of container memory.
- Deep snapshots trigger above ~90% with a ~1 hour cooldown (these thresholds are fixed defaults today).
