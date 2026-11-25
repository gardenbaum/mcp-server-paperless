---
id: PLMCP-100.1
title: 'Pick among matches a: objects, on both protocol eras'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-25T17:37:39Z'
updated_date: '2026-11-25T17:44:42Z'
labels:
  - tools
dependencies: []
documentation:
  - docs/vendor/fastmcp/docs/servers/elicitation.mdx
parent_task_id: PLMCP-100
priority: low
type: enhancement
ordinal: 112000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
First part of PLMCP-100: an ambiguous name costs a turn, and the assistant can pick the wrong object on a write. Design approved by the owner on 2026-11-24, all recommendations (file design-plmcp-100.md of the main agent): question dialog 'Gibst du die Entwürfe 81, 87, 88, 89, 90, 83 und 100 mit allen empfohlenen Optionen frei, samt Guide-Limit 2943 bis 3229, Anweisungsgrenze 1465 und Definitionsgrenze 1010?' (Do you approve the designs 81, 87, 88, 89, 90, 83 and 100 with all recommended options, including the guide limit 2943 to 3229, the instructions limit 1465 and the definition limit 1010?), answer 'Alle wie empfohlen' (all as recommended).
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 On a write with an ambiguous name of an object, a client that declares form elicitation lets the user pick from the titled matches, and the call goes on with the picked one on both protocol eras; the pick must be one of the offered matches
- [ ] #2 Reads, dry runs, clients without form elicitation, declined or cancelled dialogs, malformed answers and more than 20 matches keep the present error with its list, without running the tool again; only after a declined or closed dialog one sentence more says that the user did not pick
- [ ] #3 A pick never confirms: where a call needs both, both are asked; no dialog is shown once the call has written
- [ ] #4 Every write tool that takes names of objects asks, writes exactly what the call with the id writes after a pick, and nothing after a no
- [ ] #5 Tests cover pick, decline, cancel, a malformed answer, several ambiguous names in one call and no-handler on both protocol eras, over stdio and HTTP
- [ ] #6 No tool, argument, description, guide or instructions change; a decision records the rule
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
1. Probes P1 and P2 of the design, hosted, in a temporary E2E module.
2. Domain: Choice, Question, picked/picking, the written mark; Ambiguous carries a question.
3. pick() raises with the question and returns the picked match.
4. PaperlessClient and the fake mark writes.
5. Boundary: asks on Ambiguous for tools that are not read-only and not dry_run; state carries picks and the original error.
6. Registry-wide tests over every write tool, HTTP tests, E2E, decision, docs.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Probes (hosted, run 45666927531, part 1 of 6; temporary module deleted): P1: FastMCP's client gets oneOf and enumNames unchanged on 2026-07-28 and handshake; over HTTP 2025-11-25, 2025-06-18, 2025-03-26 and 2024-11-05 carry both unchanged and answers come back; 8 rounds work on both eras, 11 end in InputRequiredRoundsExceededError on 2026-07-28. So clients before 2025-11-25 get enum and enumNames (owner decision 5a).
P2 (3.5.1): tags, correspondents, document types, storage paths and saved views hold one exact name once per owner and once without owner, and any other case; custom fields, workflows, users and groups differ in case only. Two objects of one exact name therefore always differ by owner: owner decision 6, 'b if P2 shows that most doubles differ only by owner', takes effect, the title names the owner.
<!-- SECTION:NOTES:END -->
