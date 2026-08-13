#!/usr/bin/env python3
"""Apply profile migrations when the engine version moves. Idempotent; safe to run every session.

Since marketplace issue #11 this is also the startup home of the INSTALL self-heal
(`heal_install.py`, adr-014): the same `SessionStart` hook keeps both the user's data and their
install current with the running version, and neither ever asks the user to run a command.

⭐ WHY THIS EXISTS
------------------
0.4.0 retired two `focus.md` sections. Shipping that as "run this script" put the work on every
installer, who would have to know the migration existed at all — **that does not scale past the
one person who happened to read the release note.** A plugin that changes the shape of a user's
data has to carry the change with it.

## ⭐⭐ THE RULE THAT SHAPES EVERY MIGRATION HERE: SAFE APPLIES, DESTRUCTIVE REPORTS.

A migration runs unattended, at session start, on data this engine does not own. So:

    SAFE         idempotent, and losslessly reversible from git -> APPLIED automatically.
    DESTRUCTIVE  could discard something recorded nowhere else -> REPORTED, never applied.

The retired-sections migration is the exact case: removing two dead headings is safe, **but not
if `🔧 Open` still holds items nobody filed** — those exist in no other place, and deleting them
at session start would be silent data loss the user never asked for. So it refuses and says what
to do. `retire_process_sections.py` already encodes that judgement; this runner defers to it.

⚠️ **FAILS OPEN, ALWAYS.** A migration that errors must never block a session — the user would be
locked out of their own search by a housekeeping step. Every failure path exits 0 and says so.

⚠️ **RESOLVES THE PROFILE FROM THE CURRENT DIRECTORY ONLY — never the remembered pointer.** The
pointer exists so a process with no cwd can still find the profile, which is exactly wrong here:
a session running in the ENGINE repo would otherwise resolve the user's profile and migrate it
from a session that has no business touching it. No profile under cwd means nothing to do.

The stamp lives in the PROFILE (`.jobsearch-schema`), because it records what has been done to
THIS user's data, not what version of the engine happens to be installed.

Usage:
    python3 migrate.py --check     # what is pending; writes nothing
    python3 migrate.py             # apply the safe ones, report the rest
    python3 migrate.py --hook      # same, but silent when there is nothing to say

Python 3.9+. Standard library only.
"""

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _diag import log as diag

STAMP = ".jobsearch-schema"
MARKERS = ("config.json", "data")


def engine_version():
    try:
        with open(os.path.join(os.path.dirname(HERE), ".claude-plugin", "plugin.json"),
                  encoding="utf-8") as fh:
            return json.load(fh).get("version") or "0.0.0"
    except Exception:
        return "0.0.0"


def profile_from_cwd():
    """Walk up from cwd. Deliberately NOT `profile_root()` — see the module docstring."""
    cur = os.path.abspath(os.getcwd())
    while True:
        if any(os.path.exists(os.path.join(cur, m)) for m in MARKERS):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def ver(s):
    try:
        return tuple(int(x) for x in str(s).split(".")[:3])
    except Exception:
        return (0, 0, 0)


def read_stamp(profile):
    """The schema version this profile has been migrated to.

    ⭐ ACCEPTS BOTH SHAPES. The stamp was a bare version string; it is now a small JSON
    record that also carries the last ATTEMPT (see write_stamp). A profile written by an
    older engine still holds the bare string, and must keep working untouched — the record
    is an addition, never a precondition."""
    try:
        with open(os.path.join(profile, STAMP), encoding="utf-8") as fh:
            raw = fh.read().strip()
    except OSError:
        return "0.0.0"
    if raw.startswith("{"):
        try:
            return str(json.loads(raw).get("schema") or "0.0.0")
        except ValueError:
            return "0.0.0"
    return raw or "0.0.0"


def read_attempt(profile):
    """The last migration ATTEMPT, or None if this profile has never recorded one.

    ⭐⭐ THIS IS THE POINT OF GitHub #41. The stamp recorded only the version ACHIEVED, so
    `nothing to migrate` and `never looked` were the same observation — a stamp eleven
    minors behind, a hook present the whole time, and no way to tell whether it had ever
    run. An attempt record makes the difference visible: a run that found nothing still
    writes one, so an ABSENT record means the hook genuinely never fired."""
    try:
        with open(os.path.join(profile, STAMP), encoding="utf-8") as fh:
            raw = fh.read().strip()
        if raw.startswith("{"):
            return json.loads(raw).get("last_attempt") or None
    except (OSError, ValueError):
        pass
    return None


def write_stamp(profile, version):
    """Write the schema stamp. Never raises (see module docstring: FAILS OPEN, ALWAYS) — but
    an OSError here used to be swallowed silently and reported as though it had succeeded
    (GitHub #8). That reproduces this project's worst-case shape: work gets APPLIED, the profile
    is left recording the OLD schema version, and every subsequent session re-applies the same
    migrations forever, with nothing anywhere saying so. Returns (ok, error) instead — the
    caller in main() is responsible for being LOUD about a failure; this function only reports
    one, it never hides it."""
    return write_stamp_record(profile, version, None)


def write_stamp_record(profile, version, attempt):
    """Write the stamp, optionally recording what the last attempt DID.

    ⭐ `attempt` is written even when nothing needed doing — that is the whole value. A run
    that found nothing to do leaves `{"result": "no-op"}`, so a MISSING record means the
    migration never ran at all, which is the condition #41 could not distinguish."""
    doc = {"schema": version}
    prior = read_attempt(profile)
    if attempt is not None:
        doc["last_attempt"] = attempt
    elif prior is not None:
        doc["last_attempt"] = prior
    try:
        with open(os.path.join(profile, STAMP), "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True)
            fh.write("\n")
        return True, None
    except OSError as e:
        return False, e


def attempt_record(engine, result, detail=""):
    import datetime
    rec = {"at": datetime.datetime.now().replace(microsecond=0).isoformat(),
           "engine": engine, "result": result}
    if detail:
        rec["detail"] = detail[:200]
    return rec


def record_noop(profile, engine):
    """Record that a migration run happened and found nothing to do.

    ⚠️ RATE-LIMITED ON PURPOSE. The profile is usually a git repository, and stamping every
    single SessionStart would put a one-line commit's worth of churn in the user's own
    history for the rest of time. Once per (engine version, day) is enough to answer the
    only question this record exists for — did it EVER run — without becoming noise."""
    prior = read_attempt(profile) or {}
    today = attempt_record(engine, "no-op")["at"][:10]
    if prior.get("engine") == engine and str(prior.get("at", ""))[:10] == today:
        return True, None
    return write_stamp_record(profile, read_stamp(profile),
                              attempt_record(engine, "no-op"))


def m_0_4_0(profile, apply_it):
    """0.4.0 — retire the Process sections that the dashboard no longer renders.

    Delegates to retire_process_sections.py, which refuses when `🔧 Open` still has content.
    That refusal IS the destructive-vs-safe boundary: nothing here forces past it.
    """
    cmd = [sys.executable, os.path.join(HERE, "retire_process_sections.py")]
    if not apply_it:
        cmd.append("--check")
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=profile)
    out = (r.stdout or "") + (r.stderr or "")
    if "already clean" in out or "nothing (already clean)" in out:
        return True, ""
    if r.returncode == 0:
        return True, "  ✅ focus.md — retired the Process sections the dashboard no longer renders."
    # The script relocates rather than refusing, so a non-zero here is a genuine failure
    # (unwritable archive, unreadable focus.md) — not a decision waiting on a human.
    return False, ("  ⚠️ focus.md could not be migrated and was left unchanged:\n     %s"
                   % (out.strip().splitlines() or ["unknown error"])[-1][:160])


def m_0_13_0(profile, apply_it):
    """0.13.0 — the dashboard title becomes DATA, and this preserves the one already in use.

    ⭐ PRESERVE, THEN TRANSFORM. `generate_dashboard.py` used to write a hard-coded title
    naming one person, in three places. It now renders
    `config.dashboard.title_template` × `user.json`'s name, defaulting to a neutral form.

    Left alone, an upgrade would silently rename every existing dashboard. So carry the
    existing title forward — read from the dashboard THIS PROFILE last generated, never from a
    literal in the engine. Copying the string into this file would just move the leak from the
    generator into the migration.

    No dashboard yet, or no title in it: nothing to preserve, and the new default applies.
    """
    import re as _re
    cfg_path = os.path.join(profile, "config.json")
    if not os.path.exists(cfg_path):
        return True, ""
    try:
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception as e:
        return False, "  ⚠️ config.json is unreadable, so the dashboard title was left alone: %s" % e
    if (cfg.get("dashboard") or {}).get("title_template"):
        return True, ""                      # already carries its own title

    existing = ""
    for candidate in ("dashboard.html", "dashboard_artifact.html"):
        path = os.path.join(profile, candidate)
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                m = _re.search(r"<title>(.*?)</title>", fh.read(), _re.S)
            if m and m.group(1).strip():
                existing = m.group(1).strip()
                break
        except Exception:
            continue
    if not existing:
        return True, ""                      # nothing generated yet — the default is correct

    if not apply_it:
        return True, "  would preserve the current dashboard title: %r" % existing
    cfg.setdefault("dashboard", {})["title_template"] = existing
    tmp = cfg_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, cfg_path)                # atomic: never a half-written config.json
    return True, "  ✅ config.json — dashboard title preserved as %r" % existing


def m_0_14_0(profile, apply_it):
    """0.14.0 — `access` states the REQUIREMENT; the mechanism moves to config.

    Channel records carried values like `login-chrome`, which fused what a channel NEEDS
    (a signed-in session) with how we happened to reach it in 2026 (the Chrome extension).
    The in-app Browser pane and dedicated site plugins both arrived later, so the mechanism
    had to change and every record naming one became wrong at once.

    Rewrites the legacy values in place and seeds `config.sourcing.route_preference` if the
    profile has none. Resolution lives in `route.py`; nothing here decides a mechanism.
    """
    import route as _route
    cpath = os.path.join(profile, "data", "channels.jsonl")
    cfgpath = os.path.join(profile, "config.json")
    if not os.path.exists(cpath):
        return True, ""

    rows, changed = [], 0
    try:
        with open(cpath, encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                row = json.loads(line)
                raw = row.get("access")
                if raw in _route.LEGACY:
                    row["access"] = _route.LEGACY[raw]
                    changed += 1
                rows.append(row)
    except Exception as e:
        return False, "  ⚠️ channels.jsonl could not be read, so routes were left alone: %s" % e

    seed = False
    cfg = {}
    if os.path.exists(cfgpath):
        try:
            with open(cfgpath, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except Exception as e:
            return False, "  ⚠️ config.json is unreadable, so routes were left alone: %s" % e
        seed = not ((cfg.get("sourcing") or {}).get("route_preference"))

    if not changed and not seed:
        return True, ""
    if not apply_it:
        return True, ("  would rewrite %d legacy channel access value(s)%s"
                      % (changed, " and seed config.sourcing.route_preference" if seed else ""))

    if changed:
        tmp = cpath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.replace(tmp, cpath)          # atomic: never a half-written pipeline file
    if seed:
        cfg.setdefault("sourcing", {})["route_preference"] = list(_route.DEFAULT_PREFERENCE)
        cfg["sourcing"].setdefault("plugins", {})
        tmp = cfgpath + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, cfgpath)
    bits = []
    if changed:
        bits.append("%d channel access value(s) now state the requirement, not the mechanism"
                    % changed)
    if seed:
        bits.append("config.sourcing.route_preference seeded to %s"
                    % " -> ".join(_route.DEFAULT_PREFERENCE))
    return True, "  ✅ " + "; ".join(bits)


def m_0_17_0(profile, apply_it):
    """0.17.0 — adr-012: the profile is always a git repository; `config.sync.mode` declares
    the remote.

    Seeds the declaration FROM WHAT THE PROFILE ACTUALLY IS — never a default that overwrites
    an existing one: repo with an origin -> "remote"; repo without -> "local-only"; plain
    folder -> ⭐ PRESERVE, THEN TRANSFORM: `git init`, then an initial commit staging the
    profile's known artifacts BY EXPLICIT PATH (never `git add -A` — the list of what the
    initial commit contains is also documentation of what a profile is), then "local-only".
    Additive and reversible by deleting `.git/`; a plain folder has no second writer, so the
    shared-tree scar behind the explicit-path rule cannot recur here, and the rule is kept
    anyway.

    git absent from PATH: report loudly, apply NOTHING, and return not-ok so the schema is
    never stamped — the unstamped migration retries every session, so the condition cannot go
    quiet (adr-012's defined behaviour for its residual uncertainty). An unreadable declared
    mode is likewise refused, never guessed over: `sync.py --set` is the fix, and it validates.
    """
    import sync as _sync
    from _atomic import write_json as _write_json
    cfgpath = os.path.join(profile, "config.json")
    if not os.path.exists(cfgpath):
        return True, ""                 # a data/-only tree; nothing to declare a mode in yet
    try:
        with open(cfgpath, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception as e:
        return False, "  ⚠️ config.json is unreadable, so no sync mode was seeded: %s" % e
    try:
        if _sync.parse_mode(cfg):
            return True, ""             # already declared — never overwritten, valid by parse
    except _sync.SyncError as e:
        return False, ("  ⚠️ config.sync.mode exists but cannot be read (%s).\n"
                       "     Nothing was overwritten — fix it with `sync.py --set "
                       "remote|local-only`, which validates first." % str(e)[:120])

    state = _sync.git_state(profile)
    if not state["git"]:
        return False, ("  ⚠️ git is not on PATH, so this profile cannot become a repository and\n"
                       "     no sync mode was seeded. Marketplace installs require git — this\n"
                       "     machine changed since install. Restore git; this retries every "
                       "session.")

    def _git(*a):
        return subprocess.run(["git", "-C", profile] + list(a),
                              capture_output=True, text=True, timeout=30)

    if not apply_it:
        if not state["repo"]:
            return True, ("  would `git init`, commit the profile's known artifacts by explicit "
                          "path, and seed sync.mode: local-only")
        return True, ("  would seed sync.mode: %s (from what the repository is)"
                      % ("remote" if state["origin"] else "local-only"))

    steps = []
    if not state["repo"]:
        r = _git("init", "-q")
        if r.returncode != 0:
            return False, ("  ⚠️ `git init` failed; nothing changed: %s"
                           % (r.stderr.strip() or "unknown")[:160])
        steps.append("git init")
        state = _sync.git_state(profile)

    # An initial commit when the repository has none — covers both the fresh `git init` above
    # and a half-completed earlier attempt. A repo that already has commits is the user's own
    # history; nothing here stages into it.
    if _git("rev-parse", "-q", "--verify", "HEAD").returncode != 0:
        known = ["config.json", "user.json", "data", "kb", "call_preps", "archive"]
        try:
            known += sorted(n for n in os.listdir(profile)
                            if n.endswith(".md") and os.path.isfile(os.path.join(profile, n)))
        except OSError:
            pass
        paths = [p for p in known if os.path.exists(os.path.join(profile, p))]
        r = _git("add", "--", *paths)
        if r.returncode != 0:
            return False, ("  ⚠️ staging the initial commit failed; not stamped: %s"
                           % (r.stderr.strip() or "unknown")[:160])
        ident = []
        if not (_git("config", "user.email").stdout or "").strip():
            # No git identity on this machine. Use a neutral one for THIS commit only via -c —
            # never written into the user's config, and their real identity takes over the
            # moment they set one.
            ident = ["-c", "user.name=jobsearch-migrate", "-c", "user.email=migrate@localhost"]
        r = subprocess.run(["git", "-C", profile] + ident +
                           ["commit", "-q", "-m",
                            "profile becomes a git repository (adr-012 migration)"],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return False, ("  ⚠️ the initial commit failed; not stamped (retries next session): %s"
                           % (r.stderr.strip() or r.stdout.strip() or "unknown")[:160])
        steps.append("initial commit (%d path(s), each named)" % len(paths))

    mode = "remote" if state["origin"] else "local-only"
    cfg.setdefault("sync", {})["mode"] = mode
    _write_json(cfgpath, cfg)           # atomic — never a half-written config.json
    steps.append("sync.mode seeded: %s" % mode)
    return True, "  ✅ " + "; ".join(steps)


def m_0_18_0(profile, apply_it):
    """0.18.0 — draft preconditions held in prose become data (GitHub issue #13).

    `precondition.py` shipped the `**Blocked until:**` field with no migration, so a profile
    predating it kept its holds in prose and the tool reported every such draft **sendable** —
    a false green with the authority of a gate, strictly worse than the prose it replaced.

    ⭐ PRESERVE, THEN TRANSFORM. For each drafts.md entry whose text carries a hold phrase
    (`precondition.HOLD_RE`) but no structured field, insert

        **Blocked until:** unresolved (migrated 0.18.0 from prose)

    directly under the title. The prose stays untouched — it is the human-readable evidence —
    and the FACT that the draft is blocked moves into the queryable store, where
    `precondition.py` reports it `unresolved` (never sendable) until someone replaces the
    marker with the real join, `contact:<id> outcome:<...>`. Nothing here guesses the contact:
    a guessed join that resolves against the wrong person is the same false green again.

    Additive and idempotent: entries already carrying ANY `**Blocked until:**` field are
    skipped, so a second run finds nothing to do. SAFE by this module's own rule — inserts a
    line, deletes nothing, reversible from git.
    """
    import precondition as _pre
    path = os.path.join(profile, "drafts.md")
    if not os.path.exists(path):
        return True, ""
    try:
        with open(path, encoding="utf-8") as fh:
            md = fh.read()
    except OSError as e:
        return False, "  ⚠️ drafts.md could not be read, so no preconditions were marked: %s" % e

    import re as _re
    marked, out, pos = 0, [], 0
    for m in _re.finditer(r"^##\s+(.+?)$(.*?)(?=^##\s|\Z)", md, _re.M | _re.S):
        title, body = m.group(1).strip(), m.group(2)
        needs = (not _pre.FIELD_RE.search(body)
                 and (_pre.HOLD_RE.search(title) or _pre.HOLD_RE.search(body)))
        out.append(md[pos:m.end(1)])
        if needs:
            out.append("\n**Blocked until:** unresolved (migrated 0.18.0 from prose)")
            marked += 1
        out.append(md[m.end(1):m.end()])
        pos = m.end()
    out.append(md[pos:])

    if not marked:
        return True, ""
    if not apply_it:
        return True, ("  would mark %d draft(s) whose send-precondition lives only in prose as "
                      "`**Blocked until:** unresolved`" % marked)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("".join(out))
    os.replace(tmp, path)               # atomic: never a half-written drafts.md
    return True, ("  ✅ drafts.md — %d prose precondition(s) marked as data (`**Blocked "
                  "until:** unresolved`); they now report as blocked, not sendable. Structure "
                  "each with contact:<id> outcome:<...> when the join is known." % marked)


def m_0_19_0(profile, apply_it):
    """0.19.0 — the knowledge stores get their join to the pipeline (GitHub issue #12).

    `kb/` filenames were free-form and drifted; dated call-prep notes joined to nothing; the
    promote-before-archive rule lived only in prose. `knowledge.py` now resolves three fields
    (`**Company:**`, `**Companies:**`, `**Promoted:**`) against the data model, and this
    migration marks every pre-existing file so a gap reports as `unresolved` — never as absent,
    and never as fine. Same shape as m_0_18_0; fires when the engine version reaches 0.19.0.

    ⭐ PRESERVE, THEN TRANSFORM — nothing is renamed, moved, or deleted. Files gain one or two
    marker lines; existing content is untouched, so every drifted kb file stays exactly where
    its owner knows it, just LOUDLY unjoined until someone structures the join.

    The one join written confidently: a kb filename that equals a company id after separator
    normalization (`Bluewater_Grid.md` → `bluewater-grid`) gets its `**Company:**` field —
    deterministic, not a guess. A business-unit-vs-parent mismatch or a no-match filename gets
    `unresolved`: guessing that join would file one organization's intel under another,
    which is the same store-that-answers-wrongly this whole issue is about.

    Additive and idempotent: files already carrying a field are skipped. SAFE by this module's
    rule — inserts lines, deletes nothing, reversible from git.
    """
    import knowledge as _kn

    def _insert(path, lines):
        """Insert marker lines after a leading heading (or at the top), atomically."""
        with open(path, encoding="utf-8") as fh:
            md = fh.read()
        body = md.splitlines(True)
        at = 1 if body and body[0].lstrip().startswith("#") else 0
        block = "".join(l + "\n" for l in lines)
        if at and body[0] and not body[0].endswith("\n"):
            block = "\n" + block
        new = "".join(body[:at]) + block + "".join(body[at:])
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(new)
        os.replace(tmp, path)

    def _norm(s):
        import re as _re
        return _re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")

    cids = _kn.company_ids(profile)
    by_norm = {}
    for cid in cids:
        by_norm.setdefault(_norm(cid), []).append(cid)

    planned = []   # (path, lines, note)
    kb_dir = os.path.join(profile, "kb")
    for name in sorted(os.listdir(kb_dir)) if os.path.isdir(kb_dir) else []:
        if not name.endswith(".md") or name.lower() in _kn.KB_EXEMPT:
            continue
        path = os.path.join(kb_dir, name)
        try:
            with open(path, encoding="utf-8") as fh:
                md = fh.read()
        except OSError:
            continue
        stem = name[:-3]
        if _kn.KB_FIELD_RE.search(md) or stem in cids:
            continue                          # already joined, by field or by name
        hits = by_norm.get(_norm(stem)) or []
        if len(hits) == 1:
            planned.append((path, ["**Company:** company:%s (migrated 0.19.0 from filename)"
                                   % hits[0]], "kb/%s → company:%s" % (name, hits[0])))
        else:
            planned.append((path, ["**Company:** unresolved (migrated 0.19.0 — filename "
                                   "resolves to no company id)"], "kb/%s → unresolved" % name))

    prep_dir = os.path.join(profile, "call_preps")
    for name in sorted(os.listdir(prep_dir)) if os.path.isdir(prep_dir) else []:
        if not name.endswith(".md"):
            continue
        path = os.path.join(prep_dir, name)
        try:
            with open(path, encoding="utf-8") as fh:
                md = fh.read()
        except OSError:
            continue
        if not _kn.PREP_FIELD_RE.search(md):
            planned.append((path, ["**Companies:** unresolved (migrated 0.19.0 — organizations "
                                   "not yet recorded)"], "call_preps/%s → unresolved" % name))

    for sub in _kn.ARCHIVE_DIRS:
        d = os.path.join(profile, sub)
        for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if not name.endswith(".md"):
                continue
            path = os.path.join(d, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    md = fh.read()
            except OSError:
                continue
            lines = []
            if not _kn.PREP_FIELD_RE.search(md):
                lines.append("**Companies:** unresolved (migrated 0.19.0 — organizations "
                             "not yet recorded)")
            if not _kn.PROMOTED_RE.search(md):
                lines.append("**Promoted:** unresolved (migrated 0.19.0 — promotion not "
                             "recorded)")
            if lines:
                planned.append((path, lines, "%s → unresolved" % os.path.join(sub, name)))

    if not planned:
        return True, ""
    if not apply_it:
        return True, ("  would mark %d knowledge-store file(s) whose join or promotion is "
                      "unrecorded (kb/, call_preps/, archived preps)" % len(planned))
    for path, lines, _note in planned:
        _insert(path, lines)
    return True, ("  ✅ knowledge stores — %d file(s) marked: joins written where the filename "
                  "resolves deterministically, `unresolved` where only a human can say. "
                  "`knowledge.py --check` is loud until each is structured; nothing was "
                  "renamed, moved, or deleted." % len(planned))


def m_0_20_0(profile, apply_it):
    """0.20.0 — a config KEY that names the owner becomes generic (GitHub issue #46).

    `compensation.standout_exception_requires_<owner>` carries the owner's first name in the
    KEY. The rulebook's fixture rule is explicit that only structure crosses over and every
    string is synthesized *because even map keys can be personal data* — and this one reached
    a public repository inside the generated test fixture, where the purity gate could not
    see it: the gate matched VALUES, and `\\b` treats `_` as a word character, so a name
    inside an identifier was invisible to it. Both halves are fixed (#45); this is the data.

    ⭐ PRESERVE, THEN TRANSFORM. The value is carried across before the old key is removed,
    so a profile that had it set to false keeps false. Nothing in the engine reads this key
    today, which is what makes the rename safe rather than a behaviour change.

    Idempotent: a profile already carrying the new key, or neither key, is a no-op.
    """
    path = os.path.join(profile, "config.json")
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except (OSError, ValueError):
        return True, ""
    comp = cfg.get("compensation")
    if not isinstance(comp, dict):
        return True, ""
    old = [k for k in comp if k.startswith("standout_exception_requires_")
           and k != "standout_exception_requires_owner"]
    if not old:
        return True, ""
    if not apply_it:
        return True, ("  would rename %d compensation key(s) that name the owner to "
                      "`standout_exception_requires_owner`" % len(old))
    for k in old:
        comp.setdefault("standout_exception_requires_owner", comp[k])
        del comp[k]
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except OSError as e:
        return False, "  could not rewrite config.json: %s" % e
    return True, ("  renamed %d compensation key(s) naming the owner to "
                  "`standout_exception_requires_owner`" % len(old))


MIGRATIONS = (("0.4.0", m_0_4_0), ("0.13.0", m_0_13_0), ("0.14.0", m_0_14_0),
              ("0.17.0", m_0_17_0), ("0.18.0", m_0_18_0), ("0.19.0", m_0_19_0),
              ("0.20.0", m_0_20_0))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="report only; write nothing")
    ap.add_argument("--hook", action="store_true", help="silent when there is nothing to say")
    args = ap.parse_args()

    # ── install self-heal, BEFORE the profile half (marketplace issue #11, adr-014) ─────────
    # One mechanism keeps both halves current with the running version: this hook migrates the
    # user's DATA below, and heals the INSTALL here. The heal does not depend on a profile —
    # the install belongs to the machine — so it runs even when the cwd has nothing to migrate.
    # Its own envelope, so a heal crash can never cost the profile its migrations.
    try:
        import heal_install
        verdict, h_lines = heal_install.heal_default(apply_it=not args.check)
        if h_lines:
            print("jobsearch: install self-heal (%s)" % verdict)
            print("\n".join(h_lines))
    except Exception as e:                     # noqa: BLE001 — housekeeping must never block
        diag("migrate", verdict="heal-error", reason=type(e).__name__)
        if not args.hook:
            print("Install self-heal skipped: %s" % e, file=sys.stderr)

    try:
        profile = profile_from_cwd()
        if not profile:
            diag("migrate", verdict="no-profile", mode="hook" if args.hook else "cli")
            if not args.hook:
                print("No profile under the current directory — nothing to migrate.")
            return 0

        engine = engine_version()
        stamp = read_stamp(profile)
        pending = [(v, fn) for v, fn in MIGRATIONS if ver(stamp) < ver(v) <= ver(engine)]

        if not pending:
            diag("migrate", verdict="current", engine=engine, stamp=stamp)
            # ⭐ RECORD THE NO-OP (#41). Without this, "ran and found nothing" and "never ran
            # at all" leave identical traces, which is exactly how a stamp sat eleven minors
            # behind while the hook shipped in every version across that range.
            if not args.check:
                record_noop(profile, engine)
            if not args.hook:
                print("Profile is current (schema %s, engine %s)." % (stamp, engine))
            return 0

        lines, all_done = [], True
        for v, fn in pending:
            ok, msg = fn(profile, apply_it=not args.check)
            all_done = all_done and ok
            if msg:
                lines.append(msg)

        if lines:
            print("jobsearch: profile migration %s → %s" % (stamp, engine))
            print("\n".join(lines))
        diag("migrate", verdict=("applied" if all_done else "refused"),
             engine=engine, stamp=stamp, pending=len(pending),
             mode=("check" if args.check else ("hook" if args.hook else "cli")))
        if all_done and not args.check:
            stamped, err = write_stamp_record(
                profile, engine,
                attempt_record(engine, "applied", "%d migration(s)" % len(pending)))
            if not stamped:
                # ⚠️ LOUD, NEVER SILENT (GitHub #8) — same principle as an unparseable
                # precondition: a failure nobody can see is worse than none. But per this
                # module's FAILS OPEN, ALWAYS rule, being loud must not mean being fatal — this
                # still returns 0 below, because housekeeping must never lock the user out of
                # their own session. The migration DID apply; only the record of it failed, so
                # say exactly that, or the discrepancy between "applied" above and "still 0.x.x
                # next session" reads as a mystery instead of a known, named failure.
                diag("migrate", verdict="stamp-failed", engine=engine, stamp=stamp,
                     reason=type(err).__name__)
                print("  ⚠️ schema stamp could not be written (%s: %s) — the migration WAS "
                      "applied but NOT recorded, so it will be re-applied every session until "
                      "the stamp succeeds. Check that %s is writable."
                      % (type(err).__name__, err, os.path.join(profile, STAMP)),
                      file=sys.stderr)
        elif not all_done:
            # Do NOT stamp: leaving it unstamped is what makes this retry next session rather
            # than silently deciding the migration is finished when it is not.
            print("  (Not stamped — this will be offered again next session.)")
        return 0
    except Exception as e:                     # noqa: BLE001 — housekeeping must never block
        diag("migrate", verdict="error", error=type(e).__name__)
        if not args.hook:
            print("Migration check skipped: %s" % e, file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
