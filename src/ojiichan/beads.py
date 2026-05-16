"""Optional Beads issue-tracker handoff helpers.

This intentionally shells out to `bead`/`beads` when present instead of taking a
hard dependency on a Python API, so ojiichan remains reusable in projects that do
not use Beads.
"""

from __future__ import annotations

import shutil
import subprocess


def beads_available() -> bool:
    return bool(shutil.which("bead") or shutil.which("beads"))


def create_bead(title: str, body: str, *, labels: list[str] | None = None) -> bool:
    exe = shutil.which("bead") or shutil.which("beads")
    if not exe:
        return False
    cmd = [exe, "create", title, "--body", body]
    for label in labels or []:
        cmd.extend(["--label", label])
    try:
        subprocess.run(cmd, check=True)
        return True
    except Exception:
        return False
