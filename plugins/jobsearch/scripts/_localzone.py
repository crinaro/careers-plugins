#!/usr/bin/env python3
"""Which wall clock is the CANDIDATE reading? Never assume; resolve it.

⭐ WHY THIS EXISTS
------------------
`parse_ics.py` and `parse_meeting_mail.py` both carried:

    LOCAL_STD, LOCAL_DST = -8, -7  # <the commute anchor>, CA

A purity pass redacted the city in that comment and left the numbers. **That removed the evidence
of the assumption and kept the assumption** — the engine still hard-coded US Pacific, in the one
code path that decodes when an interview starts. For anyone outside Pacific the answer was wrong
by hours, silently, with a plausible-looking time printed next to it.

There was a second layer under it: `in_us_dst()` hard-codes the *US* changeover dates, so a
non-US candidate was wrong twice — wrong offset, and wrong on the days either side of a European
or Southern-Hemisphere transition, which do not fall on US dates.

⚠️ A WRONG TIME IS THE WORST SHAPE OF ERROR HERE. It does not raise. It prints a confident
"TIME: 08:00 PDT" and the candidate misses the interview. Everything else in this engine treats a
thing it cannot determine as a REPORTED GAP; this path quietly guessed.

## The resolution order, and why config comes first

    1. config.json  geography.timezone   an IANA name, e.g. "America/New_York"
    2. the TZ environment variable
    3. the host's own local zone

**Config outranks the host on purpose.** Surfaces S4/S5 run this unattended in a container whose
clock is UTC. Host-derived would then be confidently wrong for every scheduled run, which is
exactly the class of failure that costs a real meeting. A candidate who declares their zone gets
the same answer everywhere; one who declares nothing gets their laptop's zone, which is right on
the surface where a person is present to notice.

⚠️ Offsets are returned as FLOAT hours, not int. India is +5.5 and Nepal +5.75; an int return
type is the same parochial assumption in a different disguise.

Python 3.9+. Standard library only — `zoneinfo` is 3.9+, and a host without a tz database falls
back to the host zone rather than raising.
"""

import json
import os
import sys
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:                       # no tzdata on this host
    ZoneInfo = None

_CACHE = []                               # [(tzinfo|None, name|None)] — resolved at most once


def _configured_name():
    """The zone the candidate DECLARED, or None. Never raises: this is a display path."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from _root import profile_root
        with open(os.path.join(profile_root(), "config.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
        name = (cfg.get("geography") or {}).get("timezone")
        return name.strip() or None if isinstance(name, str) else None
    except Exception:
        return None


def _zone():
    if _CACHE:
        return _CACHE[0]
    name = _configured_name() or os.environ.get("TZ") or None
    tz = None
    if name and ZoneInfo is not None:
        try:
            tz = ZoneInfo(name)
        except Exception:
            tz = None                     # a name we cannot load is not a reason to guess wrong
    _CACHE.append((tz, name))
    return _CACHE[0]


def reset_cache():
    """Tests change the profile under us; production resolves once."""
    del _CACHE[:]


def offset_for_local(naive_local):
    """UTC offset, in hours, that applies to this naive LOCAL wall time."""
    tz, _ = _zone()
    if tz is not None:
        return naive_local.replace(tzinfo=tz).utcoffset().total_seconds() / 3600.0
    # A naive datetime's .astimezone() is interpreted as local time by the platform, which
    # applies that zone's real DST rules — not a US-shaped approximation of them.
    return naive_local.astimezone().utcoffset().total_seconds() / 3600.0


def offset_for_utc(naive_utc):
    """UTC offset, in hours, that applies at this instant (given as a naive UTC datetime).

    Separate from offset_for_local on purpose. Converting an instant used to mean guessing a
    local time, then asking what offset applied to the guess — which is wrong for any instant
    within an hour of a DST transition. An instant has exactly one answer; ask for it directly.
    """
    aware = naive_utc.replace(tzinfo=timezone.utc)
    tz, _ = _zone()
    target = aware.astimezone(tz) if tz is not None else aware.astimezone()
    return target.utcoffset().total_seconds() / 3600.0


def label_for_local(naive_local):
    """Short zone label for display — 'PDT', 'CEST', '+0530'. Never empty."""
    tz, _ = _zone()
    dt = naive_local.replace(tzinfo=tz) if tz is not None else naive_local.astimezone()
    return dt.tzname() or dt.strftime("%z") or "local"


def display_name():
    """How to NAME the candidate's zone in a message to them."""
    _, name = _zone()
    if name:
        return name
    return datetime.now().astimezone().tzname() or "local time"


if __name__ == "__main__":
    now = datetime.now()
    print("zone:   %s" % display_name())
    print("offset: %+g h" % offset_for_local(now))
    print("label:  %s" % label_for_local(now))
