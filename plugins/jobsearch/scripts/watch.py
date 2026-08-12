#!/usr/bin/env python3
"""
The WATCHER — a frequent, strictly read-only sweep that reports findings to a queue.

WHY THIS EXISTS
---------------
The candidate, 2026-08-02:

    "How do we support the daily check running more often. The issue, I'm interacting in a
     session and it conflicts with another session running. Is there a way to have a global
     (coordinator concept) that's the interactive session and what happens in the daily today
     can run behind the scenes and executes more often and sends a message back to the
     coordinator (long running session)... I could run what's happening in the daily more often
     (say every 2 hours between 7am and 4pm)."

**The conflict is about WRITES, not reads.** Two sessions reading the mailbox is harmless; two
sessions rewriting `focus.md`, `log.md` and git is what clobbers. So the split is:

    WATCHER (this script, every 2h)   read-only. Finds things. Appends to data/inbox.jsonl.
                                      NEVER writes state, NEVER touches git, NEVER edits focus.md.
    COORDINATOR (the interactive      the ONLY writer. Drains the queue, decides, updates the
    session, or a daily run)          JSON, commits.

**A queue file beats a message**, which matters because `send_message` is explicitly unavailable
in scheduled-task runs and cannot deliver to them either. A file also survives session death, can
be drained hours later, and is idempotent — findings are keyed, so running every two hours does
not produce ten copies of the same alert.

By construction this script cannot conflict with anything: the only file it opens for writing is
`data/inbox.jsonl`, and only in append mode.

WHAT IT WATCHES
---------------
  * new alert-digest roles not already in the pipeline
  * meeting artifacts whose date is not in focus.md's This Week
  * inbound mail from a tracked contact after we last wrote to them (an unrecorded reply)
  * ATS receipts for applications still marked `submitted`

Usage:
    python3 scripts/watch.py                  # sweep and queue findings
    python3 scripts/watch.py --dry-run        # show what it would queue, write nothing
    python3 scripts/watch.py --since 4        # look back N hours (default 3)

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

try:
    from gmail_mcp_server import (
        Mailbox, configured_accounts, decode_header_value, CredentialError,
    )
except ImportError as exc:  # pragma: no cover
    sys.stderr.write("Run as `python3 scripts/watch.py` from the repo root: %s\n" % exc)
    sys.exit(2)

ROOT = _profile_root()
DATA = os.path.join(ROOT, "data")
INBOX = os.path.join(DATA, "inbox.jsonl")


def load(name):
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def existing_keys():
    """Every finding already queued, so a 2-hourly sweep doesn't re-report the same thing."""
    keys = set()
    if os.path.exists(INBOX):
        with open(INBOX, encoding="utf-8") as fh:
            for l in fh:
                if l.strip():
                    try:
                        keys.add(json.loads(l).get("key"))
                    except ValueError:
                        pass
    return keys


class Reader(object):
    """Read-only mailbox access. One connection per account, reused."""

    def __init__(self):
        self.boxes, self.errors = {}, []
        for acct in configured_accounts():
            try:
                mb = Mailbox(acct)
                mb.__enter__()
                self.boxes[acct] = mb
            except CredentialError as exc:
                self.errors.append("%s: %s" % (acct, exc))
            except Exception as exc:
                self.errors.append("%s: %s: %s" % (acct, type(exc).__name__, exc))

    def search(self, query, limit=30):
        out = []
        for acct, mb in self.boxes.items():
            try:
                for uid in reversed(mb.search(query)[-limit:]):
                    msg = mb.fetch_headers(uid)
                    if msg is None:
                        continue
                    out.append({
                        "account": acct, "uid": str(uid),
                        "date": decode_header_value(msg.get("Date")),
                        "from": decode_header_value(msg.get("From")),
                        "subject": decode_header_value(msg.get("Subject")),
                    })
            except Exception as exc:
                self.errors.append("%s: %s: %s" % (acct, type(exc).__name__, exc))
        return out

    def close(self):
        for mb in self.boxes.values():
            try:
                mb.__exit__(None, None, None)
            except Exception:
                pass


def main():
    ap = argparse.ArgumentParser(description="Read-only watcher; queues findings for the coordinator.")
    ap.add_argument("--since", type=int, default=3, help="Look-back window in hours (default 3).")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    now = datetime.datetime.now()
    opps = load("opportunities.jsonl")
    seen = existing_keys()
    findings = []

    def add(kind, key, summary, detail=None, opp_id=None, urgency="normal"):
        if key in seen:
            return
        seen.add(key)
        findings.append({
            "id": "%s-%s" % (now.strftime("%Y%m%dT%H%M"), key[:48]),
            "key": key, "kind": kind, "urgency": urgency,
            "found_at": now.isoformat(timespec="seconds"),
            "opp_id": opp_id, "summary": summary, "detail": detail,
            "status": "pending", "acked_at": None,
        })

    rd = Reader()
    days = max(1, (args.since + 23) // 24)

    # ---- 1. alert digests: roles we may not have -------------------------------
    for m in rd.search('(from:indeed OR from:linkedin OR from:ladders OR '
                       'subject:("new jobs" OR "job alert")) newer_than:%dd' % days, limit=20):
        add("alert", "alert:%s:%s" % (m["account"], m["uid"]),
            "Job-alert digest: %s" % (m["subject"] or "")[:90],
            "from %s | %s" % (m["from"], m["date"]))

    # ---- 2. meeting artifacts not in This Week ---------------------------------
    focus = ""
    fp = os.path.join(ROOT, "focus.md")
    if os.path.exists(fp):
        with open(fp, encoding="utf-8") as fh:
            t = fh.read()
        i = t.find("## 📅 This Week")
        if i >= 0:
            j = t.find("\n## ", i + 5)
            focus = t[i:j if j > 0 else len(t)]
    for m in rd.search('(subject:(Invitation OR "Updated invitation" OR "Appointment booked" OR '
                       '"Event accepted" OR interview) OR filename:ics) newer_than:%dd' % days,
                       limit=20):
        subj = m["subject"] or ""
        # ⭐ SENDER-BASED exclusion, deliberately NOT subject-based. LinkedIn's connection-request
        # notifier sends "You have an invitation", which the Invitation sweep matches — on
        # 2026-08-03 three of four MEETING findings were connection requests, diluting the one
        # queue kind that costs opportunities when it sits. invitations@linkedin.com never sends a
        # calendar invite, and these already appear under `alert`, so nothing is lost.
        # ⚠️ Do NOT narrow this sweep by SUBJECT — CLAUDE.md's hard rule is that a subject line can
        # be weeks stale while the newest message in the thread schedules something tomorrow.
        if "invitations@linkedin.com" in (m["from"] or "").lower():
            continue
        dates = set(re.findall(r"\b(20\d\d-\d\d-\d\d)\b", subj))
        unseen = [d for d in dates if d not in focus]
        add("meeting", "meet:%s:%s" % (m["account"], m["uid"]),
            "Meeting artifact: %s" % subj[:90],
            "from %s | %s%s" % (m["from"], m["date"],
                                " | date(s) not in This Week: %s" % ", ".join(unseen) if unseen else ""),
            urgency="high" if unseen else "normal")

    # ---- 3. inbound mail from a tracked contact we're awaiting ------------------
    awaiting = []
    for o in opps:
        for r in (o.get("outreach") or []):
            if r.get("status") == "sent" and r.get("outcome") == "awaiting" and r.get("to"):
                nm = re.split(r"\(|,", r["to"])[0].strip()
                if len(nm.split()) >= 2:
                    awaiting.append((o, r, nm))
    for o, r, nm in awaiting[:25]:
        for m in rd.search('from:"%s" newer_than:%dd' % (nm, days), limit=5):
            add("reply", "reply:%s:%s" % (m["account"], m["uid"]),
                "POSSIBLE REPLY from %s — row says awaiting" % nm,
                "%s | %s | opp %s" % (m["subject"], m["date"], o["id"]),
                opp_id=o["id"], urgency="high")

    # ---- 4. ATS movement on submitted applications -----------------------------
    for m in rd.search('(subject:("your application" OR "application status" OR interview OR '
                       '"next steps" OR "not selected" OR "no longer under consideration")) '
                       'newer_than:%dd' % days, limit=20):
        add("ats", "ats:%s:%s" % (m["account"], m["uid"]),
            "ATS/application mail: %s" % (m["subject"] or "")[:90],
            "from %s | %s" % (m["from"], m["date"]))

    rd.close()

    print("WATCHER — read-only sweep, %s (window %dh)" % (now.strftime("%Y-%m-%d %H:%M"), args.since))
    print("=" * 74)
    if rd.errors:
        print("!! INCOMPLETE COVERAGE: %s" % "; ".join(sorted(set(rd.errors))))
        print("   Findings below are PARTIAL. Do not read a zero as an absence.")
    if not findings:
        print("  Nothing new. (%d finding(s) already queued from earlier sweeps.)" % len(seen))
    for f in sorted(findings, key=lambda x: (x["urgency"] != "high", x["kind"])):
        flag = "⚠️ " if f["urgency"] == "high" else "   "
        print("  %s[%s] %s" % (flag, f["kind"], f["summary"]))
        if f["detail"]:
            print("        %s" % f["detail"][:110])

    if findings and not args.dry_run:
        # APPEND ONLY. This is the single file this script writes, and the reason it
        # cannot conflict with an interactive session or a daily run.
        with open(INBOX, "a", encoding="utf-8") as fh:
            for f in findings:
                fh.write(json.dumps(f, ensure_ascii=False) + "\n")
        print("\n  Queued %d finding(s) -> data/inbox.jsonl" % len(findings))
        print("  The COORDINATOR (your interactive session) drains it:")
        print("      python3 scripts/inbox.py")
    elif findings:
        print("\n  (--dry-run: nothing written)")

    print("\n  This sweep wrote NO state and touched NO git. The coordinator is the only writer.")
    return 1 if rd.errors else 0


if __name__ == "__main__":
    sys.exit(main())
