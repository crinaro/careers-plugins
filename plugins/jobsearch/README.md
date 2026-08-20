# jobsearch

A job-search assistant that runs as a set of Claude Code skills, agents, and scripts. It sources
roles, screens them against *your* stated criteria, tracks every application and conversation, and
drafts outreach in your voice — while never sending anything without you.

**This repository is the ENGINE only.** Your resume, your contacts, your compensation floors and
your search history live in a separate private repository that you own. Nothing personal is here,
and a CI gate enforces that.

## What it actually does for you

- **Sweeps your mailbox and LinkedIn** for recruiter replies, interview invitations and application
  receipts, and tells you what needs a decision.
- **Screens roles against your comp floors and geography** before they reach you, so the pipeline
  holds things you would actually take.
- **Drafts** outreach and cover letters that cite your own resume sentences rather than paraphrasing
  them, and refuses to invent facts to fill a gap.
- **Remembers** every touch, so you never send someone a cold introduction they already answered.

## Requirements, stated honestly up front

| | |
|---|---|
| **Claude Code** | any subscription tier — see *Budget* below |
| **A desktop (macOS)** | **required for LinkedIn.** LinkedIn has no API; it needs a real signed-in browser. This is the one thing that cannot run from a phone or the cloud, and it is roughly half the outreach funnel |
| **An email account** | IMAP with an app password, stored in your own OS keychain |
| **Python 3.9+** | standard library only, no packages to install |

**The engine never handles a credential.** You place them; it uses what your OS already holds.

## Budget — pick a tier that matches your subscription

Cost is essentially `runs per day × agents per run`. Deterministic work (mailbox sweeps, calendar
checks, silence detection, the dashboard) is free at every tier and carries most of the daily value.

| posture | runs/day | agents/run | unattended |
|---|---|---|---|
| `minimal` | 1 | 0 | sweeps only |
| `economy` | 2 | 1 | + LinkedIn — **the default** |
| `standard` | 3 | 2 | + research |
| `full` | 5 | 5 | + drafting |

Set `search.posture` in your `config.json`, or define your own. See
[How it works — Cost](../../docs/user/jobsearch/how-it-works.md#cost).

## Getting started

1. **Install the plugin.** See [INSTALL.md](INSTALL.md) — in short, add this repository as a
   marketplace and install `jobsearch` from it via `/plugin`.
2. **Create YOUR own private search directory**, then from inside it:
   ```bash
   ~/.claude/jobsearch/run init_profile.py --scaffold
   ```
   (If `~/.claude/jobsearch/run` does not exist yet, run
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/install_launcher.py"` once first.)
3. **The conversational half** — invoke the `onboarding` skill in Claude Code. It reads your
   resume, fills your config, and asks the handful of things a resume never says: comp floors,
   geography, and what you will NOT do.
4. **Credentials** — yours to place. See the generated `CREDENTIALS.md`.

## Understanding it

- **[How it works](../../docs/user/jobsearch/how-it-works.md)** — the engine/profile split, what runs when,
  and the rules it holds itself to: it never sends, never handles a credential, and never invents
  a fact about you.
- **[Your data](../../docs/user/jobsearch/your-data.md)** — every file it keeps, what the fields mean, and
  how to edit them by hand safely.
- **[Reading what the search produces](../../docs/user/jobsearch/reading-your-files.md)** — which file
  opens in which tool: the dashboard and its published Artifact, kb files, call preps, drafts and
  letters, on the desktop, in an editor, in a browser, or on a phone.

The one idea worth carrying into everything else: **datasets are JSON, documents are markdown.**
If it gets counted, sorted or joined it is data; if you read and edit it in full it is a
document. That is why a cover letter's text is markdown but the link between that letter and the
role is a field.

## Status

Extracted from a working single-user system that has been running daily since July 2026. It is
honest, not polished: the engine is well-tested (200+ regression tests, six CI gates) and the
onboarding path has been tested against a fresh install but **not yet against a second human**.
Expect rough edges in setup and tell me about them.

## License

Apache License 2.0 — see [`LICENSE`](../../LICENSE) at the repository root.
