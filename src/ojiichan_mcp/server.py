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
from ojiichan.credentials import get_secret
from ojiichan.dsl import DiagnosticDoc, parse_any
from ojiichan.elk import ElkClient, check_elk
from ojiichan.provision import (
    ElkProvisionConfig,
    ElkProvisioner,
    OtelProvisionConfig,
    OtelProvisioner,
    RustrakProvisionConfig,
    RustrakProvisioner,
    provision_all,
)
from ojiichan.rustrak import RustrakClient, check_rustrak
from ojiichan.scaffold import available_languages, render_files
from ojiichan.scaleway import ScalewayLogsClient, check_scaleway
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
def scaleway_health(hours: int = 24, top: int = 5) -> dict[str, Any]:
    """Check Scaleway Cockpit (Loki) reachability and recent failure-line volume.

    Returns {"status": "fail"} with guidance when SCALEWAY_LOKI_URL /
    SCALEWAY_COCKPIT_TOKEN are not configured.
    """
    return check_scaleway(hours=_safe_limit(hours, default=24, maximum=24 * 30), top_n=top)


@mcp.tool()
def scaleway_logs(
    hours: int = 24,
    limit: int = 50,
    topic: str | None = None,
    source: str | None = None,
    failures_only: bool = False,
) -> dict[str, Any]:
    """Query Scaleway Cockpit (Loki) runtime logs, e.g. Serverless Function stdout/stderr.

    Args:
        hours: Lookback window in hours.
        limit: Maximum number of log lines to return.
        topic: Optional substring line filter (Loki ``|= topic``).
        source: Optional Scaleway ``resource_name`` (e.g. a function name) to scope to.
        failures_only: Only return error/exception/5xx-looking lines.
    """
    total, entries = ScalewayLogsClient().query_logs(
        hours=_safe_limit(hours, default=24, maximum=24 * 30),
        limit=_safe_limit(limit, default=50, maximum=500),
        topic=topic,
        source=source,
        failures_only=failures_only,
    )
    return {"total": total, "entries": [entry.__dict__ for entry in entries]}


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
    """Parse an ojiichan diagnostic code comment into structured fields.

    Handles both DSL forms: the compact `ojiichan: key=value` line and the
    extended `ojiichan:doc` markdown block (summary, `## errors`, `## checks`).
    For a doc block the response includes the parsed `errors`/`checks` lists and
    an agent-facing `markdown` render. Accepts Python/shell (`#`), C/Rust/TS
    (`//`, `///`, `//!`), and block/JSDoc (`/* ... */`, `*`) comment markers.
    """
    parsed = parse_any(text)
    if isinstance(parsed, DiagnosticDoc):
        return {
            "kind": "doc",
            "fields": parsed.to_event_data(),
            "summary": parsed.summary,
            "errors": list(parsed.errors),
            "checks": list(parsed.checks),
            "markdown": parsed.to_markdown(),
        }
    return {"kind": "hint", "fields": parsed.to_event_data()}


@mcp.tool()
def scaffold_observability(language: str, backend: str = "rustrak") -> dict[str, Any]:
    """Render observability setup files for a non-Python service.

    Returns the rendered file contents (the agent writes them) so a TypeScript,
    Rust, or Swift service can report to the same ELK + Rustrak/Sentry stack as the
    Python services: Sentry/Rustrak error reporting plus ELK structured events.

    Args:
        language: Target language, one of "typescript", "rust", or "swift".
        backend: Error-reporting backend referenced in the files
            (``rustrak`` / ``sentry`` / ``bugsink``); the SDK wiring is identical.
    """
    try:
        files = render_files(language, backend=backend)
    except ValueError as exc:
        return {"ok": False, "error": str(exc), "languages": list(available_languages())}
    return {"ok": True, "language": language, "backend": backend, "files": files}


@mcp.tool()
def provision_project(
    project: str,
    apply: bool = False,
    write_vault: bool = True,
) -> dict[str, Any]:
    """One-shot: onboard a project's logging + telemetry onto the obachan cluster.

    The low-interaction entry point — pass just ``project`` and ojiichan resolves
    the OpenSearch admin connection, OTLP endpoint, and Rustrak config from env or
    the vault, then creates the project's ELK access, records its OTLP wiring, and
    registers its Rustrak project, writing every credential back to the vault.
    Backends with no usable config are skipped (with a note), not failed.

    Defaults to a dry-run plan; set ``apply=True`` to make changes.
    """
    results = provision_all(
        project=project,
        elk_admin_url=get_secret("OPENSEARCH_ADMIN_URL"),
        elk_admin_password=get_secret("OPENSEARCH_ADMIN_PASSWORD"),
        elk_admin_user=get_secret("OPENSEARCH_ADMIN_USER") or "admin",
        otlp_endpoint=get_secret("OTEL_EXPORTER_OTLP_ENDPOINT"),
        otlp_headers=get_secret("OTEL_EXPORTER_OTLP_HEADERS"),
        rustrak_dsn=get_secret("SENTRY_DSN"),
        rustrak_api_token=get_secret("RUSTRAK_API_TOKEN"),
        rustrak_project_id=get_secret("RUSTRAK_PROJECT_ID"),
        rustrak_admin_url=get_secret("RUSTRAK_ADMIN_URL"),
        rustrak_admin_token=get_secret("RUSTRAK_ADMIN_TOKEN"),
        dry_run=not apply,
        write_vault=write_vault,
    )
    return {
        "ok": all(r.ok for r in results),
        "results": [r.to_dict() for r in results],
    }


@mcp.tool()
def provision_elk(
    project: str,
    admin_url: str,
    admin_password: str,
    admin_user: str = "admin",
    prefix: str | None = None,
    retention_days: int = 30,
    apply: bool = False,
    write_vault: bool = True,
) -> dict[str, Any]:
    """Onboard one project onto an existing OpenSearch (index template, ISM, role, user).

    Defaults to a dry-run plan (``apply=False``); set ``apply=True`` to create the
    resources and write the scoped credentials to Vault. Mirrors obachan's
    opensearch-bootstrap contract, scoped to a single project.
    """
    config = ElkProvisionConfig(
        admin_url=admin_url,
        admin_user=admin_user,
        admin_password=admin_password,
        project=project,
        index_prefix=prefix or project,
        retention_days=retention_days,
    )
    result = ElkProvisioner(config).provision(dry_run=not apply, write_vault=write_vault)
    return result.to_dict()


@mcp.tool()
def provision_rustrak(
    project: str,
    dsn: str | None = None,
    api_token: str | None = None,
    project_id: str | None = None,
    admin_url: str | None = None,
    admin_token: str | None = None,
    apply: bool = False,
    write_vault: bool = True,
) -> dict[str, Any]:
    """Register (or create) a project's Rustrak DSN/token and stash it in Vault.

    Register-only when given ``dsn``/``api_token``; creates the project when
    ``admin_url``/``admin_token`` are supplied. Dry-run unless ``apply=True``.
    """
    config = RustrakProvisionConfig(
        project=project,
        dsn=dsn,
        api_token=api_token,
        project_id=project_id,
        admin_url=admin_url,
        admin_token=admin_token,
    )
    result = RustrakProvisioner(config).provision(dry_run=not apply, write_vault=write_vault)
    return result.to_dict()


@mcp.tool()
def provision_otel(
    project: str,
    endpoint: str,
    headers: str | None = None,
    probe: bool = False,
    apply: bool = False,
    write_vault: bool = True,
) -> dict[str, Any]:
    """Record the project's OTLP exporter endpoint/headers in Vault.

    Dry-run unless ``apply=True``. ``project`` is accepted for symmetry; the OTLP
    config is shared, so the same endpoint serves every project.
    """
    config = OtelProvisionConfig(endpoint=endpoint, headers=headers, probe=probe)
    result = OtelProvisioner(config).provision(dry_run=not apply, write_vault=write_vault)
    return result.to_dict()


@mcp.tool()
def bugsink_resolve_issue(issue_id: str) -> dict[str, Any]:
    """Bugsink issue resolution is not supported by ojiichan.

    Returns a structured message pointing users at Rustrak or Sentry.
    """
    return BugsinkClient().resolve_issue(issue_id)


@mcp.tool()
def sentry_resolve_issue(issue_id: str) -> dict[str, Any]:
    """Resolve a Sentry issue/group by id."""
    return SentryApiClient().resolve_issue(issue_id)


@mcp.tool()
def rustrak_health(hours: int = 24, top: int = 5) -> dict[str, Any]:
    """Check Rustrak configuration and unresolved recent issue activity."""
    return check_rustrak(hours=_safe_limit(hours, default=24, maximum=24 * 30), top_n=top)


@mcp.tool()
def rustrak_issues(
    hours: int = 24, top: int = 10, project_id: str | None = None
) -> dict[str, Any]:
    """List recent unresolved Rustrak issues."""
    issues = RustrakClient().recent_unresolved(
        hours=_safe_limit(hours, default=24, maximum=24 * 30),
        top_n=_safe_limit(top, default=10, maximum=100),
        project_id=project_id,
    )
    return {"issues": [issue.__dict__ for issue in issues]}


@mcp.tool()
def rustrak_resolve_issue(issue_id: str, project_id: str | None = None) -> dict[str, Any]:
    """Resolve a Rustrak issue by id.

    Rustrak is a Sentry-compatible error tracker; resolution scopes by project.
    ``project_id`` falls back to ``RUSTRAK_PROJECT_ID`` when not given.
    """
    return RustrakClient().resolve_issue(issue_id, project_id=project_id)


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
