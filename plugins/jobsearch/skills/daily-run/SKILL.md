---
name: daily-run
description: 'Run one job-search sweep: mailbox, LinkedIn, sourcing, state updates, dashboard, and a summary to the coordinator queue. Invoked by the scheduled routine, or on demand.'
---

# Daily run


## Binding — say which profile this session is acting on (dev #150)

```bash
~/.claude/jobsearch/run binding.py
```

One line, before the journal start: it names the profile and the evidence (`cwd`, `env`, or
`pointer`). Invoking this skill by name is itself evidence of intent, so `pointer` does not
refuse here — but it is announced, never silent. **`NO PROFILE` (exit 3) means stop and say
so.** When you later dispatch a jobsearch agent from a `pointer`-bound session, name the
profile root in the dispatch prompt — agents refuse pointer-only binding, and the prefix
`CLAUDESEARCH_ROOT=<root>` on their commands is how a legitimate dispatch binds them.

## ⭐⭐ THE VERY FIRST ACTION OF THE RUN — JOURNAL THE START

```bash
~/.claude/jobsearch/run journal.py --start daily
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



Model **Sonnet** · schedule from `~/.claude/jobsearch/run posture.py --cron` (never retyped here — this line said 07:00–16:00 while the generator emitted 07,09,11,13,15) · working folder = this repo.

Run the daily job-search check for the candidate. Read `CLAUDE.md` first — it governs everything.
All state lives in this repo. Be token-frugal.

## Three invariants

1. **STEP ORDER IS LOAD-BEARING.** Every mutation happens BEFORE the dashboard is generated, and
   the commit is genuinely LAST. The opposite arrangement once shipped: the dashboard and commit
   ran mid-run while later steps wrote `channels.jsonl` and `opportunities.jsonl`, so those writes
   were never committed and never published.
2. **REFERENCE STEPS BY NAME, NEVER BY NUMBER.** A numeric cross-reference goes stale the first
   time anything is renumbered, which already sent one "still do step N" pointing at the wrong step.
3. **USE A SINCE-LAST-RUN WINDOW** (check `log.md` for the last timestamp), never a flat 24h
   lookback — back-to-back runs (`posture.py --cron` sets how many a day) would each re-scan the
   same ground. **A quiet run is the NORMAL outcome at this cadence.** Say so in one line and
   stop. **Do NOT pad it with make-work** — no re-researching settled roles, no speculative
   outreach, no padding `data/asks.jsonl` because the summary felt thin. The value of frequency is
   catching a reply within one run interval, not multiplying the work. A late run (the app was
   closed) is a "catch-up run"; the window handles the gap.

---

## 0. CHECK THE LOCK — DO NOT TAKE IT

```bash
~/.claude/jobsearch/run runlock.py --status
```

**⭐ THE READ PHASE RUNS UNLOCKED, ALWAYS, even while the candidate has a session open.** Running
*alongside* them is the point. This step used to take the lock for the whole run, which serialised
every run against their session: an idle coordinator held it from 07:02 and nothing could write for
hours, costing that morning's run outright.

- **UNLOCKED or LOCKED — PROCEED either way.** Note who holds it.
- **Carry forward:** if it was held, expect state to have moved during your read phase. UPDATE
  STATE re-checks with `changed.py` before writing.
- **Degrade only if the mailbox is unreachable** (credentials `[MISSING]`): run
  `~/.claude/jobsearch/run watch.py --since 3` to queue what you can, and say so.

## 1. HYGIENE — before any new work

```bash
~/.claude/jobsearch/run push_init.sh                        # mint this session's push token (a no-op that says so under local-only — adr-012)
~/.claude/jobsearch/run check_stale_claims.py       # decayed claims
~/.claude/jobsearch/run check_followups.py          # silent threads
~/.claude/jobsearch/run check_sections.py           # ask/commitment invariants (dev #93)
~/.claude/jobsearch/run check_action_claims.py      # a hand-authored ask the data already answered (#43)
~/.claude/jobsearch/run validate_data.py            # schema · enums · referential integrity
~/.claude/jobsearch/run channels_due.py             # which sources are due
~/.claude/jobsearch/run check_rule_homes.py         # archived lessons still have a home
~/.claude/jobsearch/run check_dashboard_fresh.py    # dashboard behind its sources, or PUBLISHED view behind the repo (dev #133)
~/.claude/jobsearch/run check_engine_purity.py      # engine files carry no profile data
~/.claude/jobsearch/run check_pointers.py           # every pointer resolves to real data
~/.claude/jobsearch/run knowledge.py                # kb/prep joins resolve; promotion debt; kb files due
```

**Dispose of what they report BEFORE starting.** Verify every **system-state** claim against the
machine — read the plist, tail the log, run the script — and correct the tracker line in the same
pass. **`validate_data.py` is the only one that gates**; the rest are advisory and exit 0 so they
cannot wedge an unattended run.

## 2. DRAIN THE QUEUE

```bash
~/.claude/jobsearch/run inbox.py
```

Findings a previous run queued rather than wrote, plus run-summaries. Work each one — the tool
prints what to do per kind — then `--ack` it. **A reply or a meeting artifact is the urgent kind;
those are what cost opportunities when they sit.**

## 2b. ⭐ DRAIN THE *REQUEST* QUEUE — work asked of this laptop (added 2026-08-05)

```bash
~/.claude/jobsearch/run deferred.py --claimable
```

**`inbox.py` above carries findings TO the owner. THIS carries requests FROM the owner to this laptop, and it was WRITE-ONLY until today.** The run
prompt called `deferred.py --add` and nothing ever called `--claim`/`--done`, so every request
queued for the laptop sat forever — three were pending when this was found, including a LinkedIn
sweep queued when the browser died. **A request channel nobody drains is worse than none: the
asker believes the work is scheduled.**

For each item THIS worker can run (`--claimable` filters by capability, so only take what you can
finish): **`--claim <id>` → do the work → `--done <id>`.** If you claim and cannot finish,
`--release <id>` — *a claimed task that fails looks handled, which is the worst state.* Leases
expire in 45 minutes so a dead worker cannot strand work.

**Items needing a capability this worker lacks stay unclaimed and are REPORTED in the summary**
("2 items need chrome; this worker has none") rather than silently skipped — that visibility is
the whole point of `whoami.py` declaring capability instead of everyone attempting everything.

## 3. GMAIL — two passes, and the order matters

**a. ALERT SWEEP — run this DIRECTLY, do NOT delegate it.**
`~/.claude/jobsearch/run alert_sweep.py` (`--days 1`; widen if a run was skipped). It deterministically
finds board and aggregator digests across EVERY configured mailbox. Read them yourself and cross-check
against the pipeline — **the exclusion list is `verdict: pass` OR `status: passed`.** This is a
script because a model summary once reported these "silent" for three consecutive runs while the
digest sat in the mailbox: **a daily, predictable artifact is a query, not a summary.** A non-zero
exit means an account could not be searched — the result is PARTIAL, never a zero.

**b. ⭐ WRITE IT DOWN NOW — the JSON is the OPERATING STORE.** The daily run does not re-derive
state from the mailbox; it reads `data/*.jsonl` and writes changes **immediately**:

| finding | write |
|---|---|
| a **reply** | the outreach row's `outcome` + `responded_on`, AND the message into `data/messages.jsonl` (`direction: inbound`, with `source`) |
| a **new person** | a `contacts[]` entry with `contact_id`, structured `email`/`linkedin`, and the outreach row's `contact_id` pointing at it |
| a **meeting booked** | advance `stage`, add it to This Week |
| **the candidate reports sending** | the outreach row AND the draft body moved into `data/messages.jsonl` (`direction: outbound`) |

**Never leave a change for the weekly audit to find.** A reply once sat unrecorded for 11 days
while the knowledge was already in the repo, written in one place and not the other.

**⭐ READ THE WHOLE THREAD, BOTH DIRECTIONS — the candidate may have ALREADY REPLIED (added 2026-08-05, per
the candidate: "enhance the daily process to also look at additional items in an email thread,
responses I may have already made").** An inbound reply is not automatically an open loop. Before
you treat one as needing a response, **fetch the full thread and look for a message the candidate
sent AFTER the inbound** — search the candidate's sent mail in that thread (`from:<candidate's address>` on the same
subject, or `in:sent`/`in:anywhere`). Two outcomes:
- **They already replied** → record BOTH directions into `data/messages.jsonl` (the inbound AND their
  reply, each with its `source` uid), set the outreach row to reflect the latest state, and **do
  NOT queue a needs-response or spawn a drafter** — the loop is closed. Note "candidate already
  replied" in the run summary so the candidate knows it was seen, not missed.
- **The last word is theirs** → proceed to §7b (queue + draft).
This happened 2026-08-05: Ashford Search's <an employer> decline landed overnight and the candidate replied ~1 hour
later; the scan saw only the inbound and nearly drafted a response to a thread the candidate had already closed.

**c. HUMAN/MEETING PASS — delegate to `inbox-scan`.** New recruiter/human inbound, thread replies,
and — swept FIRST per the hard rule — meeting artifacts. **It is NOT trusted for the digests**
(pass a owns those); brief it so it neither re-reports nor skips them.

## 4. LINKEDIN — delegate to `linkedin-runner`

**⭐⭐ THE RESPONSE SWEEP IS DRIVEN BY THE OUTREACH STATE, NOT BY UI SURFACES (2026-08-04, per the
candidate: "if our process has me sending messages & connection requests, it should be checking
linkedin messages").** The old instruction said "reply check on open outreach + message inbox,"
and a hiring-line response from a 3rd-degree contact sat unseen because it did not live
in either place — a 3rd-degree recipient can REPLY TO AN INVITATION WITHOUT ACCEPTING IT, and
that response lands on the invitations/message-requests surfaces the sweep never opened.
**Mail is no backstop: the candidate's mailbox receives NO LinkedIn notification emails** (verified
2026-08-04 — zero in 2 days despite two known acceptances), so the browser pass is the ONLY
detector for LinkedIn events.

**The sweep, every run:**
1. Build the check-list FROM THE DATA: every `outreach[]` row with medium `linkedin-*` and
   `outcome` in (`awaiting`, `accepted`) — `~/.claude/jobsearch/run pipeline_index.py` or the JSONL.
2. For EACH person on that list, open their thread via profile → Message (in-app message SEARCH
   false-negatives; never use it to conclude absence) and report replied / accepted / no change.
3. Then the four surfaces, **INBOX FIRST**: the **Focused AND Other inbox tabs**, **message
   requests**, the notification bell, and **Sent invitations LAST — it truncates.** Measured at
   10 of 20 (08/02) and 10 of 25 (08/04): it caps near ten while the pending list grows, so the
   share it hides widens with every send. **Absence from Sent Invitations proves nothing.** A
   hiring-line reply was invisible there on 08/04 while sitting plainly in the inbox. An invitation REPLY without an accept is a first-class response — it is how a senior
   3rd-degree contact answers without connecting.
4. Anything found goes into the run's incremental write (`outcome`, `responded_on`,
   `messages.jsonl` with the verbatim text) — not just the summary.

Job search since the last successful run (remote AND on-site/hybrid within the commute anchor —
the JOB SEARCH capability covers both), contact-path lookup for any new appealing role.

**If it reports BROWSER UNAVAILABLE or NOT SIGNED IN**, flag it at the top of the summary, tell the candidate to run **`/jobsearch:linkedin`** (a lapsed session is the commonest cause and they must sign in directly), **and QUEUE the work:**

```bash
~/.claude/jobsearch/run deferred.py --add "LinkedIn pass: reply check + inbox + job search" \
    --why "Chrome extension unreachable this run"
```

**Do not skip the queue step.** That flag used to be prose in a run summary and nothing else — no
later run recovered the work, and a skipped pass looked identical to one that found nothing.
Degrading gracefully was never the problem; forgetting was. **Expect this to fire rarely** — if it
fires repeatedly, run `scripts/wake_chrome.sh` and check macOS Automation access for the app
before assuming a new fault (`docs/architecture.md` §3d).

**Weekly (first run on/after Monday, per `log.md`):** dispatch `board-sweeper` for the non-LinkedIn sourcing pass — boards, aggregators and employer career pages, each reached by its configured route.
**Check `~/.claude/jobsearch/run channels_due.py` rather than assuming anything is due** — anchor-employer
career pages moved to MONTHLY, and two job boards were RETIRED after a zero-yield trial. Do not
re-add a retired channel.

## 5. RESEARCH — delegate to `opportunity-researcher`

For every genuinely new role, in one batch. It finds the posting on the company's own site/ATS
(not the alert snippet), reads the full JD, and researches the company. Fold its findings into the
role's record — comp, scope, reporting line, company snapshot, and any discrepancy versus the
alert. **Skip roles already excluded or already researched.** An alert's title/company/comp snippet
alone cannot support a fit judgement or a draft.

**⭐ ANY ROLE THAT BECOMES A PURSUIT GETS A `fit` ANALYSIS** (shape in `docs/schema.md`):
requirement → aligned/partial/not-aligned/unknown, each with cited evidence and a `pitch_line`.
**Every `unknown` carries a targeted question, and a dated one carries `act_by`.** Run
`~/.claude/jobsearch/run fit_report.py --gaps` and put the open questions on Your Move — the answers file
into `projects.md`, `resume.md`'s addenda, or `kb_<company>.md`, so the NEXT role's analysis starts
fuller. **The resume is deliberately incomplete; this is the loop that closes that gap while a live
role makes it concrete.**

## 6. NETWORK CADENCE

From `network.md`: check warm-intro deadlines. If fewer than 2 network actions in the last 7 days
(per `log.md`), propose the next 1–2 with drafts.

**Any draft must be saved IN FULL to `drafts.md`** (its header carries the entry format — **the
body MUST be `> `-blockquoted or it publishes EMPTY**). A one-line summary elsewhere is not enough;
**the candidate reads the full text off the dashboard, not the transcript.** Cover letters go to
`cover_letters.md` via **`cover-letter-writer`** — a different artifact with different length,
constraints and failure mode.

**⭐ WARM-PATH ON EVERY APPLICATION.** An ATS application is NOT done until an inside-human touch
is attempted OR explicitly ruled out. The evidence: 11 ATS applications produced 9 auto-acks, 1
rejection and ZERO human contact, while warm outreach replied at 50% and produced every advancing
conversation. So for each role applied to, have `linkedin-runner` run a contact-path lookup and,
if a real path exists, draft the touch; if none exists, say so in the row. **Never fabricate a
connection.** This is per-application, not weekly.

---

## 7. UPDATE STATE — ⭐ THE WRITE PHASE BEGINS HERE

```bash
~/.claude/jobsearch/run changed.py --as daily
~/.claude/jobsearch/run runlock.py --take "daily $(date +%H:%M) write" --wait 120
```

- **STATE CHANGED** → **re-read the files you are about to edit.** Your reads are as old as this
  run is long. Acting on a stale read is how a run re-drafts outreach for a reply that already landed.
- **`--wait 120`** blocks rather than discarding the run. Holders release in seconds now.
  **Do NOT downgrade here** — the research is already done, and throwing it away is the failure
  this design removed.
- **Still refused, or reported STALE** → a session died holding it. `--steal`, and say so.

**⭐ USE THE WRITE API — do not hand-edit the JSONL. A brand-new row is `create`, not an edit.**

**⭐ `--already-locked` on every call in this phase.** This run took the run lock two commands
ago, and record.py normally takes it again — waiting on your own hold never ends (public #17).
The flag says "write under the run's hold"; record.py verifies a hold actually exists and
neither takes nor releases anything.

```bash
~/.claude/jobsearch/run record.py create <opp_id> '{"company_id":"...","title":"...","status":"backlog","stage":"sourced","verdict":"undecided","jd_url":null,"location":{...},"sightings":[...],"next_action_owner":"..."}' --already-locked
~/.claude/jobsearch/run record.py set <opp_id> stage screening --already-locked
~/.claude/jobsearch/run record.py set-in <opp_id> outreach contact_id=<cid> outcome replied --already-locked
~/.claude/jobsearch/run record.py append <opp_id> research_log '{"date":"...","note":"..."}' --already-locked
```

It re-reads inside the lock, writes **atomically** (`os.replace` — a partial write would
destroy every record in the file), and validates. **A full cycle is ~0.2s.** Ad-hoc
`read-all / write-all` is what forced the lock to be coarse in the first place. **`--dry-run`
describes the change without touching anything.** (Outside a lock-holding run — an interactive
one-off — drop the flag and record.py takes and releases the lock itself, in milliseconds.)

Then: remaining edits to `network.md`; record any new cross-cutting ask in `data/asks.jsonl`
(kind: role|system — an ask leaves by setting `resolved_on`+`resolution`, never by rewriting
its text). **⭐ A decision ask requesting a specific action on a linked role — "approve
applying?", "approve this outreach?" — carries `resolves_when` (`application`|`outreach`) plus
`opp_id`: then `record.py`'s write that records the action resolves the ask in the same locked
transaction, and no drift window exists (dev #133).** Then any newly confirmed meeting in
`data/commitments.jsonl` (date verified from the
.ics, never recall); rewrite `handoff.md` — the letter to the next session; append a `log.md`
entry. **All mutation belongs in this step and the four below — nothing after
DASHBOARD may change tracked state.** Keep the window to COMMIT tight; it should be minutes.

## 7b. ⭐ WHEN A REPLY ARRIVES — RECORD IT, THEN DRAFT THE RESPONSE (added 2026-08-04, per the candidate)

**The candidate: "that process or the coordinator should suggest a response." It lives HERE, because a
reply can land at 9am while no coordinator session is open — a suggestion that waits hours for a
session is a post-mortem, not a suggestion.**

For every INBOUND reply this run discovers (Gmail or LinkedIn), after the incremental state write:
0. **⭐ FIRST confirm the candidate hasn't already answered it** (the thread-completeness check in §3b):
   pull the full thread and look for a message the candidate sent AFTER the inbound. If so, record both
   directions, note "already replied" in the summary, and **STOP — do not queue or draft.** Only if
   the last word is theirs do steps 1–3 apply.
1. **Queue an URGENT `reply` finding** with the sender, the role, and a ONE-LINE proposed response
   angle — this reaches the candidate's device via the completion notification.
2. **If the reply plainly calls for an answer** (a question, an objection, proposed times, a
   referral pointer), **spawn `outreach-drafter` to draft the response into `drafts.md`**, marked
   `**AUTO-DRAFT from a background run — review extra carefully before sending.**` A draft is
   inert by design; the send is always the candidate's. Skip drafting only when the reply closes the
   thread (a rejection, a "not my search" with no pointer) — then the queue line says so instead.
3. **Regenerate + verify the dashboard** (the DASHBOARD step covers it) so the draft is readable there.

⚠️ The drafter must read the SENT thread from `outreach[]`/`messages.jsonl` so the response builds
on what was already said — never a fresh introduction (the Part-B lesson, 2026-08-04).

## 8. MEETING CROSS-CHECK

`~/.claude/jobsearch/run meeting_check.py` — sweeps every configured mailbox for calendar artifacts and diffs them
against This Week on **date AND counterparty**. **Every meeting miss in this repo's history would
have been caught by this.** It reports artifacts TO GO READ; the authoritative time comes from
`gmail_get_attachment` + `parse_ics.py`, because a subject line can be weeks stale while the newest
message schedules something tomorrow. **An artifact carrying an `.ics` with no readable date is an
UNKNOWN, never a pass**, and two artifacts sharing an iCalendar UID are ONE meeting revised —
highest `SEQUENCE` wins. Anything it surfaces goes into `data/commitments.jsonl` (the store
behind the This Week tab) before the dashboard.

**⭐ BEFORE PROMISING A CALL-PREP NOTE, CHECK `call_preps/` — ONE MAY ALREADY EXIST.** For any
upcoming call, `ls call_preps/` (files are named `call_prep_<date>.md`) before writing "prep note
to follow" anywhere — This Week, the summary, or the handoff. If a note for that date exists, **link
to it, never re-promise it.** Added 2026-08-04 after this run told the candidate "prep notes to follow before
Wed AM" for two calls whose prep note (`call_prep_2026-08-05.md`) had been written the day before,
and This Week carried the same stale "I'll prep a note" promise on both. A promise from a stateless
background run to do future work is the failure mode this whole design avoids — the artifact already
on disk is the source of truth; verify it before asserting work is outstanding.

**⭐ A PREP NOTE JOINS THE PIPELINE; ITS ARCHIVE RECORDS THE PROMOTION** (issue #12 — both
stores looked populated and answered nothing). Every prep note carries a `**Companies:**`
line of `company:<id>` token(s) (`channel:<id>` for a call with a recruiting firm; the literal
`none` if genuinely untracked) — the note is date-keyed on purpose, so this line is the ONLY
path from a pursuit to its conversation history. Before a prep is archived, promote durable
content to `kb/<company_id>.md` and record `**Promoted:** kb:<id> on <date>` (or
`nothing-durable`). `knowledge.py` in HYGIENE reports every unjoined file, unrecorded
promotion, and conversation-stage pursuit with no kb file — **dispose of those in this run:
structuring a join or creating a named kb file is run work, never a note for the candidate.**

## 9. STAMP THE CHANNELS YOU SWEPT

`~/.claude/jobsearch/run channels_due.py --stamp <channel_id>`. **Nothing else writes `last_reviewed`**, so
skipping it makes the sourcing queue report false overdues — and a queue that is wrong in a known
direction stops being an instrument and becomes something a run talks itself out of.

## 10. ADVANCE `stage`, AND RECORD HOW IT WAS SENT

Set `stage` (`sourced`→`contacted`→`screening`→`interviewing`→`offer`→`closed`) in the same edit
that changes it. **It is the only field that can answer "what actually converts," and nothing
infers it.** A move into `screening` creates `kb/<company_id>.md` in the same pass if it is
missing — accumulation is the run's job, and `knowledge.py` names every gap.

**When the candidate reports sending something, WRITE THE OUTREACH ROW** with its `date` — a log entry once
claimed a row had been added and it never was, and `validate_data.py` cannot catch that because a
MISSING row is schema-valid.

**Every new outreach row carries** `medium` · `touch_type` · `recipient_role` · `delivery` (plus
`address_status` for email, `campaign_id` for a multi-touch push). **`outreach-drafter` emits these
with the draft — copy them verbatim**, because the drafter is the only actor that knows which
medium applies. Then **MOVE the `drafts.md` entry into `data/messages.jsonl`** and set
`message_ref`. The old rule deleted the text, which is why the historical rows can be analyzed for
channel but never for content.

## 11. PROCESS DEBT (advisory here)

`~/.claude/jobsearch/run check_process_debt.py`. Daily runs may append freely; **the WEEKLY run drains it
to zero.**

## 12. DASHBOARD — after the LAST mutation

```bash
~/.claude/jobsearch/run check_dashboard_fresh.py --fix
```

Then **grep the OUTPUT** (`dashboard_artifact.html`) for a distinctive phrase from what you added.
**Verifying the source file is not verifying the deliverable** — a body that fails to render is
indistinguishable from one never written. Publish with the Artifact tool on
`dashboard_artifact.html`, passing `dashboard_artifact_url.txt` as `url` so it redeploys to the
same artifact; create it if absent. Skip gracefully (and note it) if the tool is unavailable.

**⭐ If the publish reports a VERSION CONFLICT, another session published since you generated
(dev #133 / public #22). Never pass `force`, and never drop the publish silently:** re-run
`check_dashboard_fresh.py --fix` (the store is the merge — both racers generate from it) and
publish again. **After every successful publish, stamp it:**

```bash
~/.claude/jobsearch/run check_dashboard_fresh.py --stamp-published
```

The stamp is what lets the next run detect a dropped publish mechanically; a publish that could
not succeed is a finding the summary states loudly, and the next run's hygiene check will flag
it regardless — that convergence is the design, so never silence it.

## 13. SUMMARY

Top 3 focus areas · new items with comp/fit assessment · status changes · proposed drafts.
**NEVER send messages, emails or applications — drafts are for approval only.** If nothing is new,
one line, but still do UPDATE STATE and DASHBOARD.

**⭐ MONDAY DECISION BATCH.** On the first run on/after Monday, present a consolidated batch
pairing the standing ATS-portal stage check with EVERY open Your Move role ask, each carrying an
explicit lean (pursue/pass/apply) AND an act-by date. **The binding constraint is the candidate's
decision throughput, not sourcing** — make the queue clearable in one 15-minute pass. **Role-ask lapse
rule:** a *role-only* pursue/pass ask unanswered past its act-by date is closed as "lapsed" and
removed from Your Move — reversible, and logged. **NEVER applies to letters, sends, or anything
with an external deadline.**

## 14. POST A RUN-SUMMARY, THEN COMMIT + PUSH

```bash
~/.claude/jobsearch/run inbox.py --post "<one line: what changed>" \
    --detail "<what the candidate must decide, act-by date first>" --urgency high
```

**Do this even on a quiet run** — "nothing new" is information, and its absence is
indistinguishable from a run that never fired. **Writing state is not the same as telling the candidate:**
one run booked a call and sourced three roles while `notifyOnCompletion` was unclaimed and the run
queued nothing, so none of it reached their view. `--urgency high` if anything needs them.

Then commit, genuinely last, and let the sync resolver decide the push half:

```bash
# ⚠️ EXPLICIT PATHS, never `git add -A`. Subagents have written into this same tree during the
# run, and `-A` bundles whatever they left mid-edit into this commit -- the exact failure the
# rulebook records for 2026-07-25. Name what this run changed.
git add data/ handoff.md log.md drafts.md cover_letters.md dashboard.html 2>/dev/null
git commit -m "Daily run $(date +%F): <one-line summary>"
~/.claude/jobsearch/run sync.py --end-of-run
```

**`~/.claude/jobsearch/run sync.py --end-of-run` replaces the old unconditional push (ADR-012).**
It resolves the profile's DECLARED `config.sync.mode` and acts: under `remote` it delegates to
`push.sh` (which reads this session's `.git/push_token`, minted at run start — a bare `git push`
still fails by design); under `local-only` it prints the one line the run summary must carry
verbatim: *committed locally; not pushed — this profile declares `sync.mode: local-only`*.
**Copy its output into the run summary either way** — the summary is where "did this run's work
leave the machine?" travels as fact, never as ad-hoc prose. Two exits are LOUD, not optional
reading: **NOT PERSISTED** means the push FAILED and the commit exists only on this machine —
the summary carries `NOT PUSHED: <reason>`, because a run that persisted off-machine and one
that did not are otherwise identical afterwards. **COMMIT-ONLY** means the mode is `undeclared`,
`mismatch`, or unreadable — never push by hand on a guess; say so and let `migrate.py` or
`sync.py --set` resolve it. **`git status` should be clean when you finish**; if it isn't, a
step after the commit mutated state and the ordering is wrong again.

**THEN, and only then:** `~/.claude/jobsearch/run runlock.py --release`. **Release even if the run failed
partway** — a held lock blocks every subsequent run until it goes stale, which is far worse than
the collision it was preventing.
