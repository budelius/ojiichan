# Implementation and verification

Implemented diagnostic read API, CLI and MCP tool with explicit project/read identity,
UTC windows, identifier filters, direct + OTLP schema handling, coverage bounds and
safe nonzero errors. Normal writers are not used for reads; admin access is opt-in.
Legacy Python/MCP read interfaces remain available; migration documented.

Read-only live checks against the production project confirmed earliest available
event 2026-10-12T17:15:42Z, no correlated room event from the prior day, and a whole-
index 30-day deletion policy on undated continually reused indices. The latter is
a retention hazard, not proof of a specific historical deletion. No live retention
policy was changed. Python and generated Swift/TS/Rust direct/Logstash emitters now
use daily UTC names. Existing indices are not migrated or deleted.

Validation: ruff check . passed; full pytest suite 132 passed. Tests isolated from
operator Vault credentials after two baseline tests were found to read real local
credentials. Production historical query now returns missing_historical_coverage.

Companion iOS branch fix/room-creation-diagnostics in stoz3n-ios-room-diagnostics
adds privacy-safe lifecycle events and daily app/NSE indices. Free and Pro each
passed 17 focused tests (payload privacy, correlation/action capture, UTC rollover,
and existing DirectMessageResolver coverage); final Pro rerun also passed17.

Release state: feature branches, not merged into app dev; no mobile/Mac deployment.
New installed client versions are required for future room events/daily writes.
No room, account, or message was modified during verification.
