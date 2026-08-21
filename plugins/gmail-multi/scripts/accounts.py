#!/usr/bin/env python3
"""Configure which Gmail accounts the gmail-multi connector searches.

⭐⭐ ADDRESSES ONLY — NEVER SECRETS. This tool writes email addresses to
`~/.claude/gmail-multi/accounts.json` and PRINTS the command that stores an app
password in the OS credential store. It cannot accept, store, print or log a
password itself — see scripts/credentials.py for why there is no `--set` flag.

The config file is the connector's single source of truth (the server re-reads
it on every tool call):

    {
      "accounts": ["you@example.com"],          literal addresses
      "include":  ["/abs/path/to/some.json"]    files whose addresses merge in
    }

`include` exists so a consumer plugin can DELEGATE its account list instead of
copying it (the jobsearch plugin points an include at its profile's user.json).
This tool edits only what it owns: --add/--remove touch `accounts`,
--include/--drop-include touch `include`, and nothing else in the file is
rewritten.

Usage:
    python3 accounts.py --status                 what is configured; credential presence
    python3 accounts.py --add you@example.com
    python3 accounts.py --remove you@example.com
    python3 accounts.py --include /path/to.json
    python3 accounts.py --drop-include /path/to.json

Python 3.9+. Standard library only.
"""

import argparse
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import credentials as _cred
from gmail_mcp_server import (  # noqa: E402
    AccountsError, CONFIG_PATH, UNCONFIGURED_HELP, configured_accounts,
)


def _config_path():
    return os.environ.get("GMAIL_MULTI_CONFIG", "").strip() or CONFIG_PATH


def _load():
    path = _config_path()
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise SystemExit("%s must hold a JSON object, got %s — fix or remove it."
                         % (path, type(data).__name__))
    return data


def _save(data):
    """Atomic write — a torn accounts.json would make every search fail loudly,
    which is better than silently, but better still is neither."""
    path = _config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    print("Wrote %s" % path)


def cmd_status():
    print("Config file: %s%s" % (_config_path(),
                                 "" if os.path.exists(_config_path()) else "  (absent)"))
    env = os.environ.get("GMAIL_MCP_ACCOUNTS", "").strip()
    if env:
        print("GMAIL_MCP_ACCOUNTS is set and OVERRIDES the file: %s" % env)
    try:
        accounts = configured_accounts()
    except AccountsError as exc:
        print("\n%s" % exc)
        return 1
    if not accounts:
        print("\n%s" % UNCONFIGURED_HELP)
        return 1
    print("\nAccounts the server will search (all of them, together, by default):")
    missing = []
    for acct in accounts:
        ok = _cred.has_credential(acct)
        print("  [%s] %s" % ("OK     " if ok else "MISSING", acct))
        if not ok:
            missing.append(acct)
    for acct in missing:
        shell, cmd, note = _cred.store_command(acct)
        print("\nStore the app password for %s yourself, in %s:\n\n  %s\n\n  %s"
              % (acct, shell, cmd, note))
    return 1 if missing else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--status", action="store_true")
    g.add_argument("--add", metavar="ADDRESS")
    g.add_argument("--remove", metavar="ADDRESS")
    g.add_argument("--include", metavar="PATH")
    g.add_argument("--drop-include", metavar="PATH")
    args = ap.parse_args()

    if args.status:
        return cmd_status()

    data = _load()
    if args.add:
        addr = args.add.strip()
        if "@" not in addr:
            raise SystemExit("%r does not look like an email address." % addr)
        accounts = data.setdefault("accounts", [])
        if addr in accounts:
            print("%s is already configured." % addr)
            return 0
        accounts.append(addr)
        _save(data)
        shell, cmd, note = _cred.store_command(addr)
        print("\nNow store its app password yourself, in %s:\n\n  %s\n\n  %s"
              % (shell, cmd, note))
        return 0
    if args.remove:
        addr = args.remove.strip()
        accounts = data.get("accounts") or []
        if addr not in accounts:
            raise SystemExit("%s is not in `accounts` (includes are removed with "
                             "--drop-include; an env override is unset in your shell)." % addr)
        accounts.remove(addr)
        _save(data)
        return 0
    if args.include:
        path = os.path.abspath(os.path.expanduser(args.include))
        if not os.path.exists(path):
            raise SystemExit("%s does not exist. An include that cannot be read makes "
                             "every search fail loudly — refusing to write it." % path)
        includes = data.setdefault("include", [])
        if path in includes:
            print("%s is already included." % path)
            return 0
        includes.append(path)
        _save(data)
        return 0
    if args.drop_include:
        path = os.path.abspath(os.path.expanduser(args.drop_include))
        includes = data.get("include") or []
        if path not in includes:
            raise SystemExit("%s is not in `include`." % path)
        includes.remove(path)
        _save(data)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
