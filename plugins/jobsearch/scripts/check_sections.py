#!/usr/bin/env python3
"""Enforce the section invariants against the stores that feed the generated dashboard.

WHY THIS EXISTS (2026-07-20)
----------------------------
The candidate: "it doesn't seem like we have clear rules when something is categorized in
a specific area. why is something on 'Your Move' when it's already setup
(meetings are an example)".

The cause was mechanical: resolved items were being REWRITTEN IN PLACE as "✅ CONFIRMED ..."
status lines instead of being deleted, and the same subject sat in two panels at once.

⭐ WHAT CHANGED IN 0.25.0 (dev #93 / public #21)
------------------------------------------------
This used to parse `focus.md`, because that file was where the ask lists were hand-written.
focus.md is retired as a source of state: the asks live in `data/asks.jsonl`, the scheduled
commitments in `data/commitments.jsonl`, and the dashboard renders those stores directly. So
the invariants are now enforced AGAINST THE STORES — the render is a pure function of them,
so a clean store IS a clean surface, and checking prose nobody hand-writes any more would
check nothing. What each historic rule became:

  1. RESOLVED ITEM IN AN ASK LIST — an OPEN ask (no `resolved_on`) whose text reads as
     settled ("✅", "CONFIRMED", "done", ...). The store's own resolution mechanism is
     `resolved_on` + `resolution`; prose-resolving a row keeps it rendering forever, which
     is the exact staleness the cutover exists to end.
  2. ONE ITEM, ONE SECTION — (a) two open asks about the same subject (the render puts each
     row in exactly one group, so the only duplicate left is two ROWS); (b) an open ask that
     duplicates a scheduled commitment — a commitment is not an ask, and This Week is its
     only home; (c) an ask duplicating a role the JSONL already routes to Your Move by
     `next_action_owner` — the derived row renders anyway, so the ask is a second copy.
  3. ASK SHAPE — an open ask must read as a question or an imperative aimed at the owner.
  4. WRONG-DOMAIN — kind=role text that is plainly a system/tooling decision, or
     kind=system text that is plainly a role decision. `kind` picks the panel, so a wrong
     kind is the modern form of "system item in Your Move".
  5. UNRESOLVED COMMITMENT DATE — a commitment whose date is the migration marker
     `unresolved` is surfaced here too; an unreadable date is an unknown, never a pass.
  6. NUMERIC CROSS-REFERENCE ROT — now checked in `handoff.md` (the surviving hand-written
     narrative): "Your Move #4" goes stale the instant a generated list reorders.

Advisory only: always exits 0, so it can never wedge an unattended run.

    python3 scripts/check_sections.py

Python 3.9+. Standard library only.
"""

import json
import os
import re
import sys

import os, sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root as _profile_root
import profile as _profile
import your_move as _ym

ROOT = _profile_root()


def _candidate_name_words():
    """This candidate's own name, lowercased — carries no identifying signal in an item's own
    text (it appears everywhere) so it belongs in STOP the same way "his"/"her"/"your" do. A
    previous version hardcoded one specific candidate's own first name here as a literal word,
    correct for exactly one installation and silently wrong for anyone else's profile."""
    try:
        ident = _profile.user()["identity"]
    except (OSError, KeyError):
        return set()
    name = ident.get("full_name") or ident.get("display_name") or ""
    return {w.lower() for w in re.findall(r"[A-Za-z']+", name)}


# Phrases that mean "this is settled" -- an open ask should not contain them.
RESOLVED_MARKERS = (
    "✅", "confirmed:", "— confirmed", "resolved:", "已", "done —", "completed",
    "sent 20", "already sent", "no longer needed", "withdrawn",
)

# A real ask reads as a question or an imperative aimed at the candidate.
ASK_SHAPES = (
    "?", "approve", "send me", "send the", "tell me", "review", "decide",
    "pursue or pass", "go or drop", "confirm whether", "sign", "upgrade",
    "needs your", "need your", "yes/no", "your call", "worth a",
)

# Words that mark an item as SYSTEM/tooling rather than a role decision.
SYSTEM_WORDS = (
    "script", "launchagent", "cron", "config", "python", "gmail forwarding",
    "dashboard", "tooling", "extension", "plist", "repo", "sudo", "proposal",
    # Data-architecture vocabulary (added 2026-07-20): a decision about the data
    # model IS a system item, but the word "roles" (job openings) was tripping the
    # role-word matcher and dragging ADR decisions toward Your Move. These are
    # unambiguously system terms.
    "schema", "data model", "jsonl", "migration", "adr", "validator",  # NOT "architecture" — collides with Enterprise Architecture job titles
)


def _title_words():
    """Words from this profile's own target titles, lowercased. A previous version hardcoded
    one candidate's target seniority ("cto", "cio") as engine constants — role vocabulary for
    exactly one installation, silently missing every other candidate's titles, so their role
    items drifted toward the wrong panel with no error. Same class as the stopword fix above:
    the value belongs to the profile, the mechanism to the engine."""
    try:
        titles = _profile.load()["targets"]["titles"]
    except (OSError, KeyError, ValueError):
        return set()
    words = set()
    for t in titles:
        for w in re.findall(r"[A-Za-z][A-Za-z']+", str(t)):
            w = w.lower()
            if w not in ("of", "and", "the", "or", "for"):
                words.add(w)
    return words


# ... but these are role/outreach words that outrank them.
ROLE_WORDS = tuple(sorted(_title_words())) + (
    "recruiter", "outreach", "draft", "intro", "referral",
    "pursue", "pass", "role", "call with", "interview",
    # Added 2026-07-21. The candidate's search vocabulary is inherently technical -- an item
    # asking what to put in a cover letter about a cloud architecture read as a "system
    # decision" purely because it contained the word "migration". Role words outrank system
    # words, so naming the artifact is enough to resolve it.
    "cover letter", "resume", "application", "apply", "employer", "jd",
    "job description", "confidential",
)

STOP = set("""the a an and or of for to in on with at by from is are was were be been
this that these those it its his her their our your my we i  — - new open still
need needs needed item items please can could should would about into over under
weekly daily review search process call meeting strategy update run runs session
2026 tuesday monday sunday""".split()) | _candidate_name_words()
# Domain-generic words are stopped above on purpose: an early version flagged
# "Weekly strategy review - Sunday" (a scheduled event) as a duplicate of
# "Weekly-review proposals need a yes/no" (an ask) purely on the shared words
# "weekly" and "review". In this repo those words carry almost no identifying
# signal, so leaving them in produces confident false positives.


def load_jsonl(name):
    path = os.path.join(ROOT, "data", name)
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass                    # validate_data.py owns malformed-line errors
    except OSError:
        pass
    return out


def read(name):
    path = os.path.join(ROOT, name)
    if not os.path.exists(path):
        return ""
    fh = open(path, "r", errors="replace")
    try:
        return fh.read()
    finally:
        fh.close()


def keywords(text):
    t = re.sub(r"\[.*?\]\(.*?\)", " ", str(text).lower())
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return set(w for w in t.split() if len(w) > 3 and w not in STOP)


def main():
    asks = load_jsonl("asks.jsonl")
    commitments = load_jsonl("commitments.jsonl")
    opps = load_jsonl("opportunities.jsonl")
    open_asks = _ym.open_asks(asks)          # membership is your_move.py's, never re-derived
    problems = []

    def _hit(words, low):
        # Word-boundary matching, NOT substring. Fixed 2026-07-20 after "script" matched
        # **Sure**script**s** and flagged a resume-content item as a tooling decision.
        return any(re.search(r"\b" + re.escape(w) + r"s?\b", low) for w in words)

    # ---- 1, 3, 4: per-ask checks -----------------------------------------
    for a in open_asks:
        aid = a.get("id", "?")
        title = str(a.get("title") or "")
        low = ("%s %s" % (title, a.get("ask") or "")).lower()

        if any(mk in title.lower() for mk in RESOLVED_MARKERS):
            problems.append((
                "RESOLVED ITEM IN AN ASK LIST", "asks[%s]" % aid, title,
                "Reads as settled but has no resolved_on. Ask rows are expelled by setting "
                "resolved_on + resolution — never by rewriting the text into a status line."))
            continue

        if not any(sh in low for sh in ASK_SHAPES):
            problems.append((
                "NOT PHRASED AS AN ASK", "asks[%s]" % aid, title,
                "Doesn't read as a question or an imperative aimed at the owner. "
                "If it's a status report, it belongs on the record, not in asks.jsonl."))

        sysh = _hit(SYSTEM_WORDS, low)
        roleh = _hit(ROLE_WORDS, low)
        if a.get("kind") == "role" and sysh and not roleh:
            problems.append((
                "SYSTEM ITEM FILED AS kind=role", "asks[%s]" % aid, title,
                "Looks like a system/tooling decision — set kind: system so it renders in "
                "the System & tooling group, not the role queue."))
        if a.get("kind") == "system" and roleh and not sysh:
            problems.append((
                "ROLE ITEM FILED AS kind=system", "asks[%s]" % aid, title,
                "Looks like a role/outreach decision — set kind: role."))

    # ---- 2: one item, one section ----------------------------------------
    # The render puts each ROW in exactly one panel, so the duplicates left are duplicates
    # in the DATA: two open asks about one subject, an ask restating a commitment, or an ask
    # restating a role the JSONL already routes to Your Move.
    # TITLE keywords, deliberately — matching the focus.md-era comparison. Folding the full
    # ask text in dilutes the overlap ratio until real duplicates stop flagging.
    ask_keys = [(a, keywords(a.get("title") or "")) for a in open_asks]
    for i in range(len(ask_keys)):
        for j in range(i + 1, len(ask_keys)):
            (ai, ki), (aj, kj) = ask_keys[i], ask_keys[j]
            if not ki or not kj:
                continue
            overlap = ki & kj
            if len(overlap) >= 2 and len(overlap) >= min(len(ki), len(kj)) * 0.6:
                problems.append((
                    "DUPLICATE ASKS", "asks[%s] vs asks[%s]" % (ai.get("id", "?"),
                                                                aj.get("id", "?")),
                    "%s  ||  %s" % (str(ai.get("title") or "")[:44],
                                    str(aj.get("title") or "")[:44]),
                    "One item, one row. Shared: " + ", ".join(sorted(overlap))))

    cm_keys = [(c, keywords("%s %s" % (c.get("title") or "", c.get("who") or "")))
               for c in commitments]
    for a, ka in ask_keys:
        for c, kc in cm_keys:
            if not ka or not kc:
                continue
            overlap = ka & kc
            if len(overlap) >= 2 and len(overlap) >= min(len(ka), len(kc)) * 0.6:
                problems.append((
                    "SCHEDULED COMMITMENT IN AN ASK LIST",
                    "asks[%s] vs commitments[%s]" % (a.get("id", "?"), c.get("id", "?")),
                    str(a.get("title") or "")[:60],
                    "A confirmed commitment's only home is This Week (commitments.jsonl). "
                    "If nothing more is needed from the owner, resolve the ask."))

    # An ask duplicating a role decision the store already derives. IMPORTANT NUANCE carried
    # over from the focus.md era: an ask that merely POINTS AT a tracked role is legitimate —
    # what is flagged is an ask on a role whose record ALREADY routes it to Your Move
    # (next_action_owner = the owner), because the derived row renders regardless and the ask
    # is then a second copy that can only ever disagree.
    try:
        owner = _profile.owner_token()
    except Exception:
        owner = None
    derived = [(o, keywords("%s %s" % (o.get("title") or "", o.get("next_action") or "")))
               for o in opps
               if owner and o.get("next_action_owner") == owner
               and o.get("status") in _ym.LIVE_OPP_STATUSES]
    for a, ka in ask_keys:
        for o, ko in derived:
            if a.get("opp_id") and a["opp_id"] == o.get("id"):
                problems.append((
                    "ASK DUPLICATES A DERIVED ROLE ROW",
                    "asks[%s] vs opportunities[%s]" % (a.get("id", "?"), o.get("id", "?")),
                    str(a.get("title") or "")[:60],
                    "This role's record already routes it to Your Move via "
                    "next_action_owner; the derived row renders by itself. Put the ask text "
                    "in the record's next_action and resolve this row."))

    # ---- 5: a commitment whose date is the migration marker ---------------
    for c in commitments:
        if str(c.get("date")) == "unresolved":
            problems.append((
                "COMMITMENT DATE UNRESOLVED", "commitments[%s]" % c.get("id", "?"),
                str(c.get("title") or "")[:60],
                "The date is the migration marker `unresolved`. Verify the real time from "
                "the invite's .ics (never recall) and set it."))

    # ---- 6: numeric cross-reference rot in the surviving narrative ---------
    XREF_RE = re.compile(r"(Your Move|Needs \w+|This Week|Active Pursuit)\s*#\d")
    for i, line in enumerate(read("handoff.md").splitlines(), 1):
        if XREF_RE.search(re.sub(r"`[^`]*`", "", line)):
            problems.append((
                "NUMERIC CROSS-REFERENCE (rots on renumber)", "handoff.md:%d" % i,
                line.strip()[:90],
                "Points at a generated list item by number; numbers shift on every "
                "regeneration. Refer to it by subject instead."))

    print("Section-rule check — data/asks.jsonl · data/commitments.jsonl · handoff.md")
    if not problems:
        print("\n  Clean. Every open ask reads as an ask, nothing resolved is lingering,")
        print("  and no item appears in two places.")
        return 0

    print("\n" + "=" * 72)
    print("%d PROBLEM(S)" % len(problems))
    print("=" * 72)
    kind_last = None
    for kind, where, title, why in problems:
        if kind != kind_last:
            print("\n-- %s --" % kind)
            kind_last = kind
        print("  [%s]" % where)
        print("      %s" % str(title)[:96])
        print("      -> %s" % why)
    return 0


if __name__ == "__main__":
    sys.exit(main())
