---
description: 'Something is not working, or you want to know whether the search is working well. Use whenever the user says a thing is broken, wrong, missing, stuck, or slow — "this doesn''t work", "why didn''t it find that", "it keeps failing", "how do I get this fixed" — and for a periodic effectiveness review. Triages the complaint, then routes it: a fix here, a decision for the user, or an issue filed on the plugin''s repository.'
---

# Checkup — something is wrong, or is this working?

**⭐ THE COMMON ENTRY IS NOT A COMMAND. It is the user typing "this doesn't work."** Treat that
sentence as an invocation of this file. It is the most valuable input the engine ever gets — the
person using it, reporting a real failure — and before this existed there was **no route for it at
all**, so the report died at the end of the session.

## Two modes. Pick by whether the user named something specific.

| the user | mode |
|---|---|
| named a specific failure — "the LinkedIn sweep found nothing again" | **A · TRIAGE** — start at §T |
| asked broadly, or you are running a periodic review | **B · REVIEW** — start at §1 |

**Do not run the full review in answer to a specific complaint.** It is slow, it buries the thing
they asked about, and it reads as not having listened.

---

# §T · TRIAGE — for a specific complaint

**T1. Reproduce or locate it before classifying.** Ask which run, or find it: `log.md`, the run
summary, the script's own output. ⭐ **Never classify from the user's phrasing alone** — "it didn't
find anything" is the same sentence for an empty mailbox, an unsigned-in LinkedIn, a wrong data
root, and a genuine engine bug, and those have four different owners.

**T2. ⭐ IS IT ACTUALLY BROKEN? Rule this out first, every time.**

- **Absent on this surface is not broken.** A headless session has no browser; a cloud container
  has no keychain. That is routing — say which surface can do it (`docs/deployment.md` has the
  matrix) and stop. Filing it upstream wastes the engine team's time and teaches everyone the
  channel is noisy.
- **A correct empty answer is not broken.** A quiet day is normal at this cadence.
- ⚠️ **But an empty result from a source that could not be reached IS broken**, and the two look
  identical from outside. If a banner said `!! INCOMPLETE COVERAGE`, or a credential is missing,
  or the profile root resolved somewhere unexpected — that is the bug, and it is the failure this
  whole system is organised against. Check before concluding "working as intended."

**T3. Classify into exactly one bucket** (§2's table). If you cannot tell whether it is the engine
or this profile's data, **say so and check the engine file** — guessing sends it to someone who
cannot fix it.

**T4. Act on the bucket.** Engine → §3 and §4. Owner's decision → Your Move. Data or config here →
fix it and verify by re-running the thing. **Then tell the user which bucket it landed in and why**
— that is the answer to "how do I get this fixed", and it is more useful than the fix itself,
because it teaches where the next one goes.

---

# §B · REVIEW — the periodic pass

**⭐ THIS IS NOT `/jobsearch:doctor`, AND THE TWO MUST NOT BE MERGED.**

| | asks | how |
|---|---|---|
| `/jobsearch:doctor` | *is it installed and configured correctly?* | deterministic script, seconds, no model judgement |
| `/jobsearch:checkup` | *is it working effectively FOR YOU?* | deterministic reports **plus** an agent's judgement |

`doctor` has to stay fast, offline and deterministic — `setup-routines` calls it, and an
unattended run may. **An effectiveness review is judgement and dispatches an agent**; folding it
into `doctor` would make the one check that must always be cheap into one that sometimes is not.
A green `doctor` and a bad `checkup` is a perfectly coherent result: correctly installed, not
working well.

## 1. Facts first — never open with an opinion

```bash
~/.claude/jobsearch/run doctor.py
~/.claude/jobsearch/run funnel_report.py
~/.claude/jobsearch/run channels_due.py
~/.claude/jobsearch/run check_followups.py
~/.claude/jobsearch/run check_stale_claims.py
```

**Do not hand-derive anything these compute.** `funnel_report.py` refuses to print a rate below
n=5 and states plainly what the data cannot answer; deriving a rate by hand is how a confidently
wrong number reaches a review. If a number is not there, the honest answer is *"not enough data
yet"*, and saying so is more useful than a figure nobody should act on.

## 2. Sort what you found into exactly three buckets

**⭐ The bucket decides who can fix it, and putting an item in the wrong one guarantees nobody
does.**

| bucket | means | goes to |
|---|---|---|
| **the ENGINE is at fault** | a script, gate, skill or agent misbehaved | an **issue** on the plugin's repo — dispatch `engine-reporter` |
| **the OWNER must decide** | a credential, a cadence, a comp floor, an account | **⚡ Your Move**, System & tooling group |
| **the STRATEGY is off** | a channel yields nothing, follow-ups slip, targeting is wrong | `search-strategist`, or a direct recommendation here |

**Two mistakes to avoid, both of which have happened:**

- ⚠️ *"The mailbox has no stored credential"* is **not** an engine defect. No issue on the engine
  repo can place a credential in someone's keychain. It is the owner's, and filing it upstream
  moves it somewhere they do not look.
- ⚠️ A capability that is **absent on this surface** is not broken. A headless session has no
  browser; that is routing, not a defect. Say which surface can do it — `docs/deployment.md` in
  the plugin has the matrix.

## 3. Dispatch `engine-reporter` for the first bucket

It reads what actually happened, checks the plugin's open issues so it does not propose a
duplicate, and hands back ready-to-file proposals. **Dispatch it with no mention of approval** —
that is its propose-only mode, and it is the right one here.

## 4. Confirm, then let it file what was approved

Show the proposed issues and **ask.** Filing creates a permanent, externally visible record on
another team's tracker, so it is the owner's call every time.

**On a yes, re-dispatch `engine-reporter` and say in the prompt exactly which items were
approved** — it files those and only those. It has no channel to the owner and cannot ask, so
approval that is not written into its prompt does not exist as far as it is concerned. **Approval
is per item, never a blanket.** Filing directly with the command below is equally fine when only
one item is involved:

```bash
python3 <careers-plugins>/scripts/intake.py --add --dry-run …   # read it back first
```

⚠️ **State each bug as the RULE that misbehaved, never the instance.** The tool refuses a
submission carrying a comp figure, address, phone or name — it crosses from a private repo into
the engine's, and git history is permanent.

## 5. Say the honest headline

Lead with the single thing most worth changing, then the rest. **If the search is working, say
that in one line and stop.** A checkup that manufactures findings to look thorough teaches the
reader to skim the next one — and the finding that matters is usually the one they skimmed past.
