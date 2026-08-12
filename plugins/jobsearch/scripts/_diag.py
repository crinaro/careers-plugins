#!/usr/bin/env python3
"""A diagnostic log that carries NO user data — so a silent no-op is distinguishable from a bug.

⭐ WHY THIS EXISTS
------------------
`migrate.py` refuses to apply a destructive migration and deliberately does not stamp, so it will
retry next session. That is correct. But it meant **"refused" and "never ran" left identical
evidence** — no stamp, unchanged file — and there was no way to tell which had happened from the
outside. That is this project's signature failure, a missing thing reading as an empty thing, in
the very tool written to prevent it.

So: every run records that it ran and what it decided, separately from the stamp that gates retries.

⭐⭐ WHAT MAY NEVER GO IN HERE.
This log lives outside the profile repo precisely so it can be read, pasted into an issue, or
attached to a bug report without a second thought. That property only holds if it is boring:

    ALLOWED   an event name · a verdict code · a version · a COUNT · a fixed reason code
    ⛔ NEVER  a file's contents · a company, role, contact or mailbox · a comp figure ·
              a path inside the profile · anything free-text that came from the user's data

`redact()` enforces the shape by only ever writing scalars the caller passed as keyword
arguments, and by refusing values that look like prose. **A log you have to sanitise before
sharing is a log nobody shares**, and then the diagnostic value is zero.

Location: `~/.claude/jobsearch/diagnostics.log` — engine-side, not the profile, so it is never
committed into a private repo and never crosses into the engine repo either.

⭐ THE PATH IS OVERRIDABLE — `CLAUDESEARCH_DIAG_LOG`, same shape as `CLAUDESEARCH_LOCK_PATH`
(GitHub #9). Without this, the regression suite's own migration tests appended straight into
the REAL production log: they exercise migrations against synthetic temp fixtures, so the log
ended up recording `applied` events for schema versions never actually applied to any real
profile — actively misleading a diagnosis, which is worse than the log not existing. Set once,
before this module is imported, and every caller in-process sees it; a subprocess picks it up
fresh from the environment the same way.

Append-only, one JSON object per line, capped so it cannot grow without bound.
Python 3.9+. Standard library only.
"""

import json
import os
import re
import time

LOG = os.environ.get("CLAUDESEARCH_DIAG_LOG") or os.path.join(
    os.path.expanduser("~"), ".claude", "jobsearch", "diagnostics.log")
MAX_LINES = 500

# A value that is long, or contains spaces plus mixed case, is prose — and prose is where user
# data hides. Codes, versions, counts and booleans are what this log is for.
_CODE = re.compile(r"^[A-Za-z0-9_.:+-]{0,64}$")


def _now():
    """UTC, second precision, `_CODE`-safe (no spaces) so `redact()` never has to touch it."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def redact(value):
    """Return the value if it is demonstrably data-free, else a shape description."""
    if isinstance(value, bool) or isinstance(value, int):
        return value
    if value is None:
        return None
    s = str(value)
    if _CODE.match(s):
        return s
    return "<%d chars omitted>" % len(s)


def log(event, when=None, **fields):
    """Append one event. Best-effort and silent — diagnostics must never break a run.

    `when` is passed IN rather than read from the clock BY DEFAULT, so a caller that already
    knows the run's timestamp (or wants a deterministic value in a test) can supply its own and
    have it recorded exactly as given — this parameter stays; do not remove it.

    ⚠️ But a caller that has NO opinion must not leave the event untimed (GitHub #10).
    `migrate.py` never passed `when`, so every migration event in the log carried no timestamp
    at all, and the log — a ring buffer, so position cannot stand in for time either — could not
    answer "did this happen after the reboot", which is the one question an event log exists
    for. So: stamp at write time whenever the caller supplies nothing.
    """
    try:
        rec = {"event": str(event)[:64], "at": redact(when) if when else _now()}
        for k, v in sorted(fields.items()):
            rec[str(k)[:32]] = redact(v)
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        lines = []
        if os.path.exists(LOG):
            with open(LOG, "r", encoding="utf-8") as fh:
                lines = fh.readlines()[-(MAX_LINES - 1):]
        lines.append(json.dumps(rec, sort_keys=True) + "\n")
        tmp = LOG + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.writelines(lines)
        os.replace(tmp, LOG)
    except Exception:
        pass


def tail(n=20):
    try:
        with open(LOG, "r", encoding="utf-8") as fh:
            return [l.rstrip("\n") for l in fh.readlines()[-n:]]
    except OSError:
        return []
