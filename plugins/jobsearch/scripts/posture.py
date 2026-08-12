#!/usr/bin/env python3
"""What may this run do? Reads the ACTIVE posture and reports its budget.

WHY THIS EXISTS (2026-08-05)
----------------------------
The owner: *"i want to support multiple tiers, that could be a configuration of the user so it
works according to their budget."*

Cost in this system is almost entirely **runs per day x agents per run**. Deterministic sweeps are
free at any tier. So the tier is not a mode in the code — it is two numbers and a permission list,
and the engine reads them rather than hard-coding a cadence.

⭐ A RUN MUST ASK THIS BEFORE SPAWNING AN AGENT. The prompt cannot know which tier it is on, and
"just one quick research pass" is exactly how a token budget dies quietly.

Postures ship as DATA in `config.json` (`search.postures`), so a user can retune one or add their
own without touching the engine. The engine reads the knobs, never the name.

    python3 scripts/posture.py              # what am I allowed to do?
    python3 scripts/posture.py --may research
    python3 scripts/posture.py --cron       # the cron line this posture implies

Advisory; always exits 0 except for an explicit --may miss (exit 1) so a run can branch on it.
Python 3.9+, stdlib only.
"""

import argparse
import json
import os
import sys

import os, sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root as _profile_root

ROOT = _profile_root()


def load():
    path = os.path.join(ROOT, "config.json")
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError) as e:
        return None, None, "cannot read config.json (%s)" % e
    search = cfg.get("search") or {}
    name = search.get("posture")
    postures = search.get("postures") or {}
    if not name:
        return None, None, "config.search.posture is unset"
    if name not in postures:
        # A named posture with no definition is worse than none: the run cannot tell whether it
        # is permitted to do anything, and the safe reading is the cheapest one.
        return name, None, ("posture %r is not defined in config.search.postures (have: %s)"
                            % (name, ", ".join(sorted(postures)) or "none"))
    return name, postures[name], None


def main():
    ap = argparse.ArgumentParser(description="What may this run do?")
    ap.add_argument("--may", metavar="CAPABILITY",
                    help="Exit 0 if the active posture permits it unattended, 1 if not. "
                         "e.g. sweeps, linkedin, research, drafting")
    ap.add_argument("--cron", action="store_true", help="Print the cron line this posture implies.")
    args = ap.parse_args()

    name, p, err = load()

    if err:
        # Fail SAFE, and say so loudly. An unreadable budget must not read as an unlimited one.
        if args.may:
            print("POSTURE UNKNOWN (%s) — refusing %r. Treating as MINIMAL." % (err, args.may))
            return 1
        print("⚠️ POSTURE UNKNOWN — %s" % err)
        print("   Falling back to the cheapest reading: deterministic sweeps only, no agents.")
        return 0

    if args.cron:
        print(p.get("cron", ""))
        return 0

    if args.may:
        allowed = args.may in (p.get("unattended") or [])
        print("%s: %r is %s unattended on posture %r"
              % ("OK" if allowed else "REFUSED", args.may,
                 "PERMITTED" if allowed else "NOT permitted", name))
        if not allowed:
            print("   Queue it instead:  python3 scripts/deferred.py --add \"...\" --why \"posture %s\"" % name)
        return 0 if allowed else 1

    print("POSTURE — %s" % name)
    print("=" * 70)
    print("  runs per day        %s" % p.get("runs_per_day", "?"))
    print("  cron                %s" % p.get("cron", "?"))
    print("  max agents per run  %s" % p.get("max_agents_per_run", "?"))
    print("  unattended          %s" % ", ".join(p.get("unattended") or ["(nothing)"]))
    if p.get("_for"):
        print("\n  %s" % p["_for"])
    print("\n  ⭐ Deterministic sweeps are FREE at every tier and always run:")
    print("     alert_sweep · meeting_check · check_followups · channels_due · gates · dashboard")
    print("\n  Check one:  python3 scripts/posture.py --may research")
    return 0


if __name__ == "__main__":
    sys.exit(main())
