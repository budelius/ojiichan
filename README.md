# ojiichan

Reusable diagnostics toolkit for projects that report to **ELK** and **Bugsink/Sentry**.

It provides both:

- a Python library (`ojiichan`) for structured ELK events and Sentry/Bugsink setup;
- a CLI (`ojiichan`) for health checks, recent logs, and test event emission.

Extracted from the diagnostics patterns used in `M3RP/airtable-csv-importer` so the same
observability workflow can be reused across services like `stoz3n-chat-agent`.

## Install

```bash
uv sync
# or from another project, once packaged/published:
uv add /Users/budelius/Documents/Projects/dev-ops/ojiichan
```

## Environment

Conventional names are supported, with optional `OJIICHAN_` prefixed overrides.

```bash
ELASTICSEARCH_HOST=...
ELASTICSEARCH_PORT=443
ELASTICSEARCH_USERNAME=...
ELASTICSEARCH_PASSWORD=...
ELASTICSEARCH_USE_SSL=true
ELASTICSEARCH_VERIFY_CERTS=true
ELASTICSEARCH_INDEX_PREFIX=stoz3n-chat-agent

SENTRY_DSN=https://public@bugsink.example/123
BUGSINK_API_TOKEN=...
# optional if not derivable from DSN:
BUGSINK_HOST=bugsink.example
BUGSINK_PROJECT_ID=123
```

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
    {"operation": "matrix_exchange", "user_id": "@budelius:matrix.pseekoo.io"},
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

## Notes

- Bugsink ingestion uses the normal Sentry SDK via `SENTRY_DSN`.
- Bugsink issue reads use the canonical read API at `/api/canonical/0/issues/` and require `BUGSINK_API_TOKEN`.
- Beads integration is intentionally optional; `ojiichan.beads` shells out only when `bead`/`beads` is installed.
