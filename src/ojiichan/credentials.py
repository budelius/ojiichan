"""Credential resolution for Ojiichan.

Environment variables are still the explicit override, but secret values can be
resolved from Vaultwarden via Locke when the optional ``vault`` extra is
installed.

To minimise pressure on Vaultwarden's per-IP rate limits on
``/identity/connect/token`` and ``/api/sync``, this module:

* reuses a single ``VaultClient`` per process,
* warms every known secret in one ``list_secrets()`` call instead of N
  per-secret round trips, and
* persists the warmed snapshot to a 0600 file in the user cache dir so that
  short-lived MCP server processes can reuse it within the TTL window.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import tempfile
import time
from pathlib import Path
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

VAULT_FOLDER_ENV = "OJIICHAN_VAULT_FOLDER"
DEFAULT_VAULT_FOLDER = "ojiichan"
VAULT_CACHE_TTL_ENV = "OJIICHAN_VAULT_CACHE_TTL"
DEFAULT_VAULT_CACHE_TTL = 300  # seconds
VAULT_CACHE_DISABLE_ENV = "OJIICHAN_VAULT_CACHE_DISABLE"
VAULT_CACHE_PATH_ENV = "OJIICHAN_VAULT_CACHE_PATH"
VAULT_PROJECT_ENV = "OJIICHAN_VAULT_PROJECT"

# Env var name -> vault secret name. By default the full vault path is
# ``{OJIICHAN_VAULT_FOLDER or ojiichan}/{secret_name}``. When
# ``OJIICHAN_VAULT_PROJECT`` is set, reads check
# ``{folder}/projects/{project}/{secret_name}`` first, then fall back to the
# shared path. Provisioning writes project credentials to that project-scoped
# path so onboarding one service does not overwrite another service's writer
# creds.
SECRET_MAP: dict[str, str] = {
    "ELASTICSEARCH_HOST": "elk/host",
    "ELASTICSEARCH_USERNAME": "elk/username",
    "ELASTICSEARCH_PASSWORD": "elk/password",
    "ELASTICSEARCH_INDEX_PREFIX": "elk/index-prefix",
    "RUSTRAK_API_TOKEN": "rustrak/api-token",
    "SENTRY_DSN": "sentry-dsn",
    "BUGSINK_API_TOKEN": "bugsink-api-token",
    "BUGSINK_HOST": "bugsink-host",
    "BUGSINK_PROJECT_ID": "bugsink-project-id",
    "SENTRY_AUTH_TOKEN": "sentry-auth-token",
    "SENTRY_BASE_URL": "sentry-base-url",
    "SCALEWAY_LOKI_URL": "scaleway/loki-url",
    "SCALEWAY_COCKPIT_TOKEN": "scaleway/cockpit-token",
    # obachan platform: future REST API credentials (resolved via locke at runtime).
    "OBACHAN_API_URL": "obachan/api-url",
    "OBACHAN_API_KEY": "obachan/api-key",
    # Optional override pointing at a different OpenSearch from the shared ELK target.
    "OBACHAN_OPENSEARCH_HOST": "obachan/opensearch-host",
    # OpenTelemetry exporter wiring, written by `ojiichan provision otel`. The
    # endpoint is non-secret but lives here so the read-path resolves it from the
    # vault too; the headers may carry a bearer token, so they are a secret.
    "OTEL_EXPORTER_OTLP_ENDPOINT": "otel/exporter-endpoint",
    "OTEL_EXPORTER_OTLP_HEADERS": "otel/exporter-headers",
    # Platform admin connection for `ojiichan provision`. These are the obachan
    # cluster's privileged OpenSearch credentials, seeded once (by obachan or the
    # operator) so an agent can `provision --project X` with no admin args — the
    # admin URL/user/password resolve from the vault automatically.
    "OPENSEARCH_ADMIN_URL": "elk/admin-url",
    "OPENSEARCH_ADMIN_USER": "elk/admin-username",
    "OPENSEARCH_ADMIN_PASSWORD": "elk/admin-password",
    "RUSTRAK_ADMIN_URL": "rustrak/admin-url",
    "RUSTRAK_ADMIN_TOKEN": "rustrak/admin-token",
}

_lock = Lock()
_memcache: dict[str, str | None] = {}
_client: Any | None = None
_warmed = False


def vault_folder() -> str:
    """Return the configured Locke/Vaultwarden folder prefix."""
    return os.getenv(VAULT_FOLDER_ENV, DEFAULT_VAULT_FOLDER).strip("/")


def vault_project() -> str | None:
    """Return the optional project-scoped vault namespace."""
    project = os.getenv(VAULT_PROJECT_ENV)
    return project.strip("/") if project else None


def vault_project_folder(project: str | None = None) -> str | None:
    """Return ``<folder>/projects/<project>`` for scoped project credentials."""
    scoped = (project or vault_project() or "").strip("/")
    if not scoped:
        return None
    return f"{vault_folder()}/projects/{scoped}"


def _disk_cache_enabled() -> bool:
    return not os.getenv(VAULT_CACHE_DISABLE_ENV)


def _disk_cache_ttl() -> int:
    raw = os.getenv(VAULT_CACHE_TTL_ENV)
    if raw is None:
        return DEFAULT_VAULT_CACHE_TTL
    try:
        ttl = int(raw)
    except ValueError:
        logger.debug("invalid %s=%r, falling back to default", VAULT_CACHE_TTL_ENV, raw)
        return DEFAULT_VAULT_CACHE_TTL
    return max(0, ttl)


def _disk_cache_path() -> Path:
    override = os.getenv(VAULT_CACHE_PATH_ENV)
    if override:
        return Path(override)
    base = os.getenv("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    suffix = vault_folder().replace("/", "-")
    project = vault_project()
    if project:
        suffix = f"{suffix}-project-{project.replace('/', '-')}"
    return Path(base) / "ojiichan" / f"vault-{suffix}.json"


def _load_disk_cache() -> dict[str, str] | None:
    if not _disk_cache_enabled():
        return None
    ttl = _disk_cache_ttl()
    if ttl == 0:
        return None
    path = _disk_cache_path()
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.debug("failed to read vault cache at %s: %s", path, exc)
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.debug("vault cache at %s is malformed: %s", path, exc)
        return None
    if not isinstance(data, dict):
        return None
    timestamp = data.get("timestamp")
    secrets = data.get("secrets")
    folder = data.get("folder")
    if not isinstance(timestamp, (int, float)) or not isinstance(secrets, dict):
        return None
    if folder != vault_folder():
        return None
    if time.time() - timestamp > ttl:
        return None
    return {k: v for k, v in secrets.items() if isinstance(k, str) and isinstance(v, str)}


def _save_disk_cache(secrets: dict[str, str]) -> None:
    if not _disk_cache_enabled():
        return
    if _disk_cache_ttl() == 0:
        return
    path = _disk_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.parent.chmod(stat.S_IRWXU)  # 0700
        except OSError:
            pass
        payload = json.dumps(
            {"timestamp": time.time(), "folder": vault_folder(), "secrets": secrets}
        )
        # Write via tempfile in the same dir so the atomic replace stays on one FS,
        # and so we can chmod 0600 before the file is visible at its final name.
        fd, tmp_name = tempfile.mkstemp(prefix=".vault-", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(payload)
            os.chmod(tmp_name, stat.S_IRUSR | stat.S_IWUSR)  # 0600
            os.replace(tmp_name, path)
        except OSError:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
    except OSError as exc:
        logger.debug("failed to write vault cache at %s: %s", path, exc)


def _get_client() -> Any | None:
    global _client
    if _client is not None:
        return _client
    try:
        from locke.vault import VaultClient
    except ImportError:
        logger.debug("locke not installed; vault lookups disabled")
        return None
    try:
        _client = VaultClient.connect()
    except Exception as exc:  # noqa: BLE001 - credential backends are best-effort.
        logger.debug("failed to connect to Locke/Vaultwarden: %s", exc)
        return None
    return _client


def _warm_from_locke() -> None:
    """Bulk-fetch every known secret in a single ``list_secrets()`` call.

    Populates ``_memcache`` with ``None`` for any known env var that isn't in
    the vault, so subsequent lookups don't re-hit Vaultwarden.
    """
    client = _get_client()
    if client is None:
        return

    cleaned: dict[str, str] = {}
    prefixes = [f"{vault_folder()}/"]
    project_folder = vault_project_folder()
    if project_folder:
        prefixes.append(f"{project_folder}/")

    for prefix in prefixes:
        try:
            secrets = client.list_secrets(prefix)
        except Exception as exc:  # noqa: BLE001 - credential backends are best-effort.
            logger.debug("failed to list secrets from Locke/Vaultwarden: %s", exc)
            continue
        for path, value in secrets.items():
            if isinstance(path, str) and path.startswith(prefix) and isinstance(value, str):
                cleaned[path[len(prefix):]] = value
    _apply_snapshot(cleaned)
    _save_disk_cache(cleaned)


def _apply_snapshot(snapshot: dict[str, str]) -> None:
    """Populate ``_memcache`` for every known env var from a vault snapshot."""
    for env_var, secret_name in SECRET_MAP.items():
        if env_var in _memcache:
            continue
        _memcache[env_var] = snapshot.get(secret_name)


def _ensure_warmed() -> None:
    global _warmed
    if _warmed:
        return
    snapshot = _load_disk_cache()
    if snapshot is not None:
        _apply_snapshot(snapshot)
    else:
        _warm_from_locke()
    _warmed = True


def get_secret(env_var: str) -> str | None:
    """Resolve a value from env first, then Locke/Vaultwarden when available."""
    with _lock:
        if env_var in _memcache:
            return _memcache[env_var]

        value = os.getenv(env_var)
        if value:
            _memcache[env_var] = value
            return value

        _ensure_warmed()
        return _memcache.get(env_var)


def _reset_for_tests() -> None:
    """Clear in-process caches. Intended for use in tests only."""
    global _client, _warmed
    with _lock:
        _memcache.clear()
        _client = None
        _warmed = False
