#!/usr/bin/env python3
"""The single owner of "which group does this row belong in?" for Your Move. GitHub #79.

⭐ THE DEFECT THIS CLOSES
-------------------------
The "needs you" queue used to select rows by OWNERSHIP ALONE: `next_action_owner ==
<candidate> and status in live`. That is a necessary condition, not a sufficient one — a
role future-dated weeks out, one waiting on someone else entirely, and one genuinely overdue
all rendered identically. `next_action_date` was read ONLY as a sort key, never as a cutoff,
and a channel's `next_touch.date` being merely truthy was enough to list it forever, even
after the touch it asked for had already happened.

`generate_dashboard.py` must never re-derive group membership. It imports this module and
renders exactly what `classify_opportunities` / `classify_channels` say.

## Role states, in precedence order

    unresolved   blocked_until is the literal `unresolved`, or unparseable. Its own loud
                 callout — NEVER the primary "needs you" group.
    waiting      blocked_until parses, but no outreach touch to that contact has reached a
                 listed outcome yet.
    scheduled    no unfired trigger, and next_action_date is in the future.
    now          owner is the candidate, status is live, no unfired trigger, and the date is
                 today or in the past (or absent).

## The `blocked_until` field

Grammar is `precondition.py`'s VERBATIM: `contact:<contact_id> outcome:<v>|<v>`, resolved
against the RECORD'S OWN `outreach[]` — never the global pipeline, because a join to another
opportunity's touch would say this role moved when it did not. Plus the literal `unresolved`.
No `date:` form: a time trigger already lives in `next_action_date`, and inventing a second
way to spell the same thing is the exact duplication issue #6 removed for drafts.

## Channel touches are DERIVED, never a hand-authored `last_touch`

`last_touch` is gone from the schema (nothing ever wrote it — see `migrate.py`'s note). A
channel's last touch is computed here, always: the max of (the latest OUTBOUND message in
`messages.jsonl` whose `contact_id` joins any of the channel's `contacts[].contact_id`) and
(the latest `log[]` entry date).

⭐ THE FULFILMENT RULE'S SHARP EDGE. A derived touch dated ON OR AFTER `next_touch.date`
fulfils the plan. An EARLIER touch does NOT — the row stays `now`, because the cheap error is
a look at a handled row and the expensive one is a phantom fulfilment cancelling a call that
is still owed.

`next_touch` itself gets no mechanical write path here, deliberately: it is a plan authored
by judgement (`record.py`), and nothing in this module ever advances or clears it.

Usage:
    python3 your_move.py            # human-readable: every role and channel, with its state
    python3 your_move.py --json
    python3 your_move.py --check    # exit 1 on an unresolved / unreadable blocked_until

Python 3.9+. Standard library only.
"""

import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root                                      # noqa: E402
import precondition as _pre                                         # noqa: E402
import profile as _profile                                          # noqa: E402

# Re-exported so a caller (validate_data.py) needs exactly one import to validate the field.
PreconditionError = _pre.PreconditionError

LIVE_OPP_STATUSES = {"active-pursuit", "needs-resolution"}

ROLE_STATES = ("unresolved", "waiting", "scheduled", "now")
CHANNEL_STATES = ("now", "scheduled", "fulfilled")


def _load_jsonl(root, name):
    path = os.path.join(root, "data", name)
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    except OSError:
        pass
    return out


def parse_blocked_until(raw):
    """'contact:x outcome:accepted|replied' -> {'contact': 'x', 'outcomes': {...}}, or the
    sentinel `_pre.UNRESOLVED` for the literal `unresolved`. Raises `PreconditionError` on
    anything else — never guessed over, same rule precondition.py enforces for drafts."""
    if _pre.UNRESOLVED_RE.match(str(raw)):
        return _pre.UNRESOLVED
    return _pre.parse(raw)


def role_state(o, today):
    """(state, why) for one LIVE, owner-owned opportunity row. See the module docstring for
    the precedence order this implements."""
    raw = o.get("blocked_until")
    if raw is not None:
        try:
            parsed = parse_blocked_until(raw)
        except _pre.PreconditionError as e:
            return "unresolved", "blocked_until is unreadable: %s" % e
        if parsed == _pre.UNRESOLVED:
            return "unresolved", ("blocked_until is the literal 'unresolved' — no structured "
                                  "join yet; write contact:<id> outcome:<...>")
        # THE RECORD'S OWN outreach[], never the global pipeline (see module docstring).
        touches = {}
        for r in (o.get("outreach") or []):
            cid = r.get("contact_id")
            if cid:
                touches.setdefault(cid, []).append(r)
        ok, why = _pre.resolve(parsed, touches)
        if not ok:
            return "waiting", why
        # Precondition satisfied — an unfired trigger has fired, so fall through to the date
        # check below exactly as an opportunity with no blocked_until at all would.
    d = o.get("next_action_date")
    if d and str(d) > today:
        return "scheduled", "next_action_date %s is in the future" % d
    return "now", ""


def classify_opportunities(opps, owner_token, today=None):
    """[(opp, state, why)] for every LIVE opportunity owned by `owner_token` — the ONE
    definition of Your Move role-group membership. `generate_dashboard.py` consumes this;
    it must never re-derive the filter itself."""
    today = today or datetime.date.today().isoformat()
    out = []
    for o in opps:
        if o.get("next_action_owner") != owner_token or o.get("status") not in LIVE_OPP_STATUSES:
            continue
        state, why = role_state(o, today)
        out.append((o, state, why))
    return out


def derive_channel_last_touch(channel, messages):
    """(date, evidence) — the ISO date of the channel's most recently derived touch and a
    short string naming what produced it, or (None, None) if it has neither.

    max of: the latest OUTBOUND message.sent_on whose contact_id joins any of this channel's
    contacts[].contact_id, and the latest log[] entry date. Never a hand-authored field."""
    contact_ids = {c.get("contact_id") for c in (channel.get("contacts") or [])
                   if c.get("contact_id")}
    candidates = []
    if contact_ids:
        for m in messages:
            if m.get("direction") == "outbound" and m.get("contact_id") in contact_ids:
                d = m.get("sent_on")
                if d:
                    candidates.append((str(d), "message %s" % (m.get("id") or "?")))
    for e in (channel.get("log") or []):
        d = e.get("date")
        if d:
            candidates.append((str(d), "log entry (%s)" % (e.get("note") or d)))
    if not candidates:
        return None, None
    return max(candidates, key=lambda t: t[0])


def channel_state(c, messages, today):
    """(state, derived_date, evidence) for one channel carrying a next_touch plan."""
    nt = c.get("next_touch") or {}
    plan = str(nt.get("date"))
    touch, evidence = derive_channel_last_touch(c, messages)
    # THE FULFILMENT RULE. On-or-after fulfils; strictly earlier does not — see module
    # docstring for why the asymmetry is deliberate.
    if touch and touch >= plan:
        return "fulfilled", touch, evidence
    if plan > today:
        return "scheduled", touch, evidence
    return "now", touch, evidence


def classify_channels(channels, messages, today=None):
    """[(channel, state, derived_date, evidence)] for every channel carrying a next_touch
    plan. A channel with no plan at all is not a candidate and is excluded here, unchanged
    from before this module existed."""
    today = today or datetime.date.today().isoformat()
    out = []
    for c in channels:
        nt = c.get("next_touch")
        if not isinstance(nt, dict) or not nt.get("date"):
            continue
        state, touch, evidence = channel_state(c, messages, today)
        out.append((c, state, touch, evidence))
    return out


def contact_joinability_gaps(channels):
    """Channel ids carrying a next_touch plan but no joinable contact_id anywhere in
    contacts[] — the outbound-message half of the derivation can then never fire for them,
    and their last touch silently degrades to log[]-only forever. Declaring this is the
    gate-must-assert-its-own-coverage rule: a derivation that can never see half its inputs
    has to say so, not just return a quietly-partial answer."""
    gaps = []
    for c in channels:
        nt = c.get("next_touch")
        if not isinstance(nt, dict) or not nt.get("date"):
            continue
        if not any(ct.get("contact_id") for ct in (c.get("contacts") or [])):
            gaps.append(c.get("id") or c.get("label") or "?")
    return gaps


def report(root, today=None):
    """Everything --json / --check need, computed once from the profile at `root`."""
    opps = _load_jsonl(root, "opportunities.jsonl")
    channels = _load_jsonl(root, "channels.jsonl")
    messages = _load_jsonl(root, "messages.jsonl")
    owner = _profile.owner_token()
    roles = classify_opportunities(opps, owner, today)
    chans = classify_channels(channels, messages, today)
    return {
        "roles": [{"id": o.get("id"), "title": o.get("title"), "state": s, "why": w}
                  for o, s, w in roles],
        "channels": [{"id": c.get("id"), "label": c.get("label") or c.get("id"), "state": s,
                      "derived_last_touch": t, "evidence": ev}
                     for c, s, t, ev in chans],
        "contact_joinability_gaps": contact_joinability_gaps(channels),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if any role's blocked_until is unresolved or unreadable")
    args = ap.parse_args()

    root = profile_root()
    data = report(root)

    if args.json:
        print(json.dumps(data, indent=1))
    else:
        print("YOUR MOVE — role and channel group membership\n")
        marks = {"now": "🎯", "scheduled": "🗓️ ", "waiting": "⏳", "unresolved": "🚧"}
        for r in data["roles"]:
            print("  %s %-10s %s" % (marks[r["state"]], r["state"],
                                     (r["title"] or r["id"] or "?")[:60]))
            if r["why"]:
                print("        %s" % r["why"])
        cmarks = {"now": "🤝", "scheduled": "🗓️ ", "fulfilled": "✅"}
        for c in data["channels"]:
            print("  %s %-10s %s" % (cmarks[c["state"]], c["state"], c["label"]))
            if c["state"] == "fulfilled":
                print("        plan fulfilled on %s by %s" % (c["derived_last_touch"],
                                                                c["evidence"]))
        n_unres = sum(1 for r in data["roles"] if r["state"] == "unresolved")
        n_fulfilled = sum(1 for c in data["channels"] if c["state"] == "fulfilled")
        print("\n  %d role(s) unresolved · %d channel plan(s) fulfilled but not yet cleared"
              % (n_unres, n_fulfilled))
        for gid in data["contact_joinability_gaps"]:
            print("  ⚠️  channel %s has no joinable contact_id in contacts[] — its derived "
                  "touch can only ever come from log[]" % gid)

    if args.check:
        bad = False
        for r in data["roles"]:
            if r["state"] == "unresolved":
                print("⛔ %s [unresolved]: %s" % ((r["title"] or r["id"] or "?")[:60], r["why"]),
                      file=sys.stderr)
                bad = True
        for c in data["channels"]:
            if c["state"] == "fulfilled":
                print("ℹ️  %s: plan fulfilled on %s by %s; clear next_touch or author the "
                      "next one" % (c["label"], c["derived_last_touch"], c["evidence"]),
                      file=sys.stderr)
        for gid in data["contact_joinability_gaps"]:
            print("⚠️  channel %s: no joinable contact_id in contacts[] — the outbound-message "
                  "half of its derived touch can never fire" % gid, file=sys.stderr)
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
