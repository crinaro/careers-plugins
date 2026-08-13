# The job-search tracker — rulebook

This repo is the single source of truth for the candidate's executive job search. All sessions
(interactive, scheduled tasks, subagents) follow these rules.

## Files — the index. **Each file's own header carries the detail; read it when you edit that file.**

**DATA (JSON — queried, counted, validated).** Engine `docs/schema.md`; rationale ADR-004.
- **`data/opportunities.jsonl` · `companies.jsonl` · `channels.jsonl` — THE PIPELINE.** Edit
  these, then run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/validate_data.py"`. **⭐ An APPLICATION goes in
  `applications[]`, an outreach touch in `outreach[]` — separate arrays because they are
  separate funnels.** A reply sets the row's `outcome`/`responded_on`, not just prose somewhere.
  The **exclusion list** is `verdict: pass` OR `status: passed` (`scripts/pipeline_index.py`).
- **`data/messages.jsonl`** — **every actual communication, BOTH DIRECTIONS**, joined to the
  opportunity and the contact, each with a `source` (`gmail:<account>:<uid>`) so it can be
  re-verified. On send a draft **moves** here rather than being deleted.
- **⭐ THE JSON IS THE OPERATING STORE. Update it INCREMENTALLY, during the daily run**, as
  replies land and people are found — **never leave a change for the weekly audit to find.**
  `scripts/reconcile.py` re-derives state from the mailbox, but it is a **weekly AUDIT ONLY**
  (the candidate, 2026-08-02); anything it finds is a process failure, not a routine result.
- **`user.json`** (who the candidate is) · **`config.json`** (how the search behaves). Never retype a
  value from prose — read it with `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/profile.py"`.

**HUMAN-EDITED NARRATIVE (markdown by design — the candidate edits these himself).**
- **`resume.md`** — verbatim canonical resume; **the source of truth for any background claim,
  including its one-line role descriptions.** ⭐ **Copy its own sentences; do not paraphrase from
  memory** — a paraphrase of one employer once dropped the clause naming its marquee customers,
  so the sentence survived and the credential in it did not. Its **"Additional Detail (elicited beyond the
  resume)" addenda hold facts the candidate chose not to print — absence from the printed resume is NOT
  evidence a fact can't be used.**
- **`projects.md`** — proof points with a `Surface when:` trigger per entry. **⭐ GREP IT for the
  JD's own terms whenever reading a JD or drafting; never read it whole, never dump projects into
  a message.** Add to it whenever the candidate mentions a project in passing.
- **`strategy.md`** — fit logic and the outreach playbook. **Read one section via
  `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/section.py" strategy.md "<heading>"`, not the whole 16,700 words.**
- **`network.md`** — warm-intro targets and alumni.

**STATE AND OUTPUT.**
- **`focus.md`** — a decision tool, not a log. **Role state is GENERATED from the JSONL — to
  change what shows there, edit the role's `status`/`next_action`, not this file.** Section rules are
  in its header; `scripts/check_sections.py` enforces them.
- **`log.md`** — append-only. Never edit past entries.
- **`drafts.md`** (pending outreach) · **`cover_letters.md`** (pending letters). **⭐ BODIES MUST
  BE `> `-BLOCKQUOTED OR THEY PUBLISH EMPTY** — that shipped once, and only the candidate noticed. Entry
  format: each file's header.
- **`kb/<company_id>.md`** (durable knowledge, keyed by `company_id`) ·
  **`call_preps/call_prep_<date>.md`** (**stamp `Companies:`; promote durable content to
  kb/ BEFORE archiving, recording `Promoted:`** — `archive/README.md`; `scripts/knowledge.py`
  audits). **⭐ EVERY KB LINE IS TAGGED BY SOURCE** — `[CANDIDATE]` `[JD]`
  `[RESEARCH]` `[CLAUDE]` `[OPEN]`. Never blur a company's self-description, or my
  inference, with what someone said.
- **`dashboard.html` / `dashboard_artifact.html`** — GENERATED. Never hand-edit; the daily-run
  prompt carries the generate/publish steps. **⭐ Then grep the OUTPUT for what you just added.**
- **`process_archive.md`** (retired process items) · **[docs/incident_archive.md](docs/incident_archive.md)**
  (the stories behind these rules — reference only, read on demand by `search-strategist` alone).
- **`opportunities.md` — RETIRED 2026-07-20, frozen. Do not read it, do not edit it.**

**ENGINE.** `skills/` (run prompts) · `agents/` (each carries its own CONTEXT BUDGET) ·
`scripts/` · `docs/`.

## ⭐ WHERE DOES AN ITEM GO? Answer two questions, in order.

**(1) Does the candidate have to decide, approve, or do something?**
- **No** → it is STATE: This Week (a scheduled commitment) · role/thread state (the JSONL) ·
  Network. **⭐ If it is the ENGINE's fault — a script, a gate, a skill, an agent — it is NOT
  state and NOT a local to-do: it is an ISSUE on the plugin's repository.** Propose it with the
  `engine-reporter` agent, file it with `report_issue.py` after the candidate agrees.
  *(`Process → 🔧 Open` and its dashboard tab were retired 2026-08-06. A capability's defects
  belong on that capability's tracker; a local copy is a second place to look and the one that
  goes stale.)*
- **Yes** → question 2.

**(2) Is the decision about a ROLE/relationship, or about the SYSTEM?**
- Role, person, outreach → **⚡ Your Move**
- Script, config, credential, tooling, cadence → **⚙️ Process → ⚡ Needs the candidate**, which
  renders inside **Your Move** as the *System & tooling* group. ⚠️ **This is the one process
  category that survives**, and it must not be filed as an engine issue: a credential, a cadence
  or an account setting is a decision only the candidate can make, and no issue on the engine repo
  can resolve it for him.

**Three invariants:** **ONE ITEM, ONE SECTION** (appearing in two panels is a bug) · **ASK LISTS
EXPEL RESOLVED ITEMS** the moment they're answered — never rewrite one into a "✅ CONFIRMED"
status line in place · **a Your Move line must read as a question or an imperative aimed at
the candidate**; if it reads as a status report it is in the wrong section. `check_sections.py` enforces
this. **⭐ The weekly run drains ENGINE observations by FILING them** — `engine-reporter` proposes,
the candidate agrees, `report_issue.py` files. Nothing engine-related stays local; a copy is a
second place to look and the one that goes stale.

## ⭐ PROFILE vs ENGINE — `config.json` / `user.json` HOLD THE VALUES

**PROFILE (this person's data):** `user.json` (who the candidate is) · `config.json` (how the search
behaves) · `resume.md` · `projects.md` · `strategy.md` · `data/`.
**ENGINE (reusable machinery):** this file · `scripts/` · `.claude/agents/` · `docs/`.

**`config.json`/`user.json` hold the VALUES; this file holds the REASONING. Never retype a number
from prose — read it with `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/profile.py"`.** `check_profile_leakage.py` fails the run
if a comp figure appears in an engine file.

**⭐ SCREEN COMP WITH `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/profile.py" --screen-all`, never from
memory.** The four settings, the local-onsite tier and the undisclosed-comp rule are stated once,
under THE THREE READING RULES below — they were restated here too, which is the duplication this
file warns about two sections later.
_Incident history: [docs/incident_archive.md](docs/incident_archive.md#application-dates-and-ats-receipts) covers the related retype-from-memory failure._

## Profile pointers — ⭐ THE VALUES ARE NOT HERE

**This file is the RULEBOOK. `config.json` and `user.json` are the CANDIDATE.** Every preference
below is a POINTER, deliberately. This section restated titles, geography, background and mailbox
addresses that already existed in JSON, which is the same duplication that let comp figures drift
before they were centralized — a value stated twice is a value that disagrees with itself later.

**Read them, never retype them:** `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/profile.py"`
(`--screen-all` to screen the live pipeline).

| what | lives in |
|---|---|
| titles, and that org structure is not a filter on its own | `config.targets` |
| commute anchors, the radius, open relocation destinations | `config.geography` |
| comp tiers, floors, basis, the standout carve-out | `config.compensation` |
| what to lead with; employer recognition; stated boundaries | `config.positioning` |
| per-medium limits, default sequence, the two-jobs rule | `config.communications` |
| ATS sender domains, receipt phrases, query order | `config.ats` |
| identity, mailboxes, narrative source files | `user.json` |
| background, verbatim | `resume.md` + its addenda · `projects.md` |
| fit logic and the outreach playbook | `strategy.md` (read ONE section via `scripts/section.py`) |

**⭐ THE THREE READING RULES THAT ARE NOT EXPRESSIBLE AS A VALUE — internalize these:**

1. **There are FOUR settings, and `location.type: onsite` does NOT mean relocation.** An office
   inside the commute radius is the separate, LOWER **local-onsite** tier — a local discount that
   costs the candidate nothing. A re-scoring pass once failed a role against the RELOCATION bar
   when the office was 25 minutes from the house. `effective_setting()` encodes this; an
   unrecognized onsite location returns **NEEDS-COMMUTE-CHECK, never relocation-by-default.**
2. **Undisclosed comp is KEPT, never screened** — comp is the first question, not a filter. But a
   band whose **TOP** is below the applicable floor is **REMOVED, not merely flagged**, unless the
   candidate explicitly opts in. `--screen-all` is the arbiter; do not re-derive a tier from prose.
3. **A stated boundary is data, not a gap to write around.** `config.positioning.scope_boundaries`
   lists capabilities confirmed absent. Naming a limit makes everything before it more credible;
   stretching one loses the room.

**⚠️ One open question lives in the data, not here:** the relocation tier's `basis_confirmed` is
still `false`. Ask before any comp conversation leans on that tier.

## Hard rules
- **SCAN FOR MEETING ARTIFACTS AND HUMAN SENDERS *FIRST*, BEFORE ANY NAMED PRIORITY LIST.** Added 2026-07-21 after the 2PM Gmail scan missed a **confirmed interview booked for the next morning** (<an employer>, Wed 7/22 9:00am) that had arrived *inside* its window, and reported "no new recruiter/human contact." **Two causes worth generalising: (1) a numbered priority list in a scan brief reads as permission to ignore everything not on it** — so the sweep for `subject:(Invitation OR "Appointment booked" OR "Updated invitation")` and for any non-automated human sender must come FIRST and unconditionally; **(2) a thread's SUBJECT LINE can be weeks stale while its newest message schedules something tomorrow** — this one read "Re: Appointment booked: ... @ Wed Jul 15" and contained the 7/22 booking. **Never judge a thread's freshness by its subject.** Corollary: `focus.md`'s This Week is only as good as the scan feeding it — a stale This Week is the *symptom*; a scan that skipped an artifact type is the *cause*.
- NEVER fabricate mutual-connection or referral claims.
- **AN APPLICATION'S DATE COMES FROM THE CONFIRMATION EMAIL. Search the mailbox before inferring
  it from anything else** — never from a tracker line, a "last modified" timestamp, or LinkedIn's
  In-Progress tab (which is evidence of nothing in either direction). **Search in this order, and
  it matters: `company-name` → `sender-domain` → `subject-text`.** The domain and phrase lists are
  DATA in **`config.json.ats`** — **paste them from there; do NOT retype them from memory.**
  **A zero-result search is a reason to check the query, not an answer.** Match subject phrases
  *without* trailing punctuation. A role may legitimately carry more than one application record
  via different `method`s — that is not a duplicate.
  _Incident history: [docs/incident_archive.md](docs/incident_archive.md#application-dates-and-ats-receipts) — four employers, and the time this exact list was retyped wrong on the very next search._

- **Some background facts live OFF the resume by design.** `resume.md`'s **"Additional Detail
  (elicited beyond the resume)" addenda** hold facts the candidate confirmed but chose not to print — <a former employer>
  marquee customer names that were deliberately left off the printed page, for one. **Absence from the printed
  resume is NOT evidence a fact can't be used.** Check the addenda before concluding something
  isn't available. Equally: don't assume the resume corroborates every claim a message makes — if
  a message leans on an addendum fact, expect to explain it when a resume is requested.
- **NEVER repeat a claim about the state of the SYSTEM (a script, LaunchAgent, config,
  filter, file, permission) by quoting a tracker. The machine is the source of truth; a
  tracker only records what was true when someone typed it.** Go check — `cat` the plist,
  read the log, run the script — then correct the tracker line in the same pass. Added
  2026-07-19 after the weekly review ranked "apply the wake_chrome fix, still unapplied
  after 4 days" as its #2 proposal: the fix had landed 2026-07-17 with the repo move and
  the job had been firing cleanly for three days. The stale sentence was written 7/15,
  never revisited, and got read back as researched fact by an agent that never looked at
  the machine. Five separate focus.md lines had propagated the same wrong claim.
  `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/check_stale_claims.py"` flags these lines; verifying them is still a
  human/agent step. Corollary: when a fix lands, correct EVERY line asserting it was
  broken — the linter finds the copies.
- **✅ SUPERSEDED 2026-07-21 (read this before the ladder below): `gmail_get_attachment` in the local Gmail MCP
  server fetches `.ics` bytes DIRECTLY and prints the `parse_ics.py` command.** Proven end-to-end the same day
  against the <an employer> invite: search -> fetch -> parse -> *Wednesday, July 22, 2026, 9:00 AM PDT,
  CONFIRMED*, with no Chrome involved. **Use it first.** The Chrome ladder below remains valid as a fallback
  (browser down, credentials missing), and the acceptance-receipt search at step 0 is still the cheapest first
  move when there is no attachment at all.
- **NEVER state a meeting date or time you have not actually verified.** The body of an invite
  often omits the date entirely; it lives in the `.ics`. **Use `gmail_get_attachment` FIRST** — it
  fetches the bytes directly and prints the `parse_ics.py` command. Then, cheapest-first:
  **(a) search for the ARTIFACT, not the person** — `subject:("Event accepted" OR "Invitation" OR
  "Updated invitation")`; Google's acceptance receipts spell the date out in plain text with an
  exact numeric UTC offset, which `parse_meeting_mail.py` converts exactly. **(b)** `parse_ics.py`
  on the attachment. **(c)** ask the candidate. **⚠️ `~/Downloads` collisions get `(1)`, `(2)` suffixes —
  take the newest via `ls -lat ~/Downloads/*.ics`, never assume `invite.ics`.**
  `scripts/meeting_check.py` now runs this sweep deterministically every daily run.
  _Incident history: [docs/incident_archive.md](docs/incident_archive.md#meeting-times-and-the-ics-ladder) — the <a recruiter>/<a firm> time that sat readable in a sent receipt while being reported as unknowable, and the Chrome workaround this replaced._

- **A calendar receipt proves only what was booked WHEN IT WAS SENT — it is not the current
  schedule.** Added 2026-07-20 after exactly this error: a 7/17 acceptance receipt reading
  "July 20, 2026 at 8:00 AM US/Eastern" was reported as the live meeting time and as a possibly
  missed call, when the candidate had rescheduled out of band to Tue 7/21 9:30am PT. Reschedules happen
  by phone, from another mailbox, or in a thread this account cannot see. **Always confirm a
  receipt-derived time against a newer invite, the calendar, or the candidate.**
- **NEVER conclude a message "does not exist" from a search that covered one mailbox.
  ⭐ Gmail: the configured set in `user.json` is the COMPLETE set** — read it with
  `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/profile.py"`, never retype an address, and never add one that shows up in a
  recruiting database (at least one such address is bad data and must not be re-raised as a
  coverage gap). `scripts/gmail_mcp_server.py` searches **all** accounts by default and raises a
  loud `!! INCOMPLETE COVERAGE` banner for any it cannot reach — **never pass a single `account`
  unless you specifically mean to narrow it, and never read a banner-ed result as a zero.**
  Correspondence also spans LinkedIn and phone, so any one mailbox is a partial view. Added
  2026-07-20 after accusing a subagent of fabricating a real email. **When a subagent reports
  something you cannot find, the first hypothesis is a gap in YOUR search scope, not invention by
  the agent.** Say "I couldn't find it in X," never "it doesn't exist."
  _Forwarding, per-account quirks, and the excluded address are DATA:_ `user.json`.
  _Incident history: [docs/incident_archive.md](docs/incident_archive.md#mailbox-coverage)._
- NEVER silently resolve a relative date/time reference ("next Friday," "in two weeks")
  into a specific calendar date without deliberately checking the day-of-week math — this
  has produced a confident, wrong answer before ("next Friday" mis-resolved to a
  Wednesday). Quote the sender's exact words, verify the actual date with a real
  day-of-week check (e.g. `date` command), and if there's genuine ambiguity, say so
  rather than asserting one reading as fact.
- NEVER send messages, emails, or applications without the candidate's explicit fresh approval.
- **⭐ ALWAYS PASS THE JOB-SEARCH DRIVE FOLDER AS `parentId` WHEN CREATING A DOCUMENT.**
  The folder name and id are DATA — `config.drive` (`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/profile.py"`). **Check for
  the folder BEFORE creating, not after: the connector CANNOT MOVE A FILE**, so a document created
  without a parent lands in My Drive root and the only fix is a second copy for the candidate to
  delete. Do not "fix" anything already sitting in root unasked.
- **⭐ NEVER TAKE A ROLE'S `location` FROM A LINKEDIN-SOURCED FETCH — CONFIRM IT ON THE EMPLOYER'S OWN POSTING.** A WebFetch of a LinkedIn job page **invents a location, and it invents THE CANDIDATE'S OWN COMMUTE ANCHOR** — the single worst wrong answer, because it is the one city that looks plausible. On 2026-07-27 it reported the candidate's own commute anchor as the JD location for **three separate postings** (a confidential Chief Transformation Officer, <an employer> GCC, <an employer> SVP); all three actually say "United States" or "various locations." Cause looks benign — LinkedIn renders a distance-from-your-profile personalization line and the summarizer reads it as a JD fact — but the consequence is not: **an invented city can be the one location that flips a role into the **local-onsite** comp tier instead of **remote** or **relocation** (the floors differ materially — `config.json` is authoritative), so a hallucinated home-city silently mis-tiers the comp screen and can make a failing role look like it passes.** Caught only because the researcher cross-checked all three; it leaves no trace otherwise.
- **⭐ AFTER GENERATING THE DASHBOARD, GREP THE *OUTPUT* FOR A DISTINCTIVE PHRASE FROM WHATEVER YOU JUST ADDED. Verifying the source file is not verifying the deliverable.** On 2026-07-27 a cover letter published with its heading and metadata but **no letter text at all**, and only the candidate noticed. `parse_drafts`/`parse_cover_letters` build the body from **only `>`-prefixed lines**, and the body had been written as plain text. **The dangerous part is that the source file reads perfectly** — every constraint check (word count, em-dashes, US English) passed, because they all ran against the file rather than against what published. An empty body is indistinguishable from "not drafted yet." Both parsers now print a loud `!! WARNING` for an entry with no quoted body (verified by inducing the failure), and `cover_letters.md`'s own header carries the blockquote requirement — but the standing habit is the real guard.

- **⭐⭐ NEVER CONCLUDE A JOB POSTING IS DEAD FROM A WebFetch. RENDER IT IN A BROWSER FIRST.** Modern ATS platforms are
  JS-heavy single-page apps, and a plain fetch returns a shell whose fallback text is often **actively misleading** —
  not merely empty. **Three separate instances in a single day (2026-07-22):** a WebFetch reported <an employer>'s live
  req as *"this position is no longer available"* (the candidate was mid-application); <an employer>'s ATS returned only the
  search-portal shell; and <an employer>'s returned **"the job you are trying to apply for has been filled"** for a req that
  was live with a working Apply button and a stated 07/31 close date. **In every case the browser render showed the
  truth.** So: a fetch may confirm a posting is LIVE, but it can never establish that one is DEAD — and telling the candidate
  a role is gone when it isn't costs him a real opportunity. **A genuine negative needs two agreeing signals** (e.g.
  <an employer>'s closed "Lead Director – Cloud Solution Architecture" returned both HTTP 410 and a browser render saying
  unavailable — that is a confirmed close).
- **⭐ CANONICAL COVER-LETTER HEADER — the template is DATA, in `config.writing.cover_letter_header`.**
  Render it with `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/profile.py"`; never retype a name, city, phone or address into a
  letter. The candidate corrected this header himself (2026-07-22), and it deliberately uses a
  **precise city** where `resume.md`'s header uses a broader metro phrasing. **That difference is
  their own choice and is NOT to be "corrected" in either direction unasked** — a letter to a
  specific city benefits from precision; the resume is a different artifact making a different
  call.
- **⭐ ONE-PAGE RULE FOR COVER LETTERS.** The candidate exports to PDF, so a second page carrying
  only a signature is a real defect. **Target body length and the page cap are DATA:
  `config.writing.cover_letter_target_body_words` and `cover_letter_max_pages`.** If it still
  spills, cut margins before cutting substance. **Verify the page count in the browser — never
  assume it fits. ⚠️ AND MEASURE IT ONLY AFTER ACCEPTING TRACKED SUGGESTIONS, NEVER WHILE THEY ARE
  PENDING** — suggesting mode keeps BOTH the struck-through old text and the inserted new text in
  the flow, so the count is inflated. That once read 3 pages with a lone line on page 3, and 2
  pages the instant the suggestions were accepted; acting on the inflated number would have
  deleted a real bullet to fix a problem that did not exist. **Accept first, then measure.**
- **⭐ STRIP AI-TELL MARKERS FROM EVERYTHING A READER SEES** — cover letters, outreach, drafts,
  profile copy, any human-facing message. **The banned characters and phrasings are DATA:
  `config.writing.banned_characters` and the surrounding notes** (`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/profile.py"`).
  The em-dash is the specific tell the candidate named; also avoid the "not just X, but Y"
  construction, "not only… but also," reflexive tricolons, and AI-cliché vocabulary.
  **PROOF EVERY PIECE FOR US ENGLISH before it goes out — `config.writing.us_english` carries the
  word list.** **Scope = the LETTER/MESSAGE TEXT a reader sees, NOT internal tracker or log prose.**
  **Before pushing any letter or staging any draft, grep the body for the banned characters and
  confirm zero, then do the US-English pass** — a letter shipped with em-dashes once had to be
  re-created clean, so do both checks up front.
- **THE DOCUMENT AND MAIL CONNECTORS ARE CREATE-ONLY — no update, no delete.** Capabilities per
  connector live in `config.tooling`. Consequences: **push a document only when the text is
  genuinely FINAL**, and **never pre-stage a draft** — create one only when the candidate
  explicitly asks, then stop. **The repo is the source of truth for draft text; the connector gets
  exactly one copy, at the last possible moment.** When a revision is unavoidable, create the
  replacement, hand over BOTH links, and say plainly which to delete — only the candidate can.
  **Verify a new document by READING IT BACK**; the create response's reported size is meaningless
  for native docs.
  _Incident history: [docs/incident_archive.md](docs/incident_archive.md#create-only-connectors)._
- NEVER assert a specific fact about a document (e.g., "the JD doesn't mention X") unless
  someone actually checked for it. A task scoped to one question doesn't answer a different one
  just because it didn't come up — flag it as an assumption, or go check.
- When a role's JD calls for something `resume.md` only covers thinly, **DON'T paper over the gap
  with vague language and don't invent specifics.** Ask the candidate a targeted question instead — that is
  what the `fit` block's `question_for_candidate` is for. Only ask when the missing specific would
  materially change how compelling the pitch is.
- **NEVER edit the candidate's live LinkedIn profile without their explicit fresh approval** — a public,
  identity-facing profile is at least as consequential as a sent message. Draft suggested copy via
  `profile-optimizer` for his review first.
- Outreach style: warm, role-specific, healthcare/SaaS background, **no comp mentioned upfront**.
  Per-medium limits and the two-jobs rule live in `config.json.communications` — read them with
  `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/profile.py"`, never retype one.
- Channel priority: **retained-firm relationships and warm intros first (~85% of exec placements);
  job boards second.** ⚠️ Read this as a PLACEMENT/relationship-investment rule, **not a sourcing
  directive** — the 2026-08-02 review found they are different funnels: self-sourced LinkedIn finds
  the targets, human channels produce the conversations.

- **Canonical email signature — render it, don't retype it:** `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/profile.py"`
  prints it from `config.json`'s template × `user.json`'s values. Pass `htmlBody` with an
  explicit `<a href="...">` anchor built from the profile URL in `user.json` plus a plain-text `body`,
  so the link text and the `https://www.` URL are exact rather than guessed.
  **⚠️ Do NOT claim this removes the `google.com/url?q=` wrapper — it does not, and it doesn't
  need to. That wrapper is added by Gmail when DISPLAYING mail, not by the sender**, so a
  redirect seen in a draft is a rendering artifact in the candidate's own mailbox; the recipient gets the
  clean href. Don't "fix" it and don't flag it as a defect.
  _Incident history: [docs/incident_archive.md](docs/incident_archive.md#the-googlecomurl-wrapper-that-was-never-a-defect)._


## ⭐ THIS SESSION DOES NOT EDIT THE ENGINE — route it instead

**The search runs here; the engine is maintained in the `careers-plugins` repo, by its own
session.** In development both sit on one disk, so nothing physical stops a search session
reaching over and "just fixing" a script — which is exactly what the split exists to prevent.
Engine changes once landed **mid-run, while a scheduled run was reading the same files.**

**A `PreToolUse` hook now refuses the write** (`hooks/hooks.json` → `guard_engine_writes.py`) when
a session whose cwd is not the engine repo edits engine code, or when anything edits the installed
plugin **cache** (a copy — the edit appears to work and is destroyed by the next sync). It fails
open on any unexpected condition, because a guard that breaks all editing is worse than the
mistake it prevents. **It is a guard, not a sandbox: the behavioural rule is still the first line.**

**Found an engine bug? Route it with `report_issue.py` and carry on searching.** The
exact command is in that repo's `docs/intake.md` — and the hook prints it for you at the moment it
blocks, which is when you need it. It is deliberately not repeated here: a command written twice
is a command that disagrees with itself later.

⚠️ **State the bug as the RULE that misbehaved, never the instance.** That queue crosses into a
repo gated at zero personal data and **refuses** a comp figure, address, phone or name — git
history is permanent. It is also the better bug report: the engine is made of rules, not instances.

## Token discipline
- Deterministic work goes to `scripts/` (Python), not model output.
- Delegate scanning to the `inbox-scan` agent (haiku) and browser work to `linkedin-runner` (sonnet).
- Targeted edits only; don't re-read unchanged files; don't rewrite whole files.

## Git commits
- Subagents (`outreach-drafter`, `opportunity-researcher`, etc.) may commit locally when they
  update tracker files as part of their assigned task (2026-07-14, per the candidate: "the team should
  be updating data to support the overall process") — this is no longer a scope overrun worth
  flagging. **A subagent that commits must stage its OWN explicit paths, never `git add -A`** —
  the subagent shares the parent's working tree, so `-A` bundles the main session's in-flight
  edits (this happened 2026-07-25). Pushing to the remote stays a main-session, end-of-session
  step (see checklist below), not something individual subagents need to do themselves.
- **⭐ PUSHING IS MECHANICALLY GATED BY A PER-SESSION SECRET (2026-07-27, P4; hardened 2026-07-29).**
  A `pre-push` git hook refuses any push whose `CLAUDESEARCH_PUSH_TOKEN` doesn't match this
  session's secret in `.git/push_token`. **That token is RANDOM per session and lives in NO
  tracked file** — which closes the hole that defeated the old gate: the previous marker was a
  fixed constant printed right here in CLAUDE.md, so subagents read it and reproduced it (7/21,
  7/22, 7/25, 7/28). There is no constant to copy anymore. **Flow for the main session:**
  1. At run start, mint the token: `scripts/push_init.sh` (writes a fresh `.git/push_token`).
  2. To push, use the helper: **`scripts/push.sh`** (it reads the token and runs `git push`).
  **Subagents must never push** (their definitions say so, and the hook now enforces it — a
  subagent that copies any wording from these docs still cannot construct a valid token).
  Tracked hook source is `scripts/git-hooks/pre-push`; `.git/hooks/` isn't version-controlled,
  so re-install after a fresh clone with
  `cp scripts/git-hooks/pre-push .git/hooks/pre-push && chmod +x .git/hooks/pre-push`.
  *Honest limit: a subagent with shell access that deliberately reverse-engineers the hook could
  still read `.git/push_token`; this defeats reflexive copying, not a determined bypass. The
  behavioral rule (subagents never push) remains the first line.*

## Scripts — **read the script's own docstring before changing it.** This is the index.

**Run-start hygiene** (both task prompts inline these):
`check_stale_claims.py` · `check_followups.py` · `check_sections.py` · `channels_due.py` —
advisory, always exit 0 so they cannot wedge an unattended run.
`check_narrative.py` — are the `projects.md` triggers and resume addenda still greppable? A
broken one reads downstream as "no matching proof points" — same as having none.
`validate_data.py` — **the real gate**; exits 1 on a schema/enum/reference problem.
`check_rule_homes.py` — no archived lesson lost its rule; CLAUDE.md within its word ratchet.

**Deterministic sweeps** (a daily, predictable artifact is a query, not a model summary):
- `alert_sweep.py` — board/aggregator digests, every configured mailbox.
- `meeting_check.py` — calendar artifacts diffed against `focus.md`'s This Week.
- `gmail_mcp_server.py` — multi-account Gmail over IMAP. **⭐ `account` defaults to `all`; an
  unreachable account raises a loud `!! INCOMPLETE COVERAGE` banner. Never pass a single
  `account` unless you specifically mean to narrow it.** Credentials live only in the OS
  credential store (service `claudesearch-imap`), never in the repo, never a CLI argument. **Claude must not
  handle them** — if `gmail_accounts` reports `[MISSING]`, say so rather than silently
  searching one mailbox.

**Reading and screening:**
- `profile.py` — loads `user.json` + `config.json`. **`--screen-all` is the executable comp
  screen. ⭐ `location.type: onsite` does NOT mean relocation** — an office inside the commute
  radius is the lower local-onsite tier; an unrecognized one returns NEEDS-COMMUTE-CHECK, never
  relocation-by-default.
- `pipeline_index.py` — compact "is this already tracked?" index. Use instead of the raw JSONL.
- `section.py` — one markdown section instead of a whole file.
- `fit_report.py` — JD fit register · `--gaps` (questions for the candidate) · `--pitch <opp_id>` (what
  the drafting agents read).
- `funnel_report.py` — what's working: channel yield, applications, outreach by medium/touch/
  recipient. **Denominator is RESOLVED sends, not sends.** `--recommend` proposes config changes
  and never applies them.
- `route.py` — ⭐ **how to reach a sourcing channel.** `access` states what the channel NEEDS;
  the mechanism (site plugin · in-app browser · Chrome extension) resolves at run time. **Never
  pick one from memory.** An unreadable `access` exits 1: an unroutable channel is skipped, and
  that looks exactly like one searched and found empty. _Why they were fused: the script header._
- `check_process_debt.py --weekly` — the zero-open-items invariant.
- `check_profile_leakage.py` — `config.json` is the single source of truth for values.
- `parse_ics.py` · `parse_meeting_mail.py` — decode a meeting time. `generate_dashboard.py`.
- `push_init.sh` / `push.sh` — mint and use this session's push token. `wake_chrome.sh` — a
  LaunchAgent pre-wakes Chrome at 06:58/13:58.

**Python environment.** Scripts target **3.9+, stdlib only**, so they run unattended. ⚠️ The
harness shell is non-login, so plain `python3` resolves to `/usr/bin/python3` (3.9.6), not the
Homebrew 3.14. That is fine — but never *assume* 3.14; call `/opt/homebrew/bin/python3`
explicitly if a script ever genuinely needs it. `zoneinfo` is available and used.

## ⭐ THE PROCESS FLOW — full design in `docs/architecture.md`

That document is the map: the three layers, the data model, the session model, **who is
responsible for what and which commands each job runs**, the invariants and the gate enforcing
each, and the known limits. Read it before changing how the jobs fit together.

## ⭐ THE PROCESS FLOW — in one paragraph

**the candidate talks to ONE session: the COORDINATOR, which they start themselves — `/coordinator`** in any session, any device. **Deliberately not scheduled:** a scheduled version was built and removed 2026-08-02 because it gained no asynchrony (a scheduled run can be neither notified nor messaged) and **lost the one thing only a session opened directly can do — claim `notifyOnCompletion`.** Rationale: `docs/architecture.md`. **`search-daily` runs per `posture.py --cron`** and **⭐ RUNS ITS READ PHASE UNLOCKED, ALONGSIDE AN OPEN SESSION** (mailbox, browser, research: reads never conflict). **The lock covers the WRITE only** — `changed.py` → `--take --wait 120` → edits + commit → `--release`. Holding it for a whole run or a whole session is the 2026-08-03 bug that cost a scheduled run; see §3e.
**`search-strategy-weekly` runs weekly** and, if refused, reports SKIPPED rather than
downgrading — a silently degraded audit is worse than none. **Updates reach the coordinator by
PULL (`scripts/inbox.py`, durable) and PUSH (`notifyOnCompletion`, which only a REGULAR session
can claim, so the candidate re-claims it each session).** There is no session-to-session messaging.

## ⭐ WHY IT CANNOT CLOBBER — the mechanics

**The conflict is about WRITES, not reads.** `watch.py` is read-only and appends to one queue, so
it cannot conflict by construction. `scripts/runlock.py` stops two *writers*: `--take` at run
start, `--release` after the commit **even if the run failed** (a held lock blocks every later
scheduled run until it goes stale at 150 min, then it can be `--steal`n). **A queued finding is
something the JSON does not know yet** — draining it means writing it into `data/*.jsonl`; the
queue is a hand-off, never a substitute for state. **A quiet run is normal at this cadence** —
say so in one line and stop; never pad it with make-work.

## Session continuity

Every scheduled run is a **new session with zero memory of the last**, and a run's session often
outlives its summary — the candidate replies hours later, and that reply exists only in that transcript.
**The repo is the only thing that crosses the boundary.**

**START:** read `focus.md`'s `## 🔗 Session Handoff` (the last run wrote it for you), then
`list_sessions` — if any session here has `lastActivityAt` newer than the latest `log.md` entry,
read its tail with `list_events` and fold the candidate's instructions in before new work. **END:** rewrite
the Handoff block — a letter to a colleague who wasn't there. **A daily run can take 2+ hours**
(2026-07-19: 07:08 → 09:23); space schedules against that.

## Run hygiene

**Both task prompts inline the run-start commands** — that is where they are read. Dispose of what
they report BEFORE new work: **verify every system-state claim against the machine** (read the
plist, tail the log, run the script) and correct the tracker line in the same pass. A stale line is
how a wrong fact gets laundered into a confident report.

## End-of-session checklist

**The task prompts carry the ordered steps** — the `jobsearch:daily-run` skill and
the `jobsearch:weekly-review` skill. In short: update state → regenerate the dashboard → **grep the
OUTPUT for what you just added** → publish via the Artifact tool (passing
`dashboard_artifact_url.txt` as `url`) → commit → **push with `scripts/push.sh`** → **release the
run lock**. Save any pending draft in FULL to `drafts.md` and any pending letter to
`cover_letters.md` — the candidate reads the full text off the dashboard, not the transcript.
