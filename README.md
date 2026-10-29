# Moodle → Todoist 📚➜✅

Your Moodle deadlines (assignments, quizzes and exams) automatically appear in
**Todoist**, with the exact due date, a reminder, the course name and a link to
Moodle. It runs every morning on GitHub for free. After a 10-minute setup you
never touch it again, and you never need a terminal.

Works with any university's Moodle, in Hebrew or English.

```
Moodle calendar ──► GitHub (every day at 10:00) ──► Todoist: phone, iPad widget, Windows, Mac, web
```

**What it looks like in Todoist:** a project called **University**, with one section per course:

* 🎓 `Linear Systems: Midterm`: exams, red priority
* 🧠 `Software 1: Lecture 3 Quiz`: quizzes
* 📝 `Data Structures: תרגיל בית 2`: assignments

**Cost:** free. Todoist's free plan is enough, and the daily run uses about 3%
of GitHub's free monthly allowance. GitHub won't charge you unless you add a
payment method *and* raise your spending limit.

---

## Setup (about 10 minutes, all in the browser)

### 1. Make your own private copy

Click the green **Use this template** button (top right of this page) →
**Create a new repository**.

* **Name:** anything, e.g. `my-moodle-sync`
* **Visibility: ⚠️ choose Private.** Your copy will store a list of your courses and deadlines.

> ❌ Don't use **Fork**: forks are forced to be public, and GitHub turns off
> their daily schedule.

### 2. Get your Todoist token

1. Sign up at [todoist.com](https://todoist.com) (free) and install the app on your phone, tablet or computer.
2. Todoist → **Settings → Integrations → Developer** → copy the **API token**.

### 3. Get your Moodle calendar link

1. In Moodle, open the **Calendar** (or go to `your-moodle-site/calendar/view.php`).
2. Scroll down → **Export calendar** (sometimes under *Import or export calendars*).
3. Events to export: **All events**. Time period: **Recent and next 60 days**.
4. Click **Get calendar URL** → copy the link.

### 4. Add both as secrets in your copy

In **your** repository: **Settings → Secrets and variables → Actions → New repository secret**, twice:

| Name | Value |
|---|---|
| `MOODLE_ICAL_URL` | the Moodle link |
| `TODOIST_API_TOKEN` | the Todoist token |

> 🔒 Both work like passwords. Only paste them into GitHub's secrets page, never into the code, an issue or a chat.

### 5. Run it

**Actions** tab → if asked, click **I understand my workflows, go ahead and enable them** →
**Sync Moodle → Todoist** (left) → **Run workflow** → **Run workflow**.

After about a minute the run turns green ✅ and your deadlines are in Todoist. 🎉
From now on it runs **every day at 10:00 (Israel time)** by itself.

### 6. Recommended extras

* **Reminder timing:** Todoist → Settings → **Reminders** → choose how early to be alerted.
* **Widget:** add the Todoist widget to your phone or iPad home screen.
* **Failure emails:** GitHub avatar → Settings → **Notifications → Actions** →
  "Only notify for failed workflows". If your Moodle link ever stops working, you'll know.

---

## FAQ

**How do I know it's running?**
The **Actions** tab shows two *Scheduled* runs each morning. One syncs and the other
skips on purpose; that's how 10:00 survives daylight-saving changes. Both should be green.
To sync right now, click **Run workflow** anytime. Running it extra times is always safe.

**Will it create duplicates?** No. Re-runs change nothing. If a lecturer moves a deadline,
the existing task is updated (marked ⚠️). Tasks you complete or delete are never re-added.
Your own Todoist tasks are never touched; only tasks labelled `@moodle` are.

**A deadline is missing / something irrelevant was added.**
Open the latest run in the **Actions** tab and scroll to the bottom: **"All Moodle events
and filter decisions"** shows every event and why it was kept or dropped. The keyword
lists are in [`moodle_sync/rules.py`](moodle_sync/rules.py). Open it in your copy, click ✏️,
add the word, and commit. It already knows מבחן, בוחן, מבדק, מטלה, הגשה, עבודה, ש"ב, ממ"ן,
quiz, exam, assignment, due, … and ignores lectures (הרצאה), recitations (תרגול), office hours, etc.

**I want nicer course names.** Codes like `0368215899 - Data Structures` are cleaned
automatically. For custom names, edit `COURSE_MAP` in `moodle_sync/rules.py`:
```python
COURSE_MAP = {
    "0368215899 - מבני נתונים": "Data Structures",
}
```
Existing tasks move to the renamed section automatically on the next run.

**Different time or timezone?** Repo **Settings → Secrets and variables → Actions →
Variables** tab → add `RUN_HOUR` (e.g. `8`) and/or `TIMEZONE` (e.g. `Europe/London`).

**The run failed.**
| Error says | Fix |
|---|---|
| *did not return an iCal file* | Your Moodle link expired (e.g. you changed your Moodle password). Get a new one (step 3) and update the secret. |
| *rejected the API token* | Copy the Todoist token again (step 2) and update the secret. |
| HTTP 403 / timeouts from Moodle | Some universities block access from abroad, and GitHub runs in the US. Ask the person who shared this with you about `scripts/run_local.sh`. |

---

<details>
<summary>For developers</summary>

Python 3.10+, standard library only (no dependencies). `pytest` runs the tests,
including end-to-end runs against a fake Todoist server.

```
moodle_sync/
  rules.py     course mapping + keyword rules (the file to edit)
  fetcher.py   Moodle iCal download (retries, token redaction)
  parser.py    iCal parser
  filters.py   keep/drop decisions + course mapping
  todoist.py   Todoist API v1 client
  sync.py      reconciliation: create / update in place / never recreate
  state.py     data/synced_tasks.json, the dedup state the workflow commits back
tests/         pytest suite + fake Todoist server
```

Local run: `cp .env.example .env`, fill both values, then
`python3 -m moodle_sync --inspect` (show filter decisions) or `--dry-run`.
</details>
