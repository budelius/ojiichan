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


def check_openbao(
    *,
    timeout: float = 5.0,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Report the seal state of every configured OpenBao member.

    Status is ``ok`` only when every member was reached AND none is sealed.
    A member that could not be probed yields ``unknown`` — never ``ok`` —
    because "I could not look" and "I looked and it was fine" are different
    answers, and collapsing them is how a monitor comes to report green
    through an outage.
    """
    addresses = _node_addresses()
    if not addresses:
        return {
            "status": "skip",
            "message": (
                "set OJIICHAN_OPENBAO_ADDRS to a comma-separated list of member "
                "addresses (one per pod, not the Service) to check seal state"
            ),
            "nodes": [],
        }

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
