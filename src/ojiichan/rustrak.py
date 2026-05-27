"""Rustrak (Sentry-compatible) issue operations.

Rustrak is an ultra-lightweight error tracking server compatible with Sentry
SDKs (https://github.com/AbianS/rustrak). Ojiichan talks to its REST API for
issue reads and resolution; ingestion still happens via the regular
``sentry-sdk`` and ``SENTRY_DSN``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from .config import RustrakConfig


@dataclass(frozen=True)
class RustrakIssue:
    id: str | None
    type: str
    message: str
    events: int
    first_seen: str | None
    last_seen: str | None
    url: str | None = None


class RustrakClient:
    """Talk to a Rustrak server: list issues, resolve issues."""

    def __init__(self, config: RustrakConfig | None = None) -> None:
        self.config = config or RustrakConfig.from_env()

    def _require_project(self, project_id: str | int | None) -> str:
        pid = str(project_id) if project_id is not None else self.config.project_id
        if not pid:
            raise RuntimeError(
                "RUSTRAK_PROJECT_ID is required (or pass project_id=) for Rustrak calls"
            )
        return pid

    def issues(
        self,
        *,
        project_id: str | int | None = None,
        per_page: int = 100,
        filter: str = "open",
        sort: str = "last_seen",
        order: str = "desc",
    ) -> list[dict[str, Any]]:
        if not self.config.api_token:
            raise RuntimeError("RUSTRAK_API_TOKEN is required to query Rustrak issues")
        pid = self._require_project(project_id)

        url = f"{self.config.base_url}/api/projects/{pid}/issues"
        response = httpx.get(
            url,
            params={
                "per_page": min(max(per_page, 1), 100),
                "filter": filter,
                "sort": sort,
                "order": order,
            },
            headers={"Authorization": f"Bearer {self.config.api_token}"},
            timeout=self.config.timeout,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("items", []) if isinstance(data, dict) else []

    def recent_unresolved(
        self,
        *,
        hours: int = 24,
        top_n: int = 5,
        project_id: str | int | None = None,
    ) -> list[RustrakIssue]:
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        out: list[RustrakIssue] = []
        for issue in self.issues(project_id=project_id, filter="open"):
            if issue.get("is_resolved") or issue.get("is_muted"):
                continue
            last_seen = issue.get("last_seen")
            if not last_seen:
                continue
            ts = datetime.fromisoformat(str(last_seen).replace("Z", "+00:00"))
            if ts < cutoff:
                continue
            issue_id = issue.get("id")
            short_id = issue.get("short_id") or issue_id
            ui_root = self.config.ui_url  # https://rustrak.pseekoo.io/projects/<pid>
            issue_url = f"{ui_root}/issues/{issue_id}" if (ui_root and issue_id) else None
            out.append(
                RustrakIssue(
                    id=str(issue_id) if issue_id is not None else None,
                    type=str(issue.get("title") or issue.get("level") or "?"),
                    message=str(issue.get("value") or short_id or "")[:300],
                    events=int(issue.get("event_count") or 0),
                    first_seen=issue.get("first_seen"),
                    last_seen=last_seen,
                    url=issue_url,
                )
            )
        out.sort(key=lambda item: item.events, reverse=True)
        return out[:top_n]

    def resolve_issue(
        self, issue_id: str, project_id: str | int | None = None
    ) -> dict[str, Any]:
        """Mark a Rustrak issue as resolved."""
        if not self.config.api_token:
            raise RuntimeError("RUSTRAK_API_TOKEN is required to resolve Rustrak issues")
        pid = self._require_project(project_id)

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


def check_rustrak(
    *, hours: int = 24, top_n: int = 5, config: RustrakConfig | None = None
) -> dict[str, Any]:
    cfg = config or RustrakConfig.from_env()
    out: dict[str, Any] = {"status": "ok", "window_hours": hours, "backend": "rustrak"}
    out["base_url"] = cfg.base_url
    out["project_id"] = cfg.project_id
    out["ui_url"] = cfg.ui_url
    if not cfg.api_token:
        return {
            **out,
            "status": "warn",
            "message": "RUSTRAK_API_TOKEN not set; cannot query Rustrak issues.",
        }
    if not cfg.project_id:
        return {
            **out,
            "status": "warn",
            "message": "RUSTRAK_PROJECT_ID not set; cannot query Rustrak issues.",
        }

    try:
        client = RustrakClient(cfg)
        items = client.issues()
        unresolved = [i for i in items if not i.get("is_resolved") and not i.get("is_muted")]
        recent = client.recent_unresolved(hours=hours, top_n=top_n)
    except Exception as exc:
        return {**out, "status": "warn", "message": f"Rustrak query failed: {exc}"}

    out["total_issues"] = len(items)
    out["unresolved"] = len(unresolved)
    out["recent_unresolved"] = [issue.__dict__ for issue in recent]
    if recent:
        out["status"] = "warn"
        out["message"] = f"{len(recent)} unresolved issue(s) with activity in last {hours}h."
    return out
