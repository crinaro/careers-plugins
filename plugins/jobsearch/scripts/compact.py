#!/usr/bin/env python3
"""
RETENTION — the periodic cleanup for append-only data.

WHY THIS EXISTS
---------------
The candidate, 2026-08-02: *"With append only, how is the data periodically cleaned up?"*

A fair question, and the honest answer at the time was **nothing did**. `inbox.py --prune` existed
but no run ever called it, and `log.md` had reached **956 KB / 140,000 words** with no retention
path whatsoever — re-committed in full on every one of the (now 2-hourly) runs.

Append-only is the right shape for correctness — it is what removed the clobber race on
`data/inbox.jsonl`, where a whole-file rewrite could silently destroy a concurrent append. But
append-only without compaction is just a leak with good intentions. **Compaction is the other half
of the design, and it belongs on a schedule.**

## The policy, per artifact — and the reasoning, not just the number

| artifact | policy | why |
|---|---|---|
| `data/inbox.jsonl` | drop **acked** findings older than 30d | A handled finding has already been written into real state; the queue row is a receipt, not the record |
| `log.md` | roll entries older than **120d** into `archive/log_<year>.md` | Runs read the TAIL, never the whole file; but git re-commits all 956 KB every run |
| `data/messages.jsonl` | **never pruned** | It IS the evidence base for "which communications work". Deleting it would delete the answer |
| `data/opportunities.jsonl` | **never pruned** | Closed roles stay: `sightings[]` feed channel yield, and every `company_id`/`channel_id` FK must keep resolving |
| `docs/incident_archive.md`, `process_archive.md` | **never pruned** | Already the cold tier. Pruning cold storage is just deletion |

**Nothing here deletes anything that is still evidence.** Only receipts and old narrative move, and
narrative moves rather than disappearing.

## Safety

Rewriting a file races any concurrent append, which is the exact bug this whole design exists to
avoid. So **this takes the write lock and refuses if it cannot get it.**

⚠️ **`--holding-lock` exists because the original design contradicted itself and never once ran.**
This docstring used to say it "is invoked from the weekly review, which already holds the lock" —
and the weekly review DOES hold it, which made `--take` fail every single time. Verified
2026-08-02 by running it mid-review: exit 1, `REFUSED`, zero bytes compacted, and the refusal text
blamed a phantom concurrent writer. **The one caller it was written for could never call it.**
So a caller that already owns the lock passes `--holding-lock`: compaction proceeds without taking
it, and does NOT release it — the lock stays the caller's to release, which matters because the
weekly review still has a commit to make afterwards. Without a lock held by anyone, the flag is
refused rather than treated as a bypass.

Usage:
    python3 scripts/compact.py --dry-run       # what would move, and how much
    python3 scripts/compact.py                 # do it (takes and releases the lock itself)
    python3 scripts/compact.py --holding-lock  # caller already owns the lock (the weekly review)

Python 3.9+. Standard library only.
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys

import os, sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root as _profile_root
ENGINE_SCRIPTS = os.path.dirname(os.path.realpath(__file__))

ROOT = _profile_root()
INBOX = os.path.join(ROOT, "data", "inbox.jsonl")
LOG = os.path.join(ROOT, "log.md")
ARCHIVE = os.path.join(ROOT, "archive")

INBOX_KEEP_DAYS = 30
LOG_KEEP_DAYS = 120
# The size backstop. 120 days cannot bind a repo that started 2026-07-07, so age alone left
# log.md growing unbounded (measured: 78.2 MB of .git was old log.md blobs). The floor is the
# safety rail — nothing inside it is ever rolled, however big the file gets.
LOG_MIN_KEEP_DAYS = 30
LOG_MAX_BYTES = 400 * 1024
# ⭐ THE FLOOR IS A COUNT, NOT ONLY AN AGE — and that is the fix for a young repo.
# Measured 2026-08-02: log.md 1.0 MB (2.5x budget), .git 165 MB, and compact.py could move
# **0 of 188 entries**, because the repo was 26 days old and EVERY entry was newer than the
# 30-day age floor. So the size backstop — added precisely because "age alone cannot touch a
# young repo" — was itself gated behind an age rule and could never fire either.
# What runs actually need is the TAIL, which is a COUNT. Keep the last N entries however new
# or old they are; below that, the size budget may roll them.
LOG_MIN_KEEP_ENTRIES = 40


def human(n):
    return "%.0f KB" % (n / 1024.0) if n < 1024 * 1024 else "%.1f MB" % (n / 1048576.0)


def compact_inbox(cutoff, dry):
    """Drop acked findings older than the cutoff. Keeps everything still pending."""
    if not os.path.exists(INBOX):
        return 0, 0
    with open(INBOX, encoding="utf-8") as fh:
        rows = [json.loads(l) for l in fh if l.strip()]
    # Replay so an ack record is understood, then keep pending + recent.
    acked = {r["id"] for r in rows if r.get("kind") == "_ack"}
    keep = []
    for r in rows:
        rid = r.get("id")
        if r.get("kind") == "_ack":
            # keep the ack only if its finding is kept
            continue
        old = (r.get("found_at") or "") < cutoff
        if rid in acked and old:
            continue
        keep.append(r)
    kept_ids = {r.get("id") for r in keep}
    keep += [r for r in rows if r.get("kind") == "_ack" and r.get("id") in kept_ids]
    dropped = len(rows) - len(keep)
    if dropped and not dry:
        with open(INBOX, "w", encoding="utf-8") as fh:
            for r in keep:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return dropped, len(rows)


def entry_date(e):
    m = re.match(r"## (\d{4})-(\d{2})-(\d{2})", e)
    return "%s-%s-%s" % m.groups() if m else None


def compact_log(cutoff, dry, floor_cutoff=None, max_bytes=None):
    """Roll log entries into archive/log_<year>.md — by AGE, then by SIZE.

    `log.md` is append-only BY RULE — past entries are never edited. Moving a whole entry to a
    dated archive is not an edit; it is the same treatment `process_archive.md` already gives
    `focus.md`. The entries stay readable and stay in git history either way.

    TWO RULES, because age alone did not solve the problem it was written for. The age rule
    (`cutoff`, 120d) keeps recent narrative readable. But the stated motivation was that git
    re-commits the whole file on every 2-hourly run, and **that cost is real and was measured,
    not assumed: on 2026-08-02, `git rev-list --objects HEAD -- log.md | git cat-file
    --batch-check` reported 414 blob versions of log.md occupying 78.2 MB — just under HALF of
    the 163 MB .git.** A 120-day rule cannot touch a 26-day-old repo, so the policy as first
    written would have compacted nothing until ~November while that 78 MB kept growing.

    So: after the age pass, if the file still exceeds `max_bytes`, keep rolling the OLDEST
    remaining entries until it fits — but NEVER one newer than `floor_cutoff`. The floor wins
    over the budget. An over-budget file whose entries are all recent is left over-budget and
    reported honestly, rather than eating narrative the runs may still need.
    """
    if not os.path.exists(LOG):
        return 0, 0
    with open(LOG, encoding="utf-8") as fh:
        text = fh.read()
    parts = re.split(r"(?m)^(?=## \d{4}-\d{2}-\d{2})", text)
    head, entries = parts[0], parts[1:]
    keep, moved = [], {}
    for e in entries:
        d = entry_date(e)
        if d and d < cutoff:
            moved.setdefault(d[:4], []).append(e)
        else:
            keep.append(e)

    # SIZE pass — oldest first, stopping at the recency floor.
    if max_bytes and floor_cutoff:
        def size():
            return len(("".join([head] + keep)).encode("utf-8"))
        while keep and size() > max_bytes:
            if len(keep) <= LOG_MIN_KEEP_ENTRIES:
                break                       # the COUNT floor — runs read the tail
            d = entry_date(keep[0])
            if not d or d >= floor_cutoff:
                # The AGE floor yields once the count floor is satisfied. Keeping 40 entries
                # is the real guarantee; refusing to roll entry 41 because it is 26 days old
                # is what left a 1.0 MB log and a 165 MB .git untouched.
                if len(keep) > LOG_MIN_KEEP_ENTRIES:
                    moved.setdefault(d[:4], []).append(keep.pop(0))
                    continue
                break
            moved.setdefault(d[:4], []).append(keep.pop(0))

    n_moved = sum(len(v) for v in moved.values())
    if n_moved and not dry:
        os.makedirs(ARCHIVE, exist_ok=True)
        for year, es in moved.items():
            path = os.path.join(ARCHIVE, "log_%s.md" % year)
            new = not os.path.exists(path)
            with open(path, "a", encoding="utf-8") as fh:
                if new:
                    fh.write("# Run log — %s (archived)\n\n"
                             "Rolled out of `log.md` by `scripts/compact.py`. **Nothing here is "
                             "live.** Runs read the TAIL of `log.md`, never the whole file; this "
                             "exists so git stops re-committing years of narrative on every "
                             "2-hourly run.\n\n" % year)
                fh.write("".join(es))
        with open(LOG, "w", encoding="utf-8") as fh:
            fh.write(head + "".join(keep))
    return n_moved, len(entries)


def main():
    ap = argparse.ArgumentParser(description="Periodic compaction of append-only data.")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--holding-lock", action="store_true",
                    help="The CALLER already owns the write lock (the weekly review). Proceed "
                         "without taking it, and do not release it.")
    args = ap.parse_args()

    today = datetime.date.today()
    inbox_cut = (today - datetime.timedelta(days=INBOX_KEEP_DAYS)).isoformat()
    log_cut = (today - datetime.timedelta(days=LOG_KEEP_DAYS)).isoformat()
    log_floor = (today - datetime.timedelta(days=LOG_MIN_KEEP_DAYS)).isoformat()

    print("COMPACTION — %s%s" % (today.isoformat(), "  (dry run)" if args.dry_run else ""))
    print("=" * 72)

    took_lock = False
    if not args.dry_run:
        lock = subprocess.run([sys.executable, os.path.join(ENGINE_SCRIPTS, "runlock.py"),
                               "--take", "compaction"], capture_output=True, text=True)
        if lock.returncode == 0:
            took_lock = True
        elif args.holding_lock:
            # The caller (the weekly review) owns it. Proceed, but leave the release to them —
            # they still have a commit to make after this returns.
            print("  Lock is held and --holding-lock was passed: the CALLER owns it. Compacting")
            print("  under the caller's lock; it stays theirs to release.")
        else:
            print("  REFUSED — a writer holds the lock. Compaction REWRITES files, which would")
            print("  race a concurrent append. That race is the exact bug append-only removed;")
            print("  do not work around it. Try again when the lock is free.")
            return 1
    elif args.holding_lock:
        print("  (--holding-lock is a no-op with --dry-run; nothing is rewritten.)")
    try:
        before_i = os.path.getsize(INBOX) if os.path.exists(INBOX) else 0
        before_l = os.path.getsize(LOG) if os.path.exists(LOG) else 0

        d, tot = compact_inbox(inbox_cut, args.dry_run)
        print("  inbox.jsonl : %d of %d row(s) %s (acked, older than %dd)"
              % (d, tot, "would drop" if args.dry_run else "dropped", INBOX_KEEP_DAYS))
        print("                a handled finding is a RECEIPT — the real state was already written")

        m, tote = compact_log(log_cut, args.dry_run, log_floor, LOG_MAX_BYTES)
        print("  log.md      : %d of %d entr%s %s to archive/log_<year>.md"
              % (m, tote, "y" if tote == 1 else "ies",
                 "would move" if args.dry_run else "moved"))
        print("                rules: older than %dd, THEN oldest-first over %s"
              % (LOG_KEEP_DAYS, human(LOG_MAX_BYTES)))
        if not args.dry_run:
            after_l = os.path.getsize(LOG) if os.path.exists(LOG) else 0
            print("                %s -> %s" % (human(before_l), human(after_l)))
            if after_l > LOG_MAX_BYTES:
                print("                ⚠️  STILL OVER BUDGET (%s) — every remaining entry is"
                      % human(LOG_MAX_BYTES))
                print("                newer than the %dd floor, and the floor wins. Not a"
                      % LOG_MIN_KEEP_DAYS)
                print("                failure; it self-corrects as entries age past the floor.")

        print("\n  NOT pruned, deliberately:")
        print("    data/messages.jsonl      it IS the evidence for 'which comms work'")
        print("    data/opportunities.jsonl closed roles feed channel yield; FKs must resolve")
        print("    the archives               already the cold tier")
    finally:
        if took_lock:
            subprocess.run([sys.executable, os.path.join(ENGINE_SCRIPTS, "runlock.py"),
                            "--release"], capture_output=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
