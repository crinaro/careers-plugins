#!/usr/bin/env python3
"""Validate the JSONL sourcing dataset: schema, enums, types, referential integrity.

Companion to docs/schema.md. Runs in the start-of-run hygiene step alongside
check_stale_claims / check_followups / check_sections. This is the piece that
kills the whole data-integrity bug class markdown couldn't prevent: it guarantees
every record is typed, every enum is in range, and every cross-reference resolves.

    python3 scripts/validate_data.py

Exit 0 = clean, 1 = problems found (so a caller CAN gate on it if desired).

Targets Python 3.9+ (see CLAUDE.md), stdlib only.
"""

import json
import os
import re
import sys

import os, sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root as _profile_root
import route as _route
import profile as _profile
import your_move as _ym

ROOT = _profile_root()
# ⭐ Overridable so a FRESH INSTALL can be verified (2026-08-05). A new user's very first gate run
# must pass against an EMPTY profile; if it fails they conclude the system is broken before they
# have entered anything. Same override as init_profile.py, and the same reason as
# CLAUDESEARCH_DATA_DIR on funnel_report.py: a guarantee nobody tests is a guarantee nobody has.
DATA = os.environ.get("CLAUDESEARCH_DATA_DIR") or os.path.join(ROOT, "data")

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

VERTICALS = {"healthcare-payer", "healthcare-provider", "healthtech", "saas",
             "fintech", "insurtech", "other"}
COMPANY_STATUS = {"active-target", "watching", "passed"}
CHANNEL_TYPES = {"job-board", "aggregator", "company-site", "recruiter",
                 "referral", "alert-email"}
CADENCES = {"daily", "weekly", "biweekly", "monthly", "on-inbound"}
# `expired` added 2026-08-11 (issue #6): the posting vanished/closed before any decision was
# recorded. TERMINAL, and distinct from `passed` — "I declined this" and "it disappeared before
# I decided" are different signals wanting different remedies, and recording an expiry as a pass
# overstates the pass rate while hiding that the pipeline loses roles to expiry. Existing
# `parked` rows written as an expiry workaround are deliberately NOT reclassified: nothing can
# retroactively distinguish a genuine park from the workaround (adr-013).
OPP_STATUS = {"active-pursuit", "needs-resolution", "in-motion", "backlog", "passed", "expired"}
STAGES = {"sourced", "contacted", "screening", "interviewing", "offer", "closed"}
# ⭐ `play_stage` — where a pursued role sits in the POST-APPLICATION PLAY (public #19 / dev
# #95). `stage` is the funnel position; the play sequence is finer-grained: which step of the
# apply-then-reach-the-recruiter play is next. It used to be encoded as numbered free-text
# markers prefixed onto `next_action`, which nothing could filter, group, count, sort or
# validate — the fourth instance of "a fact a run knows goes into the queryable store"
# (act_by, precondition.py, location 'unresolved'). ORDERED, so consumers can sort by
# sequence position rather than alphabetically.
PLAY_SEQUENCE = ("needs-application", "applied", "needs-recruiter-contact", "verify-req-live",
                 "identify-recruiter", "reach-insider", "contact-recruiter", "awaiting-reply")
# `unresolved` is the migration marker (same precedent as blocked_until and location.type): a
# play position was detected in prose but could not be structured mechanically. Valid, durable,
# and deliberately NOT part of the sequence — the way out is a human writing the real value.
PLAY_STAGES = set(PLAY_SEQUENCE) | {"unresolved"}
# Every play position from `applied` onward presupposes a submitted application on the record.
POST_APPLICATION_PLAY = set(PLAY_SEQUENCE[1:])
# applications[].status values that prove a submission actually happened.
SUBMITTED_APP_STATUS = {"submitted", "acknowledged", "rejected", "advanced"}
VERDICTS = {"pursue", "pass", "parked", "undecided"}
# `unresolved` added 2026-08-11 (issue #4): a posting that declares two settings at once (e.g.
# tagged both hybrid and remote) previously forced a silent pick, and the pick selected which
# comp floor applied. `unresolved` makes "contested — ask the employer" a representable value
# instead of an absence; the verbatim declared text goes in `location.declared` (required for
# this type), and profile.screen_comp() DECLINES to pick a tier for it (adr-013).
LOC_TYPES = {"remote", "hybrid", "onsite", "relocation", "unresolved"}
# ⭐ Never a hardcoded name. `next_action_owner` ∈ {this candidate's own token, "me"} — "me"
# means the engine/assistant acts next, the candidate's own token (read from user.json via
# profile.owner_token(), never typed here) means the human does. A previous version spelled
# that first value out as one specific candidate's own literal name — correct for exactly one
# installation and silently wrong for anyone whose profile names them anything else.
OWNERS = {_profile.owner_token(), "me"}
CONTACT_EMAIL_STATUS = {"verified-published", "verified-received", "pattern-inferred", "unknown"}
OUTREACH_STATUS = {"drafted", "staged", "sent", "declined"}
# Added 2026-07-21, per the candidate: "We should be tracking who we connected with & when
# i applied to analyze what works and what doesnt." Applications were previously
# stuffed into outreach[] with a person-shaped `to` field reading e.g.
# "<a recognizable employer> careers (direct ATS application)" -- so counting them meant string-matching
# a free-text name, and an ATS submission was indistinguishable from a networking
# note. They are different funnels with different success measures; they get
# different arrays.
APPLICATION_METHODS = {"company-ats", "linkedin-easy-apply", "recruiter-submitted",
                       "email", "referral"}
APPLICATION_STATUS = {"not-started", "started", "submitted", "acknowledged",
                      "rejected", "advanced", "withdrawn"}
# Whether outreach got a reply -- the other half of "what works".
OUTREACH_OUTCOME = {"awaiting", "replied", "no-response", "declined",
                    "meeting-booked", "accepted", "n/a"}
# `accepted` added 2026-08-02 (the candidate's Decision 2). An accepted connection request that drew
# no reply is a REAL positive signal for the connection-note medium — the candidate's own stated
# mechanism is that the accept is what unlocks a better second touch. Scoring it identically
# to "ignored" made the medium the candidate believes in look weaker than it is. Reported on its own
# line, never merged into `replied`.

# ---- Communications: HOW a message was sent, and what kind it was (added 2026-08-02) ----
# WHY: `channel_id` was carrying three meanings at once — relationship (firm:halloway-partners),
# medium (linkedin-direct), and implicitly purpose. Worse, `linkedin-direct`'s own label read
# "exec-to-exec InMail" while the 12 rows stamped with it on 7/31 were CONNECTION-REQUEST NOTES.
# Every question the candidate asked about which comms work turns on distinctions that field erased.
MEDIA = {"linkedin-connection-note", "linkedin-inmail", "linkedin-message",
         "email-cold", "email-reply", "phone", "sms", "other", "unknown"}
# ⭐ Channel ids that still EXIST in channels.jsonl but must never be used again. They resolve,
# so a referential check cannot catch them; only naming them can. Kept as rows rather than
# deleted because historical outreach still points at them and a dangling pointer is worse.
RETIRED_CHANNEL_IDS = {"linkedin-direct", "email-direct"}
TOUCH_TYPES = {"first-touch", "chase", "reply", "referral-ask", "intro-request",
               "thank-you", "reconnect", "apply-path", "unknown"}
RECIPIENT_ROLES = {"hiring-manager", "hiring-line", "talent-acquisition", "recruiter-agency",
                   "warm-contact", "peer-network", "other", "unknown"}
ADDRESS_STATUS = {"verified-published", "verified-received", "pattern-inferred", "unknown"}
# A bounce that looks like a non-reply silently poisons the only comms metric there is.
DELIVERY = {"delivered", "bounced", "unknown"}
# Unknown keys are REJECTED. Four alias keys (sent_on, replied_on, channel, notes) drifted
# into the data precisely because nothing rejected them.
OUTREACH_KEYS = {"to", "contact_id", "channel_id", "status", "date", "responded_on", "outcome",
                 "medium", "touch_type", "recipient_role", "campaign_id", "address_status",
                 "delivery", "message_ref", "variant", "note"}
# New required fields apply only from the cutover — backfilled history carries "unknown"
# where no contemporaneous record supports a value. Without this the validator would fail
# against 46 legacy rows on day one and get ignored.
COMMS_CUTOVER = "2026-08-02"
PATH_TYPES = {"warm-referral", "recruiter", "hiring-manager", "hiring-context", "internal", "cold"}
# JD fit analysis (added 2026-08-02, per the candidate: "how is the candidate match to the JD?").
# DATA, not a document — requirement/verdict/evidence/question is a dataset, so it lives on
# the opportunity record and is validated like everything else.
FIT_VERDICTS = {"aligned", "partial", "not-aligned", "unknown"}
FIT_Q_STATUS = {"n/a", "open", "answered"}
# ⭐ Issue #34, part 2. This used to be a hand-maintained copy of `route.py`'s vocabulary and it
# drifted: `migrate.py`'s `m_0_14_0` rewrites legacy `access` values to route.py's canonical
# requirements (`login-chrome` -> `login`, `public-bot-limited` -> `bot-limited`, per
# `route.LEGACY`), but this set never gained `login`/`bot-limited` — so a channel the ENGINE
# ITSELF just migrated failed validation immediately after. Deriving from `route.py` (its
# REQUIREMENTS plus the LEGACY values migrate.py has not yet rewritten) makes that drift
# structurally impossible instead of relying on two files being edited together.
# "manual-candidate" is not part of route.py's vocabulary (it predates the requirement/mechanism
# split and is documented in docs/schema.md) - kept here rather than silently invalidating any
# existing data that carries it. Renamed (issue #35) from a value spelling out one specific
# candidate's own name literally: zero live records used that value, so this was a rename with
# no data to migrate, not a schema change.
ACCESS = set(_route.REQUIREMENTS) | set(_route.LEGACY) | {"manual-candidate"}
# ---- Asks and commitments (dev #93 / public #21) ----------------------------------------
# The hand-authored tail of Your Move and the This Week schedule were the last state living in
# focus.md prose, where a hand-written copy of a record went stale beside the generated row.
# They are stores now: an ask is OPEN until `resolved_on` is set (views filter, so expulsion is
# structural), and a commitment's `date` may be the literal `unresolved` — the migration marker
# for a date that could not be parsed, same precedent as blocked_until and play_stage.
ASK_KINDS = {"role", "system"}
UNRESOLVED = "unresolved"


def load(name):
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        return None, ["%s does not exist" % name]
    recs, errs = [], []
    for i, line in enumerate(open(path, encoding="utf-8"), 1):
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except ValueError as e:
            errs.append("%s line %d: invalid JSON — %s" % (name, i, e))
    return recs, errs


def is_date(v):
    return isinstance(v, str) and DATE_RE.match(v)


def req(rec, field, label, problems):
    if field not in rec:
        problems.append("%s: missing required field '%s'" % (label, field))
        return False
    return True


def enum(rec, field, allowed, label, problems, nullable=False):
    if field not in rec:
        problems.append("%s: missing '%s'" % (label, field))
        return
    v = rec[field]
    if v is None and nullable:
        return
    if v not in allowed:
        problems.append("%s: '%s'=%r not in {%s}" % (label, field, v, ", ".join(sorted(allowed))))


def main():
    problems = []
    companies, e = load("companies.jsonl"); problems += e or []
    channels, e = load("channels.jsonl"); problems += e or []
    opps, e = load("opportunities.jsonl"); problems += e or []

    if companies is None or channels is None or opps is None:
        # Files not created yet — this is fine before the migration lands.
        print("Data validation — dataset not present yet (pre-migration). Nothing to check.")
        return 0

    # every contact_id known anywhere — opportunities AND channels both carry people
    all_contact_ids = set()
    for _r in opps:
        for _c in (_r.get("contacts") or []):
            if _c.get("contact_id"):
                all_contact_ids.add(_c["contact_id"])
    for _r in (channels or []):
        for _c in (_r.get("contacts") or []):
            if _c.get("contact_id"):
                all_contact_ids.add(_c["contact_id"])

    sent_msgs, e = load("messages.jsonl"); problems += e or []
    sent_ids = set()
    for m in (sent_msgs or []):
        if m.get("id") == "_README":
            continue
        mid = m.get("id", "?")
        ml = "messages[%s]" % mid
        if mid in sent_ids:
            problems.append("%s: duplicate id" % ml)
        sent_ids.add(mid)
        for f in ("direction", "sent_on", "medium", "body"):
            if not m.get(f):
                problems.append("%s: missing required field '%s'" % (ml, f))

        # ⭐ A MESSAGE MAY BELONG TO A RELATIONSHIP RATHER THAN A ROLE (2026-08-04).
        # `opp_id` was unconditionally required, which could not express the single most
        # valuable message type in an executive search: a WARM INTRODUCTION. A run recorded two
        # real ones — a third party introducing the candidate to a search-firm partner, and that
        # partner's reply — and both failed validation because they attach to a FIRM
        # RELATIONSHIP, not to any one role. Forcing an opp_id would have been a lie; dropping
        # them would have deleted the touch that produced the meeting.
        # So: a message must anchor to SOMETHING — an opportunity or a channel — but not both
        # by force.
        if not m.get("opp_id") and not m.get("channel_id"):
            problems.append("%s: needs an anchor — set 'opp_id' for a role-specific message, or "
                            "'channel_id' for one that belongs to a relationship (a warm intro, "
                            "a recruiter thread). A message anchored to nothing is unfindable."
                            % ml)

        # ⭐ THIRD-PARTY is a real direction. A referral endorsement written BY someone else
        # ABOUT the candidate is neither inbound nor outbound — the candidate is cc'd, not a
        # participant — and it is often the highest-value artifact in the whole record.
        if m.get("direction") not in ("inbound", "outbound", "third-party", None):
            problems.append("%s: direction %r must be inbound|outbound|third-party"
                            % (ml, m.get("direction")))
        # Provenance is required: a stored body with no traceable source is an assertion,
        # not a record. Format: gmail:<account>:<uid>, or 'drafts.md' for one the candidate sent directly.
        # ⭐ A MESSAGE'S contact_id MUST RESOLVE — to an opportunity's contacts[] OR a channel's.
        # Added 2026-08-04 after THREE guessed ids passed unnoticed in one afternoon:
        # 'derek-holland' for 'derek-holland-acme', 'priya-nakamura' for
        # 'priya-nakamura-globex', and a contact anchored to the wrong firm entirely just to
        # satisfy the anchor rule. A join key that does not join is worse than no key: it makes
        # "what is the whole history with X?" silently return nothing instead of failing.
        mcid = m.get("contact_id")
        if mcid and mcid not in all_contact_ids:
            problems.append("%s: contact_id %r resolves to no contacts[] entry on any "
                            "opportunity or channel. A guessed id silently breaks every "
                            "person-level query." % (ml, mcid))
        if not m.get("source"):
            problems.append("%s: missing 'source' — a body with no provenance cannot be "
                            "re-verified against the mailbox" % ml)
        if m.get("sent_on") and not is_date(m["sent_on"]):
            problems.append("%s: sent_on not ISO — %r" % (ml, m.get("sent_on")))
        # MEDIA is the OUTREACH taxonomy — it exists to answer "which of the candidate's own
        # channels works". A third-party message is not their outreach, so a plain generic is
        # correct for it and forcing e.g. 'email-cold' would corrupt the funnel denominators.
        msg_media = MEDIA | {"email"} if m.get("direction") == "third-party" else MEDIA
        if m.get("medium") and m["medium"] not in msg_media:
            problems.append("%s: medium %r not in {%s}"
                            % (ml, m["medium"], ", ".join(sorted(msg_media))))

    company_ids, channel_ids = set(), set()

    # ⭐ UNKNOWN-KEY GUARD FOR EVERY ARRAY, FROM docs/data_model.json (2026-08-04).
    # Only outreach[] had one before, which is why `nxet_action_owner` wrote silently and this
    # validator reported CLEAN. The definition lives in ONE file that record.py also reads —
    # restating the field list here would be the same drift the banned_aliases exist to stop.
    try:
        # ⭐ ENGINE path, not ROOT (2026-08-05). The schema ships with the ENGINE; the data
        # belongs to the USER. Resolving it off ROOT conflated the two and broke the moment
        # a data dir was pointed elsewhere — which is precisely what ADR-007's repo split
        # does permanently. Anchored to this file's own location instead.
        _engine = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(_engine, "docs", "data_model.json"), encoding="utf-8") as _fh:
            _model = json.load(_fh)
    except Exception as _e:
        problems.append("cannot read docs/data_model.json (%s) — the key guard is OFF" % _e)
        _model = None
    if _model:
        _ali = {k: v for k, v in _model["banned_aliases"].items() if not k.startswith("_")}
        _spec = _model["stores"]["opportunities"]
        for r in opps:
            _l = "opportunities[%s]" % r.get("id", "?")
            for _k in r:
                if _k in _ali:
                    problems.append("%s: %r is a banned alias for %r — two spellings of one "
                                    "meaning make a query miss half the data" % (_l, _k, _ali[_k]))
                elif _k not in _spec["fields"]:
                    problems.append("%s: unknown key %r. Add it to docs/data_model.json if it is "
                                    "genuinely new; otherwise it is a typo that every query "
                                    "against the real field will silently miss." % (_l, _k))
            for _arr, _aspec in (_spec.get("arrays") or {}).items():
                # Dotted names address a nested array (`fit.requirements`). A plain `r.get()`
                # returns None for those, so the model would declare fields that nothing
                # enforced — a schema that silently checks nothing is worse than no schema.
                _node = r
                for _part in _arr.split(".")[:-1]:
                    _node = _node.get(_part) or {}
                for _i, _item in enumerate(_node.get(_arr.split(".")[-1]) or []):
                    for _k in _item:
                        if _k in _ali:
                            problems.append("%s: %s[%d] %r is a banned alias for %r"
                                            % (_l, _arr, _i, _k, _ali[_k]))
                        elif _k not in _aspec["fields"]:
                            problems.append("%s: %s[%d] unknown key %r (known: %s)"
                                            % (_l, _arr, _i, _k, ", ".join(sorted(_aspec["fields"]))))

    # ---- companies ----
    for r in companies:
        cid = r.get("id", "?")
        label = "companies[%s]" % cid
        for f in ("id", "name", "vertical", "status"):
            req(r, f, label, problems)
        if r.get("id") in company_ids:
            problems.append("%s: duplicate id" % label)
        company_ids.add(r.get("id"))
        enum(r, "vertical", VERTICALS, label, problems)
        enum(r, "status", COMPANY_STATUS, label, problems)
        for entry in r.get("research_log", []):
            if not is_date(entry.get("date", "")):
                problems.append("%s: research_log date not ISO — %r" % (label, entry.get("date")))

    # ---- channels ----
    for r in channels:
        chid = r.get("id", "?")
        label = "channels[%s]" % chid
        for f in ("id", "label", "type", "review_cadence"):
            req(r, f, label, problems)
        if r.get("id") in channel_ids:
            problems.append("%s: duplicate id" % label)
        channel_ids.add(r.get("id"))
        enum(r, "type", CHANNEL_TYPES, label, problems)
        enum(r, "review_cadence", CADENCES, label, problems)
        if "access" in r:
            enum(r, "access", ACCESS, label, problems)
        lr = r.get("last_reviewed")
        if lr is not None and not is_date(lr):
            problems.append("%s: last_reviewed not ISO or null — %r" % (label, lr))
        nt = r.get("next_touch")
        if nt is not None and not is_date(nt.get("date", "")):
            problems.append("%s: next_touch.date not ISO — %r" % (label, nt.get("date")))
        for e in r.get("log", []):
            if not is_date(e.get("date", "")):
                problems.append("%s: log date not ISO — %r" % (label, e.get("date")))

    # ---- opportunities ----
    opp_ids = set()
    for r in opps:
        oid = r.get("id", "?")
        label = "opportunities[%s]" % oid
        for f in ("id", "company_id", "title", "status", "stage", "verdict"):
            req(r, f, label, problems)
        if r.get("id") in opp_ids:
            problems.append("%s: duplicate id" % label)
        opp_ids.add(r.get("id"))

        enum(r, "status", OPP_STATUS, label, problems)
        enum(r, "stage", STAGES, label, problems)
        enum(r, "verdict", VERDICTS, label, problems)

        # ---- play_stage: the post-application play position (public #19 / dev #95) ----
        # Optional and nullable — but an unreadable value must be LOUD, never carried: a play
        # position nobody can parse looks handled and is not (the precondition.py rule).
        ps = r.get("play_stage")
        if ps is not None:
            if ps not in PLAY_STAGES:
                problems.append("%s: play_stage %r not in {%s} — an unreadable play position "
                                "looks handled and is not; fix the value or null the field"
                                % (label, ps, ", ".join(sorted(PLAY_STAGES))))
            else:
                # Resolve against data the store ALREADY has, the same move as act_by and
                # precondition.py: the applications[] array is the evidence of submission.
                submitted = any(a.get("status") in SUBMITTED_APP_STATUS
                                for a in (r.get("applications") or []))
                if ps == "needs-application" and submitted:
                    problems.append("%s: play_stage 'needs-application' but an applications[] "
                                    "row is already %s — the store knows this role was applied "
                                    "to; advance the play_stage" %
                                    (label, "/".join(sorted(SUBMITTED_APP_STATUS))))
                if ps in POST_APPLICATION_PLAY and not submitted:
                    problems.append("%s: play_stage %r presupposes a submitted application, but "
                                    "no applications[] row has status in {%s} — a post-"
                                    "application play on a role never applied to is a claim the "
                                    "store contradicts" %
                                    (label, ps, ", ".join(sorted(SUBMITTED_APP_STATUS))))
                if r.get("status") in ("passed", "expired"):
                    problems.append("%s: status %r with play_stage %r — a terminal role has no "
                                    "live play position; null the field when a role leaves the "
                                    "funnel" % (label, r.get("status"), ps))

        # referential integrity
        if r.get("company_id") not in company_ids:
            problems.append("%s: company_id %r does not resolve" % (label, r.get("company_id")))
        ch = r.get("channel_id")
        if ch is not None and ch not in channel_ids:
            problems.append("%s: channel_id %r does not resolve" % (label, ch))

        # ---- fit analysis (optional block) ----
        fit = r.get("fit")
        if fit is not None:
            if not isinstance(fit, dict):
                problems.append("%s: fit must be an object" % label)
            else:
                if not is_date(fit.get("analyzed_on", "")):
                    problems.append("%s: fit.analyzed_on not ISO — %r" % (label, fit.get("analyzed_on")))
                reqs = fit.get("requirements")
                if not isinstance(reqs, list) or not reqs:
                    problems.append("%s: fit.requirements must be a non-empty list" % label)
                else:
                    for i, q in enumerate(reqs):
                        rl = "%s fit.requirements[%d]" % (label, i)
                        if not (q.get("requirement") or "").strip():
                            problems.append("%s: empty 'requirement'" % rl)
                        enum(q, "verdict", FIT_VERDICTS, rl, problems)
                        enum(q, "question_status", FIT_Q_STATUS, rl, problems)
                        # An alignment claim with no citation is a gap wearing a disguise.
                        if q.get("verdict") in ("aligned", "partial") and not (q.get("evidence") or "").strip():
                            problems.append("%s: verdict=%r requires 'evidence' — an uncited "
                                            "alignment claim is not evidence of alignment"
                                            % (rl, q.get("verdict")))
                        # An unknown with no question is a gap nobody will ever close.
                        # ⭐ act_by — added 2026-08-03. A question with a DATE is a different
                        # object from one without. The candidate: "does the coordinator know to suggest a
                        # draft a nudge to <a recruiter> for today?" It did not. <a recruiter>'s auto-reply
                        # said she returns Monday August 3; that fact went into the question as
                        # PROSE, and nothing can sort or surface prose. A date in a field can be.
                        ab = q.get("act_by")
                        if ab is not None and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(ab)):
                            problems.append("%s: act_by %r is not an ISO date" % (rl, ab))
                        if q.get("verdict") == "unknown" and not (q.get("question_for_candidate") or "").strip():
                            problems.append("%s: verdict='unknown' requires 'question_for_candidate' — "
                                            "otherwise the gap is recorded and never harvested" % rl)
                        if q.get("question_status") == "answered" and not (q.get("landed_in") or "").strip():
                            problems.append("%s: question_status='answered' requires 'landed_in' "
                                            "(projects.md / resume.md-addendum / resume.md / kb_<company>.md)" % rl)
                        if q.get("answered_on") and not is_date(q.get("answered_on")):
                            problems.append("%s: answered_on not ISO — %r" % (rl, q.get("answered_on")))

        # jd_url must be present as string or explicit null
        if "jd_url" not in r:
            problems.append("%s: jd_url missing (use explicit null if none)" % label)

        # comp typed
        comp = r.get("comp")
        if comp is not None:
            mn, mx = comp.get("min"), comp.get("max")
            if not isinstance(mn, (int, float)) or not isinstance(mx, (int, float)):
                problems.append("%s: comp.min/max must be numbers — %r" % (label, comp))
            elif mn > mx:
                problems.append("%s: comp.min %s > comp.max %s" % (label, mn, mx))

        # location shape
        loc = r.get("location", {})
        if not isinstance(loc, dict) or loc.get("type") not in LOC_TYPES:
            problems.append("%s: location.type not in {%s}" % (label, ", ".join(sorted(LOC_TYPES))))
        if isinstance(loc, dict):
            decl = loc.get("declared")
            if decl is not None and not isinstance(decl, str):
                problems.append("%s: location.declared must be a string — it is the posting's "
                                "own verbatim work-setting text" % label)
            # ⭐ `unresolved` without the verbatim evidence is just a guess deferred. The whole
            # point of the state (issue #4) is that something downstream can revisit what the
            # posting ACTUALLY said — today that information is destroyed at parse time.
            if loc.get("type") == "unresolved" and not (decl if isinstance(decl, str) else "").strip():
                problems.append("%s: location.type 'unresolved' requires 'declared' — the "
                                "posting's verbatim work-setting text is what the question to "
                                "the employer gets asked FROM; without it nothing can revisit "
                                "the conflict" % label)

        # sightings — the overlap records
        sightings = r.get("sightings", [])
        if not sightings:
            problems.append("%s: no sightings (how was it found?)" % label)
        for i, sg in enumerate(sightings):
            scid = sg.get("channel_id")
            if scid not in channel_ids:
                problems.append("%s: sighting[%d].channel_id %r does not resolve" % (label, i, scid))
            if not is_date(sg.get("seen_on", "")):
                problems.append("%s: sighting[%d].seen_on not ISO — %r" % (label, i, sg.get("seen_on")))

        # ownership — required, drives Your Move vs my-tasks generation
        enum(r, "next_action_owner", OWNERS, label, problems)

        # ⭐ blocked_until — GitHub #79. Grammar is precondition.py's VERBATIM, owned by
        # your_move.py (the single place that decides Your Move group membership). Only a
        # genuinely UNREADABLE value is a schema problem: the literal 'unresolved' is valid,
        # durable data (a decided, not-yet-structured state — see your_move.py's docstring),
        # exactly as precondition.py never treats its own `unresolved` marker as a parse
        # error. An unreadable precondition is worse than none because it looks handled and
        # is not, so this fails loudly rather than silently defaulting to 'now'.
        bu = r.get("blocked_until")
        if bu is not None:
            try:
                _ym.parse_blocked_until(bu)
            except _ym.PreconditionError as e:
                problems.append("%s: blocked_until %r is unreadable — %s" % (label, bu, e))
        # contacts — warm paths / hiring managers / internal
        for i, ct in enumerate(r.get("contacts", [])):
            if "name" not in ct:
                problems.append("%s: contact[%d] missing name" % (label, i))
            if ct.get("path_type") and ct["path_type"] not in PATH_TYPES:
                problems.append("%s: contact[%d].path_type %r invalid" % (label, i, ct.get("path_type")))
        # ---- contacts[]: the people, with a stable id so outreach can JOIN to them ----
        # Added 2026-08-02 after the candidate asked whether the structure was managing all the contact
        # data for an opportunity. It wasn't: 20 of 46 outreach rows had NO contact record, and
        # `to` was free text that couldn't match `name` even when both existed.
        contact_ids = set()
        for i, ct in enumerate(r.get("contacts", [])):
            cl = "%s: contacts[%d]" % (label, i)
            cid = ct.get("contact_id")
            if not cid:
                problems.append("%s: missing 'contact_id' — outreach cannot join to it" % cl)
            elif cid in contact_ids:
                problems.append("%s: duplicate contact_id %r" % (cl, cid))
            else:
                contact_ids.add(cid)
            if not (ct.get("name") or "").strip():
                problems.append("%s: missing 'name'" % cl)
            em = ct.get("email")
            if em and not re.match(r"^[\w.+-]+@[\w.-]+\.\w{2,}$", em):
                problems.append("%s: email %r is not an address" % (cl, em))
            # ⭐ A structured address must carry HOW WE KNOW IT. Added 2026-08-03 after
            # a contact's address sat in a prose note marked UNVERIFIED: lifting it
            # into `email` makes it queryable, but without this it would read as confirmed.
            # Same distinction outreach[].address_status already draws.
            es = ct.get("email_status")
            if es is not None and es not in CONTACT_EMAIL_STATUS:
                problems.append("%s: email_status %r not in {%s}"
                                % (cl, es, ", ".join(sorted(CONTACT_EMAIL_STATUS))))

        # outreach — links drafts to the role (kills the phantom-drafts bug)
        for i, o2 in enumerate(r.get("outreach", [])):
            if o2.get("status") not in OUTREACH_STATUS:
                problems.append("%s: outreach[%d].status %r not in {%s}" % (label, i, o2.get("status"), ", ".join(sorted(OUTREACH_STATUS))))
            ocid = o2.get("channel_id")
            if ocid is not None and ocid not in channel_ids:
                problems.append("%s: outreach[%d].channel_id %r does not resolve" % (label, i, ocid))
            # ⭐⭐ A MEDIUM IS NOT A RELATIONSHIP — enforced HERE, not only in the test suite.
            #
            # `linkedin-direct` and `email-direct` are legacy rows that still RESOLVE in
            # channels.jsonl, so the resolve check above waves them through. The rule that they
            # are media masquerading as relationships lived only in `test_checks.py`, which runs
            # weekly and in CI — **so the write API could not enforce it.** A row stamped
            # `channel_id: linkedin-direct` was written on 2026-08-06, passed validation, passed
            # record.py's post-write check, and persisted; only the regression suite noticed,
            # days later, by which point it is history rather than a rejected keystroke.
            #
            # THE GENERAL LESSON, and it is the reason this moved: **a rule that lives only in
            # the test suite cannot protect a write.** The validator runs on every write; the
            # suite runs on a schedule. Any invariant about DATA belongs in the validator, and
            # the suite's job is to assert that the validator still enforces it.
            if ocid in RETIRED_CHANNEL_IDS:
                problems.append(
                    "%s: outreach[%d].channel_id %r is a MEDIUM, not a relationship — it is "
                    "retired. Put the medium in 'medium' (%s) and leave channel_id null unless a "
                    "real relationship (a firm or referrer) carried the message."
                    % (label, i, ocid, ", ".join(sorted(MEDIA))))
            if o2.get("outcome") is not None and o2["outcome"] not in OUTREACH_OUTCOME:
                problems.append("%s: outreach[%d].outcome %r not in {%s}" % (label, i, o2.get("outcome"), ", ".join(sorted(OUTREACH_OUTCOME))))

            ol = "%s: outreach[%d]" % (label, i)
            # Unknown keys REJECTED — this is what catches the next sent_on/replied_on alias.
            extra = set(o2) - OUTREACH_KEYS
            if extra:
                problems.append("%s: unknown key(s) %s — an alias key that nothing rejects is "
                                "how sent_on/replied_on/channel/notes drifted into the data"
                                % (ol, ", ".join(sorted(extra))))
            # `to` and `date` were never required or type-checked before 2026-08-02.
            if o2.get("status") == "sent":
                if not (o2.get("to") or "").strip():
                    problems.append("%s: status='sent' requires a non-empty 'to'" % ol)
                if not is_date(o2.get("date") or ""):
                    problems.append("%s: status='sent' requires an ISO 'date' — an undated row "
                                    "makes check_followups over-report silence" % ol)
            for fld, allowed in (("medium", MEDIA), ("touch_type", TOUCH_TYPES),
                                 ("recipient_role", RECIPIENT_ROLES), ("delivery", DELIVERY)):
                v = o2.get(fld)
                if v is not None and v not in allowed:
                    problems.append("%s: %s=%r not in {%s}" % (ol, fld, v, ", ".join(sorted(allowed))))
            if o2.get("address_status") is not None and o2["address_status"] not in ADDRESS_STATUS:
                problems.append("%s: address_status=%r not in {%s}"
                                % (ol, o2["address_status"], ", ".join(sorted(ADDRESS_STATUS))))
            # An email medium without an address_status can't distinguish a bounce from silence.
            if (o2.get("medium") or "").startswith("email") and not o2.get("address_status"):
                problems.append("%s: medium=%r requires 'address_status' — otherwise a bounced "
                                "pattern-inferred address is indistinguishable from a non-reply"
                                % (ol, o2.get("medium")))
            # From the cutover, the comms fields are required (history carries 'unknown').
            if (o2.get("date") or "") >= COMMS_CUTOVER and o2.get("status") == "sent":
                for fld in ("medium", "touch_type", "recipient_role", "delivery"):
                    if not o2.get(fld):
                        problems.append("%s: '%s' is required on rows dated %s or later"
                                        % (ol, fld, COMMS_CUTOVER))
            # THE JOIN. If the candidate messaged someone, they must exist as a contact of this
            # opportunity — otherwise "what is the whole history with this person?" is
            # unanswerable, which is exactly the gap the candidate identified.
            ocid = o2.get("contact_id")
            if not ocid:
                problems.append("%s: missing 'contact_id' — every outreach row must name the "
                                "person it went to (run scripts/migrate_contacts.py)" % ol)
            elif ocid not in contact_ids:
                problems.append("%s: contact_id %r does not resolve to a contacts[] entry on "
                                "this opportunity" % (ol, ocid))

            if o2.get("message_ref") and o2["message_ref"] not in sent_ids:
                problems.append("%s: message_ref %r does not resolve in data/messages.jsonl "
                                "— a pointer to text that isn't there is worse than no pointer"
                                % (ol, o2["message_ref"]))
            if o2.get("campaign_id") and not re.match(r"^[a-z0-9][a-z0-9-]*$", o2["campaign_id"]):
                problems.append("%s: campaign_id %r must be a lowercase slug" % (ol, o2["campaign_id"]))
        # applications[] — when the candidate applied, how, and what came back
        for i, ap in enumerate(r.get("applications", [])):
            if ap.get("method") not in APPLICATION_METHODS:
                problems.append("%s: applications[%d].method %r not in {%s}" % (label, i, ap.get("method"), ", ".join(sorted(APPLICATION_METHODS))))
            if ap.get("status") not in APPLICATION_STATUS:
                problems.append("%s: applications[%d].status %r not in {%s}" % (label, i, ap.get("status"), ", ".join(sorted(APPLICATION_STATUS))))
            # A submitted application must carry the date it went out, or the
            # whole point (measuring time-to-response) is lost.
            if ap.get("status") in ("submitted", "acknowledged", "rejected", "advanced") and not ap.get("date"):
                problems.append("%s: applications[%d] is %r but has no date — that is the field the funnel analysis runs on" % (label, i, ap.get("status")))
        # status vs. stage — orthogonal, but not every pairing is coherent.
        # Added 2026-07-21: the markdown backfill left two live active pursuits
        # (two employers) sitting at stage "closed", which
        # says we're actively pursuing a role we've also marked as out of the
        # funnel. Nothing caught it because each field was independently valid.
        st, stg = r.get("status"), r.get("stage")
        if st in ("active-pursuit", "needs-resolution", "in-motion") and stg == "closed":
            problems.append("%s: status %r with stage 'closed' — a live role cannot be out of the funnel" % (label, st))
        if st == "passed" and stg not in ("closed", None):
            problems.append("%s: status 'passed' but stage %r — passed roles belong at stage 'closed'" % (label, stg))
        # `expired` is terminal (issue #6): out of the funnel, like passed…
        if st == "expired" and stg not in ("closed", None):
            problems.append("%s: status 'expired' but stage %r — an expired role is out of the "
                            "funnel and belongs at stage 'closed'" % (label, stg))
        # …but it records the ABSENCE of a decision. A decided pass is status 'passed';
        # stamping a role both 'expired' and verdict 'pass' would re-create the exact
        # corruption the state exists to remove (an expiry counted as a deliberate pass).
        if st == "expired" and r.get("verdict") == "pass":
            problems.append("%s: status 'expired' with verdict 'pass' — expired records that NO "
                            "decision was made before the posting vanished; if the candidate "
                            "decided to pass, the status is 'passed'" % label)

    # ---- asks (dev #93) — the hand-authored tail of Your Move, structured ----
    # Absence is legal: a profile predating the 0.25.0 migration has no asks.jsonl yet, and
    # the fixture ships without one. Present-but-broken is a problem like any other store.
    asks, e = load("asks.jsonl")
    if asks is None:
        asks = []
    else:
        problems += e or []
    ask_ids = set()
    for r in asks:
        aid = r.get("id", "?")
        label = "asks[%s]" % aid
        for f in ("id", "kind", "title", "ask", "created"):
            req(r, f, label, problems)
        if r.get("id") in ask_ids:
            problems.append("%s: duplicate id" % label)
        ask_ids.add(r.get("id"))
        enum(r, "kind", ASK_KINDS, label, problems)
        if not is_date(r.get("created", "")):
            problems.append("%s: created not ISO — %r" % (label, r.get("created")))
        for f in ("act_by", "resolved_on"):
            v = r.get(f)
            if v is not None and f in r and not is_date(v):
                problems.append("%s: %s not ISO or null — %r" % (label, f, v))
        if r.get("opp_id") and r["opp_id"] not in opp_ids:
            problems.append("%s: opp_id %r resolves to no opportunity" % (label, r["opp_id"]))
        if r.get("channel_id") and r["channel_id"] not in channel_ids:
            problems.append("%s: channel_id %r resolves to no channel" % (label, r["channel_id"]))
        # An ask that is resolved must say how it resolved — "expelled" with no outcome is
        # the old delete-the-prose move with less accountability, not more.
        if r.get("resolved_on") and not r.get("resolution"):
            problems.append("%s: resolved_on with no resolution — say how it resolved "
                            "(answered / lapsed / superseded / done)" % label)

    # ---- commitments (dev #93) — This Week, structured ----
    commitments, e = load("commitments.jsonl")
    if commitments is None:
        commitments = []
    else:
        problems += e or []
    cm_ids = set()
    for r in commitments:
        cid = r.get("id", "?")
        label = "commitments[%s]" % cid
        for f in ("id", "date", "title"):
            req(r, f, label, problems)
        if r.get("id") in cm_ids:
            problems.append("%s: duplicate id" % label)
        cm_ids.add(r.get("id"))
        d = r.get("date")
        if d is not None and not is_date(d) and d != UNRESOLVED:
            # An unreadable date must be LOUD (the precondition.py rule): a commitment nobody
            # can place on a calendar looks handled and is not.
            problems.append("%s: date %r is neither ISO nor the literal %r" % (label, d, UNRESOLVED))
        if r.get("opp_id") and r["opp_id"] not in opp_ids:
            problems.append("%s: opp_id %r resolves to no opportunity" % (label, r["opp_id"]))
        if r.get("channel_id") and r["channel_id"] not in channel_ids:
            problems.append("%s: channel_id %r resolves to no channel" % (label, r["channel_id"]))

    # Unknown-key guard for both new stores — same model-driven rule opportunities already
    # gets, because `nxet_action_owner` is exactly the class of typo these fields will grow.
    if _model:
        _ali = {k: v for k, v in _model["banned_aliases"].items() if not k.startswith("_")}
        for store_name, rows_ in (("asks", asks), ("commitments", commitments)):
            _sspec = _model["stores"].get(store_name) or {}
            for r in rows_:
                _l = "%s[%s]" % (store_name, r.get("id", "?"))
                for _k in r:
                    if _k in _ali:
                        problems.append("%s: %r is a banned alias for %r" % (_l, _k, _ali[_k]))
                    elif _k not in (_sspec.get("fields") or ()):
                        problems.append("%s: unknown key %r (known: %s)"
                                        % (_l, _k, ", ".join(sorted(_sspec.get("fields") or ()))))

    print("Data validation — %d companies, %d channels, %d opportunities, %d asks, "
          "%d commitments" % (len(companies), len(channels), len(opps), len(asks),
                              len(commitments)))
    if not problems:
        print("\n  Clean. Schema, enums, types, and every cross-reference resolve.")
        return 0
    print("\n" + "=" * 68)
    print("%d PROBLEM(S)" % len(problems))
    print("=" * 68)
    for p in problems:
        print("  - " + p)
    return 1


if __name__ == "__main__":
    sys.exit(main())
