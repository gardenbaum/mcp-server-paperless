---
updated_date: '2026-11-26T09:12:14Z'
id: decision-110
title: >-
  Standard input of the stdio server goes through a filter of lines that takes
  out lone surrogates
date: '2026-11-25T18:22:15Z'
status: accepted
---
Accepted by the owner on 2026-11-26: "Bitte fixe 190 und 189 und merge danach." (Please fix 190 and 189 and merge them afterwards.)

## Context

<!-- SECTION:CONTEXT:BEGIN -->
A lone surrogate (half of a character outside the basic plane) is no text UTF-8 can hold, but JSON can hold it as an escape: `JSON.stringify` writes one for a text that was cut in an emoji. The stdio transport of the MCP SDK parses every line with pydantic, whose JSON parser refuses the whole line. The SDK then passes the error on instead of a message, and nobody listens there: it is logged at DEBUG. The client gets no answer at all and waits until its own limit (probed, PLMCP-118). FastMCP and the SDK offer no hook before the parser: `run_stdio_async` opens the transport itself. Over HTTP every such call is answered: protocol 2026-07-28 parses with the standard library, so the call reaches the tool, and older versions answer HTTP 400 with the reason.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- `run_stdio` (cli.py) sets `sys.stdin` to a stream over a duplicate of descriptor 0, whose lines go through `parseable` (domain/texts.py). The SDK reads the buffer of `sys.stdin`, so it needs no change and no private part of it is used.
- A line the parser rejects today is never run. A line changes only if the escape of a lone surrogate is the one reason why it is rejected: it is decoded as the SDK decodes it (UTF-8, other bytes become U+FFFD), and with U+FFFD for the escape the same parser takes it. Every other line is handed on byte for byte, also a broken one, one with a carriage return inside (where the SDK ends a line), a notification, an answer, and a request whose id holds the surrogate: they have no answer, as before.
- A call of a tool becomes the same call with the single argument `mcp-server-paperless/refused-text`, which says where the text was (at most 100 characters), wherever in the message it was: in the arguments, the name or `_meta`. The middleware `TextRefusal` refuses it as on every other transport, before arguments are validated. A caller who sends that argument only refuses a call of their own.
- Any other request (prompts, resources) becomes one with a method nobody has, whose name is the reason: "Method not found" with that name as its data. It keeps its id and `_meta`.
- A refused line keeps nothing but `jsonrpc`, `id`, `method` and `params`: an `error` or a `result` beside the method would make it the answer to a question the server asked.
- The duplicate of descriptor 0 is never one of 0 to 2, which a child process would inherit (with stderr closed, `os.dup` would give 2).
- The SDK points descriptor 0 at the null device, so that child processes cannot read the messages, only if `sys.stdin` is that descriptor. With the filter it is not, so the filter does the same, and undoes it at the end.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- Buffering: one line at a time is read, as the SDK does; no line waits for the next, and none is kept once it was handed on. A line has no limit of length, before and after. For a line of 30 MB the process peaked at 193 MiB without the filter and 261 MiB with it. Lines without `\ud8` to `\udf` are not looked at further.
- Raw bytes of a surrogate (ED A0 80) are no UTF-8 and still become U+FFFD in the SDK, so such a call runs with U+FFFD, as before. It is not refused here.
- Signals: with input still open, the server ends at the third Ctrl-C, with and without the filter (tests compare both). The stream is therefore never closed with the thread that reads it still waiting. SIGTERM and the end of input were compared by hand on macOS.
- Windows: the server does not start there today, for another reason (`services/upload_listing.py` uses `os.O_CLOEXEC` when it is imported), so nothing of the stream itself is tested there; the logic of the lines is, in the hosted Windows job. No POSIX call is used. The SDK also points the standard handle of Windows at the null device (`rebind_std_handle_to_fd`), because it says that `os.dup2` changes the descriptor table of the C runtime only. The filter does not, so a child process that inherits that handle could read from it: to be looked at with the first start on Windows. Today's only child, the renderer, has pipes of its own.
- A fault in the filter would reach every stdio user.
- If the SDK answers such lines itself one day, the sweep over stdio still holds without the filter, and it can go.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives

<!-- SECTION:ALTERNATIVES:BEGIN -->
- Leaving it to the SDK: the client waits for nothing.
- Replacing the escape by U+FFFD where it is in no argument: the call would run, and it did not before.
- An own copy of `run_stdio_async` with the transport between: private parts of FastMCP, to be followed with every release.
- A pipe and a thread in front of descriptor 0: the SDK would keep hiding it, but there is one thread and one copy more.
- Refusing U+FFFD or a private character as a stand-in for the escape: such a character can be meant.
- Answering the line from the filter: two writers on standard output.
<!-- SECTION:ALTERNATIVES:END -->

## Enforcement

- `tests/test_stdin.py`: lines, line ends, partial reads, the end of input, long lines, child processes, Ctrl-C.
- `tests/domain/test_texts.py`: what stays byte for byte, and a property over changed lines: what the parser of the SDK rejects is never run, only refused.
- `tests/tools/test_text_refusal.py`: every text of every tool on the wire of a stdio server, and over HTTP.
