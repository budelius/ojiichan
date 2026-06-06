from __future__ import annotations

from typing import Any

import pytest

from ojiichan.config import ScalewayConfig
from ojiichan.scaleway import ScalewayLogsClient, check_scaleway


def _cfg() -> ScalewayConfig:
    return ScalewayConfig(
        loki_url="https://abc.logs.cockpit.fr-par.scw.cloud",
        token="tok_secret",
        resource_type="serverless_function",
    )


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:  # noqa: D401 - mimic httpx.Response
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def test_query_url_derivation() -> None:
    cfg = _cfg()
    assert cfg.query_url == "https://abc.logs.cockpit.fr-par.scw.cloud/loki/api/v1/query_range"


def test_query_url_none_without_loki_url() -> None:
    assert ScalewayConfig(loki_url=None, token="t").query_url is None


def test_build_query_composition() -> None:
    client = ScalewayLogsClient(_cfg())
    assert client.build_query() == '{resource_type="serverless_function"}'
    # resource_name is a substring (regex) match — Scaleway names are hash-prefixed.
    assert client.build_query(source="m3rp-api-staging") == (
        '{resource_type="serverless_function", resource_name=~".*m3rp-api-staging.*"}'
    )
    q = client.build_query(source="fn", topic="AUTH", failures_only=True)
    assert q.startswith('{resource_type="serverless_function", resource_name=~".*fn.*"}')
    assert '|= "AUTH"' in q
    assert "|~ `" in q  # failure regex in a backtick (raw) LogQL literal


def test_query_logs_parses_loki_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_get(url: str, *, params: dict[str, Any], headers: dict[str, str], timeout: float):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        return _FakeResponse(
            {
                "data": {
                    "result": [
                        {
                            "stream": {
                                "resource_name": "m3rp-api-staging-api",
                                "resource_type": "serverless_function",
                            },
                            "values": [
                                ["1700000000000000000", "[AUTH] Login failed: boom"],
                                ["1700000001000000000", "[INFO] started"],
                            ],
                        }
                    ]
                }
            }
        )

    monkeypatch.setattr("ojiichan.scaleway.httpx.get", fake_get)

    total, entries = ScalewayLogsClient(_cfg()).query_logs(
        hours=2, limit=10, source="m3rp-api-staging-api"
    )

    assert captured["headers"]["X-Token"] == "tok_secret"
    assert captured["headers"]["Authorization"] == "Bearer tok_secret"
    assert captured["url"].endswith("/loki/api/v1/query_range")
    assert total == 2
    # sorted newest-first
    assert entries[0].message == "[INFO] started"
    assert entries[0].level == "info"
    assert entries[1].message == "[AUTH] Login failed: boom"
    assert entries[1].level == "error"
    assert entries[1].index == "m3rp-api-staging-api"
    assert entries[1].operation == "serverless_function"


def test_query_logs_requires_config() -> None:
    client = ScalewayLogsClient(ScalewayConfig(loki_url=None, token=None))
    with pytest.raises(RuntimeError, match="SCALEWAY_LOKI_URL"):
        client.query_logs()


def test_check_scaleway_unconfigured() -> None:
    out = check_scaleway(config=ScalewayConfig(loki_url=None, token=None))
    assert out["status"] == "fail"
    assert "SCALEWAY_LOKI_URL" in out["error"]
