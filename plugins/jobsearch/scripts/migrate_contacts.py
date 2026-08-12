#!/usr/bin/env python3
"""
Make `contacts[]` the real contact record for an opportunity, and JOIN outreach to it.

WHY THIS EXISTS
---------------
The candidate, 2026-08-02, after a reply from a recruiter was found 11 days late:

    "I sent a message to <a contact> so is the data structure not managing all the contact
     data for an opportunity (<a contact> and <a second contact>)?"

Measured, the answer was no:

⭐ EVERY NAME BELOW IS SYNTHETIC — same rule as `tests/fixtures/`: only the structure crosses
over, every string is made up. The real names were here until 2026-08-11, and this docstring is
in an ENGINE file that ships to every installation.

  * **20 of 46 outreach rows (43%) had NO contact record at all** — <an employer>,
    <a contact>, <a second contact>, people the candidate actually messaged.
  * **`outreach[].to` was free text**, so it could not join to `contacts[].name` even when both
    existed: `"Marlow Quist (<an employer>)"` vs
    `"Marlow Quist (linkedin.com/in/marlowquist)"`.
  * **0 of 60 contacts had a structured email field.** Six addresses existed, all buried in prose
    notes — including a contact's real address, which is why the reply that supplied the second contact's
    address had to be rediscovered by re-reading the mailbox.
  * `contacts[].status` was hand-written prose that goes stale ("Interviewing the candidate 2026-07-22
    9:00am PT" — 11 days after that interview happened).

So the opportunity held two half-overlapping lists of people that could not be joined, which
means you could not ask "what is the whole history with Marlow?" — the exact question the candidate
asked, about a real person.

WHAT IT DOES (idempotent — safe to re-run)
------------------------------------------
  1. Gives every contact a stable `contact_id` slug, unique within the opportunity.
  2. Lifts `email` and `linkedin` out of prose into structured fields (the prose stays).
  3. Sets `outreach[].contact_id` by matching on name, so the two arrays actually join.
  4. **CREATES a contact record for every orphaned outreach row** — if the candidate messaged someone,
     they are a contact of that opportunity by definition.
  5. Leaves the free-text `status` alone but stops relying on it: real state is derivable from
     the outreach rows now that they join.

Usage:
    python3 scripts/migrate_contacts.py            # report what it would change
    python3 scripts/migrate_contacts.py --apply

Python 3.9+. Standard library only.
"""

import argparse
import json
import os
import re
import sys

import os, sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root as _profile_root
from _atomic import write_jsonl, write_json

ROOT = _profile_root()
DATA = os.path.join(ROOT, "data")
PATH = os.path.join(DATA, "opportunities.jsonl")

EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w{2,}")
LINKEDIN_RE = re.compile(r"(?:https?://)?(?:www\.)?linkedin\.com/in/[\w%-]+", re.I)


def clean_name(raw):
    """'Marlow Quist (linkedin.com/in/x)' -> 'Marlow Quist'."""
    s = re.sub(r"\(.*?\)", "", raw or "")
    s = EMAIL_RE.sub("", s)
    s = LINKEDIN_RE.sub("", s)
    return re.sub(r"\s{2,}", " ", s).strip(" ,;—-")


def slug(name):
    s = re.sub(r"[^a-z0-9 ]", "", (name or "").lower()).strip()
    return "-".join(s.split()[:3]) or "unknown"


def key(name):
    """Match key: first+last, lowercased. Tolerates trailing titles and firms."""
    parts = clean_name(name).lower().split()
    return " ".join(parts[:2]) if len(parts) >= 2 else " ".join(parts)


def harvest(*texts):
    email = linkedin = None
    for t in texts:
        if not t:
            continue
        if not email:
            m = EMAIL_RE.search(t)
            if m:
                email = m.group(0)
        if not linkedin:
            m = LINKEDIN_RE.search(t)
            if m:
                linkedin = m.group(0)
    return email, linkedin


# outreach.recipient_role -> contacts.path_type, so the two stop disagreeing.
ROLE_TO_PATH = {
    "hiring-manager": "hiring-manager", "hiring-line": "hiring-manager",
    "talent-acquisition": "recruiter", "recruiter-agency": "recruiter",
    "warm-contact": "warm-referral", "peer-network": "warm-referral",
}


def main():
    ap = argparse.ArgumentParser(description="Join outreach to contacts; structure contact data.")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    with open(PATH, encoding="utf-8") as fh:
        opps = [json.loads(l) for l in fh if l.strip()]

    created = linked = emails = linkedins = ids = 0
    report = []

    for o in opps:
        contacts = o.get("contacts") or []
        # 1) ids + structured fields
        used = set()
        by_key = {}
        for c in contacts:
            nm = clean_name(c.get("name"))
            if not c.get("contact_id"):
                base = slug(nm)
                cid, n = base, 2
                while cid in used:
                    cid, n = "%s-%d" % (base, n), n + 1
                c["contact_id"] = cid
                ids += 1
            used.add(c["contact_id"])
            e, li = harvest(c.get("name"), c.get("notes"), c.get("status"))
            if e and not c.get("email"):
                c["email"] = e
                emails += 1
            if li and not c.get("linkedin"):
                c["linkedin"] = li if li.startswith("http") else "https://" + li.lstrip("/")
                linkedins += 1
            if nm and nm != c.get("name"):
                c["name"] = nm          # keep the NAME a name; the URL now has its own field
            by_key[key(nm)] = c

        # 2) join outreach -> contacts, creating the contact when the candidate messaged someone
        #    who was never recorded. If he wrote to them, they ARE a contact.
        for r in (o.get("outreach") or []):
            if r.get("contact_id"):
                continue
            k = key(r.get("to"))
            c = by_key.get(k)
            if c is None:
                nm = clean_name(r.get("to")) or (r.get("to") or "unknown")
                base = slug(nm)
                cid, n = base, 2
                while cid in used:
                    cid, n = "%s-%d" % (base, n), n + 1
                used.add(cid)
                e, li = harvest(r.get("to"), r.get("note"))
                c = {
                    "contact_id": cid,
                    "name": nm,
                    "role": None,
                    "path_type": ROLE_TO_PATH.get(r.get("recipient_role"), "cold"),
                    "status": None,
                    "notes": "Created 2026-08-02 by migrate_contacts.py — this person was "
                             "messaged but had no contact record. If the candidate wrote to them, they "
                             "are a contact of this opportunity by definition.",
                }
                if e:
                    c["email"] = e
                if li:
                    c["linkedin"] = li
                contacts.append(c)
                by_key[k] = c
                created += 1
                report.append(("CREATED", o["id"], nm))
            r["contact_id"] = c["contact_id"]
            linked += 1
            # lift an address out of the outreach row into the person record
            e, li = harvest(r.get("to"), r.get("note"))
            if e and not c.get("email"):
                c["email"] = e
                emails += 1
        if contacts:
            o["contacts"] = contacts

    print("CONTACT MODEL MIGRATION%s" % ("" if args.apply else "  (dry run — use --apply)"))
    print("=" * 70)
    print("  contact_id assigned      : %d" % ids)
    print("  outreach rows joined     : %d" % linked)
    print("  contacts CREATED from an : %d   <- these people were messaged with no record" % created)
    print("    orphaned outreach row")
    print("  emails lifted from prose : %d" % emails)
    print("  linkedin URLs structured : %d" % linkedins)
    if report:
        print("\n  contacts created:")
        for kind, oid, nm in report[:25]:
            print("    %-40s %s" % (oid[:40], nm))

    if args.apply:
        write_jsonl(PATH, opps)
        print("\n  APPLIED. Run validate_data.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
