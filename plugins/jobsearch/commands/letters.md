---
argument-hint: "[drive | local_docx | <opp-id>]"
description: Configure where finished cover letters go — a Google Doc in your Drive folder, or a local .docx — and render one.
---

# Cover-letter output

Show the current setting:

```bash
~/.claude/jobsearch/run letter_out.py --status
```

Switch modes (`$ARGUMENTS` may name one):

```bash
~/.claude/jobsearch/run letter_out.py --set-mode local_docx
~/.claude/jobsearch/run letter_out.py --set-mode drive
```

Render a drafted letter:

```bash
~/.claude/jobsearch/run letter_out.py --render "<an employer>"
```

## The two modes

**`drive`** — the letter becomes a Google Doc in the job-search Drive folder. Needs a Google
account and the documents connector. The folder id comes from `config.drive.job_search_folder_id`.

**`local_docx`** — the letter is written as a real `.docx` next to the profile, using the standard
library alone. **No Google account, no connector, no network.** Use this when someone has no Drive,
or a workplace that blocks it.

⭐ `drive` is the default only because it was the original behavior. It is not the better mode —
**if `--status` reports mode `drive` with no folder id configured, that is worse than `local_docx`**,
because a document created without a `parentId` lands in My Drive root and the connector **cannot
move a file afterwards**. The only remedy is a second copy for the user to delete.

## ⚠️ In `drive` mode, the script does NOT create the document

It prints the `parentId` and stops, deliberately. **The connector is CREATE-ONLY — no update, no
delete** — so a document must be pushed exactly once, when the text is genuinely final. Never
pre-stage one. After creating it, **read the document back to verify it**: the create response's
reported size is meaningless for a native Google Doc.

## What to check before rendering either way

`--render` reports these and will not write an empty document:

- **The body must be `> `-blockquoted.** A letter written as plain text publishes EMPTY, and the
  source file reads perfectly while it happens — every constraint check passes against the file
  rather than the deliverable. An empty body is indistinguishable from "not drafted yet."
- **No banned characters** (the em-dash especially) and **US English**, both read from
  `config.writing`.
- **One page.** The script warns on length, but it cannot measure pages. **Open the file and look**
  before sending — a second page carrying only a signature is a real defect. If it spills, cut
  margins before cutting substance.
