#!/usr/bin/env python3
"""
WHERE AM I RUNNING, AND WHAT CAN I ACTUALLY DO?

WHY THIS EXISTS
---------------
The candidate, 2026-08-04: *"I want a multi-agent experience that's distributed."* An audit of the
capability split found that **exactly ONE thing is irreducibly bound to the desktop** — LinkedIn
authenticated browsing, because it needs a real logged-in Chrome and there is no API. Gmail is a
credentials-placement decision, and everything else (research, drafting, every script, the
dashboard, git) already runs anywhere.

So the architecture is not "make the coordinator run everywhere." It is: **the repo is the
coordinator, and sessions are workers that attach to it with different capabilities.** A worker
must therefore be able to answer *what can I do here?* before it claims work.

    ⚠️ THE POINT IS TO STOP GUESSING. Until now, capability was implicit: a run assumed Chrome was
    there, tried, and reported BROWSER UNAVAILABLE on failure. That is fine for one machine and
    useless for routing — you cannot decide who should take a task by having everyone attempt it.

## What it probes, and how — never by assumption

    repo        the git repo is present and readable        (the bus itself)
    python      scripts run                                 (deterministic core)
    keychain    the OS credential store has IMAP creds      (Gmail scans — any platform,
                                                             token name kept for queue compat)
    chrome      the extension's MCP server is configured    (⭐ the desktop lock)
    browser     an unauthenticated browser pane exists      (research)

**`chrome` is deliberately conservative.** It reports the CONFIGURED capability, not a live
handshake — a probe cannot tell whether the browser will answer, and a worker that claims
LinkedIn work on an optimistic probe is worse than one that never claims it. The runtime check
stays where it belongs: `linkedin-runner` calls `list_connected_browsers` and returns
BROWSER UNAVAILABLE. This answers *"could this environment ever do LinkedIn work?"*, not
*"will it work right now?"*

Usage:
    python3 scripts/whoami.py              # human-readable capability report
    python3 scripts/whoami.py --json       # machine-readable, for routing
    python3 scripts/whoami.py --can chrome # exit 0 if capable, 1 if not

Python 3.9+. Standard library only.
"""

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys

import os, sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root as _profile_root
ENGINE_SCRIPTS = os.path.dirname(os.path.realpath(__file__))

ROOT = _profile_root()

# Capability -> the queued-work `requires` token. Keep these stable; they are written into
# data/pending_actions.jsonl and a rename would orphan every queued item.
CAPABILITIES = ("repo", "python", "keychain", "chrome", "browser")


def _has_repo():
    return os.path.isdir(os.path.join(ROOT, ".git")) and \
        os.path.exists(os.path.join(ROOT, "data", "opportunities.jsonl"))


def _has_python():
    return os.path.exists(os.path.join(ENGINE_SCRIPTS, "validate_data.py"))


def _has_keychain():
    """The OS credential store (Keychain / PasswordVault / secret-service) holds an IMAP
    credential — the same cross-platform store gmail_mcp_server.py actually reads.

    ⭐ THE TOKEN NAME STAYS "keychain" DELIBERATELY, even though the probe is no longer
    macOS-only: the token is written into data/pending_actions.jsonl `requires`, and renaming
    it would orphan every queued item (or force a deferred.py alias/migration). Only the BODY
    changed. Before this, the probe answered False on every non-Mac, so a capable Linux or
    Windows worker declined Gmail work it could do — a capability reading as absent is
    indistinguishable from one that is.

    On macOS this checks the SERVICE's presence and never touches the secret. On the other
    backends the store's read API is the only probe, so the value is read and immediately
    discarded — never printed, never logged (credentials.has_credential)."""
    if platform.system() == "Darwin":
        if not shutil.which("security"):
            return False
        try:
            r = subprocess.run(["security", "find-generic-password", "-s", "claudesearch-imap"],
                               capture_output=True, timeout=10)
            return r.returncode == 0
        except Exception:
            return False
    try:
        import credentials as _credentials
        import profile as _profile
        return any(_credentials.has_credential(a) for a in _profile.mailboxes())
    except Exception:
        return False


def _has_chrome():
    """⭐ THE DESKTOP LOCK. Is the Chrome-extension MCP server CONFIGURED for this environment?

    Deliberately a configuration check, not a handshake — see the module docstring. A worker
    routing on an optimistic probe would claim LinkedIn work it cannot finish, and a claimed
    task that fails is worse than an unclaimed one because it looks handled.
    """
    for rel in (".mcp.json", ".claude/settings.json", ".claude/settings.local.json"):
        p = os.path.join(ROOT, rel)
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as fh:
                    if "claude-in-chrome" in fh.read():
                        return True
            except Exception:
                pass
    home = os.path.expanduser("~/.claude.json")
    if os.path.exists(home):
        try:
            with open(home, encoding="utf-8") as fh:
                return "claude-in-chrome" in fh.read()
        except Exception:
            return False
    return False


def _has_browser():
    """An UNAUTHENTICATED browser pane — research, JS-heavy ATS pages. Not identity-bound, so
    any environment offering it qualifies."""
    for rel in (".mcp.json", ".claude/settings.json", ".claude/settings.local.json"):
        p = os.path.join(ROOT, rel)
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as fh:
                    if "Claude_Browser" in fh.read():
                        return True
            except Exception:
                pass
    return _has_chrome()          # an environment with Chrome can always render a page


PROBES = {
    "repo": _has_repo,
    "python": _has_python,
    "keychain": _has_keychain,
    "chrome": _has_chrome,
    "browser": _has_browser,
}


def probe():
    caps = {}
    for name in CAPABILITIES:
        try:
            caps[name] = bool(PROBES[name]())
        except Exception:
            caps[name] = False
    return caps


def worker_id(caps):
    """A stable-ish name for this environment, used as `claimed_by` on queued work."""
    kind = "desktop" if caps.get("chrome") else ("cloud" if caps.get("repo") else "unknown")
    try:
        host = socket.gethostname().split(".")[0][:20]
    except Exception:
        host = "host"
    return "%s:%s" % (kind, host)


def main():
    ap = argparse.ArgumentParser(description="What can this environment actually do?")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--can", metavar="CAPABILITY",
                    help="Exit 0 if capable, 1 if not. Use in a run before claiming work.")
    args = ap.parse_args()

    caps = probe()

    if args.can:
        if args.can not in CAPABILITIES:
            print("Unknown capability %r. Known: %s" % (args.can, ", ".join(CAPABILITIES)))
            return 2
        return 0 if caps[args.can] else 1

    if args.json:
        print(json.dumps({"worker": worker_id(caps), "capabilities": caps}, indent=2))
        return 0

    print("WORKER — %s" % worker_id(caps))
    print("=" * 66)
    for name in CAPABILITIES:
        mark = "yes" if caps[name] else "NO "
        note = ""
        if name == "chrome":
            note = "  ⭐ the only irreducible desktop lock (LinkedIn has no API)"
        elif name == "keychain":
            note = "  Gmail scans; a credentials-placement decision, not a hard lock"
        print("  %-10s %s%s" % (name, mark, note))

    print("\n  WHAT THIS WORKER MAY CLAIM")
    if caps["chrome"]:
        print("    Everything, including LinkedIn-authenticated work.")
    elif caps["repo"]:
        print("    Research, drafting, scripts, dashboard, git — everything EXCEPT")
        print("    LinkedIn-authenticated work%s." % ("" if caps["keychain"] else " and Gmail scans"))
        print("    Leave `requires: [chrome]` items for a desktop worker; do not attempt them.")
    else:
        print("    Nothing — no repo. This is a control surface (read, decide, approve),")
        print("    not a worker.")
    print("\n  Route queued work with:  python3 scripts/deferred.py --claimable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
