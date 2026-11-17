---
id: PLMCP-32
title: 'Toolset raw: read-only paperless_api_get'
status: Done
assignee:
  - '@claude'
created_date: '2026-11-09T19:26:00Z'
updated_date: '2026-11-17T00:47:00Z'
labels: []
milestone: m-4
dependencies:
  - PLMCP-15
documentation:
  - backlog/docs/doc-1 - paperless-mcp-design.md
type: feature
ordinal: 32000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
An escape hatch for reading anything the curated tools do not cover, so the user can do everything possible. It must not become a way to write or to reach other hosts (design §6, anti-goals).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 paperless_api_get(path, params) performs GET only, for relative paths under /api/; absolute URLs, scheme or host changes and path traversal are rejected
- [x] #2 Responses are size-capped and truncation is reported
- [x] #3 The toolset is off by default
- [x] #4 Adapter tests are schema-guarded and E2E covers the main flow on seeded data
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Decision (new, via CLI; body written in the file): raw toolset rules. Allow-list paths: relative, /api/ only, ASCII [A-Za-z0-9_-] segments, no percent-encoding, no dot/empty segments, no query/fragment/@/backslash/control chars; GET only; secret endpoints (profile, config, mail_accounts, token, oauth) and binary endpoints (download, preview, thumb, bulk_download) denied; secret-looking JSON keys redacted; non-JSON bodies refused; size cap with truncation reported; redirects never followed.
2. Domain: RawReader port (path, params, max_bytes) -> RawReply; RawApiResponse tool model.
3. Client: stream_get keeping a capped prefix (status, content type, truncated).
4. Adapter PaperlessRawReader: builds URL only through the client, re-asserts base host and /api/ prefix, refuses 3xx.
5. Service RawApiService: path/param validation table, deny list, redaction, JSON parse, truncation.
6. Tool paperless_api_get in toolset raw (off by default), read-only annotations; registry, snapshot, visibility, README, settings docs.
7. Tests red first at each level: service table of rejected paths plus hypothesis property, adapter (schema guard, redirects, host), contract on fake and live, tool, registry/visibility, e2e on 3.4.2.
8. Local gates: just check, mutation of changed modules, raw e2e only; independent opus review; rebase, squash, finalize.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Decisions: decision-25 records the rules (allow-list path, denied secret and file endpoints, JSON only, redaction by key, 1 MiB read limit, 20 000 character output cap, same-origin guard in the client, redirects never followed, off by default; all selects the toolset). Live finding: 3.4.2 answers an unknown /api path and a path without trailing slash with a redirect, which the tool reports as an error. Tests written first (red as import errors for the service; red failures for client, adapter, tool): all levels. Not red first: the e2e test file and the exact-wording mutation tests (written after the survivors were seen, they were green on first run by design). Mutation: raw modules and the changed client functions (_build, download) have 0 unreviewed survivors; equivalents recorded in mutation-equivalents.toml (typing.cast, ensure_ascii=None, case-insensitive headers/method); two existing client entries renumbered because _build gained a line. No independent opus reviewer subagent could be spawned from this session (no Agent tool); a self-review with extra attack paths was done instead.

DoD #1 left unchecked by the controller: tests/e2e/test_raw.py and the wording and limit tests added after mutation were not run red first.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Adds the opt-in raw toolset with paperless_api_get(path, params): GET only, relative paths under /api/ matched by an allow-list (no encoding, dots, backslashes, @, query, control characters), secret endpoints (profile, config, mail_accounts, token, oauth) and file endpoints (download, preview, thumb, bulk_download) denied, JSON only with secret keys redacted, 1 MiB read limit and 20 000 character output cap with truncated reported, same-origin guard in the client and no redirects followed. Rules in decision-25. Verified with just check (100% diff coverage), mutation of the changed modules, and the raw contract and e2e tests on live 3.4.2 (22 passed). DoD #2 (CI checks) and #4 (merge) are not true yet; no independent reviewer ran.
<!-- SECTION:FINAL_SUMMARY:END -->
