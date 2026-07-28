# Changelog

All notable changes to ojiichan are documented here. Versions follow semantic
versioning; release dates use ISO 8601.

## [Unreleased]

## [0.11.0] - 2026-07-28

### Added

- Add `ojiichan verify` and the MCP `verify` tool to aggregate observability
  backend checks with public HTTP 200 health checks.
- Add project-scoped credential selection to CLI logs and MCP health/log tools.

### Documentation

- Define API-first production verification and remove `kubectl`/in-pod helpers
  from the routine release acceptance boundary.
- Document the aggregate verification workflow and supported Ojiichan interfaces.

## [0.10.1] - 2026-07-24

### Fixed

- Clearly separate project-scoped direct OpenSearch writers from Obachan's shared
  Data Prepper `logstash` and OTLP ingest credentials.
- Warn when an OTLP endpoint is stored without a `--probe` reachability check.

### Documentation

- Document the Obachan ingest credential boundary and stable production endpoint
  in the README, provisioning contract, agent guidance, and all scaffold docs.

## [0.10.0] - 2026-07-22

### Added

- Add `ojiichan airtable-log` for structured Airtable maintenance audit events.
- Add `--env` to provisioning commands for independent `<project>-<env>` Rustrak,
  ELK, and Vault resources.

### Fixed

- Use Rustrak's `api/projects` project-create path by default.

### Documentation

- Document Airtable maintenance events and environment-scoped provisioning.

## [0.9.7] - 2026-07-14

### Documentation

- Add this changelog and link it from the README and package metadata.
- Clarify the tested tag-based release procedure and current polyglot packaging status.

## [0.9.6] - 2026-07-14

### Fixed

- Map Rustrak project configuration through the project-scoped Vault read/write paths.

## [0.9.5] - 2026-07-12

### Documentation

- Document project-scoped health backend selection.

## [0.9.4] - 2026-07-12

### Fixed

- Select health backends per project and keep Scaleway checks opt-in.

## [0.9.3] - 2026-07-10

### Added

- Write provisioned service credentials to OpenBao/Vault KV v2 as well as Locke.

### Fixed

- Preserve explicit environment-variable precedence over warmed Vault snapshots.

## [0.9.2] - 2026-07-09

### Fixed

- Store generated provisioning credentials under project-scoped Vault paths.

## [0.9.1] - 2026-07-08

### Added

- Expose Rustrak issue filters and shareable issue references.

## [0.9.0] - 2026-07-03

### Added

- Support private CA bundles through `ELASTICSEARCH_CA_CERT` across ELK reads,
  write transports, and provisioning clients.
- Add the obachan/OpenSearch logs backend and CLI routing.

## [0.8.0] - 2026-06-29

### Added

- Add idempotent `provision all|elk|rustrak|otel` project onboarding.
- Add project-scoped Vault credential writes and dry-run-by-default MCP tools.
- Add provisioning and polyglot packaging design contracts.

## [0.7.0] - 2026-06-26

### Added

- Use `opensearch-py` for OpenSearch compatibility.
- Add `es`, `logstash`, and OTLP structured-event write transports.

## [0.6.0] - 2026-06-19

### Added

- Add the Swift/iOS/macOS observability scaffold.

## [0.5.1] - 2026-06-18

### Documentation

- Document how agents integrate the TypeScript scaffold into existing diagnostics code.

## [0.5.0] - 2026-06-18

### Added

- Add TypeScript and Rust observability scaffolds and MCP scaffolding support.
- Parse diagnostic annotations in C-style and block comments.

## [0.4.0] - 2026-06-06

### Added

- Add Scaleway Cockpit/Loki runtime log querying and health checks.
- Cache bulk Vault reads to reduce Vaultwarden rate-limit pressure.

## [0.3.0] - 2026-06-06

### Changed

- Move ELK and Rustrak credentials into shared Vault sub-namespaces.

## [0.2.1] - 2026-05-27

### Fixed

- Route issue health and reads to Rustrak when configured and correct its API host.

## [0.2.0] - 2026-05-27

### Added

- Add Rustrak issue resolution, diagnostics annotations, and issue tooling.

## [0.1.1] - 2026-05-18

### Changed

- Publish with public GitLab repository metadata.

## [0.1.0] - 2026-05-17

### Added

- Initial public Python library, CLI, MCP server, ELK integration, and
  Sentry/Bugsink issue workflows.

[Unreleased]: https://gitlab.com/martin-wieser/ojiichan/-/compare/v0.11.0...main
[0.11.0]: https://gitlab.com/martin-wieser/ojiichan/-/compare/v0.10.1...v0.11.0
[0.10.1]: https://gitlab.com/martin-wieser/ojiichan/-/compare/v0.10.0...v0.10.1
[0.10.0]: https://gitlab.com/martin-wieser/ojiichan/-/compare/v0.9.7...v0.10.0
[0.9.7]: https://gitlab.com/martin-wieser/ojiichan/-/compare/v0.9.6...v0.9.7
[0.9.6]: https://gitlab.com/martin-wieser/ojiichan/-/compare/v0.9.5...v0.9.6
[0.9.5]: https://gitlab.com/martin-wieser/ojiichan/-/compare/v0.9.4...v0.9.5
[0.9.4]: https://gitlab.com/martin-wieser/ojiichan/-/compare/v0.9.3...v0.9.4
[0.9.3]: https://gitlab.com/martin-wieser/ojiichan/-/compare/v0.9.2...v0.9.3
[0.9.2]: https://gitlab.com/martin-wieser/ojiichan/-/compare/v0.9.1...v0.9.2
[0.9.1]: https://gitlab.com/martin-wieser/ojiichan/-/compare/v0.9.0...v0.9.1
[0.9.0]: https://gitlab.com/martin-wieser/ojiichan/-/compare/v0.8.0...v0.9.0
[0.8.0]: https://gitlab.com/martin-wieser/ojiichan/-/compare/v0.7.0...v0.8.0
[0.7.0]: https://gitlab.com/martin-wieser/ojiichan/-/compare/v0.6.0...v0.7.0
[0.6.0]: https://gitlab.com/martin-wieser/ojiichan/-/compare/v0.5.1...v0.6.0
[0.5.1]: https://gitlab.com/martin-wieser/ojiichan/-/compare/v0.5.0...v0.5.1
[0.5.0]: https://gitlab.com/martin-wieser/ojiichan/-/compare/v0.4.0...v0.5.0
[0.4.0]: https://gitlab.com/martin-wieser/ojiichan/-/compare/v0.3.0...v0.4.0
[0.3.0]: https://gitlab.com/martin-wieser/ojiichan/-/compare/v0.2.1...v0.3.0
[0.2.1]: https://gitlab.com/martin-wieser/ojiichan/-/compare/v0.2.0...v0.2.1
[0.2.0]: https://gitlab.com/martin-wieser/ojiichan/-/compare/v0.1.1...v0.2.0
[0.1.1]: https://gitlab.com/martin-wieser/ojiichan/-/compare/v0.1.0...v0.1.1
[0.1.0]: https://gitlab.com/martin-wieser/ojiichan/-/tags/v0.1.0
