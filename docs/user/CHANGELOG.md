# Changelog

Generated from public issues confirmed fixed and shipped. Newest first.

## 0.26.0
- [#24](https://github.com/crinaro/careers-plugins/issues/24) — A user-owned role recorded with a future action date and a sourced/backlog status is invisible on the 'decisions waiting on you' surface until its action date arrives
- [#23](https://github.com/crinaro/careers-plugins/issues/23) — record.py's create path leaves the caller to guess a valid record at every stage: --dry-run passes input the real write then refuses, rejection messages name no field, and the fields listing omits enum and type constraints
- [#22](https://github.com/crinaro/careers-plugins/issues/22) — The generated-dashboard publish model has a staleness window: the published view can lag committed state via two distinct mechanisms

## 0.25.0
- [#21](https://github.com/crinaro/careers-plugins/issues/21) — OPEN DESIGN QUESTION: two dashboard views present the same records through two different taxonomies instead of one lifecycle-state view
- [#20](https://github.com/crinaro/careers-plugins/issues/20) — Generated dashboard renders knowledge-base and call-preparation artifacts as filename strings, not their content
- [#19](https://github.com/crinaro/careers-plugins/issues/19) — No schema field represents a pursued opportunity's post-application play-sequence stage
- [#18](https://github.com/crinaro/careers-plugins/issues/18) — A skill mandates an unconditional per-turn re-claim of a session-scoped notification subscription whose delivery cannot be observed by the session
- [#17](https://github.com/crinaro/careers-plugins/issues/17) — The write API cannot CREATE a new record, and separately deadlocks when called while the run lock is held, together forcing direct hand-editing of the data store
- [#15](https://github.com/crinaro/careers-plugins/issues/15) — The browser automation cannot reach a social platform's secondary/message-requests inbox surface, reproducible across multiple runs
- [#14](https://github.com/crinaro/careers-plugins/issues/14) — A validation gate can never pass on a clean install: shipped design docs point at scripts present in no released version
- [#13](https://github.com/crinaro/careers-plugins/issues/13) — The weekly reconcile audit again mis-attributes replies and platform events to the wrong sibling outreach row for the same recipient
- [#12](https://github.com/crinaro/careers-plugins/issues/12) — The outbound-click guard's startup selftest fails to open its own transcript path, so every click is classified unresolvable and allowed through instead of blocked
