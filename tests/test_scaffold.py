from __future__ import annotations

from pathlib import Path

import pytest

from ojiichan.scaffold import available_languages, render_files, scaffold

_TS_FILES = {
    "OBSERVABILITY.md",
    "src/lib/config.ts",
    "src/lib/sentry.ts",
    "src/lib/elk.ts",
    "src/handler-scaleway.ts",
}

_SWIFT_FILES = {
    "OBSERVABILITY.md",
    "Sources/Observability/Config.swift",
    "Sources/Observability/Sentry.swift",
    "Sources/Observability/Elk.swift",
}


def test_available_languages() -> None:
    assert set(available_languages()) == {"typescript", "rust", "swift"}


def test_render_typescript_contains_expected_files() -> None:
    files = render_files("typescript", backend="rustrak")
    assert set(files) == _TS_FILES
    sentry = files["src/lib/sentry.ts"]
    assert "initSentry" in sentry
    assert "captureException" in sentry
    assert "flushSentry" in sentry
    assert "/_doc" in files["src/lib/elk.ts"]
    assert "process.env" in files["src/lib/config.ts"]
    assert "eventTime.slice(0, 10)" in files["src/lib/elk.ts"]


def test_render_rust_contains_module() -> None:
    files = render_files("rust")
    assert set(files) == {"OBSERVABILITY.md", "src/observability.rs"}
    body = files["src/observability.rs"]
    assert "pub fn init_sentry" in body
    assert "pub fn log_event" in body
    assert "_doc" in body
    assert 'event_time.format("%Y-%m-%d")' in body


def test_render_swift_contains_expected_files() -> None:
    files = render_files("swift", backend="rustrak")
    assert set(files) == _SWIFT_FILES
    sentry = files["Sources/Observability/Sentry.swift"]
    assert "func initSentry" in sentry
    assert "func captureException" in sentry
    assert "func flushSentry" in sentry
    assert "_doc" in files["Sources/Observability/Elk.swift"]
    assert "formatter.formatOptions = [.withFullDate]" in files["Sources/Observability/Elk.swift"]
    assert "ProcessInfo.processInfo.environment" in files["Sources/Observability/Config.swift"]


def test_backend_token_substituted() -> None:
    files = render_files("typescript", backend="sentry")
    for content in files.values():
        assert "__OJIICHAN_BACKEND__" not in content
    assert "**sentry**" in files["OBSERVABILITY.md"]


def test_unknown_language_raises() -> None:
    with pytest.raises(ValueError, match="unsupported language"):
        render_files("go")


def test_unknown_backend_raises() -> None:
    with pytest.raises(ValueError, match="unsupported backend"):
        render_files("rust", backend="raygun")


def test_scaffold_writes_then_skips(tmp_path: Path) -> None:
    result = scaffold("typescript", dest=tmp_path)
    assert result.language == "typescript"
    assert result.backend == "rustrak"
    assert (tmp_path / "src/lib/sentry.ts").is_file()
    assert set(result.written) == _TS_FILES
    assert result.skipped == []

    again = scaffold("typescript", dest=tmp_path)
    assert again.written == []
    assert set(again.skipped) == _TS_FILES


def test_scaffold_swift_creates_nested_dirs(tmp_path: Path) -> None:
    result = scaffold("swift", dest=tmp_path)
    assert (tmp_path / "Sources/Observability/Sentry.swift").is_file()
    assert set(result.written) == _SWIFT_FILES
    assert result.skipped == []


def test_scaffold_force_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "src" / "observability.rs"
    scaffold("rust", dest=tmp_path)
    target.write_text("// edited", encoding="utf-8")

    scaffold("rust", dest=tmp_path, force=True)
    assert "init_sentry" in target.read_text(encoding="utf-8")
