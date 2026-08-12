#!/usr/bin/env python3
"""The run journal — what a run knows, written the moment it knows it.

⭐ TWO ISSUES, ONE DEFECT (GitHub #4 and #5)
--------------------------------------------
**#4 — a run that dies early loses everything it learned.** Findings are held in the session
buffer and written out in one terminal step, so any early termination discards all of them. One
observed run had, before terminating: resolved a genuine contradiction between a generated
artifact and its underlying data, and enumerated roughly three times more outstanding items in a
channel than the previous run carried forward. **None of it reached any file.** Worse, the next
run cannot tell *did not fire* from *fired and found nothing* from *fired, found things, and died*.

**#5 — a coverage gap is recorded as prose.** When a sweep cannot finish, the gap goes into a
run-log narrative asking a future run to re-check. Nothing sorts it, nothing escalates it, no
run-start check reads it. One gap survived three runs with nothing flagging it.

⭐⭐ THEY ARE THE SAME BUG: **a fact the run ALREADY KNOWS, stored in the one format no check can
read.** So they get one mechanism, not two — fixing the class rather than the instance.

## The shape, copied from `precondition.py` deliberately

A named record · a strict parser that refuses what it cannot read · a resolver over data that
already exists. Append-only, one JSON object per line, because **two runs may journal at once and
an append cannot lose a concurrent write the way a rewrite can.**

    start   a run began              → an unmatched start is a run that DIED
    note    something was learned    → survives termination, because it is written NOW
    gap     a sweep did not complete  → structured, sortable, and closeable
    end     the run finished cleanly

⚠️ **Writing at the end is the bug.** `--note` and `--gap` are worth nothing if a run batches them;
they must be called as the run learns each thing. The whole point is that a crash one line later
still leaves the finding on disk.

Usage:
    python3 journal.py --start daily                     # prints the run id
    python3 journal.py --run <id> --note "what was found"
    python3 journal.py --run <id> --gap linkedin:replies --reason browser-unavailable \
                       --closes-when "a run with chrome completes the sweep"
    python3 journal.py --run <id> --end
    python3 journal.py --unfinished     # runs that started and never ended, with their notes
    python3 journal.py --open-gaps      # oldest first
    python3 journal.py --close-gap <gap_id>
    python3 journal.py --check          # exit 1 if a gap is stale or a run died with findings

Python 3.9+. Standard library only.
"""

import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root

JOURNAL = os.path.join("data", "runs.jsonl")
EVENTS = ("start", "note", "gap", "gap-closed", "end")

# A reason is a CODE, not a sentence — codes can be counted, sentences cannot. An unrecognised
# one is refused rather than stored, because a taxonomy nobody enforces becomes free text within
# a month and then nothing can group by it.
REASONS = {"browser-unavailable", "credential-missing", "rate-limited", "timeout",
           "partial-results", "skipped-for-cost", "upstream-error", "interrupted", "other"}

STALE_DAYS = 3


class JournalError(ValueError):
    """Unparseable or unrecognised. Loud on purpose."""


def path(root):
    return os.path.join(root, JOURNAL)


def now_iso():
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def append(root, rec):
    p = path(root)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")
    return rec


def read(root):
    out = []
    try:
        with open(path(root), encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    raise JournalError("%s line %d is not valid JSON — a journal that cannot be "
                                       "read is a journal that is not protecting anything"
                                       % (JOURNAL, n))
                if rec.get("event") not in EVENTS:
                    raise JournalError("%s line %d has unknown event %r"
                                       % (JOURNAL, n, rec.get("event")))
                out.append(rec)
    except FileNotFoundError:
        pass
    return out


def new_run_id(kind, at):
    slug = re.sub(r"[^a-z0-9]+", "-", (kind or "run").lower()).strip("-") or "run"
    return "%s-%s" % (slug, re.sub(r"[^0-9]", "", at)[:14])


def unfinished(recs):
    """Runs with a start and no end. ⭐ Their notes are the recoverable work."""
    started, ended = {}, set()
    for r in recs:
        if r["event"] == "start":
            started[r["run_id"]] = r
        elif r["event"] == "end":
            ended.add(r["run_id"])
    out = []
    for rid, s in started.items():
        if rid in ended:
            continue
        notes = [r for r in recs if r.get("run_id") == rid and r["event"] == "note"]
        gaps = [r for r in recs if r.get("run_id") == rid and r["event"] == "gap"]
        out.append({"run_id": rid, "at": s.get("at"), "kind": s.get("kind"),
                    "notes": [n.get("text") for n in notes], "gaps": len(gaps)})
    return sorted(out, key=lambda x: x.get("at") or "")


def open_gaps(recs):
    closed = {r.get("gap_id") for r in recs if r["event"] == "gap-closed"}
    gaps = [r for r in recs if r["event"] == "gap" and r.get("gap_id") not in closed]
    return sorted(gaps, key=lambda g: g.get("at") or "")


def age_days(at):
    try:
        then = datetime.datetime.fromisoformat(at)
        return (datetime.datetime.now() - then).days
    except Exception:
        return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", metavar="KIND")
    ap.add_argument("--run", metavar="ID")
    ap.add_argument("--note")
    ap.add_argument("--gap", metavar="SCOPE")
    ap.add_argument("--reason", choices=sorted(REASONS))
    ap.add_argument("--closes-when", dest="closes_when")
    ap.add_argument("--end", action="store_true")
    ap.add_argument("--unfinished", action="store_true")
    ap.add_argument("--open-gaps", dest="open_gaps", action="store_true")
    ap.add_argument("--close-gap", dest="close_gap", metavar="GAP_ID")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--at", help="ISO timestamp; for tests and for replaying a known time")
    args = ap.parse_args()

    root = profile_root()
    at = args.at or now_iso()

    try:
        if args.start:
            rid = new_run_id(args.start, at)
            append(root, {"event": "start", "run_id": rid, "kind": args.start, "at": at})
            print(rid)
            return 0

        if args.note or args.gap or args.end or args.close_gap:
            if args.close_gap:
                append(root, {"event": "gap-closed", "gap_id": args.close_gap, "at": at})
                print("closed %s" % args.close_gap)
                return 0
            if not args.run:
                raise JournalError("--run <id> is required (get one from --start)")
            if args.note:
                append(root, {"event": "note", "run_id": args.run, "text": args.note, "at": at})
                print("noted")
            if args.gap:
                if not args.reason:
                    raise JournalError(
                        "--reason is required with --gap. A gap without a reason code cannot be "
                        "counted or grouped, which is the whole reason this is not prose.")
                gid = "%s:%s" % (args.run, re.sub(r"[^A-Za-z0-9:._-]+", "-", args.gap))
                append(root, {"event": "gap", "run_id": args.run, "gap_id": gid,
                              "scope": args.gap, "reason": args.reason,
                              "closes_when": args.closes_when or "", "at": at})
                print(gid)
            if args.end:
                append(root, {"event": "end", "run_id": args.run, "at": at})
                print("ended")
            return 0

        recs = read(root)
        dead = unfinished(recs)
        gaps = open_gaps(recs)

        if args.json:
            print(json.dumps({"unfinished": dead, "open_gaps": gaps}, indent=1))
        elif args.open_gaps:
            print("OPEN COVERAGE GAPS — oldest first\n")
            for g in gaps:
                print("  [%2dd] %s  (%s)" % (age_days(g.get("at", "")), g.get("scope"),
                                             g.get("reason")))
                print("        %s · closes when: %s" % (g.get("gap_id"),
                                                        g.get("closes_when") or "unstated"))
            if not gaps:
                print("  None. Every sweep that started has been completed or closed.")
        elif args.unfinished:
            print("RUNS THAT STARTED AND NEVER ENDED\n")
            for d in dead:
                print("  %s  (%s)  %d note(s), %d gap(s)"
                      % (d["run_id"], d.get("at"), len(d["notes"]), d["gaps"]))
                for n in d["notes"][:5]:
                    print("      · %s" % str(n)[:110])
            if not dead:
                print("  None. Every run that started also finished.")
        else:
            print("RUN JOURNAL\n")
            print("  %d run(s) died mid-flight · %d open coverage gap(s)" % (len(dead), len(gaps)))
            if dead:
                print("\n  ⭐ A run that started and never ended did NOT necessarily do nothing —")
                print("     its notes below are work that would otherwise have been lost.")
                for d in dead[:3]:
                    print("     %s: %d note(s)" % (d["run_id"], len(d["notes"])))
            if gaps:
                print("\n  ⏳ oldest gap: %s (%dd, %s)"
                      % (gaps[0].get("scope"), age_days(gaps[0].get("at", "")),
                         gaps[0].get("reason")))

        if args.check:
            stale = [g for g in gaps if age_days(g.get("at", "")) >= STALE_DAYS]
            lost = [d for d in dead if d["notes"]]
            for g in stale:
                print("⛔ coverage gap %r open %d days (%s)"
                      % (g.get("scope"), age_days(g.get("at", "")), g.get("reason")),
                      file=sys.stderr)
            for d in lost:
                print("⛔ run %s died holding %d unwritten finding(s)"
                      % (d["run_id"], len(d["notes"])), file=sys.stderr)
            return 1 if (stale or lost) else 0
        return 0

    except JournalError as e:
        print("⛔ %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
