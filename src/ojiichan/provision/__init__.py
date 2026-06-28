"""Project-scoped provisioning for the ELK + Rustrak + OpenTelemetry stack.

``obachan`` installs the *platform* (an OpenSearch cluster, the OTLP ingestion
pipeline, the Rustrak deployment, OpenBao/Vault) once per environment. This
package handles the complementary, per-*project* step: onboarding a single
service onto that already-running platform and writing the resulting credentials
back into Vault so the service — and the TypeScript/Rust/Swift siblings ojiichan
scaffolds — resolve them through the normal read-path.

Each provisioner is idempotent: it mirrors obachan's create-or-replace contract
(GET to check, PUT/POST to apply) so re-running reports ``exists`` rather than
churning state. ``dry_run=True`` plans without mutating anything.
"""

from __future__ import annotations

from .all import provision_all
from .base import ProvisionResult, ProvisionStep, StepOutcome, run_steps
from .elk import ElkProvisionConfig, ElkProvisioner
from .otel import OtelProvisionConfig, OtelProvisioner
from .rustrak import RustrakProvisionConfig, RustrakProvisioner

__all__ = [
    "ElkProvisionConfig",
    "ElkProvisioner",
    "OtelProvisionConfig",
    "OtelProvisioner",
    "ProvisionResult",
    "ProvisionStep",
    "RustrakProvisionConfig",
    "RustrakProvisioner",
    "StepOutcome",
    "provision_all",
    "run_steps",
]
