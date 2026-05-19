"""ojiichan command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from dotenv import load_dotenv

from .bugsink import BugsinkClient, check_bugsink
from .elk import ElkClient, check_elk
from .sentry import SentryApiClient


def _status_glyph(status: str) -> str:
    return {"ok": "[ OK ]", "warn": "[WARN]", "fail": "[FAIL]"}.get(status, "[ ?? ]")


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, default=str))


def cmd_health(args: argparse.Namespace) -> int:
    elk = check_elk(hours=args.hours, top_n=args.top)
    bugsink = check_bugsink(hours=args.hours, top_n=args.top)
    payload = {"elk": elk, "bugsink": bugsink}
    if args.json:
        _print_json(payload)
    else:
        print(f"Ojiichan health window={args.hours}h\n")
        print(f"{_status_glyph(elk['status'])} ELK")
        if "events_total" in elk:
            print(f"  events={elk['events_total']} failures={elk.get('events_failures', 0)}")
            for op, count in elk.get("top_failure_ops") or []:
                print(f"    └─ {op}: {count}")
        if elk.get("message"):
            print(f"  {elk['message']}")
        if elk.get("error"):
            print(f"  {elk['error']}")

        print(f"\n{_status_glyph(bugsink['status'])} Bugsink/Sentry")
        if bugsink.get("ui_url"):
            print(f"  triage: {bugsink['ui_url']}")
        if "total_issues" in bugsink:
            print(
                f"  issues total={bugsink['total_issues']} "
                f"unresolved={bugsink['unresolved']} "
                f"recent={len(bugsink.get('recent_unresolved') or [])}"
            )
            for issue in bugsink.get("recent_unresolved") or []:
                print(f"    └─ [{issue['events']}x] {issue['type']}: {issue['message']}")
        if bugsink.get("message"):
            print(f"  {bugsink['message']}")

    return 1 if "fail" in {elk["status"], bugsink["status"]} else 0


def cmd_logs(args: argparse.Namespace) -> int:
    total, entries = ElkClient().query_logs(
        hours=args.hours,
        limit=args.limit,
        topic=args.topic,
        source=args.source,
        failures_only=args.failures_only,
    )
    if args.json:
        _print_json({"total": total, "entries": [entry.__dict__ for entry in entries]})
    else:
        print(f"ELK matches={total if total is not None else '?'} showing={len(entries)}")
        for entry in entries:
            print(
                f"  [{entry.timestamp}] {entry.level:5s} "
                f"{entry.index:36s} {entry.operation}: {entry.message}"
            )
    return 0


def cmd_resolve_issue(args: argparse.Namespace) -> int:
    if args.backend == "bugsink":
        payload = BugsinkClient().resolve_issue(args.issue_id)
    elif args.backend == "sentry":
        payload = SentryApiClient().resolve_issue(args.issue_id)
    else:
        try:
            payload = BugsinkClient().resolve_issue(args.issue_id)
            if not payload.get("ok"):
                payload = SentryApiClient().resolve_issue(args.issue_id)
        except Exception:
            payload = SentryApiClient().resolve_issue(args.issue_id)

    if args.json:
        _print_json(payload)
    else:
        print("resolved" if payload.get("ok") else "not resolved")
        if not payload.get("ok"):
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reusable diagnostics for ELK + Bugsink/Sentry")
    parser.add_argument("--env-file", help="Load environment variables from a .env file")
    sub = parser.add_subparsers(dest="cmd", required=True)

    health = sub.add_parser("health", help="Check ELK and Bugsink health")
    health.add_argument("--hours", type=int, default=24)
    health.add_argument("--top", type=int, default=5)
    health.add_argument("--json", action="store_true")
    health.set_defaults(func=cmd_health)

    logs = sub.add_parser("logs", help="Query recent ELK events")
    logs.add_argument("--hours", type=int, default=24)
    logs.add_argument("--limit", type=int, default=50)
    logs.add_argument("--topic")
    logs.add_argument("--source")
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

    resolve = sub.add_parser("resolve-issue", help="Resolve a Bugsink/Sentry issue by id")
    resolve.add_argument("issue_id")
    resolve.add_argument("--backend", choices=["auto", "bugsink", "sentry"], default="auto")
    resolve.add_argument("--json", action="store_true")
    resolve.set_defaults(func=cmd_resolve_issue)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    load_dotenv(args.env_file)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
