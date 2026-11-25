"""Temporary probes P1 to P4 of PLMCP-88; deleted before the pull request is ready."""

import json
import uuid
from typing import TYPE_CHECKING

import httpx2
import pytest

from e2e.helpers import client, num, obj, parse

if TYPE_CHECKING:
    from e2e.helpers import Stack

pytestmark = pytest.mark.e2e

COMPUTED = {
    "trash_delay",
    "version",
    "app_title",
    "app_logo",
    "auditlog_enabled",
    "remote_ocr",
    "email_enabled",
    "ai_enabled",
    "gmail_oauth_url",
    "outlook_oauth_url",
}
UI = "/api/ui_settings/"
VIEWS = "/api/saved_views/"
VIEW_RIGHTS = ["view_savedview", "add_savedview", "change_savedview", "delete_savedview"]


class Probe:
    def __init__(self, stack: Stack) -> None:
        self.stack = stack
        self.lines: list[str] = []
        self.made: list[int] = []

    def note(self, label: str, answer: httpx2.Response | object) -> None:
        if isinstance(answer, httpx2.Response):
            answer = f"{answer.status_code} {answer.text[:600]}"
        self.lines.append(f"PROBE {label}: {answer}")

    def user(self, *rights: str) -> tuple[int, httpx2.Client]:
        name, password = f"contract-user-{uuid.uuid4().hex[:8]}", f"pw-{uuid.uuid4().hex}"
        body = {"username": name, "password": password, "user_permissions": list(rights)}
        made = self.stack.admin.post("/api/users/", json=body).raise_for_status()
        number = num(obj(parse(made))["id"])
        self.made.append(number)
        with httpx2.Client(
            base_url=self.stack.base_url,
            auth=(name, password),
            headers={"Accept": "application/json; version=10"},
        ) as signed_in:
            issued = signed_in.post("/api/profile/generate_auth_token/").raise_for_status()
        return number, client(str(parse(issued)))

    def view(self, api: httpx2.Client | None = None, **more: object) -> int:
        body = {"name": f"contract-view-e2e-{uuid.uuid4().hex[:8]}", "filter_rules": [], **more}
        made = (api or self.stack.admin).post(VIEWS, json=body)
        if made.is_error:
            self.note("create failed", made)
            return 0
        return num(obj(parse(made))["id"])

    def done(self) -> None:
        for row in obj(parse(self.stack.admin.get(VIEWS, params={"page_size": 200})))["results"]:
            if str(row["name"]).startswith("contract-view-e2e-"):
                _ = self.stack.admin.delete(f"{VIEWS}{row['id']}/")
        for number in self.made:
            _ = self.stack.admin.delete(f"/api/users/{number}/")
        pytest.fail("\n".join(self.lines))


def settings_of(api: httpx2.Client) -> dict:
    return obj(obj(parse(api.get(UI).raise_for_status()))["settings"])


def stored(settings: dict) -> dict:
    kept = {k: v for k, v in settings.items() if k not in COMPUTED}
    if isinstance(kept.get("update_checking"), dict):
        kept["update_checking"] = {
            k: v for k, v in kept["update_checking"].items() if k != "backend_setting"
        }
        if not kept["update_checking"]:
            del kept["update_checking"]
    return kept


def test_p1(stack: Stack) -> None:
    p = Probe(stack)
    _, api = p.user("view_uisettings", "add_uisettings")
    first = settings_of(api)
    p.note("P1 fresh", json.dumps(first))
    p.note("P1 post empty", api.post(UI, json={"settings": stored(first)}))
    p.note("P1 after empty equal", settings_of(api) == first)
    rich = {
        "language": "de-de",
        "nested": {"a": [1, 2.5, None, "x", {"b": True}], "Umlaut ä": "ü😀", "n": {}},
        "saved_views": {
            "dashboard_views_visible_ids": [3, "4", 999999],
            "sidebar_views_visible_ids": [],
            "dashboard_views_sort_order": [4, 3],
            "warn_on_unsaved_change": True,
        },
        "update_checking": {"enabled": True},
        "big": 12345678901234567890123,
        "float": 1e-7,
        "empty": None,
        "nul": "line\nbreak",
    }
    p.note("P1 post rich", api.post(UI, json={"settings": rich}))
    second = settings_of(api)
    p.note("P1 rich", json.dumps(second))
    p.note("P1 rich stored equal", stored(second) == rich)
    p.note("P1 post back", api.post(UI, json={"settings": stored(second)}))
    p.note("P1 round trip", settings_of(api) == second)
    p.note(
        "P1 post only backend", api.post(UI, json={"settings": {"update_checking": {"backend_setting": "x"}}})
    )
    p.note("P1 after only backend", json.dumps(settings_of(api)["update_checking"]))
    p.note("P1 post update_checking {}", api.post(UI, json={"settings": {"update_checking": {}}}))
    p.note("P1 after {}", json.dumps(settings_of(api)["update_checking"]))
    for odd in ([], "text", None, 5):
        p.note(f"P1 saved_views={odd!r}", api.post(UI, json={"settings": {"saved_views": odd}}))
        p.note("P1 read", json.dumps(settings_of(api).get("saved_views", "absent")))
    p.note("P1 update_checking text", api.post(UI, json={"settings": {"update_checking": "x"}}))
    p.note("P1 get then", api.get(UI).status_code)
    p.note("P1 repair", api.post(UI, json={"settings": {}}))
    p.note("P1 no settings", api.post(UI, json={}))
    p.note("P1 get after null", api.get(UI).status_code)
    p.note("P1 repair", api.post(UI, json={"settings": {}}))
    p.note("P1 bad nul char", api.post(UI, json={"settings": {"a": "\x00"}}).status_code)
    p.note("P1 get", api.get(UI).status_code)
    p.done()


def test_p2(stack: Stack) -> None:
    p = Probe(stack)
    owner_id, owner = p.user(*VIEW_RIGHTS)
    viewer_id, viewer = p.user(*VIEW_RIGHTS)
    changer_id, changer = p.user(*VIEW_RIGHTS)
    other_id, other = p.user(*VIEW_RIGHTS)
    view = p.view(owner)
    grants = {
        "view": {"users": [viewer_id], "groups": []},
        "change": {"users": [changer_id], "groups": []},
    }
    p.note("P2 owner grants", owner.patch(f"{VIEWS}{view}/", json={"set_permissions": grants}))
    for label, api in (("owner", owner), ("viewer", viewer), ("changer", changer)):
        p.note(f"P2 {label} full", api.get(f"{VIEWS}{view}/", params={"full_perms": "true"}))
        p.note(f"P2 {label} plain", api.get(f"{VIEWS}{view}/"))
    p.note("P2 other get", other.get(f"{VIEWS}{view}/", params={"full_perms": "true"}))
    for label, api in (("viewer", viewer), ("changer", changer), ("admin", stack.admin)):
        p.note(
            f"P2 {label} grants",
            api.patch(
                f"{VIEWS}{view}/", params={"full_perms": "true"}, json={"set_permissions": grants}
            ),
        )
        p.note(f"P2 {label} same owner", api.patch(f"{VIEWS}{view}/", json={"owner": owner_id}))
    p.note("P2 viewer rename", viewer.patch(f"{VIEWS}{view}/", json={"icon": "heart"}))
    p.note("P2 changer icon", changer.patch(f"{VIEWS}{view}/", json={"icon": "heart"}))
    p.note("P2 changer take owner", changer.patch(f"{VIEWS}{view}/", json={"owner": changer_id}))
    empty = {"view": {"users": [], "groups": []}, "change": {"users": [], "groups": []}}
    free = p.view(stack.admin, owner=None)
    p.note("P2 unowned", stack.admin.get(f"{VIEWS}{free}/", params={"full_perms": "true"}))
    p.note("P2 unowned by other", other.patch(f"{VIEWS}{free}/", params={"full_perms": "true"}, json={"set_permissions": grants}))
    p.note("P2 unowned unchanged grants", other.patch(f"{VIEWS}{free}/", json={"set_permissions": empty}))
    p.note("P2 unknown user", owner.patch(f"{VIEWS}{view}/", json={"set_permissions": {"view": {"users": [999999], "groups": []}, "change": {"users": [], "groups": []}}}))
    # T1
    p.note("P2 T1 id__in", [r["id"] for r in obj(parse(stack.admin.get(VIEWS, params={"id__in": str(view), "page_size": 1})))["results"]] == [view])
    # T5
    name = f"contract-view-e2e-same-{uuid.uuid4().hex[:6]}"
    mine = p.view(owner, name=name)
    _ = p.view(other, name=name)
    wider = {"view": {"users": [viewer_id, other_id], "groups": []}, "change": {"users": [], "groups": []}}
    p.note("P2 T5 hand over", owner.patch(f"{VIEWS}{mine}/", json={"owner": other_id, "set_permissions": wider}))
    p.note("P2 T5 after", stack.admin.get(f"{VIEWS}{mine}/", params={"full_perms": "true"}))
    p.note("P2 T5 hand over to nobody", owner.patch(f"{VIEWS}{mine}/", params={"full_perms": "true"}, json={"owner": None}))
    p.note("P2 page with full_perms", stack.admin.get(VIEWS, params={"full_perms": "true", "page_size": 1}))
    p.note("P2 put on unowned", other.get(f"{VIEWS}{mine}/"))
    p.done()


def test_p3(stack: Stack) -> None:
    p = Probe(stack)
    _, neither = p.user("view_savedview")
    _, reads = p.user("view_savedview", "view_uisettings")
    _, writes = p.user("view_savedview", "add_uisettings")
    _, changes = p.user("view_savedview", "view_uisettings", "change_uisettings")
    for label, api in (("neither", neither), ("reads", reads), ("writes", writes), ("changes", changes)):
        p.note(f"P3 {label} get", str(api.get(UI).status_code))
        p.note(f"P3 {label} post", api.post(UI, json={"settings": {}}))
    p.done()


def test_p4(stack: Stack) -> None:
    p = Probe(stack)
    api = stack.admin
    field = num(obj(parse(api.post("/api/custom_fields/", json={"name": f"contract-{uuid.uuid4().hex[:8]}", "data_type": "string"}).raise_for_status()))["id"])
    for label, value in (
        ("null", None), ("empty", []), ("twice", ["title", "title"]), ("unknown", ["nope"]),
        ("field", [f"custom_field_{field}", "title"]), ("missing field", ["custom_field_999999"]),
        ("no number", ["custom_field_"]), ("prefix", [f"xcustom_field_{field}"]),
        ("leading zero", [f"custom_field_0{field}"]), ("text", "title"), ("object", {"title": 1}),
        ("number", [1]), ("placeholder", ["custom_field_%d"]), ("title object", {"nope": 1}), ("all 12", ["title", "created", "added", "tag", "correspondent", "documenttype", "storagepath", "note", "owner", "shared", "asn", "pagecount"]),
    ):
        made = api.post(VIEWS, json={"name": f"contract-view-e2e-{uuid.uuid4().hex[:8]}", "filter_rules": [], "display_fields": value})
        shown = obj(parse(made)).get("display_fields") if made.is_success else made.text[:120]
        p.note(f"P4 create {label}", f"{made.status_code} {shown}")
        view = p.view(display_fields=["asn"])
        done = api.patch(f"{VIEWS}{view}/", json={"display_fields": value})
        shown = obj(parse(done)).get("display_fields") if done.is_success else done.text[:120]
        p.note(f"P4 patch {label}", f"{done.status_code} {shown}")
    view = p.view(display_fields=[f"custom_field_{field}", "title"])
    p.note("P4 delete field", str(api.delete(f"/api/custom_fields/{field}/").status_code))
    p.note("P4 after delete", api.get(f"{VIEWS}{view}/"))
    p.note("P4 rename after delete", api.patch(f"{VIEWS}{view}/", json={"icon": "heart"}))
    p.note("P4 resend after delete", api.patch(f"{VIEWS}{view}/", json={"display_fields": [f"custom_field_{field}"]}))
    for body in (
        {"icon": "nope"}, {"icon": None}, {"icon": ""}, {"icon": "Heart"}, {"display_mode": ""}, {"display_mode": None},
        {"display_mode": "smallcards"}, {"display_mode": "smallCards"}, {"display_fields": []}, {"display_fields": ["asn"]},
    ):
        p.note(f"P4 patch {body}", api.patch(f"{VIEWS}{view}/", json=body))
    p.note("P4 create mode blank", str(p.view(display_mode="")))
    _, plain = p.user("view_savedview", "add_savedview")
    p.note("P4 user without view_customfield list", str(plain.get("/api/custom_fields/").status_code))
    p.done()
