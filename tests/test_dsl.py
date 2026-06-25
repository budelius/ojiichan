from __future__ import annotations

import pytest

from ojiichan.dsl import (
    DiagnosticDoc,
    DiagnosticHint,
    is_doc_annotation,
    parse_annotation,
    parse_any,
    parse_doc,
)


def test_python_hash_comment() -> None:
    h = parse_annotation("# ojiichan: operation=login topic=auth failure=true tags=a,b")
    assert h.operation == "login"
    assert h.topic == "auth"
    assert h.failure is True
    assert h.tags == ("a", "b")


def test_typescript_slash_comment() -> None:
    h = parse_annotation("// ojiichan: operation=fetchUser topic=users")
    assert h.operation == "fetchUser"
    assert h.topic == "users"


def test_rust_doc_comments() -> None:
    assert parse_annotation("/// ojiichan: operation=spawn").operation == "spawn"
    assert parse_annotation("//! ojiichan: operation=boot").operation == "boot"


def test_block_comment() -> None:
    h = parse_annotation("/* ojiichan: operation=charge failure=true */")
    assert h.operation == "charge"
    assert h.failure is True


def test_jsdoc_star_line() -> None:
    assert parse_annotation(" * ojiichan: operation=render").operation == "render"


def test_without_comment_marker() -> None:
    assert parse_annotation("ojiichan: operation=plain").operation == "plain"


def test_requires_operation() -> None:
    with pytest.raises(ValueError):
        parse_annotation("// ojiichan: topic=auth")


# --- Extended "doc" DSL -------------------------------------------------------

DOC_BLOCK = """\
# ojiichan:doc operation=matrix_exchange topic=auth failure=true issue_backend=rustrak tags=m,auth
# summary: Exchange a Matrix login token for an access token.
# ## errors
# - 401 invalid_token -> token expired; re-run exchange
# - timeout -> homeserver slow; check Scaleway logs
# ## checks
# 1. ELK: topic=auth operation=matrix_exchange failure=true
# 2. Rustrak issues tagged matrix,auth
# runbook: docs/runbooks/matrix-auth.md"""


def test_parse_doc_scalars_and_lists() -> None:
    doc = parse_doc(DOC_BLOCK)
    assert isinstance(doc, DiagnosticDoc)
    assert doc.operation == "matrix_exchange"
    assert doc.topic == "auth"
    assert doc.failure is True
    assert doc.issue_backend == "rustrak"
    assert doc.tags == ("m", "auth")
    assert doc.summary.startswith("Exchange a Matrix")
    assert doc.runbook == "docs/runbooks/matrix-auth.md"
    assert len(doc.errors) == 2
    assert doc.errors[0].startswith("401 invalid_token")
    assert doc.checks == (
        "ELK: topic=auth operation=matrix_exchange failure=true",
        "Rustrak issues tagged matrix,auth",
    )


def test_doc_markdown_round_trips() -> None:
    doc = parse_doc(DOC_BLOCK)
    assert parse_doc(doc.to_markdown()) == doc


def test_doc_to_hint_downcast() -> None:
    hint = parse_doc(DOC_BLOCK).to_hint()
    assert isinstance(hint, DiagnosticHint)
    assert hint.operation == "matrix_exchange"
    data = hint.to_event_data()
    assert data["topic"] == "auth"
    assert data["failure"] is True


def test_doc_works_with_slash_comments() -> None:
    block = "// ojiichan:doc operation=login\n// ## checks\n// 1. check ELK topic=auth"
    doc = parse_doc(block)
    assert doc.operation == "login"
    assert doc.checks == ("check ELK topic=auth",)


def test_is_doc_annotation_detection() -> None:
    assert is_doc_annotation(DOC_BLOCK) is True
    assert is_doc_annotation("# ojiichan: operation=login") is False


def test_parse_any_dispatches_by_form() -> None:
    assert isinstance(parse_any(DOC_BLOCK), DiagnosticDoc)
    assert isinstance(parse_any("# ojiichan: operation=login"), DiagnosticHint)


def test_doc_requires_operation() -> None:
    with pytest.raises(ValueError):
        parse_doc("# ojiichan:doc topic=auth\n# summary: no operation here")
