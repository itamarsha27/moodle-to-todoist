"""
Minimal Todoist API v1 client (https://developer.todoist.com/api/v1), standard library only.

Auth is a personal API token (Todoist → Settings → Integrations → Developer).
It doesn't expire and needs no OAuth dance, which is why it suits unattended runs.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Iterator

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.todoist.com/api/v1"
DEFAULT_COLOR = "charcoal"  # what Todoist gives a label when no color is chosen
# All colors the Todoist API accepts.
TODOIST_COLORS = (
    "berry_red", "red", "orange", "yellow", "olive_green", "lime_green", "green", "mint_green", "teal",
    "sky_blue", "light_blue", "blue", "grape", "violet", "lavender", "magenta", "salmon", "charcoal", "grey", "taupe",
)


class TodoistError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class TodoistClient:
    def __init__(self, token: str, base_url: str = DEFAULT_BASE_URL, timeout: int = 30, attempts: int = 4):
        if not token:
            raise TodoistError("TODOIST_API_TOKEN is empty")
        self._token = token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.attempts = attempts

    # ------------------------------------------------------------------ http
    def _request(self, method: str, path: str, *, params: dict | None = None, body: dict | None = None) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
            # Same id on every retry, so a create that timed out but actually
            # succeeded is not applied twice.
            headers["X-Request-Id"] = str(uuid.uuid4())

        for attempt in range(1, self.attempts + 1):
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:300]
                if exc.code in (401, 403):
                    raise TodoistError(
                        f"Todoist rejected the API token (HTTP {exc.code}). "
                        "Copy a fresh one from Todoist → Settings → Integrations → Developer.",
                        exc.code,
                    ) from None
                if exc.code == 429 or exc.code >= 500:
                    delay = int(exc.headers.get("Retry-After") or 2 ** attempt)
                    if attempt < self.attempts:
                        log.warning("Todoist HTTP %s on %s %s; retrying in %ss", exc.code, method, path, delay)
                        time.sleep(min(delay, 60))
                        continue
                raise TodoistError(f"Todoist HTTP {exc.code} on {method} {path}: {detail}", exc.code) from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                if attempt < self.attempts:
                    log.warning("Todoist network error (%s); retrying", exc)
                    time.sleep(2 ** attempt)
                    continue
                raise TodoistError(f"Could not reach Todoist: {exc}") from None
        raise TodoistError("unreachable")  # pragma: no cover

    def _paginate(self, path: str, params: dict | None = None) -> Iterator[dict]:
        params = dict(params or {}, limit=200)
        while True:
            page = self._request("GET", path, params=params)
            if isinstance(page, list):  # tolerate non-paginated responses
                yield from page
                return
            yield from page.get("results", [])
            cursor = page.get("next_cursor")
            if not cursor:
                return
            params["cursor"] = cursor

    # ------------------------------------------------------------ resources
    def ensure_project(self, name: str) -> str:
        for p in self._paginate("/projects"):
            if p.get("name") == name and not p.get("is_archived") and not p.get("is_deleted"):
                return str(p["id"])
        log.info("Creating Todoist project %r", name)
        return str(self._request("POST", "/projects", body={"name": name})["id"])

    def ensure_sections(self, project_id: str, names: set[str]) -> dict[str, str]:
        existing = {s["name"]: str(s["id"]) for s in self._paginate("/sections", {"project_id": project_id})}
        for name in sorted(names - existing.keys()):
            log.info("Creating section %r", name)
            existing[name] = str(self._request("POST", "/sections", body={"name": name, "project_id": project_id})["id"])
        return existing

    def ensure_labels(self, names: set[str], colors: dict[str, str] | None = None) -> dict[str, str]:
        """Create missing labels and apply colors; return {name: id} for ALL personal labels.

        `colors` maps label name → desired color. An existing label's color is
        only changed if it still has Todoist's default color, so colors you
        picked yourself in Todoist are never overwritten. `colors` entries
        marked with a leading "!" (from COURSE_COLORS) are always applied.
        """
        colors = colors or {}
        labels = {lab["name"]: lab for lab in self._paginate("/labels")}
        for name in sorted(names):
            want = colors.get(name)
            forced = bool(want and want.startswith("!"))
            want = want.lstrip("!") if want else None
            lab = labels.get(name)
            if lab is None:
                log.info("Creating label %r%s", name, f" ({want})" if want else "")
                body = {"name": name, **({"color": want} if want else {})}
                labels[name] = self._request("POST", "/labels", body=body)
            elif want and lab.get("color") != want and (forced or lab.get("color") in (None, DEFAULT_COLOR)):
                log.info("Coloring label %r → %s", name, want)
                self._request("POST", f"/labels/{lab['id']}", body={"color": want})
                lab["color"] = want
        return {n: str(lab["id"]) for n, lab in labels.items()}

    def label_colors(self) -> dict[str, str]:
        return {lab["name"]: lab.get("color") or DEFAULT_COLOR for lab in self._paginate("/labels")}

    def active_tasks_with_label(self, label: str) -> list[dict]:
        return list(self._paginate("/tasks", {"label": label}))

    def create_task(self, fields: dict) -> dict:
        return self._request("POST", "/tasks", body=fields)

    def update_task(self, task_id: str, fields: dict) -> dict:
        return self._request("POST", f"/tasks/{task_id}", body=fields)

    def move_task(self, task_id: str, section_id: str) -> None:
        self._request("POST", f"/tasks/{task_id}/move", body={"section_id": section_id})

    def active_tasks_in_section(self, section_id: str) -> list[dict]:
        return list(self._paginate("/tasks", {"section_id": section_id}))

    def delete_section(self, section_id: str) -> None:
        self._request("DELETE", f"/sections/{section_id}")

    def delete_label(self, label_id: str) -> None:
        self._request("DELETE", f"/labels/{label_id}")
