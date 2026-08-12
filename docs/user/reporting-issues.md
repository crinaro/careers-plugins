# Reporting a problem

Before filing anything, it is worth separating three things that look identical from the outside:

| symptom | usually | what to do |
|---|---|---|
| "it says I have no roles / no messages" | the profile pointer is aimed at the wrong directory | run a checkup — it names which of the three layers is at fault |
| "the new feature isn't there" | the session started before the update finished | start a fresh session; skills and agents load once, at session start |
| "it did the wrong thing with my data" | a genuine defect | file it, using the guidance below |

A checkup verifies the installation, the profile pointer and your data's referential integrity,
and tells you which one is wrong. It is almost always faster than guessing.

---

## What belongs here, and what does not

**File an issue when the plugin misbehaves for anyone** — a script crashes, a check passes when
it should fail, a capability is missing, a document contradicts what the code does.

**Do not file** questions about how *your* search should be run — which titles to target, which
regions, how often to sweep, whether your compensation floor is right. Those are answered inside
your own session against your own data, and nobody else can act on them.

A useful way to test which one you have: *would this change be an improvement for everyone who
installs the plugin, or only for me?* If it is only for you, it belongs in your configuration
rather than in the engine. A one-off change that serves one person's setup adds surface everyone
else has to maintain.

Three outcomes are all legitimate, and two of them are not failures: it gets fixed, it gets
closed as not-a-defect with the reason stated, or it gets generalised — because the report was a
symptom and the class of problem is worth fixing instead of the instance.

---

## ⚠️ Never include personal data

Issues are **public and permanent**. Git history and issue history cannot be meaningfully
scrubbed after the fact.

Before pasting anything, remove:

- your name, email address, phone number and physical location
- any other person's name — recruiters, hiring managers, referrals
- employer names, whether you work there, applied there or were approached by them
- LinkedIn profile URLs, message threads and job-posting URLs that identify you
- compensation figures
- file paths containing your username

**Replace them, do not delete them.** `<a contact>`, `<an employer>`, `<a location>` keep the
report readable and reproducible. The shape of the problem is what makes it fixable; the actual
values almost never are.

---

## What makes a report actionable

1. **What you ran** — the command or what you asked for.
2. **What happened** — the actual output, with identifiers replaced as above.
3. **What you expected instead**, and why.
4. **Whether it repeats** — every time, or once.
5. **Your plugin version**, which a checkup prints.

The single most valuable thing you can add is the difference between what the output *said* and
what was *true*. This project's recurring failure is a check that reports success having examined
nothing — an empty result reading as a clean one. If something claimed to be fine and was not,
say so explicitly, because that is the class of bug that hides best.
