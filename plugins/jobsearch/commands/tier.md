---
description: Change how often the job search runs — the main lever on cost
argument-hint: [minimal|economy|standard|full]
---

Change the user's budget tier, **and actually apply it** — the whole point is that these two stay
in agreement.

**Cost here is `runs per day × agents per run`.** Deterministic sweeps (mailbox digests, calendar
artifacts, silence detection, the dashboard) are free at every tier and always run, so a lower tier
loses timeliness, not coverage.

## 1. Show where they are

```bash
~/.claude/jobsearch/run posture.py
```

## 2. If they named a tier, set it

Edit `search.posture` in their `config.json`. Do not touch the `postures` definitions unless they
ask — those are theirs to tune.

## 3. ⭐ APPLY IT TO THE SCHEDULE — this is the step that makes it real

The tier declares a `cron`; the scheduler holds the one that actually fires. **Changing the config
alone changes nothing about what you are billed.** Update the `search-daily` scheduled task's cron
to the tier's value using `update_scheduled_task`.

⚠️ **Never change a task's schedule from inside a run of that task** — it re-arms and can fire
immediately.

## 4. Confirm the trade honestly

Tell them what they just bought or gave up, in latency rather than tokens. On `minimal`, LinkedIn
is not swept unattended at all. On `economy`, a recruiter reply may sit for hours rather than
minutes. That is usually fine for a job search — but they should hear it now, not discover it when
a message sits overnight.
