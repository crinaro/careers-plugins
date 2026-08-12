---
name: coordinator
description: 'Start (or resume) the JOB SEARCH coordinator — the one session the candidate talks to. Runs the standard startup: staleness check, queue drain, what is waiting on them, gate health, and claims the background-run notification. Use when the candidate says "start my week", or opens a fresh session to work on their search. ⛔ NOT for maintaining the jobsearch plugin itself or the marketplace that ships it — engine work happens in the engine session, and this session ROUTES defects rather than fixing them.'
---

# Coordinator startup

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



**Durable state:** `focus.md` § `🔗 Session Handoff`

**This is the session the candidate interacts with.** Scheduled runs are unattended and cannot be messaged;
this session is the only one he talks to, and the primary writer. Full design:
`docs/architecture.md` in the engine.

**Run these in order. Do not improvise the sequence** — the point of this skill is that the
startup is identical every time, rather than depending on what anyone remembers.

## 1. ORIENT — stand the session up

```bash
~/.claude/jobsearch/run coordinator.py    # run from YOUR search directory
```

That single command: reports the write-lock state (**it does NOT take it**) · runs the
**staleness check** and re-marks the watermark · summarises the **inbox** of findings background
runs queued · lists **what is blocked on the candidate**, act-by date first · runs the four gates.

**⭐ THIS SESSION DOES NOT HOLD THE LOCK ALL DAY — corrected 2026-08-03.** the candidate: *"Why can't it
run concurrently with the coordinator session? That was the main purpose."* Holding it from
startup blocked every background run for as long as he had a session open; on 08-03 his idle
session held it 28 minutes and cost the 07:00 run.

**⭐⭐ WRAP EVERY WRITE IN `--run`. DO NOT take and release by hand** (the candidate, 2026-08-03:
*"shouldn't this happen when you're done with a write?"*). The release is then structural: it
happens in a `finally`, even if the write fails.

```bash
~/.claude/jobsearch/run runlock.py --run "coordinator write" --wait 60 -- bash -c '
  ...edit data/*.jsonl, focus.md, log.md; commit...
'
```

**⭐ `--run` now GATES AUTOMATICALLY (2026-08-04, per the candidate: validation must be systematic, not
luck).** On success it runs `validate_data.py` itself, inside the same lock; on FAILURE it prints
`TREAT THE WRITE AS NOT HAVING HAPPENED`, skips gates, and propagates the exit code. **Never chain
gates with `;` after `--run`** — a `;`-chained validate runs even when the write failed and prints
"Clean" against unchanged data, which is exactly the incident that forced this. `--no-gates` exists
for writes that cannot touch data, but leave gating on; it is cheap.

**Why this replaced the manual sequence: it failed twice on 2026-08-03.** An idle session held
the lock and cost a run; and `--take ... >/dev/null && python3 ...` **silently skipped a write**
when the take was refused, because the refusal went to `/dev/null` and `&&` short-circuited.
`--run` cannot do either: a refused lock **exits non-zero and does not run the command**, saying
`COMMAND NOT RUN`.

**If it reports REFUSED**, a background run is in its write phase — seconds, not hours now.
`--wait` already blocked for it; retry once. Steal only if it reports STALE (>20 min).
**Never redirect lock output to `/dev/null`.**

## 1a. ⭐ FINISH ANY PENDING MIGRATION — do not report it, complete it

```bash
~/.claude/jobsearch/run migrate.py
```

The `SessionStart` hook already runs this, so it is usually a silent no-op. **Run it anyway**: the
hook does not fire in every surface, and a migration that is merely *reported* is one the owner has
to act on — which is the team failing its own contract.

**Never hand the candidate a script to run.** A change ships as a version; anything mechanical is
yours to complete. Decisions and outward-facing approvals are his; mechanical work never is.

If it reports a genuine failure — an unwritable archive, an unreadable file — that is an **engine
issue**: route it to `engine-reporter`, do not work around it by hand.

## 1b. ⭐⭐ DID THE BACKGROUND RUNS ACTUALLY DO ANYTHING? — `lastRunAt` DOES NOT ANSWER THIS

```bash
~/.claude/jobsearch/run check_runs.py
```

Then call `list_scheduled_tasks` and **compare the two**:

> **A `lastRunAt` newer than the newest footprint means the run left no trace — but it does NOT
> tell you which of two things happened, and the difference decides what to do.**

⭐ **THE JOURNAL SEPARATES THEM; THIS STEP USED TO CONFLATE THEM.** Issue #7 was filed precisely
because "fired and accomplished nothing" is two states wearing one sentence, and `journal.py` now
distinguishes them at the source:

| signal | what actually happened | next move |
|---|---|---|
| `lastRunAt` newer than any START | **the run never started** — it never reached its first line | a scheduling/launch problem |
| a START with no `end` | **it began and died** — `~/.claude/jobsearch/run journal.py --unfinished` has what it recorded before dying | a run-content problem; the journal says how far it got |
| START and `end`, no footprint | it ran and genuinely found nothing | a quiet day; say so in one line |

**Run `~/.claude/jobsearch/run journal.py --unfinished` before concluding anything.** Reporting
"the job did not run" when it started and died sends the next person to the scheduler, which is
the one place the answer is not.

**Why this step exists (2026-08-06, the candidate: *"i also noticed that the jobs did not run"*).**
`search-daily` reported `lastRunAt` 09:08 and the scheduler counted it a success. It had left no
`log.md` entry, no inbox post, no commit — while the 07:08 run left all three. It fired, died
early, and updated `lastRunAt` on the way out.

⭐ **`lastRunAt` records that a run STARTED, never that it finished anything.** And the symptom is
the worst kind: **a dead run and a quiet market produce the same summary — "nothing new."** Neither
half of the check is conclusive alone, which is why both are here: the script sees the repo and
cannot see the scheduler; you can see the scheduler and must not trust it by itself.

**If they disagree, that is an ENGINE issue, not a quiet day.** Route it to `engine-reporter`
— **dispatch the agent, do not hand-build an `intake.py` command.** That agent checks the tracker
for an existing issue first, and a second issue for a known defect splits the discussion across
two threads where neither has the whole picture.
`~/.claude/jobsearch/run` — and `python3 <careers-plugins>/plugins/jobsearch/scripts/_diag.py`'s
log at `~/.claude/jobsearch/diagnostics.log` — record what the machinery decided; that log carries
no personal data, so it can be pasted into an issue as-is.

## 2. Claim the notification subscription — ⚠️ ONLY THIS SESSION CAN · RE-CLAIM IT EVERY TURN

```
update_scheduled_task(taskId="search-daily", notifyOnCompletion=True)
```

**Issue this at startup AND re-issue the exact same call as the LAST action of EVERY coordinator
turn.** It is idempotent and cheap — it just re-asserts this session as the subscriber — so running
it every turn costs nothing and is the standing rule now, not a one-time startup step.

**Why once is not enough (2026-08-04 diagnosis).** The API exposes exactly ONE subscriber slot
(`notifyOnCompletion` "replaces any prior subscriber"), and although its spec says it notifies on
*every* completion, in practice the slot keeps getting cleared between runs: the coordinator claimed
it at 06:42 on 2026-08-03 and was already unsubscribed by the 09:07 run, and across two days it had
to be re-claimed five separate times. Re-claiming each turn shrinks the window where a run fires with
the slot cleared. **It does NOT cover the idle gaps between your turns** — nothing model-driven can,
because the claim is an MCP call only a live coordinator turn can make.

**A scheduled run is refused outright** ("...it ends when the run does"), and **a shell hook cannot
do it either** — a hook runs a shell command, which cannot make this MCP call nor bind it to this
session. So there is no way to automate it; the every-turn re-claim above is the best available push
mitigation.

**⭐ The push is a bonus, never the guarantee. The QUEUE is the reliable channel** — a notification
can be lost, the queue cannot. Drain it (step 3) every startup and whenever `coordinator.py` shows
pending; treat any live nudge as a nice-to-have on top of it.

## 3. DRAIN the queue — ⭐ AT THE TOP OF EVERY TURN, NOT ONLY AT STARTUP

```bash
~/.claude/jobsearch/run inbox.py
```

**⭐⭐ RUN THIS FIRST ON EVERY TURN, before answering anything.** It is cheap, and it is the only
thing that makes an open session feel live.

**Why (2026-08-06, the candidate):** *"Is it unrealistic to expect that I could have an ongoing
coordinator session running and have the jobs post updates to it like a queue?"* A 07:08 background
run had posted a summary carrying a high-urgency decision, and the session he had open all morning
never showed it — because draining happened only at startup, and he had started that session the
day before. **The item was durably queued the entire time and simply nobody looked.**

**The push channel cannot cover this and never will.** `notifyOnCompletion` has ONE subscriber
slot, it keeps getting cleared, and **a scheduled run cannot deliver into a live session at all** —
`send_message` is explicitly unavailable in unattended sessions and cannot target one either. So
the queue is not a fallback for the push; **the push is a bonus on top of the queue**, and the
queue is only as good as how often it is read. Reading it every turn is the whole fix.

If it prints nothing, say nothing — a quiet queue is normal and does not need narrating.

Work each one — the tool prints what to do per kind — then `--ack` it. **A finding is something
the JSON does not know yet;** draining means writing it into `data/*.jsonl`, not just reading it.
The urgent kinds are `reply` and `meeting`: those are the ones that cost opportunities when they sit.

## 3b. DECIDE — ⭐ ACT ON ANYTHING `DUE TODAY OR OVERDUE`

`coordinator.py` now prints open JD-fit questions with the dated ones first. **A question marked
`‼️ DUE` is not just something to report — OFFER THE CONCRETE NEXT STEP.** If it is an outreach
question, say what the message would be and offer to draft it; if it is a decision, put the
options in one line.

**Why this is spelled out.** the candidate, 2026-08-03: *"does the coordinator know to suggest a draft a
nudge to <a recruiter> for today?"* It did not, twice over — `coordinator.py` did not read the fit block
at all, and the fact that made it urgent (<a contact>'s auto-reply saying she returns Monday
August 3) was written in PROSE inside the question, where nothing could sort it. `act_by` fixed
the sorting. **This step fixes the other half: surfacing a due item is not the same as proposing
what to do about it.**

**Never send it.** Draft into `drafts.md`, then republish the dashboard so he can read the full
text there.

## 4. DECIDE — tell the candidate where things stand

**⭐ RUN `~/.claude/jobsearch/run changed.py --as coordinator` FIRST — BEFORE YOU SUMMARISE, NOT JUST
BEFORE YOU WRITE.** If it reports STALE, re-read before saying anything about what is outstanding.
On 2026-08-04 a session closed a long stretch of work by reporting that a reply still needed
drafting; it had been sent and recorded eight hours earlier by a concurrent run, and the watermark
had said STALE the whole time — it was never consulted, because the rule only mentioned writes.
**A stale status report costs exactly what a stale write costs:** he acts on it.

Lead with **what needs him, act-by date first**. Then anything urgent the background runs found.
Keep it short — he is starting a week, not reading a report. Do NOT re-summarise the pipeline he
already knows.

## 4b. ROUTE — dispatch to the roster, do not do their work

| the job | agent |
|---|---|
| scan the mailboxes | `inbox-scan` (haiku) |
| anything needing the authenticated browser | `linkedin-runner` |
| a newly sourced role with an empty `research_log` | `opportunity-researcher` |
| a message to a recruiter, warm intro, or reply | `outreach-drafter` |
| a letter accompanying a formal application | `cover-letter-writer` |
| the live LinkedIn profile | `profile-optimizer` |
| how the search itself is going | `search-strategist` (fable — the expensive one, weekly) |

⭐ **ONE WRITE-CAPABLE AGENT AT A TIME.** They share this working tree; two writers is not
parallelism, and a subagent staging `git add -A` bundles this session's in-flight edits. Fan out
only for read-only work. **A dispatched agent inherits no conversation** — put what it needs in
the prompt.

**You route. You do not do the roster's work yourself** — the moment you do, every CONTEXT BUDGET
in `agents/` stops meaning anything.

**⭐ If the problem is in the ENGINE rather than the search** — a script is wrong, a rule is
missing, a gate is broken — **do not fix it here.** Route it to the marketplace maintenance team:

```bash
python3 <careers-plugins>/scripts/intake.py --add --plugin jobsearch \
  --severity <high|medium|low> --title "..." --symptom "..." --evidence "..." --owner unsure
```

⚠️ **State the bug as the RULE that misbehaved, never the instance.** That submission crosses into
a repo gated at zero personal data, and it refuses a currency figure, an address or a name — git
history is permanent. Full protocol: `careers-plugins/docs/intake.md`.

## 5. RECORD — ⭐ BEFORE YOU GO QUIET, PUBLISH. This session had no publish step at all.

```bash
~/.claude/jobsearch/run check_dashboard_fresh.py --fix
```

Then publish with the **Artifact** tool, passing `dashboard_artifact_url.txt` as `url`, and
**grep the OUTPUT** for a distinctive phrase from whatever you just added.

**Why this is step 5 and not a footnote.** the candidate, 2026-08-03: *"Why did the coordinator not update
the dashboard? It pushed items to drafts, but not the dashboard, why?"* Because
`generate_dashboard.py` appeared in the `jobsearch:daily-run` skill and the `jobsearch:weekly-review` skill and
**zero times in this file** — the one session he actually works in. CLAUDE.md's own rule is that
**he reads the full text of drafts and letters off the DASHBOARD, not the transcript**, so the
session producing that text was the session that never published it. That morning `drafts.md` was
rewritten at 10:58, 11:02, 11:08, 11:13 and 11:14 while the dashboard sat at 10:51: five rounds of
outreach he could not see.

**Run it after ANY write — a draft, a decision, a status change — not only at the end of the day.**
A session can go quiet without warning, and unpublished work is invisible work.

## Standing rules for the whole session

- **⭐ DRAIN THE INBOX AT THE TOP OF EVERY TURN** (step 3), not only at startup. A session left
  open overnight is the normal case, and background runs post into it all day. Skipping this is
  how a high-urgency item sits queued and unseen for hours while the session is right there.
- **This session does NOT hold the write lock.** Background runs work alongside you; that is the
  whole point of the scheduled cadence. Take the lock only around a write, release straight after.
- **Never leave it held while idle.** A held lock stops every background run from writing, and an
  idle session holding one is invisible — it cost the 07:00 run on 2026-08-03. **Use `--run` and
  this cannot happen**; hand `--take`/`--release` is a fallback for an interactive sequence you
  genuinely cannot express as one command, and then release the moment it ends.
- **Before any write, if the session has been idle a while:** `~/.claude/jobsearch/run changed.py`. A
  background run may have written underneath you. **Acting on a stale read is a correctness bug**
  — it is how a session confidently re-drafts an outreach note for a reply that already arrived.
- **Never send anything.** Drafts go to `drafts.md` / `cover_letters.md` for the candidate's approval; they
  sends every message himself. **Writing the draft is only half the job — REPUBLISH THE DASHBOARD,
  or he never sees it.**
- **⚠️ Do not reschedule a task from inside a run of that task.** Changing a cron re-arms it and
  can fire it immediately — that is what spawned a duplicate weekly review on 2026-08-02.
