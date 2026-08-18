#!/usr/bin/env python3
"""
The COORDINATOR's view of the watcher queue — what the background sweeps found.

WHY THIS EXISTS
---------------
The candidate, 2026-08-02, wanted the daily check to run every ~2 hours without colliding with the
session the candidate is actually working in. The split that makes that safe:

    WATCHER (scripts/watch.py, every 2h)   read-only; appends findings here. Never writes state.
    COORDINATOR (the interactive session)  the ONLY writer. Drains this queue and acts.

This is the coordinator half. It never touches the mailbox — it reads the queue the watcher
already built, so draining it is instant and costs nothing.

**Why a queue rather than a message:** `send_message` is explicitly unavailable in
scheduled-task runs and cannot deliver to them, so a worker literally cannot message a session.
A file is also better: it survives session death, can be drained hours later, and is keyed so
the same finding is never queued twice.

Usage:
    python3 scripts/inbox.py                 # pending findings, urgent first
    python3 scripts/inbox.py --all           # include already-handled
    python3 scripts/inbox.py --ack <id>      # mark one handled
    python3 scripts/inbox.py --ack-kind alert  # mark a whole class handled
    python3 scripts/inbox.py --prune 30      # drop handled findings older than N days

Python 3.9+. Standard library only.
"""

import argparse
import datetime
import json
import os
import sys

import os, sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root as _profile_root
from _atomic import write_jsonl, write_json
ENGINE_SCRIPTS = os.path.dirname(os.path.realpath(__file__))

ROOT = _profile_root()
INBOX = os.path.join(ROOT, "data", "inbox.jsonl")

# What the coordinator should DO with each kind. Stated here so a drain is not improvised.
ACTION = {
    "reply": ("Read it. If they answered, set the outreach row's `outcome` + `responded_on` AND "
              "append the message to data/messages.jsonl (direction: inbound, with its source). "
              "This is the finding that costs opportunities when missed."),
    "meeting": ("Confirm the real time via gmail_get_attachment + parse_ics.py — a subject line "
                "can be weeks stale. Then put it in This Week and advance `stage`."),
    "alert": ("Read the roles. Cross-check `pipeline_index.py --excluded` before treating any as "
              "new; hand genuinely-new ones to opportunity-researcher."),
    "ats": ("Update the application's `status`. A rejection or an interview invite both change "
            "what the candidate should do next."),
    "run-summary": ("READ THIS FIRST — it is what a background run did while you were away. The "
                    "state is ALREADY written; this is the notification, not the work. Ack it "
                    "once you have told the candidate anything they need to act on."),
}

# ⭐⭐ WHY `run-summary` EXISTS — added 2026-08-03, and it closes a hole I opened that morning.
#
# The candidate: *"Did the 9am run send an update to the coordinator session? I didn't see that. There is a
# bunch of information in the 9am session I didn't see in the coordinator view."* Both channels
# were silent, for two different reasons:
#
#   PUSH  `notifyOnCompletion` had never been claimed — no subscriber, so no notification.
#   PULL  this queue had **zero** records from that run.
#
# The PULL half is the one I broke. Before the lock redesign, a run that collided with the candidate's
# session DOWNGRADED to `watch.py`, which queued what it found, and the coordinator drained it.
# Making runs able to write concurrently removed the downgrade — correct for state, but the
# downgrade path was **also the only thing feeding this queue**. So a full run wrote everything
# into data/*.jsonl and told the coordinator nothing.
#
# Nothing was lost; it was invisible, which for a role that needs a decision is nearly as bad.
# The 09:07 run booked a Larkbridge Technology call for Wed 08/05 9:00 AM and sourced three roles, and none of
# it surfaced in their view.
#
# So a FULL run now posts a summary here as its last act. **This is a notification, never a
# substitute for state** — the writes still happen in the run.


def load():
    if not os.path.exists(INBOX):
        return []
    with open(INBOX, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def append(records):
    """APPEND ONLY. Never rewrite this file.

    ⚠️ THE RACE THIS FIXES (found 2026-08-02 by mapping data ownership): `watch.py` APPENDS to
    this file from a downgraded background run, which by design runs *while the coordinator
    holds the lock* — so the two writers are concurrent on purpose. This function used to
    rewrite the whole file on `--ack`, and a rewrite that began before an append landed would
    silently CLOBBER it. The finding would vanish with no trace.

    So state is now a LOG, not a document: `--ack` appends an ack record, and current status is
    derived by replaying the file. Appends of this size are atomic; a rewrite never is.
    """
    with open(INBOX, "a", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def replay(rows):
    """Fold the log into current state: the newest record per `id` wins."""
    state = {}
    for r in rows:
        rid = r.get("id")
        if not rid or rid == "_README":
            continue
        if rid in state and r.get("kind") == "_ack":
            state[rid] = dict(state[rid], status="handled", acked_at=r.get("acked_at"))
        elif r.get("kind") != "_ack":
            state[rid] = r
    return list(state.values())


def main():
    ap = argparse.ArgumentParser(description="Drain the watcher's finding queue.")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--ack", metavar="ID")
    ap.add_argument("--ack-kind", metavar="KIND")
    ap.add_argument("--prune", type=int, metavar="DAYS")
    ap.add_argument("--post", metavar="SUMMARY",
                    help="Append a finding for the coordinator. A FULL run posts its run-summary "
                         "here as its last act — otherwise it writes state and tells the candidate nothing.")
    ap.add_argument("--kind", default="run-summary")
    ap.add_argument("--detail", default="")
    ap.add_argument("--urgency", default="normal", choices=("normal", "high"))
    args = ap.parse_args()

    raw = load()
    rows = replay(raw)
    now = datetime.datetime.now()

    if args.post:
        rec = {"id": "%s-%s" % (args.kind, now.strftime("%Y%m%dT%H%M%S")),
               "kind": args.kind, "summary": args.post, "detail": args.detail,
               "urgency": args.urgency, "status": "pending",
               "found_at": now.isoformat(timespec="seconds")}
        append([rec])          # APPEND, never rewrite — see append()'s docstring
        print("Posted %s for the coordinator:\n  %s" % (rec["id"], args.post))
        return 0

    if args.ack or args.ack_kind:
        acks = []
        for r in rows:
            if (args.ack and r["id"] == args.ack) or \
               (args.ack_kind and r["kind"] == args.ack_kind and r["status"] == "pending"):
                acks.append({"id": r["id"], "kind": "_ack",
                             "acked_at": now.isoformat(timespec="seconds")})
        append(acks)          # APPEND, never rewrite — see append()'s docstring
        print("Marked %d finding(s) handled." % len(acks))
        return 0

    if args.prune:
        # The ONLY safe rewrite: take the write lock first, so no watcher can be appending.
        import subprocess as _sp
        lock = _sp.run([sys.executable, os.path.join(ENGINE_SCRIPTS, "runlock.py"),
                        "--take", "inbox prune"], capture_output=True, text=True)
        if lock.returncode:
            print("Refused — a writer holds the lock. Pruning REWRITES the file, which would")
            print("race a watcher's append. Try again when the lock is free.")
            return 1
        try:
            cut = (now - datetime.timedelta(days=args.prune)).isoformat()
            keep = [r for r in rows if r["status"] == "pending" or (r.get("acked_at") or "") >= cut]
            write_jsonl(INBOX, keep)
            print("Pruned %d handled finding(s) older than %d days."
                  % (len(rows) - len(keep), args.prune))
        finally:
            _sp.run([sys.executable, os.path.join(ENGINE_SCRIPTS, "runlock.py"), "--release"],
                    capture_output=True)
        return 0

    pending = [r for r in rows if r["status"] == "pending"]
    show = rows if args.all else pending

    print("COORDINATOR INBOX — what the background watcher found")
    print("=" * 74)
    print("  %d pending · %d handled · %d total" %
          (len(pending), len(rows) - len(pending), len(rows)))
    if not rows:
        print("\n  Empty. Either the watcher hasn't run, or nothing new has happened.")
        print("  Run it manually: python3 scripts/watch.py")
        return 0
    if not show:
        print("\n  Nothing pending — every finding has been handled.")
        return 0

    for kind in ("run-summary", "reply", "meeting", "ats", "alert"):
        group = [r for r in show if r["kind"] == kind]
        if not group:
            continue
        print("\n" + "-" * 74)
        print("%s  (%d)" % (kind.upper(), len(group)))
        print("  DO: %s" % ACTION.get(kind, "review it"))
        print("-" * 74)
        for r in sorted(group, key=lambda x: x["found_at"], reverse=True):
            flag = "⚠️ " if r["urgency"] == "high" else "   "
            mark = "" if r["status"] == "pending" else "  [handled]"
            print("  %s%s%s" % (flag, r["summary"][:96], mark))
            if r.get("detail"):
                print("       %s" % r["detail"][:104])
            print("       id: %s" % r["id"])

    print("\n" + "=" * 74)
    print("  Mark handled:  python3 scripts/inbox.py --ack <id>")
    print("  Or a class:    python3 scripts/inbox.py --ack-kind alert")
    print("\n  ⚠️ A finding here is a thing the JSON does not know yet. The daily run's job is to")
    print("     write it into data/*.jsonl — the queue is a hand-off, not a substitute for state.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
