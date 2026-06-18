from __future__ import annotations

import pytest

from ojiichan.dsl import parse_annotation


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
