"""Rustrak (Sentry-compatible) issue operations.

Rustrak is an ultra-lightweight error tracking server compatible with Sentry
SDKs (https://github.com/AbianS/rustrak). Ojiichan talks to its REST API for
issue reads and resolution; ingestion still happens via the regular
``sentry-sdk`` and ``SENTRY_DSN``.
"""

from __future__ import annotations

from collections.abc import Sequence
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
    state: str | None = None
    reference: str | None = None


VALID_ISSUE_FILTERS = {"open", "resolved", "muted", "all"}
ISSUE_COUNT_FILTERS = ("open", "resolved", "muted", "all")


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


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

    def issue_page(
        self,
        *,
        project_id: str | int | None = None,
        per_page: int = 100,
        filter: str = "open",
        sort: str = "last_seen",
        order: str = "desc",
    ) -> dict[str, Any]:
        if not self.config.api_token:
            raise RuntimeError("RUSTRAK_API_TOKEN is required to query Rustrak issues")
        if filter not in VALID_ISSUE_FILTERS:
            raise ValueError(f"filter must be one of {sorted(VALID_ISSUE_FILTERS)}")
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
        return data if isinstance(data, dict) else {"items": []}

    def project_page(
        self,
        *,
        page: int = 1,
        per_page: int = 100,
        order: str = "desc",
    ) -> dict[str, Any]:
        if not self.config.api_token:
            raise RuntimeError("RUSTRAK_API_TOKEN is required to query Rustrak projects")

        url = f"{self.config.base_url}/api/projects"
        response = httpx.get(
            url,
            params={
                "page": max(page, 1),
                "per_page": min(max(per_page, 1), 100),
                "order": order,
            },
            headers={"Authorization": f"Bearer {self.config.api_token}"},
            timeout=self.config.timeout,
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {"items": []}

    def projects(self, *, per_page: int = 100, order: str = "desc") -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page = 1
        while True:
            data = self.project_page(page=page, per_page=per_page, order=order)
            page_items = data.get("items") or []
            if isinstance(page_items, list):
                items.extend(item for item in page_items if isinstance(item, dict))

            total_pages = max(_as_int(data.get("total_pages")) or page, page)
            if page >= total_pages:
                break
            page += 1
        return items

    def issues(
        self,
        *,
        project_id: str | int | None = None,
        per_page: int = 100,
        filter: str = "open",
        sort: str = "last_seen",
        order: str = "desc",
    ) -> list[dict[str, Any]]:
        data = self.issue_page(
            project_id=project_id,
            per_page=per_page,
            filter=filter,
            sort=sort,
            order=order,
        )
        items = data.get("items", [])
        if not isinstance(items, list):
            return []
        return [self.enrich_issue(item, project_id=project_id) for item in items]

    def enrich_issue(
        self, issue: dict[str, Any], *, project_id: str | int | None = None
    ) -> dict[str, Any]:
        issue_id = issue.get("id")
        if issue_id is None:
            return issue
        enriched = dict(issue)
        ref = self.issue_reference(str(issue_id), project_id=project_id)
        enriched.setdefault("url", ref["url"])
        enriched.setdefault("reference", ref["ref"])
        enriched.setdefault("share_markdown", ref["markdown"])
        enriched.setdefault("state", self._issue_state(issue))
        return enriched

    @staticmethod
    def _issue_state(issue: dict[str, Any]) -> str:
        if issue.get("is_resolved"):
            return "resolved"
        if issue.get("is_muted"):
            return "muted"
        return "open"

    def issue_counts(self, *, project_id: str | int | None = None) -> dict[str, int]:
        counts: dict[str, int] = {}
        for issue_filter in ISSUE_COUNT_FILTERS:
            page = self.issue_page(project_id=project_id, per_page=1, filter=issue_filter)
            total = page.get("total_count")
            counts[issue_filter] = int(total) if total is not None else len(page.get("items") or [])
        return counts

    def _ui_root(self, project_id: str) -> str:
        base = RustrakConfig(
            api_token=self.config.api_token,
            base_url=self.config.base_url,
            project_id=project_id,
            timeout=self.config.timeout,
        )
        return base.ui_url or ""

    def issue_reference(self, issue_id: str, project_id: str | int | None = None) -> dict[str, Any]:
        pid = self._require_project(project_id)
        ui_root = self._ui_root(pid)
        url = f"{ui_root}/issues/{issue_id}" if ui_root else None
        ref = f"rustrak://projects/{pid}/issues/{issue_id}"
        markdown = f"[Rustrak issue {issue_id}]({url})" if url else ref
        return {
            "backend": "rustrak",
            "project_id": pid,
            "issue_id": issue_id,
            "url": url,
            "ref": ref,
            "markdown": markdown,
        }

    def project_stats(self, project: dict[str, Any] | str | int) -> dict[str, Any]:
        if isinstance(project, dict):
            project_id = project.get("id")
            if project_id is None:
                raise ValueError("Rustrak project payload is missing id")
            pid = str(project_id)
            name = str(project.get("name") or project.get("slug") or pid)
            slug = project.get("slug")
            stored_events = _as_int(project.get("stored_event_count"))
            digested_events = _as_int(project.get("digested_event_count"))
        else:
            pid = str(project)
            name = pid
            slug = None
            stored_events = 0
            digested_events = 0

        counts = self.issue_counts(project_id=pid)
        return {
            "id": pid,
            "name": name,
            "slug": slug,
            "ui_url": self._ui_root(pid) or None,
            "errors_total": counts.get("all", 0),
            "open_errors": counts.get("open", 0),
            "resolved_errors": counts.get("resolved", 0),
            "muted_errors": counts.get("muted", 0),
            "issues": counts,
            "events": {
                "stored": stored_events,
                "digested": digested_events,
            },
        }

    def dashboard(
        self, *, project_ids: Sequence[str | int] | None = None
    ) -> dict[str, Any]:
        if project_ids:
            projects: list[dict[str, Any] | str | int] = list(project_ids)
        else:
            projects = self.projects()
            if not projects and self.config.project_id:
                projects = [self.config.project_id]

        project_rows = [self.project_stats(project) for project in projects]
        project_rows.sort(
            key=lambda project: (
                -project["open_errors"],
                -project["errors_total"],
                project["name"],
            )
        )

        issue_totals = {
            key: sum(int(project["issues"].get(key, 0)) for project in project_rows)
            for key in ISSUE_COUNT_FILTERS
        }
        event_totals = {
            "stored": sum(int(project["events"].get("stored", 0)) for project in project_rows),
            "digested": sum(int(project["events"].get("digested", 0)) for project in project_rows),
        }
        return {
            "backend": "rustrak",
            "base_url": self.config.base_url,
            "project_count": len(project_rows),
            "totals": {
                "errors_total": issue_totals["all"],
                "open_errors": issue_totals["open"],
                "resolved_errors": issue_totals["resolved"],
                "muted_errors": issue_totals["muted"],
                "issues": issue_totals,
                "events": event_totals,
            },
            "projects": project_rows,
        }

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
            issue_id_str = str(issue_id) if issue_id is not None else None
            ref = self.issue_reference(issue_id_str, project_id=project_id) if issue_id_str else {}
            state = self._issue_state(issue)
            out.append(
                RustrakIssue(
                    id=issue_id_str,
                    type=str(issue.get("title") or issue.get("level") or "?"),
                    message=str(issue.get("value") or short_id or "")[:300],
                    events=int(issue.get("event_count") or 0),
                    first_seen=issue.get("first_seen"),
                    last_seen=last_seen,
                    url=ref.get("url"),
                    state=state,
                    reference=ref.get("ref"),
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
        counts = client.issue_counts()
        items = client.issues(filter="open")
        unresolved = [i for i in items if not i.get("is_resolved") and not i.get("is_muted")]
        recent = client.recent_unresolved(hours=hours, top_n=top_n)
    except Exception as exc:
        return {**out, "status": "warn", "message": f"Rustrak query failed: {exc}"}

    out["total_issues"] = counts.get("all", len(items))
    out["open"] = counts.get("open", len(unresolved))
    out["resolved"] = counts.get("resolved", 0)
    out["muted"] = counts.get("muted", 0)
    out["unresolved"] = out["open"]
    out["recent_unresolved"] = [issue.__dict__ for issue in recent]
    if recent:
        out["status"] = "warn"
        out["message"] = f"{len(recent)} unresolved issue(s) with activity in last {hours}h."
    return out


def rustrak_dashboard(
    *,
    project_ids: Sequence[str | int] | None = None,
    config: RustrakConfig | None = None,
) -> dict[str, Any]:
    cfg = config or RustrakConfig.from_env()
    out: dict[str, Any] = {"status": "ok", "backend": "rustrak", "base_url": cfg.base_url}
    if not cfg.api_token:
        return {
            **out,
            "status": "warn",
            "message": "RUSTRAK_API_TOKEN not set; cannot query Rustrak dashboard.",
        }

    client = RustrakClient(cfg)
    try:
        data = client.dashboard(project_ids=project_ids)
    except Exception as exc:
        if project_ids or not cfg.project_id:
            return {**out, "status": "warn", "message": f"Rustrak dashboard query failed: {exc}"}
        try:
            data = client.dashboard(project_ids=[cfg.project_id])
        except Exception as fallback_exc:
            return {
                **out,
                "status": "warn",
                "message": (
                    "Rustrak dashboard query failed: "
                    f"{exc}; configured-project fallback failed: {fallback_exc}"
                ),
            }
        notes = list(data.get("notes") or [])
        notes.append(f"project list failed ({exc}); used configured RUSTRAK_PROJECT_ID")
        data["notes"] = notes
    return {**out, **data, "status": "ok"}
