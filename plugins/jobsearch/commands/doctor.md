---
description: Check that this job search is healthy and current with the installed plugin
---

Run the health check for the user's job-search profile, then **explain the results in plain
language** — they should never have to read a script's output to understand what is wrong.

```bash
~/.claude/jobsearch/run doctor.py
```

If that path does not resolve in the shell, fall back to locating the engine from this command
file's own directory. **Run it from the user's search directory**, never from inside the plugin —
the scripts resolve the user's profile from the working directory, and running elsewhere makes them
read an empty profile and report it as fact.

## ⭐ Verify the REAL cron — the script cannot

`doctor.py` reads the cron your tier *declares* but has no way to see what the scheduler actually
fires, so it reports that as unverifiable rather than pretending. **You can close that gap:** call
`list_scheduled_tasks` and compare the live `cronExpression` for `search-daily` against the tier's
declared cron.

If they disagree, that is the finding that costs real money — say which one is winning, and offer
`/jobsearch:tier` to reconcile them.

## Then report like a person, not a linter

Group what you found into three buckets and lead with the one that matters:

1. **Costing you money right now** — the tier declared in `config.json` disagreeing with the cron
   that actually fires. Say which one is winning and what it is costing.
2. **Broken** — a scheduled run pointing at stale paths, a missing store, data that will not
   validate. These stop the search working, silently.
3. **Yours to fix** — a missing keychain entry, no mailboxes configured. Say plainly that only they
   can do it and why it matters (no mailbox means no application receipts, no recruiter replies).

**Offer `--fix` only for the additive config gaps** it can safely repair, and say what it would add
before running it. Never present a credential problem or a tier decision as something you can fix.

If everything is clean, say so in one line and stop. Do not pad a healthy report.
