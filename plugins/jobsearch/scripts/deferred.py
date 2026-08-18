#!/usr/bin/env python3
"""
Work that needs the LAPTOP — queued from anywhere, executed where the resources are.

WHY THIS EXISTS
---------------
The candidate, 2026-08-02: *"How does the coordinator connect with resources only available on the laptop
such as send a LinkedIn message through the chrome extension?"*

**The blunt answer: it doesn't, and everything in this system is laptop-bound.** The repo, the
Gmail credentials (macOS Keychain, service `claudesearch-imap`), `wake_chrome.sh` (`osascript`),
every script, and above all the **Chrome extension — which is the candidate's own logged-in browser** — all
live on the Mac. Scheduled tasks are documented as running only *"while this app is open"*, i.e.
the desktop app.

So a coordinator session that cannot reach the Mac can **decide**, but it cannot **act**.

This is the queue for the gap — the mirror of `data/inbox.jsonl`:

    data/inbox.jsonl            background run  →  coordinator     "here is what I found"
    data/pending_actions.jsonl  coordinator     →  laptop run      "here is what needs your hands"

## ⛔ WHAT THIS QUEUE MUST NEVER CARRY: A SEND

**Deliberately excluded, and this is the important part of the answer.** CLAUDE.md's standing rule
is *"NEVER send messages, emails, or applications without the candidate's explicit fresh approval."* **A
send queued now and executed by an unattended run three hours later is not fresh approval** — it is
approval-at-a-distance for an irreversible, outward-facing act. Building that would quietly convert
a hard rule into a soft one.

**And the constraint bites far less than it looks**, because the candidate sends everything directly anyway.
When Claude has sent a LinkedIn message it was always *"on the candidate's explicit in-session instruction"*,
with them present at that moment. That workflow simply requires the laptop and their attention
together — which is exactly what it required before this queue existed.

**So what belongs here is laptop-bound work that is SAFE unattended and REVERSIBLE:** a contact-path
lookup, a reply/degree check, reading a JD an ATS won't render to a plain fetch, a profile read.
Research and reads. Never a send, never an application, never a profile edit.

Usage:
    python3 scripts/deferred.py                       # what is waiting for the laptop
    python3 scripts/deferred.py --add "<what>" --why "<why it needs the laptop>"
    python3 scripts/deferred.py --done <id>

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
QUEUE = os.path.join(ROOT, "data", "pending_actions.jsonl")

# A queued item matching any of these is refused outright. See the docstring: a send executed
# later by an unattended run is not the "explicit fresh approval" the standing rule requires.
FORBIDDEN = re.compile(
    r"\b(send|sending|reply to|respond to|submit|apply to|applying|post|publish|"
    r"accept|connect with|inmail|dm|message (him|her|them)|email (him|her|them))\b", re.I)


def load():
    if not os.path.exists(QUEUE):
        return []
    with open(QUEUE, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def replay(rows):
    """Append-only, same as the inbox: newest record per id wins.

    ⭐ CLAIMS ARE APPEND RECORDS TOO. In a distributed model two workers can read this file at
    once, so a claim must be an atomic append and never a rewrite — the same reasoning that made
    the findings queue append-only. Last claim wins, which is safe because a claim is a LEASE:
    it expires, so a worker that dies holding one cannot strand the task forever.
    """
    state = {}
    for r in rows:
        rid = r.get("id")
        if not rid:
            continue
        kind = r.get("kind")
        if kind == "_done" and rid in state:
            state[rid] = dict(state[rid], status="done", done_at=r.get("done_at"))
        elif kind == "_claim" and rid in state:
            state[rid] = dict(state[rid], claimed_by=r.get("claimed_by"),
                              claimed_at=r.get("claimed_at"))
        elif kind == "_release" and rid in state:
            state[rid] = dict(state[rid], claimed_by=None, claimed_at=None)
        elif kind not in ("_done", "_claim", "_release"):
            state[rid] = r
    return list(state.values())


# A claim is a LEASE, not a lock. A worker that dies mid-task must not strand the work.
CLAIM_LEASE_MINUTES = 45


def claim_is_live(rec, now):
    at = rec.get("claimed_at")
    if not rec.get("claimed_by") or not at:
        return False
    try:
        held = (now - datetime.datetime.fromisoformat(at)).total_seconds() / 60.0
    except Exception:
        return False
    return held < CLAIM_LEASE_MINUTES


def capabilities():
    """Ask whoami.py, never assume. A worker that guesses its own capability is the bug."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import whoami
        return whoami.probe(), whoami.worker_id(whoami.probe())
    except Exception:
        return {}, "unknown"


def can_run(rec, caps):
    return all(caps.get(r) for r in (rec.get("requires") or []))


def main():
    ap = argparse.ArgumentParser(description="Work that needs the laptop.")
    ap.add_argument("--add", metavar="WHAT")
    ap.add_argument("--why", metavar="WHY", default="")
    ap.add_argument("--requires", metavar="CAPS", default="",
                    help="Comma-separated capabilities this work NEEDS (e.g. chrome,keychain). "
                         "Routing depends on it: a worker claims only what it can finish.")
    ap.add_argument("--claimable", action="store_true",
                    help="Show only what THIS worker can actually execute.")
    ap.add_argument("--claim", metavar="ID", help="Lease an item for this worker.")
    ap.add_argument("--release", metavar="ID", help="Give an item back.")
    ap.add_argument("--done", metavar="ID")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    now = datetime.datetime.now()
    rows = replay(load())

    if args.add:
        if FORBIDDEN.search(args.add):
            print("⛔ REFUSED — this reads like a SEND, and sends never queue.")
            print()
            print("   CLAUDE.md: 'NEVER send messages, emails, or applications without the candidate's")
            print("   explicit fresh approval.' A send queued now and executed by an unattended")
            print("   run later is not fresh approval — it is approval-at-a-distance for an")
            print("   irreversible, outward-facing act.")
            print()
            print("   the candidate sends everything directly anyway. Put the DRAFT in drafts.md and let")
            print("   them send it when they are at the laptop with the thread in front of them.")
            return 1
        rid = "act-%s" % now.strftime("%Y%m%dT%H%M%S")
        reqs = [r.strip() for r in args.requires.split(",") if r.strip()]
        rec = {"id": rid, "kind": "action", "what": args.add, "why": args.why,
               "requires": reqs,
               "queued_at": now.isoformat(timespec="seconds"), "status": "pending",
               "claimed_by": None, "claimed_at": None, "done_at": None}
        with open(QUEUE, "a", encoding="utf-8") as fh:      # append-only, like the inbox
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print("Queued %s%s:\n  %s"
              % (rid, (" [requires: %s]" % ", ".join(reqs)) if reqs else "", args.add))
        return 0

    caps, me = capabilities()

    if args.claim or args.release:
        rid = args.claim or args.release
        cur = {r["id"]: r for r in rows}.get(rid)
        if not cur:
            print("No such item: %s" % rid)
            return 1
        if args.claim:
            if not can_run(cur, caps):
                need = ", ".join(cur.get("requires") or [])
                print("⛔ THIS WORKER CANNOT RUN %s — it requires: %s" % (rid, need))
                print("   %s has: %s" % (me, ", ".join(k for k, v in caps.items() if v) or "nothing"))
                print("   Leave it for a worker that can. A claimed task that fails is WORSE than")
                print("   an unclaimed one, because it looks handled.")
                return 1
            if claim_is_live(cur, now) and cur.get("claimed_by") != me:
                print("Already claimed by %s at %s (lease %d min). Not stealing."
                      % (cur["claimed_by"], cur.get("claimed_at"), CLAIM_LEASE_MINUTES))
                return 1
            rec = {"id": rid, "kind": "_claim", "claimed_by": me,
                   "claimed_at": now.isoformat(timespec="seconds")}
        else:
            rec = {"id": rid, "kind": "_release"}
        with open(QUEUE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print("%s %s" % ("Claimed" if args.claim else "Released", rid))
        return 0

    if args.done:
        with open(QUEUE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": args.done, "kind": "_done",
                                 "done_at": now.isoformat(timespec="seconds")}) + "\n")
        print("Marked %s done." % args.done)
        return 0

    pending = [r for r in rows if r.get("status") == "pending"]
    mine = [r for r in pending if can_run(r, caps)]
    blocked = [r for r in pending if not can_run(r, caps)]
    show = rows if args.all else (mine if args.claimable else pending)

    print("DEFERRED WORK — worker %s" % me)
    print("=" * 72)
    print("  %d pending · %d this worker can run · %d need another environment · %d done"
          % (len(pending), len(mine), len(blocked), len(rows) - len(pending)))
    if blocked and not args.claimable:
        print("\n  ⛔ NEEDS ANOTHER ENVIRONMENT — do not attempt these here:")
        for r in blocked:
            print("     %-52s requires %s" % ((r.get("what") or "")[:52],
                                              ", ".join(r.get("requires") or [])))
    if not show:
        print("\n  Nothing waiting for this worker.")
        return 0
    for r in sorted(show, key=lambda x: x.get("queued_at") or ""):
        mark = "" if r.get("status") == "pending" else "  [done]"
        print("\n  • %s%s" % (r.get("what"), mark))
        if r.get("why"):
            print("      why deferred: %s" % r["why"])
        if r.get("requires"):
            print("      requires: %s" % ", ".join(r["requires"]))
        if claim_is_live(r, now):
            print("      CLAIMED by %s at %s" % (r.get("claimed_by"), r.get("claimed_at")))
        print("      queued %s · id %s" % (r.get("queued_at"), r.get("id")))
    print("\n" + "=" * 72)
    print("  Claim:     python3 scripts/deferred.py --claim <id>")
    print("  Mark done: python3 scripts/deferred.py --done <id>")
    print("  ⛔ Sends are never queued here — the candidate sends those directly, at the laptop.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
