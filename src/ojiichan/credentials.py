"""Credential resolution for Ojiichan.

Environment variables are still the explicit override, but secret values can be
resolved from Vaultwarden via Locke when the optional ``vault`` extra is
installed.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

VAULT_FOLDER_ENV = "OJIICHAN_VAULT_FOLDER"
DEFAULT_VAULT_FOLDER = "ojiichan"

# Env var name -> vault secret name. The full vault path is
# ``{OJIICHAN_VAULT_FOLDER or ojiichan}/{secret_name}``.
SECRET_MAP: dict[str, str] = {
    "ELASTICSEARCH_HOST": "elasticsearch-host",
    "ELASTICSEARCH_USERNAME": "elasticsearch-username",
    "ELASTICSEARCH_PASSWORD": "elasticsearch-password",
    "ELASTICSEARCH_INDEX_PREFIX": "elasticsearch-index-prefix",
    "SENTRY_DSN": "sentry-dsn",
    "BUGSINK_API_TOKEN": "bugsink-api-token",
    "BUGSINK_HOST": "bugsink-host",
    "BUGSINK_PROJECT_ID": "bugsink-project-id",
    "SENTRY_AUTH_TOKEN": "sentry-auth-token",
    "SENTRY_BASE_URL": "sentry-base-url",
}

_cache: dict[str, str | None] = {}


def vault_folder() -> str:
    """Return the configured Locke/Vaultwarden folder prefix."""
    return os.getenv(VAULT_FOLDER_ENV, DEFAULT_VAULT_FOLDER).strip("/")


def _fetch_from_locke(env_var: str) -> str | None:
    secret_name = SECRET_MAP.get(env_var)
    if not secret_name:
        return None

    path = f"{vault_folder()}/{secret_name}"
    try:
        from locke.vault import get_vault_secret
    except ImportError:
        logger.debug("locke not installed; %s is env-var only", env_var)
        return None

    try:
        return get_vault_secret(path)
    except Exception as exc:  # noqa: BLE001 - credential backends should be best-effort here.
        logger.debug("failed to fetch %s from Locke/Vaultwarden: %s", path, exc)
        return None


def get_secret(env_var: str) -> str | None:
    """Resolve a value from env first, then Locke/Vaultwarden when available."""
    if env_var in _cache:
        return _cache[env_var]

    value = os.getenv(env_var)
    if value:
        _cache[env_var] = value
        return value

    value = _fetch_from_locke(env_var)
    if value:
        _cache[env_var] = value
        return value

    return None
