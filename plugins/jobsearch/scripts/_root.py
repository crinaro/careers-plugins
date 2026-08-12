#!/usr/bin/env python3
"""Where is the USER'S profile? (Not: where is the engine?)

⭐ THE DISTINCTION THIS MODULE EXISTS TO ENFORCE (2026-08-05)
------------------------------------------------------------
Every script used to derive its root as `dirname(dirname(abspath(__file__)))` — the directory
above `scripts/`. That is correct only when the engine and the data live in the same tree, which
was true exactly once: the original single-user installation.

**As a PLUGIN they are never the same tree.** The engine installs under `${CLAUDE_PLUGIN_ROOT}`
somewhere in Claude's plugin directory; the user's resume, config and pipeline live in whatever
directory they run from. Deriving the data root from `__file__` points every script at the
PLUGIN, which contains no data.

That failure is not loud. On 2026-08-05 a symlinked install did exactly this and
`generate_dashboard.py` regenerated the dashboard with ZERO opportunities and overwrote the real
one — exit 0, valid HTML, no data. **A wrong root produces empty results, not errors.**

Resolution order:
  1. `CLAUDESEARCH_ROOT`  — explicit; also what lets an AGENCY point the engine at one candidate's
     profile out of many, and what the test suite uses for isolation.
  2. the current working directory, walking UP to find a profile marker.
  3. ⭐ the REMEMBERED profile (`~/.claude/jobsearch/profile_root`).

⭐⭐ WHY (3) EXISTS — AN MCP SERVER HAS NEITHER OF THE FIRST TWO (2026-08-05).
A long-lived MCP server is spawned by the Claude runtime, not from a shell: it inherits no
`CLAUDESEARCH_ROOT` and no guarantee about its working directory. `gmail_mcp_server.py` therefore
resolved a non-profile directory, found no `user.json`, and served ZERO mailboxes for the life of
the process — and a mailbox-blind search returns the same empty result as a genuinely empty
mailbox. It reported "no new mail" while the deterministic per-call sweeps, which DO start from a
shell in the profile, reached both accounts fine.

The pointer is maintained automatically: any resolution that finds a genuine profile records it.
So a single normal run from the profile directory repairs it for every process that cannot see one.

A "profile" is a directory containing `config.json` or `data/`. Walking up means you can run from
a subdirectory, the way git does.
"""

import os

MARKERS = ("config.json", "data")

# Written whenever a real profile is resolved; read when nothing else can identify one.
POINTER = os.path.join(os.path.expanduser("~"), ".claude", "jobsearch", "profile_root")
# ⭐ The ENGINE's own location, for callers that have no `${CLAUDE_PLUGIN_ROOT}`. Each Bash tool
# call is a FRESH SHELL, so a variable exported by one command is gone by the next; a run needs a
# path it can read from disk in every command. Written on import, so any script run repairs it.
ENGINE_POINTER = os.path.join(os.path.expanduser("~"), ".claude", "jobsearch", "engine_root")


# ⭐⭐ A TEST FIXTURE AND A TEMP DIRECTORY LOOK EXACTLY LIKE A PROFILE — and must never be
# recorded as one. Both have `config.json` and `data/`, which is the whole test for a profile.
#
# Observed 2026-08-06: running the suite left the remembered pointer aimed at
# `tests/fixtures/profile`. The next 46 tests skipped, which is the LOUD symptom — the quiet one
# is far worse. **An MCP server has no cwd and no env; the pointer is all it has.** With the
# pointer aimed at a fixture, a real run resolves a store containing synthesized rows and reports
# it as the user's pipeline. That is the same shape as the engine-pointer bug fixed in 0.2.1:
# a durable pointer must never be allowed to name something disposable.
_NOT_A_REAL_PROFILE = (
    os.sep + "tests" + os.sep + "fixtures" + os.sep,
    os.sep + "fixtures" + os.sep,
    os.sep + "Temp" + os.sep,
    os.sep + "tmp" + os.sep,
)


def is_disposable_profile(path):
    """Would recording this path aim the pointer at a fixture or a temp tree?

    Conservative: a false negative only preserves today's behaviour, while a false positive would
    refuse to remember a legitimate profile and leave an MCP server with nothing to resolve.

    ⚠️ ASK THE PLATFORM WHERE TEMP IS — a hardcoded list is not enough, and this was caught the
    same day the check was written. macOS puts temp under
    `/var/folders/<hash>/T/`, which contains neither `/tmp/` nor `/Temp/`, so a literal-marker
    check waved it straight through and a test run left the pointer aimed at a temp directory
    that no longer existed. `tempfile.gettempdir()` is the authoritative answer on every platform;
    the literal markers stay as a backstop for paths that are temp-shaped but not the default.
    """
    import tempfile
    p = os.path.realpath(path or "")
    if not p.endswith(os.sep):
        p += os.sep
    try:
        tmp = os.path.realpath(tempfile.gettempdir())
        if not tmp.endswith(os.sep):
            tmp += os.sep
        if p.startswith(tmp):
            return True
    except Exception:
        pass
    return any(marker in p for marker in _NOT_A_REAL_PROFILE)


def _remember(path):
    """Record a resolved profile. Best-effort and silent: never break a run over a cache."""
    try:
        if not looks_like_profile(path):
            return
        if is_disposable_profile(path):
            return
        if os.path.exists(POINTER):
            with open(POINTER, encoding="utf-8") as fh:
                if fh.read().strip() == path:
                    return                      # unchanged; avoid rewriting on every import
        os.makedirs(os.path.dirname(POINTER), exist_ok=True)
        with open(POINTER, "w", encoding="utf-8") as fh:
            fh.write(path)
    except OSError:
        pass


def remembered_profile():
    """The last profile a run resolved, if it still looks like one."""
    try:
        with open(POINTER, encoding="utf-8") as fh:
            path = fh.read().strip()
        return path if path and looks_like_profile(path) else None
    except OSError:
        return None


def looks_like_profile(path):
    return any(os.path.exists(os.path.join(path, m)) for m in MARKERS)


def profile_root(start=None):
    """The USER's profile directory. Never the engine's."""
    env = os.environ.get("CLAUDESEARCH_ROOT")
    if env:
        root = os.path.abspath(env)
        _remember(root)
        return root
    cur = os.path.abspath(start or os.getcwd())
    while True:
        if looks_like_profile(cur):
            _remember(cur)
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            # Nothing above the CWD is a profile. Before giving up, use the profile a previous
            # run recorded — this is the ONLY thing an MCP server has to go on.
            been_here = remembered_profile()
            if been_here:
                return been_here
            # Still nothing. Return the CWD rather than guessing: a caller that needs data will
            # fail visibly on a missing file, which is far better than silently reading the
            # engine's own directory and reporting an empty pipeline as fact.
            return os.path.abspath(start or os.getcwd())
        cur = parent


# ⭐⭐ EPHEMERAL ENGINE LOCATIONS — the desktop app materialises a plugin PER SESSION.
#
# Observed 2026-08-05 on the desktop, which is how most people will run this: `engine_root` had
# been overwritten with
#
#   ~/Library/Application Support/Claude/local-agent-mode-sessions/<session-id>/…/rpm/plugin_<id>
#
# That path is real and works — for the life of that session. `~/.claude/jobsearch/run` reads this
# pointer on EVERY call, so once the session is gone every scheduled run fails at its first script
# call. **The failure lands hours later, in an unattended run, far from the session that caused
# it**, which is the worst shape a bug can have here.
#
# The cause is that _remember_engine recorded whatever copy happened to run last. A pointer meant
# to outlive sessions must therefore refuse to point INTO one.
_EPHEMERAL_MARKERS = (
    os.sep + "local-agent-mode-sessions" + os.sep,
    os.sep + "rpm" + os.sep + "plugin_",
    os.sep + "Temp" + os.sep,
    os.sep + "tmp" + os.sep,
)


def is_ephemeral_engine(path):
    """Would recording this path leave a pointer that dies with a session?

    Conservative on purpose: a false NEGATIVE leaves today's behaviour, while a false POSITIVE
    would refuse to record a legitimate engine and strand a user with no pointer at all.
    """
    p = os.path.realpath(path or "")
    if not p.endswith(os.sep):
        p += os.sep
    return any(marker in p for marker in _EPHEMERAL_MARKERS)


# ⭐ Where the marketplace puts an INSTALLED copy, as opposed to somebody's checkout.
INSTALL_CACHE = os.path.join(os.path.expanduser("~"), ".claude", "plugins", "cache",
                             "careers-plugins", "jobsearch")


def is_installed_engine(path):
    """Is this a marketplace-installed copy rather than a working tree?

    Deliberately a path test and not a content test: a checkout and an installed copy hold the
    same files, so nothing INSIDE them can tell the two apart. Where it sits is the only signal.
    """
    p = os.path.realpath(path or "")
    return bool(p) and p.startswith(os.path.realpath(INSTALL_CACHE) + os.sep)


def _remember_engine(path):
    try:
        if is_ephemeral_engine(path):
            return  # never point at something that dies with a session
        if os.path.exists(ENGINE_POINTER):
            with open(ENGINE_POINTER, encoding="utf-8") as fh:
                current = fh.read().strip()
            # Overwrite an unchanged pointer for nothing, no — but DO heal one that is already
            # ephemeral or has been deleted. Self-healing matters because the run that would
            # notice is unattended and has nobody to tell.
            if current == path:
                return
            # ⭐⭐ A CHECKOUT RUN MUST NOT HIJACK A POINTER THAT NAMES AN INSTALLED COPY.
            #
            # This function runs on IMPORT, so merely executing an engine script re-aims the
            # durable pointer at whatever copy ran last. That is right for healing and wrong for
            # everything else: running one gate inside a checkout silently redirects the user's
            # UNATTENDED runs at a working tree — mid-refactor code, uncommitted edits, whatever
            # happens to be on disk at the moment the schedule fires. The person running the gate
            # sees nothing; the cost lands hours later in a run nobody is watching.
            #
            # An installed copy therefore outranks a checkout. Pointing at a checkout on purpose
            # is still available — `install_launcher.py` writes the pointer directly, which is
            # what makes it a deliberate act rather than a side effect of running anything.
            if (current and os.path.isdir(current)
                    and is_installed_engine(current) and not is_installed_engine(path)):
                return
            if current and not is_ephemeral_engine(current) and os.path.isdir(current):
                if os.path.realpath(current) == os.path.realpath(path):
                    return
        os.makedirs(os.path.dirname(ENGINE_POINTER), exist_ok=True)
        with open(ENGINE_POINTER, "w", encoding="utf-8") as fh:
            fh.write(path)
    except OSError:
        pass


def engine_root():
    """Where the ENGINE physically lives — for schemas, prompts and templates that ship with it.

    ⭐ realpath, NOT abspath — the OPPOSITE of profile_root, deliberately.
    A development install may reach the engine through a symlink (`scripts -> ../engine/scripts`).
    `abspath` would then answer with the SYMLINK's directory, i.e. the user's profile, and every
    engine-structure lookup would search the wrong tree. The engine is where the FILE IS, so we
    resolve. The profile is where the USER IS, so we never resolve. Getting these backwards is
    the whole class of bug this module exists to prevent."""
    return os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


def profile_or_fixture(start=None):
    """The user's profile, or the test fixture when there is none.

    ⭐ CI runs from a bare engine checkout with no profile at all. A gate that cannot execute
    proves nothing, so the gates fall back to `tests/fixtures/profile` — synthetic, containing no
    real person, employer, address or figure. Locally this always returns the real profile, so the
    gates keep testing real data where it exists."""
    import os as _o
    r = profile_root(start)
    if _o.path.exists(_o.path.join(r, "config.json")):
        return r
    fx = _o.path.join(engine_root(), "tests", "fixtures", "profile")
    return fx if _o.path.exists(_o.path.join(fx, "config.json")) else r


_ENGINE_AT_IMPORT = engine_root()
_remember_engine(_ENGINE_AT_IMPORT)
