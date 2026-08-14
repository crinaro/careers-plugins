#!/usr/bin/env python3
"""
DO THE POINTERS RESOLVE? — the other half of engine/data separation.

WHY THIS EXISTS
---------------
The candidate, 2026-08-03, on a run of prose-trimming refactors: *"when you make cuts, have you
been checking that the same data exists in the data structure? If not, enhancing the data
structure to support what is being cut."*

**The honest answer was no.** Each refactor verified that every RULE survived in the rewritten
file — by asserting its text was still present. Nothing verified that the DATA each new pointer
points at actually exists. Those are different guarantees, and only the second one matters once
prose has been replaced by `config.geography.commute_anchors`.

**A dangling pointer is strictly worse than the duplication it replaced.** Duplicated prose can
drift and become wrong; a pointer to nothing makes the rule unreadable, and the reader has no text
to fall back on. Every trim that swaps a value for a reference must therefore prove the reference
lands.

**And the ad-hoc check is not good enough — this script exists because mine was wrong twice.**
Auditing by hand after the fact, the field was guessed as `cadence` when the schema says
`review_cadence`, and a channel was reported MISSING because its id was guessed rather than looked
up. Both produced a confident false alarm. A checker that reads the real schema cannot make either
mistake.

## What it verifies

    dotted config keys   `config.foo.bar` / `config.json.foo.bar` mentioned in an ENGINE file
                         must resolve in config.json
    user keys            `user.json`'s `identity.x` / `mailboxes` likewise
    scripts              every `scripts/<name>.py` named in an engine file must exist
    channel ids          a backticked id used as a channel must exist in data/channels.jsonl

Usage:
    python3 scripts/check_pointers.py            # exit 1 on any dangling pointer
    python3 scripts/check_pointers.py --verbose  # show every pointer checked, resolved or not

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
from _root import profile_root as _profile_root, engine_root as _engine_root, profile_or_fixture as _pof, engine_root as _engine_root, profile_or_fixture as _pof


def _profile_or_fixture():
    """CI runs with no profile. Fall back to the fixture so the gate is RUNNABLE — a pointer check
    that cannot execute proves nothing (2026-08-05)."""
    import os as _o
    r = _profile_root()
    if _o.path.exists(_o.path.join(r, "config.json")):
        return r
    fx = _o.path.join(_engine_root(), "tests", "fixtures", "profile")
    return fx if _o.path.exists(_o.path.join(fx, "config.json")) else r
ENGINE_SCRIPTS = os.path.dirname(os.path.realpath(__file__))

ROOT = _pof()
ENGINE_ROOT = _engine_root()

# ⭐ Issue #34, part 3. This used to glob `.claude/agents/`, `tasks/`, `.claude/skills/` and
# `docs/adr-*.md` OFF THE PROFILE ROOT — the pre-ADR-007 monorepo layout, where engine and
# profile shared one tree and those paths made sense. As a split marketplace install none of
# them exist under the profile; only `RULEBOOK.md` (already sourced from the engine) ever
# matched, so this silently scanned ONE file and printed "ALL RESOLVE" — the same vacuous-glob
# class already fixed in check_engine_purity.py. Named families (same shape as that file's
# ENGINE_FAMILIES), sourced from where the engine actually lives, and checked for emptiness in
# main() below rather than trusted to resolve.
#
# ⭐⭐ dev #77 / public #10, 2026-08-14: fixing the root above traded one vacuous-glob failure
# for another. `docs/adr-*.md` is design rationale — `publish_manifest.py` deliberately never
# ships it (`/docs/adr-` in PRIVATE_RULES: "the design rationale, deliberately not shipped") —
# so an INSTALLED engine has zero files under that glob BY DESIGN, and this gate printed
# "THE GATE SCANNED NOTHING — this is a BROKEN GATE" on every daily/weekly run for every real
# install, because `check_pointers.py` is instructed from daily-run and weekly-review SKILL.md.
# check_engine_purity.py hit the identical shape first (its `docs` family) and the fix there is
# the fix here too: an empty family is a hard failure in a CHECKOUT (a glob has gone stale) but
# only a NOTE in a SHIPPED PACKAGE (the family was never going to be there). `tests/` never
# ships, so its presence is the marker main() uses below to tell the two apart.
ENGINE_FAMILIES = (
    ("agents", os.path.join(ENGINE_ROOT, "agents", "*.md")),
    ("skills", os.path.join(ENGINE_ROOT, "skills", "*", "SKILL.md")),
    ("commands", os.path.join(ENGINE_ROOT, "commands", "*.md")),
    ("adrs", os.path.join(ENGINE_ROOT, "docs", "adr-*.md")),
)

ENGINE = (sorted(sum((glob.glob(pat) for _n, pat in ENGINE_FAMILIES), []))
          + [os.path.join(ENGINE_ROOT, "RULEBOOK.md")])

# `scripts/<name>.py` in an engine file can mean either THIS plugin's own scripts (most of them)
# or the marketplace's own tooling one level further up (`scripts/intake.py`,
# `scripts/check_marketplace.py` — CLAUDE.md names both as the marketplace's tools, and
# agents/skills legitimately point at them). Best-effort: if the marketplace root cannot be
# found (e.g. an install that only ships this one plugin), this is simply never a match, same
# as before.
MARKETPLACE_SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(ENGINE_ROOT)), "scripts")

# A dotted key is only a POINTER if it starts at a real top-level section. Without this the
# scanner treats prose like "config.json is the source" or a sentence-ending "…config.targets."
# as keys and invents failures.
CONFIG_RE = re.compile(r"`?\bconfig(?:\.json)?\.([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)")
USER_RE = re.compile(r"`?\buser\.json\.([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)")
SCRIPT_RE = re.compile(r"scripts/([A-Za-z0-9_]+\.(?:py|sh))")


def load(name):
    p = os.path.join(ROOT, name)
    with open(p, encoding="utf-8") as fh:
        if name.endswith(".jsonl"):
            return [json.loads(l) for l in fh if l.strip()]
        return json.load(fh)


def resolve(d, dotted):
    """Resolve a dotted path STRICTLY. Returns "ok" | "prefix:<path>" | "no".

    ⚠️ AN EARLIER VERSION WALKED BACK segment by segment and returned True on any prefix that
    resolved. That made it useless: a fabricated `config.positioning.tone_of_voice` passed
    because `config.positioning` exists. Induced-failure testing caught it — the checker
    reported ALL RESOLVE while the pointer it was handed was invented. Same over-permissive
    failure as the geo extractor in check_engine_purity.py, and the same lesson: a checker that
    is too generous certifies the bug.

    A prefix hit is now reported SEPARATELY, because it is the interesting case: the section is
    real and the leaf is not, which is exactly what a stale or aspirational pointer looks like.
    """
    parts = dotted.split(".")
    cur = d
    for i, p in enumerate(parts):
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            hit = ".".join(parts[:i])
            return ("prefix:" + hit) if i else "no"
    return "ok"


def main():
    ap = argparse.ArgumentParser(description="Do engine pointers resolve to real data?")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    print("ENGINE POINTERS — do rules in %s point at real data in %s?" % (ENGINE_ROOT, ROOT))
    print("=" * 74)

    # ⭐⭐ A SCAN THAT COVERED NOTHING IS A FAILURE, NEVER A PASS (issue #34, part 3 — same rule
    # as check_engine_purity.py). Nothing scanned at all is always a hard failure. But a single
    # empty FAMILY means different things in the two trees a copy of this script runs in:
    #
    #   checkout  every family should be populated, so an empty one is a stale glob (the
    #             rename this guard exists to catch) -> hard failure.
    #   package   `docs/adr-*.md` deliberately never ships (dev #77 / public #10, same shape as
    #             check_engine_purity.py's `docs` family) -> a note, not a failure. Hard-failing
    #             here made every real install's daily/weekly run print "BROKEN GATE" forever,
    #             which is worse than the vacuous scan this rule exists to catch.
    #
    # `tests/` never ships, so its presence is the marker for "this is a checkout".
    empty = [name for name, pat in ENGINE_FAMILIES if not glob.glob(pat)]
    if not ENGINE:
        print("  !! THE GATE SCANNED NOTHING — this is a BROKEN GATE, not a clean tree.")
        print("     Either ENGINE_ROOT above is wrong, or every family was renamed and this")
        print("     list was not. Do NOT read a green run as evidence of anything.")
        return 1
    if empty:
        is_checkout = os.path.isdir(os.path.join(ENGINE_ROOT, "tests"))
        if is_checkout:
            print("  !! FAMILY MATCHED NOTHING in a checkout: %s" % ", ".join(empty))
            print("     Every family should be populated here, so a glob has gone stale.")
            print("     Do NOT read a green run as evidence of anything.")
            return 1
        print("  note: family matching zero files in this package: %s" % ", ".join(empty))

    cfg, usr = load("config.json"), load("user.json")
    chan_ids = {c["id"] for c in load("data/channels.jsonl")}

    bad, checked = [], 0
    for path in ENGINE:
        rel = os.path.relpath(path, ENGINE_ROOT)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()

        for m in CONFIG_RE.finditer(text):
            key = m.group(1).rstrip(".,;:)`*")
            if key in ("json",):
                continue
            checked += 1
            r = resolve(cfg, key)
            if r != "ok":
                bad.append((rel, "config.%s   [%s]" % (key, r)))
            elif args.verbose:
                print("  ok  %-40s config.%s" % (rel, key))

        for m in USER_RE.finditer(text):
            key = m.group(1).rstrip(".,;:)`*")
            checked += 1
            r = resolve(usr, key)
            if r != "ok":
                bad.append((rel, "user.json.%s   [%s]" % (key, r)))

        for m in SCRIPT_RE.finditer(text):
            checked += 1
            name = m.group(1)
            if not (os.path.exists(os.path.join(ENGINE_SCRIPTS, name))
                    or os.path.exists(os.path.join(MARKETPLACE_SCRIPTS, name))):
                bad.append((rel, "scripts/%s" % name))

        # channel ids appear as `--stamp <id>` or in backticks next to the word channel
        for m in re.finditer(r"--stamp\s+`?([a-z0-9][a-z0-9-]{3,})`?", text):
            cid = m.group(1)
            if cid in ("channel_id", "<channel_id>", "id"):
                continue
            checked += 1
            if cid not in chan_ids:
                bad.append((rel, "channel id %r" % cid))

    print("  %d checked across %d file(s)" % (checked, len(ENGINE)))
    if not bad:
        print("  ALL RESOLVE. Every value a rule points at exists in the data.")
        return 0

    print("  ⚠️  %d DANGLING POINTER(S) — the rule is unreadable, and there is no prose to fall"
          % len(bad))
    print("      back on. This is strictly worse than the duplication it replaced.\n")
    for rel, ptr in bad:
        print("    %-44s %s" % (rel, ptr))
    print("\n  FIX BY ADDING THE DATA, not by putting the value back in prose.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
