"""
User-editable rules: course mapping and event filtering.

This is the one file you're expected to edit. Run
    python -m moodle_sync --inspect
to see every raw Moodle event, which rule kept or dropped it, and which course
name it was mapped to. Then tune the lists below.

All patterns are Python regular expressions, matched case-insensitively.
"""

# --------------------------------------------------------------------------- #
# 1. Course mapping
# --------------------------------------------------------------------------- #
# Moodle puts the course *short name* in each event's CATEGORIES field
# (e.g. "234218-W26" or "מבני נתונים 2026א"). Map the exact raw value to a
# clean display name. This name is used in the reminder title and notes.
COURSE_MAP: dict[str, str] = {
    # "234218-W26": "Data Structures",
    # "044131 - מערכות לינאריות": "Linear Systems",
}

# Fallback: if the raw value isn't in COURSE_MAP, the first regex that matches
# wins. Handy when the short name changes every semester
# ("234218-W26" -> "234218-S27").
COURSE_PATTERNS: list[tuple[str, str]] = [
    # (r"^234218", "Data Structures"),
    # (r"044131|לינאריות", "Linear Systems"),
]

# Applied to raw course names that aren't in COURSE_MAP / COURSE_PATTERNS.
# The defaults strip numeric course codes, so "0368215899 - מבני נתונים" becomes "מבני נתונים".
COURSE_NAME_CLEANUP: list[str] = [
    r"^\s*\d[\d\-.]{3,}?\s*[-–—:|]\s+",    # leading code:  "0368215899 - Name"
    r"\s+[-–—:|]\s*\d[\d\-.]{3,}\s*$",     # trailing code: "Name - 0368215899"
]

# Courses to ignore entirely (raw short name or clean name).
IGNORED_COURSES: set[str] = {
    # "Library Orientation",
}

# Events with no course (site-wide or personal events). Keep them?
KEEP_EVENTS_WITHOUT_COURSE = False

# --------------------------------------------------------------------------- #
# 2. Filtering
# --------------------------------------------------------------------------- #
# Order of evaluation:
#   past / beyond horizon / ignored course → dropped
#   ALWAYS_EXCLUDE match                   → dropped   ("Quiz 3 opens"), except exams
#   EXCLUDE match, unless STRONG_INCLUDE   → dropped   ("Lecture 5 recording")
#                                            but kept  ("Lecture 1 quiz closes")
#   INCLUDE match                          → kept
ALWAYS_EXCLUDE_PATTERNS: list[str] = [
    # "Quiz 2 opens" / "המטלה נפתחת": for homework and quizzes, the deadline is the
    # *close* event. Exception: exams (EXAM_PATTERNS) are kept, because when an
    # exam opens is when you sit it.
    r"\bopens?\b|\bopening\b",
    r"נפתח|נפתחת|נפתחים|ייפתח|תיפתח|פתיחת",
]

EXCLUDE_PATTERNS: list[str] = [
    r"\blectures?\b|הרצאה|הרצאות",
    r"\brecitation\b|\btutorial session\b|תרגול|תרגולים",       # תרגול = class session (≠ תרגיל = exercise)
    r"\boffice hours?\b|שעות קבלה|שעת קבלה",
    r"\brecording\b|הקלטה|הקלטת",
    r"\battendance\b|נוכחות",
    r"\bzoom\b|\bwebinar\b|זום|וובינר",
]

# Words that make an event a real deadline even if it also matches EXCLUDE
# ("Lecture 1 quiz", "בוחן הרצאה 3").
STRONG_INCLUDE_PATTERNS: list[str] = [
    r"\bdue\b|\bdeadline\b|\bsubmi(t|ssion)\b|\bcloses?\b",
    r"\bquiz(zes)?\b|\bexam\b|\bmidterm\b|\bassignment\b|\bhomework\b|\bhw ?\d+",
    r"הגשה|הגשת|להגיש|מטלה|מטלת|תרגיל בית|ש[\"״]ב|שיעורי בית|ממ[\"״]?ן",
    r"מבחן|בחינה|בחינת|בוחן|מבדק|חידון",
    r"נסגר|ננעל|נעילת|תאריך יעד|מועד אחרון|מועד הגשה|דדליין",
]

INCLUDE_PATTERNS: list[str] = [
    # English
    r"\bis due\b|\bdue\b|\bdeadline\b|\bsubmi(t|ssion)\b|\bcloses?\b",
    r"\bassignment\b|\bhomework\b|\bhw ?\d+|\bex(ercise)? ?\d+|\bproblem set\b|\bpset\b|\bworksheet\b",
    r"\bproject\b|\breport\b|\blab\b|\bessay\b|\bpaper\b|\bpresentation\b|\bmilestone\b|\bcheckpoint\b",
    r"\bexam\b|\bquiz(zes)?\b|\bmidterm\b|\bfinal exam\b|\btest\b|\btake-?home\b",
    # Hebrew: submissions & assignments
    r"הגשה|הגשת|להגיש|מטלה|מטלת|מטלות",
    r"תרגיל|תרגילי|ש[\"״]ב|שיעורי בית|ממ[\"״]?ן|ממ[\"״]?ח",
    r"עבודה|עבודת|עבודות|פרויקט|פרוייקט|דו[\"״]?ח|דוח|רפרט|סמינריון|מצגת|פוסטר",
    # Hebrew: exams & quizzes
    r"מבחן|מבחני|בחינה|בחינת|מועד [אבג]['׳]?|בוחן|בחנים|מבדק|חידון",
    # Hebrew: closing / due-date wording
    r"נסגר|נסגרת|נסגרים|סגירה|ננעל|נעילת|תאריך יעד|מועד אחרון|מועד הגשה|דדליין",
]

# Matching events are tagged as exams: 🎓, priority p1 (red).
EXAM_PATTERNS: list[str] = [
    r"\bexam\b|\bmidterm\b|\bfinal exam\b|\btest\b",
    r"מבחן|מבחני|בחינה|בחינת|מועד [אבג]['׳]?",
]

# Matching events are tagged as quizzes: 🧠, same priority as assignments (p2).
QUIZ_PATTERNS: list[str] = [
    r"\bquiz(zes)?\b",
    r"בוחן|בחנים|מבדק|חידון",
]

# If an event matches neither list, keep it anyway?
KEEP_UNMATCHED = False

# --------------------------------------------------------------------------- #
# 3. Title cleanup
# --------------------------------------------------------------------------- #
# Removes Moodle boilerplate, so "HW3 is due" becomes "HW3".
TITLE_CLEANUP_PATTERNS: list[str] = [
    r"\s+is due$",
    r"\s+closes$",
    r"\s+\(due\)$",
    r"^יש להגיש את\s+",
    r"^(תאריך|מועד) (הגשה|אחרון|סיום|יעד)( של| עבור| ל)?:?\s*[-–]?\s*",
    r"^(due date|deadline)( for)?:?\s*[-–]?\s*",
    r"\s+נסגר(ת)?$",
]
