"""Tiny diagnostics DSL for code annotations and agent hints.

The DSL is intentionally plain data: projects can attach hints to functions via
``@diagnostic`` or leave nearby comments that coding agents can read, e.g.::

    # ojiichan: topic=auth operation=login failure=true issue_backend=bugsink

Agents can use these hints to know which ELK topic/operation and which issue
backend to inspect when debugging a code path.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True)
class DiagnosticHint:
    """Machine-readable hint for logs, issues, and remediation paths."""

    operation: str
    topic: str | None = None
    service: str | None = None
    failure: bool = False
    issue_backend: str | None = None
    issue_query: str | None = None
    runbook: str | None = None
    notes: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)

    def to_event_data(self) -> dict[str, Any]:
        """Return fields suitable for ``ElkClient.log_event(..., data=...)``."""
        return {k: v for k, v in asdict(self).items() if v not in (None, (), [])}

    def annotation(self) -> str:
        """Render a compact comment form: ``ojiichan: key=value ...``."""
        parts = []
        for key, value in self.to_event_data().items():
            if isinstance(value, tuple):
                value = ",".join(value)
            parts.append(f"{key}={value}")
        return "ojiichan: " + " ".join(parts)


def hint(
    operation: str,
    *,
    topic: str | None = None,
    service: str | None = None,
    failure: bool = False,
    issue_backend: str | None = None,
    issue_query: str | None = None,
    runbook: str | None = None,
    notes: str | None = None,
    tags: list[str] | tuple[str, ...] = (),
) -> DiagnosticHint:
    """Build a diagnostic hint for code or log-event annotations."""
    return DiagnosticHint(
        operation=operation,
        topic=topic,
        service=service,
        failure=failure,
        issue_backend=issue_backend,
        issue_query=issue_query,
        runbook=runbook,
        notes=notes,
        tags=tuple(tags),
    )


def diagnostic(diag: DiagnosticHint | None = None, **kwargs: Any) -> Callable[[F], F]:
    """Attach a ``DiagnosticHint`` to a function for agents/tools to inspect."""
    resolved = diag or hint(**kwargs)

    def decorate(func: F) -> F:
        func.__ojiichan_diagnostic__ = resolved  # type: ignore[attr-defined]
        return func

    return decorate


def get_diagnostic(obj: Any) -> DiagnosticHint | None:
    """Return a hint attached by ``@diagnostic``, if present."""
    value = getattr(obj, "__ojiichan_diagnostic__", None)
    return value if isinstance(value, DiagnosticHint) else None


def parse_annotation(text: str) -> DiagnosticHint:
    """Parse ``ojiichan: key=value`` comment text into a ``DiagnosticHint``.

    Supported booleans: ``true/false/1/0/yes/no``. Comma-separated ``tags`` are
    converted to a tuple. Unknown keys are ignored so comments can evolve safely.
    """
    raw = text.strip()
    if raw.startswith("#"):
        raw = raw[1:].strip()
    if raw.lower().startswith("ojiichan:"):
        raw = raw.split(":", 1)[1].strip()

    values: dict[str, Any] = {}
    for token in raw.split():
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        if key == "failure":
            values[key] = value.lower() in {"1", "true", "yes", "on"}
        elif key == "tags":
            values[key] = tuple(part for part in value.split(",") if part)
        else:
            values[key] = value

    if "operation" not in values:
        raise ValueError("ojiichan annotation requires operation=<name>")
    allowed = {field.name for field in DiagnosticHint.__dataclass_fields__.values()}
    return DiagnosticHint(**{key: value for key, value in values.items() if key in allowed})
