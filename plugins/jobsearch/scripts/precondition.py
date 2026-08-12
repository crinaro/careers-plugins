#!/usr/bin/env python3
"""A draft's send-precondition, as data instead of prose. Resolves against the pipeline.

⭐ THE DEFECT THIS CLOSES (GitHub issue #6)
-------------------------------------------
A multi-part sequence stages part A (sent) and part B (held until the recipient accepts or
replies). The precondition on part B lived in `**Status:**` prose, so **the dashboard rendered
every staged draft under a heading meaning "awaiting your approval to send"** — including the ones
the candidate cannot act on at all. One observed state: seven items presented as needing him, one
genuinely actionable.

That inverts the surface. `focus.md`'s invariant is that a Your Move line must read as a question
or an imperative aimed at the candidate, and **a draft that cannot be sent yet is neither**. The
predictable result is that he learns to skim the one list that is supposed to be unskippable.

⭐⭐ AND THE DATA WAS ALREADY THERE. Every outreach touch carries `outcome`
(awaiting/accepted/replied/…), `responded_on`, `contact_id` and `message_ref`. The system could
always answer *"has this person accepted or replied?"* — what was missing was any machine-readable
link from the draft to the touch it waits on. **A missing join, not missing information**, which
is why this is cheap to fix and expensive to leave.

## The general rule this is the first instance of

    ⭐ A FACT A RUN KNOWS GOES INTO THE QUERYABLE STORE, NEVER INTO NARRATIVE.

Issues #4 and #5 are the same defect in different clothes — a coverage gap in a run-log narrative,
a run's findings in a session buffer that never reaches disk. The engine has solved it once before:
open fit questions carried urgency in prose until `act_by` made it a sortable field. This module is
that move applied to drafts, and the shape is meant to be copied rather than re-invented.

## The field

One meta line in the draft entry, alongside `**Medium:**` and the rest:

    **Blocked until:** contact:dana-holbrook outcome:accepted|replied

`contact:` is a `contact_id` as it appears in `contacts[]`. `outcome:` is one or more values from
the outreach outcome enum, `|`-separated. The draft is **sendable** when any outreach touch to that
contact has one of those outcomes.

⚠️ **An unparseable value is reported, never guessed.** A precondition nobody can read is worse
than none: it looks handled and is not. `--check` fails on one, so a typo surfaces at run start
rather than by a draft silently sitting in the wrong group.

## ⭐ THE LEGACY PROSE FORM IS DETECTED, NEVER TREATED AS ABSENT (GitHub issue #13)

The first release of this module shipped the field with no migration and no enforcement, so a
profile predating it kept its preconditions in prose — and this tool reported every one of those
drafts **sendable, "no precondition"**. Four drafts saying *in their own titles* that they were
held pending someone's acceptance rendered as `9 sendable, 0 blocked`. A false green is worse
than the prose it replaced, because it stops anyone looking.

The root error: "no draft is blocked" and "no draft has been migrated" are opposite states, and
absence-of-field rendered them identically. So now:

- a draft whose text carries a **hold phrase** (`HOLD_RE`) but no structured field is state
  `unresolved` — a migration gap, named as such, never sendable;
- `**Blocked until:** unresolved`, which `migrate.py`'s 0.18.0 migration writes under each
  detected prose hold, is the same state as durable data rather than a heuristic re-match;
- `--check` fails on `unresolved` exactly as on `unreadable`, and the summary reports the count.

The way out of `unresolved` is a human (or the drafting agent) replacing it with the real join:
`contact:<contact_id> outcome:<...>` — or, for a false positive, rewording the prose. The
default direction is deliberate: the cheap error is a draft waiting for a look, the expensive
one is a blocked draft presented as actionable.

Usage:
    python3 precondition.py            # every draft: sendable / blocked / unresolved / unreadable
    python3 precondition.py --json
    python3 precondition.py --check    # exit 1 on an unparseable or unresolved precondition

Python 3.9+. Standard library only.
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root

FIELD_RE = re.compile(r"^\*\*Blocked until:\*\*\s*(.+?)\s*$", re.M | re.I)
TOKEN_RE = re.compile(r"(contact|outcome)\s*:\s*([A-Za-z0-9_|-]+)")

# The literal the migration writes when it finds a prose hold it cannot structure itself.
UNRESOLVED_RE = re.compile(r"^unresolved\b", re.I)

# The legacy prose forms actually observed in the field (issue #13: "held until she accepts",
# "held until each accepts", "still pending her acceptance") plus their near neighbours.
# Deliberately pronoun-restricted so a drafted message ADDRESSED to the recipient ("once you
# accept") does not trip it; a false positive only parks a draft as `unresolved` for a look,
# while a false negative reproduces the false green this exists to close.
HOLD_RE = re.compile(
    r"\bheld\s+(?:until|pending)\b"
    r"|\bhold\s+until\b"
    r"|\bpending\s+(?:[\w'’]+\s+){0,3}?acceptance\b"
    r"|\bawaiting\s+(?:[\w'’]+\s+){0,2}?acceptance\b"
    r"|\b(?:until|once)\s+(?:she|he|they|each|either|both|the\s+recipient)\s+"
    r"(?:accepts?|replies|responds?)\b",
    re.I)

# States that must NEVER render as "needs you". Owned here so consumers (generate_dashboard.py)
# group by membership instead of re-deriving the set — `state != "blocked"` was how `unreadable`
# drafts ended up under "awaiting your approval to send".
NOT_SENDABLE = frozenset({"blocked", "unreadable", "unresolved"})

# Sentinels used by drafts_with_preconditions for the two non-parse states (see its docstring).
PROSE_HOLD = "prose-hold"
UNRESOLVED = "unresolved"

# The outreach outcome enum, mirrored from validate_data. Kept as a literal so a precondition
# naming a value that does not exist is caught here rather than resolving to "never satisfied".
OUTCOMES = {"awaiting", "accepted", "replied", "no-response", "declined", "meeting-booked"}


class PreconditionError(ValueError):
    """Unparseable. Deliberately loud — see the module docstring."""


def parse(raw):
    """'contact:x outcome:accepted|replied' -> {'contact': 'x', 'outcomes': {...}}"""
    found = dict(TOKEN_RE.findall(raw or ""))
    contact = found.get("contact")
    outcomes = {o for o in (found.get("outcome") or "").split("|") if o}
    if not contact:
        raise PreconditionError("no `contact:<contact_id>` in %r" % raw)
    if not outcomes:
        raise PreconditionError("no `outcome:<value>` in %r" % raw)
    bad = outcomes - OUTCOMES
    if bad:
        raise PreconditionError(
            "outcome %s is not in the enum {%s} — a value that cannot occur means the draft is "
            "blocked forever and nothing will say so"
            % (", ".join(sorted(bad)), ", ".join(sorted(OUTCOMES))))
    return {"contact": contact, "outcomes": outcomes}


def touches_by_contact(root):
    """contact_id -> [outreach rows], across every opportunity."""
    out = {}
    path = os.path.join(root, "data", "opportunities.jsonl")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                for o in r.get("outreach") or []:
                    cid = o.get("contact_id")
                    if cid:
                        out.setdefault(cid, []).append(o)
    except OSError:
        pass
    return out


def resolve(pre, touches):
    """Satisfied? Returns (bool, evidence_or_reason). Evidence names WHICH touch satisfied it."""
    rows = touches.get(pre["contact"]) or []
    if not rows:
        return False, "no outreach touch to %r yet" % pre["contact"]
    for o in rows:
        if o.get("outcome") in pre["outcomes"]:
            return True, "%s outcome=%s on %s" % (pre["contact"], o.get("outcome"),
                                                  o.get("responded_on") or o.get("date") or "?")
    have = sorted({str(o.get("outcome")) for o in rows})
    return False, "%s has %s; waiting for %s" % (pre["contact"], "/".join(have),
                                                 "|".join(sorted(pre["outcomes"])))


def drafts_with_preconditions(root, filename="drafts.md"):
    """[(title, raw_or_None, parsed)] for every '## ' entry, where parsed is one of:

        None               no field, no hold phrase — genuinely sendable
        PROSE_HOLD         no field, but the text carries a hold phrase — the legacy prose
                           form of issue #13; a migration gap, never sendable
        UNRESOLVED         a `**Blocked until:** unresolved …` marker (what the migration
                           writes) — known blocked, join not yet structured
        PreconditionError  a field nobody can read — loud, never guessed over
        dict               a parsed precondition, ready for resolve()
    """
    path = os.path.join(root, filename)
    try:
        with open(path, encoding="utf-8") as fh:
            md = fh.read()
    except OSError:
        return []
    out = []
    for m in re.finditer(r"^##\s+(.+?)$(.*?)(?=^##\s|\Z)", md, re.M | re.S):
        title, body = m.group(1).strip(), m.group(2)
        fm = FIELD_RE.search(body)
        if not fm:
            if HOLD_RE.search(title) or HOLD_RE.search(body):
                out.append((title, None, PROSE_HOLD))
            else:
                out.append((title, None, None))
            continue
        raw = fm.group(1)
        if UNRESOLVED_RE.match(raw):
            out.append((title, raw, UNRESOLVED))
            continue
        try:
            out.append((title, raw, parse(raw)))
        except PreconditionError as e:
            out.append((title, raw, e))
    return out


def report(root):
    touches = touches_by_contact(root)
    rows = []
    for title, raw, parsed in drafts_with_preconditions(root):
        if parsed is None:
            rows.append({"title": title, "state": "sendable", "why": "no precondition"})
        elif parsed is PROSE_HOLD:
            rows.append({"title": title, "state": "unresolved",
                         "why": "hold phrase in prose but no structured precondition — the "
                                "pre-0.18.0 form; write `**Blocked until:** contact:<id> "
                                "outcome:<...>` (or reword the prose if it is not a hold)"})
        elif parsed is UNRESOLVED:
            rows.append({"title": title, "state": "unresolved",
                         "why": "known blocked, precondition not yet structured (%s) — replace "
                                "with `contact:<id> outcome:<...>`" % raw})
        elif isinstance(parsed, PreconditionError):
            rows.append({"title": title, "state": "unreadable", "why": str(parsed)})
        else:
            ok, why = resolve(parsed, touches)
            rows.append({"title": title, "state": "sendable" if ok else "blocked", "why": why})
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if any precondition is unreadable or unresolved")
    args = ap.parse_args()

    rows = report(profile_root())
    if args.json:
        print(json.dumps(rows, indent=1))
    else:
        print("DRAFT PRECONDITIONS — what can actually be sent right now\n")
        for r in rows:
            mark = {"sendable": "✅", "blocked": "⏳", "unreadable": "⛔",
                    "unresolved": "🚧"}[r["state"]]
            print("  %s %-10s %s" % (mark, r["state"], r["title"][:72]))
            print("        %s" % r["why"])
        n_send = sum(1 for r in rows if r["state"] == "sendable")
        n_block = sum(1 for r in rows if r["state"] == "blocked")
        n_unres = sum(1 for r in rows if r["state"] == "unresolved")
        line = "\n  %d sendable · %d blocked on someone else" % (n_send, n_block)
        if n_unres:
            line += " · %d unresolved (precondition in prose, not yet structured)" % n_unres
        print(line)
        if n_block:
            print("  ⭐ Blocked drafts must NOT render as 'needs you'. A line there has to be a")
            print("     question or an imperative aimed at the candidate; a draft he cannot send")
            print("     is neither, and padding that list is how it stops being read.")
        if n_unres:
            print("  🚧 An unresolved draft is treated as blocked, not sendable — 'no draft is")
            print("     blocked' and 'no draft has been migrated' are opposite states (#13).")
            print("     Structure each with `**Blocked until:** contact:<id> outcome:<...>`.")

    if args.check:
        bad = [r for r in rows if r["state"] in ("unreadable", "unresolved")]
        for r in bad:
            print("⛔ %s [%s]: %s" % (r["title"][:60], r["state"], r["why"]), file=sys.stderr)
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
