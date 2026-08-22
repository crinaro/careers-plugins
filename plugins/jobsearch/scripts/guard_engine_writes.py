#!/usr/bin/env python3
"""PreToolUse guard: a PROFILE session must not edit the ENGINE.

⭐ WHY THIS EXISTS
------------------
This project deliberately split into two sessions: the SEARCH (a profile repo — resume, config,
pipeline) and the ENGINE (this marketplace — scripts, agents, skills). The split was made after
one session did both all day, and the cost was concrete: real decisions sat behind refactors, and
**engine changes landed mid-run while a scheduled run was reading the very files being changed.**

In development the two repos sit side by side on one disk, so nothing physically stops a search
session from reaching into the engine and "just fixing" a script. That is precisely the failure
the split exists to prevent, and a behavioural rule alone does not survive a session that is
mid-task and confident.

    The rule: work on the engine happens in the ENGINE session. A search session that finds an
    engine bug ROUTES it — `marketplace-dev/scripts/intake.py` — and carries on searching.

⚠️ THIS IS A GUARD, NOT A SANDBOX. It matches Write/Edit-family tools. A determined session with
shell access can still write a file, exactly as the push-token hook could always be bypassed by
something that set out to. **The behavioural rule remains the first line**; this stops the
reflexive edit, which is the one that actually happens.

## The test, stated once

DENY when **the target is inside an engine checkout and the session is not working there.**

  target inside a repo containing `.claude-plugin/marketplace.json` or `.claude-plugin/plugin.json`
  AND cwd is NOT inside that same repo                                       -> DENY
  target inside the installed plugin CACHE                                   -> DENY (see below)
  target inside `.install-manifests/` (the install tamper records, adr-014)  -> DENY (see below)
  anything else                                                              -> allow

Deriving it from cwd rather than from a flag is what makes it correct in both directions: the
engine session's cwd IS the engine repo, so it is never blocked, and it needs no configuration
that could drift out of date.

**The cache clause is separate and worth its own sentence.** `~/.claude/plugins/cache/...` is a
COPY made at install time. An edit there looks like it worked, changes what Claude loads, and is
destroyed by the next sync with no warning — the worst combination available. Edit the source.

⭐⭐ FAILS OPEN, ALWAYS. Any unexpected condition exits 0 and allows the write. A guard that
breaks all editing is worse than the mistake it prevents, and this one runs on every file edit in
every session that has the plugin installed.

Protocol: stdin is the PreToolUse payload; **exit 2 blocks and stderr is shown to the model**.
Python 3.9+, stdlib only.
"""

import json
import os
import sys

MARKERS = (os.path.join(".claude-plugin", "marketplace.json"),
           os.path.join(".claude-plugin", "plugin.json"))

CACHE = os.path.join(os.path.expanduser("~"), ".claude", "plugins", "cache")

# The install manifests are the TAMPER RECORD for every installed plugin (adr-014). Hand-editing
# one erases the very evidence the install verification exists to read — and it was hand-"repaired"
# twice on 2026-08-11 before the mechanical self-heal existed. The one sanctioned writer is
# `heal_install.py`, which runs hook-side (not through these tools) and only rewrites a mismatch
# EXPLAINED by a version move; an unexplained mismatch must stay loud until a human decides.
MANIFESTS = os.path.join(os.path.expanduser("~"), ".claude", "plugins", ".install-manifests")

# Write-family tools. Bash is deliberately NOT matched: this guard is about the reflexive edit,
# and matching Bash would mean parsing shell to guess intent, which fails both ways.
FILE_FIELDS = ("file_path", "notebook_path", "path")


def engine_root_of(path):
    """The engine checkout containing `path`, or None. Walks up like git does."""
    cur = os.path.abspath(path)
    if not os.path.isdir(cur):
        cur = os.path.dirname(cur)
    seen = 0
    while cur and cur != os.path.dirname(cur) and seen < 40:
        for marker in MARKERS:
            if os.path.exists(os.path.join(cur, marker)):
                # Keep walking: a plugin inside a marketplace should answer with the MARKETPLACE,
                # so an edit from anywhere in the tree is judged against one root.
                outer = engine_root_of(os.path.dirname(cur))
                return outer or cur
        cur = os.path.dirname(cur)
        seen += 1
    return None


def contains(root, path):
    root = os.path.realpath(root)
    path = os.path.realpath(path)
    return path == root or path.startswith(root + os.sep)


def install_mode(engine):
    """DEVELOPMENT or CONSUMER — and they are genuinely different situations.

    ⭐ A `directory`-source marketplace registration IS the development pattern: the engine's
    real source tree is on this disk and is what `/plugin` copied from. A `github` source is the
    consumer pattern: nothing editable is local, and the only engine on disk is the cache.

    The distinction changes the advice, not just the wording. In development the fix is "make the
    change in the engine session, in that repo." For a consumer there is no local repo to make it
    in, and telling them to go edit one sends them to the cache — the one place an edit both
    appears to work and is silently destroyed.

    Returns ("development", source_path) | ("consumer", None) | (None, None) when unknown, and
    ⚠️ UNKNOWN MUST READ AS UNKNOWN. An earlier version answered from *any* registered
    marketplace, so a machine with the official first-party marketplace installed reported
    CONSUMER for a repo that was plainly a local checkout — a confidently wrong instruction,
    which is the failure this whole project is organised against. Always resolve the marketplace
    this file actually belongs to.
    """
    registry = _registry()
    if not engine:
        return None, None
    for entry in registry.values():
        src = (entry or {}).get("source") or {}
        if src.get("source") == "directory" and src.get("path"):
            if contains(src["path"], engine) or contains(engine, src["path"]):
                return "development", src["path"]
    return None, None


def _registry():
    path = os.path.join(os.path.expanduser("~"), ".claude", "plugins", "known_marketplaces.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def mode_of_marketplace(name):
    """Mode of ONE named marketplace — used for a cache path, where the name is in the path."""
    src = (_registry().get(name) or {}).get("source") or {}
    if src.get("source") == "directory" and src.get("path"):
        return "development", src["path"]
    if src.get("source"):
        return "consumer", None
    return None, None


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # unreadable payload is not evidence of a violation

    try:
        tool_input = payload.get("tool_input") or {}
        target = ""
        for field in FILE_FIELDS:
            if tool_input.get(field):
                target = str(tool_input[field])
                break
        if not target:
            return 0

        cwd = payload.get("cwd") or os.getcwd()
        target_abs = target if os.path.isabs(target) else os.path.join(cwd, target)

        if contains(MANIFESTS, target_abs):
            # Unconditional, like the cache clause: there is no session whose job is to hand-edit
            # a tamper record. The mechanical, version-scoped repair already runs at session start.
            sys.stderr.write(
                "⛔ BLOCKED: that is an install manifest — the tamper record for an installed "
                "plugin.\n\n"
                "   %s\n\n"
                "   Hand-editing it erases the evidence the install verification exists to read. A\n"
                "   mismatch EXPLAINED by a version move heals itself at session start\n"
                "   (scripts/heal_install.py, adr-014); an UNEXPLAINED one is corruption or\n"
                "   tampering and must stay loud until a human decides. Neither is fixed here.\n"
                % target_abs)
            return 2

        if contains(CACHE, target_abs):
            # The marketplace name is the first path segment under the cache root, so the mode
            # can be resolved for THIS marketplace rather than for whatever else is installed.
            rest = os.path.realpath(target_abs)[len(os.path.realpath(CACHE)):].strip(os.sep)
            mode, source = mode_of_marketplace(rest.split(os.sep)[0] if rest else "")
            if mode == "development" or source:
                where = ("   Edit the source instead — %s — then let `release-manager` sync it.\n"
                         % source) if source else \
                        "   Edit the marketplace source repo instead, then sync.\n"
            elif mode == "consumer":
                where = ("   This install came from a git source, so there is NO local source to\n"
                         "   edit. Report the problem upstream rather than patching the cache.\n")
            else:
                where = ("   Find the marketplace source and edit there; if this install came from\n"
                         "   a git source there is no local copy to change.\n")
            sys.stderr.write(
                "⛔ BLOCKED: that path is inside the installed plugin CACHE.\n\n"
                "   %s\n\n"
                "   The cache is a COPY made at install time. An edit here appears to work, does\n"
                "   change what Claude loads, and is destroyed by the next sync with no warning.\n"
                "%s" % (target_abs, where))
            return 2

        engine = engine_root_of(target_abs)
        if engine and not contains(engine, cwd):
            mode, _ = install_mode(engine)
            # Naming the mode matters: it is the difference between "you are in the development
            # pattern, so there IS a source repo and it is that one" and a consumer being told to
            # go edit something that does not exist on their disk.
            banner = {
                "development": "   mode:   DEVELOPMENT — this marketplace is registered as a local\n"
                               "           directory source, so that repo IS the live engine source.\n",
                "consumer": "   mode:   CONSUMER — this marketplace came from a git source.\n",
            }.get(mode, "")
            sys.stderr.write(
                "⛔ BLOCKED: this session is not the engine session, and that file is engine code.\n\n"
                "   target: %s\n"
                "   engine: %s\n"
                "   cwd:    %s\n"
                "%s\n"
                "   Work on the engine happens in the ENGINE session, in that repo. This split\n"
                "   exists because engine changes once landed mid-run while a scheduled run was\n"
                "   reading the same files.\n\n"
                "   ⭐ ROUTE IT INSTEAD, then carry on with what you were doing:\n\n"
                "     ~/.claude/jobsearch/run report_issue.py \\\n"
                "       --title \"...\" --symptom \"...\" --evidence \"...\"\n\n"
                "   State the bug as the RULE that misbehaved, never the instance — that queue\n"
                "   refuses personal data, and git history is permanent. See %s/docs/intake.md\n"
                % (target_abs, engine, cwd, banner.rstrip("\n"), engine, engine))
            return 2
    except Exception:
        return 0  # fail open, deliberately

    return 0


if __name__ == "__main__":
    sys.exit(main())
