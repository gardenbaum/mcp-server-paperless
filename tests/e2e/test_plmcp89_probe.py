"""Temporary probes of PLMCP-89 (P1 to P6): every test prints what Paperless 3.5.1 does and fails.

Deleted before the pull request is ready.
"""

# ruff: noqa
# pyright: basic

import json
import subprocess
import time
import uuid

import pytest

from e2e.helpers import PORT, client, consume, consume_text, in_container, pdf, token_for

pytestmark = pytest.mark.e2e


class Report:
    def __init__(self):
        self.lines = []

    def __call__(self, label, value):
        if hasattr(value, "status_code"):
            value = (value.status_code, value.text[:700])
        self.lines.append(f"{label}: {value}")

    def done(self):
        pytest.fail("PROBE\n" + "\n".join(self.lines), pytrace=False)


def user(stack, perms, **more):
    name = f"p89-{uuid.uuid4().hex[:8]}"
    pw = "probe-user-Passw0rd"
    made = stack.admin.post(
        "/api/users/", json={"username": name, "password": pw, "user_permissions": perms, **more}
    ).json()
    return made["id"], client(token_for(name, pw))


def test_p1(stack):
    r = Report()
    consume(stack.admin, f"p89-{uuid.uuid4().hex}", pdf(uuid.uuid4().hex))
    consume(stack.admin, f"p89-broken-{uuid.uuid4().hex}", b"%PDF-1.4 broken")
    a = stack.admin
    r("active", a.get("/api/tasks/active/"))
    r("active+filter", a.get("/api/tasks/active/", params={"task_type": "nope", "status": "nope"}))
    for q in ({}, {"task_type": "mail_fetch"}, {"task_type": "nope"}, {"days": 0}, {"days": 1000}, {"days": -1}, {"days": "x"}):
        res = a.get("/api/tasks/summary/", params=q)
        r(f"summary {q}", (res.status_code, len(res.text), res.text[:500]))
    base = a.get("/api/tasks/status_counts/").json()
    r("counts", base)
    for q in (
        {"task_type": "consume_file"}, {"task_type": ["consume_file", "sanity_check"]}, {"trigger_source": "api_upload"},
        {"acknowledged": "false"}, {"acknowledged": "true"}, {"status": "failure"}, {"is_complete": "true"},
        {"owner": 2}, {"owner": 99999}, {"name": "p89-broken"}, {"name": "consume"}, {"result": "error"},
        {"date_created_after": "2126-01-01T00:00:00Z"}, {"date_created_before": "2020-01-01T00:00:00+02:00"},
        {"date_created_before": "bad"}, {"task_type": "nope"}, {"task_id": "nope"},
    ):
        r(f"counts {q}", a.get("/api/tasks/status_counts/", params=q))
        res = a.get("/api/tasks/", params={**q, "page_size": 1})
        r(f"list {q}", (res.status_code, res.json().get("count", res.text[:200])))
    for o in ("date_created", "date_done", "status", "task_type", "duration_seconds", "wait_time_seconds", "-status,-date_created", "bogus"):
        res = a.get("/api/tasks/", params={"ordering": o, "page_size": 4}).json()
        r(f"order {o}", [(t["status"], t["task_type"], t["date_done"], t["duration_seconds"], t["wait_time_seconds"]) for t in res["results"]])
    uid, u = user(stack, ["view_paperlesstask"])
    r("user list count", u.get("/api/tasks/", params={"page_size": 1}).json()["count"])
    r("user summary", u.get("/api/tasks/summary/").text[:300])
    r("user counts", u.get("/api/tasks/status_counts/"))
    sid, s = user(stack, ["view_system_monitoring"])
    r("monitor list", s.get("/api/tasks/", params={"page_size": 1}))
    r("monitor summary", s.get("/api/tasks/summary/"))
    r("monitor counts", s.get("/api/tasks/status_counts/"))
    tid, t = user(stack, ["view_system_monitoring", "view_paperlesstask"])
    r("monitor+view list count", t.get("/api/tasks/", params={"page_size": 1}).json().get("count"))
    r("monitor+view summary", t.get("/api/tasks/summary/").text[:300])
    nid, n = user(stack, [])
    for p in ("active", "summary", "status_counts"):
        r(f"nothing {p}", n.get(f"/api/tasks/{p}/"))
    r("status monitor", s.get("/api/status/"))
    r("status nothing", n.get("/api/status/"))
    r("statistics nothing", n.get("/api/statistics/"))
    r("status admin", a.get("/api/status/").text)
    r("statistics admin", a.get("/api/statistics/").text)
    for i in (uid, sid, tid, nid):
        a.delete(f"/api/users/{i}/")
    r.done()


def test_p2(stack):
    r = Report()
    kinds = ["apply_ai_suggestions", "build_share_link", "bulk_delete", "bulk_update", "check_workflows", "consume_file", "empty_trash", "index_optimize", "mail_fetch", "reprocess_document", "nope"]
    for k in kinds:
        r(k, stack.admin.post("/api/tasks/run/", json={"task_type": k}))
    r("two", stack.admin.post("/api/tasks/run/", json={"task_type": ["sanity_check", "train_classifier"]}))
    i, staff = user(stack, ["view_paperlesstask", "change_paperlesstask"], is_staff=True)
    r("staff", staff.post("/api/tasks/run/", json={"task_type": "sanity_check"}))
    stack.admin.delete(f"/api/users/{i}/")
    r.done()


def test_p3(stack):
    r = Report()
    a = stack.admin
    consume(a, f"p89-{uuid.uuid4().hex}", b"%PDF-1.4 broken")
    i, u = user(stack, ["view_paperlesstask", "change_paperlesstask", "add_document"])
    own = u.post("/api/documents/post_document/", files={"document": ("own.pdf", b"%PDF-1.4 x", "application/pdf")})
    r("own upload", own)
    before = a.get("/api/tasks/status_counts/", params={"acknowledged": "false"}).json()
    ub = u.get("/api/tasks/status_counts/", params={"acknowledged": "false"}).json()
    mine = u.get("/api/tasks/", params={"acknowledged": "false", "page_size": 100}).json()
    r("user sees owners", sorted({str(t["owner"]) for t in mine["results"]}))
    r("ack both", u.post("/api/tasks/acknowledge/", json={"all": True, "tasks": [1]}))
    r("ack neither", u.post("/api/tasks/acknowledge/", json={}))
    r("ack foreign id", u.post("/api/tasks/acknowledge/", json={"tasks": [next(t["id"] for t in a.get("/api/tasks/", params={"owner": 2}).json()["results"])]}))
    r("ack all user", u.post("/api/tasks/acknowledge/", json={"all": True}))
    after = a.get("/api/tasks/status_counts/", params={"acknowledged": "false"}).json()
    r("admin before/after", (before, after))
    r("user before", ub)
    r("active after", [(t["status"], t["acknowledged"]) for t in a.get("/api/tasks/active/").json()])
    r("ack all again", u.post("/api/tasks/acknowledge/", json={"all": True}))
    v, w = user(stack, ["view_paperlesstask"])
    r("ack without change", w.post("/api/tasks/acknowledge/", json={"all": True}))
    for x in (i, v):
        a.delete(f"/api/users/{x}/")
    r.done()


SERVER = r'''
import http.server, json
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        open("/tmp/embed.log", "a").write(json.dumps([self.path, str(body)[:300]]) + "\n")
        texts = body.get("input"); n = len(texts) if isinstance(texts, list) else 1
        out = json.dumps({"object": "list", "model": "m", "data": [{"object": "embedding", "index": i, "embedding": [0.1] * 8} for i in range(n)], "usage": {"prompt_tokens": 1, "total_tokens": 1}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", 9999), H).serve_forever()
'''


def docker(*command):
    cid = subprocess.run(["docker", "ps", "-q", "--filter", f"publish={PORT}"], capture_output=True, text=True).stdout.strip()
    return subprocess.run(["docker", "exec", *command[:1], cid, *command[1:]] if command[0] == "-d" else ["docker", "exec", cid, *command], capture_output=True, text=True).stdout


def test_p4(stack):
    r = Report()
    a = stack.admin

    def go(label):
        docker("sh", "-c", ": > /tmp/embed.log; chmod 666 /tmp/embed.log")
        task = a.post("/api/tasks/run/", json={"task_type": "llm_index"}).json()["task_id"]
        row = {}
        for _ in range(90):
            row = a.get("/api/tasks/", params={"task_id": task}).json()["results"][0]
            if row["status"] in ("success", "failure"):
                break
            time.sleep(1)
        r(label, (row["status"], row["result_data"], row["input_data"]))
        log = docker("cat", "/tmp/embed.log").splitlines()
        r(f"{label} requests", (len(log), [line[:350] for line in log[:3]]))

    go("AI off")
    docker("-d", "python3", "-c", SERVER)
    one = consume_text(a, f"p89-{uuid.uuid4().hex}")
    two = consume_text(a, f"p89-{uuid.uuid4().hex}")
    res = a.patch("/api/config/1/", json={"ai_enabled": True, "llm_embedding_backend": "openai-like", "llm_embedding_endpoint": "http://127.0.0.1:9999/v1", "llm_embedding_model": "m", "llm_embedding_api_key": "probe"})
    r("config", res)
    try:
        go("no index yet")
        go("index, nothing changed")
        a.patch(f"/api/documents/{one}/", json={"title": "p89 changed"}).raise_for_status()
        time.sleep(5)
        go("one changed")
        a.patch("/api/config/1/", json={"llm_embedding_model": "m2"})
        go("other model")
    finally:
        a.patch("/api/config/1/", json={"ai_enabled": None, "llm_embedding_backend": None, "llm_embedding_endpoint": None, "llm_embedding_model": None, "llm_embedding_api_key": None})
        for d in (one, two):
            a.delete(f"/api/documents/{d}/")
    r.done()


def test_p5(stack):
    r = Report()
    a = stack.admin
    doc = consume_text(a, f"p89-{uuid.uuid4().hex}")
    path = f"/api/documents/{doc}/history/"
    r("fresh", a.get(path))
    r("page param", (len(a.get(path, params={"page": 2, "page_size": 1}).json()), len(a.get(path).json())))
    colon = a.post("/api/custom_fields/", json={"name": f"a:b {uuid.uuid4().hex[:5]}", "data_type": "string"}).json()
    long = a.post("/api/custom_fields/", json={"name": f"p89 long {uuid.uuid4().hex[:5]}", "data_type": "longtext"}).json()
    r("set", a.patch(f"/api/documents/{doc}/", json={"custom_fields": [{"field": colon["id"], "value": "v"}, {"field": long["id"], "value": "x:y " * 500}]}).status_code)
    h = a.get(path)
    r("with fields", (h.status_code, h.text[:1500]))
    for n in range(20):
        a.patch(f"/api/documents/{doc}/", json={"content": ("text " * 2000) + str(n)}).raise_for_status()
    h = a.get(path)
    r("after 20 corrections", (h.status_code, len(h.content), len(h.json()) if h.status_code == 200 else None, h.headers.get("content-encoding")))
    if h.status_code == 200:
        stamps = [e["timestamp"] for e in h.json()]
        r("sorted", stamps == sorted(stamps, reverse=True))
        r("ids unique", len({e["id"] for e in h.json()}) == len(stamps))
        r("ids", [e["id"] for e in h.json()][:30])
    a.delete(f"/api/custom_fields/{colon['id']}/")
    a.delete(f"/api/custom_fields/{long['id']}/")
    h = a.get(path)
    r("deleted fields", (h.status_code, h.text[:400]))
    i, u = user(stack, ["view_document", "view_logentry"])
    r("other", u.get(path))
    a.patch(f"/api/documents/{doc}/", json={"owner": None})
    r("unowned by other", (u.get(path).status_code, u.get(path).text[:80]))
    r("unknown", a.get("/api/documents/999999/history/"))
    a.delete(f"/api/users/{i}/")
    a.delete(f"/api/documents/{doc}/")
    r.done()


def test_p6(stack):
    r = Report()
    a = stack.admin
    doc = consume_text(a, f"p89-{uuid.uuid4().hex}")
    consume(a, f"p89-t-{uuid.uuid4().hex}", pdf("x" + uuid.uuid4().hex))
    a.post("/api/documents/bulk_edit/", json={"documents": [doc], "method": "modify_tags", "parameters": {"add_tags": [], "remove_tags": []}})
    a.post("/api/documents/reprocess/", json={"documents": [doc]})
    a.post("/api/documents/remove_password/", json={"documents": [doc], "password": "probe-pw"})
    a.post("/api/documents/rotate/", json={"documents": [doc], "degrees": 90})
    a.post("/api/share_link_bundles/", json={"document_ids": [doc]})
    a.post("/api/tasks/run/", json={"task_type": "sanity_check"})
    time.sleep(20)
    seen = {}
    page = 1
    while page and page < 10:
        res = a.get("/api/tasks/", params={"page": page, "page_size": 100}).json()
        for t in res["results"]:
            key = (t["task_type"], tuple(sorted(t["input_data"] or {})), tuple(sorted(t["result_data"] or {})) if isinstance(t["result_data"], dict) else str(t["result_data"]))
            seen.setdefault(key, (t["trigger_source"], t["owner"], json.dumps(t["input_data"])[:400], json.dumps(t["result_data"])[:200], t["wait_time_seconds"]))
        page = page + 1 if res["next"] else None
    for k, v in seen.items():
        r(str(k), v)
    r("secret in list", "probe-pw" in a.get("/api/tasks/", params={"page_size": 100}).text)
    a.delete(f"/api/documents/{doc}/")
    r.done()
