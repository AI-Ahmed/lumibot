"""
Setup script for lumibot package.

This setup.py handles dynamic loading of private packages based on environment variables.
The main configuration is in pyproject.toml, but private packages require dynamic loading.

Usage:
    uv pip install -e '.[all]'     # Install all packages including private (if GIT_TOKEN available)
    uv pip install -e '.[private]' # Install private packages only (if GIT_TOKEN available)  
    uv pip install -e '.[ta]'      # Install technical analysis packages only
"""
import setuptools
import sys
import os

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

# No need for complex functions - we'll put FPAP directly in extras when environment is active


with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

# Base requirements without the private package
install_requires = [
    "loguru>=0.7.3,<0.8.3",
    "polygon-api-client>=1.13.3",
    "alpaca-py>=0.28.1",
    "alpha_vantage",
    "ibapi==9.81.1.post1",
    "yfinance>=0.2.61",
    "matplotlib>=3.3.3",
    "quandl",
    "numpy>=1.26.4",
    "pandas>=2.2.0",
    "pandas-market-calendars==5.1.1",
    "plotly>=5.18.0",
    "sqlalchemy",
    "bcrypt",
    "pytest",
    "scipy>=1.14.0",
    "quantstats-lumi>=1.0.1",
    "python-dotenv",  # Secret Storage
    "ccxt>=4.4.80",
    "termcolor>=2.0",
    "jsonpickle",
    "apscheduler>=3.10.4",
    "appdirs",
    "pyarrow>=15.0.0",
    "tqdm",
    "lumiwealth-tradier>=0.1.16",
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


setuptools.setup(
    # Basic package information
    name="lumibot",
    version="3.18.5",
    author="Robert Grzesik",
    author_email="rob@lumiwealth.com",
    description="Backtesting and Trading Library, Made by Lumiwealth.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/AI-Ahmed/lumibot",
    
    # Package configuration
    packages=setuptools.find_packages(),
    license="MIT",
    include_package_data=True, 
    install_requires=install_requires,
    extras_require=extras_require,
    
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.10",
)