# ojiichan

Pseekoo diagnostics toolkit for projects that report to **ELK** and **Bugsink/Sentry/Rustrak**.

It provides both:

- a Python library (`ojiichan`) for structured ELK events and Sentry/Bugsink setup;
- a CLI (`ojiichan`) for health checks, recent logs, and test event emission.

It standardizes the same observability workflow across Python services.

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
Override it with `OJIICHAN_VAULT_FOLDER` when needed.

Non-secret options can still be set as env vars, for example `ELASTICSEARCH_PORT`, `ELASTICSEARCH_USE_SSL`, `ELASTICSEARCH_VERIFY_CERTS`, `ELASTICSEARCH_TIMEOUT`, and `BUGSINK_TIMEOUT`.

## CLI

```bash
ojiichan health --hours 6
ojiichan logs --hours 2 --topic matrix --failures-only
ojiichan emit matrix_exchange_failed --topic auth --level error --failure \
  --data '{"operation":"matrix_exchange","room_id":"!abc"}'
ojiichan resolve-issue 7b3a... --backend rustrak --project-id 42
ojiichan resolve-issue 98765 --backend sentry
```

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

Hints line up with ELK fields (`operation`, `topic`, `failure`) and issue tooling (`issue_backend`, `issue_query`, `runbook`) so an agent can jump from code to logs/issues quickly.

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

- `elk_health`
- `elk_logs`
- `emit_elk_event`
- `bugsink_health`
- `bugsink_issues`
- `parse_diagnostic_annotation`
- `bugsink_resolve_issue` (returns "not supported" message)
- `sentry_resolve_issue`
- `rustrak_resolve_issue`
- `rustrak_health`
- `rustrak_issues`
- `create_bead_from_issue`
- `xcode_build`
- `xcode_test`

For Xcode projects, an agent can call `xcode_test` with a local `project_path`, `scheme`, and `destination`.

## Release

Publishing is handled by GitLab CI in the public `martin-wieser/ojiichan` repository:

- pushes to the default branch publish a PEP 440 dev build to the GitLab PyPI registry;
- tags matching `vX.Y.Z` publish the release to the GitLab PyPI registry and to pypi.org;
- tagged releases require `PYPI_API_TOKEN` in GitLab CI variables.

To release the current version:

```bash
VERSION=$(python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
git tag "v${VERSION}"
git push origin main --tags
```

## Notes

- Bugsink ingestion uses the normal Sentry SDK via `SENTRY_DSN`.
- Bugsink issue reads use the canonical read API at `/api/canonical/0/issues/` and require `BUGSINK_API_TOKEN`.
- Issue resolution uses Sentry's `/api/0/issues/{issue_id}/` PUT API and Rustrak's `/api/projects/{project_id}/issues/{issue_id}` PATCH API. Bugsink resolution is intentionally disabled.
- Beads integration is intentionally optional; `ojiichan.beads` shells out only when `bead`/`beads` is installed.
- The public distribution and import package are both `ojiichan`.
