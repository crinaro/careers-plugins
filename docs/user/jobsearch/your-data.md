# Your data

Everything the plugin knows about your search lives in **your** profile directory, in files you
can read, edit and diff. There is no database and no hidden state. This page explains what each
file holds, what the important fields mean, and how to change things by hand safely.

---

## Two kinds of file, and the rule that decides

**Datasets are JSON. Documents are markdown.**

If something gets counted, sorted, filtered or joined to something else, it is data and lives in
a `.jsonl` file. If something is prose you read and edit in full, it is a document and lives in
markdown. The dividing question is *"would I ever want to ask a question across all of these?"*

That is why a cover letter's **text** is markdown but the **link** between that letter and the
role is a field: you will never sort your letters, but you will absolutely want to ask whether
applications with a letter attached get more responses.

### The datasets — `data/*.jsonl`

One record per line. Hand-editable, and every line is a self-contained JSON object, so a change
shows up as a one-line diff rather than a reformatted file.

| file | holds |
|---|---|
| `companies.jsonl` | employers — one record each, however many roles they post |
| `channels.jsonl` | where roles come from: job boards, company career pages, recruiting firms, referrals |
| `opportunities.jsonl` | the roles themselves, with their contacts, outreach, applications and fit analysis |
| `messages.jsonl` | every communication, both directions, with its full text |

### The documents

| file | holds |
|---|---|
| `resume.md` | your resume, plus an *Additional Detail* section for things a resume never says |
| `projects.md` | projects and their scale, each with a note about when it is worth surfacing |
| `focus.md` | what needs your attention now — generated, not hand-maintained |
| `drafts.md` | staged messages awaiting your review |
| `cover_letters.md` | letters, one anchor per role |
| `kb_<company>.md` | what you have learned about a specific company |

---

## Companies

An employer, recorded once. Twelve roles at the same company is one company record and twelve
opportunities — so a fact you learn about the company attaches in one place instead of being
copied twelve times.

| field | what it is |
|---|---|
| `id` | a short stable slug you will see referenced elsewhere |
| `name`, `aliases` | display name, plus other names it gets listed under |
| `vertical` | the sector, used for tiering and search targeting |
| `size_ownership` | e.g. public, PE-backed, employee count — context for whether the role is a fit |
| `career_url` | its own careers page; setting this makes the company reviewable as its own channel |
| `status` | `active-target` · `watching` · `passed` |
| `research_log` | append-only notes; company-level findings go here, not on each role |

## Channels — where roles come from

A channel is any source: a job board, an aggregator, a company's own careers page, a recruiting
firm, a referral, an alert email.

**A recruiting firm is just a channel** whose `type` is `recruiter`. A role a recruiter brings
you is a sighting whose channel is that firm — which means "which recruiters actually produce
roles I pursue?" is a query, not a memory exercise.

| field | what it is |
|---|---|
| `review_cadence` | `daily` · `weekly` · `biweekly` · `monthly` · `on-inbound` — drives the "what is due?" queue |
| `last_reviewed` | the date it was last checked |
| `scope_notes` | which titles, filters and locations this channel covers |
| `access` | how it is reached — see below |
| `contacts`, `relationship_status`, `log` | for firms and referrals: who, where you stand, and the thread history |

`access` states what a source **requires**, not the mechanism used to reach it:

- `public` — no login, reliable
- `login-chrome` — needs your signed-in desktop browser (this is LinkedIn)
- `public-bot-limited` — searchable by hand, but automation gets blocked
- `manual-candidate` — you review it yourself
- `human` — a recruiter or a referral
- `n/a`

## Opportunities — the roles

The main record. Everything about one role hangs off it.

| field | what it is |
|---|---|
| `company_id` | must resolve to a company |
| `title` | |
| `comp` | `{min, max, period, basis}` as **typed numbers**, so it sorts and screens. `null` if genuinely undisclosed — never a guess |
| `location` | `{type, primary, remote, declared}` — see *contested settings* below |
| `status` | what you are **doing** about it |
| `stage` | where it **is** in the funnel |
| `verdict` | `pursue` · `pass` · `parked` · `undecided` |
| `jd_url` | the posting. Required as a URL **or an explicit `null`** — never simply missing |
| `sightings` | every time this role was seen, and where |
| `next_action`, `next_action_date`, `next_action_owner` | what happens next, when, and whose move it is |
| `research_log` | append-only role history |

### `status` and `stage` are different questions

This is the one thing people mix up, so it is worth stating plainly:

- **`status`** = what you are doing about it → `active-pursuit` · `needs-resolution` · `in-motion` · `backlog` · `passed` · `expired`
- **`stage`** = where it sits in the funnel → `sourced` · `contacted` · `screening` · `interviewing` · `offer` · `closed`

They are orthogonal and you want both. A live pursuit waiting on a recruiter is
`status: active-pursuit`, `stage: contacted`. A role you have shelved is `status: backlog`,
`verdict: parked`, `stage: sourced`.

### Two values that exist to stop a guess

**`location.type: unresolved`.** Some postings declare two work settings at once — tagged both
hybrid and remote. Picking one silently decides which compensation floor applies. So instead the
setting is recorded as `unresolved`, with the posting's **verbatim** wording kept in `declared`,
and screening **declines to pick a tier**. The role stays in your pipeline and the question goes
to the employer. It is never quietly dropped.

**`status: expired`.** A posting that vanished before you ruled on it is not a role you passed
on. Recording it as `passed` would overstate how selective you are being. `expired` records the
*absence* of a decision — and because you never declined it, a repost of that same role surfaces
as a fresh signal rather than being filtered out.

### Contacts — the people

Contacts live **on the opportunity**, because that is where the conversation happens.

| field | what it is |
|---|---|
| `contact_id` | required and unique within the role — this is the join key |
| `name` | a name. URLs and email addresses go in their own fields, not in here |
| `email`, `linkedin` | structured and validated, never prose |
| `role`, `path_type`, `status`, `notes` | who they are, how you reached them, where it stands |

`path_type` is `warm-referral` · `recruiter` · `hiring-manager` · `hiring-context` ·
`internal` · `cold`.

> **If you messaged someone, they are a contact of that role by definition.** Every outreach
> record must point at a contact that exists, and this is enforced — which is what makes
> "what is my whole history with this person?" answerable.

*Known limit, stated rather than hidden:* contact IDs are unique within a role, so the same
person appearing on two roles is two records. Searching by name spans every role, which covers
the practical need.

### Outreach — messages you sent

One record per touch. The fields exist to make "which approach actually works?" a real question.

| field | why it exists |
|---|---|
| `medium` | a LinkedIn connection note, an InMail and a direct message get read at completely different rates. Pooling them makes any reply rate meaningless |
| `touch_type` | a first touch and a chase have different base rates |
| `recipient_role` | a hiring manager, a recruiter and a peer are not the same audience |
| `campaign_id` | groups a multi-touch push so it can be evaluated as one thing |
| `address_status` | required for email — records whether the address was verified or pattern-guessed |
| `delivery` | `delivered` · `bounced` · `unknown`. **Bounces are excluded from every denominator** — a bounce that looks like a non-reply poisons the metric |
| `outcome` | `awaiting` · `replied` · `accepted` · `no-response` · `declined` · `meeting-booked` |
| `message_ref` | points at the full text in `messages.jsonl`, and must resolve |

`accepted` — an accepted connection request that drew no reply — is reported on its own line and
never merged into `replied`, because it is a real positive signal that unlocks a better second
touch.

### Applications — places you applied

Deliberately **separate** from outreach, because they are different funnels with different
success measures. An application asks *did anyone respond at all*; outreach asks *did this
person reply*. Collapsing them makes both unmeasurable.

`{date, method, url, status, cover_letter, cover_letter_attached, notes}`

`cover_letter` says a letter **exists** for the role. `cover_letter_attached` says one was
actually **submitted**. They are separate because only you know the second, and an assumed
`true` would corrupt the only comparison that makes your letters measurable. Leave it `null`
rather than guessing.

### Fit — how you match the role

Optional. Absent means the job description has not been analysed yet.

Each material requirement from the posting becomes a row: the requirement in the posting's own
words, a verdict of `aligned` · `partial` · `not-aligned` · `unknown`, and then:

- **`evidence`** — required when aligned or partial. A pointer to the resume sentence, project
  or note that backs the claim. An alignment claim with no citation is a gap in disguise.
- **`pitch_line`** — how to *present* that match, so your positioning is not reinvented for
  every draft.
- **`question_for_candidate`** — required when `unknown`. A targeted question, asked only when
  the answer would change the pitch.
- **`landed_in`** — where your answer was filed, so the same question is never asked twice.

`not-aligned` rows are kept, not suppressed. They tell you where you are stretching, and a fit
analysis that lists only matches is marketing rather than analysis.

Counts are always **computed** from these rows, never stored.

---

## Editing by hand

You can. The formats exist to be readable and diffable. Two habits make it safe:

**Validate after editing.** A validator checks the whole profile: that every company and channel
reference resolves, that enum values are in range, that `comp.min ≤ comp.max`, that dates are
ISO `YYYY-MM-DD`, that slugs are unique, and that required fields are present rather than
missing.

**Unknown keys are rejected.** If you misspell a field name the validator refuses it rather than
silently ignoring it. Aliases drifted into the data historically precisely because nothing
rejected them.

Two nuances worth knowing:

- Newly required fields apply only to records dated after they were introduced. Older rows are
  explicitly grandfathered rather than failing the validator on day one.
- `unknown` is a legitimate value, not a gap. It makes the hole countable.

---

## Backups

Your profile is yours and lives outside the plugin. Nothing the plugin updates will touch it
except the data-format migrations, which run automatically, preserve content before transforming
it, and are safe to run twice.

Putting your profile directory in a **private** git repository is the recommended backup — you
get history and a diff of every change your search makes.
