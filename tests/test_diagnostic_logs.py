from datetime import UTC, datetime

import pytest

from ojiichan import diagnostic_logs as logs
from ojiichan.cli import main
from ojiichan.config import ElkConfig


class Search:
    def __init__(self, fail=False, empty=False):
        self.calls = []
        self.fail = fail
        self.empty = empty

    def search(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("secret-password-must-not-appear")
        if kwargs["body"]["size"] == 0:
            value = None if self.empty else datetime(2026, 10, 12, tzinfo=UTC).timestamp() * 1000
            return {"aggregations": {"oldest": {"value": value}, "newest": {"value": value}}}
        return {"hits": {"total": {"value": 0}, "hits": []}}


@pytest.fixture
def search(monkeypatch):
    result = Search()
    monkeypatch.setattr(
        logs, "read_config", lambda *a, **kw: ElkConfig(host="https://example.test")
    )
    monkeypatch.setattr(logs.ElkClient, "connect", lambda self: result)
    return result


def test_missing_history_and_both_schemas(search):
    result = logs.query_diagnostics(
        "example", since="2026-10-11T09:25:00Z", until="2026-10-11T09:35:00Z", room_id="!id:server"
    )
    assert result["status"] == "missing_historical_coverage"
    assert [c["index"] for c in search.calls] == ["example-*"] * 2 + ["app-otlp-*"] * 2
    assert '"time"' in __import__("json").dumps(search.calls[3])
    assert "serviceName" in str(search.calls[2])
    assert "!id:server" in str(search.calls[1])
    assert "query_string" not in str(search.calls)


def test_no_available_logs(search):
    search.empty = True
    assert logs.query_diagnostics("example")["status"] == "no_available_logs"


def test_no_matches_with_available_history(search):
    assert (
        logs.query_diagnostics(
            "example", since="2026-10-12T00:00:00Z", until="2026-10-12T01:00:00Z"
        )["status"]
        == "no_matches"
    )


@pytest.mark.parametrize(
    "options",
    [
        {"since": "2026-10-11T09:00:00"},
        {"limit": 0},
        {"hours": -1},
        {"since": "2026-10-12T00:00:00Z", "until": "2026-10-11T00:00:00Z"},
    ],
)
def test_validation_before_network(search, options):
    with pytest.raises(ValueError):
        logs.query_diagnostics("example", **options)
    assert not search.calls


def test_query_failure_not_zero_or_secret(search, capsys):
    search.fail = True
    assert main(["logs", "--project", "example", "--json"]) == 1
    output = capsys.readouterr().out
    assert "query_failed" in output
    assert "secret-password" not in output


def test_requires_project(capsys):
    assert main(["logs", "--json"]) == 1
    assert "require --project" in capsys.readouterr().out


def test_no_implicit_writer_fallback(monkeypatch):
    monkeypatch.setattr(
        logs.ElkConfig,
        "from_env",
        lambda: ElkConfig(host="writer-host", username="writer", password="writer-secret"),
    )
    monkeypatch.setattr(logs, "env", lambda name: None)
    with pytest.raises(ValueError, match="Writer credentials are not used"):
        logs.read_config("example")


def test_explicit_admin_and_project_pin(monkeypatch):
    monkeypatch.setattr(
        logs.ElkConfig,
        "from_env",
        lambda: ElkConfig(host="wrong-host", index_prefix="other-project"),
    )
    values = dict(
        OPENSEARCH_ADMIN_URL="https://read.test",
        OPENSEARCH_ADMIN_USER="admin",
        OPENSEARCH_ADMIN_PASSWORD="secret",
    )
    monkeypatch.setattr(logs, "env", values.get)
    config = logs.read_config("example", admin=True)
    assert config.index_prefix == "example"
    assert config.host == "https://read.test"
    with pytest.raises(ValueError):
        logs.read_config("*", admin=True)


def test_topic_uses_index_for_legacy_direct_events(search):
    logs.query_diagnostics("example", topic="usage", storage="direct")
    assert search.calls[1]["index"] == "example-usage*"
    assert "log.attributes.topic" not in str(search.calls[1])
    with pytest.raises(ValueError):
        logs.query_diagnostics("example", topic="usage,other-*")


def test_unsupported_backend_flags_fail(capsys):
    assert main(["logs", "--backend", "scaleway", "--room-id", "!room:host"]) == 1
    assert "require --backend elk" in capsys.readouterr().err


def test_merge_sorts_both_timestamp_schemas_and_preserves_total(search, monkeypatch):
    def response(**kwargs):
        if kwargs["body"]["size"] == 0:
            return {"aggregations": {}}
        direct = kwargs["index"] == "example-*"
        doc = (
            {"@timestamp": "2026-10-11T09:30:00Z"}
            if direct
            else {"time": "2026-10-11T09:31:00Z", "serviceName": "example"}
        )
        return {
            "hits": {"total": {"value": 1}, "hits": [{"_index": kwargs["index"], "_source": doc}]}
        }

    monkeypatch.setattr(search, "search", response)
    result = logs.query_diagnostics("example", limit=1)
    assert result["total"] == 2
    assert len(result["entries"]) == 1
    assert result["entries"][0]["storage"] == "otlp"


def test_mcp_diagnostic_logs_uses_same_scoped_path(search):
    from ojiichan_mcp.server import diagnostic_logs

    result = diagnostic_logs("example", since="2026-10-11T09:25:00Z", until="2026-10-11T09:35:00Z")
    assert result["status"] == "missing_historical_coverage"
    search.fail = True
    assert diagnostic_logs("example")["status"] == "query_failed"


def test_daily_index_rotation(monkeypatch):
    from ojiichan import elk

    class Clock(datetime):
        @classmethod
        def now(cls, tz):
            return datetime(2026, 10, 11, tzinfo=UTC)

    monkeypatch.setattr(elk, "datetime", Clock)
    assert elk.ElkClient(ElkConfig(host=None, index_prefix="example")).index_name("usage") == (
        "example-usage-2026-10-11"
    )


def test_partial_results_rejected():
    for response in ({"timed_out": True}, {"_shards": {"failed": 1}}):
        with pytest.raises(RuntimeError):
            logs._complete(response)
