from __future__ import annotations

from ojiichan import ElkClient, diagnostic, get_diagnostic, parse_annotation
from ojiichan.bugsink import BugsinkClient
from ojiichan.config import BugsinkConfig, SentryApiConfig
from ojiichan.sentry import SentryApiClient
from ojiichan_mcp.server import mcp


def test_ojiichan_import() -> None:
    assert ElkClient.__name__ == "ElkClient"


def test_diagnostic_dsl() -> None:
    diag = parse_annotation("# ojiichan: operation=login topic=auth failure=true tags=api,auth")

    @diagnostic(diag)
    def login() -> None:
        pass

    assert get_diagnostic(login) == diag
    assert diag.to_event_data()["operation"] == "login"
    assert diag.to_event_data()["failure"] is True


def test_bugsink_resolve_issue(monkeypatch) -> None:
    calls = []

    class Response:
        content = b"{}"

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"id": "123"}

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return Response()

    monkeypatch.setattr("ojiichan.bugsink.httpx.request", fake_request)
    client = BugsinkClient(
        BugsinkConfig(
            dsn=None,
            api_token="token",
            host="bugsink.example",
            project_id="1",
        )
    )

    result = client.resolve_issue("123")

    assert result["ok"] is True
    assert calls[0][0] == "patch"
    assert calls[0][2]["json"] == {"is_resolved": True}


def test_sentry_resolve_issue(monkeypatch) -> None:
    calls = []

    class Response:
        content = b"{}"

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"id": "456"}

    def fake_put(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr("ojiichan.sentry.httpx.put", fake_put)
    client = SentryApiClient(SentryApiConfig(auth_token="token", base_url="https://sentry.example"))

    result = client.resolve_issue("456")

    assert result["ok"] is True
    assert calls[0][0] == "https://sentry.example/api/0/issues/456/"
    assert calls[0][1]["json"] == {"status": "resolved"}


def test_mcp_server_exposes_expected_tools() -> None:
    names = {tool.name for tool in mcp._tool_manager.list_tools()}

    assert {
        "elk_health",
        "elk_logs",
        "emit_elk_event",
        "bugsink_health",
        "bugsink_issues",
        "parse_diagnostic_annotation",
        "bugsink_resolve_issue",
        "sentry_resolve_issue",
        "create_bead_from_issue",
        "xcode_build",
        "xcode_test",
    }.issubset(names)
