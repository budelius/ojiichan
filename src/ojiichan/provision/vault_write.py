"""Vault credential write-path for provisioning.

:mod:`ojiichan.credentials` is the *read* side — it resolves secrets from
Vaultwarden via Locke. Provisioning needs the inverse: after creating a backend
resource it stores the resulting credential at the **same vault path the read
side will later resolve**, so a freshly-provisioned project is immediately usable
by ``ojiichan`` and by the scaffolded sibling services.

Writes go through Locke's ``VaultClient.set_secret`` (create-or-update). The
optional ``vault`` extra must be installed; without it these helpers report
``"unavailable"`` rather than raising, so provisioning still completes the
backend work and tells the operator the creds need to be stored manually.
"""

from __future__ import annotations

import logging
from typing import Any

from ..credentials import SECRET_MAP, vault_folder

logger = logging.getLogger(__name__)


def vault_available() -> bool:
    """Return ``True`` when Locke (the ``vault`` extra) is importable."""
    try:
        import locke.vault  # noqa: F401
    except ImportError:
        return False
    return True


def connect() -> Any | None:
    """Connect a Locke ``VaultClient``, or ``None`` when unavailable.

    Best-effort: a missing ``locke`` install or a failed connection both yield
    ``None`` so callers degrade gracefully instead of crashing the provision.
    """
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


def secret_path(env_var: str) -> str:
    """Map an env-var name to its full vault path.

    Known env vars resolve through :data:`ojiichan.credentials.SECRET_MAP`
    (e.g. ``ELASTICSEARCH_PASSWORD`` -> ``<folder>/elk/password``) so writes land
    exactly where the read-path looks. Unknown names fall back to
    ``<folder>/<env_var>`` so callers can store project-specific extras.
    """
    name = SECRET_MAP.get(env_var, env_var)
    return f"{vault_folder()}/{name}"


def store_secret(
    env_var: str,
    value: str,
    *,
    client: Any | None = None,
    username: str | None = None,
    uri: str | None = None,
    notes: str | None = None,
) -> str:
    """Write one credential to its vault path.

    Returns ``"created"`` / ``"updated"`` (from Locke), or ``"unavailable"`` when
    no vault client could be obtained.
    """
    cli = client or connect()
    if cli is None:
        return "unavailable"
    return cli.set_secret(
        secret_path(env_var), value, username=username, uri=uri, notes=notes
    )


def store_secrets(
    mapping: dict[str, str | None], *, client: Any | None = None
) -> dict[str, str]:
    """Bulk-write a ``{env_var: value}`` mapping via one shared client.

    ``None`` values are reported as ``"skipped"`` (nothing to store). When vault
    is unavailable every entry reports ``"unavailable"``.
    """
    cli = client or connect()
    if cli is None:
        return {env_var: "unavailable" for env_var in mapping}
    out: dict[str, str] = {}
    for env_var, value in mapping.items():
        if value is None or value == "":
            out[env_var] = "skipped"
            continue
        try:
            out[env_var] = store_secret(env_var, value, client=cli)
        except Exception as exc:  # noqa: BLE001 - record per-secret failure, keep going.
            logger.debug("failed to store %s: %s", env_var, exc)
            out[env_var] = "failed"
    return out
