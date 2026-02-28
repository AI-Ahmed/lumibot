import os
import sys


def test_disable_dotenv_skips_recursive_scan(monkeypatch):
    """When LUMIBOT_DISABLE_DOTENV is set, importing credentials must not walk directories."""

    monkeypatch.setenv("LUMIBOT_DISABLE_DOTENV", "1")

    def _boom(*_args, **_kwargs):
        raise AssertionError("os.walk should not be called when LUMIBOT_DISABLE_DOTENV=1")

    monkeypatch.setattr(os, "walk", _boom)

    # credentials.py runs at import-time; force a clean import for this test.
    sys.modules.pop("lumibot.credentials", None)
    __import__("lumibot.credentials")
