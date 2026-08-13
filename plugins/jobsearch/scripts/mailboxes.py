#!/usr/bin/env python3
"""Configure which mailboxes the job search reads. Backs the `/jobsearch:mailboxes` command.

Two halves, deliberately separate:
  THE LIST   — which addresses to search. Plain config, lives in `user.json.mailboxes`. This
               script edits it.
  THE SECRETS— one app password per address. Lives in the OS credential store. ⭐ THIS SCRIPT
               NEVER TOUCHES ONE. It prints the command for you to run and then verifies that a
               credential appeared. See scripts/credentials.py for why.

⭐⭐ WHY THE LIST MATTERS AS MUCH AS THE SECRETS: a search that covers one mailbox can never prove
a message does not exist, and the failure is silent — a mailbox nobody configured returns exactly
what an empty mailbox returns. `gmail_mcp_server.py` therefore searches ALL configured accounts by
default and raises a loud INCOMPLETE COVERAGE banner for any it cannot reach. Adding an address
here is what makes it searchable; storing its password is what makes it reachable. **Both, or the
account is invisible.**

    python3 scripts/mailboxes.py --status              # what is configured and what works
    python3 scripts/mailboxes.py --add you@work.com    # add an address (prints the next step)
    python3 scripts/mailboxes.py --remove old@x.com    # stop searching it
"""

import argparse
import json
import os
import platform
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root
import credentials as cred

PROVIDER_HELP = {
    "gmail.com": ("Google", "https://myaccount.google.com/apppasswords",
                  "Requires 2-Step Verification to be ON. Workspace accounts also need an admin "
                  "to leave IMAP enabled."),
    "outlook.com": ("Microsoft", "https://account.microsoft.com/security",
                    "Create an app password under Advanced security options."),
    "hotmail.com": ("Microsoft", "https://account.microsoft.com/security", ""),
    "yahoo.com": ("Yahoo", "https://login.yahoo.com/account/security", ""),
}


def _user_json_path():
    return os.path.join(profile_root(), "user.json")


def _load():
    p = _user_json_path()
    if not os.path.exists(p):
        print("No user.json at %s.\nRun the onboarding first: /jobsearch:onboarding" % p)
        raise SystemExit(1)
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def _save(data):
    p = _user_json_path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, p)          # atomic: a partial write would destroy the profile's identity


def _provider_note(address):
    domain = address.split("@")[-1].lower()
    for suffix, (name, url, extra) in PROVIDER_HELP.items():
        if domain == suffix or domain.endswith("." + suffix):
            return name, url, extra
    return None, None, ""


def status():
    data = _load()
    boxes = data.get("mailboxes", []) or []
    b = cred.backend()
    store_name = {"keychain": "macOS Keychain",
                  "wincred": "Windows Credential Manager (PasswordVault)",
                  "secretservice": "Linux secret-service (libsecret)"}.get(b, b)

    print("MAILBOXES — %s" % _user_json_path())
    print("=" * 74)
    print("  Platform        : %s" % platform.system())
    print("  Credential store: %s" % store_name)
    print("  Service name    : %s" % cred.SERVICE)
    print()

    if not boxes:
        print("  No mailboxes configured. The search is mailbox-blind: every query will come")
        print("  back empty, which looks exactly like a quiet inbox.")
        print("\n  Add one:  python3 scripts/mailboxes.py --add you@example.com")
        return 1

    problems = 0
    for m in boxes:
        addr = m.get("address", "")
        ok = cred.has_credential(addr)
        problems += 0 if ok else 1
        print("  [%s] %-38s %s" % ("OK " if ok else "!! ", addr,
                                   "credential present" if ok else "NO CREDENTIAL — not searchable"))
        if not ok:
            shell_kind, cmd, note = cred.store_command(addr)
            name, url, extra = _provider_note(addr)
            print()
            if name:
                print("       1. Create an app password at %s (%s)." % (url, name))
                if extra:
                    print("          %s" % extra)
            else:
                print("       1. Create an app password in your mail provider's security settings.")
            print("       2. Store it yourself, in %s:" % shell_kind)
            for line in cmd.splitlines():
                print("            %s" % line)
            print("          %s" % note)
            print()

    print()
    if problems:
        print("  %d of %d mailbox(es) cannot be searched." % (problems, len(boxes)))
        print("  ⚠️ Until fixed, a search of those accounts returns empty — indistinguishable")
        print("     from no mail. Never read that as 'nothing arrived'.")
    else:
        print("  All %d mailbox(es) configured and reachable." % len(boxes))
    return 1 if problems else 0


def add(address):
    data = _load()
    boxes = data.setdefault("mailboxes", [])
    if any(m.get("address", "").lower() == address.lower() for m in boxes):
        print("%s is already configured." % address)
        return status()
    boxes.append({"address": address,
                  "kind": "consumer" if "@gmail." in address else "workspace",
                  "keychain_service": cred.SERVICE})
    _save(data)
    print("Added %s to user.json.\n" % address)
    print("⭐ It is NOT searchable yet — it still needs an app password in your credential store.")
    print()
    return status()


def remove(address):
    data = _load()
    boxes = data.get("mailboxes", []) or []
    kept = [m for m in boxes if m.get("address", "").lower() != address.lower()]
    if len(kept) == len(boxes):
        print("%s is not configured; nothing to remove." % address)
        return 1
    data["mailboxes"] = kept
    _save(data)
    print("Removed %s from user.json. It will no longer be searched." % address)
    print()
    print("⚠️ Its stored password was NOT deleted — this tool never touches the credential store.")
    shell_kind, _, _ = cred.store_command(address)
    if cred.backend() == "keychain":
        print("   To delete it yourself:  security delete-generic-password -a %s -s %s"
              % (address, cred.SERVICE))
    elif cred.backend() == "wincred":
        print("   To delete it yourself:  Windows Credential Manager > Web Credentials > %s"
              % cred.SERVICE)
    else:
        print("   To delete it yourself:  secret-tool clear service %s account %s"
              % (cred.SERVICE, address))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--status", action="store_true", help="what is configured and what works")
    ap.add_argument("--add", metavar="ADDRESS", help="add a mailbox to search")
    ap.add_argument("--remove", metavar="ADDRESS", help="stop searching a mailbox")
    a = ap.parse_args()
    if a.add:
        return add(a.add)
    if a.remove:
        return remove(a.remove)
    return status()


if __name__ == "__main__":
    sys.exit(main())
