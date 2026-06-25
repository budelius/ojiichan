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


def strip_comment_marker(line: str) -> str:
    """Strip a leading/trailing comment marker from one source line.

    Order matters: longer/more specific markers (Rust ``///``/``//!`` docs,
    ``/*`` blocks) are checked before their prefixes so the right one wins.
    Covers Python/shell (``#``), C/Rust/TS (``//``), and block/JSDoc (``/*``,
    ``*``) comment styles.
    """
    raw = line.strip()
    if raw.endswith("*/"):
        raw = raw[:-2].strip()
    for marker in ("//!", "///", "//", "#", "/*", "*"):
        # A bare ``## heading`` is markdown, not a ``#`` line comment — leave it
        # intact so doc-block section headings survive when a line carries no
        # comment marker (e.g. ``DiagnosticDoc.to_markdown`` output). A real
        # Python-comment heading is authored as ``# ## errors`` and still strips
        # to ``## errors`` here because its first two chars are ``# `` not ``##``.
        if marker == "#" and raw.startswith("##"):
            break
        if raw.startswith(marker):
            raw = raw[len(marker) :].strip()
            break
    return raw


def _parse_kv_tokens(raw: str) -> dict[str, Any]:
    """Parse whitespace-separated ``key=value`` tokens into typed values."""
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
    return values


def parse_annotation(text: str) -> DiagnosticHint:
    """Parse ``ojiichan: key=value`` comment text into a ``DiagnosticHint``.

    Supported booleans: ``true/false/1/0/yes/no``. Comma-separated ``tags`` are
    converted to a tuple. Unknown keys are ignored so comments can evolve safely.
    """
    raw = strip_comment_marker(text.strip())
    if raw.lower().startswith("ojiichan:"):
        raw = raw.split(":", 1)[1].strip()

    values = _parse_kv_tokens(raw)
    if "operation" not in values:
        raise ValueError("ojiichan annotation requires operation=<name>")
    allowed = {field.name for field in DiagnosticHint.__dataclass_fields__.values()}
    return DiagnosticHint(**{key: value for key, value in values.items() if key in allowed})


# --- Extended "doc" DSL: agent-first markdown comment blocks ------------------

_DOC_MARKER = "ojiichan:doc"
# Scalar keys (``key: value`` lines) understood in a doc block, beyond the
# key=value tokens carried on the marker line itself.
_DOC_SCALAR_KEYS = {
    "summary",
    "operation",
    "topic",
    "service",
    "issue_backend",
    "issue_query",
    "runbook",
    "notes",
}
# Markdown ``## section`` names captured as ordered lists on the doc.
_DOC_LIST_SECTIONS = {"errors", "checks"}


@dataclass(frozen=True)
class DiagnosticDoc:
    """Rich, agent-first diagnostics doc parsed from a markdown comment block.

    A superset of :class:`DiagnosticHint`: the same scalar fields plus a free
    ``summary``, a list of known ``errors`` (signature → next step), and an
    ordered list of ``checks`` (where an agent should look first). Render it back
    to markdown with :meth:`to_markdown` or downcast to a hint for log events
    with :meth:`to_hint`.
    """

    operation: str
    topic: str | None = None
    service: str | None = None
    failure: bool = False
    issue_backend: str | None = None
    issue_query: str | None = None
    runbook: str | None = None
    notes: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)
    summary: str | None = None
    errors: tuple[str, ...] = field(default_factory=tuple)
    checks: tuple[str, ...] = field(default_factory=tuple)

    def to_hint(self) -> DiagnosticHint:
        """Downcast to a :class:`DiagnosticHint` for ``ElkClient.log_event``."""
        return DiagnosticHint(
            operation=self.operation,
            topic=self.topic,
            service=self.service,
            failure=self.failure,
            issue_backend=self.issue_backend,
            issue_query=self.issue_query,
            runbook=self.runbook,
            notes=self.notes or self.summary,
            tags=self.tags,
        )

    def to_event_data(self) -> dict[str, Any]:
        """Return fields suitable for ``ElkClient.log_event(..., data=...)``."""
        return self.to_hint().to_event_data()

    def to_markdown(self) -> str:
        """Render the doc as agent-facing markdown.

        Mirrors the authored block so the rendered output round-trips back
        through :func:`parse_doc`.
        """
        tokens = [f"operation={self.operation}"]
        if self.topic:
            tokens.append(f"topic={self.topic}")
        if self.failure:
            tokens.append("failure=true")
        if self.issue_backend:
            tokens.append(f"issue_backend={self.issue_backend}")
        if self.tags:
            tokens.append("tags=" + ",".join(self.tags))
        lines = [f"{_DOC_MARKER} " + " ".join(tokens)]
        if self.summary:
            lines.append(f"summary: {self.summary}")
        for scalar in ("service", "issue_query", "runbook", "notes"):
            value = getattr(self, scalar)
            if value:
                lines.append(f"{scalar}: {value}")
        if self.errors:
            lines.append("")
            lines.append("## errors")
            lines.extend(f"- {item}" for item in self.errors)
        if self.checks:
            lines.append("")
            lines.append("## checks")
            lines.extend(f"{i}. {item}" for i, item in enumerate(self.checks, 1))
        return "\n".join(lines)


def is_doc_annotation(text: str) -> bool:
    """Return ``True`` if ``text`` looks like an ``ojiichan:doc`` block."""
    for line in text.splitlines():
        stripped = strip_comment_marker(line)
        if stripped:
            return stripped.lower().startswith(_DOC_MARKER)
    return False


def parse_doc(text: str) -> DiagnosticDoc:
    """Parse an ``ojiichan:doc`` markdown comment block into a :class:`DiagnosticDoc`.

    The first content line is ``ojiichan:doc key=value ...`` (carrying the same
    scalar hints as the compact form). Subsequent lines are either ``key: value``
    scalar fields or markdown ``## errors`` / ``## checks`` sections whose
    ``-``/``*``/``1.`` items become ordered lists. Comment markers (``#``,
    ``//``, ``///``, ``//!``, ``/* */``, ``*``) are stripped per line, so the
    same block works across Python, TypeScript, Rust, and Swift source.
    """
    values: dict[str, Any] = {}
    errors: list[str] = []
    checks: list[str] = []
    section: str | None = None
    seen_marker = False

    for line in text.splitlines():
        stripped = strip_comment_marker(line)
        if not stripped:
            continue

        if not seen_marker:
            if not stripped.lower().startswith(_DOC_MARKER):
                continue
            seen_marker = True
            values.update(_parse_kv_tokens(stripped[len(_DOC_MARKER) :].strip()))
            continue

        if stripped.startswith("##"):
            section = stripped.lstrip("#").strip().lower()
            continue

        if stripped[:1] in {"-", "*"} or (stripped[:1].isdigit() and "." in stripped[:4]):
            item = stripped.lstrip("-*").strip()
            if stripped[:1].isdigit():
                item = stripped.split(".", 1)[1].strip()
            if section == "errors":
                errors.append(item)
            elif section == "checks":
                checks.append(item)
            continue

        if ":" in stripped:
            key, value = stripped.split(":", 1)
            key = key.strip().lower()
            if key in _DOC_SCALAR_KEYS:
                values[key] = value.strip()
            elif key == "tags":
                values[key] = tuple(part.strip() for part in value.split(",") if part.strip())
            elif key == "failure":
                values[key] = value.strip().lower() in {"1", "true", "yes", "on"}
            section = None

    if not seen_marker:
        raise ValueError(f"not an {_DOC_MARKER} block")
    if "operation" not in values:
        raise ValueError(f"{_DOC_MARKER} block requires operation=<name>")

    allowed = {f.name for f in DiagnosticDoc.__dataclass_fields__.values()}
    kwargs = {key: value for key, value in values.items() if key in allowed}
    return DiagnosticDoc(errors=tuple(errors), checks=tuple(checks), **kwargs)


def parse_any(text: str) -> DiagnosticHint | DiagnosticDoc:
    """Parse either DSL form: a doc block if present, else the compact line."""
    return parse_doc(text) if is_doc_annotation(text) else parse_annotation(text)
