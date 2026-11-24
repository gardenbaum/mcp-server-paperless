---
id: PLMCP-104
title: Upload and say so when the trash is too large to check
status: To Do
assignee: []
created_date: '2026-11-24T07:06:13Z'
labels: []
dependencies: []
priority: high
ordinal: 79000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Above 64 MiB of trash the duplicate check fails and nothing can be uploaded (decision-47). The owner decided on 2026-11-24 ('bitte wie von dir empfohlen'): upload, and say that the trash was not checked, as above 500 entries. Outages, timeouts and every other failure still refuse.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 An answer over the byte limit is a named gap in not_checked for upload_document, upload_document_version and upload_documents; nothing else of the rule changes
- [ ] #2 A new decision supersedes exactly that part of decision-47
- [ ] #3 Service, adapter, contract and tool tests cover the limit crossed, and every other failure still refuses
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [ ] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [ ] #4 Merged to main through the task's own pull request
<!-- DOD:END -->
