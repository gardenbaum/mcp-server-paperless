"""Temporary probes of PLMCP-83 (deleted afterwards)."""

import json
import time
import uuid

import httpx2
import pytest

from e2e.helpers import client, consume_text, in_container, num, obj, parse, results, token_for

pytestmark = pytest.mark.e2e
OUT = {}


def show(name, reply):
    OUT[name] = [reply.status_code, reply.text[:700]]


def test_probe(stack):
    a, r = stack.admin, stack.reader
    blind = client(stack.blind_token)
    u = uuid.uuid4().hex[:8]
    word = "zzqx" + u
    docs = [consume_text(a, f"{word} Ärzteblatt Émile {n}") for n in range(2)]
    body = {"username": "probe" + u, "password": "p" + u * 3, "user_permissions": ["view_document"]}
    uid = num(obj(parse(a.post("/api/users/", json=body).raise_for_status()))["id"])
    mine = client(token_for("probe" + u, "p" + u * 3))
    try:
        for n, c in (("admin", a), ("reader", r), ("blind", blind), ("viewer", mine)):
            show("asn " + n, c.get("/api/documents/next_asn/"))
            show("auto " + n, c.get("/api/search/autocomplete/", params={"term": word, "limit": 5}))
        top = num(parse(a.get("/api/documents/next_asn/")))
        a.patch(f"/api/documents/{docs[0]}/", json={"archive_serial_number": top + 5})
        show("asn gap", a.get("/api/documents/next_asn/"))
        a.delete(f"/api/documents/{docs[0]}/")
        show("asn trash", a.get("/api/documents/next_asn/"))
        show("asn used by trash", a.patch(f"/api/documents/{docs[1]}/", json={"archive_serial_number": top + 5}))
        a.post("/api/trash/", json={"documents": [docs[0]], "action": "restore"})
        time.sleep(3)
        a.patch(f"/api/documents/{docs[0]}/", json={"archive_serial_number": None})
        for t, l in (("ärzt", 5), ("ARZT", 5), ("Émi", 5), ("ém", "5"), (word, "abc"), (word, 0), ("", 5), ("zz qx", 5), (word + "é", 5), ("a", 50)):
            show(f"auto {t!r} {l}", a.get("/api/search/autocomplete/", params={"term": t, "limit": l}))
        path = "/api/storage_paths/test/"
        d = docs[1]
        for n, t in (("ok", "{{ created_year }}/{{ title }}"), ("undef", "{{ nope }}"), ("syntax", "{{ title"), ("dots", "../x"), ("abs", "/etc/x"), ("empty", "{{ '' }}"), ("blank", "  "), ("old", "{title}"), ("long", "{% for i in range(5000) %}a{% endfor %}"), ("loop", "{% for i in range(200000) %}{% endfor %}x"), ("sec", "{{ ''.__class__.__mro__ }}"), ("none", "{{ correspondent }}/x"), ("ref", "{{ document.correspondent.name }}/x")):
            t0 = time.monotonic()
            show("path " + n, a.post(path, json={"document": d, "path": t}))
            OUT["path " + n].append(round(time.monotonic() - t0, 2))
        show("path foreign", mine.post(path, json={"document": stack.seeded.private_document, "path": "x"}))
        show("path viewer", mine.post(path, json={"document": stack.seeded.public_document, "path": "{{ title }}"}))
        show("path blind", blind.post(path, json={"document": stack.seeded.public_document, "path": "x"}))
        show("path missing", a.post(path, json={"document": 99999999, "path": "x"}))
        sel = "/api/documents/selection_data/"
        show("sel reader", r.post(sel, json={"documents": [stack.seeded.public_document]}))
        show("sel foreign", r.post(sel, json={"documents": [stack.seeded.private_document]}))
        show("sel missing", a.post(sel, json={"documents": [99999999]}))
        show("sel twice", a.post(sel, json={"documents": [d, d]}))
        show("sel empty", a.post(sel, json={"documents": []}))
        show("sel viewer", mine.post(sel, json={"documents": [stack.seeded.public_document]}))
        show("sel blind", blind.post(sel, json={"documents": [stack.seeded.public_document]}))
        f = num(obj(parse(a.post("/api/custom_fields/", json={"name": "probe" + u, "data_type": "string"})))["id"])
        a.patch(f"/api/documents/{d}/", json={"custom_fields": [{"field": f, "value": "x"}]})

        def fields():
            rows = obj(parse(a.post(sel, json={"documents": [d]})))["selected_custom_fields"]
            return [x for x in rows if x["id"] == f]

        OUT["cf set"] = fields()
        a.patch(f"/api/documents/{d}/", json={"custom_fields": []})
        OUT["cf removed"] = fields()
        a.patch(f"/api/documents/{d}/", json={"custom_fields": [{"field": f, "value": "y"}]})
        OUT["cf again"] = fields()
        a.delete(f"/api/documents/{d}/")
        a.post("/api/trash/", json={"documents": [d], "action": "restore"})
        time.sleep(2)
        OUT["cf restored"] = fields()
        a.delete(f"/api/custom_fields/{f}/")
        notes = f"/api/documents/{d}/notes/"
        show("note md", a.post(notes, json={"note": "**x**", "format": "markdown"}))
        show("note plain", a.post(notes, json={"note": "y"}))
        show("note bad", a.post(notes, json={"note": "z", "format": "html"}))
        OUT["notes row"] = obj(parse(a.get(f"/api/documents/{d}/")))["notes"]
        show("thumb v", a.get(f"/api/documents/{d}/thumb/", params={"version": d}))
        show("thumb bad", a.get(f"/api/documents/{d}/thumb/", params={"version": 99999999}))
        show("thumb other", a.get(f"/api/documents/{d}/thumb/", params={"version": docs[0]}))
        today = time.strftime("%Y-%m-%d")
        for n, q in (("mod", {"modified__date__gte": today, "query": word}), ("modlt", {"modified__date__lte": "2000-01-01", "query": word}), ("asn", {"archive_serial_number__gte": 1, "query": word}), ("name", {"original_filename__icontains": word}), ("nametext", {"original_filename__icontains": "pdf", "query": word})):
            reply = a.get("/api/documents/", params={**q, "fields": "id,original_file_name,modified"})
            show("doc " + n, reply)
        show("bundle status", a.get("/api/share_link_bundles/", params={"status": "ready"}))
        show("bundle bad", a.get("/api/share_link_bundles/", params={"status": "nope"}))
        show("bundle docs", a.get("/api/share_link_bundles/", params={"documents": d}))
        show("bundle docs missing", a.get("/api/share_link_bundles/", params={"documents": 99999999}))
        show("totp none", a.post(f"/api/users/{uid}/deactivate_totp/"))
        show("totp staffless", mine.post(f"/api/users/{uid}/deactivate_totp/"))
        show("totp other", mine.post("/api/users/1/deactivate_totp/"))
        show("totp missing", a.post("/api/users/999999/deactivate_totp/"))
        code = f"from allauth.mfa.totp.internal.auth import TOTP, generate_totp_secret as g; from django.contrib.auth.models import User; TOTP.activate(User.objects.get(pk={uid}), g())"
        try:
            in_container("python", "manage.py", "shell", "-c", code)
        except BaseException as e:  # noqa
            OUT["totp make"] = repr(e)[:600]
        OUT["user mfa"] = obj(parse(a.get(f"/api/users/{uid}/")))
        show("totp set", a.post(f"/api/users/{uid}/deactivate_totp/"))
        OUT["user mfa after"] = obj(parse(a.get(f"/api/users/{uid}/")))["is_mfa_enabled"]
        t0 = time.monotonic()
        show("remote", a.get("/api/remote_version/", timeout=60))
        OUT["remote"].append(round(time.monotonic() - t0, 2))
        show("remote viewer", mine.get("/api/remote_version/", timeout=60))
    finally:
        a.delete(f"/api/users/{uid}/")
        for x in docs:
            a.delete(f"/api/documents/{x}/")
    pytest.fail("\n".join(f"PROBE {k}: {json.dumps(v, ensure_ascii=False, default=str)}" for k, v in OUT.items()))
