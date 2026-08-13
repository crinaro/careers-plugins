#!/usr/bin/env python3
"""Notice mid-session that the engine moved underneath this session — GitHub #42.

SessionStart fires exactly once. A session that spans a version bump therefore keeps its
loaded skills, agents and hooks from the OLD version while every script it shells out to
resolves the NEW one through `~/.claude/jobsearch/engine_root`, which is read at call time.
The migration hook cannot re-fire, so the profile stays on the old shape for the rest of
that session — and both halves look healthy on their own.

⭐ THIS IS A GUARD, NOT THE MIGRATION. It compares two strings and says something. It does
not apply anything: mutating a user's data mid-turn, while a live session holds assumptions
about that data in context, is its own hazard, and a scheduled run may be holding the write
lock. Per #42 the failure being fixed is SILENCE, not the absence of an automatic fix.

⚠️ IT RUNS ON EVERY PROMPT, so it must stay cheap and it must never block. Two small file
reads, a string compare, and exit 0 on every path including every error.

Also surfaces the condition behind #41: a stamp sitting behind the installed engine with
nothing anywhere saying so.

Python 3.9+. Standard library only.
"""

import json
import os
import re
import sys


def _quiet_exit():
    sys.exit(0)


def _ver(s):
    return tuple(int(x) for x in re.findall(r"\d+", str(s or "0"))[:3] or [0])


def _announce_once(message, session, key):
    """Say it once per session per distinct condition.

    ⚠️ Announcing on every prompt trains the user to ignore it, which ends up exactly where
    saying nothing does. The key includes what is being announced, so a NEW condition in the
    same session is still heard."""
    state = os.path.join(os.path.expanduser("~"), ".claude", "jobsearch", "drift")
    sess = re.sub(r"[^A-Za-z0-9_-]", "", str(session))[:64] or "nosession"
    marker = os.path.join(state, sess)
    stamp = re.sub(r"\s+", " ", key)[:200]
    try:
        with open(marker, encoding="utf-8") as fh:
            if stamp in fh.read().split("\n"):
                return
    except OSError:
        pass
    try:
        os.makedirs(state, exist_ok=True)
        with open(marker, "a", encoding="utf-8") as fh:
            fh.write(stamp + "\n")
    except OSError:
        pass                        # cannot record it; still worth saying once
    print(message)


def main():
    # Hook input arrives as JSON on stdin. A missing or malformed payload is not a reason to
    # bother the user, so every failure path here is silent.
    session = ""
    try:
        if not sys.stdin.isatty():
            session = str((json.loads(sys.stdin.read() or "{}") or {}).get("session_id") or "")
    except Exception:                                   # noqa: BLE001
        session = ""

    try:
        here = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, here)
        from _root import engine_root, profile_root, is_installed_engine

        eroot = engine_root()
        with open(os.path.join(eroot, ".claude-plugin", "plugin.json"),
                  encoding="utf-8") as fh:
            engine = str(json.load(fh).get("version") or "")

        # ⭐⭐ WHICH ENGINE IS THIS, AND IS IT THE VERSION IT CLAIMS? — GitHub #59.
        #
        # Six engine versions coexist in the install cache on an ordinary machine, so every
        # script name exists at six paths at once, plus once more in any checkout. Two
        # conditions are worth announcing, and neither announced anything before:
        #
        #   1. The engine is NOT the installed copy. The pointer deliberately honours a
        #      checkout — that is the only way to test an unreleased change — but running
        #      one unknowingly is how a stale tree gets mistaken for the release.
        #   2. The engine IS installed but its version DISAGREES with the directory it sits
        #      in. The cache path ends in the version, so `.../jobsearch/0.19.0/` holding a
        #      manifest that says 0.20.0 is skew, and it is silent otherwise.
        note = ""
        if not is_installed_engine(eroot):
            note = ("jobsearch: running a NON-INSTALLED engine at %s (version %s). That is "
                    "deliberate when you are testing an unreleased change, and wrong if you "
                    "expected the released one." % (eroot, engine or "unknown"))
        else:
            seg = os.path.basename(os.path.realpath(eroot))
            if engine and re.match(r"^\d+\.\d+\.\d+$", seg) and seg != engine:
                note = ("jobsearch: VERSION SKEW — the engine is installed at %s but its "
                        "manifest says %s. Those must match; a mismatch means the cache "
                        "directory and the code in it disagree." % (seg, engine))
        if note:
            _announce_once(note, session, "engine:%s:%s" % (eroot, engine))
        profile = profile_root()
        raw = ""
        try:
            with open(os.path.join(profile, ".jobsearch-schema"), encoding="utf-8") as fh:
                raw = fh.read().strip()
        except OSError:
            _quiet_exit()          # no profile here — this is not a jobsearch session
        stamp = raw
        if raw.startswith("{"):
            stamp = str((json.loads(raw) or {}).get("schema") or "")
        if not engine or not stamp or _ver(stamp) >= _ver(engine):
            _quiet_exit()

        _announce_once(
            "jobsearch: your profile's data shape is %s but the installed engine is %s. "
            "The migration only runs at session start, so this session will keep using the "
            "old shape. Start a new session, or run `~/.claude/jobsearch/run migrate.py` "
            "from your search directory." % (stamp, engine),
            session, "schema:%s:%s" % (stamp, engine))
    except Exception:                                   # noqa: BLE001
        pass                        # a guard that breaks a session is worse than no guard
    return 0


if __name__ == "__main__":
    sys.exit(main())
