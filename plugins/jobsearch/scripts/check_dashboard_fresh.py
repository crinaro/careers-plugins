#!/usr/bin/env python3
"""
Is the dashboard STALE — does it predate the state it is supposed to show?

WHY THIS EXISTS
---------------
The candidate, 2026-08-03: *"If the daily search ran just after 11am, why did the coordinator not update
the dashboard? Especially since my conversations should have caused it to update. It pushed items
to drafts, but not the dashboard, why?"*

**Because the coordinator had no dashboard step at all.** `generate_dashboard.py` appeared in
`the `jobsearch:daily-run` skill` and `the `jobsearch:weekly-review` skill` — and **zero times** in
`.claude/skills/coordinator/SKILL.md`.

That is the worst possible place for the gap, because the coordinator is the session the candidate actually
works in, and CLAUDE.md's own rule says **"the candidate reads the full text off the dashboard, not the
transcript."** So the one session whose entire output he consumes visually was the one session
that never published. Measured that morning: `drafts.md` was rewritten at 10:58, 11:02, 11:08,
11:13 and 11:14, while `dashboard.html` sat untouched since 10:51 — five rounds of drafting he
could not see.

**Why a script and not a line in the prompt.** The prompts already carried the step and it still
went missing, because the step lived in the runs and the writing happened in the session. A rule
that depends on the right prompt being loaded fails exactly when a different prompt is loaded.
This compares mtimes and does not care who was supposed to remember.

    ⚠️ mtime, not content. If a source is newer than the dashboard, the dashboard cannot be
    showing it. The converse is not proven — a fresh dashboard may still be wrong for other
    reasons, which is why the standing habit is to GREP THE OUTPUT after generating.

Usage:
    python3 scripts/check_dashboard_fresh.py          # exit 1 if stale
    python3 scripts/check_dashboard_fresh.py --fix    # regenerate, then re-check

Python 3.9+. Standard library only.
"""

import argparse
import os
import subprocess
import sys
import time

import os, sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root as _profile_root
ENGINE_SCRIPTS = os.path.dirname(os.path.realpath(__file__))

ROOT = _profile_root()
DASH = os.path.join(ROOT, "dashboard.html")
ARTIFACT = os.path.join(ROOT, "dashboard_artifact.html")

# Everything the dashboard renders. If one of these moved, the dashboard is behind it.
SOURCES = [
    "focus.md", "drafts.md", "cover_letters.md", "network.md",
    "data/opportunities.jsonl", "data/companies.jsonl", "data/channels.jsonl",
]


def mtime(p):
    return os.path.getmtime(p) if os.path.exists(p) else 0


def stale():
    """[(source, how_many_seconds_newer)] — empty means the dashboard is current."""
    d = min(mtime(DASH), mtime(ARTIFACT))
    if not d:
        return [(s, 0) for s in SOURCES if os.path.exists(os.path.join(ROOT, s))]
    out = []
    for s in SOURCES:
        m = mtime(os.path.join(ROOT, s))
        if m > d:
            out.append((s, int(m - d)))
    return out


def main():
    ap = argparse.ArgumentParser(description="Is the dashboard behind its sources?")
    ap.add_argument("--fix", action="store_true", help="Regenerate, then re-check.")
    args = ap.parse_args()

    bad = stale()
    if args.fix and bad:
        print("Regenerating (%d source(s) newer)..." % len(bad))
        r = subprocess.run([sys.executable, os.path.join(ENGINE_SCRIPTS, "generate_dashboard.py")],
                           capture_output=True, text=True)
        print("  " + (r.stdout.strip().splitlines() or ["?"])[-1])
        if r.returncode:
            print("  !! generate_dashboard.py failed:\n" + r.stderr.strip()[:400])
            return 1
        bad = stale()

    if not bad:
        print("DASHBOARD CURRENT — no source is newer than the generated files.")
        print("  (Freshness is not correctness: still GREP THE OUTPUT for what you added.)")
        return 0

    print("⚠️  DASHBOARD IS STALE — %d source(s) are newer than what was generated." % len(bad))
    print("=" * 72)
    for s, secs in sorted(bad, key=lambda x: -x[1]):
        mins = secs // 60
        print("  %-32s newer by %s" % (s, "%d min" % mins if mins else "%d sec" % secs))
    print("\n  the candidate reads the full text of drafts and letters OFF THE DASHBOARD, not the")
    print("  transcript. A stale dashboard means work he cannot see — on 2026-08-03 that was")
    print("  five rounds of outreach drafting between 10:58 and 11:14.")
    print("\n  Fix:  python3 scripts/check_dashboard_fresh.py --fix")
    print("  Then publish with the Artifact tool, passing dashboard_artifact_url.txt as `url`.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
