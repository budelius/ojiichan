"""Query Scaleway Cockpit (Loki) logs for Serverless Functions and other resources.

Scaleway pipes Serverless Function stdout/stderr into the project's Cockpit,
exposed through a Loki-compatible HTTP API. This module gives ojiichan the same
``logs``/``health`` surface for Scaleway as it has for ELK, so a single CLI can
read application events (ELK) *and* platform/runtime logs (Scaleway).

Setup (one-time, values live in Locke/Vaultwarden — see ``locke.json``):

    # the logs data-source URL for the project
    scw cockpit data-source list region=fr-par        # copy the type=logs "url"
    # a token scoped to read logs
    scw cockpit token create name=ojiichan token-scopes.query_logs=true region=fr-par

Then store ``ojiichan/scaleway/loki-url`` and ``ojiichan/scaleway/cockpit-token``
in the vault (or export ``OJIICHAN_SCALEWAY_LOKI_URL`` / ``OJIICHAN_SCALEWAY_COCKPIT_TOKEN``).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from .config import ScalewayConfig

logger = logging.getLogger(__name__)

# Matches the failure-ish lines emitted by Pseekoo services (Express/winston,
# Python tracebacks, FaaS wrappers): "[ERR]", "[CRITICAL]", HTTP 5xx,
# "INTERNAL_SERVER_ERROR", "Login failed", stack traces, etc.
#
# This pattern is also sent to Loki as a LogQL `|~` filter, so it must stay
# within Google RE2 syntax — notably NO `\b` word boundaries (RE2 rejects them
# with a 400). `\d`, alternation, groups and `(?i)` are all RE2-safe.
_FAILURE_RE = (
    r"(?i)(error|exception|traceback|critical|unhandled|"
    r"internal_server_error|fail(ed|ure)?|\[(err|critical|warn)\]|5\d\d)"
)
_FAILURE_PATTERN = re.compile(_FAILURE_RE)
# Best-effort level extraction from a raw log line.
_LEVEL_RE = re.compile(
    r"(?i)\b(critical|fatal|error|err|warn(?:ing)?|info|debug|trace)\b"
)
_LEVEL_NORMALISE = {"err": "error", "warning": "warn", "fatal": "critical"}


@dataclass(frozen=True)
class ScalewayLogEntry:
    """Mirror of :class:`ojiichan.elk.ElkLogEntry` so the CLI renders both alike."""

    timestamp: str
    level: str
    index: str
    operation: str
    message: str
    source: dict[str, Any]


def _logql_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _detect_level(line: str) -> str:
    match = _LEVEL_RE.search(line)
    if match:
        level = match.group(1).lower()
        return _LEVEL_NORMALISE.get(level, level)
    # No explicit level word — treat failure-shaped lines (e.g. "Login failed",
    # "INTERNAL_SERVER_ERROR", HTTP 5xx) as errors so they surface in triage.
    if _FAILURE_PATTERN.search(line):
        return "error"
    return "info"


def _ns_to_iso(ns: str | int) -> str:
    try:
        seconds = int(ns) / 1_000_000_000
        return datetime.fromtimestamp(seconds, tz=UTC).isoformat()
    except (ValueError, TypeError, OverflowError):
        return str(ns)


class ScalewayLogsClient:
    """Read logs from a Scaleway Cockpit Loki data source."""

    def __init__(self, config: ScalewayConfig | None = None) -> None:
        self.config = config or ScalewayConfig.from_env()

    def _headers(self) -> dict[str, str]:
        token = self.config.token or ""
        # Scaleway Cockpit accepts the token via X-Token; we also send a Bearer
        # header so the same code path works against a raw Loki gateway.
        return {"X-Token": token, "Authorization": f"Bearer {token}"}

    def build_query(
        self,
        *,
        topic: str | None = None,
        source: str | None = None,
        failures_only: bool = False,
    ) -> str:
        """Compose a LogQL query from ojiichan's generic log filters."""
        selectors = [f'resource_type="{_logql_escape(self.config.resource_type)}"']
        if source:
            # Scaleway function resource_names are hash-prefixed
            # (e.g. "m3rpapistagingxmxzweeo-m3rp-api-staging-api"), so match a
            # substring rather than requiring the exact name.
            selectors.append(f'resource_name=~".*{_logql_escape(source)}.*"')
        query = "{" + ", ".join(selectors) + "}"
        if topic:
            query += f' |= "{_logql_escape(topic)}"'
        if failures_only:
            # Backtick = raw LogQL string literal, so regex backslashes (\d, ...)
            # reach Loki's RE2 engine intact instead of being parsed as Go-string
            # escapes (which 400s as "invalid char escape").
            query += f" |~ `{_FAILURE_RE}`"
        return query

    def query_logs(
        self,
        *,
        hours: int = 24,
        limit: int = 50,
        topic: str | None = None,
        source: str | None = None,
        failures_only: bool = False,
    ) -> tuple[int | None, list[ScalewayLogEntry]]:
        if not self.config.query_url:
            raise RuntimeError(
                "SCALEWAY_LOKI_URL is not set (Cockpit logs data-source URL)"
            )
        if not self.config.token:
            raise RuntimeError("SCALEWAY_COCKPIT_TOKEN is not set (Cockpit query token)")

        now_ns = int(datetime.now(tz=UTC).timestamp() * 1_000_000_000)
        start_ns = now_ns - hours * 3_600 * 1_000_000_000
        params = {
            "query": self.build_query(
                topic=topic, source=source, failures_only=failures_only
            ),
            "start": str(start_ns),
            "end": str(now_ns),
            "limit": str(limit),
            "direction": "backward",
        }

        resp = httpx.get(
            self.config.query_url,
            params=params,
            headers=self._headers(),
            timeout=self.config.timeout,
        )
        resp.raise_for_status()
        payload = resp.json()

        # Loki query_range -> {data: {result: [{stream: {labels}, values: [[ns, line], ...]}]}}
        rows: list[tuple[int, str, dict[str, Any]]] = []
        for stream in payload.get("data", {}).get("result", []):
            labels = stream.get("stream", {}) or {}
            for ts_ns, line in stream.get("values", []) or []:
                try:
                    ts_int = int(ts_ns)
                except (ValueError, TypeError):
                    ts_int = 0
                rows.append((ts_int, line, labels))

        rows.sort(key=lambda r: r[0], reverse=True)
        rows = rows[:limit]

        entries = [
            ScalewayLogEntry(
                timestamp=_ns_to_iso(ts),
                level=_detect_level(line),
                index=str(labels.get("resource_name") or labels.get("resource_id") or "scaleway"),
                operation=str(labels.get("resource_type") or "log"),
                message=line[:500],
                source={"labels": labels, "line": line},
            )
            for ts, line, labels in rows
        ]
        return len(entries), entries


def check_scaleway(
    *, hours: int = 24, top_n: int = 5, config: ScalewayConfig | None = None
) -> dict[str, Any]:
    """Health check: Loki reachability + recent failure-line volume."""
    out: dict[str, Any] = {"status": "ok", "hours": hours}
    client = ScalewayLogsClient(config)
    if not client.config.query_url or not client.config.token:
        return {
            **out,
            "status": "fail",
            "error": "SCALEWAY_LOKI_URL / SCALEWAY_COCKPIT_TOKEN not set",
            "message": "Scaleway Cockpit logs unavailable; set the Loki URL + token.",
        }

    try:
        total, recent = client.query_logs(hours=hours, limit=top_n)
        _, failures = client.query_logs(hours=hours, limit=top_n, failures_only=True)
    except Exception as exc:  # noqa: BLE001 - surface as a health failure, not a crash.
        return {**out, "status": "fail", "error": f"Scaleway Loki query failed: {exc}"}

    out["events_total"] = total or 0
    out["events_failures"] = len(failures)
    out["recent_failures"] = [
        {"timestamp": e.timestamp, "resource": e.index, "message": e.message}
        for e in failures
    ]
    if not total:
        out["status"] = "warn"
        out["message"] = f"No Scaleway logs in the last {hours}h."
    elif failures:
        out["status"] = "warn"
        out["message"] = f"{len(failures)} failure-like line(s) in the last {hours}h."
    return out


def query_scaleway_logs(**kwargs: Any) -> tuple[int | None, list[ScalewayLogEntry]]:
    return ScalewayLogsClient().query_logs(**kwargs)
