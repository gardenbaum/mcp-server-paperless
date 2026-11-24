"""Whether Paperless can find the files of documents before one of them replaces the others.

Checksums come from the database and stay when a file is lost, so a duplicate, or the target of a
merge, may be a document whose file is gone. Read are the file Paperless compares (the root version)
and the one a document shows (the newest).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from mcp_server_paperless.services.documents import CHECK_SECONDS, read_facts

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from mcp_server_paperless.domain.documents import DocumentRecord, FileMetadata
    from mcp_server_paperless.domain.ports import DocumentReader


@dataclass(frozen=True, slots=True)
class FileCheck:
    """What is known of the files of some documents.

    Attributes:
        facts: The files that could be read, by document and version.
        unchecked: The documents of which a file could not be checked, and why.
    """

    facts: Mapping[int, Mapping[int, FileMetadata]]
    unchecked: Mapping[int, str]

    def lost(self, document_id: int) -> list[str]:
        """The files of a document Paperless cannot find, in words."""
        files = self.facts.get(document_id, {})
        several = len(files) > 1 or document_id in self.unchecked
        found: list[str] = []
        for version, facts in files.items():
            nouns = ["original"] if facts.lacks("original") else []
            if facts.has_archive_version and facts.lacks("archive"):
                nouns.append("archive version")
            if nouns:
                which = f" (version {version})" if several else ""
                found.append(f"the {' and the '.join(nouns)} of document {document_id}{which}")
        return found

    def outdone_by(self, kept: int, copies: Sequence[int]) -> list[int]:
        """The copies that still have the compared file if ``kept`` is known to lack it.

        An archive version counts only if ``kept`` has none either: Paperless makes it again from
        the original. Nothing is said of what could not be checked.
        """
        root = self.facts.get(kept, {}).get(kept)
        if root is None or self._has_original(kept):
            return []

        def has_it(copy: int) -> bool:
            own = self.facts.get(copy, {}).get(copy)
            archive = own is not None and root.lacks("archive") and not own.lacks("archive")
            return archive or self._has_original(copy)

        return [copy for copy in copies if has_it(copy)]

    def _has_original(self, document_id: int) -> bool:
        """Whether the original upload is there, also as a later version of the same file."""
        files = self.facts.get(document_id, {})
        root = files.get(document_id)
        return root is not None and any(
            f.original_checksum.lower() == root.original_checksum.lower()
            and not f.lacks("original")
            for f in files.values()
        )

    def notes(self, kept: int) -> list[str]:
        """What has to be said about the files, the kept document's as a warning."""
        found: list[str] = []
        for document_id in self.facts:
            lead = "WARNING: " if document_id == kept else ""
            found += [f"{lead}Paperless cannot find {lost}" for lost in self.lost(document_id)]
            if document_id in self.unchecked:
                unsure = f"Whether Paperless can find the file of document {document_id}"
                found.append(f"{lead}{unsure} could not be checked")
        return found


async def check_files(
    reader: DocumentReader, documents: Sequence[DocumentRecord], *, seconds: float = CHECK_SECONDS
) -> FileCheck:
    """Look whether the files of ``documents`` are there; at most ``seconds`` for all of them."""
    wanted = {d.id: tuple(dict.fromkeys((d.id, d.versions[0].id))) for d in documents}
    files = [(number, version) for number, versions in wanted.items() for version in versions]
    facts, reasons = await read_facts(reader, files, seconds)
    return FileCheck(
        facts={n: {v: facts[n, v] for v in vs if (n, v) in facts} for n, vs in wanted.items()},
        unchecked={n: reasons[n, v] for n, vs in wanted.items() for v in vs if (n, v) in reasons},
    )
