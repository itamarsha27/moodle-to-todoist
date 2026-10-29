"""Course mapping and keep/drop decisions, driven by rules.py."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache

from . import rules
from .models import MoodleEvent, Task


def _compile(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


@lru_cache(maxsize=1)
def _compiled():
    return {
        "include": _compile(rules.INCLUDE_PATTERNS),
        "exclude": _compile(rules.EXCLUDE_PATTERNS),
        "always_exclude": _compile(getattr(rules, "ALWAYS_EXCLUDE_PATTERNS", [])),
        "strong": _compile(getattr(rules, "STRONG_INCLUDE_PATTERNS", [])),
        "exam": _compile(rules.EXAM_PATTERNS),
        "quiz": _compile(getattr(rules, "QUIZ_PATTERNS", [])),
        "cleanup": _compile(rules.TITLE_CLEANUP_PATTERNS),
        "course_cleanup": _compile(getattr(rules, "COURSE_NAME_CLEANUP", [])),
        "course": [(re.compile(p, re.IGNORECASE), name) for p, name in rules.COURSE_PATTERNS],
    }


def _first_match(patterns: list[re.Pattern], text: str) -> re.Pattern | None:
    return next((p for p in patterns if p.search(text)), None)


def resolve_course(raw: str) -> str:
    """Map a raw Moodle course short name to a clean name."""
    raw = raw.strip()
    if not raw:
        return "General"
    if raw in rules.COURSE_MAP:
        return rules.COURSE_MAP[raw]
    for pattern, name in _compiled()["course"]:
        if pattern.search(raw):
            return name
    name = raw
    for pattern in _compiled()["course_cleanup"]:
        name = pattern.sub("", name).strip()
    return name or raw


_QUOTES = "'\"`׳״‘’“”„"


def clean_title(summary: str) -> str:
    title = summary.strip()
    for pattern in _compiled()["cleanup"]:
        title = pattern.sub("", title).strip()
    # Moodle often wraps the activity name in quotes: 'תרגיל בית 1' → תרגיל בית 1
    if len(title) > 2 and title[0] in _QUOTES and title[-1] in _QUOTES:
        title = title[1:-1].strip()
    return title or summary.strip()


@dataclass(frozen=True)
class Decision:
    keep: bool
    reason: str


def decide(event: MoodleEvent, now: datetime, horizon_days: int) -> Decision:
    c = _compiled()
    course = resolve_course(event.course_raw)

    if event.due < now:
        return Decision(False, "past")
    if event.due > now + timedelta(days=horizon_days):
        return Decision(False, f"beyond {horizon_days}-day horizon")
    if not event.course_raw and not rules.KEEP_EVENTS_WITHOUT_COURSE:
        return Decision(False, "no course (site or personal event)")
    if event.course_raw in rules.IGNORED_COURSES or course in rules.IGNORED_COURSES:
        return Decision(False, "ignored course")

    if p := _first_match(c["always_exclude"], event.summary):
        if e := _first_match(c["exam"], event.summary):
            return Decision(True, f"exam start /{e.pattern}/")
        return Decision(False, f"exclude /{p.pattern}/")
    if p := _first_match(c["exclude"], event.summary):
        if s := _first_match(c["strong"], event.summary):
            return Decision(True, f"include /{s.pattern}/ (overrides /{p.pattern}/)")
        return Decision(False, f"exclude /{p.pattern}/")
    if p := _first_match(c["include"], event.summary):
        return Decision(True, f"include /{p.pattern}/")
    if rules.KEEP_UNMATCHED:
        return Decision(True, "unmatched (KEEP_UNMATCHED=True)")
    return Decision(False, "no include pattern matched")


def to_task(event: MoodleEvent) -> Task:
    c = _compiled()
    if _first_match(c["exam"], event.summary):
        kind = "exam"
    elif _first_match(c["quiz"], event.summary):
        kind = "quiz"
    else:
        kind = "assignment"
    return Task(
        uid=event.uid,
        course=resolve_course(event.course_raw),
        course_raw=event.course_raw,
        title=clean_title(event.summary),
        kind=kind,
        due=event.due,
        url=event.url,
        description=event.description,
    )


def select_tasks(events: list[MoodleEvent], now: datetime, horizon_days: int) -> list[Task]:
    tasks = [to_task(e) for e in events if decide(e, now, horizon_days).keep]
    return sorted(tasks, key=lambda t: t.due)
