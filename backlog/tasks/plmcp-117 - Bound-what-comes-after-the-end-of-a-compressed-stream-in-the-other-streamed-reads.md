---
id: PLMCP-117
title: >-
  Bound what comes after the end of a compressed stream in the other streamed
  reads
status: In Progress
assignee:
  - '@claude'
created_date: '2026-11-24T21:58:47Z'
updated_date: '2026-11-25T18:19:37Z'
labels:
  - safety
dependencies: []
priority: low
type: bug
ordinal: 98000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
httpx2's gzip and deflate decoders keep data after the end of the stream and copy it with every piece, and do not count empty blocks (PLMCP-111: 67 MB on the wire took 2.6 GB in a JSON read). list_trash and the error bodies of bounded JSON reads are bounded since PLMCP-111; streamed downloads (download, stream, stream_post) and their error bodies still use those decoders. Read in the code, not probed.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 Streamed downloads and the error bodies of every streamed read take bounded memory whatever follows the end of a compressed stream, shown by the memory of a process
- [ ] #2 Files that Paperless sends, also compressed, are read as before
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
1. Red: memory of a child process for download, stream and stream_post (gzip, deflate, two layers, zstd tails) and their error bodies, then files read as before in every encoding and chunking.
2. Green: one decoding function in paperless/unpacking.py that ends a gzip or deflate body at the end of its stream, at every layer of Content-Encoding; used by every streamed read; error bodies cut to 64 KiB as they arrive, also in stream_post.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Cause: httpx2's gzip and deflate decoders keep data after the end in zlib's unused_data and copy it with every piece. Measured before the change (32 MiB on the wire): 1.8 GB of process memory in download, stream and stream_post and in their error bodies, 460 MB for a 64 KiB body in two layers; zstd tails and empty blocks took none. 39 of 60 memory cases red first.

decoded() in paperless/unpacking.py replaces aiter_bytes() in every streamed read: gzip and deflate end at the end of their stream in every layer of Content-Encoding, one piece more is read so that the connection of an answer that is over can be kept (checked with a real server: 4 reads, 1 connection). zstd and br stay with httpx2. Files are read as before in 11 packings and 4 cuts; a stream without an end still gives what arrived. Error bodies are cut to the 64 KiB that arrive, now also in stream_post (read whole before); one that cannot be unpacked is still the error of its status.

Deliberate changes: stream and download no longer read on after the end; a raw deflate body whose first piece is one byte is read (was an error). Not changed: time is not bounded (empty blocks in a download, layered bombs in an error body); non-streamed request(); download() and stream_post() let DecodingError through (internal error) as before. No decision: decision-77 names this as open. Memory tests use 8 MiB on the wire and a bound of 8 MB.

main merged (PLMCP-83.2 to 83.4): post_json shares the error body path, in the memory cases with its error body. A 200 body that cannot be unpacked is a typed unexpected answer in download and stream_post too (was an internal error; 10 tests red first).

Codex on #188: a raw deflate body that starts like a zlib header was refused, httpx2 reads it. A body counts as zlib only if its first piece unpacks as such (3 tests red first).
<!-- SECTION:NOTES:END -->
