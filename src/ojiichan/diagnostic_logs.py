"""Read-only, project-scoped diagnostic queries across direct and OTLP storage."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import ElkConfig, env
from .credentials import project_scope
from .elk import ElkClient


class DiagnosticInputError(ValueError):
    """Safe operator-facing validation message (never backend exception content)."""


def timestamp(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise DiagnosticInputError("timestamps must be ISO-8601 dates with timezone") from None
    if result.tzinfo is None:
        raise DiagnosticInputError("timestamps must include a timezone (e.g. 2026-10-11T09:30:00Z)")
    return result.astimezone(UTC)


def exact(fields: list[str], value: str) -> dict[str, Any]:
    # Both keyword-mapped and dynamically mapped strings occur in deployed indices.
    return {
        "bool": {
            "should": [
                {"term": {key: value}} for field in fields for key in (field, field + ".keyword")
            ],
            "minimum_should_match": 1,
        }
    }


def read_config(project: str, *, admin: bool = False) -> ElkConfig:
    """Scope Python callers too; never implicitly use a writer for a read."""
    with project_scope(project):
        return _read_config(project, admin=admin)


def _read_config(project: str, *, admin: bool) -> ElkConfig:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project):
        raise DiagnosticInputError("project must be a lowercase index-safe name, without wildcards")
    config = ElkConfig.from_env()
    if admin:
        host, user, password = (
            env(k)
            for k in ("OPENSEARCH_ADMIN_URL", "OPENSEARCH_ADMIN_USER", "OPENSEARCH_ADMIN_PASSWORD")
        )
    else:
        host, user, password = (
            env(k)
            for k in (
                "ELASTICSEARCH_READ_HOST",
                "ELASTICSEARCH_READ_USERNAME",
                "ELASTICSEARCH_READ_PASSWORD",
            )
        )
    if not all((host, user, password)):
        raise DiagnosticInputError(
            "Configure ELASTICSEARCH_READ_HOST/USERNAME/PASSWORD for a read-capable identity, "
            "or explicitly opt in to --read-as-admin. Writer credentials are not used."
        )
    return replace(config, host=host, username=user, password=password, index_prefix=project)


def query_diagnostics(
    project: str,
    *,
    since: str | None = None,
    until: str | None = None,
    hours: int = 24,
    limit: int = 50,
    room_id: str | None = None,
    user_hash: str | None = None,
    correlation_id: str | None = None,
    services: list[str] | None = None,
    storage: str = "both",
    admin: bool = False,
    topic: str | None = None,
    source: str | None = None,
    failures_only: bool = False,
) -> dict[str, Any]:
    if storage not in {"both", "direct", "otlp"}:
        raise DiagnosticInputError("storage must be both, direct or otlp")
    if not 1 <= limit <= 1000 or hours <= 0:
        raise DiagnosticInputError("limit must be 1..1000 and hours must be positive")
    if topic and not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", topic):
        raise DiagnosticInputError("topic must be an index-safe name without wildcards")
    end = timestamp(until) if until else datetime.now(UTC)
    start = timestamp(since) if since else end - timedelta(hours=hours)
    if start >= end:
        raise DiagnosticInputError("since must be earlier than until")
    client = ElkClient(read_config(project, admin=admin))
    es = client.connect()
    entries: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    total = 0
    for kind, index, clock in [
        ("direct", f"{project}-{topic}*" if topic else f"{project}-*", "@timestamp"),
        ("otlp", "app-otlp-*", "time"),
    ]:
        if storage not in ("both", kind):
            continue
        scope: list[dict[str, Any]] = []
        if kind == "otlp":
            scope.append(
                {
                    "bool": {
                        "should": [
                            exact(["serviceName", "resource.attributes.service@name"], service)
                            for service in (services or [project])
                        ],
                        "minimum_should_match": 1,
                    }
                }
            )
        base = es.search(
            index=index,
            ignore_unavailable=True,
            allow_no_indices=True,
            body={
                "size": 0,
                "track_total_hits": True,
                "query": {"bool": {"filter": scope}},
                "aggs": {"oldest": {"min": {"field": clock}}, "newest": {"max": {"field": clock}}},
            },
        )
        _complete(base)
        bounds = base.get("aggregations", {})
        oldest = bounds.get("oldest", {}).get("value")
        newest = bounds.get("newest", {}).get("value")
        coverage.append(
            {
                "storage": kind,
                "index": index,
                "oldest": _iso(oldest),
                "newest": _iso(newest),
                "requested_start_precedes_available": oldest is not None
                and start.timestamp() * 1000 < oldest,
            }
        )
        filters = [*scope, {"range": {clock: {"gte": start.isoformat(), "lte": end.isoformat()}}}]
        for field, value in (
            ("room_id", room_id),
            ("user_hash", user_hash),
            ("correlation_id", correlation_id),
            ("source", source),
        ):
            if value:
                filters.append(exact([field, "log.attributes." + field], value))
        if failures_only:
            filters.append(
                {
                    "bool": {
                        "should": [
                            {"term": {"failure": True}},
                            {"term": {"log.attributes.failure": True}},
                        ],
                        "minimum_should_match": 1,
                    }
                }
            )
        if topic and kind == "otlp":
            filters.append(exact(["topic", "log.attributes.topic"], topic))
        response = es.search(
            index=index,
            ignore_unavailable=True,
            allow_no_indices=True,
            body={
                "size": limit,
                "track_total_hits": True,
                "query": {"bool": {"filter": filters}},
                "sort": [{clock: {"order": "desc", "unmapped_type": "date"}}],
            },
        )
        _complete(response)
        hits = response.get("hits", {})
        count = hits.get("total", 0)
        total += count.get("value", 0) if isinstance(count, dict) else count
        for hit in hits.get("hits", []):
            doc = hit.get("_source", {})
            entries.append(
                {
                    "timestamp": doc.get(clock),
                    "index": hit.get("_index"),
                    "storage": kind,
                    "source": doc,
                }
            )
    entries.sort(
        key=lambda e: (
            timestamp(e["timestamp"]) if e["timestamp"] else datetime.min.replace(tzinfo=UTC)
        ),
        reverse=True,
    )
    status = "matches" if total else "no_matches"
    if not total:
        if not any(c["oldest"] for c in coverage):
            status = "no_available_logs"
        elif any(c["requested_start_precedes_available"] for c in coverage):
            status = "missing_historical_coverage"
    return {
        "project": project,
        "status": status,
        "total": total,
        "since": start.isoformat(),
        "until": end.isoformat(),
        "coverage": coverage,
        "entries": entries[:limit],
        "coverage_note": "Bounds describe available events, not guaranteed continuous coverage "
        "or the configured retention policy. No matches do not prove no action occurred.",
    }


def _complete(response: dict[str, Any]) -> None:
    if response.get("timed_out") or response.get("_shards", {}).get("failed", 0):
        raise RuntimeError("Log query returned incomplete results (timeout or failed shards)")


def _iso(milliseconds: float | None) -> str | None:
    return (
        datetime.fromtimestamp(milliseconds / 1000, UTC).isoformat()
        if milliseconds is not None
        else None
    )
