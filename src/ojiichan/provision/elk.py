"""Project-scoped ELK/OpenSearch provisioning.

Mirrors obachan's ``opensearch-bootstrap`` contract — index template + ISM
retention policy + least-privilege role + internal user + role-mapping — but
scoped to a *single* project and driven from the ojiichan CLI against an
already-running OpenSearch that obachan installed. Every apply is an idempotent
``PUT`` (create-or-replace); each check ``GET``s first so a second run reports
``exists``.

On success the generated scoped credential is written to Vault
(``ELASTICSEARCH_HOST`` / ``USERNAME`` / ``PASSWORD`` / ``INDEX_PREFIX``) so the
project and its scaffolded siblings resolve it through the normal read-path. The
password is only stored when this run actually created the internal user (or when
the caller passed an explicit ``--scoped-password``), so re-runs never desync the
stored secret from the live one.
"""

from __future__ import annotations

import secrets as _secrets
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import tls_verify
from . import vault_write
from .base import ProvisionResult, ProvisionStep, run_steps

# Canonical structured-event mapping, mirrored from obachan's opensearch-bootstrap
# ConfigMap so app indices share one shape across every ojiichan-monitored service.
_APP_MAPPINGS: dict[str, dict[str, str]] = {
    "@timestamp": {"type": "date"},
    "failure": {"type": "boolean"},
    "level": {"type": "keyword"},
    "service": {"type": "keyword"},
    "operation": {"type": "keyword"},
    "event_type": {"type": "keyword"},
    "subtype": {"type": "keyword"},
    "topic": {"type": "keyword"},
    "hostname": {"type": "keyword"},
}


@dataclass
class ElkProvisionConfig:
    """Inputs for provisioning one project onto an existing OpenSearch."""

    admin_url: str
    admin_user: str
    admin_password: str
    project: str
    index_prefix: str
    retention_days: int = 30
    scoped_user: str | None = None
    scoped_password: str | None = None
    verify_certs: bool = True
    ca_cert: str | None = None
    timeout: float = 15.0


class ElkProvisioner:
    """Create the per-project OpenSearch resources, then stash creds in Vault."""

    def __init__(self, config: ElkProvisionConfig, *, client: httpx.Client | None = None) -> None:
        self.c = config
        self.scoped_user = config.scoped_user or f"{config.project}-writer"
        # Track whether *we* minted the password this run (vs. it pre-existing).
        self._password_explicit = bool(config.scoped_password)
        self.scoped_password = config.scoped_password or _secrets.token_urlsafe(24)
        self._client = client or httpx.Client(
            base_url=config.admin_url.rstrip("/"),
            auth=(config.admin_user, config.admin_password),
            verify=tls_verify(config.verify_certs, config.ca_cert),
            timeout=config.timeout,
            headers={"Content-Type": "application/json"},
        )

    # --- resource names -----------------------------------------------------
    @property
    def template_name(self) -> str:
        return f"{self.c.project}-logs"

    @property
    def policy_name(self) -> str:
        return f"{self.c.project}-logs-retention"

    @property
    def role_name(self) -> str:
        return f"{self.c.project}-writer"

    # --- low-level helpers --------------------------------------------------
    def _exists(self, path: str) -> bool:
        resp = self._client.get(path)
        return resp.status_code == 200

    def _put(self, path: str, body: dict[str, Any]) -> str:
        resp = self._client.put(path, json=body)
        resp.raise_for_status()
        return f"PUT {path} -> {resp.status_code}"

    # --- payload builders (mirrored from obachan) ---------------------------
    def _template_body(self) -> dict[str, Any]:
        return {
            "index_patterns": [f"{self.c.index_prefix}-*"],
            "template": {
                "settings": {"number_of_replicas": 0},
                "mappings": {"properties": _APP_MAPPINGS},
            },
        }

    def _policy_body(self) -> dict[str, Any]:
        days = self.c.retention_days
        return {
            "policy": {
                "description": f"{self.c.project} log retention: delete indices older than {days}d",
                "default_state": "hot",
                "states": [
                    {
                        "name": "hot",
                        "actions": [],
                        "transitions": [
                            {
                                "state_name": "delete",
                                "conditions": {"min_index_age": f"{days}d"},
                            }
                        ],
                    },
                    {"name": "delete", "actions": [{"delete": {}}], "transitions": []},
                ],
                "ism_template": [
                    {"index_patterns": [f"{self.c.index_prefix}-*"], "priority": 100}
                ],
            }
        }

    def _role_body(self) -> dict[str, Any]:
        return {
            "cluster_permissions": ["cluster_monitor", "cluster_composite_ops"],
            "index_permissions": [
                {
                    "index_patterns": [f"{self.c.index_prefix}-*"],
                    "allowed_actions": ["indices_all"],
                },
                {
                    "index_patterns": ["*"],
                    "allowed_actions": ["indices:admin/aliases/get"],
                },
            ],
        }

    def _user_body(self) -> dict[str, Any]:
        return {
            "password": self.scoped_password,
            "backend_roles": [],
            "attributes": {"ojiichan_managed": "true", "project": self.c.project},
        }

    def _mapping_body(self) -> dict[str, Any]:
        return {"users": [self.scoped_user]}

    # --- step assembly ------------------------------------------------------
    def steps(self) -> list[ProvisionStep]:
        sec = "_plugins/_security/api"
        user_path = f"{sec}/internalusers/{self.scoped_user}"
        return [
            ProvisionStep(
                name="index_template",
                description=f"index template {self.template_name} ({self.c.index_prefix}-*)",
                check=lambda: self._exists(f"_index_template/{self.template_name}"),
                apply=lambda: self._put(
                    f"_index_template/{self.template_name}", self._template_body()
                ),
            ),
            ProvisionStep(
                name="ism_policy",
                description=f"ISM retention policy {self.policy_name} ({self.c.retention_days}d)",
                check=lambda: self._exists(f"_plugins/_ism/policies/{self.policy_name}"),
                apply=lambda: self._put(
                    f"_plugins/_ism/policies/{self.policy_name}", self._policy_body()
                ),
            ),
            ProvisionStep(
                name="role",
                description=f"role {self.role_name} (read/write {self.c.index_prefix}-*)",
                check=lambda: self._exists(f"{sec}/roles/{self.role_name}"),
                apply=lambda: self._put(f"{sec}/roles/{self.role_name}", self._role_body()),
            ),
            ProvisionStep(
                name="internal_user",
                description=f"internal user {self.scoped_user}",
                check=lambda: self._exists(user_path),
                apply=lambda: self._put(user_path, self._user_body()),
            ),
            ProvisionStep(
                name="role_mapping",
                description=f"role mapping {self.role_name} -> {self.scoped_user}",
                check=lambda: self._exists(f"{sec}/rolesmapping/{self.role_name}"),
                apply=lambda: self._put(
                    f"{sec}/rolesmapping/{self.role_name}", self._mapping_body()
                ),
            ),
        ]

    # --- orchestration ------------------------------------------------------
    def provision(self, *, dry_run: bool = False, write_vault: bool = True) -> ProvisionResult:
        result = run_steps("elk", self.steps(), dry_run=dry_run)
        if dry_run or not result.ok or not write_vault:
            if write_vault and not dry_run and not result.ok:
                result.notes.append("backend steps failed; vault write skipped")
            return result

        user_created = any(
            s.name == "internal_user" and s.status == "created" for s in result.steps
        )
        to_store: dict[str, str | None] = {
            "ELASTICSEARCH_HOST": self.c.admin_url,
            "ELASTICSEARCH_USERNAME": self.scoped_user,
            "ELASTICSEARCH_INDEX_PREFIX": self.c.index_prefix,
        }
        # Only persist the password when we know it matches the live user: either
        # we just created the user, or the caller supplied it explicitly.
        if user_created or self._password_explicit:
            to_store["ELASTICSEARCH_PASSWORD"] = self.scoped_password
        else:
            result.notes.append(
                f"internal user {self.scoped_user} already existed; "
                "ELASTICSEARCH_PASSWORD left unchanged (pass --scoped-password to rotate)"
            )

        written = vault_write.store_secrets(to_store, project=self.c.project)
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
