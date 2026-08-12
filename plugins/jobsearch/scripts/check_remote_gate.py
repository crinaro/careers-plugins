#!/usr/bin/env python3
"""
IS THE PUSH GATE ACTUALLY ON THE REMOTE? — verify, never assume.

WHY THIS EXISTS
---------------
The local `pre-push` hook cannot protect this repo. `.git/hooks/` is not version-controlled, so
**a fresh clone has no hook and can push freely** — verified 2026-08-04 by standing one up. A
cloud worker IS a fresh clone, so every "subagents never push" guard rested on something a new
environment does not have.

`.github/workflows/gates.yml` runs the eight credential-free gates on every push. **But a green
CI run blocks nothing.** GitHub Actions reports; it does not refuse. The gate only becomes a gate
when `gates / verify` is a REQUIRED status check under branch protection on the default branch.

**So this script exists because the repo has a standing rule against exactly the claim it would
otherwise be tempting to write:** *never repeat a claim about the state of the system by quoting a
tracker — the machine is the source of truth.* A doc line saying "branch protection is enabled"
is a tracker line. This asks GitHub.

    ⚠️ IT REPORTS UNKNOWN RATHER THAN GUESSING. If `gh` is missing or unauthenticated it says so
    and exits 0 — an advisory check that cannot see the answer must not manufacture one, in
    either direction. A false "protected" is worse than no check at all.

Usage:
    python3 scripts/check_remote_gate.py           # advisory; exit 0 unless definitely OFF
    python3 scripts/check_remote_gate.py --strict  # exit 1 unless definitely ON

Python 3.9+. Standard library only (shells out to `gh` when present).
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

import os, sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root as _profile_root

ROOT = _profile_root()
REQUIRED_CHECK = "verify"          # the job name in .github/workflows/gates.yml
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "gates.yml")


def repo_slug():
    r = subprocess.run(["git", "remote", "get-url", "origin"],
                       capture_output=True, text=True, cwd=ROOT)
    url = r.stdout.strip()
    if not url:
        return None
    for sep in ("github.com/", "github.com:"):
        if sep in url:
            return url.split(sep, 1)[1].removesuffix(".git")
    return None


def gh_available():
    if not shutil.which("gh"):
        return False, "gh is not installed"
    r = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    if r.returncode != 0:
        return False, "gh is installed but not authenticated"
    return True, ""


def default_branch(slug):
    """⭐ ASK THE REMOTE WHICH BRANCH IS DEFAULT — do not assume.

    This checked `master` unconditionally. The repo's default is `main`, so the API call 404'd,
    and 404 is mapped below to "no branch protection" — meaning the gate reported an UNPROTECTED
    branch for a repo whose protection it had never actually looked at. A confidently wrong
    answer, which is the failure mode this whole project is organised against, and it would have
    read as a real finding to anyone acting on it.

    Falls back to `main` rather than `master`: if the lookup fails we are guessing either way,
    and `main` is the default for every repo created since 2020.
    """
    r = subprocess.run(["gh", "api", "repos/%s" % slug, "--jq", ".default_branch"],
                       capture_output=True, text=True)
    name = (r.stdout or "").strip()
    return name if r.returncode == 0 and name else "main"


def protection(slug, branch=None):
    """Returns (state, detail). state is 'on' | 'off' | 'unknown'."""
    branch = branch or default_branch(slug)
    r = subprocess.run(
        ["gh", "api", "repos/%s/branches/%s/protection" % (slug, branch),
         "--jq", ".required_status_checks.contexts"],
        capture_output=True, text=True)
    if r.returncode != 0:
        err = (r.stderr or "").strip()
        # ⭐ THE PLAN WALL — found 2026-08-04. Branch protection is NOT AVAILABLE on a private
        # repo on the free plan: "Upgrade to GitHub Pro or make this repository public."
        # This is a distinct state from "not configured" and must not be reported as one:
        # nobody can fix it by clicking the setting, so telling them to is a dead end.
        # ⛔ AND MAKING THE REPO PUBLIC IS NOT AN OPTION — it holds comp figures, every
        # contact, resume addenda and the whole search history.
        if "Upgrade to GitHub Pro" in err or "403" in err:
            return "unavailable", ("branch protection is not available on a private repo on "
                                   "this plan (HTTP 403)")
        if "Branch not protected" in err or "404" in err:
            return "off", "no branch protection on %s" % branch
        return "unknown", err[:160] or "gh api failed"
    try:
        contexts = json.loads(r.stdout or "[]") or []
    except json.JSONDecodeError:
        return "unknown", "unexpected gh output"
    hit = [c for c in contexts if REQUIRED_CHECK in c]
    if hit:
        return "on", "required checks: %s" % ", ".join(contexts)
    return "off", "protected, but %r is NOT a required check (has: %s)" % (
        REQUIRED_CHECK, ", ".join(contexts) or "none")


def main():
    ap = argparse.ArgumentParser(description="Is the push gate enforced on the remote?")
    ap.add_argument("--strict", action="store_true",
                    help="Exit 1 unless protection is definitely ON. Use before granting a "
                         "cloud worker push credentials.")
    args = ap.parse_args()

    print("REMOTE PUSH GATE")
    print("=" * 70)

    # ⭐ adr-012 self-guard: under a DECLARED `local-only` profile there is no push step, so
    # there is nothing for branch protection to gate. That is NOT APPLICABLE — a distinct,
    # honest state, exactly like the plan wall below. Reporting OFF here would send someone to
    # configure protection for a remote the profile deliberately does not push to; failing
    # under --strict would be worse, because the reason --strict exists (gate before granting
    # push credentials) cannot arise when nothing may push at all. Only a verified `ok` +
    # `local-only` takes this exit — every other verdict (including an unreadable declaration)
    # falls through to the existing probe, which fails open into UNKNOWN, never into a guess.
    try:
        import sync as _sync
        _verdict, _mode, _state, _notes = _sync.resolve(ROOT)
    except Exception:
        _verdict, _mode = "unknown", None
    if _verdict == "ok" and _mode == "local-only":
        print("  NOT APPLICABLE — this profile declares `sync.mode: local-only` (adr-012).")
        print("  There is no push step, so there is no push gate to verify, in --strict mode")
        print("  or otherwise. If this profile should push: `git remote add origin <url>`,")
        print("  then `sync.py --set remote`, then run this again.")
        return 0

    have_wf = os.path.exists(WORKFLOW)
    print("  workflow file            %s" % ("present" if have_wf else "MISSING — CI cannot run"))
    if not have_wf:
        return 1

    slug = repo_slug()
    print("  repo                     %s" % (slug or "could not read origin"))

    ok, why = gh_available()
    if not ok or not slug:
        print("  protection               UNKNOWN — %s" % (why or "no origin"))
        print("\n  Not guessing. An advisory check that cannot see the answer must not")
        print("  manufacture one; a false 'protected' is worse than no check at all.")
        print("\n  TO CHECK BY HAND:")
        print("    Settings > Branches > Branch protection rules > the default branch")
        print("    'Require status checks to pass before merging' must be ON, with")
        print("    'gates / verify' selected.")
        return 1 if args.strict else 0

    state, detail = protection(slug)
    print("  protection               %s — %s" % (state.upper(), detail))

    if state == "on":
        print("\n  ENFORCED. A clone with no hooks still cannot land invalid state.")
        return 0

    if state == "unavailable":
        print("\n  BRANCH PROTECTION IS NOT PURCHASABLE ON THIS PLAN FOR A PRIVATE REPO.")
        print("  ⛔ Do NOT 'fix' this by making the repo public — it holds comp figures, every")
        print("  contact, resume addenda and the whole search history.")
        print("\n  ⭐ AND THAT IS LARGELY FINE — DECIDED 2026-08-04, do not re-propose it.")
        print("  A fork-and-PR topology was designed and then DROPPED after asking what it")
        print("  actually buys over the write API. Answer: almost nothing. record.py writes")
        print("  atomically and validates, validate_data enforces schema and referential")
        print("  integrity, and the 186 tests all TRAVEL WITH ANY CLONE. CI here catches what")
        print("  slips past them.")
        print("\n  The remaining gap is narrow and honest: CI DETECTS invalid state after a")
        print("  push rather than refusing it, and nothing guards a force-push. For a")
        print("  single-user private repo that is an acceptable trade — reverting is cheap.")
        print("\n  Revisit ONLY on evidence: a worker that actually pushes something you would")
        print("  rather have reviewed. Then the options are GitHub Pro, or a separate remote")
        print("  the worker can write and origin it cannot. Not before.")
        return 0

    print("\n  ⚠️ NOT ENFORCED. CI reports, but nothing refuses a push.")
    print("  Until this is on, DO NOT give a cloud worker push credentials — it would be a")
    print("  fresh clone with no local hook and no remote gate, i.e. no gate at all.")
    print("\n  TO ENABLE:")
    print("    Settings > Branches > Add branch protection rule > master")
    print("    ✓ Require status checks to pass before merging")
    print("    ✓ select 'gates / verify'")
    print("    (leave PR-required OFF if you want to keep pushing to master directly —")
    print("     the status check still gates the push.)")
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
