"""Bugsink/Sentry-compatible issue diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from .config import BugsinkConfig


@dataclass(frozen=True)
class BugsinkIssue:
    id: str | int | None
    type: str
    message: str
    events: int
    first_seen: str | None
    last_seen: str | None
    url: str | None = None


class BugsinkClient:
    """Read unresolved issues from Bugsink's canonical API."""

    def __init__(self, config: BugsinkConfig | None = None) -> None:
        self.config = config or BugsinkConfig.from_env()

    def issues(self, *, ordering: str = "-last_seen", limit: int = 100) -> list[dict[str, Any]]:
        if not self.config.host or not self.config.project_id:
            raise RuntimeError("SENTRY_DSN or BUGSINK_HOST/BUGSINK_PROJECT_ID is required")
        if not self.config.api_token:
            raise RuntimeError("BUGSINK_API_TOKEN is required to query issues")

        url = f"https://{self.config.host}/api/canonical/0/issues/"
        response = httpx.get(
            url,
            params={"project": self.config.project_id, "ordering": ordering, "limit": limit},
            headers={"Authorization": f"Bearer {self.config.api_token}"},
            timeout=self.config.timeout,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("results", []) if isinstance(data, dict) else []

    def resolve_issue(self, issue_id: str | int) -> dict[str, Any]:
        """Bugsink issue resolution is intentionally not supported by ojiichan.

        Earlier versions of ojiichan attempted to PATCH the canonical issue
        endpoint, but Bugsink does not expose a stable resolve API for our
        projects. Use Rustrak (recommended) or Sentry instead.
        """
        return {
            "ok": False,
            "supported": False,
            "backend": "bugsink",
            "issue_id": str(issue_id),
            "message": (
                "Issue resolution is not supported on Bugsink. "
                "Use Rustrak (recommended) or Sentry by passing "
                "--backend rustrak / --backend sentry, or set OJIICHAN_ISSUE_BACKEND."
            ),
        }

    def recent_unresolved(self, *, hours: int = 24, top_n: int = 5) -> list[BugsinkIssue]:
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        out: list[BugsinkIssue] = []
        for issue in self.issues():
            if issue.get("is_resolved") or issue.get("is_muted"):
                continue
            last_seen = issue.get("last_seen")
            if not last_seen:
                continue
            ts = datetime.fromisoformat(str(last_seen).replace("Z", "+00:00"))
            if ts < cutoff:
                continue
            issue_id = issue.get("id")
            out.append(
                BugsinkIssue(
                    id=issue_id,
                    type=issue.get("calculated_type") or "?",
                    message=str(issue.get("calculated_value") or "")[:300],
                    events=int(issue.get("digested_event_count") or 0),
                    first_seen=issue.get("first_seen"),
                    last_seen=last_seen,
                    url=f"https://{self.config.host}/issues/{issue_id}/" if issue_id else None,
                )
            )
        out.sort(key=lambda item: item.events, reverse=True)
        return out[:top_n]


def check_bugsink(
    *, hours: int = 24, top_n: int = 5, config: BugsinkConfig | None = None
) -> dict[str, Any]:
    cfg = config or BugsinkConfig.from_env()
    out: dict[str, Any] = {"status": "ok", "window_hours": hours}
    if not cfg.dsn and not (cfg.host and cfg.project_id):
        return {
            **out,
            "status": "warn",
            "message": "SENTRY_DSN not set; exceptions are not reported.",
        }
    out["host"] = cfg.host
    out["project_id"] = cfg.project_id
    out["ui_url"] = cfg.ui_url
    if not cfg.api_token:
        return {
            **out,
            "status": "warn",
            "message": "BUGSINK_API_TOKEN not set; cannot query issues.",
        }

    try:
        client = BugsinkClient(cfg)
        issues = client.issues()
        unresolved = [i for i in issues if not i.get("is_resolved") and not i.get("is_muted")]
        recent = client.recent_unresolved(hours=hours, top_n=top_n)
    except Exception as exc:
        return {**out, "status": "warn", "message": f"Bugsink query failed: {exc}"}

    out["total_issues"] = len(issues)
    out["unresolved"] = len(unresolved)
    out["recent_unresolved"] = [issue.__dict__ for issue in recent]
    if recent:
        out["status"] = "warn"
        out["message"] = f"{len(recent)} unresolved issue(s) with activity in last {hours}h."
    return out
