---
id: decision-68
title: >-
  insert_pages builds the file in the renderer's child process and adds one
  version or document
date: '2026-11-24T13:25:58Z'
status: accepted
---
## Context

<!-- SECTION:CONTEXT:BEGIN -->
Paperless 3.5.1 has no operation that puts pages of one document into another: `edit_pdf` takes one source, and `merge` takes whole files and always makes a new document. The owner asked for inserting pages at any place (AC 2 and 3 of PLMCP-79).

The design was put to the owner with recommendations: this server builds the file with the renderer and adds one version or document (1 B, against three operations in Paperless with two temporary documents), a new document without grants, notes and archive serial number (5a), no duplicate check for the result (13), the guide limit of the overview up to 2880 bytes for the three new names (7). On 2026-11-24 the owner approved: "Bitte alles wie von dir empfohlen umsetzen." (please do everything as you recommend) (PLMCP-79). The rest was decided within the design.
<!-- SECTION:CONTEXT:END -->

## Decision

<!-- SECTION:DECISION:BEGIN -->
- **Supplements decision-42.** The child process of decision-42 gets a third job kind, `assemble`: two PDFs in, one PDF out. Every limit of that decision holds unchanged: 1 GiB, 30 s, one at a time (the turn is shared with `get_document_pages` and `inspect_pdf`, the download included), 100 MiB of input, here for both files together, and 120 s to download both. Its answer is bound by 100 MiB for the result beside the 1 MiB of a call. Import contracts and rights of the child are unchanged.
- **One write.** Both records and file facts are read and checked; both files are downloaded; the child puts them together; both documents are read again and must still have the file that was read; then one `update_version` or `post_document`, followed like an upload. A step that fails before it writes nothing and leaves nothing. No answer to that one request is reported as not known, with the place to look before repeating. No Paperless operation is used, nothing is trashed, no temporary document is made.
- **What is checked of the answer.** It is untrusted: `%PDF-` header, at most 100 MiB, and a second child that gets the result alone reads the size of every page and counts them: those of the document plus those inserted. Both children share the 30 s. A compromised child could still alter what is on the pages; the earlier version stays.
- **Broken files.** Every page of the target and every wanted page of the source must have a size before PDFium is asked to import: importing next to a missing page crashes it (found while testing; it would be contained, but not explained). A target with another number of pages than Paperless counted is refused. A page tree whose inner counts are wrong makes PDFium insert a page at another place, or where no reader finds it (found in review): so marker pages of sizes the target does not have are first inserted at the same places of a throwaway copy, which is saved and opened again; if one is not where it must be, the target is `damaged`. The result is opened again, too, and must have every page of the target and of the source at its place with its size. After the import each inserted page is given its rotation and crop box as entries of its own, because it would otherwise inherit those of the target's page tree (found while testing).
- **Encrypted files are refused**, target and source, also those that open without a password but restrict what may be done: PDFium would keep the encryption of a target and silently drop that of a source.
- **Sources.** PDFs, and the archive PDF of any other type, which the plan names. The target must be a PDF whose pages Paperless counted.
- **Rights.** A new version needs a change grant on the target, checked before anything is downloaded; a view of the source is enough. A new document needs a view of both. Global permissions are left to Paperless.
- **No confirm** (decision-6): every page of the saved file was found at its place, the older version stays, no document is trashed. The tool is marked non-destructive. The plan says that everybody who may see the document can read the inserted pages. `dry_run` does everything but the second reading and the upload, so it also finds what only opening the files shows.
- **decision-47 is not applied**: the bytes of the result differ every time, so a duplicate check could never match.
- `PAPERLESS_MCP_MAX_UPLOAD_BYTES` bounds what a caller brings. This file comes from Paperless and goes back to it, so the limits above hold instead.
- **What comes along and what is lost** is in the plan. Along with a page comes what it refers to, also where it is not visible: links, comments with the replies to them and the files in them (a file-attachment annotation brings its file), actions, private and page data, and resources shared with other pages of the source. Other pages themselves do not come. Lost: a signature in the target; bookmarks, document attachments and scripts, properties, layer settings and the form of the inserted pages (their fields become a picture). The target keeps its bookmarks, attachments and form. Accessibility tags and PDF/A conformance are not checked.
<!-- SECTION:DECISION:END -->

## Consequences

<!-- SECTION:CONSEQUENCES:BEGIN -->
- Up to 100 MiB of traffic each way. Measured with 49 and 50 MiB on macOS: 0.8 s, 600 MB in the child, 203 MiB in the server besides the inputs.
- The overview of the server names one more tool (15 bytes of the guide: 2864).
- A layer that the source hides by default can become visible, because the settings of layers belong to the file and not to the page.
- **Form fields become a picture.** A field belongs to the form of its file. Imported, PDFium draws it only in a file that has a form, and where that form asks for new appearances with the value of a field of the same name there (both found in review). So fields are taken off the inserted pages in memory. In a second copy, whose form is loaded, everything but the fields is taken off and the fields are flattened; that picture is put on the imported page as one locked stamp annotation. Compared: as more page content it depended on what the content leaves behind (a clip, an unclosed `q`, an invisible text mode hid the values in all readers tried); an annotation does not. Drawn and compared with the page of the source in PDFium, with and without its form drawing, and (locally) in MuPDF, Poppler and Ghostscript: text of one and more lines, password (asterisks), check box, radio and push button, combo box and list; targets without a form, with one, and with one that asks for new appearances; the same and other names; rotation, crop box, shifted, inverted and inherited boxes, UserUnit. Poppler, MuPDF and Ghostscript find the values as text, as in the source. A stored appearance is kept unless the file asks for new ones. Left out: hidden and NoView fields. Fields without the print flag are drawn, so they are printed, too: a missing value would be worse. The stamp lies above other annotations of its page. Characters that the font of a field lacks are missing. Content that stays in the copy and could hide the fields is refused as `damaged`. The builds have neither V8 nor XFA, and no document or page action is triggered, so no script runs.
- **Page labels** of the target are kept by position, so those behind the inserted pages move.
- **Sizes.** A page over 10 000 000 points (3.5 km) is refused as `size`: such numbers are not written down exactly.
- Between the second reading and the upload (milliseconds) another user can still add a version; the document then shows the inserted file and theirs is an earlier version.
- A PDF restricted without a password has to be rewritten first (`edit_pdf` with all pages as a new version).
- Inserting takes longer than linearly with the number of pages: 5000 into 5000 took 5 s, 50 000 pages end at the 30 s.
- The built file is dropped before the task is followed; uploads at the same time hold up to 100 MiB each.
- Repeating a call after a lost answer inserts the pages again.
<!-- SECTION:CONSEQUENCES:END -->

## Alternatives

<!-- SECTION:ALTERNATIVES:BEGIN -->
- Merge, `edit_pdf` and `merge_as_versions` in Paperless: two temporary documents that run workflows, matching and OCR, ownership and delete permission, leftovers on failure (owner decision 1).
- Counting the pages in the same child: says nothing if that child is compromised or its PDFium state is broken.
- Flattening the inserted pages themselves: PDFium then flattens links and comments, too. Unique names for imported fields: no values in readers where the target has no form. The picture of the fields as page content: see above.
- Removing file-attachment annotations from inserted pages: about 15 lines (PDFium removes them from the source in memory, and the file does not come along), but a comment the reader of the source saw would be missing. Left to the owner.
<!-- SECTION:ALTERNATIVES:END -->

## Enforcement

- `tests/rendering/test_assemble.py` (marker decision-68): order and place, sizes, rotation, inheritance, structure kept and lost, broken, encrypted and packed files, hostile answers, the second count, time, memory, 100 MiB, a child that may open nothing; also in the Windows job.
- `tests/services/test_pdf_insert.py`, `tests/tools/test_insert.py` (marker decision-68): every refusal and every way a call can end, with what was written; the shared turn; plan and dry run.
- `tests/contract/test_pdf_insert.py` and `tests/e2e/test_pdf_insert.py`: rights, metadata of the new document and the version on Paperless 3.5.1.
- `tests/tools/test_toolset_budget.py`: the guide limit. `scripts/image_render_smoke.sh`: in the container image.
