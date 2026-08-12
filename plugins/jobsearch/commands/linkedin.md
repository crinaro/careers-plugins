---
description: Check whether LinkedIn is signed in for the job search, and walk the user through signing in if it is not.
---

# LinkedIn sign-in

**LinkedIn is where the outreach funnel lives.** Replies to invitations, message requests,
acceptances and contact paths exist nowhere else — **the mailbox receives no LinkedIn notification
emails** (verified: zero over two days despite two known acceptances), so the browser is the ONLY
detector for these events. A search with no session finds nothing and reports a quiet week.

## 0. ⭐ FIRST — IS THERE A BROWSER AT ALL?

**A headless / CLI session (`claude -p`) has NO browser tools — neither surface.** Verified
2026-08-05: `mcp__Claude_Browser__*` and `mcp__claude-in-chrome__*` are both absent there, so this
command cannot run and nothing is wrong with the LinkedIn session.

If neither tool family is available, say exactly that and stop:

> LinkedIn sign-in has to be done in the Claude desktop app — this session has no browser. Open
> the app and run `/jobsearch:linkedin` there.

**Do NOT report `BROWSER UNAVAILABLE`, and do NOT substitute a WebFetch.** They mean different
things and the distinction matters: `BROWSER UNAVAILABLE` says the browser is broken and the sweep
should be queued; "no browser in this session" says the sweep belongs somewhere else. And a
WebFetch of LinkedIn returns a logged-out page as HTTP 200 with plausible content — a confident,
worthless answer, which is also why CLAUDE.md bars taking any LinkedIn fact from a fetch.

## 1. Check the session

Open LinkedIn in Claude's own in-app browser and read the page:

- `mcp__Claude_Browser__navigate` → `https://www.linkedin.com/feed/`
- `mcp__Claude_Browser__get_page_text`

Get the user's own name with `~/.claude/jobsearch/run profile.py` — **never hard-code or guess it**.

⚠️ **MATCH THE FIRST AND LAST NAME TOKENS, NOT THE WHOLE STRING.** A profile's `full_name` often
carries a middle initial that LinkedIn does not display: "the candidate M. the candidate" in `user.json` against
"the candidate" on the page. A strict substring match reports a logged-OUT session for one that is
perfectly signed in — and the run then queues work it could have done. Caught 2026-08-05 while
testing this command.

**Signed in** → the feed text carries both name tokens plus their headline. Say so, name the
surface, stop.

**Not signed in** → a sign-in wall, or the feed without their name.

⚠️ **A logged-out LinkedIn page still returns HTTP 200 with plausible content.** Never treat
"the page loaded" as "the session works" — check for the name specifically. This failure is silent
and looks exactly like "nothing new," which is how a whole run once reported an empty inbox it
could not actually see.

## 2. If not signed in — ⛔ THE USER SIGNS IN, NOT YOU

Tell them, in plain terms:

> The Browser pane is open on LinkedIn's sign-in page. Please sign in there yourself — including
> any 2FA — and tell me when you're done. I won't see or handle your password.

**Do not type a password, do not accept one pasted in chat, and do not offer to store one.** A
password in a transcript is retained. If a password manager integration is available, the user
approves each item in the password manager's own prompt and the value never reaches this session —
that is the only acceptable assisted path.

**Do not attempt any CAPTCHA or bot check.** If one appears, the user clears it.

Then re-run the check in step 1 and confirm.

## 3. Report which surface the search will use

| state | what the daily run does |
|---|---|
| in-app browser signed in | **Preferred.** No Chrome, no extension, nothing that touches the user's own browser |
| in-app not signed in, Chrome extension connected | Falls back to the extension and their real Chrome session |
| neither | Reports `BROWSER UNAVAILABLE` and **queues the sweep** via `deferred.py` — the work is not lost, it waits for a session that can do it |

## When to suggest this

Run it as part of setup, and **any time a run reports `BROWSER UNAVAILABLE` or a suspiciously
quiet LinkedIn pass.** A session can lapse silently; re-checking costs one page load, and the
alternative is a funnel that looks empty because nothing could see it.
