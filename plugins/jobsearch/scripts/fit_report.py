#!/usr/bin/env python3
"""
The JD-fit register: how the candidate matches each role, and the OPEN GAPS worth asking about.

WHY THIS EXISTS
---------------
Added 2026-08-02, per the candidate:

    "How is the candidate match to the JD? How would you present the candidate is a fit and
     what are items that don't align? If you do this you get alignment on the marketing for
     alignment and also fill in gaps. I personally don't have everything on my resume and it
     helps build context for what the candidate has done so the knowledge base for the
     candidate builds to improve communication for future job opportunities, improving their
     resume or any other communication."

Two outputs, and the second is the durable one:

1. **Marketing alignment** — the matched requirements and their `pitch_line`s, so outreach and
   the cover letter are built from a stated fit case instead of re-inventing positioning per
   draft.
2. **Gap harvesting** — every requirement nothing on file corroborates becomes a targeted
   question. His answer is filed into a store that already exists (`projects.md`,
   `resume.md`'s "Additional Detail" addenda, or `kb_<company>.md`), so the NEXT role starts
   from a fuller picture. His resume is deliberately incomplete; this is how the missing
   context gets captured while a live role makes it concrete.

**The counts here are COMPUTED every run, never stored.** Storing a summary is exactly how
`funnel_report.py` came to print a hardcoded "stage never advances past contacted" claim that
was true when written and false when read.

Usage:
    python3 scripts/fit_report.py                 # register + open gaps
    python3 scripts/fit_report.py --gaps          # ONLY the open questions (for Your Move)
    python3 scripts/fit_report.py --role <opp_id> # one role's full fit case
    python3 scripts/fit_report.py --pitch <opp_id># just the pitch lines, for drafting

Python 3.9+. Standard library only.
"""

import argparse
import json
import os
import sys

import os, sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root as _profile_root
# ⭐ THE ONE DEFINITION OF "already ruled out" — pipeline_index.is_excluded, whose own
# docstring says "Do not re-derive this elsewhere." This report did not apply it at all
# (GitHub #4), so a question on a passed/closed role still counted as open and could date
# itself into the overdue set. An audit must honour the pipeline's own exclusion predicate,
# or it reports work on roles the candidate already declined.
from pipeline_index import is_excluded as _is_excluded

ROOT = _profile_root()
DATA = os.path.join(ROOT, "data")

# Where a harvested answer is allowed to land. Deliberately a CLOSED list of stores that
# already exist — a fourth knowledge store would fragment the thing this is meant to build.
STORES = ("projects.md", "resume.md-addendum", "resume.md", "kb_")


def load(name):
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def rule(title):
    print("\n" + "=" * 76)
    print(title)
    print("=" * 76)


def counts(reqs):
    out = {}
    for q in reqs:
        out[q.get("verdict")] = out.get(q.get("verdict"), 0) + 1
    return out


def main():
    ap = argparse.ArgumentParser(description="JD fit register and gap harvest.")
    ap.add_argument("--gaps", action="store_true", help="Only the open questions for the candidate.")
    ap.add_argument("--role", metavar="OPP_ID")
    ap.add_argument("--pitch", metavar="OPP_ID", help="Just the pitch lines, for drafting.")
    args = ap.parse_args()

    opps = load("opportunities.jsonl")
    companies = {c["id"]: c.get("name", c["id"]) for c in load("companies.jsonl")}
    analyzed = [o for o in opps if o.get("fit")]

    def name(o):
        return "%s — %s" % (companies.get(o.get("company_id"), o.get("company_id")),
                            o.get("title", "?"))

    # ---- single role: the pitch lines only -------------------------------------
    if args.pitch:
        hit = [o for o in opps if o["id"] == args.pitch]
        if not hit or not hit[0].get("fit"):
            print("No fit analysis for %r. Run the analysis first." % args.pitch)
            return 1
        o = hit[0]
        print("PITCH LINES — %s" % name(o))
        print("=" * 76)
        for q in o["fit"]["requirements"]:
            if q.get("verdict") in ("aligned", "partial") and q.get("pitch_line"):
                print("\n  [%s] %s" % (q["verdict"].upper(), q["requirement"]))
                print("      %s" % q["pitch_line"])
                print("      evidence: %s" % q.get("evidence"))
        notal = [q for q in o["fit"]["requirements"] if q.get("verdict") == "not-aligned"]
        if notal:
            print("\n  ⚠️ DO NOT CLAIM these — they are genuine non-matches:")
            for q in notal:
                print("      - %s" % q["requirement"])
        return 0

    # ---- single role: full case ------------------------------------------------
    if args.role:
        hit = [o for o in opps if o["id"] == args.role]
        if not hit or not hit[0].get("fit"):
            print("No fit analysis for %r." % args.role)
            return 1
        o = hit[0]
        fit = o["fit"]
        rule("FIT ANALYSIS — %s" % name(o))
        print("  analyzed %s · source: %s" % (fit.get("analyzed_on"), fit.get("jd_source")))
        c = counts(fit["requirements"])
        print("  " + " · ".join("%s=%d" % (k, v) for k, v in sorted(c.items())))
        for verdict in ("aligned", "partial", "not-aligned", "unknown"):
            rows = [q for q in fit["requirements"] if q.get("verdict") == verdict]
            if not rows:
                continue
            print("\n  --- %s ---" % verdict.upper())
            for q in rows:
                print("  • %s" % q["requirement"])
                if q.get("evidence"):
                    print("      evidence : %s" % q["evidence"])
                if q.get("pitch_line"):
                    print("      pitch    : %s" % q["pitch_line"])
                if q.get("question_for_candidate"):
                    print("      ❓ ASK   : %s  [%s]"
                          % (q["question_for_candidate"], q.get("question_status", "?")))
        return 0

    # ---- register --------------------------------------------------------------
    rule("JD FIT REGISTER — %d of %d role(s) analyzed" % (len(analyzed), len(opps)))
    if not analyzed:
        print("  No fit analyses yet. The analysis runs when a role becomes a pursuit.")

    for o in sorted(analyzed, key=lambda x: x["id"]):
        c = counts(o["fit"]["requirements"])
        openq = sum(1 for q in o["fit"]["requirements"] if q.get("question_status") == "open")
        print("  %-52s %s%s" % (
            name(o)[:52],
            " · ".join("%s=%d" % (k, v) for k, v in sorted(c.items())),
            ("   ❓ %d open" % openq) if openq else ""))

    # ---- the harvest: open questions -------------------------------------------
    gaps, excluded_q = [], 0
    for o in analyzed:
        for q in o["fit"]["requirements"]:
            if q.get("question_status") == "open" and q.get("question_for_candidate"):
                if _is_excluded(o):
                    excluded_q += 1      # the role is decided; the question is moot
                    continue
                gaps.append((o, q))

    rule("OPEN GAPS — targeted questions whose answers grow the knowledge base")
    if excluded_q:
        # Say what was withheld and why. A count that silently shrinks is its own puzzle.
        print("  (%d question(s) on passed/closed roles are not counted — the role is decided,"
              % excluded_q)
        print("   so the question is moot. pipeline_index.is_excluded is the predicate.)\n")
    if not gaps:
        print("  None open. Every gap surfaced so far has been answered and filed.")
    else:
        print("  %d question(s). Each answer is filed into an EXISTING store —" % len(gaps))
        print("  projects.md · resume.md's 'Additional Detail' addenda · kb_<company>.md —")
        print("  so the next role's analysis starts from a fuller picture.\n")
        for o, q in gaps:
            print("  ❓ %s" % q["question_for_candidate"])
            print("     role        : %s" % name(o))
            print("     requirement : %s" % q["requirement"])
            print()

    # ---- is the knowledge base actually accumulating? ---------------------------
    answered = [(o, q) for o in analyzed for q in o["fit"]["requirements"]
                if q.get("question_status") == "answered"]
    rule("KNOWLEDGE-BASE ACCUMULATION")
    total_q = len(gaps) + len(answered)
    if not total_q:
        print("  No questions raised yet — nothing to measure.")
    else:
        print("  %d of %d gap question(s) answered and filed (%d still open)."
              % (len(answered), total_q, len(gaps)))
        where = {}
        for _o, q in answered:
            where[q.get("landed_in")] = where.get(q.get("landed_in"), 0) + 1
        for store, n in sorted(where.items(), key=lambda x: -x[1]):
            print("      %-24s %d" % (store, n))
        print("\n  A working loop shows LATER roles surfacing fewer NEW unknowns, because the")
        print("  stores answer more of the JD up front. Too few analyses to read that trend yet;")
        print("  this line will say so until it isn't true.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
