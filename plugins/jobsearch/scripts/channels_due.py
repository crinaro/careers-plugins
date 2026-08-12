#!/usr/bin/env python3
"""The sourcing queue: which channels are due for review, from data/channels.jsonl.

Turns sourcing from "remember to check Dice" into a data-driven loop. Run at the
start of every daily/weekly run: it lists the active-search channels that are due
(cadence + last_reviewed), the recruiter relationships that have gone quiet, and
after reviewing a channel you stamp its last_reviewed so it drops off the queue.

    python3 scripts/channels_due.py                 # what's due today
    python3 scripts/channels_due.py --stamp <id>    # mark <id> reviewed today (--date YYYY-MM-DD to override)

IMPORTANT — what "review a channel" means (the 2026-07-20 clarification):
  Active-search channels are reviewed by DIRECT SEARCH on the source's own job
  pages (LinkedIn Jobs, Dice, Indeed, BuiltIn) plus organizations' own career
  pages/ATS — NOT by reading the source's algorithmic ALERT EMAILS, which are
  noise. Recruiters are reviewed on-inbound (a human reached out) and by not
  letting an open thread go silent.

Date handling: pass --today YYYY-MM-DD (the runtime forbids Date.now-style calls
being assumed; scripts should be handed the date). Defaults to the system date.

Targets Python 3.9+, stdlib only.
"""

import argparse
import datetime
import json
import os

import os, sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root as _profile_root
from _atomic import write_jsonl, write_json

ROOT = _profile_root()
DATA = os.path.join(ROOT, "data")
CH = os.path.join(DATA, "channels.jsonl")

CADENCE_DAYS = {"daily": 1, "weekly": 7, "biweekly": 14, "monthly": 30}
# "monthly" added 2026-08-02 with the anchor-employer cadence cut. Without it the
# lookup missed and `if not d: continue` dropped the channel from the queue SILENTLY —
# a cadence change would have read as full coverage while nothing was ever swept.
QUIET_RECRUITER_DAYS = 10  # a recruiter relationship untouched this long is worth a nudge


def load():
    return [json.loads(l) for l in open(CH, encoding="utf-8") if l.strip()]


def load_opps():
    """Live pipeline — the other place a real channel touch gets recorded."""
    path = os.path.join(DATA, "opportunities.jsonl")
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def save(chans):
    # Atomic: this runs unattended and rewrites the whole channel store.
    write_jsonl(CH, chans)


def as_date(s):
    try:
        return datetime.date.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stamp", metavar="ID")
    ap.add_argument("--date", metavar="YYYY-MM-DD")
    ap.add_argument("--today", metavar="YYYY-MM-DD")
    ap.add_argument("--yield-since", metavar="YYYY-MM-DD",
                    help="report sightings per channel since this date (trial-yield decision)")
    args = ap.parse_args()

    chans = load()

    if args.stamp:
        stamp = args.date or (args.today or datetime.date.today().isoformat())
        hit = False
        for c in chans:
            if c["id"] == args.stamp:
                c["last_reviewed"] = stamp
                hit = True
        if not hit:
            print("No channel with id %r" % args.stamp)
            return 1
        save(chans)
        print("Stamped %s reviewed on %s." % (args.stamp, stamp))
        return 0

    if args.yield_since:
        import json as _j, os as _os
        since = as_date(args.yield_since)
        opps = [_j.loads(l) for l in open(_os.path.join(DATA, "opportunities.jsonl"), encoding="utf-8") if l.strip()]
        labels = {c["id"]: c.get("label", c["id"]) for c in chans}
        counts, pursued = {}, {}
        for o in opps:
            for sg in o.get("sightings", []):
                sd = as_date(sg.get("seen_on"))
                if sd and since and sd >= since:
                    cid = sg["channel_id"]
                    counts[cid] = counts.get(cid, 0) + 1
                    if o.get("verdict") == "pursue" or o.get("status") in ("active-pursuit", "needs-resolution"):
                        pursued[cid] = pursued.get(cid, 0) + 1
        print("Channel yield since %s — new sightings / of-which-pursued" % args.yield_since)
        print("=" * 60)
        if not counts:
            print("  No new sightings recorded in the window yet.")
        for cid, n in sorted(counts.items(), key=lambda x: -x[1]):
            print("  %-28s %d new / %d pursued" % (labels.get(cid, cid)[:28], n, pursued.get(cid, 0)))
        print("\n  Decision rule (Dice/Indeed/CareerBuilder trial, ~2026-08-03): a bot-limited")
        print("  source earning 0 pursued that direct LinkedIn/ATS + recruiters didn't already")
        print("  surface is not worth the friction — drop it.")
        return 0

    today = as_date(args.today) or datetime.date.today()
    print("Channel review queue — as of %s\n" % today.isoformat())

    due, upcoming = [], []
    retired = []
    for c in chans:
        if c.get("type") in ("recruiter", "referral"):
            continue
        # A retired channel stays in the file (its sighting history must keep resolving for
        # validate_data.py and funnel_report.py) but must never be queued for review again.
        # Added 2026-08-02 with the Dice/CareerBuilder retirement — without this the status
        # field was decorative and the dead sources kept appearing in the sourcing queue.
        if c.get("relationship_status") == "retired":
            retired.append(c)
            continue
        cad = c.get("review_cadence")
        d = CADENCE_DAYS.get(cad)
        if not d:
            # Never drop a channel silently just because its cadence is unrecognized —
            # that reads as coverage. on-inbound has no schedule by design; anything
            # else is a real misconfiguration and must be visible. (2026-08-02)
            if cad != "on-inbound":
                print("  !! WARNING: channel %r has unrecognized review_cadence %r — "
                      "NOT being scheduled. Add it to CADENCE_DAYS." % (c["id"], cad))
            continue
        lr = as_date(c.get("last_reviewed"))
        if lr is None:
            due.append((c, "never reviewed"))
            continue
        due_date = lr + datetime.timedelta(days=d)
        if due_date <= today:
            days_over = (today - due_date).days
            due.append((c, "due %s%s" % (due_date.isoformat(),
                        " (%d days over)" % days_over if days_over else "")))
        else:
            upcoming.append((c, due_date))

    print("=" * 68)
    print("DUE NOW — review by DIRECT SEARCH on the source, then --stamp it")
    print("=" * 68)
    if due:
        for c, why in sorted(due, key=lambda x: x[0].get("last_reviewed") or ""):
            acc = c.get("access", "?")
            print("  ● %-24s %-9s [%s] %s" % (c["label"][:24], c["review_cadence"], acc, why))
            if c.get("scope_notes"):
                print("      %s" % c["scope_notes"][:80])
    else:
        print("  Nothing due. All active-search channels are current.")

    if upcoming:
        print("\nUPCOMING:")
        for c, dd in sorted(upcoming, key=lambda x: x[1]):
            print("  ○ %-24s [%s] next %s" % (c["label"][:24], c.get("access","?"), dd.isoformat()))

    # Named explicitly rather than silently dropped — a source that vanishes without a trace
    # reads as "we still cover it" to the next run (CLAUDE.md's no-silent-caps principle).
    if retired:
        print("\nRETIRED — deliberately no longer swept:")
        for c in retired:
            print("  ⛔ %-24s %s" % (c["label"][:24], (c.get("scope_notes") or "")[:70]))

    # Recruiter relationships gone quiet (soft signal — these are on-inbound, but a
    # silent open thread is worth a nudge; complements check_followups on opportunities).
    #
    # ⭐ 2026-08-02: this used to read ONLY channels.jsonl (log[] + last_reviewed), so a
    # real touch recorded as an OUTREACH ROW on an opportunity was invisible and the firm
    # looked silent. That produced a partly-false "15 firms gone quiet" list at the weekly
    # review — Ashford Search showed 12 days while the <an employer> chase to Calloway had gone out 7/31.
    # A nudge decision made off that list would have been wrong, so the live pipeline is
    # now folded in as a touch source.
    outreach_touch = {}   # channel_id -> most recent outreach date
    for o in load_opps():
        for r in o.get("outreach", []) or []:
            d = as_date(r.get("date"))
            cid = r.get("channel_id")
            if d and cid and (cid not in outreach_touch or d > outreach_touch[cid]):
                outreach_touch[cid] = d

    quiet = []
    for c in chans:
        if c.get("type") != "recruiter":
            continue
        touches = [as_date(e.get("date")) for e in c.get("log", [])]
        touches += [as_date(c.get("last_reviewed"))]
        if c["id"] in outreach_touch:
            touches.append(outreach_touch[c["id"]])
        touches = [t for t in touches if t]
        if touches and (today - max(touches)).days >= QUIET_RECRUITER_DAYS:
            quiet.append((c, (today - max(touches)).days))
    if quiet:
        print("\nRECRUITER RELATIONSHIPS GONE QUIET (%d+ days) — consider a nudge:" % QUIET_RECRUITER_DAYS)
        for c, n in sorted(quiet, key=lambda x: -x[1]):
            print("  ~ %-24s %d days since last touch" % (c["label"][:24], n))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
