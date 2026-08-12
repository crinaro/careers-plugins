#!/usr/bin/env python3
"""Flag drafts/letters that have ALREADY BEEN SENT OR USED but are still shown as pending.

⭐ WHY THIS EXISTS (2026-08-03). The candidate, looking at the dashboard: *"on your move, i'm still seeing
the drafts for <an employer> (what's wrong)"*. He was right. He had sent the email and
applied with the cover letter attached; both facts were correctly written into `data/*.jsonl`, and
**both entries were still sitting in `drafts.md` / `cover_letters.md` as "awaiting approval."**

The failure is structural, not careless. Recording a send touches the JSON; retiring the markdown
entry is a SECOND, SEPARATE step, and anything that depends on remembering a second step
eventually does not happen. It is the same shape as the lock bug fixed earlier the same day
(`runlock.py --run`), and the same shape as the stale-dashboard bug before that.

**What it costs when it slips:** the candidate's Your Move panel is his queue. An item that is actually
DONE sitting in it is worse than noise — he either re-does the work, or he starts distrusting the
panel, and a queue he distrusts stops being a queue.

Heuristic and deliberately conservative: it matches a pending entry's heading against contact
names and company names that already carry a `sent` outreach row or a `submitted`/`rejected`
application. It reports SUSPECTS, not verdicts, and **always exits 0** — this is run-start hygiene
and must never wedge an unattended run. `validate_data.py` remains the only hard gate.

⚠️ A legitimately half-sent entry is normal: a connection request can be out while the follow-up
message waits on acceptance. Say so IN THE HEADING (e.g. "PART A SENT · part B pending") and this
check will still flag it — read the flag, confirm the pending half is real, and move on.

Python 3.9+, stdlib only.
"""

import json
import os
import re
import sys

import os, sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root as _profile_root

ROOT = _profile_root()
OPPS = os.path.join(ROOT, "data", "opportunities.jsonl")
FILES = ("drafts.md", "cover_letters.md")


def load_opps():
    rows = []
    if not os.path.exists(OPPS):
        return rows
    with open(OPPS, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    return rows


def sent_markers(opps):
    """-> list of (label, why) for anything already sent or submitted."""
    out = []
    for r in opps:
        for o in r.get("outreach") or []:
            if o.get("status") == "sent" and o.get("to"):
                out.append((o["to"], "outreach sent %s (%s)"
                            % (o.get("date", "?"), o.get("medium", "?"))))
        for a in r.get("applications") or []:
            if a.get("status") in ("submitted", "acknowledged", "rejected", "advanced"):
                out.append((r.get("title", ""), "application %s %s"
                            % (a.get("status"), a.get("date", "?"))))
    return out


def pending_headings(path):
    """-> [(lineno, heading)] for '## ' entries. A '_..._' status note is NOT pending."""
    p = os.path.join(ROOT, path)
    if not os.path.exists(p):
        return []
    out = []
    with open(p, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            if line.startswith("## ") and not line.startswith("## ⚠️ Questions"):
                out.append((i, line[3:].strip()))
    return out


def norm(s):
    return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())


def main():
    opps = load_opps()
    markers = sent_markers(opps)
    hits = []
    for path in FILES:
        for lineno, heading in pending_headings(path):
            h = norm(heading)
            for label, why in markers:
                lab = norm(label)
                if len(lab) < 5:
                    continue
                # Match on a distinctive multi-word label (a person's name or a role title).
                parts = [w for w in lab.split() if len(w) > 3]
                if len(parts) >= 2 and all(w in h for w in parts[:2]):
                    hits.append((path, lineno, heading, label, why))
                    break

    print("Sent-but-still-pending check — drafts.md / cover_letters.md")
    if not hits:
        print("\n  Clean. Nothing shown as pending has already been sent or submitted.")
        return 0
    print("\n  ⚠️ %d entr%s look ALREADY SENT/USED but still render as pending on Your Move:\n"
          % (len(hits), "y still" if len(hits) == 1 else "ies still"))
    for path, lineno, heading, label, why in hits:
        print("  %s:%d" % (path, lineno))
        print("     %s" % heading[:96])
        print("     matched: %r — %s" % (label, why))
    print("\n  If the send is real, RETIRE the entry: replace it with a one-line `_..._` status")
    print("  note (the sent-and-logged rule) so it leaves the candidate's queue. If only HALF is sent")
    print("  (e.g. connection request out, follow-up message waiting on acceptance), that is")
    print("  legitimate — say so in the heading and leave it.")
    return 0   # advisory only, never wedges a run


if __name__ == "__main__":
    sys.exit(main())
