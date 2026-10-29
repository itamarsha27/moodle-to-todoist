"""End-to-end sync tests against an in-process fake Todoist server (real HTTP code path)."""

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from moodle_sync import __main__ as cli
from moodle_sync import rules, todoist
from moodle_sync.filters import _compiled, select_tasks
from moodle_sync.parser import parse_calendar
from moodle_sync.state import StateStore
from moodle_sync.sync import TodoistSettings, run_sync
from moodle_sync.todoist import TodoistClient, TodoistError

from .fake_todoist import TOKEN, FakeServer

TZ = ZoneInfo("Asia/Jerusalem")
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=TZ)
FIXTURE = Path(__file__).parent / "fixtures" / "sample.ics"
CFG = TodoistSettings()


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(rules, "COURSE_MAP", {"044131 - מערכות לינאריות": "Linear Systems"})
    monkeypatch.setattr(rules, "COURSE_PATTERNS", [(r"^234218", "Data Structures")])
    monkeypatch.setattr(todoist.time, "sleep", lambda s: None)  # fast retries
    _compiled.cache_clear()
    yield
    _compiled.cache_clear()


@pytest.fixture
def server():
    with FakeServer() as s:
        yield s


@pytest.fixture
def tasks():
    return select_tasks(parse_calendar(FIXTURE.read_bytes(), TZ, "https://moodle.example.ac.il"), NOW, 60)


def _sync(server, tasks, state_path, **kw):
    client = TodoistClient(TOKEN, base_url=server.base_url)
    state = StateStore(state_path).load()
    return run_sync(tasks, client, state, CFG, NOW, **kw), state


def test_first_run_creates_everything(server, tasks, tmp_path):
    report, state = _sync(server, tasks, tmp_path / "s.json")
    f = server.fake
    assert len(report.created) == 5 and not report.errors
    assert [p["name"] for p in f.projects.values()] == ["University"]
    assert {s["name"] for s in f.sections.values()} == {"Data Structures", "Linear Systems", "236350-W26"}
    assert {lab["name"] for lab in f.labels.values()} == {"moodle", "Data_Structures", "Linear_Systems", "236350-W26"}

    hw3 = next(t for t in f.tasks.values() if "HW3" in t["content"])
    assert hw3["content"] == "📝 Data Structures: HW3"
    assert hw3["due_datetime"] == "2026-11-03T21:59:00Z"
    assert hw3["priority"] == 3 and set(hw3["labels"]) == {"moodle", "Data_Structures"}
    assert "`moodle-uid: 1001@moodle.example.ac.il`" in hw3["description"]
    exam = next(t for t in f.tasks.values() if "מבחן" in t["content"])
    assert exam["priority"] == 4 and exam["content"].startswith("🎓")
    assert len(state.tasks) == 5 and (tmp_path / "s.json").exists()


def test_rerun_is_a_no_op(server, tasks, tmp_path):
    _sync(server, tasks, tmp_path / "s.json")
    posts_before = sum(1 for m, _ in server.fake.calls if m == "POST")
    report, _ = _sync(server, tasks, tmp_path / "s.json")
    assert report.unchanged == 5 and not report.created and not report.updated
    assert sum(1 for m, _ in server.fake.calls if m == "POST") == posts_before  # zero writes
    assert len(server.fake.tasks) == 5


def test_moved_deadline_updates_in_place(server, tasks, tmp_path):
    _sync(server, tasks, tmp_path / "s.json")
    moved = [replace(t, due=t.due + timedelta(days=2)) if t.title == "HW3" else t for t in tasks]
    report, _ = _sync(server, moved, tmp_path / "s.json")
    assert len(report.rescheduled) == 1 and not report.created
    assert len(server.fake.tasks) == 5  # no duplicate
    hw3 = next(t for t in server.fake.tasks.values() if "HW3" in t["content"])
    assert hw3["content"].startswith("⚠️") and hw3["due_datetime"] == "2026-11-05T21:59:00Z"
    assert "Deadline changed" in hw3["description"]
    # and stable afterwards
    report, _ = _sync(server, moved, tmp_path / "s.json")
    assert report.unchanged == 5


def test_completed_or_deleted_tasks_are_never_recreated(server, tasks, tmp_path):
    _sync(server, tasks, tmp_path / "s.json")
    ids = list(server.fake.tasks)
    server.fake.complete(ids[0])
    server.fake.delete(ids[1])
    moved = [replace(t, due=t.due + timedelta(days=1)) for t in tasks]  # even if deadlines move
    report, _ = _sync(server, moved, tmp_path / "s.json")
    assert report.closed_by_user == 2 and not report.created
    assert len(server.fake.tasks) == 5
    assert server.fake.tasks[ids[0]]["checked"] is True


def test_lost_state_relinks_instead_of_duplicating(server, tasks, tmp_path):
    _sync(server, tasks, tmp_path / "s.json")
    (tmp_path / "s.json").unlink()                     # e.g. repo reset
    report, state = _sync(server, tasks, tmp_path / "s.json")
    assert not report.created and len(report.adopted) == 5
    assert len(server.fake.tasks) == 5 and len(state.tasks) == 5


def test_transient_500_does_not_duplicate(server, tasks, tmp_path):
    server.fake.fail_next_posts = 1  # first POST (project create) succeeds server-side, response lost
    report, _ = _sync(server, tasks, tmp_path / "s.json")
    assert len(report.created) == 5 and not report.errors
    assert len(server.fake.projects) == 1 and len(server.fake.tasks) == 5


def test_bad_token(server, tasks, tmp_path):
    client = TodoistClient("wrong", base_url=server.base_url)
    with pytest.raises(TodoistError, match="rejected the API token"):
        run_sync(tasks, client, StateStore(tmp_path / "s.json"), CFG, NOW)


def test_dry_run_touches_nothing(server, tasks, tmp_path):
    report = run_sync(tasks, None, StateStore(tmp_path / "s.json"), CFG, NOW, dry_run=True)
    assert len(report.created) == 5
    assert not server.fake.calls and not (tmp_path / "s.json").exists()


def test_cli_end_to_end(server, tmp_path, monkeypatch):
    """Full CLI run, with an .ics generated relative to *now* so it never goes stale."""
    due = (datetime.now(TZ) + timedelta(days=3)).astimezone(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
    ics = tmp_path / "cal.ics"
    ics.write_text(
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:42@m\r\nSUMMARY:HW9 is due\r\n"
        f"DTSTART:{due}\r\nCATEGORIES:234218-W26\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    monkeypatch.setenv("TODOIST_API_TOKEN", TOKEN)
    monkeypatch.setenv("TODOIST_BASE_URL", server.base_url)
    monkeypatch.setenv("STATE_PATH", str(tmp_path / "state.json"))
    assert cli.main(["--ics-file", str(ics)]) == 0
    assert cli.main(["--ics-file", str(ics)]) == 0
    assert [t["content"] for t in server.fake.tasks.values()] == ["📝 Data Structures: HW9"]

    monkeypatch.setenv("TODOIST_API_TOKEN", "bad")
    assert cli.main(["--ics-file", str(ics)]) == 2


def test_course_rename_migrates_existing_tasks(server, tasks, tmp_path, monkeypatch):
    """Replays the real first run: tasks created under raw names like
    '0368215899 - מבני נתונים', then a run with the cleaned names."""
    state_path = tmp_path / "s.json"
    raw = [replace(t, course=t.course_raw or t.course) for t in tasks]   # old behaviour: raw names
    _sync(server, raw, state_path)
    f = server.fake
    old_sections = {s["name"] for s in f.sections.values()}
    assert "044131 - מערכות לינאריות" in old_sections

    # The user drops their own task into one of the old sections → that section must survive.
    keep_sid = next(sid for sid, s in f.sections.items() if s["name"] == "234218-W26")
    f.tasks["9999"] = {"id": "9999", "content": "my own note", "labels": [], "checked": False,
                       "is_deleted": False, "section_id": keep_sid, "description": ""}

    report, state = _sync(server, tasks, state_path)                    # new behaviour: clean names
    # 4 tasks change course name; "236350-W26" has no clean name, so it stays as is.
    assert not report.created and len(report.updated) == 4 and report.unchanged == 1
    assert report.moved_section == 4
    moodle_tasks = [t for t in f.tasks.values() if "moodle" in t["labels"] and not t["is_deleted"]]
    assert len(moodle_tasks) == 5
    by_content = {t["content"]: t for t in moodle_tasks}
    hw2 = by_content["📝 Linear Systems: תרגיל בית 2"]
    assert f.sections[hw2["section_id"]]["name"] == "Linear Systems"
    assert set(hw2["labels"]) == {"moodle", "Linear_Systems"}

    names = {s["name"] for s in f.sections.values()}
    assert "044131 - מערכות לינאריות" not in names       # empty old section removed
    assert "234218-W26" in names                          # still holds the user's own task
    assert not f.tasks["9999"]["is_deleted"]
    labels = {lab["name"] for lab in f.labels.values()}
    assert "044131_-_מערכות_לינאריות" not in labels and "234218-W26" not in labels
    assert {"moodle", "Linear_Systems", "Data_Structures"} <= labels

    # Settles: the next run changes nothing.
    calls = len(f.calls)
    report, _ = _sync(server, tasks, state_path)
    assert report.unchanged == 5 and report.moved_section == 0 and not report.cleaned_up
    assert all(m == "GET" for m, _ in f.calls[calls:])


def test_manual_section_move_is_respected(server, tasks, tmp_path):
    _sync(server, tasks, tmp_path / "s.json")
    f = server.fake
    tid = next(iter(f.tasks))
    other = next(sid for sid in f.sections if sid != f.tasks[tid]["section_id"])
    f.tasks[tid]["section_id"] = other                    # the user drags it elsewhere
    report, _ = _sync(server, tasks, tmp_path / "s.json")
    assert report.moved_section == 0 and f.tasks[tid]["section_id"] == other
