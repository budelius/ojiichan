from __future__ import annotations

from ojiichan import ElkClient, diagnostic, get_diagnostic, parse_annotation
from ojiichan.bugsink import BugsinkClient
from ojiichan.config import BugsinkConfig, RustrakConfig, SentryApiConfig
from ojiichan.rustrak import RustrakClient
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


def test_bugsink_resolve_issue_is_not_supported() -> None:
    client = BugsinkClient(
        BugsinkConfig(
            dsn=None,
            api_token="token",
            host="bugsink.example",
            project_id="1",
        )
    )

    result = client.resolve_issue("123")

    assert result["ok"] is False
    assert result["supported"] is False
    assert result["backend"] == "bugsink"
    assert "Rustrak" in result["message"]


def test_rustrak_resolve_issue(monkeypatch) -> None:
    calls = []

    class Response:
        content = b"{}"

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"id": "abc"}

    def fake_patch(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr("ojiichan.rustrak.httpx.patch", fake_patch)
    client = RustrakClient(
        RustrakConfig(
            api_token="token",
            base_url="https://rustrak.example",
            project_id="42",
        )
    )

    result = client.resolve_issue("abc")

    assert result["ok"] is True
    assert result["backend"] == "rustrak"
    assert result["project_id"] == "42"
    assert calls[0][0] == "https://rustrak.example/api/projects/42/issues/abc"
    assert calls[0][1]["json"] == {"is_resolved": True}
    assert calls[0][1]["headers"] == {"Authorization": "Bearer token"}


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
        "rustrak_resolve_issue",
        "create_bead_from_issue",
        "xcode_build",
        "xcode_test",
    }.issubset(names)
