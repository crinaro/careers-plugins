#!/usr/bin/env python3
"""The per-company knowledge stores, joined to the pipeline. Resolves against the data model.

⭐ THE DEFECT THIS CLOSES (GitHub issue #12)
-------------------------------------------
The profile has two places for durable, accumulating knowledge about a target organization —
`kb/<company_id>.md` and the dated `call_preps/call_prep_<date>.md` notes — and neither
accumulated reliably, for three structural reasons:

1. **NO JOIN.** The kb filename was free-form, so nothing connected a pipeline row to its
   accumulated history or back. In a live profile the naming drifted silently (a business unit
   where the id names the parent, a different word separator) and no check noticed, because no
   check existed.
2. **PREP NOTES ARE KEYED BY DATE.** One dated file holds conversations with unrelated
   organizations; a second conversation with the SAME organization lands under an unrelated
   filename. Assembling one relationship's history required already knowing the dates — exactly
   what the store was supposed to remember.
3. **THE PROMOTION PATH WAS PROSE.** "Promote durable content to the kb BEFORE archiving"
   existed only as narrative, so nothing detected a prep archived without promotion, and the kb
   never grew.

This is the fourth instance of one defect — ⭐ A FACT A RUN KNOWS GOES INTO THE QUERYABLE STORE,
NEVER INTO NARRATIVE — and it copies the established shape (`act_by`, `precondition.py`,
`m_0_18_0`) rather than inventing a fifth: a named field, a strict parser that refuses what it
cannot read, and a resolver against data that already exists.

## ⚠️ Why prep notes STAY date-keyed — the reversal is deliberate, and this module respects it

Organization-named prep notes were already tried: `call_prep_<company>.md` lived at the repo
root, and `generate_dashboard.py`'s docstring still records one being promoted to
`kb/<company>.md`. On 2026-08-03 the candidate himself asked for the current form — preps go
stale as DOCUMENTS long before their content does, one day's file can cover several unrelated
calls, so live preps are date-keyed in `call_preps/` and durable content is promoted to the
org-keyed `kb/`. A regression test (`test_promoted_kb_knowledge_survived`) guards that
retirement. **So the fix here is a recorded JOIN from the dated note to the organizations it
discusses — never a re-keying of the note itself.**

## The fields

kb file (`kb/*.md`) — the filename stem IS the join when it equals a `companies.jsonl` id;
otherwise the file must carry one, on its own line:

    **Company:** company:<company_id>

call-prep note (`call_preps/*.md`) — which organizations this day's conversations concern:

    **Companies:** company:<id> company:<id> channel:<channel_id>
    **Companies:** none                      # explicitly: no tracked organization

(`channel:<id>` covers a call with a recruiting firm that is a channel, not a target company —
resolved against `data/channels.jsonl`, so a firm call needs no fake company row.)

archived prep (`archive/call-preps/*.md`) — the promotion, recorded at archive time:

    **Promoted:** kb:<company_id> on <date>  # one kb: token per file that received content
    **Promoted:** nothing-durable            # explicitly: reviewed, nothing worth keeping

A `kb:<id>` claim is verified: the id must resolve AND `kb/<id>.md` must exist non-empty —
a promotion whose target is missing or empty lost its content, and says so loudly.

## States

    joined / promoted / none / nothing-durable   resolved against the data model
    unresolved   known gap, not yet structured — what `migrate.py`'s 0.19.0 migration marks,
                 and what a file with no field and no resolving filename reports as
    unreadable   a field nobody can parse, or an id that resolves to nothing — LOUD, never
                 guessed over (a store that looks populated and answers nothing is the whole
                 failure this module closes)
    missing      an active pursuit at a conversation stage (screening/interviewing/offer)
                 with no joined kb file — the accumulation the run must perform

⚠️ `--check` fails on `unreadable` and `unresolved`. It does NOT fail on `missing`: the gate
would trip the moment a stage advances, BEFORE the run that creates the file can act, and wedge
the very run that fixes it. `missing` is reported with the exact path to create instead.

Usage:
    python3 knowledge.py            # every store row: state + why
    python3 knowledge.py --json
    python3 knowledge.py --check    # exit 1 on an unreadable or unresolved join/promotion

Python 3.9+. Standard library only.
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root

KB_FIELD_RE = re.compile(r"^\*\*Company:\*\*\s*(.+?)\s*$", re.M | re.I)
PREP_FIELD_RE = re.compile(r"^\*\*Companies:\*\*\s*(.+?)\s*$", re.M | re.I)
PROMOTED_RE = re.compile(r"^\*\*Promoted:\*\*\s*(.+?)\s*$", re.M | re.I)
# The id class includes ':' because channel ids really carry one (`firm:<slug>`); a colon that
# produces a nonexistent id fails resolution loudly rather than being silently truncated.
TOKEN_RE = re.compile(r"\b(company|channel|kb)\s*:\s*([A-Za-z0-9_.:-]+)")

# The literal the migration writes when it finds a gap it cannot structure itself.
UNRESOLVED_RE = re.compile(r"^unresolved\b", re.I)

# Stages at which a pursuit is IN CONVERSATION and its history must have somewhere to accumulate.
CONVERSATION_STAGES = frozenset({"screening", "interviewing", "offer"})
# Statuses that mean the pursuit is live. Mirrors validate_data.OPP_STATUS minus the terminal
# and shelved values — `passed`/`expired` are over, `backlog` has no conversations yet.
ACTIVE_STATUSES = frozenset({"active-pursuit", "needs-resolution", "in-motion"})

# States --check fails on. `missing` is deliberately absent — see the module docstring.
LOUD = frozenset({"unreadable", "unresolved"})

# kb/ files exempt from the join requirement: documentation about the folder, not org knowledge.
KB_EXEMPT = frozenset({"readme.md"})

# Both spellings scanned: the regression suite names archive/call-preps/, older trees may carry
# the underscore form. Scanning a directory that does not exist is a no-op, not an error.
ARCHIVE_DIRS = (os.path.join("archive", "call-preps"), os.path.join("archive", "call_preps"))


class KnowledgeError(ValueError):
    """Unparseable. Deliberately loud — see the module docstring."""


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _jsonl(root, name):
    rows = []
    try:
        with open(os.path.join(root, "data", name), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return rows


def company_ids(root):
    return {r.get("id") for r in _jsonl(root, "companies.jsonl") if r.get("id")}


def channel_ids(root):
    return {r.get("id") for r in _jsonl(root, "channels.jsonl") if r.get("id")}


def parse_tokens(raw, allowed, cids, chids):
    """`company:x channel:y` -> sorted company ids, validating every token. Raises loudly."""
    found = TOKEN_RE.findall(raw or "")
    if not found:
        raise KnowledgeError("no `%s:<id>` token in %r"
                             % ("|".join(sorted(allowed)), raw))
    companies = []
    for kind, ident in found:
        if kind not in allowed:
            raise KnowledgeError("`%s:` is not valid here (%r) — allowed: %s"
                                 % (kind, raw, ", ".join(sorted(allowed))))
        pool = chids if kind == "channel" else cids
        if ident not in pool:
            raise KnowledgeError(
                "%s:%s resolves to no row in data/%s.jsonl — a join to nothing looks handled "
                "and is not" % (kind, ident, "channels" if kind == "channel" else "companies"))
        if kind != "channel":
            companies.append(ident)
    return sorted(set(companies))


def kb_rows(root, cids):
    """One row per kb/*.md: joined / unresolved / unreadable. Also returns the joined ids."""
    rows, joined = [], set()
    kb_dir = os.path.join(root, "kb")
    for name in sorted(os.listdir(kb_dir)) if os.path.isdir(kb_dir) else []:
        if not name.endswith(".md") or name.lower() in KB_EXEMPT:
            continue
        rel = os.path.join("kb", name)
        try:
            md = _read(os.path.join(kb_dir, name))
        except OSError as e:
            rows.append({"kind": "kb", "file": rel, "state": "unreadable", "why": str(e)})
            continue
        m = KB_FIELD_RE.search(md)
        stem = name[:-3]
        if m:
            raw = m.group(1)
            if UNRESOLVED_RE.match(raw):
                rows.append({"kind": "kb", "file": rel, "state": "unresolved",
                             "why": "join not yet structured (%s) — replace with "
                                    "`**Company:** company:<id>`" % raw})
                continue
            try:
                ids = parse_tokens(raw, {"company"}, cids, set())
            except KnowledgeError as e:
                rows.append({"kind": "kb", "file": rel, "state": "unreadable", "why": str(e)})
                continue
            joined.update(ids)
            rows.append({"kind": "kb", "file": rel, "state": "joined",
                         "why": "field: company:%s" % "|".join(ids)})
        elif stem in cids:
            joined.add(stem)
            rows.append({"kind": "kb", "file": rel, "state": "joined",
                         "why": "filename is the company id"})
        else:
            rows.append({"kind": "kb", "file": rel, "state": "unresolved",
                         "why": "filename resolves to no company id and the file carries no "
                                "`**Company:** company:<id>` — this history is unreachable "
                                "from the pipeline"})
    return rows, joined


def _prep_row(kind, rel, md, cids, chids):
    m = PREP_FIELD_RE.search(md)
    if not m:
        return {"kind": kind, "file": rel, "state": "unresolved",
                "why": "no `**Companies:**` line — nothing joins this dated note to the "
                       "organizations it discusses; add `company:<id>` token(s), or `none`"}
    raw = m.group(1)
    if UNRESOLVED_RE.match(raw):
        return {"kind": kind, "file": rel, "state": "unresolved",
                "why": "join not yet structured (%s) — replace with `company:<id>` "
                       "token(s), or `none`" % raw}
    if raw.strip().lower() == "none":
        return {"kind": kind, "file": rel, "state": "none",
                "why": "explicitly concerns no tracked organization"}
    try:
        ids = parse_tokens(raw, {"company", "channel"}, cids, chids)
    except KnowledgeError as e:
        return {"kind": kind, "file": rel, "state": "unreadable", "why": str(e)}
    return {"kind": kind, "file": rel, "state": "joined",
            "why": "companies: %s" % ("|".join(ids) or "(channel only)")}


def prep_rows(root, cids, chids):
    rows = []
    d = os.path.join(root, "call_preps")
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if name.endswith(".md"):
            rows.append(_prep_row("prep", os.path.join("call_preps", name),
                                  _read(os.path.join(d, name)), cids, chids))
    return rows


def archived_prep_rows(root, cids, chids):
    """Archived preps answer BOTH questions: which orgs, and was durable content promoted."""
    rows = []
    for sub in ARCHIVE_DIRS:
        d = os.path.join(root, sub)
        for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if not name.endswith(".md"):
                continue
            rel = os.path.join(sub, name)
            md = _read(os.path.join(d, name))
            rows.append(_prep_row("archived-prep", rel, md, cids, chids))
            m = PROMOTED_RE.search(md)
            if not m:
                rows.append({"kind": "promotion", "file": rel, "state": "unresolved",
                             "why": "archived with no `**Promoted:**` record — the exact "
                                    "silent path by which the kb never grows; record "
                                    "`kb:<id>` or `nothing-durable`"})
                continue
            raw = m.group(1)
            if UNRESOLVED_RE.match(raw):
                rows.append({"kind": "promotion", "file": rel, "state": "unresolved",
                             "why": "promotion not yet recorded (%s) — promote, then record "
                                    "`kb:<id>` or `nothing-durable`" % raw})
                continue
            if raw.strip().lower().startswith("nothing-durable"):
                rows.append({"kind": "promotion", "file": rel, "state": "nothing-durable",
                             "why": "explicitly reviewed; nothing worth keeping"})
                continue
            try:
                ids = parse_tokens(raw, {"kb"}, cids, set())
            except KnowledgeError as e:
                rows.append({"kind": "promotion", "file": rel, "state": "unreadable",
                             "why": str(e)})
                continue
            bad = []
            for cid in ids:
                target = os.path.join(root, "kb", cid + ".md")
                try:
                    ok = bool(_read(target).strip())
                except OSError:
                    ok = False
                if not ok:
                    bad.append(cid)
            if bad:
                rows.append({"kind": "promotion", "file": rel, "state": "unreadable",
                             "why": "claims promotion to kb/%s.md, which is missing or empty "
                                    "— the promotion lost its content" %
                                    ".md, kb/".join(bad)})
            else:
                rows.append({"kind": "promotion", "file": rel, "state": "promoted",
                             "why": "promoted to kb/%s.md" % ".md, kb/".join(ids)})
    return rows


def pursuit_rows(root, joined):
    """An active pursuit in conversation must have a kb file to accumulate into."""
    rows = []
    for r in _jsonl(root, "opportunities.jsonl"):
        if r.get("status") not in ACTIVE_STATUSES:
            continue
        if r.get("stage") not in CONVERSATION_STAGES:
            continue
        cid = r.get("company_id")
        if cid and cid not in joined:
            rows.append({"kind": "pursuit", "file": r.get("id") or "?", "state": "missing",
                         "why": "stage=%s and no joined kb file — create kb/%s.md so this "
                                "relationship's history has somewhere to accumulate"
                                % (r.get("stage"), cid)})
    return rows


def report(root):
    cids = company_ids(root)
    chids = channel_ids(root)
    rows, joined = kb_rows(root, cids)
    rows += prep_rows(root, cids, chids)
    rows += archived_prep_rows(root, cids, chids)
    rows += pursuit_rows(root, joined)
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if any join or promotion is unreadable or unresolved")
    args = ap.parse_args()

    rows = report(profile_root())
    if args.json:
        print(json.dumps(rows, indent=1))
    else:
        print("KNOWLEDGE STORES — can each pursuit find its own history?\n")
        for r in rows:
            mark = {"joined": "✅", "promoted": "✅", "none": "▫️", "nothing-durable": "▫️",
                    "unresolved": "🚧", "unreadable": "⛔", "missing": "📄"}[r["state"]]
            print("  %s %-15s %-13s %s" % (mark, r["state"], r["kind"], r["file"]))
            print("        %s" % r["why"])
        n_loud = sum(1 for r in rows if r["state"] in LOUD)
        n_miss = sum(1 for r in rows if r["state"] == "missing")
        n_ok = len(rows) - n_loud - n_miss
        print("\n  %d resolved · %d unjoined/unrecorded · %d pursuit(s) with no kb file"
              % (n_ok, n_loud, n_miss))
        if n_loud:
            print("  🚧 An unjoined file is a store that looks populated and answers nothing.")
            print("     Structure each with company:<id> / kb:<id> — or `none` / "
                  "`nothing-durable` if that is the truth.")
        if n_miss:
            print("  📄 Create the named kb file(s) THIS run — accumulation is the run's job,")
            print("     not something a person must remember.")

    if args.check:
        bad = [r for r in rows if r["state"] in LOUD]
        for r in bad:
            print("⛔ %s [%s]: %s" % (r["file"], r["state"], r["why"]), file=sys.stderr)
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
