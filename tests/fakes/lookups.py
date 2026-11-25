"""Words of the search index and tried templates, as Paperless 3.5.1 answers them (PLMCP-83.2).

Words are lower case, without accents and split at every character that is no letter or digit; a
template that fails gives null. Of the template language only plain variables are understood.
"""

import re
import unicodedata
from collections import Counter
from typing import TYPE_CHECKING, Final, cast

from mcp_server_paperless.domain.errors import UpstreamUnavailable

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

_VARIABLE: Final = re.compile(r"\{\{\s*(\w+|'')\s*\}\}")


def _ascii(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def suggested(texts: Iterable[str], term: str, limit: int) -> tuple[str, ...]:
    """The words of `texts` (one per document) that begin with `term`; most documents first."""
    found: Counter[str] = Counter()
    begin = _ascii(term.strip())
    for text in texts:
        words = {_ascii(match[0]) for match in re.finditer(r"[^\W_]+", text)}
        found.update(word for word in words if word.startswith(begin))
    return tuple(sorted(found, key=lambda word: (-found[word], word))[:limit]) if begin else ()


def rendered(template: str, values: Mapping[str, str]) -> str | None:
    """The path of a PDF, "" when the template gives nothing, None when it cannot be rendered."""
    names = cast("list[str]", _VARIABLE.findall(template))
    if any(name not in values for name in names):
        return None
    text = _VARIABLE.sub(lambda match: values[match[1]], template)
    path = re.sub(r"\s*/\s*", "/", text).strip().strip("/")
    if "{{" in text or ".." in path.split("/"):
        return None
    if path and not [part for part in path.split("/") if part not in {"", "."}]:
        # Paperless fails to put the extension behind a name that is not there (read in 3.5.1).
        message = "Paperless is temporarily unavailable (HTTP 500)."
        raise UpstreamUnavailable(message, status=500)
    return f"{path}.pdf" if path else ""
