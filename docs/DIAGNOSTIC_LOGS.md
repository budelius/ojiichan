# Reliable diagnostic log reads

Use `ojiichan logs --backend elk --project <project>` or MCP `diagnostic_logs`.
The legacy Python `ElkClient.query_logs` / MCP `elk_logs` interfaces retain their
old behavior; prefer the diagnostic API for investigations.

```bash
ojiichan logs --project stoz3n-ios-chat --read-as-admin \
  --since 2026-10-11T09:25:00Z --until 2026-10-11T09:35:00Z \
  --room-id '!example:matrix.example' --json
```

- Explicit project is required. Direct indices are pinned to `<project>-*`, not
  an unrelated prefix found in shared writer configuration.
- Both direct (`@timestamp`) and OTLP (`time`) storage are queried by default.
  Shared `app-otlp-*` queries filter by exact service name, defaulting to project.
  Use repeatable `--service` for explicit alternate OTLP service names, or
  `--storage direct|otlp`. This is a query filter, not an authorization boundary:
  the server must grant the reader appropriately scoped access.
- `--since` and `--until` accept timezone-aware ISO-8601 dates. Without `--since`,
  `--hours` is measured backwards from `--until` (or now). Boundaries are inclusive.
- `--room-id`, `--user-hash`, `--correlation-id` are exact identifier filters,
  not query-string expressions. `--topic` matches the direct index topic prefix
  (including old undated indices); OTLP requires a topic attribute. Older OTLP
  emitters without that attribute cannot be filtered by topic reliably.
- Results are merged newest-first and capped at `--limit` (1–1000); `total` is
  the total number of matching documents, not the number returned. Dual-shipped
  documents are not deduplicated. Returned records retain their source/index.
- Exact-window/identifier options are rejected for other backends, not silently ignored.

## Read credentials are separate from write credentials

Set `OJIICHAN_ELASTICSEARCH_READ_HOST`, `_READ_USERNAME`, `_READ_PASSWORD`
(or unprefixed equivalents). Vault keys are `elk/read-host`, `elk/read-username`,
`elk/read-password`, under `ojiichan/projects/<project>/`, with the normal shared
read-key fallback. Configure a read-capable identity; the existing provisioner
still provisions writers, not a new reader automatically.

For an authorized operator, `--read-as-admin` explicitly selects existing
`OPENSEARCH_ADMIN_URL/USER/PASSWORD` (Vault `ojiichan/elk/admin-*`). Admin access
is never an automatic fallback. No credential values appear in diagnostics.
These operator credentials must never be distributed in mobile apps.

Authentication, authorization, transport, timeout and failed-shard errors return
`query_failed` and nonzero CLI exit status, not a zero-match success. Backend
exception bodies are suppressed because they can contain credentials or URLs.

## Coverage is evidence, not a retention guarantee

Responses include each storage family's oldest/newest available project event:

- `matches`: documents found.
- `no_matches`: no matching documents; inspect bounds and gaps before concluding anything.
- `missing_historical_coverage`: requested start precedes available project history.
- `no_available_logs`: neither selected storage family has events for this scope.

Bounds are not proof of continuous logging, client opt-in, delivery, or retention
configuration. A missing record cannot establish that an action never happened.

Read-only production verification on 2026-10-15 found Stozn's earliest available
project event at 2026-10-12T17:15:42Z. Its room was created a day earlier. The new
CLI correctly reports missing historical coverage for that investigation.

## Index-age retention and daily writes

The live `stoz3n-ios-chat-logs-retention` policy deletes an **entire index** at
30 days of index age. Undated, continually reused indices therefore lose even
recent documents when the index expires. This is a confirmed configuration
hazard, not proof of what deleted any particular historical event.

Python and newly generated Swift/TypeScript/Rust emitters now write direct and
Logstash events to `<prefix>-<topic>-YYYY-MM-DD` in UTC and include `topic` in
payloads. Existing `<prefix>-*` templates/roles/policies cover these names.
OTLP still uses the platform's daily index routing. Regenerate/integrate client
scaffolds and deploy consumers to adopt this; existing installed emitters do not
change automatically. Old indices are not migrated or deleted by this change.

No live policy was modified. Daily buckets make index-age retention meaningful
at approximately daily granularity; they are not per-document exact-TTL storage.
