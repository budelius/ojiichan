"""Ojiichan diagnostics toolkit."""

from .bugsink import BugsinkClient, BugsinkIssue, check_bugsink
from .dsl import (
    DiagnosticDoc,
    DiagnosticHint,
    diagnostic,
    get_diagnostic,
    hint,
    is_doc_annotation,
    parse_annotation,
    parse_any,
    parse_doc,
)
from .elk import ElkClient, check_elk, query_elk_logs
from .provision import (
    ElkProvisionConfig,
    ElkProvisioner,
    OtelProvisionConfig,
    OtelProvisioner,
    ProvisionResult,
    RustrakProvisionConfig,
    RustrakProvisioner,
)
from .rustrak import RustrakClient, RustrakIssue, check_rustrak
from .scaffold import ScaffoldResult, available_languages, render_files, scaffold
from .scaleway import ScalewayLogEntry, ScalewayLogsClient, check_scaleway, query_scaleway_logs
from .sentry import SentryApiClient, capture_exception, init_sentry

__all__ = [
    "BugsinkClient",
    "BugsinkIssue",
    "DiagnosticDoc",
    "DiagnosticHint",
    "ElkClient",
    "ElkProvisionConfig",
    "ElkProvisioner",
    "OtelProvisionConfig",
    "OtelProvisioner",
    "ProvisionResult",
    "RustrakClient",
    "RustrakIssue",
    "RustrakProvisionConfig",
    "RustrakProvisioner",
    "ScaffoldResult",
    "ScalewayLogEntry",
    "ScalewayLogsClient",
    "SentryApiClient",
    "available_languages",
    "capture_exception",
    "check_bugsink",
    "check_elk",
    "check_rustrak",
    "check_scaleway",
    "diagnostic",
    "get_diagnostic",
    "hint",
    "init_sentry",
    "is_doc_annotation",
    "parse_annotation",
    "parse_any",
    "parse_doc",
    "query_elk_logs",
    "query_scaleway_logs",
    "render_files",
    "scaffold",
]
