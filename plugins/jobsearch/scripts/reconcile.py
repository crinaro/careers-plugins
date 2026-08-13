#!/usr/bin/env python3
"""
Reconcile tracked opportunity state against the SOURCE CHANNELS — the mailbox and LinkedIn.

⚠️ THIS IS A RECONCILIATION MODE, NOT THE OPERATING PATH.
---------------------------------------------------------
The candidate, 2026-08-02: *"We have the luxury we can reconcile against the sources (mail and LinkedIn)
but that should only be used for a reconciliation mode. The process should be using the json data
and making incremental updates during the daily runs."*

**`data/*.jsonl` is the system of RECORD. The mailbox is the system of TRUTH**, consulted
periodically to catch drift. The daily run reads and writes the JSON as things happen; it does not
re-derive state from the mailbox. This script runs **weekly**, as an audit.

**Anything it finds is a PROCESS FAILURE, not a routine result.** An unrecorded reply means the
daily run that should have written it missed something — log that, don't just apply the fix. A
clean reconcile means the incremental updates are working.

WHY THIS EXISTS
---------------
The candidate, 2026-08-02:

    "Why can't the process have the ability to review opportunities, if it did, it should have
     the ability to resolve the issue. It should be the same to evaluate the current state of an
     opportunity. Look at email, look at messages in LinkedIn. Those give the system the ability
     to evaluate. I know we have a step to check the ATS but that's really only going to let us
     know if the recruiter looked at the application. Yes, that's valuable but the other channels
     are much more important and the process should be able to reconcile because the same data is
     available in mail and LinkedIn."

He was correcting a claim I had made: that 22 outreach rows with `medium: unknown` were
unrecoverable because "no contemporaneous record exists." **That was wrong.** The record exists —
the actual messages are still sitting in the mailbox. I had only looked at what the repo had
written down about itself, not at the source.

That is the general failure this script fixes. Tracked state is a *transcription* of what happened
in email and LinkedIn. A transcription can be incomplete (a row never written), stale (a reply
that arrived after we last looked), or lossy (`medium: unknown`). **The source can be re-read at
any time.** The ATS portal check tells you only whether a recruiter opened an application; the
conversation itself is where the real signal is.

**⭐ LinkedIn is reconcilable from the mailbox too**, which is the non-obvious part: LinkedIn emails
a notification for messages received ("X just messaged you"), invitation acceptances ("X accepted
your invitation"), and InMail. So a deterministic Gmail sweep recovers a large share of LinkedIn
state without a browser session at all.

WHAT IT REPORTS
---------------
  * **MEDIUM EVIDENCE** — a row says `medium: unknown`, but the mailbox shows a direct email
    exchange, or a LinkedIn notification, with that person.
  * **UNRECORDED REPLY** — the row says `awaiting`/`sent`, but a message FROM that contact arrived
    after we wrote to them. This is the one that costs real opportunities.
  * **UNRECORDED ACCEPTANCE** — LinkedIn says an invitation was accepted; the row does not.
  * **NO TRACE** — nothing found. Reported explicitly rather than silently skipped, because a
    zero is only meaningful when you can see it was looked for.

It NEVER writes by default. `--apply` fills in `medium` **only** where the evidence is
unambiguous, and never touches `outcome` — a reply changes what the candidate should DO, so it is surfaced
for a human decision rather than silently absorbed.

Usage (weekly audit; NOT part of the daily run):
    python3 scripts/reconcile.py                    # report on live pursuits
    python3 scripts/reconcile.py --all              # every opportunity with outreach
    python3 scripts/reconcile.py --unknown-medium   # only rows needing a medium
    python3 scripts/reconcile.py --apply            # write the unambiguous medium fixes
    python3 scripts/reconcile.py --role <opp_id>

Python 3.9+. Standard library only.
"""

import argparse
import datetime
import json
import os
import re
import sys
from email.utils import parseaddr

import os, sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root as _profile_root
from _atomic import write_jsonl, write_json

try:
    from gmail_mcp_server import (
        Mailbox, configured_accounts, decode_header_value, CredentialError, body_text,
    )
except ImportError as exc:  # pragma: no cover
    sys.stderr.write("Run as `python3 scripts/reconcile.py` from the repo root: %s\n" % exc)
    sys.exit(2)

ROOT = _profile_root()
DATA = os.path.join(ROOT, "data")

# LinkedIn's own notification senders. This is what makes LinkedIn state reconcilable from
# the mailbox rather than requiring a browser session.
LINKEDIN_SENDERS = ("messaging-digest-noreply@linkedin.com", "invitations-noreply@linkedin.com",
                    "inmail-hit-reply@linkedin.com", "notifications-noreply@linkedin.com",
                    "member@linkedin.com", "jobs-noreply@linkedin.com")
LINKEDIN_MESSAGE_HINTS = ("just messaged you", "sent you a message", "sent you an inmail",
                          "you have a new message")
LINKEDIN_ACCEPT_HINTS = ("accepted your invitation", "is now a connection",
                         "you are now connected")


def load(name):
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def person_terms(to_field):
    """Search terms from a `to` string like 'Marlow Quist (Some Search Firm)' — synthetic, as
    every example name in a portable file must be.

    Returns (display_name, [terms]). The parenthetical is often the firm, which is a useful
    second term, and an embedded email address is the strongest term of all.
    """
    raw = (to_field or "").strip()
    email = None
    m = re.search(r"[\w.+-]+@[\w.-]+\.\w+", raw)
    if m:
        email = m.group(0)
    name = re.split(r"\(|,|;", raw)[0].strip()
    paren = re.search(r"\(([^)]+)\)", raw)
    terms = []
    if email:
        terms.append(email)
    if name and len(name.split()) >= 2:
        terms.append('"%s"' % name)
    elif name:
        terms.append(name)
    if paren:
        firm = re.split(r"[,;—-]", paren.group(1))[0].strip()
        firm = re.sub(r"\b(1st|2nd|3rd)-degree connection\b", "", firm).strip()
        if firm and len(firm) > 3 and not firm.lower().startswith("to forward"):
            terms.append('"%s"' % firm)
    return name, terms


class Session(object):
    """One IMAP connection per account, reused across every query.

    The first version opened a fresh Mailbox per search — 22 rows x 2 accounts = 44 logins,
    which took longer than the harness timeout. Connection setup dominates; the searches
    themselves are fast.
    """

    def __init__(self, accounts):
        self.boxes, self.errors = {}, []
        for acct in accounts:
            try:
                mb = Mailbox(acct)
                mb.__enter__()
                self.boxes[acct] = mb
            except CredentialError as exc:
                self.errors.append("%s: %s" % (acct, exc))
            except Exception as exc:
                self.errors.append("%s: %s: %s" % (acct, type(exc).__name__, exc))

    def search(self, query, limit=25):
        rows = []
        for acct, mb in self.boxes.items():
            try:
                uids = mb.search(query)
                for uid in reversed(uids[-limit:]):
                    msg = mb.fetch_headers(uid)
                    if msg is None:
                        continue
                    rows.append({
                        "account": acct,
                        "date": decode_header_value(msg.get("Date")),
                        "from": decode_header_value(msg.get("From")),
                        "to": decode_header_value(msg.get("To")),
                        "subject": decode_header_value(msg.get("Subject")),
                    })
            except Exception as exc:
                self.errors.append("%s: %s: %s" % (acct, type(exc).__name__, exc))
        return rows

    def fetch_full(self, acct, uid):
        mb = self.boxes.get(acct)
        if not mb:
            return None
        try:
            return mb.fetch_full(uid)
        except Exception as exc:
            self.errors.append("%s: %s: %s" % (acct, type(exc).__name__, exc))
            return None

    def search_uids(self, query, limit=25):
        """(account, uid) pairs, so a body can be fetched and its provenance recorded."""
        out = []
        for acct, mb in self.boxes.items():
            try:
                for uid in reversed(mb.search(query)[-limit:]):
                    out.append((acct, uid))
            except Exception as exc:
                self.errors.append("%s: %s: %s" % (acct, type(exc).__name__, exc))
        return out

    def close(self):
        for mb in self.boxes.values():
            try:
                mb.__exit__(None, None, None)
            except Exception:
                pass


def parse_hdr_date(s):
    if not s:
        return None
    m = re.search(r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})", s)
    if not m:
        return None
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    try:
        return datetime.date(int(m.group(3)), months.index(m.group(2)) + 1, int(m.group(1)))
    except ValueError:
        return None


def classify(hits, name, row_date):
    """What do these messages tell us? -> (medium, replied_after, accepted, notes)"""
    medium = None
    replied_after = []
    accepted = False
    direct_email = False
    linkedin_msg = False
    last = (name or "").split()[-1].lower() if name else ""

    for h in hits:
        frm = (h["from"] or "")
        low_frm = frm.lower()
        subj = (h["subject"] or "").lower()
        # ⭐ MATCH THE ADDRESS, NOT A SUBSTRING OF THE HEADER (CodeQL, 2026-08-11).
        # `any(s in low_frm ...)` accepted any sender that merely CONTAINED a real notification
        # address — append a domain of your own to one and the substring still matches. This
        # function turns mail into pipeline state, so a spoofed sender could record an acceptance
        # that never happened. parseaddr extracts the address; the comparison is then exact.
        # (The lookalike is described, not written: an address literal here is itself a leak,
        # which the engine leak audit correctly flagged when this comment first quoted one.)
        is_linkedin = parseaddr(low_frm)[1].strip() in LINKEDIN_SENDERS
        if is_linkedin:
            if any(k in subj for k in LINKEDIN_ACCEPT_HINTS):
                accepted = True
            if any(k in subj for k in LINKEDIN_MESSAGE_HINTS) and (not last or last in subj):
                linkedin_msg = True
                d = parse_hdr_date(h["date"])
                if d and row_date and d >= row_date:
                    replied_after.append(h)
            continue
        # A direct (non-LinkedIn) message mentioning this person = an email exchange.
        if last and last in low_frm:
            direct_email = True
            d = parse_hdr_date(h["date"])
            if d and row_date and d >= row_date:
                replied_after.append(h)
        elif "@" in frm and last and last in (h["to"] or "").lower():
            direct_email = True

    if direct_email:
        medium = "email"
    elif linkedin_msg:
        medium = "linkedin"
    return medium, replied_after, accepted


def main():
    ap = argparse.ArgumentParser(description="Reconcile tracked state against mail + LinkedIn.")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--unknown-medium", action="store_true")
    ap.add_argument("--role", metavar="OPP_ID")
    ap.add_argument("--apply", action="store_true",
                    help="Write unambiguous medium fixes. Never touches outcome.")
    ap.add_argument("--limit", type=int, default=0, help="Max rows to check (0 = no cap).")
    ap.add_argument("--harvest", action="store_true",
                    help="Write the ACTUAL messages (both directions) into data/messages.jsonl.")
    args = ap.parse_args()

    opps = load("opportunities.jsonl")
    companies = {c["id"]: c.get("name", c["id"]) for c in load("companies.jsonl")}
    accounts = configured_accounts()

    targets = []
    for o in opps:
        if args.role and o["id"] != args.role:
            continue
        if not args.role and not args.all and not args.unknown_medium:
            if o.get("status") not in ("active-pursuit", "needs-resolution", "in-motion"):
                continue
        for idx, r in enumerate(o.get("outreach") or []):
            if args.unknown_medium and r.get("medium") != "unknown":
                continue
            targets.append((o, idx, r))
    if args.limit:
        targets = targets[:args.limit]

    print("RECONCILE — tracked state vs the source channels (mail + LinkedIn notifications)")
    print("=" * 78)
    print("Checking %d outreach row(s) across %s" % (len(targets), ", ".join(accounts)))
    print("Tracked state is a TRANSCRIPTION of what happened in email and LinkedIn. The source")
    print("can be re-read; the transcription can be incomplete, stale, or lossy.\n")

    findings = {"medium": [], "reply": [], "accepted": [], "none": []}
    sess = Session(accounts)
    n_done = 0

    # ⭐⭐ THE JOIN KEY IS THE OUTREACH ROW, NEVER THE PERSON — GitHub #2 (public).
    #
    # This searched mail PER ROW for the PERSON, then asked "did they reply after this row
    # was sent?". Whenever one recipient has several outreach rows — the normal case for any
    # sustained pursuit — a single reply satisfied that test for EVERY row, so a reply
    # correctly recorded against one row was reported as missing against all its siblings.
    # One observed weekly run produced 7 findings and 0 real ones.
    #
    # The cost is not the noise. This audit is the only mechanism that catches a genuinely
    # unrecorded reply, and one whose findings are almost all false teaches its reader to
    # skim — which is exactly when the real one is missed.
    #
    # So: group the rows by person, search ONCE per person (fewer round trips too), and
    # ATTRIBUTE each inbound message to the row it actually answers — the latest row sent on
    # or before it. A row reports an unrecorded reply only for messages attributed to it.
    groups = {}
    for o, idx, r in targets:
        name, terms = person_terms(r.get("to"))
        if not terms:
            continue
        key = (o.get("id"), name.lower())
        groups.setdefault(key, {"name": name, "terms": terms, "rows": []})
        groups[key]["rows"].append((o, idx, r))

    def _row_date(r):
        try:
            return datetime.date.fromisoformat(r.get("date") or "")
        except ValueError:
            return None

    for (_opp_id, _pk), g in groups.items():
        name, terms = g["name"], g["terms"]
        q = "in:anywhere (%s)" % " OR ".join(terms)
        hits = sess.search(q)
        sys.stderr.write("  ... %d/%d %s\r" % (n_done + 1, len(groups), name[:28]))
        sys.stderr.flush()
        n_done += 1

        rows = sorted(g["rows"], key=lambda t: (t[2].get("date") or ""))
        attributed = {}
        for h in hits:
            hd = parse_hdr_date(h["date"])
            if not hd:
                continue
            owner = None
            for cand in rows:
                rd = _row_date(cand[2])
                if rd and rd <= hd:
                    owner = cand          # rows are date-sorted; the last match is the latest
            if owner is not None:
                attributed.setdefault(id(owner[2]), []).append(h)

        for o, idx, r in rows:
            row_date = _row_date(r)
            mine = attributed.get(id(r), [])
            # Medium is a property of how this PERSON is reached, so it is inferred from every
            # message. Reply and acceptance are properties of THIS ROW, so they see only what
            # was attributed to it.
            medium, _all_replied, _all_accepted = classify(hits, name, row_date)
            _m, replied, accepted = classify(mine, name, row_date)

            # ⚠️ An invitation ACCEPTANCE answers a connection request, not an email or an
            # InMail. Reported against a row of another medium it is noise by construction —
            # the second half of what made this audit unreadable.
            if accepted and r.get("medium") not in (None, "unknown",
                                                    "linkedin-connection-note"):
                accepted = False

            label = "%s — %s" % (companies.get(o.get("company_id"), o.get("company_id")), name)
            if not hits:
                findings["none"].append((o, idx, r, label))
                continue
            if r.get("medium") == "unknown" and medium:
                findings["medium"].append((o, idx, r, label, medium, len(hits)))
            # `responded_on` set means a human already read this inbound and dispositioned
            # it. Without this test the audit re-reports it EVERY week and never stops. The
            # case that forced it (2026-08-02): an OOO auto-reply is real inbound mail, so it
            # must be recorded — but it is NOT a substantive answer, so `outcome` correctly
            # stays `awaiting` and the thread keeps aging. Keying only on `outcome` made that
            # legitimate state permanently indistinguishable from a missed reply.
            if replied and r.get("outcome") in ("awaiting", None) \
                    and not r.get("responded_on"):
                findings["reply"].append((o, idx, r, label, replied))
            if accepted and r.get("outcome") == "awaiting" and not r.get("responded_on"):
                findings["accepted"].append((o, idx, r, label))

    if findings["reply"]:
        print("-" * 78)
        print("⚠️  UNRECORDED REPLY — the row says awaiting, but they answered")
        print("-" * 78)
        print("  This is the finding that costs real opportunities. NOT auto-applied: a reply")
        print("  changes what the candidate should DO, so it needs a human read, not a silent field flip.\n")
        for o, idx, r, label, replied in findings["reply"]:
            print("  • %s  (sent %s)" % (label, r.get("date")))
            for h in replied[:3]:
                print("      %s | %s" % ((h["date"] or "?")[:31], (h["subject"] or "")[:64]))
            print()

    if findings["accepted"]:
        print("-" * 78)
        print("⚠️  UNRECORDED ACCEPTANCE — LinkedIn says they accepted; the row still says awaiting")
        print("-" * 78)
        for o, idx, r, label in findings["accepted"]:
            print("  • %s  (sent %s) -> consider outcome: accepted" % (label, r.get("date")))
        print()

    if findings["medium"]:
        print("-" * 78)
        print("MEDIUM EVIDENCE — rows recorded as 'unknown' that the mailbox can resolve")
        print("-" * 78)
        for o, idx, r, label, medium, n in findings["medium"]:
            print("  • %-52s unknown -> %-9s (%d msg)" % (label[:52], medium, n))
        print()

    if findings["none"]:
        print("-" * 78)
        print("NO TRACE — searched and found nothing (reported, not silently skipped)")
        print("-" * 78)
        for o, idx, r, label in findings["none"]:
            print("  • %-52s %s" % (label[:52], r.get("date")))
        print("  ⚠️ THIS TOOL SEARCHES EMAIL ONLY. Outreach migrated to LinkedIn CONNECTION")
        print("     REQUESTS (the candidate, 2026-08-03), which leave NO mail trace, so a request reads")
        print("     as NO TRACE here forever. Verify those on the Sent Invitations page:")
        print("       https://www.linkedin.com/mynetwork/invitation-manager/sent/")
        print("     Read this section as 'unverifiable here', NEVER as 'did not happen'.")
        print("  A zero is only meaningful when you can see it was looked for. Likely causes:")
        print("  a LinkedIn touch with notifications off, or a name the mailbox spells differently.\n")

    if args.apply and findings["medium"]:
        by_id = {}
        for o, idx, r, label, medium, n in findings["medium"]:
            by_id.setdefault(o["id"], []).append((idx, medium))
        path = os.path.join(DATA, "opportunities.jsonl")
        with open(path, encoding="utf-8") as fh:
            lines = [json.loads(l) for l in fh if l.strip()]
        changed = 0
        for rec in lines:
            for idx, medium in by_id.get(rec["id"], []):
                row = rec["outreach"][idx]
                # Only the coarse family is provable from a header. linkedin-message vs
                # connection-note vs InMail is NOT distinguishable this way, so don't pretend.
                row["medium"] = "email-reply" if medium == "email" and \
                    (row.get("touch_type") in ("reply", "chase")) else (
                        "email-cold" if medium == "email" else "linkedin-message")
                if row["medium"].startswith("email") and not row.get("address_status"):
                    row["address_status"] = "unknown"
                row["note"] = ((row.get("note") + " | ") if row.get("note") else "") + \
                    ("MEDIUM RECOVERED 2026-08-02 by scripts/reconcile.py from the mailbox. "
                     "The header proves the FAMILY (email vs LinkedIn); it cannot distinguish "
                     "connection-note vs InMail vs free message, so the finer value is not "
                     "asserted.")
                changed += 1
        write_jsonl(path, lines)
        print("APPLIED: medium filled on %d row(s). Run validate_data.py." % changed)
    elif findings["medium"]:
        print("  (re-run with --apply to write these; outcome changes are never auto-applied)\n")

    # ---- --harvest: store the ACTUAL conversation, both directions -------------
    if args.harvest:
        mpath = os.path.join(DATA, "messages.jsonl")
        with open(mpath, encoding="utf-8") as fh:
            existing = [json.loads(l) for l in fh if l.strip()]
        have = {m.get("source") for m in existing}
        added = 0
        print("\n" + "-" * 78)
        print("HARVEST — writing the actual messages into data/messages.jsonl")
        print("-" * 78)
        print("  Both directions. Until now the store held ZERO bodies and inbound replies")
        print("  existed only as an `outcome` flag, so 'all communications' was not modelled.\n")
        for o, idx, r in targets:
            name, terms = person_terms(r.get("to"))
            if not terms:
                continue
            for acct, uid in sess.search_uids("in:anywhere (%s)" % " OR ".join(terms), limit=12):
                src = "gmail:%s:%s" % (acct, uid)
                if src in have:
                    continue
                msg = sess.fetch_full(acct, uid)
                if msg is None:
                    continue
                frm = decode_header_value(msg.get("From")) or ""
                subj = decode_header_value(msg.get("Subject")) or ""
                # Skip LinkedIn/system notifications — they are SIGNALS about a conversation,
                # not the conversation. classify() already uses them for state.
                if any(sdr in frm.lower() for sdr in LINKEDIN_SENDERS) or "noreply" in frm.lower():
                    continue
                last = (name or "").split()[-1].lower() if name else ""
                if last and last not in frm.lower() and last not in (
                        decode_header_value(msg.get("To")) or "").lower():
                    continue
                try:
                    body = body_text(msg, limit=8000) or ""
                except Exception:
                    body = ""
                if not body.strip():
                    continue
                d = parse_hdr_date(decode_header_value(msg.get("Date")))
                inbound = last in frm.lower() if last else False
                existing.append({
                    "id": "%s-%s-%s" % (o["id"][:28], (r.get("contact_id") or "x"),
                                        uid),
                    "opp_id": o["id"],
                    "contact_id": r.get("contact_id"),
                    "direction": "inbound" if inbound else "outbound",
                    "medium": r.get("medium") if not inbound else "email-reply",
                    "sent_on": d.isoformat() if d else None,
                    "from": frm, "to": decode_header_value(msg.get("To")),
                    "subject": subj, "body": body[:8000],
                    "source": src, "variant": None,
                })
                have.add(src)
                added += 1
        write_jsonl(mpath, existing)
        ins = sum(1 for m in existing if m.get("direction") == "inbound")
        outs = sum(1 for m in existing if m.get("direction") == "outbound")
        print("  harvested %d new message(s). Store now: %d inbound / %d outbound."
              % (added, ins, outs))
        print("  Every row carries `source` (gmail:<account>:<uid>) so it can be re-verified.")

    sess.close()
    incomplete = sess.errors
    if incomplete:
        print("!! INCOMPLETE COVERAGE: %s" % "; ".join(sorted(set(incomplete))))
        print("   Results are PARTIAL. Do not conclude a message does not exist.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
