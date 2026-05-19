"""MCP server exposing ojiichan diagnostics to coding agents.

Run with:
    uv run ojiichan-mcp

The server uses stdio transport by default, which is what most MCP clients
expect for local tools.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from ojiichan.beads import create_bead
from ojiichan.bugsink import BugsinkClient, check_bugsink
from ojiichan.dsl import parse_annotation
from ojiichan.elk import ElkClient, check_elk
from ojiichan.sentry import SentryApiClient

mcp = FastMCP("ojiichan")


def _safe_limit(value: int, *, default: int, maximum: int) -> int:
    if value <= 0:
        return default
    return min(value, maximum)


@mcp.tool()
def elk_health(hours: int = 24, top: int = 5) -> dict[str, Any]:
    """Check Elasticsearch connectivity, recent event volume, and failure-tagged events."""
    return check_elk(hours=_safe_limit(hours, default=24, maximum=24 * 30), top_n=top)


@mcp.tool()
def elk_logs(
    hours: int = 24,
    limit: int = 50,
    topic: str | None = None,
    source: str | None = None,
    failures_only: bool = False,
) -> dict[str, Any]:
    """Query recent structured ELK events.

    Args:
        hours: Lookback window in hours.
        limit: Maximum number of log entries to return.
        topic: Optional index topic suffix, e.g. "auth" or "matrix".
        source: Optional exact-match source field filter.
        failures_only: Only return events where failure=true.
    """
    total, entries = ElkClient().query_logs(
        hours=_safe_limit(hours, default=24, maximum=24 * 30),
        limit=_safe_limit(limit, default=50, maximum=500),
        topic=topic,
        source=source,
        failures_only=failures_only,
    )
    return {"total": total, "entries": [entry.__dict__ for entry in entries]}


@mcp.tool()
def emit_elk_event(
    event_type: str,
    data: dict[str, Any] | None = None,
    level: str = "info",
    topic: str | None = None,
    subtype: str | None = None,
    service: str | None = None,
    failure: bool = False,
) -> dict[str, Any]:
    """Emit a structured diagnostic event to ELK."""
    ok = ElkClient(service=service).log_event(
        event_type,
        data or {},
        level=level,
        topic=topic,
        subtype=subtype,
        failure=failure,
    )
    return {"ok": ok, "event_type": event_type, "topic": topic}


@mcp.tool()
def bugsink_health(hours: int = 24, top: int = 5) -> dict[str, Any]:
    """Check Bugsink/Sentry configuration and unresolved recent issue activity."""
    return check_bugsink(hours=_safe_limit(hours, default=24, maximum=24 * 30), top_n=top)


@mcp.tool()
def bugsink_issues(hours: int = 24, top: int = 10) -> dict[str, Any]:
    """List recent unresolved Bugsink issues."""
    issues = BugsinkClient().recent_unresolved(
        hours=_safe_limit(hours, default=24, maximum=24 * 30),
        top_n=_safe_limit(top, default=10, maximum=100),
    )
    return {"issues": [issue.__dict__ for issue in issues]}


@mcp.tool()
def parse_diagnostic_annotation(text: str) -> dict[str, Any]:
    """Parse an `ojiichan: key=value` code comment into diagnostic fields."""
    return parse_annotation(text).to_event_data()


@mcp.tool()
def bugsink_resolve_issue(issue_id: str) -> dict[str, Any]:
    """Resolve a Bugsink issue by id."""
    return BugsinkClient().resolve_issue(issue_id)


@mcp.tool()
def sentry_resolve_issue(issue_id: str) -> dict[str, Any]:
    """Resolve a Sentry issue/group by id."""
    return SentryApiClient().resolve_issue(issue_id)


@mcp.tool()
def create_bead_from_issue(
    title: str, body: str, labels: list[str] | None = None
) -> dict[str, Any]:
    """Create a Beads issue if the local bead/beads CLI is installed."""
    ok = create_bead(title, body, labels=labels or ["diagnostics"])
    return {"ok": ok, "title": title}


@mcp.tool()
def xcode_test(
    project_path: str,
    scheme: str,
    destination: str,
    only_testing: str | None = None,
    allow_provisioning_updates: bool = False,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Run an xcodebuild test command and return captured output.

    Useful for Mac Designed-for-iPad checks, e.g. destination="platform=macOS,arch=arm64".
    """
    cwd = Path(project_path).expanduser().resolve()
    if not cwd.exists():
        return {"ok": False, "error": f"project_path does not exist: {cwd}"}

    cmd = ["xcodebuild", "-scheme", scheme, "-destination", destination]
    if allow_provisioning_updates:
        cmd.append("-allowProvisioningUpdates")
    if only_testing:
        cmd.append(f"-only-testing:{only_testing}")
    cmd.append("test")

    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=_safe_limit(timeout_seconds, default=300, maximum=1800),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "timed_out": True,
            "cmd": cmd,
            "stdout": exc.stdout,
            "stderr": exc.stderr,
        }

    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "cmd": cmd,
        "stdout_tail": proc.stdout[-12000:],
        "stderr_tail": proc.stderr[-12000:],
    }


@mcp.tool()
def xcode_build(
    project_path: str,
    scheme: str,
    destination: str,
    code_signing_allowed: bool | None = None,
    allow_provisioning_updates: bool = False,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Run an xcodebuild build command and return captured output."""
    cwd = Path(project_path).expanduser().resolve()
    if not cwd.exists():
        return {"ok": False, "error": f"project_path does not exist: {cwd}"}

    cmd = ["xcodebuild", "-scheme", scheme, "-destination", destination]
    if allow_provisioning_updates:
        cmd.append("-allowProvisioningUpdates")
    if code_signing_allowed is not None:
        cmd.append(f"CODE_SIGNING_ALLOWED={'YES' if code_signing_allowed else 'NO'}")
    cmd.append("build")

    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=_safe_limit(timeout_seconds, default=300, maximum=1800),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "timed_out": True,
            "cmd": cmd,
            "stdout": exc.stdout,
            "stderr": exc.stderr,
        }

    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "cmd": cmd,
        "stdout_tail": proc.stdout[-12000:],
        "stderr_tail": proc.stderr[-12000:],
    }


def main() -> None:
    load_dotenv()
    mcp.run()


if __name__ == "__main__":
    main()
