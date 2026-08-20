# Reading what the search produces

The search writes files that exist to be read by you — company knowledge, call preps, drafts
awaiting your review, and the dashboard. They are not all the same kind of file, and **they do
not all open in the same tool**: the dashboard is HTML and wants a browser, everything else is
markdown and wants a markdown viewer. Open one in the wrong tool and you get something readable
but ugly — raw HTML source, or a wall of `#` and `>` markers where formatting should be.

This page says which file opens where, and walks through every realistic way to get at them.

---

## What there is to read

All paths are inside **your profile directory** (the private folder you created at setup — see
[Your data](your-data.md)).

| file | what it is | format |
|---|---|---|
| `dashboard.html` | the generated dashboard: pipeline state, what needs you, drafts and letters in full | HTML |
| `dashboard_artifact.html` | the same dashboard, in the variant that gets published as a claude.ai Artifact | HTML |
| `dashboard_artifact_url.txt` | the URL of the published Artifact — a link, not a page | plain text |
| `kb/<company>.md` | durable knowledge about one company, e.g. `kb/acme-health.md` | markdown |
| `call_preps/call_prep_<date>.md` | prep notes for a scheduled call, e.g. `call_prep_2026-01-15.md` | markdown |
| `drafts.md` | staged outreach messages awaiting your review | markdown |
| `cover_letters.md` | cover letters awaiting your review | markdown |

The datasets (`data/*.jsonl`) are deliberately not in this table — they are for querying, not
reading, and [Your data](your-data.md) covers them. Neither is `.jobsearch/`, a folder you will
also see in your profile: it holds engine diagnostics, not anything about your search, and
[Your data](your-data.md#jobsearch-engine-state-not-your-data) explains what it is.

---

## Which file opens in which tool

This is the part nothing else tells you, so here it is as a table:

| you want to read | open it in | in the wrong tool you get |
|---|---|---|
| the dashboard | a **web browser** — double-click `dashboard.html`, or open the published Artifact link | a text editor shows thousands of lines of raw HTML |
| a call prep, kb file, `drafts.md`, `cover_letters.md` | a **markdown-rendering viewer** — the desktop app, or an editor with markdown preview | a browser or plain editor shows the unrendered source: readable, but the structure that makes a call prep scannable is gone |
| the Artifact URL | it is just a link — open the file, copy the URL into any browser on any device | — |

Rule of thumb: **`.html` means browser, `.md` means markdown viewer.**

---

## The routes, cleanest first

### 1. The desktop app's `</>` Code side — recommended

Open the Claude Code side of the desktop app in your profile directory and ask for what you
want: *"show me the call prep for tomorrow"*, *"what is in the kb file for Acme Health?"*,
*"read me the pending drafts."* The session finds the file, renders the markdown properly, and
can answer questions about it — which no file manager can. This is the cleanest route because
it is the same place the search already runs, and it needs no extra tooling.

For the dashboard, ask the session to open `dashboard.html` in your browser, or use route 2.

### 2. The published dashboard Artifact — best on a phone

Every daily run republishes the dashboard as a claude.ai Artifact at a **stable URL**, kept in
`dashboard_artifact_url.txt` in your profile. Open that URL in any browser, on any device —
it is the one route that needs neither the desktop app nor the local folder, which makes it
the practical way to check state from a phone. Drafts and cover letters appear there in full,
and since the fix for issue #20, so do your kb files and call preps, rendered as content.

The Artifact is **default-private** to your claude.ai account. It shows what the last run
published; for state newer than the last run, use routes 1 or 3.

### 3. The local folder, with a markdown-capable editor

Everything is a plain file, so any editor opens it — but for the markdown to *render* you want
an editor with a markdown preview. VS Code is the common choice: open your profile folder,
select a `.md` file, and toggle preview with **Cmd+Shift+V** (macOS) or **Ctrl+Shift+V**
(Windows/Linux). Any dedicated markdown viewer works the same way. Without a preview you get
the raw source — every fact is there, the scannability is not.

### 4. The browser, for the HTML only

Double-click `dashboard.html` and it opens in your default browser, fully rendered, no server
needed. This is the freshest view after a run finishes locally. Do **not** open the `.md` files
this way — a browser does not render markdown, so a call prep becomes a single run-on wall of
text.

### 5. Your profile's private git remote, if you have one

If you followed the backup recommendation and your profile syncs to a **private** git
repository, the hosting site's web view renders markdown files properly — which quietly gives
you a phone-friendly reader for kb files and call preps too, at whatever freshness your last
push was. This route is only as private as that repository; keep it private.

---

## The morning-of-an-interview case

A call prep exists precisely because something is scheduled soon, so the fast paths matter:

- **At a computer:** route 1 — ask the session for the prep by date or company.
- **On a phone:** route 2 — the published dashboard renders call preps as content.
- **No Claude available at all:** route 3 or 5 — the file itself, in anything that shows
  markdown.

---

## What this page is not

It does not decide which of these routes is *supported* — they all are; the plugin's files are
plain HTML and markdown exactly so that no single tool owns them. It also does not cover
editing: for changing files by hand and validating afterwards, see
[Your data](your-data.md#editing-by-hand).
