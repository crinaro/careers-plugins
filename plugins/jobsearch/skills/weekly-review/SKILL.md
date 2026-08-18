---
name: weekly-review
description: 'Weekly strategy review: channel yield, cadence adherence, and config proposals. Invoked by the scheduled routine, or on demand.'
---

# Weekly strategy review


## ⭐⭐ THE VERY FIRST ACTION OF THE RUN — JOURNAL THE START

```bash
~/.claude/jobsearch/run journal.py --start weekly
```

Keep the run id it prints. **This is the evidence that a session existed**, and it must be written
before any work, because its whole value is being there when nothing else is.

**Why (GitHub #7).** A scheduled run can advance `lastRunAt` while **never creating a session at
all** — no step executed, nothing written. From outside that is identical to a quiet run: the task
shows enabled, correctly scheduled, recently run, and every health check passes. It was caught only
because a human noticed the sweep's effects were absent and went looking for the session.

⚠️ **`lastRunAt` is not evidence that a run occurred.** The START record is. With it, three states
that were one become three:

| | means |
|---|---|
| `lastRunAt` newer than any START | **the run never started** (#7) |
| a START with no `end` | it began and died — `journal.py --unfinished` has what it recorded (#4) |
| START + `end`, footprint empty | it ran; a quiet day is normal |

**As the run learns things, journal them immediately** — `--note` for a finding, `--gap` with a
reason code for a sweep that could not finish. Batching them to the end is the bug those exist to
fix.

**And close it:** `~/.claude/jobsearch/run journal.py --run <id> --end` as the last action. An
unmatched start is read as a death, so forgetting the end reports a failure that did not happen.

## ⚠️ HOW SCRIPTS ARE RUN HERE

**Every engine script is called through the launcher, by bare name:**

    ~/.claude/jobsearch/run validate_data.py

`${CLAUDE_PLUGIN_ROOT}` is NOT set inside a Bash tool call, and each Bash call is a FRESH SHELL, so
a variable exported once does not carry. Resolving the engine per command worked but produced a
COMPOUND command with a QUOTED path — **which a permission allowlist cannot match, so every call
stopped for approval. An unattended run that stops for a prompt is a run that does not happen.**
The launcher makes each call a single unquoted command with a stable prefix; one allowlist entry
covers all of them, and the engine can move without editing any skill.

If the launcher is missing, reinstall it with
`python3 <plugin>/scripts/install_launcher.py`.



Suggested settings: model **Fable**, weekly on the day/time `posture.py --cron` reports (this line said 5:00 PM while the rulebook said 18:00), working folder = this repo.

Run the weekly strategy review of the candidate's search. Read `CLAUDE.md` first.

## 0. START

```bash
~/.claude/jobsearch/run push_init.sh                          # mint this session's push token (a no-op that says so under local-only — adr-012)
~/.claude/jobsearch/run runlock.py --take "weekly review" --wait 120
~/.claude/jobsearch/run inbox.py                      # drain what background runs queued
~/.claude/jobsearch/run check_stale_claims.py         # decayed claims — verify against the machine
~/.claude/jobsearch/run check_followups.py            # silent threads
~/.claude/jobsearch/run check_action_claims.py        # a hand-authored ask the data already answered (#43)
~/.claude/jobsearch/run check_sections.py             # ask/commitment invariants (dev #93)
~/.claude/jobsearch/run validate_data.py              # schema · enums · referential integrity
~/.claude/jobsearch/run channels_due.py               # which sources are due
~/.claude/jobsearch/run check_rule_homes.py           # no archived lesson lost its rule
~/.claude/jobsearch/run check_engine_purity.py        # engine files carry no profile data
~/.claude/jobsearch/run check_pointers.py             # every pointer resolves to real data
~/.claude/jobsearch/run check_remote_gate.py          # is the push gate enforced on the remote?
~/.claude/jobsearch/run funnel_report.py              # what is actually working
~/.claude/jobsearch/run reconcile.py --all            # AUDIT ONLY — tracked state vs mail/LinkedIn
~/.claude/jobsearch/run compact.py --holding-lock     # retention
```

- **Lock refused → report the review SKIPPED and stop.** Do NOT downgrade to a watcher pass the
  way the daily does: an audit that quietly becomes a read-only sweep is worse than one that
  never ran, because it gets recorded as done. Reported STALE → `--steal` and say so.
  **`--release` after the commit, even if the run failed partway.**
- **`compact.py` REQUIRES `--holding-lock` here.** It takes the lock itself; this review already
  holds it, so the bare form refuses and compacts nothing. Guarded by `TestCompactionActuallyRuns`.
- **`funnel_report.py` output is EVIDENCE — hand it to the strategist.** Never ask the agent to
  re-derive channel yield by hand; the script refuses to print a rate below n=5 and states plainly
  what the data cannot answer.

Then delegate the review to the **`search-strategist`** agent: channel yield, cadence adherence,
waste, prioritized proposals.

## 1. ⭐⭐ THE DEFINING JOB: DRAIN PROCESS DEBT TO ZERO

> *"On the process report there should be zero items for you to resolve... The issues can
> accumulate during the week from the daily runs but zero should exist after the weekly run."*

Process debt is a **weekly work queue, not a museum.** Daily runs accumulate observations; **this
run must leave nothing local.**

> **⭐ CHANGED 2026-08-06 — ENGINE ITEMS ARE NO LONGER FIXED HERE, THEY ARE FILED.**
> `Process → 🔧 Open` and its dashboard tab are retired. This search does not own the engine, and
> carrying a local list of engine defects meant two places to look, one of which went stale. **A
> capability's defects belong on that capability's tracker.** Dispatch **`engine-reporter`**: it
> reads what actually happened, checks the plugin's open issues so it does not duplicate one, and
> hands back ready-to-file proposals. **Confirm with the candidate, then file with the
> `report_issue.py`.** `check_process_debt.py` still runs and still exits 1 on a leftover
> local section, so a half-migrated profile fails loudly instead of quietly keeping two lists.

Every item leaves exactly one of four ways:

1. **FILED** — it is the engine's fault → an issue on the plugin's repo, then archive the entry
   with the issue number. **⚠️ State it as the RULE that misbehaved, never the instance** — the
   intake tool refuses a comp figure, address, phone or name, and git history is permanent.
2. **FIXED HERE** — it is this profile's data, config or cadence → do it, **verify by running the
   thing**, archive.
3. **NOT MINE** — needs a decision, credential, or fact only the candidate has → move it to
   `Process → ⚡ Needs the candidate`, which renders in **Your Move**. ⚠️ **Never file one of these
   as an engine issue**: no issue on the engine repo can put a credential in someone's keychain.
4. **WON'T FIX** — one line saying why, then archive. A declined item is closed.

**⚠️ BEFORE ARCHIVING, CHECK THE LESSON HAS A DURABLE HOME** — `CLAUDE.md`, an agent definition,
a task prompt, a config key, or a test. **Archiving an item whose rule lives nowhere else DELETES
the knowledge, and a rule that only ever lived in a tracker bullet was never really a rule.**

## 2. FIX IT YOURSELF — the approval gate is narrower than it looks

> *"if they are items you can fix, why are they not fixed?"*

**Do not hand the candidate a queue of things you could have done.** Fix your own bugs, scripts,
data and cadences; run the tool to verify; report them DONE.

**Genuinely needs the candidate:** sending or publishing anything outward-facing · applying to a role · a
fact only they have · a judgment only they can make (pursue/pass, comp tradeoffs, which firms to
approach). **A decision they have already pre-decided by setting a rule is NOT a new decision** — if
their own stated rule determines the answer, the answer is arithmetic, not an approval.

**The one real gate:** the *strategist* must not silently rewrite task prompts, agent definitions
or scripts on its own say-so. You verifying a claim and then fixing the thing yourself is not that.

## 3. VERIFY, DON'T QUOTE

Any claim that something is broken, unapplied, or never ran **must be verified against the machine
in this run — cite the command.** Prefer *"verified X on `<date>` by `<command>`"* over *"the
tracker says X."* The failure this prevents is **an assertion written once and read forever**: a
review once quoted a hardcoded sentence in a script as a finding, and it was false.

## 4. RECONCILIATION IS AN AUDIT, NOT THE OPERATING PATH

> *"that should only be used for a reconciliation mode. The process should be using the json data
> and making incremental updates during the daily runs."*

`data/*.jsonl` is the **system of record**; the mailbox is the **system of truth**, consulted
periodically to catch drift. So `reconcile.py` belongs here, weekly — not in the daily hot path,
where it would be slow, redundant, and would quietly excuse the daily run from keeping the JSON
current.

**Anything it finds is a PROCESS FAILURE, not a routine result.** An unrecorded reply means the
daily run that should have written it missed something — log that, don't just apply the fix.

## 5. TESTS AND SEPARATION

  fix a bug, add a case** rather than only writing a paragraph. A rule in prose is re-interpreted
  every run; a test fails loudly and for free.
- `~/.claude/jobsearch/run check_profile_leakage.py` — `config.json` is the single source of truth for
  values. **Never retype a comp figure; run `scripts/profile.py`.**
- `~/.claude/jobsearch/run check_engine_purity.py` — agent specs, task prompts and ADRs must carry no
  profile data. This is the honest measure of how packageable the system is for a second person.

## 6. ⭐ STANDING WEEKLY ACTION — ONE TOUCH ON THE TOP PROACTIVE CHANNEL

The proactive channel is the one that goes quiet without a standing commitment, whichever
channel that is for THIS profile. **Every weekly review proposes ONE named action on the
profile's highest-priority proactive channel** (the channel-priority rule in `CLAUDE.md`, read
against `data/channels.jsonl`). For a search where retained/executive-search firms lead, that
is a retained-firm touch — a new firm to register with, or a warm reconnect with a dormant
`type: recruiter` contact. For a search whose configured channels lead elsewhere (direct
applications, a professional community, a staffing agency), it is the equivalent named touch on
that channel. Either way: an `outreach-drafter` draft in `drafts.md` ready to go, and **name
the specific firm or contact; "do more networking" is not an action.** An earlier version
mandated a retained-firm touch unconditionally — one person's executive search baked in as
every installation's standing weekly action.

## 7. FINISH

Append the review summary to `log.md` → regenerate the dashboard
(`~/.claude/jobsearch/run check_dashboard_fresh.py --fix`) → **grep the OUTPUT for what you added** →
publish via the Artifact tool → commit (explicit paths) →
**`~/.claude/jobsearch/run sync.py --end-of-run`** → `--release` the lock.

**`~/.claude/jobsearch/run sync.py --end-of-run` replaces the old unconditional push (ADR-012).**
Under a declared `remote` mode it delegates to `push.sh`, which reads this session's
`.git/push_token`, minted at run start — a bare `git push` still fails by design: the `pre-push`
hook requires the per-session token, and no fixed bypass constant exists any more. Under
`local-only` it prints the line the review summary must carry verbatim: *committed locally; not
pushed — this profile declares `sync.mode: local-only`*. **Copy its output into the summary
either way**, and treat **NOT PERSISTED** (a failed push) or **COMMIT-ONLY** (`undeclared` /
`mismatch` / unreadable) as findings the summary states loudly — never push by hand on a guess.
