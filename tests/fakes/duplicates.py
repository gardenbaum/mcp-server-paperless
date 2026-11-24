"""Duplicates in the fake, as Paperless 3.5.1 finds them: by the SHA-256 of the files.

Root files, archives and versions are compared by their bytes. The document and user are
protocols, so this module does not import the fake it serves.
"""

import hashlib
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from datetime import date


class File(Protocol):
    @property
    def original(self) -> bytes: ...
    @property
    def archive(self) -> bytes | None: ...


class Doc(File, Protocol):
    """What the comparison reads of a fake document (live or in the trash)."""

    @property
    def id(self) -> int: ...
    @property
    def created(self) -> date: ...
    @property
    def root_id(self) -> int | None: ...
    @property
    def version_files(self) -> Mapping[int, File]: ...


def sha256(content: bytes) -> str:
    """The checksum Paperless stores for a file."""
    return hashlib.sha256(content).hexdigest()


def root_checksum(document: Doc) -> str:
    """The checksum of a document's original upload."""
    return sha256(document.version_files.get(document.id, document).original)


def _checksums(file: File) -> set[str]:
    return {sha256(f) for f in (file.original, file.archive) if f is not None}


def duplicates_of[D: Doc](document: Doc, others: Iterable[D]) -> list[D]:
    """The documents with the root or archive file of this one, newest first (no versions)."""

    def files(of: Doc) -> set[str]:
        return _checksums(of.version_files.get(of.id, of))

    found = [
        d
        for d in others
        if d.root_id is None and d.id != document.id and files(d) & files(document)
    ]
    return sorted(found, key=lambda d: d.created, reverse=True)


def rejected_for(content: bytes, live: Iterable[Doc], trash: Iterable[Doc]) -> int | None:
    """The id PAPERLESS_CONSUMER_DELETE_DUPLICATES names: any document or version with this file."""
    checksum = sha256(content)
    found = [
        (document.created, number)
        for document in (*live, *trash)
        for number, file in {document.id: document, **document.version_files}.items()
        if checksum in _checksums(file)
    ]
    return max(found, key=lambda f: f[0])[1] if found else None
