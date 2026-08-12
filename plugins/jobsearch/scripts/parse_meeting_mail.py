#!/usr/bin/env python3
"""Extract a meeting date/time from Gmail message TEXT, when the .ics is unreachable.

WHY THIS EXISTS (2026-07-20)
----------------------------
CLAUDE.md's hard rule says: never state a meeting time from an email body when a
`.ics` attachment exists -- save the attachment and run parse_ics.py.

That rule turned out to be UNEXECUTABLE. There is no Gmail attachment-download
tool in this stack: the Gmail MCP connector exposes search/get/draft/label only.
`get_message` returns an `attachmentId` and mimeType, but nothing fetches the
bytes. macOS Calendar (~/Library/Calendars) is TCC-blocked. So parse_ics.py --
written 2026-07-19 -- had never once been fed real data, and two meeting dates
(<an employer>, <a firm>) sat unknown for days because of it.

This script is the workaround that DOES work with the tools we have. Google
Calendar's own acceptance/invitation receipts render the date and time as plain
TEXT in the message body, and those receipts are machine-generated from the .ics
-- so they are evidence, not recall. Better still, Google's format carries an
EXACT numeric UTC offset in seconds, so the conversion to local time is arithmetic,
not guesswork.

    "the candidate M the candidate has accepted your invitation to the event: <an employer> Reconnect,
     scheduled for July 20, 2026 at 8:00 AM (US/Eastern (EDT) offset -14400 (Daylight))."

Feed this script that text and it prints the day of week computed from the date,
the original time, and the candidate's local equivalent.

    python3 scripts/parse_meeting_mail.py <file>
    python3 scripts/parse_meeting_mail.py -            # read stdin
    python3 scripts/parse_meeting_mail.py --text "..."

*** THE STALENESS TRAP -- READ THIS ***
An acceptance receipt proves only what was booked AT THE MOMENT IT WAS SENT. It
is NOT proof of the current schedule. On 2026-07-20 this exact mistake was made:
a 7/17 receipt reading "July 20 at 8:00 AM US/Eastern" was reported as the live
meeting time, when the candidate had since rescheduled the call out of band. The script
prints this warning on every run on purpose. Always confirm against a newer
invite, the calendar, or the candidate himself before acting on the output.

Targets system Python 3.8 (/usr/bin/python3): no zoneinfo, no third-party
packages, no walrus, no X | Y annotations.
"""

import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _localzone

# ⭐ The candidate's own zone is RESOLVED, never assumed — see _localzone.py.

# Fallback table for receipts that name a zone but give no numeric offset.
# Deliberately US-only and deliberately small -- an unknown zone must produce
# NOT CONVERTED, never a plausible-looking guess.
NAMED_OFFSETS = {
    "EDT": -4, "EST": -5, "CDT": -5, "CST": -6,
    "MDT": -6, "MST": -7, "PDT": -7, "PST": -8,
    "UTC": 0, "GMT": 0,
}

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}


def local_offset(naive_local):
    return _localzone.offset_for_local(naive_local)


def local_label(naive_local):
    return _localzone.label_for_local(naive_local)


# "July 20, 2026 at 8:00 AM" / "Jul 20, 2026 at 8:00AM"
DATE_TIME = re.compile(
    r"([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})\s*(?:at|,)?\s*"
    r"(\d{1,2}):(\d{2})\s*([APap][Mm])?",
)
# "(US/Eastern (EDT) offset -14400 (Daylight))" -- offset is in SECONDS
OFFSET_SECONDS = re.compile(r"offset\s+(-?\d+)")
# a bare zone abbreviation in parens, e.g. "(EDT)" or "8:00 AM PT"
ZONE_ABBR = re.compile(r"\b(EDT|EST|CDT|CST|MDT|MST|PDT|PST|UTC|GMT)\b")


def parse(text):
    """Return (naive_datetime, offset_hours_or_None, zone_label)."""
    m = DATE_TIME.search(text)
    if not m:
        return None, None, None

    mon_raw, day, year, hour, minute, ampm = m.groups()
    mon = MONTHS.get(mon_raw.lower()) or MONTHS.get(mon_raw.lower()[:3] + "")
    if mon is None:
        for name, num in MONTHS.items():
            if name.startswith(mon_raw.lower()[:3]):
                mon = num
                break
    if mon is None:
        return None, None, None

    hour = int(hour)
    if ampm:
        up = ampm.upper()
        if up == "PM" and hour != 12:
            hour += 12
        elif up == "AM" and hour == 12:
            hour = 0

    naive = datetime(int(year), mon, int(day), hour, int(minute))

    # Prefer the exact numeric offset when Google supplies it.
    tail = text[m.end():m.end() + 200]
    om = OFFSET_SECONDS.search(tail) or OFFSET_SECONDS.search(text)
    if om:
        secs = int(om.group(1))
        if secs % 3600 == 0:
            return naive, secs // 3600, "offset %+d" % (secs // 3600)
        return naive, secs / 3600.0, "offset %+.2fh" % (secs / 3600.0)

    zm = ZONE_ABBR.search(tail) or ZONE_ABBR.search(text)
    if zm:
        ab = zm.group(1)
        return naive, NAMED_OFFSETS[ab], ab

    return naive, None, None


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2

    if args[0] == "--text":
        raw = " ".join(args[1:])
    elif args[0] == "-":
        raw = sys.stdin.read()
    else:
        try:
            with open(args[0], "r", errors="replace") as fh:
                raw = fh.read()
        except IOError as exc:
            sys.stderr.write("cannot read %s: %s\n" % (args[0], exc))
            return 1

    naive, off, label = parse(raw)
    if naive is None:
        sys.stderr.write(
            "No date/time found.\n"
            "This script reads Google Calendar acceptance/invitation RECEIPTS,\n"
            "which spell the date out in text. A plain Zoom invite body usually\n"
            "carries NO date at all -- that lives only in the .ics attachment,\n"
            "which cannot currently be downloaded. In that case, ask the candidate or\n"
            "check the calendar; do not guess.\n"
        )
        return 1

    print("Meeting date/time extracted from message text")
    print("=" * 62)
    print("  Day of week : %s   <- computed from the date, not copied" %
          naive.strftime("%A"))
    print("  Date        : %s" % naive.strftime("%Y-%m-%d (%B %d, %Y)"))

    if off is None:
        print("  Time        : %s" % naive.strftime("%-I:%M %p"))
        print("  Zone        : UNKNOWN")
        print("")
        print("  *** NOT CONVERTED ***")
        print("  No numeric offset and no recognized US zone abbreviation.")
        print("  Reporting the time in its original zone. Do NOT guess the")
        print("  local equivalent -- confirm against the calendar.")
    else:
        utc = naive - timedelta(hours=off)
        local_naive = utc + timedelta(hours=_localzone.offset_for_utc(utc))
        print("  Time        : %s (%s)" % (naive.strftime("%-I:%M %p"), label))
        print("  Local       : %s %s on %s" % (
            local_naive.strftime("%-I:%M %p"),
            local_label(local_naive),
            local_naive.strftime("%A %Y-%m-%d"),
        ))
        if local_naive.date() != naive.date():
            print("  NOTE: the local conversion lands on a DIFFERENT DAY.")

    print("")
    print("  " + "!" * 58)
    print("  STALENESS WARNING -- this is not proof of the current schedule.")
    print("  A receipt records only what was booked when it was SENT. If the")
    print("  meeting was rescheduled afterwards (possibly out of band, by")
    print("  phone or from another mailbox), this value is obsolete. This")
    print("  exact error was made 2026-07-20. Confirm against a newer invite,")
    print("  the calendar, or the candidate before acting on it.")
    print("  " + "!" * 58)
    return 0


if __name__ == "__main__":
    sys.exit(main())
