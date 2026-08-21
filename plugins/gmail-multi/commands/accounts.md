---
description: 'Configure or check WHICH Gmail accounts the gmail-multi connector searches — add or remove an address, see whether each account''s app password is stored, and get the exact command to store a missing one. Use when a gmail-multi tool says NO ACCOUNTS CONFIGURED, when a search reports INCOMPLETE COVERAGE, or when the user wants another mailbox covered. This configures the CONNECTOR itself and needs no other plugin; a job-search profile''s mailbox list is jobsearch''s own /jobsearch:mailboxes.'
---

# gmail-multi accounts — configure the connector

Run the deterministic status first; it answers most of this without judgement:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/accounts.py" --status
```

It prints the config file path, every account the server will search, and — for any
account whose app password is missing — the exact platform command the USER runs to
store it. Never ask for, accept, or type a password yourself: the flow is always that
the tool prints the command and the person runs it (see `scripts/credentials.py`).

| the user wants | run |
|---|---|
| another mailbox searched | `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/accounts.py" --add their@address` |
| a mailbox dropped | `... --remove their@address` |
| a consumer file's mailboxes merged in (e.g. a jobsearch profile's `user.json`) | `... --include /abs/path/to/user.json` |

After any change, run `--status` again and show the user the result. The MCP server
re-reads the config on every call — no restart needed.

**Never conclude "no accounts" from an empty search result.** The server refuses loudly
when unconfigured; an empty result therefore means the accounts that WERE searched had
no matches, and the output names exactly which those were.
