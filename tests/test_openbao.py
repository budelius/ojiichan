from __future__ import annotations

import httpx

from ojiichan import openbao as openbao_module
from ojiichan.openbao import check_openbao


def _addrs(monkeypatch, value: str | None) -> None:
    """Pin OJIICHAN_OPENBAO_ADDRS without falling through to the real secret store."""

    def fake_env(name: str, default: str | None = None, **_: object) -> str | None:
        if name in {"OPENBAO_ADDRS", "OPENBAO_ADDR"}:
            return value
        return default

    monkeypatch.setattr(openbao_module, "env", fake_env)


def _client(codes: dict[str, int]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/sys/health"
        return httpx.Response(codes[request.url.host], request=request)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_skips_when_no_addresses_configured(monkeypatch) -> None:
    _addrs(monkeypatch, None)

    result = check_openbao()

    assert result["status"] == "skip"
    assert result["nodes"] == []
    assert "OJIICHAN_OPENBAO_ADDRS" in result["message"]


def test_active_and_standby_members_are_both_ok(monkeypatch) -> None:
    _addrs(monkeypatch, "https://bao0.test, https://bao1.test ,https://bao2.test")

    with _client({"bao0.test": 200, "bao1.test": 429, "bao2.test": 473}) as client:
        result = check_openbao(client=client)

    assert result["status"] == "ok"
    assert result["total"] == 3
    assert result["sealed"] == 0
    assert [node["state"] for node in result["nodes"]] == [
        "unsealed, active",
        "unsealed, standby",
        "unsealed, performance standby",
    ]


def test_one_sealed_member_fails_even_though_the_others_serve(monkeypatch) -> None:
    """The prod2 case: two members answer fine, so nothing else notices."""
    _addrs(monkeypatch, "https://bao0.test,https://bao1.test,https://bao2.test")

    with _client({"bao0.test": 503, "bao1.test": 200, "bao2.test": 429}) as client:
        result = check_openbao(client=client)

    assert result["status"] == "fail"
    assert result["sealed"] == 1
    assert "https://bao0.test" in result["message"]
    assert "SEALED" in result["message"]


def test_uninitialised_member_fails(monkeypatch) -> None:
    _addrs(monkeypatch, "https://bao0.test")

    with _client({"bao0.test": 501}) as client:
        result = check_openbao(client=client)

    assert result["status"] == "fail"
    assert result["nodes"][0]["state"] == "NOT INITIALIZED"


def test_unreachable_member_is_unknown_not_ok(monkeypatch) -> None:
    _addrs(monkeypatch, "https://bao0.test,https://bao1.test")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "bao1.test":
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = check_openbao(client=client)

    assert result["status"] == "unknown"
    assert result["unknown"] == 1
    assert result["nodes"][1]["state"] == "unreachable"
    assert "ConnectError" in result["nodes"][1]["error"]


def test_sealed_outranks_unknown(monkeypatch) -> None:
    """A confirmed seal is actionable; report it rather than the unreachable peer."""
    _addrs(monkeypatch, "https://bao0.test,https://bao1.test")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "bao1.test":
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(503, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = check_openbao(client=client)

    assert result["status"] == "fail"
    assert result["sealed"] == 1
    assert result["unknown"] == 1


def test_unrecognised_status_code_is_unknown(monkeypatch) -> None:
    _addrs(monkeypatch, "https://bao0.test")

    with _client({"bao0.test": 418}) as client:
        result = check_openbao(client=client)

    assert result["status"] == "unknown"
    assert result["nodes"][0]["state"] == "unrecognised HTTP 418"
