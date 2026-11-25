"""The schema guard rejects what the vendored schema does not allow (it must bite)."""

import json

import httpx2
import pytest
from observation import OBSERVED, RESPONSES, Observation, method_name

from adapter.conftest import BASE_URL, ClientFactory, streaming, tag
from adapter.schema_guard import SchemaViolationError, check_request, check_response
from mcp_server_paperless.domain.documents import DocumentUpload
from mcp_server_paperless.paperless.admin import PaperlessAdminReader
from mcp_server_paperless.paperless.documents import PaperlessDocumentWriter
from mcp_server_paperless.paperless.trash import PaperlessTrashReader, PaperlessTrashWriter

HEADERS = {"Accept": "application/json; version=10", "Authorization": "Token t"}
TAG_PAGE = {"count": 1, "next": None, "previous": None, "results": [tag(1, "inbox")]}


def get(path: str, **params: str) -> httpx2.Request:
    return httpx2.Request("GET", BASE_URL + path, params=params, headers=HEADERS)


def test_accepts_a_documented_request_and_response() -> None:
    request = get("/api/tags/", page="2", page_size="25")
    check_request(request)
    check_response(request, httpx2.Response(200, json=TAG_PAGE))


@pytest.mark.parametrize(
    "request_",
    [
        pytest.param(get("/api/no_such_endpoint/"), id="unknown path"),
        pytest.param(
            httpx2.Request("TRACE", BASE_URL + "/api/tags/", headers=HEADERS), id="unknown method"
        ),
        pytest.param(get("/api/tags/", page="two"), id="wrong parameter type"),
        pytest.param(get("/api/tags/", name__contains="x"), id="undeclared parameter"),
        pytest.param(get("/api/documents/", more_like="1"), id="undeclared, not an exception"),
        pytest.param(httpx2.Request("GET", BASE_URL + "/api/tags/"), id="no credentials"),
        pytest.param(
            httpx2.Request("POST", BASE_URL + "/api/tags/", json={"title": "x"}, headers=HEADERS),
            id="body without the required name",
        ),
    ],
)
def test_rejects_requests_outside_the_schema(request_: httpx2.Request) -> None:
    with pytest.raises(SchemaViolationError):
        check_request(request_)


def test_listed_exceptions_pass() -> None:
    check_request(get("/api/documents/", more_like_id="1"))


def test_a_listed_mistyped_parameter_takes_the_value_paperless_really_reads() -> None:
    check_request(get("/api/documents/", shared_by__id="2"))


@pytest.mark.parametrize(
    "request_",
    [
        pytest.param(get("/api/documents/", archive_serial_number="two"), id="other parameter"),
        pytest.param(get("/api/tags/", shared_by__id="2"), id="other endpoint"),
    ],
)
def test_a_mistyped_parameter_exemption_covers_only_that_parameter(
    request_: httpx2.Request,
) -> None:
    with pytest.raises(SchemaViolationError):
        check_request(request_)


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(200, json={"count": "one", "results": []}),
        httpx2.Response(200, json={"count": 1, "results": [{"id": 1}]}),
        httpx2.Response(201, json=TAG_PAGE),
        httpx2.Response(404, json={"detail": 404}),
        httpx2.Response(404, text="<html>Not Found</html>"),
    ],
    ids=["wrong type", "incomplete item", "undocumented status", "not DRF", "not JSON"],
)
def test_rejects_responses_outside_the_schema(response: httpx2.Response) -> None:
    with pytest.raises(SchemaViolationError):
        check_response(get("/api/tags/"), response)


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(404, json={"detail": "No Document matches the given query."}),
        httpx2.Response(400, json={"name": ["This field is required."]}),
        httpx2.Response(400, json={"custom_fields": [{"value": ["Not a valid date."]}]}),
        httpx2.Response(503, text="<html>Service Unavailable</html>"),
    ],
)
def test_accepts_drf_errors_and_any_server_error(response: httpx2.Response) -> None:
    check_response(get("/api/documents/1/"), response)


@pytest.mark.anyio
async def test_a_wrong_request_fails_the_adapter_call(make_client: ClientFactory) -> None:
    client = make_client(lambda _: httpx2.Response(200, json=TAG_PAGE))

    with pytest.raises(SchemaViolationError, match="undeclared query parameters"):
        _ = await client.request("GET", "/api/tags/", params={"nmae__icontains": "x"})


@pytest.mark.anyio
async def test_a_wrong_recorded_response_fails_the_adapter_call(make_client: ClientFactory) -> None:
    client = make_client(lambda _: httpx2.Response(200, json={"id": 1, "name": "inbox"}))

    with pytest.raises(SchemaViolationError, match="response does not match"):
        _ = await client.request("GET", "/api/tags/1/")


@pytest.mark.parametrize(
    ("path", "response"),
    [
        pytest.param("/api/status/", httpx2.Response(200, json={"tasks": {}}), id="status 200"),
        pytest.param("/api/status/", httpx2.Response(403, text="Forbidden"), id="status 403"),
        pytest.param(
            "/api/ui_settings/", httpx2.Response(200, json={"user": {}}), id="ui settings 200"
        ),
    ],
)
def test_listed_response_deviations_pass(path: str, response: httpx2.Response) -> None:
    check_response(get(path), response)


def test_a_response_deviation_covers_only_its_status() -> None:
    with pytest.raises(SchemaViolationError):
        check_response(get("/api/status/"), httpx2.Response(404, text="<html>Not Found</html>"))


FILE_PATHS = ["/api/documents/1/thumb/", "/api/documents/1/download/"]
FILE_HEADERS = {
    "Content-Type": "application/pdf",
    "Content-Disposition": 'inline; filename="a.pdf"',
}


@pytest.mark.parametrize("path", FILE_PATHS)
def test_a_file_response_passes_although_the_schema_says_json(path: str) -> None:
    response = httpx2.Response(200, content=b"%PDF-1.4", headers=FILE_HEADERS)

    check_response(get(path), response)


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(200, content=b"", headers=FILE_HEADERS),
        httpx2.Response(
            200, content=b"{}", headers={**FILE_HEADERS, "Content-Type": "application/json"}
        ),
        httpx2.Response(200, content=b"x", headers={"Content-Disposition": "inline"}),
        httpx2.Response(200, content=b"x", headers={"Content-Type": "application/pdf"}),
        httpx2.Response(200, content=b"x", headers={**FILE_HEADERS, "Content-Disposition": "x"}),
        httpx2.Response(
            200, content=b"{}", headers={**FILE_HEADERS, "Content-Type": "Application/JSON; x=1"}
        ),
        httpx2.Response(204, headers=FILE_HEADERS),
    ],
    ids=[
        "empty",
        "json",
        "no content type",
        "no disposition",
        "odd disposition",
        "json in other case",
        "undocumented",
    ],
)
def test_a_file_response_is_still_checked_for_what_it_must_have(response: httpx2.Response) -> None:
    with pytest.raises(SchemaViolationError):
        check_response(get("/api/documents/1/download/"), response)


@pytest.mark.parametrize("path", ["/api/documents/1/metadata/", "/api/documents/1/preview/"])
def test_the_file_exemption_covers_only_the_endpoints_the_adapter_uses(path: str) -> None:
    response = httpx2.Response(200, content=b"%PDF-1.4", headers=FILE_HEADERS)

    with pytest.raises(SchemaViolationError):
        check_response(get(path), response)


def test_a_hidden_document_is_answered_with_html_by_the_file_endpoints() -> None:
    response = httpx2.Response(
        403, text="Insufficient permissions", headers={"Content-Type": "text/html"}
    )

    check_response(get("/api/documents/1/thumb/"), response)
    check_response(get("/api/documents/1/download/"), response)


def patch(body: object) -> httpx2.Request:
    return httpx2.Request(
        "PATCH", BASE_URL + "/api/documents/1/", json=body, params={"fields": "id"}, headers=HEADERS
    )


def test_a_document_patch_may_carry_boolean_and_id_list_custom_field_values() -> None:
    check_request(
        patch(
            {
                "title": "t",
                "custom_fields": [
                    {"field": 1, "value": True},
                    {"field": 2, "value": [3, 4]},
                    {"field": 3, "value": "EUR12.50"},
                ],
            }
        )
    )


@pytest.mark.parametrize(
    "body",
    [
        {"title": 5},
        {"tags": "one"},
        {"custom_fields": [{"field": "one", "value": True}]},
        {"custom_fields": [{"value": True}]},
        {"custom_fields": [{"field": 1, "value": None}]},
        {"custom_fields": [{"field": 1, "value": {"nested": 1}}, {"field": 2}]},
    ],
)
def test_the_rest_of_a_document_patch_is_still_checked(body: object) -> None:
    with pytest.raises(SchemaViolationError):
        check_request(patch(body))


def upload(
    fields: dict[str, str | list[str]], *, document: bool = True, headers: dict[str, str] = HEADERS
) -> httpx2.Request:
    """A multipart upload; `document=False` leaves out the one required part."""
    files = (
        {"document": ("a.pdf", b"%PDF-1.4", "application/pdf")} if document else {"t": ("", b"")}
    )
    request = httpx2.Request(
        "POST",
        BASE_URL + "/api/documents/post_document/",
        data=fields,
        files=files,
        headers=headers,
    )
    _ = request.read()
    return request


def test_a_form_upload_with_plain_values_passes() -> None:
    check_request(
        upload({"title": "t", "created": "2026-02-03", "tags": ["1", "2"], "correspondent": "3"})
    )
    check_request(upload({"created": "2026-02-03 06:15:00+02:00", "archive_serial_number": "0"}))


@pytest.mark.parametrize(
    "request_",
    [
        pytest.param(upload({"title": "t", "colour": "red"}), id="undeclared field"),
        pytest.param(upload({"title": "t"}, document=False), id="no document"),
        pytest.param(upload({"correspondent": "one"}), id="integer that is not one"),
        pytest.param(upload({"tags": ["1", "x"]}), id="a tag that is not an integer"),
        pytest.param(upload({"archive_serial_number": "-1"}), id="below the minimum"),
        pytest.param(upload({"archive_serial_number": "4294967296"}), id="above the maximum"),
        pytest.param(upload({"created": "yesterday"}), id="not a date"),
        pytest.param(upload({"title": ""}), id="an empty title"),
        pytest.param(upload({"custom_fields": "{not json"}), id="custom fields that are no JSON"),
        pytest.param(upload({"custom_fields": '{"1": '}), id="custom fields cut short"),
        pytest.param(upload({"title": "t"}, headers={"Accept": HEADERS["Accept"]}), id="no token"),
    ],
)
def test_a_form_upload_outside_the_schema_is_rejected(request_: httpx2.Request) -> None:
    with pytest.raises(SchemaViolationError):
        check_request(request_)


def test_a_json_body_for_the_upload_is_rejected() -> None:
    request = httpx2.Request(
        "POST", BASE_URL + "/api/documents/post_document/", json={"title": "t"}, headers=HEADERS
    )

    with pytest.raises(SchemaViolationError, match="not multipart"):
        check_request(request)


def test_the_document_must_be_a_file_part_not_a_text_field() -> None:
    request = httpx2.Request(
        "POST",
        BASE_URL + "/api/documents/post_document/",
        data={"document": "just text"},
        files={"other": ("", b"")},
        headers=HEADERS,
    )
    _ = request.read()

    with pytest.raises(SchemaViolationError, match="document is not a file"):
        check_request(request)


def test_valid_custom_fields_pass() -> None:
    check_request(upload({"custom_fields": '{"1": "a", "2": [3, 4], "3": true}'}))
    check_request(upload({"custom_fields": "[1, 2]"}))


# The version endpoints of PLMCP-24

HTML = {"Content-Type": "text/html"}


def version_form(
    fields: dict[str, str], *, document: bool = True, path: str = "/api/documents/1/update_version/"
) -> httpx2.Request:
    """A multipart version upload; `document=False` leaves out the one required part."""
    files = (
        {"document": ("v2.pdf", b"%PDF-1.4", "application/pdf")} if document else {"t": ("", b"")}
    )
    request = httpx2.Request("POST", BASE_URL + path, data=fields, files=files, headers=HEADERS)
    _ = request.read()
    return request


def test_a_version_upload_form_with_and_without_a_label_passes() -> None:
    check_request(version_form({}))
    check_request(version_form({"version_label": "final"}))
    check_request(version_form({"version_label": "x" * 64}))


@pytest.mark.parametrize(
    "request_",
    [
        pytest.param(version_form({"title": "t"}), id="undeclared field"),
        pytest.param(version_form({}, document=False), id="no document"),
        pytest.param(version_form({"version_label": "x" * 65}), id="a label over 64 characters"),
        pytest.param(
            httpx2.Request(
                "POST",
                BASE_URL + "/api/documents/1/update_version/",
                json={"version_label": "x"},
                headers=HEADERS,
            ),
            id="JSON instead of a form",
        ),
    ],
)
def test_a_version_upload_form_outside_the_schema_is_rejected(request_: httpx2.Request) -> None:
    with pytest.raises(SchemaViolationError):
        check_request(request_)


def test_the_label_patch_and_the_merge_body_are_checked_against_their_schemas() -> None:
    label = httpx2.Request(
        "PATCH",
        BASE_URL + "/api/documents/1/versions/2/",
        json={"version_label": None},
        headers=HEADERS,
    )
    merge = httpx2.Request(
        "POST",
        BASE_URL + "/api/documents/merge_as_versions/",
        json={"documents": [1, 2], "root_document_id": 1, "version_label": "x"},
        headers=HEADERS,
    )
    check_request(label)
    check_request(merge)
    for bad in (
        httpx2.Request(
            "PATCH",
            BASE_URL + "/api/documents/1/versions/2/",
            json={"version_label": "x" * 65},
            headers=HEADERS,
        ),
        httpx2.Request(
            "POST",
            BASE_URL + "/api/documents/merge_as_versions/",
            json={"documents": [1, 2]},
            headers=HEADERS,
        ),
    ):
        with pytest.raises(SchemaViolationError):
            check_request(bad)


def test_deleting_a_version_is_answered_200_with_the_current_version_not_204() -> None:
    request = httpx2.Request("DELETE", BASE_URL + "/api/documents/1/versions/2/", headers=HEADERS)

    check_response(request, httpx2.Response(200, json={"result": "OK", "current_version_id": 1}))
    check_response(request, httpx2.Response(400, text="Cannot delete the root", headers=HTML))


def test_a_viewer_merging_is_answered_with_html() -> None:
    request = httpx2.Request(
        "POST",
        BASE_URL + "/api/documents/merge_as_versions/",
        json={"documents": [1, 2], "root_document_id": 1},
        headers=HEADERS,
    )

    check_response(request, httpx2.Response(403, text="Insufficient permissions", headers=HTML))


VIEW = {
    "id": 1,
    "name": "v",
    "icon": "funnel",
    "sort_field": None,
    "sort_reverse": False,
    "filter_rules": [{"rule_type": 6, "value": "1"}],
    "page_size": None,
    "display_mode": None,
    "display_fields": None,
    "owner": 2,
    "user_can_change": True,
}
VIEW_PAGE = {"count": 1, "next": None, "previous": None, "results": [VIEW]}


@pytest.mark.parametrize(
    ("request_", "status", "body"),
    [
        (get("/api/saved_views/"), 200, VIEW_PAGE),
        (get("/api/saved_views/1/"), 200, VIEW),
        (
            httpx2.Request(
                "POST",
                BASE_URL + "/api/saved_views/",
                json={"name": "v", "filter_rules": []},
                headers=HEADERS,
            ),
            201,
            VIEW,
        ),
        (
            httpx2.Request(
                "PATCH", BASE_URL + "/api/saved_views/1/", json={"name": "w"}, headers=HEADERS
            ),
            200,
            VIEW,
        ),
    ],
    ids=["list", "get", "create", "update"],
)
def test_a_saved_view_without_the_permissions_the_schema_requires_passes(
    request_: httpx2.Request, status: int, body: dict[str, object]
) -> None:
    check_response(request_, httpx2.Response(status, json=body))


@pytest.mark.parametrize(
    "body",
    [
        {**VIEW, "name": 5},
        {**VIEW, "filter_rules": [{"rule_type": 99, "value": "1"}]},
        {key: value for key, value in VIEW.items() if key != "name"},
    ],
    ids=["wrong type", "unknown rule type", "missing name"],
)
def test_the_rest_of_a_saved_view_is_still_checked(body: dict[str, object]) -> None:
    with pytest.raises(SchemaViolationError):
        check_response(get("/api/saved_views/1/"), httpx2.Response(200, json=body))
    with pytest.raises(SchemaViolationError):
        check_response(
            get("/api/saved_views/"),
            httpx2.Response(200, json={**VIEW_PAGE, "results": [body]}),
        )


def test_a_full_perms_answer_may_lack_user_can_change_only_where_it_is_listed() -> None:
    row = {k: v for k, v in tag(1, "inbox").items() if k != "user_can_change"}
    page = {"count": 1, "next": None, "previous": None, "results": [row]}
    listed = get("/api/tags/", full_perms="true")
    plain = get("/api/tags/")
    unlisted = get("/api/tags/1/", full_perms="true")  # GET of one tag is not in the table

    check_response(listed, httpx2.Response(200, json=page))
    with pytest.raises(SchemaViolationError):
        check_response(plain, httpx2.Response(200, json=page))  # no full_perms: required
    with pytest.raises(SchemaViolationError):
        check_response(unlisted, httpx2.Response(200, json=row))


def test_a_full_perms_answer_is_still_checked_for_the_rest() -> None:
    row = {k: v for k, v in tag(1, "inbox").items() if k not in {"user_can_change", "name"}}
    page = {"count": 1, "next": None, "previous": None, "results": [row]}

    with pytest.raises(SchemaViolationError):
        check_response(get("/api/tags/", full_perms="true"), httpx2.Response(200, json=page))


WORKFLOW = {
    "id": 1,
    "name": "w",
    "order": 0,
    "enabled": True,
    "triggers": [{"id": 1, "type": 2, "sources": [1, 2, 3], "filter_has_tags": [1]}],
    "actions": [{"id": 1, "type": 1, "assign_title": "x", "email": None, "webhook": None}],
}
WORKFLOW_PAGE = {"count": 1, "next": None, "previous": None, "results": [WORKFLOW]}


@pytest.mark.parametrize(
    ("request_", "status", "body"),
    [
        (get("/api/workflows/"), 200, WORKFLOW_PAGE),
        (get("/api/workflows/1/"), 200, WORKFLOW),
        (
            httpx2.Request(
                "POST",
                BASE_URL + "/api/workflows/",
                json={"name": "w", "triggers": [], "actions": []},
                headers=HEADERS,
            ),
            201,
            WORKFLOW,
        ),
        (
            httpx2.Request(
                "PATCH", BASE_URL + "/api/workflows/1/", json={"name": "v"}, headers=HEADERS
            ),
            200,
            WORKFLOW,
        ),
    ],
    ids=["list", "get", "create", "update"],
)
def test_a_workflow_action_with_null_email_and_webhook_passes(
    request_: httpx2.Request, status: int, body: dict[str, object]
) -> None:
    check_response(request_, httpx2.Response(status, json=body))


@pytest.mark.parametrize(
    "body",
    [
        {**WORKFLOW, "name": 5},
        {**WORKFLOW, "triggers": [{"id": 1, "type": 99}]},
        {**WORKFLOW, "actions": [{"id": 1, "type": 3, "email": {"subject": "s"}}]},
        {key: value for key, value in WORKFLOW.items() if key != "name"},
    ],
    ids=["wrong type", "unknown trigger type", "incomplete email", "missing name"],
)
def test_the_rest_of_a_workflow_is_still_checked(body: dict[str, object]) -> None:
    with pytest.raises(SchemaViolationError):
        check_response(get("/api/workflows/1/"), httpx2.Response(200, json=body))
    with pytest.raises(SchemaViolationError):
        check_response(
            get("/api/workflows/"),
            httpx2.Response(200, json={**WORKFLOW_PAGE, "results": [body]}),
        )


QUEUED_OPERATIONS = [
    "bulk_edit",
    "delete",
    "edit_pdf",
    "merge",
    "merge_as_versions",
    "remove_password",
    "reprocess",
    "rotate",
]


def queued(operation: str) -> httpx2.Request:
    return httpx2.Request(
        "POST", f"{BASE_URL}/api/documents/{operation}/", json={}, headers=HEADERS
    )


@pytest.mark.parametrize("operation", QUEUED_OPERATIONS)
def test_a_queued_operation_answers_with_the_task_it_queued(operation: str) -> None:
    check_response(queued(operation), httpx2.Response(200, json={"result": "OK", "task_id": "t-1"}))


@pytest.mark.parametrize(
    "body", [{"result": "OK"}, {"task_id": "t-1"}, {"result": "OK", "task_id": 5}]
)
def test_a_queued_answer_without_its_task_or_with_a_wrong_one_is_refused(
    body: dict[str, object],
) -> None:
    with pytest.raises(SchemaViolationError):
        check_response(queued("rotate"), httpx2.Response(200, json=body))


# The guard records what each adapter method sends, for the coverage table (PLMCP-91).
TRASH_PAGE: dict[str, object] = {"count": 0, "next": None, "previous": None, "results": []}


@pytest.mark.parametrize(
    ("code_name", "name"),
    [
        ("list_trash", "list_trash"),
        ("xǁPaperlessTrashReaderǁlist_trash__mutmut_orig", "list_trash"),
        ("xǁPaperlessTrashReaderǁ_rows__mutmut_12", "_rows"),
    ],
)
def test_the_recorded_method_is_the_one_of_the_source_in_a_mutation_run_too(
    code_name: str, name: str
) -> None:
    assert method_name(code_name) == name


@pytest.mark.anyio
async def test_a_request_is_recorded_with_the_port_method_that_sent_it(
    make_client: ClientFactory,
) -> None:
    start = len(OBSERVED)
    reader = PaperlessTrashReader(make_client(lambda _: httpx2.Response(200, json=TRASH_PAGE)))

    _ = await reader.list_trash(page=2, page_size=5, max_bytes=10**6)

    assert OBSERVED[start:] == [
        Observation(
            tool=None,
            call=("TrashReader", "list_trash"),
            operation=("GET", "/api/trash/"),
            query=frozenset({"page", "page_size"}),
            body=frozenset(),
            values=frozenset({"query:page=2", "query:page_size=5"}),
        )
    ]


@pytest.mark.anyio
async def test_the_fields_of_a_json_body_are_recorded(make_client: ClientFactory) -> None:
    start = len(OBSERVED)
    writer = PaperlessTrashWriter(
        make_client(lambda _: httpx2.Response(200, json={"result": "OK"}))
    )

    await writer.restore_from_trash((1,))

    [seen] = OBSERVED[start:]
    assert seen.call == ("TrashWriter", "restore_from_trash")
    assert seen.operation == ("POST", "/api/trash/")
    assert seen.body == {"action", "documents"}


@pytest.mark.anyio
async def test_the_fields_of_a_form_are_recorded(make_client: ClientFactory) -> None:
    start = len(OBSERVED)
    writer = PaperlessDocumentWriter(make_client(lambda _: httpx2.Response(200, json="task-1")))
    upload = DocumentUpload(
        filename="a.pdf", mime_type="application/pdf", content=b"%PDF-1.4", title="T"
    )

    _ = await writer.upload_document(upload)

    [seen] = OBSERVED[start:]
    assert seen.operation == ("POST", "/api/documents/post_document/")
    assert seen.body == {"document", "title"}


@pytest.mark.anyio
async def test_a_request_sent_outside_the_adapter_has_no_port_method(
    make_client: ClientFactory,
) -> None:
    start = len(OBSERVED)
    client = make_client(lambda _: httpx2.Response(200, json=TAG_PAGE))

    _ = await client.request("GET", "/api/tags/")

    assert OBSERVED[start:] == []


@pytest.mark.anyio
async def test_a_streamed_request_is_recorded_although_the_guard_cannot_check_it() -> None:
    start = len(OBSERVED)
    client, _ = streaming(lambda _: httpx2.Response(200, content=b"PK-zip"))

    _ = await PaperlessAdminReader(client).download_documents(
        [1], content="both", compression="deflated", follow_formatting=True, max_bytes=100
    )

    [seen] = OBSERVED[start:]
    assert seen.call == ("AdminReader", "download_documents")
    assert seen.operation == ("POST", "/api/documents/bulk_download/")
    assert seen.body == {"documents", "content", "compression", "follow_formatting"}


@pytest.mark.anyio
async def test_a_good_answer_is_kept_per_operation_for_the_replay(
    make_client: ClientFactory,
) -> None:
    _ = RESPONSES.pop(("GET", "/api/trash/"), None)
    reader = PaperlessTrashReader(make_client(lambda _: httpx2.Response(200, json=TRASH_PAGE)))

    _ = await reader.list_trash(page=1, page_size=5, max_bytes=10**6)

    status, headers, content = RESPONSES[("GET", "/api/trash/")]
    assert (status, headers["content-type"]) == (200, "application/json")
    assert "content-length" not in headers
    assert json.loads(content) == TRASH_PAGE


def test_a_response_that_may_be_empty_is_checked_when_it_is_not() -> None:
    request = httpx2.Request(
        "POST",
        "http://paperless.test/api/storage_paths/test/",
        json={"document": 1, "path": "x"},
    )

    check_response(request, httpx2.Response(200, content=b""))
    check_response(request, httpx2.Response(200, json="a.pdf"))
    for wrong in ({"path": "a.pdf"}, 5, ["a.pdf"]):
        with pytest.raises(SchemaViolationError):
            check_response(request, httpx2.Response(200, json=wrong))
