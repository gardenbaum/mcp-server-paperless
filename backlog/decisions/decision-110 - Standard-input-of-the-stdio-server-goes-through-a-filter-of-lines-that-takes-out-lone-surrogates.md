---
id: decision-110
title: >-
  Standard input of the stdio server goes through a filter of lines that takes
  out lone surrogates
date: '2026-11-25T18:22:15Z'
status: proposed
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
A lone surrogate (half of a character outside the basic plane) is no text UTF-8 can hold, but JSON can hold it as an escape: `JSON.stringify` writes one for a text that was cut in an emoji. The stdio transport of the MCP SDK parses every line with pydantic, whose JSON parser refuses the whole line. The SDK then passes the error on instead of a message, and nobody listens there: it is logged at DEBUG. The client gets no answer at all and waits until its own limit (probed, PLMCP-118). FastMCP and the SDK offer no hook before the parser: `run_stdio_async` opens the transport itself. Over HTTP every such call is answered: protocol 2026-07-28 parses with the standard library, so the call reaches the tool, and older versions answer HTTP 400 with the reason.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- `run_stdio` (cli.py) sets `sys.stdin` to a stream over a duplicate of descriptor 0, whose lines go through `parseable` (domain/texts.py). The SDK reads the buffer of `sys.stdin`, so it needs no change and no private part of it is used.
- A line without the escape of a lone surrogate is handed on byte for byte. Pairs, escaped backslashes and broken lines count as such.
- A call of a tool with one in its arguments becomes the same call with the single argument `mcp-server-paperless/refused-text`, which says where it was. The refusal itself is that of every other transport: the middleware `TextRefusal`, before arguments are validated. A caller who sends that argument only refuses a call of their own.
- Elsewhere (the name of a tool, `_meta`, prompts, resources, ids) U+FFFD takes its place, so that the message has an answer, as invalid UTF-8 bytes already become U+FFFD in the SDK. It is not refused there: none of it is written to Paperless.
- The SDK points descriptor 0 at the null device, so that child processes cannot read the messages, only if `sys.stdin` is that descriptor. With the filter it is not, so the filter does the same, and undoes it at the end.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- Buffering: one line at a time is read, as the SDK does; no line waits for the next. A line has no limit of length, before and after. For a line of 30 MB the process peaked at 193 MiB without the filter, 261 MiB with it, and 394 MiB if an escape is taken out of it; 100 MB take about 3 s.
- Signals and the end of input: the exit codes on SIGINT, SIGTERM and end of input were compared by hand on macOS and are the same. The blocked read of a thread that a SIGINT meets is there in both.
- Windows: no POSIX call is used (`os.dup`, `os.dup2`, the null device). The hosted Windows job runs the tests of the filter and of a stdio server. Signals are not tested there.
- A fault in the filter would reach every stdio user; a line it cannot read as JSON is handed on with only the escapes replaced.
- If the SDK parses such lines itself one day, the sweep over stdio still holds without the filter, and it can go.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives

<!-- SECTION:ALTERNATIVES:BEGIN -->
- Leaving it to the SDK: the client waits for nothing.
- An own copy of `run_stdio_async` with the transport between: private parts of FastMCP, to be followed with every release.
- A pipe and a thread in front of descriptor 0: the SDK would keep hiding it, but there is one thread and one copy more.
- Refusing U+FFFD or a private character as a stand-in for the escape: such a character can be meant.
- Answering the line from the filter: two writers on standard output.
<!-- SECTION:ALTERNATIVES:END -->

## Enforcement

- `tests/test_stdin.py`: lines, line ends, partial reads, the end of input, long lines, child processes; on Windows too.
- `tests/domain/test_texts.py`: what stays byte for byte, what is replaced, and that the result has no lone surrogate.
- `tests/tools/test_text_refusal.py`: every text of every tool on the wire of a stdio server, and over HTTP.
