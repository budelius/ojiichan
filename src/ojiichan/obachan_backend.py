"""OpenSearch/obachan backend for ojiichan diagnostics.

Reads log events from the obachan platform's OpenSearch, providing an alternative
to Scaleway Cockpit Loki for Knative function log queries and issue-type error
aggregations. Uses the existing opensearch-py client (already a dependency) via
the ElkClient wrapper so TLS, credential resolution, and query logic are reused
without duplication.
"""

from __future__ import annotations

import logging
from typing import Any

from .config import ObachanBackendConfig

logger = logging.getLogger(__name__)


class ObachanLogsClient:
    """Read logs and aggregations from the obachan platform's OpenSearch."""

    def __init__(self, config: ObachanBackendConfig | None = None) -> None:
        self.config = config or ObachanBackendConfig.from_env()
        self._elk_client: Any | None = None

    def _get_elk_client(self) -> Any:
        """Lazily create and cache an ElkClient backed by our elk_config."""
        if self._elk_client is None:
            from .elk import ElkClient

            self._elk_client = ElkClient(config=self.config.elk_config)
        return self._elk_client

    def query_logs(
        self,
        hours: int = 24,
        limit: int = 50,
        *,
        topic: str | None = None,
        source: str | None = None,
        failures_only: bool = False,
        query: str | None = None,  # reserved for future DSL pass-through
    ) -> list[dict[str, Any]]:
        """Return recent log entries from OpenSearch as plain dicts.

        Wraps :meth:`ElkClient.query_logs` and normalises each
        :class:`~ojiichan.elk.ElkLogEntry` into a plain ``dict`` so callers
        do not need to import the dataclass.

        Raises :exc:`RuntimeError` (with a descriptive prefix) when the
        underlying OpenSearch query fails, so callers can handle the error
        uniformly without catching raw ``opensearchpy`` exceptions.
        """
        try:
            _total, entries = self._get_elk_client().query_logs(
                hours=hours,
                limit=limit,
                topic=topic,
                source=source,
                failures_only=failures_only,
            )
            return [
                {
                    "timestamp": e.timestamp,
                    "level": e.level,
                    "index": e.index,
                    "operation": e.operation,
                    "message": e.message,
                    "source": e.source,
                }
                for e in entries
            ]
        except Exception as exc:  # noqa: BLE001 - surface as a typed error to callers
            raise RuntimeError(f"obachan OpenSearch query failed: {exc}") from exc

    def health(self) -> dict[str, Any]:
        """Probe OpenSearch connectivity and return a status dict.

        Returns ``{"status": "ok", "node": <url>}`` on success or
        ``{"status": "fail", "message": <reason>}`` on any connection error.
        """
        try:
            self._get_elk_client().connect()
            return {"status": "ok", "node": self.config.elk_config.url}
        except Exception as exc:  # noqa: BLE001 - surface as structured status
            return {"status": "fail", "message": str(exc)}


def check_obachan(hours: int = 24, top_n: int = 5) -> dict[str, Any]:
    """Health check: OpenSearch reachability + recent error-level log volume.

    Mirrors :func:`~ojiichan.elk.check_elk` so the caller in ``cmd_health``
    can treat both backends uniformly.  Returns one of three status dicts:

    * ``{"status": "skip", ...}`` — ELASTICSEARCH_HOST is not configured.
    * ``{"status": "ok" | "warn", "events_total": N, "events_failures": M,
        "top_failure_ops": [(op, count), ...]}``
    * ``{"status": "fail", "message": <reason>}`` — query raised an exception.
    """
    client = ObachanLogsClient()
    if not client.config.elk_config.url:
        return {"status": "skip", "message": "ELASTICSEARCH_HOST not configured"}

    try:
        entries = client.query_logs(hours=hours, limit=200, failures_only=False)
        events_total = len(entries)
        failures = [
            e
            for e in entries
            if str(e.get("level", "")).upper() in {"ERROR", "CRITICAL", "FATAL"}
        ]
        events_failures = len(failures)

        # Group failure entries by operation field for triage output.
        op_counts: dict[str, int] = {}
        for e in failures:
            op = str(
                e.get("operation")
                or (e.get("source") or {}).get("operation")
                or "(unknown)"
            )
            op_counts[op] = op_counts.get(op, 0) + 1
        top_failure_ops = sorted(op_counts.items(), key=lambda x: x[1], reverse=True)[:top_n]

        return {
            "status": "ok" if events_failures == 0 else "warn",
            "events_total": events_total,
            "events_failures": events_failures,
            "top_failure_ops": top_failure_ops,
        }
    except Exception as exc:  # noqa: BLE001 - surface as a health failure
        return {"status": "fail", "message": str(exc)}
