#!/usr/bin/env python3
"""
Deterministic meeting-artifact sweep, cross-checked against focus.md's "This Week".

WHY THIS EXISTS
---------------
Every meeting miss in this repo's history came from the same shape of failure: a
calendar artifact arrived in the mailbox, a model-driven scan didn't surface it,
and `focus.md`'s This Week stayed stale until the candidate noticed. It happened with the
<an employer> call, the <a recruiter>/<a firm> call, and — worst — the **<an employer> round-2
interview booked for the NEXT MORNING**, which landed *inside* the 2PM scan's own
window and was reported as "no new recruiter/human contact."

The 2026-07-21 fix was a rule ("scan for meeting artifacts FIRST"), and rules of
that kind have already failed twice here: a numbered priority list in a brief reads
as permission to ignore everything else, and the agent obeys the list. This script
is the deterministic version of that rule — the same promotion `alert_sweep.py`
made for job alerts, applied to calendar artifacts:

    a daily, predictable artifact belongs in a QUERY, not a model summary.

WHAT IT DOES
------------
1. Sweeps EVERY configured mailbox for calendar/meeting artifacts (invitations, acceptances,
   updates, cancellations, .ics attachments, and the booking-tool wording).
2. Extracts every date-looking token from each subject.
3. Diffs those against the dates already named in focus.md's `## 📅 This Week`.
4. Prints anything found in the mail but ABSENT from This Week as a loud
   ⚠️ UNRECONCILED block.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It does NOT decide the meeting time. Subject lines lie — CLAUDE.md's hard rule is
that a thread's subject can be weeks stale while its newest message schedules
something tomorrow ("Re: Appointment booked: ... @ Wed Jul 15" carried the 7/22
booking). So this reports ARTIFACTS TO GO READ, and the authoritative time still
comes from `gmail_get_attachment` + `parse_ics.py`. A clean run here means
"nothing unreconciled," never "the schedule is correct."

Same coverage guarantee as the Gmail server it reuses: every configured account by default,
and an unreachable account is a LOUD banner and a non-zero exit, never a silent zero.

Usage:
    python3 scripts/meeting_check.py               # last 7 days (the run default)
    python3 scripts/meeting_check.py --days 14
    python3 scripts/meeting_check.py --today 2026-08-02

Python 3.9+. Standard library only.
"""

import argparse
import datetime
import os
import re
import sys

import os, sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root as _profile_root
import profile as _profile

try:
    from gmail_mcp_server import (
        Mailbox, configured_accounts, decode_header_value, CredentialError,
        body_text,
    )
except ImportError as exc:  # pragma: no cover - defensive
    sys.stderr.write(
        "Could not import gmail_mcp_server from the scripts/ dir: %s\n"
        "Run this as `python3 scripts/meeting_check.py` from the repo root.\n" % exc)
    sys.exit(2)

ROOT = _profile_root()
FOCUS = os.path.join(ROOT, "focus.md")

# Artifact-shaped, not person-shaped. The 2026-07-20 lesson: when you want a meeting
# time, search for the SHAPE OF THE RECORD, not for the person — the <a recruiter>/<a firm>
# time sat in a sent acceptance receipt while it was being reported as unknowable.
MEETING_QUERY = (
    'subject:(Invitation OR "Updated invitation" OR "Invitation from" '
    'OR "Appointment booked" OR "Event accepted" OR "Accepted:" OR "Declined:" '
    'OR "Canceled event" OR "Cancelled event" OR "has been scheduled" '
    'OR "is scheduled" OR "Reschedule" OR "Rescheduled" OR "confirmed your meeting" '
    'OR "booked a meeting" OR "interview" OR "meeting request") '
    'OR filename:ics OR from:calendar-notification@google.com '
    'OR from:calendly.com OR from:x.ai OR from:goodtime.io OR from:hubspot'
)

MONTHS = ("january february march april may june july august september "
          "october november december").split()

# Dates as they appear in real subject lines: "Wed Jul 15", "July 22, 2026",
# "2026-07-22", "7/22", "22 Jul".
DATE_PATTERNS = [
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
    re.compile(r"\b(%s)[a-z]*\.?\s+(\d{1,2})\b" % "|".join(m[:3] for m in MONTHS), re.I),
    re.compile(r"\b(\d{1,2})\s+(%s)[a-z]*\b" % "|".join(m[:3] for m in MONTHS), re.I),
    re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b"),
]


def norm_dates(text, default_year):
    """Every date-looking token in `text`, as ISO strings. Best-effort by design."""
    if not text:
        return set()
    found = set()
    m3 = {m[:3]: i + 1 for i, m in enumerate(MONTHS)}

    for mo in DATE_PATTERNS[0].finditer(text):
        found.add("%s-%s-%s" % mo.groups())
    for mo in DATE_PATTERNS[1].finditer(text):
        mon, day = mo.group(1).lower()[:3], int(mo.group(2))
        if mon in m3:
            found.add("%04d-%02d-%02d" % (default_year, m3[mon], day))
    for mo in DATE_PATTERNS[2].finditer(text):
        day, mon = int(mo.group(1)), mo.group(2).lower()[:3]
        if mon in m3:
            found.add("%04d-%02d-%02d" % (default_year, m3[mon], day))
    for mo in DATE_PATTERNS[3].finditer(text):
        a, b, y = mo.group(1), mo.group(2), mo.group(3)
        try:
            month, day = int(a), int(b)
            if not (1 <= month <= 12 and 1 <= day <= 31):
                continue
            year = default_year
            if y:
                year = int(y) if len(y) == 4 else 2000 + int(y)
            found.add("%04d-%02d-%02d" % (year, month, day))
        except ValueError:
            continue
    return found


# ⭐⭐ THE DATE-COLLISION BLIND SPOT — fixed 2026-08-03.
#
# Reconciliation used to be `future & known`: a set of dates from the mail against a set of dates
# from This Week. **So the SECOND meeting on an already-listed date was invisible.** On 2026-08-03
# Aldric Fenwick's invite booked Larkbridge Technology (synthesized names) for Wed 08/05 9:00 AM
# *during* the run. 08/05 was already in This Week for the Halloway Partners intro, so
# `future & known` was non-empty and this script
# said "reconciled". It was caught only because the run pulled the .ics anyway per the hard rule.
#
# **A date is not an identity.** Two calls can land on one morning — these two did, back-to-back
# at 8:00 and 9:00. So a match now needs the DATE *and* a shared counterparty token, and a date
# that matches with no shared token is reported as a COLLISION, which is louder than "new",
# because it is the case a human is most likely to wave through.

def _candidate_name_tokens():
    """This candidate's own name, as lowercase word tokens.

    It appears in nearly every invite or acceptance receipt they're party to (sender,
    recipient, signature), so it can never identify a COUNTERPARTY. A previous version of
    this set hardcoded one specific candidate's own first and last name as literal words —
    correct for exactly one installation and silently wrong (every invite would look like a
    collision with itself) for any other candidate's profile. Read from user.json instead,
    like every other per-candidate fact in this engine.
    """
    try:
        ident = _profile.user()["identity"]
    except (OSError, KeyError):
        return set()
    name = ident.get("full_name") or ident.get("display_name") or ""
    return {w.lower() for w in re.findall(r"[A-Za-z']+", name)}


# Words that appear in nearly every invite or This Week line and so cannot identify anyone.
STOP = {
    "meeting", "invitation", "invite", "invited", "call", "event", "accepted",
    "updated", "appointment", "booked", "calendar", "google", "zoom", "teams", "meet", "with",
    "your", "you", "the", "and", "for", "from", "this", "that", "week", "time", "date", "schedule",
    "scheduled", "confirmed", "discussion", "sync", "synch", "intro", "introduction", "chat",
    "conversation", "am", "pm", "pdt", "pst", "edt", "est", "utc", "min", "mins", "minutes",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december", "re", "fwd", "no", "subject", "hiring", "role", "https",
    "com", "www", "http", "gmail", "email", "phone", "http", "link", "join", "video", "here",
} | _candidate_name_tokens()


def tokens(text):
    """Identity-bearing words: who this meeting is with. Names, companies, products."""
    return {w for w in re.findall(r"[A-Za-z][A-Za-z0-9'\-]{2,}", (text or "").lower())
            if w not in STOP and len(w) > 2}


def this_week_entries(default_year):
    """Return (all_dates, per_line, section).

    `per_line` is [(dates_on_that_line, identity_tokens)] — the unit that makes a date+who match
    possible. A whole-section token bag would NOT work: it would let the Halloway Partners line's
    tokens vouch for the Larkbridge Technology invite simply because both sit under This Week.
    """
    if not os.path.exists(FOCUS):
        return set(), [], ""
    text = open(FOCUS, encoding="utf-8").read()
    start = text.find("## 📅 This Week")
    if start < 0:
        return set(), [], ""
    nxt = text.find("\n## ", start + 5)
    section = text[start:nxt if nxt > 0 else len(text)]
    per_line = []
    for line in section.splitlines():
        d = norm_dates(line, default_year)
        if d:
            per_line.append((d, tokens(line)))
    return norm_dates(section, default_year), per_line, section


def classify(future, art_tokens, per_line):
    """new | collision | matched — and the This Week lines involved.

    **collision** is the case this function exists for: the date is already spoken for by a
    DIFFERENT counterparty, which the old date-only check reported as reconciled.
    """
    on_date = [(d, t) for (d, t) in per_line if d & future]
    if not on_date:
        return "new", []
    for d, t in on_date:
        if t & art_tokens:
            return "matched", []
    return "collision", on_date


def sweep(account, query):
    """rows = (received, from, subject, body, has_ics). The BODY matters: Google's acceptance
    receipts put the date ONLY in the body ("scheduled for July 31, 2026 at 8:00 AM
    (US/Eastern (EDT) offset -14400)") and leave it out of the subject entirely.
    Subject-only parsing missed exactly that artifact on the first real run of this
    script (2026-08-02, a real interview acceptance), which is the whole failure mode
    this is meant to catch."""
    rows = []
    try:
        with Mailbox(account) as mb:
            uids = mb.search(query)
            for uid in reversed(uids[-40:]):
                msg = mb.fetch_full(uid)
                if msg is None:
                    continue
                try:
                    body = body_text(msg, limit=4000) or ""
                except Exception:
                    body = ""
                # ⭐ Does it CARRY a calendar object? Added 2026-08-03. The Larkbridge invite has no
                # parseable date in the subject OR the body — the time exists only inside the
                # .ics. Without this flag such an artifact is listed and then silently skipped,
                # and the script reports "nothing unreconciled" for a meeting it could not read.
                has_ics = False
                try:
                    for part in msg.walk():
                        ctype = (part.get_content_type() or "").lower()
                        fname = (part.get_filename() or "").lower()
                        if ctype == "text/calendar" or fname.endswith(".ics"):
                            has_ics = True
                            break
                except Exception:
                    pass
                rows.append((
                    decode_header_value(msg.get("Date")),
                    decode_header_value(msg.get("From")),
                    decode_header_value(msg.get("Subject")),
                    body,
                    has_ics,
                ))
        return rows, None
    except CredentialError as exc:
        return [], str(exc)
    except Exception as exc:
        return [], "%s: %s" % (type(exc).__name__, exc)


def main():
    ap = argparse.ArgumentParser(description="Meeting-artifact sweep vs focus.md This Week.")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--today", metavar="YYYY-MM-DD")
    ap.add_argument("--account", default=None)
    args = ap.parse_args()

    try:
        today = (datetime.date.fromisoformat(args.today) if args.today
                 else datetime.date.today())
    except ValueError:
        sys.stderr.write("--today must be YYYY-MM-DD\n")
        return 2

    accounts = [args.account] if args.account else configured_accounts()
    query = "(%s) newer_than:%dd" % (MEETING_QUERY, args.days)
    known, per_line, section = this_week_entries(today.year)

    print("Meeting-artifact check — window: last %d day(s), as of %s"
          % (args.days, today.isoformat()))
    print("=" * 72)
    if not section:
        print("!! focus.md has no '## 📅 This Week' section — nothing to reconcile against.")
    else:
        print("This Week currently names %d date(s): %s"
              % (len(known), ", ".join(sorted(known)) or "(none)"))
    if args.account:
        print("!! NARROWED to one account (%s) — NOT every configured mailbox." % args.account)

    incomplete, unreconciled, collisions, undated, seen = [], [], [], [], 0
    for account in accounts:
        rows, err = sweep(account, query)
        print("\n[%s]" % account)
        if err:
            incomplete.append(account)
            print("  !! INCOMPLETE COVERAGE — %s" % err)
            print("  Results MISSING, not empty. Do not conclude a meeting does not exist.")
            continue
        if not rows:
            print("  (no meeting artifacts in window)")
            continue
        seen += len(rows)
        for date, frm, subj, body, has_ics in rows:
            # Subject AND body — see sweep()'s docstring for why body is not optional.
            cand = norm_dates(subj, today.year) | norm_dates(body, today.year)
            future = {d for d in cand if d >= today.isoformat()}
            flag = "  "
            if not future and has_ics:
                # ⭐ CARRIES AN .ics BUT NO READABLE DATE — an UNKNOWN, never a pass.
                flag = "❓"
                undated.append((account, date, frm, subj))
            elif future:
                # date AND who — see classify(). A date alone is not an identity.
                status, clash = classify(future, tokens(subj) | tokens(frm), per_line)
                if status == "new":
                    flag = "⚠️"
                    unreconciled.append((account, date, frm, subj, sorted(future)))
                elif status == "collision":
                    flag = "‼️"
                    collisions.append((account, date, frm, subj, sorted(future), clash))
            print("  %s %-28s | %s" % (flag, (date or "?")[:28], (subj or "(no subject)")[:78]))

    print("\n" + "=" * 72)
    if collisions:
        print("‼️  %d DATE COLLISION(S) — the date IS in This Week, but for SOMEONE ELSE."
              % len(collisions))
        print("   This is the case the old date-only check waved through: on 2026-08-03 the")
        print("   Larkbridge invite for 08/05 was absorbed by the Halloway Partners entry already on")
        print("   08/05. They were two different calls, back-to-back at 8:00 and 9:00.")
        for account, date, frm, subj, dates, clash in collisions:
            print("\n  - [%s] %s" % (account, subj))
            print("      from %s | received %s" % (frm, date))
            print("      date(s): %s" % ", ".join(dates))
            for d, tk in clash:
                print("      This Week already has %s for: %s"
                      % (", ".join(sorted(d)), ", ".join(sorted(tk)[:6]) or "(unnamed)"))
        print("\n   CONFIRM VIA THE .ics — do not assume it is the meeting already listed.")
        print("=" * 72)
    if undated:
        print("❓  %d ARTIFACT(S) CARRY AN .ics BUT NO READABLE DATE — PULL THE ATTACHMENT."
              % len(undated))
        print("   Found 2026-08-03: the Larkbridge invite ('the candidate & Larkbridge Technology') has")
        print("   NO date in its subject or body — the time exists only inside the .ics. Before")
        print("   this check it was listed and then silently skipped, and the script printed")
        print("   'no unreconciled future dates' for a meeting it could not read.")
        print("   **AN UNREADABLE DATE IS AN UNKNOWN, NEVER A PASS.**")
        for account, date, frm, subj in undated:
            print("\n  - [%s] %s" % (account, subj))
            print("      from %s | received %s" % (frm, date))
        print("\n   Resolve with: gmail_get_attachment -> scripts/parse_ics.py")
        print("   ⭐ TWO ARTIFACTS WITH THE SAME iCalendar UID ARE ONE MEETING, REVISED —")
        print("      compare SEQUENCE, and the HIGHEST wins. Proven 2026-08-03: Fenwick sent")
        print("      SEQUENCE:0 for Mon 08/03 9:00 AM and SEQUENCE:1 for Wed 08/05 9:00 AM six")
        print("      seconds later, same UID. Read as two meetings it looks like the candidate missed a")
        print("      call this morning; read as a revision it is simply a corrected booking.")
        print("=" * 72)
    if unreconciled:
        print("⚠️  %d ARTIFACT(S) NAME A FUTURE DATE NOT IN 'This Week' — GO READ THESE:"
              % len(unreconciled))
        for account, date, frm, subj, dates in unreconciled:
            print("  - [%s] %s" % (account, subj))
            print("      from %s | received %s" % (frm, date))
            print("      date(s) in subject: %s" % ", ".join(dates))
        print("\n  A SUBJECT LINE IS NOT AUTHORITATIVE — it can be weeks stale while the")
        print("  newest message in the thread schedules something tomorrow. Confirm the real")
        print("  time via gmail_get_attachment + scripts/parse_ics.py before writing it down.")
    elif seen and not undated:
        print("No unreconciled future dates: every artifact matches This Week on DATE AND WHO.")
        print("(Still not proof the schedule is right — reschedules happen out of band.)")
    elif seen:
        # NEVER pair an all-clear with unresolved artifacts. Printing "every artifact matches"
        # directly beneath five whose dates could not be read is the contradictory reassurance
        # this script exists to eliminate.
        print("NOT AN ALL-CLEAR — %d artifact(s) above have no readable date. Resolve those"
              % len(undated))
        print("before treating This Week as reconciled.")
    else:
        print("No meeting artifacts in the window.")

    if seen and (collisions or undated):
        # `seen` counts artifacts actually swept. Saying "no artifacts" after listing five of
        # them is the kind of contradictory all-clear this whole script exists to prevent.
        print("\n%d artifact(s) swept: %d collision(s), %d unreadable date(s), %d new."
              % (seen, len(collisions), len(undated), len(unreconciled)))

    if incomplete:
        print("\n!! %d account(s) could not be searched: %s" % (len(incomplete), ", ".join(incomplete)))
        print("   This run did NOT achieve full coverage.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
