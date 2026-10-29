"""
A small, dependency-free RFC 5545 (iCal) reader. It covers what Moodle emits:
line folding, escaped text, parameters, UTC, TZID, floating times and all-day dates.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import MoodleEvent

log = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>\"')]+")
_TAG_RE = re.compile(r"<[^>]+>")


# --------------------------------------------------------------------------- #
# Low-level iCal helpers
# --------------------------------------------------------------------------- #
def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _split_line(line: str) -> tuple[str, dict[str, str], str]:
    """'DTSTART;TZID=Asia/Jerusalem:20261103T235900' -> (name, params, value)."""
    # The first ':' that is not inside a quoted parameter value separates name/params from value.
    in_quotes = False
    for i, ch in enumerate(line):
        if ch == '"':
            in_quotes = not in_quotes
        elif ch == ":" and not in_quotes:
            head, value = line[:i], line[i + 1 :]
            break
    else:
        return line.upper(), {}, ""

    name, *param_parts = head.split(";")
    params = {}
    for p in param_parts:
        k, _, v = p.partition("=")
        params[k.upper()] = v.strip('"')
    return name.upper(), params, value


def _unescape(value: str) -> str:
    out, i = [], 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append("\n" if nxt in "nN" else nxt)
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _parse_dt(value: str, params: dict[str, str], tz: ZoneInfo) -> datetime:
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        d = datetime.strptime(value, "%Y%m%d").date()
        # All-day deadline: treat as end of that day.
        return datetime.combine(d, time(23, 59), tzinfo=tz)

    if value.endswith("Z"):
        dt = datetime.strptime(value[:-1], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        return dt.astimezone(tz)

    dt = datetime.strptime(value, "%Y%m%dT%H%M%S")
    src_tz = tz
    if "TZID" in params:
        try:
            src_tz = ZoneInfo(params["TZID"])
        except (ZoneInfoNotFoundError, ValueError):
            log.debug("Unknown TZID %r, assuming %s", params["TZID"], tz)
    return dt.replace(tzinfo=src_tz).astimezone(tz)


def _clean_text(text: str) -> str:
    text = html.unescape(_TAG_RE.sub(" ", text))
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def parse_calendar(data: bytes | str, tz: ZoneInfo, base_url: str | None = None) -> list[MoodleEvent]:
    text = data.decode("utf-8-sig", "replace") if isinstance(data, bytes) else data
    events: list[MoodleEvent] = []
    current: dict[str, tuple[dict[str, str], str]] | None = None
    depth = 0  # handle nested components such as VALARM inside VEVENT

    for line in _unfold(text):
        name, params, value = _split_line(line)
        if name == "BEGIN" and value.upper() == "VEVENT":
            current, depth = {}, 0
            continue
        if current is None:
            continue
        if name == "BEGIN":
            depth += 1
            continue
        if name == "END":
            if depth:
                depth -= 1
                continue
            if value.upper() == "VEVENT":
                ev = _build_event(current, tz, base_url)
                if ev:
                    events.append(ev)
                current = None
            continue
        if depth == 0 and name not in current:  # keep first occurrence
            current[name] = (params, value)

    log.info("Parsed %d events from calendar", len(events))
    return events


def _build_event(props: dict, tz: ZoneInfo, base_url: str | None) -> MoodleEvent | None:
    try:
        uid = _unescape(props["UID"][1]).strip()
        start_params, start_val = props.get("DTSTART") or props["DTEND"]
        due = _parse_dt(start_val, start_params, tz)
    except (KeyError, ValueError) as exc:
        log.warning("Skipping malformed event (%s): %s", exc, props.get("SUMMARY", ("", "?"))[1])
        return None

    summary = _clean_text(_unescape(props.get("SUMMARY", ({}, ""))[1]))
    description = _clean_text(_unescape(props.get("DESCRIPTION", ({}, ""))[1]))
    categories = _unescape(props.get("CATEGORIES", ({}, ""))[1]).strip()

    last_modified = None
    if "LAST-MODIFIED" in props:
        try:
            last_modified = _parse_dt(props["LAST-MODIFIED"][1], props["LAST-MODIFIED"][0], tz)
        except ValueError:
            pass

    url = props.get("URL", ({}, ""))[1].strip()
    if not url:
        m = _URL_RE.search(description)
        url = m.group(0) if m else ""
    if not url and base_url:
        url = f"{base_url}/calendar/view.php?view=day&time={int(due.timestamp())}"

    return MoodleEvent(
        uid=uid,
        summary=summary,
        description=description,
        course_raw=categories,
        due=due,
        url=url,
        last_modified=last_modified,
    )
