#!/usr/bin/env python3
"""Is this session BOUND to a job-search profile — and by what evidence? (dev #150)

⭐ WHY THIS EXISTS
------------------
The plugin installs at user scope, so its agents are listed in every session on the machine.
Being listed is untidy; being DISPATCHED is the problem: `profile_root()`'s remembered pointer
(`~/.claude/jobsearch/profile_root`) is machine-global, so an agent dispatched from a repository
that has nothing to do with the search does not fail cleanly — it resolves the real profile and,
for the drafting agents, writes to it. The gap was that nothing distinguished *"a job-search
session that lost its cwd"* from *"an unrelated session that should not be here."*

This module makes that distinction explicit. It reports the SIGNAL a resolution rests on:

    env      `CLAUDESEARCH_ROOT` is set — an explicit, deliberate binding.        BOUND
    cwd      a profile marker sits at or above the working directory.             BOUND
    pointer  ONLY the machine-global remembered pointer names a profile.          NOT bound
    none     no evidence of any profile at all.                                   NOT bound

⭐⭐ IT DOES NOT TOUCH `profile_root()` AND MUST NEVER GATE IT. The pointer fallback is what
lets an MCP server — spawned with no env and no meaningful cwd — find the profile at all;
without it the Gmail server once served zero mailboxes for a whole run while reporting "no new
mail." Resolution stays permissive; this module only answers, at a POINT OF USE (agent entry,
skill entry), whether the resolution carries evidence that the session belongs to that profile.
The check runs at use time, never at session start, because a scheduled run's prompt `cd`s into
the profile AFTER its session starts — at session start its cwd is wherever the scheduler chose.

Who calls what:

    agents            `binding.py --assert` as their first command. A pointer-only or empty
                      context REFUSES (exit 2 / 3, loud): a model-initiated dispatch carries no
                      evidence of intent. A dispatching session that IS the search but started
                      elsewhere re-dispatches naming the root, and the agent prefixes commands
                      with `CLAUDESEARCH_ROOT=<root>`, which is the `env` signal.
    skills            `binding.py` (no flag) to ANNOUNCE the binding. A user typing a jobsearch
                      skill by name is itself evidence of intent, so pointer-only is acceptable
                      there — but it is said out loud, never silent.
    MCP servers       nothing. They resolve through `profile_root()` exactly as before.

A refusal also records a coded `binding` event in the diagnostics log, so an attempted
unrelated-context dispatch leaves evidence an operator can read (`doctor`), not just a stopped
agent. Honest limit: this defeats reflexive dispatch, not a determined bypass — nothing stops a
process from reading the pointer and exporting it. The agents' own definitions carry the
behavioural rule; this makes the reflexive path loud and mechanical.

Usage:
    python3 binding.py             # announce: signal + root, exit 0 unless no profile at all
    python3 binding.py --assert    # exit 0 bound · 2 pointer-only (refused) · 3 no profile
    python3 binding.py --json      # the raw record, for tools

Python 3.9+. Standard library only.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from _root import looks_like_profile, remembered_profile  # noqa: E402

EXIT_BOUND = 0
EXIT_POINTER_ONLY = 2
EXIT_NO_PROFILE = 3


def binding(start=None):
    """{signal, root, bound} — mirrors `profile_root()`'s precedence exactly, but reports
    WHICH rung answered instead of flattening them into one path. Read-only: unlike
    `profile_root()` it never records the pointer, because asking about a binding must not
    manufacture one."""
    env = os.environ.get("CLAUDESEARCH_ROOT")
    if env:
        return {"signal": "env", "root": os.path.abspath(env), "bound": True}
    cur = os.path.abspath(start or os.getcwd())
    while True:
        if looks_like_profile(cur):
            return {"signal": "cwd", "root": cur, "bound": True}
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    pointed = remembered_profile()
    if pointed:
        return {"signal": "pointer", "root": pointed, "bound": False}
    return {"signal": "none", "root": None, "bound": False}


def _diag_event(verdict):
    try:
        from _diag import log as diag
        diag("binding", verdict=verdict)
    except Exception:                                   # noqa: BLE001
        pass                       # evidence is best-effort; the verdict never depends on it


def main():
    ap = argparse.ArgumentParser(
        description="Is this session bound to a job-search profile, and by what evidence?")
    ap.add_argument("--assert", dest="assert_", action="store_true",
                    help="exit 0 only when bound by env or cwd; refuse pointer-only, loudly")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    b = binding()
    if args.json:
        print(json.dumps(b, sort_keys=True))
        return EXIT_BOUND if b["bound"] else (
            EXIT_POINTER_ONLY if b["signal"] == "pointer" else EXIT_NO_PROFILE)

    if b["bound"]:
        print("BOUND via %s: %s" % (b["signal"], b["root"]))
        return EXIT_BOUND

    if b["signal"] == "pointer":
        if args.assert_:
            _diag_event("refused-pointer-only")
            print("NOT BOUND — REFUSED (pointer-only). The machine remembers a profile at\n"
                  "  %s\n"
                  "but this session shows no evidence it belongs to it: CLAUDESEARCH_ROOT is "
                  "unset and no profile marker (config.json or data/) sits at or above the "
                  "working directory. A jobsearch agent dispatched here must STOP without "
                  "reading or writing that profile (dev #150).\n"
                  "If this dispatch genuinely is part of the job search, either run from the "
                  "profile directory or have the dispatching session name the root so every "
                  "command can be prefixed with CLAUDESEARCH_ROOT=<root>. MCP servers and "
                  "read-only diagnostics are unaffected — they do not call --assert."
                  % b["root"])
            return EXIT_POINTER_ONLY
        print("NOT BOUND (pointer-only): the machine remembers a profile at %s, but this "
              "session carries no evidence of its own (no CLAUDESEARCH_ROOT, no profile at or "
              "above the cwd). Say which profile you are acting on before acting." % b["root"])
        return EXIT_BOUND

    _diag_event("no-profile")
    print("NO PROFILE — nothing bindable: CLAUDESEARCH_ROOT is unset, no profile marker at or "
          "above the working directory, and nothing remembered at ~/.claude/jobsearch/"
          "profile_root. If a job-search profile exists on this machine, run from its "
          "directory; if none exists yet, the jobsearch:onboarding skill creates one.")
    return EXIT_NO_PROFILE


if __name__ == "__main__":
    sys.exit(main())
