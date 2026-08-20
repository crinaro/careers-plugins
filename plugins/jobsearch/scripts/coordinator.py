#!/usr/bin/env python3
"""
COORDINATOR — the one command to run when the candidate opens their working session.

WHY THIS EXISTS
---------------
The candidate, 2026-08-02: *"What is the overall process flow for the different jobs and what session
should I interact with? If we have the concept of a coordinator session, how does it get updates
from the dependent sessions so it runs its processes?"*

## The answer to "which session do I talk to"

**The COORDINATOR — the long-running session you open at the start of the week.** You never talk
to a scheduled run; they are unattended and cannot receive a message (the platform blocks
`send_message` both to and from them). This script is what that session runs first.

## The flow

    ┌─ COORDINATOR ─────────────── you, one long-running session, all week ────────────┐
    │  Runs this at start. Does NOT hold the write lock. Drains the queue. Decides.     │
    │  Decides. It is the ONLY session you interact with.                               │
    └──────────────────────────────────────────────────────────────────────────────────┘
              ▲ notification per run                        │ drains
              │ (notifyOnCompletion)                        ▼
    ┌─ search-daily ── every 2h, 7am-4pm ──────────┐   ┌─ data/inbox.jsonl ────────────┐
    │  READ phase  -> UNLOCKED, runs alongside     │──▶│  append-only findings queue    │
    │                 your open session             │   │  keyed: never duplicated      │
    │  WRITE phase -> lock, edit, commit, release  │   └───────────────────────────────┘
    │                 SECONDS, not hours            │
    └──────────────────────────────────────────────┘
    ┌─ search-strategy-weekly ── Sun 11am ─────────┐
    │  audit: funnel yield, process debt -> zero,  │
    │  reconcile vs mail/LinkedIn, proposals       │
    └──────────────────────────────────────────────┘

## How the coordinator gets UPDATES — the honest mechanics

There are exactly two channels, and only one of them is a push:

1. **PUSH — `notifyOnCompletion`.** A scheduled task notifies **the session that subscribed to
   it**, each time it finishes. `update_scheduled_task(taskId="search-daily",
   notifyOnCompletion=True)` **replaces any prior subscriber**, so only ever one session is
   notified. That is the only real push available.

   **⚠️ ONLY A REGULAR SESSION CAN CLAIM IT — a scheduled run cannot.** Verified 2026-08-02:
   attempting it from inside a scheduled run is refused outright, *"Can't subscribe a
   scheduled-task run session to completion notifications — it ends when the run does."* So
   **The candidate must run this from THEIR interactive session**; no automation can set it up for them, and
   it must be re-claimed whenever they start a fresh coordinator session.

2. **PULL — `data/inbox.jsonl`.** Everything a background run found. This is the durable half:
   it survives a closed app, a dead session, and a missed notification, and findings are keyed so
   nothing is ever queued twice. **A notification can be lost; the queue cannot.**

**There is no message-passing between sessions here.** `send_message` cannot reach or come from a
scheduled run. So state moves through FILES — the queue, `data/*.jsonl`, and `handoff.md`, the
session-handoff letter. That is a feature: a file is inspectable, replayable, and does not depend on any
session still being alive.

Usage:
    python3 scripts/coordinator.py          # stand up the session; does NOT take the lock
    python3 scripts/coordinator.py --take   # hold it all session (rarely correct)

**⭐ IT DOES NOT TAKE THE LOCK — corrected 2026-08-03.** the candidate: *"Why can't it run concurrently
with the coordinator session? That was the main purpose."* Holding it from startup blocked every
background run for as long as the candidate had a session open; on 08-03 an idle session held it 28 minutes
and cost the 07:00 run outright. **Lock around each WRITE and release immediately:**
`runlock.py --take "coordinator write" --wait 60` ... edit ... `--release`.

Python 3.9+. Standard library only.
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys

import os, sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root as _profile_root
import profile as _profile
ENGINE_SCRIPTS = os.path.dirname(os.path.realpath(__file__))

ROOT = _profile_root()
DATA = os.path.join(ROOT, "data")


def load(name):
    p = os.path.join(DATA, name)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def rule(t):
    print("\n" + "=" * 76)
    print(t)
    print("=" * 76)


def main():
    ap = argparse.ArgumentParser(description="Stand up the coordinator session.")
    # ⭐⭐ DOES NOT TAKE THE LOCK AT STARTUP — reverted 2026-08-03, the candidate: "Why can't it run
    # concurrently with the coordinator session? That was the main purpose."
    #
    # Taking it here was right about ONE thing (every writer must take the lock) and wrong about
    # WHEN. Held from session start, it blocked every background run for as long as the candidate had a
    # session open. Measured 08-03: their session went idle at 07:02 still holding; at 07:09 it had
    # been held 28 min and nothing could write until the staleness expiry ~09:11. The 07:00 run
    # was lost and 09:00 would have been too. That is the opposite of running every 2 hours.
    #
    # The invariant survives in a better form: **take it around each WRITE, not for the session.**
    # Startup only reports. --take is kept for the rare case of a long deliberate edit sequence.
    ap.add_argument("--take", action="store_true",
                    help="Hold the lock from startup. RARELY CORRECT — it blocks every background "
                         "run for as long as you hold it. Prefer taking it per-write.")
    ap.add_argument("--no-take", action="store_true",
                    help="Deprecated no-op; not taking the lock is now the default.")
    args = ap.parse_args()

    print("COORDINATOR — %s" % datetime.datetime.now().strftime("%A %Y-%m-%d %H:%M"))
    print("=" * 76)
    print("You are in the session you interact with. Scheduled runs are unattended and")
    print("cannot be messaged — they hand work over through data/inbox.jsonl.")

    # adr-012: the resolved sync mode, in the one session the user actually reads — an
    # undeclared or mismatched state surfaces HERE, not at some run's push step hours later.
    try:
        import sync as _sync
        _v, _m, _st, _ = _sync.resolve(ROOT)
        if _v == "ok" and _m == "remote":
            print("Sync mode: remote — end-of-run runs commit, then push.")
        elif _v == "ok":
            print("Sync mode: local-only — commit only. SINGLE COPY on this machine: no")
            print("off-machine backup, and no cloud or second-machine worker can attach.")
        else:
            print("⚠️ SYNC %s — runs are COMMIT-ONLY until this is resolved. Run:" % _v.upper())
            print("      python3 scripts/sync.py --status")
    except Exception:
        print("⚠️ SYNC state unreadable — python3 scripts/sync.py --status")

    # ---- 1. is anyone else writing right now? ---------------------------------
    rule("1. WRITE LOCK — is a background run mid-flight?")
    out = subprocess.run([sys.executable, os.path.join(ENGINE_SCRIPTS, "runlock.py"),
                          "--status"], capture_output=True, text=True)
    print("  " + (out.stdout.strip().replace("\n", "\n  ") or "?"))
    if args.take:
        t = subprocess.run([sys.executable, os.path.join(ENGINE_SCRIPTS, "runlock.py"),
                            "--take", "coordinator session"], capture_output=True, text=True)
        print("  " + t.stdout.strip().replace("\n", "\n  "))
        print("  ⚠️ You are holding the lock for the WHOLE session. Every background run will")
        print("     degrade until you --release. Release as soon as the edit sequence is done.")
    else:
        print("\n  NOT taking the lock — background runs are free to work alongside you.")
        print("  ⭐ TAKE IT AROUND EACH WRITE INSTEAD, then release straight away:")
        print("       python3 scripts/runlock.py --take \"coordinator write\" --wait 60")
        print("       ...edit data/*.jsonl, handoff.md, log.md; commit...")
        print("       python3 scripts/runlock.py --release")
        print("  Holding it all session is what lost the 07:00 run on 2026-08-03.")

    # ---- 1b. did anything move under this session since it last looked? --------
    rule("1b. STALENESS — has a background run written since you last looked?")
    ch = subprocess.run([sys.executable, os.path.join(ENGINE_SCRIPTS, "changed.py"),
                         "--as", "coordinator"], capture_output=True, text=True)
    print("  " + ch.stdout.strip().replace("\n", "\n  ")[:900])
    subprocess.run([sys.executable, os.path.join(ENGINE_SCRIPTS, "changed.py"), "--mark",
                    "--as", "coordinator"],
                   capture_output=True)

    # ---- 2. what did the background runs find? --------------------------------
    # ⭐ inbox.jsonl is a LOG, not a document: an `--ack` APPENDS an `_ack` record rather than
    # rewriting the finding, so the original row keeps `status: "pending"` forever. Reading the
    # raw rows therefore counts every finding ever queued as outstanding — this reported
    # "16 run-summary pending ⚠️ 9 URGENT" against a queue inbox.py showed as fully drained
    # (2026-08-05). Replay the acks, exactly as inbox.py does, so the two agree by construction.
    try:
        sys.path.insert(0, ENGINE_SCRIPTS)
        import inbox as _ib
        inbox = _ib.replay(_ib.load())
    except Exception:
        inbox = load("inbox.jsonl")
    pending = [r for r in inbox if r.get("status") == "pending"]
    rule("2. INBOX — what the background runs handed you")
    if not pending:
        print("  Nothing pending (%d handled historically)." % len(inbox))
    else:
        by_kind = {}
        for r in pending:
            by_kind.setdefault(r["kind"], []).append(r)
        # run-summary first: it is what a background run did while the candidate was away.
        for kind, rows in sorted(by_kind.items(), key=lambda kv: (kv[0] != "run-summary", kv[0])):
            urgent = sum(1 for r in rows if r.get("urgency") == "high")
            print("  %-10s %d pending%s" % (kind, len(rows),
                                            "  ⚠️ %d URGENT" % urgent if urgent else ""))
        print("\n  Full detail + what to do with each:  python3 scripts/inbox.py")

    # ---- 2b. work that was deferred because it needs THIS machine ---------------
    # Everything here is laptop-bound (Chrome, Keychain, the repo). A session that could not
    # reach the Mac queued it; this session is ON the Mac, so it can clear it. See
    # docs/architecture.md §3d. Sends never appear here — deferred.py refuses them.
    try:
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import deferred as _d
        waiting = [r for r in _d.replay(_d.load()) if r.get("status") == "pending"]
    except Exception:
        waiting = []
    if waiting:
        rule("2b. NEEDS THIS MACHINE — deferred laptop-bound work")
        for r in waiting:
            print("  • %s" % (r.get("what") or "")[:70])
            if r.get("why"):
                print("      %s" % r["why"][:66])
        print("\n  You are on the laptop now, so this is the session that can clear it.")
        print("  Detail: python3 scripts/deferred.py")

    # ---- 3. what needs the candidate ----------------------------------------------------
    rule("3. NEEDS YOU — decisions nothing can proceed without")
    opps = load("opportunities.jsonl")
    mine = [o for o in opps if o.get("next_action_owner") == _profile.owner_token()
            and o.get("status") in ("active-pursuit", "needs-resolution", "in-motion")]
    companies = {c["id"]: c.get("name", c["id"]) for c in load("companies.jsonl")}
    if not mine:
        print("  No role decisions are blocked on you.")
    for o in sorted(mine, key=lambda x: x.get("next_action_date") or "9999"):
        by = o.get("next_action_date") or "no date"
        print("  • [%s] %s — %s" % (by, companies.get(o.get("company_id"), "?")[:28],
                                    (o.get("title") or "")[:40]))
    # dev #93 — the cross-cutting asks are a store, not a focus.md section.
    try:
        import your_move as _ym
        open_asks = _ym.open_asks(load("asks.jsonl"))
    except Exception:
        open_asks = []
    if open_asks:
        n_sys = sum(1 for a in open_asks if a.get("kind") == "system")
        print("\n  Your Move carries %d open ask(s) (%d system/tooling) — see the dashboard."
              % (len(open_asks), n_sys))

    # ---- 3b. open QUESTIONS from the JD fit analysis ---------------------------
    # ⭐⭐ ADDED 2026-08-03. The candidate: "does the coordinator know to suggest a draft a nudge to
    # <a recruiter> for today?" It did NOT — this file mentioned `fit` zero times, so 28 open questions
    # produced by the fit analysis were invisible to the one session the candidate works in. Same failure
    # as the dashboard that morning: analysis written but never surfaced is analysis nobody has.
    #
    # ACT-BY FIRST, and anything due today or overdue is called out, because a dated question is
    # the only kind that can be missed by waiting. <a recruiter>'s out-of-office said she returns
    # Monday August 3; that was sitting in the question text as prose, which nothing can sort.
    rule("3b. OPEN QUESTIONS — from the JD fit analysis")
    qs = []
    for o in opps:
        for r in ((o.get("fit") or {}).get("requirements") or []):
            if r.get("question_status") == "open" and r.get("question_for_candidate"):
                qs.append((r.get("act_by") or "9999-99-99", o, r))
    today = datetime.date.today().isoformat()
    due = [x for x in qs if x[0] <= today]
    dated = [x for x in qs if today < x[0] < "9999-99-99"]
    if not qs:
        print("  None open.")
    else:
        print("  %d open (%d dated, %d DUE TODAY OR OVERDUE)." % (len(qs), len(dated) + len(due), len(due)))
        # key ONLY — tuples carrying dicts blow up when two act_by dates tie.
        for by, o, r in sorted(due + dated, key=lambda x: x[0])[:6]:
            cname = companies.get(o.get("company_id"), o.get("company_id") or "?")
            flag = "‼️ DUE" if by <= today else "  " + by
            print("\n  %s  %s" % (flag, cname[:40]))
            q = r["question_for_candidate"]
            print("      %s" % (q[:150] + ("..." if len(q) > 150 else "")))
        undated = len(qs) - len(due) - len(dated)
        if undated:
            print("\n  + %d undated question(s) — see the dashboard's JD fit section." % undated)
        print("\n  Full detail:  python3 scripts/fit_report.py --gaps")

    # ---- 4. is the machinery healthy? -----------------------------------------
    rule("4. GATES — is the machinery sound?")
    for name, label in (("validate_data.py", "data integrity"),
                        ("check_rule_homes.py", "no rule lost / CLAUDE.md budget"),
                        ("check_profile_leakage.py", "config is single-source")):
        # ⚠️ test_checks.py is NOT here. It is the maintainer regression suite and does not
        # ship, so on an installed plugin this checklist reported it FAILING every run with
        # no way to clear it (#1). A gate a user cannot satisfy is not a gate.
        rc = subprocess.run([sys.executable, os.path.join(ENGINE_SCRIPTS, name)],
                            capture_output=True, text=True).returncode
        print("  %-26s %s" % (label, "OK" if rc == 0 else "❌ FAILING — fix before writing"))
    # dev #133 / public #22 — the published dashboard is a deliverable with its own drift: a
    # publish that lost a version-conflict race used to be dropped SILENTLY, leaving the
    # candidate reading a stale published view as current. Checked here mechanically so every
    # coordinator startup sees it, whatever prompt loaded. The fix is a republish, not a
    # write-freeze, so the message says exactly that.
    rc = subprocess.run([sys.executable,
                         os.path.join(ENGINE_SCRIPTS, "check_dashboard_fresh.py"),
                         "--publish-state"], capture_output=True, text=True).returncode
    print("  %-26s %s" % ("published dashboard",
                          "OK" if rc == 0 else
                          "❌ BEHIND the repo — republish (check_dashboard_fresh.py --fix, "
                          "Artifact tool, then --stamp-published)"))

    rule("HOW UPDATES REACH YOU")
    print("  PUSH  notifyOnCompletion — a scheduled run notifies THE SUBSCRIBING SESSION when it")
    print("        finishes. ⚠️ CLAIM IT NOW, from THIS session — ask me to run:")
    print("            update_scheduled_task(taskId='search-daily', notifyOnCompletion=True)")
    print("        ONLY A REGULAR SESSION CAN — a scheduled run is refused ('it ends when the")
    print("        run does'), so no automation can set this up for you. Re-claim it each time")
    print("        you start a new coordinator session; it replaces any prior subscriber.")
    print("  PULL  data/inbox.jsonl — the durable half. Survives a closed app, a dead session,")
    print("        and a missed notification. A notification can be lost; the queue cannot.")
    print("  STALE python3 scripts/changed.py — 'did the data move under me?'. ⚠️ A SESSION IS")
    print("        TURN-DRIVEN: it cannot wake itself or poll. This answers when ASKED, so the")
    print("        convention is CHECK BEFORE YOU WRITE. A notification says a run finished; it")
    print("        does not tell your session the ground it is reasoning from has moved.")
    print("\n  There is NO session-to-session messaging: `send_message` cannot reach or come from")
    print("  a scheduled run. State moves through files, which is why it is replayable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
