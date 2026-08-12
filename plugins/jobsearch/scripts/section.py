#!/usr/bin/env python3
"""
Print ONE section of a markdown file, so an agent can read the part it needs instead of
the whole file.

WHY THIS EXISTS
---------------
`strategy.md` is ~16,700 words and was being loaded WHOLE by four agents, each of which
names in its own prompt the single section it actually wants (Positioning; Message style;
Location strategy; Target roles). Telling an agent "read only § Positioning" is aspirational
— it still opens the file. This makes the scoped read mechanical.

Chosen over splitting `strategy.md` into several files: its change log is append-only and
would have to stay whole anyway, and splitting creates cross-reference debt across every
agent and task prompt.

Usage:
    python3 scripts/section.py strategy.md "Positioning"     # fuzzy heading match
    python3 scripts/section.py strategy.md --list            # list all headings
    python3 scripts/section.py CLAUDE.md "Hard rules"

Matching is case-insensitive substring against the heading text, so "positioning" finds
"## Positioning — proof points". If more than one heading matches, all matches are listed
and nothing is printed — an ambiguous read should fail loudly rather than silently return
the wrong section.

Python 3.9+. Standard library only.
"""

import argparse
import os
import re
import sys

import os, sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root as _profile_root

ROOT = _profile_root()
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def headings(lines):
    """[(index, level, text)] for every markdown heading."""
    out = []
    for i, line in enumerate(lines):
        m = HEADING.match(line)
        if m:
            out.append((i, len(m.group(1)), m.group(2).strip()))
    return out


def main():
    ap = argparse.ArgumentParser(description="Print one markdown section.")
    ap.add_argument("file")
    ap.add_argument("heading", nargs="?", help="Substring of the heading to print.")
    ap.add_argument("--list", action="store_true", help="List all headings and exit.")
    args = ap.parse_args()

    path = args.file if os.path.isabs(args.file) else os.path.join(ROOT, args.file)
    if not os.path.exists(path):
        sys.stderr.write("No such file: %s\n" % path)
        return 2
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")

    heads = headings(lines)

    if args.list or not args.heading:
        print("Headings in %s (%d lines, %d words):"
              % (args.file, len(lines), sum(len(l.split()) for l in lines)))
        for _, level, text in heads:
            print("  %s%s" % ("  " * (level - 1), text[:96]))
        return 0

    needle = args.heading.lower()
    matches = [h for h in heads if needle in h[2].lower()]
    if not matches:
        sys.stderr.write("No heading matching %r in %s. Use --list to see them.\n"
                         % (args.heading, args.file))
        return 1
    if len(matches) > 1:
        # Ambiguity fails loudly. Silently returning the first match is how an agent ends
        # up confidently reading the wrong section.
        sys.stderr.write("Ambiguous — %d headings match %r:\n" % (len(matches), args.heading))
        for _, level, text in matches:
            sys.stderr.write("  %s\n" % text)
        sys.stderr.write("Narrow the query.\n")
        return 1

    start, level, text = matches[0]
    end = len(lines)
    for i, lv, _t in heads:
        if i > start and lv <= level:
            end = i
            break

    body = "\n".join(lines[start:end]).rstrip()
    print(body)
    print("\n<!-- %s § %s — %d words. Full file is %d words; the rest was NOT read. -->"
          % (args.file, text[:60], len(body.split()),
             sum(len(l.split()) for l in lines)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
