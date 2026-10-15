# Diagnostic log retrieval and room creation tracing

GSD quick context: 261015-i5n (initiated from stoz3n-ios-pending-recordings).

Implement on feature branches, without disturbing unrelated iOS work:
1. Add explicit read credentials/operator-admin opt-in, exact UTC windows, scoped direct + OTLP queries, identifiers and coverage diagnostics. Never silently treat query/auth errors as zero matches or fall back to another project's writer/index.
2. Test CLI/backend with synthetic responses and read-only live production verification; inspect retention without changing it.
3. Add privacy-safe room creation lifecycle instrumentation in an isolated current-dev iOS worktree; record originating action, correlation, invite count, returned room ID, outcome. No text/tokens or raw account IDs.
4. Document validation, limits, and release state. Missing historical events cannot be recovered by this change. No automatic app deployment requested.
