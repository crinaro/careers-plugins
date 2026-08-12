---
name: onboarding
description: Set up a NEW user's job search from scratch — read their resume, fill user.json and config.json, build resume.md and projects.md, and elicit the facts a resume never contains (comp floors, relocation, hard boundaries). Use when someone is installing this for the first time, says "set up my job search", "onboard me", or has run init_profile.py and needs the conversational half.
---

# Onboarding — the conversational half

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



`scripts/init_profile.py --scaffold` already made the files. **Your job is the part that needs a
human: reading their resume, and asking what a resume does not contain.**

**⚠️ THIS IS THE MOST EXPENSIVE CONVERSATION THE SYSTEM WILL EVER HAVE, AND IT IS WORTH IT.**
Everything downstream — every screen, every draft, every pass/pursue call — reads what you write
here. A rushed onboarding produces a system that confidently does the wrong thing forever. Take the
time; it is one-time.

## 0. Scaffold first if it has not been done

```bash
~/.claude/jobsearch/run init_profile.py --check
```

Missing files → `--scaffold`. **Never hand-create these**; the script guarantees they validate.

## 1. Read the resume — and copy it, do not summarize it

Ask for it in any form (paste, file, PDF). Then:

- **`resume.md` gets the resume VERBATIM.** Its own sentences, its own numbers, its own bullets.
  This file becomes the source of truth for every background claim the system ever makes, and
  **paraphrasing at this step poisons everything downstream** — a summarized bullet once dropped
  the clause naming an employer's marquee customers, and the sentence survived while the credential
  in it did not. Copy. Do not improve.
- **`user.json`** gets name, city/metro, contact details, LinkedIn URL, mailboxes.
- **`projects.md`** gets one entry per substantial thing they built, each with a
  **`Surface when:`** trigger naming the kind of JD it answers. This is what later drafts grep.

## 2. Elicit what the resume does NOT say — the actual value of this step

A resume is a public document, written to be safe. The facts that win roles are usually absent from
it. **Ask these, and record the answers in a `## Additional Detail (elicited beyond the resume)`
section of `resume.md`:**

1. **Customers, scale and numbers that were left off.** "Your resume says you built X — who used
   it? How many? What did it save?" Marquee customer names are routinely omitted and are routinely
   the most persuasive fact available.
2. **Compensation, precisely, and the BASIS.** Floors are **base salary**, not total. Ask for a
   floor per setting — remote, hybrid, onsite-local, relocation — because they genuinely differ.
   **An office inside their commute radius is a LOWER bar than relocation**; conflating them
   silently mis-screens roles.
3. **Geography.** Commute anchor and realistic radius in minutes. Where would they actually move,
   and for what? Remote acceptable?
4. **⭐ What they will NOT do.** The most under-asked question in job search. Deep-IC roles with no
   org influence? Pure sales quotas? Domains they are done with? **Record these as
   `positioning.scope_boundaries`.** Naming a limit makes everything else more credible — and it
   stops the system spending weeks sourcing roles they would decline.
5. **Titles they are actually targeting**, plus the ladder synonyms their industry uses (Staff VP,
   Executive Director, Lead Director, Managing Director all mean different things in different
   sectors).
6. **Availability**, and whether their current employer knows.

**Ask these one or two at a time, conversationally.** A wall of twenty questions gets skimmed
answers, and skimmed answers become confident wrong screens.

## 3. Set the budget posture — say the trade-off out loud

```bash
~/.claude/jobsearch/run posture.py
```

Default is `economy`. **Tell them what each tier costs them in latency**, not just in tokens:
`minimal` means LinkedIn is never swept unattended; `economy` means a reply may sit hours.
**They should choose knowing that**, rather than discover it when a recruiter's message sits for a
day. Moving tiers later is one config edit.

## 4. Hand over credentials — never take them

Point at `CREDENTIALS.md` and stop. **Do not ask for a password, do not offer to store one, do not
accept one if it is pasted.** If they paste a credential, tell them plainly to rotate it.

They need: an IMAP app password in their own OS keychain, and Chrome plus the extension signed in
if they want LinkedIn. **Say clearly that LinkedIn requires a desktop and cannot work from a phone
or the cloud** — it is the one irreducible local dependency, and it is roughly half the outreach
funnel.

## 5. Verify, then prove it works

```bash
~/.claude/jobsearch/run validate_data.py     # must be clean on an empty profile
~/.claude/jobsearch/run profile.py           # reads back what you just wrote
~/.claude/jobsearch/run profile.py --screen-all
```

**Read `profile.py` back to them.** It renders their comp tiers, geography and signature from the
config — if anything reads wrong, it is wrong in the data, and this is the cheapest moment to fix
it.

## 6. Seed the pipeline with ONE role, end to end

Do not leave them with an empty system. Take one real job posting they care about, walk it all the
way through — sourced, researched, screened against their comp floors, a draft written — and show
them the dashboard. **A system that has done one useful thing gets used; an empty one gets
abandoned.**

## 7. ⭐ TURN THE ROUTINES ON — the step that makes it a system rather than a tool

**Do not stop at a working profile.** Everything so far runs only when they ask. The background
routines are what catch a recruiter reply at 9am while they are in a meeting, and a new user has no
idea they need to be created.

**Invoke `/jobsearch:setup-routines`.** It creates `search-daily` and `search-strategy-weekly` as
thin pointers to the plugin's own skills, with the cron taken from the tier they chose in step 3.

⚠️ **Never hand-write a scheduled-task prompt.** Pinning run steps into a per-machine file is
precisely what made an earlier version unmaintainable: a plugin improvement could never reach an
installed routine, and one prompt drifted so far it was still telling runs to edit files retired
weeks earlier.

## 8. Verify, and hand them the one command they need to remember

```bash
~/.claude/jobsearch/run doctor.py
```

Green means the profile is healthy AND current with the installed plugin. **Tell them this is the
command to run after any plugin update** — it is the only thing that catches a config that has
fallen behind the engine, or a schedule that no longer matches the tier they are paying for.

Then tell them, in one line each, what now happens without them: when the sweeps run, and that
**nothing is ever sent on their behalf.**

## Last steps — connect the two surfaces the search actually reads

**Both are required, and BOTH FAIL SILENTLY IF SKIPPED.** A profile with neither is a search that
reports a quiet week forever, because it has nothing to read.

1. **`/jobsearch:mailboxes`** — the mail accounts. An unreachable account does not raise; the
   sweep just covers less and reports the smaller result as the whole picture.
2. **`/jobsearch:linkedin`** — the browser session. **The mailbox gets no LinkedIn notification
   emails**, so the browser is the only detector for replies, acceptances and message requests.
   Once signed in, the session persists across runs.

⛔ **The user signs in themselves, in their own browser.** Never ask for a password, never store
one, never sign in on their behalf.

## What NOT to do

- **Do not invent a fact to fill a field.** An empty field is honest; a plausible guess becomes a
  claim in a cover letter three weeks later.
- **Do not set comp floors "provisionally."** They are the screen. A guessed floor silently
  discards real roles or fills the pipeline with ones they would never take.
- **Do not leave the routines uncreated.** A profile with no schedule is a tool they must remember to use, and they will not. Step 7 is not optional.
- **Do not skip the boundaries question** because it feels negative. It is the highest-signal
  answer in this whole conversation.
