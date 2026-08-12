#!/usr/bin/env python3
"""Decode a calendar invite (.ics) into a plain-text answer: what day, what time.

Why this exists: .ics attachments arrive as base64 blobs that are invisible to a
text-only read of the message, so a scheduled invite can land in the inbox and
leave the tracker with a time but no DATE (this happened twice in the week of
2026-07-12 — the <an employer> call, the top-ranked pursuit, and <a recruiter>/<a firm>).

It also enforces CLAUDE.md's hard rule on dates: the output always states the
day of week explicitly, computed from the timestamp, never inferred.

Usage:
    python3 scripts/parse_ics.py invite.ics
    pbpaste | python3 scripts/parse_ics.py -

Input may be a raw .ics file, a base64-encoded one (auto-detected), or an email
body with the .ics inline. Times are reported in the candidate's own zone, which
is RESOLVED by _localzone.py (config, then TZ, then the host) and never assumed —
this file hard-coded US Pacific until 2026-08-10.

Targets Python 3.9+ with NO third-party packages, so it runs unattended in
scheduled tasks. (Raised from 3.8 on 2026-07-20 after confirming the interpreter
these scripts actually resolve to is /usr/bin/python3 = 3.9.6.)

Timezones: uses stdlib **zoneinfo** (3.9+) for exact, DST-correct, WORLDWIDE
conversion, including Windows/Outlook/Zoom TZIDs like "Eastern Standard Time" via
an alias map. Falls back to the hand-maintained US offset table if zoneinfo is
ever unavailable. Before 2026-07-20 there was no zoneinfo and anything outside
that US table printed NOT CONVERTED — Europe/Dublin among them, which mattered
because this candidate's work authorization puts non-US roles in scope (see
user.json; a different candidate's own authorization may put a different set of
zones in scope, which is exactly why this can't be a fixed table).

**A genuinely unresolvable zone still refuses to convert rather than guessing.**
A wrong meeting time is worse than an unconverted one — that principle is
unchanged, it just applies far less often now.
"""

import argparse
import base64
import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _localzone

# zoneinfo is stdlib from Python 3.9. Confirmed available 2026-07-20 on the
# interpreter these scripts actually resolve to (/usr/bin/python3 = 3.9.6).
# Before this, the script REFUSED to convert anything outside the hand-maintained
# US table below -- Europe/Dublin printed NOT CONVERTED, which matters because
# this candidate's work authorization puts non-US roles in scope, and another
# candidate's own authorization may put an entirely different set of zones in
# scope (see user.json — never assume US-only from the engine).
# Kept behind a try/except so the script degrades to the offset table rather than
# breaking if it ever runs somewhere without zoneinfo.
try:
    from zoneinfo import ZoneInfo
    HAVE_ZONEINFO = True
except ImportError:
    ZoneInfo = None
    HAVE_ZONEINFO = False

# Windows/Outlook/Zoom TZIDs -> IANA, so zoneinfo can resolve them too.
WINDOWS_TO_IANA = {
    "EASTERN STANDARD TIME": "America/New_York",
    "EASTERN DAYLIGHT TIME": "America/New_York",
    "CENTRAL STANDARD TIME": "America/Chicago",
    "CENTRAL DAYLIGHT TIME": "America/Chicago",
    "MOUNTAIN STANDARD TIME": "America/Denver",
    "MOUNTAIN DAYLIGHT TIME": "America/Denver",
    "PACIFIC STANDARD TIME": "America/Los_Angeles",
    "PACIFIC DAYLIGHT TIME": "America/Los_Angeles",
    "US MOUNTAIN STANDARD TIME": "America/Phoenix",
    "GMT STANDARD TIME": "Europe/London",
    "W. EUROPE STANDARD TIME": "Europe/Berlin",
    "ROMANCE STANDARD TIME": "Europe/Paris",
    "CENTRAL EUROPE STANDARD TIME": "Europe/Budapest",
    "INDIA STANDARD TIME": "Asia/Kolkata",
    "COORDINATED UNIVERSAL TIME": "UTC",
}


def zone_to_utc(naive, tzid):
    """Convert a naive local datetime to UTC using zoneinfo. Returns None if the
    zone can't be resolved -- callers must then refuse to guess, as before."""
    if not HAVE_ZONEINFO or not tzid:
        return None
    for cand in (tzid, WINDOWS_TO_IANA.get(tzid.upper(), "")):
        if not cand:
            continue
        try:
            aware = naive.replace(tzinfo=ZoneInfo(cand))
            return aware.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        except Exception:
            continue
    return None

# Offsets from UTC in hours: (standard, daylight)
US_ZONES = {
    "PACIFIC": (-8, -7), "US/PACIFIC": (-8, -7), "AMERICA/LOS_ANGELES": (-8, -7),
    "MOUNTAIN": (-7, -6), "US/MOUNTAIN": (-7, -6), "AMERICA/DENVER": (-7, -6),
    "AMERICA/PHOENIX": (-7, -7),  # Arizona: no DST
    "CENTRAL": (-6, -5), "US/CENTRAL": (-6, -5), "AMERICA/CHICAGO": (-6, -5),
    "EASTERN": (-5, -4), "US/EASTERN": (-5, -4), "AMERICA/NEW_YORK": (-5, -4),
    "UTC": (0, 0), "GMT": (0, 0), "ETC/UTC": (0, 0),

    # Windows/Outlook/Zoom-style TZIDs. Added 2026-07-20, the first time this
    # script was ever run on a real downloaded .ics -- and it punted with
    # NOT CONVERTED because Zoom emits these names, not IANA ones.
    #
    # IMPORTANT SEMANTICS: "Eastern Standard Time" here is the NAME OF THE ZONE,
    # not an assertion that the date falls in standard time. Windows uses the
    # "... Standard Time" label year-round, including during DST. So these map
    # to the same (std, dst) pairs as their IANA equivalents and the script's
    # normal US DST rule decides which half applies -- exactly right for the
    # <an employer> invite, whose TZID says "Eastern Standard Time" for a July date
    # that is actually EDT.
    "EASTERN STANDARD TIME": (-5, -4), "EASTERN DAYLIGHT TIME": (-5, -4),
    "CENTRAL STANDARD TIME": (-6, -5), "CENTRAL DAYLIGHT TIME": (-6, -5),
    "MOUNTAIN STANDARD TIME": (-7, -6), "MOUNTAIN DAYLIGHT TIME": (-7, -6),
    "PACIFIC STANDARD TIME": (-8, -7), "PACIFIC DAYLIGHT TIME": (-8, -7),
    "US MOUNTAIN STANDARD TIME": (-7, -7),  # Arizona: no DST
    "COORDINATED UNIVERSAL TIME": (0, 0),
}
# ⭐ The candidate's own zone is RESOLVED, never assumed — see _localzone.py. This file used to
# hard-code US Pacific here, which printed a confident wrong time for anyone else.


def nth_weekday(year, month, weekday, n):
    """Date of the nth given weekday (0=Mon) in a month. Sunday=6."""
    d = datetime(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def in_us_dst(naive_local):
    """US DST: 2nd Sunday of March 02:00 -> 1st Sunday of November 02:00."""
    y = naive_local.year
    start = nth_weekday(y, 3, 6, 2).replace(hour=2)
    end = nth_weekday(y, 11, 6, 1).replace(hour=2)
    return start <= naive_local < end


def local_offset(naive_local):
    return _localzone.offset_for_local(naive_local)


def local_label(naive_local):
    return _localzone.label_for_local(naive_local)


def load(source):
    """Read input, transparently base64-decoding if that's what it is."""
    if source == "-":
        raw = sys.stdin.read()
    else:
        with open(source, "r", errors="replace") as fh:
            raw = fh.read()

    if "BEGIN:VCALENDAR" in raw or "BEGIN:VEVENT" in raw:
        return raw

    candidate = re.sub(r"\s+", "", raw)
    for variant in (candidate, candidate.replace("-", "+").replace("_", "/")):
        try:
            padded = variant + "=" * (-len(variant) % 4)
            decoded = base64.b64decode(padded).decode("utf-8", errors="replace")
            if "BEGIN:VCALENDAR" in decoded or "BEGIN:VEVENT" in decoded:
                return decoded
        except Exception:
            continue
    return raw  # caller reports "no VEVENT found"


def unfold(text):
    """RFC 5545 line unfolding: a leading space/tab continues the previous line."""
    out = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def split_params(prop):
    """'DTSTART;TZID=America/Chicago' -> ('DTSTART', {'TZID': 'America/Chicago'})."""
    parts = prop.split(";")
    params = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k.upper()] = v.strip('"')
    return parts[0].upper(), params


def parse_dt(value, params):
    """Return dict describing the instant, or None.

    keys: naive (datetime in its own zone), utc (datetime or None if unknown zone),
          all_day (bool), zone (label), exact (bool - False if we couldn't convert)
    """
    value = value.strip()

    if params.get("VALUE") == "DATE" or re.match(r"^\d{8}$", value):
        try:
            return {"naive": datetime.strptime(value, "%Y%m%d"), "utc": None,
                    "all_day": True, "zone": "", "exact": True}
        except ValueError:
            return None

    fmt = "%Y%m%dT%H%M%S"
    if value.endswith("Z"):
        try:
            dt = datetime.strptime(value[:-1], fmt)
            return {"naive": dt, "utc": dt, "all_day": False, "zone": "UTC", "exact": True}
        except ValueError:
            return None

    try:
        naive = datetime.strptime(value, fmt)
    except ValueError:
        return None

    tzid = params.get("TZID", "")

    # Prefer zoneinfo: exact, worldwide, DST-correct, and no hand-maintained table.
    utc = zone_to_utc(naive, tzid)
    if utc is not None:
        return {"naive": naive, "utc": utc, "all_day": False,
                "zone": tzid or "UTC", "exact": True}

    key = tzid.upper()
    if key in US_ZONES:
        std, dst = US_ZONES[key]
        off = dst if (std != dst and in_us_dst(naive)) else std
        return {"naive": naive, "utc": naive - timedelta(hours=off),
                "all_day": False, "zone": tzid or "UTC", "exact": True}

    # Unknown zone, or none at all: do NOT guess an offset.
    return {"naive": naive, "utc": None, "all_day": False,
            "zone": tzid or "(floating - no timezone given)", "exact": False}


def unescape(v):
    return v.replace("\\n", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def events(text):
    found, cur = [], None
    for line in unfold(text):
        if line.startswith("BEGIN:VEVENT"):
            cur = {}
            continue
        if line.startswith("END:VEVENT"):
            if cur is not None:
                found.append(cur)
            cur = None
            continue
        if cur is None or ":" not in line:
            continue

        prop, value = line.split(":", 1)
        name, params = split_params(prop)

        if name in ("DTSTART", "DTEND"):
            cur[name] = parse_dt(value, params)
        elif name in ("SUMMARY", "LOCATION", "ORGANIZER", "STATUS", "RRULE"):
            cur[name] = unescape(value)
        elif name == "ATTENDEE":
            cur.setdefault("ATTENDEES", []).append(unescape(value))
    return found


def fmt_day(dt):
    # %-d / %-I are platform-specific; build them by hand for portability.
    return "%s, %s %d, %d" % (dt.strftime("%A"), dt.strftime("%B"), dt.day, dt.year)


def fmt_time(dt):
    hour = dt.hour % 12 or 12
    return "%d:%02d %s" % (hour, dt.minute, dt.strftime("%p"))


def describe(ev, index, total):
    if total > 1:
        print("\n--- event %d of %d ---" % (index, total))
    print("  %s" % ev.get("SUMMARY", "(no title)"))

    start, end = ev.get("DTSTART"), ev.get("DTEND")
    if not start:
        print("  ! no DTSTART - cannot determine the date")
        return

    if start["all_day"]:
        print("  DATE:     %s (all-day)" % fmt_day(start["naive"]))
    elif start["exact"] and start["utc"] is not None:
        utc = start["utc"]
        # Convert via the local offset that applies at that instant.
        local = utc + timedelta(hours=_localzone.offset_for_utc(utc))
        print("  DATE:     %s   <- day of week computed, not inferred" % fmt_day(local))
        line = "  TIME:     %s %s" % (fmt_time(local), local_label(local))
        if end and end.get("utc"):
            mins = int((end["utc"] - utc).total_seconds() // 60)
            line += " (%d min)" % mins
        print(line)
        # ⭐ Issue #34, part 4. This used to compare `start["zone"]` against a hardcoded list of
        # Pacific-zone spellings — under a docstring that already claimed the file no longer
        # hardcodes US Pacific (line 18 above), it still did, in the one place that decides
        # whether to show what the ORGANISER actually wrote. For a candidate whose resolved
        # zone (_localzone.py) is anything other than Pacific, this was true for EVERY invite,
        # so the "(invite states ...)" line NEVER printed and the organiser's own wording was
        # silently dropped. The question this line answers is not "is the invite's zone
        # Pacific" — it is "does the invite's stated wall-clock time differ from what was just
        # printed as the candidate's local time", which needs no zone-name comparison at all:
        # compare the two RENDERED values directly. Zone names arrive in wildly different
        # spellings (IANA, Windows/Outlook, the legacy US_ZONES keys) and comparing formatted
        # output sidesteps normalizing all of them.
        if (fmt_day(start["naive"]) != fmt_day(local)
                or fmt_time(start["naive"]) != fmt_time(local)):
            print("            (invite states %s %s %s)"
                  % (fmt_day(start["naive"]), fmt_time(start["naive"]), start["zone"]))
        days = (local.date() - datetime.now().date()).days
        when = ("TODAY" if days == 0 else "TOMORROW" if days == 1 else
                "in %d days" % days if days > 0 else "%d days AGO" % abs(days))
        print("  WHEN:     %s" % when)
    else:
        print("  DATE:     %s" % fmt_day(start["naive"]))
        print("  TIME:     %s  in zone %s" % (fmt_time(start["naive"]), start["zone"]))
        print("  ! NOT CONVERTED to %s - this zone isn't in the offset table, so the"
              % _localzone.display_name())
        print("    local time is UNKNOWN. Confirm against the calendar app before")
        print("    treating the time above as the candidate's local time.")

    for key, label in (("LOCATION", "LOCATION "), ("ORGANIZER", "ORGANIZER"), ("STATUS", "STATUS   ")):
        if ev.get(key):
            print("  %s %s" % (label, ev[key].replace("mailto:", "")))
    if ev.get("RRULE"):
        print("  REPEATS:  %s" % ev["RRULE"])
    if ev.get("ATTENDEES"):
        people = [a.split(":")[-1] for a in ev["ATTENDEES"]]
        shown = ", ".join(people[:5]) + (" (+%d more)" % (len(people) - 5) if len(people) > 5 else "")
        print("  ATTENDEES %s" % shown)


def main():
    ap = argparse.ArgumentParser(description="Decode a .ics invite into date/time facts.")
    ap.add_argument("source", help="path to .ics / base64 / email body, or '-' for stdin")
    args = ap.parse_args()

    try:
        text = load(args.source)
    except IOError:
        sys.stderr.write("error: cannot read %s\n" % args.source)
        return 2

    found = events(text)
    if not found:
        sys.stderr.write("error: no VEVENT found - is this actually a calendar invite?\n")
        sys.stderr.write("hint: if it came from Gmail, save the ATTACHMENT, not the message\n")
        sys.stderr.write("      body. The .ics is a separate base64-encoded part.\n")
        return 1

    print("Parsed %d event(s):" % len(found))
    for i, ev in enumerate(found, 1):
        describe(ev, i, len(found))
    return 0


if __name__ == "__main__":
    sys.exit(main())
