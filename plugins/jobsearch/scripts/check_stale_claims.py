#!/usr/bin/env python3
"""Find status claims in the trackers that have gone stale, and system-state
claims that must be re-verified against the machine before anyone repeats them.

Why this exists (2026-07-19): the weekly strategy review reported "apply the
wake_chrome fix - still unapplied after 4 days" as its #2 proposal. It was
wrong. The fix had landed on 2026-07-17 as a side effect of the repo move out
of ~/Documents, and the LaunchAgent had been firing cleanly at 06:58/13:58 ever
since. The stale sentence in focus.md ("not yet applied - awaiting the candidate's
go-ahead") was written 7/15, never revisited, and got read back as current fact.

Two distinct failure modes, both handled here:

  1. AGING CLAIMS - "awaiting the candidate", "pending approval", "sitting since 7/10".
     These decay silently. Nothing marks them stale; they just get re-read.

  2. SYSTEM-STATE CLAIMS - "the fix is not yet applied", "the script is broken",
     "the LaunchAgent never fires". These are assertions about the machine, and
     the machine is the source of truth, NOT the tracker. A tracker can only
     ever tell you what was true when someone typed it.

Type 2 is the dangerous one: it reads as researched fact, and it is checkable in
seconds. This script cannot verify them for you - it flags them so a human or
agent goes and looks.

Usage:
    python3 scripts/check_stale_claims.py              # default: 5-day threshold
    python3 scripts/check_stale_claims.py --days 3
    python3 scripts/check_stale_claims.py --quiet      # only exit code, for hooks

Exit codes: 0 = nothing flagged, 1 = stale claims found (advisory, not a failure).

Targets system Python 3.8, no third-party packages, so it runs unattended.
"""

import argparse
import os
import re
import sys
from datetime import datetime

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _root import profile_or_fixture as _pof
import profile as _profile


def _candidate_ref():
    """This candidate's own reference token, for matching "awaiting <name>" style claims in
    their own trackers. A previous version of the AGING pattern below spelled one specific
    candidate's own first name out as a literal in the regex — correct for exactly one
    installation and silently blind to the same claim in anyone else's focus.md."""
    try:
        ident = _profile.user()["identity"]
    except (OSError, KeyError):
        return "the-candidate"
    ref = ident.get("preferred_reference") or ident.get("display_name") or ""
    ref = ref.strip().lower()
    return re.escape(ref) if ref else "the-candidate"

# ⭐ Issue #34, part 3. `REPO` used to be derived from `__file__` — the ENGINE's own location.
# `focus.md`/`network.md`/`drafts.md` are the CANDIDATE's own trackers and live in the PROFILE,
# which is a different tree as a plugin. So this scanned the engine, found none of them, and
# `scan()`'s `except IOError: return aging, system` swallowed that silently — every run printed
# "Nothing flagged. Trackers look current." having read zero lines. Same root-resolution bug as
# check_pointers.py, same fix: resolve against the profile (falling back to the fixture so the
# gate is RUNNABLE in CI, matching every other gate's convention).
REPO = _pof()
# NOTE (2026-07-21): `opportunities.md` is deliberately NOT scanned. It was
# retired 2026-07-20, is frozen as a historical snapshot, and is not to be
# edited -- so every line it flagged was unfixable by construction and would
# have been re-reported every run forever. Role-level decay is now caught by
# check_followups.py against data/opportunities.jsonl, which IS live.
FILES = ["focus.md", "network.md", "drafts.md"]

# Claims that decay with time - someone is blocked, or something is unresolved.
_REF = _candidate_ref()
AGING = re.compile(
    r"awaiting (?:%s|his|your|approval|a reply|response|go-?ahead)" % _REF
    + r"|pending (?:%s|approval|his|your)" % _REF
    + r"|await(?:s|ing) (?:%s'?s )?(?:go-?ahead|nod|approval|decision|review)" % _REF
    + r"|blocked on %s" % _REF
    + r"|sitting since|has sat|hold expired"
    r"|still (?:unconfirmed|pending|silent|open|waiting)"
    r"|no (?:reply|response) (?:yet|since)"
    r"|to be (?:confirmed|scheduled)|TBD",
    re.I,
)

# Claims about the state of the SYSTEM - scripts, configs, jobs, files.
# These must be re-verified against the machine, never quoted from a tracker.
SYSTEM_SUBJECT = re.compile(
    r"wake_chrome|launchagent|launchctl|plist|cron|scheduled task"
    r"|\.sh\b|\.py\b|\.plist\b|script|gitignore|settings\.local"
    r"|dashboard\.html|artifact|filter|forwarding|extension|permission|TCC",
    re.I,
)
SYSTEM_STATE = re.compile(
    r"not (?:yet )?applied|never (?:applied|ran|fired|executed|worked)"
    r"|un(?:applied|fixed|resolved)|still (?:broken|failing|unapplied|blocked)"
    r"|fix (?:identified|not applied|pending)|doesn'?t work|is broken"
    r"|awaiting .{0,30}(?:go-?ahead|approval)|not in place|missing",
    re.I,
)

DATE = re.compile(r"20\d{2}-\d{2}-\d{2}")
SHORT_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})\b")


def newest_date(line, today):
    """Most recent date referenced in the line, as a date object, or None."""
    found = []
    for m in DATE.finditer(line):
        try:
            found.append(datetime.strptime(m.group(0), "%Y-%m-%d").date())
        except ValueError:
            pass
    for m in SHORT_DATE.finditer(line):
        month, day = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            try:
                d = datetime(today.year, month, day).date()
                # A "7/21" in the future is a scheduled date, not an age marker.
                if d <= today:
                    found.append(d)
            except ValueError:
                pass
    return max(found) if found else None


def trim(line, width=150):
    line = line.strip().lstrip("0123456789. ").strip()
    line = re.sub(r"\s+", " ", line)
    return line if len(line) <= width else line[: width - 1] + "…"


def scan(path, today, threshold):
    """Returns (aging, system, found). `found=False` means the file could not be opened at
    all — the caller must treat that as UNCHECKED, never as "scanned, nothing to flag"."""
    aging, system = [], []
    try:
        with open(os.path.join(REPO, path), "r", errors="replace") as fh:
            lines = fh.readlines()
    except IOError:
        return aging, system, False

    for n, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith(("#", "|---", "_")):
            continue

        is_system = bool(SYSTEM_SUBJECT.search(line) and SYSTEM_STATE.search(line))
        if is_system:
            system.append((path, n, trim(line), newest_date(line, today)))
            continue

        if AGING.search(line):
            d = newest_date(line, today)
            age = (today - d).days if d else None
            if age is None or age >= threshold:
                aging.append((path, n, trim(line), age))
    return aging, system, True


def main():
    ap = argparse.ArgumentParser(description="Flag stale tracker claims.")
    ap.add_argument("--days", type=int, default=5, help="age threshold (default 5)")
    ap.add_argument("--quiet", action="store_true", help="suppress output")
    args = ap.parse_args()

    today = datetime.now().date()
    all_aging, all_system, found = [], [], []
    for path in FILES:
        a, s, f = scan(path, today, args.days)
        all_aging += a
        all_system += s
        if f:
            found.append(path)

    # ⭐⭐ A SCAN THAT COVERED NOTHING IS A FAILURE, NEVER A PASS (issue #34, part 3 — same rule
    # as check_engine_purity.py / check_pointers.py). None of the declared tracker files being
    # openable means REPO resolved to the wrong root (or genuinely no profile is reachable, e.g.
    # CI with no fixture) — either way that is UNCHECKED, and must never be reported as the
    # trackers being clean. `--quiet` still exits nonzero, matching "something needs attention";
    # the verbose path stays advisory (exit 0, per the deliberate 2026-08-02 no-wedge rule) but
    # the TEXT must say NOT CHECKED, never "look current".
    if not found:
        if args.quiet:
            return 1
        print("Stale-claim check - %s (threshold: %d days)" % (today.isoformat(), args.days))
        print("\n  !! NOT CHECKED — none of %s could be opened under %s"
              % (", ".join(FILES), REPO))
        print("     This is a BROKEN GATE state, not current trackers. Either REPO resolved to")
        print("     the wrong root, or no profile/fixture is reachable from here at all.")
        print("     Do NOT read this as evidence the trackers are current.")
        return 0

    if args.quiet:
        return 1 if (all_aging or all_system) else 0

    print("Stale-claim check - %s (threshold: %d days)" % (today.isoformat(), args.days))

    if all_system:
        print("\n" + "=" * 72)
        print("SYSTEM-STATE CLAIMS - VERIFY AGAINST THE MACHINE BEFORE REPEATING")
        print("=" * 72)
        print("These assert something about a script, job, config or file. The\n"
              "machine is the source of truth, not the tracker. Go check, then\n"
              "correct the line. Do NOT carry these into a report unverified.\n")
        for path, n, text, d in sorted(all_system, key=lambda r: (r[0], r[1])):
            age = " [%d days old]" % (today - d).days if d else " [undated]"
            print("  %s:%d%s" % (path, n, age))
            print("      %s\n" % text)

    if all_aging:
        print("\n" + "=" * 72)
        print("AGING CLAIMS - dispose of each: chase, re-date, or close out")
        print("=" * 72 + "\n")
        undated = [r for r in all_aging if r[3] is None]
        dated = sorted([r for r in all_aging if r[3] is not None],
                       key=lambda r: -r[3])
        for path, n, text, age in dated:
            print("  %s:%d  [%d days]" % (path, n, age))
            print("      %s\n" % text)
        for path, n, text, _ in undated:
            print("  %s:%d  [NO DATE - cannot age it; add one]" % (path, n))
            print("      %s\n" % text)

    missing = [p for p in FILES if p not in found]
    if missing:
        print("\n  (not found, so not scanned: %s)" % ", ".join(missing))

    if not all_aging and not all_system:
        print("\nNothing flagged. Trackers look current (scanned: %s)." % ", ".join(found))
        return 0

    print("-" * 72)
    print("%d system-state claim(s) to verify, %d aging claim(s) to dispose of."
          % (len(all_system), len(all_aging)))
    # Advisory, like check_followups.py and check_sections.py: ALWAYS exit 0.
    # This used to return 1, which made it the odd one out among the three run-start
    # hygiene gates and meant a routine "you have claims to re-verify" could wedge an
    # UNATTENDED scheduled run (`cmd1 && cmd2` chains stop dead). Findings are reported
    # in the output, which is what the run reads. (Made consistent 2026-08-02.)
    return 0


if __name__ == "__main__":
    sys.exit(main())
