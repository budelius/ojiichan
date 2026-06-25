from __future__ import annotations

from typing import Any

import pytest

from ojiichan import transports
from ojiichan.config import ElkConfig
from ojiichan.elk import ElkClient


class _FakeResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code


class _Capture:
    """Records the last httpx.post call so transports can be asserted on."""

    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.url: str | None = None
        self.json: Any = None
        self.kwargs: dict[str, Any] = {}

    def __call__(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.url = url
        self.json = kwargs.get("json")
        self.kwargs = kwargs
        return _FakeResponse(self.status_code)


def test_transport_defaults_to_es() -> None:
    cfg = ElkConfig(host="es.example", index_prefix="app")
    assert cfg.transport == "es"
    assert cfg.write_target == "https://es.example:443"


def test_unknown_transport_falls_back_to_es(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OJIICHAN_ELK_TRANSPORT", "kafka")
    assert ElkConfig.from_env().transport == "es"


def test_write_target_per_transport() -> None:
    logstash = ElkConfig(host=None, transport="logstash", logstash_url="http://ls:8080")
    assert logstash.write_target == "http://ls:8080"
    otlp = ElkConfig(host=None, transport="otlp", otlp_endpoint="http://collector:4318")
    assert otlp.write_target == "http://collector:4318"


def test_logstash_transport_posts_document(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _Capture()
    monkeypatch.setattr(transports.httpx, "post", cap)
    cfg = ElkConfig(host=None, transport="logstash", logstash_url="http://ls:8080")
    client = ElkClient(cfg, service="svc")

    assert client.log_event("login_ok", {"operation": "login"}, topic="auth") is True
    assert cap.url == "http://ls:8080"
    assert cap.json["index"] == "app-auth"  # index travels as a field for pipeline routing
    assert cap.json["event_type"] == "login_ok"
    assert cap.json["service"] == "svc"


def test_otlp_transport_builds_logrecord(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _Capture()
    monkeypatch.setattr(transports.httpx, "post", cap)
    cfg = ElkConfig(host=None, transport="otlp", otlp_endpoint="http://collector:4318")
    client = ElkClient(cfg, service="svc")

    ok = client.log_event(
        "boom", {"operation": "charge"}, level="error", topic="billing", failure=True
    )
    assert ok is True
    assert cap.url == "http://collector:4318/v1/logs"
    record = cap.json["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
    assert record["severityText"] == "ERROR"
    assert record["severityNumber"] == 17
    resource_attrs = cap.json["resourceLogs"][0]["resource"]["attributes"]
    assert {"key": "service.name", "value": {"stringValue": "svc"}} in resource_attrs
    attr_keys = {a["key"] for a in record["attributes"]}
    assert {"event_type", "operation", "failure", "service"} <= attr_keys


def test_otlp_endpoint_not_double_suffixed(monkeypatch: pytest.MonkeyPatch) -> None:
    cap = _Capture()
    monkeypatch.setattr(transports.httpx, "post", cap)
    cfg = ElkConfig(host=None, transport="otlp", otlp_endpoint="http://collector:4318/v1/logs")
    ElkClient(cfg).log_event("e", {"operation": "op"})
    assert cap.url == "http://collector:4318/v1/logs"


def test_unconfigured_transport_falls_back_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("no network call should be made when unconfigured")

    monkeypatch.setattr(transports.httpx, "post", _boom)
    cfg = ElkConfig(host=None, transport="logstash", logstash_url=None)
    assert ElkClient(cfg).log_event("e", {"operation": "op"}) is False


def test_otlp_headers_parsing() -> None:
    headers = transports.parse_otlp_headers("authorization=Bearer xyz,x-tenant=acme")
    assert headers == {"authorization": "Bearer xyz", "x-tenant": "acme"}


def test_otlp_severity_mapping() -> None:
    assert transports.otlp_severity_number("info") == 9
    assert transports.otlp_severity_number("warning") == 13
    assert transports.otlp_severity_number("error") == 17
    assert transports.otlp_severity_number("nonsense") == 9
