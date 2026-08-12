#!/usr/bin/env python3
"""Do the enum values an agent is told to emit VERBATIM still agree with the schema?

⭐ WHY THIS EXISTS (GitHub #5)
------------------------------
`outreach-drafter.md` names four fields — `medium`, `touch_type`, `recipient_role`,
`address_status` — and says *"you are the only actor that knows which medium applies,"*
instructing the agent to fill them in and copy them verbatim into the outreach record.
`validate_data.py` gates every one of those fields against a fixed enum. Nothing connected the
two: the agent spec could say anything, the schema could change under it, and the first anyone
would learn of a mismatch was a draft rejected by the gate — or worse, three invalid values
landing in one draft because nothing constrained them at all.

**Fix the class, not the instance.** Patching that one agent's prose leaves every other agent
free to drift the same way the moment a schema changes — an enum gets a value renamed, and
whichever spec still names the old one goes stale silently.

## THE MECHANISM — deliberately NOT free-text scanning

⚠️ A gate that scans prose for words that happen to match a valid enum value has a brutal
false-positive rate — `check_narrative.py`'s first version was wrong 4 times out of 5 doing
exactly that shape of thing. Mentioning a field name, or using one of its values in an example
sentence, is not the same as INSTRUCTING an agent to emit it.

So this checks a single, deliberately-authored, unambiguous block and nothing else:

    <!-- verbatim-enum:start -->
    - `field_name`: value-one | value-two | value-three
    <!-- verbatim-enum:end -->

Nothing outside that exact pair of HTML comments is ever read. An agent file with no such block
is not an error — most agents correctly have nothing to check — and NOTHING about the rest of
the file's prose is scanned, so a field name mentioned in passing can never trigger a false
positive. The only way to get a false NEGATIVE (a real drift this gate misses) is to write a
verbatim-enum block that is wrong in a way that still parses — which is exactly why every field
named inside one is required to be a KNOWN schema-gated field (below); an unrecognised field name
is itself a PROBLEM, not silently skipped, so a typo in the block cannot go unchecked either.

## What is checked, and against what

    FIELD              VALIDATED AGAINST validate_data.py's
    medium             MEDIA
    touch_type         TOUCH_TYPES
    recipient_role     RECIPIENT_ROLES
    address_status     ADDRESS_STATUS
    delivery           DELIVERY

The comparison is SET EQUALITY, not subset — a block missing a value the schema allows is also a
problem (an agent that can never know that value exists cannot emit it), not just a block naming
a value the schema rejects.

Usage:
    python3 scripts/check_verbatim_enums.py            # exit 1 on any mismatch
    python3 scripts/check_verbatim_enums.py --verbose  # show every block found, matched or not

Python 3.9+. Standard library only.
"""

import argparse
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _root import engine_root as _engine_root

ENGINE_ROOT = _engine_root()

# ⭐ NAMED, NOT INLINED — a family that stops matching anything (a rename, a moved directory)
# must be visible rather than silently scanning zero files. Same shape as check_engine_purity.py's
# ENGINE_FAMILIES, and for the same reason.
AGENT_FAMILIES = (
    ("agents", os.path.join(ENGINE_ROOT, "agents", "*.md")),
    ("skills", os.path.join(ENGINE_ROOT, "skills", "*", "SKILL.md")),
    ("commands", os.path.join(ENGINE_ROOT, "commands", "*.md")),
)

# The one place a field name maps to the constant that governs it. Adding a new verbatim-checked
# field to some future agent spec means adding one line here — not writing a new gate.
FIELD_TO_CONSTANT = {
    "medium": "MEDIA",
    "touch_type": "TOUCH_TYPES",
    "recipient_role": "RECIPIENT_ROLES",
    "address_status": "ADDRESS_STATUS",
    "delivery": "DELIVERY",
}

_BLOCK = re.compile(r"<!--\s*verbatim-enum:start\s*-->(.*?)<!--\s*verbatim-enum:end\s*-->",
                    re.S)
_LINE = re.compile(r"^-\s*`([A-Za-z0-9_]+)`\s*:\s*(.+?)\s*$")


def find_blocks(text):
    """Yield (field, [values]) for every line inside every verbatim-enum block in `text`.
    Malformed lines (present inside a block, not matching the expected shape) are yielded as
    (None, [raw_line]) so the caller can report them rather than silently skip them."""
    for block in _BLOCK.findall(text):
        for line in block.splitlines():
            line = line.strip()
            if not line:
                continue
            m = _LINE.match(line)
            if not m:
                yield None, [line]
                continue
            field = m.group(1)
            values = [v.strip() for v in m.group(2).split("|")]
            yield field, values


def load_schema():
    """Import validate_data.py's enum constants. Never the values duplicated here — this
    script would become exactly the drift it exists to catch."""
    import validate_data as vd
    return vd


def check_file(path, vd, verbose=False):
    """Returns (problems, notes, blocks_found) for one file."""
    problems, notes = [], []
    rel = os.path.relpath(path, ENGINE_ROOT)
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as e:
        return (["%s: unreadable — %s" % (rel, e)], [], 0)

    found = list(find_blocks(text))
    for field, values in found:
        if field is None:
            problems.append("%s: malformed verbatim-enum line — %r (expected "
                            "'- `field_name`: value | value | ...')" % (rel, values[0]))
            continue
        if field not in FIELD_TO_CONSTANT:
            problems.append("%s: verbatim-enum names `%s`, which is not a known schema-gated "
                            "field (%s). Typo, or a genuinely new field that needs adding to "
                            "FIELD_TO_CONSTANT in check_verbatim_enums.py?"
                            % (rel, field, ", ".join(sorted(FIELD_TO_CONSTANT))))
            continue
        const_name = FIELD_TO_CONSTANT[field]
        schema_values = getattr(vd, const_name, None)
        if schema_values is None:
            problems.append("%s: `%s` maps to `validate_data.%s`, which does not exist — the "
                            "schema was renamed and this mapping was not" % (rel, field, const_name))
            continue
        listed = set(values)
        schema = set(schema_values)
        extra = listed - schema           # instructed but not schema-valid — the reported bug
        missing = schema - listed         # schema-valid but the agent is never told about it
        if extra:
            problems.append("%s: `%s` instructs verbatim emission of %s — NOT in "
                            "validate_data.%s. Copying these verbatim would fail the data gate."
                            % (rel, field, ", ".join("%r" % v for v in sorted(extra)), const_name))
        if missing:
            problems.append("%s: `%s`'s verbatim-enum block is missing %s from "
                            "validate_data.%s — the agent can never emit a value it is never "
                            "told exists." % (rel, field, ", ".join("%r" % v for v in sorted(missing)),
                                              const_name))
        if verbose and not extra and not missing:
            notes.append("%s: `%s` OK (%d value(s))" % (rel, field, len(schema)))
    return problems, notes, len(found)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verbose", action="store_true", help="show every block found, matched or not")
    args = ap.parse_args()

    print("VERBATIM ENUMS — do agent specs instructing verbatim emission agree with the schema?")
    print("  engine: %s" % ENGINE_ROOT)

    empty = [name for name, pat in AGENT_FAMILIES if not glob.glob(pat)]
    if empty:
        print("\n  !! family matching ZERO files: %s — this gate would silently check nothing "
              "for it. Renamed directory, or a wrong engine root." % ", ".join(empty))
        return 1

    try:
        vd = load_schema()
    except Exception as e:
        print("\n  !! could not import validate_data.py — %s. This gate cannot run without the "
              "schema it checks against." % e)
        return 1

    problems, notes, total_blocks, files_with_blocks = [], [], 0, 0
    for _name, pattern in AGENT_FAMILIES:
        for path in sorted(glob.glob(pattern)):
            p, n, count = check_file(path, vd, verbose=args.verbose)
            problems += p
            notes += n
            total_blocks += count
            if count:
                files_with_blocks += 1

    print("  %d verbatim-enum block line(s) found across %d file(s)"
          % (total_blocks, files_with_blocks))

    if args.verbose:
        for n in notes:
            print("    %s" % n)

    if not problems:
        print("\n  CLEAN. Every verbatim-emission instruction agrees with validate_data.py.")
        return 0

    print("\n  %d PROBLEM(S)" % len(problems))
    for p in problems:
        print("    - " + p)
    return 1


if __name__ == "__main__":
    sys.exit(main())
