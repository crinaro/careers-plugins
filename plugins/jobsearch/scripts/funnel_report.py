#!/usr/bin/env python3
"""What's actually working: channel yield, application outcomes, outreach reply rates.

WHY THIS EXISTS (2026-07-21)
----------------------------
The candidate: "We should be tracking who we connected with & when i applied to analyze
what works and what doesnt."

Two things were in the way, both fixed the same day:

  1. Applications were stuffed into `outreach[]` with a person-shaped `to` field
     reading "<a recognizable employer> careers (direct ATS application)". Counting applications meant
     string-matching a free-text name, and an ATS submission looked identical to a
     networking note. They are different funnels -- an application's measure is
     "did anyone respond", outreach's is "did they reply" -- so they now live in
     separate arrays.
  2. `contacts[]` had been backfilled from markdown prose, so ~33 of its entries
     were placeholders ("N/A -- not pursued", "None found", "Not yet checked")
     sitting in the `name` field, every one typed `path_type: recruiter`. Averages
     over that are worse than no number at all.

This reads data/opportunities.jsonl and reports only what the data can actually
support. Where the sample is too small to mean anything, it says so rather than
printing a confident percentage over n=3.

    python3 scripts/funnel_report.py

Advisory only; always exits 0. Targets Python 3.9+, stdlib only.
"""

import argparse
import collections
import datetime
import json
import os
import sys

import os, sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root as _profile_root

ROOT = _profile_root()
# ⭐ TEST-ISOLATABLE (2026-08-05). test_says_insufficient_data_today froze a date-dependent
# snapshot of the live data into an assertion ("everything is still awaiting") — and went red in
# CI the moment replies arrived and old awaiting rows aged past the 14-day resolution line. The
# suite failed BECAUSE the search succeeded. A guard test must run against a FIXTURE, not against
# whatever the pipeline looks like today; same pattern as runlock.py's CLAUDESEARCH_LOCK_PATH.
DATA = os.environ.get("CLAUDESEARCH_DATA_DIR") or os.path.join(ROOT, "data")

# Below this, a percentage is noise dressed up as a finding.
MIN_SAMPLE = 5


def load(name):
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        return []
    out = []
    fh = open(path, encoding="utf-8")
    try:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    finally:
        fh.close()
    return out


def pct(n, d):
    """A rate, or an explicit refusal to compute one."""
    if d == 0:
        return "n/a"
    if d < MIN_SAMPLE:
        return "%d/%d (too few to rate)" % (n, d)
    return "%d/%d = %d%%" % (n, d, round(100.0 * n / d))


def days_since(d):
    if not d:
        return None
    try:
        y, m, dd = (int(x) for x in d.split("-"))
        return (datetime.date.today() - datetime.date(y, m, dd)).days
    except ValueError:
        return None


def rule(title):
    print("")
    print("=" * 72)
    print(title)
    print("=" * 72)


def recommend(cut_stats, comms_cfg):
    """Compare the CONFIGURED channel default against what the data actually shows.

    Two guards, both required before this will say a configured option is CONTRADICTED:
      * n >= MIN_SAMPLE **resolved** in each arm — sends are not evidence, and with 5-10
        samples a "winner" is usually noise;
      * a gap of at least MIN_GAP_PP percentage points.
    Both thresholds are PRINTED alongside the verdict so the recommendation is auditable
    rather than oracular.

    It NEVER edits config.json. It emits a proposal that the weekly review takes to the candidate —
    same rule search-strategist already follows.
    """
    MIN_GAP_PP = 20
    rule("RECOMMENDATION — is the configured default supported by the data?")
    default = comms_cfg.get("default_sequence", [])
    print("  configured default_sequence: %s" % " + ".join(default))
    print("  thresholds: n>=%d RESOLVED per arm AND a >=%d point gap. This script NEVER"
          % (MIN_SAMPLE, MIN_GAP_PP))
    print("  edits config.json — it proposes; the candidate decides at the weekly review.\n")

    ratable = {k: v for k, v in cut_stats.items() if v["resolved"] >= MIN_SAMPLE}
    if not ratable:
        for k in default:
            st = cut_stats.get(k, {"resolved": 0, "sent": 0})
            print("    INSUFFICIENT DATA  %-26s n=%d resolved (need %d) — no recommendation"
                  % (k, st["resolved"], MIN_SAMPLE))
        print("\n  Nothing is ratable yet. With the current pipeline the earliest meaningful")
        print("  read is once a first-touch arm reaches %d RESOLVED sends." % MIN_SAMPLE)
        return

    rates = {k: 100.0 * v["win"] / v["resolved"] for k, v in ratable.items()}
    best = max(rates, key=rates.get)
    for k in default:
        if k not in ratable:
            st = cut_stats.get(k, {"resolved": 0})
            print("    INSUFFICIENT DATA  %-26s n=%d resolved (need %d)"
                  % (k, st["resolved"], MIN_SAMPLE))
            continue
        gap = rates[best] - rates[k]
        if best != k and gap >= MIN_GAP_PP:
            print("    CONTRADICTED       %-26s %d%% vs %s at %d%% (gap %d pts >= %d)"
                  % (k, round(rates[k]), best, round(rates[best]), round(gap), MIN_GAP_PP))
            print("                       -> propose changing communications.default_sequence")
        else:
            print("    CONSISTENT         %-26s %d%% over n=%d resolved"
                  % (k, round(rates[k]), ratable[k]["resolved"]))


def main():
    ap = argparse.ArgumentParser(description="What's actually working.")
    ap.add_argument("--recommend", action="store_true",
                    help="Also compare the configured channel default against the data.")
    args = ap.parse_args()

    opps = load("opportunities.jsonl")
    companies = {c["id"]: c for c in load("companies.jsonl")}
    channels = {c["id"]: c for c in load("channels.jsonl")}

    print("Funnel report - %s" % datetime.date.today().isoformat())
    print("%d opportunities tracked" % len(opps))

    # ---- 1. Channel yield: which sources produce roles worth pursuing? ------
    rule("CHANNEL YIELD - which sources produce roles worth pursuing")
    seen = collections.defaultdict(lambda: {"total": 0, "pursued": 0})
    for o in opps:
        pursued = o.get("verdict") == "pursue"
        for s in o.get("sightings") or []:
            cid = s.get("channel_id") or "(none)"
            seen[cid]["total"] += 1
            if pursued:
                seen[cid]["pursued"] += 1
    if not seen:
        print("  No sightings recorded.")
    for cid, d in sorted(seen.items(), key=lambda kv: -kv[1]["total"]):
        label = channels.get(cid, {}).get("label", cid)
        print("  %-34s %3d sourced -> %s pursued" % (label[:34], d["total"], pct(d["pursued"], d["total"])))
    print("")
    print("  NOTE: most sightings carry channel_id 'legacy-import' - the 2026-07-20")
    print("  markdown backfill could not recover where those roles originally came")
    print("  from. Channel yield only becomes meaningful for roles sourced after")
    print("  that date. Reported as-is rather than quietly excluded.")

    # ---- 2. Applications ----------------------------------------------------
    rule("APPLICATIONS - when the candidate applied, how, and what came back")
    apps = []
    for o in opps:
        for a in o.get("applications") or []:
            apps.append((o, a))
    if not apps:
        print("  None recorded.")
    else:
        by_status = collections.Counter(a.get("status") for _, a in apps)
        by_method = collections.Counter(a.get("method") for _, a in apps)
        print("  %d application record(s)" % len(apps))
        print("  by status: " + ", ".join("%s=%d" % (k, v) for k, v in sorted(by_status.items())))
        print("  by method: " + ", ".join("%s=%d" % (k, v) for k, v in sorted(by_method.items())))
        print("")
        live = [(o, a) for o, a in apps if a.get("status") in ("submitted", "acknowledged")]
        heard = [(o, a) for o, a in apps if a.get("status") in ("advanced", "rejected")]
        print("  Response rate on submitted applications: %s" % pct(len(heard), len(live) + len(heard)))
        # Does sending a cover letter correlate with hearing back? Only answerable
        # because cover_letter_attached is recorded from the candidate rather than inferred.
        withcl = [a for _, a in apps if a.get("cover_letter_attached") is True]
        nocl = [a for _, a in apps if a.get("cover_letter_attached") is False]
        unknown = [a for _, a in apps if a.get("cover_letter_attached") is None]
        print("  Cover letter attached: %d yes / %d no / %d unrecorded" % (len(withcl), len(nocl), len(unknown)))
        if len(withcl) + len(nocl) < MIN_SAMPLE:
            print("    (too few recorded either way to compare against response rate yet)")
        print("")
        for o, a in sorted(apps, key=lambda t: (t[1].get("date") or "9999")):
            name = companies.get(o.get("company_id"), {}).get("name", o.get("company_id"))
            age = days_since(a.get("date"))
            age_s = "%3d days ago" % age if age is not None else "  no date  "
            print("  %-11s %-13s %-34s %s" % (a.get("date") or "(none)", a.get("status"), name[:34], age_s))
            if a.get("cover_letter"):
                print("              cover letter: %s" % a["cover_letter"])
            if a.get("notes"):
                print("              %s" % a["notes"][:150])

    # ---- 3. Outreach: who we connected with, and did they reply? -----------
    rule("OUTREACH - who the candidate connected with, and whether they replied")
    rows = []
    for o in opps:
        for x in o.get("outreach") or []:
            rows.append((o, x))
    if not rows:
        print("  None recorded.")
    else:
        sent = [(o, x) for o, x in rows if x.get("status") == "sent"]
        replied = [(o, x) for o, x in sent if x.get("outcome") in ("replied", "meeting-booked")]
        print("  %d outreach record(s); %d sent" % (len(rows), len(sent)))
        print("  Reply rate: %s" % pct(len(replied), len(sent)))
        print("")
        for o, x in sorted(sent, key=lambda t: t[1].get("date") or ""):
            name = companies.get(o.get("company_id"), {}).get("name", o.get("company_id"))
            age = days_since(x.get("date"))
            print("  %-11s %-14s %-26s %s" % (
                x.get("date") or "(none)", x.get("outcome") or "?", (x.get("to") or "")[:26], name[:26]))
            if age is not None and age >= 7 and x.get("outcome") == "awaiting":
                print("              ^ %d days silent" % age)

    # ---- 3b. WHAT ACTUALLY WORKS: cuts by medium / touch type / recipient ---
    #
    # THE BINDING DENOMINATOR IS *RESOLVED* SENDS, NOT SENDS. The 12 touches from
    # 2026-07-31 are all still `awaiting` — that is n=0 for rating, not n=12. Printing
    # "12 connection notes sent" and letting a reader treat it as evidence is exactly the
    # dishonest failure this section has to avoid.
    NO_RESPONSE_AFTER = 14   # an `awaiting` row older than this is COUNTED as resolved,
                             # but the row is NEVER mutated — the inference stays in the report

    sent_rows = [(o, x) for o, x in rows if x.get("status") == "sent"]

    def resolution(x):
        """resolved / awaiting / bounced / undeliverable-unknown."""
        if x.get("delivery") == "bounced":
            return "bounced"
        oc = x.get("outcome")
        if oc in ("replied", "meeting-booked", "accepted", "no-response", "declined"):
            return "resolved"
        age = days_since(x.get("date"))
        if oc == "awaiting" and age is not None and age >= NO_RESPONSE_AFTER:
            return "resolved-aged"
        # A pattern-inferred address with unknown delivery cannot distinguish silence from
        # a bounce. It is evidence of NOTHING either way and must not sit in a denominator.
        if (x.get("medium") or "").startswith("email") and \
           x.get("address_status") == "pattern-inferred" and x.get("delivery") == "unknown":
            return "undeliverable-unknown"
        return "awaiting"

    def is_win(x):
        return x.get("outcome") in ("replied", "meeting-booked", "accepted")

    def cut(field, title):
        buckets = {}
        for o, x in sent_rows:
            v = x.get(field) or "unknown"
            if v == "unknown":
                continue           # excluded from every rate; counted separately below
            b = buckets.setdefault(v, {"sent": 0, "resolved": 0, "win": 0,
                                       "awaiting": 0, "bounced": 0, "unverifiable": 0})
            b["sent"] += 1
            st = resolution(x)
            if st in ("resolved", "resolved-aged"):
                b["resolved"] += 1
                if is_win(x):
                    b["win"] += 1
            elif st == "bounced":
                b["bounced"] += 1
            elif st == "undeliverable-unknown":
                b["unverifiable"] += 1
            else:
                b["awaiting"] += 1
        skipped = sum(1 for _o, x in sent_rows if (x.get(field) or "unknown") == "unknown")
        print("\n  %s" % title)
        if not buckets:
            print("    (nothing classified yet)")
        for k, b in sorted(buckets.items(), key=lambda t: -t[1]["sent"]):
            rate = pct(b["win"], b["resolved"])
            print("    %-26s %2d sent · %2d resolved · %s"
                  % (k, b["sent"], b["resolved"], rate))
            detail = []
            if b["awaiting"]:
                detail.append("%d awaiting" % b["awaiting"])
            if b["bounced"]:
                detail.append("%d BOUNCED (excluded)" % b["bounced"])
            if b["unverifiable"]:
                detail.append("%d deliverability unverified (excluded)" % b["unverifiable"])
            if b["resolved"] < MIN_SAMPLE:
                detail.append("need %d more resolved to rate" % (MIN_SAMPLE - b["resolved"]))
            if detail:
                print("    %-26s   %s" % ("", " · ".join(detail)))
        if skipped:
            print("    (%d row(s) with %s='unknown' excluded from this cut. `scripts/reconcile.py`"
                  % (skipped, field))
            print("     recovered every one the MAILBOX could prove; what remains is LinkedIn-only")
            print("     traffic with no email notification — that needs a browser session.)")
        return buckets

    rule("OUTREACH — WHAT ACTUALLY WORKS")
    print("  A 'win' is replied · meeting-booked · accepted. The DENOMINATOR is RESOLVED sends,")
    print("  not sends: an `awaiting` row is not yet evidence. `awaiting` older than %d days is" % NO_RESPONSE_AFTER)
    print("  counted as resolved (computed here — the row itself is never mutated).")
    print("  BOUNCED rows are excluded entirely; a bounce that reads as a non-reply would poison")
    print("  every rate below. So are pattern-inferred emails whose delivery is unknown.")
    print("  ⚠️ medium/touch_type/recipient_role are only reliable for rows after 2026-08-02;")
    print("     earlier rows were backfilled from contemporaneous record where one existed.")

    medium_stats = cut("medium", "BY MEDIUM — the question the candidate actually asked")
    cut("touch_type", "BY TOUCH TYPE — a chase and a first touch are not the same bet")
    cut("recipient_role", "BY RECIPIENT — who is worth writing to")

    first = [(o, x) for o, x in sent_rows if x.get("touch_type") == "first-touch"]
    fres = [(o, x) for o, x in first if resolution(x) in ("resolved", "resolved-aged")]
    fwin = [(o, x) for o, x in fres if is_win(x)]
    print("\n  HEADLINE — FIRST TOUCHES ONLY (chases and replies inflate a pooled rate)")
    print("    %d first touches · %d resolved · %s" % (len(first), len(fres), pct(len(fwin), len(fres))))

    camps = {}
    for o, x in sent_rows:
        if x.get("campaign_id"):
            camps.setdefault(x["campaign_id"], []).append((o, x))
    if camps:
        print("\n  BY CAMPAIGN — a multi-touch push is one bet, not N independent ones")
        for cid, items in sorted(camps.items()):
            media = sorted({x.get("medium") for _o, x in items})
            people = len({(x.get("to") or "").split("(")[0].strip() for _o, x in items})
            wins = sum(1 for _o, x in items if is_win(x))
            print("    %-26s %d touches · %d people · %s · %d win(s)"
                  % (cid, len(items), people, "+".join(m or "?" for m in media), wins))

    if args.recommend:
        try:
            sys.path.insert(0, os.path.join(ROOT, "scripts"))
            import profile as _prof
            recommend(medium_stats, _prof.comms())
        except Exception as exc:
            print("\n  (could not load communications config: %s)" % exc)

    # ---- 4. Paths: which kind of contact actually converts? ----------------
    rule("CONTACT PATHS - which kind of connection converts")
    paths = collections.defaultdict(lambda: {"total": 0, "pursued": 0})
    for o in opps:
        for c in o.get("contacts") or []:
            pt = c.get("path_type") or "(unset)"
            paths[pt]["total"] += 1
            if o.get("verdict") == "pursue":
                paths[pt]["pursued"] += 1
    for pt, d in sorted(paths.items(), key=lambda kv: -kv[1]["total"]):
        print("  %-18s %3d contacts -> %s on roles we pursued" % (pt, d["total"], pct(d["pursued"], d["total"])))
    print("")
    print("  CAVEAT: path_type is unreliable on backfilled rows - the markdown")
    print("  import typed nearly everything 'recruiter' regardless of what it was.")
    print("  Trust this only for contacts added after 2026-07-21.")

    # ---- 5. What the data still can't answer -------------------------------
    rule("WHAT THIS STILL CANNOT TELL YOU")
    print("  - Time-to-first-response: needs a responded_on date on outreach and an")
    print("    outcome date on applications. The fields exist; almost nothing fills")
    print("    them yet, because replies get read in Gmail and never written back.")
    print("  - Whether a warm intro beats a cold application: only %d contacts survive"
          % sum(d["total"] for d in paths.values()))
    print("    the placeholder purge, and the pursued/not split is confounded by")
    print("    the candidate choosing which roles to pursue in the first place.")
    stage_counts = {}
    for o in opps:
        s = o.get("stage")
        stage_counts[s] = stage_counts.get(s, 0) + 1

    # ⚠️ 2026-08-02: this used to print, unconditionally, "`stage` never goes past
    # 'contacted' on any record" — a HARDCODED sentence that was true when written and
    # false by the time it was read. The 08/02 weekly review quoted it as a finding and
    # proposed a backfill for records that were already correct. A script asserting a
    # stale fact is worse than a tracker doing it, because the output reads as measured.
    # Now computed from the data every run.
    deep = {s: n for s, n in stage_counts.items()
            if s in ("screening", "interviewing", "offer") and n}
    if deep:
        detail = ", ".join("%s=%d" % (s, n) for s, n in sorted(deep.items()))
        print("  - Interview conversion: PARTIALLY measurable — %s." % detail)
        print("    Still thin, and `stage` is advanced by hand, so treat it as a floor")
        print("    on how far roles actually got, not a count.")
    else:
        print("  - Interview conversion: no record has advanced past 'contacted', so the")
        print("    funnel below that is unmeasured, not empty.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
