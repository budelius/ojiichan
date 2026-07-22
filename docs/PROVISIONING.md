# PROVISIONING.md — `ojiichan provision` (agent spec)

> Audience: a coding agent implementing or extending project-scoped provisioning.
> This is a contract + status doc. The skeleton in `src/ojiichan/provision/` already
> implements most of it; sections marked **TODO(agent)** are the remaining work.

## 1. Goal

Let an agent **create a service's access on the obachan cluster with one command**
and have every credential land in Vault automatically — no platform setup, no
teardown:

```bash
ojiichan provision all --project m3rp-api
```

`provision all` resolves the OpenSearch admin connection, OTLP endpoint, and any
Rustrak config from `--flag` → env → Vault, then runs ELK + OTel + Rustrak
onboarding for the project. Backends with no usable config are **skipped with a
note**, not failed. The individual subcommands (`elk` / `rustrak` / `otel`) exist
for granular control. Pass `--env staging` (or another environment name) to use
`<project>-<env>` consistently for backend resources and Vault scope.

After it runs (with the `vault` extra installed), `ojiichan health`, the Python
library, and the scaffolded TS/Rust/Swift siblings all resolve their config from
Vault with no further wiring.

### Low-interaction setup (one-time, by obachan/operator)

So an agent can run `provision all --project X` with nothing else, seed the shared
platform admin connection in Vault once. These resolve via
`credentials.SECRET_MAP`:

| Env var | Vault path | What |
| --- | --- | --- |
| `OPENSEARCH_ADMIN_URL` | `<folder>/elk/admin-url` | obachan OpenSearch URL |
| `OPENSEARCH_ADMIN_USER` | `<folder>/elk/admin-username` | admin user (default `admin`) |
| `OPENSEARCH_ADMIN_PASSWORD` | `<folder>/elk/admin-password` | admin password |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `<folder>/otel/exporter-endpoint` | platform OTLP endpoint |
| `RUSTRAK_ADMIN_URL` / `RUSTRAK_ADMIN_TOKEN` | `<folder>/rustrak/admin-*` | optional Rustrak admin API |

## 2. Boundary with obachan (read this first)

`obachan` installs the **platform** once per environment — the OpenSearch cluster,
the OTLP ingestion pipeline, the Rustrak deployment, OpenBao/Vault + ESO. Its
`deploy/platform/opensearch-bootstrap/configmap.yaml` is the canonical
cluster-wide bootstrap.

`ojiichan provision` does the **per-project** slice on top of that — it **creates a
project's access** by talking to the backend **APIs** (OpenSearch Security/ISM,
Rustrak admin, OTLP). It must **never** install or reconfigure the cluster, and it
does **not** deprovision/teardown/revert (out of scope by design). Where a resource
is genuinely cluster-level (e.g. a shared ISM policy), prefer a project-scoped
variant; only defer to obachan when no project-scoped form exists.

The ELK provisioner deliberately **mirrors the obachan configmap contract** so the
two never diverge: same index-template mapping, same role/user/role-mapping shape,
same idempotent create-or-replace PUTs.

## 3. The step engine (`provision/base.py`)

A miniature of obachan's predicate-driven registry.

- `ProvisionStep(name, description, apply, check=None)` — `check()` returns `True`
  when the resource already exists; `apply()` creates-or-replaces it.
- `run_steps(target, steps, *, dry_run) -> ProvisionResult`:
  - `check()` True → outcome `exists` (skip apply).
  - `dry_run` → outcome `would-create` (never apply).
  - else `apply()` → `created`, or `failed` (and **stop**, since later steps depend
    on earlier ones).
- `ProvisionResult.to_dict()` is the JSON surface for `--json` and MCP.

## 4. CLI contract

All subcommands share: `--project <name>` (required), optional `--env <name>`,
`--dry-run`, `--no-vault`, `--insecure`, `--ca-cert`, and `--json`.
`--env` changes the effective project to `<project>-<env>` and pins
`OJIICHAN_VAULT_PROJECT` to that value, creating independent backend resources
and `ojiichan/projects/<project>-<env>/` credentials. Omit it to retain one
shared project. Admin/connection inputs resolve `--flag` → env → Vault so
CI/agents needn't pass long arg lists.

### `ojiichan provision all` (the headline)
Runs `elk` + `otel` + `rustrak` for one project, each resolving its config from
`--flag` → env → Vault. Accepts the union of the per-backend flags (all optional
except `--project`). A backend with no usable config is **skipped with a note**, so
a partial platform still onboards cleanly. Returns one `ProvisionResult` per
backend (a JSON array under `--json`). Orchestrated by
`provision.provision_all(...)`; MCP equivalent is `provision_project`.

### `ojiichan provision elk`
Creates, against `OPENSEARCH_ADMIN_URL` as `admin`:

| Resource | Name | Mirrors obachan |
| --- | --- | --- |
| index template | `<project>-logs` (patterns `<prefix>-*`, canonical mappings) | ✓ |
| ISM retention policy | `<project>-logs-retention` (`--retention-days`, default 30) | ✓ |
| role | `<project>-writer` (`indices_all` on `<prefix>-*`, cluster monitor) | ✓ |
| internal user | `<project>-writer` (or `--scoped-user`), generated password | ✓ |
| role mapping | `<project>-writer` → user | ✓ |

Flags: `--admin-url` (`OPENSEARCH_ADMIN_URL`), `--admin-user` (default `admin`),
`--admin-password` (`OPENSEARCH_ADMIN_PASSWORD`), `--prefix` (default = project),
`--retention-days`, `--scoped-user`, `--scoped-password`.

Then writes to Vault: `ELASTICSEARCH_HOST`, `ELASTICSEARCH_USERNAME`,
`ELASTICSEARCH_INDEX_PREFIX`, and `ELASTICSEARCH_PASSWORD` **only when** the user
was created this run or `--scoped-password` was given (so re-runs never desync the
stored password from the live one — a note explains when it's skipped).

### `ojiichan provision rustrak`
Two modes:
- **register-only** (default): `--dsn` (`SENTRY_DSN`), `--api-token`
  (`RUSTRAK_API_TOKEN`), `--project-id` (`RUSTRAK_PROJECT_ID`) → store in Vault.
- **admin-create**: `--admin-url` (`RUSTRAK_ADMIN_URL`) + `--admin-token`
  (`RUSTRAK_ADMIN_TOKEN`) → POST `--create-path` (default `api/projects`, matching
  Rustrak), parse DSN/token from the response, and store it in Vault.

Writes to Vault: `SENTRY_DSN`, `RUSTRAK_API_TOKEN`, `RUSTRAK_PROJECT_ID`.

### `ojiichan provision otel`
Config-first (the OTLP endpoint is installed by obachan; per project we just record
it). `--endpoint` (`OTEL_EXPORTER_OTLP_ENDPOINT`), `--headers`
(`OTEL_EXPORTER_OTLP_HEADERS`), `--probe` (POST an empty LogRecord to
`<endpoint>/v1/logs`; any HTTP response = reachable).

Writes to Vault: `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`.
Reminds the operator to set `ELK_TRANSPORT=otlp` in the service.

## 5. Vault write-path (`provision/vault_write.py`)

The inverse of `credentials.py`. Key invariant: **writes land where reads look.**

- `secret_path(env_var)` resolves through the same `credentials.SECRET_MAP`
  (`ELASTICSEARCH_PASSWORD` → `<folder>/elk/password`, etc.). Unknown vars fall
  back to `<folder>/<env_var>`.
- Provisioned service credentials are written under
  `<folder>/projects/<project>/<secret_name>` so onboarding another service does
  not overwrite them. Runtime services opt in with
  `OJIICHAN_VAULT_PROJECT=<project>`; reads fall back to shared platform paths.
- `store_secret(env_var, value, ...)` / `store_secrets(mapping)` write to Locke's
  `VaultClient.set_secret(path, value, ...)` and/or OpenBao/Vault KV v2.
  Configure OpenBao with `OPENBAO_ADDR` + `OPENBAO_TOKEN` (or `VAULT_ADDR` +
  `VAULT_TOKEN`), optional `OPENBAO_KV_MOUNT` (default `secret`), and optional
  `OJIICHAN_VAULT_WRITE_TARGETS=locke,openbao`.
- No `vault` extra installed → returns `"unavailable"` (provision still completes
  the backend work and tells the operator which secrets to store manually).

`SECRET_MAP` was extended for OTLP (`otel/exporter-endpoint`,
`otel/exporter-headers`); `locke.json` documents all paths.

## 6. Idempotency & safety

- Re-running any provision is a no-op for already-present resources (`exists`).
- `--dry-run` (CLI) and `apply=False` (MCP, the default) mutate nothing.
- `--no-vault` does the backend work but skips credential storage.
- First failed step stops the run; the partial `ProvisionResult` shows exactly
  where it stopped and why.

## 7. MCP tools

`provision_project` (one-shot, just `project`), plus `provision_elk`,
`provision_rustrak`, `provision_otel` in `src/ojiichan_mcp/server.py`. **Dry-run by
default** (`apply=False`); set `apply=True` to mutate. Return
`ProvisionResult.to_dict()` (a list under `provision_project`).

## 8. End-to-end example

With the platform admin connection seeded in Vault (see §1), the whole flow is:

```bash
uv run --with "ojiichan[vault]" ojiichan provision all --project m3rp-api --dry-run  # inspect plan
uv run --with "ojiichan[vault]" ojiichan provision all --project m3rp-api            # create access + write Vault
ojiichan health --json                          # now resolves everything from Vault

# Or isolate staging and production completely:
ojiichan provision all --project m3rp-api --env staging
ojiichan provision all --project m3rp-api --env production
```

Without Vault seeding, pass the admin connection once via env or flags:

```bash
export OPENSEARCH_ADMIN_URL=https://opensearch.pseekoo.io:9200
export OPENSEARCH_ADMIN_PASSWORD=...            # obachan-managed admin
ojiichan provision all --project m3rp-api \
    --endpoint https://otel.pseekoo.io \
    --dsn "https://<key>@ingest.rustrak.pseekoo.io/42" --project-id 42
```

## 9. Acceptance criteria

- [x] `provision all` onboards a project from just `--project`; admin conn + OTLP
      resolve from `--flag`→env→Vault; missing backends skip (not fail).
- [x] Optional `--env` isolates backend resources and project-scoped credentials
      under the effective `<project>-<env>` name.
- [x] `provision elk|rustrak|otel` parse and dispatch; `--dry-run` mutates nothing.
- [x] ELK resources match obachan's configmap names/shapes; all idempotent PUTs.
- [x] Vault write-path stores project creds at project-scoped `SECRET_MAP` paths;
      password not desynced on re-run.
- [x] `--no-vault` and missing-`vault`-extra both degrade gracefully.
- [x] MCP `provision_*` default to dry-run.
- [x] `ruff check .` clean; `pytest tests/test_provision.py` green.

## 10. TODO(agent) — remaining work

1. **Rustrak admin API response validation**: project creation now defaults to
   Rustrak's `POST /api/projects`; confirm all live response variants for DSN and
   token fields, then tighten `_create_project`. Register-only mode remains the
   safest fallback when an installation differs.
2. **OpenSearch reachability preflight**: optional `doctor`-style check that the
   admin URL/creds work before walking steps (clearer error than a mid-run 401).
3. **Rotation**: `--rotate` to force a new scoped password and re-store it.
4. **Tests against a live OpenSearch** in `tests/e2e/` (mirror obachan's e2e bash
   pattern) — current tests use `httpx.MockTransport`.
5. **Seed helper**: a small `obachan`-side step (or `ojiichan provision seed-admin`)
   that writes the platform admin connection to `elk/admin-*` so §1 setup is itself
   one command.
