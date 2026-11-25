"""FakePaperless behaviour the contract suite cannot reach with the seeded users.

Each rule mirrors Paperless 3.4.2 (documents/views.py and documents/permissions.py of the tag);
the adapter tests check the adapter's side of the same cases on recorded responses.
"""

from datetime import UTC, datetime, timedelta

import pytest
from e2e.helpers import SEEDED_OBJECTS

from fakes.paperless import (
    FakeDocument,
    FakePaperless,
    FakeState,
    FakeTag,
    FakeTask,
    FakeUser,
    WriteNotAllowedError,
    seeded,
    write,
)
from mcp_server_paperless.domain.choices import picking
from mcp_server_paperless.domain.documents import DocumentUpload
from mcp_server_paperless.domain.errors import (
    FeatureUnavailable,
    NotFound,
    PermissionDenied,
    Validation,
)
from mcp_server_paperless.domain.pdf import PageSlot
from mcp_server_paperless.paperless.taxonomy import TAG_DELETE_HINT

pytestmark = [pytest.mark.anyio, pytest.mark.decision("decision-8")]


def state_with(*users: FakeUser) -> FakeState:
    state = seeded()
    state.users.extend(users)
    return state


async def test_without_ui_settings_permission_the_user_and_ai_flag_are_unknown() -> None:
    state = state_with(FakeUser(id=10, username="bare", permissions=frozenset({"view_document"})))

    overview = await FakePaperless(state, user="bare").get_overview()

    assert (overview.current_user, overview.ai_enabled) == (None, None)
    assert overview.statistics.documents == 1


async def test_staff_and_system_monitoring_grant_the_system_status() -> None:
    state = state_with(
        FakeUser(id=10, username="staff", is_staff=True),
        FakeUser(id=11, username="monitor", permissions=frozenset({"view_system_monitoring"})),
    )

    for user in ("staff", "monitor"):
        assert (await FakePaperless(state, user=user).get_overview()).system_status is not None


async def test_global_statistics_count_every_object() -> None:
    state = state_with(
        FakeUser(id=10, username="stats", permissions=frozenset({"view_global_statistics"}))
    )

    statistics = (await FakePaperless(state, user="stats").get_overview()).statistics

    assert (statistics.documents, statistics.tags) == (2, 4)


async def test_an_object_level_view_grant_makes_an_object_visible() -> None:
    state = seeded()
    reader = next(user for user in state.users if user.username == "e2e-reader")
    state.documents.append(FakeDocument(id=99, title="granted", owner=1, viewers={reader.id}))

    statistics = (await FakePaperless(state, user="e2e-reader").get_overview()).statistics

    assert statistics.documents == 2


async def test_inbox_documents_are_unknown_without_a_visible_inbox_tag() -> None:
    state = seeded()
    state.tags = [tag for tag in state.tags if not tag.is_inbox]

    statistics = (await FakePaperless(state, user="admin").get_overview()).statistics

    assert statistics.inbox_documents is None


async def test_ai_enabled_follows_the_instance_configuration() -> None:
    state = seeded()
    state.ai_enabled = True

    assert (await FakePaperless(state, user="admin").get_overview()).ai_enabled is True


def test_an_unknown_user_is_a_test_setup_error() -> None:
    with pytest.raises(LookupError, match="nobody"):
        _ = FakePaperless(seeded(), user="nobody")


class TaggingFake(FakePaperless):
    """A test-only write, standing in for the write ports later tasks add."""

    @write
    async def add_tag(self, name: str) -> int:
        tag = FakeTag(id=len(self.state.tags) + 1, name=name, owner=self.user.id)
        self.state.tags.append(tag)
        return tag.id


async def test_a_write_fails_unless_the_test_opts_in() -> None:
    state = seeded()
    before = list(state.tags)

    with pytest.raises(WriteNotAllowedError, match=r"add_tag.*allow_writes=True"):
        _ = await TaggingFake(state, user="admin").add_tag("new")

    assert state.tags == before


async def test_a_write_runs_when_the_test_opts_in() -> None:
    state = seeded()

    tag_id = await TaggingFake(state, user="admin", allow_writes=True).add_tag("new")

    assert [tag.name for tag in state.tags if tag.id == tag_id] == ["new"]


async def test_reads_need_no_opt_in() -> None:
    overview = await TaggingFake(seeded(), user="admin").get_overview()

    assert overview.statistics.documents == 2


async def test_the_seed_matches_the_e2e_seed_manifest() -> None:
    state = seeded()
    admin = (await FakePaperless(state, user="admin").get_overview()).statistics
    reader = (await FakePaperless(state, user="e2e-reader").get_overview()).statistics

    assert {
        "documents": (reader.documents, admin.documents - reader.documents),
        "inbox_documents": (
            reader.inbox_documents,
            (admin.inbox_documents or 0) - (reader.inbox_documents or 0),
        ),
        "tags": (reader.tags, admin.tags - reader.tags),
        "correspondents": (reader.correspondents, admin.correspondents - reader.correspondents),
        "document_types": (reader.document_types, admin.document_types - reader.document_types),
        "storage_paths": (reader.storage_paths, admin.storage_paths - reader.storage_paths),
    } == SEEDED_OBJECTS


LOG_VIEWER = FakeUser(
    id=10, username="log-viewer", permissions=frozenset({"view_document", "view_logentry"})
)


async def test_the_history_needs_ownership_not_just_an_object_level_view_grant() -> None:
    # Live 3.4.2: a user with view_document + view_logentry and a view grant on a document
    # owned by someone else reads the document but gets 403 on /history/ (views.py `history`).
    state = state_with(LOG_VIEWER)
    state.documents[1].viewers.add(LOG_VIEWER.id)
    fake = FakePaperless(state, user=LOG_VIEWER.username)

    assert (await fake.get_document(2)).id == 2
    with pytest.raises(PermissionDenied):
        _ = await fake.list_document_history(2)
    assert await fake.list_document_history(1)


async def test_a_missing_document_is_not_found_even_without_the_log_permission() -> None:
    state = state_with(FakeUser(id=11, username="plain", permissions=frozenset({"view_document"})))

    with pytest.raises(NotFound):
        _ = await FakePaperless(state, user="plain").list_document_history(999_999)


async def test_a_disabled_audit_log_is_a_feature_error_before_anything_else() -> None:
    state = seeded()
    state.audit_log_enabled = False

    with pytest.raises(FeatureUnavailable) as raised:
        _ = await FakePaperless(state, user="admin").list_document_history(999_999)

    assert raised.value.hint


NOTER = FakeUser(
    id=12,
    username="noter",
    permissions=frozenset(
        {"view_document", "view_note", "add_note", "delete_note", "change_document"}
    ),
)
ERASER = FakeUser(id=13, username="eraser", permissions=frozenset({"delete_document"}))


async def test_notes_need_a_visible_document_the_user_may_change() -> None:
    # Live 3.4.2, probed with a temporary user: on a document owned by someone else, a view-only
    # user reads the notes but is refused (403) when adding or deleting one, a hidden document
    # is 403 for every note action, and an unowned or own document is fine.
    state = state_with(NOTER)
    state.documents.extend(
        [
            FakeDocument(id=50, title="view-only", owner=1, viewers={NOTER.id}),
            FakeDocument(id=51, title="hidden", owner=1),
            FakeDocument(id=52, title="unowned"),
            FakeDocument(id=53, title="own", owner=NOTER.id),
            FakeDocument(id=54, title="shared", owner=1, viewers={NOTER.id}, editors={NOTER.id}),
        ]
    )
    admin = FakePaperless(state, user="admin", allow_writes=True)
    fake = FakePaperless(state, user=NOTER.username, allow_writes=True)
    left_by_admin = await admin.add_note(50, "adm")

    assert await fake.list_document_notes(50)
    with pytest.raises(PermissionDenied):
        _ = await fake.add_note(50, "x")
    with pytest.raises(PermissionDenied):
        await fake.delete_note(50, left_by_admin.id)
    with pytest.raises(PermissionDenied):
        _ = await fake.add_note(51, "x")
    for allowed in (52, 53, 54):
        assert (await fake.add_note(allowed, "ok")).user == "noter"


async def test_deleting_a_document_needs_ownership_and_no_view_permission() -> None:
    # Live 3.4.2, probed with temporary users: with delete_document alone a user deletes an
    # unowned document (204); a view-only or change-granted document is 403, a hidden one 404.
    state = state_with(ERASER)
    state.documents.extend(
        [
            FakeDocument(id=60, title="unowned"),
            FakeDocument(id=61, title="view-only", owner=1, viewers={ERASER.id}),
            FakeDocument(id=62, title="shared", owner=1, viewers={ERASER.id}, editors={ERASER.id}),
            FakeDocument(id=63, title="hidden", owner=1),
        ]
    )
    fake = FakePaperless(state, user=ERASER.username, allow_writes=True)

    for refused in (61, 62):
        with pytest.raises(PermissionDenied):
            await fake.delete_document(refused)
    with pytest.raises(NotFound):
        await fake.delete_document(63)
    await fake.delete_document(60)

    assert [d.id for d in state.trash] == [60]
    assert 60 not in {d.id for d in state.documents}


async def test_note_ids_are_never_reused() -> None:
    state = seeded()
    fake = FakePaperless(state, user="admin", allow_writes=True)
    first = await fake.add_note(1, "one")
    await fake.delete_note(1, first.id)

    second = await fake.add_note(1, "two")

    assert second.id > first.id


async def test_notes_and_deletion_write_nothing_without_the_opt_in() -> None:
    state = seeded()
    fake = FakePaperless(state, user="admin")

    with pytest.raises(WriteNotAllowedError):
        _ = await fake.add_note(1, "x")
    with pytest.raises(WriteNotAllowedError):
        await fake.delete_note(1, 1)
    with pytest.raises(WriteNotAllowedError):
        await fake.delete_document(1)

    assert (len(state.documents), len(state.trash), len(state.documents[0].notes)) == (2, 0, 1)


async def test_an_upload_fails_unless_the_test_opts_in_and_leaves_no_task() -> None:
    state = seeded()
    upload = DocumentUpload(filename="a.pdf", mime_type="application/pdf", content=b"%PDF-1.4")

    with pytest.raises(WriteNotAllowedError, match=r"upload_document.*allow_writes=True"):
        _ = await FakePaperless(state, user="admin").upload_document(upload)

    assert state.tasks == []
    assert len(state.documents) == 2


# --- what 3.5 changed, which the fake models (decision-43)


def writer(state: FakeState, user: str) -> FakePaperless:
    return FakePaperless(state, user=user, allow_writes=True)


@pytest.mark.decision("decision-43")
async def test_the_fake_is_the_reference_release() -> None:
    assert (await FakePaperless(seeded(), user="admin").get_overview()).paperless_version == (
        "3.5.1"
    )


@pytest.mark.decision("decision-43")
async def test_reading_the_configuration_needs_its_permission() -> None:
    nothing = FakeUser(id=10, username="nothing", permissions=frozenset({"view_uisettings"}))
    allowed = FakeUser(
        id=11, username="allowed", permissions=frozenset({"view_applicationconfiguration"})
    )
    state = state_with(nothing, allowed)

    with pytest.raises(PermissionDenied, match="read the application configuration") as denied:
        _ = await FakePaperless(state, user="nothing").get_config()
    assert denied.value.hint
    assert (await FakePaperless(state, user="allowed").get_config()).id == 1
    assert (await FakePaperless(state, user="admin").get_config()).id == 1


@pytest.mark.decision("decision-43")
async def test_blank_notes_are_rejected() -> None:
    state = seeded()

    for text in ("", "   "):
        with pytest.raises(Validation, match="blank"):
            _ = await writer(state, "admin").add_note(1, text)
    assert (await writer(state, "admin").add_note(1, "text")).note == "text"


def tags_for_deletion(state: FakeState, *, owner_of_child: int | None) -> None:
    """Tag 20 (owned by the user "owner", id 10) with child 21 and grandchild 22."""
    state.tags += [
        FakeTag(id=20, name="parent", owner=10),
        FakeTag(id=21, name="child", parent=20, owner=owner_of_child),
        FakeTag(id=22, name="grandchild", parent=21, owner=2),
    ]


OWNER = FakeUser(id=10, username="owner", permissions=frozenset({"view_tag", "delete_tag"}))


@pytest.mark.decision("decision-43")
@pytest.mark.parametrize("bulk", [False, True])
async def test_a_tag_deletion_that_takes_tags_of_others_is_refused(*, bulk: bool) -> None:
    state = state_with(OWNER)
    tags_for_deletion(state, owner_of_child=None)

    # The grandchild is the admin's: Paperless refuses to delete it with its parent.
    async def delete() -> None:
        if bulk:
            await writer(state, "owner").delete_taxonomy_items("tag", (20,))
        else:
            await writer(state, "owner").delete_taxonomy_item("tag", 20)

    with pytest.raises(PermissionDenied) as refused:
        await delete()

    assert {20, 21, 22} <= {tag.id for tag in state.tags}
    # The single deletion gets the hint of the adapter; the bulk one that of its own refusal.
    assert refused.value.hint
    assert (refused.value.hint == TAG_DELETE_HINT) is not bulk
    assert ("nothing was changed" in refused.value.message) is bulk


@pytest.mark.decision("decision-43")
@pytest.mark.parametrize("bulk", [False, True])
async def test_a_tag_deletion_that_takes_only_own_or_unowned_tags_works(*, bulk: bool) -> None:
    state = state_with(OWNER)
    tags_for_deletion(state, owner_of_child=10)
    state.tags[-1].owner = None

    if bulk:
        await writer(state, "owner").delete_taxonomy_items("tag", (20,))
    else:
        await writer(state, "owner").delete_taxonomy_item("tag", 20)

    assert {20, 21, 22}.isdisjoint(tag.id for tag in state.tags)


@pytest.mark.decision("decision-43")
async def test_a_superuser_deletes_any_tree() -> None:
    state = state_with(OWNER)
    tags_for_deletion(state, owner_of_child=10)

    await writer(state, "admin").delete_taxonomy_item("tag", 20)

    assert {20, 21, 22}.isdisjoint(tag.id for tag in state.tags)


@pytest.mark.decision("decision-48")
@pytest.mark.parametrize("wrong", ["a\x00b", "a\ud800b"])
async def test_the_text_of_the_root_takes_no_nul_and_no_lone_surrogate(wrong: str) -> None:
    state = state_with()
    before = state.documents[0].content
    fake = FakePaperless(state, user="admin", allow_writes=True)

    with pytest.raises(Validation, match="not allowed"):
        _ = await fake.set_document_text(1, 1, wrong)

    assert state.documents[0].content == before


@pytest.mark.decision("decision-67")
async def test_the_search_by_name_doubts_listed_tasks_and_earlier_requests_of_the_last_hour() -> (
    None
):
    state = seeded()
    [field] = [f.id for f in state.custom_fields if f.data_type == "date"]
    state.documents.append(
        FakeDocument(id=50, title="Dated", owner=2, custom_fields={field: "2026-01-15"})
    )
    fake = FakePaperless(state, user="admin", allow_writes=True)
    page = (PageSlot(page=1),)
    first = await fake.edit_pdf(50, page, output="new_document")
    second = await fake.edit_pdf(50, page, output="new_document")
    assert first.files is not None
    assert second.files is not None

    pending = await fake.find_copies(second.files)
    state.copying[0].started -= timedelta(hours=2)
    found = await fake.find_copies(second.files)
    state.tasks.append(
        FakeTask(id=99, task_id="x", created=datetime.now(UTC), file_name="50_edit_1.pdf")
    )
    tasked = await fake.find_copies(second.files)

    assert (pending.doubt, pending.passing) == (
        "files of earlier operations of this user may still come",
        True,
    )
    assert found.doubt is None
    assert (tasked.doubt, tasked.ids) == (
        "a task of its own makes a file of one of these names",
        (None,),
    )


@pytest.mark.decision("decision-109")
async def test_a_write_of_the_fake_is_noted_and_a_read_is_not() -> None:
    fake = FakePaperless(seeded(), user="admin", allow_writes=True)

    with picking([]) as run:
        _ = await fake.list_taxonomy("tag")
        assert not run.written
        _ = await fake.create_tag("new")
        assert run.written
