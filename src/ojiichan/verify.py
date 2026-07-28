"""Aggregate observability and public application health verification."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urlparse

import httpx

from .bugsink import check_bugsink
from .credentials import project_scope
from .elk import check_elk
from .obachan_backend import check_obachan
from .rustrak import check_rustrak
from .scaleway import check_scaleway

BackendCheck = Callable[..., dict[str, Any]]

BACKEND_CHECKS: dict[str, BackendCheck] = {
    "elk": check_elk,
    "rustrak": check_rustrak,
    "bugsink": check_bugsink,
    "obachan": check_obachan,
    "scaleway": check_scaleway,
}
DEFAULT_VERIFY_BACKENDS = ("elk", "rustrak")


def check_public_health(
    url: str,
    *,
    timeout: float = 10.0,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Check one public HTTP health endpoint; HTTP 200 is considered healthy."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {"status": "fail", "url": url, "error": "expected an http(s) URL"}

    started = time.monotonic()
    own_client = client is None
    http = client or httpx.Client(follow_redirects=True, timeout=max(timeout, 0.1))
    try:
        response = http.get(url)
        elapsed_ms = round((time.monotonic() - started) * 1000)
        result: dict[str, Any] = {
            "status": "ok" if response.status_code == 200 else "fail",
            "url": url,
            "status_code": response.status_code,
            "elapsed_ms": elapsed_ms,
        }
        if str(response.url) != url:
            result["final_url"] = str(response.url)
        if response.status_code != 200:
            result["error"] = f"expected HTTP 200, got {response.status_code}"
        return result
    except httpx.HTTPError as exc:
        return {
            "status": "fail",
            "url": url,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "error": str(exc),
        }
    finally:
        if own_client:
            http.close()


def verify_production(
    *,
    project: str | None = None,
    backends: Iterable[str] = DEFAULT_VERIFY_BACKENDS,
    public_urls: Iterable[str] = (),
    hours: int = 24,
    top: int = 5,
    timeout: float = 10.0,
    public_client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Run selected Ojiichan backend checks and public HTTP health checks."""
    selected = tuple(dict.fromkeys(backends))
    unknown = [backend for backend in selected if backend not in BACKEND_CHECKS]
    if unknown:
        raise ValueError(f"unknown verification backend(s): {', '.join(unknown)}")

    backend_results: dict[str, dict[str, Any]] = {}
    with project_scope(project):
        for backend in selected:
            try:
                backend_results[backend] = BACKEND_CHECKS[backend](hours=hours, top_n=top)
            except Exception as exc:  # noqa: BLE001 - aggregate checks must continue.
                backend_results[backend] = {"status": "fail", "error": str(exc)}

    public_results = [
        check_public_health(url, timeout=timeout, client=public_client)
        for url in dict.fromkeys(public_urls)
    ]
    statuses = [result.get("status", "fail") for result in backend_results.values()]
    statuses.extend(result["status"] for result in public_results)
    overall = "fail" if "fail" in statuses else "warn" if "warn" in statuses else "ok"

    return {
        "status": overall,
        "project": project,
        "window_hours": hours,
        "backends": backend_results,
        "public_health": public_results,
        "summary": {
            "ok": statuses.count("ok"),
            "warn": statuses.count("warn"),
            "fail": statuses.count("fail"),
        },
    }
