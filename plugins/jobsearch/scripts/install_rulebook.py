#!/usr/bin/env python3
"""Copy the engine's rulebook into the user's profile, where it is actually loaded.

⭐ THE PROBLEM THIS SOLVES — a plugin CANNOT ship project context.
`claude plugin tag` says it plainly: *"CLAUDE.md at the plugin root is not loaded as project
context."* Claude Code loads `CLAUDE.md` from the **working directory**, and a run's working
directory is the user's PROFILE, never the plugin. So the rulebook that every rule in this system
depends on reaches a session only if a copy exists in the profile.

Until now each installation solved that by hand — a symlink, or a bespoke sync script.

⭐ THE SOURCE IS `RULEBOOK.md` AT THE PLUGIN ROOT; IT INSTALLS AS THE PROFILE'S `CLAUDE.md`.
The engine-side file is a TEMPLATE and is deliberately not named CLAUDE.md — a CLAUDE.md at the
plugin root is never loaded as project context (the validator warns on exactly that), and naming
a template after the job it cannot do from that location misled two separate reviews into filing
it as inert. The name changes at install time; the content does not.

⚠️⚠️ AND A SYMLINK IS THE ONE SOLUTION THAT MUST NOT BE USED. A committed symlink pointing out of
its own repo **dangles in every clone**, and a dangling symlink reads as an ABSENT FILE — so the
session starts with no rulebook, no agents, and NO ERROR. That is exactly how a job-search session
came up with nothing on 2026-08-05. This script therefore copies a REAL FILE, always.

## What it writes, and why the stamp matters

A copy plus a provenance line naming the engine version it came from. Without the stamp there is
no way to tell a current copy from one made three releases ago — and a stale rulebook is worse
than an absent one, because it is read as authoritative. `--check` compares the stamp and says
whether a re-copy is needed; `doctor.py` can call it.

Usage:
    python3 install_rulebook.py            # copy into the profile (walks up from cwd)
    python3 install_rulebook.py --check    # exit 1 if missing or stale, print why
    python3 install_rulebook.py --dest DIR # explicit profile directory

Python 3.9+. Standard library only.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root, engine_root, is_tracked_fixture, is_engine_root

STAMP = "<!-- installed-from: jobsearch %s | do not edit here; edit the plugin's RULEBOOK.md and re-run install_rulebook.py -->"
MARKER = "<!-- installed-from: jobsearch "

# ⭐ dev #81 — a ~38KB CLAUDE.md landed in the checked-in tests/fixtures/profile/ TWICE during
# the 0.24.0 work. profile_root() legitimately resolves to that fixture when nothing else looks
# like a profile (test_checks.py's own fallback, deliberately, for READS) or when
# CLAUDESEARCH_ROOT is explicitly pointed there — but this script writes, and the fixture is
# GENERATED (make_fixture.py) and must never be hand- or self-edited. `_root.is_tracked_fixture`
# already carried this exclusion for the remembered pointer (`_remember`); this script never
# inherited it. See that module's docstring for why the check stays narrower than
# `is_disposable_profile` — a tempfile.mkdtemp() scratch profile stays a legitimate write target.
_FIXTURE_REFUSAL = (
    "  ⛔ %s is the checked-in test fixture (or lives inside one), not a real profile — "
    "the rulebook writer refuses to read or write there. It is GENERATED, not hand-maintained; "
    "a self-heal write would be silent drift indistinguishable from the real thing.")

# ⭐ dev #87 — the sibling case. `profile_root()`'s own "return CWD" fallback can legitimately
# answer with the ENGINE'S OWN ROOT (a maintenance session, a CI runner, any run with no profile
# anywhere above it and nothing remembered) — and this script WRITES a stamped CLAUDE.md over
# whatever is at `dest_dir`. Landing there and writing would overwrite the engine's OWN
# maintenance rulebook (this repository's real, hand-maintained `CLAUDE.md`) with the profile
# template, in the one checkout every session here depends on. `refresh_if_stale()`'s "unmanaged"
# branch happens to catch this too (the real CLAUDE.md carries no install stamp) — but that
# safety is incidental to a stamp format, not a designed refusal, and `main()`'s direct-write path
# does not check for "unmanaged" at all. Refuse explicitly, the same shape as the fixture case.
_ENGINE_ROOT_REFUSAL = (
    "  ⛔ %s IS THE ENGINE'S OWN ROOT, not a profile — the rulebook writer refuses to read or "
    "write there. profile_root() found no profile anywhere above the working directory and "
    "nothing remembered, and fell back to the CWD, which is this engine checkout. Run this from "
    "inside a real profile, or pass --dest.")


def engine_version():
    path = os.path.join(engine_root(), ".claude-plugin", "plugin.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh).get("version") or "unversioned"
    except Exception:
        return "unversioned"


def source_text():
    src = os.path.join(engine_root(), "RULEBOOK.md")
    with open(src, "r", encoding="utf-8") as fh:
        return fh.read()


def installed_stamp(dest_file):
    try:
        with open(dest_file, "r", encoding="utf-8") as fh:
            head = fh.read(400)
    except OSError:
        return None
    i = head.find(MARKER)
    if i == -1:
        return ""            # a copy exists but was not made by this script
    return head[i + len(MARKER):].split("|")[0].strip()


def refresh_if_stale(dest_dir=None, apply_it=True):
    """Keep the installed rulebook current, unattended — returns (verdict, lines).

    ⭐⭐ WHY THIS EXISTS: A RULEBOOK INSTALL WAS "AND THEN THE USER RUNS THIS SCRIPT".
    That is the one thing this marketplace says a change may never be. Nothing called this
    script from any hook, so the only thing keeping a profile's rulebook current was
    somebody remembering — and on 2026-08-13 a live profile was found running rules from
    **0.17.0 while the engine was 0.21.0**. Four minors of rules the sessions never read,
    with no error anywhere, which is precisely the failure this file's own docstring names:
    a stale rulebook is worse than an absent one, because it is read as authoritative.

    Called from the SessionStart hook beside the migration and the install self-heal, so the
    three things that must track the running version now do so together.

    ⚠️ THE UNMANAGED CASE IS A REFUSAL, NOT A REPAIR. A CLAUDE.md with no provenance stamp
    was written by someone, not by this script. Overwriting it unattended would be silent
    data loss of the exact kind `SAFE APPLIES, DESTRUCTIVE REPORTS` forbids — so it says so
    and changes nothing. Same for a symlink, for the same reason.
    """
    dest_dir = dest_dir or profile_root()
    dest_file = os.path.join(dest_dir, "CLAUDE.md")
    version = engine_version()

    if is_tracked_fixture(dest_dir):
        return "fixture", [_FIXTURE_REFUSAL % dest_dir]
    if is_engine_root(dest_dir):
        return "engine-root", [_ENGINE_ROOT_REFUSAL % dest_dir]
    if not os.path.isdir(dest_dir):
        return "no-profile", []
    if os.path.islink(dest_file):
        return "symlink", [
            "  ⛔ %s is a SYMLINK — not replaced. Remove it and re-run install_rulebook.py."
            % dest_file]

    have = installed_stamp(dest_file)
    if have == version:
        return "current", []
    if have == "":
        return "unmanaged", [
            "  ⚠️ %s was not installed by this plugin, so it was NOT replaced." % dest_file,
            "     It may hold local edits. Move it aside and run install_rulebook.py to adopt",
            "     the shipped rulebook, or leave it and accept that engine rules are not loaded."]

    if not apply_it:
        return ("missing" if have is None else "stale"), [
            "  would install the rulebook into %s (have %s, engine %s)"
            % (dest_file, have or "nothing", version)]

    text = source_text()
    if MARKER in text[:400]:
        return "bad-source", ["  ⛔ the engine's RULEBOOK.md already carries a stamp; not installed"]
    try:
        tmp = dest_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(STAMP % version + "\n" + text)
        os.replace(tmp, dest_file)
    except OSError as e:
        return "failed", ["  ⚠️ could not write %s (%s) — rules stay at %s"
                          % (dest_file, e, have or "none")]
    return ("installed" if have is None else "refreshed"), [
        "  rulebook %s -> %s. ⚠️ It loads at SESSION START, so THIS session still has the old one."
        % (have or "none", version)]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="report staleness; write nothing")
    ap.add_argument("--dest", help="the profile directory (default: resolved from cwd)")
    args = ap.parse_args()

    dest_dir = args.dest or profile_root()
    dest_file = os.path.join(dest_dir, "CLAUDE.md")
    version = engine_version()

    if is_tracked_fixture(dest_dir):
        print(_FIXTURE_REFUSAL % dest_dir, file=sys.stderr)
        return 2

    if is_engine_root(dest_dir):
        print(_ENGINE_ROOT_REFUSAL % dest_dir, file=sys.stderr)
        return 2

    have = installed_stamp(dest_file)

    if args.check:
        if have is None:
            print("MISSING  %s\n  The session will start with NO rulebook and no error." % dest_file)
            return 1
        if have == "":
            print("UNMANAGED  %s\n  A CLAUDE.md exists but was not installed by this script — it "
                  "may be hand-written or a stale copy. Re-run without --check to replace it, or "
                  "move it aside first if it holds local edits." % dest_file)
            return 1
        if have != version:
            print("STALE  %s\n  installed from %s, engine is %s. A stale rulebook is worse than an "
                  "absent one: it is read as authoritative." % (dest_file, have, version))
            return 1
        print("OK  %s is current (jobsearch %s)." % (dest_file, version))
        return 0

    if os.path.islink(dest_file):
        # Refuse rather than overwrite: the link may be someone's deliberate dev setup, and
        # silently converting it would hide the change. Say what to do.
        print("⛔ %s is a SYMLINK. Remove it first, then re-run.\n"
              "   A symlink out of the repo dangles in every clone, and a dangling symlink reads "
              "as an absent file — the session starts with no rulebook and no error."
              % dest_file, file=sys.stderr)
        return 2

    if not os.path.isdir(dest_dir):
        print("No such directory: %s" % dest_dir, file=sys.stderr)
        return 2

    text = source_text()
    if MARKER in text[:400]:
        print("Refusing to install a copy as the source — %s already carries a provenance stamp."
              % os.path.join(engine_root(), "RULEBOOK.md"), file=sys.stderr)
        return 2

    with open(dest_file, "w", encoding="utf-8") as fh:
        fh.write(STAMP % version + "\n" + text)
    print("✅ Installed the rulebook into %s (from jobsearch %s)." % (dest_file, version))
    print("   ⚠️ It loads at SESSION START — this session still has the old one, if any.")
    print("   Edit the PLUGIN's RULEBOOK.md and re-run this; edits here are overwritten.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
