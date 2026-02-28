from __future__ import annotations

from datetime import date

from lumibot.entities import Asset
from lumibot.tools import thetadata_helper


class _FakeQueueClient:
    """Deterministic fake for ThetaData queue client used in chain building.

    We use this to assert which expiration strike-list requests would be submitted without making
    any network calls. This keeps the test fast and avoids coupling to downloader availability.
    """

    max_concurrent = 8

    def __init__(self) -> None:
        self.submitted: list[tuple[str, str]] = []
        self._request_map: dict[str, tuple[str, str]] = {}

    def check_or_submit(self, *, method: str, path: str, query_params: dict, headers: dict):
        symbol = query_params["symbol"]
        expiration = query_params["expiration"]
        request_id = f"req:{symbol}:{expiration}"
        self.submitted.append((symbol, expiration))
        self._request_map[request_id] = (symbol, expiration)
        return request_id, {"status": "pending"}, True

    def wait_for_result(self, *, request_id: str, timeout: float):
        # Build a minimal strike payload (matches the shape expected by build_historical_chain).
        symbol, expiration = self._request_map[request_id]
        _ = (symbol, expiration, timeout)
        return {
            "header": {"format": ["strike"]},
            "response": [[100], [105], [110]],
        }, 200


def _fake_expirations_response(values: list[str]) -> dict:
    return {
        "header": {"format": ["expiration"]},
        "response": [[v] for v in values],
    }


def test_build_historical_chain_applies_default_max_days_out(monkeypatch):
    """Regression: default chain builds must not fan out into years of expirations.

    Theta's expirations payload can include expirations many years in the future for historical
    backtest dates, which would force one strike-list request per expiration. That behavior makes
    cold-cache backtests unusably slow (NVDA/SPX investigations).
    """

    fake_queue = _FakeQueueClient()

    monkeypatch.setattr(
        "lumibot.tools.data_downloader_queue_client.get_queue_client",
        lambda: fake_queue,
    )

    # Default horizon is bounded (2y for stocks). Far-future expirations should be excluded.
    expirations = ["2022-01-07", "2022-02-18", "2022-03-18", "2023-01-20", "2026-01-20"]

    def fake_get_request(*, url: str, headers: dict, querystring: dict):
        assert querystring.get("format") == "json"
        assert url.endswith(thetadata_helper.OPTION_LIST_ENDPOINTS["expirations"])
        return _fake_expirations_response(expirations)

    monkeypatch.setattr(thetadata_helper, "get_request", fake_get_request)

    asset = Asset("NVDA", asset_type=Asset.AssetType.STOCK)
    chains = thetadata_helper.build_historical_chain(asset=asset, as_of_date=date(2022, 1, 3))

    assert chains is not None
    call_expirations = set(chains["Chains"]["CALL"].keys())
    assert "2022-01-07" in call_expirations
    assert "2022-02-18" in call_expirations
    assert "2022-03-18" in call_expirations
    assert "2023-01-20" in call_expirations
    assert "2026-01-20" not in call_expirations

    submitted_exps = [exp for _sym, exp in fake_queue.submitted]
    assert submitted_exps == ["2022-01-07", "2022-02-18", "2022-03-18", "2023-01-20"]


def test_build_historical_chain_respects_explicit_max_hint(monkeypatch):
    fake_queue = _FakeQueueClient()
    monkeypatch.setattr(
        "lumibot.tools.data_downloader_queue_client.get_queue_client",
        lambda: fake_queue,
    )

    expirations = ["2022-01-07", "2022-02-18", "2022-03-18"]

    def fake_get_request(*, url: str, headers: dict, querystring: dict):
        assert querystring.get("format") == "json"
        assert url.endswith(thetadata_helper.OPTION_LIST_ENDPOINTS["expirations"])
        return _fake_expirations_response(expirations)

    monkeypatch.setattr(thetadata_helper, "get_request", fake_get_request)

    asset = Asset("NVDA", asset_type=Asset.AssetType.STOCK)
    chains = thetadata_helper.build_historical_chain(
        asset=asset,
        as_of_date=date(2022, 1, 3),
        chain_constraints={"max_expiration_date": date(2022, 1, 10)},
    )

    assert chains is not None
    call_expirations = set(chains["Chains"]["CALL"].keys())
    assert call_expirations == {"2022-01-07"}
    assert [exp for _sym, exp in fake_queue.submitted] == ["2022-01-07"]
