"""An in-process fake of the Todoist API v1 endpoints we use, for end-to-end tests."""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

TOKEN = "test-token"
PAGE = 2  # tiny pages, to exercise cursor pagination


class FakeTodoist:
    def __init__(self):
        self.projects, self.sections, self.labels, self.tasks = {}, {}, {}, {}
        self.seen_request_ids: dict[str, dict] = {}
        self.fail_next_posts = 0  # simulate 500s
        self.calls: list[tuple[str, str]] = []
        self._next = 1000
        self._lock = threading.Lock()

    def new_id(self) -> str:
        self._next += 1
        return str(self._next)

    def active_tasks(self):
        return [t for t in self.tasks.values() if not t["checked"] and not t["is_deleted"]]

    # helpers for tests
    def complete(self, task_id):
        self.tasks[task_id]["checked"] = True

    def delete(self, task_id):
        self.tasks[task_id]["is_deleted"] = True


def _handler(fake: FakeTodoist):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence
            pass

        def _send(self, code, obj=None):
            body = b"" if obj is None else json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _auth(self):
            if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                self._send(401, {"error": "Unauthorized"})
                return False
            return True

        def _page(self, items, qs):
            start = int(qs.get("cursor", ["0"])[0])
            chunk = items[start : start + PAGE]
            nxt = str(start + PAGE) if start + PAGE < len(items) else None
            self._send(200, {"results": chunk, "next_cursor": nxt})

        def do_GET(self):
            if not self._auth():
                return
            u = urlsplit(self.path)
            qs = parse_qs(u.query)
            path = u.path.removeprefix("/api/v1")
            fake.calls.append(("GET", path))
            if path == "/projects":
                return self._page(list(fake.projects.values()), qs)
            if path == "/sections":
                pid = qs.get("project_id", [None])[0]
                return self._page([s for s in fake.sections.values() if s["project_id"] == pid], qs)
            if path == "/labels":
                return self._page(list(fake.labels.values()), qs)
            if path == "/tasks":
                label = qs.get("label", [None])[0]
                sec = qs.get("section_id", [None])[0]
                items = [t for t in fake.active_tasks()
                         if (label is None or label in t["labels"]) and (sec is None or t["section_id"] == sec)]
                return self._page(items, qs)
            self._send(404, {"error": "not found"})

        def do_DELETE(self):
            if not self._auth():
                return
            path = urlsplit(self.path).path.removeprefix("/api/v1")
            fake.calls.append(("DELETE", path))
            m = re.fullmatch(r"/(sections|labels)/(\d+)", path)
            store = {"sections": fake.sections, "labels": fake.labels}.get(m.group(1)) if m else None
            if not store or m.group(2) not in store:
                return self._send(404, {"error": "not found"})
            del store[m.group(2)]
            if m.group(1) == "sections":  # Todoist deletes the section's tasks too
                for t in fake.tasks.values():
                    if t["section_id"] == m.group(2):
                        t["is_deleted"] = True
            self.send_response(204)
            self.end_headers()

        def do_POST(self):
            if not self._auth():
                return
            path = urlsplit(self.path).path.removeprefix("/api/v1")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            fake.calls.append(("POST", path))
            rid = self.headers.get("X-Request-Id")
            with fake._lock:
                if rid and rid in fake.seen_request_ids:  # idempotent replay
                    return self._send(200, fake.seen_request_ids[rid])
                if fake.fail_next_posts:
                    fake.fail_next_posts -= 1
                    # Simulate "server processed it but the response was lost".
                    result = self._apply(path, body)
                    if rid and result is not None:
                        fake.seen_request_ids[rid] = result
                    return self._send(503, {"error": "temporarily unavailable"})
                result = self._apply(path, body)
                if result is None:
                    return self._send(404, {"error": "not found"})
                if rid:
                    fake.seen_request_ids[rid] = result
                self._send(200, result)

        def _apply(self, path, body):
            if path == "/projects":
                p = {"id": fake.new_id(), "name": body["name"], "is_archived": False}
                fake.projects[p["id"]] = p
                return p
            if path == "/sections":
                s = {"id": fake.new_id(), "name": body["name"], "project_id": body["project_id"]}
                fake.sections[s["id"]] = s
                return s
            if path == "/labels":
                lab = {"id": fake.new_id(), "name": body["name"], "color": body.get("color", "charcoal")}
                fake.labels[lab["id"]] = lab
                return lab
            m = re.fullmatch(r"/labels/(\d+)", path)
            if m and m.group(1) in fake.labels:
                assert set(body) == {"color"}
                fake.labels[m.group(1)].update(body)
                return fake.labels[m.group(1)]
            if path == "/tasks":
                assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", body["due_datetime"])
                t = {"id": fake.new_id(), "checked": False, "is_deleted": False, "section_id": None, **body}
                fake.tasks[t["id"]] = t
                return t
            m = re.fullmatch(r"/tasks/(\d+)/move", path)
            if m and m.group(1) in fake.tasks:
                assert list(body) == ["section_id"] and body["section_id"] in fake.sections
                fake.tasks[m.group(1)]["section_id"] = body["section_id"]
                return fake.tasks[m.group(1)]
            m = re.fullmatch(r"/tasks/(\d+)", path)
            if m and m.group(1) in fake.tasks:
                assert "project_id" not in body  # update must not try to move tasks
                fake.tasks[m.group(1)].update(body)
                return fake.tasks[m.group(1)]
            return None

    return H


class FakeServer:
    def __enter__(self):
        self.fake = FakeTodoist()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _handler(self.fake))
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.httpd.server_address[1]}/api/v1"
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
