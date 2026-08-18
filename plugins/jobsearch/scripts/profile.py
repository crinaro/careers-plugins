#!/usr/bin/env python3
"""
Load `profile.json` and make its screening rules EXECUTABLE.

WHY THIS EXISTS
---------------
Created 2026-08-02, when the candidate asked to segment the process from the user data.

The tiered comp floor lived only as ~400 words of prose in `CLAUDE.md`. Prose gets
re-interpreted by a fresh model every run, and this particular prose has already
produced a real, costly error: on **2026-07-22 a re-scoring pass failed <an employer> against
the relocation bar** — but <an employer> is **25 minutes from the candidate's house**.
The pass had read `location.type == "onsite"` as "must relocate."

`onsite` in the data does NOT mean "move." It means the seat is in an office. Whether
that costs the candidate anything depends entirely on WHERE the office is, and there is a whole
separate, LOWER local-onsite tier for offices inside their commute radius. Getting this backwards flips a passing role into a failing one.

    screen_comp() encodes that distinction so it cannot drift again.

Usage:
    python3 scripts/profile.py                      # print the loaded profile summary
    python3 scripts/profile.py --screen <opp_id>    # screen one opportunity
    python3 scripts/profile.py --screen-all         # screen the whole live pipeline

    from profile import load, screen_comp, effective_setting

Python 3.9+. Standard library only.
"""

import argparse
import json
import os
import sys

import os, sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_or_fixture as _profile_or_fixture

# ⭐ FIXTURE FALLBACK, LIKE EVERY OTHER GATE — and NOT a try/except that returns 0.
#
# `validate_data.py` began importing this module when `owner_token()` replaced a hardcoded name
# (issue #35), so a resolver that only ever finds a REAL profile took the data gate down in CI:
# `FileNotFoundError: .../user.json`, on a runner that deliberately has no profile.
#
# ⚠️ The obvious repair — catch the error and exit 0 when there is no profile — is the exact
# defect three gates were fixed for in this same branch: a check that reports success having
# read nothing. `profile_or_fixture()` is the answer the rest of the engine already uses. It
# resolves the real profile where one exists and the SYNTHESIZED fixture otherwise, so CI runs
# the real code against real-shaped data instead of skipping it.
ROOT = _profile_or_fixture()
USER_PATH = os.path.join(ROOT, "user.json")      # LAYER 1 — who the person is
CONFIG_PATH = os.path.join(ROOT, "config.json")  # LAYER 2 — how the search behaves

# Verdicts
CLEARS = "CLEARS"
BELOW = "BELOW-FLOOR"
UNDISCLOSED = "UNDISCLOSED"
NEEDS_COMMUTE = "NEEDS-COMMUTE-CHECK"
# Added 2026-08-11 (issue #4). A posting that declares two settings at once (tagged both
# hybrid and remote) has `location.type: "unresolved"` — and the resolver DECLINES to pick,
# because the pick selects which comp floor applies. Same family as NEEDS-COMMUTE-CHECK:
# an answer exists, it just has to come from the employer instead of an inference.
UNRESOLVED = "UNRESOLVED-SETTING"


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def user():
    """LAYER 1 — user data: who this person is."""
    return _read(USER_PATH)


def config():
    """LAYER 2 — configuration: how the search should behave."""
    return _read(CONFIG_PATH)


def load(path=None):
    """Both layers merged, for callers that just want 'the profile'.

    Kept so the engine has ONE obvious entry point. The layers stay separate ON DISK
    (that is the whole point); this is a read-time convenience, not a third file.
    """
    if path:
        return _read(path)
    merged = dict(config())
    merged.update(user())
    return merged


def mailboxes():
    """Addresses to search. THE source — never hardcode a mailbox in a script."""
    return [m["address"] for m in user()["mailboxes"]]


def owner_token():
    """The literal value `next_action_owner` uses for 'the candidate must act'.

    This candidate's own `preferred_reference`, lowercased — never a hardcoded name.
    `next_action_owner` ∈ {owner_token(), "me"}: "me" means the engine/assistant acts next,
    the candidate's own token means the human does. A previous version of this schema spelled
    the first value out as a literal name, one specific candidate's, hardcoded across
    validate_data.py, coordinator.py and generate_dashboard.py — correct for exactly one
    installation and silently wrong for anyone else's profile. Read it from user.json instead,
    the same way every other per-candidate fact in this engine is read rather than typed twice.
    """
    ident = user()["identity"]
    ref = ident.get("preferred_reference") or ident.get("display_name") or ident["full_name"]
    return ref.strip().lower()


def currency_symbol():
    """The symbol comp figures render with. `config.json.compensation.currency_symbol`,
    default "$" when unset — additive, so no existing profile needs a migration.

    The engine never converts between currencies: every figure in a profile is taken to be in
    that profile's own currency. This fixes only the LABEL, which used to be a hardcoded "$"
    for every installation — a profile in another currency got the right number under a wrong
    unit, which is worse than an error because nothing looked broken."""
    try:
        return config().get("compensation", {}).get("currency_symbol") or "$"
    except (OSError, ValueError):
        return "$"


def _render(template_lines):
    """Render a config TEMPLATE against user data — the two layers meeting.

    This is the concrete demonstration that configuration and user data are separate:
    config.json holds the SHAPE of a signature, user.json holds the phone number, and
    neither file contains the other's content.
    """
    u = user()["identity"]
    fields = {
        "full_name": u["full_name"],
        "phone": u["phone"],
        "city": u["city"],
        "primary_email": u["primary_email"],
        "linkedin": u["linkedin"],
        "linkedin_display": u["linkedin"].replace("https://", "").rstrip("/"),
    }
    return [line.format(**fields) for line in template_lines]


def comms():
    """LAYER 2 — how outreach is sent. The single source of truth for every constraint."""
    return config()["communications"]


def medium_constraints(medium):
    """Constraints for one medium, e.g. {'max_chars': 300, 'target_chars': [200, 260]}.

    Read this instead of retyping a limit. The 300-character connection-note cap lived in
    prose in four separate places before 2026-08-02, and the <=120-word cap was hardcoded
    inside an agent definition where nothing could check it.
    """
    return comms()["constraints_by_medium"].get(medium, {})


def email_signature():
    return _render(config()["writing"]["email_signature_template"])


def cover_letter_header():
    return _render(config()["writing"]["cover_letter_header_template"])


def tiers(profile):
    return {t["setting"]: t for t in profile["compensation"]["tiers"]}


def effective_setting(location, profile, commute_ok=None):
    """Map an opportunity's `location` object to a COMP TIER name.

    This is the function that exists because of the <an employer> error. The mapping is NOT
    a straight passthrough of `location.type`:

      remote                      -> "remote"
      hybrid                      -> "hybrid"
      relocation                  -> "relocation"
      onsite + inside the radius  -> "local-onsite"   (NO move: The candidate's own discount)
      onsite + outside the radius -> "relocation"     (a real move)
      onsite + unknown location   -> None             (ASK; never assume relocation)
      unresolved                  -> None             (the POSTING is contested — ask the
                                                       employer which setting governs;
                                                       location.declared holds its verbatim
                                                       wording. Never pick a tier.)

    `commute_ok`: pass True/False if you already know. Otherwise it is inferred from
    the location string against profile geography anchors, and an unrecognized place
    returns None rather than guessing — the whole point is to stop silent guessing.
    """
    if not isinstance(location, dict):
        return None
    ltype = location.get("type")
    if ltype in ("remote", "hybrid", "relocation"):
        return ltype
    if ltype == "unresolved":
        return None      # contested by the posting itself — screen_comp() surfaces the question
    if ltype != "onsite":
        return None

    if commute_ok is None:
        commute_ok = within_commute(location, profile)
    if commute_ok is None:
        return None
    return "local-onsite" if commute_ok else "relocation"


def within_commute(location, profile):
    """True / False / None(unknown). Text matching, deliberately conservative."""
    hay = " ".join(str(location.get(k) or "") for k in ("primary", "note")).lower()
    if not hay.strip():
        return None
    for anchor in profile["geography"]["commute_anchors"]:
        names = [anchor["place"]] + list(anchor.get("includes", []))
        for n in names:
            # match on the distinctive part ("<the commute anchor>", "<a nearby metro>", "<a nearby metro>")
            token = n.split(",")[0].strip().lower()
            if token and token in hay:
                return True
    return None  # unknown, NOT False — never infer "relocation" from silence


def screen_comp(opp, profile, commute_ok=None):
    """Screen one opportunity's comp against the applicable tier.

    Returns (verdict, detail). Verdicts: CLEARS / BELOW-FLOOR / UNDISCLOSED /
    NEEDS-COMMUTE-CHECK / UNRESOLVED-SETTING.

    Screening is applied to the TOP of the stated band (profile's below_floor rule),
    and an undisclosed band is explicitly KEPT — comp is the first question to ask,
    so it cannot be screened on.
    """
    comp = opp.get("comp")
    loc = opp.get("location") or {}

    # ⭐ A CONTESTED SETTING IS DECLINED, NOT RESOLVED (issue #4). The posting itself declared
    # more than one setting; whichever one a resolver picked would silently select the comp
    # floor the role is screened against. This is the third instance of the standing rule that
    # a fact a run knows goes into the queryable store: the run KNOWS the setting is contested,
    # and this verdict is where that fact surfaces — loudly, as a question for the EMPLOYER,
    # never as a silent skip. The role stays in the pipeline until the answer arrives.
    if loc.get("type") == "unresolved":
        declared = loc.get("declared")
        return UNRESOLVED, (
            "the posting declares a contested work setting%s — the answer selects which comp "
            "floor applies, so NO tier is picked. ASK THE EMPLOYER which setting governs, then "
            "record the answer in location.type. This role needs an answer; it is NOT screened "
            "and must not quietly drop out."
            % (" (verbatim: %r)" % declared if declared else ""))

    setting = effective_setting(loc, profile, commute_ok)

    if setting is None:
        return NEEDS_COMMUTE, (
            "location.type=%r at %r — cannot tell whether this is a commute or a MOVE. "
            "Ask/check before applying any floor. Never assume 'onsite' means relocation."
            % (loc.get("type"), loc.get("primary")))

    tier = tiers(profile)[setting]
    floor = tier["floor"]

    if not comp or comp.get("max") is None:
        return UNDISCLOSED, (
            "no stated band — KEEP (comp is the first question; it cannot be screened on). "
            "Applicable tier would be %s (floor $%sK base)." % (setting, floor // 1000))

    top = comp["max"]
    basis = (comp.get("basis") or "base").lower()
    note = ""
    if basis != "base":
        note = (" ⚠️ band is stated as %r, but the floor is BASE — compare like with like "
                "before trusting this verdict." % basis)

    if top >= floor:
        return CLEARS, ("top of band $%sK >= %s floor $%sK.%s"
                        % (int(top) // 1000, setting, floor // 1000, note))
    gap = floor - top
    return BELOW, (
        "top of band $%sK is $%sK BELOW the %s floor ($%sK). Profile says REMOVE unless "
        "the candidate explicitly opts in (standout-quality exception).%s"
        % (int(top) // 1000, int(gap) // 1000, setting, floor // 1000, note))


def _load_opps():
    path = os.path.join(ROOT, "data", "opportunities.jsonl")
    with open(path, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def main():
    ap = argparse.ArgumentParser(description="Profile loader + executable comp screen.")
    ap.add_argument("--screen", metavar="OPP_ID")
    ap.add_argument("--screen-all", action="store_true")
    args = ap.parse_args()

    profile = load()

    if args.screen or args.screen_all:
        opps = _load_opps()
        if args.screen:
            opps = [o for o in opps if o["id"] == args.screen]
            if not opps:
                print("No opportunity with id %r" % args.screen)
                return 1
        elif args.screen_all:
            opps = [o for o in opps if o.get("status") in
                    ("active-pursuit", "needs-resolution", "in-motion")]

        print("Comp screen — %d opportunit%s" % (len(opps), "y" if len(opps) == 1 else "ies"))
        print("=" * 78)
        counts = {}
        for o in sorted(opps, key=lambda x: x["id"]):
            verdict, detail = screen_comp(o, profile)
            counts[verdict] = counts.get(verdict, 0) + 1
            print("  %-20s %s" % (verdict, o["id"][:52]))
            print("  %-20s %s" % ("", detail))
        print("\n" + "=" * 78)
        print("  " + " · ".join("%s=%d" % (k, v) for k, v in sorted(counts.items())))
        print("\n  Reminder: BELOW-FLOOR is a REMOVE unless the candidate explicitly opts in.")
        print("  NEEDS-COMMUTE-CHECK is never 'relocation by default' — that assumption")
        print("  is what mis-scored <an employer> on 2026-07-22.")
        print("  UNRESOLVED-SETTING is a question FOR THE EMPLOYER, never a silent pick —")
        print("  the posting declared two settings, and the pick would select the comp floor.")
        return 0

    print("THREE-LAYER MODEL")
    print("  1. user.json    USER DATA      — who the candidate is")
    print("  2. config.json  CONFIGURATION  — how the search behaves")
    print("  3. engine       scripts/ .claude/agents/ CLAUDE.md tasks/ docs/")
    print()
    ident = profile["identity"]
    print("Profile: %s — %s" % (ident["full_name"], ident["city"]))
    print("  available: %s" % ident["availability"])
    print("  mailboxes: %s" % ", ".join(m["address"] for m in profile["mailboxes"]))
    # The titles themselves, not a count: inbox-scan and board-sweeper build their search
    # queries from this line. A count made agents fall back to whatever titles they assumed.
    print("  titles:    %s" % "; ".join(profile["targets"]["titles"]))
    print("             org-structure-is-a-filter = %s"
          % (profile["targets"]["org_structure_is_not_a_filter"] is False))
    print("\n  comp tiers (base/yr):")
    for t in profile["compensation"]["tiers"]:
        flag = "" if t.get("basis_confirmed", True) else "   ⚠️ basis UNCONFIRMED — ask the candidate"
        print("    %-14s %s%sK%s" % (t["setting"], currency_symbol(), t["floor"] // 1000, flag))
    cm = comms()
    print("\n  communications (config.json is the single source):")
    print("    default sequence : %s  (sent TOGETHER)" % " + ".join(cm["default_sequence"]))
    print("    last resort      : %s" % ", ".join(cm["last_resort"]))
    print("    every message    : %s" % " + ".join(cm["message_requirements"]))
    for med, con in cm["constraints_by_medium"].items():
        lim = ("%d chars" % con["max_chars"]) if "max_chars" in con else ("%d words" % con["max_words"])
        print("    %-26s %s" % (med, lim))

    print("\n  rendered signature (config template x user data):")
    for line in email_signature():
        print("    %s" % line)
    print("\n  mailboxes (user.json is THE source):  %s" % ", ".join(mailboxes()))
    print("\n  Screen the live pipeline:  python3 scripts/profile.py --screen-all")
    return 0


if __name__ == "__main__":
    sys.exit(main())
