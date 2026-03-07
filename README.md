[![CI Status](https://github.com/Lumiwealth/lumibot/actions/workflows/cicd.yaml/badge.svg?branch=dev)](https://github.com/Lumiwealth/lumibot/actions/workflows/cicd.yaml)
[![Coverage](https://raw.githubusercontent.com/Lumiwealth/lumibot/badge/coverage.svg)](https://github.com/Lumiwealth/lumibot/actions/workflows/cicd.yaml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![PyPI version](https://badge.fury.io/py/lumibot.svg)](https://badge.fury.io/py/lumibot)

# 🚀 Lumibot - Professional Algorithmic Trading Framework

**The Ultimate Backtesting and Live Trading Library for Stocks, Options, Crypto, Futures, FOREX and More!**

Lumibot is a comprehensive, production-ready algorithmic trading framework designed for quantitative researchers, portfolio managers, and algorithmic traders. Built with modern Python practices, it seamlessly bridges the gap between backtesting and live trading with the same codebase.

**IMPORTANT: This library requires data for backtesting. Our recommended data source is [ThetaData](https://www.thetadata.net/) because they provide the deepest historical coverage we’ve found and directly support BotSpot. Use the promo code `BotSpot10` at checkout for 10% off the first order (the code also tells ThetaData you were referred by us).**

> **Contributor note:** Read `AGENTS.md` before running anything Theta-related. That file spells out the hard rules—never launch ThetaTerminal or the shared downloader locally, always point LumiBot at the AWS-hosted downloader, and wrap all long
> commands with `/Users/robertgrzesik/bin/safe-timeout`. Breaking these rules kills the only licensed Theta session.

## Architecture Documentation

- `docs/BACKTESTING_ARCHITECTURE.md` - Detailed documentation of the backtesting data flow (Yahoo, ThetaData, Polygon data sources, caching, and data flow diagrams)
- `docs/ACCEPTANCE_BACKTESTS.md` - Manual end-to-end acceptance backtest suite + performance gate (ThetaData)
- `docsrc/environment_variables.rst` - Public documentation page for environment variables (update when env vars change)
- `CHANGELOG.md` - Deployment/release notes (keep this updated)
- `CLAUDE.md` - AI assistant instructions for working with the codebase
- `AGENTS.md` - Critical rules for ThetaData and production safety

## Releases / Deployments (internal)

For production deployments (BotSpot / BotManager), keep releases traceable:

- Update `CHANGELOG.md` for every deploy (include the deploy commit hash)
- Tag the deploy commit as `vX.Y.Z` and push the tag
- Create a GitHub Release from that tag using the `CHANGELOG.md` entry

## 🌟 Key Features

- **🔄 Unified Codebase**: Same code for backtesting and live trading
- **📈 Multi-Asset Support**: Stocks, Options, Crypto, Futures, FOREX
- **⚡ High Performance**: Optimized for speed and efficiency
- **🔌 Multiple Brokers**: Alpaca, Interactive Brokers, Tradier, Schwab, and more
- **📊 Rich Analytics**: Built-in performance metrics and visualization
- **🛡️ Risk Management**: Advanced position sizing and risk controls
- **🐍 Modern Python**: Type hints, async support, and clean architecture

## 📚 Documentation

**📖 Complete Documentation: [lumibot.lumiwealth.com](http://lumibot.lumiwealth.com/)**

## 📦 Installation

### Prerequisites

Before installing Lumibot, ensure you have:

```bash
# Install python-dotenv (required for private packages)
pip install python-dotenv

# Or with uv (recommended)
uv pip install python-dotenv
```

### Standard Installation

```bash
# Install from PyPI (recommended for most users)
pip install lumibot

# Or with uv (faster and more reliable)
uv pip install lumibot

# Or install from source
pip install -e .
```

### Development Installation

For contributors and developers who want access to the latest features:

#### Using pip (Traditional Method)

```bash
# Clone the repository
git clone https://github.com/AI-Ahmed/lumibot.git
cd lumibot

# Create virtual environment with Python 3.12 (required for TA libraries)
python3.12 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install python-dotenv first (required for private packages)
pip install python-dotenv

# Install development dependencies
pip install -r requirements_dev.txt

# Install package in editable mode
pip install -e .
```

#### Using uv (Recommended - Faster & More Reliable)

```bash
# Install uv if you haven't already
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone the repository
git clone https://github.com/AI-Ahmed/lumibot.git
cd lumibot

# Create virtual environment with Python 3.12
uv venv --python 3.12
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install python-dotenv first
uv pip install python-dotenv

# Install development dependencies
uv pip install -r requirements_dev.txt

# Install package in editable mode
uv pip install -e .
```

### Installation with Extras

Lumibot supports several installation extras for different use cases:

#### Using pip

```bash
# Technical Analysis tools 
# - Python >= 3.12: pandas-ta (fully supported)
pip install -e ".[ta]"

# All public packages (includes TA tools)
pip install -e ".[all]"

# Private packages (for contributors with access)
pip install -e ".[private]"  # Requires GIT_TOKEN
```

#### Using uv (Recommended)

```bash
# Technical Analysis tools with uv
uv pip install -e ".[ta]"

# All public packages with uv
uv pip install -e ".[all]"

# Private packages with uv (requires GIT_TOKEN)
uv pip install -e ".[private]"

# Install all extras at once
uv pip install -e ".[all,private]"
```

#### Dependency Compatibility & Conflict Resolution

**⚠️ Important: pandas-ta & FPAP Dependency Conflict**

Due to conflicting numpy version requirements, the dependency resolver cannot install both packages simultaneously:

- **FPAP**: Declares `numpy==1.26.4` (pinned version)
- **pandas-ta**: Requires `numpy>=2.2.6` (newer version)

**Note**: While both packages may work together at runtime with numpy 2.2.6, the strict dependency declarations prevent automatic co-installation.

**Installation Options:**

1. **FPAP Only** (Recommended for contributors):

   ```bash
   uv pip install -e ".[private]"  # Installs FPAP without pandas-ta
   ```

2. **pandas-ta Only** (Public users):

   ```bash
   uv pip install -e ".[ta]"       # Installs pandas-ta without FPAP
   ```

3. **Separate Environments** (Advanced users):

   ```bash
   # Environment 1: FPAP
   uv venv fpap-env --python 3.12
   source fpap-env/bin/activate
   uv pip install -e ".[private]"
   
   # Environment 2: pandas-ta
   uv venv ta-env --python 3.12
   source ta-env/bin/activate
   uv pip install -e ".[ta]"
   ```

**Compatibility Matrix:**

| Installation | FPAP | pandas-ta | Core Trading | Notes |
|-------------|------|-----------|--------------|-------|
| `.[private]` | ✅ | ❌ | ✅ | For contributors with access |
| `.[ta]` | ❌ | ✅ | ✅ | For public users needing TA |
| `.[all]` | ❌ | ✅ | ✅ | Public packages only |
| `.[all,private]` | ⚠️ | ❌ | ✅ | FPAP conflicts with pandas-ta |

### 🔐 Private Package Installation

Contributors with access to private packages can install additional proprietary tools:

#### Method 1: Environment Variable (Recommended)

```bash
# Set your GitHub token
export GIT_TOKEN=your_github_personal_access_token

# Install with private packages using pip
pip install -e ".[private]"

# Or with uv (recommended)
uv pip install -e ".[private]"

# Or install everything (public + private)
uv pip install -e ".[all,private]"
```

#### Method 2: .env File (Automatic Detection)

Create a `.env` file in the project root:

```env
GIT_TOKEN=your_github_personal_access_token
```

Then install (the token will be automatically detected):

```bash
# With pip
pip install -e ".[private]"

# With uv (recommended)
uv pip install -e ".[private]"
```

#### Complete Development Setup with Private Packages

```bash
# 1. Clone and setup
git clone https://github.com/AI-Ahmed/lumibot.git
cd lumibot

# 2. Create .env file
echo "GIT_TOKEN=your_github_token" > .env

# 3. Setup with uv (recommended)
uv venv --python 3.12
source .venv/bin/activate
uv pip install python-dotenv
uv pip install -e ".[all,private]"

# 4. Verify installation
uv pip list | grep fpap  # Should show FPAP package
```

> **Note**: Private packages include advanced analytics tools (FPAP) and are only available to authorized contributors. The package works fully without these dependencies.

## ⚡ Quick Start

### Prerequisites

- **Python 3.10+** (Python 3.12+ recommended for best performance)
- **Virtual Environment** (strongly recommended)
- **Data Source**: [Polygon.io](https://polygon.io/?utm_source=affiliate&utm_campaign=lumi10) (free tier available)
  - Use coupon code `LUMI10` for 10% off 💰

### Your First Strategy

```python
from lumibot.strategies import Strategy
from lumibot.backtesting import YahooDataBacktesting
from lumibot.traders import Trader
from datetime import datetime

class BuyAndHold(Strategy):
    def initialize(self):
        self.sleeptime = 1  # Sleep for 1 day between iterations
        
    def on_trading_iteration(self):
        if self.first_iteration:
            # Buy $10,000 worth of SPY on first iteration
            order = self.create_order("SPY", 10000, "buy")
            self.submit_order(order)

# Backtest the strategy
backtesting_start = datetime(2020, 1, 1)
backtesting_end = datetime(2023, 12, 31)

BuyAndHold.backtest(
    YahooDataBacktesting,
    backtesting_start,
    backtesting_end,
)
```

### Run Example Strategy

```bash
# Run a built-in example
python -m lumibot.example_strategies.stock_buy_and_hold
```

## Build Trading Bots with AI

Want to build trading bots without code? Check out our new platform [BotSpot](https://botspot.trade/sales?utm_source=lumibot+docs&utm_medium=documentation&utm_campaign=GitHub+Readme) where you can create and deploy trading strategies using AI! BotSpot allows you to:

- Build trading bots using natural language and AI
- Test your strategies with historical data
- Deploy your bots to trade automatically
- Join a community of algorithmic traders

**Visit [BotSpot.trade](https://botspot.trade/sales?utm_source=lumibot+docs&utm_medium=documentation&utm_campaign=GitHub+Readme) to get started building AI-powered trading bots today!**

## Learn More

Check out example strategies and tutorials on our blog, or use our AI agent to build strategies for you:

**Blog:** <https://lumiwealth.com/blog/>
**AI Strategy Builder:** <https://www.botspot.trade/?utm_source=github&utm_medium=referral&utm_campaign=lumibot_readme>

## 🚀 Example Strategies & Backtesting

### Quick Backtest

```bash
# Run a simple buy and hold backtest
python -m lumibot.example_strategies.stock_buy_and_hold
```

### Example Strategy Repository

You can select a backtesting data source via the `BACKTESTING_DATA_SOURCE` environment variable (this overrides any explicit `datasource_class` in code):

```bash
# Single-provider backtesting (examples)
export BACKTESTING_DATA_SOURCE=thetadata
export BACKTESTING_DATA_SOURCE=ibkr
```

Multi-provider routing (by asset type) is supported by setting a JSON mapping:

```bash
# Example: ThetaData for stocks/options/indexes, IBKR for futures/crypto
export BACKTESTING_DATA_SOURCE='{"default":"thetadata","stock":"thetadata","option":"thetadata","index":"thetadata","future":"ibkr","crypto":"ibkr"}'

# Example: route crypto to CCXT via a specific exchange id (if desired)
export BACKTESTING_DATA_SOURCE='{"default":"thetadata","crypto":"coinbase"}'
```

## Run an Example Strategy

Explore our comprehensive example strategy: **[Stock Example Algorithm](https://github.com/Lumiwealth-Strategies/stock_example_algo)**

Deploy it instantly:

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/Lumiwealth-Strategies/stock_example_algo)

[![Run on Repl.it](https://replit.com/badge/github/Lumiwealth-Strategies/stock_example_algo)](https://replit.com/new/github/Lumiwealth-Strategies/stock_example_algo)

**For more information on this example strategy, you can check out the README in the example strategy repository here: [Example Algorithm](https://github.com/Lumiwealth-Strategies/stock_example_algo)**

## 🤝 Contributing

We welcome contributions from the community! Whether you're fixing bugs, adding features, or improving documentation, your help is appreciated.

### 🎥 Getting Started Video

Watch our contributor onboarding video: **[Watch The Video](https://youtu.be/Huz6VxqafZs)**

### 🔧 Development Setup

#### Option 1: Using uv (Recommended)

```bash
# 1. Fork and clone the repository
git clone https://github.com/yourusername/lumibot.git
cd lumibot

# 2. Create virtual environment with Python 3.12
uv venv --python 3.12
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# 3. Install python-dotenv first
uv pip install python-dotenv

# 4. Install development dependencies
uv pip install -r requirements_dev.txt

# 5. Install package in editable mode with all extras
uv pip install -e ".[all]"

# 6. Install pre-commit hooks (optional but recommended)
pre-commit install
```

#### Option 2: Using pip (Traditional)

```bash
# 1. Fork and clone the repository
git clone https://github.com/yourusername/lumibot.git
cd lumibot

# 2. Create virtual environment with Python 3.12
python3.12 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# 3. Install python-dotenv first
pip install python-dotenv

# 4. Install development dependencies
pip install -r requirements_dev.txt

# 5. Install package in editable mode
pip install -e ".[all]"

# 6. Install pre-commit hooks (optional but recommended)
pre-commit install
```

### 🚀 Contribution Workflow

1. **Watch the video**: [Contributing Guide](https://youtu.be/Huz6VxqafZs)
2. **Fork** the repository on GitHub
3. **Create a feature branch**: `git checkout -b feature/your-feature-name`
4. **Make your changes** with proper tests and documentation
5. **Run tests**: `pytest` (ensure all tests pass)
6. **Check code quality**: `ruff check .` and `ruff format .`
7. **Commit your changes**: `git commit -m "feat: add amazing feature"`
8. **Push to your fork**: `git push origin feature/your-feature-name`
9. **Create a Pull Request** targeting the `dev` branch

### 📋 Contribution Guidelines

- **Code Style**: We use [Ruff](https://docs.astral.sh/ruff/) for linting and formatting
- **Testing**: Add tests for new features using `pytest`
- **Documentation**: Update docstrings and README as needed
- **Commit Messages**: Use conventional commits (feat:, fix:, docs:, etc.)
- **Branch Naming**: Use descriptive names like `feature/options-trading` or `fix/memory-leak`

### 🔐 Private Dependencies Access

Contributors with access to private packages can install additional development tools:

```bash
# Create .env file with your GitHub token
echo "GIT_TOKEN=your_github_token" > .env

# Install with private dependencies
pip install -e ".[private]"
```

### 🏗️ Build System

Our build system supports:

- **Dynamic dependency resolution** based on Python version
- **Private package integration** for authorized contributors
- **Modern Python packaging** with `pyproject.toml` and `setuptools`

## 🧪 Testing

We maintain high code quality with comprehensive testing using `pytest`.

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage report
pytest --cov=lumibot --cov-report=html

# Run specific test file
pytest tests/test_asset.py

# Run tests matching a pattern
pytest -k "test_order"

# Run tests with verbose output
pytest -v
```

### Test Configuration

Some tests require API keys in a `.env` file:

```env
# Required for broker integration tests
ALPACA_API_KEY=your_alpaca_key
ALPACA_SECRET_KEY=your_alpaca_secret
POLYGON_API_KEY=your_polygon_key
```

### Test Categories

- **Unit Tests**: Fast, isolated component tests
- **Integration Tests**: Broker and data source integration
- **Backtest Tests**: Strategy backtesting validation
- **Performance Tests**: Speed and memory benchmarks

### Run CI locally before push

To run the same steps as GitHub Actions (install, lint, unit tests, backtest tests) before pushing:

```bash
# Full CI (lint + unit + backtest) — same as Actions
make ci

# Quick check (lint + unit only, no backtest) — faster
make ci-quick

# Install deps only (CI-like; use GIT_TOKEN if you need private FPAP)
make install-ci

# Lint only (same Ruff scope as CI)
make lint
```

Optional: run CI automatically before every push by enabling the sample hook:

```bash
git config core.hooksPath .githooks
```

Then `git push` will run `make ci-quick` first; if it fails, the push is aborted.

## Remote Cache Configuration

Lumibot can mirror its local parquet caches to AWS S3 when you enable the new
backtest cache manager. The feature is optional and defaults to local storage.
To configure the environment variables, understand the key naming convention,
and follow the manual validation checklist, review `docs/remote_cache.md`.

### Showing Code Coverage

To show code coverage, you can run the following command:

```bash
coverage run; coverage report; coverage html
```

#### Adding an Alias on Linux or MacOS

This will show you the code coverage in the terminal and also create a folder called "htmlcov" which will have a file called "index.html". You can open this file in your browser to see the code coverage in a more readable format.

If you don't want to keep typing out the command, you can add it as an alias in bash. To do this, you can run the following command:

```bash
alias cover='coverage run; coverage report; coverage html'
```

This will now allow you to run the command by just typing "cover" in the terminal.

```bash
cover
```

If you want to also add it to your .bashrc file. You can do this by running the following command:

```bash
echo "alias cover='coverage run; coverage report; coverage html'" >> ~/.bashrc
```

#### Adding an Alias on Windows

If you are on Windows, you can add an alias by running the following command:

Add to your PowerShell Profile: (profile.ps1)

```powershell
function cover { 
 coverage run
 coverage report
 coverage html
}
```

### Setting Up PyTest in VS Code

To set up in VS Code for debugging, you can add the following to your launch.json file under "configurations". This will allow you to go into "Run and Debug" and run the tests from there, with breakpoints and everything.

NOTE: You may need to change args to the path of your tests folder.

```json
{
    "name": "Python: Pytest",
    "type": "python",
    "request": "launch",
    "module": "pytest",
    "args": [
        "lumibot/tests"
    ],
    "console": "integratedTerminal",
}
```

Here's an example of an actual launch.json file:

```json
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Python: Pytest",
            "type": "python",
            "request": "launch",
            "module": "pytest",
            "args": [
                "lumibot/tests"
            ],
            "console": "integratedTerminal",
        }
    ]
}
```

## 📊 Supported Data Sources

Lumibot supports multiple data providers for comprehensive market coverage:

### Data Source Comparison

| Data Source | Asset Types | OHLCV | Split Adjusted | Dividends | Returns | Dividend Adjusted |
|-------------|-------------|-------|----------------|-----------|---------|-------------------|
| **Polygon** | Stocks, Options, Crypto, Forex | ✅ | ✅ | ❌ | ✅ | ❌ |
| **Yahoo Finance** | Stocks, ETFs, Indices | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Alpaca** | Stocks, Crypto | ✅ | ✅ | ❌ | ✅ | ❌ |
| **Interactive Brokers** | All Asset Classes | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Tradier** | Stocks, Options | ✅ | ✅ | ❌ | ✅ | ❌ |
| **CSV/Pandas** | Custom Data | ✅ | ✅ | ✅ | ✅ | ✅ |

### Data Source Features

- **Real-time Data**: Live market data for active trading
- **Historical Data**: Extensive historical coverage for backtesting
- **Multiple Timeframes**: From tick data to daily bars
- **Corporate Actions**: Automatic adjustment for splits and dividends
- **Alternative Data**: Support for custom data feeds

> **Recommended**: [Polygon.io](https://polygon.io/?utm_source=affiliate&utm_campaign=lumi10) offers the best balance of features, reliability, and cost. Use code `LUMI10` for 10% off.

## 🏗️ Architecture & Design

### Core Components

- **Strategies**: Define your trading logic and rules
- **Brokers**: Execute trades across multiple platforms  
- **Data Sources**: Feed market data to your strategies
- **Risk Management**: Built-in position sizing and risk controls
- **Analytics**: Performance tracking and visualization
- **Backtesting**: Historical strategy validation

### Design Principles

- **Modularity**: Plug-and-play components
- **Extensibility**: Easy to add new brokers and data sources
- **Performance**: Optimized for speed and memory efficiency
- **Reliability**: Production-tested with error handling
- **Maintainability**: Clean, well-documented codebase

## 🔧 Git Workflow for Contributors

### Creating a Feature Branch

```bash
# Create and switch to feature branch
git checkout -b feature/your-feature-name

# Keep your branch updated with latest dev
git fetch origin
git merge origin/dev
```

### Making Changes

```bash
# Stage and commit your changes
git add .
git commit -m "feat: add your feature description"

# Push to your fork
git push -u origin feature/your-feature-name
```

### Keeping Your Branch Updated

```bash
# Update dev branch
git checkout dev
git fetch origin
git merge origin/dev

# Rebase your feature branch
git checkout feature/your-feature-name
git rebase dev

# Force push (safely) to update remote branch
git push --force-with-lease origin feature/your-feature-name
```

### After PR Approval

```bash
# Clean up after merge
git checkout dev
git fetch origin
git merge origin/dev
git branch -D feature/your-feature-name
git push origin --delete feature/your-feature-name
```

## 🔧 Troubleshooting

### Common Installation Issues

#### Dependency Conflicts

**Problem**: `No solution found when resolving dependencies` with numpy versions

**Solution**: This occurs when trying to install both FPAP and pandas-ta together:

```bash
# Error message example:
# fpap==0.4.6 depends on numpy==1.26.4 
# pandas-ta==0.4.71b0 depends on numpy>=2.2.6

# Solution 1: Install FPAP only (recommended for contributors)
uv pip install -e ".[private]"

# Solution 2: Install pandas-ta only (recommended for public users)  
uv pip install -e ".[ta]"

# Solution 3: Use separate environments
uv venv separate-env --python 3.12
```

**Alternative Solutions for FPAP + TA Users:**

#### Option A: Alternative TA Libraries (Recommended)

```bash
# Install FPAP first
uv pip install -e ".[private]"

# Then install numpy 1.26.4 compatible TA libraries
uv pip install finta           # Financial Technical Analysis
uv pip install ta              # Technical Analysis Library
# Note: talib-binary requires Python < 3.12
```

#### Option B: Manual pandas-ta Installation (Advanced)

```bash
# Install FPAP first
uv pip install -e ".[private]"

# Force install pandas-ta (may work despite version conflict)
uv pip install pandas-ta --force-reinstall

# Verify both work (they often do despite the version warning)
python -c "import fpap, pandas_ta; print('✅ Both packages working')"
```

> **⚠️ Warning**: Option B bypasses dependency resolution and may cause instability. Use at your own risk and test thoroughly.

#### Environment Setup Issues

**Problem**: `python3.12` not found

**Solution**: Install Python 3.12 or use available version:

```bash
# Check available Python versions
ls /usr/bin/python*

# Use available version (e.g., python3.11)
uv venv --python python3.11

# Or install Python 3.12
# macOS: brew install python@3.12
# Ubuntu: sudo apt install python3.12
```

## 🔒 Security & Best Practices

### API Key Management

- **Never commit API keys** to version control
- Use **environment variables** or `.env` files
- **Rotate keys regularly** for production systems
- **Use separate keys** for development and production

### Production Deployment

- **Test thoroughly** in paper trading mode first
- **Monitor positions** and risk metrics continuously  
- **Implement circuit breakers** for unexpected behavior
- **Keep logs** for audit and debugging purposes

### Risk Management

- **Position sizing**: Never risk more than you can afford to lose
- **Diversification**: Don't put all capital in one strategy
- **Stop losses**: Implement proper exit strategies
- **Monitoring**: Set up alerts for unusual behavior

## 🌐 Community & Support

Join our thriving community of algorithmic traders and developers!

### 💬 Discord Community

**[Join our Discord Server](https://discord.gg/TmMsJCKY3T)** - Connect with other traders, get help, and share strategies

### 🤖 AI Trading Platform

**[BotSpot.trade](https://botspot.trade/)** - Build, test, and deploy trading strategies using AI assistance (no coding required!)

### 📚 Educational Resources

Enhance your algorithmic trading skills with our comprehensive courses:

- **[Algorithmic Trading Course](https://lumiwealth.com/algorithmic-trading-landing-page)** - Master the fundamentals
- **[Machine Learning for Trading](https://www.lumiwealth.com/product-category/machine-learning-purchase/)** - Advanced ML techniques
- **[Options Trading Course](https://www.lumiwealth.com/product-category/options-trading-purchase/)** - Options strategies and Greeks

### 📖 Additional Resources

- **[Blog](https://lumiwealth.com/blog/)** - Strategy examples and tutorials
- **[Documentation](http://lumibot.lumiwealth.com/)** - Complete API reference
- **[YouTube Channel](https://youtube.com/@lumiwealth)** - Video tutorials and webinars
- **[GitHub Discussions](https://github.com/AI-Ahmed/lumibot/discussions)** - Technical discussions

## 🏆 Acknowledgments

Special thanks to all our contributors who make Lumibot better every day!

### Core Contributors

- **Robert Grzesik** - Original creator and maintainer
- **Ahmed** - Advanced features and architecture improvements
- **Community Contributors** - Bug fixes, documentation, and feature requests

### Powered By

- **[Polygon.io](https://polygon.io/?utm_source=affiliate&utm_campaign=lumi10)** - Market data provider
- **[Alpaca](https://alpaca.markets/)** - Commission-free trading API
- **[Interactive Brokers](https://www.interactivebrokers.com/)** - Professional trading platform

## 📄 License

This project is licensed under the **MIT License** - see the [LICENSE](LICENSE) file for details.

```
MIT License

Copyright (c) 2024 Lumiwealth

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
```

---

<div align="center">

**⭐ Star this repository if Lumibot helps you build better trading strategies! ⭐**

[**🚀 Get Started**](http://lumibot.lumiwealth.com/) | [**📖 Documentation**](http://lumibot.lumiwealth.com/) | [**💬 Discord**](https://discord.gg/TmMsJCKY3T) | [**🐦 Twitter**](https://twitter.com/lumiwealth)

</div>
