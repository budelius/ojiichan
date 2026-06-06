"""Ojiichan diagnostics toolkit."""

from .bugsink import BugsinkClient, BugsinkIssue, check_bugsink
from .dsl import DiagnosticHint, diagnostic, get_diagnostic, hint, parse_annotation
from .elk import ElkClient, check_elk, query_elk_logs
from .rustrak import RustrakClient, RustrakIssue, check_rustrak
from .scaleway import ScalewayLogEntry, ScalewayLogsClient, check_scaleway, query_scaleway_logs
from .sentry import SentryApiClient, capture_exception, init_sentry

__all__ = [
    "BugsinkClient",
    "BugsinkIssue",
    "DiagnosticHint",
    "ElkClient",
    "RustrakClient",
    "RustrakIssue",
    "ScalewayLogEntry",
    "ScalewayLogsClient",
    "SentryApiClient",
    "capture_exception",
    "check_bugsink",
    "check_elk",
    "check_rustrak",
    "check_scaleway",
    "diagnostic",
    "get_diagnostic",
    "hint",
    "init_sentry",
    "parse_annotation",
    "query_elk_logs",
    "query_scaleway_logs",
]
