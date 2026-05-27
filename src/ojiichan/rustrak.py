"""Rustrak (Sentry-compatible) issue operations.

Rustrak is an ultra-lightweight error tracking server compatible with Sentry
SDKs (https://github.com/AbianS/rustrak). Ojiichan talks to its REST API for
issue resolution; ingestion still happens via the regular ``sentry-sdk`` and
``SENTRY_DSN``.
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import RustrakConfig


class RustrakClient:
    """Resolve issues against a Rustrak server."""

    def __init__(self, config: RustrakConfig | None = None) -> None:
        self.config = config or RustrakConfig.from_env()

    def resolve_issue(
        self, issue_id: str, project_id: str | int | None = None
    ) -> dict[str, Any]:
        """Mark a Rustrak issue as resolved.

        Rustrak issues are scoped to a project, so a ``project_id`` is required.
        It can come from the call site or from ``RUSTRAK_PROJECT_ID``.
        """
        if not self.config.api_token:
            raise RuntimeError("RUSTRAK_API_TOKEN is required to resolve Rustrak issues")
        pid = str(project_id) if project_id is not None else self.config.project_id
        if not pid:
            raise RuntimeError(
                "RUSTRAK_PROJECT_ID is required (or pass project_id=) to resolve Rustrak issues"
            )

        url = f"{self.config.base_url}/api/projects/{pid}/issues/{issue_id}"
        response = httpx.patch(
            url,
            json={"is_resolved": True},
            headers={"Authorization": f"Bearer {self.config.api_token}"},
            timeout=self.config.timeout,
        )
        response.raise_for_status()
        return {
            "ok": True,
            "backend": "rustrak",
            "issue_id": str(issue_id),
            "project_id": pid,
            "response": response.json() if response.content else {},
        }
