---
id: PLMCP-69
title: Show a short demo recording at the top of the README
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T11:37:58Z'
updated_date: '2026-11-23T11:57:07Z'
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
- [ ] #1 The README shows a GIF of at most 30 seconds near the top, with alt text, that also renders on PyPI
- [ ] #2 It shows answers of the real released tools against a paperless-ngx instance with invented documents only, and no personal data
- [ ] #3 The recording is reproducible from a script or a documented recipe in the repository, and the file is small enough to load quickly (at most 3 MB)
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
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
Red: the two README/GIF tests failed before docs/demo existed. docs/demo holds the client script, the tape, the recorder image and the compose project; just demo records in containers only (no host tools). Result: docs/demo/demo.gif, 1200x700, 24.96 s, 278526 bytes, frames inspected. Only the questions and the tool calls are scripted (no model); GIF header, alt text, caption and docs/development.md say so; answers are shortened to the fields shown and the confirmation hint to the assistant is left out. uv build: no demo file in sdist or wheel. docs/demo/demo.py is linted but, outside scripts/, not type-checked and not in coverage: it is a recording script, proven by running it. vhs Wait did not end while recording, so the tape uses a fixed Sleep and the recorder fails unless the script reached its end.
<!-- SECTION:NOTES:END -->
