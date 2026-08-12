#!/usr/bin/env python3
"""Did the scheduled runs actually DO anything? (Not: did the scheduler fire them?)

⭐⭐ THE DISTINCTION THIS EXISTS FOR — the candidate noticed it before the system did.
On 2026-08-06 `search-daily` reported `lastRunAt` 09:08 and the scheduler considered it a
success. It had left **no footprint at all**: no `log.md` entry, no inbox post, no commit. The
07:08 run had done all three. The 09:08 run fired, died early, and updated `lastRunAt` on its way
out — most likely at its first script call, because the engine pointer was aimed at a per-session
plugin copy that had gone away.

    ⭐ `lastRunAt` RECORDS THAT A RUN STARTED, NOT THAT IT ACCOMPLISHED ANYTHING. A run that dies
      in its first ten seconds is indistinguishable, from the scheduler's side, from one that
      swept two mailboxes and found nothing. **And "no new findings" looks exactly like "no runs"
      to the person reading the summary** — which is why this went unnoticed until a human asked.

So the scheduler is not the source of truth here; the repo is. This script asks the only question
that matters: **when did a run last leave evidence?**

⚠️ It cannot see `lastRunAt` — that lives behind an MCP tool no script can call. That is
deliberate division of labour: this reports the FOOTPRINT, the coordinator compares it against
`list_scheduled_tasks`, and **a gap between the two is the finding.** Neither half is conclusive
alone, and the coordinator skill says so.

Advisory: ALWAYS exits 0, so it can never wedge an unattended run.

Usage:
    python3 check_runs.py            # human summary
    python3 check_runs.py --json     # for the coordinator to compare against the scheduler

Python 3.9+. Standard library only.
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root

try:
    import journal as _journal
except Exception:                                  # pragma: no cover - journal is optional
    _journal = None

# `## 2026-08-06 (Thu, ~07:08–07:35 AM PDT) — Daily run …`
LOG_ENTRY = re.compile(r"^##\s*(\d{4}-\d{2}-\d{2})\b(.*)$", re.M)


def footprints(root):
    """Every dated trace a run leaves, newest first. Three independent sources on purpose —
    a run can fail after writing one and before writing another, and the gap localises it."""
    out = {"log_entries": [], "inbox_posts": [], "latest": None}

    path = os.path.join(root, "log.md")
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        for m in LOG_ENTRY.finditer(text):
            if "run" in m.group(2).lower():
                out["log_entries"].append({"date": m.group(1), "head": m.group(2).strip()[:80]})
    except OSError:
        pass
    out["log_entries"] = list(reversed(out["log_entries"]))[:10]

    path = os.path.join(root, "data", "inbox.jsonl")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                when = str(r.get("posted_at") or r.get("at") or r.get("date") or "")
                if when:
                    out["inbox_posts"].append({"at": when, "kind": str(r.get("kind") or "")})
    except OSError:
        pass
    out["inbox_posts"].sort(key=lambda x: x["at"], reverse=True)
    out["inbox_posts"] = out["inbox_posts"][:10]

    # ⭐ GitHub #7 — THE THIRD STATE. A run can advance `lastRunAt` while never creating a
    # session at all: no log entry, no commit, nothing. From outside that is identical to a quiet
    # run, and every automated signal reads green. It was caught only because a human noticed the
    # sweep's effects were absent and went looking for the session.
    #
    # The journal's `start` event is the evidence a session EXISTED, written before any work.
    # With it, the three states finally separate:
    #
    #     lastRunAt advanced, no `start`          -> NEVER STARTED        (#7)
    #     `start` with no `end`                   -> started and died     (#4)
    #     `start` + `end`, footprint may be empty -> ran; quiet is normal
    #
    # ⚠️ This is NOT a fix for the scheduler recording a run that did not happen — that is Claude
    # Code's, and nothing here can change it. What is ours is refusing to treat `lastRunAt` as
    # evidence that a run occurred, because it is not evidence of that.
    starts = []
    if _journal is not None:
        try:
            recs = _journal.read(root)
            starts = sorted((r for r in recs if r.get("event") == "start"),
                            key=lambda r: r.get("at") or "", reverse=True)
        except Exception:
            starts = []
    out["run_starts"] = [{"run_id": r.get("run_id"), "at": r.get("at"), "kind": r.get("kind")}
                         for r in starts[:10]]
    out["last_start"] = starts[0].get("at") if starts else None

    dates = [e["date"] for e in out["log_entries"]] + \
            [p["at"][:10] for p in out["inbox_posts"] if len(p["at"]) >= 10]
    out["latest"] = max(dates) if dates else None
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = profile_root()
    fp = footprints(root)

    if args.json:
        print(json.dumps(fp, indent=1, sort_keys=True))
        return 0

    print("RUN FOOTPRINT — what the runs actually LEFT BEHIND\n")
    if not fp["log_entries"] and not fp["inbox_posts"]:
        print("  ⚠️ No run footprint found at all. Either nothing has run, or every run is dying")
        print("     before it writes. Both are serious; the scheduler cannot tell you which.")
        return 0

    print("  most recent evidence: %s" % (fp["latest"] or "unknown"))
    print("\n  log.md run entries (newest first):")
    for e in fp["log_entries"][:5]:
        print("    %s  %s" % (e["date"], e["head"]))
    if not fp["log_entries"]:
        print("    (none)")
    print("\n  inbox posts (newest first):")
    for p in fp["inbox_posts"][:5]:
        print("    %s  %s" % (p["at"], p["kind"]))
    if not fp["inbox_posts"]:
        print("    (none)")

    print("\n  last journalled START: %s" % (fp.get("last_start") or "none recorded"))
    for s2 in fp.get("run_starts", [])[:3]:
        print("    %s  %s" % (s2.get("at"), s2.get("run_id")))

    print("\n  ⭐ NOW COMPARE WITH THE SCHEDULER — `list_scheduled_tasks`. THREE STATES:")
    print("     lastRunAt newer than any START      -> THE RUN NEVER STARTED. No session was")
    print("                                            created and nothing executed. (issue #7)")
    print("     a START with no matching end        -> it began and died; `journal.py --unfinished`")
    print("                                            has the findings it managed to record.")
    print("     START + end, footprint empty        -> it ran and the day was quiet. Normal.")
    print("\n     ⚠️ `lastRunAt` IS NOT EVIDENCE THAT A RUN OCCURRED. It advances even when no")
    print("     session is created, so a run can be missed every morning with every automated")
    print("     signal still green. The START record is the evidence; the scheduler is not.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
