# Security

Please report a security problem privately, through
[GitHub's private vulnerability reporting](https://github.com/gardenbaum/mcp-server-paperless/security/advisories/new),
not in a public issue. Leave out real tokens and document content.

## Files are rendered, and the renderer is not a sandbox

`get_document_pages` and `inspect_pdf` draw pages of your documents with PDFium (pypdfium2) and Pillow. A document
can come from anyone, so this is native code reading untrusted files. It runs in a child process of
its own for each call, with limits on time, memory and output, and it does not inherit the server's
environment (tokens), stdin, stdout and open files. On Linux and macOS it also cannot write data
into files with ordinary writes, fork or start threads (as root on Linux it can fork). Where no
system fonts are installed, as in the container image, it can open no file and no network connection
either.

That contains crashes, hangs and memory. It is not a sandbox: somebody who gains code execution in
the child through a malicious file acts as the user the server runs as. Outside the container image
that includes reading that user's files and using the network. Everywhere it can include changing,
emptying, renaming or deleting files that user may change (memory mapping, truncate, rename and
unlink are not ordinary writes) and running another program in its place. Run the container image
read-only and as its unprivileged user: then there is nothing to change.

On Linux the server closes its own environment and memory to other processes of its user. It cannot
do that for others that hold the same token: started with `uvx`, the `uv` process stays the parent
and has `PAPERLESS_TOKEN` in its environment, and an MCP client keeps it in its configuration file.
On a Linux desktop with system fonts, or on macOS and Windows, a compromised child can read those;
on macOS and Windows the server's own environment, too. If this matters to you, run the container
image, or switch the tools off with `PAPERLESS_MCP_DISABLED_TOOLS=get_document_pages,inspect_pdf`.

Vulnerability scanners do not report PDFium's vulnerabilities for pypdfium2. This repository checks
weekly whether the newest release is locked; keep the server up to date.

## Confirmations

Tools that can lose data or widen access need a confirmation. By default the model may give it
itself, with `confirm=true`: clients without dialogs have no other way. A model that reads documents
others wrote can be talked into that (prompt injection).

`PAPERLESS_MCP_REQUIRE_DIALOG=true` protects against it: only a dialog that was accepted in the client
confirms, `confirm=true` does not, and with a client that cannot show dialogs, or after any other
outcome, nothing is changed
([details](https://github.com/gardenbaum/mcp-server-paperless/blob/main/docs/configuration.md#only-dialogs-confirm)).
It does not prove that a human answered. The server sees only what the client sends: a client can
answer dialogs automatically, by a setting, a hook or because nobody is present, and who controls the
client controls the answer. Nor does it cover what needs no confirmation, such as changing a title or
a tag, or somebody who has the Paperless token.

## Saved downloads

Nothing is saved unless you set `PAPERLESS_MCP_DOWNLOAD_DIRS`, and never over HTTP, in read-only mode
or on Windows. A model that reads documents others wrote can be talked into calls, so the server
itself holds the limits: one new plain file in an existing directory inside those you named, with
the ending of the type Paperless detected (from a fixed table), never hidden, never executable, and an existing file is
replaced only after a confirmation: a dialog where your client can show one, else `confirm=true`,
which the model passes (see [Confirmations](#confirmations) to allow only dialogs). Name a
directory of its own for it, not a project folder, and none that a program runs or loads files from:
a file such as `requirements.txt` or `CMakeLists.txt` can have the ending of a text document.

Another process of the same user can interfere in the microseconds after the server's last look: it
can make a confirmed replacement replace something it put there (never write through a link), give
the file's name to something it put under the hidden temporary name, or move the directory away with
the file. Whoever may rewrite the parents of the directory you named can move it.
