"""
Setup script for lumibot package.

This setup.py handles dynamic loading of private packages based on environment variables.
The main configuration is in pyproject.toml, but private packages require dynamic loading.

Usage:
    uv pip install -e '.[all]'     # Install all packages including private (if GIT_TOKEN available)
    uv pip install -e '.[private]' # Install private packages only (if GIT_TOKEN available)  
    uv pip install -e '.[ta]'      # Install technical analysis packages only
"""

import os
import sys
import shutil
from pathlib import Path

import setuptools
from setuptools.command.build_py import build_py as _build_py


# Load environment variables from .env file
try:
    from dotenv import load_dotenv, find_dotenv
    # Search for .env file in current directory and parent directories
    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        print(f"Found .env file at: {dotenv_path}")
        load_dotenv(dotenv_path=dotenv_path)
        if "GIT_TOKEN" in os.environ:
            print(f"✅ Successfully loaded GIT_TOKEN from .env (length: {len(os.environ['GIT_TOKEN'])})")
        else:
            print("⚠️  Warning: GIT_TOKEN not found in .env file")
    else:
        print("⚠️  Warning: .env file not found")
except ImportError:
    print("⚠️  Warning: python-dotenv not installed. Private packages will not be available.")
    pass

PROJECT_ROOT = Path(__file__).resolve().parent
DIST_DIR = PROJECT_ROOT / "dist"
if DIST_DIR.exists():
    shutil.rmtree(DIST_DIR)


class BuildWithThetaJar(_build_py):
    """Optionally bundle ThetaTerminal.jar if present locally.

    This makes ThetaData optional at build/install time. If the JAR is not
    present in lumibot/resources, we simply skip bundling it instead of failing
    the build.
    """

    def run(self):
        super().run()
        self._maybe_copy_theta_terminal()

    def _maybe_copy_theta_terminal(self):
        src = PROJECT_ROOT / "lumibot" / "resources" / "ThetaTerminal.jar"
        if not src.exists():
            # Optional: nothing to do if JAR isn't in the repo
            print("[build] ThetaTerminal.jar not found, skipping bundling (ThetaData is optional).")
            return
        dest = Path(self.build_lib) / "lumibot" / "resources" / "ThetaTerminal.jar"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        print(
            f"[build] Bundled ThetaTerminal.jar -> {dest} "
            f"(size={dest.stat().st_size} bytes)"
        )


with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

# Base requirements without the private package
install_requires = [
    "loguru>=0.7.3,<0.8.3",
    "polygon-api-client>=1.13.3",
    "alpaca-py>=0.42.0",
    "alpha_vantage",
    "ibapi==9.81.1.post1",
    "yfinance>=0.2.61",
    "matplotlib>=3.3.3",
    "quandl",
    "numpy>=1.26.4",
    "pandas>=2.2.0",
    "polars>=1.32.3",
    "pandas_market_calendars>=5.1.0",
    "pandas-ta-classic>=0.3.14b0",
    "pendulum>=3.1.0",
    "plotly>=5.18.0",
    "sqlalchemy",
    "bcrypt",
    "pytest",
    "scipy>=1.14.0",
    "yappi>=1.6.0",
    "quantstats-lumi>=1.1.0",
    "python-dotenv",  # Secret Storage
    "ccxt>=4.4.80",
    "termcolor>=2.0",
    "jsonpickle",
    "apscheduler>=3.10.4",
    "appdirs",
    "pyarrow>=15.0.0",
    "tqdm",
    "lumiwealth-tradier>=0.1.18",
    "pytz",
    "psycopg2-binary",
    "exchange_calendars>=4.6.0",
    "duckdb",
    "tabulate",
    "thetadata==0.9.11",
    "databento>=0.42.0",
    "holidays",
    "psutil",
    "openai",
    "schwab-py>=1.5.0",
    "Flask>=2.3",
    "free-proxy",
    "requests-oauthlib",
    "tenacity>=8.0.0",
    "aiohttp>=3.9.0",
    "boto3>=1.40.64",

]

# Handle TA libraries with dependency conflict awareness
if "GIT_TOKEN" in os.environ and os.environ.get('GIT_TOKEN', '').strip():
    # If private packages (FPAP) will be installed, skip pandas-ta due to numpy conflict
    print("⚠️  Warning: Skipping pandas-ta installation due to numpy version conflict with FPAP")
    print("   FPAP requires numpy==1.26.4, pandas-ta requires numpy>=2.2.6")
    print("   Install pandas-ta separately if needed: pip install pandas-ta")
    ta_extras = []
else:
    # If no private packages, install pandas-ta
    if sys.version_info >= (3, 12):
        print("ℹ️  Info: Installing pandas-ta for Python 3.12+")
        ta_extras = ["pandas_ta @ https://www.pandas-ta.dev/assets/zip/pandas_ta-0.4.71b0.tar.gz"]
    else:
        print("ℹ️  Info: Installing pandas-ta for Python < 3.12")
        ta_extras = ["pandas_ta @ https://www.pandas-ta.dev/assets/zip/pandas_ta-0.4.71b0.tar.gz"]


# Define private packages based on GIT_TOKEN availability
private_extras = []
if "GIT_TOKEN" in os.environ:
    git_token = os.environ.get('GIT_TOKEN')
    if git_token and len(git_token.strip()) > 0:
        # Put FPAP URL directly in extras - this is the professional way
        fpap_url = f"FPAP @ git+https://{git_token}@github.com/AI-Ahmed/FPAP.git"
        private_extras = [fpap_url]
        print(f"✅ Private packages available in extras: FPAP")
    else:
        print("⚠️  Warning: GIT_TOKEN is empty")
else:
    print("ℹ️  Info: GIT_TOKEN not found. Private packages not available in extras.")
    print("   Create a .env file with GIT_TOKEN=your_github_token to enable private packages.")

# Define extras with private packages included when environment is active
extras_require = {
    "ta": ta_extras,
    "private": private_extras,  # Will include FPAP if GIT_TOKEN is available
    "all": ta_extras + private_extras,  # All packages (public + private)
}

theta_jar_path = PROJECT_ROOT / "lumibot" / "resources" / "ThetaTerminal.jar"

setuptools.setup(
    # Basic package information
    name="lumibot",
    version="4.4.5.2",
    author="Robert Grzesik",
    author_email="rob@lumiwealth.com",
    description="Backtesting and Trading Library, Made by Lumiwealth.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/AI-Ahmed/lumibot",
    packages=setuptools.find_packages(include=["lumibot", "lumibot.*"]),
    license="MIT",
    include_package_data=True, 
    install_requires=install_requires,
    extras_require=extras_require,
    
    # Include configuration files, and only include ThetaTerminal.jar if present
    package_data={
        "lumibot": [
            "resources/conf.yaml",
        ] + (["resources/ThetaTerminal.jar"] if theta_jar_path.exists() else []),
    },
    extras_require={
        # Optional dependencies to enable ThetaData support
        "thetadata": [
            "thetadata",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.10",
    cmdclass={"build_py": BuildWithThetaJar},
)
