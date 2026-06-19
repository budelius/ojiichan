"""ojiichan command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from dotenv import load_dotenv

from .bugsink import BugsinkClient, check_bugsink
from .config import ISSUE_BACKENDS, RustrakConfig, ScalewayConfig, default_issue_backend
from .elk import ElkClient, check_elk
from .rustrak import RustrakClient, check_rustrak
from .scaffold import available_languages, scaffold
from .scaleway import ScalewayLogsClient, check_scaleway
from .sentry import SentryApiClient


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


def cmd_health(args: argparse.Namespace) -> int:
    elk = check_elk(hours=args.hours, top_n=args.top)
    backend = _select_issue_health_backend()
    if backend == "rustrak":
        issue_health = check_rustrak(hours=args.hours, top_n=args.top)
        issue_label = "Rustrak"
    else:
        issue_health = check_bugsink(hours=args.hours, top_n=args.top)
        issue_label = "Bugsink/Sentry"
    # Scaleway Cockpit logs are included only when a Loki URL is configured, so
    # projects that don't run on Scaleway see no change.
    scaleway_health = None
    if ScalewayConfig.from_env().query_url:
        scaleway_health = check_scaleway(hours=args.hours, top_n=args.top)

    payload = {"elk": elk, "issues": issue_health, "issue_backend": backend}
    if scaleway_health is not None:
        payload["scaleway"] = scaleway_health
    if args.json:
        _print_json(payload)
    else:
        print(f"Ojiichan health window={args.hours}h backend={backend}\n")
        print(f"{_status_glyph(elk['status'])} ELK")
        if "events_total" in elk:
            print(f"  events={elk['events_total']} failures={elk.get('events_failures', 0)}")
            for op, count in elk.get("top_failure_ops") or []:
                print(f"    └─ {op}: {count}")
        if elk.get("message"):
            print(f"  {elk['message']}")
        if elk.get("error"):
            print(f"  {elk['error']}")

        if scaleway_health is not None:
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

        print(f"\n{_status_glyph(issue_health['status'])} {issue_label}")
        if issue_health.get("ui_url"):
            print(f"  triage: {issue_health['ui_url']}")
        if "total_issues" in issue_health:
            print(
                f"  issues total={issue_health['total_issues']} "
                f"unresolved={issue_health['unresolved']} "
                f"recent={len(issue_health.get('recent_unresolved') or [])}"
            )
            for issue in issue_health.get("recent_unresolved") or []:
                print(f"    └─ [{issue['events']}x] {issue['type']}: {issue['message']}")
        if issue_health.get("message"):
            print(f"  {issue_health['message']}")

    statuses = {elk["status"], issue_health["status"]}
    if scaleway_health is not None:
        statuses.add(scaleway_health["status"])
    return 1 if "fail" in statuses else 0


def cmd_logs(args: argparse.Namespace) -> int:
    backend = getattr(args, "backend", "elk")
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reusable diagnostics for ELK + Bugsink/Sentry")
    parser.add_argument("--env-file", help="Load environment variables from a .env file")
    sub = parser.add_subparsers(dest="cmd", required=True)

    health = sub.add_parser("health", help="Check ELK and Bugsink health")
    health.add_argument("--hours", type=int, default=24)
    health.add_argument("--top", type=int, default=5)
    health.add_argument("--json", action="store_true")
    health.set_defaults(func=cmd_health)

    logs = sub.add_parser("logs", help="Query recent logs (ELK app events or Scaleway Cockpit)")
    logs.add_argument(
        "--backend",
        choices=["elk", "scaleway"],
        default="elk",
        help=(
            "elk: structured app events from Elasticsearch (default). "
            "scaleway: runtime stdout/stderr from Scaleway Cockpit (Loki); "
            "use --source <function-name> to scope to one Serverless Function."
        ),
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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    load_dotenv(args.env_file)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
