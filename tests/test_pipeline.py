"""Parsing, filtering and course-mapping tests (no network)."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from moodle_sync import rules
from moodle_sync.filters import _compiled, select_tasks
from moodle_sync.parser import parse_calendar

TZ = ZoneInfo("Asia/Jerusalem")
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=TZ)
FIXTURE = Path(__file__).parent / "fixtures" / "sample.ics"
BASE = "https://moodle.example.ac.il"


@pytest.fixture
def events():
    return parse_calendar(FIXTURE.read_bytes(), TZ, BASE)


@pytest.fixture(autouse=True)
def course_map(monkeypatch):
    monkeypatch.setattr(rules, "COURSE_MAP", {"044131 - מערכות לינאריות": "Linear Systems"})
    monkeypatch.setattr(rules, "COURSE_PATTERNS", [(r"^234218", "Data Structures")])
    _compiled.cache_clear()
    yield
    _compiled.cache_clear()


def test_parses_all_events(events):
    assert len(events) == 10
    folded = next(e for e in events if e.uid.startswith("1009"))
    assert folded.summary.endswith("across two lines")
    assert folded.url == "https://moodle.example.ac.il/mod/assign/view.php?id=555"


def test_timezones(events):
    hw3 = next(e for e in events if e.uid.startswith("1001"))
    assert (hw3.due.hour, hw3.due.minute) == (23, 59)          # 21:59Z = 23:59 Israel time
    hw2 = next(e for e in events if e.uid.startswith("1004"))
    assert (hw2.due.hour, hw2.due.minute) == (23, 59)          # TZID parameter
    exam = next(e for e in events if e.uid.startswith("1007"))  # all-day → end of day
    assert (exam.due.day, exam.due.hour) == (20, 23)


def test_filtering_and_mapping(events):
    tasks = select_tasks(events, NOW, horizon_days=60)
    got = [(t.course, t.title, t.kind) for t in tasks]
    assert got == [
        ("Data Structures", "HW3", "assignment"),
        ("Linear Systems", "תרגיל בית 2", "assignment"),
        ("Data Structures", "Quiz 2", "quiz"),
        ("Linear Systems", "מבחן אמצע סמסטר", "exam"),
        ("236350-W26", "Final project report is due - a very long title that is folded across two lines", "assignment"),
    ]  # opens / lecture / office hours / past / site events are dropped
    assert len(select_tasks(events, NOW, horizon_days=30)) == 4  # Dec 10 is beyond 30 days


def test_course_code_and_quote_cleanup(monkeypatch):
    from moodle_sync.filters import clean_title, resolve_course
    monkeypatch.setattr(rules, "COURSE_MAP", {})
    monkeypatch.setattr(rules, "COURSE_PATTERNS", [])
    _compiled.cache_clear()
    assert resolve_course("0368215899 - מבני נתונים") == "מבני נתונים"
    assert resolve_course("0509284399 - אנליזה הרמונית") == "אנליזה הרמונית"
    assert resolve_course("Linear Systems - 044131") == "Linear Systems"
    assert resolve_course("234218-W26") == "234218-W26"          # code only: left alone
    assert clean_title("'תרגיל בית 1'") == "תרגיל בית 1"
    assert clean_title("יש להגיש את 'תרגיל בית 2'") == "תרגיל בית 2"
    assert clean_title("Bob's HW") == "Bob's HW"
    assert clean_title("תאריך הגשה Lecture 1 Quiz") == "Lecture 1 Quiz"
    assert clean_title("מועד הגשה: תרגיל 3") == "תרגיל 3"
    assert clean_title("Due date for HW2") == "HW2"
    assert clean_title("תאריך הגשה") == "תאריך הגשה"          # never empties a title


def test_lecture_quizzes_are_kept(monkeypatch):
    from datetime import timedelta
    from moodle_sync.filters import decide, to_task
    from moodle_sync.models import MoodleEvent
    def ev(summary):
        return MoodleEvent("u", summary, "", "0368-1105 - מבוא להנדסת תוכנה", NOW + timedelta(days=3), "")
    for s in ["Lecture 1 quiz", "Lecture 2 quiz closes", "בוחן הרצאה 1 נסגר"]:
        assert decide(ev(s), NOW, 60).keep, s
        assert to_task(ev(s)).kind == "quiz"
    for s in ["Lecture 1 quiz opens", "Lecture 5 - recording available", "הרצאה 4"]:
        assert not decide(ev(s), NOW, 60).keep, s
    assert to_task(ev("Midterm exam closes")).kind == "exam"


@pytest.mark.parametrize("summary,kind", [
    ("מבדק 2", "quiz"), ("חידון שבועי", "quiz"), ("בחנים - נסגר", "quiz"), ("Lecture 1 quiz", "quiz"),
    ("עבודה מסכמת", "assignment"), ("יש להגיש את עבודה 3", "assignment"), ('ש"ב 4', "assignment"),
    ("ש״ב 5", "assignment"), ('ממ"ן 11', "assignment"), ("הגשת פרויקט", "assignment"),
    ('דו"ח מעבדה 1', "assignment"), ("תרגול 3 - הגשת תרגיל", "assignment"), ("Problem set 2 is due", "assignment"),
    ("מועד א' - מבחן סופי", "exam"), ("מבחן אמצע נפתח", "exam"), ("Midterm exam opens", "exam"),
])
def test_hebrew_and_english_keywords_kept(summary, kind):
    from datetime import timedelta
    from moodle_sync.filters import decide, to_task
    from moodle_sync.models import MoodleEvent
    ev = MoodleEvent("u", summary, "", "c", NOW + timedelta(days=3), "")
    assert decide(ev, NOW, 60).keep
    assert to_task(ev).kind == kind


@pytest.mark.parametrize("summary", ["מטלה 2 נפתחת", "Quiz 2 opens", "תרגול 3", "הקלטת הרצאה 5", "שעות קבלה", "הרצאה 4"])
def test_noise_dropped(summary):
    from datetime import timedelta
    from moodle_sync.filters import decide
    from moodle_sync.models import MoodleEvent
    assert not decide(MoodleEvent("u", summary, "", "c", NOW + timedelta(days=3), ""), NOW, 60).keep
