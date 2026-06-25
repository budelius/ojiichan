"""Write transports for structured ELK events.

``ElkClient.log_event`` builds one canonical document and then ships it through
the transport selected by ``ELK_TRANSPORT`` (``es`` | ``logstash`` | ``otlp``).
The ``es`` path lives in :mod:`ojiichan.elk` (it uses the Elasticsearch client);
this module holds the two HTTP-based alternatives so the document shape stays in
one place and only the sink changes.

Both helpers return ``True`` on success and ``False`` on any failure so the
diagnostics path never raises — the caller logs to stdlib logging as a fallback.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from .config import ElkConfig

# Map a textual log level to an OTLP SeverityNumber.
# https://opentelemetry.io/docs/specs/otel/logs/data-model/#severity-fields
_OTLP_SEVERITY = {
    "trace": 1,
    "debug": 5,
    "info": 9,
    "warn": 13,
    "warning": 13,
    "error": 17,
    "fatal": 21,
    "critical": 21,
}


def otlp_severity_number(level: str) -> int:
    return _OTLP_SEVERITY.get(level.lower(), 9)


def parse_otlp_headers(raw: str | None) -> dict[str, str]:
    """Parse the standard ``OTEL_EXPORTER_OTLP_HEADERS`` format.

    Format is comma-separated ``key=value`` pairs, e.g.
    ``authorization=Bearer xyz,x-tenant=acme``.
    """
    headers: dict[str, str] = {}
    for pair in (raw or "").split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        headers[key.strip()] = value.strip()
    return headers


def send_logstash(config: ElkConfig, index: str, document: dict[str, Any]) -> bool:
    """POST one document to a Logstash HTTP input.

    The target index is attached as an ``index`` field so a Logstash pipeline
    can route to the same ``<prefix>-<topic>`` index the ``es`` transport writes
    to directly (e.g. ``elasticsearch { index => "%{index}" }``).
    """
    if not config.logstash_url:
        return False
    payload = {**document, "index": index}
    auth = (
        (config.username, config.password)
        if config.username and config.password
        else None
    )
    response = httpx.post(
        config.logstash_url,
        json=payload,
        auth=auth,
        timeout=config.timeout,
        verify=config.verify_certs,
    )
    return 200 <= response.status_code < 300


def _otlp_any_value(value: Any) -> dict[str, Any]:
    """Wrap a Python value as an OTLP AnyValue."""
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, (dict, list)):
        return {"stringValue": _json_compact(value)}
    return {"stringValue": str(value)}


def _json_compact(value: Any) -> str:
    import json

    return json.dumps(value, default=str, separators=(",", ":"))


def _time_unix_nano(timestamp: str | None) -> str:
    """Convert the document's ISO ``@timestamp`` to a UnixNano string."""
    if timestamp:
        try:
            dt = datetime.fromisoformat(timestamp)
            return str(int(dt.timestamp() * 1_000_000_000))
        except ValueError:
            pass
    return "0"


def send_otlp(config: ElkConfig, document: dict[str, Any], *, level: str) -> bool:
    """Ship the event as an OTLP/HTTP JSON LogRecord.

    Posts to ``<OTEL_EXPORTER_OTLP_ENDPOINT>/v1/logs``. Every document field
    becomes a log-record attribute; ``service`` is promoted to the resource's
    ``service.name`` so the event lands correctly in OTel-aware backends.
    """
    endpoint = config.otlp_endpoint
    if not endpoint:
        return False
    url = endpoint if endpoint.endswith("/v1/logs") else f"{endpoint}/v1/logs"

    attributes = [
        {"key": key, "value": _otlp_any_value(value)}
        for key, value in document.items()
        if key != "@timestamp"
    ]
    body_text = str(document.get("message") or document.get("event_type") or "")
    payload = {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        {
                            "key": "service.name",
                            "value": {"stringValue": str(document.get("service") or "app")},
                        }
                    ]
                },
                "scopeLogs": [
                    {
                        "scope": {"name": "ojiichan"},
                        "logRecords": [
                            {
                                "timeUnixNano": _time_unix_nano(document.get("@timestamp")),
                                "severityNumber": otlp_severity_number(level),
                                "severityText": level.upper(),
                                "body": {"stringValue": body_text},
                                "attributes": attributes,
                            }
                        ],
                    }
                ],
            }
        ]
    }
    headers = {"Content-Type": "application/json", **parse_otlp_headers(config.otlp_headers)}
    response = httpx.post(
        url,
        json=payload,
        headers=headers,
        timeout=config.timeout,
        verify=config.verify_certs,
    )
    return 200 <= response.status_code < 300
