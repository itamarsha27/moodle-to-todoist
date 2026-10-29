"""
CLI entry point.

    python -m moodle_sync              # fetch Moodle, sync to Todoist
    python -m moodle_sync --dry-run    # show what would change; touches nothing
    python -m moodle_sync --inspect    # every raw Moodle event + keep/drop reason
    python -m moodle_sync --ics-file tests/fixtures/sample.ics   # use a local file
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from .config import ConfigError, Settings
from .fetcher import FetchError, fetch_ical
from .filters import decide, resolve_course, select_tasks
from .parser import parse_calendar
from .state import StateStore
from .sync import TodoistSettings, run_sync
from .todoist import DEFAULT_BASE_URL, TodoistClient, TodoistError

log = logging.getLogger("moodle_sync")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")


def _inspect(events, now, settings) -> None:
    print(f"{'KEEP':<5} {'DUE':<17} {'COURSE':<28} {'SUMMARY':<45} REASON")
    print("-" * 124)
    for ev in sorted(events, key=lambda e: e.due):
        d = decide(ev, now, settings.horizon_days)
        course = resolve_course(ev.course_raw)
        if ev.course_raw and course != ev.course_raw:
            course = f"{course} <- {ev.course_raw}"
        print(f"{'✅' if d.keep else '·':<5} {ev.due:%Y-%m-%d %H:%M} {course[:28]:<28} {ev.summary[:45]:<45} {d.reason}")
    print("\nRaw course names seen (paste these into COURSE_MAP in moodle_sync/rules.py):")
    for raw in sorted({e.course_raw for e in events if e.course_raw}):
        print(f'    "{raw}": "{resolve_course(raw)}",')


def _decision_table(events, now, settings) -> list[str]:
    """Every Moodle event and why it was kept/dropped, for the GitHub run summary."""
    rows = ["", "<details><summary>All Moodle events and filter decisions</summary>", "",
            "| | Due | Course | Moodle title | Reason |", "|---|---|---|---|---|"]
    for ev in sorted(events, key=lambda e: e.due):
        d = decide(ev, now, settings.horizon_days)
        cell = lambda x: str(x).replace("|", "\\|")
        rows.append(f"| {'✅' if d.keep else '·'} | {ev.due:%d/%m %H:%M} | {cell(resolve_course(ev.course_raw))} "
                    f"| {cell(ev.summary)} | {cell(d.reason)} |")
    rows += ["", "</details>"]
    return rows


def _step_summary(lines: list[str]) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="moodle_sync", description="Sync Moodle deadlines into Todoist.")
    ap.add_argument("--dry-run", action="store_true", help="show planned changes; don't call Todoist or write state")
    ap.add_argument("--inspect", action="store_true", help="print every Moodle event with its filter decision, then exit")
    ap.add_argument("--ics-file", type=Path, help="read a local .ics file instead of fetching")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    _setup_logging(args.verbose)

    try:
        settings = Settings.from_env()
        now = datetime.now(settings.tz)

        if args.ics_file:
            raw = args.ics_file.read_bytes()
        elif settings.ical_url:
            raw = fetch_ical(settings.ical_url, timeout=settings.http_timeout)
        else:
            raise ConfigError("MOODLE_ICAL_URL is not set (put it in .env or a GitHub secret)")

        events = parse_calendar(raw, settings.tz, settings.base_url)
        if args.inspect:
            _inspect(events, now, settings)
            return 0

        tasks = select_tasks(events, now, settings.horizon_days)
        log.info("%d of %d Moodle events are actionable", len(tasks), len(events))

        client = None
        if not args.dry_run:
            if not settings.todoist_token:
                raise ConfigError("TODOIST_API_TOKEN is not set (put it in .env or a GitHub secret)")
            client = TodoistClient(
                settings.todoist_token,
                base_url=os.getenv("TODOIST_BASE_URL", DEFAULT_BASE_URL),
                timeout=settings.http_timeout,
            )

        state = StateStore(settings.state_path).load()
        cfg = TodoistSettings(
            project=settings.todoist_project,
            sync_label=settings.todoist_label,
            course_sections=settings.course_sections,
            course_labels=settings.course_labels,
        )
        report = run_sync(tasks, client, state, cfg, now, settings.prune_after_days, dry_run=args.dry_run)

        log.info(
            "Done: created=%d moved=%d updated=%d relinked=%d unchanged=%d already-closed=%d pruned=%d errors=%d%s",
            len(report.created), len(report.rescheduled), len(report.updated), len(report.adopted),
            report.unchanged, report.closed_by_user, report.pruned, len(report.errors),
            " (dry run)" if args.dry_run else "",
        )
        _step_summary(
            [
                "### Moodle → Todoist",
                f"- {len(tasks)} upcoming tasks in Moodle",
                f"- created **{len(report.created)}**, deadline moved **{len(report.rescheduled)}**, "
                f"updated **{len(report.updated)}**, unchanged {report.unchanged}",
            ]
            + ([f"- cleaned up: {', '.join(report.cleaned_up)}"] if report.cleaned_up else [])
            + [f"- ❌ {e}" for e in report.errors]
            + _decision_table(events, now, settings)
        )
        return 1 if report.errors else 0

    except (ConfigError, FetchError, TodoistError) as exc:
        log.error("%s", exc)
        return 2
    except Exception:  # noqa: BLE001 - top-level safety net, with traceback
        log.exception("Unexpected failure")
        return 1


if __name__ == "__main__":
    sys.exit(main())
