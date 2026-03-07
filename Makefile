# Local CI targets — run the same steps as GitHub Actions before push.
# Requires: Python 3.10+, pip, ruff. Optional: GIT_TOKEN for private FPAP dep.
#
# Usage:
#   make ci          # full: install + lint + unit tests + backtest tests
#   make ci-quick    # fast: install + lint + unit tests only (no backtest)
#   make install-ci  # install deps only (CI-like resolve + pip install)
#   make lint        # ruff only (same files as CI)

.PHONY: ci ci-quick install-ci lint

ci:
	./scripts/run_ci_local.sh

ci-quick:
	RUN_BACKTEST=0 ./scripts/run_ci_local.sh

install-ci:
	@echo "Resolving and installing dependencies (CI-like)..."
	@export AIOHTTP_NO_EXTENSIONS=$${AIOHTTP_NO_EXTENSIONS:-1}; \
	if [ -n "$$GIT_TOKEN" ]; then \
	  sed 's/\$${GIT_TOKEN}/'"$$GIT_TOKEN"'/g' requirements.txt > requirements.resolved.txt; \
	else \
	  grep -v 'FPAP.git' requirements.txt > requirements.resolved.txt; \
	fi; \
	pip install -r requirements.resolved.txt

lint:
	python -m ruff check --select F,I \
	  lumibot/tools/thetadata_helper.py \
	  lumibot/tools/data_downloader_queue_client.py \
	  lumibot/backtesting/thetadata_backtesting_pandas.py \
	  lumibot/components/options_helper.py \
	  lumibot/strategies/_strategy.py \
	  tests/backtest/test_acceptance_backtests_ci.py \
	  tests/test_thetadata_day_timestamp_alignment.py \
	  tests/test_thetadata_get_last_price_trade_only.py \
	  tests/test_options_helper_thetadata_actionable_strikes.py \
	  tests/test_thetadata_queue_client.py
