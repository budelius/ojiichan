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


def tls_verify(verify_certs: bool, ca_cert: str | None) -> bool | str:
    """Resolve a ``verify=`` value for httpx / opensearch-py / requests.

    ``False`` disables verification entirely (the ``--insecure`` escape hatch).
    Otherwise a ``ca_cert`` path pins trust to that CA bundle — needed for
    obachan's OpenSearch, whose node/HTTP cert is issued by the private
    ``obachan-internal-ca`` and so is not in the system trust store — while a
    missing ``ca_cert`` falls back to the system store (``True``).
    """
    if not verify_certs:
        return False
    return ca_cert or True


ELK_TRANSPORTS = ("es", "otlp", "logstash")


@dataclass(frozen=True)
class ElkConfig:
    host: str | None
    port: int = 443
    username: str | None = None
    password: str | None = None
    use_ssl: bool = True
    verify_certs: bool = True
    # Path to a CA bundle (PEM) to trust in addition to / instead of the system
    # store. Set ``ELASTICSEARCH_CA_CERT`` to obachan's ``obachan-internal-ca``
    # so reads/writes against the internal OpenSearch verify instead of failing.
    ca_cert: str | None = None
    index_prefix: str = "app"
    timeout: float = 10
    # Write transport for structured events. ``es`` indexes straight into
    # Elasticsearch; ``logstash`` POSTs the document to a Logstash HTTP input;
    # ``otlp`` ships an OpenTelemetry LogRecord to an OTLP/HTTP endpoint (usually
    # an OpenTelemetry Collector) which then routes to ELK. The document shape is
    # identical across transports, so events stay queryable together. Reads
    # (``health``/``logs``) always go to Elasticsearch regardless of transport.
    transport: str = "es"
    logstash_url: str | None = None
    otlp_endpoint: str | None = None
    otlp_headers: str | None = None

    @classmethod
    def from_env(cls) -> ElkConfig:
        transport = (env("ELK_TRANSPORT", "es") or "es").strip().lower()
        if transport not in ELK_TRANSPORTS:
            transport = "es"
        return cls(
            host=env("ELASTICSEARCH_HOST"),
            port=int(env("ELASTICSEARCH_PORT", "443") or "443"),
            username=env("ELASTICSEARCH_USERNAME"),
            password=env("ELASTICSEARCH_PASSWORD"),
            use_ssl=env_bool("ELASTICSEARCH_USE_SSL", True),
            verify_certs=env_bool("ELASTICSEARCH_VERIFY_CERTS", True),
            ca_cert=env("ELASTICSEARCH_CA_CERT"),
            index_prefix=env("ELASTICSEARCH_INDEX_PREFIX", "app") or "app",
            timeout=float(env("ELASTICSEARCH_TIMEOUT", "10") or "10"),
            transport=transport,
            logstash_url=(env("LOGSTASH_URL") or "").rstrip("/") or None,
            # Standard OpenTelemetry env vars (no OJIICHAN_ prefix needed, though
            # the resolver still honours an OJIICHAN_-prefixed override first).
            otlp_endpoint=(env("OTEL_EXPORTER_OTLP_ENDPOINT") or "").rstrip("/") or None,
            otlp_headers=env("OTEL_EXPORTER_OTLP_HEADERS"),
        )

    @property
    def url(self) -> str | None:
        if not self.host:
            return None
        if self.host.startswith(("http://", "https://")):
            return self.host
        scheme = "https" if self.use_ssl else "http"
        return f"{scheme}://{self.host}:{self.port}"

    @property
    def verify(self) -> bool | str:
        """``verify=`` value for httpx transports (bool or CA-bundle path)."""
        return tls_verify(self.verify_certs, self.ca_cert)

    @property
    def write_target(self) -> str | None:
        """Return the configured sink URL for the active write transport.

        ``None`` means the transport is unconfigured, so callers fall back to
        console logging instead of attempting a doomed network call.
        """
        if self.transport == "logstash":
            return self.logstash_url
        if self.transport == "otlp":
            return self.otlp_endpoint
        return self.url


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


@dataclass(frozen=True)
class SentryApiConfig:
    auth_token: str | None
    base_url: str = "https://sentry.io"
    timeout: float = 10

    @classmethod
    def from_env(cls) -> SentryApiConfig:
        return cls(
            auth_token=env("SENTRY_AUTH_TOKEN"),
            base_url=(env("SENTRY_BASE_URL", "https://sentry.io") or "https://sentry.io").rstrip("/"),
            timeout=float(env("SENTRY_TIMEOUT", "10") or "10"),
        )


_RUSTRAK_API_DEFAULT = "https://ingest.rustrak.pseekoo.io"


@dataclass(frozen=True)
class RustrakConfig:
    """Configuration for Rustrak (a Sentry-compatible error tracker).

    ``base_url`` is the **API** host (``ingest.rustrak.pseekoo.io``); the
    dashboard/UI host is served from the same domain minus the ``ingest.``
    prefix and is exposed via :pyattr:`ui_url` for triage links.
    """

    api_token: str | None
    base_url: str = _RUSTRAK_API_DEFAULT
    project_id: str | None = None
    timeout: float = 10

    @classmethod
    def from_env(cls) -> RustrakConfig:
        return cls(
            api_token=env("RUSTRAK_API_TOKEN"),
            base_url=(env("RUSTRAK_BASE_URL", _RUSTRAK_API_DEFAULT) or _RUSTRAK_API_DEFAULT).rstrip(
                "/"
            ),
            project_id=env("RUSTRAK_PROJECT_ID"),
            timeout=float(env("RUSTRAK_TIMEOUT", "10") or "10"),
        )

    @property
    def ui_url(self) -> str | None:
        """Return the dashboard URL for the configured project, if any.

        Strips a leading ``ingest.`` from the hostname so triage links point at
        the Next.js dashboard rather than the API host.
        """
        if not self.project_id:
            return None
        parsed = urlparse(self.base_url)
        host = parsed.hostname or ""
        if host.startswith("ingest."):
            host = host[len("ingest.") :]
        netloc = f"{host}:{parsed.port}" if parsed.port else host
        scheme = parsed.scheme or "https"
        return f"{scheme}://{netloc}/projects/{self.project_id}"


@dataclass(frozen=True)
class ScalewayConfig:
    """Configuration for querying Scaleway Cockpit (Loki) logs.

    Scaleway Serverless Functions ship their stdout/stderr to the project's
    Cockpit, queryable through a Loki-compatible HTTP API. Point ``loki_url`` at
    the project's logs data-source URL (``scw cockpit data-source list`` →
    ``type=logs`` → ``url``) and ``token`` at a Cockpit token created with the
    ``query_logs`` scope (``scw cockpit token create``).

    ``resource_type`` is the default Loki label used as the stream selector;
    callers narrow to a single function with the ``--source`` flag, which maps
    to the ``resource_name`` label.
    """

    loki_url: str | None
    token: str | None
    resource_type: str = "serverless_function"
    timeout: float = 15

    @classmethod
    def from_env(cls) -> ScalewayConfig:
        return cls(
            loki_url=(env("SCALEWAY_LOKI_URL") or "").rstrip("/") or None,
            token=env("SCALEWAY_COCKPIT_TOKEN"),
            resource_type=env("SCALEWAY_RESOURCE_TYPE", "serverless_function")
            or "serverless_function",
            timeout=float(env("SCALEWAY_TIMEOUT", "15") or "15"),
        )

    @property
    def query_url(self) -> str | None:
        if not self.loki_url:
            return None
        return f"{self.loki_url}/loki/api/v1/query_range"


@dataclass(frozen=True)
class ObachanBackendConfig:
    """Configuration for the obachan/OpenSearch backend.

    Reads from ELASTICSEARCH_* env vars (same as ElkConfig) with an optional
    OBACHAN_OPENSEARCH_HOST override for cases where ojiichan points at a different
    OpenSearch cluster from the general ELK target. Wraps an ElkConfig so existing
    credential resolution and TLS logic is reused without duplication.

    The ``knative_namespace`` is the Kubernetes namespace where Knative services run;
    it is used as a contextual label when querying Knative function logs.
    """

    elk_config: ElkConfig
    knative_namespace: str = "knative-serving"

    @classmethod
    def from_env(cls) -> ObachanBackendConfig:
        obachan_host = env("OBACHAN_OPENSEARCH_HOST")
        if obachan_host:
            # A distinct OpenSearch endpoint for the obachan platform — build a
            # custom ElkConfig pointing at that host while inheriting all other
            # ELASTICSEARCH_* settings (auth, TLS, index prefix, timeout).
            elk = ElkConfig(
                host=obachan_host,
                port=int(env("ELASTICSEARCH_PORT", "443") or "443"),
                username=env("ELASTICSEARCH_USERNAME"),
                password=env("ELASTICSEARCH_PASSWORD"),
                use_ssl=env_bool("ELASTICSEARCH_USE_SSL", True),
                verify_certs=env_bool("ELASTICSEARCH_VERIFY_CERTS", True),
                ca_cert=env("ELASTICSEARCH_CA_CERT"),
                index_prefix=env("ELASTICSEARCH_INDEX_PREFIX", "app") or "app",
                timeout=float(env("ELASTICSEARCH_TIMEOUT", "10") or "10"),
            )
        else:
            elk = ElkConfig.from_env()
        return cls(
            elk_config=elk,
            knative_namespace=env("OBACHAN_KNATIVE_NAMESPACE") or "knative-serving",
        )


ISSUE_BACKENDS = ("rustrak", "sentry", "bugsink", "obachan")


def default_issue_backend() -> str:
    """Return the project-declared issue backend, defaulting to ``auto``.

    Projects pick a backend by exporting ``OJIICHAN_ISSUE_BACKEND=rustrak`` (or
    ``sentry`` / ``bugsink`` / ``obachan``). When no explicit backend is declared,
    prefers ``obachan`` if an OpenSearch URL is configured (ELASTICSEARCH_HOST
    or OJIICHAN_ELASTICSEARCH_HOST), otherwise returns ``auto`` so callers can
    fall back to their own heuristics (e.g. prefer Rustrak when a token exists).
    """
    value = (env("ISSUE_BACKEND") or "").strip().lower()
    if value in ISSUE_BACKENDS:
        return value
    # Auto-detect: obachan/OpenSearch is the preferred default when configured.
    if ElkConfig.from_env().url is not None:
        return "obachan"
    return "auto"


def parse_sentry_dsn(dsn: str) -> tuple[str | None, str | None]:
    """Extract Bugsink/Sentry host and project id from a DSN."""
    try:
        parsed = urlparse(dsn)
    except Exception:
        return None, None
    host = parsed.hostname
    project_id = parsed.path.strip("/").split("/")[-1] if parsed.path.strip("/") else None
    return host, project_id
