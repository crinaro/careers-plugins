#!/usr/bin/env python3
"""
A compact one-line-per-role index of the pipeline — the cheap answer to
"is this role already tracked?"

WHY THIS EXISTS
---------------
`inbox-scan` runs on haiku with a ~650-word brief, and its job is to decide whether a role
in an alert is new. To do that it was being pointed at `data/opportunities.jsonl` — 161 full
records with research logs, outreach arrays and application history — which is the single
largest unnecessary read in the daily flow. The question is "have we seen this?", and that
is answerable from one line per record.

Same principle as `alert_sweep.py`: deterministic work belongs in a query, not in a model
summary (CLAUDE.md token discipline).

Usage:
    python3 scripts/pipeline_index.py                # active roles (the default view)
    python3 scripts/pipeline_index.py --all          # every record, including passed
    python3 scripts/pipeline_index.py --excluded     # ONLY the exclusion list
    python3 scripts/pipeline_index.py --company acme # filter by company id substring
    python3 scripts/pipeline_index.py --contacts     # include contact names

THE EXCLUSION LIST is `verdict: pass` OR `status: passed`. That definition lives here and in
`docs/schema.md`; agents should call this script rather than re-deriving it, because an agent
that invents its own definition of "already excluded" will re-surface roles the candidate has declined.

⭐ `status: expired` (issue #6) is terminal but is NOT on the exclusion list. Excluded means
the candidate DECLINED — never resurface. Expired means the posting vanished before any decision
was made, so a NEW sighting of an expired role is a repost and must surface as a fresh signal,
not be auto-dropped as "already ruled out". Expired rows are hidden from the default (active)
view — they are not live work — and counted separately in the footer.

Python 3.9+. Standard library only.
"""

import argparse
import json
import os
import sys

import os, sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root as _profile_root

ROOT = _profile_root()
DATA = os.path.join(ROOT, "data")


def load(name):
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def is_excluded(o):
    """The one definition of 'already ruled out'. Do not re-derive this elsewhere.

    Deliberately does NOT include `status: expired` — see the module docstring: a re-sighting
    of an expired role is a repost, and treating it as 'already ruled out' would silently drop
    the second chance the candidate never declined."""
    return o.get("verdict") == "pass" or o.get("status") == "passed"


def is_expired(o):
    """Terminal without a decision: the posting vanished before the candidate ruled on it."""
    return o.get("status") == "expired"


def main():
    ap = argparse.ArgumentParser(description="Compact pipeline index.")
    ap.add_argument("--all", action="store_true", help="Include excluded/closed records.")
    ap.add_argument("--excluded", action="store_true", help="Show ONLY the exclusion list.")
    ap.add_argument("--company", metavar="SUBSTR", help="Filter by company_id substring.")
    ap.add_argument("--contacts", action="store_true", help="Append contact names.")
    ap.add_argument("--person", metavar="NAME",
                    help="Everything known about one person, across every opportunity.")
    args = ap.parse_args()

    opps = load("opportunities.jsonl")
    companies = {c["id"]: c.get("name", c["id"]) for c in load("companies.jsonl")}

    if args.person:
        # "What is the whole history with this person?" — the question that was
        # unanswerable until outreach[] gained a contact_id joining it to contacts[]
        # (2026-08-02). Searches across ALL opportunities, because a recruiter or a warm
        # contact often spans several.
        needle = args.person.lower()
        found = False
        for o in opps:
            for c in (o.get("contacts") or []):
                if needle not in (c.get("name") or "").lower():
                    continue
                found = True
                print("%s — %s" % (c.get("name"), companies.get(o.get("company_id"), o.get("company_id"))))
                print("  role     : %s" % (c.get("role") or c.get("path_type") or "?"))
                print("  email    : %s" % (c.get("email") or "— none on record"))
                print("  linkedin : %s" % (c.get("linkedin") or "—"))
                print("  opp      : %s  [%s / %s]" % (o["id"], o.get("status"), o.get("stage")))
                touches = [r for r in (o.get("outreach") or [])
                           if r.get("contact_id") == c.get("contact_id")]
                if not touches:
                    print("  touches  : none recorded")
                for r in sorted(touches, key=lambda x: x.get("date") or ""):
                    print("    %s  %-14s %-24s %s" % (r.get("date"), r.get("outcome"),
                                                     r.get("medium"), r.get("touch_type")))
                    if r.get("responded_on"):
                        print("               replied %s" % r["responded_on"])
                if c.get("notes"):
                    print("  notes    : %s" % c["notes"][:220])
                print()
        if not found:
            print("No contact matching %r. Names come from contacts[] across all opportunities."
                  % args.person)
            return 1
        return 0

    rows = opps
    if args.excluded:
        rows = [o for o in rows if is_excluded(o)]
    elif not args.all:
        rows = [o for o in rows if not is_excluded(o) and not is_expired(o)]
    if args.company:
        rows = [o for o in rows if args.company.lower() in (o.get("company_id") or "").lower()]

    if args.excluded:
        header = "EXCLUSION LIST — already ruled out (verdict: pass OR status: passed)"
    elif args.all:
        header = "FULL PIPELINE"
    else:
        header = "ACTIVE PIPELINE — excluded records hidden (use --all or --excluded)"
    print("%s — %d of %d record(s)" % (header, len(rows), len(opps)))
    print("=" * 130)
    # `play` added with dev #95's follow-on: a session asking "is this tracked?" could not
    # see the post-application play position, so the field existed and no reader met it.
    print("  %-42s | %-20s | %-15s | %-11s | %-23s | %-5s | %s"
          % ("title", "company", "status", "stage", "play", "A=app T=touch", "verdict"))
    print("-" * 130)

    for o in sorted(rows, key=lambda x: ((x.get("company_id") or ""), x.get("id"))):
        # ⭐ ACTIVITY COLUMN — added 2026-08-03. `stage` is a MODEL of where a role is; this is
        # the RECEIPT of what was actually sent. On 2026-08-03 I read `research_log: 0` on three
        # records and reported them to the candidate as "never researched, sitting unexamined" — while
        # each had an application filed 07/31 WITH a cover letter and four outreach touches the
        # same day. The candidate corrected me. An empty research_log is not an unworked role, and `stage:
        # contacted` did not disambiguate it. **Never infer that nothing was done from ONE array.**
        napp = len(o.get("applications") or [])
        nout = len([r for r in (o.get("outreach") or []) if r.get("status") == "sent"])
        act = ("A%d" % napp if napp else "  ") + " " + ("T%d" % nout if nout else "  ")
        line = "%-42s | %-20s | %-15s | %-11s | %-23s | %-5s | %s" % (
            (o.get("title") or "?")[:42],
            (companies.get(o.get("company_id"), o.get("company_id") or "?"))[:20],
            (o.get("status") or "?")[:15],
            (o.get("stage") or "?")[:11],
            (o.get("play_stage") or "—")[:23],
            act,
            (o.get("verdict") or "?"),
        )
        print("  " + line)
        if args.contacts:
            names = [c.get("name") for c in (o.get("contacts") or []) if c.get("name")]
            reached = [r.get("to") for r in (o.get("outreach") or []) if r.get("to")]
            if names or reached:
                print("      contacts: %s" % (", ".join(names) or "none"))
                if reached:
                    print("      contacted: %s" % ", ".join(reached))

    if not args.excluded and not args.all:
        n_excl = sum(1 for o in opps if is_excluded(o))
        print("\n  (%d excluded record(s) hidden — see --excluded before treating a role as new)"
              % n_excl)
        n_exp = sum(1 for o in opps if is_expired(o) and not is_excluded(o))
        if n_exp:
            print("  (%d expired record(s) hidden — terminal but NEVER declined; a re-sighting "
                  "is a repost and should surface)" % n_exp)
    # The migration marker must stay visible from every view of this index — an `unresolved`
    # play position nobody surfaces looks handled and is not (dev #95 follow-on).
    n_play_unres = sum(1 for o in opps if o.get("play_stage") == "unresolved"
                       and o.get("status") not in ("passed", "expired"))
    if n_play_unres:
        print("  ⚠️ %d role(s) carry play_stage 'unresolved' — the migration marker, not a "
              "position; set the real value: record.py set <id> play_stage <stage>"
              % n_play_unres)
    return 0


if __name__ == "__main__":
    sys.exit(main())
