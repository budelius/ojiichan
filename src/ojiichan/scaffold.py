"""Scaffold observability setup for non-Python services (TypeScript, Rust, Swift).

ojiichan standardizes the ELK + Rustrak/Sentry + Scaleway workflow across Python
services. Sibling services written in other languages can't import the Python
library, so this module emits ready-to-edit, env-driven equivalents that report
to the same backends: Sentry/Rustrak error reporting and ELK structured events.

Templates live in :mod:`ojiichan.scaffolds` as ``<language>/<path>.tmpl`` files
and are rendered by replacing the ``__OJIICHAN_BACKEND__`` token.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path

from .config import ISSUE_BACKENDS

#: Relative output paths emitted for each supported language, in write order.
LANGUAGE_FILES: dict[str, tuple[str, ...]] = {
    "typescript": (
        "OBSERVABILITY.md",
        "src/lib/config.ts",
        "src/lib/sentry.ts",
        "src/lib/elk.ts",
        "src/handler-scaleway.ts",
    ),
    "rust": (
        "OBSERVABILITY.md",
        "src/observability.rs",
    ),
    "swift": (
        "OBSERVABILITY.md",
        "Sources/Observability/Config.swift",
        "Sources/Observability/Sentry.swift",
        "Sources/Observability/Elk.swift",
    ),
}

_BACKEND_TOKEN = "__OJIICHAN_BACKEND__"
_DEFAULT_BACKEND = "rustrak"


def available_languages() -> tuple[str, ...]:
    """Return the languages ``scaffold`` can emit."""
    return tuple(LANGUAGE_FILES)


def _template_text(language: str, relpath: str) -> str:
    parts = [language, *relpath.split("/")]
    parts[-1] += ".tmpl"
    resource = files("ojiichan.scaffolds")
    for part in parts:
        resource = resource.joinpath(part)
    return resource.read_text(encoding="utf-8")


def render_files(language: str, *, backend: str = _DEFAULT_BACKEND) -> dict[str, str]:
    """Render every scaffold file for ``language`` into ``{relpath: content}``.

    ``backend`` (``rustrak`` / ``sentry`` / ``bugsink``) only customizes comment
    and documentation references; the emitted SDK wiring is identical because
    Rustrak is Sentry-compatible and ingests via ``SENTRY_DSN``.
    """
    if language not in LANGUAGE_FILES:
        choices = ", ".join(available_languages())
        raise ValueError(f"unsupported language {language!r}; choose from {choices}")
    if backend not in ISSUE_BACKENDS:
        choices = ", ".join(ISSUE_BACKENDS)
        raise ValueError(f"unsupported backend {backend!r}; choose from {choices}")
    return {
        relpath: _template_text(language, relpath).replace(_BACKEND_TOKEN, backend)
        for relpath in LANGUAGE_FILES[language]
    }


@dataclass
class ScaffoldResult:
    """Outcome of a :func:`scaffold` run."""

    language: str
    backend: str
    dest: str
    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def scaffold(
    language: str,
    *,
    backend: str = _DEFAULT_BACKEND,
    dest: str | Path = ".",
    force: bool = False,
) -> ScaffoldResult:
    """Write observability scaffold files for ``language`` under ``dest``.

    Existing files are left untouched and reported in ``skipped`` unless
    ``force`` is set. Parent directories are created as needed.
    """
    dest_path = Path(dest)
    result = ScaffoldResult(language=language, backend=backend, dest=str(dest_path))
    for relpath, content in render_files(language, backend=backend).items():
        target = dest_path / relpath
        if target.exists() and not force:
            result.skipped.append(relpath)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        result.written.append(relpath)
    return result
