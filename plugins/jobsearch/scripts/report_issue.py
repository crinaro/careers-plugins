#!/usr/bin/env python3
"""Report an engine defect from an INSTALLED plugin — GitHub #58, #59, #61.

The engine documented exactly one route for reporting its own defects:

    python3 <careers-plugins>/scripts/intake.py --add …

⚠️ THAT PATH SHIPS NOWHERE. It exists only in the private maintainer repository. On a
machine that also has a maintainer's checkout it silently resolved to *that* tree — which is
how it was found — and on every other machine it resolves to nothing. Five shipped files
instructed it.

This is the shipped replacement, and the differences are the point:

  * ⭐ IT RESOLVES INSIDE THE INSTALL. The target comes from this plugin's own
    `plugin.json.repository`, read from the same directory this file lives in. Nothing
    searches the filesystem for a similarly-named script, which is the rule #59 asks for:
    with six engine versions coexisting in the cache, every script name exists at six paths
    at once and a search can match the wrong one.

  * ⭐ IT REPORTS TO THE PUBLIC MARKETPLACE, because that is the only tracker a user can
    see. `intake.py` targets the PRIVATE dev repo and refuses to file publicly; the two
    tools point in opposite directions on purpose, and neither should be reachable from the
    other's audience.

  * ⭐ IT CARRIES VERSION PROVENANCE (#61). Engine version and profile schema version go in
    as structured fields. A report that cannot say which version produced the behaviour
    cannot be triaged, confirmed fixed, or told apart from one about code nobody maintains.

⚠️ IT REFUSES A REPORT CARRYING PERSONAL DATA. A public issue is permanent, and the shapes
below are the ones that actually arrive: a comp figure, a phone number, an address, a
person's name. Refusal is the whole point — the reporter can rephrase; a filed issue cannot
be unfiled.

Usage:
    ~/.claude/jobsearch/run report_issue.py --title "…" --symptom "…" [--evidence "…"]
    ~/.claude/jobsearch/run report_issue.py --title "…" --symptom "…" --file
        (without --file it prints the report for you to read first)

Python 3.9+. Standard library only. Filing needs the `gh` CLI; without it the report is
printed with the tracker URL so it can be pasted in by hand.
"""

import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)

# ⚠️ Kept deliberately in step with `scripts/intake.py` in the marketplace repo. They cannot
# share an import — one ships, the other does not — so a test asserts both refuse the same
# planted samples rather than trusting two lists to stay equal by good intentions.
LEAK_PATTERNS = (
    (r"\$\s?\d[\d,]{2,}", "a currency amount — name the RULE (\"below my floor\"), not the figure"),
    (r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b", "a phone number"),
    (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "an email address — say \"my mailbox\""),
    (r"\b\d{6,}\b", "a bare 6+ digit number, which is how a comp figure usually arrives"),
    (r"(?i)\b(recruiter|contact|manager)\s+[A-Z][a-z]+\s+[A-Z][a-z]+", "a person's name"),
    (r"linkedin\.com/in/[A-Za-z0-9\-_%]+", "a LinkedIn profile URL"),
    (r"/Users/[a-z0-9._\-]+", "a home directory path containing a username"),
)


def engine_version():
    try:
        with open(os.path.join(ENGINE, ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
            return str(json.load(fh).get("version") or "unknown")
    except (OSError, ValueError):
        return "unknown"


def tracker():
    """The PUBLIC marketplace, from this plugin's own manifest — never a filesystem search."""
    try:
        with open(os.path.join(ENGINE, ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
            repo = str(json.load(fh).get("repository") or "")
    except (OSError, ValueError):
        repo = ""
    m = re.search(r"github\.com[:/]([\w.\-]+/[\w.\-]+?)(?:\.git)?/?$", repo)
    return m.group(1) if m else ""


def schema_version():
    try:
        sys.path.insert(0, HERE)
        from _root import profile_root
        with open(os.path.join(profile_root(), ".jobsearch-schema"), encoding="utf-8") as fh:
            raw = fh.read().strip()
        if raw.startswith("{"):
            return str((json.loads(raw) or {}).get("schema") or "unknown")
        return raw or "unknown"
    except Exception:                                   # noqa: BLE001
        return "unknown"


def scan(text, field):
    hits = []
    for pattern, why in LEAK_PATTERNS:
        m = re.search(pattern, text or "")
        if m:
            hits.append((field, why, m.group(0)))
    return hits


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--title", required=True)
    ap.add_argument("--symptom", required=True)
    ap.add_argument("--evidence", default="")
    ap.add_argument("--file", action="store_true",
                    help="actually file it (default: print for review)")
    args = ap.parse_args()

    repo = tracker()
    eng, schema = engine_version(), schema_version()

    hits = scan(args.title, "title") + scan(args.symptom, "symptom") + \
        scan(args.evidence, "evidence")
    if hits:
        print("⛔ Nothing was filed — this report carries personal data.\n", file=sys.stderr)
        for field, why, found in hits:
            print("   %-9s %s\n             found: %r" % (field, why, found[:60]),
                  file=sys.stderr)
        print("\n   A public issue is permanent. Describe the SHAPE of the problem, not the",
              file=sys.stderr)
        print("   values: \"a role below my comp floor\" rather than the figure, \"a recruiter\"",
              file=sys.stderr)
        print("   rather than their name. The mechanism is what makes it fixable.", file=sys.stderr)
        return 2

    body = (
        "**Engine version:** `%s`\n"
        "**Profile schema:** `%s`\n"
        "**Plugin:** `jobsearch`\n\n"
        "### Symptom\n\n%s\n\n"
        "### Evidence / how to reproduce\n\n%s\n\n"
        "---\n_Filed with `report_issue.py` from an installed plugin._\n"
        % (eng, schema, args.symptom, args.evidence or "_not supplied_"))

    if not repo:
        print("Could not determine the tracker from this plugin's manifest.", file=sys.stderr)
        print("Report it at the repository this plugin was installed from.", file=sys.stderr)
        print("\n%s\n\n%s" % (args.title, body))
        return 1

    if not args.file:
        print("DRY RUN — nothing filed. Re-run with --file to submit.\n")
        print("repo:   %s" % repo)
        print("title:  %s\n" % args.title)
        print(body)
        return 0

    r = subprocess.run(["gh", "issue", "create", "--repo", repo,
                        "--title", args.title, "--body", body],
                       capture_output=True, text=True)
    if r.returncode != 0:
        # Honest degradation: no gh, or not signed in. The report is still worth something.
        print("Could not file automatically (%s)."
              % (r.stderr.strip().split("\n")[0][:120] or "gh unavailable"), file=sys.stderr)
        print("Open https://github.com/%s/issues/new and paste:\n" % repo, file=sys.stderr)
        print("%s\n\n%s" % (args.title, body))
        return 1
    print("Filed against %s" % repo)
    print(r.stdout.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
