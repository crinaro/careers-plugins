#!/usr/bin/env python3
"""
Deterministic daily job-alert sweep — the reliable catch for board/aggregator
alert emails (Indeed, LinkedIn, Dice, CareerBuilder, Ladders).

WHY THIS EXISTS
---------------
Three consecutive daily runs (2026-07-22..24) had the `inbox-scan` agent (haiku)
report the job-alert emails "silent" when a daily Indeed alert had in fact
arrived squarely inside the window — each time the main session's own direct
`gmail_search` found it, and each time the candidate or the main session caught the miss,
not the agent. The lesson recorded in CLAUDE.md's token-discipline rule is that
a *daily, predictable* artifact like the Indeed alert is deterministic work that
belongs in a query, not a model summary. This script IS that query, promoted out
of a focus.md note (weekly-review proposal P3, approved 2026-07-27) into code so
it is never re-typed and never quietly skipped.

It does NOT judge fit — it surfaces the alert artifacts and their roles so the
run reads them itself. inbox-scan stays useful for human/recruiter mail and
meeting artifacts; this replaces it ONLY for the predictable alert digests.

Reuses the Gmail server's Keychain + IMAP plumbing (import-safe: that module
guards its stdio loop under __main__). Same coverage guarantee: EVERY configured
account by default, and an unreachable account is a LOUD banner, never a silent zero —
so this can never conclude "no alerts" from a one-mailbox view.

Usage:
    python3 scripts/alert_sweep.py                 # last 1 day (the daily default)
    python3 scripts/alert_sweep.py --days 2        # widen the window
    python3 scripts/alert_sweep.py --account you@example.com     # narrow (rare)

Python 3.9+. Standard library only.
"""

import argparse
import os
import sys

# scripts/ is on sys.path[0] when run as `python3 scripts/alert_sweep.py`,
# so the sibling server module imports cleanly. Its main() is __main__-guarded,
# so importing it starts no stdio loop.
try:
    from gmail_mcp_server import (
        Mailbox, configured_accounts, decode_header_value, CredentialError,
    )
except ImportError as exc:  # pragma: no cover - defensive
    sys.stderr.write(
        "Could not import gmail_mcp_server from the scripts/ dir: %s\n"
        "Run this as `python3 scripts/alert_sweep.py` from the repo root.\n" % exc)
    sys.exit(2)

# The alert artifacts we sweep for. Sender-first (robust to subject wording), plus
# a subject fallback for digests whose From varies. Extend as new sources appear —
# same discipline as the ATS-receipt domain list in CLAUDE.md.
ALERT_QUERY = (
    'from:indeed OR from:linkedin OR from:dice OR from:careerbuilder '
    'OR from:ladders OR from:ziprecruiter '
    'OR subject:("new jobs" OR "jobs for you" OR "job alert" OR "new job")'
)


def build_query(days):
    return "(%s) newer_than:%dd" % (ALERT_QUERY, days)


def sweep_account(account, query):
    """Return (rows, error). rows = list of (date, from, subject)."""
    rows = []
    try:
        with Mailbox(account) as mb:
            uids = mb.search(query)
            # Newest first, cap the fetch so a busy mailbox can't run long.
            for uid in reversed(uids[-40:]):
                msg = mb.fetch_headers(uid)
                if msg is None:
                    continue
                rows.append((
                    decode_header_value(msg.get("Date")),
                    decode_header_value(msg.get("From")),
                    decode_header_value(msg.get("Subject")),
                ))
        return rows, None
    except CredentialError as exc:
        return [], str(exc)
    except Exception as exc:  # network/IMAP hiccup — report, never swallow
        return [], "%s: %s" % (type(exc).__name__, exc)


def main():
    ap = argparse.ArgumentParser(description="Daily job-alert email sweep.")
    ap.add_argument("--days", type=int, default=1,
                    help="Look-back window in days (default 1 = the daily run).")
    ap.add_argument("--account", default=None,
                    help="Restrict to ONE account (default: all configured). "
                         "Narrowing forfeits the both-mailboxes guarantee — "
                         "the output says so loudly.")
    args = ap.parse_args()

    accounts = [args.account] if args.account else configured_accounts()
    query = build_query(args.days)

    print("Alert sweep — window: last %d day(s)" % args.days)
    print("Query: %s" % query)
    if args.account:
        print("!! NARROWED to a single account (%s) — NOT every configured mailbox. "
              "Do not conclude an alert is absent from this run alone." % args.account)
    print("=" * 72)

    total = 0
    incomplete = []
    for account in accounts:
        rows, err = sweep_account(account, query)
        print("\n[%s]" % account)
        if err:
            incomplete.append(account)
            print("  !! INCOMPLETE COVERAGE — %s" % err)
            print("  Results for this account are MISSING, not empty. "
                  "Do not conclude a message does not exist.")
            continue
        if not rows:
            print("  (no alert emails in window)")
            continue
        total += len(rows)
        for date, frm, subj in rows:
            print("  %-31s | %s" % ((date or "?")[:31], subj or "(no subject)"))
            print("  %-31s   from %s" % ("", frm or "?"))

    print("\n" + "=" * 72)
    if incomplete:
        print("!! %d account(s) could not be searched: %s"
              % (len(incomplete), ", ".join(incomplete)))
        print("   The count below is PARTIAL. Fix credentials before trusting a zero.")
    print("%d alert email(s) found across %d searchable account(s)."
          % (total, len(accounts) - len(incomplete)))
    print("\nNext: read each role, cross-check against data/opportunities.jsonl and "
          "its exclusion list, and hand genuinely-new roles to opportunity-researcher.")
    # Exit non-zero only when coverage was incomplete, so an unattended caller can
    # tell "clean, nothing found" from "could not check" — a zero is only trustworthy
    # when every account was reachable.
    return 3 if incomplete else 0


if __name__ == "__main__":
    sys.exit(main())
