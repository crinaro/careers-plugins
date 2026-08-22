# How it works

A tour of what this plugin is, what runs when, and what it will never do without you.
If you just want it installed, start with **INSTALL.md** and come back here.

---

## The two halves — and why they are never the same folder

| | the engine | your profile |
|---|---|---|
| what it is | this plugin: skills, agents, scripts | your search: resume, contacts, roles, history |
| where it lives | wherever Claude Code installs plugins | a private directory **you** create and own |
| who can see it | anyone — it is public | only you |
| changes when | you update the plugin | every day you run it |

Everything personal lives on the right. Nothing personal lives on the left, and an automated
check enforces that on every change we publish.

This split is the single most important thing to understand, because it explains almost every
other design choice: the engine can be public, updated, and shared precisely because it holds
none of your data. Your profile is yours — back it up, put it in a private git repository if you
like, move it between machines.

> **You point the engine at your profile once.** After that, every command resolves it at
> runtime. If the pointer is missing the engine says so loudly rather than reading an empty
> folder and reporting that you have no opportunities.

---

## What actually runs

Four kinds of thing, and the difference matters when something misbehaves.

**Scripts** do the deterministic work — sweeping a mailbox, checking a calendar, finding
silences, building the dashboard. They are plain Python with no packages to install, they give
the same answer every time, and they cost nothing beyond running them. Most of the daily value
is here.

**Commands** are what you type: `/coordinator`, `/checkup`, `/tier`. They are entry points.

**Agents** are the parts that need their own context window — bulk reading (sweeping a mailbox,
scanning boards) or judgement (reading a job description against your resume, drafting a message
in your voice, researching a company). Each runs in its own context and reports back.

**Skills** are procedures the assistant follows, loaded when a session starts.

> ⚠️ **Skills, agents and hooks load once, when a session starts.** If you update the plugin
> mid-session, scripts pick up the new version immediately but the loaded instructions do not.
> Start a fresh session after an update.

> **Gmail is a second plugin.** The tools that read, search and draft mail — this plugin never
> sends — come from `gmail-multi`, a standalone connector. Right after you install this plugin,
> its first session start installs the connector for you, from the same marketplace you
> installed this plugin from; you do not configure it separately, and the first session after
> that also points it at your profile, so a mailbox you add here reaches it without any second
> setup step. It is not this plugin misbehaving if you see `gmail-multi` in your
> installed-plugins list; it is how mail coverage works. **The tools take one extra session to
> arrive** — start a fresh session after installing before you expect Gmail tools to show up.
> **If mail tools are still missing after that,** run `claude plugin list`: this plugin's own
> commands keep working either way, so the symptom of a missing connector is never this
> plugin's commands going unrecognized — it is specifically the absence of Gmail tools. If the
> connector did not install itself (it will say so plainly when that happens), installing
> `gmail-multi` yourself from the same marketplace fixes it.

---

## A day, in order

Nothing below sends anything or commits anything without you.

1. **Session start.** The plugin checks its own installation is intact, and brings your profile's
   data format up to date if the version moved — the first such update also points the Gmail
   connector plugin at this profile, a one-time step (see **What actually runs**, above). All of
   this is automatic; none of it asks you anything.

2. **Sweep.** Your mailbox and LinkedIn are read for things that changed: recruiter replies,
   interview invitations, application receipts, rejections. This is deterministic — it finds
   what is there, it does not interpret.

3. **Reconcile.** New findings are matched against roles you already track. A role seen twice on
   two job boards is *one* opportunity with two sightings, not two opportunities. Ambiguous
   matches are surfaced for you to confirm rather than merged automatically, because one job
   title can genuinely be several different roles.

4. **Screen.** Roles are checked against the floors you set — compensation, geography, work
   setting. Something below your floor does not silently vanish; it is marked, and you can see
   why.

5. **Surface.** What needs a human decision is collected: replies waiting, applications with no
   acknowledgement, conversations that have gone quiet, questions the assistant needs answered
   to pitch you accurately.

6. **Draft — only if you asked.** Outreach and cover letters are written citing sentences from
   your own resume rather than paraphrasing them. A draft is *staged*. You read it, you edit it,
   you send it.

---

## The rules it holds itself to

**It never sends.** Not email, not LinkedIn messages, not applications. Drafts are staged for
you. This is structural, not a setting.

**It never handles a credential.** Your passwords and app passwords live in your operating
system's keychain, placed by you. The engine asks the keychain for what it needs.

**It never invents a fact about you.** If a job description asks for something your resume does
not evidence, the honest answer is recorded as a gap and you get asked a targeted question. An
alignment claim without a citation is treated as a gap wearing a disguise.

**It prefers a query to a summary.** Your dashboard and reports are computed from your data
every time they are shown. Nothing is a stored summary that was true when written and wrong
when read.

**When it does not know, it says `unknown`.** A deliberate `unknown` is countable — a report can
tell you "12 unclassified". A guess folded quietly into a bucket cannot.

---

## Cost

Roughly `runs per day × agents per run`. The deterministic half — sweeps, calendar checks,
silence detection, the dashboard — is free at every tier and carries most of the daily value.

| posture | runs/day | agents/run | what you get |
|---|---|---|---|
| `minimal` | 1 | 0 | sweeps only |
| `economy` | 2 | 1 | + LinkedIn — **the default** |
| `standard` | 3 | 2 | + research |
| `full` | 5 | 5 | + drafting |

Set `search.posture` in your configuration, or define your own tier.

---

## What needs a desktop

LinkedIn has no API. Reaching it needs a real, signed-in browser on a desktop — it cannot run
from a phone or from the cloud, and it is roughly half of the outreach funnel. Everything else
(mailbox, screening, reports, drafting) runs anywhere.

---

## When something looks wrong

Ask for a checkup first — it verifies the installation, the profile pointer, and your data's
referential integrity, and it tells you which of the three is at fault.

The most common cause of "it says I have no opportunities" is a profile pointer aimed at the
wrong directory. The second is a session started before an update finished. Both are reported
explicitly rather than shown as an empty result.

You may also see an agent refuse outright with a message like *"NOT BOUND — REFUSED
(pointer-only)."* That is deliberate, not a bug. This plugin installs once for your whole
machine, so its agents are technically reachable from any Claude Code session, in any project —
and this refusal is what stops one from reading or writing your search by accident when it is
dispatched from somewhere that is not your job search. It means: this session shows no evidence
it belongs to your profile — you are not running from your profile directory, and you have not
told the session which profile to use. Run the command again from your profile directory, or
from a session that is already part of your job search, and it will proceed normally.

To report a genuine defect, see **[reporting-issues.md](../reporting-issues.md)**.
