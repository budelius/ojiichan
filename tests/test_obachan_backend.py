"""Unit tests for the obachan/OpenSearch backend.

Verifies:
- ObachanLogsClient.health() and query_logs() behaviour under mocked ElkClient
- check_obachan() status path (skip / ok / warn / fail)
- ISSUE_BACKENDS contains "obachan"
- default_issue_backend() prefers "obachan" when OpenSearch is configured
- scaleway.py is still importable (guard against accidental deletion)
"""

from __future__ import annotations

import pytest

from ojiichan.config import ISSUE_BACKENDS, default_issue_backend
from ojiichan.credentials import _reset_for_tests
from ojiichan.elk import ElkLogEntry
from ojiichan.obachan_backend import ObachanLogsClient, check_obachan


# ---------------------------------------------------------------------------
# ISSUE_BACKENDS / default_issue_backend
# ---------------------------------------------------------------------------


def test_issue_backends_contains_obachan() -> None:
    assert "obachan" in ISSUE_BACKENDS


def test_default_issue_backend_prefers_obachan_when_opensearch_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ELASTICSEARCH_HOST", "https://os.example.com:9200")
    monkeypatch.delenv("OJIICHAN_ISSUE_BACKEND", raising=False)
    monkeypatch.delenv("ISSUE_BACKEND", raising=False)
    _reset_for_tests()
    result = default_issue_backend()
    assert result == "obachan"


def test_default_issue_backend_auto_when_no_opensearch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ELASTICSEARCH_HOST", raising=False)
    monkeypatch.delenv("OJIICHAN_ELASTICSEARCH_HOST", raising=False)
    monkeypatch.delenv("OJIICHAN_ISSUE_BACKEND", raising=False)
    monkeypatch.delenv("ISSUE_BACKEND", raising=False)
    # Disable vault disk cache so stale entries from other tests don't bleed in.
    monkeypatch.setenv("OJIICHAN_VAULT_CACHE_DISABLE", "1")
    _reset_for_tests()
    result = default_issue_backend()
    assert result == "auto"


def test_default_issue_backend_explicit_env_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit OJIICHAN_ISSUE_BACKEND value takes precedence over auto-detect."""
    monkeypatch.setenv("ELASTICSEARCH_HOST", "https://os.example.com:9200")
    monkeypatch.setenv("OJIICHAN_ISSUE_BACKEND", "rustrak")
    _reset_for_tests()
    result = default_issue_backend()
    assert result == "rustrak"
    monkeypatch.delenv("OJIICHAN_ISSUE_BACKEND")


# ---------------------------------------------------------------------------
# check_obachan
# ---------------------------------------------------------------------------


def test_check_obachan_skip_when_no_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELASTICSEARCH_HOST", raising=False)
    monkeypatch.delenv("OJIICHAN_ELASTICSEARCH_HOST", raising=False)
    # Disable vault disk cache so stale entries from other tests don't bleed in.
    monkeypatch.setenv("OJIICHAN_VAULT_CACHE_DISABLE", "1")
    _reset_for_tests()
    result = check_obachan()
    assert result["status"] == "skip"
    assert "ELASTICSEARCH_HOST" in result["message"]


def test_check_obachan_ok_when_no_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELASTICSEARCH_HOST", "https://os.example.com:9200")
    _reset_for_tests()

    fake_entry = ElkLogEntry(
        timestamp="2026-07-03T00:00:00+00:00",
        level="info",
        index="app-*",
        operation="test",
        message="all good",
        source={},
    )

    monkeypatch.setattr(
        "ojiichan.elk.ElkClient.query_logs",
        lambda self, **kwargs: (1, [fake_entry]),
    )
    monkeypatch.setattr("ojiichan.elk.ElkClient.connect", lambda self: object())

    result = check_obachan()
    assert result["status"] == "ok"
    assert result["events_total"] == 1
    assert result["events_failures"] == 0
    assert result["top_failure_ops"] == []


def test_check_obachan_warn_when_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELASTICSEARCH_HOST", "https://os.example.com:9200")
    _reset_for_tests()

    entries = [
        ElkLogEntry(
            timestamp="2026-07-03T00:00:00+00:00",
            level="ERROR",
            index="app-knative",
            operation="deploy",
            message="container failed",
            source={"operation": "deploy"},
        ),
        ElkLogEntry(
            timestamp="2026-07-03T00:00:01+00:00",
            level="info",
            index="app-knative",
            operation="scale",
            message="scaled to 3",
            source={},
        ),
    ]

    monkeypatch.setattr(
        "ojiichan.elk.ElkClient.query_logs",
        lambda self, **kwargs: (2, entries),
    )
    monkeypatch.setattr("ojiichan.elk.ElkClient.connect", lambda self: object())

    result = check_obachan()
    assert result["status"] == "warn"
    assert result["events_failures"] == 1
    assert result["events_total"] == 2
    assert len(result["top_failure_ops"]) >= 1
    assert result["top_failure_ops"][0][0] == "deploy"


def test_check_obachan_fail_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELASTICSEARCH_HOST", "https://os.example.com:9200")
    _reset_for_tests()

    def boom(self, **kwargs):  # noqa: ARG001
        raise ConnectionError("unreachable")

    monkeypatch.setattr("ojiichan.elk.ElkClient.query_logs", boom)
    monkeypatch.setattr("ojiichan.elk.ElkClient.connect", lambda self: object())

    result = check_obachan()
    assert result["status"] == "fail"
    assert "message" in result


# ---------------------------------------------------------------------------
# ObachanLogsClient.health
# ---------------------------------------------------------------------------


def test_obachan_logs_client_health_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELASTICSEARCH_HOST", "https://os.example.com:9200")
    _reset_for_tests()
    monkeypatch.setattr("ojiichan.elk.ElkClient.connect", lambda self: object())
    client = ObachanLogsClient()
    result = client.health()
    assert result["status"] == "ok"
    assert "node" in result


def test_obachan_logs_client_health_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ELASTICSEARCH_HOST", "https://os.example.com:9200")
    _reset_for_tests()

    def bad_connect(self):  # noqa: ARG001
        raise RuntimeError("conn refused")

    monkeypatch.setattr("ojiichan.elk.ElkClient.connect", bad_connect)
    client = ObachanLogsClient()
    result = client.health()
    assert result["status"] == "fail"
    assert "message" in result


# ---------------------------------------------------------------------------
# Guard: scaleway still importable
# ---------------------------------------------------------------------------


def test_scaleway_still_importable() -> None:
    from ojiichan.scaleway import ScalewayLogsClient  # noqa: PLC0415

    assert ScalewayLogsClient is not None
