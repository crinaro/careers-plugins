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
import shutil
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
            json.dump(doc, fh, indent=1, sort_keys=True, ensure_ascii=False)
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
    return _mark_prose_holds(profile, "drafts.md", apply_it, "0.18.0")


def _mark_prose_holds(profile, filename, apply_it, label):
    """Shared body of m_0_18_0 and m_0_27_0_cover_preconditions (dev #169): mark every '## '
    entry in `filename` whose text carries a hold phrase but no structured field with
    `**Blocked until:** unresolved (migrated <label> from prose)`. PRESERVE, THEN TRANSFORM —
    the prose stays as evidence; only the marker line is inserted. Additive and idempotent."""
    import precondition as _pre
    path = os.path.join(profile, filename)
    if not os.path.exists(path):
        return True, ""
    try:
        with open(path, encoding="utf-8") as fh:
            md = fh.read()
    except OSError as e:
        return False, ("  ⚠️ %s could not be read, so no preconditions were marked: %s"
                       % (filename, e))

    import re as _re
    marked, out, pos = 0, [], 0
    for m in _re.finditer(r"^##\s+(.+?)$(.*?)(?=^##\s|\Z)", md, _re.M | _re.S):
        title, body = m.group(1).strip(), m.group(2)
        needs = (not _pre.FIELD_RE.search(body)
                 and (_pre.HOLD_RE.search(title) or _pre.HOLD_RE.search(body)))
        out.append(md[pos:m.end(1)])
        if needs:
            out.append("\n**Blocked until:** unresolved (migrated %s from prose)" % label)
            marked += 1
        out.append(md[m.end(1):m.end()])
        pos = m.end()
    out.append(md[pos:])

    if not marked:
        return True, ""
    if not apply_it:
        return True, ("  would mark %d entry(s) in %s whose send-precondition lives only in "
                      "prose as `**Blocked until:** unresolved`" % (marked, filename))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("".join(out))
    os.replace(tmp, path)               # atomic: never a half-written file
    return True, ("  ✅ %s — %d prose precondition(s) marked as data (`**Blocked "
                  "until:** unresolved`); they now report as blocked, not sendable. Structure "
                  "each with contact:<id> outcome:<...> when the join is known."
                  % (filename, marked))


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
            # ⚠️ ensure_ascii=False. The default escapes every non-ASCII character, so a
            # one-key rename rewrote 74 unrelated lines of a HAND-EDITED file as \uXXXX
            # escapes — a diff its owner cannot review, and characters they typed coming
            # back as escape sequences (#3).
            json.dump(cfg, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except OSError as e:
        return False, "  could not rewrite config.json: %s" % e
    return True, ("  renamed %d compensation key(s) naming the owner to "
                  "`standout_exception_requires_owner`" % len(old))


def m_0_24_0_blocked_until(profile, apply_it):
    """0.24.0 (part 1 of 2) — Your Move role state gets its own field (GitHub issue #79).

    The "needs you" queue used to select rows by ownership alone, so a role future-dated
    weeks out and one genuinely overdue rendered identically. `your_move.py` now groups by a
    `blocked_until` field; this backfills it from what the profile already has.

    Scans LIVE, candidate-owned opportunities whose `next_action` prose carries a hold phrase
    (`precondition.HOLD_RE`, imported — never restated, same rule m_0_18_0 follows for
    drafts). Exactly one of the record's OWN `outreach[]` rows with `outcome: awaiting` names
    a single plausible contact: writes the real join, `contact:<id>
    outcome:accepted|replied`. Zero or more than one candidate is ambiguous: writes the
    literal `unresolved`, which `your_move.py` treats as its own loud callout, never
    "needs you", until a human structures the real join. No hold phrase: no field written.

    ⭐ PRESERVE, THEN TRANSFORM. `next_action`'s prose is never deleted or rewritten — it
    stays the human-readable evidence; only the FACT that the role is blocked moves into the
    queryable store (the general rule `precondition.py` documents). Additive and idempotent:
    a row already carrying ANY `blocked_until` (from an earlier run, or hand-authored) is
    skipped, so a second run finds nothing left to do.
    """
    import precondition as _pre
    import your_move as _ym
    import profile as _profile
    path = os.path.join(profile, "data", "opportunities.jsonl")
    if not os.path.exists(path):
        return True, ""
    try:
        owner = _profile.owner_token()
    except Exception:
        owner = None
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except Exception as e:
        return False, ("  ⚠️ opportunities.jsonl could not be read, so no blocked_until was "
                       "backfilled: %s" % e)

    changed, marked = 0, []
    for r in rows:
        if "blocked_until" in r:
            continue
        if r.get("next_action_owner") != owner or r.get("status") not in _ym.LIVE_OPP_STATUSES:
            continue
        text = str(r.get("next_action") or "")
        if not _pre.HOLD_RE.search(text):
            continue
        awaiting = sorted({o.get("contact_id") for o in (r.get("outreach") or [])
                           if o.get("contact_id") and o.get("outcome") == "awaiting"})
        if len(awaiting) == 1:
            r["blocked_until"] = "contact:%s outcome:accepted|replied" % awaiting[0]
            marked.append("%s → contact:%s" % (r.get("id", "?"), awaiting[0]))
        else:
            r["blocked_until"] = "unresolved"
            marked.append("%s → unresolved (%s)"
                          % (r.get("id", "?"), "no awaiting touch" if not awaiting
                             else "%d candidate contacts" % len(awaiting)))
        changed += 1

    if not changed:
        return True, ""
    if not apply_it:
        return True, ("  would backfill blocked_until on %d opportunity(ies) whose "
                      "next_action carries a hold phrase" % changed)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)               # atomic: never a half-written pipeline file
    return True, ("  ✅ opportunities.jsonl — blocked_until backfilled on %d role(s): %s"
                  % (changed, "; ".join(marked)[:300]))


def m_0_24_0_last_touch(profile, apply_it):
    """0.24.0 (part 2 of 2) — `last_touch` is removed from the channel schema (GitHub #79).

    `your_move.py`'s derived-touch computation replaces it: the max of an outbound message in
    messages.jsonl joined by contact_id, and the latest log[] entry. Nothing ever wrote
    last_touch mechanically (only two hand-written test cases and a dead read in
    check_action_claims.py did), so removing it costs no live capability. REQUIRED, not
    optional tidying: unknown keys are rejected at write time (schema.md), so a channel still
    carrying this key would fail record.py's write guard the next time anything touched it.

    ⭐ PRESERVE, THEN TRANSFORM. Any hand-authored value is folded into `log[]` as
    `{"date": <its value>, "note": "(migrated from last_touch)"}` before the key is dropped —
    nothing is discarded, it just moves to where the schema still allows it. Additive from
    log[]'s point of view and idempotent: a row with no last_touch is untouched.
    """
    path = os.path.join(profile, "data", "channels.jsonl")
    if not os.path.exists(path):
        return True, ""
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except Exception as e:
        return False, "  ⚠️ channels.jsonl could not be read, so last_touch was left alone: %s" % e

    changed, folded = 0, []
    for r in rows:
        if "last_touch" not in r:
            continue
        val = r.pop("last_touch")
        r.setdefault("log", []).append({"date": val, "note": "(migrated from last_touch)"})
        folded.append("%s (%s)" % (r.get("id", "?"), val))
        changed += 1

    if not changed:
        return True, ""
    if not apply_it:
        return True, ("  would fold last_touch into log[] and remove it on %d channel(s): %s"
                      % (changed, "; ".join(folded)[:300]))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)               # atomic: never a half-written pipeline file
    return True, ("  ✅ channels.jsonl — last_touch folded into log[] and removed on %d "
                  "channel(s): %s" % (changed, "; ".join(folded)[:300]))


def m_0_25_0_play_stage(profile, apply_it):
    """0.25.0 — the post-application play position becomes a field (public #19 / dev #95).

    The play sequence (needs application → applied → reach the recruiter through an insider →
    … → awaiting reply) could only be recorded by prefixing free-text NUMBERED MARKERS onto
    `next_action` — "3) …" — which nothing can filter, group, count, sort or validate. The
    schema now carries `play_stage`, enum-gated by validate_data.py.

    ⭐ PRESERVE, THEN TRANSFORM — and never guess. The prose is left verbatim (it remains the
    human-readable evidence); only the FACT that a play position exists moves into the
    queryable store. A number alone cannot say WHICH stage it encodes — the numbering was
    invented per-profile, per-session — so this writes the literal `unresolved`, exactly as
    m_0_24_0_blocked_until does for an ambiguous hold: valid, durable, loudly incomplete. The
    way out is a human (or the coordinator) replacing it:
    `record.py set <id> play_stage <value>`.

    Skips: rows already carrying `play_stage` (idempotent), and terminal rows (`passed`/
    `expired` — the validator refuses a play position on a role that left the funnel).
    """
    import re as _re
    # A leading numbered marker: "1) ", "(2) ", "3. ", "4: ", "[5]", "step 2 …". Anchored at
    # the start so a date ("2026-08-20 call") or a time ("3pm") never matches.
    marker = _re.compile(r"^\s*(?:\(?\d{1,2}\s*[\).:\]]\s|step\s+\d{1,2}\b)", _re.I)
    path = os.path.join(profile, "data", "opportunities.jsonl")
    if not os.path.exists(path):
        return True, ""
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except Exception as e:
        return False, ("  ⚠️ opportunities.jsonl could not be read, so no play_stage was "
                       "backfilled: %s" % e)

    changed, marked = 0, []
    for r in rows:
        if "play_stage" in r:
            continue
        if r.get("status") in ("passed", "expired"):
            continue
        if not marker.match(str(r.get("next_action") or "")):
            continue
        r["play_stage"] = "unresolved"
        marked.append(r.get("id", "?"))
        changed += 1

    if not changed:
        return True, ""
    if not apply_it:
        return True, ("  would mark play_stage 'unresolved' on %d opportunity(ies) whose "
                      "next_action carries a numbered play marker" % changed)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)               # atomic: never a half-written pipeline file
    return True, ("  ✅ opportunities.jsonl — play_stage 'unresolved' on %d role(s) with a "
                  "numbered play marker in next_action: %s. Each needs a human to set the real "
                  "value (record.py set <id> play_stage <value>)."
                  % (changed, "; ".join(marked)[:300]))


FOCUS_RETIRED_MARKER = "RETIRED as a source of state (dev #93)"


def m_0_25_0_focus_retirement(profile, apply_it):
    """0.25.0 — focus.md is retired as a source of state (dev #93 / public #21).

    The owner's call: "Keep the tabs and remove the use of focus.md, use the data in the
    json files." The verified failure behind it: a record hand-copied into focus.md's
    action-needed list duplicated its auto-rendered row and went stale, still claiming
    action was needed after it was not. The dashboard keeps both tabs; their content moves
    to stores a view cannot disagree with:

        ## ⚡ Your Move (numbered items)      -> data/asks.jsonl        kind=role
        ## ⚙️ Process — ⚡ Needs … (items)    -> data/asks.jsonl        kind=system
        ## 📅 This Week (items/bullets)      -> data/commitments.jsonl (unparseable date ->
                                                the literal `unresolved`, LOUD downstream)
        ## 🔗 Session Handoff                -> handoff.md — narrative for the next session,
                                                deliberately NOT forced into JSONL: a letter
                                                is not a record, and the stores it points at
                                                are already queryable.
        anything else with real content      -> appended to process_archive.md, stamped

    ⭐⭐ PRESERVE, THEN TRANSFORM — in that order, mechanically. Every relocation is written
    and verified BEFORE focus.md is touched; a failure part-way leaves content in BOTH
    places (recoverable), never in neither. A migration that refused because content would
    be lost has not shipped (this repo's own Process-section lesson); nothing here refuses.
    Finally focus.md is replaced with a frozen stub naming the new homes, which is also the
    idempotency marker: a stubbed file migrates to nothing on every later run. Ask and
    commitment ids are content-derived (a short hash), so even a re-run against a restored
    focus.md cannot duplicate rows. The two stores are created (empty) even when focus.md is
    absent — doctor.py asserts their existence, and an absent store must mean "not migrated
    yet", never "migrated on a profile that happened to have no focus.md".
    """
    import datetime as _dt
    import hashlib as _hashlib
    import re as _re

    data_dir = os.path.join(profile, "data")
    focus_path = os.path.join(profile, "focus.md")

    def _ensure_stores():
        made = []
        if os.path.isdir(data_dir):
            for name in ("asks.jsonl", "commitments.jsonl"):
                p = os.path.join(data_dir, name)
                if not os.path.exists(p):
                    if apply_it:
                        open(p, "a").close()
                    made.append("data/%s" % name)
        return made

    if not os.path.exists(focus_path):
        made = _ensure_stores()
        if made:
            return True, ("  %s empty store(s): %s (no focus.md to carry content from)"
                          % ("✅ created" if apply_it else "would create", ", ".join(made)))
        return True, ""

    try:
        with open(focus_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as e:
        return False, "  ⚠️ focus.md could not be read, so nothing was migrated: %s" % e

    if FOCUS_RETIRED_MARKER in text:
        _ensure_stores()
        return True, ""                     # already migrated — the stub is the marker

    # ---- parse the sections ------------------------------------------------
    def _section(pat):
        m = _re.search(r"(^##\s*" + pat + r".*?$)(.*?)(?=^##\s|\Z)", text, _re.M | _re.S)
        return (m.group(1) + m.group(2)) if m else ""

    def _items(body):
        out = []
        for line in body.splitlines():
            im = _re.match(r"^\s*(?:\d+\.|[-*])\s+(?:\*\*(.+?)\*\*\s*[—:-]?\s*)?(.*)$", line)
            if im and (im.group(1) or im.group(2).strip()):
                title = (im.group(1) or im.group(2).strip()[:80]).strip()
                rest = im.group(2).strip()
                out.append((title, rest))
        return out

    today = _dt.date.today().isoformat()

    def _hid(prefix, *parts):
        h = _hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:8]
        return "%s-mig-%s" % (prefix, h)

    # An opp_id is only carried when it RESOLVES — validate_data.py enforces referential
    # integrity on asks, and a migration must never leave the profile failing its own gate.
    # A tag that does not resolve is folded back into the ask text, so nothing is lost.
    known_opp_ids = set()
    try:
        with open(os.path.join(data_dir, "opportunities.jsonl"), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    known_opp_ids.add(json.loads(line).get("id"))
    except (OSError, ValueError):
        pass

    ask_rows = []
    ym_body = _section(r"(?:⚡\s*)?Your Move")
    for title, rest in _items(ym_body):
        opp_id = None
        tagm = _re.search(r"\s*\{opp:\s*([a-z0-9-]+)\s*\}\s*$", rest)
        if tagm:
            opp_id = tagm.group(1)
            rest = rest[:tagm.start()].rstrip()
        row = {"id": _hid("ask", "role", title, rest), "kind": "role", "title": title,
               "ask": rest or title, "created": today,
               "note": "migrated 0.25.0 from focus.md ## Your Move"}
        if opp_id and opp_id in known_opp_ids:
            row["opp_id"] = opp_id
        elif opp_id:
            row["ask"] = ("%s (tagged {opp:%s}, which resolves to no record)"
                          % (row["ask"], opp_id))
        ask_rows.append(row)
    needs_body = _section(r"⚙️\s*Process\s*—\s*⚡\s*Needs\b")
    for title, rest in _items(needs_body):
        ask_rows.append({"id": _hid("ask", "system", title, rest), "kind": "system",
                         "title": title, "ask": rest or title, "created": today,
                         "note": "migrated 0.25.0 from focus.md ## Process — Needs"})

    cm_rows = []
    tw_body = _section(r"📅\s*This Week")
    for title, rest in _items(tw_body):
        m = _re.search(r"\b(20\d\d-\d\d-\d\d)\b", "%s %s" % (title, rest))
        # A date that cannot be read mechanically is written as the literal `unresolved` —
        # LOUD downstream (validator, dashboard, check_sections), never guessed and never
        # dropped. Same precedent as blocked_until and play_stage.
        cm_rows.append({"id": _hid("cm", title, rest), "date": m.group(1) if m else "unresolved",
                        "title": title, "note": (rest or None),
                        "source": "migrated 0.25.0 from focus.md ## This Week"})

    handoff_body = _section(r"🔗\s*Session Handoff")

    # Everything not carried above, with real content, goes to the archive — including the
    # section headers themselves, so the archived copy stays readable in place.
    carried = [ym_body, needs_body, tw_body, handoff_body]
    leftover = text
    for block in carried:
        if block:
            leftover = leftover.replace(block, "", 1)
    leftover_has_content = any(
        l.strip() and not l.strip().startswith("#") for l in leftover.splitlines())

    if not apply_it:
        return True, ("  would retire focus.md: %d ask(s) -> data/asks.jsonl, %d commitment(s) "
                      "-> data/commitments.jsonl%s%s, then replace focus.md with a frozen stub"
                      % (len(ask_rows), len(cm_rows),
                         ", Session Handoff -> handoff.md" if handoff_body.strip() else "",
                         ", remaining prose -> process_archive.md"
                         if leftover_has_content else ""))

    # ---- PRESERVE (verified) ... -------------------------------------------
    try:
        os.makedirs(data_dir, exist_ok=True)

        def _append_rows(name, rows_new):
            p = os.path.join(data_dir, name)
            have = set()
            if os.path.exists(p):
                with open(p, encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if line:
                            try:
                                have.add(json.loads(line).get("id"))
                            except ValueError:
                                pass
            fresh = [r for r in rows_new if r.get("id") not in have]
            with open(p, "a", encoding="utf-8") as fh:
                for r in fresh:
                    fh.write(json.dumps({k: v for k, v in r.items() if v is not None},
                                        ensure_ascii=False) + "\n")
            # verify: every id must now be present
            with open(p, encoding="utf-8") as fh:
                now_have = {json.loads(l).get("id") for l in fh if l.strip()}
            missing = [r["id"] for r in rows_new if r["id"] not in now_have]
            if missing:
                raise IOError("store write did not verify: %s missing %s" % (name, missing))
            return len(fresh)

        n_asks = _append_rows("asks.jsonl", ask_rows)
        n_cms = _append_rows("commitments.jsonl", cm_rows)

        if handoff_body.strip():
            hp = os.path.join(profile, "handoff.md")
            existing = ""
            if os.path.exists(hp):
                with open(hp, encoding="utf-8") as fh:
                    existing = fh.read()
            payload = ("\n\n## Migrated from focus.md by jobsearch %s\n\n%s"
                       % (engine_version(), handoff_body))
            with open(hp + ".tmp", "w", encoding="utf-8") as fh:
                fh.write((existing or "# Session handoff — a letter to the next session\n")
                         + payload)
            os.replace(hp + ".tmp", hp)
            with open(hp, encoding="utf-8") as fh:
                if handoff_body.strip()[:120] not in fh.read():
                    raise IOError("handoff.md write did not verify")

        if leftover_has_content:
            ap_ = os.path.join(profile, "process_archive.md")
            existing = ""
            if os.path.exists(ap_):
                with open(ap_, encoding="utf-8") as fh:
                    existing = fh.read()
            stamp = "\n\n## Retired from focus.md by jobsearch %s\n\n" % engine_version()
            with open(ap_ + ".tmp", "w", encoding="utf-8") as fh:
                fh.write(existing + stamp + leftover.strip() + "\n")
            os.replace(ap_ + ".tmp", ap_)
    except Exception as e:                   # noqa: BLE001 — refuse to transform on ANY miss
        return False, ("  ⚠️ could not relocate focus.md content (%s) — focus.md was NOT "
                       "changed. Removing content whose relocation failed is the one outcome "
                       "this migration must never produce; it will retry next session." % e)

    # ---- ... THEN TRANSFORM ------------------------------------------------
    stub = (
        "# focus.md — %s\n\n"
        "This file is no longer read or written. Its content moved on %s:\n\n"
        "- Your Move asks        -> `data/asks.jsonl` (kind: role | system)\n"
        "- This Week commitments -> `data/commitments.jsonl`\n"
        "- Session handoff       -> `handoff.md`\n"
        "- everything else       -> `process_archive.md`\n\n"
        "Role and channel state was already generated from `data/*.jsonl`. Edit the "
        "records, never this file.\n" % (FOCUS_RETIRED_MARKER, today))
    with open(focus_path + ".tmp", "w", encoding="utf-8") as fh:
        fh.write(stub)
    os.replace(focus_path + ".tmp", focus_path)   # atomic — never a half-written stub

    bits = ["%d ask(s) -> data/asks.jsonl" % n_asks,
            "%d commitment(s) -> data/commitments.jsonl" % n_cms]
    if handoff_body.strip():
        bits.append("Session Handoff -> handoff.md")
    if leftover_has_content:
        bits.append("remaining prose -> process_archive.md")
    return True, ("  ✅ focus.md retired: %s; focus.md is now a frozen stub naming the new "
                  "homes." % "; ".join(bits))


def m_0_26_0_state_home(profile, apply_it, home=None):
    """0.26.0 — per-profile engine state moves from `~/.claude/jobsearch/` into the profile
    (dev #151).

    `diagnostics.log` and `drift/` were machine-global and keyed by SESSION, so two profiles
    on one machine interleaved in one file with no way to separate them — which made
    `guard_status()`'s own question ("has THIS install had an inert guard for two days?")
    unanswerable the moment a second profile existed. The line that holds: per-profile STATE
    belongs with the profile; only what is needed to FIND a profile (`run`, `profile_root`,
    `engine_root`) stays under `$HOME`. Those three are deliberately untouched.

    ⭐ PRESERVE, THEN TRANSFORM. `diagnostics.log` is the store `doctor`/`whoami` read for
    guard status, so its rows are merged into `<profile>/.jobsearch/diagnostics.log` — global
    rows first, then any the new code already wrote there — and the source is removed only
    after the destination is verified readable. The rows carry no identifiers by `_diag.py`'s
    own contract, so with one known profile they are all attributable to it; a second profile
    migrating later finds nothing left to claim and no-ops. `drift/` markers move the same
    way. `.jobsearch/` is added to the profile's `.gitignore`: the state is machine-local and
    data-free, and committing ring-buffer churn would pollute every commit.

    SAFE (nothing is discarded; everything is relocated), so it applies automatically.
    `home` is a test seam; real runs resolve `$HOME`.
    """
    home = home or os.path.expanduser("~")
    src_dir = os.path.join(home, ".claude", "jobsearch")
    src_log = os.path.join(src_dir, "diagnostics.log")
    src_drift = os.path.join(src_dir, "drift")
    dest_dir = os.path.join(profile, ".jobsearch")
    dest_log = os.path.join(dest_dir, "diagnostics.log")
    dest_drift = os.path.join(dest_dir, "drift")

    have_log = os.path.exists(src_log)
    have_drift = os.path.isdir(src_drift)
    gi_path = os.path.join(profile, ".gitignore")
    try:
        with open(gi_path, encoding="utf-8") as fh:
            gi_lines = [l.strip() for l in fh.read().splitlines()]
    except OSError:
        gi_lines = None                       # no .gitignore yet
    need_gi = not gi_lines or ".jobsearch/" not in gi_lines
    if not (have_log or have_drift or need_gi):
        return True, ""                       # nothing global left, ignore rule in place

    if not apply_it:
        bits = []
        if have_log:
            bits.append("merge ~/.claude/jobsearch/diagnostics.log into .jobsearch/")
        if have_drift:
            bits.append("move ~/.claude/jobsearch/drift/ into .jobsearch/")
        if need_gi:
            bits.append("gitignore .jobsearch/")
        return True, "  would %s" % "; ".join(bits)

    try:
        os.makedirs(dest_dir, exist_ok=True)
        if have_log:
            # PRESERVE: merge, oldest provenance first — the global rows predate anything the
            # relocated writer has appended here — capped at the ring buffer's own size.
            merged = []
            with open(src_log, encoding="utf-8") as fh:
                merged.extend(l for l in fh.readlines() if l.strip())
            if os.path.exists(dest_log):
                with open(dest_log, encoding="utf-8") as fh:
                    merged.extend(l for l in fh.readlines() if l.strip())
            merged = merged[-500:]
            tmp = dest_log + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.writelines(l if l.endswith("\n") else l + "\n" for l in merged)
            os.replace(tmp, dest_log)
            # verify before removing the source — a move that lost the guard history would be
            # exactly the destructive outcome this migration exists to avoid
            with open(dest_log, encoding="utf-8") as fh:
                if len([l for l in fh if l.strip()]) != len(merged):
                    return False, ("  ⚠️ diagnostics.log merge could not be verified; the "
                                   "global log was left in place (retries next session).")
            os.remove(src_log)
        if have_drift:
            os.makedirs(dest_drift, exist_ok=True)
            for name in sorted(os.listdir(src_drift)):
                s, d = os.path.join(src_drift, name), os.path.join(dest_drift, name)
                if os.path.isfile(s):
                    if os.path.exists(d):     # merge marker lines rather than clobbering
                        with open(d, encoding="utf-8") as fh:
                            seen = fh.read().splitlines()
                        with open(s, encoding="utf-8") as fh:
                            extra = [l for l in fh.read().splitlines()
                                     if l and l not in seen]
                        if extra:
                            with open(d, "a", encoding="utf-8") as fh:
                                fh.write("\n".join(extra) + "\n")
                    else:
                        shutil.copy2(s, d)
                    os.remove(s)
            try:
                os.rmdir(src_drift)           # only if now empty
            except OSError:
                pass
        if need_gi:
            existing = ""
            if gi_lines is not None:
                with open(gi_path, encoding="utf-8") as fh:
                    existing = fh.read()
            with open(gi_path + ".tmp", "w", encoding="utf-8") as fh:
                if existing and not existing.endswith("\n"):
                    existing += "\n"
                fh.write(existing + ".jobsearch/\n")
            os.replace(gi_path + ".tmp", gi_path)
    except Exception as e:                    # noqa: BLE001 — preserve on ANY miss
        return False, ("  ⚠️ could not relocate engine state (%s) — nothing was deleted; it "
                       "will retry next session." % e)

    bits = []
    if have_log:
        bits.append("diagnostics.log")
    if have_drift:
        bits.append("drift/")
    what = " and ".join(bits) if bits else "nothing left to move"
    return True, ("  ✅ per-profile engine state (%s) now lives in .jobsearch/ inside this "
                  "profile (gitignored); ~/.claude/jobsearch keeps only the run launcher and "
                  "the two locator pointers." % what)


def m_0_27_0_cover_preconditions(profile, apply_it):
    """0.27.0 — cover-letter send-holds in prose become data (GitHub issue #169, dev #169).

    The 0.18.0 migration converted `drafts.md` only, while `precondition.py` and the dashboard
    now treat `drafts.md` / `cover_letters.md` as the pair `check_sent_drafts.py` always said
    they were. Without this step, a legacy prose hold sitting in `cover_letters.md` predates
    the structured field and was never promoted to the loud `unresolved` marker — invisible
    rather than noisy, so the letter rendered READY on the outward-facing artifact.

    Same body as m_0_18_0 (`_mark_prose_holds`), aimed at the other half of the pair:
    ⭐ PRESERVE, THEN TRANSFORM — the prose stays as human-readable evidence, one marker line
    is inserted under the title, nothing is deleted. Additive and idempotent: entries already
    carrying ANY `**Blocked until:**` field are skipped.
    """
    return _mark_prose_holds(profile, "cover_letters.md", apply_it, "0.27.0")


# The sender fragments alert_sweep.py hardcoded before dev #147 — kept here ONLY as the
# backfill's source of truth for what "already covered" meant. alert_sweep.py itself never
# reads this dict again after the migration runs; it reads channels.jsonl's alert_sender field.
LEGACY_ALERT_SENDERS = {
    "indeed": "from:indeed",
    "linkedin": "from:linkedin",
    "dice": "from:dice",
    "careerbuilder": "from:careerbuilder",
    "ladders": "from:ladders",
    "ziprecruiter": "from:ziprecruiter",
}


def m_0_27_0_alert_sender_backfill(profile, apply_it):
    """0.27.0 — alert_sweep.py's aggregator sender list moves from a hardcoded constant into
    `channels.jsonl` (GitHub issue #147, dev #147).

    Retiring an aggregator channel in the store used to have no effect on the daily alert
    sweep, because the retirement decision (`relationship_status`) and the sweep's source list
    (a Python constant) lived in two disconnected places. `alert_sweep.py` now derives its
    sender list from each channel's own `alert_sender` field and honors `relationship_status:
    retired` automatically — a data decision that no longer needs an engine edit.

    ⭐ PRESERVE, THEN TRANSFORM. This backfills `alert_sender` on any channel whose `id`
    contains one of the six sender keywords the old constant hardcoded (indeed / linkedin /
    dice / careerbuilder / ladders / ziprecruiter), matched as a whole `-`/`_`/`:`-delimited
    token — never a bare substring, so an id like `dice-referral-erin` still matches on `dice`
    but a hypothetical `paradise-health` channel does not. That reproduces EXACTLY the
    coverage the sweep already had (the same six senders), from data instead of code, so
    switching to store-driven sweeping changes no behavior for an existing profile until a
    human acts on it (e.g. retires one). A channel whose id matches no keyword is left alone —
    it was never in the old constant either, so backfilling it would invent coverage that
    never existed.

    Additive and idempotent: a row already carrying `alert_sender` (however it got there,
    including a value of `null`) is never touched.
    """
    import re as _re
    path = os.path.join(profile, "data", "channels.jsonl")
    if not os.path.exists(path):
        return True, ""
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except Exception as e:
        return False, ("  ⚠️ channels.jsonl could not be read, so alert_sender was left "
                       "alone: %s" % e)

    changed, backfilled = 0, []
    for r in rows:
        if "alert_sender" in r:
            continue
        cid = str(r.get("id") or "").lower()
        tokens = _re.split(r"[-_:]", cid)
        match = next((kw for kw in LEGACY_ALERT_SENDERS if kw in tokens), None)
        if not match:
            continue
        r["alert_sender"] = LEGACY_ALERT_SENDERS[match]
        backfilled.append("%s -> %s" % (r.get("id", "?"), LEGACY_ALERT_SENDERS[match]))
        changed += 1

    if not changed:
        return True, ""
    if not apply_it:
        return True, ("  would backfill alert_sender on %d channel(s), preserving the old "
                      "sweep coverage: %s" % (changed, "; ".join(backfilled)[:300]))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)               # atomic: never a half-written pipeline file
    return True, ("  ✅ channels.jsonl — alert_sender backfilled on %d channel(s), preserving "
                  "the old hardcoded sweep coverage: %s" % (changed, "; ".join(backfilled)[:300]))


MIGRATIONS = (("0.4.0", m_0_4_0), ("0.13.0", m_0_13_0), ("0.14.0", m_0_14_0),
              ("0.17.0", m_0_17_0), ("0.18.0", m_0_18_0), ("0.19.0", m_0_19_0),
              ("0.20.0", m_0_20_0), ("0.24.0", m_0_24_0_blocked_until),
              ("0.24.0", m_0_24_0_last_touch), ("0.25.0", m_0_25_0_play_stage),
              ("0.25.0", m_0_25_0_focus_retirement), ("0.26.0", m_0_26_0_state_home),
              ("0.27.0", m_0_27_0_cover_preconditions),
              ("0.27.0", m_0_27_0_alert_sender_backfill))


def pending_for(profile, engine=None):
    """The migrations this profile still needs — THE one definition, exported.

    ⭐⭐ THE GUARD MUST ASK THE REMEDY (GitHub #6). drift_guard.py used to decide there was
    drift by comparing the schema stamp against the ENGINE VERSION, which is a different
    question and has a different answer: the stamp only advances when a migration exists,
    while the version advances on every release. After 0.21.0 and 0.22.0 — neither carrying
    a migration — a correct, fully-migrated profile sat legitimately at 0.20.0, and the
    guard warned on every prompt of every session about a condition no action could clear.
    Running the remedy it named printed "profile is current" and changed nothing.

    That is the exact failure the guard exists to prevent: a warning that always fires is
    one its reader learns to dismiss, so the release that DOES carry a migration produces a
    signal indistinguishable from the noise. Worse, it sent the reader to a script whose own
    output contradicted it, with nothing saying which was wrong.

    Both now read this. They cannot disagree, because there is only one answer.
    """
    engine = engine or engine_version()
    stamp = read_stamp(profile)
    return [v for v, _fn in MIGRATIONS if ver(stamp) < ver(v) <= ver(engine)]


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

    # ⭐⭐ AND THE RULEBOOK, for the same reason and in the same place. It installs into the
    # profile as CLAUDE.md and loads at session start, so a stale copy is read as
    # authoritative — this file's own rule. Nothing called install_rulebook.py from any hook,
    # so the only thing keeping it current was somebody remembering: a live profile was found
    # running 0.17.0 rules under a 0.21.0 engine. Its own envelope, so a failure here cannot
    # cost the profile its migrations.
    try:
        import install_rulebook
        rb_verdict, rb_lines = install_rulebook.refresh_if_stale(apply_it=not args.check)
        if rb_lines:
            print("jobsearch: rulebook (%s)" % rb_verdict)
            print("\n".join(rb_lines))
        if rb_verdict in ("refreshed", "installed"):
            # ⚠️ The FILE is current now; the SESSION is not. A rulebook is read into context
            # once and never reloaded, so the session that refreshed it is still running the
            # previous rules (#7). Leave a flag for drift_guard to say so on the next prompt.
            try:
                import _root
                st = os.path.join(_root.state_root(), "drift")
                os.makedirs(st, exist_ok=True)
                with open(os.path.join(st, "rulebook-refreshed"), "w", encoding="utf-8") as fh:
                    fh.write(engine_version())
            except OSError:
                pass
    except Exception as e:                     # noqa: BLE001
        diag("migrate", verdict="rulebook-error", reason=type(e).__name__)
        if not args.hook:
            print("Rulebook refresh skipped: %s" % e, file=sys.stderr)

    try:
        profile = profile_from_cwd()
        if not profile:
            diag("migrate", verdict="no-profile", mode="hook" if args.hook else "cli")
            if not args.hook:
                print("No profile under the current directory — nothing to migrate.")
            return 0

        engine = engine_version()
        stamp = read_stamp(profile)
        _due = set(pending_for(profile, engine))
        pending = [(v, fn) for v, fn in MIGRATIONS if v in _due]

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
