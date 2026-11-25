---
id: PLMCP-81.3
title: 'Mail c: delete_processed_mail'
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T22:44:56Z'
updated_date: '2026-11-25T16:13:29Z'
labels:
  - mail
dependencies:
  - PLMCP-81.1
  - PLMCP-81.2
parent_task_id: PLMCP-81
priority: medium
type: feature
ordinal: 101000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Third part of PLMCP-81: records of processed mail cannot be deleted, so a failed mail is never fetched again. Design approved by the owner on 2026-11-24 (decisions 1 to 12 as recommended); file design-plmcp-81.md of the main agent.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 delete_processed_mail deletes 1 to 100 records after a preview and a confirmation that names each mail, what is fetched again and when, and what the rule then does
- [x] #2 Only a second read says a record is deleted; a missing id refuses the call
- [ ] #3 Contract, tool and E2E tests with the IMAP server cover the refetch, partial and unknown outcomes and a restricted user
<!-- AC:END -->

## Definition of Done
<!-- DOD:BEGIN -->
- [ ] #1 Tests written first (red, then green) at every test level the change touches
- [ ] #2 just check passes locally and all required CI checks are green on the PR
- [x] #3 Snapshot, gate or decision changes are deliberate and explained in the PR description
- [x] #4 Merged to main through the task's own pull request
<!-- DOD:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Port MailWriter.delete_processed_mail: adapter (one POST, not repeated, its echo not believed), fake (T1, 403 for all); adapter tests, contract on fake and live with rows made in Paperless itself. 2. services/processed_mail.py: read each id, rules, accounts, siblings; refusals; effect grouped by what follows; scope; read back one by one (one_by_one of decision-73, 40 s). 3. Tool with dry_run and confirm; tables of confirmation, destinations, registry, arguments, budgets. 4. Live tests with the mail server: partial, foreign, refetch after the tool. 5. decision-83, coverage row, docs, guide 2964 to 2988, measured dialog of 100.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Owner's words (2026-11-24): 'Alle wie empfohlen' in the question 'Gibst du die Entwürfe 81, 87, 88, 89, 90, 83 und 100 mit allen empfohlenen Optionen frei, samt Guide-Limit 2943 bis 3229, Anweisungsgrenze 1465 und Definitionsgrenze 1010?' (here decisions 9a, 10, 11a). Dialog of 100 records measured, with its hint: 11,023 characters ordinary (one rule), 17,123 with a staying record each, 19,810 with long subjects and rule name, 81,027 with 100 rules that move to a 250-character folder each, hostile 54,195 (one rule) to 220,327 (100 rules, every text 250 escapes). What follows is said once per kind of outcome. WORST (5,388, one record) holds. No cap built: put to the owner. A security review asked whether bulk_delete deletes before it meets a forbidden record; it checks all first (source, and a live contract test with an older permitted record).

After the review of 81.1 other records are always looked for; dialog of 100 measured again: 11,054 ordinary, 16,887 with a staying record each, 19,841 long subjects, 81,058 with 100 rules and folders, hostile 54,226 to 220,091 characters.

Review round 1 (independent), tests first: the sentence for PROCESSED_WO_CONSUMPTION of rules that import the mail itself, the UIDVALIDITY caveat always, hidden rules, texts softened, rights before the long reads, vanished pages, doubled rows, INBOX in any case, tests for the survivors (each request checks the time, every account is bound). Dialog of 100 measured: 11,330 ordinary; 17,830 with a staying record each; 20,117 long subjects; not hostile 132,122 (100 rules, SUCCESS, move to long folders) and 171,922 (also staying records and other folders); hostile 307,322. No bound built (with the owner).

Review round 2, tests first: when the rule acts follows from what it takes (eml_and_attachments: one half is enough), other records by the rule's folder, UIDVALIDITY of a server that names none. Dialog of 100 measured: 11,341 ordinary, 17,920 with a staying record each, 20,128 long subjects; not hostile 130,252 and 170,052; hostile 305,452. No bound built (with the owner).

Confirmations above 100,000 characters (whole text with its hint) are refused, also with confirm=true, never cut; the preview stays whole and says so (owner, 2026-11-25, question dialog: "Ja, ablehnen (Recommended)", yes, refuse). Tests first. MAX_CONFIRMATION moved to domain/errors.py. Snapshot and tool text adapted.

Cleanup 2026-11-25, merged as b0ca817 (#158). AC 3 stays open. Proven: the refetch (E2E with the mail server), a restricted user (contract, E2E), an id that is none (contract on fake and 3.5.1), all records refused (contract, E2E). Missing: partial outcomes of delete_processed_mail (some records deleted, some still there) and unknown outcomes (lost answer, records that cannot be read again) in a tool or E2E test; they are tested on service level only (tests/services/test_processed_mail.py), no contract, tool or E2E test has an unknown outcome. CI 45630847344 and full E2E 45630847369 of main 1efb5cd are green. DoD 1 stays open: the PR records red runs for adapter to tool level, not for the live contract and E2E tests. DoD 2 stays open: no local run of the whole just check is recorded. On GitHub the check jobs run its recipes one by one: 45602324190 (4816fe3).

Read literally (2026-11-25), AC 3 misses: contract tests of the refetch and of unknown outcomes; tool tests of the refetch, partial and unknown outcomes and a restricted user; E2E with the IMAP server of partial and unknown outcomes. This replaces the list above.

Owner, 2026-11-26: "Alles wie von dir empfohlen." (everything as you recommend): a record that cannot be read after a deletion counts as deleted only if that can be shown (Paperless answered OK, or the user is a superuser before and after), otherwise unknown; decision-107 supersedes that point of decision-83. Tests first.
<!-- SECTION:NOTES:END -->
