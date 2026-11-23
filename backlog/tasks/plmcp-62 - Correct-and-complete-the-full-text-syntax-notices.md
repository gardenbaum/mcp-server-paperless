---
id: PLMCP-62
title: Correct and complete the full-text syntax notices
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-23T08:40:16Z'
updated_date: '2026-11-23T08:53:08Z'
labels: []
dependencies: []
type: bug
ordinal: 53000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
PLMCP-58 shipped in 0.3.0 with a wrong notice: it says field:* matches nothing, but on Paperless 3.5.1 type:* and correspondent:* match every document that has a value. The probe behind PLMCP-58 misread NOT type:* = 0 on an instance where every document has a type. Live probes (272 documents) also show silent misreads without a notice: nosuchfield:foo 0, NOT nosuchfield:foo 272, !swisscom equals swisscom, || behaves like &&. Two review findings of PLMCP-58 were deferred without owner approval (only the first match is named; a lone lowercase not in prose is advised as NOT). The owner approved fixing all of it on 2026-11-23, and approved the reworded acceptance criterion 2 of PLMCP-56.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 No notice and no tool description claims that field:* matches nothing where Paperless supports it
- [ ] #2 A field the search index does not know, a leading !, and the operators && and || give a notice with the working alternative
- [ ] #3 Every offending term is named, not only the first
- [ ] #4 A lone lowercase not in prose is not advised as an operator it cannot be
- [ ] #5 Claims are proven by live e2e tests on documents with and without the value
- [ ] #6 Docs and README state the corrected behaviour
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
1. Evidence from the parser in the 3.4.2 container (whoosh_compat AST): field:* is Every(field), unknown or wrongly cased fields, !, && and || are plain terms. 2. Failing unit, service, tool and e2e tests. 3. Rewrite query_notices; drop the presence notice. 4. Correct the query parameter, the docstring and the notices description; regenerate the surface snapshot deliberately. 5. README comparison, gates, independent review, Codex on the merge commit.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Reworked after the independent opus review of #77/#81: detection moved to services/search_syntax.py (token based, linear), also covers mixed-case operators, NOT(, NOT-x, dangling operators, a detached field value, Unicode dashes; amounts (CHF -50) and NOT -x stay silent. Presence notice removed: field:* is valid (parser AST in the 3.4.2 container, live e2e with and without a value). Surface snapshot regenerated deliberately (query, docstring, notices). README comparison rewritten from competitor sources. Not covered: field:"*", unbalanced inch marks (5" and 6").
<!-- SECTION:NOTES:END -->
