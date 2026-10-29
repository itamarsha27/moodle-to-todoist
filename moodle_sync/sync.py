"""
Reconciles Moodle tasks with Todoist.

Rules:
  * new Moodle deadline          → create a Todoist task
  * same deadline, nothing new   → do nothing (re-running is always safe)
  * deadline/title changed       → update the *existing* task in place (⚠️ marker)
  * you completed or deleted it  → leave it alone, never recreate it
  * course renamed in rules.py   → task moves to the new section; the old, now
                                   empty section and label are removed
  * state file lost or reset     → existing tasks are found again by the hidden
                                   `moodle-uid` marker in their description, so
                                   nothing is duplicated

Only tasks carrying the sync label (default @moodle) are ever touched, so your
own Todoist tasks are safe.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .models import Task
from .state import StateStore
from .todoist import TodoistClient

log = logging.getLogger(__name__)

EMOJI = {"exam": "🎓", "quiz": "🧠", "assignment": "📝"}
DESC_LIMIT = 800
MARKER_RE = re.compile(r"moodle-uid:\s*`?([^\s`]+)`?")


@dataclass(frozen=True)
class TodoistSettings:
    project: str = "University"
    sync_label: str = "moodle"
    course_sections: bool = True
    course_labels: bool = True
    exam_priority: int = 4         # Todoist API: 4 = p1 (red), 1 = no priority
    assignment_priority: int = 3   # 3 = p2 (orange)


@dataclass
class SyncReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    rescheduled: list[str] = field(default_factory=list)
    adopted: list[str] = field(default_factory=list)
    unchanged: int = 0
    closed_by_user: int = 0
    pruned: int = 0
    moved_section: int = 0
    cleaned_up: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #
def label_for_course(course: str) -> str:
    # Todoist labels can't usefully contain spaces (they break @quick-add), and some symbols are rejected.
    return re.sub(r"[\s@#,!*\"'()\[\]]+", "_", course).strip("_")[:60] or "course"


def _fmt(dt: datetime) -> str:
    return dt.strftime("%a %d %b %Y, %H:%M")


def build_description(t: Task, previous_due: str | None) -> str:
    lines = [f"**Course:** {t.course}" + (f"  ({t.course_raw})" if t.course_raw and t.course_raw != t.course else "")]
    lines.append(f"**Due:** {_fmt(t.due)}")
    if previous_due:
        prev = datetime.fromisoformat(previous_due).astimezone(t.due.tzinfo)
        lines.append(f"⚠️ **Deadline changed** (was {_fmt(prev)})")
    if t.url:
        lines.append(f"**Moodle:** {t.url}")
    desc = (t.description or "").strip()
    if desc:
        if len(desc) > DESC_LIMIT:
            desc = desc[:DESC_LIMIT].rsplit(" ", 1)[0] + "…"
        lines += ["", desc]
    lines += ["", f"`moodle-uid: {t.uid}`"]
    return "\n".join(lines)


def build_fields(t: Task, previous_due: str | None, cfg: TodoistSettings) -> dict:
    content = f"{EMOJI[t.kind]} {t.course}: {t.title}"
    if previous_due:
        content = "⚠️ " + content
    labels = [cfg.sync_label] + ([label_for_course(t.course)] if cfg.course_labels else [])
    return {
        "content": content,
        "description": build_description(t, previous_due),
        "due_datetime": t.due.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "priority": cfg.exam_priority if t.kind == "exam" else cfg.assignment_priority,
        "labels": labels,
    }


def fingerprint(fields: dict) -> str:
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# Reconciliation
# --------------------------------------------------------------------------- #
def run_sync(
    tasks: list[Task],
    client: TodoistClient | None,
    state: StateStore,
    cfg: TodoistSettings,
    now: datetime,
    prune_after_days: int = 30,
    dry_run: bool = False,
) -> SyncReport:
    report = SyncReport()

    if dry_run or client is None:
        for t in tasks:
            rec = state.tasks.get(t.uid)
            if rec is None:
                log.info("  would CREATE  %s: %s (due %s)", t.course, t.title, _fmt(t.due))
                report.created.append(t.uid)
            elif rec["due"] != t.due.isoformat():
                log.info("  would MOVE    %s: %s → %s", t.course, t.title, _fmt(t.due))
                report.rescheduled.append(t.uid)
            else:
                report.unchanged += 1
        return report

    # Course names this tool has used before (e.g. before you renamed a course in rules.py).
    previous_courses = {r.get("course") for r in state.tasks.values() if r.get("course")}

    project_id = client.ensure_project(cfg.project)
    sections = client.ensure_sections(project_id, {t.course for t in tasks}) if cfg.course_sections else {}
    wanted_labels = {cfg.sync_label} | ({label_for_course(t.course) for t in tasks} if cfg.course_labels else set())
    label_ids = client.ensure_labels(wanted_labels)

    active = client.active_tasks_with_label(cfg.sync_label)
    active_ids = {str(a["id"]): a for a in active}
    by_marker: dict[str, dict] = {}
    for a in active:
        m = MARKER_RE.search(a.get("description") or "")
        if m:
            by_marker[m.group(1)] = a

    for t in tasks:
        try:
            _reconcile_one(t, client, state, cfg, now, project_id, sections, active_ids, by_marker, report)
        except Exception as exc:  # one bad task must not block the rest
            log.error("Failed to sync %s (%s): %s", t.title, t.uid, exc)
            report.errors.append(f"{t.course}: {t.title}: {exc}")
        finally:
            state.save()  # persist after every task: a crash can't cause duplicates

    try:
        _cleanup_renamed(client, cfg, project_id, label_ids, previous_courses, {t.course for t in tasks}, report)
    except Exception as exc:  # cleanup is cosmetic; never fail the run for it
        log.warning("Cleanup of old sections/labels skipped: %s", exc)

    report.pruned = state.prune(now, prune_after_days)
    state.save()
    return report


def _cleanup_renamed(client, cfg, project_id, label_ids, previous_courses, current_courses, report) -> None:
    """Remove sections/labels left over from old course names, but only if this
    tool created them (the name came from its own state) and they are now empty."""
    stale = previous_courses - current_courses
    if not stale:
        return
    all_sections = client.ensure_sections(project_id, set()) if cfg.course_sections else {}
    for course in sorted(stale):
        sid = all_sections.get(course)
        if sid and not client.active_tasks_in_section(sid):
            client.delete_section(sid)
            report.cleaned_up.append(f"section '{course}'")
            log.info("  - removed empty old section %r", course)
        lab = label_for_course(course)
        if cfg.course_labels and lab in label_ids and lab != cfg.sync_label and not client.active_tasks_with_label(lab):
            client.delete_label(label_ids[lab])
            report.cleaned_up.append(f"label '{lab}'")
            log.info("  - removed unused old label %r", lab)



def _reconcile_one(t, client, state, cfg, now, project_id, sections, active_ids, by_marker, report) -> None:
    rec = state.tasks.get(t.uid)

    # Recovery: not in state, but a task with our marker exists → re-link it.
    if rec is None and t.uid in by_marker:
        found = by_marker[t.uid]
        rec = {
            "todoist_id": str(found["id"]),
            "due": t.due.isoformat(),
            "previous_due": None,
            "fingerprint": None,  # force a refresh below
            "first_synced": now.isoformat(timespec="seconds"),
        }
        report.adopted.append(t.uid)
        log.info("  re-linked existing Todoist task for %s: %s", t.course, t.title)

    if rec is None:
        fields = build_fields(t, None, cfg)
        body = dict(fields, project_id=project_id)
        if t.course in sections:
            body["section_id"] = sections[t.course]
        created = client.create_task(body)
        state.put(t.uid, {
            "todoist_id": str(created["id"]),
            "due": t.due.isoformat(),
            "previous_due": None,
            "fingerprint": fingerprint(fields),
            "course": t.course,
            "title": t.title,
            "first_synced": now.isoformat(timespec="seconds"),
        })
        report.created.append(t.uid)
        log.info("  + created  %s: %s (due %s)", t.course, t.title, _fmt(t.due))
        return

    moved = rec["due"] != t.due.isoformat()
    previous_due = rec["due"] if moved else rec.get("previous_due")
    fields = build_fields(t, previous_due, cfg)
    fp = fingerprint(fields)

    live = active_ids.get(rec["todoist_id"])
    if live is None:
        # Completed or deleted by you → respect that; just keep state current.
        report.closed_by_user += 1
        if moved:
            log.info("  deadline moved for a task you already closed: %s: %s", t.course, t.title)
        state.put(t.uid, {**rec, "due": t.due.isoformat(), "previous_due": previous_due,
                          "fingerprint": fp, "course": t.course, "title": t.title})
        return

    # Course renamed (or task re-linked) → move it to the matching section.
    # A task you moved yourself, with an unchanged course name, stays where you put it.
    want_section = sections.get(t.course)
    if want_section and rec.get("course") != t.course and str(live.get("section_id") or "") != want_section:
        client.move_task(rec["todoist_id"], want_section)
        report.moved_section += 1
        log.info("  → moved to section %r: %s", t.course, t.title)

    if rec.get("fingerprint") == fp:
        report.unchanged += 1
        return

    client.update_task(rec["todoist_id"], fields)
    state.put(t.uid, {**rec, "due": t.due.isoformat(), "previous_due": previous_due,
                      "fingerprint": fp, "course": t.course, "title": t.title})
    if moved:
        report.rescheduled.append(t.uid)
        log.info("  ~ moved    %s: %s → %s", t.course, t.title, _fmt(t.due))
    else:
        report.updated.append(t.uid)
        log.info("  ~ updated  %s: %s", t.course, t.title)
