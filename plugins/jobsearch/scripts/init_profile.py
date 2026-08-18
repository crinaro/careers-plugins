#!/usr/bin/env python3
"""Scaffold a NEW user's job-search profile. The deterministic half of onboarding.

WHY THIS EXISTS (2026-08-05)
----------------------------
The owner: *"the goal is to support this running on the lowest Claude subscription and someone
could have a job search assistant."* Onboarding has two halves, and only one of them needs a model:

  * SCAFFOLDING — create the files, the schema-valid empty stores, the config skeleton, the
    directory layout. Pure mechanism. **This script. Costs nothing.**
  * ELICITATION — read their resume, ask what is missing, learn the facts they would not think to
    write down. Genuinely conversational. **The `onboarding` skill.**

Doing the mechanical half in Python is not an optimisation, it is the difference between an
onboarding a low-tier user can afford and one they cannot (ADR-008).

⚠️ THIS SCRIPT NEVER TOUCHES CREDENTIALS. Mailbox passwords go in the OS keychain by the user's
own hand; the engine must not handle them (CLAUDE.md standing rule). It writes a checklist, not a
secret.

⚠️ IT REFUSES TO OVERWRITE. A populated profile is someone's search history. `--force` is
deliberately absent: if a file exists, this says so and stops.

Usage:
    python3 scripts/init_profile.py --check          # what exists, what is missing
    python3 scripts/init_profile.py --scaffold       # create what is missing
    python3 scripts/init_profile.py --scaffold --posture full

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
DATA = os.path.join(ROOT, "data")

# The empty stores. Every one is a valid (empty) JSONL — validate_data.py must pass on a fresh
# install, or a new user's first gate run fails and they conclude the system is broken.
STORES = ("opportunities.jsonl", "companies.jsonl", "channels.jsonl", "messages.jsonl",
          "inbox.jsonl", "pending_actions.jsonl", "asks.jsonl", "commitments.jsonl")

DIRS = ("data", "kb", "call_preps", "drafts_assets")

USER_SKELETON = {
    "_comment": "WHO YOU ARE. The onboarding skill fills this from your resume and a short "
                "conversation. Every value here is read by scripts - never retyped into prose.",
    # ⭐ Shape verified against profile.py's actual reads (issue #34, part 1): `_render()` reads
    # `user()["identity"]["full_name"/"phone"/"city"/"primary_email"/"linkedin"]`, and `main()`
    # reads `profile["identity"]["availability"]`. A flat "name"/"location"/"contact" shape
    # KeyErrors on the first load. `make_fixture.py` confirms this nested "identity" object is
    # the canonical form a real profile carries.
    "identity": {
        "full_name": "",
        "city": "",
        "phone": "",
        "primary_email": "",
        "linkedin": "",
        "availability": "",
    },
    "mailboxes": [],
    "narrative_sources": {"resume": "resume.md", "projects": "projects.md",
                          "strategy": "strategy.md", "network": "network.md"},
}

CONFIG_SKELETON = {
    "_comment": "HOW THE SEARCH BEHAVES. Tune this; do not edit the engine.",
    "search": {
            "posture": "economy",
            "_posture_note": "WHICH TIER YOUR BUDGET SUPPORTS. Pick a name from `postures` below, or add your own - the engine reads the knobs, not the name. Cost scales with runs_per_day x max_agents_per_run; deterministic sweeps are free at any tier. See docs/adr-008-economy-tier.md.",
            "postures": {
                    "minimal": {
                            "runs_per_day": 1,
                            "cron": "0 8 * * *",
                            "max_agents_per_run": 0,
                            "unattended": [
                                    "sweeps"
                            ],
                            "_for": "The lowest tier, or a quiet week. Deterministic sweeps only: mailbox digests, calendar artifacts, silence detection, gates, dashboard. Zero model fan-out. Everything expensive is on demand."
                    },
                    "economy": {
                            "runs_per_day": 2,
                            "cron": "0 8,15 * * *",
                            "max_agents_per_run": 1,
                            "unattended": [
                                    "sweeps",
                                    "linkedin"
                            ],
                            "_for": "DEFAULT FOR NEW INSTALLS. Adds the LinkedIn sweep, which is where the outreach funnel lives, at one agent per run. A reply can wait ~8h."
                    },
                    "standard": {
                            "runs_per_day": 3,
                            "cron": "0 8,12,16 * * *",
                            "max_agents_per_run": 2,
                            "unattended": [
                                    "sweeps",
                                    "linkedin",
                                    "research"
                            ],
                            "_for": "Adds unattended research on genuinely NEW roles. Suits an active search on a mid tier."
                    },
                    "full": {
                            "runs_per_day": 5,
                            "cron": "0 7,9,11,13,15 * * *",
                            "max_agents_per_run": 5,
                            "unattended": [
                                    "sweeps",
                                    "linkedin",
                                    "research",
                                    "drafting"
                            ],
                            "_for": "Everything unattended, including auto-drafting replies. Assumes real token headroom; this is what the original installation runs."
                    }
            }
    },
    "sync": {
        "mode": "local-only",
        "_mode_note": "adr-012: the profile is always a git repository; this declares whether "
                      "end-of-run pushes. A new profile starts local-only. To push: `git remote "
                      "add origin <url>`, then `sync.py --set remote` (validated, never by hand).",
    },
    # ⭐ "org_structure_is_not_a_filter" (issue #34, part 1): profile.py:302 reads exactly this
    # key. The old name inverted the sense AND the spelling. True here reproduces the old
    # default's actual behavior (org structure was NOT applied as a filter out of the box).
    "targets": {"titles": [], "org_structure_is_not_a_filter": True},
    "geography": {"commute_anchors": [], "radius_minutes": 60, "relocation_open_to": [],
                  "remote_ok": True},
    "compensation": {
        "_basis_note": "FLOORS ARE BASE SALARY. A band whose TOP is below the applicable floor is "
                       "removed, not flagged. Undisclosed comp is KEPT - it is the first question, "
                       "not a filter.",
        # ⭐ `tiers` is a LIST of tier objects, not a dict (issue #34, part 1). profile.py's
        # `tiers()` does `{t["setting"]: t for t in ...}` and `screen_comp()`/`main()` read
        # `t["floor"]` unconditionally — a dict of {name: None} TypeErrors on the first read,
        # and a `None` floor still crashes `t["floor"] // 1000` in `main()`. Floor 0 reproduces
        # the OLD scaffold's intent ("nothing is screened out until a real floor is set") without
        # crashing: everything clears a $0 floor. `make_fixture.py` confirms the list-of-objects
        # shape, and "local-onsite" (hyphen) is the setting name `effective_setting()` returns.
        "tiers": [
            {"setting": "remote", "floor": 0, "basis": "base"},
            {"setting": "hybrid", "floor": 0, "basis": "base"},
            {"setting": "local-onsite", "floor": 0, "basis": "base"},
            {"setting": "relocation", "floor": 0, "basis": "base"},
        ],
    },
    "positioning": {"lead_with": "", "scope_boundaries": []},
    "communications": {
        "default_sequence": ["linkedin-connection-note", "email-cold"],
        # ⭐ `last_resort` and `message_requirements` are LISTS (issue #34, part 1 - found while
        # loading the scaffold through profile.py per the issue's own instruction, beyond its
        # named 5). `main()` does `", ".join(cm["last_resort"])` and
        # `" + ".join(cm["message_requirements"])`; a bare string silently joins by CHARACTER
        # instead of raising, and the old "every_message" key name KeyErrors outright.
        "last_resort": ["linkedin-inmail"],
        "message_requirements": ["make the fit case", "ask for a specific next step"],
        # ⭐ `constraints_by_medium` (issue #34, part 1): `medium_constraints()` and `main()` both
        # read this exact key name (the old "limits" dict was never read by anything). Values are
        # unchanged from the old "limits" dict, just reshaped into {max_chars|max_words: N}.
        "constraints_by_medium": {
            "linkedin-connection-note": {"max_chars": 300},
            "linkedin-message": {"max_words": 120},
            "linkedin-inmail": {"max_words": 120},
            "email-cold": {"max_words": 200},
            "email-reply": {"max_words": 150},
        },
        "_constraints_note": "connection-note is CHARACTERS; the rest are WORDS.",
    },
    "writing": {
        "banned_characters": ["—"],
        "_banned_note": "The em-dash is the AI tell people notice. Also avoid 'not just X but Y', "
                        "'not only... but also', reflexive tricolons, and delve/landscape/realm/"
                        "showcase/seamless.",
        "us_english": True,
        "cover_letter_target_body_words": 320,
        "cover_letter_max_pages": 1,
        # ⭐ `email_signature_template` / `cover_letter_header_template` are LISTS of {placeholder}
        # lines (issue #34, part 1). `profile.py`'s `_render()` formats each line against
        # identity fields; the old "cover_letter_header": "" was never read by anything, and
        # `email_signature_template` did not exist at all - `main()` KeyErrors calling
        # `email_signature()` on a bare scaffold.
        "email_signature_template": ["{full_name}", "{phone}"],
        "cover_letter_header_template": [
            "{full_name}",
            "{city} • {linkedin_display} • {primary_email} • {phone}",
        ],
    },
    "ats": {"sender_domains": [], "receipt_phrases": []},
}

CREDENTIAL_CHECKLIST = """# Credentials — YOUR hands, not the assistant's

The engine never stores, reads, or transmits a password. These are steps only you can do.

## 1. Mailbox (IMAP) — needed for application receipts and recruiter mail

Use an APP PASSWORD, never your account password.

- Gmail: enable 2FA, then create an app password at myaccount.google.com/apppasswords
- Store it in your OS keychain under service `claudesearch-imap`, account = your address:

      security add-generic-password -s claudesearch-imap -a you@example.com -w   # macOS

- Add the address to `user.json` -> `mailboxes`.

**If you skip this:** the assistant cannot see application receipts, recruiter replies, or
calendar invites. It still works from what you tell it, but you become the only sensor.

## 2. LinkedIn — needed for the outreach half of the funnel

There is no API. It requires a real logged-in browser on a desktop, via the Claude in Chrome
extension. Sign in to Chrome, install the extension, sign in to the extension.

**⚠️ This is the one part that cannot move to a phone or the cloud** (docs/adr-006). If you have
no desktop, everything else still works; LinkedIn simply will not be swept.

## 3. Nothing else

No API keys, no tokens, no third-party services. If something asks you for one, it is not part of
this system.
"""


def exists(rel):
    return os.path.exists(os.path.join(ROOT, rel))


def report():
    missing, present = [], []
    for d in DIRS:
        (present if os.path.isdir(os.path.join(ROOT, d)) else missing).append(d + "/")
    for s in STORES:
        (present if exists(os.path.join("data", s)) else missing).append("data/" + s)
    for f in ("user.json", "config.json", "resume.md", "projects.md", "CREDENTIALS.md"):
        (present if exists(f) else missing).append(f)
    return present, missing


def main():
    ap = argparse.ArgumentParser(description="Scaffold a new job-search profile.")
    ap.add_argument("--check", action="store_true", help="Report what exists; change nothing.")
    ap.add_argument("--scaffold", action="store_true", help="Create only what is missing.")
    ap.add_argument("--posture", choices=("economy", "full"), default="economy",
                    help="economy (default) suits the lowest subscription tier; see ADR-008.")
    args = ap.parse_args()

    present, missing = report()

    if not args.scaffold:
        print("PROFILE SCAFFOLD — %s" % ROOT)
        print("=" * 74)
        print("  present (%d):" % len(present))
        for p in present:
            print("    %s" % p)
        print("\n  missing (%d):" % len(missing))
        for m in missing:
            print("    %s" % m)
        if missing:
            print("\n  Create them:  python3 scripts/init_profile.py --scaffold")
        else:
            print("\n  Nothing to scaffold. The conversational half is next:")
            print("  invoke the `onboarding` skill to fill user.json/config.json from your resume.")
        return 0

    created = []
    for d in DIRS:
        p = os.path.join(ROOT, d)
        if not os.path.isdir(p):
            os.makedirs(p, exist_ok=True)
            created.append(d + "/")
    for s in STORES:
        p = os.path.join(DATA, s)
        if not os.path.exists(p):
            open(p, "w", encoding="utf-8").close()   # valid empty JSONL
            created.append("data/" + s)

    cfg = dict(CONFIG_SKELETON)
    cfg["search"]["posture"] = args.posture

    for name, payload in (("user.json", USER_SKELETON), ("config.json", cfg)):
        p = os.path.join(ROOT, name)
        if os.path.exists(p):
            print("  SKIP %s — already exists, refusing to overwrite" % name)
            continue
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        created.append(name)

    p = os.path.join(ROOT, "CREDENTIALS.md")
    if not os.path.exists(p):
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(CREDENTIAL_CHECKLIST)
        created.append("CREDENTIALS.md")

    # adr-012: a new profile is a git repository FROM THE FIRST MOMENT, seeded local-only
    # (the config skeleton above carries the declaration). Explicit paths only, and best-effort:
    # a failed commit here is healed by migrate.py's m_0_17_0, which commits any repo with no
    # HEAD — so nothing below may abort the scaffold.
    import shutil as _sh
    import subprocess as _sp
    if not _sh.which("git"):
        print("  ⚠️ git is not on PATH — the profile was scaffolded but is NOT a repository.")
        print("     Nothing can commit, so no run can persist state. Install git first.")
    else:
        r = _sp.run(["git", "-C", ROOT, "rev-parse", "--is-inside-work-tree"],
                    capture_output=True, text=True)
        if not (r.returncode == 0 and r.stdout.strip() == "true"):
            if _sp.run(["git", "-C", ROOT, "init", "-q"], capture_output=True,
                       text=True).returncode == 0:
                created.append(".git/ (adr-012: the profile is always a git repository)")
        if _sp.run(["git", "-C", ROOT, "rev-parse", "-q", "--verify", "HEAD"],
                   capture_output=True).returncode != 0:
            paths = [x for x in ("config.json", "user.json", "CREDENTIALS.md") + DIRS
                     if os.path.exists(os.path.join(ROOT, x))]
            _sp.run(["git", "-C", ROOT, "add", "--"] + paths, capture_output=True)
            ident = []
            if not (_sp.run(["git", "-C", ROOT, "config", "user.email"], capture_output=True,
                            text=True).stdout or "").strip():
                ident = ["-c", "user.name=jobsearch-init", "-c", "user.email=init@localhost"]
            c = _sp.run(["git", "-C", ROOT] + ident + ["commit", "-q", "-m",
                        "scaffold: new job-search profile (adr-012: repo from the first moment)"],
                        capture_output=True, text=True)
            if c.returncode == 0:
                created.append("initial commit (%d path(s), each named)" % len(paths))
            else:
                print("  ⚠️ initial commit failed (%s) — migrate.py will retry it."
                      % (c.stderr.strip() or "unknown")[:120])

    print("SCAFFOLD COMPLETE — posture: %s" % args.posture)
    print("=" * 74)
    for c in created:
        print("  created  %s" % c)
    if not created:
        print("  Nothing was missing.")
    print("""
NEXT, and this half needs a conversation:

  1. Put your resume in this directory (any format you can paste or a .md file).
  2. Invoke the `onboarding` skill. It reads the resume, fills user.json and config.json,
     builds resume.md and projects.md, and asks the handful of questions a resume never
     answers - comp floors, what you would relocate for, what you will NOT do.
  3. Do CREDENTIALS.md yourself. The assistant must never hold a password.

⚠️ Comp tier floors default to $0 until you set them. Nothing is screened out on a $0 floor, so
   the pipeline will happily fill with roles you would never take.""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
