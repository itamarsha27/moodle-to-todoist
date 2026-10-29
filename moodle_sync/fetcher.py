"""Download the Moodle iCal export, with retries. Never logs the secret URL."""

from __future__ import annotations

import logging
import re
import time
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

USER_AGENT = "moodle-reminders-sync/1.0 (+https://github.com)"


class FetchError(RuntimeError):
    pass


def redact(url: str) -> str:
    """Hide the authtoken (and userid) so the URL is safe to log."""
    return re.sub(r"(authtoken|userid|token)=[^&\s'\">]+", r"\1=***", url)


def fetch_ical(url: str, timeout: int = 30, attempts: int = 4) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/calendar"})
    last_exc: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
            break
        except urllib.error.HTTPError as exc:
            # 4xx (except 429) won't fix itself on retry: bad or expired token, blocked IP, ...
            if 400 <= exc.code < 500 and exc.code != 429:
                raise FetchError(
                    f"Moodle returned HTTP {exc.code} for {redact(url)}. "
                    "If this is 403/404, your calendar token may be invalid, or the "
                    "university may block requests from outside the country "
                    "(see README, 'Troubleshooting')."
                ) from None
            last_exc = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last_exc = exc

        if attempt < attempts:
            delay = 2 ** attempt
            log.warning("Fetch attempt %d/%d failed (%s); retrying in %ds", attempt, attempts, redact(str(last_exc)), delay)
            time.sleep(delay)
    else:
        # `from None`: the original traceback would include the secret URL.
        raise FetchError(f"Could not reach Moodle after {attempts} attempts: {redact(str(last_exc))}") from None

    # An invalid/expired token usually gets an HTML error page with HTTP 200.
    head = body.lstrip()[:200].decode("utf-8", "replace")
    if "BEGIN:VCALENDAR" not in head:
        raise FetchError(
            "Moodle did not return an iCal file (got HTML or an error page instead). "
            "Your calendar export URL is probably expired: generate a new one and "
            "update MOODLE_ICAL_URL."
        )
    log.info("Fetched %d bytes from %s", len(body), redact(url))
    return body
