#!/usr/bin/env python3
"""Does this profile SYNC to a remote, or live on this machine alone? — declared, then verified.

⭐ WHY THIS EXISTS (adr-012-profile-sync-mode.md)
------------------------------------------------
The end-of-run steps said "commit, then push" unconditionally, which is correct for exactly one
kind of profile: a git repository with an origin remote. A profile without a remote hit the push
step and failed downstream of its cause; a profile that was a plain folder failed earlier and
worse. **Nothing declared which kind of profile this is, so every step that reached for the
remote was guessing.**

The rule, copied from `route.py` (which separated a channel's REQUIREMENT from the mechanism):
**the profile DECLARES its sync mode in `config.sync.mode`; this script verifies the declaration
against what the repository actually is, and refuses to resolve what it cannot read.** No script
or skill decides "is there a remote?" inline, ever — they ask this one.

## The vocabulary

MODES — the parseable values of `config.sync.mode`:

    remote        end-of-run is commit THEN push to origin. Requires an origin remote to exist.
    local-only    end-of-run is commit. There is no push step, and every reporting surface
                  (doctor, run summaries) says so rather than implying one happened.

**Undeclared is a third, LOUD state — never silently treated as either.** A run that pushes on a
guess can publish to a remote nobody chose; a run that silently skips the push looks identical
to one that persisted off-machine. Both are the missing-thing-reads-as-empty-thing failure this
project is organised against. `migrate.py` seeds the declaration from what the profile actually
is; until then, `--check` exits 1 and callers treat the run as commit-only.

## What local commits mean here

**Local commits happen in BOTH modes.** Git is present on every machine this plugin can install
on — the marketplace that delivers it is a git clone, and the platform refuses to install a
marketplace without git (verified 2026-08-10; evidence in the ADR). So the audit trail
(`compact.py`, `check_stale_claims.py`, what-changed-since-last-run) never degrades. What
`local-only` gives up is narrower and stated plainly by `--status`: no off-machine backup, and
no second machine or cloud worker can attach to the profile (the repo-is-the-bus topology of
adr-005 needs a remote to be a bus).

Usage:
    sync.py --status              # human-readable: declaration, reality, what it means
    sync.py --json                # machine-readable, for run summaries and doctor
    sync.py --check               # exit 1 unless the declaration exists and matches reality
    sync.py --set remote          # declare a mode; validated against reality BEFORE writing
    sync.py --set local-only
    sync.py --end-of-run          # the skills' post-commit step: push under `remote`, or print
                                  # the committed-locally line under `local-only`. Exit 1 means
                                  # THE WORK DID NOT PERSIST as declared -- the summary must
                                  # carry this script's output either way.

⚠️ Set the mode HERE, never by hand-editing config.json: `--set remote` refuses when there is no
origin remote — a declaration that cannot be honoured would fail at the next push, downstream of
its cause.

Python 3.9+. Standard library only.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _root import profile_root
from _atomic import write_json

MODES = ("remote", "local-only")

# The one line a `local-only` run summary must carry, verbatim (adr-012). It is what
# distinguishes a DECLARED no-push from a push that silently failed.
LOCAL_ONLY_LINE = "committed locally; not pushed — this profile declares `sync.mode: local-only`"


class SyncError(ValueError):
    """A sync declaration nobody can read. ⚠️ MUST BE LOUD: an unparseable mode silently treated
    as a default looks handled and is not — the exact failure `precondition.py` exists to
    prevent in drafts."""


def parse_mode(cfg):
    """The declared mode, or None when the profile has not declared one yet.

    None is deliberately distinct from invalid: an undeclared mode is the expected state of a
    profile the migration has not reached, and the caller reports it as pending. An INVALID
    mode is a typo that would otherwise fall through to a default and look honoured — refused.
    """
    raw = (cfg.get("sync") or {}).get("mode")
    if raw is None:
        return None
    val = str(raw).strip().lower()
    if val in MODES:
        return val
    raise SyncError("unreadable config.sync.mode %r — expected one of: %s"
                    % (raw, ", ".join(MODES)))


def git_state(root):
    """What the profile repository actually is. Returns a dict; never raises.

    {"git": bool git binary on PATH,
     "repo": bool this profile is a git work tree,
     "origin": str origin URL or ""}

    Reports rather than guesses: a missing git binary is its own loud state, not `repo: False`.
    """
    state = {"git": bool(shutil.which("git")), "repo": False, "origin": ""}
    if not state["git"]:
        return state
    try:
        r = subprocess.run(["git", "-C", root, "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True, timeout=15)
        state["repo"] = r.returncode == 0 and r.stdout.strip() == "true"
    except Exception:
        return state
    if state["repo"]:
        try:
            r = subprocess.run(["git", "-C", root, "remote", "get-url", "origin"],
                               capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                state["origin"] = r.stdout.strip()
        except Exception:
            pass
    return state


def load_config(root):
    path = os.path.join(root, "config.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise SyncError("no config.json at %s — this is not a profile" % root)
    except json.JSONDecodeError as e:
        raise SyncError("config.json at %s is unreadable: %s" % (root, e))


def save_config(root, cfg):
    """Atomic via `_atomic.write_json`: a half-written config.json breaks every reader, and the
    failure lands in the next unattended run rather than here. A truncating write inside a lock
    is still a truncating write."""
    write_json(os.path.join(root, "config.json"), cfg)


def resolve(root):
    """(verdict, mode, state, notes). verdict is one of:

    ok           declaration present and honourable; notes may still carry a warning
    undeclared   no config.sync.mode — migration pending; callers treat the run as commit-only
    mismatch     remote declared but no origin exists — the push step WILL fail; fix first
    no-repo      the profile is a plain folder — migration pending; nothing can commit
    no-git       git is not on PATH — nothing can commit, and this machine could not have
                 installed the plugin's marketplace, so something changed since install
    error        the declaration exists and cannot be read
    """
    state = git_state(root)
    try:
        mode = parse_mode(load_config(root))
    except SyncError as e:
        return "error", None, state, [str(e)]

    if not state["git"]:
        return "no-git", mode, state, [
            "git is not on PATH. Local commits and the audit trail are DOWN — this run",
            "cannot persist state. Marketplace installs require git, so its absence means",
            "this machine changed since the plugin was installed."]
    if not state["repo"]:
        return "no-repo", mode, state, [
            "this profile is a plain folder, not a git repository. Nothing can commit, so",
            "nothing this run learns will survive it in the audit trail. The engine",
            "migration (`migrate.py`) initialises the repository; run it, or a new session",
            "will offer to."]
    if mode is None:
        return "undeclared", None, state, [
            "config.sync.mode is not declared. Do NOT push on a guess and do NOT silently",
            "skip the push — treat this run as commit-only and say so in the summary.",
            "`migrate.py` seeds the declaration from what the profile is; `--set` declares",
            "it by hand."]
    if mode == "remote" and not state["origin"]:
        return "mismatch", mode, state, [
            "config.sync.mode is 'remote' but the repository has NO origin remote. The push",
            "step will fail downstream of this. Either `git remote add origin <url>` or",
            "declare `sync.py --set local-only`."]
    notes = []
    if mode == "local-only" and state["origin"]:
        notes.append("declared local-only, but an origin remote exists (%s). It will grow"
                     % state["origin"])
        notes.append("stale: nothing pushes to it. If that remote is meant to be current,")
        notes.append("declare `sync.py --set remote`.")
    return "ok", mode, state, notes


def set_mode(root, mode):
    """Validated BEFORE it is written — `remote` without an origin would be a declaration the
    next run cannot honour, failing at the push step downstream of the typo made here."""
    if mode not in MODES:
        raise SyncError("unknown mode %r; valid: %s" % (mode, ", ".join(MODES)))
    state = git_state(root)
    if not state["git"]:
        raise SyncError("git is not on PATH; cannot verify anything, so nothing is declared")
    if not state["repo"]:
        raise SyncError("this profile is not a git repository yet — run migrate.py first")
    if mode == "remote" and not state["origin"]:
        raise SyncError("cannot declare 'remote': no origin remote exists. "
                        "`git remote add origin <url>` first.")
    cfg = load_config(root)
    cfg.setdefault("sync", {})["mode"] = mode
    save_config(root, cfg)
    return state


def end_of_run(root):
    """The skills' post-commit step. The commit already happened (the skill's own `git commit`
    with explicit paths); this decides the PUSH half from the declaration, never from a guess.

    Exit 0: the run persisted AS DECLARED (pushed under `remote`, or committed-only under a
    declared `local-only`). Exit 1: it did not — a failed push is **NOT PERSISTED**, and an
    `undeclared`/`mismatch`/`error` state leaves the run **COMMIT-ONLY**, said out loud. The
    caller copies this output into the run summary verbatim: a run that persisted off-machine
    and one that did not are otherwise identical afterwards, which is gap G8's whole point.
    """
    verdict, mode, state, notes = resolve(root)

    if verdict == "ok" and mode == "local-only":
        print(LOCAL_ONLY_LINE)
        for line in notes:                      # the stale-origin warning, when one exists anyway
            print("  %s" % line)
        return 0

    if verdict == "ok" and mode == "remote":
        r = subprocess.run(["bash", os.path.join(HERE, "push.sh")],
                           cwd=root, capture_output=True, text=True)
        if r.returncode == 0:
            print("pushed to origin (%s)." % state["origin"])
            return 0
        reason = (r.stderr.strip() or r.stdout.strip() or "push failed").splitlines()[-1][:200]
        print("!! NOT PERSISTED — the push FAILED. The commit exists ONLY on this machine.")
        print("!! Carry `NOT PUSHED: %s` in the run summary." % reason)
        print("!! Do not report this run as synced, and do not retry blind: read the reason,")
        print("!! fix it (token minted? remote reachable?), then `sync.py --end-of-run` again.")
        return 1

    # undeclared / mismatch / error / no-repo / no-git: NEVER push on a guess. The run stays
    # COMMIT-ONLY (or worse — no-repo/no-git could not even commit) and the summary says so.
    print("!! COMMIT-ONLY — no push was attempted: sync verdict is %r." % verdict)
    if verdict in ("no-repo", "no-git"):
        print("!! Worse: with verdict %r the COMMIT itself cannot have happened either." % verdict)
    for line in notes:
        print("   %s" % line)
    print("   Carry this in the run summary; `sync.py --status` explains, `migrate.py` or")
    print("   `sync.py --set` resolves it.")
    return 1


def main():
    ap = argparse.ArgumentParser(description="Declared profile sync mode, verified against git.")
    ap.add_argument("--status", action="store_true", help="human-readable report (default)")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 unless the declaration exists and matches reality")
    ap.add_argument("--set", metavar="MODE", choices=MODES, dest="set_mode",
                    help="declare the mode: %s" % " | ".join(MODES))
    ap.add_argument("--end-of-run", action="store_true", dest="end_of_run",
                    help="post-commit step for the run skills: push under `remote`, print the "
                         "committed-locally line under `local-only`; exit 1 when the work did "
                         "not persist as declared")
    args = ap.parse_args()
    root = profile_root()

    if args.end_of_run:
        return end_of_run(root)

    if args.set_mode:
        try:
            state = set_mode(root, args.set_mode)
        except SyncError as e:
            print("!! NOT CHANGED: %s" % e)
            return 1
        print("sync mode is now: %s" % args.set_mode)
        if args.set_mode == "remote":
            print("End-of-run is commit THEN push (origin: %s)." % state["origin"])
        else:
            print("End-of-run is commit only. No off-machine copy exists; doctor and run")
            print("summaries will say so.")
        return 0

    verdict, mode, state, notes = resolve(root)

    if args.json:
        print(json.dumps({"verdict": verdict, "mode": mode, "git": state["git"],
                          "repo": state["repo"], "origin": state["origin"]}, indent=2))
        return 0 if verdict == "ok" else 1

    print("PROFILE SYNC")
    print("=" * 70)
    print("  declared   %s" % (mode or ("(unreadable)" if verdict == "error"
                                        else "(nothing — undeclared)")))
    print("  git        %s" % ("on PATH" if state["git"] else "MISSING"))
    print("  repository %s" % ("yes" if state["repo"] else "NO — plain folder"))
    print("  origin     %s" % (state["origin"] or "(none)"))
    if verdict == "ok":
        if mode == "remote":
            print("\n  OK. End-of-run: commit, then push.")
        elif state["origin"]:
            print("\n  OK. End-of-run: commit only. No second machine or cloud worker can")
            print("  attach to this profile (adr-012).")
        else:
            print("\n  OK. End-of-run: commit only. This profile has NO off-machine copy and")
            print("  no second machine or cloud worker can attach to it (adr-012).")
    else:
        print("\n  !! %s" % verdict.upper())
    for line in notes:
        print("  %s" % line)
    if args.check:
        return 0 if verdict == "ok" else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
