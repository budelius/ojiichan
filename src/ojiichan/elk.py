"""Reusable OpenSearch/ELK diagnostics and structured logging."""

from __future__ import annotations

import logging
import os
import socket
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .config import ElkConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ElkLogEntry:
    timestamp: str
    level: str
    index: str
    operation: str
    message: str
    source: dict[str, Any]


class ElkClient:
    """Small wrapper around OpenSearch for app diagnostics."""

    def __init__(self, config: ElkConfig | None = None, *, service: str | None = None) -> None:
        self.config = config or ElkConfig.from_env()
        self.service = service or os.getenv("SERVICE_NAME") or os.getenv("APP_NAME") or "app"
        self._client: Any | None = None

    def connect(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.config.url:
            raise RuntimeError("ELASTICSEARCH_HOST is not set")
        from opensearchpy import OpenSearch

        self._client = OpenSearch(
            hosts=[self.config.url],
            http_auth=(self.config.username, self.config.password)
            if self.config.username and self.config.password
            else None,
            verify_certs=self.config.verify_certs,
            # Trust obachan's private ``obachan-internal-ca`` when a bundle is
            # configured; ignored when verify_certs is False. ``None`` keeps the
            # system trust store (unchanged default behaviour).
            ca_certs=self.config.ca_cert,
            ssl_show_warn=False,
            timeout=self.config.timeout,
        )
        self._client.info()
        return self._client

    def index_name(self, topic: str | None = None) -> str:
        return f"{self.config.index_prefix}-{topic}" if topic else self.config.index_prefix

    def log_event(
        self,
        event_type: str,
        data: dict[str, Any] | None = None,
        *,
        level: str = "info",
        topic: str | None = None,
        subtype: str | None = None,
        failure: bool | None = None,
        redact_keys: Iterable[str] | None = None,
    ) -> bool:
        """Ship one structured event to ELK; falls back to stdlib logging on failure.

        ``redact_keys`` removes the listed keys from the payload before the
        document is built — used to keep secrets (access tokens, JWTs, etc.)
        out of the index even if a caller forgets to scrub them.
        """
        payload = dict(data or {})
        for key in redact_keys or ():
            payload.pop(key, None)
        document = {
            "@timestamp": datetime.now(UTC).isoformat(),
            "event_type": event_type,
            "operation": payload.get("operation") or event_type,
            "level": level,
            "service": self.service,
            "hostname": socket.gethostname(),
            **payload,
        }
        if subtype:
            document["subtype"] = subtype
        if failure is not None:
            document["failure"] = failure

        index = self.index_name(topic or event_type)
        try:
            return self._ship(index, document, level=level)
        except Exception as exc:
            logger.warning(
                "Failed to log via %s transport: %s", self.config.transport, exc, exc_info=True
            )
            logger.log(
                getattr(logging, level.upper(), logging.INFO),
                "[ELK:%s] %s",
                index,
                document,
            )
            return False

    def _ship(self, index: str, document: dict[str, Any], *, level: str) -> bool:
        """Dispatch one built document to the configured write transport.

        ``logstash`` and ``otlp`` are HTTP sinks; ``es`` (the default) indexes
        straight into OpenSearch. Unconfigured transports fall back to stdlib
        logging so the diagnostics path never throws.
        """
        transport = self.config.transport
        if transport in {"logstash", "otlp"}:
            if not self.config.write_target:
                logger.log(
                    getattr(logging, level.upper(), logging.INFO),
                    "[ELK:%s] %s",
                    index,
                    document,
                )
                return False
            from .transports import send_logstash, send_otlp

            if transport == "logstash":
                return send_logstash(self.config, index, document)
            return send_otlp(self.config, document, level=level)

        result = self.connect().index(index=index, body=document)
        return result.get("result") in {"created", "updated"}

    def query_logs(
        self,
        *,
        hours: int = 24,
        limit: int = 50,
        topic: str | None = None,
        source: str | None = None,
        failures_only: bool = False,
    ) -> tuple[int | None, list[ElkLogEntry]]:
        must: list[dict[str, Any]] = [{"range": {"@timestamp": {"gte": f"now-{hours}h"}}}]
        if failures_only:
            must.append({"term": {"failure": True}})
        if source:
            must.append({"term": {"source": source}})
        index = f"{self.config.index_prefix}-{topic}*" if topic else f"{self.config.index_prefix}-*"
        response = self.connect().search(
            index=index,
            body={
                "size": limit,
                "query": {"bool": {"must": must}},
                "sort": [{"@timestamp": "desc"}],
            },
        )
        total = response.get("hits", {}).get("total", {})
        total_value = total.get("value") if isinstance(total, dict) else None
        entries: list[ElkLogEntry] = []
        for hit in response.get("hits", {}).get("hits", []):
            src = hit.get("_source", {})
            entries.append(
                ElkLogEntry(
                    timestamp=str(src.get("@timestamp", "?")),
                    level=str(src.get("level", "?")),
                    index=str(hit.get("_index", "")),
                    operation=str(src.get("operation") or src.get("event_type") or "?"),
                    message=str(src.get("message") or src.get("error") or "")[:500],
                    source=src,
                )
            )
        return total_value, entries


def check_elk(
    *, hours: int = 24, top_n: int = 5, config: ElkConfig | None = None
) -> dict[str, Any]:
    """Health check: connection, recent event volume, failure-tagged events."""
    out: dict[str, Any] = {"status": "ok", "hours": hours}
    client = ElkClient(config)
    if not client.config.url:
        return {
            **out,
            "status": "fail",
            "error": "ELASTICSEARCH_HOST not set",
            "message": "ELK unreachable; structured diagnostics are not available.",
        }

    index = f"{client.config.index_prefix}-*"
    since = {"range": {"@timestamp": {"gte": f"now-{hours}h"}}}
    try:
        es = client.connect()
        out["cluster"] = es.info().get("cluster_name")
        out["events_total"] = es.count(index=index, body={"query": {"bool": {"must": [since]}}})[
            "count"
        ]
    except Exception as exc:
        return {**out, "status": "fail", "error": f"ES query failed: {exc}"}

    try:
        failures = es.count(
            index=index,
            body={"query": {"bool": {"must": [since, {"term": {"failure": True}}]}}},
        )["count"]
    except Exception:
        failures = 0
    out["events_failures"] = failures

    try:
        agg = es.search(
            index=index,
            body={
                "size": 0,
                "query": {"bool": {"must": [since, {"term": {"failure": True}}]}},
                "aggs": {
                    "by_op": {
                        "terms": {
                            "field": "operation.keyword",
                            "size": top_n,
                            "missing": "(unknown)",
                        }
                    }
                },
            },
        )
        buckets = agg.get("aggregations", {}).get("by_op", {}).get("buckets", [])
        out["top_failure_ops"] = [(b["key"], b["doc_count"]) for b in buckets]
    except Exception:
        out["top_failure_ops"] = []

    if out["events_total"] == 0:
        out["status"] = "warn"
        out["message"] = f"No ELK events in the last {hours}h."
    elif failures:
        out["status"] = "warn"
        out["message"] = f"{failures} failure-tagged event(s) in the last {hours}h."
    return out


def query_elk_logs(**kwargs: Any) -> tuple[int | None, list[ElkLogEntry]]:
    return ElkClient().query_logs(**kwargs)
