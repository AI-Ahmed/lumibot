#!/usr/bin/env bash
set -euo pipefail

/Users/robertgrzesik/bin/safe-timeout 7200s python3 -m pytest -q -m smartlimit_matrix \
  tests/test_smart_limit_live_matrix_alpaca.py \
  tests/test_smart_limit_live_matrix_tradier.py

