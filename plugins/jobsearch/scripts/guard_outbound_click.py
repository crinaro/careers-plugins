#!/usr/bin/env python3
"""PreToolUse guard: DENY a ref-based click that resolves to an outbound-terminal control.

⭐ WHY THIS EXISTS (dev #78 / public #9)
-----------------------------------------
A click that sends a message, invitation or application under the candidate's identity is
the LEAST REVERSIBLE act in this system. `guard_engine_writes.py` gives an engine-file write
a mechanical guard on top of the behavioural rule; a push gets one too (adr-005). An outbound
click had neither — the prohibition on clicking Send/Connect/Apply lived only as prose inside
a browser-driving agent's own prompt (`linkedin-runner.md`), which is exactly the shape of
control that does not survive a session that is mid-task and confident. This is that guard.

⚠️ THIS IS A GUARD, NOT A SANDBOX — same posture as `guard_engine_writes.py`, not relitigated
here. It matches the two browser-control channels `linkedin-runner` actually routes to. A
determined session with a coordinate click, a JS executor, or a channel this plugin does not
route to can still send something. **The behavioural rule remains the first line**; this stops
the reflexive click, which is the one that actually happens. See "WHAT THIS DOES NOT COVER"
below — read it before trusting this guard further than it goes.

## The test, stated once

DENY when a ref-based click resolves — via the transcript's most recent page-read output, in
THIS agent's own chain — to an element whose accessible name matches an outbound-terminal verb
(send, connect, invite, apply, submit application, InMail, post, share). Third-party-directed
verbs only: "Save" on the candidate's own profile is deliberately OUTSIDE the pattern, because
the approved-edit path (profile-optimizer drafts, the candidate applies it himself) is a
different and recoverable act.

    click has no `ref` (coordinate click)                       -> ALLOW (outside v1 — see below)
    ref does not resolve in this chain's most recent page read   -> ALLOW, loud (systemMessage)
    resolved accessible name matches an outbound-terminal verb   -> DENY
    anything else                                                -> ALLOW

## The matcher — GUARDED_CLICK_TOOLS is the single source of truth

`hooks.json`'s PreToolUse matcher for this script must equal `"|".join(GUARDED_CLICK_TOOLS)`
exactly. `check_click_guard_matcher.py` asserts that, both as a normal CI gate and inside
`check_shipped_package.py`'s materialized package (drift control, not relitigated here).

⭐ `mcp__claude-in-chrome__browser_batch` runs a SEQUENCE of `computer` actions inside one call
(`tool_input["actions"] = [{"name": "computer", "input": {...}}, ...]`). A guard that only
inspects the top-level `tool_input` lets a Send click through inside a batch — this script
iterates `tool_input["actions"]` and classifies every `computer` item in it. This is the single
most important behavioural case in this guard; `TestBrowserBatchIteration` in test_checks.py
exists because reverting the iteration is exactly the kind of change that looks like a harmless
simplification.

Deliberately NOT in the matcher, each for a stated reason: `form_input` (sets values, does not
click — an accepted v1 gap, see below); a JS executor on any server (a programmatic click
carries no ref and no accessible name — nothing to classify); a browser controller the user
installs outside this plugin (a shipped matcher cannot enumerate it; this guard covers the two
channels `linkedin-runner` routes to).

## Ref resolution, and the concurrent-agent hazard

Claude Code's transcript (`transcript_path` in the hook payload — JSONL, one record per line,
`message.content[].type == "tool_use"` / `"tool_result"`, each record carrying `uuid` and
`parentUuid`) does not tell this script which tool_use IS the click being classified. This
script anchors on the LAST record whose message contains a `tool_use` block named in
`GUARDED_CLICK_TOOLS` — true whenever the hook fires immediately after the model's own turn was
appended, which is the overwhelmingly common case, but not a guarantee against a concurrent
subagent finishing and writing a later record into the SAME transcript file in the interval
between. There is no chain identifier in the hook payload that would close that gap completely;
noted here rather than hidden.

From that anchor, resolution walks `parentUuid` BACKWARD — never scans the file by proximity —
so a parallel subagent's interleaved writes (a different, unrelated `parentUuid` lineage) are
never picked up as "the most recent read." Where the click carries a `tabId`, only a page read
with a matching `tabId` counts; where it carries none (the in-app pane has one tab), no tab
filter applies. **Unresolvable is unresolvable — never a cross-page guess.** An unresolved ref
allows the click and attaches a `systemMessage` saying so; it never denies on a guess and never
silently allows either.

## WHAT THIS DOES NOT COVER — state it, don't let a reader assume more than what's here

- **A coordinate click carries no ref.** Nothing to resolve, nothing to classify. Allowed,
  unconditionally. `browser_batch`'s own tool description warns its coordinates refer to a
  PRE-BATCH screenshot, which makes batch clicks disproportionately coordinate-based — i.e.
  disproportionately in this hole.
- **Enter-to-send in a compose box.** A `key` event carries no ref and no focus context in v1.
- **`form_input`.** Sets a field value; never clicks. Out of the matcher by design (above).
- **A JS executor on any server.** A programmatic click carries no ref — nothing to classify.
- **S6 (the scheduled/unattended cloud worker).** Whether hooks execute there at all is
  UNVERIFIED, and a stale Home-shadow install can silently run an older version of this file
  that predates the guard. **Assume UNGUARDED on S6.** The prose prohibition in
  `linkedin-runner.md` is the only control there — do not delete it because this script exists.

## `--selftest`, run from `SessionStart`

Without this the guard is unsound EVERYWHERE, because it cannot tell its own presence from its
absence: a broken parser and an absent transcript look identical from the outside (zero output
either way), and this is the plugin's FIRST transcript consumer, so nothing else would notice a
format shift. `--selftest` (a) classifies a click against an embedded, synthesized fixture
transcript, proving the parser and the verb pattern against a known shape, and (b) opens the
LIVE `transcript_path` from the SessionStart payload and asserts its envelope parses. On
failure it prints a `systemMessage`: the guard is inert on this surface, browser sends are
unguarded. It never blocks startup — same fail-open posture as the click path.

Deliberately NOT in `whoami.py`'s capability set: `whoami` declares capability for CLAIMING
work; this is a safety net, not a capability, and a probe result must never gate whether the
hook runs.

Protocol: PreToolUse stdin is the hook payload; exit 2 blocks and stderr is shown to the model
(same as `guard_engine_writes.py`). `--selftest` never blocks; it prints and exits 0 always.
⭐⭐ FAILS OPEN, ALWAYS on internal error — a guard that breaks all browsing is worse than the
failure it prevents.

Python 3.9+, stdlib only.
"""

import argparse
import json
import os
import re
import sys

# ⭐ THE SINGLE SOURCE OF TRUTH — hooks.json's matcher must equal "|".join(this).
# check_click_guard_matcher.py asserts the two agree, both in this repo and inside the
# materialized shipped package (check_shipped_package.py).
GUARDED_CLICK_TOOLS = (
    "mcp__Claude_Browser__computer",
    "mcp__claude-in-chrome__computer",
    "mcp__claude-in-chrome__browser_batch",
)

# The batch tool's own action name for a click/keyboard step, and the read tools whose output
# a ref resolves against.
BATCH_ACTION_NAME = "computer"
READ_TOOLS = (
    "mcp__Claude_Browser__read_page",
    "mcp__claude-in-chrome__read_page",
    "mcp__Claude_Browser__find",
    "mcp__claude-in-chrome__find",
)

CLICK_ACTIONS = ("left_click", "right_click", "double_click", "triple_click")

# Third-party-directed verbs only. "Save" (the candidate's own profile) is deliberately absent —
# see the module docstring.
OUTBOUND_TERMINAL_VERBS = ("send", "connect", "invite", "apply", "share", "post", "inmail")
OUTBOUND_TERMINAL_PHRASES = ("submit application",)

_VERB_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(v) for v in OUTBOUND_TERMINAL_VERBS) + r")\w*\b",
    re.IGNORECASE)
_PHRASE_RE = re.compile(
    "|".join(re.escape(p) for p in OUTBOUND_TERMINAL_PHRASES), re.IGNORECASE)
_QUOTED = re.compile(r'"([^"]*)"')


def is_outbound_terminal(accessible_name):
    """True if `accessible_name` names a third-party-directed, outbound-terminal action.

    Deliberately errs toward FALSE POSITIVES (over-blocking) rather than false negatives: a
    wrongly-blocked click is a nuisance the agent notices immediately (it gets a reason back
    and can proceed a different way); a wrongly-allowed Send is not noticed at all."""
    if not accessible_name:
        return False
    name = accessible_name.strip()
    if not name:
        return False
    if _PHRASE_RE.search(name):
        return True
    return bool(_VERB_RE.search(name))


def extract_label_for_ref(page_text, ref):
    """Best-effort accessible-name extraction for `ref` (e.g. "ref_12") out of a page-read's
    text output. Targets the documented shape — a role, a quoted accessible name, then
    `[ref_N]` on one line, e.g.:  button "Send" [ref_12]  — and degrades gracefully (a role
    word and the ref token stripped) when there is no quoted string. Returns None, never a
    guess, when `ref` does not appear at all."""
    if not page_text or not ref:
        return None
    token = "[%s]" % ref
    ref_word = re.compile(r"\b%s\b" % re.escape(ref))
    for line in page_text.splitlines():
        if token not in line and not ref_word.search(line):
            continue
        m = _QUOTED.search(line)
        if m:
            return m.group(1).strip() or None
        cleaned = line.replace(token, "")
        cleaned = ref_word.sub("", cleaned)
        cleaned = re.sub(r"^[\s*\-:]+", "", cleaned)
        cleaned = re.sub(r"^[A-Za-z][\w-]*\s*:?\s*", "", cleaned, count=1)
        cleaned = cleaned.strip()
        return cleaned or None
    return None


# --------------------------------------------------------------------------------------
# Transcript parsing — JSONL, one record per line, `uuid` / `parentUuid` link each record
# to the one that produced it. See the module docstring for the concurrent-agent hazard
# this is deliberately built to bound rather than paper over.
# --------------------------------------------------------------------------------------

def load_transcript(path):
    """Parsed JSONL records, or None if the file cannot be read at all. A line that fails to
    parse is skipped, not fatal — one bad line must not blind the guard to every other."""
    if not path:
        return None
    try:
        records = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except Exception:
                    continue
        return records
    except Exception:
        return None


def _content_blocks(record):
    msg = record.get("message") if isinstance(record, dict) else None
    content = (msg or {}).get("content")
    return content if isinstance(content, list) else []


def index_by_uuid(records):
    by_uuid = {}
    for r in records or ():
        u = r.get("uuid") if isinstance(r, dict) else None
        if u:
            by_uuid[u] = r
    return by_uuid


def find_anchor(records):
    """The most recent record whose message contains a tool_use named in GUARDED_CLICK_TOOLS —
    presumed to be the click this hook is firing for. See the module docstring: sound whenever
    the hook fires immediately after that record was appended, which is the common case and not
    a guarantee."""
    for r in reversed(records or ()):
        for block in _content_blocks(r):
            if isinstance(block, dict) and block.get("type") == "tool_use" \
                    and block.get("name") in GUARDED_CLICK_TOOLS:
                return r
    return None


def _tool_results_by_use_id(records):
    out = {}
    for r in records or ():
        for block in _content_blocks(r):
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tid = block.get("tool_use_id")
                if tid:
                    out[tid] = block
    return out


def _result_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [c.get("text") or "" for c in content
                 if isinstance(c, dict) and c.get("type") == "text"]
        return "\n".join(parts)
    return ""


def find_recent_read(records, by_uuid, anchor, want_tab_id):
    """The text of the most recent READ_TOOLS result found by walking `parentUuid` BACKWARD
    from `anchor` — never by scanning the file for proximity, which is exactly the cross-page
    guess the design forbids. Returns None when nothing qualifies in this chain."""
    if anchor is None:
        return None
    results = _tool_results_by_use_id(records)
    cur = anchor
    seen = set()
    steps = 0
    while cur is not None and steps < 2000:
        steps += 1
        u = cur.get("uuid")
        if u is not None:
            if u in seen:
                break
            seen.add(u)
        for block in _content_blocks(cur):
            if isinstance(block, dict) and block.get("type") == "tool_use" \
                    and block.get("name") in READ_TOOLS:
                res = results.get(block.get("id"))
                if res is None:
                    continue
                cand_tab = (block.get("input") or {}).get("tabId")
                if want_tab_id and cand_tab and cand_tab != want_tab_id:
                    continue
                text = _result_text(res.get("content"))
                if text:
                    return text
        parent = cur.get("parentUuid")
        cur = by_uuid.get(parent) if parent else None
    return None


# --------------------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------------------

def iter_click_inputs(tool_name, tool_input):
    """Yield one `computer`-shaped input dict per click to classify. `browser_batch` fans out
    into every `computer` action inside `tool_input["actions"]` — the case this guard exists
    to close; everything else is a single input."""
    if tool_name == "mcp__claude-in-chrome__browser_batch":
        for action in (tool_input or {}).get("actions") or []:
            if isinstance(action, dict) and action.get("name") == BATCH_ACTION_NAME:
                yield action.get("input") or {}
        return
    yield tool_input or {}


def classify_one(action_input, page_text):
    """Returns (verdict, reason). verdict is "DENY", "ALLOW", or None (unresolved -> caller
    allows and reports it)."""
    act = (action_input or {}).get("action")
    if act not in CLICK_ACTIONS:
        return "ALLOW", "not a click action (%r)" % (act,)
    ref = action_input.get("ref")
    if not ref:
        return "ALLOW", "coordinate click, no ref — outside v1 classification"
    if page_text is None:
        return None, "no resolvable page-read in this agent's chain for ref %s" % ref
    label = extract_label_for_ref(page_text, ref)
    if label is None:
        return None, "ref %s not found in the resolved page read" % ref
    if is_outbound_terminal(label):
        return "DENY", "ref %s resolved to %r — matches an outbound-terminal verb" % (ref, label)
    return "ALLOW", "ref %s resolved to %r — not outbound-terminal" % (ref, label)


def evaluate(payload):
    """The whole classify-a-hook-call pipeline. Returns (deny, deny_reasons, notes)."""
    tool_name = payload.get("tool_name") or ""
    if tool_name not in GUARDED_CLICK_TOOLS:
        return False, [], []

    tool_input = payload.get("tool_input") or {}
    inputs = list(iter_click_inputs(tool_name, tool_input))
    if not inputs:
        return False, [], []

    records = load_transcript(payload.get("transcript_path"))
    by_uuid = index_by_uuid(records) if records else {}
    anchor = find_anchor(records) if records else None

    deny_reasons, notes = [], []
    for inp in inputs:
        want_tab = inp.get("tabId")
        page_text = find_recent_read(records, by_uuid, anchor, want_tab) if records else None
        verdict, reason = classify_one(inp, page_text)
        if verdict == "DENY":
            deny_reasons.append(reason)
        elif verdict is None:
            notes.append(reason)
    return bool(deny_reasons), deny_reasons, notes


def main_hook():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # unreadable payload is not evidence of a violation — fails open

    try:
        denied, deny_reasons, notes = evaluate(payload)

        if denied:
            sys.stderr.write(
                "⛔ BLOCKED: this click resolves to an outbound-terminal control — a message,\n"
                "invitation, application, InMail, post or share directed at a third party.\n\n"
                "   %s\n\n"
                "   This guard only classifies; it does not decide for you. The behavioural rule\n"
                "   is the first line: work you are not meant to send stays a draft. If sending\n"
                "   really is the approved next step, that happens through the candidate's own\n"
                "   review, not a reflexive click here.\n"
                % "\n   ".join(deny_reasons))
            return 2

        if notes:
            sys.stdout.write(json.dumps({
                "systemMessage": "guard_outbound_click could not classify %d click(s), so "
                                  "they were allowed: %s" % (len(notes), "; ".join(notes)),
            }) + "\n")
        return 0
    except Exception:
        return 0  # fail open, deliberately — see module docstring


# --------------------------------------------------------------------------------------
# --selftest
# --------------------------------------------------------------------------------------

def _fixture_records():
    """A synthesized, self-contained transcript proving the parser and verb pattern against a
    KNOWN shape. Never real page content — see CLAUDE.md's synthesis rule."""
    return [
        {"uuid": "f-1", "parentUuid": None,
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "t-read", "name": "mcp__Claude_Browser__read_page",
              "input": {}}]}},
        {"uuid": "f-2", "parentUuid": "f-1",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "t-read", "content": [
                 {"type": "text", "text":
                  '- generic [ref_1]\n'
                  '  - button "Send" [ref_12]\n'
                  '  - button "Search" [ref_7]\n'}]}]}},
        {"uuid": "f-3", "parentUuid": "f-2",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "id": "t-click-send", "name": "mcp__Claude_Browser__computer",
              "input": {"action": "left_click", "ref": "ref_12"}}]}},
    ]


def _selftest_fixture():
    """(ok, detail) — proves classify_one + the parser against the embedded fixture: a "Send"
    ref denies, a "Search" ref allows, a coordinate click allows."""
    records = _fixture_records()
    by_uuid = index_by_uuid(records)
    anchor = find_anchor(records)
    if anchor is None or anchor.get("uuid") != "f-3":
        return False, "fixture anchor resolution failed — got %r" % (
            anchor.get("uuid") if anchor else None)

    page_text = find_recent_read(records, by_uuid, anchor, None)
    if not page_text:
        return False, "fixture page-read resolution failed"

    v_send, _ = classify_one({"action": "left_click", "ref": "ref_12"}, page_text)
    if v_send != "DENY":
        return False, "fixture 'Send' ref classified %r, expected DENY" % v_send

    v_search, _ = classify_one({"action": "left_click", "ref": "ref_7"}, page_text)
    if v_search != "ALLOW":
        return False, "fixture 'Search' ref classified %r, expected ALLOW" % v_search

    v_coord, _ = classify_one({"action": "left_click", "coordinate": [10, 10]}, page_text)
    if v_coord != "ALLOW":
        return False, "fixture coordinate click classified %r, expected ALLOW" % v_coord

    return True, "parser + verb pattern OK (Send->DENY, Search->ALLOW, coordinate->ALLOW)"


def _selftest_live(payload):
    """(ok, detail) — opens the LIVE transcript_path from the SessionStart payload and asserts
    its envelope parses at all. Does not require any particular content — an empty or
    freshly-started transcript is fine; an unreadable one is not.

    ⚠️ Deliberately does NOT reuse `load_transcript()`, which silently skips a line that fails
    to parse (correct for classification — one bad line must not blind a click's resolution).
    That leniency would make this check indistinguishable from the exact failure it exists to
    catch: a transcript whose envelope shifted so every line fails to parse looks identical,
    through `load_transcript()`, to a brand-new empty session — zero records either way. So
    this reads the file directly and tells "no lines yet" (fine) apart from "lines present, none
    match the expected {uuid, message} envelope" (a format shift, and loud)."""
    path = payload.get("transcript_path")
    if not path:
        return False, "SessionStart payload carried no transcript_path — cannot verify the live envelope"
    try:
        with open(path, encoding="utf-8") as fh:
            lines = [l.strip() for l in fh if l.strip()]
    except Exception as e:
        return False, "transcript_path %r could not be opened — %s" % (path, e)
    if not lines:
        return True, "live transcript at %s is empty (fresh session) — nothing to disagree with yet" % path
    matching = 0
    for line in lines:
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if isinstance(rec, dict) and ("uuid" in rec or "message" in rec):
            matching += 1
    if matching == 0:
        return False, ("transcript_path %r has %d line(s) but NONE parsed as the expected "
                        "{uuid, message} envelope — format shift?" % (path, len(lines)))
    return True, ("live transcript at %s parses as JSONL (%d of %d line(s) match the expected "
                  "envelope)" % (path, matching, len(lines)))


def main_selftest():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

    ok_fixture, detail_fixture = _selftest_fixture()
    ok_live, detail_live = _selftest_live(payload)

    print("guard_outbound_click --selftest")
    print("  fixture parser/verb-pattern check: %s — %s" % ("OK" if ok_fixture else "FAIL",
                                                             detail_fixture))
    print("  live transcript envelope check:    %s — %s" % ("OK" if ok_live else "FAIL",
                                                             detail_live))

    if not (ok_fixture and ok_live):
        sys.stdout.write(json.dumps({
            "systemMessage": "guard_outbound_click --selftest FAILED (%s%s%s) — the outbound-"
                              "click guard is INERT on this surface. Browser sends are "
                              "UNGUARDED; the behavioural prohibition in linkedin-runner.md is "
                              "the only control until this is fixed."
                              % (("fixture: %s" % detail_fixture if not ok_fixture else ""),
                                 (" / " if not ok_fixture and not ok_live else ""),
                                 ("live: %s" % detail_live if not ok_live else "")),
        }) + "\n")
    return 0  # never blocks startup — same posture as the click path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true",
                    help="run from SessionStart; proves the transcript parser is sound here")
    args = ap.parse_args()
    if args.selftest:
        return main_selftest()
    return main_hook()


if __name__ == "__main__":
    sys.exit(main())
