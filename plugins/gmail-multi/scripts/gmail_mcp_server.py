#!/usr/bin/env python3
"""
Multi-account Gmail MCP server (stdio, JSON-RPC 2.0) — pure standard library.

⭐ THIS IS THE `gmail-multi` CONNECTOR PLUGIN — STANDALONE BY REQUIREMENT.
It must work with nothing else installed: no other plugin, no profile directory,
no environment inherited from a shell. Its configuration is its own
(`~/.claude/gmail-multi/accounts.json`, below), and a consumer plugin that wants
to feed it accounts writes THAT file — never the other way round. This module
must never import a consumer's code or resolve a consumer's data directory.
(It was extracted from the jobsearch plugin, 2026-08-20 — ADR-004 in the
marketplace repo records the extraction and the compatibility constants.)

WHY THIS EXISTS
---------------
The claude.ai-managed Gmail connector OAuth-binds to ONE Google account. Real
correspondence often spans several:

    you@example.com       (Google Workspace)
    you@gmail.com         (consumer)

A thread living entirely on the OTHER account is invisible to every search that
covers one mailbox — and an empty result from a half-covered search looks
exactly like "no such message". That silent shape has cost real, measurable
misses; this server exists to make it structurally impossible.

DESIGN RULE THAT MATTERS MOST
-----------------------------
`account` defaults to "all". Every result is tagged with the mailbox it came
from — never conclude a message doesn't exist from a search that covered one
mailbox. Defaulting to "all" makes that structural rather than something the
model has to remember.

Corollary: a missing credential is a LOUD error naming the account, never an
empty result set. And a server with NO accounts configured refuses loudly,
naming the fix — it never serves an empty result that reads as "no mail".

WHY IMAP AND NOT THE GMAIL API
------------------------------
An OAuth app in "Testing" publishing status has refresh tokens that expire every
7 days, so it would break weekly. Escaping that requires publishing to
production, and gmail.readonly is a RESTRICTED scope, which triggers Google
verification + a security assessment. An "Internal" Workspace app avoids
verification but cannot cover a consumer account like a personal gmail.com address.

IMAP has no expiry treadmill, needs no OAuth app, preserves full Gmail query
syntax through the X-GM-RAW extension, and — unlike the managed connector —
can actually FETCH ATTACHMENTS.

CREDENTIALS
-----------
App passwords live in the OS credential store (Keychain / PasswordVault /
secret-service via scripts/credentials.py) and are read at call time. They are
never stored on disk by this plugin, never passed as command-line arguments
(that would put them in shell history / process listings), and never logged.
`python3 scripts/accounts.py --status` prints the exact store command for your
platform; on macOS it is:

    security add-generic-password -a you@example.com -s claudesearch-imap -w

(omitting a value after -w makes `security` prompt interactively, so the secret
never touches the shell history)

Python 3.9+. No third-party packages, by design.
"""

import base64
import email
import sys as _sys, os as _os
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import email.header
import email.message
import email.utils
import imaplib
import json
import os
import re
import subprocess
import sys
import tempfile

import credentials as _cred

# ⭐ COMPATIBILITY CONSTANT — the credential store IS the shared interface. The service
# name ("claudesearch-imap") is how every consumer of this connector, and every credential
# a user stored before the extraction, finds the app password for an account. Renaming it
# would fork the credential story: two places to store one mailbox's password. Never change
# it. (ADR-004 in the marketplace repo records this as part of the connector's contract.)
KEYCHAIN_SERVICE = _cred.SERVICE
IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
ALL_MAIL = '"[Gmail]/All Mail"'
DEFAULT_PROTOCOL_VERSION = "2024-11-05"

# Accounts this server searches. Emails only — never secrets.
#
# ⭐ THE SOURCE OF TRUTH IS THE CONNECTOR'S OWN CONFIG FILE, read at call time:
#
#     ~/.claude/gmail-multi/accounts.json        (override path: GMAIL_MULTI_CONFIG)
#     {
#       "accounts": ["you@example.com"],         literal addresses, and/or
#       "include":  ["/abs/path/to/some.json"]   files whose addresses are merged in
#     }
#
# An `include` entry lets a CONSUMER plugin delegate rather than copy: jobsearch points an
# include at its profile's user.json, so a mailbox added there reaches this server on the
# next call with no second bookkeeping. Each included file may carry `"accounts": [...]`,
# or `"mailboxes"` as either a list of {"address": ...} objects or a dict keyed by address
# (both shapes exist in the wild). ⚠️ An include that cannot be read or parsed is a LOUD
# error naming the path — never a silently shorter account list, because partial coverage
# reads as "no mail" (the 2026-08-05 zero-mailboxes incident, which this design replaces).
#
# Resolution order: GMAIL_MCP_ACCOUNTS env override -> the config file -> AccountsError.
# ⭐ THERE IS NO FALLBACK LIST, BY DESIGN. A hardcoded fallback made an earlier version of
# this server person-specific, and an EMPTY fallback made "unconfigured" indistinguishable
# from "no mail". Unconfigured raises AccountsError naming the exact fix.
#
# HOME-anchored, not cwd- or env-anchored, because an MCP server is spawned by the Claude
# runtime with no useful cwd and no shell environment. A durable path under ~/.claude is
# the only thing such a process can rely on.
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".claude", "gmail-multi", "accounts.json")


class AccountsError(RuntimeError):
    """No usable account configuration. The message always names the exact fix."""


UNCONFIGURED_HELP = (
    "NO ACCOUNTS CONFIGURED for the gmail-multi connector.\n"
    "This is a configuration state, not an empty mailbox — nothing was searched.\n\n"
    "Fix (either):\n"
    "  1. python3 %s --add you@example.com     (writes %s)\n"
    "  2. export GMAIL_MCP_ACCOUNTS=you@example.com   (comma-separated, env override)\n"
    "Then store each account's app password:  python3 %s --status  prints the command."
    % (os.path.join(os.path.dirname(os.path.abspath(__file__)), "accounts.py"),
       CONFIG_PATH,
       os.path.join(os.path.dirname(os.path.abspath(__file__)), "accounts.py"))
)


def _addresses_from_obj(data):
    """Pull addresses out of one parsed JSON object; tolerant of the two mailbox shapes.
    Present-but-empty is legitimate (a consumer with no mailboxes yet) and returns [] —
    require_accounts() decides loudness. Present-but-UNREADABLE never reaches here."""
    out = []
    if isinstance(data.get("accounts"), list):
        out.extend(str(a).strip() for a in data["accounts"] if str(a).strip())
    boxes = data.get("mailboxes")
    if isinstance(boxes, list):
        out.extend(m.get("address", "").strip() for m in boxes
                   if isinstance(m, dict) and m.get("address", "").strip())
    elif isinstance(boxes, dict):
        out.extend(str(k).strip() for k in boxes.keys() if str(k).strip())
    return out


def _accounts_from_config():
    """Read the connector's config file. Raises AccountsError on anything unreadable —
    an unparseable config must be LOUD, because a swallowed error here reports
    'no mailboxes configured', indistinguishable from a user who has none."""
    path = os.environ.get("GMAIL_MULTI_CONFIG", "").strip() or CONFIG_PATH
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise AccountsError("Config file %s exists but cannot be read: %s\n"
                            "Fix or remove it — refusing to guess." % (path, exc))
    if not isinstance(data, dict):
        raise AccountsError("Config file %s must hold a JSON object, got %s."
                            % (path, type(data).__name__))
    accounts = _addresses_from_obj(data)
    for inc in data.get("include") or []:
        inc = os.path.expanduser(str(inc))
        try:
            with open(inc, encoding="utf-8") as fh:
                accounts.extend(_addresses_from_obj(json.load(fh)))
        except (OSError, ValueError) as exc:
            raise AccountsError(
                "Config file %s includes %s, which cannot be read: %s\n"
                "A skipped include would silently shrink coverage, so this is an error."
                % (path, inc, exc))
    seen, ordered = set(), []
    for a in accounts:
        if a not in seen:
            seen.add(a)
            ordered.append(a)
    return ordered


def configured_accounts():
    """⭐ RE-READ EVERY CALL — never cache at import.

    This module is imported once and then serves an MCP stdio loop for the life of the
    process. Caching the account list at import meant a single bad resolution at startup
    produced a mailbox-blind server for hours: every search returned an empty result,
    which is exactly what a genuinely empty mailbox returns. Reading per call also means
    a config fixed mid-session takes effect immediately. The cost is one small JSON read
    per tool call.

    Returns possibly-[] — callers that SEARCH must go through require_accounts().
    """
    raw = os.environ.get("GMAIL_MCP_ACCOUNTS", "").strip()
    if raw:
        return [a.strip() for a in raw.split(",") if a.strip()]
    return _accounts_from_config()


def require_accounts():
    """The list, or a LOUD AccountsError. Every searching tool goes through this —
    an unconfigured server must never produce output shaped like 'no mail'."""
    accounts = configured_accounts()
    if not accounts:
        raise AccountsError(UNCONFIGURED_HELP)
    return accounts


# --------------------------------------------------------------------------
# Credentials — platform-aware, via scripts/credentials.py
# --------------------------------------------------------------------------

# One exception type across the plugin: callers that catch CredentialError keep working whether
# the store is Keychain, PasswordVault or secret-service.
CredentialError = _cred.CredentialError


def get_app_password(account):
    """Read the app password for `account` from the OS credential store.

    ⭐ Delegates to scripts/credentials.py, which speaks macOS Keychain, Windows PasswordVault and
    Linux secret-service. This used to shell out to `security` directly, which made the whole
    plugin macOS-only for no reason other than where it was first written. Never logs the value.
    """
    return _cred.get_app_password(account)


# --------------------------------------------------------------------------
# IMAP
# --------------------------------------------------------------------------

class Mailbox(object):
    def __init__(self, account):
        self.account = account
        self.conn = None

    def __enter__(self):
        pw = get_app_password(self.account)
        try:
            self.conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
            self.conn.login(self.account, pw)
        except imaplib.IMAP4.error as exc:
            msg = str(exc)
            hint = ""
            if "AUTHENTICATIONFAILED" in msg.upper() or "Invalid credentials" in msg:
                hint = (" — the app password looks wrong or revoked. Regenerate at "
                        "https://myaccount.google.com/apppasswords and update the "
                        "Keychain entry. Note: a normal account password will NOT "
                        "work; it must be a 16-character app password, and 2-Step "
                        "Verification must be on.")
            raise CredentialError("IMAP login failed for %s: %s%s"
                                  % (self.account, msg, hint))
        finally:
            del pw
        return self

    def __exit__(self, *exc):
        if self.conn is not None:
            try:
                self.conn.logout()
            except Exception:
                pass
        return False

    def select_all_mail(self):
        # All Mail so Gmail's `in:anywhere` semantics behave as expected.
        typ, _ = self.conn.select(ALL_MAIL, readonly=True)
        if typ != "OK":
            typ, _ = self.conn.select("INBOX", readonly=True)
            if typ != "OK":
                raise RuntimeError("Could not select a mailbox for %s" % self.account)

    def search(self, query):
        """Gmail query syntax via the X-GM-RAW IMAP extension. Returns UIDs."""
        self.select_all_mail()
        quoted = '"%s"' % query.replace("\\", "\\\\").replace('"', '\\"')
        try:
            typ, data = self.conn.uid("SEARCH", "X-GM-RAW", quoted)
        except imaplib.IMAP4.error:
            # Non-ASCII queries need an explicit charset.
            typ, data = self.conn.uid(
                "SEARCH", "CHARSET", "UTF-8", "X-GM-RAW", quoted)
        if typ != "OK":
            raise RuntimeError("IMAP SEARCH failed for %s: %r" % (self.account, data))
        if not data or not data[0]:
            return []
        return data[0].split()

    def fetch_headers(self, uid):
        typ, data = self.conn.uid(
            "FETCH", uid,
            "(BODY.PEEK[HEADER.FIELDS (FROM TO CC SUBJECT DATE MESSAGE-ID)])")
        if typ != "OK" or not data or not isinstance(data[0], tuple):
            return None
        return email.message_from_bytes(data[0][1])

    def fetch_full(self, uid):
        typ, data = self.conn.uid("FETCH", uid, "(BODY.PEEK[])")
        if typ != "OK" or not data or not isinstance(data[0], tuple):
            return None
        return email.message_from_bytes(data[0][1])


# --------------------------------------------------------------------------
# Message helpers
# --------------------------------------------------------------------------

def decode_header_value(raw):
    if not raw:
        return ""
    parts = []
    for chunk, enc in email.header.decode_header(raw):
        if isinstance(chunk, bytes):
            try:
                parts.append(chunk.decode(enc or "utf-8", "replace"))
            except (LookupError, UnicodeDecodeError):
                parts.append(chunk.decode("utf-8", "replace"))
        else:
            parts.append(chunk)
    return "".join(parts).strip()


def iso_date(raw):
    if not raw:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(raw)
        return dt.isoformat() if dt else None
    except (TypeError, ValueError):
        return None


def summarize(msg, account, uid):
    return {
        "account": account,
        "uid": uid.decode() if isinstance(uid, bytes) else str(uid),
        "from": decode_header_value(msg.get("From")),
        "to": decode_header_value(msg.get("To")),
        # Cc was fetched but never surfaced until 2026-07-21, which is how two
        # Aldergate contacts sat recorded as "(surname unknown)" while their full
        # names were in the headers the whole time. Display names live here.
        "cc": decode_header_value(msg.get("Cc")),
        "subject": decode_header_value(msg.get("Subject")),
        "date": decode_header_value(msg.get("Date")),
        "date_iso": iso_date(msg.get("Date")),
        "message_id": (msg.get("Message-ID") or "").strip(),
    }


def body_text(msg, limit=20000):
    """Prefer text/plain; fall back to de-tagged HTML."""
    plain, html = [], []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disp = str(part.get("Content-Disposition") or "")
        if "attachment" in disp.lower():
            continue
        ctype = part.get_content_type()
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            continue
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, "replace")
        except (LookupError, UnicodeDecodeError):
            text = payload.decode("utf-8", "replace")
        if ctype == "text/plain":
            plain.append(text)
        elif ctype == "text/html":
            html.append(text)
    out = "\n".join(plain).strip()
    if not out and html:
        stripped = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", "\n".join(html))
        stripped = re.sub(r"(?s)<[^>]+>", " ", stripped)
        stripped = re.sub(r"&nbsp;?", " ", stripped)
        out = re.sub(r"[ \t\r\f\v]+", " ", stripped)
        out = re.sub(r"\n\s*\n\s*\n+", "\n\n", out).strip()
    if len(out) > limit:
        out = out[:limit] + "\n...[truncated]"
    return out


def attachment_parts(msg):
    found = []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        filename = part.get_filename()
        disp = str(part.get("Content-Disposition") or "").lower()
        ctype = part.get_content_type()
        is_cal = ctype in ("text/calendar", "application/ics")
        if filename or "attachment" in disp or is_cal:
            found.append((decode_header_value(filename) or
                          ("invite.ics" if is_cal else "unnamed"), ctype, part))
    return found


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------

def resolve_accounts(spec):
    # ⭐ require_accounts, not configured_accounts: with zero accounts an "all" search
    # would search nothing and report "No matches" — the silent zero-mailboxes shape.
    known = require_accounts()
    if spec in (None, "", "all"):
        return known
    if spec in known:
        return [spec]
    matches = [a for a in known if a.split("@")[0] == spec or a.startswith(spec)]
    if matches:
        return matches
    raise ValueError("Unknown account %r. Configured: %s" % (spec, ", ".join(known)))


def tool_accounts(_args):
    # The diagnostic tool: unlike the searching tools it SUCCEEDS when unconfigured,
    # but its output is the loud help text, never an empty list.
    accounts = configured_accounts()
    if not accounts:
        return UNCONFIGURED_HELP
    lines = ["Configured accounts (searched together by default):", ""]
    for acct in accounts:
        try:
            get_app_password(acct)
            lines.append("  [OK]      %s — Keychain credential present" % acct)
        except CredentialError as exc:
            lines.append("  [MISSING] %s" % acct)
            lines.append("            %s" % str(exc).replace("\n", "\n            "))
    lines.append("")
    lines.append("Keychain service: %s" % KEYCHAIN_SERVICE)
    return "\n".join(lines)


def tool_search(args):
    query = (args.get("query") or "").strip()
    if not query:
        raise ValueError("`query` is required (Gmail search syntax).")
    limit = int(args.get("limit") or 25)
    accounts = resolve_accounts(args.get("account"))

    results, errors = [], []
    for acct in accounts:
        try:
            with Mailbox(acct) as mb:
                uids = mb.search(query)
                for uid in reversed(uids[-limit:]):  # newest first
                    msg = mb.fetch_headers(uid)
                    if msg is not None:
                        results.append(summarize(msg, acct, uid))
        except (CredentialError, RuntimeError, imaplib.IMAP4.error, OSError) as exc:
            errors.append("%s: %s" % (acct, exc))

    results.sort(key=lambda r: (r.get("date_iso") or ""), reverse=True)
    results = results[:limit]

    out = ["Query: %s" % query,
           "Searched: %s" % ", ".join(accounts), ""]
    if errors:
        # Loud, never silent. Partial coverage must be visible.
        out.append("!! INCOMPLETE COVERAGE — these accounts were NOT searched:")
        for e in errors:
            out.append("   " + e)
        out.append("   Results below are PARTIAL. Do not conclude a message "
                   "does not exist.")
        out.append("")
    if not results:
        out.append("No matches in the account(s) actually searched.")
    else:
        out.append("%d match(es):" % len(results))
        for r in results:
            out.append("")
            out.append("  [%s] uid=%s" % (r["account"], r["uid"]))
            out.append("  From:    %s" % r["from"])
            if r.get("cc"):
                out.append("  Cc:      %s" % r["cc"])
            out.append("  Subject: %s" % r["subject"])
            out.append("  Date:    %s" % r["date"])
    return "\n".join(out)


def tool_get_message(args):
    uid = str(args.get("uid") or "").strip()
    if not uid:
        raise ValueError("`uid` is required (from gmail_search).")
    accounts = resolve_accounts(args.get("account"))
    if len(accounts) != 1:
        raise ValueError("`account` must name ONE account for this tool "
                         "(uids are per-account). Configured: %s"
                         % ", ".join(configured_accounts()))
    acct = accounts[0]
    with Mailbox(acct) as mb:
        mb.select_all_mail()
        msg = mb.fetch_full(uid.encode())
        if msg is None:
            return "No message with uid=%s in %s" % (uid, acct)
        head = summarize(msg, acct, uid)
        atts = attachment_parts(msg)
        out = ["Account: %s   uid: %s" % (acct, uid),
               "From:    %s" % head["from"],
               "To:      %s" % head["to"],
               "Cc:      %s" % (head["cc"] or "(none)"),
               "Subject: %s" % head["subject"],
               "Date:    %s" % head["date"]]
        if atts:
            out.append("Attachments: %s"
                       % ", ".join("%s (%s)" % (n, c) for n, c, _ in atts))
        out.append("")
        out.append(body_text(msg))
        return "\n".join(out)


def tool_get_attachment(args):
    uid = str(args.get("uid") or "").strip()
    if not uid:
        raise ValueError("`uid` is required.")
    accounts = resolve_accounts(args.get("account"))
    if len(accounts) != 1:
        raise ValueError("`account` must name ONE account for this tool.")
    acct = accounts[0]
    want = (args.get("filename") or "").strip().lower()
    save_dir = args.get("save_dir") or tempfile.gettempdir()
    os.makedirs(save_dir, exist_ok=True)

    with Mailbox(acct) as mb:
        mb.select_all_mail()
        msg = mb.fetch_full(uid.encode())
        if msg is None:
            return "No message with uid=%s in %s" % (uid, acct)
        atts = attachment_parts(msg)
        if not atts:
            return "Message %s in %s has no attachments." % (uid, acct)
        saved = []
        seen_paths = set()
        for name, ctype, part in atts:
            if want and want not in name.lower():
                continue
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", name) or "attachment"
            path = os.path.join(save_dir, "%s_%s_%s" % (acct.split("@")[0], uid, safe))
            # Google sends calendar invites as TWO MIME parts (text/calendar and
            # application/ics) with the same filename. They resolve to one file on
            # disk, so report it once rather than implying two attachments exist.
            if path in seen_paths:
                continue
            seen_paths.add(path)
            with open(path, "wb") as fh:
                fh.write(payload)
            saved.append((path, ctype, len(payload)))
        if not saved:
            return ("No attachment matched %r. Present: %s"
                    % (want, ", ".join(n for n, _, _ in atts)))
        out = ["Saved %d attachment(s):" % len(saved)]
        for path, ctype, size in saved:
            out.append("  %s  (%s, %d bytes)" % (path, ctype, size))
        if any(p.lower().endswith(".ics") for p, _, _ in saved):
            out.append("")
            out.append("Calendar invite detected — the saved .ics file is the "
                       "AUTHORITATIVE date/time. Parse the .ics (DTSTART/DTEND, "
                       "with TZID) rather than trusting the email body text.")
        return "\n".join(out)


def _quote_original(msg):
    """Gmail-style attribution + quoted body, so the draft reads like a reply."""
    who = decode_header_value(msg.get("From"))
    when = decode_header_value(msg.get("Date"))
    quoted = "\n".join("> " + ln for ln in body_text(msg, limit=8000).splitlines())
    return "\n\nOn %s, %s wrote:\n%s" % (when, who, quoted)


def tool_create_draft(args):
    """APPEND a draft to [Gmail]/Drafts. Structurally cannot send."""
    accounts = resolve_accounts(args.get("account"))
    if len(accounts) != 1:
        raise ValueError(
            "`account` must name exactly ONE mailbox — a draft has to live "
            "somewhere specific. Configured: %s" % ", ".join(configured_accounts()))
    acct = accounts[0]

    to = args.get("to") or []
    if isinstance(to, str):
        to = [to]
    if not to:
        raise ValueError("`to` is required (list of email addresses).")
    cc = args.get("cc") or []
    if isinstance(cc, str):
        cc = [cc]
    body = args.get("body") or ""
    if not body.strip():
        raise ValueError("`body` is required.")
    html_body = args.get("html_body")
    subject = args.get("subject") or ""
    reply_uid = str(args.get("reply_to_uid") or "").strip()

    in_reply_to = references = None
    with Mailbox(acct) as mb:
        if reply_uid:
            mb.select_all_mail()
            original = mb.fetch_full(reply_uid.encode())
            if original is None:
                raise ValueError("No message with uid=%s in %s (reply_to_uid)"
                                 % (reply_uid, acct))
            # Message-ID and References arrive FOLDED across lines. EmailMessage
            # rejects any header containing a linefeed, so unfold to single
            # spaces before use. (Found by testing, 2026-07-21 — the first real
            # APPEND raised "Header values may not contain linefeed".)
            def _unfold(v):
                return " ".join((v or "").split())
            in_reply_to = _unfold(original.get("Message-ID")) or None
            prior = _unfold(original.get("References"))
            references = ((prior + " " + in_reply_to).strip()
                          if in_reply_to else prior) or None
            if not subject:
                osub = decode_header_value(original.get("Subject"))
                subject = osub if osub.lower().startswith("re:") else "Re: " + osub
            quote = _quote_original(original)
            body = body + quote
            if html_body:
                html_body = (html_body + "<br><br><blockquote>"
                             + quote.replace("\n", "<br>") + "</blockquote>")

        msg = email.message.EmailMessage()
        msg["From"] = acct
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["Subject"] = subject
        msg["Date"] = email.utils.formatdate(localtime=True)
        msg["Message-ID"] = email.utils.make_msgid()
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
        if references:
            msg["References"] = references
        msg.set_content(body)
        if html_body:
            msg.add_alternative(html_body, subtype="html")

        typ, resp = mb.conn.append('"[Gmail]/Drafts"', r"(\Draft)", None,
                                   msg.as_bytes())
        if typ != "OK":
            raise RuntimeError("IMAP APPEND to Drafts failed for %s: %r"
                               % (acct, resp))

    out = ["Draft created in %s -> [Gmail]/Drafts" % acct,
           "  From:    %s" % acct,
           "  To:      %s" % ", ".join(to)]
    if cc:
        out.append("  Cc:      %s" % ", ".join(cc))
    out.append("  Subject: %s" % subject)
    if in_reply_to:
        out.append("  Threaded as a reply (In-Reply-To + References set) — it "
                   "will appear inside the existing conversation.")
    out.append("")
    out.append("NOT SENT. Open Gmail, review, and send it yourself.")
    out.append("NOTE: this tool cannot delete. Get it right in one pass — a "
               "corrected copy leaves the stale one behind.")
    return "\n".join(out)


TOOLS = [
    {
        "name": "gmail_accounts",
        "description": (
            "List every Gmail account this server can search and whether its "
            "Keychain credential is present. Call this first when a search "
            "returns nothing surprising, to confirm coverage was complete."),
        "inputSchema": {"type": "object", "properties": {}},
        "handler": tool_accounts,
    },
    {
        "name": "gmail_search",
        "description": (
            "Search Gmail across ALL configured accounts at once using full "
            "Gmail query syntax (in:anywhere, subject:, from:, newer_than:, "
            "has:attachment, OR, parentheses). Defaults to every account; each "
            "result is tagged with the mailbox it came from. If an account "
            "cannot be searched, the output says so loudly — a result set is "
            "never silently partial."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "Gmail search syntax, e.g. "
                                         "'in:anywhere from:aldergate.example newer_than:30d'"},
                "account": {"type": "string",
                            "description": "Email address, its local-part, or "
                                           "'all' (default)."},
                "limit": {"type": "integer",
                          "description": "Max results, newest first. Default 25."},
            },
            "required": ["query"],
        },
        "handler": tool_search,
    },
    {
        "name": "gmail_get_message",
        "description": (
            "Fetch one message in full (headers plus decoded body, HTML "
            "stripped if there is no plain-text part) by the uid returned from "
            "gmail_search. Requires an explicit single account, since uids are "
            "per-mailbox. Lists attachment filenames if present."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "Which mailbox the uid came from."},
                "uid": {"type": "string", "description": "uid from gmail_search."},
            },
            "required": ["account", "uid"],
        },
        "handler": tool_get_message,
    },
    {
        "name": "gmail_get_attachment",
        "description": (
            "Download attachments from a message to disk and return their "
            "paths. This is the capability the managed connector lacks. For "
            "calendar invites it saves invite.ics and prints the exact "
            "parse_ics.py command to decode the authoritative date/time."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "Which mailbox the uid came from."},
                "uid": {"type": "string", "description": "uid from gmail_search."},
                "filename": {"type": "string",
                             "description": "Optional substring filter, e.g. '.ics'."},
                "save_dir": {"type": "string",
                             "description": "Directory to write into. Defaults to the temp dir."},
            },
            "required": ["account", "uid"],
        },
        "handler": tool_get_attachment,
    },
    {
        "name": "gmail_create_draft",
        "description": (
            "Create a DRAFT in a specific mailbox — including a consumer gmail account, "
            "which the managed Gmail connector cannot reach (it is OAuth-bound to "
            "one account and its create_draft has no `from` parameter). Writes by "
            "IMAP APPEND to [Gmail]/Drafts, so it is structurally incapable of "
            "sending — there is no send path in this tool. Pass reply_to_uid to "
            "thread it as a reply: In-Reply-To and References are taken from the "
            "original and the body is quoted Gmail-style. It CANNOT delete, so "
            "get the text right in one pass — a corrected copy leaves the stale "
            "one behind for the user to clean up."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "account": {"type": "string",
                            "description": "Mailbox to create the draft in. Required — 'all' is invalid here."},
                "to": {"type": "array", "items": {"type": "string"},
                       "description": "Recipient addresses."},
                "cc": {"type": "array", "items": {"type": "string"},
                       "description": "Optional Cc addresses."},
                "subject": {"type": "string",
                            "description": "Subject. Derived as 'Re: ...' from the original if omitted with reply_to_uid."},
                "body": {"type": "string", "description": "Plain-text body."},
                "html_body": {"type": "string",
                              "description": "Optional HTML alternative — use it for the signature anchor."},
                "reply_to_uid": {"type": "string",
                                 "description": "uid (in the same account) of the message being replied to."},
            },
            "required": ["account", "to", "body"],
        },
        "handler": tool_create_draft,
    },
]

HANDLERS = dict((t["name"], t["handler"]) for t in TOOLS)
TOOL_SPECS = [dict((k, v) for k, v in t.items() if k != "handler") for t in TOOLS]


# --------------------------------------------------------------------------
# JSON-RPC 2.0 over stdio (the MCP wire protocol)
# --------------------------------------------------------------------------

def respond(msg_id, result=None, error=None):
    payload = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def handle(req):
    method = req.get("method")
    msg_id = req.get("id")
    params = req.get("params") or {}

    # Notifications carry no id and must never get a response.
    if msg_id is None:
        return

    if method == "initialize":
        client_version = (params.get("protocolVersion")
                          or DEFAULT_PROTOCOL_VERSION)
        respond(msg_id, {
            "protocolVersion": client_version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "gmail-multi", "version": "0.1.0"},
        })
    elif method == "tools/list":
        respond(msg_id, {"tools": TOOL_SPECS})
    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        handler = HANDLERS.get(name)
        if handler is None:
            respond(msg_id, error={"code": -32601,
                                   "message": "Unknown tool: %s" % name})
            return
        try:
            text = handler(args)
            respond(msg_id, {"content": [{"type": "text", "text": text}]})
        except Exception as exc:  # surfaced to the model, not swallowed
            respond(msg_id, {
                "content": [{"type": "text",
                             "text": "ERROR (%s): %s" % (name, exc)}],
                "isError": True,
            })
    elif method in ("ping",):
        respond(msg_id, {})
    else:
        respond(msg_id, error={"code": -32601,
                               "message": "Unknown method: %s" % method})


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        try:
            handle(req)
        except Exception as exc:
            if isinstance(req, dict) and req.get("id") is not None:
                respond(req.get("id"),
                        error={"code": -32603, "message": str(exc)})


if __name__ == "__main__":
    main()

