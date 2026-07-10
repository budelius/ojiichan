"""Vault credential write-path for provisioning.

:mod:`ojiichan.credentials` is the *read* side — it resolves secrets from
Vaultwarden via Locke when configured. Provisioning needs the inverse: after
creating a backend resource it stores the resulting credential at the **same
vault path the read side will later resolve**, so a freshly-provisioned project
is immediately usable by ``ojiichan`` and by the scaffolded sibling services.

Writes can go through Locke's ``VaultClient.set_secret`` and/or OpenBao's KV v2
HTTP API. Missing or failing vault backends report ``"unavailable"`` / ``"failed"``
rather than raising, so provisioning still completes the backend work and tells
the operator the creds need to be stored manually.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from ..credentials import SECRET_MAP, vault_folder, vault_project_folder

logger = logging.getLogger(__name__)

OPENBAO_ADDR_ENV = "OPENBAO_ADDR"
OPENBAO_TOKEN_ENV = "OPENBAO_TOKEN"
OPENBAO_KV_MOUNT_ENV = "OPENBAO_KV_MOUNT"
VAULT_ADDR_ENV = "VAULT_ADDR"
VAULT_TOKEN_ENV = "VAULT_TOKEN"
VAULT_WRITE_TARGETS_ENV = "OJIICHAN_VAULT_WRITE_TARGETS"


class OpenBaoWriter:
    """Small OpenBao/Vault KV v2 writer matching Locke's ``set_secret`` shape."""

    def __init__(self, *, addr: str, token: str, mount: str = "secret") -> None:
        self.addr = addr.rstrip("/")
        self.token = token
        self.mount = mount.strip("/")

    def set_secret(
        self,
        path: str,
        value: str,
        *,
        username: str | None = None,
        uri: str | None = None,
        notes: str | None = None,
    ) -> str:
        data: dict[str, str] = {"value": value}
        if username:
            data["username"] = username
        if uri:
            data["uri"] = uri
        if notes:
            data["notes"] = notes
        response = httpx.post(
            f"{self.addr}/v1/{self.mount}/data/{path.strip('/')}",
            json={"data": data},
            headers={"X-Vault-Token": self.token},
            timeout=10,
        )
        response.raise_for_status()
        return "updated"


def vault_available() -> bool:
    """Return ``True`` when at least one vault write backend is available."""
    return connect() is not None or connect_openbao() is not None


def connect() -> Any | None:
    """Connect a Locke ``VaultClient``, or ``None`` when unavailable."""
    try:
        from locke.vault import VaultClient
    except ImportError:
        logger.debug("locke not installed; vault writes disabled")
        return None
    try:
        return VaultClient.connect()
    except Exception as exc:  # noqa: BLE001 - credential backends are best-effort.
        logger.debug("failed to connect to Locke/Vaultwarden: %s", exc)
        return None


def connect_openbao() -> OpenBaoWriter | None:
    """Connect an OpenBao/Vault KV v2 writer from env, if configured.

    Accepts either OpenBao-specific env vars or Vault-compatible aliases:
    ``OPENBAO_ADDR`` / ``OPENBAO_TOKEN`` or ``VAULT_ADDR`` / ``VAULT_TOKEN``.
    ``OPENBAO_KV_MOUNT`` defaults to ``secret``.
    """
    addr = os.getenv(OPENBAO_ADDR_ENV) or os.getenv(VAULT_ADDR_ENV)
    token = os.getenv(OPENBAO_TOKEN_ENV) or os.getenv(VAULT_TOKEN_ENV)
    if not addr or not token:
        return None
    return OpenBaoWriter(addr=addr, token=token, mount=os.getenv(OPENBAO_KV_MOUNT_ENV, "secret"))


def _write_targets() -> set[str]:
    raw = os.getenv(VAULT_WRITE_TARGETS_ENV)
    if raw:
        return {part.strip().lower() for part in raw.split(",") if part.strip()}
    # Backward compatible default: use Locke; if OpenBao env is present, write there too.
    targets = {"locke"}
    if os.getenv(OPENBAO_ADDR_ENV) or os.getenv(VAULT_ADDR_ENV):
        targets.add("openbao")
    return targets


def _clients(explicit: Any | None = None) -> list[Any]:
    if explicit is not None:
        return [explicit]
    clients: list[Any] = []
    targets = _write_targets()
    if "locke" in targets:
        locke = connect()
        if locke is not None:
            clients.append(locke)
    if "openbao" in targets or "vault" in targets:
        openbao = connect_openbao()
        if openbao is not None:
            clients.append(openbao)
    return clients


def secret_path(env_var: str, *, project: str | None = None) -> str:
    """Map an env-var name to its full vault path.

    Known env vars resolve through :data:`ojiichan.credentials.SECRET_MAP`.
    When ``project`` is supplied, credentials are written below
    ``<folder>/projects/<project>/`` so provisioning another service cannot
    overwrite this service's writer creds. Unknown names fall back to the raw
    env-var name so callers can store project-specific extras.
    """
    name = SECRET_MAP.get(env_var, env_var)
    folder = vault_project_folder(project) if project else None
    return f"{folder or vault_folder()}/{name}"


def _combine_actions(actions: list[str]) -> str:
    if not actions:
        return "unavailable"
    if "created" in actions:
        return "created"
    if "updated" in actions:
        return "updated"
    if all(action == "unavailable" for action in actions):
        return "unavailable"
    return "failed"


def store_secret(
    env_var: str,
    value: str,
    *,
    client: Any | None = None,
    username: str | None = None,
    uri: str | None = None,
    notes: str | None = None,
    project: str | None = None,
) -> str:
    """Write one credential to configured vault write backends."""
    clients = _clients(client)
    if not clients:
        return "unavailable"

    path = secret_path(env_var, project=project)
    actions: list[str] = []
    for cli in clients:
        try:
            actions.append(cli.set_secret(path, value, username=username, uri=uri, notes=notes))
        except Exception as exc:  # noqa: BLE001 - record backend failure, keep going.
            logger.debug("failed to store %s at %s: %s", env_var, path, exc)
            actions.append("failed")
    return _combine_actions(actions)


def store_secrets(
    mapping: dict[str, str | None], *, client: Any | None = None, project: str | None = None
) -> dict[str, str]:
    """Bulk-write a ``{env_var: value}`` mapping via configured vault backends.

    ``None`` values are reported as ``"skipped"`` (nothing to store). When no
    vault backend is available every entry reports ``"unavailable"``.
    """
    clients = _clients(client)
    if not clients:
        return {env_var: "unavailable" for env_var in mapping}
    out: dict[str, str] = {}
    for env_var, value in mapping.items():
        if value is None or value == "":
            out[env_var] = "skipped"
            continue
        path = secret_path(env_var, project=project)
        actions: list[str] = []
        for cli in clients:
            try:
                actions.append(cli.set_secret(path, value, username=None, uri=None, notes=None))
            except Exception as exc:  # noqa: BLE001 - record per-secret failure, keep going.
                logger.debug("failed to store %s at %s: %s", env_var, path, exc)
                actions.append("failed")
        out[env_var] = _combine_actions(actions)
    return out
