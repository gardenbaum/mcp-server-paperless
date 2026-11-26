---
id: decision-115
title: The image workflow builds and scans main daily, with packages of the day
date: '2026-11-26T15:20:58Z'
status: accepted
---

## Context

<!-- SECTION:CONTEXT:BEGIN -->
decision-28 (carried over by decision-40) scans the image on main and on image-related pull requests, "not on a schedule", and leaves the package upgrade to `apt-get upgrade` in the Dockerfile. The build cache of the workflow kept that layer, so on 2026-11-26 `libzstd1` stayed at a version Debian had fixed (CVE-2026-124851, HIGH) and the scan failed. Owner, 2026-11-26: "Bitte image aktualisieren und sicherstellen, dass es stets aktuell bleibt." (Please update the image and make sure it always stays current.)
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
**Supersedes of decision-28 only the words "not on a schedule" and how fresh the packages are:**

- Every build of the image workflow passes the day (UTC) as `APT_REFRESH`, which the package upgrade uses: the upgrade is at most a day old, also in a tag build.
- The image workflow runs daily on main (`schedule`): it builds, scans and smoke-tests; it publishes nothing.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- A fix Debian ships reaches the build cache within a day, and a new finding shows up within a day even when nothing is merged.
- A tag build rebuilds the upgrade layer instead of reusing the one scanned on main; its pushed digest is scanned again before it gets a version tag (decision-28).
- The published image gets new packages only with a new release.
<!-- SECTION:CONSEQUENCES:END -->

## Enforcement

- `tests/quality/test_container.py`: the upgrade uses `APT_REFRESH`, every build passes the day, the workflow has the daily schedule.
