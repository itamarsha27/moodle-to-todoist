"""Runtime settings, loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE lines). Real environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be true/false, got {raw!r}")


@dataclass(frozen=True)
class Settings:
    ical_url: str | None
    base_url: str | None
    tz: ZoneInfo
    horizon_days: int
    prune_after_days: int
    http_timeout: int
    state_path: Path
    todoist_token: str | None
    todoist_project: str
    todoist_label: str
    course_sections: bool
    course_labels: bool

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(ROOT / ".env")

        ical_url = os.getenv("MOODLE_ICAL_URL", "").strip() or None
        base_url = os.getenv("MOODLE_BASE_URL", "").strip() or None
        if not base_url and ical_url:
            parts = urlsplit(ical_url)
            base_url = f"{parts.scheme}://{parts.netloc}"
            # Moodle can be installed under a sub-path, e.g. /moodle/calendar/export_execute.php
            prefix = parts.path.split("/calendar/")[0]
            if prefix and prefix != parts.path:
                base_url += prefix
        if base_url:
            base_url = base_url.rstrip("/")

        tz_name = os.getenv("TIMEZONE", "Asia/Jerusalem").strip()
        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError as exc:
            raise ConfigError(f"Unknown TIMEZONE {tz_name!r}") from exc

        def _path(name: str, default: str) -> Path:
            p = Path(os.getenv(name, default))
            return p if p.is_absolute() else ROOT / p

        return cls(
            ical_url=ical_url,
            base_url=base_url,
            tz=tz,
            horizon_days=_env_int("HORIZON_DAYS", 180),
            prune_after_days=_env_int("PRUNE_AFTER_DAYS", 30),
            http_timeout=_env_int("HTTP_TIMEOUT", 30),
            state_path=_path("STATE_PATH", "data/synced_tasks.json"),
            todoist_token=os.getenv("TODOIST_API_TOKEN", "").strip() or None,
            todoist_project=os.getenv("TODOIST_PROJECT", "").strip() or "University",
            todoist_label=os.getenv("TODOIST_LABEL", "").strip() or "moodle",
            course_sections=_env_bool("TODOIST_COURSE_SECTIONS", True),
            course_labels=_env_bool("TODOIST_COURSE_LABELS", True),
        )
