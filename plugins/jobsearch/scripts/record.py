#!/usr/bin/env python3
"""
THE WRITE API for the mutable data files — one code path, a lock held in milliseconds.

WHY THIS EXISTS
---------------
The candidate, 2026-08-04: *"do we have the locks on the appropriate items? … if we had processes
that are reused (atomic) that will make sense but the challenge is a process putting something on
the queue for another."*

The candidate was right, and an audit made the reason precise. The repo already runs **two** concurrency
strategies and they were mixed up:

    APPEND + REPLAY   data/inbox.jsonl · data/pending_actions.jsonl · messages.jsonl
                      Lock-free BY DESIGN. Two workers append at once and replay resolves it.
                      **These have never conflicted.** The queue hand-off the candidate worried
                      about is the part that already works.

    WHOLE-FILE REWRITE  opportunities.jsonl (167 rows) · companies.jsonl · channels.jsonl
                      Every mutation was an AD-HOC `read all → mutate → write all`, invented
                      fresh by whatever session happened to be editing. **There was no write
                      API at all.**

**That is why the lock had to be coarse.** You cannot make a file safe when any session may
rewrite it however it likes; the only defence left is a global mutex held across the whole
edit. So the lock grew to cover a verify-and-write cycle lasting minutes, and the observed cost
was four false-RED gate sweeps in a single afternoon plus a duplicated LinkedIn record.

**This collapses the window.** One code path takes the lock, reads, mutates, writes atomically,
and releases — milliseconds, not minutes. The lock stops being a convention that every session
must remember and becomes correct by construction.

## ⭐ THE WRITE IS ATOMIC, AND THAT IS NOT DECORATION

Writes go to a temp file in the same directory and are `os.replace`d into place, which is atomic
on POSIX. A partial write to `opportunities.jsonl` would corrupt **167 records** — the entire
pipeline — and the old ad-hoc pattern (`open(p,'w')` then loop) had exactly that failure mode if
a session died mid-loop. Nothing recovers that but a git checkout.

## What it does NOT solve, stated plainly

Two workers editing the **same record** still serialise. This shrinks the contention window; it
does not make concurrent edits to one row commutative. If that turns out to happen in practice,
the answer is journaling the store the way the queues are journaled — but that is a data-model
migration and should wait for evidence, not a guess.

## ⭐ CALLING THIS MID-RUN: `--already-locked` (public #17 / dev #97)

The daily run takes the run lock itself at the top of its write phase and releases after the
commit. Called inside that window, this script used to try to take the same lock AGAIN — and
since the holder was the caller's own run, it waited out the full timeout for a release that
could never come. The observed result was the exact failure this API exists to prevent:
sessions gave up and hand-edited the JSONL.

So the contract is now explicit, and it does not touch the two-strategy design above:

    OUTSIDE a lock-holding run   plain call — takes the lock, writes, releases (milliseconds).
    INSIDE the run's write phase pass `--already-locked` — the write proceeds under the RUN's
                                 hold; nothing here takes or releases. Verified, not trusted:
                                 if nobody actually holds the lock the call is REFUSED, because
                                 a caller claiming a hold that does not exist is writing
                                 unprotected by accident.

A refused take now also diagnoses the self-deadlock instead of leaving a silent wait: the
default `--wait` is seconds (holders release in seconds by design), and the refusal says when
`--already-locked` is the answer.

Usage:
    python3 scripts/record.py create <opp_id> '{"company_id":"...","title":"...", ...}'
    python3 scripts/record.py set <opp_id> stage screening
    python3 scripts/record.py set <opp_id> next_action_owner <candidate>   # e.g. your own name, lowercased
    python3 scripts/record.py set-in <opp_id> outreach contact_id=jane-doe outcome replied
    python3 scripts/record.py append <opp_id> research_log '{"date":"2026-08-04","note":"..."}'
    python3 scripts/record.py show <opp_id>
    ... --file companies|channels to address the other stores. --dry-run to preview.
    ... --already-locked when the calling run already holds the run lock (see above).

Python 3.9+. Standard library only.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile

import os, sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root as _profile_root
ENGINE_SCRIPTS = os.path.dirname(os.path.realpath(__file__))

ROOT = _profile_root()
DATA = os.path.join(ROOT, "data")
STORES = {"opportunities": "opportunities.jsonl",
          "companies": "companies.jsonl",
          "channels": "channels.jsonl"}
LOCK = os.path.join(ENGINE_SCRIPTS, "runlock.py")
# ⭐ ENGINE, not profile — the data MODEL ships with the code; the DATA belongs to the user.
MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
                          "docs", "data_model.json")


def model():
    """⭐ THE DEFINITION THE API ENFORCES — docs/data_model.json.

    Added 2026-08-04, after the candidate asked whether the API needed definitions, whether keys
    were guarded against duplicates, and whether any of it was enforced. All three were NO:
    `record.py set <id> nxet_action_owner <candidate>` wrote silently and the validator reported
    **clean**, because only outreach[] had an unknown-key guard. Three guessed contact_ids landed
    the same day.

    Validating AFTER the write was never enough. A typo is already on disk by then, and
    validate_data cannot know that `nxet_action_owner` was meant to be `next_action_owner` — to
    it, an unguarded store simply has a new field.
    """
    with open(MODEL_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def known_fields(store, array=None):
    m = model()["stores"].get(store) or {}
    if array:
        return set((m.get("arrays", {}).get(array) or {}).get("fields") or [])
    return set(m.get("fields") or [])


def check_field(store, field, array=None):
    """Returns None if fine, else the reason to refuse. Refusing BEFORE the write is the point."""
    aliases = model()["banned_aliases"]
    if field in aliases and not field.startswith("_"):
        return ("%r is a BANNED ALIAS for %r. The same meaning under two spellings means a query "
                "written against one silently misses the other." % (field, aliases[field]))
    allowed = known_fields(store, array)
    if allowed and field not in allowed:
        near = [f for f in allowed if abs(len(f) - len(field)) <= 2
                and sum(a != b for a, b in zip(sorted(f), sorted(field))) <= 3]
        hint = ("  Did you mean: %s" % ", ".join(sorted(near)[:3])) if near else ""
        where = "%s[]" % array if array else store
        return ("%r is not a field of %s.%s\n  Known: %s" %
                (field, where, hint, ", ".join(sorted(allowed))))
    return None


class LockError(RuntimeError):
    pass


# ⭐ Seconds, not minutes. Holders release in seconds by design (runlock.py's own contract), so
# a wait longer than this only ever happens when the holder is the CALLER'S OWN RUN — which will
# never release while it waits on us. 120 was sized for the old coarse lock and turned that
# self-deadlock into a silent two-minute hang (public #17 / dev #97).
DEFAULT_WAIT = 10


def take_lock(why, wait=DEFAULT_WAIT):
    r = subprocess.run([sys.executable, LOCK, "--take", why, "--wait", str(wait)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise LockError(r.stdout.strip() or "could not take the write lock")


def lock_is_held():
    """True if ANY writer currently holds the run lock. runlock --status exits 1 when locked."""
    r = subprocess.run([sys.executable, LOCK, "--status"], capture_output=True, text=True)
    return r.returncode != 0


def release_lock():
    subprocess.run([sys.executable, LOCK, "--release"], capture_output=True)


def load(store):
    p = os.path.join(DATA, STORES[store])
    with open(p, encoding="utf-8") as fh:
        return [json.loads(l) for l in fh if l.strip()]


def save_atomic(store, rows):
    """Temp file in the SAME directory, then os.replace — atomic on POSIX.

    ⚠️ The old pattern was `open(path,'w')` followed by a write loop. A session dying mid-loop
    left a truncated file, i.e. a destroyed pipeline. `os.replace` either fully succeeds or
    leaves the original untouched; there is no partial state.
    """
    p = os.path.join(DATA, STORES[store])
    fd, tmp = tempfile.mkstemp(dir=DATA, prefix=".record-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def snapshot(store):
    """The store's exact bytes before a write, so a failed write can be undone. None if absent."""
    p = os.path.join(DATA, STORES[store])
    try:
        with open(p, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def restore(store, blob):
    """Put `blob` back, atomically. Returns True only if the bytes are verifiably back.

    ⚠️ Verified by reading the file again rather than trusting the write. A rollback that is
    merely *believed* to have happened is worse than none: the caller is told the store is clean
    and writes on top of whatever is actually there.
    """
    if blob is None:
        return False
    p = os.path.join(DATA, STORES[store])
    fd, tmp = tempfile.mkstemp(dir=DATA, prefix=".rollback-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(blob)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        return False
    try:
        with open(p, "rb") as fh:
            return fh.read() == blob
    except OSError:
        return False


def find(rows, rid):
    for r in rows:
        if r.get("id") == rid:
            return r
    return None


def coerce(v):
    """A CLI gives strings; the store is typed. Guessing wrong writes "true" where True belongs."""
    if v in ("true", "True"):
        return True
    if v in ("false", "False"):
        return False
    if v in ("null", "None", ""):
        return None
    if v.lstrip("-").isdigit():
        return int(v)
    if v.startswith(("{", "[")):
        return json.loads(v)
    return v


def validate():
    r = subprocess.run([sys.executable, os.path.join(ENGINE_SCRIPTS, "validate_data.py")],
                       capture_output=True, text=True)
    return r.returncode, r.stdout


def main():
    ap = argparse.ArgumentParser(description="Atomic writes to the record stores.")
    ap.add_argument("op", choices=("create", "set", "set-in", "append", "show", "fields"))
    ap.add_argument("rid", nargs="?", help="record id (e.g. an opportunity_id)")
    ap.add_argument("rest", nargs="*")
    ap.add_argument("--file", default="opportunities", choices=sorted(STORES))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--wait", type=int, default=DEFAULT_WAIT,
                    help="Seconds to wait for the run lock (default %d — holders release in "
                         "seconds; a longer wait usually means you are waiting on your own "
                         "run, which never ends: see --already-locked)." % DEFAULT_WAIT)
    ap.add_argument("--already-locked", action="store_true",
                    help="The CALLING RUN already holds the run lock (it took it for its write "
                         "phase). Write under that hold; take and release nothing. Verified: "
                         "refused if nobody actually holds the lock.")
    ap.add_argument("--force", action="store_true",
                    help="Write an unknown field anyway. Almost never right — an unknown field "
                         "is invisible to every query written against the real one.")
    ap.add_argument("--fields", action="store_true",
                    help="Print what this store accepts, so a caller never has to guess.")
    args = ap.parse_args()

    if args.op == "fields" or args.fields:
        m = model()["stores"][args.file]
        print("%s — %s" % (args.file, ", ".join(sorted(m["fields"]))))
        print("  required: %s" % ", ".join(m.get("required") or []))
        for a, spec in sorted((m.get("arrays") or {}).items()):
            print("\n  %s[] — %s" % (a, ", ".join(sorted(spec["fields"]))))
            print("     required: %s%s" % (", ".join(spec.get("required") or []),
                  ("  · id: %s" % spec["id_field"]) if spec.get("id_field") else ""))
        ban = {k: v for k, v in model()["banned_aliases"].items() if not k.startswith("_")}
        print("\n  BANNED ALIASES (same meaning, two spellings): %s"
              % ", ".join("%s->%s" % (k, v) for k, v in sorted(ban.items())))
        return 0

    rows = load(args.file)
    rec = find(rows, args.rid)
    if args.op == "create":
        # A create must land on an ABSENT id — the mirror image of every other op.
        if rec is not None:
            print("⛔ REFUSED — a %s record with id %r already exists." % (args.file, args.rid))
            print("  create never overwrites. Use set/set-in/append to change an existing "
                  "record; pick a new id for a new one.")
            return 1
    elif rec is None:
        print("No %s record with id %r." % (args.file, args.rid))
        return 1

    if args.op == "show":
        print(json.dumps({k: v for k, v in rec.items()
                          if k not in ("research_log", "fit")}, indent=2)[:3000])
        return 0

    # ---- build the mutation, describing it before touching anything -------------
    new_row = None
    if args.op == "create":
        # ⭐ THE MISSING OPERATION (public #17 / dev #97). Without it, adding a brand-new row
        # meant hand-editing the JSONL — the exact ad-hoc read-all/write-all pattern this API
        # exists to abolish, and it recurred across sessions for as long as the gap existed.
        #
        # Deliberately a NEW op rather than `set` auto-creating on an unknown id: an op that
        # creates whenever an id fails to resolve turns every typo'd id into a silent new row,
        # which is the duplicate problem wearing a different hat. Intent is stated, then checked.
        if len(args.rest) != 1:
            print("usage: create <id> '<json object for the full record>'")
            return 2
        try:
            new_row = json.loads(args.rest[0])
        except ValueError as e:
            print("⛔ REFUSED — the record is not valid JSON: %s" % e)
            return 1
        if not isinstance(new_row, dict):
            print("⛔ REFUSED — the record must be a JSON object, got %s."
                  % type(new_row).__name__)
            return 1
        m = model()["stores"][args.file]
        idf = m.get("id_field") or "id"
        if idf in new_row and new_row[idf] != args.rid:
            print("⛔ REFUSED — the JSON carries %s=%r but the command names %r. One id, "
                  "stated once." % (idf, new_row[idf], args.rid))
            return 1
        new_row[idf] = args.rid
        # Same refuse-before-write guards every other op gets: unknown keys, aliases, required.
        for k in new_row:
            bad = check_field(args.file, k)
            if bad and not args.force:
                print("⛔ REFUSED — %s" % bad)
                return 1
            spec = (m.get("arrays") or {}).get(k)
            if spec and isinstance(new_row[k], list):
                for i, item in enumerate(new_row[k]):
                    if not isinstance(item, dict):
                        continue
                    for kk in item:
                        bad = check_field(args.file, kk, array=k)
                        if bad and not args.force:
                            print("⛔ REFUSED — %s[%d]: %s" % (k, i, bad))
                            return 1
        missing = [f for f in (m.get("required") or []) if not new_row.get(f)]
        if missing and not args.force:
            print("⛔ REFUSED — %s requires %s" % (args.file, ", ".join(missing)))
            print("  A record missing its required fields is one no query can rely on. "
                  "(`fields` prints what this store accepts.)")
            return 1
        desc = "create record (%d field(s): %s)" % (len(new_row), ", ".join(sorted(new_row)))

        def apply(r):        # unused for create; the lock section appends new_row instead
            raise AssertionError("create does not mutate an existing record")

    elif args.op == "set":
        if len(args.rest) != 2:
            print("usage: set <id> <field> <value>")
            return 2
        field, val = args.rest[0], coerce(args.rest[1])
        bad = check_field(args.file, field)
        if bad and not args.force:
            print("⛔ REFUSED — %s" % bad)
            print("\n  Fix the field name, or add it to docs/data_model.json if it is genuinely")
            print("  new. --force writes anyway and is almost never right: an unknown field is")
            print("  invisible to every query written against the real one.")
            return 1
        desc = "set %s = %r" % (field, val)

        def apply(r):
            r[field] = val

    elif args.op == "append":
        if len(args.rest) != 2:
            print("usage: append <id> <array> <json>")
            return 2
        arr, blob = args.rest[0], json.loads(args.rest[1])
        m = model()["stores"].get(args.file, {})
        if arr not in (m.get("arrays") or {}):
            print("⛔ REFUSED — %r is not an array of %s. Known: %s"
                  % (arr, args.file, ", ".join(sorted((m.get("arrays") or {})))))
            return 1
        for k in blob:
            bad = check_field(args.file, k, array=arr)
            if bad and not args.force:
                print("⛔ REFUSED — %s" % bad)
                return 1
        missing = [r for r in (m["arrays"][arr].get("required") or []) if not blob.get(r)]
        if missing and not args.force:
            print("⛔ REFUSED — %s[] requires %s" % (arr, ", ".join(missing)))
            print("  A row missing its required fields is a row no query can rely on.")
            return 1
        # ⭐ ID UNIQUENESS — the duplicate problem, caught at the door
        idf = m["arrays"][arr].get("id_field")
        if idf and blob.get(idf):
            existing = {x.get(idf) for x in (rec.get(arr) or [])}
            if blob[idf] in existing:
                print("⛔ REFUSED — %s %r already exists on this record." % (idf, blob[idf]))
                print("  Use set-in to update it. Appending a second row with the same id is how")
                print("  a join silently returns two answers.")
                return 1
        desc = "append to %s[]" % arr

        def apply(r):
            r.setdefault(arr, []).append(blob)

    else:  # set-in
        if len(args.rest) != 4:
            print("usage: set-in <id> <array> <key>=<match> <field> <value>")
            return 2
        arr, match, field, val = args.rest[0], args.rest[1], args.rest[2], coerce(args.rest[3])
        if "=" not in match:
            print("match must be key=value, e.g. contact_id=jane-doe")
            return 2
        mk, mv = match.split("=", 1)
        for f in (mk, field):
            bad = check_field(args.file, f, array=arr)
            if bad and not args.force:
                print("⛔ REFUSED — %s" % bad)
                return 1
        desc = "set %s = %r on %s[] where %s == %r" % (field, val, arr, mk, mv)

        def apply(r):
            # ⭐ DOTTED PATH: `fit.requirements` is an array one level down, not a top-level one.
            # Closing an answered JD-fit question is a routine write (26 were open on 2026-08-05,
            # two of them still flagged DUE for a call that had already happened), and without
            # this the only way to do it was an ad-hoc read-mutate-write — the exact pattern this
            # API exists to abolish. Resolution is by walking dicts, so it stays a strict
            # generalisation: a name with no dot behaves exactly as before.
            node = r
            for part in arr.split(".")[:-1]:
                node = node.get(part) or {}
            leaf = arr.split(".")[-1]
            hits = [x for x in (node.get(leaf) or []) if str(x.get(mk)) == mv]
            if not hits:
                raise KeyError("no %s[] entry with %s == %r" % (arr, mk, mv))
            for x in hits:
                x[field] = val

    print("%s: %s" % (args.rid, desc))
    if args.dry_run:
        print("  --dry-run: nothing written.")
        return 0

    # ---- the ONLY window the lock is held: read, mutate, write, verify ----------
    if args.already_locked:
        # ⭐ VERIFIED, NOT TRUSTED. A caller claiming a hold nobody has is writing unprotected
        # by accident — refuse rather than proceed bare (public #17 / dev #97).
        if not lock_is_held():
            print("  REFUSED — --already-locked, but NOBODY holds the run lock.")
            print("  Take it first (runlock.py --take), or drop the flag and let this call")
            print("  take it for the milliseconds of the write.")
            return 1
    else:
        try:
            take_lock("record.py %s %s" % (args.op, args.rid), wait=args.wait)
        except LockError as e:
            print("  REFUSED — %s" % e)
            print("  If that holder is YOUR OWN run (it took the lock for its write phase),")
            print("  waiting can never succeed — re-run with --already-locked instead.")
            print("  If it is another writer: holds are short; retry in a moment.")
            return 1
    try:
        rows = load(args.file)          # re-read INSIDE the lock — the file may have moved
        rec = find(rows, args.rid)
        if args.op == "create":
            if rec is not None:
                print("  a record with id %r appeared between read and lock — aborting."
                      % args.rid)
                return 1
            rows.append(new_row)
        else:
            if rec is None:
                print("  record vanished between read and lock — aborting.")
                return 1
            try:
                apply(rec)
            except KeyError as e:
                print("  %s" % e)
                return 1
        # ⭐⭐ SNAPSHOT BEFORE THE WRITE — this is what makes the rollback below possible.
        # Raw bytes, not the parsed rows: restoring exactly what was there cannot reintroduce a
        # formatting difference, and a byte-identical restore is trivially verifiable.
        # GitHub issue #1.
        before = snapshot(args.file)

        # ⭐ Was the store ALREADY invalid? Asked here, before touching anything, because
        # validate_data.py checks the WHOLE store — an unrelated pre-existing problem would
        # otherwise make this write look guilty and get rolled back for nothing.
        pre_rc, _ = validate()

        save_atomic(args.file, rows)
        rc, out = validate()
        if rc != 0:
            if pre_rc != 0:
                # The store was already invalid. Rolling back would discard a legitimate write to
                # "fix" something it did not cause, so keep it and say exactly that.
                print("  ⚠️ WROTE. The validator still fails — BUT IT WAS ALREADY FAILING BEFORE")
                print("  this write, so this change is not the cause and was NOT rolled back.")
                print("  " + "\n  ".join(out.strip().split("\n")[-6:]))
                print("  Fix the pre-existing problem; the store is invalid for the next worker.")
                return 1

            # The store was clean and this write broke it. Put it back.
            #
            # ⭐ WHY THIS EXISTS: previously the invalid write stayed on disk and only a warning
            # was printed. The caller saw exit 1 — a failure — while the data HAD changed. **A
            # refusal that writes is worse than either honest outcome**: the caller retries and
            # duplicates the row, and the next worker inherits an invalid store. Observed
            # 2026-08-05 appending an outreach row whose message_ref did not yet resolve.
            restored = restore(args.file, before)
            post_rc, _ = validate()
            print("  ⛔ REFUSED — the write broke the store, so it was ROLLED BACK.")
            print("  " + "\n  ".join(out.strip().split("\n")[-6:]))
            if restored and post_rc == 0:
                print("  ✅ Rolled back; the store is byte-identical to before and validates.")
                print("  Nothing was written. Fix the input and re-run — retrying is safe.")
            else:
                # Never claim a rollback that did not happen. This is the one outcome that
                # needs a human, and saying so plainly is the whole point.
                print("  ⛔⭐ ROLLBACK FAILED — THE FILE IS IN AN UNKNOWN STATE. DO NOT RETRY.")
                print("  Restore %s from git before any further write." % STORES[args.file])
            return 1
    finally:
        # Under --already-locked the hold belongs to the CALLING RUN — releasing it here would
        # strip the protection off the rest of the run's write phase mid-flight.
        if not args.already_locked:
            release_lock()

    if args.already_locked:
        print("  written atomically · validator clean · run's lock left in place")
    else:
        print("  written atomically · validator clean · lock released")
    return 0


if __name__ == "__main__":
    sys.exit(main())
