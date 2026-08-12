---
description: Create or repair the scheduled runs for this job search
---

Create the background routines, or repair them if they have drifted. **The user should never author
or edit these prompts by hand** — they are long because every rule in them has an incident behind
it, and that is an argument for generating them, not for reading them.

## 1. What exists now

```bash
~/.claude/jobsearch/run doctor.py
```

Read the SCHEDULED RUNS and COST sections. A task marked STALE points at paths from before the
plugin install and **fails at its first step** — that is not a warning, it is a broken routine.

## 2. Create or repair

Two routines, and only two:

| task | when | does |
|---|---|---|
| `search-daily` | the tier's cron | mailbox + LinkedIn sweep, sourcing, state updates, dashboard |
| `search-strategy-weekly` | Sunday evening | channel yield, cadence, config proposals |

Use `create_scheduled_task` / `update_scheduled_task`. **Each prompt must be thin** — it declares
where the engine is and tells the run to read its instructions from there:

    ENGINE=<the plugin directory>
    Run instructions: the `jobsearch:daily-run` skill
    Scripts: ~/.claude/jobsearch/run <name>.py, run from the user's search directory

**Do not restate the run steps in the task prompt.** A duplicated instruction is a stale
instruction: a prompt that repeated the whole run drifted so far it was still telling runs to edit
files retired weeks earlier.

⚠️ **`${CLAUDE_PLUGIN_ROOT}` is EMPTY in a plain shell** — write the absolute engine path into the
task, not the variable.

⚠️ **The user's search directory is the working directory**, always. A run that `cd`s into the
plugin reads an empty profile and reports "nothing new" as fact.

## 3. Match the cron to their tier

Take it from `posture.py --cron`. If the tier and the live cron disagree, the tier is decorative and
they are paying for whatever the scheduler says.

## 4. Tell them what will now happen unattended, and when

One line per routine. If they are on a tier where LinkedIn is not swept unattended, say so.

## After the routines exist

Point the user at **`/jobsearch:linkedin`** to sign in, and **`/jobsearch:mailboxes`** to confirm
every mail account is searchable. **Both are silent failures if skipped** — an unsigned LinkedIn
session and a mailbox with no stored credential each return exactly what "nothing arrived" returns.
