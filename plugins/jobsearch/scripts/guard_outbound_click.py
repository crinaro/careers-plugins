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
the approved-edit path (profile-optimizer drafts, the candidate applies it directly) is a
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

## dev #102 — two distinct ways the transcript lookup can fail, and a known-inert surface must
## stay loud, not just announce itself once

Reported from a real machine, twice, on different days: the selftest could not open the
transcript it derived, and — because the LIVE click path (`evaluate()`) reads
`payload.get("transcript_path")` the same way — every click on that surface was unresolvable
and therefore allowed. Two different bugs hide behind that one report:

1. **The payload can carry no `transcript_path` at all.** `resolve_transcript_path()` now falls
   back to `derive_fallback_transcript_path()` — the observed on-disk layout,
   `~/.claude/projects/<cwd with "/" -> "-">/<session_id>.jsonl` — when the key is absent or
   empty. ⚠️ This is a **guess from an undocumented convention**, not the contract: Claude Code
   promises nothing about it, so `diagnose_transcript()` still has to prove the derived path
   actually opens and parses before anything relies on it, exactly as it would the payload's own
   value. A path the payload DOES supply is never second-guessed through the fallback — a
   present-but-broken value is itself informative (see next point) and guessing over it would
   destroy that information.
2. **A path is present but cannot be opened or does not parse.** `diagnose_transcript()` (shared
   by `--selftest` and the live click path, so the two can never disagree about what "inert"
   means) captures the real exception type and message rather than a bare "failed", and
   separately distinguishes a legitimately empty fresh transcript (fine) from one whose lines
   are present but NONE match the expected envelope (a format shift — not fine).

**The design gap this closes:** the fail-open policy for one unresolvable click is unchanged —
an unclassifiable click still allows and reports. But before this fix, "the guard cannot resolve
anything on this surface" degraded to a single `systemMessage` at `SessionStart` and then
SILENCE: every later click that could not classify only got the same generic per-click note
("no resolvable page-read... for ref X"), indistinguishable from an ordinary one-off ambiguity.
`evaluate()` now re-derives `diagnose_transcript()`'s verdict on every single invocation (no
persisted state to go stale) and, when it says the surface is unusable, every click's note
carries the loud, distinct "GUARD INERT on this surface" wording instead of the generic one —
on EVERY click, not once at startup. Whether a known-inert guard should refuse rather than warn
is a fail-open POLICY decision, left untouched here — see the hand-back for dev #102.

## dev #111 — the guard's status is QUERYABLE, never only loud in the moment

dev #102 made an inert guard loud on every click — and still only inside the session where it
happened. Nothing could answer "has this install had an inert guard for two days?"; the one
reporter who knew (public #12) knew because they personally noticed across two sessions. That is
this marketplace's recurring defect — a fact a run already knows written into a scrolling
advisory instead of the queryable store — and the fix copies the established shape (`act_by`,
`precondition.py`, `blocked_until`): a named field, a strict reader that refuses what it cannot
parse, and a resolver against data that already exists.

- **Every selftest, and the first click-path diagnosis per session (or any state change),
  records a coded `guard_status` event** via `_diag.py` — the diagnostics log that carries no
  user data by construction, so the record can be pasted into an issue as-is. Fields are CODES
  (`state`: ok|inert, `source`: selftest|click, `reason`: no-transcript-path | open-failed |
  format-shift | fixture-fail | ok), never the prose detail — the prose stays in the session's
  own systemMessage where it already is. Recording is best-effort and can never change a
  verdict or block a click.
- **`guard_status()` answers the operator's question from that record**: ACTIVE / INERT (since
  when, across how many sessions) / UNKNOWN (no observation yet) / BROKEN (not registered in
  hooks.json, or the parser fixture fails — inert EVERYWHERE, not just one surface). `doctor`
  and `whoami` carry this line, as `docs/deployment.md` specified in the #78 audit;
  `--status` prints it standalone.
- ⚠️ **A status line that says "live" against an inert guard is worse than no line** — so the
  verdict is derived only from recorded observations and the executable fixture, never from
  the guard file merely existing. `TestOutboundClickGuardStatus` proves the INERT verdict
  against a genuinely unopenable transcript (the exact Errno-2 reproduction from a real
  SessionStart), and was watched fail against an induced always-ACTIVE bug before shipping.
- ⚠️ **Whether a known-inert guard should refuse rather than warn is still the owner's open
  fail-open policy decision** — dev #111 says so explicitly. Nothing here changes the guard's
  verdict on any click; this only makes the inertness durable and queryable.

## dev #137 — `claude -p --chrome`'s selftest was never actually inert; SessionStart just asked
## the question before it could possibly have an answer

Filed as "the one S3 mode with click tools is the one where the guard is inert" — the selftest's
FAIL was real, but the conclusion drawn from it was not established. Probed directly (CLI
2.1.231, three independent `claude -p --chrome` sessions, 2026-08-18) to settle it:

- **The transcript is neither absent nor at a different path.** The payload's own
  `transcript_path` is exactly `~/.claude/projects/<cwd-slug>/<session_id>.jsonl` — the same
  convention `derive_fallback_transcript_path()` already encodes — and after the session ends
  that exact file exists and parses cleanly. **Failure mode 2 fired** (dev #102's "present but
  cannot be opened" — `FileNotFoundError`), never mode 1 (no `transcript_path` in the payload).
- **It is not a rare race — it is deterministic, and unfixable by waiting inside the hook.** A
  SessionStart hook that polls its own `transcript_path` for up to 8.5s (nearly the whole 10s
  hook timeout) never observes the file. A lightweight hook that returns immediately does see it
  — but only ~3s later, from OUTSIDE the hook, once the model's first turn begins. This proves
  transcript creation is gated on EVERY SessionStart hook returning, not on elapsed time: no
  retry loop inside `--selftest` can ever close this gap, on this surface, by construction.
- **The real click-time guard is unaffected.** `evaluate()` re-derives `diagnose_transcript()`
  fresh on every invocation (dev #102), and a click's `PreToolUse` fires from the same hook
  plumbing as any other tool's. Proxied with `Bash` (no click tool was ever given to a probe
  child — see the hand-back for the discipline this followed): the transcript already has
  content at the FIRST `PreToolUse` of the session, well before a click could plausibly be the
  session's very first tool call. **The guard was never actually inert when it mattered.**

The defect worth fixing, then, was not the guard's classification — it was dev #111's own
durable record believing the SessionStart false negative. Recording "inert" from a transcript
that merely doesn't exist YET would durably and systematically mislabel every `-p` session, which
is the false-positive twin of the missing-observation problem dev #111 was built to solve: **a
wrong fact in the queryable store is worse than no fact**, because it reads as researched truth.

`transcript_pending_creation()` recognizes this ONE narrow condition — `FileNotFoundError` on a
path that independently matches this same payload's own `derive_fallback_transcript_path()`
result, i.e. Claude Code's own addressing convention, never a guess — and both `--selftest` and
the click path skip the durable "inert" record for it, staying loud in-session with honest
wording instead. Every existing dev #102 / dev #111 regression fixture uses a payload without a
matching `session_id` + `cwd`, so none of them can satisfy this condition; a genuinely wrong or
broken transcript path — the shape those fixtures exist to prove — still records "inert"
immediately, unchanged. Fail-open posture: **completely untouched** — every affected click was
already being ALLOWed before this fix and still is; only the recording and the wording changed.

Deliberately NOT in `whoami.py`'s capability set: `whoami` declares capability for CLAIMING
work; this is a safety net, not a capability, and a probe result must never gate whether the
hook runs. `whoami` prints the status line OUTSIDE its capabilities block, and `--can` does
not accept it.

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


def derive_fallback_transcript_path(payload):
    """⚠️ FALLBACK ONLY — NOT the documented contract (dev #102). Claude Code does not promise
    this layout; it is this guard's own observation of the on-disk shape —
    ~/.claude/projects/<cwd with every "/" replaced by "-">/<session_id>.jsonl — used ONLY when
    the payload itself carries no `transcript_path` at all. Returns None when the payload lacks
    what the derivation needs (`session_id`, `cwd`), rather than guessing further. The caller
    (`diagnose_transcript`) still has to prove whatever this returns actually opens and parses —
    this function only proposes a path, it never vouches for it."""
    session_id = payload.get("session_id")
    cwd = payload.get("cwd")
    if not session_id or not cwd:
        return None
    slug = cwd.replace("\\", "-").replace("/", "-")
    if not slug:
        return None
    return os.path.join(os.path.expanduser("~"), ".claude", "projects", slug,
                        "%s.jsonl" % session_id)


def resolve_transcript_path(payload):
    """(path_or_None, used_fallback). Prefers the payload's own `transcript_path` whenever it is
    present and non-empty; falls back to `derive_fallback_transcript_path()` only when that key
    is absent or empty. A path the payload DOES supply is never second-guessed through the
    fallback, even if it turns out to be unopenable — see the dev #102 docstring section above
    for why a present-but-broken value must stay informative rather than be papered over."""
    path = payload.get("transcript_path")
    if path:
        return path, False
    return derive_fallback_transcript_path(payload), True


def transcript_pending_creation(payload, path, usable, detail):
    """dev #137 — True when `path` is unopenable specifically because Claude Code has not
    CREATED it yet, not because it is wrong or broken.

    Probed directly (CLI 2.1.231, three independent `claude -p --chrome` sessions, 2026-08-18):
    under `-p`, the SessionStart selftest's `FileNotFoundError` is not a rare race — it is
    deterministic. A SessionStart hook that polls its OWN payload's `transcript_path` for up to
    8.5s (nearly the hook's full 10s budget) never observes the file; a lightweight hook that
    returns immediately does, ~3s later, once the model's first turn begins — proving transcript
    creation is gated on EVERY SessionStart hook returning, not on elapsed time, so no amount of
    in-hook retrying can ever close this gap. Generalized via a Bash `PreToolUse` proxy (the same
    hook-timing plumbing `GUARDED_CLICK_TOOLS` uses): the transcript already has content — before
    any Bash tool result even exists — at the FIRST `PreToolUse` of any kind in the session. So
    the resolver is not wrong and the transcript is not absent; the SessionStart check simply
    runs before the file can possibly exist on this surface, and is reliably present by the time
    a real click could fire.

    This is corroboration, not a guess: it fires ONLY when `path` is exactly what
    `derive_fallback_transcript_path()` would independently compute from THIS SAME payload's own
    `session_id` + `cwd` — i.e. Claude Code's own (unofficially observed) addressing convention,
    not a path this guard invented. A payload that does not carry a matching session_id/cwd (every
    existing dev #102 / dev #111 regression fixture, none of which sets `cwd`) cannot satisfy
    this, so a genuinely wrong or broken path still reads as a real open-failed observation,
    unchanged. `usable=True` short-circuits to False — there is nothing pending about a transcript
    that already opened and parsed."""
    if usable or not path:
        return False
    if "FileNotFoundError" not in (detail or ""):
        return False
    return derive_fallback_transcript_path(payload) == path


def diagnose_transcript(path):
    """(usable: bool, detail: str) — can this guard trust `path` as a classification source at
    all? Shared by `--selftest` and the live click path (`evaluate()`) so the two can never
    disagree about what "inert" means — dev #102 was exactly that disagreement's blast radius.

    Deliberately does NOT reuse `load_transcript()`, which silently skips a line that fails to
    parse (correct for classification — one bad line must not blind a click's resolution). That
    leniency would make this indistinguishable from the exact failure it exists to catch: a
    transcript whose envelope shifted so every line fails to parse looks identical, through
    `load_transcript()`, to a brand-new empty session — zero records either way. So this reads
    the file directly and tells "no lines yet" (fine) apart from "lines present, none match the
    expected {uuid, message} envelope" (a format shift, and not fine)."""
    if not path:
        return False, ("no transcript_path in the payload, and no fallback could be derived "
                        "(need both session_id and cwd)")
    try:
        with open(path, encoding="utf-8") as fh:
            lines = [l.strip() for l in fh if l.strip()]
    except Exception as e:
        # dev #102's second failure mode: capture the ACTUAL reason, not a generic failure.
        return False, ("transcript_path %r could not be opened — %s: %s"
                        % (path, type(e).__name__, e))
    if not lines:
        return True, ("transcript at %s is empty (fresh session) — nothing to disagree with "
                      "yet" % path)
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
    return True, ("transcript at %s parses as JSONL (%d of %d line(s) match the expected "
                  "envelope)" % (path, matching, len(lines)))


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


# Set by evaluate() to this invocation's (usable, diag_detail, pending), or None when the call
# never reached diagnosis (unguarded tool, no inputs). Read by main_hook() for status recording
# (dev #111) WITHOUT re-reading the transcript — a third full read per click of a possibly
# large file, purely for bookkeeping, would be paying correctness money for accounting.
# A hook process runs evaluate() exactly once, so a module global is safe there; direct
# callers (tests, doctor) never read it.
_LAST_DIAGNOSIS = None


def evaluate(payload):
    """The whole classify-a-hook-call pipeline. Returns (deny, deny_reasons, notes).

    dev #102: `diagnose_transcript()` is re-derived on THIS invocation, fresh, every time — no
    persisted "the selftest failed at startup" flag to go stale. When it says the surface is
    unusable, that is exactly the condition the SessionStart selftest would also hit, so an
    unresolved click's note is escalated to the loud "GUARD INERT" wording rather than the
    generic per-click ambiguity note — and because this runs per hook invocation, that escalation
    happens on EVERY click while the condition persists, not once at startup.

    dev #137: unless `transcript_pending_creation()` says this specific unusable-ness is just the
    transcript not existing YET (see its docstring) — in which case the note says so honestly
    instead of claiming "GUARD INERT", because that claim would usually be false: proven, the
    transcript is reliably present by the time any tool's PreToolUse fires, well before a click
    could plausibly be the very first tool call of a session."""
    global _LAST_DIAGNOSIS
    _LAST_DIAGNOSIS = None
    tool_name = payload.get("tool_name") or ""
    if tool_name not in GUARDED_CLICK_TOOLS:
        return False, [], []

    tool_input = payload.get("tool_input") or {}
    inputs = list(iter_click_inputs(tool_name, tool_input))
    if not inputs:
        return False, [], []

    transcript_path, _used_fallback = resolve_transcript_path(payload)
    usable, diag_detail = diagnose_transcript(transcript_path)
    pending = transcript_pending_creation(payload, transcript_path, usable, diag_detail)
    _LAST_DIAGNOSIS = (usable, diag_detail, pending)
    records = load_transcript(transcript_path) if usable else None
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
            if not usable and pending:
                notes.append(
                    "transcript not created yet on this surface at this point in the session "
                    "(dev #137) -- this click was allowed because nothing can be classified "
                    "until it exists, not because the guard is proven inert -- %s"
                    % diag_detail)
            elif not usable:
                notes.append(
                    "GUARD INERT on this surface (the same condition --selftest checks at "
                    "SessionStart) -- %s -- this click was allowed because nothing on this "
                    "surface can be classified, not because it was individually ambiguous"
                    % diag_detail)
            else:
                notes.append(reason)
    return bool(deny_reasons), deny_reasons, notes


def main_hook():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # unreadable payload is not evidence of a violation — fails open

    try:
        denied, deny_reasons, notes = evaluate(payload)

        # dev #111: make this invocation's diagnosis durable and queryable. Best-effort,
        # AFTER classification, and never able to change the verdict below.
        # dev #137: EXCEPT when transcript_pending_creation() said this unusable-ness is just
        # startup timing, never recorded — a false "inert" observation in the durable log is
        # exactly the defect dev #111 exists to prevent, aimed at itself.
        if _LAST_DIAGNOSIS is not None:
            usable, diag_detail, pending = _LAST_DIAGNOSIS
            if not pending:
                record_status("ok" if usable else "inert", "click",
                              "ok" if usable else _reason_code(diag_detail),
                              payload.get("session_id"))

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
# dev #111 — durable, queryable guard status. See the module docstring section of the same
# name for the design; the shape is _diag.py's (coded scalars only, never prose, so the
# record itself can cross into a public issue).
# --------------------------------------------------------------------------------------

STATUS_EVENT = "guard_status"
STATUS_GUARD = "outbound-click"


def _import_diag():
    """The diagnostics logger, or None. A hook must run even where the sibling module is
    somehow unimportable — recording is bookkeeping, never a dependency of the verdict."""
    try:
        import _diag
        return _diag
    except Exception:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import _diag
            return _diag
        except Exception:
            return None


def _reason_code(detail):
    """Map a `diagnose_transcript()` prose detail to a fixed reason CODE — the only form the
    diagnostics log accepts (its `redact()` refuses prose, deliberately: prose is where user
    data hides, and the transcript path embeds the cwd). The prose itself stays in the
    session's systemMessage, where dev #102 put it."""
    d = detail or ""
    if "no transcript_path" in d:
        return "no-transcript-path"
    if "could not be opened" in d:
        return "open-failed"
    if "NONE parsed" in d:
        return "format-shift"
    return "unknown"


def read_status_history(path=None):
    """(records, unreadable_count) — every parseable `guard_status` record in the diagnostics
    log, oldest first. ⚠️ An unparseable value must be LOUD (CLAUDE.md): a guard_status record
    whose `state` is not a recognized code is COUNTED and surfaced by `guard_status()`, never
    silently dropped — a status nobody can read looks handled and is not. Lines that are not
    guard_status events at all are simply other tools' diagnostics, not defects."""
    if path is None:
        diag = _import_diag()
        path = diag.LOG if diag else None
    records, unreadable = [], 0
    if not path or not os.path.exists(path):
        return records, unreadable
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if not isinstance(rec, dict) or rec.get("event") != STATUS_EVENT:
                    continue
                if rec.get("guard") not in (None, STATUS_GUARD):
                    continue
                if rec.get("state") not in ("ok", "inert"):
                    unreadable += 1
                    continue
                records.append(rec)
    except Exception:
        pass
    return records, unreadable


def record_status(state, source, reason, session_id=None):
    """Append one coded guard_status event, throttled: skip when the latest record already
    carries the same (state, session) — so a session contributes one record per state, not one
    per click, and the ~500-line ring buffer holds days of history instead of minutes. A state
    CHANGE within a session (inert -> ok after a fix, or the reverse) always records.
    Best-effort and silent: recording must never alter a verdict or block anything."""
    try:
        diag = _import_diag()
        if diag is None:
            return
        records, _ = read_status_history(diag.LOG)
        if records:
            last = records[-1]
            if last.get("state") == state and (last.get("session") or None) == (session_id or None):
                return
        diag.log(STATUS_EVENT, guard=STATUS_GUARD, state=state, source=source,
                 reason=reason, session=session_id)
    except Exception:
        pass


def _registration(hooks_path):
    """(registered, detail) — is this guard actually wired into hooks.json, with the matcher it
    requires and its selftest at SessionStart? A guard file that exists but is not registered is
    inert EVERYWHERE, and a status line must never report 'live' from the file's mere presence."""
    want = "|".join(GUARDED_CLICK_TOOLS)
    try:
        with open(hooks_path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception as e:
        return False, "hooks.json unreadable at %s (%s)" % (hooks_path, type(e).__name__)
    pre = (cfg.get("hooks") or {}).get("PreToolUse") or []
    click_entries = [e for e in pre
                     if any("guard_outbound_click.py" in (h.get("command") or "")
                            for h in e.get("hooks") or [])]
    if not click_entries:
        return False, "guard_outbound_click.py is not wired into any PreToolUse entry"
    if not any(e.get("matcher") == want for e in click_entries):
        return False, ("PreToolUse matcher drifted from GUARDED_CLICK_TOOLS "
                       "(check_click_guard_matcher.py has the detail)")
    starts = (cfg.get("hooks") or {}).get("SessionStart") or []
    if not any(("guard_outbound_click.py" in (h.get("command") or "")
                and "--selftest" in (h.get("command") or ""))
               for e in starts for h in e.get("hooks") or []):
        return False, ("--selftest is not wired into SessionStart, so status is never "
                       "recorded and an inert surface goes back to being invisible")
    return True, "PreToolUse matcher matches GUARDED_CLICK_TOOLS; --selftest wired at SessionStart"


def guard_status(status_log=None, hooks_path=None):
    """The guard-status line `docs/deployment.md` promises from doctor/whoami, as data.

    Verdicts, derived ONLY from recorded observations plus the executable fixture — never from
    this file merely existing:

        BROKEN   not registered in hooks.json, or the parser fixture fails — inert everywhere
        INERT    the latest recorded observation says the transcript is unusable on that surface
        ACTIVE   the latest recorded observation verified the transcript parses
        UNKNOWN  registered and parser-sound, but no session has recorded an observation yet

    The optional parameters are test seams (same shape as CLAUDESEARCH_DIAG_LOG itself)."""
    engine = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
    if hooks_path is None:
        hooks_path = os.path.join(engine, "hooks", "hooks.json")
    registered, reg_detail = _registration(hooks_path)
    fixture_ok, fixture_detail = _selftest_fixture()
    if status_log is None:
        diag = _import_diag()
        status_log = diag.LOG if diag else None
    history, unreadable = read_status_history(status_log)
    latest = history[-1] if history else None

    inert_since, inert_sessions = None, 0
    if latest is not None and latest.get("state") == "inert":
        stretch = []
        for rec in reversed(history):
            if rec.get("state") != "inert":
                break
            stretch.append(rec)
        inert_since = stretch[-1].get("at")
        inert_sessions = len({r.get("session") for r in stretch})

    if not registered or not fixture_ok:
        verdict = "BROKEN"
        line = ("BROKEN — %s — the guard is inert on EVERY surface until this is fixed in "
                "the engine" % (reg_detail if not registered else fixture_detail))
    elif latest is None:
        verdict = "UNKNOWN"
        line = ("registered and parser-sound, but NO recorded observation yet — the "
                "SessionStart selftest writes one per session; status is unknown until a "
                "session starts on this surface")
    elif latest.get("state") == "inert":
        verdict = "INERT"
        line = ("INERT since %s across %s session(s) (latest reason: %s, via %s) — browser "
                "sends are UNGUARDED on the observed surface; the prose prohibition in "
                "linkedin-runner.md is the only control there"
                % (inert_since, inert_sessions, latest.get("reason"), latest.get("source")))
    else:
        verdict = "ACTIVE"
        line = ("ACTIVE — guarding %s; last verified %s (via %s)"
                % ("|".join(GUARDED_CLICK_TOOLS), latest.get("at"), latest.get("source")))
    if unreadable:
        line += (" ⚠️ %d guard_status record(s) were unreadable — an unparseable status looks "
                 "handled and is not; treat as a defect" % unreadable)

    return {"verdict": verdict, "line": line,
            "registered": registered, "registered_detail": reg_detail,
            "fixture_ok": fixture_ok, "fixture_detail": fixture_detail,
            "observations": len(history), "unreadable": unreadable,
            "latest": latest, "inert_since": inert_since, "inert_sessions": inert_sessions,
            "observed_from": history[0].get("at") if history else None,
            "log_path": status_log}


def main_status():
    st = guard_status()
    print("outbound-click guard: %s" % st["line"])
    print("  registered:      %s — %s" % ("yes" if st["registered"] else "NO",
                                          st["registered_detail"]))
    print("  parser fixture:  %s — %s" % ("ok" if st["fixture_ok"] else "FAIL",
                                          st["fixture_detail"]))
    print("  observations:    %d recorded%s (log: %s)"
          % (st["observations"],
             " since %s" % st["observed_from"] if st["observed_from"] else "",
             st["log_path"]))
    return 0 if st["verdict"] in ("ACTIVE", "UNKNOWN") else 1


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
    """(ok, detail) — resolves the LIVE transcript path from the SessionStart payload (the
    payload's own `transcript_path`, or — dev #102 — a derived fallback when that key is absent)
    and asserts its envelope parses at all via `diagnose_transcript()`, the SAME function the
    live click path uses, so this selftest and `evaluate()` can never disagree about what
    "inert" means. Does not require any particular content — an empty or freshly-started
    transcript is fine; an unreadable or unparseable one is not."""
    path, used_fallback = resolve_transcript_path(payload)
    ok, detail = diagnose_transcript(path)
    if used_fallback:
        if path:
            detail += " [derived fallback path — SessionStart payload carried no transcript_path]"
        else:
            detail = ("SessionStart payload carried no transcript_path, and no fallback could "
                       "be derived (need both session_id and cwd)")
    return ok, detail


def main_selftest():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

    ok_fixture, detail_fixture = _selftest_fixture()
    ok_live, detail_live = _selftest_live(payload)
    path_live, _used_fallback_live = resolve_transcript_path(payload)
    pending = ok_fixture and transcript_pending_creation(payload, path_live, ok_live, detail_live)

    # dev #111: the durable record. One coded event per session start, so "has this install
    # had an inert guard for two days?" is answerable from data instead of somebody noticing.
    # dev #137: EXCEPT when `pending` — never record "inert" from a transcript that simply does
    # not exist YET (transcript_pending_creation()'s docstring). Doing so would durably and
    # systematically mislabel every `-p` session as inert, which is the false-positive twin of
    # the missing-observation problem dev #111 exists to fix — a wrong fact in the store is worse
    # than no fact.
    if not ok_fixture:
        reason = "fixture-fail"
    elif not ok_live:
        reason = _reason_code(detail_live)
    else:
        reason = "ok"
    if not pending:
        record_status("ok" if (ok_fixture and ok_live) else "inert", "selftest", reason,
                      payload.get("session_id"))

    print("guard_outbound_click --selftest")
    print("  fixture parser/verb-pattern check: %s — %s" % ("OK" if ok_fixture else "FAIL",
                                                             detail_fixture))
    if pending:
        print("  live transcript envelope check:    PENDING — %s" % detail_live)
        print("    [dev #137: not recorded as inert -- Claude Code has not created this "
              "session's transcript yet, which is expected at SessionStart under some launch "
              "modes (e.g. `claude -p`); the live click path re-checks fresh on every click, by "
              "which point the transcript is reliably present]")
    else:
        print("  live transcript envelope check:    %s — %s" % ("OK" if ok_live else "FAIL",
                                                                 detail_live))

    if not ok_fixture or (not ok_live and not pending):
        sys.stdout.write(json.dumps({
            "systemMessage": "guard_outbound_click --selftest FAILED (%s%s%s) — the outbound-"
                              "click guard is INERT on this surface. Browser sends are "
                              "UNGUARDED; the behavioural prohibition in linkedin-runner.md is "
                              "the only control until this is fixed."
                              % (("fixture: %s" % detail_fixture if not ok_fixture else ""),
                                 (" / " if not ok_fixture and not ok_live else ""),
                                 ("live: %s" % detail_live if not ok_live else "")),
        }) + "\n")
    elif pending:
        sys.stdout.write(json.dumps({
            "systemMessage": "guard_outbound_click --selftest: could not verify the live "
                              "transcript at session start (%s). This is NOT evidence the guard "
                              "is inert (dev #137) -- Claude Code defers creating this session's "
                              "transcript until after every SessionStart hook returns, so no "
                              "SessionStart-time check can see it on this surface. The guard "
                              "re-verifies fresh on every actual click, and THAT observation is "
                              "what gets recorded." % detail_live,
        }) + "\n")
    return 0  # never blocks startup — same posture as the click path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true",
                    help="run from SessionStart; proves the transcript parser is sound here")
    ap.add_argument("--status", action="store_true",
                    help="print the guard-status line doctor/whoami carry (dev #111); "
                         "exit 1 when INERT or BROKEN")
    args = ap.parse_args()
    if args.selftest:
        return main_selftest()
    if args.status:
        return main_status()
    return main_hook()


if __name__ == "__main__":
    sys.exit(main())
