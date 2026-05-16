"""Reusable Elasticsearch/ELK diagnostics and structured logging."""

from __future__ import annotations

import logging
import os
import socket
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
    """Small wrapper around Elasticsearch for app diagnostics."""

    def __init__(self, config: ElkConfig | None = None, *, service: str | None = None) -> None:
        self.config = config or ElkConfig.from_env()
        self.service = service or os.getenv("SERVICE_NAME") or os.getenv("APP_NAME") or "app"
        self._client: Any | None = None

    def connect(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.config.url:
            raise RuntimeError("ELASTICSEARCH_HOST is not set")
        from elasticsearch import Elasticsearch

        self._client = Elasticsearch(
            hosts=[self.config.url],
            basic_auth=(self.config.username, self.config.password)
            if self.config.username and self.config.password
            else None,
            verify_certs=self.config.verify_certs,
            ssl_show_warn=False,
            request_timeout=self.config.timeout,
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
    ) -> bool:
        """Ship one structured event to ELK; falls back to stdlib logging on failure."""
        payload = dict(data or {})
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
            result = self.connect().index(index=index, document=document)
            return result.get("result") in {"created", "updated"}
        except Exception as exc:
            logger.warning("Failed to log to Elasticsearch: %s", exc, exc_info=True)
            logger.log(
                getattr(logging, level.upper(), logging.INFO),
                "[ELK:%s] %s",
                index,
                document,
            )
            return False

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
            size=limit,
            query={"bool": {"must": must}},
            sort=[{"@timestamp": "desc"}],
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
        out["events_total"] = es.count(index=index, query={"bool": {"must": [since]}})["count"]
    except Exception as exc:
        return {**out, "status": "fail", "error": f"ES query failed: {exc}"}

    try:
        failures = es.count(
            index=index,
            query={"bool": {"must": [since, {"term": {"failure": True}}]}},
        )["count"]
    except Exception:
        failures = 0
    out["events_failures"] = failures

    try:
        agg = es.search(
            index=index,
            size=0,
            query={"bool": {"must": [since, {"term": {"failure": True}}]}},
            aggs={
                "by_op": {
                    "terms": {
                        "field": "operation.keyword",
                        "size": top_n,
                        "missing": "(unknown)",
                    }
                }
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
