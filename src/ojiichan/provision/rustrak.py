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

#: Rustrak's project-management endpoint; override when a deployment differs.
DEFAULT_CREATE_PATH = "api/projects"


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
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    return None


class RustrakProvisioner:
    def __init__(
        self, config: RustrakProvisionConfig, *, client: httpx.Client | None = None
    ) -> None:
        self.c = config
        self.dsn = config.dsn
        # Rustrak 0.2.x returns a DSN and numeric project id, but not another
        # API token. Its bearer token is user-scoped and is also the token used
        # for project/issue reads, so retain the supplied admin token unless a
        # distinct runtime token was provided explicitly.
        self.api_token = config.api_token or config.admin_token
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

    def _capture_project(self, data: dict[str, Any]) -> None:
        keys = data.get("keys")
        first_key = keys[0] if isinstance(keys, list) and keys else {}
        self.dsn = (
            self.dsn
            or _first(data, "dsn", "publicDSN", "public_dsn")
            or _first(first_key, "dsn", "public")
        )
        self.api_token = self.api_token or _first(data, "token", "api_token", "authToken")
        self.project_id = self.project_id or _first(data, "id", "project_id", "slug")

    def _project_exists(self) -> bool:
        if self._client is None:  # pragma: no cover - guarded by _admin_mode
            return False
        page = 1
        while True:
            resp = self._client.get(
                self.c.create_path.rstrip("/"), params={"page": page, "per_page": 100}
            )
            resp.raise_for_status()
            payload = resp.json() if resp.content else {}
            if isinstance(payload, list):
                items = payload
                total_pages = page
            elif isinstance(payload, dict):
                items = payload.get("items") or payload.get("projects") or []
                try:
                    total_pages = int(payload.get("total_pages") or page)
                except (TypeError, ValueError):
                    total_pages = page
            else:
                items = []
                total_pages = page
            for item in items:
                if not isinstance(item, dict):
                    continue
                if item.get("slug") == self.c.project:
                    self._capture_project(item)
                    if not self.dsn:
                        raise RuntimeError(
                            f"Rustrak project {self.c.project} exists but its DSN was not returned"
                        )
                    return True
            if page >= total_pages:
                return False
            page += 1

    def _create_project(self) -> str:
        if self._client is None:  # pragma: no cover - guarded by _admin_mode
            raise RuntimeError("no admin client configured")
        body = {"name": self.c.project, "slug": self.c.project}
        resp = self._client.post(self.c.create_path, json=body)
        resp.raise_for_status()
        data = resp.json() if resp.content else {}
        if not isinstance(data, dict):
            raise RuntimeError("project created but Rustrak returned a non-object response")
        self._capture_project(data)
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
                    check=self._project_exists,
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
        return result
