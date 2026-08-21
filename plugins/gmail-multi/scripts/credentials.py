#!/usr/bin/env python3
"""Read a mail app-password from the OS credential store. macOS, Windows, Linux.

⭐⭐ THIS MODULE NEVER ACCEPTS, WRITES, PRINTS OR LOGS A SECRET.
It can READ one (to hand straight to an IMAP socket) and it can TELL YOU THE COMMAND that stores
one. It cannot store one for you, and there is deliberately no `--set` flag anywhere in this
plugin. The reason is not ceremony: a password passed as a CLI argument lands in shell history and
in the process table, where any other user on the machine can read it, and a password typed into a
chat window is retained in a transcript. Both are worse than the account's own login page.

So the flow is always: the tool prints a command, THE USER runs it, and the tool verifies a
credential now exists. Claude never sees the value at any point.

⚠️ USE AN APP PASSWORD, NEVER THE ACCOUNT PASSWORD. Google and Microsoft both issue per-app
passwords that can be revoked individually and cannot change the account's recovery settings. If
the mailbox has 2FA (it should), an app password is the only thing that will work over IMAP.

The store per platform, and why:
  macOS    Keychain via `security`          — present on every Mac, no install.
  Windows  PasswordVault via PowerShell     — built into Windows 10+, no module to install.
           (`cmdkey` is NOT usable: it stores a credential but cannot read the password back.)
  Linux    secret-service via `secret-tool` — the freedesktop standard; needs libsecret-tools.
"""

import os
import platform
import subprocess

SERVICE = "claudesearch-imap"


class CredentialError(RuntimeError):
    """Raised when a credential is missing or the store is unreachable.

    The message is always actionable — it names the exact command to run. A bare "auth failed"
    sends someone hunting through their mail provider's settings for a problem that is really an
    empty credential store.
    """


def backend():
    """Which credential store this machine uses. Override with CLAUDESEARCH_CRED_BACKEND."""
    forced = os.environ.get("CLAUDESEARCH_CRED_BACKEND", "").strip().lower()
    if forced:
        return forced
    system = platform.system()
    if system == "Darwin":
        return "keychain"
    if system == "Windows":
        return "wincred"
    return "secretservice"


def store_command(account):
    """The command the USER runs to store a password. Returns (shell, command, note)."""
    b = backend()
    if b == "keychain":
        return (
            "bash",
            "security add-generic-password -a %s -s %s -w" % (account, SERVICE),
            "Omit any value after -w. `security` then prompts, so the password never enters "
            "your shell history.",
        )
    if b == "wincred":
        return (
            "powershell",
            "[Windows.Security.Credentials.PasswordVault,Windows.Security.Credentials,"
            "ContentType=WindowsRuntime] > $null\n"
            "$pw = Read-Host 'App password' -AsSecureString\n"
            "$plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto("
            "[Runtime.InteropServices.Marshal]::SecureStringToBSTR($pw))\n"
            "$vault = New-Object Windows.Security.Credentials.PasswordVault\n"
            "$vault.Add((New-Object Windows.Security.Credentials.PasswordCredential("
            "'%s','%s',$plain)))" % (SERVICE, account),
            "Read-Host -AsSecureString keeps the password off the screen and out of PowerShell "
            "history. Run it in PowerShell, not cmd.",
        )
    return (
        "bash",
        "secret-tool store --label='%s' service %s account %s" % (SERVICE, SERVICE, account),
        "secret-tool prompts for the value. Install with `apt install libsecret-tools` or your "
        "distribution's equivalent.",
    )


def _run(cmd, shell_kind="bash"):
    if shell_kind == "powershell":
        argv = ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd]
    else:
        argv = cmd
    return subprocess.run(argv, capture_output=True, text=True, timeout=20)


def get_app_password(account):
    """Return the stored password for `account`, or raise CredentialError. Never logs it."""
    b = backend()
    try:
        if b == "keychain":
            proc = _run(["security", "find-generic-password",
                         "-a", account, "-s", SERVICE, "-w"])
        elif b == "wincred":
            proc = _run(
                "[Windows.Security.Credentials.PasswordVault,Windows.Security.Credentials,"
                "ContentType=WindowsRuntime] > $null; "
                "$v = New-Object Windows.Security.Credentials.PasswordVault; "
                "$c = $v.Retrieve('%s','%s'); $c.RetrievePassword(); "
                "Write-Output $c.Password" % (SERVICE, account),
                "powershell")
        else:
            proc = _run(["secret-tool", "lookup", "service", SERVICE, "account", account])
    except FileNotFoundError:
        raise CredentialError(
            "No credential store found for this platform (%s). Expected %s.\n"
            "Run:  python3 scripts/accounts.py --status" % (platform.system(), b))
    except subprocess.TimeoutExpired:
        raise CredentialError(
            "Credential lookup for %s timed out — a UI unlock prompt may be waiting." % account)

    if proc.returncode != 0 or not proc.stdout.strip():
        shell_kind, cmd, note = store_command(account)
        raise CredentialError(
            "No stored credential for %r (service %r).\n\nRun this yourself, in %s:\n\n%s\n\n%s"
            % (account, SERVICE, shell_kind, cmd, note))
    return proc.stdout.strip()


def has_credential(account):
    """True/False without raising, and WITHOUT holding the secret any longer than the check."""
    try:
        get_app_password(account)
        return True
    except CredentialError:
        return False
