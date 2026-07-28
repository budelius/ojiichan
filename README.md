# ojiichan

Pseekoo diagnostics toolkit for projects that report to **ELK**, **Bugsink/Sentry/Rustrak**, and **Scaleway Cockpit**.

It provides both:

- a Python library (`ojiichan`) for structured ELK events and Sentry/Bugsink setup;
- a CLI (`ojiichan`) for health checks, recent logs, and test event emission.

It standardizes the same observability workflow across Python services — and reads
platform runtime logs (Scaleway Serverless Functions stdout/stderr) alongside
application events, so one tool covers app-level *and* infra-level diagnostics.

Sibling services in other languages can't import the Python library, so ojiichan
also **scaffolds** env-driven TypeScript, Rust, and Swift equivalents (`ojiichan
scaffold --lang typescript|rust|swift`) that report to the same ELK +
Rustrak/Sentry stack — including first-class iOS/macOS (Swift) support.

## Install

```bash
uv add ojiichan
# with Locke/Vaultwarden credential support:
uv add "ojiichan[vault]"
# or for development:
uv sync
```

## Configuration and credentials

Ojiichan resolves explicit environment variables first, then Locke/Vaultwarden secrets when installed with the `vault` extra. The checked-in `locke.json` documents the expected Vaultwarden paths; no sample env file with secret-shaped values is needed.

Default vault folder: `ojiichan`.
Override it with `OJIICHAN_VAULT_FOLDER` when needed. Set
`OJIICHAN_VAULT_PROJECT=<service>` in a service to read project-scoped
credentials from `ojiichan/projects/<service>/...` first, falling back to shared
platform credentials. `ojiichan provision --project <service>` writes generated
service creds to that scoped namespace. Use `OJIICHAN_HEALTH_BACKENDS` (or the
project vault key `health/backends`) to choose which health checks run for that
service, e.g. `elk,rustrak` or `elk,scaleway,rustrak`. Scaleway is opt-in so a
local operator vault cannot accidentally add Cockpit warnings to an unrelated
project.

Provision can also write those generated creds to OpenBao/Vault KV v2. Configure
`OPENBAO_ADDR` + `OPENBAO_TOKEN` (or `VAULT_ADDR` + `VAULT_TOKEN`) and optionally
`OPENBAO_KV_MOUNT` (default `secret`). `OJIICHAN_VAULT_WRITE_TARGETS` can be
`locke`, `openbao`, or `locke,openbao`; by default OpenBao is added when its env
is present.

To minimise Vaultwarden rate-limit pressure, all known secrets are warmed in one `list_secrets` call per process and snapshotted to a `0600` file under `${XDG_CACHE_HOME:-~/.cache}/ojiichan/`. Knobs:

- `OJIICHAN_VAULT_CACHE_TTL` — seconds (default `300`, set `0` to disable disk cache only).
- `OJIICHAN_VAULT_CACHE_DISABLE` — any non-empty value disables the on-disk snapshot entirely.
- `OJIICHAN_VAULT_CACHE_PATH` — override the cache file path.

Non-secret options can still be set as env vars, for example `ELASTICSEARCH_PORT`, `ELASTICSEARCH_USE_SSL`, `ELASTICSEARCH_VERIFY_CERTS`, `ELASTICSEARCH_TIMEOUT`, and `BUGSINK_TIMEOUT`.

### ELK write transports

By default ojiichan indexes structured events straight into Elasticsearch. Set
`ELK_TRANSPORT` to route them elsewhere instead — the document shape is identical
across transports, so events stay queryable together regardless of how they were
written.

| `ELK_TRANSPORT` | Sink | Required config |
| --- | --- | --- |
| `es` (default) | `POST <host>/<prefix>-<topic>/_doc` straight to Elasticsearch | `ELASTICSEARCH_HOST` (+ auth) |
| `logstash` | `POST` the document (plus an `index` field) to a Logstash HTTP input | `LOGSTASH_URL` |
| `otlp` | `POST` an OpenTelemetry LogRecord to `<endpoint>/v1/logs` (usually a Collector) | `OTEL_EXPORTER_OTLP_ENDPOINT` (+ `OTEL_EXPORTER_OTLP_HEADERS`) |

For `logstash`, route the `index` field in your pipeline with
`elasticsearch { index => "%{index}" }`. For `otlp`, the standard
`OTEL_EXPORTER_OTLP_*` env vars are honored. The same switch and env vars exist
in the scaffolded TypeScript/Rust/Swift services.

### Obachan transport contract

Do not mix a project-scoped OpenSearch writer with Obachan's shared Data Prepper
listener:

- **Recommended for external/mobile apps:** `ELK_TRANSPORT=es`, a writer created
  by `ojiichan provision elk --project <name>`, and
  `ELASTICSEARCH_HOST=https://os.<deployment-apex>`.
- **Shared ingest:** `logstash` and `otlp` authenticate to Data Prepper as the
  shared `ojiichan` user and use the platform's `app-*` indices. A
  `<project>-writer` credential will receive `401` because Data Prepper has one
  source identity and does not forward caller credentials to OpenSearch.

Production shared-ingest clients use the stable
`https://ingest.pseekoo.io` alias; the deployment-specific
`https://ingest.<deployment-apex>` endpoint remains supported. Always run
`ojiichan provision otel --probe ...` before shipping an OTLP endpoint.

> **Reads stay on Elasticsearch.** `ELK_TRANSPORT` only changes the *write* path.
> `ojiichan health` and `ojiichan logs` read from Elasticsearch no matter which
> transport wrote the events, so those still need `ELASTICSEARCH_HOST`.

## Production verification

Use Ojiichan through its CLI, MCP tools, or Python API for production diagnostics;
do not enter application pods or require `kubectl` for routine verification.
Direct cluster access is reserved for Obachan platform troubleshooting after a
supported interface has identified a platform fault.

A release check should combine CI-provider status, Ojiichan ELK/Rustrak health,
the application's public `/health` endpoint, and the Obachan deployment result.
A local Kubernetes control-plane timeout does not invalidate those checks. See
[`docs/OPERATIONS.md`](docs/OPERATIONS.md) for the exact boundary and commands.

## CLI

```bash
ojiichan verify --project facts-production --backend elk --backend rustrak \
  --url https://facts.stozn.ai/health --json
ojiichan health --hours 6
ojiichan health --project halp                  # uses halp's project-scoped vault config
ojiichan health --project halp --backend elk --backend rustrak
ojiichan dashboard --limit 10
ojiichan dashboard --project-id 42 --json
ojiichan logs --project halp --hours 2 --topic matrix --failures-only
ojiichan emit matrix_exchange_failed --topic auth --level error --failure \
  --data '{"operation":"matrix_exchange","room_id":"!abc"}'
ojiichan airtable-log replace-endpoint --base app123 --table Services \
  --old https://old.example --new https://new.example --records 12
ojiichan resolve-issue 7b3a... --backend rustrak --project-id 42
ojiichan resolve-issue 98765 --backend sentry

# Scaffold observability setup for a TypeScript, Rust, or Swift service
ojiichan scaffold --lang typescript --backend rustrak --dir ../m3rp-api
ojiichan scaffold --lang rust --dir ../some-rust-service
ojiichan scaffold --lang swift --dir ../some-ios-app    # iOS/macOS, server-side Swift, CLI

# Scaleway Cockpit (Loki) runtime logs — e.g. a Serverless Function's stdout/stderr
ojiichan logs --backend scaleway --hours 2 --failures-only
ojiichan logs --backend scaleway --source m3rp-api-staging-api --topic "Login failed"
```

### Airtable maintenance events

`ojiichan airtable-log` records Airtable maintenance in the normal structured
ELK event stream. It emits an `airtable_maintenance` event with the operation,
base, table, affected record count, and optional old/new values and note.

```bash
ojiichan airtable-log replace-endpoint \
  --base app123 --table Services \
  --old https://old.example --new https://new.example \
  --records 12 --service maintenance-worker --json
```

Use `--failure` and `--level error` for failed maintenance. `--dry-run` marks the
recorded operation as a dry run; the audit event itself is still emitted.

### Project-scoped health

`ojiichan health` defaults to `elk,issues`. Pass `--project <service>` to resolve
`ojiichan/projects/<service>/...` first, including the optional
`health/backends` setting. Use `OJIICHAN_HEALTH_BACKENDS` or repeat `--backend`
to select exactly which checks run. Scaleway is never auto-added from local
operator credentials; choose `scaleway` only for services that actually ship to
Scaleway Cockpit.

Examples:

```bash
# project vault key: ojiichan/projects/halp/health/backends = elk,rustrak
ojiichan health --project halp

# explicit one-off selection
ojiichan health --project halp --backend elk --backend rustrak
ojiichan health --project worker-faas --backend scaleway --backend rustrak
```

### Error dashboard

`ojiichan dashboard` summarizes Rustrak error stats across projects. It lists
total/open/resolved/muted error counts, stored/digested event totals from the
Rustrak project list, and per-project rows sorted by currently open errors.

Use `--project-id` to restrict the report to one or more project IDs, `--limit`
to trim text output, and `--json` for the full machine-readable payload.

### Scaleway Cockpit logs

`--backend scaleway` reads logs from a project's Scaleway Cockpit via the Loki
HTTP API. `--source` maps to the Loki `resource_name` label (a function name);
`--topic` is a substring line filter; `--failures-only` keeps error/exception/5xx
lines. `ojiichan health` reports a Scaleway section only when selected via
`--backend scaleway` or the project `OJIICHAN_HEALTH_BACKENDS` / `health/backends`
setting.

One-time setup (store the two secrets in the vault under `ojiichan/scaleway/…`,
or export `OJIICHAN_SCALEWAY_LOKI_URL` / `OJIICHAN_SCALEWAY_COCKPIT_TOKEN`):

```bash
scw cockpit data-source list region=fr-par          # copy the type=logs "url"
scw cockpit token create name=ojiichan region=fr-par token-scopes.query_logs=true
```

## Provisioning (create a project's access on the obachan cluster)

`ojiichan provision` **creates a service's access** on the cluster `obachan`
already runs (OpenSearch, the OTLP pipeline, Rustrak) and — when the `vault` extra
is installed — writes the resulting credentials to Vault so the service and its
scaffolded siblings resolve them automatically. It only *creates access*; it never
installs the platform and never tears anything down.

The low-interaction path is one command — pass just the project name and the admin
connection + OTLP endpoint resolve from Vault (seeded once by obachan/operator):

```bash
ojiichan provision all --project m3rp-api --dry-run    # plan (mutates nothing)
ojiichan provision all --project m3rp-api              # create access + write creds to Vault
# Validate an externally reachable shared OTLP endpoint before rollout:
ojiichan provision otel --project m3rp-api \
  --endpoint https://ingest.pseekoo.io --headers "$OTLP_HEADERS" --probe

# Optional: isolate backend resources and Vault credentials by deployment environment
ojiichan provision all --project m3rp-api --env staging
ojiichan provision all --project m3rp-api --env production
```

That runs the per-backend steps below; each backend with no usable config is
skipped with a note rather than failing. Run them individually for control:

```bash
ojiichan provision elk --project m3rp-api          # index template + ISM + role + user + mapping
ojiichan provision rustrak --project m3rp-api --dsn "https://<key>@ingest.rustrak.pseekoo.io/42"
ojiichan provision otel --project m3rp-api --endpoint https://otel.pseekoo.io --probe
```

With `--env <name>`, the effective project is `<project>-<env>`, producing a
separate Rustrak project, ELK index prefix, and
`ojiichan/projects/<project>-<env>/` Vault scope. Omit it to share one project
across environments.

Admin creds resolve `--flag` → env (`OPENSEARCH_ADMIN_URL` /
`OPENSEARCH_ADMIN_PASSWORD`) → Vault (`elk/admin-*`). Every step is idempotent
(`check` then create-or-replace), so re-running reports `exists`. Use `--no-vault`
to skip credential storage, `--json` for machine output. Full contract:
[`docs/PROVISIONING.md`](docs/PROVISIONING.md). Agents: start at
[`AGENTS.md`](AGENTS.md).

## Scaffolding TypeScript / Rust / Swift services

ojiichan's Python library can't run inside a Node, Rust, or Swift service, so
`ojiichan scaffold` writes env-driven equivalents that report to the same
backends — Sentry/Rustrak error reporting plus ELK structured events.

```bash
ojiichan scaffold --lang typescript --backend rustrak --dir ./service
ojiichan scaffold --lang rust --dir ./service          # add --force to overwrite
ojiichan scaffold --lang swift --dir ./ios-app         # iOS/macOS, server-side Swift, CLI
```

Generated files (existing files are skipped unless `--force`):

- **TypeScript** — `src/lib/config.ts` (env resolution), `src/lib/sentry.ts`
  (`initSentry` / `captureException` / `flushSentry`), `src/lib/elk.ts`
  (`logEvent`), `src/handler-scaleway.ts` (a `withObservability` wrapper that
  flushes before a Serverless Function freezes), and `OBSERVABILITY.md`.
- **Rust** — `src/observability.rs` (`init_sentry` / `capture_error` /
  `log_event`) and `OBSERVABILITY.md`.
- **Swift** — `Sources/Observability/Config.swift` (env resolution),
  `Sources/Observability/Sentry.swift` (`initSentry` / `captureException` /
  `flushSentry` via sentry-cocoa), `Sources/Observability/Elk.swift` (`logEvent`
  over `URLSession`, `async`), and `OBSERVABILITY.md`. Works in iOS/macOS apps,
  server-side Swift, and CLI tools.

Every value is read from the environment, preferring an `OJIICHAN_`-prefixed
override then the conventional name (`SENTRY_DSN`, `ELASTICSEARCH_HOST`, …), so
the scaffolded service shares the Python toolkit's resolution order. The
generated `OBSERVABILITY.md` lists the required packages (`@sentry/node`;
`sentry` + `reqwest` crates; the `sentry-cocoa` SwiftPM package) and wiring
steps. The `--backend` flag only adjusts the documentation references — the SDK
wiring is identical because Rustrak is Sentry-compatible.

### Integrating into a service that already has diagnostics (for agents)

If the target repo already ships its own observability module (e.g. a
hand-written `server/diagnostics.ts`), **do not** dump the scaffold over it —
that duplicates functionality and fights the repo's structure (`server/` vs
`src/lib/`, an Elasticsearch client vs `fetch`, etc.). Instead:

1. **Scaffold to a throwaway dir** to get the canonical reference:
   `ojiichan scaffold --lang typescript --backend rustrak --dir /tmp/oji`.
2. **Diff each concern** (config resolution, `initSentry`, `captureException`,
   `logEvent`, the Scaleway wrapper) against the repo's existing module.
3. **Port only the missing pieces**, keeping the repo's conventions. The piece
   most often missing is the **flush-before-freeze** step: short-lived runtimes
   (Scaleway/Lambda Serverless Functions) freeze the process the instant the
   handler returns, dropping any buffered Sentry event. The fix is the
   `withObservability` shape from `src/handler-scaleway.ts` — wrap the exported
   handler so it `captureException`s on throw and **always** `await flushSentry()`
   in a `finally`. Keep the module's public surface compatible with this
   standard so `initSentry / captureException / logEvent / flushSentry` mean the
   same thing across services.

Where to look: the rendered `OBSERVABILITY.md` (wiring + env table), and the
templates under `src/ojiichan/scaffolds/typescript/` in this repo for the
reference implementation.

`ojiichan: key=value` diagnostic annotations (below) are recognized in `//`,
`///`, `//!`, and `/* … */` comments too, so the hints work in TypeScript,
Rust, and Swift source.

## Library

```python
from ojiichan import ElkClient, init_sentry

init_sentry(service="stoz3n-chat-agent", environment="development")

elk = ElkClient(service="stoz3n-chat-agent")
elk.log_event(
    "matrix_exchange_start",
    {"operation": "matrix_exchange", "user_id": "@user:example.org"},
    topic="auth",
)
```

For exception paths:

```python
try:
    ...
except Exception as exc:
    elk.log_event(
        "matrix_exchange_failed",
        {"operation": "matrix_exchange", "error": str(exc)},
        level="error",
        topic="auth",
        failure=True,
    )
    raise
```

## Issue resolution

Ojiichan resolves issues through **Rustrak** or **Sentry**. Bugsink is supported
for *ingestion and issue reads* but does not support resolution from ojiichan;
calling `resolve_issue` on Bugsink returns a friendly "not supported" message
that points users at Rustrak/Sentry instead.

```python
from ojiichan import RustrakClient, SentryApiClient

RustrakClient().resolve_issue("7b3a-…-uuid", project_id=42)
SentryApiClient().resolve_issue("98765")
```

Projects pick their default backend by exporting `OJIICHAN_ISSUE_BACKEND`
(`rustrak`, `sentry`, or `bugsink`). The CLI/`--backend auto` honors that
choice; with no preference it prefers Rustrak then falls back to Sentry.

Credentials:

- Rustrak: `RUSTRAK_API_TOKEN`; optionally `RUSTRAK_BASE_URL` (the **API**
  host, defaults to `https://ingest.rustrak.pseekoo.io`; the dashboard host is
  the same domain without the `ingest.` prefix and is auto-derived for triage
  links) and `RUSTRAK_PROJECT_ID` for the default project scope.
- Sentry: `SENTRY_AUTH_TOKEN`; optionally `SENTRY_BASE_URL` for self-hosted
  Sentry-compatible APIs.
- Bugsink (ingestion/reads only): `BUGSINK_API_TOKEN` plus `SENTRY_DSN` or
  `BUGSINK_HOST`.

These values are resolved through explicit env vars first, then Locke/Vaultwarden when `ojiichan[vault]` is installed.

## Diagnostics DSL for agents

Annotate code paths so coding agents know where to look for errors:

```python
from ojiichan import diagnostic, hint

@diagnostic(hint(
    operation="matrix_exchange",
    topic="auth",
    failure=True,
    issue_backend="bugsink",
    runbook="docs/runbooks/matrix-auth.md",
))
def exchange_matrix_token(...):
    ...
```

Or use a nearby comment that agents can parse/read:

```python
# ojiichan: operation=matrix_exchange topic=auth failure=true issue_backend=bugsink tags=matrix,auth
```

The comment form is recognized with Python/shell (`#`), C/Rust/TS (`//`, `///`, `//!`), and block/JSDoc (`/* … */`, `*`) markers, so the same annotation works across Python, TypeScript, Rust, and Swift source.

Hints line up with ELK fields (`operation`, `topic`, `failure`) and issue tooling (`issue_backend`, `issue_query`, `runbook`) so an agent can jump from code to logs/issues quickly.

### Extended `ojiichan:doc` blocks

For code paths that need more than a one-line hint, the extended **doc** form is
an agent-first markdown block: a short summary plus the *known failure modes* and
*where to look first*, so an agent landing on a function can read the symptoms and
the exact ELK/issue queries to run. It is a backward-compatible superset of the
compact line — the same scalar keys, plus `summary` and the `## errors` /
`## checks` sections:

```python
# ojiichan:doc operation=matrix_exchange topic=auth failure=true issue_backend=rustrak
# summary: Exchange a Matrix login token for an access token.
# ## errors
# - 401 invalid_token → token expired; re-run the exchange
# - timeout → homeserver slow; check Scaleway logs source=matrix-api
# ## checks
# 1. ELK: topic=auth operation=matrix_exchange failure=true
# 2. Rustrak issues tagged matrix,auth
# runbook: docs/runbooks/matrix-auth.md
```

It parses with the same comment markers, so the block works in Python, TypeScript,
Rust, and Swift source. In Python:

```python
from ojiichan import parse_any, parse_doc

doc = parse_doc(block_text)          # → DiagnosticDoc(summary=…, errors=(…), checks=(…))
doc.to_hint()                        # downcast to a DiagnosticHint for log_event(data=…)
doc.to_markdown()                    # agent-facing render (round-trips back through parse_doc)

parse_any(text)                      # auto-detects: DiagnosticDoc for a doc block, else DiagnosticHint
```

The MCP `parse_diagnostic_annotation` tool understands both forms; for a doc block
it returns the parsed `errors`/`checks` lists and a `markdown` render alongside the
scalar `fields`.

## MCP server

Ojiichan also ships a local stdio MCP server so coding agents can query diagnostics and run repeatable test/build checks without hardcoding shell snippets.

Run it manually:

```bash
uv run ojiichan-mcp
```

Example MCP client config:

```json
{
  "mcpServers": {
    "ojiichan": {
      "command": "uv",
      "args": [
        "run",
        "--with",
        "ojiichan[vault]",
        "ojiichan-mcp"
      ]
    }
  }
}
```

Exposed tools:

- `verify` (aggregate backend checks + public HTTP health; accepts `project`)
- `elk_health` (accepts `project`)
- `elk_logs` (accepts `project`)
- `emit_elk_event`
- `bugsink_health` (accepts `project`)
- `bugsink_issues`
- `parse_diagnostic_annotation`
- `bugsink_resolve_issue` (returns "not supported" message)
- `sentry_resolve_issue`
- `rustrak_resolve_issue`
- `rustrak_health` (accepts `project`)
- `rustrak_dashboard` (aggregate Rustrak error totals and per-project stats)
- `rustrak_issues` (supports `filter`: `open`, `resolved`, `muted`, `all`)
- `rustrak_issue_reference` (copy/share URL, `rustrak://` ref, and Markdown link)
- `scaleway_health` / `scaleway_logs` (accept `project`)
- `scaffold_observability` (renders TypeScript/Rust setup files)
- `provision_project` (one-shot: all backends from just a project name; dry-run by default)
- `provision_elk` / `provision_rustrak` / `provision_otel` (dry-run by default; `apply=True` mutates + writes Vault)
- `create_bead_from_issue`
- `xcode_build`
- `xcode_test`

For Xcode projects, an agent can call `xcode_test` with a local `project_path`, `scheme`, and `destination`.

## Release

See [`CHANGELOG.md`](CHANGELOG.md) for release notes. Publishing is handled by
GitLab CI in the public `martin-wieser/ojiichan` repository:

- pushes to the default branch publish a PEP 440 dev build to the GitLab PyPI registry;
- tags matching `vX.Y.Z` publish the release to the GitLab PyPI registry and to pypi.org;
- tagged releases require `PYPI_API_TOKEN` in GitLab CI variables.

Before releasing, update `CHANGELOG.md`, `pyproject.toml`, and `uv.lock`, then run:

```bash
uv run ruff check .
uv run pytest
rm -rf dist && uv build
uvx twine check dist/*
VERSION=$(python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
git tag -a "v${VERSION}" -m "release: ${VERSION}"
git push origin main
git push origin "v${VERSION}"
```

The tag pipeline is the authoritative publisher. Verify the resulting version on
[PyPI](https://pypi.org/project/ojiichan/) after the pipeline succeeds.

## Notes

- Bugsink ingestion uses the normal Sentry SDK via `SENTRY_DSN`.
- Bugsink issue reads use the canonical read API at `/api/canonical/0/issues/` and require `BUGSINK_API_TOKEN`.
- Issue resolution uses Sentry's `/api/0/issues/{issue_id}/` PUT API and Rustrak's `/api/projects/{project_id}/issues/{issue_id}` PATCH API. Bugsink resolution is intentionally disabled.
- Beads integration is intentionally optional; `ojiichan.beads` shells out only when `bead`/`beads` is installed.
- The public distribution and import package are both `ojiichan`.
