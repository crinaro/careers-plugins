#!/usr/bin/env python3
"""
Did the data move under me? — the staleness check for a long-running session.

WHY THIS EXISTS
---------------
The candidate, 2026-08-02: *"How does the long running session know to execute its process when data was
updated?"*

**The honest mechanics first, because they constrain the answer.** A Claude session is
**turn-driven**: it cannot wake itself, poll, or watch a file. So "the session knows" can only
mean one of two things:

  **(a) something DELIVERS A TURN to it** — `notifyOnCompletion` on a scheduled task. That is a
      genuine wake, and the only one available. **But only a REGULAR session can claim it** (a
      scheduled run is refused: *"it ends when the run does"*), so the candidate claims it directly, and
      re-claims it each time they open a new coordinator session.

  **(b) it CHECKS at the start of a turn** — which needs something to compare against. That is
      this script.

**Why (b) matters even with (a) working, and it is not a nicety.** A background run can write
state *while the candidate is mid-conversation* — their session read `data/*.jsonl` several turns ago and is
reasoning from that. A notification tells the candidate a run *finished*; it does not tell their session that
the ground it is standing on moved. **Acting on a stale read is a correctness bug, not a missed
alert** — it is how a session would confidently re-draft an outreach note for a reply that already
arrived.

So: the coordinator records a **watermark** of what it last saw, and can ask this script at any
time whether anything changed since. Cheap — size + mtime, no parsing, no hashing of large files.

    ⚠️ THIS DOES NOT MAKE THE SESSION AUTONOMOUS. Nothing here polls. It answers a question when
    asked. The convention that makes it useful is: **check before you write — AND before you
    REPORT.**

    ⭐ "BEFORE YOU WRITE" WAS TOO NARROW, 2026-08-04. A session ended a long stretch of tooling
    work by telling the candidate a reply still needed drafting. It had been sent and recorded
    eight hours earlier by a concurrent run; the watermark said STALE the whole time and was
    never consulted, because the rule only mentioned writes. **Reporting stale state costs the
    same as writing it** — the candidate acted on "you still owe Alex a reply" after already sending it.
    Run this before any status summary, not just before an edit.

Usage:
    python3 scripts/changed.py --mark       # record what I'm seeing now (after reading state)
    python3 scripts/changed.py              # has anything moved since the mark? exit 1 if yes
    python3 scripts/changed.py --verbose    # show which files, and how

Python 3.9+. Standard library only.
"""

import argparse
import datetime
import json
import os
import re
import sys

import os, sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root as _profile_root

ROOT = _profile_root()
# ⭐⭐ ONE WATERMARK PER READER — fixed 2026-08-03. It used to be a SINGLE shared file, which
# made this guard actively dangerous the moment a second session existed, i.e. always, since the
# whole coordinator + background design is about concurrent sessions.
#
# Found while answering the candidate's question "do I need to run /coordinator to get the update": their
# session marked the watermark at 06:41; my work overwrote it at 07:15; their session then asked
# "did anything move since I looked?" and got **"UNCHANGED since 07:15 — safe to write."** That
# is false for them — three commits landed in between, including a log.md rewrite that moved 106
# entries. **It failed OPEN**, handing out a confident all-clear, which is worse than no guard at
# all: the one job of this script is to catch exactly that stale read.
MARK_DIR = os.path.join(ROOT, ".git", "watermarks")   # untracked, per-machine


def mark_path(reader):
    return os.path.join(MARK_DIR, "%s.json" % re.sub(r"[^A-Za-z0-9_.-]", "_", reader))

# The state a session reasons from. If one of these moved, its read is stale.
WATCHED = [
    "data/opportunities.jsonl", "data/companies.jsonl", "data/channels.jsonl",
    "data/messages.jsonl", "data/inbox.jsonl",
    # dev #93 — the asks and commitments stores replaced focus.md; handoff.md is the
    # surviving hand-written narrative a session reasons from.
    "data/asks.jsonl", "data/commitments.jsonl",
    "handoff.md", "drafts.md", "cover_letters.md", "log.md",
]


def fingerprint():
    fp = {}
    for rel in WATCHED:
        p = os.path.join(ROOT, rel)
        if os.path.exists(p):
            st = os.stat(p)
            fp[rel] = {"size": st.st_size, "mtime": int(st.st_mtime)}
        else:
            fp[rel] = None
    return fp


def main():
    ap = argparse.ArgumentParser(description="Has state changed since this session last looked?")
    ap.add_argument("--mark", action="store_true", help="Record the current state as seen.")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--as", dest="reader", default=os.environ.get("CLAUDESEARCH_READER", "default"),
                    metavar="NAME",
                    help="WHOSE watermark. Each reader gets its own file, because a shared one "
                         "hands a second session a confident, wrong 'safe to write'. The "
                         "coordinator passes 'coordinator'; a scheduled run passes 'daily'.")
    args = ap.parse_args()
    MARK = mark_path(args.reader)

    now = fingerprint()

    if args.mark:
        os.makedirs(os.path.dirname(MARK), exist_ok=True)
        with open(MARK, "w", encoding="utf-8") as fh:
            json.dump({"at": datetime.datetime.now().isoformat(timespec="seconds"),
                       "reader": args.reader, "files": now}, fh)
        print("Marked for reader %r. %d file(s) recorded as seen." % (args.reader, len(now)))
        print("Ask `python3 scripts/changed.py` before your next write.")
        return 0

    if not os.path.exists(MARK):
        print("NO WATERMARK for reader %r — it has never marked what it saw." % args.reader)
        print("  Run `python3 scripts/changed.py --mark` after reading state, then this")
        print("  can tell you whether a background run moved anything underneath you.")
        return 0

    with open(MARK, encoding="utf-8") as fh:
        prev = json.load(fh)
    before = prev.get("files", {})

    moved, appeared, vanished = [], [], []
    for rel, cur in now.items():
        old = before.get(rel, "absent")
        if old == "absent":
            continue
        if cur is None and old is not None:
            vanished.append(rel)
        elif cur is not None and old is None:
            appeared.append(rel)
        elif cur and old and (cur["size"] != old["size"] or cur["mtime"] != old["mtime"]):
            moved.append((rel, old, cur))

    if not (moved or appeared or vanished):
        print("UNCHANGED since %s (reader %r) — your read is still current. Safe to write."
              % (prev.get("at"), args.reader))
        return 0

    print("⚠️  STATE CHANGED since %s — YOUR READ IS STALE." % prev.get("at"))
    print("=" * 72)
    for rel, old, cur in moved:
        delta = cur["size"] - old["size"]
        print("  %-32s %+d bytes" % (rel, delta))
        if args.verbose:
            print("      %s -> %s"
                  % (datetime.datetime.fromtimestamp(old["mtime"]).strftime("%H:%M:%S"),
                     datetime.datetime.fromtimestamp(cur["mtime"]).strftime("%H:%M:%S")))
    for rel in appeared:
        print("  %-32s NEW" % rel)
    for rel in vanished:
        print("  %-32s GONE" % rel)

    print("\n  A background run wrote while you were working. **RE-READ before you write** —")
    print("  acting on a stale read is how a session confidently re-drafts an outreach note")
    print("  for a reply that already arrived.")
    if any(r == "data/inbox.jsonl" for r, _, _ in moved) or "data/inbox.jsonl" in appeared:
        print("\n  data/inbox.jsonl moved -> a run queued findings for you:")
        print("      python3 scripts/inbox.py")
    print("\n  When you've caught up:  python3 scripts/changed.py --mark")
    return 1


if __name__ == "__main__":
    sys.exit(main())
