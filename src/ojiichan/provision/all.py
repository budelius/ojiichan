"""One-shot onboarding: create all of a project's access on the obachan cluster.

This is the low-interaction path. An agent runs ``ojiichan provision all
--project <name>`` and ojiichan resolves the platform admin connection + OTLP
endpoint from the vault (seeded once by obachan/operator), then creates the
project's ELK access, registers its Rustrak project, and records its OTLP wiring —
writing every resulting credential back to the vault.

Scope: ojiichan only **creates access** on the already-running obachan cluster. It
never installs the platform and never tears anything down. Each backend that has
no usable config is **skipped with a note**, not failed, so a partial platform
(e.g. ELK but no Rustrak yet) still onboards cleanly.
"""

from __future__ import annotations

from .base import ProvisionResult
from .elk import ElkProvisionConfig, ElkProvisioner
from .otel import OtelProvisionConfig, OtelProvisioner
from .rustrak import RustrakProvisionConfig, RustrakProvisioner


def _skipped(target: str, dry_run: bool, reason: str) -> ProvisionResult:
    result = ProvisionResult(target=target, dry_run=dry_run)
    result.notes.append(f"skipped: {reason}")
    return result


def provision_all(
    *,
    project: str,
    # ELK (core access)
    elk_admin_url: str | None,
    elk_admin_password: str | None,
    elk_admin_user: str = "admin",
    index_prefix: str | None = None,
    retention_days: int = 30,
    scoped_user: str | None = None,
    scoped_password: str | None = None,
    # OpenTelemetry
    otlp_endpoint: str | None = None,
    otlp_headers: str | None = None,
    otlp_probe: bool = False,
    # Rustrak
    rustrak_dsn: str | None = None,
    rustrak_api_token: str | None = None,
    rustrak_project_id: str | None = None,
    rustrak_admin_url: str | None = None,
    rustrak_admin_token: str | None = None,
    # behavior
    dry_run: bool = False,
    write_vault: bool = True,
    verify_certs: bool = True,
) -> list[ProvisionResult]:
    """Run ELK + OTel + Rustrak onboarding for one project; return one result each.

    Backends without usable config are returned as ``skipped`` results rather than
    omitted, so the caller can report exactly what was and wasn't wired.
    """
    results: list[ProvisionResult] = []

    # --- ELK: the core "create access" step --------------------------------
    if elk_admin_url and elk_admin_password:
        elk = ElkProvisioner(
            ElkProvisionConfig(
                admin_url=elk_admin_url,
                admin_user=elk_admin_user,
                admin_password=elk_admin_password,
                project=project,
                index_prefix=index_prefix or project,
                retention_days=retention_days,
                scoped_user=scoped_user,
                scoped_password=scoped_password,
                verify_certs=verify_certs,
            )
        )
        results.append(elk.provision(dry_run=dry_run, write_vault=write_vault))
    else:
        results.append(
            _skipped(
                "elk",
                dry_run,
                "no OpenSearch admin connection (set OPENSEARCH_ADMIN_URL/"
                "OPENSEARCH_ADMIN_PASSWORD or seed elk/admin-* in the vault)",
            )
        )

    # --- OpenTelemetry ------------------------------------------------------
    if otlp_endpoint:
        otel = OtelProvisioner(
            OtelProvisionConfig(
                endpoint=otlp_endpoint,
                headers=otlp_headers,
                probe=otlp_probe,
                verify_certs=verify_certs,
            )
        )
        results.append(otel.provision(dry_run=dry_run, write_vault=write_vault))
    else:
        results.append(
            _skipped("otel", dry_run, "no OTLP endpoint (OTEL_EXPORTER_OTLP_ENDPOINT)")
        )

    # --- Rustrak ------------------------------------------------------------
    has_rustrak = bool(
        rustrak_dsn or rustrak_api_token or (rustrak_admin_url and rustrak_admin_token)
    )
    if has_rustrak:
        rustrak = RustrakProvisioner(
            RustrakProvisionConfig(
                project=project,
                dsn=rustrak_dsn,
                api_token=rustrak_api_token,
                project_id=rustrak_project_id,
                admin_url=rustrak_admin_url,
                admin_token=rustrak_admin_token,
                verify_certs=verify_certs,
            )
        )
        results.append(rustrak.provision(dry_run=dry_run, write_vault=write_vault))
    else:
        results.append(
            _skipped(
                "rustrak",
                dry_run,
                "no Rustrak DSN/token and no admin API (pass --rustrak-dsn or seed "
                "rustrak/admin-* in the vault)",
            )
        )

    return results
