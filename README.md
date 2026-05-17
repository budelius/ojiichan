# ojiichan

Pseekoo diagnostics toolkit for projects that report to **ELK** and **Bugsink/Sentry**.

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
- `create_bead_from_issue`
- `xcode_build`
- `xcode_test`

For Xcode projects, an agent can call `xcode_test` with a local `project_path`, `scheme`, and `destination`.

## Release

Publishing is handled by GitLab CI in the public `pseekoo/ojiichan` repository:

- pushes to the default branch publish a PEP 440 dev build to the GitLab PyPI registry;
- tags matching `vX.Y.Z` publish the release to the GitLab PyPI registry and to pypi.org;
- tagged releases require `PYPI_API_TOKEN` in GitLab CI variables.

To release the current version:

```bash
git tag v0.1.0
git push origin main --tags
```

## Notes

- Bugsink ingestion uses the normal Sentry SDK via `SENTRY_DSN`.
- Bugsink issue reads use the canonical read API at `/api/canonical/0/issues/` and require `BUGSINK_API_TOKEN`.
- Beads integration is intentionally optional; `ojiichan.beads` shells out only when `bead`/`beads` is installed.
- The public distribution and import package are both `ojiichan`; the GitLab repository can live under the `pseekoo` group without changing Python names.
