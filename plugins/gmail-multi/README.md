# gmail-multi — a multi-account Gmail connector

The managed Gmail connector OAuth-binds to **one** Google account. This plugin covers
**all of yours in a single query** over IMAP: every search runs across every configured
account, every result is tagged with the mailbox it came from, attachments actually
download, and drafts land in `[Gmail]/Drafts` through a path that is structurally
incapable of sending.

Standalone by design — it needs no other plugin and no profile directory. Other plugins
in this marketplace (jobsearch) consume it as a dependency, but nothing about it assumes
a job search.

## Setup

1. Install the plugin. Then, in a session: `/gmail-multi:accounts`, or directly:

   ```bash
   python3 scripts/accounts.py --add you@example.com
   python3 scripts/accounts.py --status
   ```

2. `--status` prints the exact command to store each account's **app password** in your
   OS credential store (Keychain / PasswordVault / secret-service). **You** run that
   command; the plugin never accepts, stores, prints, or logs a secret, and Claude never
   sees one. Use an app password, never the account password — 2-Step Verification must
   be on.

Accounts live in `~/.claude/gmail-multi/accounts.json`. A consumer plugin can delegate
its own account list via that file's `include` field instead of copying addresses; the
server re-reads the file on every tool call.

## The rule the design serves

**A missing thing must never read as an empty thing.** An unconfigured server refuses
loudly and names the fix; an account that cannot be searched makes the result say
`INCOMPLETE COVERAGE` and name the account — a result set is never silently partial,
because "no matches in half your mail" is indistinguishable from "no matches".

## Tools

`gmail_accounts` · `gmail_search` · `gmail_get_message` · `gmail_get_attachment` ·
`gmail_create_draft` — see the schemas in `scripts/gmail_mcp_server.py`.

Python 3.9+, standard library only. Deployment surface support: `docs/deployment.md` in the
source repository (a maintenance artifact — an installed copy does not carry it).
