# AGENTS.md — ojiichan for coding agents

This file is the entry point for AI agents working in or with **ojiichan**. It
states what ojiichan is, where it sits relative to the sibling tools, and points
to the two design contracts an agent implements against.

## What ojiichan is

ojiichan is the **per-service** diagnostics toolkit for the Pseekoo stack. A
service uses it to:

- emit structured events to **ELK** (OpenSearch), with `es` / `logstash` / `otlp`
  write transports;
- report errors to **Rustrak** (Sentry-compatible) / Sentry / Bugsink;
- read **Scaleway Cockpit** runtime logs;
- **scaffold** env-driven TypeScript / Rust / Swift equivalents;
- **provision** its own slice of the platform and write the resulting creds to
  Vault (see [`docs/PROVISIONING.md`](docs/PROVISIONING.md)).

It is a library (`ojiichan`), a CLI (`ojiichan`), and a local stdio **MCP server**
(`ojiichan-mcp`) so agents can drive it without hardcoding shell.

## Where it sits (the three tools)

| Tool | Scope | Owns |
| --- | --- | --- |
| **obachan** | platform / cluster | Installs OpenSearch, the OTLP ingestion pipeline (Data Prepper/Collector), the Rustrak deployment, OpenBao/Vault + ESO — **once per environment**. |
| **ojiichan** (this repo) | per-service | Onboards one service onto that platform: app events, error reporting, and **project-scoped provisioning** (create this project's OpenSearch user/role/template, Rustrak DSN, OTLP wiring) + writes creds to Vault. |
| **locke** | credentials | Resolves/stores secrets in Vaultwarden + OS keystore. ojiichan reads via the `vault` extra and **writes** via `locke`'s `VaultClient.set_secret`. |

Rule of thumb: **platform-wide install → obachan; per-project onboarding →
ojiichan.** For Obachan writes, keep the credential contracts separate:
project-scoped writers are for direct `es`; shared Data Prepper `logstash`/`otlp`
uses the `ojiichan` ingest identity and `app-*` indices. Production's stable
shared endpoint is `https://ingest.pseekoo.io`; always provision explicitly and
probe external endpoints before rollout.

Ojiichan only **creates a project's access** on the already-running
obachan cluster (OpenSearch users/roles/templates, a Rustrak project, OTLP
wiring). It never installs the platform and never tears anything down (no
deprovision/revert). The headline is one command:

```bash
ojiichan provision all --project <name>     # admin conn + OTLP endpoint resolve from Vault
```

## The two contracts to implement against

1. **[`docs/PROVISIONING.md`](docs/PROVISIONING.md)** — the `ojiichan provision
   elk|rustrak|otel` command family, the predicate-driven step engine, the
   idempotency contract (mirrored from obachan's `opensearch-bootstrap`), and the
   Vault **write-path** (`when vault is set, create all creds`).
2. **[`docs/PACKAGING.md`](docs/PACKAGING.md)** — shipping a real **crate** and
   **npm** package (analogous to `locke-public`) so sibling services depend on a
   published library instead of copying scaffold files.

## Conventions

- **Python 3.11+**, `uv` for envs (`uv sync`, `uv run ...`), `hatchling` build,
  `ruff` lint (`line-length = 100`), `pytest`.
- **Config resolution order** (`config.py::env`): `OJIICHAN_`-prefixed env →
  conventional env (`ELASTICSEARCH_HOST`, …) → Locke/Vaultwarden → default.
- **Secrets** map env-var → vault path via `credentials.SECRET_MAP`. Anything the
  read-path resolves, the write-path (`provision.vault_write`) stores at the
  **same** path. Keep them in sync.
- **Idempotency**: every provision step is `check` (GET) then `apply`
  (create-or-replace PUT/POST). Re-running reports `exists`, never churns.
- **Safety**: `provision --dry-run` plans without mutating; MCP `provision_*`
  tools default to dry-run (`apply=False`).
- No marketing language, no fake stubs, feature branches for changes, run
  `ruff check .` + `uv run pytest` before claiming done.

## Quick map

```
src/ojiichan/
  cli.py            # argparse CLI: health logs emit resolve-issue scaffold provision
  config.py         # ElkConfig/RustrakConfig/... from_env, resolution order
  credentials.py    # READ-path: SECRET_MAP + Locke/Vaultwarden resolve (+ disk cache)
  elk.py rustrak.py scaleway.py bugsink.py sentry.py transports.py
  scaffold.py scaffolds/   # TS/Rust/Swift client templates
  provision/        # NEW — project-scoped provisioning
    base.py         #   predicate step engine (ProvisionStep / run_steps)
    vault_write.py  #   WRITE-path: locke set_secret at SECRET_MAP paths
    elk.py rustrak.py otel.py
src/ojiichan_mcp/server.py   # MCP tools incl. provision_elk/rustrak/otel (dry-run default)
docs/PROVISIONING.md docs/PACKAGING.md
```
