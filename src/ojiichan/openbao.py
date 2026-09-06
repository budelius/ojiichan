"""OpenBao/Vault seal-state health check.

A sealed OpenBao member is a silent failure. The pod stays ``1/1 Ready`` —
its readiness probe answers on a sealed node — and as long as one other
member holds the lease the cluster keeps serving, so nothing alerts. What
actually degrades is secret syncing: ExternalSecrets refreshing through the
sealed member start failing, and the workloads that depend on them drift
until someone happens to need a value.

This was found on prod2 on 2026-09-06 only because an operator command
returned ``Vault is sealed`` while doing something unrelated.

``/v1/sys/health`` is deliberately used rather than ``/v1/sys/seal-status``:
it needs no token, so this check can run anywhere the address is reachable,
and OpenBao encodes the whole state in the HTTP status code.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from typing import Any

import httpx

from .config import env

# OpenBao/Vault sys/health status codes. The unusual ones are load-balancer
# affordances: standby members answer 429 so a naive LB drops them from
# rotation without treating them as down.
_HEALTH_CODES: dict[int, tuple[str, str]] = {
    200: ("ok", "unsealed, active"),
    429: ("ok", "unsealed, standby"),
    472: ("ok", "unsealed, DR replication secondary"),
    473: ("ok", "unsealed, performance standby"),
    501: ("fail", "NOT INITIALIZED"),
    503: ("fail", "SEALED"),
}


def _node_addresses() -> list[str]:
    """Return the OpenBao addresses to probe.

    ``OJIICHAN_OPENBAO_ADDRS`` is a comma-separated list, because the whole
    point is per-MEMBER state: a single load-balanced address hides exactly
    the condition being looked for — one sealed node behind two healthy ones
    answers 200 and looks fine.
    """
    raw = env("OPENBAO_ADDRS") or env("OPENBAO_ADDR") or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


def _kubectl_members(namespace: str, context: str | None) -> list[str]:
    """Return OpenBao pod names, or [] if kubectl cannot answer.

    Discovery rather than a configured list: pod IPs churn on every restart,
    and a hand-maintained address list is a monitor that rots silently into
    checking members that no longer exist.
    """
    if not shutil.which("kubectl"):
        return []
    cmd = [
        "kubectl",
        "get",
        "pods",
        "-n",
        namespace,
        "-l",
        "app.kubernetes.io/name=openbao",
        "-o",
        "jsonpath={.items[*].metadata.name}",
    ]
    if context:
        cmd += ["--context", context]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if out.returncode != 0:
        return []
    return sorted(out.stdout.split())


def _kubectl_seal_state(pod: str, namespace: str, context: str | None) -> dict[str, Any]:
    """Read one member's seal state with ``bao status``.

    ``bao status`` is preferred over the HTTP health endpoint here because it
    answers in JSON with an explicit ``sealed`` boolean. Reached through the
    apiserver the same state arrives as an HTTP status code that kubectl
    rewrites into its own error text — 429 "unsealed, standby" surfaces as
    ``TooManyRequests``, which reads like a failure and is not one.
    """
    cmd = [
        "kubectl",
        "exec",
        "-n",
        namespace,
        pod,
        "--",
        "sh",
        "-c",
        "BAO_ADDR=https://127.0.0.1:8200 BAO_SKIP_VERIFY=true bao status -format=json",
    ]
    if context:
        cmd += ["--context", context]
    node: dict[str, Any] = {"address": f"{namespace}/{pod}"}
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            **node,
            "status": "unknown",
            "state": "unreachable",
            "error": f"{type(exc).__name__}: {exc}",
        }
    try:
        # `bao status` exits non-zero when sealed, so parse the body first and
        # let the returncode matter only when there is nothing to read.
        payload = json.loads(out.stdout)
    except (ValueError, TypeError):
        detail = (out.stderr or out.stdout or "").strip().splitlines()
        return {
            **node,
            "status": "unknown",
            "state": "unreachable",
            "error": detail[-1] if detail else f"exit {out.returncode}, no output",
        }
    if not payload.get("initialized", False):
        return {**node, "status": "fail", "state": "NOT INITIALIZED"}
    if payload.get("sealed", True):
        return {**node, "status": "fail", "state": "SEALED", "version": payload.get("version")}
    # Active vs standby is deliberately not distinguished: both serve, and
    # which member holds the lease is not the condition being monitored.
    return {**node, "status": "ok", "state": "unsealed", "version": payload.get("version")}


def check_openbao(
    *,
    timeout: float = 5.0,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Report the seal state of every OpenBao member.

    Two transports. If ``OJIICHAN_OPENBAO_ADDRS`` is set the members are
    probed over HTTP — the right shape for running inside the cluster.
    Otherwise the members are discovered with kubectl and read with ``bao
    status``, which is what makes this usable from an operator's machine
    without maintaining a list of pod IPs that change on every restart.

    Status is ``ok`` only when every member was reached AND none is sealed.
    A member that could not be probed yields ``unknown`` — never ``ok`` —
    because "I could not look" and "I looked and it was fine" are different
    answers, and collapsing them is how a monitor comes to report green
    through an outage.
    """
    addresses = _node_addresses()
    if not addresses:
        return _check_via_kubectl()

    own_client = client is None
    http = client or httpx.Client(timeout=max(timeout, 0.1), verify=False)
    nodes: list[dict[str, Any]] = []
    try:
        for address in addresses:
            url = f"{address.rstrip('/')}/v1/sys/health"
            started = time.monotonic()
            try:
                response = http.get(url)
            except Exception as exc:  # noqa: BLE001 - any transport failure is "unknown"
                nodes.append(
                    {
                        "address": address,
                        "status": "unknown",
                        "state": "unreachable",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            status, state = _HEALTH_CODES.get(
                response.status_code, ("unknown", f"unrecognised HTTP {response.status_code}")
            )
            nodes.append(
                {
                    "address": address,
                    "status": status,
                    "state": state,
                    "http_status": response.status_code,
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                }
            )
    finally:
        if own_client:
            http.close()

    return _summarise(nodes)


def _check_via_kubectl() -> dict[str, Any]:
    """Discover the members with kubectl and read each one's seal state."""
    namespace = env("OPENBAO_NAMESPACE") or "openbao"
    context = env("KUBE_CONTEXT")
    pods = _kubectl_members(namespace, context)
    if not pods:
        return {
            "status": "skip",
            "message": (
                f"no OpenBao pods found via kubectl in namespace {namespace!r} — "
                "set OJIICHAN_OPENBAO_ADDRS to probe members over HTTP instead, "
                "or OJIICHAN_OPENBAO_NAMESPACE / OJIICHAN_KUBE_CONTEXT to point "
                "kubectl at the right cluster"
            ),
            "nodes": [],
        }
    nodes = [_kubectl_seal_state(pod, namespace, context) for pod in pods]
    return _summarise(nodes)


def _summarise(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll per-member states up into one verdict."""
    # Key off node status, not the state string: 501 NOT INITIALIZED is just as
    # broken as 503 SEALED, and matching on "SEALED" alone reported it as ok.
    broken = [n for n in nodes if n["status"] == "fail"]
    sealed = [n for n in nodes if n.get("state") == "SEALED"]
    unknown = [n for n in nodes if n["status"] == "unknown"]

    if broken:
        status = "fail"
        detail = ", ".join(f"{n['address']} {n['state']}" for n in broken)
        message = (
            f"{len(broken)} of {len(nodes)} OpenBao members unusable ({detail}) — "
            "secret syncing is degraded even though the pods report Ready; "
            "unseal or initialise them"
        )
    elif unknown:
        status = "unknown"
        message = (
            f"{len(unknown)} of {len(nodes)} OpenBao members could not be probed "
            f"({', '.join(n['address'] for n in unknown)}) — seal state is unknown, "
            "not healthy"
        )
    else:
        status = "ok"
        message = f"all {len(nodes)} OpenBao members unsealed"

    return {
        "status": status,
        "message": message,
        "sealed": len(sealed),
        "unknown": len(unknown),
        "total": len(nodes),
        "nodes": nodes,
    }
