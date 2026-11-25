"""What Paperless 3.4.2 accepts in a saved view write, as pure checks for FakePaperless.

Probed on the live stack (PLMCP-25); the contract suite keeps the fake and Paperless in step. A
failed check raises `Validation` with the message of the 400.
"""

import re
from typing import TYPE_CHECKING

from fakes.document_values import reject
from mcp_server_paperless.domain.errors import UpstreamUnavailable
from mcp_server_paperless.domain.views import DISPLAY_FIELDS, ICONS, RULE_TYPES

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from mcp_server_paperless.domain.views import FilterRule, ViewValues

MAX_VALUE = 255


def check_rules(rules: Sequence[FilterRule]) -> None:
    """Every rule type is one of the 3.4.2 types and every value at most 255 characters."""
    for position, rule in enumerate(rules):
        if rule.rule_type not in RULE_TYPES:
            reject(
                f"filter_rules.{position}.rule_type", f'"{rule.rule_type}" is not a valid choice.'
            )
        if rule.value is not None and len(rule.value) > MAX_VALUE:
            reject(
                f"filter_rules.{position}.value",
                f"Ensure this field has no more than {MAX_VALUE} characters.",
            )


def check_page_size(page_size: int | None) -> None:
    """A page size is at least 1."""
    if page_size is not None and page_size < 1:
        reject("page_size", "Ensure this value is greater than or equal to 1.")


def check_display(values: ViewValues, custom_fields: Collection[int]) -> None:
    """The icon is a known one, and each display field, too (Paperless's own loose check)."""
    if values.icon is not None and values.icon not in ICONS:
        reject("icon", f'"{values.icon}" is not a valid choice.')
    for field in values.display_fields or ():
        if "custom_field_" in field:
            found = re.search(r"\d+", field)
            if found is None:
                message = "Paperless failed to process the request."  # 500 (probe P4)
                raise UpstreamUnavailable(message)
            if int(found[0]) not in custom_fields:
                reject("non_field_errors", f"Invalid field: {field}")
        elif field not in DISPLAY_FIELDS:
            reject("non_field_errors", f"Invalid field: {field}")
