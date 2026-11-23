---
id: PLMCP-69
title: Show a short demo recording at the top of the README
status: Done
assignee:
  - '@claude'
created_date: '2026-11-23T11:37:58Z'
updated_date: '2026-11-23T15:33:39Z'
labels: []
dependencies: []
priority: medium
type: enhancement
ordinal: 56000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Visitors decide in seconds. A short recording at the top of the README shows what the server does. Owner approved a GIF with invented documents on 2026-11-23.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 The README shows a GIF of at most 30 seconds near the top, with alt text, that also renders on PyPI
- [x] #2 It shows answers of the real released tools against a paperless-ngx instance with invented documents only, and no personal data
- [x] #3 The recording is reproducible from a script or a documented recipe in the repository, and the file is small enough to load quickly (at most 3 MB)
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [x] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [x] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Red: tests in tests/quality/test_docs.py for the README image (raw URL on main before the first section, alt text that names what is scripted), its size (3 MB) and duration (30 s, read from the GIF frame delays).
2. docs/demo: an MCP client script that fills a throwaway paperless-ngx 3.4.2 with invented documents and then calls uvx mcp-server-paperless==0.3.2 live; a vhs tape and a pinned recorder image, as a compose project that includes the e2e stack; recipe just demo.
3. Scenes: search by names, find and file an inbox document, refuse and then confirm a final deletion. Only the questions are scripted; GIF, alt text and caption say so.
4. Record, inspect frames, embed in README, describe in docs/development.md; check sdist and wheel; just check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Red: the README/GIF tests failed before docs/demo existed. docs/demo holds the client script, the tape, the recorder image and the compose project; just demo records in containers only (no host tools). Result after the review fixes: docs/demo/demo.gif, 1200x700, 24.84 s, 281037 bytes, every scene inspected. Scripted: the questions, the yes, the tool calls and their arguments (no model). Real but shortened: the answers (only the fields shown, and the refusal without its hint to the assistant). The GIF header, alt text, caption and docs/development.md say scripted, invented and shortened; tests check the first three. uv build: no demo file in sdist or wheel. docs/demo/demo.py is linted but, outside scripts/, not type-checked and not in coverage: it is a recording script, proven by running it. vhs Wait did not end while recording, so the tape uses a fixed Sleep and the recorder fails unless the script reached its end.

Review fixes (tests red first): the recipe passes -p mcp-server-paperless-demo, so COMPOSE_PROJECT_NAME cannot point down --volumes at the e2e stack (recorded with COMPOSE_PROJECT_NAME=paperless-mcp-e2e and a leftover demo stack: e2e containers untouched, leftover removed). The Paperless version in the scenes comes from the server, the caption is tested against SERVER and the compose image, so a bump of either needs a new recording. Packages resolve as of UV_EXCLUDE_NEWER, for the script and the server. demo.gif is replaced only after a complete run. The limit is 500 kB (large-file hook), below the 3 MB of the acceptance criterion. Known limits in docs/development.md: old PyPI pages show the GIF of main, the recorder image stays behind.

Owner approved the GIF on 2026-11-23; the re-recording changes only the header text. Independent opus review and re-review: 0 Critical, 0 Important left. Known limits: a Paperless image bump fails the caption test until re-recorded, two concurrent just demo runs share one project, GIF ownership on Linux unverified, the GIF auto-plays. PyPI shows it with the next release. DoD 2 unchecked: one known macOS-only local failure.

Owner decision 2026-11-23: the caption is no longer tied to the Paperless of the live tests (deliberately loosened gate, so Renovate's Paperless updates need no new recording); it stays tied to SERVER, and the scene text may not hard-code a Paperless version.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A 25 s GIF (281 kB) at the top of the README shows a search, filing and a confirmed deletion with the real released tools and invented documents; scripting and shortening are disclosed. just demo records it in containers under its own project.
<!-- SECTION:FINAL_SUMMARY:END -->
