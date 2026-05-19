"""Sentry/Bugsink SDK helpers."""

from __future__ import annotations

import os
from typing import Any

import httpx

from .config import SentryApiConfig, env


def init_sentry(
    *,
    dsn: str | None = None,
    service: str | None = None,
    environment: str | None = None,
    release: str | None = None,
    traces_sample_rate: float | None = None,
    profiles_sample_rate: float | None = None,
    **kwargs: Any,
) -> bool:
    """Initialize sentry-sdk if a DSN is configured.

    Works with Bugsink because Bugsink exposes Sentry-compatible ingestion.
    Returns True when initialized, False when no DSN or sentry-sdk missing.
    """
    dsn = dsn or env("SENTRY_DSN")
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        return False

    sentry_sdk.init(
        dsn=dsn,
        environment=environment or env("ENVIRONMENT") or env("APP_ENV") or os.getenv("ENV"),
        release=release or env("RELEASE") or os.getenv("GIT_COMMIT"),
        traces_sample_rate=traces_sample_rate
        if traces_sample_rate is not None
        else float(env("SENTRY_TRACES_SAMPLE_RATE", "0.0") or "0.0"),
        profiles_sample_rate=profiles_sample_rate,
        server_name=service or env("SERVICE_NAME") or env("APP_NAME"),
        **kwargs,
    )
    return True


class SentryApiClient:
    """Small Sentry REST API client for issue operations."""

    def __init__(self, config: SentryApiConfig | None = None) -> None:
        self.config = config or SentryApiConfig.from_env()

    def resolve_issue(self, issue_id: str | int) -> dict[str, Any]:
        """Mark a Sentry issue/group as resolved.

        ``issue_id`` is Sentry's group/issue id. Requires ``SENTRY_AUTH_TOKEN``
        with issue write permissions.
        """
        if not self.config.auth_token:
            raise RuntimeError("SENTRY_AUTH_TOKEN is required to resolve Sentry issues")
        url = f"{self.config.base_url}/api/0/issues/{issue_id}/"
        response = httpx.put(
            url,
            json={"status": "resolved"},
            headers={"Authorization": f"Bearer {self.config.auth_token}"},
            timeout=self.config.timeout,
        )
        response.raise_for_status()
        return {
            "ok": True,
            "backend": "sentry",
            "issue_id": str(issue_id),
            "response": response.json() if response.content else {},
        }


def capture_exception(exc: BaseException, **context: Any) -> None:
    """Capture an exception when sentry-sdk is installed/configured."""
    try:
        import sentry_sdk

        with sentry_sdk.push_scope() as scope:
            for key, value in context.items():
                scope.set_extra(key, value)
            sentry_sdk.capture_exception(exc)
    except Exception:
        return
