"""Project-scoped Rustrak provisioning.

Rustrak is a Sentry-compatible error tracker (obachan deploys it cluster-wide).
Per project, ojiichan ensures the project's ingest DSN and read/resolve API token
are recorded in Vault so the service and its scaffolded siblings can report errors
and resolve issues.

Two modes, picked by what the caller supplies:

* **register-only** (default) — the project already exists (created in the Rustrak
  UI or by obachan); pass its ``--dsn`` / ``--api-token`` / ``--project-id`` and
  ojiichan just writes them to Vault. This is the common, no-admin-credentials
  path.
* **admin-create** — when an admin endpoint and token are given
  (``--admin-url`` / ``--admin-token``), ojiichan ``POST``s to the Sentry-style
  project-creation API and reads the DSN/token from the response, then stores
  them. The create path is configurable (``create_path``) because self-hosted
  deployments vary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from ..config import tls_verify
from . import vault_write
from .base import ProvisionResult, ProvisionStep, run_steps

#: Sentry-compatible default; override per deployment when the API differs.
DEFAULT_CREATE_PATH = "api/0/projects/"


@dataclass
class RustrakProvisionConfig:
    project: str
    # register-only inputs
    dsn: str | None = None
    api_token: str | None = None
    project_id: str | None = None
    # admin-create inputs
    admin_url: str | None = None
    admin_token: str | None = None
    create_path: str = DEFAULT_CREATE_PATH
    verify_certs: bool = True
    ca_cert: str | None = None
    timeout: float = 15.0


def _first(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


class RustrakProvisioner:
    def __init__(
        self, config: RustrakProvisionConfig, *, client: httpx.Client | None = None
    ) -> None:
        self.c = config
        self.dsn = config.dsn
        self.api_token = config.api_token
        self.project_id = config.project_id
        self._client = client
        if config.admin_url and client is None:
            self._client = httpx.Client(
                base_url=config.admin_url.rstrip("/"),
                headers={
                    "Authorization": f"Bearer {config.admin_token}",
                    "Content-Type": "application/json",
                },
                verify=tls_verify(config.verify_certs, config.ca_cert),
                timeout=config.timeout,
            )

    @property
    def _admin_mode(self) -> bool:
        return bool(self.c.admin_url and self.c.admin_token)

    def _create_project(self) -> str:
        if self._client is None:  # pragma: no cover - guarded by _admin_mode
            raise RuntimeError("no admin client configured")
        body = {"name": self.c.project, "slug": self.c.project}
        resp = self._client.post(self.c.create_path, json=body)
        resp.raise_for_status()
        data = resp.json() if resp.content else {}
        keys = data.get("keys")
        first_key = keys[0] if isinstance(keys, list) and keys else {}
        self.dsn = (
            self.dsn
            or _first(data, "dsn", "publicDSN", "public_dsn")
            or _first(first_key, "dsn", "public")
        )
        self.api_token = self.api_token or _first(data, "token", "api_token", "authToken")
        self.project_id = self.project_id or _first(data, "id", "slug", "project_id")
        if not self.dsn:
            raise RuntimeError(
                "project created but no DSN found in response; pass --dsn explicitly "
                "or set the correct --create-path for this Rustrak deployment"
            )
        return f"created Rustrak project {self.c.project}"

    def steps(self) -> list[ProvisionStep]:
        if self._admin_mode:
            return [
                ProvisionStep(
                    name="create_project",
                    description=f"Rustrak project {self.c.project} via admin API",
                    # No cheap idempotency probe across deployments; create-or-replace
                    # semantics live server-side. Treat as always-apply.
                    check=None,
                    apply=self._create_project,
                )
            ]
        # register-only: nothing to call, just validate we have something to store.
        return [
            ProvisionStep(
                name="register_credentials",
                description="register provided Rustrak DSN/token",
                check=lambda: False,
                apply=lambda: "using caller-provided DSN/token",
            )
        ]

    def provision(self, *, dry_run: bool = False, write_vault: bool = True) -> ProvisionResult:
        if not self._admin_mode and not (self.dsn or self.api_token):
            result = ProvisionResult(target="rustrak", dry_run=dry_run)
            result.notes.append(
                "nothing to do: pass --dsn/--api-token (register-only) or "
                "--admin-url/--admin-token (create the project)"
            )
            return result

        result = run_steps("rustrak", self.steps(), dry_run=dry_run)
        if dry_run or not result.ok or not write_vault:
            return result

        written = vault_write.store_secrets(
            {
                "SENTRY_DSN": self.dsn,
                "RUSTRAK_API_TOKEN": self.api_token,
                "RUSTRAK_PROJECT_ID": self.project_id,
            }
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
        return result
