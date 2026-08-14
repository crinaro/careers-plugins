#!/usr/bin/env python3
"""Does `hooks.json`'s PreToolUse matcher still agree with `guard_outbound_click.py`'s
GUARDED_CLICK_TOOLS?

⭐ WHY THIS EXISTS (dev #78 / public #9)
-----------------------------------------
`guard_outbound_click.py` names its own `GUARDED_CLICK_TOOLS` tuple as the single source of
truth for which tools it guards. `hooks.json`'s matcher string is a SEPARATE, hand-maintained
copy of the same alternation, because the hook registration format has no way to import a
Python constant. Nothing stops the two from drifting the moment either side changes — a new
click surface added to the tuple without touching the matcher would silently ship a hook that
never fires for it, and the plugin would report ACTIVE while covering less than it claims.

⚠️ This must run against the SHIPPED package, not just the dev tree — `check_shipped_package.py`
runs this exact script inside its materialized copy, because a publish-classification mistake
that drops one file but not the other would be invisible from the dev checkout alone, where
both files always live side by side.

Resolves both files relative to ITS OWN location (sibling `scripts/` and `hooks/` under the
same `plugins/jobsearch/`), so the same script works unmodified whether it is run from this
repo or from inside a throwaway materialized package — no engine-root lookup needed, and
therefore nothing that could silently resolve the wrong tree.

Usage:
    python3 scripts/check_click_guard_matcher.py

Python 3.9+. Standard library only.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.dirname(HERE)
HOOKS_JSON = os.path.join(PLUGIN_ROOT, "hooks", "hooks.json")

sys.path.insert(0, HERE)


def guarded_tools():
    import guard_outbound_click as g
    return g.GUARDED_CLICK_TOOLS


def find_matcher(hooks_config):
    """The matcher string of the PreToolUse entry that runs guard_outbound_click.py, or None
    if no such entry exists at all — itself a failure, not a silent pass."""
    for entry in (hooks_config.get("hooks") or {}).get("PreToolUse") or []:
        for h in entry.get("hooks") or []:
            if "guard_outbound_click.py" in (h.get("command") or ""):
                return entry.get("matcher")
    return None


def main():
    print("CLICK GUARD MATCHER — does hooks.json agree with GUARDED_CLICK_TOOLS?")
    print("  hooks.json: %s" % HOOKS_JSON)

    try:
        tools = guarded_tools()
    except Exception as e:
        print("\n  !! could not import guard_outbound_click.py — %s. This gate cannot run "
              "without the tuple it checks against." % e)
        return 1

    if not tools:
        print("\n  !! GUARDED_CLICK_TOOLS is empty — that would make the alternation empty "
              "too, which is itself a defect, not a clean state.")
        return 1

    expected = "|".join(tools)

    if not os.path.exists(HOOKS_JSON):
        print("\n  !! hooks.json is missing entirely at %s — a family of one, resolved to "
              "zero files, and that is a failure here, not a silent pass." % HOOKS_JSON)
        return 1

    try:
        with open(HOOKS_JSON, encoding="utf-8") as fh:
            hooks_config = json.load(fh)
    except Exception as e:
        print("\n  !! hooks.json did not parse as JSON — %s" % e)
        return 1

    actual = find_matcher(hooks_config)
    if actual is None:
        print("\n  !! no PreToolUse entry in hooks.json runs guard_outbound_click.py at all — "
              "the guard is built but never wired in.")
        return 1

    print("  GUARDED_CLICK_TOOLS: %s" % ", ".join(tools))
    print("  expected matcher:    %s" % expected)
    print("  hooks.json matcher:  %s" % actual)

    if actual != expected:
        print("\n  !! DRIFT: hooks.json's matcher does not equal \"|\".join(GUARDED_CLICK_TOOLS).")
        print("     Either GUARDED_CLICK_TOOLS changed without updating hooks.json, or the")
        print("     reverse. Whichever moved, bring the other back to matching this print.")
        return 1

    print("\n  CLEAN. hooks.json's matcher equals \"|\".join(GUARDED_CLICK_TOOLS).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
