#!/usr/bin/env python3
"""How do I reach this channel — a site plugin, the in-app browser, or the Chrome extension?

⭐ WHY THIS EXISTS
------------------
This plugin was built when Claude had no browser of its own. Every authenticated surface went
through the Chrome extension, and so the word *chrome* got written into channel records as if it
were the channel's requirement:

    {"id": "linkedin-jobsearch", "access": "login-chrome"}

**It was never the requirement.** LinkedIn requires a signed-in session; *chrome* was merely the
only mechanism that existed at the time. The in-app Browser pane arrived later and now holds a
logged-in session perfectly well, and dedicated site plugins are arriving on top of that. The
record conflated WHAT A CHANNEL NEEDS with HOW WE HAPPENED TO REACH IT, so the moment the second
changed, every file naming a mechanism became wrong at once — agent bodies, skill prose, and the
channel data itself.

    The rule: `access` states the REQUIREMENT. The MECHANISM is resolved at run time, from the
    candidate's ordered preference in `config.sourcing.route_preference`.

That separation is what lets the preference change in one place — a config edit — instead of a
sweep through every agent that browses.

## ⚠️ WHAT THIS SCRIPT DELIBERATELY DOES NOT DO

**It does not claim a mechanism is available.** A shell script cannot see which MCP servers this
session has, whether the in-app pane holds a live LinkedIn session, or whether the Chrome
extension's service worker is awake. Asserting any of that would be exactly the confident-wrong
answer this project is organised against.

So it returns the ORDERED LIST of mechanisms to try, and the caller reports which one worked.
A router that guesses availability produces a sweep that silently covered less than it claims;
a router that hands back an ordered list produces a sweep that can say what it could not reach.

## The vocabulary

REQUIREMENTS (what the channel needs) — the parseable values of `access`:

    public        no authentication; any browsing mechanism will do
    bot-limited   public, but actively hostile to automation — expect partial results
    login         needs a signed-in session
    human         a person, not a surface. There is no mechanism; this is relationship work.
    n/a           not swept at all (an email-driven channel)

MECHANISMS (how we reach it) — the values of `config.sourcing.route_preference`:

    plugin        a dedicated site plugin, named per channel in `config.sourcing.plugins`
    browser       Claude's in-app Browser pane
    chrome        the Chrome extension

Usage:
    route.py --channel indeed                       # ordered mechanisms for one channel
    route.py --all                                  # every channel, including non-browsable
    route.py --check                                # exit 1 on unparseable or legacy access
    route.py --set-preference browser,plugin,chrome # change the order mechanisms are tried
    route.py --set-plugin indeed indeed-jobs        # name the plugin serving one channel

`/jobsearch:sourcing` is the command wrapper. ⚠️ Set these HERE, never by hand-editing
config.json: the setters validate first, and a typo stored in config falls through to the
default at run time and looks like it was honoured.

Python 3.9+. Standard library only.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root

REQUIREMENTS = ("public", "bot-limited", "login", "human", "n/a")
MECHANISMS = ("plugin", "browser", "chrome")

# The default order, used when the profile does not state one. In-app pane first because it is
# in-process and needs no second application running; Chrome last because it depends on a real
# browser being awake and signed in, which is the most fragile of the three.
DEFAULT_PREFERENCE = ("plugin", "browser", "chrome")

# ⭐ Legacy `access` strings, which encoded the mechanism into the requirement. Kept ONLY so a
# profile that has not migrated yet still resolves; `migrate.py` rewrites them. Mapping them
# silently forever would preserve the confusion, so `--check` reports them as needing migration.
LEGACY = {
    "login-chrome": "login",
    "public-bot-limited": "bot-limited",
}


class RouteError(ValueError):
    """An access value nobody can read. ⚠️ MUST BE LOUD: a channel whose route cannot be
    resolved is a channel that will be skipped, and a silent skip is indistinguishable from a
    channel that was searched and found nothing."""


def parse_access(raw):
    """Requirement for one channel. Returns (requirement, was_legacy)."""
    if raw is None:
        raise RouteError("no `access` value at all")
    val = str(raw).strip().lower()
    if not val:
        raise RouteError("empty `access` value")
    if val in REQUIREMENTS:
        return val, False
    if val in LEGACY:
        return LEGACY[val], True
    raise RouteError(
        "unreadable `access` value %r — expected one of %s (or a legacy %s)"
        % (raw, ", ".join(REQUIREMENTS), "/".join(sorted(LEGACY))))


def preference(cfg):
    """The candidate's ordered mechanisms, validated. An unknown mechanism is refused rather
    than skipped: a preference list with a typo would silently fall through to the default and
    look like it was honoured."""
    raw = ((cfg.get("sourcing") or {}).get("route_preference")) or list(DEFAULT_PREFERENCE)
    if not isinstance(raw, list) or not raw:
        raise RouteError("config.sourcing.route_preference must be a non-empty list")
    bad = [m for m in raw if m not in MECHANISMS]
    if bad:
        raise RouteError("unknown mechanism(s) %s in config.sourcing.route_preference; "
                         "valid: %s" % (bad, ", ".join(MECHANISMS)))
    return list(raw)


def route(requirement, pref, channel_id, cfg):
    """Ordered mechanisms to TRY for this channel, most preferred first.

    `human` and `n/a` return an empty list on purpose — they are not browsable surfaces, and
    handing back a browser for a recruiter relationship would invite exactly the wrong action.
    """
    if requirement in ("human", "n/a"):
        return []
    plugins = (cfg.get("sourcing") or {}).get("plugins") or {}
    out = []
    for mech in pref:
        if mech == "plugin":
            # Only offer a plugin route when THIS channel actually has one named.
            if plugins.get(channel_id):
                out.append("plugin:%s" % plugins[channel_id])
            continue
        out.append(mech)
    return out


def load(root):
    cfg = {}
    path = os.path.join(root, "config.json")
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except FileNotFoundError:
        raise RouteError("no config.json at %s — cannot resolve any route" % root)
    rows = []
    cpath = os.path.join(root, "data", "channels.jsonl")
    try:
        with open(cpath, encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                if line.strip():
                    rows.append((n, json.loads(line)))
    except FileNotFoundError:
        raise RouteError("no data/channels.jsonl at %s" % root)
    return cfg, rows


def save(root, cfg):
    """Atomic: a half-written config.json would break every script that reads it, and the
    failure would land in the next unattended run rather than here."""
    path = os.path.join(root, "config.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def set_preference(root, order):
    """`order` is a comma-separated mechanism list. Validated BEFORE it is written — a typo
    stored in config would fall through to the default at run time and look honoured."""
    mechs = [m.strip() for m in order.split(",") if m.strip()]
    if not mechs:
        raise RouteError("no mechanisms given")
    bad = [m for m in mechs if m not in MECHANISMS]
    if bad:
        raise RouteError("unknown mechanism(s) %s; valid: %s" % (bad, ", ".join(MECHANISMS)))
    cfg, _rows = load(root)
    cfg.setdefault("sourcing", {})["route_preference"] = mechs
    cfg["sourcing"].setdefault("plugins", {})
    save(root, cfg)
    return mechs


def set_plugin(root, channel, plugin):
    """Name the plugin that serves one channel, or clear it with an empty value.

    ⚠️ The channel must EXIST. Naming a plugin for a channel that is not in the rotation writes
    a setting that will never be read, and a setting that is never read is one you will later
    believe is in force.
    """
    cfg, rows = load(root)
    known = {r.get("id") for _n, r in rows}
    if channel not in known:
        raise RouteError("no channel %r in channels.jsonl; known: %s"
                         % (channel, ", ".join(sorted(k for k in known if k))))
    plugins = cfg.setdefault("sourcing", {}).setdefault("plugins", {})
    if plugin:
        plugins[channel] = plugin
    else:
        plugins.pop(channel, None)
    save(root, cfg)
    return plugin


def main():
    ap = argparse.ArgumentParser(description="Resolve how to reach a sourcing channel.")
    ap.add_argument("--channel")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="Exit 1 if any channel's access value is unparseable or still legacy.")
    ap.add_argument("--set-preference", metavar="plugin,browser,chrome",
                    help="Set the ordered mechanism preference.")
    ap.add_argument("--set-plugin", nargs=2, metavar=("CHANNEL", "PLUGIN"),
                    help="Name the plugin serving one channel. Empty PLUGIN clears it.")
    args = ap.parse_args()
    root = profile_root()

    if args.set_preference:
        try:
            order = set_preference(root, args.set_preference)
        except RouteError as e:
            print("!! NOT CHANGED: %s" % e)
            return 1
        print("route preference is now: %s" % " -> ".join(order))
        print("Mechanisms are tried in this order; the first that works is used.")
        return 0

    if args.set_plugin:
        channel, plugin = args.set_plugin
        try:
            set_plugin(root, channel, plugin)
        except RouteError as e:
            print("!! NOT CHANGED: %s" % e)
            return 1
        print("%s: %s" % (channel, ("plugin -> %s" % plugin) if plugin else "plugin cleared"))
        return 0

    try:
        cfg, rows = load(root)
        pref = preference(cfg)
    except RouteError as e:
        print("!! ROUTE CONFIGURATION UNREADABLE: %s" % e)
        print("   Nothing can be swept until this is fixed. Do NOT fall back to a guess.")
        return 1

    problems, resolved = [], []
    for lineno, row in rows:
        cid = row.get("id") or "<no id>"
        try:
            req, legacy = parse_access(row.get("access"))
        except RouteError as e:
            problems.append((cid, lineno, str(e)))
            continue
        resolved.append((cid, row.get("type"), req, legacy,
                         route(req, pref, cid, cfg)))

    if args.channel:
        hit = [r for r in resolved if r[0] == args.channel]
        if not hit:
            bad = [p for p in problems if p[0] == args.channel]
            if bad:
                print("!! CHANNEL %s HAS AN UNREADABLE ROUTE: %s" % (args.channel, bad[0][2]))
                print("   Report this as a gap. Do not guess a mechanism.")
                return 1
            print("!! NO SUCH CHANNEL: %s" % args.channel)
            print("   Known: %s" % ", ".join(sorted(r[0] for r in resolved)))
            return 1
        cid, ctype, req, legacy, mechs = hit[0]
        print("channel      %s  (%s)" % (cid, ctype))
        print("requirement  %s%s" % (req, "   [legacy value — run migrate.py]" if legacy else ""))
        if not mechs:
            print("route        NONE — this is not a browsable surface.")
        else:
            print("route        try in order: %s" % " -> ".join(mechs))
            print("             Report which one you actually used.")
        return 0

    print("SOURCING ROUTES — requirement is the channel's; mechanism is this machine's")
    print("=" * 78)
    print("  preference: %s" % " -> ".join(pref))
    print()
    for cid, ctype, req, legacy, mechs in resolved:
        if not args.all and req in ("human", "n/a"):
            continue
        print("  %-24s %-14s %-12s %s%s" % (
            cid, ctype or "-", req, " -> ".join(mechs) or "(not browsable)",
            "  [legacy]" if legacy else ""))

    if problems:
        print()
        print("  !! %d CHANNEL(S) WITH AN UNREADABLE `access` VALUE" % len(problems))
        for cid, lineno, msg in problems:
            print("     %-24s channels.jsonl:%d  %s" % (cid, lineno, msg))
        print()
        print("  A channel that cannot be routed WILL BE SKIPPED, and a skipped channel looks")
        print("  exactly like one that was searched and found nothing. Fix these.")
        return 1

    if args.check:
        stale = [c for c, _t, _r, legacy, _m in resolved if legacy]
        if stale:
            print()
            print("  %d channel(s) still carry a legacy access value: %s"
                  % (len(stale), ", ".join(stale)))
            print("  These resolve correctly but keep the mechanism baked into the data.")
            print("  `migrate.py` rewrites them.")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
