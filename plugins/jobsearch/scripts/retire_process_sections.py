#!/usr/bin/env python3
"""Remove the retired Process sections from focus.md. Run from the PROFILE, by the coordinator.

⭐ WHAT IS BEING RETIRED, AND WHAT MUST SURVIVE (0.4.0, 2026-08-06)
------------------------------------------------------------------
Engine defects are no longer carried in the profile. They are filed as issues on the plugin's own
repository, because a capability's defects belong on that capability's tracker and a local copy is
a second place to look — the one that goes stale. The dashboard's Process tab is gone with them.

    REMOVED   ## ⚙️ Process — 🔧 Open                 (engine work → GitHub issues)
    REMOVED   ## ⚙️ Process — ✅ Recently resolved     (a log; closed issues are the record now)
    ⭐ KEPT    ## ⚙️ Process — ⚡ Needs <owner>          (DO NOT REMOVE — see below)

⚠️⚠️ THE KEPT SECTION IS THE WHOLE REASON THIS IS A SCRIPT AND NOT A `sed` COMMAND.
`Needs <owner>` is what renders as the **System & tooling** group inside *Your Move*. It holds the
decisions only the owner can make — a credential, a cadence, an account setting — which **no issue
on the engine repo can ever resolve for them**. Deleting all three sections because they share a
prefix would silently drop that group from the dashboard, and the symptom would be an empty panel
that looks exactly like "nothing needs you." That is the failure mode this whole project is
organised against, so the script removes exactly two headings and refuses to guess.

⭐⭐ IT NEVER REFUSES, AND THAT IS THE POINT (rewritten 2026-08-06).

The first version REFUSED when `🔧 Open` still held items, on the grounds that deleting them
would lose work recorded nowhere else. That reasoning was right and the conclusion was wrong:
**a migration that refuses has not shipped — it has moved the work to the user, permanently.**
The owner had to be told twice to run it by hand, which does not scale past the one person
reading the release note, and is exactly what a version process exists to avoid.

So it RELOCATES instead of refusing. Any surviving content is appended to `process_archive.md` —
the file this profile already designates for retired process items — under a dated heading naming
the version that moved it. Then the sections go. **Nothing is deleted, nothing is asked of anyone,
and the result is idempotent.**

The general rule, for every migration after this one: **preserve, then transform.** Refusing is
correct only when there is genuinely nowhere to put the thing, which is rarer than it looks.

Usage:
    python3 retire_process_sections.py --check     # what would change; writes nothing
    python3 retire_process_sections.py             # archive any content, then remove the sections

Python 3.9+. Standard library only.
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root


def engine_version():
    try:
        import json
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(os.path.dirname(here), ".claude-plugin", "plugin.json"),
                  encoding="utf-8") as fh:
            return json.load(fh).get("version") or "unversioned"
    except Exception:
        return "unversioned"

# Matched loosely on the owner's name, which is DATA and differs per profile.
REMOVE = (
    (r"^##\s*⚙️\s*Process\s*—\s*🔧\s*Open\b.*$", "🔧 Open"),
    (r"^##\s*⚙️\s*Process\s*—\s*✅\s*Recently resolved\b.*$", "✅ Recently resolved"),
)
KEEP_RE = re.compile(r"^##\s*⚙️\s*Process\s*—\s*⚡\s*Needs\b.*$", re.M)


def split_sections(text):
    """[(heading_line_or_None, body)] — a plain sectioniser, no markdown library."""
    out, cur_head, cur = [], None, []
    for line in text.splitlines(keepends=True):
        if re.match(r"^##\s", line):
            out.append((cur_head, "".join(cur)))
            cur_head, cur = line, [line]
        else:
            cur.append(line)
    out.append((cur_head, "".join(cur)))
    return out


def has_real_content(body):
    """Anything beyond the heading, blank lines, and italic guidance notes."""
    for line in body.splitlines()[1:]:
        s = line.strip()
        if not s or s.startswith("<!--"):
            continue
        if s.startswith("_") and s.endswith("_"):
            continue          # the italic explainer blocks these sections carry
        return True
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="report only; write nothing")
    ap.add_argument("--file", default="focus.md")
    ap.add_argument("--archive", default="process_archive.md",
                    help="where surviving content is relocated (never deleted)")
    args = ap.parse_args()

    path = os.path.join(profile_root(), args.file)
    if not os.path.exists(path):
        print("No %s here. Run this from the profile directory." % path, file=sys.stderr)
        return 2
    with open(path, encoding="utf-8") as fh:
        text = fh.read()

    sections = split_sections(text)
    kept, dropped, blocking = [], [], []
    for head, body in sections:
        label = None
        if head:
            for pat, name in REMOVE:
                if re.match(pat, head.strip(), re.M):
                    label = name
                    break
        if label:
            dropped.append(label)
            if label == "🔧 Open" and has_real_content(body):
                blocking.append(body)
            continue
        kept.append(body)

    survives = bool(KEEP_RE.search(text))
    print("focus.md: %s" % path)
    print("  would remove: %s" % (", ".join(dropped) if dropped else "nothing (already clean)"))
    print("  ⭐ '⚡ Needs …' present and KEPT: %s" % ("yes" if survives else
                                                     "NOT FOUND — nothing to keep"))

    if not dropped:
        return 0

    if blocking:
        print("  surviving content: relocating to %s (nothing is deleted)" % args.archive)

    if args.check:
        print("\n  --check: nothing written.")
        return 0

    # ⭐ RELOCATE FIRST, REMOVE SECOND — and if the relocation fails, do not remove anything.
    # The order is the whole safety property: a crash between the two leaves the content in BOTH
    # places, which is recoverable. The reverse order loses it.
    if blocking:
        archive = os.path.join(os.path.dirname(path), args.archive)
        stamp = "\n\n## Retired from focus.md by jobsearch %s\n\n" % engine_version()
        payload = stamp + "".join(
            "\n".join(l for l in b.splitlines()[1:]) + "\n" for b in blocking)
        try:
            existing = ""
            if os.path.exists(archive):
                with open(archive, encoding="utf-8") as fh:
                    existing = fh.read()
            tmp = archive + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(existing + payload)
            os.replace(tmp, archive)
            with open(archive, encoding="utf-8") as fh:
                if payload.strip()[:120] not in fh.read():
                    raise IOError("archive write did not verify")
        except Exception as e:
            print("\n⛔ Could not archive the surviving content (%s) — focus.md was NOT changed."
                  % e, file=sys.stderr)
            print("   Removing a section whose content failed to relocate is the one outcome",
                  file=sys.stderr)
            print("   this migration must never produce.", file=sys.stderr)
            return 1
        print("  ✅ archived %d block(s) to %s" % (len(blocking), args.archive))

    out = "".join(kept)
    out = re.sub(r"\n{4,}", "\n\n\n", out)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(out)
    os.replace(tmp, path)
    print("\n✅ Removed %d section(s). '⚡ Needs …' left intact." % len(dropped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
