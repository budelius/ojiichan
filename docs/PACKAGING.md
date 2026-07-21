# PACKAGING.md — crate + npm distribution (agent spec)

> Audience: a coding agent making ojiichan's TypeScript and Rust support a
> **published package** instead of copied scaffold files, analogous to
> `locke-public`. This is assessment + target design + status.
>
> **Status (2026-07-14):** Python `0.9.7` is the only published package. The npm
> and crate sections below remain a design contract; do not advertise those
> packages as available until their acceptance criteria are complete.

## 1. Current state (assessment)

| Language | How it ships today | Gap |
| --- | --- | --- |
| **Python** | Published: `ojiichan` on PyPI + GitLab registry (`hatchling`, `.gitlab-ci.yml`). | None — this is the reference. |
| **TypeScript** | `ojiichan scaffold --lang typescript` copies `src/lib/*.ts` into the target repo. | Not a package: no `npm add`, no versioning, fixes don't propagate. |
| **Rust** | `ojiichan scaffold --lang rust` copies `src/observability.rs`. | Same — copy-only. |
| **Swift** | scaffold copies `Sources/Observability/*.swift`. | Out of scope here (SwiftPM is a separate track). |

So today only Python is a real dependency; TS/Rust siblings carry **forked
copies** of the observability client. `locke-public` solves exactly this by
publishing all three languages from one repo.

## 2. Target (analogous to locke-public)

Publish the observability client as real libraries so a sibling service does:

```bash
npm add @martin-wieser/ojiichan      # TypeScript/Node
cargo add ojiichan                    # Rust
```

and gets `initSentry` / `captureException` / `logEvent` / `flushSentry` +
`ELK_TRANSPORT` support with the **same env-resolution order** as the Python lib —
no copied files. The existing `scaffold` command stays for the
already-has-diagnostics integration path, but the default becomes "add the
package".

### Distribution model — **thin packages in one repo** (recommended)

Keep ojiichan a single repo (don't fork into a full polyglot monorepo yet). Add:

```
ojiichan/
  src/ojiichan/ ...            # Python (unchanged, stays at repo root)
  clients/
    typescript/
      package.json             # @martin-wieser/ojiichan
      tsconfig.json
      src/index.ts             # promoted from scaffolds/typescript/src/lib/*
      src/{config,sentry,elk,handler-scaleway}.ts
    rust/
      Cargo.toml               # crate: ojiichan (or ojiichan-observability)
      src/lib.rs               # promoted from scaffolds/rust/src/observability.rs
```

The scaffold **templates remain the source of truth** for the wiring; the package
sources are the same code promoted to a published surface. (Follow-up: generate
the package sources from the templates so they can't drift — see TODO.)

This mirrors `locke-public`'s layout (`python/`, `typescript/`, `rust/` each with
their own build) without forcing the heavier parity-test monorepo now.

## 3. Package metadata (copy-pasteable, modeled on locke-public)

### `clients/typescript/package.json`
```json
{
  "name": "@martin-wieser/ojiichan",
  "version": "0.9.7",
  "description": "Env-driven ELK + Rustrak/Sentry observability client (TypeScript)",
  "license": "MIT",
  "type": "module",
  "main": "dist/index.js",
  "types": "dist/index.d.ts",
  "files": ["dist", "README.md"],
  "publishConfig": { "access": "public" },
  "repository": { "type": "git", "url": "https://gitlab.com/martin-wieser/ojiichan.git" },
  "keywords": ["observability", "elk", "opensearch", "sentry", "rustrak", "opentelemetry"],
  "scripts": { "build": "tsc", "test": "vitest run", "prepublishOnly": "tsc" },
  "dependencies": { "@sentry/node": "^8.0.0" },
  "devDependencies": { "@types/node": "^20.0.0", "typescript": "^5.4.0", "vitest": "^2.0.0" },
  "engines": { "node": ">=18.0.0" }
}
```

### `clients/rust/Cargo.toml`
```toml
[package]
name = "ojiichan"                  # verify availability on crates.io; locke fell
                                   # back to "locke-creds" when "locke" was taken.
version = "0.9.7"
edition = "2021"
rust-version = "1.75"
description = "Env-driven ELK + Rustrak/Sentry observability client"
license = "MIT"
repository = "https://gitlab.com/martin-wieser/ojiichan"

[lib]
name = "ojiichan"
path = "src/lib.rs"

[dependencies]
sentry = "0.34"
reqwest = { version = "0.12", features = ["json", "blocking"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

Keep all three versions **in lockstep** with `pyproject.toml` (locke bumps them in
one commit; see its `scripts/release.sh`).

## 4. CI — OIDC Trusted Publishing (lift from locke-public)

ojiichan's `.gitlab-ci.yml` already publishes Python via `twine`. Add two
tag-triggered jobs copied from `locke-public/.gitlab-ci.yml` (lines ~66–115):

- **npm** — `id_tokens.NPM_ID_TOKEN` (audience `npm:registry.npmjs.org`), npm CLI
  ≥ 11.5.1, `npm publish --access public --ignore-scripts`. One-time:
  npmjs.com → package → Trusted Publisher → GitLab CI/CD.
- **crates.io** — `id_tokens.CRATES_IO_ID_TOKEN` (audience `crates.io`), exchange
  the JWT for a short-lived publish token, `cargo publish -p ojiichan`. One-time:
  crates.io → crate → Trusted Publishing → GitLab.

No stored registry tokens. Gate both on `CI_COMMIT_TAG =~ /^v\d+\.\d+\.\d+/`, same
as the Python job.

## 5. API surface parity (the contract siblings rely on)

All three packages expose the same four functions + transport switch, matching the
Python lib and the `OBSERVABILITY.md` the scaffold already documents:

- `initSentry(...)` / `init_sentry(...)` — Sentry/Rustrak init from env.
- `captureException(...)` / `capture_exception(...)`.
- `logEvent(...)` / `log_event(...)` — structured ELK event; honors
  `ELK_TRANSPORT` = `es` | `logstash` | `otlp`.
- `flushSentry()` / `flush_sentry()` — **flush-before-freeze** for short-lived
  runtimes (Scaleway/Lambda); the most commonly missing piece.
- Env resolution order identical to Python: `OJIICHAN_`-prefixed → conventional →
  default.

## 6. Acceptance criteria

- [ ] `clients/typescript` builds (`tsc`) and `npm pack` produces a tarball with
      `dist/` + types.
- [ ] `clients/rust` builds (`cargo build`) and `cargo publish --dry-run` passes.
- [ ] Versions in `package.json`, `Cargo.toml`, `pyproject.toml` match.
- [ ] CI publishes both on `v*` tags via OIDC (no stored tokens).
- [ ] A sample sibling service uses `npm add @martin-wieser/ojiichan` /
      `cargo add ojiichan` and emits one ELK event + one captured error.
- [ ] `scaffold` docs updated to present "add the package" as the default and
      file-copy as the fallback.

## 7. TODO(agent)

1. **Verify names**: check `ojiichan` on crates.io and the npm scope; fall back to
   `ojiichan-observability` / a different scope if taken (mirror locke's
   `locke-creds` decision).
2. **Promote templates → package sources** for TS and Rust, then **generate the
   package sources from the scaffold templates** in CI so the published package and
   the scaffold output can't drift (single source of truth).
3. **Add the CI jobs** from §4 and a `scripts/release.sh` that bumps all three
   versions in one commit (copy locke's).
4. **Add `scaffold --mode package`** that, instead of copying files, adds the
   dependency and writes a minimal wiring file importing from the package.
5. Decide if/when to graduate to a full `locke`-style polyglot monorepo with
   cross-language parity tests (only if the clients grow non-trivial logic).
