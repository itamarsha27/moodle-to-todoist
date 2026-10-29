from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MoodleEvent:
    """One VEVENT from the Moodle iCal export, normalised but not yet filtered."""

    uid: str
    summary: str
    description: str
    course_raw: str          # CATEGORIES (course short name), "" if none
    due: datetime            # timezone-aware, in the configured timezone
    url: str
    last_modified: datetime | None = None


@dataclass(frozen=True)
class Task:
    """An actionable item that will become a reminder."""

    uid: str
    course: str              # clean course name (after mapping)
    course_raw: str
    title: str               # cleaned title, e.g. "HW3"
    kind: str                # "assignment" | "quiz" | "exam"
    due: datetime
    url: str
    description: str
