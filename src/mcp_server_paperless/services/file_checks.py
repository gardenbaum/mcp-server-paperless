"""Whether Paperless can find the files of documents before one of them replaces the others.

Checksums come from the database and stay when a file is lost, so a duplicate, or the target of a
merge, may be a document whose file is gone. Read are the file Paperless compares (the root
version), the one a document shows (the newest) and later versions of the same file as the root.
"""

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mcp_server_paperless.domain.files import joined
from mcp_server_paperless.services.documents import CHECK_SECONDS, read_facts, root_checksum

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from mcp_server_paperless.domain.documents import DocumentRecord, FileMetadata
    from mcp_server_paperless.domain.ports import DocumentReader


def _same(one: str | None, other: str | None) -> bool:
    return bool(one and other) and (one or "").lower() == (other or "").lower()


@dataclass(frozen=True, slots=True)
class FileCheck:
    """What is known of the files of some documents.

    Attributes:
        facts: The files that could be read, by document and version.
        unchecked: The documents of which a file could not be checked, and why.
        checksums: The checksum of each original upload, as the document tells it.
    """

    facts: Mapping[int, Mapping[int, FileMetadata]]
    unchecked: Mapping[int, str]
    checksums: Mapping[int, str | None]

    def lost(self, document_id: int) -> list[str]:
        """The files of a document Paperless cannot find, in words."""
        files = self.facts.get(document_id, {})
        several = len(files) > 1 or document_id in self.unchecked
        found: list[str] = []
        for version in files:
            nouns = self.lost_nouns(document_id, version)
            if nouns:
                which = f" (version {version})" if several else ""
                found.append(f"the {' and the '.join(nouns)} of document {document_id}{which}")
        return found

    def lost_nouns(self, document_id: int, version_id: int) -> list[str]:
        """Which files of a version that was read Paperless cannot find."""
        facts = self.facts[document_id][version_id]
        nouns = ["original"] if facts.lacks("original") else []
        if facts.has_archive_version and facts.lacks("archive"):
            nouns.append("archive version")
        return nouns

    def lacks_original(self, document_id: int) -> bool:
        """Whether the original upload is known to be gone, also as a later version."""
        root = self.facts.get(document_id, {}).get(document_id)
        return root is not None and not self._has_original(document_id)

    def outdone(self, kept: int, copies: Sequence[int]) -> str | None:
        """Why ``kept`` cannot stay instead of the copies: it lacks a file that some still have.

        Nothing is said of what could not be checked. A lost archive version matters only if it is
        the file that matched; otherwise the original is there, and Paperless makes it again.
        """
        root = self.facts.get(kept, {}).get(kept)
        if root is None:
            return None
        archive_there = root.has_archive_version and not root.lacks("archive")
        if not self.lacks_original(kept):
            wanted = root.archive_checksum
            if self._holds(kept, wanted):
                return None
            # With the same original, that is the file that matched, and it is there.
            other = [c for c in copies if not self._known(c, root.original_checksum)]
            return self._still(
                "Paperless cannot find its archive version, the file that matched",
                {"that file": [c for c in other if self._holds(c, wanted)]},
            )
        same = [c for c in copies if self._holds(c, root.original_checksum)]
        own = [c for c in copies if c not in same and self._has_original(c)]
        rest = [c for c in copies if c not in same and c not in own and not archive_there]
        archived = [c for c in rest if self._has_archive(c)]
        lead = "Paperless cannot find its original"
        if archive_there:
            lead += ", only its archive version"
        elif root.has_archive_version:
            lead += " or its archive version"
        return self._still(
            lead, {"that file": same, "an original": own, "an archive version": archived}
        )

    @staticmethod
    def _still(lead: str, having: Mapping[str, Sequence[int]]) -> str | None:
        parts = [
            f"{joined(ids)} still {'has' if len(ids) == 1 else 'have'} {what}"
            for what, ids in having.items()
            if ids
        ]
        return f"{lead}, and {', '.join(parts)}" if parts else None

    def _has_original(self, document_id: int) -> bool:
        return self._holds(document_id, self.checksums.get(document_id)) or any(
            # A document without a known checksum has the one its root file tells.
            version == document_id and not f.lacks("original")
            for version, f in self.facts.get(document_id, {}).items()
        )

    def _has_archive(self, document_id: int) -> bool:
        root = self.facts.get(document_id, {}).get(document_id)
        return root is not None and not root.lacks("archive")

    def _holds(self, document_id: int, checksum: str | None) -> bool:
        """Whether a file with this checksum is there."""
        return any(
            (_same(f.original_checksum, checksum) and not f.lacks("original"))
            or (
                f.has_archive_version
                and _same(f.archive_checksum, checksum)
                and not f.lacks("archive")
            )
            for f in self.facts.get(document_id, {}).values()
        )

    def _known(self, document_id: int, checksum: str) -> bool:
        """Whether a file with this checksum belongs to the document, there or not."""
        return _same(self.checksums.get(document_id), checksum) or any(
            _same(f.archive_checksum, checksum) or _same(f.original_checksum, checksum)
            for f in self.facts.get(document_id, {}).values()
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


def _versions(document: DocumentRecord) -> list[int]:
    root = root_checksum(document)
    same = [v.id for v in document.versions if _same(v.checksum, root)]
    return list(dict.fromkeys((document.id, document.versions[0].id, *same)))


async def check_files(
    reader: DocumentReader, documents: Sequence[DocumentRecord], *, seconds: float = CHECK_SECONDS
) -> FileCheck:
    """Look whether the files of ``documents`` are there; at most ``seconds`` for all of them."""
    wanted = {d.id: _versions(d) for d in documents}
    files = [(number, version) for number, versions in wanted.items() for version in versions]
    facts, reasons = await read_facts(reader, files, seconds)
    return FileCheck(
        facts={n: {v: facts[n, v] for v in vs if (n, v) in facts} for n, vs in wanted.items()},
        unchecked={n: reasons[n, v] for n, vs in wanted.items() for v in vs if (n, v) in reasons},
        checksums={d.id: root_checksum(d) for d in documents},
    )


async def check_for_trash(
    reader: DocumentReader,
    keep: DocumentRecord,
    duplicates: Sequence[DocumentRecord],
    *,
    seconds: float = CHECK_SECONDS,
) -> FileCheck:
    """Look at the duplicates only if the kept document lacks a file, in what is left of the time.

    The kept document is not read again: a second read that fails would hide what is known.
    """
    started = time.monotonic()
    kept = await check_files(reader, [keep], seconds=seconds)
    if not kept.lost(keep.id):
        return kept
    left = max(0.0, seconds - (time.monotonic() - started))
    others = await check_files(reader, duplicates, seconds=left)
    return FileCheck(
        facts={**kept.facts, **others.facts},
        unchecked={**kept.unchecked, **others.unchecked},
        checksums={**kept.checksums, **others.checksums},
    )
