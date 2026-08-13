#!/usr/bin/env python3
"""
Audit the PROFILE / ENGINE separation — and, more importantly, detect DRIFT between
`config.json` and the numbers restated in prose.

WHY THIS EXISTS
---------------
On 2026-08-02 `config.json` was created to segment the candidate's data from the reusable
machinery. The candidate then asked the right question: *"is it clear the files that support
the agents and processes are distinctly separate to the user data?"*

Measured honestly, the answer was **no** — 348 user-data references still sat inside
engine files, and the comp tiers now existed in **two** places: `config.json` AND
~400 words of CLAUDE.md prose. **That is strictly worse than one place**, because two
sources of truth can disagree and nothing would notice. The whole point of extracting
the profile was to stop a screening rule drifting; duplicating it re-opens that door.

**The candidate's ruling (2026-08-02): "The json are suppose to be the sources of truth."** So the
fix is NOT to keep the two copies in agreement — it is to have only ONE copy. The first
version of this script checked that CLAUDE.md's numbers MATCHED profile.json, which
quietly treated the prose as a co-equal source. Inverted per the candidate: the engine must
contain **no comp values at all**. Every figure, quote, and rationale was moved into
`config.json` (nothing was deleted — the verbatim the candidate quotes live in the tier notes),
and CLAUDE.md now points at `scripts/profile.py` instead of restating anything.

  * DUPLICATION CHECK (an ERROR, exits 1) — a comp value appearing anywhere in an ENGINE
    file is a second source of truth and fails this check.
  * LEAKAGE REPORT (informational) — how much user data sits in engine files, which
    is the honest measure of how packageable the engine is for a second user.

Two different questions, deliberately separated:
    "Is the user's data in one place?"      -> mostly yes (profile.json + resume/data)
    "Is the ENGINE free of user data?"      -> no, and this quantifies the gap

Usage:
    python3 scripts/check_profile_leakage.py            # report + drift check
    python3 scripts/check_profile_leakage.py --strict   # exit 1 on drift (default is also 1)
    python3 scripts/check_profile_leakage.py --report   # leakage report only, exit 0

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
if ROOT != _profile_root():
    # Running without a profile (CI, a bare checkout). Point every downstream script at the
    # fixture too, or they resolve a real root that has no config.json in it.
    os.environ["CLAUDESEARCH_ROOT"] = ROOT
    os.environ.setdefault("CLAUDESEARCH_DATA_DIR", os.path.join(ROOT, "data"))

# The ENGINE: machinery that should ideally be reusable by a different person.
ENGINE = (["CLAUDE.md", "docs/schema.md"]
          + sorted(glob.glob(os.path.join(ENGINE_SCRIPTS, "*.py")))
          + sorted(glob.glob(os.path.join(_engine_root(), "agents", "*.md")))
          + sorted(glob.glob(os.path.join(_engine_root(), "skills", "*", "SKILL.md")))
          + sorted(glob.glob(os.path.join(_engine_root(), "commands", "*.md"))))

# The PROFILE: this person's data. Not audited — it is SUPPOSED to be full of them.
USER_FILES = ["user.json", "resume.md", "projects.md", "network.md", "data/*.jsonl"]
CONFIG_FILES = ["config.json", "strategy.md"]
STATE_FILES = ["focus.md", "log.md", "process_archive.md"]

# ⭐⭐ THE GUARD TERMS COME FROM THE PROFILE. THEY ARE NEVER WRITTEN HERE.
#
# This block used to hardcode the owner's first name, three home cities, two email handles, four
# employers — and a REAL PHONE NUMBER, spelled as a regex so a phone-shaped search would not find
# it. The leak detector was the largest concentration of personal data in the engine, and it is
# in all 55 commits of this repository's history, from the first one.
#
# ⚠️ AND IT ONLY EVER GUARDED ONE PERSON. Installed by anyone else, it watched for a stranger's
# name and cities and was blind to their own — a gate that reports CLEAN while protecting nothing,
# which is this project's defining failure shape, sitting inside the check written to prevent it.
#
# So the terms are derived from the profile at runtime. Each installation guards ITS OWN identity,
# and the engine carries none. This is the repo's own rule — `config.json`/`user.json` hold the
# VALUES, the engine holds the REASONING — applied to the one file that was exempt from it.
GENERIC = {
    # Shapes, not identities. These need no personal data and work for every installation.
    "money": re.compile(r"\$\d{3}K|\$\d{3},000|\$\d{2,3},\d{3}"),
    "phone": re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"),
}


# Keys whose VALUES are identity, wherever they appear in the profile tree. Matched on the key
# name rather than a fixed path, because identity is nested (`identity.full_name`, and mailboxes
# are dict KEYS) and the shape differs between installations. Collecting by key name is what keeps
# this general instead of tuned to one schema.
IDENTITY_KEYS = ("name", "full_name", "display_name", "city", "cities", "phone", "email",
                 "emails", "mailbox", "mailboxes", "linkedin", "commute_anchor", "commute_anchors",
                 "address", "handle")


def _walk(node, key_hint, out):
    if isinstance(node, dict):
        for k, v in node.items():
            if k.startswith("_"):
                continue                      # `_README`/`_note` keys are commentary, not data
            hit = any(t == k.lower() or k.lower().endswith("_" + t) for t in IDENTITY_KEYS)
            if hit and isinstance(v, dict):
                out.update(str(x) for x in v)  # mailboxes are keyed BY address
            _walk(v, k.lower() if hit else None, out)
    elif isinstance(node, (list, tuple)):
        for v in node:
            _walk(v, key_hint, out)
    elif key_hint and isinstance(node, (str, int)):
        out.add(str(node))


def _terms(profile):
    """Identity strings worth guarding, pulled from this installation's own profile."""
    out = set()
    _walk(profile, None, out)
    expanded = set(out)
    for t in out:
        if "@" in t:
            expanded.add(t.split("@", 1)[0])   # "a.person@x.com" also leaks as "a.person"
        # A full name leaks one word at a time; guard the parts as well as the whole.
        expanded.update(w for w in re.split(r"\W+", t) if len(w) > 3 and not w.isdigit())
    return {t.strip() for t in expanded if isinstance(t, str) and len(t.strip()) > 3}


def build_patterns(profile):
    """GENERIC shapes plus this profile's own identity terms.

    ⚠️ Returns the term count too, because **a guard with nothing to guard must say so.** Zero
    derived terms means the identity half of this check is inert, and reporting CLEAN in that
    state would be exactly the lie the check exists to prevent.
    """
    pats = dict(GENERIC)
    terms = sorted(_terms(profile), key=len, reverse=True)
    if terms:
        pats["identity"] = re.compile("|".join(re.escape(t) for t in terms), re.I)
    return pats, len(terms)


def rel(p):
    return os.path.relpath(p, ROOT)


def load_profile():
    """Both layers, for the audit. They stay separate on disk."""
    out = {}
    for name in ("config.json", "user.json"):
        with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
            out.update(json.load(fh))
    return out


MONEY_RE = re.compile(r"\$\d{3}K|\$\d{3},000|\$\d{3}\s*[-–—]\s*\d{3}K")


def duplication_check():
    """No ENGINE file may contain a compensation value. profile.json is the ONE source.

    the candidate, 2026-08-02: *"The json are suppose to be the sources of truth."* A number
    restated in prose is a second source that can silently disagree — which is the exact
    failure extracting the profile was meant to end. Prose keeps the REASONING and points
    at `scripts/profile.py`; it states no figures.
    """
    problems = []
    for path in ENGINE:
        p = path if os.path.isabs(path) else os.path.join(ROOT, path)
        if not os.path.exists(p):
            continue
        # The test suite legitimately contains figures — they are fixtures asserting
        # profile.json's own values, which is verification, not a second source.
        if os.path.basename(p) == "test_checks.py":
            continue
        with open(p, encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                for hit in MONEY_RE.findall(line):
                    problems.append(
                        "%s:%d states a comp value (%s). config.json is the ONLY source "
                        "of truth for figures — state the tier NAME and point at "
                        "`scripts/profile.py` instead." % (rel(p), n, hit))
    return problems


def criteria_check():
    """the candidate's three acceptance criteria (2026-08-02), checked rather than claimed:
         1. User data is managed independently
         2. Configuration data for the user is managed separately
         3. The agents and python processes LEVERAGE user + configuration to operate
    """
    import importlib, sys as _sys
    _sys.path.insert(0, ENGINE_SCRIPTS)
    problems = []

    # (1) + (2): the layers exist as distinct files and neither contains the other's job.
    for name in ("user.json", "config.json"):
        if not os.path.exists(os.path.join(ROOT, name)):
            problems.append("CRITERION 1/2: %s is missing — the layers are not separated." % name)
            return problems
    prof = importlib.import_module("profile")
    u, c = prof.user(), prof.config()
    for key in ("compensation", "targets", "geography", "channel_policy"):
        if key in u:
            problems.append("CRITERION 1: policy key %r is in user.json — configuration "
                            "leaked into the user layer." % key)
    if "identity" in c:
        problems.append("CRITERION 2: 'identity' is in config.json — user data leaked "
                        "into the configuration layer.")
    blob = json.dumps(c)
    for label, val in (("phone", u["identity"]["phone"]),
                       ("email", u["identity"]["primary_email"])):
        if val and val in blob:
            problems.append("CRITERION 2: the user's %s appears in config.json — "
                            "configuration must be person-agnostic." % label)

    # (3): the engine must READ the layers, not embed them. The mailbox list is the
    # canonical test — it was hardcoded in gmail_mcp_server.py until 2026-08-02.
    try:
        g = importlib.import_module("gmail_mcp_server")
        if sorted(g._accounts_from_user_json()) != sorted(prof.mailboxes()):
            problems.append("CRITERION 3: gmail_mcp_server is not resolving mailboxes "
                            "from user.json.")
    except Exception as exc:
        problems.append("CRITERION 3: could not verify gmail_mcp_server reads user.json (%s)" % exc)
    try:
        if prof.email_signature()[0] != u["identity"]["full_name"]:
            problems.append("CRITERION 3: the signature template is not rendering from user.json.")
    except Exception as exc:
        problems.append("CRITERION 3: signature template failed to render (%s)" % exc)
    return problems


def leakage():
    try:
        patterns, n_terms = build_patterns(load_profile())
    except Exception:
        patterns, n_terms = dict(GENERIC), 0
    if not n_terms:
        print("  ⚠️ NO IDENTITY TERMS could be derived from this profile, so only generic shapes")
        print("     (money, phone) are being checked. A name or city could pass unnoticed.")
        print("     This is reported rather than passed over: a guard with nothing to guard must")
        print("     say so, or CLEAN means nothing.")
    rows = []
    for path in ENGINE:
        p = path if os.path.isabs(path) else os.path.join(ROOT, path)
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as fh:
            text = fh.read()
        counts = {k: len(rx.findall(text)) for k, rx in patterns.items()}
        total = sum(counts.values())
        if total:
            rows.append((rel(p), counts, total))
    return sorted(rows, key=lambda r: -r[2])


def main():
    ap = argparse.ArgumentParser(description="Profile/engine separation audit + drift check.")
    ap.add_argument("--report", action="store_true", help="Leakage report only; always exit 0.")
    ap.add_argument("--strict", action="store_true", help="(default) exit 1 on drift.")
    args = ap.parse_args()

    profile = load_profile()

    print("PROFILE / ENGINE SEPARATION AUDIT")
    print("=" * 78)
    print("1. USER DATA     : %s" % ", ".join(USER_FILES))
    print("2. CONFIGURATION : %s" % ", ".join(CONFIG_FILES))
    print("3. ENGINE        : CLAUDE.md, scripts/, .claude/agents/, tasks/, docs/")
    print("   (run state, separate again: %s)" % ", ".join(STATE_FILES))

    rows = leakage()
    grand = sum(r[2] for r in rows)
    print("\n" + "-" * 78)
    print("USER-DATA REFERENCES INSIDE THE ENGINE — the packageability measure")
    print("-" * 78)
    # Columns are whatever the profile produced — the pattern set is no longer fixed, because
    # the terms come from the installation rather than from a hardcoded list here.
    cols = sorted({k for _, c, _ in rows for k in c}) or ["identity", "money", "phone"]
    print(("%-58s" + " %8s" * len(cols)) % tuple(["file"] + cols))
    for name, c, total in rows:
        shown = name if len(name) <= 58 else "…" + name[-57:]
        print(("%-58s" + " %8d" * len(cols))
              % tuple([shown] + [c.get(k, 0) for k in cols]))
    print("-" * 78)
    print("TOTAL: %d references across %d engine files." % (grand, len(rows)))
    print()
    print("  Not all of these are equal, and the distinction is the point:")
    print("   · DUPLICATED VALUES (money, geo, email used as config) are a DRIFT RISK —")
    print("     two sources of truth for one fact. The drift check below covers comp.")
    print("   · NARRATIVE references ('the candidate said X on 2026-07-22') are provenance, not")
    print("     configuration. They make the engine person-flavoured and would confuse a")
    print("     second user, but they cannot silently produce a WRONG ANSWER.")
    print("  So: a high count means 'not yet packageable'. It does NOT mean 'broken'.")

    problems = duplication_check()
    problems += criteria_check()
    print("\n" + "=" * 78)
    print("DUPLICATION CHECK — no comp values may live outside config.json")
    print("=" * 78)
    if not problems:
        print("  ✅ ZERO comp values in engine files. config.json is the single source")
        print("     of truth for figures; the prose carries only reasoning and pointers.")
        print()
        print("  ✅ CRITERION 1 — user data managed independently      (user.json)")
        print("  ✅ CRITERION 2 — configuration managed separately     (config.json)")
        print("  ✅ CRITERION 3 — engine reads both rather than embedding them")
        print("                   (mailboxes resolve from user.json; the signature")
        print("                    renders from a config template x user data)")
    else:
        for p in problems:
            print("  ❌ %s" % p)
        print("\n  the candidate, 2026-08-02: \"The json are suppose to be the sources of truth.\"")

    if args.report:
        return 0
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
