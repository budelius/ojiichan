# Production verification through Ojiichan

Production verification must use supported service interfaces. It must not depend
on shell access to a workload, an in-pod helper, or direct `kubectl` access to the
production cluster.

## Why `kubectl` is not part of verification

A Kubernetes API check answers whether an operator's workstation can reach the
cluster control plane. It does not prove that a deployed service can emit events,
that Ojiichan can read them, or that the public application is healthy. It also
couples routine verification to privileged cluster credentials and network
routing.

Direct cluster access belongs to **Obachan platform troubleshooting**. Use it only
when the supported interfaces have identified a platform fault that requires an
operator to inspect the cluster. A local Kubernetes timeout is therefore not a
release-verification caveat when CI, Ojiichan, and the public service endpoint all
succeed.

## Required evidence

Use these boundaries for a production verification report:

1. **Delivery:** read the latest development and default-branch pipeline states
   from the CI provider API. Both must be successful.
2. **Observability:** call Ojiichan for the deployed project's ELK and Rustrak
   health. Report connectivity and recent unresolved issue count.
3. **Application availability:** call each public service `/health` endpoint and
   require its documented success status.
4. **Platform deployment:** read the latest Obachan deployment result through its
   supported CI/API surface. Record the deployed image identifier.

Do not add an in-pod Ojiichan invocation as a fifth check. It duplicates the
observability check while introducing privileged cluster access as a false
requirement.

## Ojiichan interfaces

For operators, the CLI aggregates Ojiichan backend and public application checks:

```bash
ojiichan verify --project <service> \
  --backend elk --backend rustrak \
  --url https://service.example/health --json
```

The command succeeds for `ok` and `warn` results and exits non-zero when any
backend or public endpoint fails. HTTP health URLs must return status 200.

For agents, use the local stdio MCP `verify` tool with `project`, `backends`, and
`public_urls`. Individual `elk_health`, `elk_logs`, `rustrak_health`,
`bugsink_health`, `scaleway_health`, and `scaleway_logs` calls also accept a
`project` argument. Each call scopes Vault credentials temporarily and restores
the process's prior project afterwards.

The Python library is also a supported programmatic interface. Ojiichan does
**not currently ship a hosted HTTP diagnostics API**; “use the Ojiichan API” means
use its CLI, MCP tools, or Python API instead of entering the workload.

## Investigating historical logs

Prefer `ojiichan logs --project <service>` and MCP `diagnostic_logs` for scoped
read credentials, exact time windows, direct/OTLP coverage and identifier filters.
See [DIAGNOSTIC_LOGS.md](DIAGNOSTIC_LOGS.md) for the read-identity migration,
explicit operator-admin opt-in, and daily-index retention behavior.

## Scope retained outside Ojiichan

CI pipeline and Obachan deployment status remain provider/platform API concerns
rather than being reimplemented inside Ojiichan. A hosted Ojiichan HTTP service
is unnecessary unless remote callers cannot run the CLI/MCP client with properly
scoped credentials.
