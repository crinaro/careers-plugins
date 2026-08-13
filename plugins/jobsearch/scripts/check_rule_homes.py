#!/usr/bin/env python3
"""
Guarantee that archiving an incident never DELETES the rule it produced.

WHY THIS EXISTS
---------------
`CLAUDE.md` is loaded into every run and inherited by every subagent as project memory, and
on 2026-08-02 it measured **9,430 words, 56% of it incident narrative**. That is not merely
bloat — it is the mechanism behind a recurring failure: stale narrative gets read back as
current fact. Three separate instances landed in a single day (a log entry claiming a data
write that never happened; a focus.md item proposing an already-shipped fix; `funnel_report.py`
printing a hardcoded claim the weekly review then quoted as evidence).

The fix is to move the stories to `docs/incident_archive.md` and keep the RULES. The danger is
that ~35 of those bullets have an operating rule embedded *inside* the story, so archiving
wholesale silently deletes it.

This script makes that impossible to do by accident:

  1. **Every archive entry must carry a `→` back-pointer, and it must RESOLVE** — to text in
     `CLAUDE.md` / an agent definition / a task prompt, or to a dotted `config.json` key.
     *This is the guarantee.* An entry whose rule has no home fails the check.
  2. Every `docs/incident_archive.md#anchor` referenced from CLAUDE.md resolves to a real
     heading (catches a broken pointer in the other direction).
  3. **A CLAUDE.md word-budget RATCHET.** Same pattern as `MAX_UNDATED` in the test suite:
     the number only ever goes down. Without it the file simply re-accretes, which is the
     actual complaint.

The precedent being followed is already in the repo: `focus.md` says *"Before archiving an
item, check its lesson has a durable home in CLAUDE.md or a `.claude/agents/*.md` definition —
otherwise archiving deletes the knowledge,"* and `check_process_debt.py` prints it every run.
This is that rule, mechanized, for CLAUDE.md instead of focus.md.

Usage:
    python3 scripts/check_rule_homes.py            # verify; exit 1 on a problem
    python3 scripts/check_rule_homes.py --report   # report only, always exit 0

Python 3.9+. Standard library only.
"""

import argparse
import glob
import json
import os
import re
import sys

import os, sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root as _profile_root, engine_root as _engine_root, profile_or_fixture as _pof
ENGINE_SCRIPTS = os.path.dirname(os.path.realpath(__file__))

ROOT = _pof()
# The rulebook's SOURCE is RULEBOOK.md at the engine root — a template that install_rulebook.py
# copies into the profile AS CLAUDE.md, which is where it actually loads. Every rule, pointer and
# archive entry names it "CLAUDE.md" because that is its name everywhere it is read; only the
# engine-side template carries the different name (a CLAUDE.md at the plugin root is never loaded
# as project context, and the validator warns on it).
CLAUDE = os.path.join(_engine_root(), "RULEBOOK.md")
ARCHIVE = os.path.join(ROOT, "docs", "incident_archive.md")

# The ratchet. LOWER THIS as narrative moves out. It went UP exactly once, on 2026-08-02,
# when the load-bearing audit found the trim had DELETED FIVE HARD RULES and they were
# restored — an increase that puts genuine content back is legitimate; an increase that
# accommodates new narrative is not. CLAUDE.md was 9,468 words before the split.
MAX_CLAUDE_WORDS = 5664

# Where a rule is allowed to live. Deliberately broader than focus.md's original list, which
# named only CLAUDE.md and agent definitions — that is precisely why data-shaped lessons (the
# ATS sender-domain list) had nowhere legitimate to land and stayed embedded in a story.
def _label(path):
    """Name a home by the root it belongs to. relpath() across two unrelated trees yields
    ../../.. noise, and raises outright on Windows-style separate drives."""
    for base in (_engine_root(), ROOT):
        try:
            rel = os.path.relpath(path, base)
        except ValueError:
            continue
        if not rel.startswith(os.pardir):
            return rel
    return path


def rule_homes():
    homes = {}
    for path in ([CLAUDE]
                 + sorted(glob.glob(os.path.join(_engine_root(), "agents", "*.md")))
                 + sorted(glob.glob(os.path.join(_engine_root(), "skills", "*", "SKILL.md")))
                 + sorted(glob.glob(os.path.join(_engine_root(), "commands", "*.md")))
                 + sorted(glob.glob(os.path.join(_engine_root(), "docs", "*.md")))
                 + sorted(glob.glob(os.path.join(ROOT, "docs", "*.md")))
                 + [os.path.join(ENGINE_SCRIPTS, "test_checks.py")]):
        # NOTE: test_checks.py counts as a home because a REGRESSION TEST is a durable home for
        # a rule — arguably the strongest one. But see audit_load_bearing(): a test DOCSTRING
        # merely describing a rule is NOT a home, and letting one satisfy an anchor is how the
        # inventory reported 41/41 while a rule's real home had been genericized out from under
        # it.
        if os.path.exists(path) and os.path.abspath(path) != os.path.abspath(ARCHIVE):
            with open(path, encoding="utf-8") as fh:
                # The rulebook is keyed by its INSTALLED name: archive back-pointers say
                # "CLAUDE.md" because that is the file every session actually reads.
                key = "CLAUDE.md" if os.path.abspath(path) == os.path.abspath(CLAUDE) else _label(path)
                homes[key] = fh.read()
    return homes


def config_key_resolves(dotted):
    """`config.json.ats.receipt_sender_domains` -> does that key actually exist?"""
    # The FILENAME itself contains a dot ("config.json"), so a naive split on "." breaks it —
    # that bug made every config-key pointer silently unresolvable (found 2026-08-02).
    m = re.match(r"^([\w-]+\.json)\.(.+)$", dotted)
    if not m:
        return False
    parts = [m.group(1)] + m.group(2).split(".")
    # CI has no profile; fall back to the fixture so this gate is RUNNABLE rather than
    # vacuously red (2026-08-05).
    path = os.path.join(ROOT, parts[0])
    if not os.path.exists(path):
        path = os.path.join(_engine_root(), "tests", "fixtures", "profile", parts[0])
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as fh:
            node = json.load(fh)
    except ValueError:
        return False
    for k in parts[1:]:
        if not isinstance(node, dict) or k not in node:
            return False
        node = node[k]
    return True


# ---- The load-bearing rules. EVERY ONE must resolve somewhere, always. ----------
#
# WHY THIS LIST EXISTS, and it is not theoretical: on 2026-08-02 the CLAUDE.md trim
# DELETED FIVE HARD RULES — including "NEVER edit the candidate's live LinkedIn profile without his
# explicit fresh approval" — because they used a plain "- " prefix and the edit's bullet
# boundaries keyed on "- **". The back-pointer check above could not catch it: it verifies
# that ARCHIVED lessons kept their rule, and these were never archived. They were simply
# gone, silently, from a file nobody re-reads end to end.
#
# So this is the other half of the guarantee: a canonical inventory, checked every run.
# ADD TO IT whenever a rule is important enough that losing it would be a real problem.
LOAD_BEARING = {
    "meeting artifacts scanned first":           "SCAN FOR MEETING ARTIFACTS",   # EVIDENCE
    "never fabricate a referral":                "NEVER fabricate mutual-connection",   # TRUTHFULNESS
    "application date from the receipt":         "APPLICATION'S DATE COMES FROM THE CONFIRMATION EMAIL",   # EVIDENCE
    "ATS query order":                           "config.json.ats:query_order",   # DATA
    "off-resume addenda are usable":             "Absence from the printed",   # TRUTHFULNESS
    "never quote a tracker for system state":    "NEVER repeat a claim about the state of the SYSTEM",   # EVIDENCE
    "never state an unverified meeting time":    "NEVER state a meeting date or time",   # EVIDENCE
    "no one-mailbox negatives":                  'NEVER conclude a message "does not exist"',   # EVIDENCE
    "relative dates are verified":               "NEVER silently resolve a relative date",   # EVIDENCE
    "never assert an unchecked doc fact":        "NEVER assert a specific fact about a document",   # EVIDENCE
    "ask, don't paper over a JD gap":            "only covers thinly",   # TRUTHFULNESS
    "no send without fresh approval":            "NEVER send messages, emails, or applications",   # CONSENT
    "never edit the live LinkedIn profile":      "live LinkedIn profile without their explicit fresh approval",   # CONSENT
    "outreach style":                            "Outreach style",   # WRITING
    "channel priority":                          "retained-firm relationships and warm intros first",   # PROCESS
    "LinkedIn fetch invents a location":         "FROM A LINKEDIN-SOURCED FETCH",   # EVIDENCE
    "grep the published output":                 "GREP THE *OUTPUT*",   # EVIDENCE
    "a WebFetch cannot prove a posting dead":    "NEVER CONCLUDE A JOB POSTING IS DEAD",   # EVIDENCE
    "cover letters are one page":                "ONE-PAGE RULE FOR COVER LETTERS",   # WRITING
    "measure pages after accepting":             "ACCEPTING TRACKED SUGGESTIONS",   # WRITING
    "strip AI tells":                            "STRIP AI-TELL MARKERS",   # WRITING
    "connectors are create-only":                "CREATE-ONLY",   # TOOLING
    "Drive parent folder":                       "config.json.drive:job_search_folder_id",   # TOOLING
    "the url wrapper is not a defect":           "wrapper is added by Gmail when DISPLAYING",   # WRITING
    "subagents never push":                      "Subagents must never push",   # CONSENT
    "a subagent stages its own paths":           "never `git add -A`",   # CONSENT
    "applications and outreach are separate":    "separate funnels",   # DATA
    "the exclusion list definition":             "`verdict: pass` OR `status: passed`",   # DATA
    "blockquote or it publishes empty":          "PUBLISH EMPTY",   # WRITING
    "kb lines are tagged by source":             "TAGGED BY SOURCE",   # DATA
    "onsite is not relocation":                  "does NOT mean relocation",   # TRUTHFULNESS
    "gmail account defaults to all":             "defaults to `all`",   # DATA
    "credentials are Keychain-only":             "Claude must not",   # CONSENT
    "where does an item go":                     "WHERE DOES AN ITEM GO",   # PROCESS
    "ask lists expel resolved items":            "EXPEL RESOLVED ITEMS",   # PROCESS
    # ⚠️ SUPERSEDED 2026-08-06, NOT DELETED. The rule was "Process -> Open is a WEEKLY WORK
    # QUEUE drained to zero". Engine defects are no longer carried locally at all — they are
    # filed as issues on the plugin's own repository, so there is no local queue left to
    # drain. The OBLIGATION survives and is what this now tracks: nothing engine-related may
    # be left sitting in the profile. Retargeted rather than removed, because deleting the
    # entry is exactly the silent loss this whole list exists to prevent.
    "engine items are filed, not kept local": "drains ENGINE observations by FILING them",   # PROCESS
    "role state is generated":                   "Role state is GENERATED",   # DATA
    "log.md is append-only":                     "Never edit past entries",   # DATA
    "the dashboard is generated":                "GENERATED. Never hand-edit",   # DATA
    "opportunities.md is retired":               "RETIRED 2026-07-20, frozen",   # DATA
    "copy the resume, don't paraphrase":         "do not paraphrase",   # TRUTHFULNESS
}



RULE_CATEGORY = {'never quote a tracker for system state': 'EVIDENCE',
    'never state an unverified meeting time': 'EVIDENCE',
    'no one-mailbox negatives': 'EVIDENCE',
    'relative dates are verified': 'EVIDENCE',
    'never assert an unchecked doc fact': 'EVIDENCE',
    'a WebFetch cannot prove a posting dead': 'EVIDENCE',
    'LinkedIn fetch invents a location': 'EVIDENCE',
    'application date from the receipt': 'EVIDENCE',
    'meeting artifacts scanned first': 'EVIDENCE',
    'grep the published output': 'EVIDENCE',
    'never fabricate a referral': 'TRUTHFULNESS',
    'off-resume addenda are usable': 'TRUTHFULNESS', "ask, don't paper over a JD gap": 'TRUTHFULNESS', "copy the resume, don't paraphrase": 'TRUTHFULNESS',
    'onsite is not relocation': 'TRUTHFULNESS',
    'no send without fresh approval': 'CONSENT',
    'never edit the live LinkedIn profile': 'CONSENT',
    'subagents never push': 'CONSENT',
    'a subagent stages its own paths': 'CONSENT',
    'credentials are Keychain-only': 'CONSENT',
    'strip AI tells': 'WRITING',
    'cover letters are one page': 'WRITING',
    'measure pages after accepting': 'WRITING',
    'blockquote or it publishes empty': 'WRITING',
    'outreach style': 'WRITING',
    'the url wrapper is not a defect': 'WRITING',
    'canonical letter header': 'WRITING',
    'applications and outreach are separate': 'DATA',
    'the exclusion list definition': 'DATA',
    'kb lines are tagged by source': 'DATA',
    'log.md is append-only': 'DATA',
    'the dashboard is generated': 'DATA',
    'role state is generated': 'DATA',
    'opportunities.md is retired': 'DATA',
    'ATS query order': 'DATA',
    'gmail account defaults to all': 'DATA',
    'where does an item go': 'PROCESS',
    'ask lists expel resolved items': 'PROCESS',
    'engine items are filed, not kept local': 'PROCESS',
    'channel priority': 'PROCESS',
    'connectors are create-only': 'TOOLING',
    'Drive parent folder': 'TOOLING'}


def _only_in_docstring(src, needle):
    """True when `needle` appears in this Python source ONLY inside docstrings/comments.

    A rule is homed by an ASSERTION that enforces it, not by prose describing it.
    """
    import ast as _ast
    try:
        tree = _ast.parse(src)
    except SyntaxError:
        return False
    doc_spans = []
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Expr) and isinstance(node.value, _ast.Constant) \
                and isinstance(node.value.value, str):
            doc_spans.append((node.value.lineno, node.value.end_lineno))
    lines = src.split("\n")
    for i, line in enumerate(lines, 1):
        if needle in line:
            in_doc = any(a <= i <= b for a, b in doc_spans)
            stripped = line.strip().startswith("#")
            if not in_doc and not stripped:
                return False          # found in real code
    return True


def audit_load_bearing(homes):
    """Every canonical rule must resolve. A dotted `config.json.a:key` target checks the key."""
    missing = []
    for name, needle in sorted(LOAD_BEARING.items()):
        if needle.startswith("config.json") and ":" in needle:
            dotted, key = needle.split(":", 1)
            if not config_key_resolves(dotted + "." + key):
                missing.append((name, needle))
        else:
            # ⚠️ A DOCSTRING IS NOT A HOME. Found 2026-08-04: the anchor for "never edit the
            # live LinkedIn profile" resolved ONLY inside a test docstring that described the
            # rule as an example, while the rule itself had been reworded. The inventory
            # reported all 41 rules homed; one of them was a sentence about a rule.
            # So a test only counts as a home when the needle appears in EXECUTABLE text.
            hit = False
            for fname, txt in homes.items():
                if needle not in txt:
                    continue
                if fname.endswith(".py") and _only_in_docstring(txt, needle):
                    continue
                hit = True
                break
            if not hit:
                missing.append((name, needle))
    return missing


def slug(text):
    s = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[\s_]+", "-", s)


def main():
    ap = argparse.ArgumentParser(description="Rule-home + archive integrity check.")
    ap.add_argument("--list", action="store_true",
                    help="Print the rule inventory by category, with where each one lives. "
                         "This is the MANAGEMENT view: what the base ruleset actually contains.")
    ap.add_argument("--report", action="store_true", help="Report only; always exit 0.")
    args = ap.parse_args()

    problems = []

    with open(CLAUDE, encoding="utf-8") as fh:
        claude_text = fh.read()
    words = len(claude_text.split())

    print("RULE-HOME CHECK")
    print("=" * 74)
    print("RULEBOOK.md (installs as the profile's CLAUDE.md): %d words (ratchet: %d)"
          % (words, MAX_CLAUDE_WORDS))
    if words > MAX_CLAUDE_WORDS:
        problems.append("RULEBOOK.md is %d words, over the %d ratchet. It is loaded into EVERY "
                        "run and inherited by every subagent — move narrative to "
                        "docs/incident_archive.md and LOWER the ratchet, never raise it."
                        % (words, MAX_CLAUDE_WORDS))

    homes = rule_homes()

    if args.list:
        # ⭐ THE BASE RULESET, BY CATEGORY. Added 2026-08-04, per the candidate: *"we should be able
        # to copy the claude.md from one person to another and it works (yes the config items
        # will need to change) but other than that, it should work."* A flat 41-entry dict could
        # be CHECKED but not READ, so nobody could see what the base set contained or whether a
        # category was thin.
        by = {}
        for name, needle in LOAD_BEARING.items():
            by.setdefault(RULE_CATEGORY.get(name, "UNCATEGORIZED"), []).append((name, needle))
        print("BASE RULESET — %d rules across %d categories" % (len(LOAD_BEARING), len(by)))
        print("=" * 76)
        print("Portable by design: these apply to ANY executive search. The VALUES they operate")
        print("on live in config.json / user.json and change per person.\n")
        missing = dict(audit_load_bearing(homes))
        for cat in sorted(by):
            print("  %s  (%d)" % (cat, len(by[cat])))
            for name, needle in sorted(by[cat]):
                where = "config" if needle.startswith("config.json") else ""
                flag = "  ❌ NO HOME" if name in missing else ""
                print("     %-44s %s%s" % (name, where, flag))
            print()
        print("  Enhancements from an individual's interactions do NOT go here — they land in")
        print("  config.json, user.json, resume.md's addenda, projects.md or kb_<company>.md.")
        return 1 if missing else 0

    if not os.path.exists(ARCHIVE):
        print("\nNo docs/incident_archive.md yet.")
        for name, needle in audit_load_bearing(homes):
            problems.append("LOAD-BEARING RULE MISSING — %r" % name)
        for p in problems:
            print("  ❌ %s" % p)
        return 0 if (args.report or not problems) else 1

    with open(ARCHIVE, encoding="utf-8") as fh:
        arch_lines = fh.read().split("\n")

    entries, cur = [], None
    for line in arch_lines:
        m = re.match(r"^##\s+(.*)$", line)
        if m:
            if cur:
                entries.append(cur)
            cur = {"heading": m.group(1).strip(), "body": []}
        elif cur is not None:
            cur["body"].append(line)
    if cur:
        entries.append(cur)

    print("archive entries: %d · load-bearing rules tracked: %d"
          % (len(entries), len(LOAD_BEARING)))

    for e in entries:
        body = "\n".join(e["body"])
        pointers = re.findall(r"→\s*(?:rule now lives at\s*)?(.+)", body)
        if not pointers:
            problems.append("archive entry %r has NO '→' back-pointer — an entry whose rule "
                            "has no home is knowledge being deleted, not archived."
                            % e["heading"][:60])
            continue
        # One entry may name SEVERAL homes (a rule in CLAUDE.md plus its data in config.json).
        # Split on '·' so each is checked independently rather than as one long string.
        targets = []
        for ptr in pointers:
            for part in re.split(r"[·•]", ptr):
                part = part.strip().rstrip(".")
                if part:
                    targets.append(part)
        for ptr in targets:
            target = ptr.split("§")[0].strip().strip("`")
            ok = False
            if target in homes:
                # optionally check the quoted rule text actually appears there
                quoted = re.findall(r'"([^"]{12,})"', ptr)
                ok = all(q in homes[target] for q in quoted) if quoted else True
            elif config_key_resolves(target):
                ok = True
            else:
                # bare filename mentioned anywhere in a valid home
                ok = any(target in txt for txt in homes.values())
            if not ok:
                problems.append("archive entry %r points at %r, which does not resolve to a "
                                "rule in CLAUDE.md / an agent / a task prompt / a config key."
                                % (e["heading"][:50], target[:60]))

    for name, needle in audit_load_bearing(homes):
        problems.append("LOAD-BEARING RULE MISSING — %r (looked for %r). It was deleted "
                        "without being archived. On 2026-08-02 a CLAUDE.md trim removed five "
                        "of these silently, including the ban on editing the live LinkedIn "
                        "profile." % (name, needle[:48]))

    for anchor in set(re.findall(r"incident_archive\.md#([\w-]+)", claude_text)):
        if not any(slug(e["heading"]) == anchor for e in entries):
            problems.append("CLAUDE.md links incident_archive.md#%s — no such heading." % anchor)

    print("=" * 74)
    if problems:
        for p in problems:
            print("  ❌ %s" % p)
    else:
        print("  ✅ Every archive entry's rule resolves to a durable home, every CLAUDE.md")
        print("     anchor resolves, and CLAUDE.md is within the word ratchet.")

    return 0 if (args.report or not problems) else 1


if __name__ == "__main__":
    sys.exit(main())
