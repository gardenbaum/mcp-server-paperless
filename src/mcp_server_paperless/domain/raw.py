"""What the raw toolset reports: the answer of one GET, as the port and as the tool return it.

``RawReply`` is what the port returns (the bytes Paperless sent); ``RawApiResult`` is what the
tool returns (parsed, redacted and size-capped JSON).
"""

from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class _Frozen(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)


class RawReply(_Frozen):
    """The answer of one GET as Paperless sent it.

    Attributes:
        status: The HTTP status (a successful one; errors are raised).
        content_type: The ``Content-Type`` header, empty if there is none.
        body: The whole body; empty if it is ``truncated``.
        truncated: The body is larger than the limit that was asked for; none of it is kept.
    """

    status: int
    content_type: str
    body: bytes
    truncated: bool


class RawApiResult(_Frozen):
    """The answer of a read-only GET under /api/ of Paperless."""

    path: str
    status: Annotated[int, Field(description="The HTTP status Paperless answered, such as 200.")]
    data: Annotated[
        JsonValue,
        Field(description="The JSON body, parsed; null if there is none or it was cut."),
    ]
    truncated: Annotated[
        bool, Field(description="True if the body was too long: see text and note.")
    ]
    text: Annotated[
        str | None,
        Field(description="The first part of the JSON as text when truncated (not valid JSON)."),
    ]
    redacted: Annotated[
        int,
        Field(description="How many secret values (passwords, keys) were replaced or left out."),
    ]
    note: Annotated[str | None, Field(description="What to do next, when there is something.")]
