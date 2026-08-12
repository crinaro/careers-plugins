# Installing jobsearch as a plugin

The engine is laid out as a Claude Code plugin: `.claude-plugin/plugin.json`, `agents/`, `skills/`,
`scripts/`, `tasks/`, `docs/`. Skills invoke their own code as
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/…"`, so the engine works from wherever it is installed.

## The two roots, and why they are different

| | resolves to | used for |
|---|---|---|
| `${CLAUDE_PLUGIN_ROOT}` | where the plugin is installed | the engine's own scripts, schemas, prompts |
| **profile root** | **your working directory** (walk up to `config.json`/`data/`), or `CLAUDESEARCH_ROOT` | your resume, config, pipeline, history |

**They are never the same directory under a plugin install.** `scripts/_root.py` enforces the
split. Conflating them does not raise — it silently reads an empty profile and reports it as fact.
That is what emptied a real dashboard on 2026-08-05.

**So: always run from your search directory.** The engine finds you; you do not point at it.

## Install

Add this repository as a marketplace in Claude Code, then install the plugin from it. Use the
`/plugin` command in an interactive session — plugin installation is an app-level action, not a
repo change, and nothing should edit `~/.claude/plugins/known_marketplaces.json` by hand.

⭐ **That gets you a `directory` source, which is valid on exactly one machine.** It is the right
thing for developing the plugin and the wrong thing for using it from a clone, a container, or the
web app — where the symptom is `Unknown command` for a correctly-spelled skill, with no error. For
anything but this machine, declare the marketplace as a **`github` source in your own repo's
`.claude/settings.json`** — see [Install](../../README.md#install). A marketplace declared from a
**private** repository additionally needs per-environment authentication before a cloud session
can reach it.

**Where this plugin can and cannot run** — in short, LinkedIn needs a signed-in desktop browser
and everything else travels. See
[How it works — What needs a desktop](../../docs/user/jobsearch/how-it-works.md#what-needs-a-desktop).

## Install the rulebook into your profile

A plugin cannot ship project context: Claude Code loads `CLAUDE.md` from the working directory,
which is your profile, never the plugin. The rulebook therefore ships as a template —
[`RULEBOOK.md`](RULEBOOK.md) at the plugin root — and reaches your sessions only as an installed
copy. From your search directory:

    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/install_rulebook.py"           # installs it as CLAUDE.md
    python3 "${CLAUDE_PLUGIN_ROOT}/scripts/install_rulebook.py" --check   # is my copy current?

The copy carries a provenance stamp naming the engine version it came from; `--check` reports
MISSING / UNMANAGED / STALE / OK. **Edit the plugin's `RULEBOOK.md`, never the installed copy** —
a re-run overwrites it. A missing rulebook fails silently (the session simply starts without
rules), which is why `--check` exists; a stale one is worse, because it is read as authoritative.

## Multi-profile (an agency running several searches)

`CLAUDESEARCH_ROOT` selects the profile explicitly, so one engine serves many:

    CLAUDESEARCH_ROOT=~/clients/alice python3 "$ENGINE/scripts/coordinator.py"
    CLAUDESEARCH_ROOT=~/clients/bob   python3 "$ENGINE/scripts/coordinator.py"

⚠️ **Cross-profile contamination is the safety-critical risk in that mode.** The drafting agents
read "the candidate's" resume; with several profiles a root bug puts one person's history into
another person's cover letter. That needs a gate before the agency case is supported for real.

## The Gmail MCP server ships WITH the plugin

`plugin.json` declares `gmail-multi`, pointing at
`${CLAUDE_PLUGIN_ROOT}/scripts/gmail_mcp_server.py`. **You do not hand-write a `.mcp.json`** —
installing the plugin wires it up. It reads your mailboxes over IMAP and has **no send path at
all**; drafts are written by IMAP APPEND, so it is structurally incapable of sending.

**Credentials are yours.** The server reads them from your OS keychain (service
`claudesearch-imap`) and the accounts come from `user.json`. There is no fallback account: if
`user.json` is missing or malformed it fails loudly rather than guessing, because silently reading
someone else's mailbox is never the wanted behaviour.

⚠️ **Claude Code will ask you to approve this server the first time.** Approve it for the project,
**not** "all future MCP servers" — this plugin is externally sourced, and a blanket approval would
let a future update register a server without asking you.
