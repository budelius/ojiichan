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


# --- kubectl transport -------------------------------------------------------


class _Completed:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _kubectl(monkeypatch, *, pods: str, statuses: dict[str, _Completed]) -> list[list[str]]:
    """Stub kubectl. Returns the command list for assertions."""
    calls: list[list[str]] = []
    monkeypatch.setattr(openbao_module.shutil, "which", lambda _: "/usr/bin/kubectl")

    def fake_run(cmd, **_kwargs):
        calls.append(cmd)
        if "get" in cmd:
            return _Completed(stdout=pods)
        pod = cmd[cmd.index("-n") + 2]
        return statuses[pod]

    monkeypatch.setattr(openbao_module.subprocess, "run", fake_run)
    return calls


def _status(*, initialized: bool = True, sealed: bool = False) -> _Completed:
    body = f'{{"initialized": {str(initialized).lower()}, '
    body += f'"sealed": {str(sealed).lower()}, "version": "2.5.3"}}'
    # `bao status` exits 2 when sealed; the body is still valid JSON.
    return _Completed(stdout=body, returncode=2 if sealed else 0)


def test_kubectl_transport_is_used_when_no_addresses(monkeypatch) -> None:
    _addrs(monkeypatch, None)
    calls = _kubectl(
        monkeypatch,
        pods="openbao-0 openbao-1 openbao-2",
        statuses={f"openbao-{i}": _status() for i in range(3)},
    )

    result = check_openbao()

    assert result["status"] == "ok"
    assert result["total"] == 3
    assert [n["address"] for n in result["nodes"]] == [
        "openbao/openbao-0",
        "openbao/openbao-1",
        "openbao/openbao-2",
    ]
    assert calls[0][:3] == ["kubectl", "get", "pods"]


def test_kubectl_transport_reports_a_sealed_member(monkeypatch) -> None:
    """The prod2 case, through the transport an operator actually has."""
    _addrs(monkeypatch, None)
    _kubectl(
        monkeypatch,
        pods="openbao-0 openbao-1 openbao-2",
        statuses={
            "openbao-0": _status(sealed=True),
            "openbao-1": _status(),
            "openbao-2": _status(),
        },
    )

    result = check_openbao()

    assert result["status"] == "fail"
    assert result["sealed"] == 1
    assert "openbao/openbao-0 SEALED" in result["message"]


def test_sealed_member_is_read_from_the_body_not_the_exit_code(monkeypatch) -> None:
    """`bao status` exits 2 when sealed. Treating that as failure loses the answer."""
    _addrs(monkeypatch, None)
    _kubectl(
        monkeypatch,
        pods="openbao-0",
        statuses={"openbao-0": _status(sealed=True)},
    )

    result = check_openbao()

    assert result["nodes"][0]["state"] == "SEALED"
    assert result["nodes"][0]["status"] == "fail"


def test_uninitialised_member_via_kubectl(monkeypatch) -> None:
    _addrs(monkeypatch, None)
    _kubectl(
        monkeypatch,
        pods="openbao-0",
        statuses={"openbao-0": _status(initialized=False, sealed=True)},
    )

    result = check_openbao()

    assert result["nodes"][0]["state"] == "NOT INITIALIZED"


def test_unparseable_output_is_unknown_not_ok(monkeypatch) -> None:
    _addrs(monkeypatch, None)
    _kubectl(
        monkeypatch,
        pods="openbao-0",
        statuses={
            "openbao-0": _Completed(stderr="error: unable to upgrade connection", returncode=1)
        },
    )

    result = check_openbao()

    assert result["status"] == "unknown"
    assert result["nodes"][0]["state"] == "unreachable"
    assert "unable to upgrade connection" in result["nodes"][0]["error"]


def test_skips_when_kubectl_finds_no_pods(monkeypatch) -> None:
    _addrs(monkeypatch, None)
    _kubectl(monkeypatch, pods="", statuses={})

    result = check_openbao()

    assert result["status"] == "skip"
    assert "OJIICHAN_OPENBAO_ADDRS" in result["message"]


def test_skips_when_kubectl_is_absent(monkeypatch) -> None:
    _addrs(monkeypatch, None)
    monkeypatch.setattr(openbao_module.shutil, "which", lambda _: None)

    result = check_openbao()

    assert result["status"] == "skip"
    assert result["nodes"] == []
