"""Environment-backed configuration for reusable diagnostics."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from .credentials import get_secret


def env(name: str, default: str | None = None, *, prefix: str = "OJIICHAN") -> str | None:
    """Return prefixed env var first, then Locke/env-backed conventional value."""
    return os.getenv(f"{prefix}_{name}") or get_secret(name) or default


def env_bool(name: str, default: bool = False) -> bool:
    value = env(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ElkConfig:
    host: str | None
    port: int = 443
    username: str | None = None
    password: str | None = None
    use_ssl: bool = True
    verify_certs: bool = True
    index_prefix: str = "app"
    timeout: float = 10

    @classmethod
    def from_env(cls) -> ElkConfig:
        return cls(
            host=env("ELASTICSEARCH_HOST"),
            port=int(env("ELASTICSEARCH_PORT", "443") or "443"),
            username=env("ELASTICSEARCH_USERNAME"),
            password=env("ELASTICSEARCH_PASSWORD"),
            use_ssl=env_bool("ELASTICSEARCH_USE_SSL", True),
            verify_certs=env_bool("ELASTICSEARCH_VERIFY_CERTS", True),
            index_prefix=env("ELASTICSEARCH_INDEX_PREFIX", "app") or "app",
            timeout=float(env("ELASTICSEARCH_TIMEOUT", "10") or "10"),
        )

    @property
    def url(self) -> str | None:
        if not self.host:
            return None
        if self.host.startswith(("http://", "https://")):
            return self.host
        scheme = "https" if self.use_ssl else "http"
        return f"{scheme}://{self.host}:{self.port}"


@dataclass(frozen=True)
class BugsinkConfig:
    dsn: str | None
    api_token: str | None
    host: str | None
    project_id: str | None
    timeout: float = 10

    @classmethod
    def from_env(cls) -> BugsinkConfig:
        dsn = env("SENTRY_DSN")
        host = env("BUGSINK_HOST")
        project_id = env("BUGSINK_PROJECT_ID")
        if dsn and (not host or not project_id):
            parsed = parse_sentry_dsn(dsn)
            host = host or parsed[0]
            project_id = project_id or parsed[1]
        return cls(
            dsn=dsn,
            api_token=env("BUGSINK_API_TOKEN"),
            host=host,
            project_id=project_id,
            timeout=float(env("BUGSINK_TIMEOUT", "10") or "10"),
        )

    @property
    def ui_url(self) -> str | None:
        if not self.host or not self.project_id:
            return None
        return f"https://{self.host}/projects/{self.project_id}/"


def parse_sentry_dsn(dsn: str) -> tuple[str | None, str | None]:
    """Extract Bugsink/Sentry host and project id from a DSN."""
    try:
        parsed = urlparse(dsn)
    except Exception:
        return None, None
    host = parsed.hostname
    project_id = parsed.path.strip("/").split("/")[-1] if parsed.path.strip("/") else None
    return host, project_id
