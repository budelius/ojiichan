"""ojiichan command line interface."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from dotenv import load_dotenv

from .bugsink import BugsinkClient, check_bugsink
from .config import ISSUE_BACKENDS, RustrakConfig, default_issue_backend, health_backends
from .credentials import get_secret, project_scope, reset_cache
from .elk import ElkClient, check_elk
from .obachan_backend import ObachanLogsClient, check_obachan
from .openbao import check_openbao
from .provision import (
    ElkProvisionConfig,
    ElkProvisioner,
    OtelProvisionConfig,
    OtelProvisioner,
    RustrakProvisionConfig,
    RustrakProvisioner,
    provision_all,
)
from .provision.base import ProvisionResult
from .rustrak import RustrakClient, check_rustrak, rustrak_dashboard
from .scaffold import available_languages, scaffold
from .scaleway import ScalewayLogsClient, check_scaleway
from .sentry import SentryApiClient
from .verify import BACKEND_CHECKS, DEFAULT_VERIFY_BACKENDS, verify_production


def _status_glyph(status: str) -> str:
    return {"ok": "[ OK ]", "warn": "[WARN]", "fail": "[FAIL]"}.get(status, "[ ?? ]")


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _select_issue_health_backend() -> str:
    """Pick the issue-tracker backend for the health command.

    Honors ``OJIICHAN_ISSUE_BACKEND`` (``rustrak`` / ``sentry`` / ``bugsink``).
    Without a declared backend, prefers Rustrak when ``RUSTRAK_API_TOKEN`` is
    configured, else falls back to Bugsink.
    """
    backend = default_issue_backend()
    if backend != "auto":
        return backend
    return "rustrak" if RustrakConfig.from_env().api_token else "bugsink"


def _run_issue_health(backend: str, args: argparse.Namespace) -> tuple[dict[str, Any], str]:
    if backend == "rustrak":
        return check_rustrak(hours=args.hours, top_n=args.top), "Rustrak"
    if backend == "obachan":
        return check_obachan(hours=args.hours, top_n=args.top), "OpenSearch/obachan"
    return check_bugsink(hours=args.hours, top_n=args.top), "Bugsink/Sentry"


def _print_elk_health(elk: dict[str, Any]) -> None:
    print(f"{_status_glyph(elk['status'])} ELK")
    if "events_total" in elk:
        print(f"  events={elk['events_total']} failures={elk.get('events_failures', 0)}")
        for op, count in elk.get("top_failure_ops") or []:
            print(f"    └─ {op}: {count}")
    if elk.get("message"):
        print(f"  {elk['message']}")
    if elk.get("error"):
        print(f"  {elk['error']}")


def _print_scaleway_health(scaleway_health: dict[str, Any]) -> None:
    print(f"\n{_status_glyph(scaleway_health['status'])} Scaleway (Cockpit)")
    if "events_total" in scaleway_health:
        print(
            f"  events={scaleway_health['events_total']} "
            f"failures={scaleway_health.get('events_failures', 0)}"
        )
    for failure in scaleway_health.get("recent_failures") or []:
        print(f"    └─ {failure['resource']}: {failure['message']}")
    if scaleway_health.get("message"):
        print(f"  {scaleway_health['message']}")
    if scaleway_health.get("error"):
        print(f"  {scaleway_health['error']}")


def _print_openbao_health(openbao: dict[str, Any]) -> None:
    marks = {"ok": "OK", "fail": "FAIL", "unknown": "UNKNOWN", "skip": "SKIP"}
    mark = marks.get(openbao["status"], openbao["status"])
    print(f"  openbao: {mark} {openbao.get('message', '')}")
    for node in openbao.get("nodes") or []:
        # Print the state, not just a tick: "unreachable" and "unsealed" are
        # different findings and the summary line alone hides which is which.
        print(f"    └─ {node['address']}: {node.get('state', node['status'])}")


def _print_issue_health(issue_health: dict[str, Any], issue_label: str) -> None:
    print(f"\n{_status_glyph(issue_health['status'])} {issue_label}")
    if issue_health.get("ui_url"):
        print(f"  triage: {issue_health['ui_url']}")
    if "total_issues" in issue_health:
        print(
            f"  issues total={issue_health['total_issues']} "
            f"open={issue_health.get('open', issue_health['unresolved'])} "
            f"resolved={issue_health.get('resolved', 0)} "
            f"muted={issue_health.get('muted', 0)} "
            f"recent={len(issue_health.get('recent_unresolved') or [])}"
        )
        for issue in issue_health.get("recent_unresolved") or []:
            print(f"    └─ [{issue['events']}x] {issue['type']}: {issue['message']}")
    if issue_health.get("message"):
        print(f"  {issue_health['message']}")


def cmd_health(args: argparse.Namespace) -> int:
    with project_scope(args.project):
        return _cmd_health(args)


def _cmd_health(args: argparse.Namespace) -> int:
    selected = tuple(args.backend or health_backends())
    payload: dict[str, Any] = {"health_backends": selected}
    statuses: set[str] = set()
    issue_sections: list[tuple[dict[str, Any], str]] = []

    if "elk" in selected:
        elk = check_elk(hours=args.hours, top_n=args.top)
        payload["elk"] = elk
        statuses.add(elk["status"])

    issue_backend = None
    explicit_issue_backends = [b for b in ("rustrak", "bugsink", "obachan") if b in selected]
    if "issues" in selected:
        issue_backend = _select_issue_health_backend()
        issue_health, issue_label = _run_issue_health(issue_backend, args)
        payload["issues"] = issue_health
        payload["issue_backend"] = issue_backend
        issue_sections.append((issue_health, issue_label))
        statuses.add(issue_health["status"])

    for backend in explicit_issue_backends:
        # Avoid duplicate output when `issues` auto-selected the same backend.
        if backend == issue_backend:
            continue
        issue_health, issue_label = _run_issue_health(backend, args)
        payload[backend] = issue_health
        issue_sections.append((issue_health, issue_label))
        statuses.add(issue_health["status"])

    if "scaleway" in selected:
        scaleway_health = check_scaleway(hours=args.hours, top_n=args.top)
        payload["scaleway"] = scaleway_health
        statuses.add(scaleway_health["status"])

    if "openbao" in selected:
        openbao_health = check_openbao()
        payload["openbao"] = openbao_health
        # "skip" (no addresses configured) must not colour the overall result:
        # not asking is not a failure. "unknown" DOES propagate — a member we
        # could not reach is an unanswered question, not a pass.
        if openbao_health["status"] != "skip":
            statuses.add(openbao_health["status"])

    if args.json:
        _print_json(payload)
    else:
        project = f" project={args.project}" if args.project else ""
        print(f"Ojiichan health window={args.hours}h{project} backends={','.join(selected)}\n")
        if "elk" in payload:
            _print_elk_health(payload["elk"])
        if "scaleway" in payload:
            _print_scaleway_health(payload["scaleway"])
        for issue_health, issue_label in issue_sections:
            _print_issue_health(issue_health, issue_label)

    return 1 if "fail" in statuses else 0


def cmd_verify(args: argparse.Namespace) -> int:
    payload = verify_production(
        project=args.project,
        backends=args.backend or DEFAULT_VERIFY_BACKENDS,
        public_urls=args.url or (),
        hours=args.hours,
        top=args.top,
        timeout=args.timeout,
    )
    if args.json:
        _print_json(payload)
    else:
        print(
            f"Ojiichan verify project={args.project or '-'} "
            f"status={payload['status']} window={args.hours}h"
        )
        for backend, result in payload["backends"].items():
            detail = result.get("message") or result.get("error") or ""
            print(f"{_status_glyph(result['status'])} {backend}: {detail}".rstrip())
        for result in payload["public_health"]:
            detail = result.get("status_code") or result.get("error") or ""
            print(f"{_status_glyph(result['status'])} {result['url']}: {detail}".rstrip())
    return 1 if payload["status"] == "fail" else 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    payload = rustrak_dashboard(project_ids=args.project_id or None)
    if args.json:
        _print_json(payload)
        return 0 if payload["status"] == "ok" else 1

    print("Ojiichan error dashboard backend=rustrak\n")
    print(f"{_status_glyph(payload['status'])} Rustrak")
    if payload["status"] != "ok":
        if payload.get("message"):
            print(f"  {payload['message']}")
        return 1

    totals = payload["totals"]
    issues = totals["issues"]
    events = totals["events"]
    print(
        f"  errors total={totals['errors_total']} "
        f"open={issues.get('open', 0)} "
        f"resolved={issues.get('resolved', 0)} "
        f"muted={issues.get('muted', 0)} "
        f"projects={payload['project_count']}"
    )
    print(f"  events stored={events.get('stored', 0)} digested={events.get('digested', 0)}")

    projects = payload.get("projects") or []
    limit = max(args.limit, 0)
    visible = projects if limit == 0 else projects[:limit]
    for project in visible:
        project_issues = project["issues"]
        project_events = project["events"]
        print(
            f"  - {project['name']} ({project['id']}): "
            f"errors total={project['errors_total']} "
            f"open={project_issues.get('open', 0)} "
            f"resolved={project_issues.get('resolved', 0)} "
            f"muted={project_issues.get('muted', 0)} "
            f"events={project_events.get('stored', 0)}"
        )
        if project.get("ui_url"):
            print(f"    triage: {project['ui_url']}")
    hidden = len(projects) - len(visible)
    if hidden > 0:
        print(f"  ... {hidden} more project(s); use --json for full output")
    for note in payload.get("notes") or []:
        print(f"  note: {note}")
    return 0


def cmd_airtable_log(args: argparse.Namespace) -> int:
    """Emit a structured Airtable maintenance event.

    This is intentionally small and generic: operational tools that mutate
    Airtable records can call this after a dry-run/apply so endpoint rewrites are
    visible in the normal ELK/ojiichan event stream.
    """
    client = ElkClient(service=args.service)
    payload = {
        "operation": args.operation,
        "base_id": args.base,
        "table": args.table,
        "records": args.records,
        "dry_run": args.dry_run,
    }
    if args.old is not None:
        payload["old"] = args.old
    if args.new is not None:
        payload["new"] = args.new
    if args.note:
        payload["note"] = args.note
    ok = client.log_event(
        "airtable_maintenance",
        payload,
        level=args.level,
        topic=args.topic,
        subtype="maintenance",
        failure=args.failure,
    )
    if args.json:
        _print_json({"ok": ok, "event_type": "airtable_maintenance", "payload": payload})
    else:
        print("logged" if ok else "not logged (ELK unavailable)")
    return 0 if ok else 2


def cmd_logs(args: argparse.Namespace) -> int:
    with project_scope(getattr(args, "project", None)):
        return _cmd_logs(args)


def _cmd_logs(args: argparse.Namespace) -> int:
    backend = getattr(args, "backend", "elk")

    if backend == "obachan":
        # ObachanLogsClient.query_logs returns list[dict] rather than a tuple,
        # so it is handled as a separate code path.
        obachan_client = ObachanLogsClient()
        label = "OpenSearch/obachan"
        try:
            entries_dict = obachan_client.query_logs(
                hours=args.hours,
                limit=args.limit,
                topic=args.topic,
                source=args.source,
                failures_only=args.failures_only,
            )
        except RuntimeError as exc:
            print(f"OpenSearch/obachan error: {exc}", file=sys.stderr)
            return 1
        total_obachan = len(entries_dict)
        if args.json:
            _print_json({"backend": backend, "total": total_obachan, "entries": entries_dict})
        else:
            print(f"{label} matches={total_obachan} showing={total_obachan}")
            for entry in entries_dict:
                print(
                    f"  [{entry.get('timestamp')}] {str(entry.get('level', '?')):5s} "
                    f"{str(entry.get('index', '?')):36s} "
                    f"{entry.get('operation', '?')}: {entry.get('message', '')}"
                )
        return 0

    if backend == "scaleway":
        client: ElkClient | ScalewayLogsClient = ScalewayLogsClient()
        label = "Scaleway"
    else:
        client = ElkClient()
        label = "ELK"

    total, entries = client.query_logs(
        hours=args.hours,
        limit=args.limit,
        topic=args.topic,
        source=args.source,
        failures_only=args.failures_only,
    )
    if args.json:
        _print_json(
            {"backend": backend, "total": total, "entries": [entry.__dict__ for entry in entries]}
        )
    else:
        print(f"{label} matches={total if total is not None else '?'} showing={len(entries)}")
        for entry in entries:
            print(
                f"  [{entry.timestamp}] {entry.level:5s} "
                f"{entry.index:36s} {entry.operation}: {entry.message}"
            )
    return 0


def cmd_resolve_issue(args: argparse.Namespace) -> int:
    backend = args.backend if args.backend != "auto" else default_issue_backend()

    if backend == "rustrak":
        payload = RustrakClient().resolve_issue(args.issue_id, project_id=args.project_id)
    elif backend == "sentry":
        payload = SentryApiClient().resolve_issue(args.issue_id)
    elif backend == "bugsink":
        payload = BugsinkClient().resolve_issue(args.issue_id)
    else:
        # No project-level default: prefer Rustrak, then Sentry.
        try:
            payload = RustrakClient().resolve_issue(args.issue_id, project_id=args.project_id)
        except Exception as exc:
            payload = SentryApiClient().resolve_issue(args.issue_id)
            payload.setdefault("notes", []).append(f"rustrak unavailable: {exc}")

    if args.json:
        _print_json(payload)
    else:
        print("resolved" if payload.get("ok") else "not resolved")
        if payload.get("message"):
            print(f"  {payload['message']}")
        for error in payload.get("errors") or []:
            print(f"  {error}")
    return 0 if payload.get("ok") else 1


def cmd_emit(args: argparse.Namespace) -> int:
    data = json.loads(args.data) if args.data else {}
    ok = ElkClient(service=args.service).log_event(
        args.event_type,
        data,
        level=args.level,
        topic=args.topic,
        subtype=args.subtype,
        failure=args.failure,
    )
    print("ok" if ok else "fallback")
    return 0 if ok else 1


def cmd_scaffold(args: argparse.Namespace) -> int:
    result = scaffold(args.lang, backend=args.backend, dest=args.dir, force=args.force)
    if args.json:
        _print_json(result.__dict__)
    else:
        print(
            f"Scaffolded {result.language} observability "
            f"(backend={result.backend}) -> {result.dest}"
        )
        for rel in result.written:
            print(f"  + {rel}")
        for rel in result.skipped:
            print(f"  = {rel} (exists; use --force to overwrite)")
        if not result.written and result.skipped:
            print("No files written; all targets already exist.")
    return 0


def _render_provision(result: ProvisionResult, args: argparse.Namespace) -> int:
    if args.json:
        _print_json(result.to_dict())
    else:
        verb = "Plan" if result.dry_run else "Provision"
        print(f"{verb} {result.target} (ok={result.ok})")
        for step in result.steps:
            glyph = {
                "exists": "[ = ]",
                "created": "[ + ]",
                "would-create": "[ ~ ]",
                "failed": "[FAIL]",
            }.get(step.status, "[ ? ]")
            print(f"  {glyph} {step.name}: {step.detail or step.status}")
            if step.error:
                print(f"        error: {step.error}")
        if result.secrets_written:
            print(f"  vault: wrote {', '.join(result.secrets_written)}")
        for note in result.notes:
            print(f"  note: {note}")
    return 0 if result.ok else 1


def _scoped_project(args: argparse.Namespace) -> str:
    """Effective project for provisioning: ``<project>-<env>`` when ``--env`` is
    given, else ``<project>``. Also pins ``OJIICHAN_VAULT_PROJECT`` to it so any
    vault reads during provisioning resolve from the same env-scoped path, and
    resets any previously-warmed global vault snapshot."""
    env = (getattr(args, "env", None) or "").strip()
    project = f"{args.project}-{env}" if env else args.project
    os.environ["OJIICHAN_VAULT_PROJECT"] = project
    reset_cache()
    return project


def cmd_provision_all(args: argparse.Namespace) -> int:
    """Onboard one project's logging + telemetry with near-zero interaction.

    Resolves the obachan platform admin connection and OTLP endpoint from
    ``--flag`` -> env -> Vault, so the common case is just ``--project <name>``.
    With ``--env`` the project is scoped per-environment (see ``_scoped_project``).
    """
    project = _scoped_project(args)
    results = provision_all(
        project=project,
        elk_admin_url=args.admin_url or get_secret("OPENSEARCH_ADMIN_URL"),
        elk_admin_password=args.admin_password or get_secret("OPENSEARCH_ADMIN_PASSWORD"),
        elk_admin_user=args.admin_user or get_secret("OPENSEARCH_ADMIN_USER") or "admin",
        index_prefix=args.prefix,
        retention_days=args.retention_days,
        otlp_endpoint=args.endpoint or get_secret("OTEL_EXPORTER_OTLP_ENDPOINT"),
        otlp_headers=args.headers or get_secret("OTEL_EXPORTER_OTLP_HEADERS"),
        otlp_probe=args.probe,
        rustrak_dsn=args.dsn or get_secret("SENTRY_DSN"),
        rustrak_api_token=args.api_token or get_secret("RUSTRAK_API_TOKEN"),
        rustrak_project_id=args.project_id or get_secret("RUSTRAK_PROJECT_ID"),
        rustrak_admin_url=get_secret("RUSTRAK_ADMIN_URL"),
        rustrak_admin_token=get_secret("RUSTRAK_ADMIN_TOKEN"),
        dry_run=args.dry_run,
        write_vault=not args.no_vault,
        verify_certs=not args.insecure,
        ca_cert=args.ca_cert or get_secret("ELASTICSEARCH_CA_CERT"),
    )
    if args.json:
        _print_json([r.to_dict() for r in results])
        return 0 if all(r.ok for r in results) else 1
    rc = 0
    for result in results:
        rc |= _render_provision(result, args)
    return rc


def cmd_provision_elk(args: argparse.Namespace) -> int:
    admin_url = args.admin_url or get_secret("OPENSEARCH_ADMIN_URL")
    admin_password = args.admin_password or get_secret("OPENSEARCH_ADMIN_PASSWORD")
    if not admin_url or not admin_password:
        print(
            "error: OpenSearch admin URL and password are required "
            "(--admin-url/--admin-password, OPENSEARCH_ADMIN_URL/OPENSEARCH_ADMIN_PASSWORD, "
            "or seed elk/admin-* in the vault)",
            file=sys.stderr,
        )
        return 2
    project = _scoped_project(args)
    config = ElkProvisionConfig(
        admin_url=admin_url,
        admin_user=args.admin_user or get_secret("OPENSEARCH_ADMIN_USER") or "admin",
        admin_password=admin_password,
        project=project,
        index_prefix=args.prefix or project,
        retention_days=args.retention_days,
        scoped_user=args.scoped_user,
        scoped_password=args.scoped_password,
        verify_certs=not args.insecure,
        ca_cert=args.ca_cert or get_secret("ELASTICSEARCH_CA_CERT"),
    )
    result = ElkProvisioner(config).provision(dry_run=args.dry_run, write_vault=not args.no_vault)
    return _render_provision(result, args)


def cmd_provision_rustrak(args: argparse.Namespace) -> int:
    project = _scoped_project(args)
    config = RustrakProvisionConfig(
        project=project,
        dsn=args.dsn or get_secret("SENTRY_DSN"),
        api_token=args.api_token or get_secret("RUSTRAK_API_TOKEN"),
        project_id=args.project_id or get_secret("RUSTRAK_PROJECT_ID"),
        admin_url=args.admin_url or get_secret("RUSTRAK_ADMIN_URL"),
        admin_token=args.admin_token or get_secret("RUSTRAK_ADMIN_TOKEN"),
        create_path=args.create_path,
        verify_certs=not args.insecure,
        ca_cert=args.ca_cert or get_secret("ELASTICSEARCH_CA_CERT"),
    )
    result = RustrakProvisioner(config).provision(
        dry_run=args.dry_run, write_vault=not args.no_vault
    )
    return _render_provision(result, args)


def cmd_provision_otel(args: argparse.Namespace) -> int:
    endpoint = args.endpoint or get_secret("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        print(
            "error: OTLP endpoint required (--endpoint or OTEL_EXPORTER_OTLP_ENDPOINT)",
            file=sys.stderr,
        )
        return 2
    config = OtelProvisionConfig(
        project=_scoped_project(args),
        endpoint=endpoint,
        headers=args.headers or get_secret("OTEL_EXPORTER_OTLP_HEADERS"),
        probe=args.probe,
        verify_certs=not args.insecure,
        ca_cert=args.ca_cert or get_secret("ELASTICSEARCH_CA_CERT"),
    )
    result = OtelProvisioner(config).provision(dry_run=args.dry_run, write_vault=not args.no_vault)
    return _render_provision(result, args)


def _add_provision_commands(sub: argparse._SubParsersAction) -> None:
    provision = sub.add_parser(
        "provision",
        help="Onboard a project onto the ELK/Rustrak/OTLP platform and stash creds in Vault",
    )
    psub = provision.add_subparsers(dest="target", required=True)

    def _common(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--project", required=True, help="Project/service name")
        parser.add_argument(
            "--env",
            help=(
                "Environment (e.g. staging, production). When set, the effective "
                "project becomes '<project>-<env>' — a separate Rustrak project, "
                "ELK index prefix, and vault scope (ojiichan/projects/<project>-<env>/). "
                "Omit for a single shared project across environments."
            ),
        )
        parser.add_argument(
            "--dry-run", action="store_true", help="Plan only; do not mutate or write Vault"
        )
        parser.add_argument(
            "--no-vault", action="store_true", help="Skip writing credentials to Vault"
        )
        parser.add_argument("--insecure", action="store_true", help="Do not verify TLS certs")
        parser.add_argument(
            "--ca-cert",
            help=(
                "Path to a CA bundle (PEM) to trust — e.g. obachan's "
                "obachan-internal-ca — so TLS to the internal OpenSearch verifies "
                "instead of failing (or ELASTICSEARCH_CA_CERT / vault)"
            ),
        )
        parser.add_argument("--json", action="store_true")

    allp = psub.add_parser(
        "all",
        help="Onboard a project's logging + telemetry in one shot (just --project)",
    )
    _common(allp)
    allp.add_argument("--admin-url", help="OpenSearch URL (or OPENSEARCH_ADMIN_URL / vault)")
    allp.add_argument("--admin-user", help="OpenSearch admin user (default: admin)")
    allp.add_argument(
        "--admin-password", help="OpenSearch admin password (or OPENSEARCH_ADMIN_PASSWORD / vault)"
    )
    allp.add_argument("--prefix", help="Index prefix (default: project name)")
    allp.add_argument("--retention-days", type=int, default=30)
    allp.add_argument("--endpoint", help="OTLP endpoint (or OTEL_EXPORTER_OTLP_ENDPOINT / vault)")
    allp.add_argument("--headers", help="OTLP headers (or OTEL_EXPORTER_OTLP_HEADERS / vault)")
    allp.add_argument("--probe", action="store_true", help="Probe OTLP endpoint reachability")
    allp.add_argument("--dsn", help="Rustrak ingest DSN to register (or SENTRY_DSN / vault)")
    allp.add_argument("--api-token", help="Rustrak API token (or RUSTRAK_API_TOKEN / vault)")
    allp.add_argument("--project-id", help="Rustrak project id (or RUSTRAK_PROJECT_ID / vault)")
    allp.set_defaults(func=cmd_provision_all)

    elk = psub.add_parser("elk", help="Create the project's OpenSearch template/role/user")
    _common(elk)
    elk.add_argument("--admin-url", help="OpenSearch URL (or OPENSEARCH_ADMIN_URL)")
    elk.add_argument("--admin-user", help="OpenSearch admin user (default: admin)")
    elk.add_argument(
        "--admin-password", help="OpenSearch admin password (or OPENSEARCH_ADMIN_PASSWORD)"
    )
    elk.add_argument("--prefix", help="Index prefix (default: project name)")
    elk.add_argument("--retention-days", type=int, default=30)
    elk.add_argument("--scoped-user", help="Scoped writer username (default: <project>-writer)")
    elk.add_argument("--scoped-password", help="Scoped writer password (default: generated)")
    elk.set_defaults(func=cmd_provision_elk)

    rustrak = psub.add_parser("rustrak", help="Register/create the project's Rustrak DSN + token")
    _common(rustrak)
    rustrak.add_argument("--dsn", help="Existing ingest DSN to register (or SENTRY_DSN)")
    rustrak.add_argument(
        "--api-token", help="Existing API token to register (or RUSTRAK_API_TOKEN)"
    )
    rustrak.add_argument("--project-id", help="Rustrak project id (or RUSTRAK_PROJECT_ID)")
    rustrak.add_argument("--admin-url", help="Admin API base URL (or RUSTRAK_ADMIN_URL)")
    rustrak.add_argument("--admin-token", help="Admin API token (or RUSTRAK_ADMIN_TOKEN)")
    rustrak.add_argument(
        # pseekoo's Rustrak (abians7/rustrak) exposes project creation at
        # POST /api/projects, NOT the Sentry-style /api/0/projects/. Override
        # per deployment if the backend differs.
        "--create-path",
        default="api/projects",
        help="Admin project-create path",
    )
    rustrak.set_defaults(func=cmd_provision_rustrak)

    otel = psub.add_parser("otel", help="Record the OTLP endpoint/headers for the project")
    _common(otel)
    otel.add_argument("--endpoint", help="OTLP endpoint (or OTEL_EXPORTER_OTLP_ENDPOINT)")
    otel.add_argument("--headers", help="OTLP headers (or OTEL_EXPORTER_OTLP_HEADERS)")
    otel.add_argument("--probe", action="store_true", help="Probe endpoint reachability")
    otel.set_defaults(func=cmd_provision_otel)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reusable diagnostics for ELK + Bugsink/Sentry")
    parser.add_argument("--env-file", help="Load environment variables from a .env file")
    sub = parser.add_subparsers(dest="cmd", required=True)

    verify = sub.add_parser(
        "verify", help="Aggregate observability backends and public HTTP health checks"
    )
    verify.add_argument("--project", help="Project-scoped Vault credential namespace")
    verify.add_argument(
        "--backend",
        action="append",
        choices=sorted(BACKEND_CHECKS),
        help="Backend to check; repeat to combine (default: elk,rustrak)",
    )
    verify.add_argument(
        "--url", action="append", help="Public HTTP health URL expected to return 200; repeatable"
    )
    verify.add_argument("--hours", type=int, default=24)
    verify.add_argument("--top", type=int, default=5)
    verify.add_argument("--timeout", type=float, default=10.0)
    verify.add_argument("--json", action="store_true")
    verify.set_defaults(func=cmd_verify)

    health = sub.add_parser("health", help="Check project-selected observability backends")
    health.add_argument("--hours", type=int, default=24)
    health.add_argument("--top", type=int, default=5)
    health.add_argument(
        "--project",
        help="Project vault scope (sets OJIICHAN_VAULT_PROJECT before resolving backends/creds)",
    )
    health.add_argument(
        "--backend",
        action="append",
        choices=["elk", "issues", "rustrak", "bugsink", "obachan", "scaleway"],
        help=(
            "Backend to check; repeat to combine. Defaults to OJIICHAN_HEALTH_BACKENDS "
            "or project vault health/backends (default: elk,issues). Scaleway is opt-in."
        ),
    )
    health.add_argument("--json", action="store_true")
    health.set_defaults(func=cmd_health)

    dashboard = sub.add_parser("dashboard", help="Show Rustrak error totals by project")
    dashboard.add_argument(
        "--project-id",
        action="append",
        help="Restrict to a Rustrak project id; repeat to include multiple projects",
    )
    dashboard.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum projects to show in text output (0 shows all)",
    )
    dashboard.add_argument("--json", action="store_true")
    dashboard.set_defaults(func=cmd_dashboard)

    logs = sub.add_parser("logs", help="Query recent logs (ELK app events or Scaleway Cockpit)")
    logs.add_argument(
        "--backend",
        choices=["elk", "scaleway", "obachan"],
        default="elk",
        help=(
            "elk: structured app events from Elasticsearch (default). "
            "scaleway: runtime stdout/stderr from Scaleway Cockpit (Loki); "
            "use --source <function-name> to scope to one Serverless Function. "
            "obachan: log events from the obachan platform OpenSearch."
        ),
    )
    logs.add_argument(
        "--project", help="Project vault scope used before resolving backend credentials"
    )
    logs.add_argument("--hours", type=int, default=24)
    logs.add_argument("--limit", type=int, default=50)
    logs.add_argument("--topic", help="Substring/line filter (Loki: |= topic)")
    logs.add_argument("--source", help="ELK source field, or Scaleway resource_name (function)")
    logs.add_argument("--failures-only", action="store_true")
    logs.add_argument("--json", action="store_true")
    logs.set_defaults(func=cmd_logs)

    emit = sub.add_parser("emit", help="Emit a structured ELK event")
    emit.add_argument("event_type")
    emit.add_argument("--data", default="{}", help="JSON object payload")
    emit.add_argument("--level", default="info")
    emit.add_argument("--topic")
    emit.add_argument("--subtype")
    emit.add_argument("--service")
    emit.add_argument("--failure", action="store_true")
    emit.set_defaults(func=cmd_emit)

    airtable_log = sub.add_parser(
        "airtable-log",
        help="Emit an Airtable maintenance event to ELK",
    )
    airtable_log.add_argument("operation", help="Operation name, e.g. replace-endpoint")
    airtable_log.add_argument("--base", required=True, help="Airtable base id")
    airtable_log.add_argument("--table", required=True, help="Airtable table name/id")
    airtable_log.add_argument("--old", help="Old endpoint/text fragment")
    airtable_log.add_argument("--new", help="New endpoint/text fragment")
    airtable_log.add_argument("--records", type=int, default=0, help="Records affected")
    airtable_log.add_argument("--dry-run", action="store_true")
    airtable_log.add_argument("--note")
    airtable_log.add_argument("--level", default="info")
    airtable_log.add_argument("--topic", default="airtable")
    airtable_log.add_argument("--service")
    airtable_log.add_argument("--failure", action="store_true")
    airtable_log.add_argument("--json", action="store_true")
    airtable_log.set_defaults(func=cmd_airtable_log)

    resolve = sub.add_parser("resolve-issue", help="Resolve a Rustrak/Sentry issue by id")
    resolve.add_argument("issue_id")
    resolve.add_argument(
        "--backend",
        choices=["auto", "rustrak", "sentry", "bugsink"],
        default="auto",
        help=(
            "Issue backend. 'auto' uses OJIICHAN_ISSUE_BACKEND if set, else tries "
            "Rustrak then Sentry. 'bugsink' is intentionally not supported and "
            "returns a friendly message."
        ),
    )
    resolve.add_argument(
        "--project-id",
        help="Rustrak project id (defaults to RUSTRAK_PROJECT_ID)",
    )
    resolve.add_argument("--json", action="store_true")
    resolve.set_defaults(func=cmd_resolve_issue)

    scaffold_cmd = sub.add_parser(
        "scaffold",
        help="Write observability setup (Sentry/Rustrak + ELK) for a TS/Rust/Swift service",
    )
    scaffold_cmd.add_argument(
        "--lang",
        required=True,
        choices=list(available_languages()),
        help="Target language for the scaffolded files",
    )
    scaffold_cmd.add_argument(
        "--backend",
        default="rustrak",
        choices=list(ISSUE_BACKENDS),
        help="Error-reporting backend referenced in the generated files",
    )
    scaffold_cmd.add_argument(
        "--dir", default=".", help="Destination directory (default: current directory)"
    )
    scaffold_cmd.add_argument(
        "--force", action="store_true", help="Overwrite files that already exist"
    )
    scaffold_cmd.add_argument("--json", action="store_true")
    scaffold_cmd.set_defaults(func=cmd_scaffold)

    _add_provision_commands(sub)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    load_dotenv(args.env_file)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
