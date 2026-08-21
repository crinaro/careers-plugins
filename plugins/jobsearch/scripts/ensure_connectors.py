#!/usr/bin/env python3
"""Self-install the companion connector plugins this engine declares — at session start,
loudly, and never as an instruction to the user.

WHY THIS EXISTS (2026-08-21)
----------------------------
The platform's own `dependencies` field cannot deliver a connector: deployment-auditor
measured (CLI 2.1.231, directory and git-over-HTTP sources) that the load check judges an
INSTALLED plugin against the marketplace catalog's CURRENT manifest — so merely publishing a
dependency-declaring version breaks every existing install at its next catalog refresh,
whether or not that user ever upgrades. The declaration was removed the same day
(owner-approved; doc-impact rows #19–#23). What replaces it is this script: jobsearch loads
unconditionally, and a SessionStart hook installs the connector itself.

The precondition was PROBED before this was designed (2026-08-21, scratch CLAUDE_CONFIG_DIR,
no real install state touched): a SessionStart hook CAN run `claude plugin install` against
its own live config dir — it fired mid-session, exit 0, and needs no model auth at all
(plugin install is a local marketplace-copy operation, not a model call). ⚠️ SCOPE, corrected
the same day by the deployment audit: that probe's hook lived in USER SETTINGS. A
plugin-shipped hook like this one does NOT run in an unauthenticated session at all — the CLI
exits at the login gate before the plugin subsystem starts (measured: planted marker in an
installed copy's hooks.json never fired under `claude -p` while logged out) — so this script
effectively runs only in authenticated sessions. That is acceptable (nothing else of this
plugin runs before login either) and is recorded per-surface in docs/deployment.md.
An already-installed
re-run measured ~1.5s — idempotent, but far too slow to pay every session, hence the fast
path below. A failed install exits 1 and names its own remedy.

THE CONTRACT — every clause is load-bearing
-------------------------------------------
* **Fast path first, no CLI spawn.** Steady state is one JSON read (~1ms): any key in
  `installed_plugins.json` starting `<connector>@` means done, silently. `claude` is spawned
  only on a miss, stdin from `/dev/null`, under a hard timeout.
* **The marketplace name is DERIVED, never hardcoded** — the same structural move as
  `_root.is_installed_engine` (dev #199): this file's own realpath is
  `<config>/plugins/cache/<marketplace>/<plugin>/…`, so the segment after `plugins/cache/` is
  the marketplace and the prefix before it is the config dir that holds
  `plugins/installed_plugins.json`. A rename of the marketplace cannot break this file, and
  the connector is always installed from the SAME marketplace this engine came from.
  Derivation fails (a dev checkout, a worktree, a directory-source layout outside the cache)
  → skip with one honest line. Never guess a marketplace; never install on a guess.
* **Exit 0 on every path.** jobsearch loading regardless of the connector's fate is the
  entire point of removing the declaration; this hook must never make session start worse.
* **Failure is LOUD and names the remedy** — the `!! INCOMPLETE COVERAGE` family
  (`require_accounts()` precedent): quote the CLI's own words, print the exact command the
  user can run, say plainly that mail tools are absent meanwhile. A missing thing must never
  read as an empty thing (CLAUDE.md, traps 1–4).
* **Success says the tools arrive NEXT session** — trap 1: skills and MCP servers load at
  session start, so the session that installs the connector still runs without it.
* **Never uninstalls, never updates, never touches another plugin's state.** The only write
  this script ever causes is the platform's own `plugin install`.

Connectors are declared in this plugin's manifest under `metadata.connectors` (free-form
metadata — the platform does not interpret it, so it cannot re-create the stranding the
`dependencies` field caused). Every future connector is one string in that list; the
mechanism is deliberately generic.
"""

import json
import os
import subprocess
import sys

INSTALL_TIMEOUT = 60          # hard cap per install attempt; the hook entry allows 90
BANNER = "!! CONNECTOR MISSING"


def plugin_root():
    """This engine's own location. realpath, matching `_root.engine_root()` — a symlinked
    checkout must resolve to where the file IS, which is exactly what makes the derivation
    below refuse it (a checkout is not under any install cache)."""
    return os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


def derive_install_identity(root=None):
    """(config_dir, marketplace) from this engine's own installed location, or None.

    STRUCTURAL, never name-pinned (dev #199): `<config>/plugins/cache/<marketplace>/<plugin>/…`.
    The marketplace segment is read positionally and never compared to a literal, so renaming
    the marketplace cannot break it; `CLAUDE_CONFIG_DIR` relocations are covered for free,
    because the config dir is derived from the same path rather than assumed to be `~/.claude`.
    Returns None when the path has no `plugins/cache/<marketplace>/<plugin>` spine — a dev
    checkout or worktree — and None means SKIP, never guess.
    """
    p = os.path.realpath(root or plugin_root())
    parts = p.split(os.sep)
    for i in range(len(parts) - 3):
        if parts[i] == "plugins" and parts[i + 1] == "cache":
            # parts[i+2] is the marketplace, parts[i+3] the plugin — both must exist.
            config_dir = os.sep.join(parts[:i])
            marketplace = parts[i + 2]
            if config_dir and marketplace:
                return config_dir, marketplace
    return None


def declared_connectors(root=None):
    """The connector names this plugin's manifest declares under `metadata.connectors`.

    Strings only; anything else is ignored rather than guessed at. An unreadable manifest
    returns [] — the caller prints the loud line, because silence is the one wrong answer."""
    manifest = os.path.join(root or plugin_root(), ".claude-plugin", "plugin.json")
    try:
        with open(manifest, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    names = (data.get("metadata") or {}).get("connectors") or []
    return [n for n in names if isinstance(n, str) and n]


def installed_plugin_names(registry_path):
    """Plugin names (the part before '@') with at least one install record, or None when the
    registry cannot be read. None is distinct from set() ON PURPOSE: an unreadable registry is
    unknown, not empty — the caller treats unknown as 'attempt the install', which is safe
    because the install is idempotent (measured: exit 0, ~1.5s when already installed)."""
    try:
        with open(registry_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    plugins = data.get("plugins") or {}
    if not isinstance(plugins, dict):
        return None
    return {key.split("@", 1)[0] for key, records in plugins.items() if records}


def _run_install(spec):
    """Default runner: spawn the CLI exactly as the probe did — non-TTY, stdin closed, hard
    timeout. Returns (returncode, combined_output_text). Raises only what the caller catches."""
    exe = os.environ.get("CLAUDE_CODE_EXECPATH") or "claude"
    proc = subprocess.run(
        [exe, "plugin", "install", spec],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=INSTALL_TIMEOUT,
        text=True,
    )
    return proc.returncode, (proc.stdout or "").strip()


def _loud_failure(out, spec, detail):
    print("%s — jobsearch could not install its companion connector %r." % (BANNER, spec),
          file=out)
    if detail:
        print("   the installer said: %s" % detail, file=out)
    print("   Mail tools (gmail_*) are UNAVAILABLE until it is installed. Remedy, run by hand:",
          file=out)
    print("       claude plugin install %s" % spec, file=out)
    print("   jobsearch itself keeps running; nothing else is affected.", file=out)


def ensure(root=None, runner=None, out=None):
    """The SessionStart entry point. Always returns 0 — see the module contract."""
    out = out or sys.stdout
    runner = runner or _run_install
    root = root or plugin_root()

    identity = derive_install_identity(root)
    if identity is None:
        print("connector self-install skipped: this engine is not running from an installed "
              "plugin cache (dev checkout?) — no marketplace to derive, nothing attempted.",
              file=out)
        return 0
    config_dir, marketplace = identity

    connectors = declared_connectors(root)
    if connectors is None:
        print("%s — this plugin's own manifest (.claude-plugin/plugin.json) is unreadable, so "
              "its declared connectors are UNKNOWN, not absent. Nothing attempted." % BANNER,
              file=out)
        return 0
    if not connectors:
        return 0

    registry = os.path.join(config_dir, "plugins", "installed_plugins.json")
    present = installed_plugin_names(registry)
    if present is None:
        print("note: %s is unreadable; treating declared connectors as possibly missing and "
              "attempting an (idempotent) install." % registry, file=out)
        present = set()

    for name in connectors:
        if name in present:
            continue                       # fast path: one JSON read, no CLI spawn
        spec = "%s@%s" % (name, marketplace)
        try:
            code, text = runner(spec)
        except FileNotFoundError:
            _loud_failure(out, spec, "the `claude` CLI is not on this hook's PATH")
            continue
        except subprocess.TimeoutExpired:
            _loud_failure(out, spec, "the install did not finish within %ss" % INSTALL_TIMEOUT)
            continue
        except Exception as exc:           # exit 0 on every path — never break session start
            _loud_failure(out, spec, "unexpected error: %s" % exc)
            continue
        if code == 0:
            print("jobsearch installed its companion connector %s. Connector tools load at "
                  "session start, so they arrive in your NEXT session — this one still runs "
                  "without them." % spec, file=out)
        else:
            _loud_failure(out, spec, text or "exit %s with no output" % code)
    return 0


def main():
    try:
        return ensure()
    except Exception as exc:               # the hook chain must proceed no matter what
        print("%s — connector self-install crashed (%s); continuing without it." % (BANNER, exc))
        return 0


if __name__ == "__main__":
    sys.exit(main())
