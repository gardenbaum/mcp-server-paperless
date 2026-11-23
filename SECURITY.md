# Security

Please report a security problem privately, through
[GitHub's private vulnerability reporting](https://github.com/gardenbaum/mcp-server-paperless/security/advisories/new),
not in a public issue. Leave out real tokens and document content.

## Files are rendered, and the renderer is not a sandbox

`get_document_pages` draws pages of your documents with PDFium (pypdfium2) and Pillow. A document
can come from anyone, so this is native code reading untrusted files. It runs in a child process of
its own for each call, with limits on time, memory and output, without the server's environment
(tokens), stdin, stdout and open files; it cannot write files or start processes (not on Windows).
Where no system fonts are installed, as in the container image, it can open no file and no network
connection either.

That contains crashes, hangs and memory. It is not a sandbox: somebody who gains code execution in
the child through a malicious file acts as the user the server runs as. Outside the container image
that includes reading that user's files and using the network. On Linux the server keeps its own
environment and memory closed to that process; on macOS and Windows it does not. If this matters
to you, run the container image, or switch the tool off with
`PAPERLESS_MCP_DISABLED_TOOLS=get_document_pages`.

Vulnerability scanners do not report PDFium's vulnerabilities for pypdfium2. This repository checks
weekly whether the newest release is locked; keep the server up to date.
