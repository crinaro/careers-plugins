#!/usr/bin/env python3
"""Enforce focus.md's section rules: no resolved items in ask lists, no duplicates.

WHY THIS EXISTS (2026-07-20)
----------------------------
The candidate: "it doesn't seem like we have clear rules when something is categorized in
a specific area. why is something on 'Your Move' when it's already setup
(meetings are an example)".

He was right, and the cause was mechanical: resolved items were being REWRITTEN
IN PLACE as "✅ CONFIRMED ..." status lines instead of being deleted. Your Move
turned from a queue into a status board -- it held two already-scheduled meetings
and two system items, none of which needed anything from him.

Written rules decay. This checks them:

  1. RESOLVED ITEM IN AN ASK LIST -- a Your Move / Process-Needs-the candidate entry that
     reads as settled (leading checkmark, "CONFIRMED", "DONE", "sent", ...).
     Ask lists must EXPEL resolved items, not annotate them.
  2. DUPLICATE ACROSS SECTIONS -- the same subject in two panels. One item, one
     section. A confirmed meeting belongs in This Week only.
  3. STATUS-SHAPED YOUR MOVE LINE -- an entry that isn't phrased as a question or
     an imperative aimed at the candidate.
  4. WRONG-DOMAIN ASK -- a system/tooling item sitting in Your Move (belongs in
     Process -> the Needs list) or vice versa.

Advisory only: always exits 0, so it can never wedge an unattended run.

    python3 scripts/check_sections.py

Targets system Python 3.8 (/usr/bin/python3): no third-party packages, no
zoneinfo, no walrus, no X | Y annotations.
"""

import os
import re
import sys

import os, sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root as _profile_root
import profile as _profile

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

# ⭐ NAME-FREE BY CONSTRUCTION — corrected 2026-08-09.
# These were "Process — ⚡ Needs the candidate". The owner's first name was thus part of the ENGINE's
# matching logic, so this gate only enforced anything for one person: a profile whose heading
# read "Needs the candidate" — which is what the plugin's own CLAUDE.md documents — matched
# NOTHING, and every per-item check below was skipped silently. A gate that stops enforcing
# without saying so is the failure this repo is organised against, and it was hiding inside the
# section checker itself.
#
# The distinction the code actually needs is Your-Move vs Process, and "Needs" carries that on
# its own. Whose name follows it is the profile's business.
ASK_SECTIONS = ("⚡ Your Move", "⚡ Needs")
PROCESS_MARKER = "Needs"

# Phrases that mean "this is settled" -- an ask list should not contain them.
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
    # Added 2026-07-21. Cover letters became a first-class artifact this day, and
    # The candidate's search vocabulary is inherently technical -- an item asking what to put
    # in a cover letter about a cloud architecture read as a "system decision"
    # purely because it contained the word "migration". Same collision that got
    # "architecture" removed from SYSTEM_WORDS on 2026-07-20. Role words outrank
    # system words, so naming the artifact is enough to resolve it.
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


def read(name):
    path = os.path.join(ROOT, name)
    if not os.path.exists(path):
        return ""
    fh = open(path, "r", errors="replace")
    try:
        return fh.read()
    finally:
        fh.close()


def sections(md):
    """Return list of (header, [(num, title, body), ...])."""
    out = []
    cur_h = None
    cur_items = []
    for line in md.splitlines():
        hm = re.match(r"^##\s+(.+?)\s*$", line)
        if hm:
            if cur_h is not None:
                out.append((cur_h, cur_items))
            cur_h = hm.group(1)
            cur_items = []
            continue
        im = re.match(r"^(\d+)\.\s+\*\*(.+?)\*\*\s*(.*)$", line)
        if im and cur_h is not None:
            cur_items.append((im.group(1), im.group(2), im.group(3)))
    if cur_h is not None:
        out.append((cur_h, cur_items))
    return out


def keywords(title):
    t = re.sub(r"\[.*?\]\(.*?\)", " ", title.lower())
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return set(w for w in t.split() if len(w) > 3 and w not in STOP)


def main():
    md = read("focus.md")
    secs = sections(md)
    problems = []

    # STRUCTURAL: a '## ' header glued onto the end of the previous line.
    # This bit twice on 2026-07-20 while editing sections programmatically: a
    # regex ending at a (?=^## ) lookahead drops the newline, and the next
    # header silently becomes part of the last list item. The whole following
    # section then parses as part of the previous one -- Your Move appeared to
    # have 19 items when it had 7. Cheap to detect, invisible if you don't.
    for i, line in enumerate(md.splitlines(), 1):
        # Strip inline code spans first. Prose legitimately quotes header names
        # (e.g. describing focus.md's `## Active Pursuit` section), and counting
        # those as glued headers is a false positive that punishes precise
        # writing. Added 2026-07-21, when exactly that tripped it.
        bare = re.sub(r"`[^`]*`", "", line)
        if "## " in bare and not bare.lstrip().startswith("#"):
            problems.append((
                "HEADER GLUED TO PREVIOUS LINE", "focus.md", str(i),
                line.strip()[:90],
                "A '## ' header is not at the start of its line, so every section "
                "below it parses into the one above. Insert a blank line before it."))

    # NUMERIC CROSS-REFERENCE ROT: prose that points at an item by its number
    # ("Your Move #4", "Needs <owner> #1") goes stale the instant the list renumbers
    # -- which happens constantly. Reference items by SUBJECT, not number. Added
    # 2026-07-29 after stale "#N" refs piled up in the Session Handoff (and role
    # decisions moved to a generated JSONL view, so their numbers aren't even
    # authored anymore). Skip the header's own rules-prose lines.
    XREF_RE = re.compile(r"(Your Move|Needs \w+|This Week|Active Pursuit|Process\s*→\s*Open)\s*#\d")
    for i, line in enumerate(md.splitlines(), 1):
        if XREF_RE.search(re.sub(r"`[^`]*`", "", line)):
            problems.append((
                "NUMERIC CROSS-REFERENCE (rots on renumber)", "focus.md", str(i),
                line.strip()[:90],
                "Points at an item by number; numbers shift every time the list changes. "
                "Refer to it by subject instead (e.g. 'the <a contact> intro', not 'Your Move #4')."))

    # ---- 1 & 3 & 4: per-item checks inside ask sections -------------------
    for header, items in secs:
        if not any(a in header for a in ASK_SECTIONS):
            continue
        is_process = PROCESS_MARKER in header
        for num, title, body in items:
            low = (title + " " + body).lower()
            tlow = title.lower()

            if any(mk in tlow for mk in RESOLVED_MARKERS):
                problems.append((
                    "RESOLVED ITEM IN AN ASK LIST", header, num, title,
                    "Reads as settled. Ask lists expel resolved items -- delete it, "
                    "log the outcome, and if it became a commitment put it in This Week."))
                continue

            if not any(sh in low for sh in ASK_SHAPES):
                problems.append((
                    "NOT PHRASED AS AN ASK", header, num, title,
                    "Doesn't read as a question or an imperative aimed at the owner. "
                    "If it's a status report, it belongs in a state section."))

            # Word-boundary matching, NOT substring. Fixed 2026-07-20 after
            # "script" matched **Sure**script**s** and flagged a resume-content
            # item as a tooling decision. Substring matching on short domain
            # words produces confident nonsense: "script" also hits
            # "transcript"/"description", "repo" hits "report"/"reporting".
            def _hit(words):
                return any(re.search(r"\b" + re.escape(w) + r"s?\b", low) for w in words)
            sysh = _hit(SYSTEM_WORDS)
            roleh = _hit(ROLE_WORDS)
            if not is_process and sysh and not roleh:
                problems.append((
                    "SYSTEM ITEM IN YOUR MOVE", header, num, title,
                    "Looks like a system/tooling decision -- move to the Process ask list."))
            if is_process and roleh and not sysh:
                problems.append((
                    "ROLE ITEM IN PROCESS", header, num, title,
                    "Looks like a role/outreach decision -- move to Your Move."))

    # ---- 2: duplicates across sections -----------------------------------
    # IMPORTANT NUANCE (refined 2026-07-20 after the first version over-flagged):
    # an ask in Your Move that POINTS AT a role tracked in Active Pursuit / Needs
    # Resolution is NOT a duplicate -- Your Move is an INDEX of what needs the candidate,
    # and the role's detail rightly lives in its own section. That pairing is
    # intended and must not be "fixed" by deleting one side.
    #
    # What IS a genuine duplicate, and what the candidate actually hit:
    #   - the same ask sitting in BOTH ask lists (Your Move + the Process Needs list)
    #   - a scheduled commitment appearing in an ask list (This Week is its only home)
    # So compare ask-vs-ask, and This-Week-vs-ask. Nothing else.
    def _kind(h):
        if any(a in h for a in ASK_SECTIONS):
            return "ask"
        if "This Week" in h:
            return "week"
        return "state"

    seen = []
    for header, items in secs:
        if "Passed" in header or "archive" in header.lower():
            continue
        if _kind(header) == "state":
            continue
        for num, title, _ in items:
            seen.append((header, num, title, keywords(title)))
    for i in range(len(seen)):
        for j in range(i + 1, len(seen)):
            hi, ni, ti, ki = seen[i]
            hj, nj, tj, kj = seen[j]
            if hi == hj or not ki or not kj:
                continue
            ka, kb = _kind(hi), _kind(hj)
            # ask-vs-ask, or week-vs-ask. Never state-vs-anything.
            if not ((ka == "ask" and kb == "ask") or set([ka, kb]) == set(["week", "ask"])):
                continue
            overlap = ki & kj
            if len(overlap) >= 2 and len(overlap) >= min(len(ki), len(kj)) * 0.6:
                problems.append((
                    "DUPLICATE ACROSS SECTIONS", "%s  vs  %s" % (hi, hj),
                    "%s/%s" % (ni, nj), "%s  ||  %s" % (ti[:48], tj[:48]),
                    "One item, one section. Shared: " + ", ".join(sorted(overlap))))

    print("Section-rule check - focus.md")
    if not problems:
        print("\n  Clean. Every ask reads as an ask, nothing resolved is lingering,")
        print("  and no item appears in two sections.")
        return 0

    print("\n" + "=" * 72)
    print("%d PROBLEM(S)" % len(problems))
    print("=" * 72)
    kind_last = None
    for kind, header, num, title, why in problems:
        if kind != kind_last:
            print("\n-- %s --" % kind)
            kind_last = kind
        print("  [%s] item %s" % (header, num))
        print("      %s" % title[:96])
        print("      -> %s" % why)
    return 0


if __name__ == "__main__":
    sys.exit(main())
