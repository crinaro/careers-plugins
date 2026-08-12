#!/usr/bin/env python3
"""
Enforce the process-debt invariant: **ZERO open process items after a weekly run.**

THE RULE (the candidate, 2026-08-02)
---------------------------
    "On the process report there should be zero items for you to resolve, because
     they should be resolved in the weekly strategy run. The issues can accumulate
     during the week from the daily runs but zero should exist after the weekly run."

So `## ⚙️ Process — 🔧 Open` in focus.md is a WEEKLY WORK QUEUE, not a museum. Daily
runs may append to it freely; the weekly run must drain it to empty. An item leaves
the queue exactly one of four ways:

  1. FIXED           — do the work, verify it by running the thing, archive the entry.
  2. ALREADY FIXED   — verify against the machine, then archive. (On 2026-08-02, 10 of
                       23 items were closed lessons nobody had cleared.)
  3. NOT MINE        — it needs the candidate's decision/credential/fact → move it to
                       `## ⚙️ Process — ⚡ Needs the candidate`, which is a real ask list.
  4. WON'T FIX       — say why, in one line, and archive it. A declined item is closed.

Before archiving anything, check the DURABLE HOME. An item usually carries a lesson;
if that lesson is not already a rule in `CLAUDE.md` or in a `.claude/agents/*.md`
definition, archiving it DELETES the knowledge. On 2026-08-02 three rules had no
durable home (the LinkedIn-fetch invents-<the commute anchor> rule, the grep-the-published-output
rule, the connection-degree reply check) and were promoted before their items were
archived.

WHY A SCRIPT AND NOT A RULE
---------------------------
Because the same list grew to 23 items while a rule saying "keep it short" sat at the
top of the file. This repo's own recurring lesson is that a rule in prose is re-read
and re-interpreted every run, while a check fails loudly and for free.

Usage:
    python3 scripts/check_process_debt.py            # advisory (daily) — always exit 0
    python3 scripts/check_process_debt.py --weekly   # ENFORCING — exit 1 if not drained

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

ROOT = _profile_root()
FOCUS = os.path.join(ROOT, "focus.md")
ARCHIVE = os.path.join(ROOT, "process_archive.md")

OPEN_HEADER = "## ⚙️ Process — 🔧 Open"
NEEDS_CANDIDATE_HEADER = "## ⚙️ Process — ⚡ Needs the candidate"

RESOLVED_MARKERS = re.compile(
    r"✅|FIXED|RESOLVED|UPGRADED|RETRACTED|WITHDRAWN|SUPERSEDED|MOOT|DECLINED", re.I)
DATE_RE = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)\b")


def section(text, header):
    start = text.find(header)
    if start < 0:
        return ""
    nxt = text.find("\n## ", start + len(header))
    return text[start:nxt if nxt > 0 else len(text)]


def items(sec):
    return re.findall(r"^(\d+)\. (.*)$", sec, re.M)


def oldest_date(body):
    hits = DATE_RE.findall(body)
    if not hits:
        return None
    try:
        return min(datetime.date(int(y), int(m), int(d)) for y, m, d in hits)
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser(description="Process-debt invariant check.")
    ap.add_argument("--weekly", action="store_true",
                    help="Enforcing mode: exit 1 unless the Open queue is empty.")
    ap.add_argument("--today", metavar="YYYY-MM-DD")
    args = ap.parse_args()

    try:
        today = (datetime.date.fromisoformat(args.today) if args.today
                 else datetime.date.today())
    except ValueError:
        sys.stderr.write("--today must be YYYY-MM-DD\n")
        return 2

    with open(FOCUS, encoding="utf-8") as fh:
        text = fh.read()

    open_sec = section(text, OPEN_HEADER)
    if not open_sec:
        print("No '%s' section found in focus.md." % OPEN_HEADER)
        return 0

    rows = items(open_sec)
    mode = "ENFORCING (weekly)" if args.weekly else "advisory (daily)"
    print("Process-debt check — %s — as of %s" % (mode, today.isoformat()))
    print("=" * 72)
    print("Open process items (mine to fix): %d" % len(rows))

    if not rows:
        print("\n  ✅ Queue is EMPTY. The invariant holds: a weekly run leaves zero")
        print("     process items outstanding.")
        return 0

    stale, aging = [], []
    for num, body in rows:
        d = oldest_date(body)
        age = (today - d).days if d else None
        if RESOLVED_MARKERS.search(body):
            stale.append((num, body, age))
        else:
            aging.append((num, body, age))

    if stale:
        print("\n" + "-" * 72)
        print("ALREADY-RESOLVED items still sitting in the queue — ARCHIVE THESE.")
        print("They read as outstanding work and inflate the list. Verify the fix")
        print("against the machine, confirm the lesson has a durable home in")
        print("CLAUDE.md or an agent definition, then move them to process_archive.md.")
        print("-" * 72)
        for num, body, age in stale:
            print("  [%s] %s%s" % (num, re.sub(r"\*\*|`", "", body)[:96],
                                   "  (%dd old)" % age if age else ""))

    if aging:
        print("\n" + "-" * 72)
        print("OPEN items — each needs one of: FIXED / ALREADY-FIXED / NOT-MINE / WON'T-FIX")
        print("-" * 72)
        for num, body, age in sorted(aging, key=lambda r: -(r[2] or 0)):
            print("  [%s] %s%s" % (num, re.sub(r"\*\*|`", "", body)[:96],
                                   "  (%dd old)" % age if age else ""))

    print("\n" + "=" * 72)
    if args.weekly:
        print("❌ INVARIANT VIOLATED: the weekly run must leave this queue EMPTY.")
        print("   %d item(s) remain. Drain them before finishing the run — fix, archive," % len(rows))
        print("   reassign to 'Needs the candidate', or decline with a reason. Do not carry them.")
        print("   (the candidate, 2026-08-02: 'zero should exist after the weekly run.')")
        return 1

    print("Advisory only — daily runs may accumulate debt. The WEEKLY run must drain it")
    print("to zero (`--weekly` enforces this and is wired into the weekly task prompt).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
