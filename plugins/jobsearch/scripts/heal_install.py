#!/usr/bin/env python3
"""Startup self-heal for the install manifest (marketplace issue #11, adr-014).

⭐ WHY THIS EXISTS
------------------
Neither `claude plugin install` nor `claude plugin update` rewrites the per-plugin manifest under
`~/.claude/plugins/.install-manifests/` — verified twice on consecutive releases on 2026-08-11,
including a full uninstall-then-reinstall cycle that left the manifest byte-identical (same mtime,
same `createdAt`, same file count). `installed_plugins.json` moves correctly — `installPath` and
`gitCommitSha` both update — while the manifest keeps describing the PREVIOUS version. The
release gate `check_install.py` then reports drift on every release, and until this script the
repair was a hand edit — the exact "and then someone runs a command" failure the rulebook forbids.

So the engine heals it itself, at session start, from the same `SessionStart` hook that already
migrates profile data (`migrate.py --hook`): one mechanism keeps both the user's DATA and their
INSTALL current with the running version.

## ⭐⭐ THE SCOPE RULE: HEAL ONLY THE MISMATCH THAT IS EXPLAINED

The manifest exists to detect tampering and corruption. Regenerating whenever hashes mismatch
would turn `check_install.py` into a script that erases its own failure. The one mismatch this
script repairs is the installer defect, stated as a testable condition:

    EXPLAINED (healed)      the manifest's recorded hash of `.claude-plugin/plugin.json` differs
                            from the file on disk — the version-carrying file itself moved —
                            AND `installed_plugins.json` records the same version the disk does
                            (i.e. the installer RAN and updated everything except the manifest).

    UNEXPLAINED (loud,      same version on both sides but hashes differ; or the version file
    never written)          was never recorded; or `installed_plugins.json` disagrees with the
                            disk. That is corruption or tampering, not the installer defect,
                            and it must stay visible until a human decides.

    MISSING MANIFEST        reported, never fabricated. First install demonstrably writes one,
    (loud, never written)   so absence is anomalous — inventing a baseline would stamp
                            install-time provenance on files this script cannot vouch for.

⚠️ The write target is a file OWNED BY CLAUDE CODE. Schema observed 2026-08-11:
`{pluginId, createdAt, files}`. A heal preserves every field it does not understand, replaces
only `files`, appends a `heals` audit record, and leaves the replaced content beside the manifest
as `<name>.json.bak-heal` — preserve, then transform. A future Claude Code build that rewrites
manifests itself simply finds nothing to heal.

⚠️ FAILS OPEN, ALWAYS — same rule as `migrate.py`: housekeeping must never block a session.
And silent when healthy: the hook output is for things a person should see.

The guard question (adr-010) is resolved deliberately in adr-014, not routed around: this script
is the ONE sanctioned writer of a manifest, and `guard_engine_writes.py` now denies Write/Edit
tool calls into `.install-manifests/` unconditionally, so a session cannot hand-"repair" the
tamper record the way it was hand-repaired twice on 2026-08-11.

Usage:
    python3 heal_install.py            # heal if explained; loud if not; silent if healthy
    python3 heal_install.py --check    # report only; writes nothing
    python3 heal_install.py --root P   # diagnose an explicit installed copy (test seam, and
                                       # lets release-manager point it at a cache path)

Exit codes: 0 healthy/healed/not-installed · 1 unexplained/no-manifest (so it can serve as a
check) — but `migrate.py` calls `heal()` in-process and keeps its own always-0 contract.

Python 3.9+. Standard library only.
"""

import argparse
import hashlib
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _diag import log as diag

# Mirror scripts/check_install.py in the marketplace repo: transient files the installer never
# records. Recording them would guarantee false drift on the very next session.
IGNORE_DIRS = ("__pycache__", ".git", ".in_use")
IGNORE_SUFFIX = (".pyc", ".pyo")
PLUGIN_JSON = os.path.join(".claude-plugin", "plugin.json")
MAX_HEAL_RECORDS = 10


def plugins_dir_default():
    """`CLAUDESEARCH_PLUGINS_DIR` is a test seam, same shape as CLAUDESEARCH_DIAG_LOG — the
    suite must exercise this against a synthetic plugins dir, never the live install."""
    return (os.environ.get("CLAUDESEARCH_PLUGINS_DIR")
            or os.path.join(os.path.expanduser("~"), ".claude", "plugins"))


def engine_root_default():
    """The installed copy this code is physically running from. realpath, like
    `_root.engine_root()`: the engine is where the FILE IS."""
    return os.path.dirname(os.path.dirname(os.path.realpath(__file__)))


def interesting(rel):
    parts = rel.split(os.sep)
    if any(p in IGNORE_DIRS for p in parts):
        return False
    return not rel.endswith(IGNORE_SUFFIX)


def tree_hashes(root):
    out = {}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for f in files:
            p = os.path.join(base, f)
            rel = os.path.relpath(p, root)
            if not interesting(rel):
                continue
            try:
                with open(p, "rb") as fh:
                    out[rel] = hashlib.sha256(fh.read()).hexdigest()
            except OSError:
                pass
    return out


def disk_version(root):
    try:
        with open(os.path.join(root, PLUGIN_JSON), encoding="utf-8") as fh:
            return json.load(fh).get("version") or ""
    except Exception:
        return ""


def find_install(plugins_dir, root):
    """(plugin_id, entry) for the installed copy at `root`, or (None, None).

    Matching by realpath(installPath) is the safety property that matters most here: a checkout,
    a CI clone, or a test tree is never an installPath, so running this code from anywhere but a
    genuine install finds nothing and writes nothing.
    """
    try:
        with open(os.path.join(plugins_dir, "installed_plugins.json"), encoding="utf-8") as fh:
            plugins = json.load(fh).get("plugins") or {}
    except Exception:
        return None, None
    want = os.path.realpath(root)
    for plugin_id, entries in plugins.items():
        for entry in entries or []:
            path = (entry or {}).get("installPath")
            if path and os.path.realpath(path) == want:
                return plugin_id, entry
    return None, None


def heal(root, plugins_dir, apply_it=True):
    """Returns (verdict, lines). Never raises.

    verdict: not-installed | healthy | healed | would-heal | unexplained | no-manifest | error
    """
    try:
        return _heal(root, plugins_dir, apply_it)
    except Exception as e:                     # noqa: BLE001 — fails open, deliberately
        diag("heal_install", verdict="error", reason=type(e).__name__)
        return "error", ["  ⚠️ install self-heal skipped (%s: %s)" % (type(e).__name__, e)]


def _heal(root, plugins_dir, apply_it):
    plugin_id, entry = find_install(plugins_dir, root)
    if not plugin_id:
        diag("heal_install", verdict="not-installed")
        return "not-installed", []

    man_path = os.path.join(plugins_dir, ".install-manifests", "%s.json" % plugin_id)
    try:
        with open(man_path, encoding="utf-8") as fh:
            doc = json.load(fh)
        recorded = {k: v for k, v in (doc.get("files") or {}).items() if interesting(k)}
    except Exception:
        diag("heal_install", verdict="no-manifest")
        return "no-manifest", [
            "  ⚠️ no readable install manifest at %s" % man_path,
            "     First install writes one, so its absence is anomalous — nothing was invented",
            "     to replace it. `scripts/check_install.py` (marketplace repo) shows the state;",
            "     a reinstall of the plugin is the only writer with install-time authority."]

    disk = tree_hashes(root)
    missing = sorted(k for k in disk if k not in recorded)
    drifted = sorted(k for k in disk if k in recorded and recorded[k] != disk[k])
    vanished = sorted(k for k in recorded if k not in disk)
    if not (missing or drifted or vanished):
        diag("heal_install", verdict="healthy", files=len(disk))
        return "healthy", []

    ver = disk_version(root)
    explained = (PLUGIN_JSON in recorded and PLUGIN_JSON in disk
                 and recorded[PLUGIN_JSON] != disk[PLUGIN_JSON]
                 and bool(ver) and entry.get("version") == ver)
    if not explained:
        diag("heal_install", verdict="unexplained", files_disk=len(disk),
             files_manifest=len(recorded), drifted=len(drifted))
        return "unexplained", [
            "  ⛔ install manifest disagrees with the installed files and the mismatch is NOT",
            "     explained by a version move (%d drifted · %d unrecorded · %d vanished, at "
            "version %s)." % (len(drifted), len(missing), len(vanished), ver or "unknown"),
            "     Same-version drift is corruption or tampering, not the installer defect, so",
            "     nothing was rewritten — this stays loud on purpose (adr-014). Compare with",
            "     `scripts/check_install.py` in the marketplace repo; a reinstall of the plugin",
            "     is the honest repair once the cause is understood."]

    if not apply_it:
        diag("heal_install", verdict="would-heal", engine=ver)
        return "would-heal", [
            "  would rewrite the install manifest for %s — the installed version (%s) is not the"
            % (plugin_id, ver),
            "  one the manifest records (its recorded `.claude-plugin/plugin.json` differs), which",
            "  is the installer defect of marketplace issue #11 (adr-014)."]

    # ── PRESERVE, THEN TRANSFORM ─────────────────────────────────────────────────────────────
    try:
        with open(man_path, "rb") as fh:
            old_bytes = fh.read()
        with open(man_path + ".bak-heal", "wb") as fh:
            fh.write(old_bytes)
    except OSError as e:
        diag("heal_install", verdict="backup-failed", reason=type(e).__name__)
        return "error", ["  ⚠️ could not preserve the old manifest (%s) — healed nothing." % e]

    doc["files"] = {k: disk[k] for k in sorted(disk)}
    heals = doc.setdefault("heals", [])
    heals.append({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  "by": "jobsearch heal_install (adr-014)", "toVersion": ver,
                  "reason": "version-moved", "filesBefore": len(recorded),
                  "filesAfter": len(disk)})
    del heals[:-MAX_HEAL_RECORDS]
    tmp = man_path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1)
            fh.write("\n")
        os.replace(tmp, man_path)              # atomic: never a half-written manifest
    except OSError as e:
        diag("heal_install", verdict="write-failed", reason=type(e).__name__)
        return "error", ["  ⚠️ manifest rewrite failed (%s) — old manifest left in place." % e]
    diag("heal_install", verdict="healed", engine=ver, files=len(disk))
    return "healed", [
        "  ✅ install manifest healed to %s (%d file(s) recorded; previous content preserved as"
        % (ver, len(disk)),
        "     %s.bak-heal). The mismatch was explained by a version move — the installer" % os.path.basename(man_path),
        "     updates the files and `installed_plugins.json` but not the manifest (issue #11)."]


def heal_default(apply_it=True):
    """What `migrate.py --hook` calls: the running install, the real plugins dir."""
    return heal(engine_root_default(), plugins_dir_default(), apply_it=apply_it)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="report only; write nothing")
    ap.add_argument("--root", help="an installed copy to diagnose (default: the one running)")
    args = ap.parse_args()
    root = os.path.abspath(args.root) if args.root else engine_root_default()
    verdict, lines = heal(root, plugins_dir_default(), apply_it=not args.check)
    if lines:
        print("jobsearch: install self-heal (%s)" % verdict)
        print("\n".join(lines))
    elif verdict == "healthy":
        print("Install manifest matches the installed files (%s)." % root)
    elif verdict == "not-installed":
        print("Not an installed copy (%s) — nothing to heal." % root)
    return 1 if verdict in ("unexplained", "no-manifest") else 0


if __name__ == "__main__":
    sys.exit(main())
