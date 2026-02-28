#!/usr/bin/env bash
set -euo pipefail

/Users/robertgrzesik/bin/safe-timeout 1200s python3 -m pytest -q \
  tests/test_smart_limit_utils_unit.py \
  tests/test_smart_limit_single_leg_unit.py \
  tests/test_smart_limit_multileg_unit.py \
  tests/test_smart_limit_resilience_unit.py \
  tests/test_tradier_stream_optional_unit.py
