from __future__ import annotations

import json
import os

import httpx

from ojiichan import cli
from ojiichan import verify as verify_module
from ojiichan.credentials import project_scope
from ojiichan.verify import check_public_health, verify_production
from ojiichan_mcp import server


def test_project_scope_selects_and_restores_project(monkeypatch) -> None:
    monkeypatch.setenv("OJIICHAN_VAULT_PROJECT", "operator")

    with project_scope("facts-production"):
        assert os.environ["OJIICHAN_VAULT_PROJECT"] == "facts-production"

    assert os.environ["OJIICHAN_VAULT_PROJECT"] == "operator"


def test_public_health_requires_http_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        status = 200 if request.url.path == "/health" else 503
        return httpx.Response(status, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        healthy = check_public_health("https://facts.example/health", client=client)
        unhealthy = check_public_health("https://facts.example/ready", client=client)

    assert healthy["status"] == "ok"
    assert healthy["status_code"] == 200
    assert unhealthy["status"] == "fail"
    assert unhealthy["status_code"] == 503


def test_verify_aggregates_backends_and_public_health(monkeypatch) -> None:
    monkeypatch.setitem(
        verify_module.BACKEND_CHECKS,
        "elk",
        lambda **kwargs: {"status": "ok", "events_total": 3},
    )
    monkeypatch.setitem(
        verify_module.BACKEND_CHECKS,
        "rustrak",
        lambda **kwargs: {"status": "warn", "unresolved": 1},
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = verify_production(
            project="facts-production",
            backends=["elk", "rustrak"],
            public_urls=["https://facts.example/health"],
            public_client=client,
        )

    assert result["status"] == "warn"
    assert result["project"] == "facts-production"
    assert result["summary"] == {"ok": 2, "warn": 1, "fail": 0}
    assert result["public_health"][0]["status_code"] == 200


def test_cli_verify_json(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        cli,
        "verify_production",
        lambda **kwargs: {
            "status": "ok",
            "project": kwargs["project"],
            "backends": {"elk": {"status": "ok"}},
            "public_health": [],
            "summary": {"ok": 1, "warn": 0, "fail": 0},
        },
    )
    args = cli.build_parser().parse_args(
        ["verify", "--project", "facts", "--backend", "elk", "--json"]
    )

    assert cli.cmd_verify(args) == 0
    assert json.loads(capsys.readouterr().out)["project"] == "facts"


def test_cli_logs_accepts_project() -> None:
    args = cli.build_parser().parse_args(["logs", "--project", "facts-production"])
    assert args.project == "facts-production"


def test_mcp_health_project_is_scoped(monkeypatch) -> None:
    captured: dict[str, str | None] = {}

    def fake_check(**kwargs):
        captured["project"] = os.environ.get("OJIICHAN_VAULT_PROJECT")
        return {"status": "ok"}

    monkeypatch.setattr(server, "check_elk", fake_check)
    assert server.elk_health(project="facts-production")["status"] == "ok"
    assert captured["project"] == "facts-production"


def test_mcp_verify_delegates_project_and_urls(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_verify(**kwargs):
        captured.update(kwargs)
        return {"status": "ok"}

    monkeypatch.setattr(server, "verify_production", fake_verify)
    result = server.verify(
        project="facts-production",
        backends=["elk", "rustrak"],
        public_urls=["https://facts.example/health"],
    )

    assert result == {"status": "ok"}
    assert captured["project"] == "facts-production"
    assert captured["public_urls"] == ["https://facts.example/health"]
