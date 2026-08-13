#!/usr/bin/env python3
"""
ENGINE vs DATA — does a reusable file still carry one person's data?

WHY THIS EXISTS
---------------
The candidate, 2026-08-03: *"I'm going through the markdowns for the process and agents and too many
still have my data. Can we clean them up and make sure there is a clear separation between the
system and the data (context)?"*

`check_profile_leakage.py` already guards ONE value type: comp figures. A full audit found **394
personal-data markers across all 18 engine files** — names, employers, target companies, cities,
mailboxes, and named individuals. The worst were not the names. They were **rules with one
person's resume baked into them**, e.g. an agent spec naming one specific employer to lead with and three
others as "niche, a stranger won't recognize them." That is a GENERIC rule (lead with the
employer a stranger recognizes) wearing one person's resume. Ship the engine to someone else and
it confidently gives wrong instructions.

## ⭐ THE TAXONOMY — and why NOT everything gets scrubbed

Blanket-stripping names would destroy the thing that makes this repo work: rules that carry the
incident that produced them. So files are classified, and only two classes must be pure.

    ENGINE   must be PORTABLE — zero personal data. A different candidate could use these
             unchanged.  agents/*.md · skills/*/SKILL.md · commands/*.md · scripts/*.py
             · docs/*.md · RULEBOOK.md   (see ENGINE_FAMILIES — the authoritative list)

    PROFILE  IS the personal data, by design.  user.json · config.json · resume.md
             · projects.md · strategy.md · network.md · data/*

    RECORD   the history OF THIS SEARCH. Inherently about this person, never meant to be
             portable, and genericizing it would delete the evidence a rule rests on.
             docs/incident_archive.md · log.md · focus.md · kb/*.md

    MANUAL   RULEBOOK.md — the rulebook (installed into the profile as its CLAUDE.md).
             Rules are engine; the values they operate on are not.
             It may NAME a pointer (`config.json.positioning`) but must not restate a value.

**The test for ENGINE, stated once:** could another candidate use this file unchanged? If a
sentence would be WRONG for them, the fact belongs in `config.json`/`user.json` and the file
should point at it.

Usage:
    python3 scripts/check_engine_purity.py            # exit 1 if an ENGINE file carries data
    python3 scripts/check_engine_purity.py --verbose  # show every hit with its line
    python3 scripts/check_engine_purity.py --all      # audit every class, advisory

Python 3.9+. Standard library only.
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys

import os, sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import engine_root as _engine_root, profile_root as _profile_root

# ⭐⭐ TWO ROOTS, AND CONFLATING THEM IS TRAP #2 IN THE MARKETPLACE RULEBOOK.
#
# The FILES to scan are ENGINE — they live with this script, wherever the plugin is installed.
# The TERMS to scan for are PROFILE — they live in the user's own data directory. These are
# never the same directory, and a checker that reads one when it means the other does not
# raise: it scans an empty set and certifies the result.
#
# ⚠️ THAT IS EXACTLY WHAT THIS FILE DID until 2026-08-09. Both globs resolved under
# `profile_root()`, and they were written before the plugin split moved engine files out of the
# profile entirely. Locally the pointer resolved a real profile, so the ENGINE list came out as
# ONE file — the profile's own CLAUDE.md — and the gate printed
#
#     CLEAN. Every agent spec and run prompt is portable.
#
# having scanned zero agent specs and zero run prompts. In CI, with no profile on the host, it
# read zero terms and exited 0 with a different message. Two vacuity modes, both green, for
# days. This is the marketplace's signature failure — a missing thing reading as an empty thing
# and getting reported as fact — committed by the very gate written to prevent it.
ENGINE_ROOT = _engine_root()
ROOT = _profile_root()

# ⭐ ADRs ARE ENGINE — corrected 2026-08-03. They were first classed RECORD ("the history of
# this search"), which the candidate challenged: *"Are you sure adrs are search decisions or
# architecture? I would have assumed architectural."* That was right, and the name says so —
# Architecture Decision Record. Reading them settled it: ADR-001 separates datasets from
# documents, ADR-002 defines the sourcing schema, ADR-003 stress-tests that schema, ADR-004 sets
# the markdown-vs-JSON rule. Every one decides how the ENGINE is built; not one decides anything
# about a job search. They must therefore be portable like any other spec.
#
# ⭐ THE FAMILIES ARE NAMED, NOT INLINED — so a family that resolves to nothing is VISIBLE.
# The old list was one flat concatenation, which is why `tasks/*.md` could stop existing (it
# became `skills/`) without anything noticing. Each family is now checked for emptiness by
# TestEngineIsPortable.test_every_engine_family_actually_matches_files.
ENGINE_FAMILIES = (
    ("agents", os.path.join(ENGINE_ROOT, "agents", "*.md")),
    ("skills", os.path.join(ENGINE_ROOT, "skills", "*", "SKILL.md")),
    # `commands/` did not exist when this gate was written. It does now, and a command is as
    # portable-or-not as an agent: `commands/linkedin.md` carried a real city four times.
    ("commands", os.path.join(ENGINE_ROOT, "commands", "*.md")),
    # ⭐ scripts/*.py was in the docstring taxonomy from day one and was never in the glob. It
    # is where the data actually accumulated — docstrings carry the incident that produced each
    # rule, and those incidents name real people, employers and cities.
    ("scripts", os.path.join(ENGINE_ROOT, "scripts", "*.py")),
    # ⭐ THE WHOLE docs/ TREE, not just the ADRs — widened 2026-08-09. `schema.md`,
    # `architecture.md` and `deployment.md` describe the ENGINE and ship with it, and a real
    # employer name survived in schema.md precisely because only `adr-*.md` was globbed. The
    # profile's own `incident_archive.md` is RECORD and lives in the PROFILE, not here.
    ("docs", os.path.join(ENGINE_ROOT, "docs", "*.md")),
)

FAMILY_FILES = (sorted(sum((glob.glob(pat) for _n, pat in ENGINE_FAMILIES), []))
          # ⭐ THE RULEBOOK IS IN THE GATE — added 2026-08-04 (then named CLAUDE.md; it is now
          # RULEBOOK.md, the template installed as the profile's CLAUDE.md). The candidate: *"we should be able to
          # copy the claude.md from one person to another and it works (yes the config items will
          # need to change) but other than that, it should work."* It is the file most needing to be
          # portable, and it was the one file NOT being checked, which made "portable" an
          # aspiration rather than a property. It is MANUAL class: rules are engine, values are
          # not, so it may name a pointer but must never restate a value.
          + [os.path.join(ENGINE_ROOT, "RULEBOOK.md")])


# ⭐⭐ COVERAGE IS ENUMERATED, NOT GLOBBED — GitHub #45.
#
# The five families above are a good taxonomy and a terrible file list. They matched **101 of
# the engine's 129 tracked files**, and this gate printed
#
#     CLEAN. Every agent spec and run prompt is portable.
#
# having never opened the other 28 — among them `scripts/*.sh` (the scripts family globs
# `*.py`), `tasks/`, `ci-setup/`, and the whole of `tests/fixtures/`, which is where a KEY
# naming the owner still sits. Three real leaks were found there by a scan written by hand in
# a scratch file, because the gate could not see them.
#
# So the file list now comes from the repository itself: **every tracked file is scanned unless
# it cannot be read as text**, and the count of each is reported. A family that matches nothing
# is still an error — that guard catches a rename — but the families no longer DECIDE what gets
# read. This is the same correction #38 made to term extraction, applied to file selection: stop
# asking a hand-written pattern what exists, and ask the thing that knows.
def _tracked_engine_files():
    """Every tracked file under the engine, from git; a walk when it is an installed copy.

    An installed plugin is a COPY and not a checkout, so `git ls-files` fails there and the
    walk is the real path in production. Both must agree on what they return, which is why
    neither filters by extension."""
    try:
        out = subprocess.run(["git", "-C", ENGINE_ROOT, "ls-files"],
                             capture_output=True, text=True, timeout=60)
        if out.returncode == 0 and out.stdout.strip():
            return sorted(os.path.join(ENGINE_ROOT, p)
                          for p in out.stdout.split("\n") if p.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    found = []
    for dirpath, dirnames, filenames in os.walk(ENGINE_ROOT):
        dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__")]
        found += [os.path.join(dirpath, f) for f in filenames if not f.endswith(".pyc")]
    return sorted(found)


# ⚠️ A KNOWN EXCEPTION MUST NAME AN ISSUE, AND A STALE ONE IS AN ERROR.
#
# The alternative — quietly excluding a path — is how a gate acquires permanent blind spots.
# Each entry is reported on every run, and an entry that no longer matches anything FAILS the
# gate, so an exception cannot outlive the defect it documents.
# Empty, and that is the desired steady state. The first entry — a fixture KEY naming the
# owner (#46) — was removed the moment its migration shipped and the fixture was regenerated,
# because the staleness check FAILED the gate until it was. That is the mechanism working:
# a suppression cannot quietly outlive the defect it documents.
KNOWN_EXCEPTIONS = ()

# Every tracked engine file. The families above remain a taxonomy and an emptiness guard; this
# is what actually gets read.
ENGINE = _tracked_engine_files()

RECORD = {"docs/incident_archive.md", "log.md", "focus.md"}


def _profile_terms():
    """The names to look for come FROM the profile — never hard-coded here, or this script
    becomes the very thing it is checking for."""
    terms = {}
    try:
        with open(os.path.join(ROOT, "user.json"), encoding="utf-8") as fh:
            u = json.load(fh)
        ident = u.get("identity", {})
        for k in ("full_name", "name", "preferred_reference"):
            if ident.get(k):
                for part in re.split(r"\s+", str(ident[k])):
                    if len(part) > 2:
                        terms.setdefault("name", set()).add(part.strip(".,"))
        for m in (u.get("mailboxes") or {}).values() if isinstance(u.get("mailboxes"), dict) \
                else (u.get("mailboxes") or []):
            if isinstance(m, str) and "@" in m:
                terms.setdefault("mailbox", set()).add(m.split("@")[1].split(".")[0])
    except Exception:
        pass
    try:
        with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as fh:
            c = json.load(fh)
        er = (c.get("positioning") or {}).get("employer_recognition") or {}
        for e in (er.get("recognizable") or []) + (er.get("niche_needs_context") or []):
            terms.setdefault("employer", set()).add(e)
        # ⚠️ Extract the CITY TOKEN, not the whole string. The first version required a term
        # with no comma and no space, so a "City, ST" anchor never became a search term at all.
        # The gate then reported CLEAN while a task prompt described searches as "<city>-area
        # on-site" and an agent spec hard-coded that city into a LinkedIn search URL four times.
        # A checker that cannot see the profile's own commute anchors is worse than no checker,
        # because it CERTIFIES the leak. Guarded by
        # TestEngineIsPortable.test_the_checker_can_actually_see_the_profile_s_key_terms.
        geo = c.get("geography") or {}
        for v in re.findall(r'"([^"]{4,60})"', json.dumps(geo)):
            for tok in re.split(r"[,/()]| and |\s{2,}", v):
                tok = tok.strip()
                # a place name: capitalised, not a sentence, not a bare state code
                if (2 < len(tok) < 30 and tok[0].isupper() and tok.count(" ") <= 2
                        and not tok.endswith(".") and tok.upper() != tok):
                    terms.setdefault("geo", set()).add(tok)
    except Exception:
        pass

    # ⭐⭐ TARGET COMPANIES AND CONTACTS COME FROM `data/`, NOT FROM CONFIG — added 2026-08-09.
    #
    # Until now the employer terms came only from `config.positioning.employer_recognition`,
    # i.e. the places this person has WORKED. But the names that actually accumulate in engine
    # docstrings are the ones a run encountered: the company whose ATS lied about a closed req,
    # the recruiter whose draft published empty, the employer 25 minutes away that got
    # mis-tiered. Those live in `data/*.jsonl` and the gate was blind to every one of them —
    # roughly sixty survived across seventeen files while it reported CLEAN.
    #
    # ⚠️ AND NAMING THE FILES IS NOT THE SAME AS READING THEM. This comment said `data/*.jsonl`
    # from the start; the code below read two flat fields in two files, and the people were in
    # a nested array in a third. See the `contacts[]` block at the end of this function.
    #
    # ⚠️ LENGTH FLOOR AND WORD-BOUNDARY MATCHING ARE BOTH LOAD-BEARING. Short company names
    # ("Box", "Meta") appear in ordinary prose, and a gate that cries wolf gets switched off.
    # ⭐ PLATFORMS ARE NOT PROFILE DATA — and the difference is in the data, not in a list here.
    # The engine legitimately names the surfaces it INTEGRATES with: it parses Indeed digests
    # and drives LinkedIn. Those appear in `channels.jsonl` as `job-board`/`aggregator`. A
    # `recruiter` firm or an employer in the same file is a RELATIONSHIP, and naming one in a
    # portable file is a leak. Flagging the platforms too would produce noise in every run, and
    # a gate that cries wolf is a gate somebody switches off.
    platforms = set()
    try:
        with open(os.path.join(ROOT, "data", "channels.jsonl"), encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if row.get("type") in ("job-board", "aggregator"):
                    for tok in re.split(r"[-:_]", str(row.get("id") or "")):
                        if len(tok) > 2:
                            platforms.add(tok.lower())
    except Exception:
        pass

    # ⭐ A MASKED EMPLOYER NAMES NO ENTITY — found auditing GitHub #19's blast radius. Several
    # company records here are recruiter-fronted searches where the end client was never
    # disclosed, recorded per this repo's own convention as "Confidential" plus a trailing
    # qualifier — a parenthetical naming the agency, or a longer descriptive phrase. The
    # head-truncation below (built to drop a trailing parenthetical qualifier) collapses the
    # FIRST shape down to the bare word "Confidential" — which is also ordinary engine
    # vocabulary (used generically elsewhere, for undisclosed-employer handling ANY candidate
    # might need) and not a leak: the whole point of the placeholder is that no real name exists
    # to leak. A descriptive-phrase record keeps real signal (geography, sector) after the
    # placeholder and is deliberately NOT touched here — only a head that, once truncated, IS one
    # of these placeholders verbatim is dropped.
    _MASKED_EMPLOYER_PLACEHOLDERS = {"confidential", "undisclosed", "unnamed", "unknown", "n/a"}

    def encountered(val):
        """One `encountered` term, or None. ⭐ ONE implementation, called from every source.

        The guards (length floor, capitalisation, platform exclusion) are the difference
        between a gate people keep and a gate people switch off, so they must not be
        re-typed per source and drift apart."""
        val = str(val or "").strip()
        if not val or "@" in val:
            return None
        # A person's display name and a company name are the same shape here, and both are
        # equally out of place in a portable file.
        head = val.split(",")[0].split("(")[0].strip()
        if not (4 <= len(head) <= 40 and head[0].isupper()):
            return None
        if head.lower() in platforms:
            return None
        if head.lower() in _MASKED_EMPLOYER_PLACEHOLDERS:
            return None
        return head

    def record(val):
        head = encountered(val)
        if head:
            terms.setdefault("encountered", set()).add(head)

    def rows(rel):
        try:
            with open(os.path.join(ROOT, "data", rel), encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    yield json.loads(line)
        except Exception:
            return

    for rel, fields in (("companies.jsonl", ("name",)),
                        ("messages.jsonl", ("from", "to"))):
        for row in rows(rel):
            for f in fields:
                record(row.get(f))

    # ⭐⭐ CONTACTS LIVE NESTED, AND THE FLAT SCAN ABOVE COULD NOT SEE THEM — added 2026-08-11.
    #
    # `companies.jsonl.name` and `messages.jsonl.from/to` are top-level string fields, so the
    # loop above reads them by name. **A contact is not stored that way.** It lives in the
    # `contacts[]` ARRAY inside an opportunity — which is where the names of actual humans
    # accumulate, because that is where the engine puts them. The gate therefore drew zero
    # person-terms from the one file that holds people, and a contact named in a portable file
    # was invisible to it.
    #
    # ⚠️ It certified exactly that: four real third-party names survived across `daily-run`'s
    # SKILL.md, `docs/schema.md`, `docs/architecture.md`, `migrate_contacts.py`,
    # `reconcile.py` and `test_checks.py` through every green run — every one of them a person
    # in this array. Same signature failure as the two above it: a source that is never read
    # produces an empty term set, and an empty term set reads as CLEAN.
    #
    # `outreach[].to` is deliberately NOT read here: it is the free text that
    # `migrate_contacts.py` exists to normalise INTO `contacts[].name`, and every person it
    # names has a contact record after that migration. Reading the canonical field is the
    # point; reading the messy one as well would only add malformed variants.
    for row in rows("opportunities.jsonl"):
        for c in (row.get("contacts") or []):
            if isinstance(c, dict):
                record(c.get("name"))

    return {k: sorted(v) for k, v in terms.items() if v}


def _publisher_allowances():
    """Strings that are PUBLIC BY DESIGN, so a match inside one is not a leak.

    ⭐ A marketplace has to say who publishes it. `marketplace.json.owner` and
    `plugin.json.author` carry the owner's name and address deliberately, and they are the
    reason a whole-tree scan reports the owner's own first name twice. Suppressing the
    FILES would blind the gate to everything else in them, so the allowance is the exact
    published identity string and nothing else — read from the manifests rather than
    hard-coded, because a hard-coded name here is the very thing this gate exists to stop.
    """
    vals = set()
    candidates = [os.path.join(ENGINE_ROOT, ".claude-plugin", "plugin.json")]
    try:
        top = subprocess.run(["git", "-C", ENGINE_ROOT, "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=30).stdout.strip()
        if top:
            candidates.append(os.path.join(top, ".claude-plugin", "marketplace.json"))
    except (OSError, subprocess.SubprocessError):
        pass
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            continue
        for key in ("author", "owner"):
            who = doc.get(key)
            if isinstance(who, dict):
                for f in ("name", "email"):
                    if who.get(f):
                        vals.add(str(who[f]))
            elif isinstance(who, str) and who:
                vals.add(who)
        for p in (doc.get("plugins") or []):
            if isinstance(p, dict) and isinstance(p.get("author"), dict):
                for f in ("name", "email"):
                    if p["author"].get(f):
                        vals.add(str(p["author"][f]))
    return sorted(vals, key=len, reverse=True)


ALLOWANCES = _publisher_allowances()


def scan(path, terms):
    rel = os.path.relpath(path, ENGINE_ROOT)
    hits = []
    if not os.path.exists(path):
        # A profile need not contain every optional file. This crashed CI on a fixture with no
        # CLAUDE.md while passing locally, because the local pointer resolved a real profile that
        # had one - a gate that depends on the developer's own machine is not a gate.
        return rel, hits
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except (OSError, UnicodeDecodeError):
        return rel, None          # not text — the caller counts it as excluded, with a reason
    for n, line in enumerate(lines, 1):
        # Spans that are published identity, so a term inside one is not a finding.
        allowed = [m.span() for a in ALLOWANCES
                   for m in re.finditer(re.escape(a), line, re.IGNORECASE)]
        for kind, words in terms.items():
            for w in words:
                    # ⭐⭐ CASE-INSENSITIVE, DELIBERATELY (GitHub #19) — the second near-miss in
                    # this exact match. The first missed terms stored as regex source rather than
                    # plain text (fixed by `re.escape`, above). This one missed a term written in
                    # ANY OTHER CASE than the one it happened to be derived in — `_profile_terms()`
                    # takes a name exactly as `user.json` capitalises it, so an UPPERCASED source
                    # tag elsewhere in the engine (RULEBOOK.md's own knowledge-base convention
                    # line names its source with one) never matched, and the scan of that exact
                    # file reported CLEAN. `re.IGNORECASE` is the general fix — a term can appear
                    # in mixed case too, which no small set of explicit case variants (Title /
                    # UPPER / lower) would cover, and generating one is just re-deriving what the
                    # flag already does. This gate certifies a repository safe to publish; a case
                    # it cannot see is a leak it certifies clean.
                    # ⭐ IDENTIFIER-AWARE, NOT JUST WORD-BOUNDARY — GitHub #45.
                    #
                    # `\b` treats `_` as a word character, so `\bname\b` does NOT match
                    # `..._requires_<owner>` or `NEEDS_<OWNER>_HEADER`. Both sat in
                    # tracked files while this gate reported CLEAN, because it was matching
                    # VALUES and a name had been baked into an IDENTIFIER. Requiring a
                    # non-alphanumeric neighbour instead treats `_`, `-`, `.` and `/` as the
                    # separators they are, while still refusing to fire inside `johnson`.
                    for m in re.finditer(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(w),
                                         line, re.IGNORECASE):
                        if any(s <= m.start() and m.end() <= e for s, e in allowed):
                            continue      # inside the published publisher identity
                        hits.append((n, kind, w, line.strip()[:110]))
                        break
    return rel, hits


def main():
    ap = argparse.ArgumentParser(description="Is a reusable file carrying one person's data?")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--all", action="store_true", help="Audit every class, advisory only.")
    ap.add_argument("--require-profile", action="store_true",
                    help="Fail if no profile is reachable, instead of reporting NOT CHECKED. "
                         "Use before a push: locally there IS a profile, so a run that cannot "
                         "find one is a resolution bug worth stopping for.")
    args = ap.parse_args()

    print("ENGINE PURITY — could another candidate use these files unchanged?")
    print("=" * 78)
    print("  engine : %s" % ENGINE_ROOT)
    print("  profile: %s" % ROOT)

    # ⭐⭐ A SCAN THAT COVERED NOTHING IS A FAILURE, NEVER A PASS. Both halves are required:
    # files to scan, and terms to scan for. Missing either one used to exit 0 — one silently,
    # one with a friendly sentence — which is how this gate stayed green while checking nothing.
    # Report the two conditions SEPARATELY, because they have different causes and different
    # fixes, and a merged message sends you looking in the wrong place.
    empty = [name for name, pat in ENGINE_FAMILIES if not glob.glob(pat)]
    if empty or not FAMILY_FILES or not ENGINE:
        print("\n  !! THE GATE SCANNED NOTHING — this is a BROKEN GATE, not a clean tree.")
        print("     engine families matching zero files: %s" % (", ".join(empty) or "ALL"))
        print("     Either the engine root above is wrong, or a family was renamed and this")
        print("     list was not. Do NOT read a green run as evidence of anything.")
        return 1

    # ⭐⭐ COVERAGE IS STRUCTURAL, SO IT PRINTS BEFORE THE TERM CHECK AND ON EVERY PATH.
    # Printing it only after terms were found meant CI — which has no profile and returns
    # early — never showed what the gate could see, which is exactly the half CI CAN answer
    # and the half that was broken (#45).
    excluded, readable = [], []
    for p in ENGINE:
        try:
            with open(p, encoding="utf-8") as fh:
                fh.read(1)
            readable.append(p)
        except (OSError, UnicodeDecodeError):
            excluded.append((os.path.relpath(p, ENGINE_ROOT), "not readable as UTF-8 text"))
    print("  scanned %d of %d tracked engine file(s)  ·  %d excluded  ·  %d family taxonomy"
          % (len(readable), len(ENGINE), len(excluded), len(ENGINE_FAMILIES)))
    for rel, why in excluded[:10]:
        print("      excluded: %-44s %s" % (rel, why))
    if len(readable) + len(excluded) != len(ENGINE):
        print("\n  !! COVERAGE DOES NOT ADD UP — %d scanned + %d excluded != %d tracked."
              % (len(readable), len(excluded), len(ENGINE)))
        return 1

    terms = _profile_terms()
    if not terms:
        # No profile on this host — CI, a fresh clone, a container. The question this gate asks
        # ("does an engine file carry THIS person's data?") is genuinely unanswerable, so it must
        # say NOT CHECKED rather than CLEAN. It still ran the structural check above, which is
        # the half that CI can answer and the half that was actually broken.
        print("\n  %d engine file(s) found — structure OK." % len(ENGINE))
        print("  !! NOT CHECKED for profile data: no user.json/config.json reachable from %s"
              % ROOT)
        print("     This is expected in CI. It is NOT a clean result and must not be read as one.")
        return 1 if args.require_profile else 0

    print("  terms drawn from the PROFILE (never hard-coded here): %s"
          % ", ".join("%s=%d" % (k, len(v)) for k, v in terms.items()))
    total, dirty = 0, []
    fired = set()
    for p in readable:
        rel, hits = scan(p, terms)
        if hits is None:
            continue
        keep = []
        for h in hits:
            exc = next((e for e in KNOWN_EXCEPTIONS
                        if e[0] == rel and e[1].lower() in h[3].lower()), None)
            if exc:
                fired.add(exc)
            else:
                keep.append(h)
        if keep:
            dirty.append((rel, keep)); total += len(keep)
        hits = keep
        if args.verbose and hits:
            print("\n  %s" % rel)
            for n, kind, w, line in hits[:12]:
                print("    %4d  [%s:%s]  %s" % (n, kind, w, line))

    # ⭐⭐ THE COVERAGE LINE IS THE POINT OF #45. A gate that cannot say what it skipped is a
    # gate whose green run means nothing. scanned + excluded must equal the tracked total, and
    # every exclusion is named on the line above.
    # ⚠️ "Stale" means the file is STILL HERE and the term is GONE — the defect was fixed and
    # the exception outlived it. An exception whose file is absent from the tree under scan is
    # simply not applicable: the throwaway trees the regression tests build contain none of
    # these paths, and judging those as stale made this gate fail against every synthetic tree.
    applicable = [e for e in KNOWN_EXCEPTIONS
                  if os.path.exists(os.path.join(ENGINE_ROOT, e[0]))]
    stale = [e for e in applicable if e not in fired]
    if fired:
        print("\n  KNOWN EXCEPTIONS — suppressed here, tracked as issues, NOT resolved:")
        for path, term, issue, why in sorted(fired):
            print("      #%-4d %s  (%s)" % (issue, path, why))
    if stale:
        # An exception that matches nothing has outlived its defect. Left in place it becomes a
        # permanent blind spot that nobody re-examines, so it fails rather than being ignored.
        print("\n  !! %d STALE KNOWN EXCEPTION(S) — they match nothing and must be deleted:"
              % len(stale))
        for path, term, issue, _why in stale:
            print("      #%-4d %s :: %s" % (issue, path, term))
        return 1

    print("\n  %d ENGINE file(s) carrying profile data · %d marker(s)" % (len(dirty), total))
    if not dirty:
        print("\n  CLEAN. Every agent spec and run prompt is portable.")
        return 0

    for rel, hits in sorted(dirty, key=lambda x: -len(x[1])):
        kinds = {}
        for _n, k, w, _l in hits:
            kinds.setdefault(k, set()).add(w)
        print("    %-46s %3d  %s" % (rel, len(hits),
              " ".join("%s:%s" % (k, ",".join(sorted(v))[:34]) for k, v in kinds.items())))

    print("\n  THE TEST: could another candidate use this file unchanged? If a sentence would be")
    print("  WRONG for them, move the fact to config.json/user.json and point at it instead.")
    print("  RECORD files (docs/incident_archive.md, log.md, focus.md) are EXEMPT by design —")
    print("  they are the history of this search, and genericizing them deletes the evidence")
    print("  a rule rests on.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
