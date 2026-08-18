#!/usr/bin/env python3
"""Does a hand-authored action item claim something the operating store already disproves?

GitHub #43. The decision surface carries two kinds of item. Derived ones — role decisions
from `opportunities.jsonl`, relationship follow-ups from `channels.jsonl` — cannot go stale,
because they are filters over records and vanish when the record changes. The cross-cutting
asks (`data/asks.jsonl` since dev #93; focus.md prose before that) have hand-authored TEXT
that can assert a state the store already contradicts, and nothing compared the two: the
schema validator checks structure, the section checker checks phrasing and duplication, and
the mailbox reconciliation touches the communications store but never the ask text. So a
resolved ask sat listed as pending until a human happened to notice.

⭐ THIS IS THE BACKSTOP, NOT THE PRIMARY CONTROL. #44 removes the drift class for items that
are derivable at all; this catches what is left, which should be only genuinely unmodelled
asks. A shrinking output here is the design working.

⚠️ ADVISORY, AND DELIBERATELY CONSERVATIVE — EXIT 0 ALWAYS. Matching prose to records is
inexact, and a check that cries wolf is a check somebody switches off. It flags only when a
named entity in the item is matched to a dated record that is NEWER than the item's own
deadline; it never edits, never blocks, and says plainly that a flag is a question.

Usage:
    python3 scripts/check_action_claims.py
    python3 scripts/check_action_claims.py --verbose

Python 3.9+. Standard library only.
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _root import profile_or_fixture as _pof                       # noqa: E402
import your_move as _ym                                            # noqa: E402

ROOT = _pof()
DATE_RE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")


def rows(rel):
    path = os.path.join(ROOT, "data", rel)
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)
    except (OSError, ValueError):
        return


def hand_authored_items():
    """The OPEN rows of data/asks.jsonl — the asks whose TEXT is still authored by a human.

    (Until dev #93 this parsed focus.md's `## ⚡ Your Move` section; the asks are a store
    now, but a store row's prose can still claim what the record contradicts, so the
    backstop reads the same rows the dashboard renders.) Membership is `your_move.
    open_asks`'s, never re-derived here. Returns (title, ask, opp_id) tuples — the shape
    the flagging loop below has always consumed."""
    return [(a.get("title") or a.get("id") or "?", a.get("ask") or "", a.get("opp_id"))
            for a in _ym.open_asks(list(rows("asks.jsonl")))]


def known_entities():
    """Names the store knows about, each mapped to the newest dated evidence of contact.

    Only entities the operating store actually models are considered. A proper noun the
    store has never heard of cannot be reconciled against anything, and guessing at one is
    how a checker starts producing noise."""
    ent = {}

    def note(name, when, why):
        name = str(name or "").strip()
        if len(name) < 4 or not when:
            return
        cur = ent.get(name)
        if cur is None or str(when) > cur[0]:
            ent[name] = (str(when), why)

    # ⭐ `last_touch` was removed from the channel schema (GitHub #79) — nothing ever wrote it
    # mechanically, so this read was already dead in practice, and it would now be reading a
    # rejected key besides. `your_move.derive_channel_last_touch` is the correct source: the
    # max of an outbound message joined by contact_id and the latest log[] entry.
    #
    # dev #82 (2026-08-14): the 0.24.0 fix below was landed with no test on the CONTACTS
    # branch specifically — every existing regression case named the channel's own label, so
    # the per-contact `note()` call two lines down had never been exercised. Verified by
    # fail-on-purpose (reverting it to the old dead `c.get("last_touch")` read turns
    # TestCheckActionClaimsDerivesTheTouch red): the derivation itself was already correct,
    # it only lacked coverage. No further rewrite is needed here.
    _channel_rows = list(rows("channels.jsonl"))
    _message_rows = list(rows("messages.jsonl"))
    for c in _channel_rows:
        label = c.get("label") or c.get("id")
        derived, _evidence = _ym.derive_channel_last_touch(c, _message_rows)
        if derived:
            note(label, derived, "the channel's derived last touch (outbound message or log)")
        note(label, c.get("last_reviewed"), "the channel's own last_reviewed")
        for person in (c.get("contacts") or []):
            if isinstance(person, dict):
                note(person.get("name"), derived, "a touch on their channel")

    for m in _message_rows:
        if m.get("direction") == "outbound":
            note(m.get("to"), m.get("sent_on"), "an outbound message in messages.jsonl")

    for o in rows("opportunities.jsonl"):
        by_id = {c.get("contact_id"): c for c in (o.get("contacts") or [])
                 if isinstance(c, dict)}
        for out in (o.get("outreach") or []):
            if not isinstance(out, dict) or out.get("status") != "sent":
                continue
            person = by_id.get(out.get("contact_id")) or {}
            note(person.get("name"), out.get("date"), "a sent outreach row")
    return ent


TERMINAL = {"passed", "expired"}


def closed_roles_named_in_prose():
    """Roles the RECORD has closed that the hand-written narrative still discusses — #60.

    The surviving hand-written narrative is `handoff.md`, the session-handoff letter (until
    dev #93 it was a focus.md section, alongside generated content this check had to strip
    first; the store cutover removed the generated half entirely, so the whole file is
    hand-authored now and is read as-is). The incident this catches: a coordinator startup
    reported two roles as open decisions awaiting the operator when the pipeline had
    recorded one `passed`/`closed` two days earlier and the other applied the day before.

    ⚠️ Note what was NOT wrong in that incident: the dashboard. The generated surface
    filtered the closed role out correctly. Only the narrative drifted — which is the
    strongest argument that the prose copy has negative value, and why this flags it.

    Conservative on purpose: only TERMINAL records count. A role that is merely quiet is a
    legitimate thing to still be writing about; one recorded `passed` or `expired` is not.
    """
    try:
        with open(os.path.join(ROOT, "handoff.md"), encoding="utf-8") as fh:
            prose = fh.read()
    except OSError:
        return []

    companies = {}
    for c in rows("companies.jsonl"):
        if c.get("id") and c.get("name"):
            companies[c["id"]] = str(c["name"])

    out = []
    for o in rows("opportunities.jsonl"):
        status = str(o.get("status") or "")
        if status not in TERMINAL and str(o.get("verdict") or "") != "pass":
            continue
        name = companies.get(o.get("company_id"))
        if not name or len(name) < 4 or name not in prose:
            continue
        out.append((name, str(o.get("title") or ""), status or "verdict:pass"))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    print("ACTION CLAIMS — is a hand-authored ask already answered by the data?")
    print("=" * 78)
    items = hand_authored_items()
    ent = known_entities()
    print("  open ask(s) with hand-authored text: %d   ·   entities the store knows: %d"
          % (len(items), len(ent)))

    if not items:
        print("\n  No open asks in data/asks.jsonl — every Your Move item is derived from a")
        print("  record and cannot drift. That is the #44 end state, not an empty check.")
        return 0
    if not ent:
        # ⚠️ NOT A CLEAN RESULT. No entities means nothing could have been compared, which is
        # the vacuous-scan shape this repo keeps re-finding. Say so rather than print OK.
        print("\n  !! NOTHING TO COMPARE AGAINST — the store yielded no dated entities.")
        print("     This is NOT a clean result; it means the check could not run.")
        return 0

    flagged = []
    for item in items:
        # hand_authored_items yields (title, ask, opp_id) from the open rows of asks.jsonl.
        title, ask = item[0], item[1]
        text = "%s %s" % (title, ask)
        deadline = max(DATE_RE.findall(text) or [""])
        for name, (when, why) in ent.items():
            if name not in text:
                continue
            # Only newer-than-the-ask evidence is a contradiction. Without the date
            # comparison every item naming a known contact would flag forever.
            if deadline and when <= deadline:
                continue
            flagged.append((title, name, when, why, deadline))
            break

    closed = closed_roles_named_in_prose()
    if closed:
        print("\n  %d CLOSED role(s) still discussed in the hand-written narrative (#60):"
              % len(closed))
        for name, title, why in closed:
            print("    · %s — %s   record says %s" % (name, title[:44], why))
        print("      The record is the source of truth; the prose is a copy that drifted.")
        print("      Remove the narrative mention — the generated sections already reflect it.")

    if not flagged:
        if not closed:
            print("\n  No hand-authored ask is contradicted by a newer record.")
        return 0

    print("\n  %d item(s) a record may already have answered — QUESTIONS, not verdicts:"
          % len(flagged))
    for title, name, when, why, deadline in flagged:
        print("    · %s" % title[:66])
        print("        %s was last contacted %s (%s)%s"
              % (name, when, why, (", after this ask's %s" % deadline) if deadline else ""))
    print("\n  If the action already happened, resolve the ask (resolved_on + resolution) or")
    print("  move it onto the record — a channel's next_touch or an opportunity's next_action")
    print("  surfaces on Your Move by itself and leaves it by itself. See GitHub #44.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
