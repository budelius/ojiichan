from __future__ import annotations

from ojiichan import ElkClient
from ojiichan_mcp.server import mcp


def test_ojiichan_import() -> None:
    assert ElkClient.__name__ == "ElkClient"


def test_mcp_server_exposes_expected_tools() -> None:
    names = {tool.name for tool in mcp._tool_manager.list_tools()}

    assert {
        "elk_health",
        "elk_logs",
        "emit_elk_event",
        "bugsink_health",
        "bugsink_issues",
        "create_bead_from_issue",
        "xcode_build",
        "xcode_test",
    }.issubset(names)
