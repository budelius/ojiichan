from __future__ import annotations

import json

from ojiichan import ElkClient, diagnostic, get_diagnostic, parse_annotation
from ojiichan.bugsink import BugsinkClient
from ojiichan.config import BugsinkConfig, RustrakConfig, SentryApiConfig
from ojiichan.rustrak import RustrakClient
from ojiichan.rustrak import rustrak_dashboard as build_rustrak_dashboard
from ojiichan.sentry import SentryApiClient
from ojiichan_mcp.server import mcp, rustrak_dashboard, rustrak_issue_reference, rustrak_issues


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


def test_rustrak_config_defaults_ingest_host_and_ui_url() -> None:
    cfg = RustrakConfig(api_token="x", project_id="7")
    assert cfg.base_url == "https://ingest.rustrak.pseekoo.io"
    assert cfg.ui_url == "https://rustrak.pseekoo.io/projects/7"

    cfg_custom = RustrakConfig(
        api_token="x", base_url="https://ingest.rustrak.example", project_id="3"
    )
    assert cfg_custom.ui_url == "https://rustrak.example/projects/3"

    cfg_no_project = RustrakConfig(api_token="x")
    assert cfg_no_project.ui_url is None


def test_rustrak_list_recent_issues(monkeypatch) -> None:
    calls = []

    class Response:
        content = b'{"items":[]}'

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "items": [
                    {
                        "id": "11111111-1111-1111-1111-111111111111",
                        "project_id": 42,
                        "short_id": "PROJ-1",
                        "title": "RuntimeError",
                        "value": "boom",
                        "first_seen": "2026-05-26T10:00:00Z",
                        "last_seen": "2026-05-27T10:00:00Z",
                        "event_count": 5,
                        "is_resolved": False,
                        "is_muted": False,
                    }
                ],
                "total_count": 1,
                "page": 1,
                "per_page": 100,
                "total_pages": 1,
            }

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr("ojiichan.rustrak.httpx.get", fake_get)
    client = RustrakClient(
        RustrakConfig(
            api_token="token",
            base_url="https://ingest.rustrak.example",
            project_id="42",
        )
    )

    issues = client.recent_unresolved(hours=24 * 365)

    assert calls[0][0] == "https://ingest.rustrak.example/api/projects/42/issues"
    assert calls[0][1]["headers"] == {"Authorization": "Bearer token"}
    assert calls[0][1]["params"]["filter"] == "open"
    assert calls[0][1]["params"]["sort"] == "last_seen"
    assert calls[0][1]["params"]["order"] == "desc"
    assert len(issues) == 1
    only = issues[0]
    assert only.events == 5
    assert only.message == "boom"
    assert only.url.startswith("https://rustrak.example/projects/42/issues/")


def test_rustrak_dashboard_counts_all_projects(monkeypatch) -> None:
    calls = []

    class Response:
        content = b"{}"

        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self.payload

    totals = {
        ("42", "open"): 2,
        ("42", "resolved"): 1,
        ("42", "muted"): 0,
        ("42", "all"): 3,
        ("7", "open"): 1,
        ("7", "resolved"): 0,
        ("7", "muted"): 1,
        ("7", "all"): 2,
    }

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/api/projects"):
            return Response(
                {
                    "items": [
                        {
                            "id": 42,
                            "name": "alpha-api",
                            "slug": "alpha-api",
                            "stored_event_count": 10,
                            "digested_event_count": 8,
                        },
                        {
                            "id": 7,
                            "name": "beta-worker",
                            "slug": "beta-worker",
                            "stored_event_count": 3,
                            "digested_event_count": 3,
                        },
                    ],
                    "total_count": 2,
                    "page": 1,
                    "per_page": 100,
                    "total_pages": 1,
                }
            )
        project_id = url.split("/api/projects/", 1)[1].split("/issues", 1)[0]
        issue_filter = kwargs["params"]["filter"]
        return Response({"items": [], "total_count": totals[(project_id, issue_filter)]})

    monkeypatch.setattr("ojiichan.rustrak.httpx.get", fake_get)
    client = RustrakClient(
        RustrakConfig(
            api_token="token",
            base_url="https://ingest.rustrak.example",
            project_id="42",
        )
    )

    dashboard = client.dashboard()

    assert calls[0][0] == "https://ingest.rustrak.example/api/projects"
    assert calls[0][1]["headers"] == {"Authorization": "Bearer token"}
    assert dashboard["project_count"] == 2
    assert dashboard["totals"]["errors_total"] == 5
    assert dashboard["totals"]["open_errors"] == 3
    assert dashboard["totals"]["events"] == {"stored": 13, "digested": 11}
    assert dashboard["projects"][0]["id"] == "42"
    assert dashboard["projects"][0]["ui_url"] == "https://rustrak.example/projects/42"


def test_rustrak_dashboard_warns_without_token() -> None:
    dashboard = build_rustrak_dashboard(config=RustrakConfig(api_token=None))

    assert dashboard["status"] == "warn"
    assert "RUSTRAK_API_TOKEN" in dashboard["message"]


def test_health_routes_to_rustrak_when_backend_selected(monkeypatch) -> None:
    from ojiichan import cli as cli_mod

    monkeypatch.setenv("OJIICHAN_ISSUE_BACKEND", "rustrak")
    monkeypatch.setenv("RUSTRAK_API_TOKEN", "x")
    monkeypatch.setenv("RUSTRAK_PROJECT_ID", "42")
    captured: dict[str, bool] = {"rustrak": False, "bugsink": False}

    def fake_check_rustrak(**kwargs):
        captured["rustrak"] = True
        return {"status": "ok", "window_hours": kwargs.get("hours"), "backend": "rustrak"}

    def fake_check_bugsink(**kwargs):
        captured["bugsink"] = True
        return {"status": "ok", "window_hours": kwargs.get("hours")}

    def fake_check_elk(**kwargs):
        return {"status": "ok"}

    monkeypatch.setattr(cli_mod, "check_rustrak", fake_check_rustrak)
    monkeypatch.setattr(cli_mod, "check_bugsink", fake_check_bugsink)
    monkeypatch.setattr(cli_mod, "check_elk", fake_check_elk)

    parser = cli_mod.build_parser()
    args = parser.parse_args(["health", "--json"])
    rc = cli_mod.cmd_health(args)

    assert rc == 0
    assert captured["rustrak"] is True
    assert captured["bugsink"] is False


def test_health_does_not_query_scaleway_unless_selected(monkeypatch) -> None:
    from ojiichan import cli as cli_mod

    called: dict[str, bool] = {"scaleway": False}

    monkeypatch.delenv("OJIICHAN_HEALTH_BACKENDS", raising=False)
    monkeypatch.setenv("SCALEWAY_LOKI_URL", "https://loki.example")
    monkeypatch.setattr(cli_mod, "check_elk", lambda **kwargs: {"status": "ok"})
    monkeypatch.setattr(cli_mod, "check_bugsink", lambda **kwargs: {"status": "ok"})

    def fake_check_scaleway(**kwargs):
        called["scaleway"] = True
        return {"status": "warn"}

    monkeypatch.setattr(cli_mod, "check_scaleway", fake_check_scaleway)

    args = cli_mod.build_parser().parse_args(["health", "--json"])
    assert cli_mod.cmd_health(args) == 0
    assert called["scaleway"] is False

    args = cli_mod.build_parser().parse_args(["health", "--json", "--backend", "scaleway"])
    assert cli_mod.cmd_health(args) == 0
    assert called["scaleway"] is True


def test_health_project_sets_vault_scope_before_resolution(monkeypatch) -> None:
    from ojiichan import cli as cli_mod

    captured: dict[str, str | None] = {}

    def fake_health_backends():
        captured["project"] = __import__("os").environ.get("OJIICHAN_VAULT_PROJECT")
        return ("elk",)

    monkeypatch.setattr(cli_mod, "health_backends", fake_health_backends)
    monkeypatch.setattr(cli_mod, "check_elk", lambda **kwargs: {"status": "ok"})

    args = cli_mod.build_parser().parse_args(["health", "--json", "--project", "halp"])
    assert cli_mod.cmd_health(args) == 0
    assert captured["project"] == "halp"


def test_rustrak_issue_reference() -> None:
    client = RustrakClient(
        RustrakConfig(
            api_token=None,
            base_url="https://ingest.rustrak.example",
            project_id="42",
        )
    )

    ref = client.issue_reference("abc")

    assert ref["url"] == "https://rustrak.example/projects/42/issues/abc"
    assert ref["ref"] == "rustrak://projects/42/issues/abc"
    assert ref["markdown"] == "[Rustrak issue abc](https://rustrak.example/projects/42/issues/abc)"


def test_mcp_rustrak_filter_and_reference(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class FakeClient:
        def issues(self, **kwargs):
            calls["issues"] = kwargs
            return [{"id": "abc", "state": "resolved"}]

        def issue_reference(self, issue_id, project_id=None):
            calls["reference"] = {"issue_id": issue_id, "project_id": project_id}
            return {"ref": f"rustrak://projects/{project_id}/issues/{issue_id}"}

    monkeypatch.setattr("ojiichan_mcp.server.RustrakClient", FakeClient)

    listed = rustrak_issues(filter="resolved", top=2, project_id="42")
    shared = rustrak_issue_reference("abc", project_id="42")

    assert calls["issues"] == {"project_id": "42", "per_page": 2, "filter": "resolved"}
    assert listed == {"filter": "resolved", "issues": [{"id": "abc", "state": "resolved"}]}
    assert shared == {"ref": "rustrak://projects/42/issues/abc"}


def test_mcp_rustrak_dashboard(monkeypatch) -> None:
    def fake_dashboard(**kwargs):
        return {"status": "ok", "project_ids": kwargs["project_ids"]}

    monkeypatch.setattr("ojiichan_mcp.server.build_rustrak_dashboard", fake_dashboard)

    assert rustrak_dashboard(project_ids=["42"]) == {"status": "ok", "project_ids": ["42"]}


def test_cli_dashboard_json(monkeypatch, capsys) -> None:
    from ojiichan import cli as cli_mod

    def fake_dashboard(**kwargs):
        assert kwargs["project_ids"] == ["42"]
        return {
            "status": "ok",
            "backend": "rustrak",
            "project_count": 1,
            "totals": {
                "errors_total": 3,
                "open_errors": 2,
                "issues": {"open": 2, "resolved": 1, "muted": 0, "all": 3},
                "events": {"stored": 10, "digested": 8},
            },
            "projects": [],
        }

    monkeypatch.setattr(cli_mod, "rustrak_dashboard", fake_dashboard)
    parser = cli_mod.build_parser()
    args = parser.parse_args(["dashboard", "--json", "--project-id", "42"])

    assert cli_mod.cmd_dashboard(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["project_count"] == 1
    assert payload["totals"]["errors_total"] == 3


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
        "rustrak_health",
        "rustrak_dashboard",
        "rustrak_issues",
        "rustrak_issue_reference",
        "create_bead_from_issue",
        "xcode_build",
        "xcode_test",
    }.issubset(names)
