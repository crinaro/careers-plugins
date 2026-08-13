#!/usr/bin/env python3
"""Are the two narrative files still MACHINE-READABLE?

⭐ WHY THIS EXISTS
------------------
`resume.md` and `projects.md` are human-edited by design — they are the candidate's own prose and
must stay that way. But two conventions inside them are load-bearing for the ENGINE, and until
now nothing checked either one:

    projects.md   every proof point carries a `**Surface when:**` trigger. Eight agents and
                  skills GREP for it. That grep is how a project reaches a draft at all.

    resume.md     an `Additional Detail` addenda section holds facts confirmed but deliberately
                  not printed. Agents check it before concluding a fact is unavailable.

**Neither is validated by `validate_data.py`** — that gates the four JSONL files and only mentions
these two in prose. So the conventions that make the files usable were enforced by nothing.

## ⚠️ THE FAILURE IS SILENT, WHICH IS WHY IT NEEDS A GATE

Reword the heading, or write `Surface if:`, or drop a colon, and every grep returns nothing. The
run does not error. It reports *no matching proof points* — which is exactly what it reports when
the candidate genuinely has none. **A malformed trigger and an empty file are indistinguishable
downstream**, and the cost lands on the one artifact that most needs the evidence: the draft.

⚠️ **Honest scope: on the profile it was written against, this found NOTHING broken** — 38
well-formed triggers and no defects. It is a guard against a class of failure that has not
happened yet, not a fix for one that had. Said plainly because the opposite claim is the kind
this project keeps having to unpick: a first draft of this header asserted it had caught a real
malformed trigger, which came from misreading a legitimate parenthetical variant as a defect.

## What is a FAILURE and what is a NOTE

**FAIL** — a trigger line whose bold never closes (`**Surface when: foo` with no `:**`). The
`**` runs on, the trigger text is swallowed into the bold run, and what a reader sees is a
formatting smudge rather than a missing hook.

**FAIL** — `resume.md` with no `Additional Detail` heading at all. Agents check the addenda
before concluding a fact is unavailable; without the heading that check silently finds nothing.

**NOTE** — an entry with no trigger. Some sections legitimately are not proof points (a list of
open questions, for instance), so this cannot be an error without producing a permanent false
positive — and a gate that cries wolf is a gate somebody switches off.

**DELIBERATELY NOT A FAILURE** — a variant like `**Surface when (add to the triggers above):**`.
It is line-initial, it closes its bold, and it is findable. Narrowing the rule to one exact
string would break a form the candidate chose on purpose, which is not this gate's business.

⚠️ This checker does NOT read the content of either file beyond its structure, and it never
prints a proof point. Structure is engine business; the content is the candidate's.

Usage:
    check_narrative.py            # exit 1 if a convention is broken
    check_narrative.py --verbose  # show every trigger it found, by line

Python 3.9+. Standard library only.
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root

# ⭐ ONLY LINE-INITIAL TRIGGERS COUNT — and getting this wrong makes the gate useless.
#
# The first version matched the words "surface when" anywhere on a line. It reported five
# failures against a healthy file: three were PROSE ABOUT the convention (the file's own header
# explaining it, in backticks), one was the ordinary English verb ("surface for any role
# where..."), and only one was a real defect. A gate with a 4-in-5 false-positive rate is a gate
# that gets ignored, which is worse than no gate — so the shape is now anchored.
#
# A trigger is a line that BEGINS (after optional list marker) with a bold `Surface when`.
TRIGGER_LINE = re.compile(r"^\s*(?:[-*+]\s*)?\*\*Surface\s+when\b", re.I)

# ...and it must close its bold before the trigger text. `**Surface when: foo` renders as one
# runaway bold run and the trigger text is swallowed into it.
WELL_FORMED = re.compile(r"^\s*(?:[-*+]\s*)?\*\*Surface\s+when\b[^*\n]*:\*\*", re.I)

# Any mention at all — used ONLY to decide whether an entry has a trigger, never to fail one.
ANY_MENTION = re.compile(r"Surface\s+when\b", re.I)

ADDENDA = re.compile(r"^#{1,6}\s*Additional Detail\b", re.M)


def check_projects(path, verbose=False):
    """Returns (failures, notes, trigger_count)."""
    fails, notes, good = [], [], 0
    if not os.path.exists(path):
        return [("projects.md", 0, "file is missing entirely")], [], 0
    with open(path, encoding="utf-8") as fh:
        text = fh.read()

    for n, line in enumerate(text.split("\n"), 1):
        if not TRIGGER_LINE.search(line):
            continue                      # prose mentioning the convention is not a trigger
        if WELL_FORMED.search(line):
            good += 1
            if verbose:
                notes.append(("projects.md", n, "ok: %s" % line.strip()[:60]))
        else:
            fails.append(("projects.md", n,
                          "trigger never closes its bold — %r. The `**` runs on, the trigger "
                          "text is swallowed into it, and a reader sees a formatting smudge "
                          "rather than a missing hook." % line.strip()[:60]))

    blocks = re.split(r"^## ", text, flags=re.M)[1:]
    for b in blocks:
        title = b.split("\n")[0].strip()[:56]
        if not ANY_MENTION.search(b):
            notes.append(("projects.md", 0,
                          "entry %r has no trigger — it can only be found by someone who "
                          "already knows it is there" % title))
    return fails, notes, good


def check_resume(path):
    fails = []
    if not os.path.exists(path):
        return [("resume.md", 0, "file is missing entirely")]
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if not ADDENDA.search(text):
        fails.append(("resume.md", 0,
                      "no `Additional Detail` heading. Agents check the addenda before "
                      "concluding a fact is unavailable; without this heading that check "
                      "silently finds nothing and real, confirmed facts go unused."))
    return fails


def main():
    ap = argparse.ArgumentParser(description="Are the narrative conventions still readable?")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    root = profile_root()

    projects = os.path.join(root, "projects.md")
    resume = os.path.join(root, "resume.md")

    print("NARRATIVE CONVENTIONS — can the engine still find what is in these files?")
    print("=" * 78)
    print("  profile: %s" % root)

    if not os.path.exists(projects) and not os.path.exists(resume):
        # No profile on this host (CI, a fresh clone). Say NOT CHECKED rather than clean —
        # the same rule the purity gate learned the hard way.
        print("\n  !! NOT CHECKED: neither projects.md nor resume.md is present here.")
        print("     Expected in CI. This is NOT a clean result and must not be read as one.")
        return 0

    fails, notes, good = check_projects(projects, args.verbose)
    fails += check_resume(resume)

    print("  %d well-formed trigger(s) in projects.md" % good)

    if notes:
        print()
        for f, n, msg in notes:
            print("  note  %s%s  %s" % (f, (":%d" % n) if n else "", msg))

    if not fails:
        print("\n  CLEAN. Every trigger is greppable and the addenda section is present.")
        return 0

    print()
    print("  !! %d BROKEN CONVENTION(S) — each one is INVISIBLE to the engine" % len(fails))
    for f, n, msg in fails:
        print("     %s%s" % (f, (":%d" % n) if n else ""))
        print("        %s" % msg)
    print()
    print("  These do not raise at run time. A malformed trigger and an empty file produce the")
    print("  same downstream result — 'no matching proof points' — so the failure surfaces as a")
    print("  weaker draft, weeks later, with nothing pointing back here.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
