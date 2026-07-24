"""Project-scoped OpenTelemetry (OTLP) wiring.

The platform's OTLP ingestion endpoint (an OpenTelemetry Collector / Data Prepper
that fronts OpenSearch) is installed by obachan. Per project, ojiichan records the
exporter endpoint and any auth headers in Vault so a service can flip
``ELK_TRANSPORT=otlp`` and ship structured events through the standard
``OTEL_EXPORTER_OTLP_*`` configuration — identical document shape, different sink.

This provisioner is config-first: there is no resource to create, only the
endpoint/headers to validate and persist. With ``--probe`` it does a cheap
reachability check (an empty OTLP LogRecord ``POST`` to ``<endpoint>/v1/logs``);
any HTTP response — including 4xx — counts as reachable, only a transport error
fails it.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from ..config import tls_verify
from . import vault_write
from .base import ProvisionResult, ProvisionStep, run_steps


@dataclass
class OtelProvisionConfig:
    project: str
    endpoint: str
    headers: str | None = None
    probe: bool = False
    verify_certs: bool = True
    ca_cert: str | None = None
    timeout: float = 10.0


class OtelProvisioner:
    def __init__(
        self, config: OtelProvisionConfig, *, client: httpx.Client | None = None
    ) -> None:
        self.c = config
        self._client = client

    def _logs_url(self) -> str:
        endpoint = self.c.endpoint.rstrip("/")
        return endpoint if endpoint.endswith("/v1/logs") else f"{endpoint}/v1/logs"

    def _probe(self) -> str:
        client = self._client or httpx.Client(
            verify=tls_verify(self.c.verify_certs, self.c.ca_cert), timeout=self.c.timeout
        )
        from ..transports import parse_otlp_headers

        headers = {"Content-Type": "application/json", **parse_otlp_headers(self.c.headers)}
        # Minimal well-formed OTLP/HTTP body; the collector may accept or reject it,
        # either way the endpoint is reachable.
        resp = client.post(self._logs_url(), json={"resourceLogs": []}, headers=headers)
        return f"reachable ({resp.status_code})"

    def steps(self) -> list[ProvisionStep]:
        steps = [
            ProvisionStep(
                name="endpoint_resolved",
                description=f"OTLP endpoint {self.c.endpoint}",
                check=lambda: bool(self.c.endpoint),
                apply=lambda: "endpoint set",
            )
        ]
        if self.c.probe:
            steps.append(
                ProvisionStep(
                    name="endpoint_reachable",
                    description=f"probe {self._logs_url()}",
                    check=None,
                    apply=self._probe,
                )
            )
        return steps

    def provision(self, *, dry_run: bool = False, write_vault: bool = True) -> ProvisionResult:
        if not self.c.endpoint:
            result = ProvisionResult(target="otel", dry_run=dry_run)
            result.notes.append("nothing to do: pass --endpoint (OTEL_EXPORTER_OTLP_ENDPOINT)")
            return result

        result = run_steps("otel", self.steps(), dry_run=dry_run)
        if dry_run or not result.ok or not write_vault:
            return result

        written = vault_write.store_secrets(
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": self.c.endpoint,
                "OTEL_EXPORTER_OTLP_HEADERS": self.c.headers,
            },
            project=self.c.project,
        )
        result.secrets_written = [
            env for env, action in written.items() if action in {"created", "updated"}
        ]
        unavailable = [env for env, action in written.items() if action == "unavailable"]
        if unavailable:
            result.notes.append(
                "vault unavailable (install ojiichan[vault]); store these manually: "
                + ", ".join(unavailable)
            )
        result.notes.append("set ELK_TRANSPORT=otlp in the service to use this endpoint")
        if not self.c.probe:
            result.notes.append(
                "endpoint stored without a reachability check; rerun with --probe before rollout"
            )
        return result
