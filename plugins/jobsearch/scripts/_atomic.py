#!/usr/bin/env python3
"""Atomic file replacement — write a temp file beside the target, then `os.replace`.

⭐ WHY THIS EXISTS
------------------
`record.py` has done this correctly since it was written, and its docstring says why:

    The old pattern was `open(path,'w')` followed by a write loop. A session dying mid-loop
    left a truncated file, i.e. a destroyed pipeline. `os.replace` either fully succeeds or
    leaves the original untouched; there is no partial state.

That reasoning was never wrong — it was just never applied anywhere else. A 2026-08-10 review
found six other writers still using the truncating pattern **on the same files record.py guards**:
`channels_due.py`, `reconcile.py` (twice), `migrate_contacts.py`, `inbox.py`, and `doctor.py`.

**`doctor.py` was the worst of them.** It truncated `config.json` with `json.dump`, then RE-OPENED
the file in append mode to add a trailing newline — two separate opens of the user's entire
configuration, with a window in between. A failure during the dump leaves a truncated
`config.json`, which is not a lost edit but a lost profile: comp floors, geography, mailboxes,
ATS domains, writing rules. Everything downstream then reads a half-file and reports whatever it
finds as fact.

⚠️ **The risk is not theoretical here.** These run unattended, every two hours, and several run
inside a lock whose whole purpose is to make a partial write impossible. A truncating write
inside a lock is still a truncating write — the lock stops a SECOND writer, not a dying one.

## Why a module rather than copying record.py's function

`record.py.save_atomic` is bound to its own `STORES` map and `DATA` directory, so the other six
could not call it without importing a store abstraction they do not use. This takes a path.

Python 3.9+. Standard library only.
"""

import json
import os
import tempfile


def _replace(path, write_body):
    """Temp file in the SAME directory (so `os.replace` stays on one filesystem), fsync, swap.

    Same directory matters: `os.replace` across filesystems is not atomic and raises on some
    platforms. A temp file in /tmp would reintroduce exactly the failure this prevents.
    """
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".atomic-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            write_body(fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def write_jsonl(path, rows):
    """One JSON object per line, trailing newline on each — the store format."""
    _replace(path, lambda fh: [fh.write(json.dumps(r, ensure_ascii=False) + "\n") for r in rows])


def write_json(path, obj, indent=2):
    """A single JSON document, with the trailing newline written in the SAME pass.

    The newline is not cosmetic here: `doctor.py` used to add it via a second `open(..., "a")`,
    which is a second chance to fail on the file it had just truncated.
    """
    def body(fh):
        json.dump(obj, fh, indent=indent, ensure_ascii=False)
        fh.write("\n")
    _replace(path, body)


def write_text(path, text):
    _replace(path, lambda fh: fh.write(text))
