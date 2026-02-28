#!/usr/bin/env bash
set -euo pipefail

/Users/robertgrzesik/bin/safe-timeout 2400s python3 -m pytest -q -m "apitest and not smartlimit_matrix" \
  tests/test_smart_limit_live_alpaca.py \
  tests/test_smart_limit_live_tradier.py

