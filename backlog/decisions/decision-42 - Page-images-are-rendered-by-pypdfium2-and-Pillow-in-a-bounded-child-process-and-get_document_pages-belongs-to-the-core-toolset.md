---
id: decision-42
title: >-
  Page images are rendered by pypdfium2 and Pillow in a bounded child process,
  and get_document_pages belongs to the core toolset
date: '2026-11-23T19:12:25Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
Paperless serves files and a thumbnail of page 1; it renders no pages. The owner asked on 2026-11-23 for pages a model can read: an OCR spot (a signature date) could not be checked. Rendering means parsing untrusted files with native code. The default profile had 19 of 20 tools (decision-34 left one slot and asks that a further default tool be weighed against it).

Compared: pypdfium2 5.15.2 (BSD-3-Clause and Apache-2.0; wheels of 3.6 to 4.9 MiB for Linux glibc and musl, macOS and Windows on x64 and arm64; no V8 and no XFA in the default builds), Pillow 13.0.0 (MIT-CMU), PyMuPDF (AGPL, over 60 MiB, pulls numpy and onnxruntime), pdf2image and Ghostscript (system programs under the GPL or AGPL), pikepdf and pypdf (do not draw a page).

Decided by the controller under the owner's delegation of 2026-11-23; the owner did not approve the details.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- **Renderer.** pypdfium2 draws PDFs, Pillow decodes and encodes images. Only `rendering/` imports them. Pillow opens JPEG, PNG, TIFF and WebP and nothing else.
- **One child process per call**, of the same interpreter, isolated (`-I`). It gets the file on a pipe and answers on a pipe: never the server's stdin or stdout, no environment variable (Windows: `SystemRoot` only, which it needs to start), no other descriptor, no file. A cancelled call ends it. One document is rendered at a time, its download included; a call waits at most 30 s for its turn.
- **Limits are constants, not settings.** The file: 100 MiB (the largest upload) and 120 s to download. The image original: 100 megapixels per page. The child: 1 GiB of memory, looked at every 20 ms, and 30 s; its watchdog fails closed, so whatever it cannot read ends the process. The result: 5 images and 720 KiB of image bytes a call, 256 to 2576 pixels on the long side (default 1568), WebP. A single event of the MCP SDK's HTTP client may hold 1 MiB, which is 720 KiB in base64 with the report; Claude Code's default of 25 000 output tokens holds five images of 2576 pixels. A page that is too large alone gets a lower quality and then fewer pixels, and the report says so; pages that do not fit are named with the specification to pass next. `PAPERLESS_MCP_MAX_DOWNLOAD_BYTES` is about delivery and does not apply.
- **The child gives up rights it does not need.** POSIX: it writes no file, dumps no core and starts no process or thread (`RLIMIT_FSIZE`, `RLIMIT_CORE`, `RLIMIT_NPROC`). Where no system fonts exist, as in the container image, it can open nothing after it started, neither a file nor a socket (`RLIMIT_NOFILE`); where there are system fonts it still can, because PDFium reads them for text whose font is not embedded, and leaving them out would change what is shown. Linux: also 2 GiB of address space and `no_new_privs`. The server itself sets `PR_SET_DUMPABLE` to 0 on Linux, so no other process of its user, the child included, reads its environment (the tokens), memory or descriptors through `/proc` or attaches to it (root and `CAP_SYS_PTRACE` still can).
- **This is not a sandbox.** It contains crashes, hangs and memory. Code execution in the child runs as the server's user. With system fonts, on macOS or on Windows it can read that user's files and use the network; on Windows none of the rights above are taken. seccomp, Landlock and job objects are not used.
- **Windows.** The memory limit reads the committed memory through `kernel32`; the tests of `rendering/` also run in a `windows-latest` job, which is not a required check.
- **Following PDFium.** OSV lists no vulnerabilities for pypdfium2, so neither `pip-audit` nor the image scan reports PDFium's. A weekly workflow compares the lock with the newest release on PyPI and keeps one issue open while it is behind. It updates nothing by itself.
- **Versions.** A version is shown only if the document lists it: Paperless answers the file of a deleted version while it is in the trash.
- **MEDIA_TOOLS.** `get_document_pages` joins it (decision-11): its payload is images.
- **Core toolset.** `get_document_pages` joins `core`; the default profile has 20 of 20 tools. Weighed against decision-34: `run_saved_view` finds documents; this tool reads the ones whose text layer is missing or wrong, which no other default tool can (the thumbnail is too small, the download limit too low for most scans). It is read-only and the request came from a default profile. Correcting text (PLMCP-75) and saving files (PLMCP-76) are rarer and can live in opt-in toolsets. A further default tool must replace one or come with a new budget decision.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- The installed packages grow by about 23 to 30 MB; no system package is added to the image.
- The container needs up to about 1 GiB above its usual memory while a document is rendered, plus up to about 200 MiB in the server for a file of 100 MiB (estimated, not measured).
- A slow document delays other callers by up to 30 s of waiting, then they are told to try again. Every call downloads the file again; nothing is cached.
- Text whose font is not embedded can look wrong in the container (no system fonts); the archive version usually embeds its fonts.
- The server writes no core dump on Linux, and a debugger needs root.
- Text in the images is data, never instructions; the tool and every report say so.
- `just check` runs on macOS and Linux only; whether the Windows job passes shows on the first pull request.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives

<!-- SECTION:ALTERNATIVES:BEGIN -->
- Rendering in a thread: cannot be stopped, and a crash ends the server.
- PyMuPDF, poppler, Ghostscript: licence, size, system packages. Extracting embedded images: not the page as shown.
- Pages in `get_document_thumbnail`: no slot, but a changed contract and a misleading name.
- `RLIMIT_NOFILE` everywhere: a smaller reach for a compromised child, at the price of wrong glyphs where system fonts would have helped.
- Landlock or seccomp: a narrower reach with fonts too, but Linux only and code of its own; not chosen now.
<!-- SECTION:ALTERNATIVES:END -->

## Enforcement

- Import-linter contract "Only rendering imports pypdfium2 and PIL" (pyproject.toml).
- `tests/rendering/` (marker decision-42 on the runner): environment, descriptors, pipes, time, memory, flood, crash, cancellation (asyncio and anyio), the fail-closed watchdog, the resource limits in a real process, and on Linux that a child cannot read the protected server's environment or memory.
- `tests/services/test_pages.py`, `tests/tools/test_pages.py`, `tests/tools/test_pages_transport.py` (marker decision-42): each limit, the version rule, the image budget over HTTP in both modes of the client, and stdio.
- `tests/tools/test_registry.py`: MEDIA_TOOLS and the budget of 20.
- `tests/quality/test_renderer_drift.py`, `tests/quality/test_rendering.py` (marker decision-42): the weekly alert, the Windows job, the licence files in the installed packages.
- `scripts/image_smoke.sh` and `scripts/image_stdio_smoke.sh`: a page is rendered in the image, read-only, without capabilities and as uid 10001, and the child can open no file there.
