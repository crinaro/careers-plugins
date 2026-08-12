#!/usr/bin/env python3
"""Generate dashboard.html from the tracker markdown files.

Deterministic, zero-token rendering: the model only maintains the .md files
(especially focus.md); this script assembles the dashboard HTML.

Usage: python3 scripts/generate_dashboard.py   (run from the profile folder root)
Output: dashboard.html in the folder root.
"""
import datetime
import html
import re
import os
from pathlib import Path

import os, sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root as _profile_root
import profile as _profile

# ⚠️ .absolute(), NOT .resolve() — 2026-08-05. `.resolve()` FOLLOWS SYMLINKS, and the tracker
# consumes this engine as a submodule with `scripts -> engine/scripts`. Resolving made ROOT
# the ENGINE directory, which holds no data, so the dashboard regenerated with ZERO
# opportunities and silently overwrote the real one. Every sibling script uses
# os.path.abspath, which does NOT follow symlinks; this file was the lone exception.
ROOT = Path(_profile_root())
# The literal `next_action_owner` value meaning "the candidate must act" — this candidate's own
# reference token, never a hardcoded name. See profile.owner_token().
OWNER_TOKEN = _profile.owner_token()
# The profile's own currency symbol, never a hardcoded "$" — see profile.currency_symbol():
# relabelling another currency as dollars is a right number under a wrong unit.
CURRENCY = _profile.currency_symbol()


FOCUS_CLAMP = 240  # chars of focus "why" prose shown before collapsing behind a toggle

# ⭐ `next_action` IS NOT A SHORT IMPERATIVE — measured, not assumed. On the live pipeline all 35
# live roles carrying one are over 120 chars: median 419, max 1052. They are recommendation
# memos ("YOUR CALL, recommend PASS - act by ... Three independent reasons ..."). Rendering
# them in full put a wall of text in the action colour on every row and destroyed the scan the
# row layout exists for. The FIRST clause is the valuable part — it carries the verdict and the
# act-by date — so the row shows that and the rest lives under Detail.
OPP_ACTION_CLAMP = 110



# ⭐ THE DASHBOARD TITLE IS DATA — generalised 2026-08-09.
#
# It was a literal "<target titles> Search — <a real full name>", written into the <h1> and BOTH
# <title> tags. Three copies of one string, so it was already a value that could disagree with
# itself, and it named one person in a file every installation ships.
#
# ⚠️ PRESERVE, THEN TRANSFORM. The template defaults to a neutral form, and `migrate.py` writes
# the existing phrasing into the profile's own config on upgrade, so nobody's dashboard title
# silently changes under them. A migration that merely REPORTS the change would move the work
# to the owner permanently, which is not shipping.
def _dashboard_title():
    """`config.dashboard.title_template` × `user.json`'s name. Never a hard-coded name."""
    import json as _json
    name, template = "", "{name} — Job Search"
    try:
        with open(os.path.join(_profile_root(), "user.json"), encoding="utf-8") as fh:
            name = ((_json.load(fh).get("identity") or {}).get("full_name") or "").strip()
    except Exception:
        pass
    try:
        with open(os.path.join(_profile_root(), "config.json"), encoding="utf-8") as fh:
            template = ((_json.load(fh).get("dashboard") or {}).get("title_template")
                        or template)
    except Exception:
        pass
    title = template.replace("{name}", name).strip()
    # A profile with no name must not yield a title starting with a stray dash.
    return title.strip("— ").strip() or "Job Search"

def read(name: str) -> str:
    p = ROOT / name
    return p.read_text(encoding="utf-8") if p.exists() else ""


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def md_inline(s: str) -> str:
    """Escape, then apply **bold**, `code`, and [text](target) links.

    Link support added 2026-07-20 — focus entries routinely reference local files
    like [call_preps/call_prep_2026-08-05.md](call_preps/call_prep_2026-08-05.md)
    and those were rendering as literal markdown on the dashboard. (The original
    example, call_prep_acme.md, was promoted to kb/acme.md on 2026-08-03.)"""
    s = esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)

    def _link(m):
        text, target = m.group(1), m.group(2)
        if target.startswith(("http://", "https://")):
            return ('<a href="%s" target="_blank" rel="noopener noreferrer">%s</a>'
                    % (target, text))
        # Local repo file (call_prep_*.md, log.md, ...): no useful href from a
        # published artifact, so render as a filename chip rather than a dead link.
        return '<code class="fileref">%s</code>' % text
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", _link, s)
    return s


def section_text(md: str, header: str) -> str:
    """Return the text of a '## header' section (until next ## or EOF)."""
    m = re.search(rf"^##\s+{re.escape(header)}.*?$(.*?)(?=^##\s|\Z)",
                  md, re.M | re.S)
    return m.group(1) if m else ""


def parse_table(text: str):
    """Parse the first markdown pipe table in text -> (headers, rows)."""
    lines = [l for l in text.splitlines() if l.strip().startswith("|")]
    if len(lines) < 2:
        return [], []
    def cells(line):
        return [c.strip() for c in line.strip().strip("|").split("|")]
    headers = cells(lines[0])
    rows = [cells(l) for l in lines[2:]]  # skip separator row
    return headers, [r for r in rows if any(c and not c.startswith("_(") for c in r)]


def is_closed_status(status: str) -> bool:
    s = status.lower()
    return any(k in s for k in ("closed", "removed", "ruled out", "not pursued", "dropped",
                                 "declined", "no contact", "passed", "filled", "excluded"))


def status_chip(status: str) -> str:
    s = status.lower()
    if is_closed_status(status):
        cls = "closed"
    elif any(k in s for k in ("your move", "%s replies" % OWNER_TOKEN, "%s sends" % OWNER_TOKEN,
                               "%s:" % OWNER_TOKEN, "next:", "reply owed", "book ")):
        cls = "action"
    elif any(k in s for k in ("scheduled", "booked", "call held", "held")):
        cls = "scheduled"
    else:
        cls = "waiting"
    return f'<span class="chip {cls}">{md_inline(status)}</span>'


def first_url(text: str) -> str:
    """Pull the first http(s) URL out of a free-text cell (e.g. a Notes column's
    'Posting: https://...' convention), trimming trailing prose punctuation."""
    m = re.search(r"https?://\S+", text)
    if not m:
        return ""
    return m.group(0).rstrip(").,;”’'\"")


def comp_dot(comp: str) -> str:
    """A small colored dot signaling whether comp text reads as clearing or missing the floor."""
    c = comp.lower()
    if any(k in c for k in ("clears", "clear the", "comfortably clear", "exceeds")):
        return '<span class="dot dot-clear" title="Clears comp floor"></span>'
    if any(k in c for k in ("below", "not disclosed", "unconfirmed", "unverified")):
        return '<span class="dot dot-below" title="Below floor / unconfirmed"></span>'
    return '<span class="dot dot-unknown" title="Comp unclear"></span>'


def render_table(headers, rows, status_cols=(), comp_col=None, link_col=None) -> str:
    if not rows:
        return '<div class="sub">Nothing here right now.</div>'
    out = ["<table><tr>"]
    out += [f"<th>{esc(h)}</th>" for h in headers]
    out.append("</tr>")
    for r in rows:
        out.append("<tr>")
        for i, c in enumerate(r[:len(headers)]):
            if i == link_col:
                cell = f'<a href="{esc(c)}" target="_blank" rel="noopener">JD ↗</a>' if c else '<span class="sub">—</span>'
            elif i in status_cols:
                cell = status_chip(c)
            elif i == comp_col:
                cell = comp_dot(c) + md_inline(c)
            else:
                cell = md_inline(c)
            out.append(f"<td>{cell}</td>")
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


YOUR_MOVE_RE = r"^##\s*(?:⚡\s*)?Your Move.*?$(.*?)(?=^##\s|\Z)"


def parse_your_move(md: str):
    """Extract the '## Your Move' section's numbered items as (title, ask) tuples.
    This section is what's actually waiting on the candidate — it renders as its own panel at
    the top of the dashboard so priority decisions aren't buried in the focus prose."""
    m = re.search(YOUR_MOVE_RE, md, re.M | re.S)
    if not m:
        return []
    items = []
    for line in m.group(1).splitlines():
        im = re.match(r"^\d+\.\s+\*\*(.+?)\*\*\s*[—-]?\s*(.*)$", line)
        if im:
            title, ask = im.group(1), im.group(2)
            # Optional trailing tag {opp:<id>} links a role decision to its JSONL
            # record so the generator can surface that role's JD link WITHOUT the URL
            # being duplicated into focus.md (single source of truth = the JSONL).
            opp_id = None
            tagm = re.search(r"\s*\{opp:\s*([a-z0-9-]+)\s*\}\s*$", ask)
            if tagm:
                opp_id = tagm.group(1)
                ask = ask[:tagm.start()].rstrip()
            items.append((title, ask, opp_id))
    return items


def strip_your_move(md: str) -> str:
    """Remove the Your Move section so it doesn't render twice (panel + focus list)."""
    return re.sub(YOUR_MOVE_RE, "", md, flags=re.M | re.S)



def render_fit(opps, companies) -> str:
    """The JD fit register — why the candidate is a fit, and what he must NOT claim.

    ⭐ ADDED 2026-08-03. The fit{} block had existed since 2026-08-02 and the dashboard rendered
    it NOWHERE, so 28 analyses were invisible on the surface the candidate actually reads. That is the
    same failure he caught the same morning about drafts: work that is written but not published
    is work he cannot use. The DO-NOT-CLAIM half matters most — a not-aligned requirement is the
    thing that keeps a letter or an interview answer honest.
    """
    rows = [o for o in opps if o.get("fit")]
    if not rows:
        return '<div class="sub">No fit analysis recorded yet.</div>'
    def rank(o):
        r = o["fit"].get("requirements") or []
        return -sum(1 for x in r if x.get("verdict") == "aligned")
    out = []
    for o in sorted(rows, key=rank):
        f = o["fit"]; reqs = f.get("requirements") or []
        cnt = {}
        for r in reqs:
            cnt[r.get("verdict")] = cnt.get(r.get("verdict"), 0) + 1
        cname = companies.get(o.get("company_id"), {}).get("name", o.get("company_id") or "?")
        chips = " ".join(
            '<span class="pill">%s %d</span>' % (v, cnt[v])
            for v in ("aligned", "partial", "unknown", "not-aligned") if cnt.get(v))
        pitches = [r for r in reqs if r.get("verdict") == "aligned" and r.get("pitch_line")][:3]
        nots = [r for r in reqs if r.get("verdict") == "not-aligned"]
        qs = [r for r in reqs if r.get("question_for_candidate") and r.get("question_status") == "open"]
        # ⭐ DATED QUESTIONS FIRST, AND SHOW THE DATE. `act_by` exists because the fact that made
        # a question urgent used to live as PROSE inside the question text, where nothing could
        # sort it (the <a recruiter> miss, 2026-08-03). That was fixed in coordinator.py — but NOT here,
        # so the dashboard, which is the surface the candidate actually reads, still rendered every
        # question as an undifferentiated bullet with no deadline on it. A deadline he cannot see
        # is a deadline that only exists in a terminal he may not be looking at.
        qs.sort(key=lambda r: (not r.get("act_by"), r.get("act_by") or ""))
        body = []
        if pitches:
            body.append('<div class="sub" style="margin:6px 0 2px"><strong>Lead with</strong></div><ul style="margin:0 0 6px 18px">'
                        + "".join("<li>%s</li>" % esc(r["pitch_line"]) for r in pitches) + "</ul>")
        if nots:
            body.append('<div class="sub" style="margin:6px 0 2px"><strong>⛔ Do NOT claim</strong></div><ul style="margin:0 0 6px 18px">'
                        + "".join("<li>%s</li>" % esc(r["requirement"]) for r in nots) + "</ul>")
        if qs:
            _iso = datetime.date.today().isoformat()

            def _q(r):
                ab = r.get("act_by")
                if not ab:
                    return "<li>%s</li>" % esc(r["question_for_candidate"])
                due = ab <= _iso
                return ('<li><strong>%s %s</strong> &middot; %s</li>'
                        % ("‼️ DUE" if due else "⏳ by", esc(ab),
                           esc(r["question_for_candidate"])))
            body.append('<div class="sub" style="margin:6px 0 2px"><strong>❓ Needs you</strong></div><ul style="margin:0 0 6px 18px">'
                        + "".join(_q(r) for r in qs) + "</ul>")
        out.append('<div class="card" style="margin-bottom:10px"><div class="ym-head">%s — %s</div>'
                   '<div class="sub" style="margin:2px 0 6px">%s &middot; <em>%s</em></div>%s</div>'
                   % (esc(cname), esc(o.get("title") or ""), chips,
                      esc((f.get("jd_source") or "")[:150]), "".join(body)))
    return "".join(out)


# ─────────────────────────────────────────────────────────────────────────────
# THE OPPORTUNITY ROW — one role, one place.
#
# ⭐⭐ WHY THIS REPLACED FIVE SECTIONS (2026-08-10).
#
# The Opportunities tab used to render the same roles five times: a JD-fit register, three
# application buckets (submitted / in play through a person / nothing sent), a focus-areas list
# and a sourced-pipeline table. Measured against the live pipeline: **every one of the 46 live
# roles appeared in at least two of those sections, 16 appeared in four, and not one appeared in
# exactly one.** Answering "where does this role stand" meant reading four tables and joining
# them by company name in your head.
#
# The cause was not too much content. It was that the tab was organised by ATTRIBUTE — fit,
# application state, focus, source — while the reader's unit of thought is the ROLE.
#
# ⭐ THE FIX IS STRUCTURAL, NOT COSMETIC. Bucket membership becomes a POSITION rather than a
# table you are listed in. `stage` already exists on every record, so the rail below shows where
# a role actually is; the three application buckets become a filter over one list instead of
# three copies of it. A status CHIP would not have done this — a chip shows state but not
# progression, and it would have left all four tables standing.
#
# Everything the old sections showed is still here. Each fact now appears once.
# ─────────────────────────────────────────────────────────────────────────────

STAGES = ("sourced", "contacted", "screening", "interviewing")
_STAGE_LABEL = {"sourced": "Sourced", "contacted": "Contacted",
                "screening": "Screening", "interviewing": "Interviewing"}



def best_link(o):
    """The role's posting URL — `jd_url`, else the first sighting that carried one.

    ⭐ ONE RULE, TWO SURFACES. Your Move and the Opportunities row both need this, and a second
    copy would drift. On the live pipeline the fallback is not decorative: it is the difference
    between 33 and 34 of 38 live roles having a reachable posting.
    """
    if o.get("jd_url"):
        return o["jd_url"]
    for s in (o.get("sightings") or []):
        if (s or {}).get("source_url"):
            return s["source_url"]
    return None


def opp_bucket(o):
    """The one bucket this role belongs to — the same three the old tables split across.

    Order matters and is the old precedence: an application beats a person, a person beats
    nothing. Kept identical so the filter counts match what the buckets used to report.
    """
    if o.get("applications"):
        return "applied"
    if o.get("outreach") or o.get("contacts"):
        return "person"
    return "nothing"


def stage_rail(o):
    """Four segments, one per real pipeline stage, with the CURRENT stage the only saturated one.

    ⚠️ An unrecognised stage renders every segment as pending rather than guessing a position.
    Inventing a position would put a role further along than it is, which is the one error that
    makes this rail worse than the tables it replaced.
    """
    cur = str(o.get("stage") or "").lower()
    idx = STAGES.index(cur) if cur in STAGES else -1
    segs = []
    for i, st in enumerate(STAGES):
        cls = "seg done" if (idx >= 0 and i < idx) else ("seg now" if i == idx else "seg todo")
        segs.append(f'<span class="{cls}" title="{esc(_STAGE_LABEL[st])}"></span>')
    label = _STAGE_LABEL.get(cur, "stage not set")
    return (f'<span class="rail" role="img" aria-label="Stage: {esc(label)}">'
            + "".join(segs) + f'</span><span class="rail-label">{esc(label)}</span>')


def _fit_detail(o):
    f = o.get("fit") or {}
    reqs = f.get("requirements") or []
    if not reqs and not f.get("summary"):
        return ""
    aligned = [r for r in reqs if r.get("verdict") == "aligned"]
    other = [r for r in reqs if r.get("verdict") != "aligned"]
    out = ['<div class="od-h">JD fit</div>']
    if f.get("summary"):
        out.append(f'<div class="od-p">{md_inline(str(f["summary"]))}</div>')
    if aligned:
        out.append('<div class="od-p"><strong>Evidence exists for:</strong> '
                   + esc(", ".join(str(r.get("requirement", "")) for r in aligned)) + "</div>")
    if other:
        # ⭐ The DO-NOT-CLAIM half is the half that keeps a letter honest, so it is never
        # collapsed away behind the aligned list.
        out.append('<div class="od-p od-warn"><strong>Do not claim:</strong> '
                   + esc(", ".join(str(r.get("requirement", "")) for r in other)) + "</div>")
    return "".join(out)


def _touch_detail(o):
    out = []
    apps = o.get("applications") or []
    if apps:
        rows = []
        for a in apps:
            when = esc(str(a.get("applied_on") or a.get("date") or "date unrecorded"))
            how = esc(str(a.get("method") or "application"))
            cl = a.get("cover_letter")
            cl = "cover letter recorded" if cl else "cover letter unrecorded"
            rows.append(f"<li>{when} — {how} · {cl}</li>")
        out.append('<div class="od-h">Applications</div><ul class="od-l">'
                   + "".join(rows) + "</ul>")
    tou = o.get("outreach") or []
    if tou:
        rows = []
        for t in tou:
            when = esc(str(t.get("sent_on") or t.get("date") or "date unrecorded"))
            who = esc(str(t.get("to") or t.get("contact_id") or "recipient unrecorded"))
            med = esc(str(t.get("medium") or ""))
            res = t.get("outcome") or "no reply yet"
            rows.append(f"<li>{when} — {who}{' · ' + med if med else ''} · {esc(str(res))}</li>")
        out.append('<div class="od-h">Outreach</div><ul class="od-l">'
                   + "".join(rows) + "</ul>")
    return "".join(out)


def render_opportunity_list(opps, companies):
    """One row per LIVE role. Closed roles are not here — they are not opportunities."""
    live = [o for o in opps if o.get("status") not in _CLOSED_STATUSES]
    if not live:
        return '<div class="sub">No live opportunities.</div>', {}
    order = {"active-pursuit": 0, "needs-resolution": 1, "in-motion": 2, "backlog": 3}

    def key(o):
        # Anything waiting on the candidate sorts first: the tab's job is to be actionable.
        waiting = 0 if str(o.get("next_action_owner") or "").lower() not in ("me", "") else 1
        return (waiting, order.get(o.get("status"), 9),
                -(STAGES.index(o["stage"]) if o.get("stage") in STAGES else -1))

    counts = {"all": 0, "you": 0, "applied": 0, "person": 0, "nothing": 0}
    rows = []
    for o in sorted(live, key=key):
        comp = companies.get(o.get("company_id"), {})
        bucket = opp_bucket(o)
        owner = str(o.get("next_action_owner") or "").lower()
        waits = owner not in ("me", "")
        counts["all"] += 1
        counts[bucket] += 1
        if waits:
            counts["you"] += 1

        meta = " · ".join(x for x in (
            _fmt_loc(o.get("location")), _fmt_comp(o.get("comp")),
            esc(str(o.get("channel_id") or "").replace("firm:", "via ")) or "") if x)
        na = o.get("next_action")
        action_full = ""
        if na:
            due = o.get("next_action_date")
            who = "you" if waits else "the run"
            text = str(na).strip()
            # Flatten markdown before clamping so the teaser never shows raw syntax.
            flat = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
            flat = re.sub(r"[*`]+", "", flat)
            if len(flat) > OPP_ACTION_CLAMP:
                teaser = flat[:OPP_ACTION_CLAMP].rsplit(" ", 1)[0].rstrip(" ,;:—-") + "…"
                action_full = text
            else:
                teaser = flat
            nxt = (f'<div class="opp-next{" opp-next-you" if waits else ""}">'
                   f'<span class="opp-arrow">→</span> {esc(teaser)} '
                   f'<span class="opp-owner">{who}{" · due " + esc(str(due)) if due else ""}</span></div>')
        else:
            # ⚠️ Named, not omitted. A role with no next action is a decision nobody has made,
            # and an empty space reads as "handled".
            nxt = '<div class="opp-next opp-next-none">No next action set</div>'

        # ⭐⭐ THE POSTING LINK IS A ROW-LEVEL AFFORDANCE, NEVER BEHIND `Detail`.
        # The reported frustration was literally "I just wanted the JD link" — and the first
        # version of this layout still cost a click to reach it. Collapsing the duplication is
        # not the same as making the most-wanted thing reachable. It sits next to the title,
        # always in the same place, so it can be hit without reading the row.
        link = best_link(o)
        if link:
            jd = (f'<a class="opp-jd" href="{esc(link)}" target="_blank" rel="noopener" '
                  f'title="Open the posting">JD ↗</a>')
        else:
            # Named, not blank — "no link recorded" is a fact about the role, and an empty
            # space here reads as "look harder".
            jd = '<span class="opp-jd opp-jd-none" title="No posting URL recorded">no link</span>'

        detail = ""
        if action_full:
            # The full recommendation, first — it is the reason the reader opened the row.
            detail += ('<div class="od-h">The call in full</div>'
                       f'<div class="od-p">{md_inline(action_full)}</div>')
        detail += _fit_detail(o) + _touch_detail(o)
        body = (f'<details class="opp-more"><summary>Detail</summary>'
                f'<div class="opp-detail">{detail}</div></details>') if detail else ""

        rows.append(
            f'<div class="opp" data-bucket="{bucket}" data-you="{"1" if waits else "0"}">'
            f'  <div class="opp-head">'
            f'    <div class="opp-title">{esc(str(o.get("title") or "Untitled role"))}'
            f'      <span class="opp-co">{esc(comp.get("name", o.get("company_id", "")))}</span>'
            f'      {jd}</div>'
            f'    <div class="opp-rail">{stage_rail(o)}</div>'
            f'  </div>'
            f'  <div class="opp-meta">{meta}</div>'
            f'  {nxt}{body}'
            f'</div>')
    return "".join(rows), counts

def render_your_move(items, links=None) -> str:
    if not items:
        return '<div class="sub">Nothing is waiting on you right now.</div>'
    links = links or {}
    parts = []
    for n, item in enumerate(items, 1):
        t, w = item[0], item[1]
        opp_id = item[2] if len(item) > 2 else None
        link = links.get(opp_id) if opp_id else None
        jd_html = (f' <a class="ym-jd" href="{link}" target="_blank" rel="noopener">JD ↗</a>'
                   if link else '')
        parts.append(
            f'<div class="ym-item"><div class="ym-num">{n}</div><div>'
            f'<div class="ym-title">{md_inline(t)}{jd_html}</div>'
            f'<div class="ym-ask">{md_inline(w)}</div></div></div>')
    return "".join(parts)


def parse_focus(md: str):
    """Return a list of ('h', heading_text) and ('i', title, why) tuples, in document order."""
    entries = []
    for line in md.splitlines():
        hm = re.match(r"^##\s+(.+?)\s*$", line)
        if hm:
            entries.append(("h", hm.group(1)))
            continue
        im = re.match(r"^\d+\.\s+\*\*(.+?)\*\*\s*[—-]?\s*(.*)$", line)
        if im:
            entries.append(("i", im.group(1), im.group(2)))
    return entries


def render_focus(entries, show_headers: bool = True) -> str:
    """Render parsed focus entries to HTML. Buffers '## ' headers and only emits one
    once a real numbered item follows it, so a header with nothing under it (e.g.
    Backlog/Passed, deliberately left as non-numbered prose) doesn't render as an
    empty section."""
    parts = []
    i = 0
    pending_header = None
    for entry in entries:
        if entry[0] == "h":
            # Process groups pass show_headers=False: the panel already labels the
            # group ("Needs your input"), so repeating the markdown header inside
            # the card is pure duplication.
            pending_header = entry[1] if show_headers else None
        else:
            if pending_header is not None:
                parts.append(f'<div class="focus-section">{md_inline(pending_header)}</div>')
                pending_header = None
            i += 1
            _, t, w = entry
            # Long entries get collapsed behind a one-line teaser so the list stays
            # scannable — the detail is still there, one click away.
            if len(w) > FOCUS_CLAMP:
                # Flatten markdown before clamping: strip **bold**, and reduce
                # [text](target) to just text so the teaser doesn't show raw
                # link syntax (fixed 2026-07-20).
                flat = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", w)
                flat = re.sub(r"[*`]+", "", flat)
                teaser = flat[:FOCUS_CLAMP].rsplit(" ", 1)[0].rstrip(" ,;—-")
                why = (f'<details class="focus-more"><summary>{esc(teaser)}…</summary>'
                       f'<div class="focus-why focus-full">{md_inline(w)}</div></details>')
            else:
                why = f'<div class="focus-why">{md_inline(w)}</div>'
            parts.append(
                f'<div class="focus-item"><div class="focus-num">{i}</div><div>'
                f'<div class="focus-title">{md_inline(t)}</div>{why}</div></div>')
    return "".join(parts)


def parse_cover_letters(md: str):
    """Entries from cover_letters.md, minus the trailing '⚠️ Questions...' section.

    Same '## ' shape as drafts.md, so parse_drafts does the splitting; this only
    filters out the housekeeping section at the bottom, which is a note to me
    rather than a letter the candidate would review.
    """
    entries = [(ttl, blocks) for ttl, blocks in parse_drafts(md)
               if "questions that would sharpen" not in ttl.lower()]
    # A letter whose body isn't blockquoted parses to an EMPTY body and publishes
    # silently as a heading with no text -- which is exactly how the PCG letter
    # reached the dashboard invisible on 2026-07-27 (the candidate caught it, not the run).
    # The body MUST be '> '-prefixed; warn loudly rather than shipping a blank.
    for ttl, blocks in entries:
        if not any(k == "quote" and any(p.strip() for p in v) for k, v in blocks):
            print("  !! WARNING: cover letter '%s' has NO quoted body -- "
                  "the letter text must be blockquoted with '> ' or it renders EMPTY."
                  % ttl[:70])
    return entries


def parse_drafts(md: str):
    """Split drafts.md on '## ' entries -> list of (title, blocks).

    ⭐⭐ REWRITTEN 2026-08-03. The candidate: *"can you fix the formatting for the html output for the
    drafts in the Your Move section? when i look at the drafts.md, they are so much easier to
    read versus the html page."*

    **What was wrong, and it was worse than cosmetic.** The old parser kept only two things: the
    lines starting with `**`, and EVERY `>` line in the entry CONCATENATED INTO ONE BLOB. For a
    single-message draft that was survivable. For a multi-recipient campaign it destroyed the
    document: the <an employer> entry has two recipients, each with a connection note AND a follow-up
    message, and all four bodies merged into one undifferentiated wall with no way to tell which
    text goes to whom or which piece is which. `### Recipient 1 of 2` and `#### A. / B.` headings
    were dropped outright, so the only signposts vanished too.

    **And free prose was silently discarded** — drafts.md's own header warns about this
    ("Free-form prose paragraphs without a `**Label:**` prefix are silently dropped... verified
    this the hard way 2026-07-10"). A parser that eats content and warns you in prose is a trap.
    This one renders everything, so the trap is gone rather than documented.

    Returns ordered, TYPED blocks so the HTML can mirror the markdown:
      ('meta', [lines])  '**Label:** value' runs
      ('h3', text)       '### ' — a recipient
      ('h4', text)       '#### ' — a piece (A. the note, B. the message)
      ('quote', [paras]) a '> ' run — THE TEXT THE CANDIDATE ACTUALLY SENDS
      ('note', [lines])  anything else, previously dropped
      ('rule', None)     '---'
    """
    entries = []
    for m in re.finditer(r"^##\s+(.+?)$(.*?)(?=^##\s|\Z)", md, re.M | re.S):
        title, body = m.group(1).strip(), m.group(2)
        blocks, buf, kind = [], [], None

        def flush():
            if not buf:
                return
            if kind == "quote":
                # A bare '>' is a paragraph break inside the message, not a blank line to drop.
                paras, cur = [], []
                for ln in buf:
                    if ln.strip():
                        cur.append(ln)
                    elif cur:
                        paras.append("\n".join(cur)); cur = []
                if cur:
                    paras.append("\n".join(cur))
                blocks.append(("quote", paras))
            else:
                blocks.append((kind, list(buf)))
            buf.clear()

        for raw in body.splitlines():
            s = raw.strip()
            if s.startswith(">"):
                if kind != "quote":
                    flush(); kind = "quote"
                buf.append(raw.lstrip()[1:].lstrip() if raw.lstrip().startswith(">") else raw)
            elif s.startswith("####"):
                flush(); kind = None; blocks.append(("h4", s.lstrip("#").strip()))
            elif s.startswith("###"):
                flush(); kind = None; blocks.append(("h3", s.lstrip("#").strip()))
            elif s in ("---", "***", "___"):
                flush(); kind = None; blocks.append(("rule", None))
            elif s.startswith("**"):
                if kind != "meta":
                    flush(); kind = "meta"
                buf.append(s)
            elif not s:
                flush(); kind = None
            else:
                if kind != "note":
                    flush(); kind = "note"
                buf.append(s)
        flush()
        entries.append((title, blocks))

    # ⭐ AN UNTITLED ENTRY IS AN INVISIBLE ENTRY — added 2026-08-05, and it had already shipped.
    # The candidate: *"I still don't see the message for <a contact>."* The draft was in drafts.md, was
    # correctly `> `-blockquoted, and its text WAS in the published HTML — so every existing guard
    # passed. But it had been written with a `**Label:**` line where every other entry uses a
    # `## ` heading, and entries are split on `## ` alone. So it never began a card: it was
    # absorbed into the TAIL of the previous, unrelated draft (MedImpact/Marjan), under that
    # draft's title. Nothing was missing; it was filed under someone else's name.
    #
    # Why the existing checks could not catch it: the no-quoted-body guard asks whether the text
    # EXISTS, and it did. This asks the different question — whether the text is FINDABLE. A
    # `---` rule followed by a `**Label:**` line is precisely the shape of an entry that forgot
    # its heading, and it does not occur in a well-formed one.
    for m in re.finditer(r"^##\s+(.+?)$(.*?)(?=^##\s|\Z)", md, re.M | re.S):
        _t, _b = m.group(1).strip(), m.group(2)
        if re.search(r"^---\s*$\s*^\*\*Label:\*\*", _b, re.M):
            print("  !! WARNING: draft '%s' contains a '---' followed by a '**Label:**' line. "
                  "That is an entry that forgot its '## ' heading, so it renders INSIDE this "
                  "one instead of as its own card -- findable only by whoever already knew it "
                  "was there. Give it a '## ' title." % _t[:70])

    # Same silent-empty guard as before: a draft body must be '> '-quoted or it renders BLANK,
    # which is indistinguishable from "not drafted yet". That shipped once and only the candidate noticed.
    for ttl, blocks in entries:
        if "questions that would sharpen" in ttl.lower():
            continue
        if not any(k == "quote" and any(p.strip() for p in v) for k, v in blocks):
            print("  !! WARNING: draft '%s' has NO quoted body -- "
                  "the message text must be blockquoted with '> ' or it renders EMPTY."
                  % ttl[:70])
    return entries


def render_draft_entries(entries, empty_msg):
    """Render typed blocks so the page reads like the markdown does."""
    if not entries:
        return '<div class="sub">%s</div>' % empty_msg
    out = []
    for title, blocks in entries:
        parts = ['<div class="draft"><div class="draft-title">%s</div>' % md_inline(title)]
        for kind, val in blocks:
            if kind == "meta":
                parts.append('<div class="draft-meta">%s</div>'
                             % "".join("<div>%s</div>" % md_inline(l) for l in val))
            elif kind == "h3":
                parts.append('<div class="draft-h3">%s</div>' % md_inline(val))
            elif kind == "h4":
                parts.append('<div class="draft-h4">%s</div>' % md_inline(val))
            elif kind == "rule":
                parts.append('<hr class="draft-rule">')
            elif kind == "note":
                parts.append('<div class="draft-note">%s</div>'
                             % "<br>".join(md_inline(l) for l in val))
            elif kind == "quote":
                # THE SENDABLE TEXT. Its own card, so it is obvious what to copy.
                paras = "".join(
                    "<p>%s</p>" % "<br>".join(md_inline(x) for x in p.split("\n"))
                    for p in val if p.strip())
                parts.append('<div class="draft-quote">%s</div>' % paras)
        parts.append("</div>")
        out.append("".join(parts))
    return "".join(out)


def load_jsonl(name):
    import json as _json
    path = ROOT / "data" / name
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(_json.loads(line))
    return out


def _fmt_comp(c):
    if not c:
        return "Not disclosed"
    def k(v): return ("%s%dK" % (CURRENCY, round(v/1000))) if v else "?"
    lo, hi = c.get("min"), c.get("max")
    rng = k(lo) if lo == hi else "%s–%s" % (k(lo), k(hi))
    return "%s %s" % (rng, c.get("basis", ""))


def _fmt_loc(loc):
    if not isinstance(loc, dict):
        return ""
    t = loc.get("type", "")
    p = loc.get("primary", "")
    return ("%s — %s" % (t, p)) if p and t not in p.lower() else (p or t)


# Disposition -> the Contact-Status-style label the renderer chips on, and the live/closed split.
# `expired` (issue #6) is terminal like `passed` but is LABELLED apart from it — the whole point
# of the state is that "the posting vanished" and "I declined" stop looking like one thing.
_STATUS_LABEL = {
    "active-pursuit": "Active pursuit", "needs-resolution": "Needs resolution",
    "in-motion": "In motion", "backlog": "Parked", "passed": "Passed / closed",
    "expired": "Expired",
}
_CLOSED_STATUSES = {"passed", "backlog", "expired"}


def opps_from_jsonl():
    """Build the sourced-pipeline table from data/*.jsonl (the 2026-07-20 cutover).
    Returns (headers, live_rows, closed_rows, status_col, comp_col, jd_col) mirroring
    what the old markdown-parse path produced, so render_table is unchanged."""
    companies = {c["id"]: c for c in load_jsonl("companies.jsonl")}
    opps = load_jsonl("opportunities.jsonl")
    headers = ["Company", "Title", "Comp", "Location", "Status", "JD"]
    live, closed = [], []
    # active first, then by company; closed bucket sorted by company
    order = {"active-pursuit": 0, "needs-resolution": 1, "in-motion": 2, "backlog": 3,
             "passed": 4, "expired": 5}
    for o in sorted(opps, key=lambda o: (order.get(o.get("status"), 9), o.get("company_id", ""))):
        comp = companies.get(o.get("company_id"), {})
        row = [
            comp.get("name", o.get("company_id", "")),
            o.get("title", ""),
            _fmt_comp(o.get("comp")),
            _fmt_loc(o.get("location")),
            _STATUS_LABEL.get(o.get("status"), o.get("status", "")),
            o.get("jd_url") or "",
        ]
        (closed if o.get("status") in _CLOSED_STATUSES else live).append(row)
    return headers, live, closed, 4, 2, 5


def firms_from_channels():
    """Recruiter/referral channels -> the Network tab's firms table (cutover 2026-07-20,
    replacing network.md's firm sections). Columns mirror the old table."""
    chans = [c for c in load_jsonl("channels.jsonl") if c.get("type") in ("recruiter", "referral")]
    headers = ["Firm", "Contact(s)", "Relationship"]
    rows = []
    for c in sorted(chans, key=lambda c: c.get("label", "")):
        contacts = ", ".join(ct.get("name", "") for ct in c.get("contacts", [])) or "—"
        rows.append([c.get("label", ""), contacts, c.get("relationship_status") or ""])
    return headers, rows


SUBMITTED_STATES = ("submitted", "acknowledged", "interviewing", "offer")


def application_tables(today=None):
    """Answer 'what have I applied to, and what haven't I?' — added 2026-07-22 at the candidate's ask.

    Splits into THREE buckets, because "no application" is not one thing:
      1. Submitted        — an application actually went in.
      2. Human path       — in play through a recruiter or a live conversation, where
                            applying would be redundant or wrong (<an employer> via Ashford Search,
                            <an employer> mid-process). NOT a gap.
      3. Nothing sent     — being carried as a pursuit with no application AND no
                            outreach. THIS is the real gap and the only bucket that
                            should ever feel uncomfortable.
    """
    import datetime as _dt
    today = today or _dt.date.today()
    opps = load_jsonl("opportunities.jsonl")
    companies = {c["id"]: c for c in load_jsonl("companies.jsonl")}

    def cname(o):
        return companies.get(o.get("company_id"), {}).get("name") or o.get("company_id", "")

    def age(d):
        try:
            return (today - _dt.date(*map(int, d.split("-")))).days
        except Exception:
            return None

    submitted, human, nothing = [], [], []
    for o in opps:
        # Terminal roles have no application gap to report — an expired posting can no longer
        # be applied to, so listing it under "nothing sent" would nag about the impossible.
        if o.get("status") in ("passed", "expired"):
            continue
        apps = [a for a in (o.get("applications") or []) if a.get("status") in SUBMITTED_STATES]
        if apps:
            a = sorted(apps, key=lambda x: x.get("date") or "")[-1]
            days = age(a.get("date") or "")
            cl = a.get("cover_letter_attached")
            cl_txt = "yes" if cl is True else ("no" if cl is False else "unrecorded")
            submitted.append([cname(o), o.get("title", ""), a.get("date") or "—",
                              f"{days}d" if days is not None else "—",
                              a.get("status", ""), cl_txt,
                              (a.get("method") or "").replace("-", " ")])
            continue
        contacts = len(o.get("outreach") or [])
        # `in-motion` is defined in CLAUDE.md as a recruiter/network thread — the
        # recruiter approached the candidate, so there is no outreach[] row from his side and
        # its absence is NOT evidence that nothing is happening.
        if contacts or o.get("status") == "in-motion" or o.get("stage") in ("screening", "interviewing", "offer"):
            who = ", ".join(x.get("to", "") for x in (o.get("outreach") or []) if x.get("to")) or "in process"
            nxt = (o.get("next_action") or "").strip()
            human.append([cname(o), o.get("title", ""), o.get("stage", ""), who, nxt[:150]])
        else:
            nxt = (o.get("next_action") or "").strip()
            nothing.append([cname(o), o.get("title", ""), o.get("status", ""), nxt[:180] or "— no next action recorded —"])

    submitted.sort(key=lambda r: r[2], reverse=True)
    return submitted, human, nothing


def opp_focus_from_jsonl():
    """Generate the Opportunities-tab focus groups (Active Pursuit / Needs Resolution /
    In Motion) FROM data/opportunities.jsonl, so they can never drift from the data
    (cutover 2026-07-20 — focus.md's hand-maintained role sections were going stale:
    passed roles lingering, decided roles still showing as 'needs a decision')."""
    companies = {c["id"]: c for c in load_jsonl("companies.jsonl")}
    opps = load_jsonl("opportunities.jsonl")
    groups = [
        ("🎯 Active pursuits — what's next on each", "active-pursuit"),
        ("❓ Needs resolution — a pursue/pass call", "needs-resolution"),
        ("🔄 In motion — threads to watch", "in-motion"),
    ]
    parts = []
    for label, status in groups:
        rows = [o for o in opps if o.get("status") == status]
        if not rows:
            continue
        parts.append('<div class="focus-section">%s (%d)</div>' % (label, len(rows)))
        for o in sorted(rows, key=lambda o: o.get("company_id", "")):
            comp = companies.get(o.get("company_id"), {})
            title = "%s — %s" % (comp.get("name", o.get("company_id", "")), o.get("title", ""))
            bits = []
            c = o.get("comp")
            if c:
                bits.append(_fmt_comp(c))
            loc = _fmt_loc(o.get("location"))
            if loc:
                bits.append(loc)
            na = o.get("next_action")
            if na:
                who = "you" if o.get("next_action_owner") == OWNER_TOKEN else "me"
                bits.append("<strong>Next (%s):</strong> %s" % (who, na))
            jd = o.get("jd_url")
            if jd:
                bits.append('<a href="%s" target="_blank" rel="noopener noreferrer">JD ↗</a>' % jd)
            why = " · ".join(bits)
            parts.append(
                '<div class="focus-item"><div class="focus-num">•</div><div>'
                '<div class="focus-title">%s</div><div class="focus-why">%s</div></div></div>'
                % (md_inline(title), why))
    return "".join(parts)


def your_move_roles_from_jsonl():
    """Role decisions on Your Move are a FILTERED VIEW of data/opportunities.jsonl —
    not hand-copied prose (added 2026-07-29, per the candidate: 'the your move page should be a
    targeted view of the data on opportunities'). Any opportunity flagged with this
    candidate's own next_action_owner token (see profile.owner_token()) while still live
    surfaces here automatically, with its
    comp / location / lean / JD link sourced straight from the record. To move a role on
    or off Your Move, change its next_action_owner in the JSONL — never edit focus.md.
    Returns (title, ask, opp_id) tuples matching render_your_move's shape (opp_id lets it
    resolve the JD link from the same links map the tagged manual asks use)."""
    companies = {c["id"]: c for c in load_jsonl("companies.jsonl")}
    live = {"active-pursuit", "needs-resolution"}
    items = []
    for o in load_jsonl("opportunities.jsonl"):
        if o.get("next_action_owner") != OWNER_TOKEN or o.get("status") not in live:
            continue
        comp = companies.get(o.get("company_id"), {})
        title = "🎯 %s — %s" % (comp.get("name", o.get("company_id", "")), o.get("title", ""))
        ctx = [b for b in (_fmt_comp(o.get("comp")) if o.get("comp") else "",
                           _fmt_loc(o.get("location"))) if b]
        ask = ((" · ".join(ctx) + ". ") if ctx else "") + (o.get("next_action") or "")
        items.append((title, ask, o.get("id"), o.get("next_action_date") or "9999"))
    # Soonest act-by first, so the most time-sensitive decision leads.
    items.sort(key=lambda t: t[3])
    return [(t, a, oid) for (t, a, oid, _d) in items]


def main():
    # `opps = read("opportunities.md")` lived here until 2026-08-02 and was DEAD — the sourced
    # pipeline has been read from data/opportunities.jsonl since the 2026-07-20 cutover, and the
    # local variable was never used again. It kept a retired 166 KB file looking load-bearing,
    # which is exactly why three agents were still being pointed at it. Removed with the file.
    net = read("network.md")
    focus_raw = read("focus.md")
    drafts = parse_drafts(read("drafts.md"))
    # Cover letters are a distinct artifact from outreach drafts (added 2026-07-21,
    # per the candidate: the "why is this job a great fit" message was missing entirely).
    # Same file shape, so parse_drafts handles it; rendered in its own panel.
    covers = [c for c in parse_cover_letters(read("cover_letters.md"))]

    # Split focus.md into two dashboard sections, per the candidate's request (2026-07-14):
    # opportunity-facing content (Active Pursuit, Needs Resolution, In Motion, Other
    # open items) vs. process/agent-improvement content (the Search Process section).
    # Mixing "should I pursue this job" with "here's a bug I fixed" in one card was
    # exactly the clutter this split is meant to fix.
    # Process sections are now OWNERSHIP-TAGGED (2026-07-20, per the candidate: "what i care
    # about are the items you need my input on"). focus.md carries three of them —
    # "Needs the candidate", "Open (mine to fix)", "Recently resolved" — and the dashboard
    # renders them as three distinct groups instead of one undifferentiated wall.
    # This is the data-model half of the tabs change: a flat append-only list can't
    # be segmented by any view, because the data carries no ownership or status.
    # `## Search Process` is still matched so older/archived content still renders.
    process_groups = []          # list of (label, raw_text)
    opportunity_focus_raw = focus_raw
    for pat, label in ((r"^## ⚙️ Process — ⚡ Needs .*?$", "needs"),
                       (r"^## ⚙️ Process — 🔧 Open.*?$", "open"),
                       (r"^## ⚙️ Process — ✅ Recently resolved.*?$", "done"),
                       (r"^## Search Process.*?$", "legacy")):
        mm = re.search(r"(" + pat + r"(?:.*?))(?=^## |\Z)", opportunity_focus_raw, re.M | re.S)
        if mm:
            process_groups.append((label, mm.group(1)))
            opportunity_focus_raw = (opportunity_focus_raw[:mm.start()]
                                     + opportunity_focus_raw[mm.end():])
    process_raw = "\n".join(t for _, t in process_groups)

    # "This Week" is pulled out the same way (added 2026-07-20, per the candidate: the dashboard
    # was overloading job-search content with process content and near-term commitments
    # had nowhere to live). Its own tab, so imminent calls aren't buried.
    tw_match = re.search(r"(^## 📅 This Week.*?)(?=^## |\Z)", opportunity_focus_raw, re.M | re.S)
    if tw_match:
        thisweek_raw = tw_match.group(1)
        opportunity_focus_raw = (opportunity_focus_raw[:tw_match.start()]
                                 + opportunity_focus_raw[tw_match.end():])
    else:
        thisweek_raw = ""

    your_move = parse_your_move(focus_raw)
    opportunity_focus_raw = strip_your_move(opportunity_focus_raw)
    focus = parse_focus(opportunity_focus_raw)
    process_focus = parse_focus(process_raw)
    process_parsed = [(lab, parse_focus(txt)) for lab, txt in process_groups]
    thisweek_focus = parse_focus(thisweek_raw)

    # SOURCED PIPELINE — now read from data/*.jsonl (cutover 2026-07-20). The old
    # markdown-table parse is retired; opportunities.md's main table is superseded.
    sh2, live_rows, closed_rows, status_idx2, comp_idx2, jd_col_idx = opps_from_jsonl()
    srows2 = live_rows + closed_rows

    # Firms now come from channels.jsonl (recruiter/referral). Inbound recruiter roles
    # are ordinary opportunities in the main table now, so the separate inbound mini-table
    # is retired. Alumni + register-with pills still read network.md (relationship doc).
    fh, frows = firms_from_channels()
    ah, arows = parse_table(section_text(net, "Alumni network reactivation"))
    firms = re.findall(r"^- \[( |x)\]\s+(.+)$", section_text(net, "Retained firms"), re.M)

    def clears_comp(comp_text):
        c = comp_text.lower()
        return any(k in c for k in ("clears", "clear the", "comfortably clear", "exceeds"))

    clearing_count = sum(1 for r in live_rows
                          if comp_idx2 is not None and comp_idx2 < len(r) and clears_comp(r[comp_idx2]))
    stats_html = (
        '<div class="stats-row">'
        f'<div class="stat"><strong>{len(live_rows)}</strong> active</div>'
        f'<div class="stat"><strong>{clearing_count}</strong> clear comp floor</div>'
        f'<div class="stat"><strong>{len(closed_rows)}</strong> passed / closed</div>'
        f'<div class="stat"><strong>{len(srows2)}</strong> total sourced</div>'
        '</div>'
    )

    ym_links = {o["id"]: best_link(o) for o in load_jsonl("opportunities.jsonl")}
    # Your Move = auto-generated role decisions (a filtered view of the opportunities
    # data) FIRST, then the hand-maintained cross-cutting asks from focus.md (ATS-portal
    # check, consulting, cover-letter measurement) that don't map to a single opp.
    role_decisions = your_move_roles_from_jsonl()
    your_move = role_decisions + your_move
    your_move_html = render_your_move(your_move, ym_links)
    thisweek_html = render_focus(thisweek_focus)

    # ⭐ THE PROCESS TAB WAS REMOVED 2026-08-06 — engine work is not a local to-do list.
    #
    # It showed "🔧 Open — mine to fix": engine and tooling items the search had noticed and was
    # carrying in `focus.md`. Those now belong to the plugin that owns the engine, and are filed
    # as GitHub issues via `careers-plugins/scripts/intake.py`. A capability's defects belong on
    # that capability's tracker, not duplicated in every profile that uses it — a local copy is a
    # second place to look and the one that goes stale.
    #
    # Only the "Needs the candidate" group is still rendered, and it was never on that tab anyway: it
    # appears on Your Move as the "System & tooling" group, because it is a DECISION the owner has
    # to make about his own setup — a credential, a cadence — which no issue on the engine repo
    # can resolve for him. That distinction is the whole reason the split survives the removal.
    _pmap = dict(process_parsed)
    _pcount = lambda lab: sum(1 for e in _pmap.get(lab, []) if e[0] == "i")
    needs_html = render_focus(_pmap.get("needs", []), show_headers=False)
    n_needs = _pcount("needs")

    pills = "".join(
        f'<span class="pill{" done" if x == "x" else ""}">{md_inline(name)}</span>'
        for x, name in firms)

    def multiline(s: str) -> str:
        return "<br>".join(md_inline(l) for l in s.splitlines())

    # ⭐ GitHub issue #6 — SPLIT SENDABLE FROM BLOCKED, and count only the sendable as "needs you".
    #
    # Every staged draft used to render under "awaiting your approval to send", including part-B
    # messages that cannot go until the recipient accepts. One observed state showed seven items
    # as needing the candidate, of which ONE was actionable. That inverts the surface: a Your Move line has
    # to be a question or an imperative aimed at him, and a draft he cannot send is neither — so
    # padding the list is how the one list that must be unskippable stops being read.
    #
    # The precondition is now DATA (`**Blocked until:** contact:<id> outcome:a|b`), resolved
    # against the outreach `outcome` that already existed. Falls back to treating everything as
    # sendable if the resolver cannot run: a dashboard that renders is worth more than one that
    # is right about grouping, and the old behaviour is the safe direction to fail in.
    # ⭐ GitHub issue #13 — group by precondition.NOT_SENDABLE, never by a literal comparison.
    # `state != "blocked"` treated `unreadable` (and would have treated `unresolved`, the
    # legacy-prose state) as sendable, so a draft the resolver could NOT vouch for rendered
    # under "awaiting your approval to send". The set of states that must never read as "needs
    # you" is precondition.py's to own; this only consumes it. A draft the resolver did not
    # report at all still defaults to sendable — that is the render-over-grouping fallback above.
    try:
        import precondition as _pre
        _states = {r["title"]: r for r in _pre.report(str(ROOT))}
        _not_sendable = _pre.NOT_SENDABLE
    except Exception:
        _states, _not_sendable = {}, frozenset()
    _sendable = [d for d in drafts
                 if _states.get(d[0], {}).get("state") not in _not_sendable]
    _blocked = [d for d in drafts if _states.get(d[0], {}).get("state") in _not_sendable]
    drafts_html = render_draft_entries(_sendable, "No pending drafts.")
    blocked_html = render_draft_entries(_blocked, "")
    n_blocked = len(_blocked)
    _blocked_why = {t: _states.get(t, {}).get("why", "") for t, _ in _blocked}

    # ⭐ ONE LIST, NOT FIVE. See render_opportunity_list for why.
    _opp_rows = load_jsonl("opportunities.jsonl")
    _opp_comps = {c["id"]: c for c in load_jsonl("companies.jsonl")}
    opp_list_html, opp_counts = render_opportunity_list(_opp_rows, _opp_comps)

    covers_html = render_draft_entries(covers, "No cover letters pending.")

    # %-d is a glibc/BSD strftime extension; on Windows it raises ValueError and kills the
    # dashboard at the last step. Build the day number by hand (same fix as parse_ics.py).
    _t = datetime.date.today()
    today = "%s %d, %d" % (_t.strftime("%B"), _t.day, _t.year)
    css = """
  :root {
    color-scheme: light dark;
    --bg: #f7f7f5; --fg: #1a1a1a; --muted: #777; --muted2: #666; --th: #888;
    --card-bg: #fff; --card-border: #e4e2dd; --divider: #f0efeb;
    --focus-num-bg: #1a1a1a; --focus-num-fg: #fff;
    --chip-waiting-bg: #fef3cd; --chip-waiting-fg: #8a6d00;
    --chip-action-bg: #fde2e2; --chip-action-fg: #a12626;
    --rail-done: #9aa3ad; --rail-todo: #dcdcd8; --rail-now: #2f5fd0;
    --chip-scheduled-bg: #d9f2e3; --chip-scheduled-fg: #1c7c46;
    --chip-closed-bg: #ececec; --chip-closed-fg: #666;
    --pill-bg: #f0efeb; --pill-fg: #1a1a1a; --pill-done-fg: #999;
    --note-bg: #eef4fb; --note-border: #d5e4f5; --note-fg: #2c5580;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #17181a; --fg: #ececeb; --muted: #9a9a97; --muted2: #a8a8a5; --th: #8e8e8b;
      --card-bg: #201f1f; --card-border: #34322f; --divider: #2b2a28;
      --focus-num-bg: #ececeb; --focus-num-fg: #17181a;
      --chip-waiting-bg: #3d3210; --chip-waiting-fg: #f0c750;
      --chip-action-bg: #3d1e1e; --chip-action-fg: #f0908f;
      --rail-done: #5a626b; --rail-todo: #33322f; --rail-now: #7aa2f7;
      --chip-scheduled-bg: #123526; --chip-scheduled-fg: #6fdba4;
      --chip-closed-bg: #2e2d2b; --chip-closed-fg: #9a9a97;
      --pill-bg: #2b2a28; --pill-fg: #ececeb; --pill-done-fg: #767674;
      --note-bg: #172433; --note-border: #253a52; --note-fg: #8fbaea;
    }
  }
  :root[data-theme="dark"] {
    --bg: #17181a; --fg: #ececeb; --muted: #9a9a97; --muted2: #a8a8a5; --th: #8e8e8b;
    --card-bg: #201f1f; --card-border: #34322f; --divider: #2b2a28;
    --focus-num-bg: #ececeb; --focus-num-fg: #17181a;
    --chip-waiting-bg: #3d3210; --chip-waiting-fg: #f0c750;
    --chip-action-bg: #3d1e1e; --chip-action-fg: #f0908f;
    --chip-scheduled-bg: #123526; --chip-scheduled-fg: #6fdba4;
    --chip-closed-bg: #2e2d2b; --chip-closed-fg: #9a9a97;
    --pill-bg: #2b2a28; --pill-fg: #ececeb; --pill-done-fg: #767674;
    --note-bg: #172433; --note-border: #253a52; --note-fg: #8fbaea;
  }
  :root[data-theme="light"] {
    --bg: #f7f7f5; --fg: #1a1a1a; --muted: #777; --muted2: #666; --th: #888;
    --card-bg: #fff; --card-border: #e4e2dd; --divider: #f0efeb;
    --focus-num-bg: #1a1a1a; --focus-num-fg: #fff;
    --chip-waiting-bg: #fef3cd; --chip-waiting-fg: #8a6d00;
    --chip-action-bg: #fde2e2; --chip-action-fg: #a12626;
    --chip-scheduled-bg: #d9f2e3; --chip-scheduled-fg: #1c7c46;
    --chip-closed-bg: #ececec; --chip-closed-fg: #666;
    --pill-bg: #f0efeb; --pill-fg: #1a1a1a; --pill-done-fg: #999;
    --note-bg: #eef4fb; --note-border: #d5e4f5; --note-fg: #2c5580;
  }
  * { box-sizing: border-box; margin: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--fg); padding: 20px; font-size: 14px; }
  h1 { font-size: 20px; margin-bottom: 2px; }
  .updated { color: var(--muted); font-size: 12px; margin-bottom: 18px; }
  h2 { font-size: 15px; margin: 22px 0 10px; }
  .mega-header { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted2); background: var(--divider); border-radius: 8px; padding: 8px 14px; margin: 28px 0 4px; }
  .mega-header:first-of-type { margin-top: 8px; }
  .card { background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 10px; padding: 14px 16px; margin-bottom: 10px; overflow-x: auto; }
  .focus-section { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; color: var(--muted2); padding: 16px 0 6px; }
  .focus-section:first-child { padding-top: 0; }
  .focus-item { display: flex; gap: 12px; align-items: flex-start; padding: 10px 0; border-bottom: 1px solid var(--divider); }
  .focus-item:last-child { border-bottom: none; }
  .focus-num { background: var(--focus-num-bg); color: var(--focus-num-fg); border-radius: 50%; width: 22px; height: 22px; min-width: 22px; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 600; margin-top: 1px; }
  .focus-title { font-weight: 600; }
  .focus-why { color: var(--muted2); font-size: 12.5px; margin-top: 2px; }
  .focus-more { margin-top: 2px; }
  .focus-more > summary { color: var(--muted2); font-size: 12.5px; cursor: pointer; list-style: none; }
  .focus-more > summary::-webkit-details-marker { display: none; }
  .focus-more > summary::after { content: " ▸ more"; font-size: 11px; opacity: 0.75; font-weight: 600; }
  .focus-more[open] > summary::after { content: " ▾ less"; }
  .focus-more[open] > summary { margin-bottom: 4px; }
  .focus-full { margin-top: 0; }
  /* Your-move panel — the priority-decision surface, deliberately loud */
  .ym-card { background: var(--card-bg); border: 2px solid #d97706; border-radius: 12px;
             padding: 14px 16px; margin: 18px 0 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.07); }
  .ym-head { font-size: 13px; font-weight: 800; text-transform: uppercase; letter-spacing: 0.05em;
             margin-bottom: 10px; }
  .ym-item { display: flex; gap: 12px; align-items: flex-start; padding: 9px 0;
             border-bottom: 1px solid var(--divider); }
  .ym-item:last-child { border-bottom: none; }
  .ym-num { background: #d97706; color: #fff; border-radius: 50%; width: 22px; height: 22px;
            min-width: 22px; display: flex; align-items: center; justify-content: center;
            font-size: 12px; font-weight: 700; margin-top: 1px; }
  .ym-title { font-weight: 700; }
  .ym-jd { font-weight: 600; font-size: 11px; color: #b45309; text-decoration: none;
           background: #fef3c7; border: 1px solid #fcd34d; border-radius: 4px;
           padding: 1px 6px; margin-left: 6px; white-space: nowrap; vertical-align: middle; }
  .ym-jd:hover { background: #fde68a; }
  .ym-ask { color: var(--muted2); font-size: 12.5px; margin-top: 2px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; color: var(--th); font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: .04em; padding: 6px 10px 6px 0; border-bottom: 1px solid var(--card-border); }
  td { padding: 8px 10px 8px 0; border-bottom: 1px solid var(--divider); vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  .chip { display: inline-block; padding: 2px 9px; border-radius: 20px; font-size: 11.5px; font-weight: 600; }
  .chip.waiting { background: var(--chip-waiting-bg); color: var(--chip-waiting-fg); }
  .chip.action { background: var(--chip-action-bg); color: var(--chip-action-fg); }
  .chip.scheduled { background: var(--chip-scheduled-bg); color: var(--chip-scheduled-fg); }
  .chip.closed { background: var(--chip-closed-bg); color: var(--chip-closed-fg); }
  .sub { color: var(--muted2); font-size: 12px; }
  .pill-row { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
  .pill { background: var(--pill-bg); color: var(--pill-fg); border-radius: 6px; padding: 3px 8px; font-size: 12px; }
  .pill.done { text-decoration: line-through; color: var(--pill-done-fg); }
  .note { background: var(--note-bg); border: 1px solid var(--note-border); border-radius: 8px; padding: 10px 14px; font-size: 12.5px; color: var(--note-fg); margin-top: 16px; }
  .stats-row { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
  .stat { background: var(--divider); border-radius: 8px; padding: 6px 12px; font-size: 12.5px; }
  .stat strong { font-size: 15px; }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; vertical-align: middle; }
  .dot-clear { background: #1c7c46; }
  .dot-below { background: #a12626; }
  .dot-unknown { background: #999; }
  details.closed-group { margin-top: 10px; }
  details.closed-group summary { cursor: pointer; color: var(--muted2); font-size: 12.5px; padding: 6px 0; }
  details.closed-group summary:hover { color: var(--fg); }
  .draft { padding: 10px 0; border-bottom: 1px solid var(--divider); }
  .draft:last-child { border-bottom: none; }
  .draft-title { font-weight: 600; }
  .draft-meta { color: var(--muted2); font-size: 12px; margin: 2px 0 8px; }
  .draft-body { font-size: 13px; line-height: 1.5; background: var(--divider); border-radius: 8px; padding: 10px 12px; }
  /* ⭐ Draft structure — added 2026-08-03. The candidate: the markdown was far easier to read than the
     page. The old renderer merged every quoted line in an entry into one blob, so a two-recipient
     campaign became an undifferentiated wall. These give each level the separation the markdown
     already had, and make the SENDABLE TEXT visually distinct from the commentary about it. */
  .draft-h3 { font-weight: 650; font-size: 14px; margin: 16px 0 4px;
              padding-top: 12px; border-top: 2px solid var(--divider); }
  .draft-h4 { font-weight: 600; font-size: 12px; margin: 12px 0 4px;
              color: var(--muted2); text-transform: none; letter-spacing: .01em; }
  .draft-note { font-size: 12px; line-height: 1.55; color: var(--muted2); margin: 6px 0; }
  .draft-rule { border: 0; border-top: 1px dashed var(--divider); margin: 14px 0; }
  /* The message itself: its own card, so it is obvious what to copy and send. */
  .draft-quote { font-size: 13.5px; line-height: 1.62; background: var(--divider);
                 border-left: 3px solid var(--accent, #6b8afd); border-radius: 6px;
                 padding: 10px 14px; margin: 4px 0 10px; }
  .draft-quote p { margin: 0 0 .7em; }
  .draft-quote p:last-child { margin-bottom: 0; }
  .draft-meta > div { margin: 1px 0; }
  code.fileref { font-size: 12px; opacity: .85; }

  /* --- Tabs (added 2026-07-20, per the candidate: the dashboard was overloading job-search
     content with process/meta content). CSS-only via hidden radios + :checked ~ sibling
     selectors -- deliberately NO JavaScript, so nothing can be blocked by the artifact's
     CSP or fail to hydrate. Panels are plain siblings after the inputs. */
  .tabwrap { margin-top: 18px; }
  .tabwrap > input[type="radio"] { position: absolute; opacity: 0; pointer-events: none; }
  .tabbar { display: flex; flex-wrap: wrap; gap: 6px; border-bottom: 2px solid var(--divider);
            margin-bottom: 16px; position: sticky; top: 0; background: var(--bg); z-index: 5;
            padding-top: 4px; }
  .tabbar label { cursor: pointer; padding: 9px 14px; font-size: 13.5px; font-weight: 600;
                  color: var(--muted2); border-bottom: 2px solid transparent; margin-bottom: -2px;
                  border-radius: 6px 6px 0 0; white-space: nowrap; user-select: none; }
  .tabbar label:hover { color: var(--fg); background: var(--divider); }
  .tabbar label .tcount { font-weight: 500; opacity: .65; font-size: 12px; margin-left: 4px; }
  .tabpanel { display: none; }
  #tab-week:checked    ~ .tabbar label[for="tab-week"],
  #tab-actions:checked ~ .tabbar label[for="tab-actions"],
  #tab-jobs:checked    ~ .tabbar label[for="tab-jobs"],
  #tab-network:checked ~ .tabbar label[for="tab-network"] {
      color: var(--fg); border-bottom-color: var(--accent, #c96442); background: transparent; }
  #tab-week:checked    ~ .panel-week,
  #tab-actions:checked ~ .panel-actions,
  #tab-jobs:checked    ~ .panel-jobs,
  #tab-network:checked ~ .panel-network { display: block; }
  .tabpanel > h2:first-child { margin-top: 0; }
  .tabpanel { scroll-margin-top: 64px; }
  .tabbar { box-shadow: 0 6px 10px -8px rgba(0,0,0,.35); }
  @media print { .tabpanel { display: block !important; } .tabbar { display: none; } }

  /* ── Opportunity rows ──────────────────────────────────────────────────────
     One role, one place. The rail is the only new structural device, and the
     ONLY saturated colour on the row is the stage it is actually at, plus the
     existing action colour when it is waiting on the candidate. Everything else
     stays quiet so those two reads survive a fast scan. */
  .opp { border-top: 1px solid var(--card-border); padding: 12px 2px 10px; }
  .opp:first-child { border-top: 0; }
  .opp-head { display:flex; align-items:baseline; gap:14px; justify-content:space-between;
  @media (max-width: 620px) {
    /* On a phone the rail must not be stranded a full row width from its title. */
    .opp-head { gap:4px; }
    .opp-rail { width:100%; margin-top:2px; }
    .rail .seg { flex:1; max-width:48px; }
  }
              flex-wrap:wrap; }
  .opp-title { font-weight:650; font-size:15px; letter-spacing:-0.01em; }
  .opp-co { font-weight:450; opacity:.72; margin-left:8px; }
  /* The posting link is the single most-requested thing on this tab, so it is always in the
     same place and reachable without opening anything. Quiet until hovered or focused. */
  .opp-jd { margin-left:10px; font-size:11.5px; font-weight:600; letter-spacing:.02em;
            text-decoration:none; color:var(--rail-now); opacity:.85;
            border:1px solid var(--card-border); border-radius:5px; padding:1px 6px;
            white-space:nowrap; }
  .opp-jd:hover, .opp-jd:focus-visible { opacity:1; border-color:var(--rail-now); }
  .opp-jd-none { color:inherit; opacity:.4; font-weight:450; border-style:dashed; }
  .opp-meta { font-size:12.5px; opacity:.66; margin-top:2px; }
  .opp-next { font-size:13px; margin-top:7px; }
  .opp-arrow { opacity:.5; margin-right:4px; }
  .opp-owner { font-size:11.5px; opacity:.6; margin-left:6px; white-space:nowrap;
               display:inline-block; }
  /* ⭐ SPEND THE COLOUR IN ONE PLACE. Waiting-on-you rows sort first, so colouring the whole
     sentence turned the top of the list into a block of red and the signal stopped being a
     signal. The marker and the owner carry it; the text stays readable. */
  .opp-next-you .opp-arrow { color: var(--chip-action-fg); opacity:1; }
  .opp-next-you { font-weight:520; }
  .opp-next-you .opp-owner { color: var(--chip-action-fg); opacity:1; font-weight:600;
                             background: var(--chip-action-bg); border-radius:4px;
                             padding:1px 5px; }
  .opp-next-none { opacity:.55; font-style:italic; }
  .opp-more > summary { cursor:pointer; font-size:12px; opacity:.6; margin-top:6px;
                        list-style:none; }
  .opp-more > summary::-webkit-details-marker { display:none; }
  .opp-more > summary::before { content:"▸ "; }
  .opp-more[open] > summary::before { content:"▾ "; }
  .opp-detail { margin:8px 0 2px 14px; padding-left:12px;
                border-left:2px solid var(--card-border); }
  .od-h { font-size:11px; text-transform:uppercase; letter-spacing:.07em; opacity:.55;
          margin:8px 0 3px; }
  .od-p { font-size:13px; margin:3px 0; }
  .od-warn { color: var(--chip-action-fg); }
  .od-l { font-size:13px; margin:3px 0 3px 16px; padding:0; }

  /* The rail: four segments, one per real pipeline stage. */
  .rail { display:inline-flex; gap:3px; vertical-align:middle; }
  .rail .seg { width:26px; height:5px; border-radius:2px; background:var(--rail-todo); }
  .rail .seg.done { background:var(--rail-done); }
  .rail .seg.now  { background:var(--rail-now); }
  .rail-label { font-size:11px; opacity:.6; margin-left:8px; vertical-align:middle; }

  /* CSS-only filters, matching the tab pattern already used on this page. */
  .oppfilter { display:none; }
  .oppbar { display:flex; gap:6px; flex-wrap:wrap; margin:2px 0 8px; }
  .oppbar label { font-size:12px; padding:4px 10px; border-radius:999px; cursor:pointer;
                  border:1px solid var(--card-border); opacity:.75; }
  #of-all:checked ~ .oppbar label[for="of-all"],
  #of-you:checked ~ .oppbar label[for="of-you"],
  #of-app:checked ~ .oppbar label[for="of-app"],
  #of-per:checked ~ .oppbar label[for="of-per"],
  #of-non:checked ~ .oppbar label[for="of-non"] {
      opacity:1; font-weight:600; border-color:var(--rail-now); color:var(--rail-now); }
  #of-you:checked ~ .opp-list .opp[data-you="0"],
  #of-app:checked ~ .opp-list .opp[data-bucket="person"],
  #of-app:checked ~ .opp-list .opp[data-bucket="nothing"],
  #of-per:checked ~ .opp-list .opp[data-bucket="applied"],
  #of-per:checked ~ .opp-list .opp[data-bucket="nothing"],
  #of-non:checked ~ .opp-list .opp[data-bucket="applied"],
  #of-non:checked ~ .opp-list .opp[data-bucket="person"] { display:none; }
  @media (prefers-reduced-motion: reduce) { .opp-more { transition:none; } }
"""
    n_drafts = len(_sendable)
    n_covers = len(covers)
    _sub, _hum, _noth = application_tables()
    n_submitted, n_human, n_nothing = len(_sub), len(_hum), len(_noth)
    n_move = len(your_move)
    # count only real items ('i'), not the '## ' header entries parse_focus also returns
    n_week = sum(1 for e in thisweek_focus if e[0] == 'i')
    n_process = sum(1 for e in process_focus if e[0] == 'i')

    body_inner = f"""<h1>{html.escape(_dashboard_title())}</h1>
<div class="updated">Tracker snapshot: <strong>{today}</strong> · generated by scripts/generate_dashboard.py · source: the tracker repo (git)</div>

<div class="tabwrap">
<input type="radio" name="dtab" id="tab-week" checked>
<input type="radio" name="dtab" id="tab-actions">
<input type="radio" name="dtab" id="tab-jobs">
<input type="radio" name="dtab" id="tab-network">
<div class="tabbar">
  <label for="tab-week">📅 This Week<span class="tcount">{n_week}</span></label>
  <label for="tab-actions">⚡ Your Move<span class="tcount">{n_move + n_needs + n_drafts + n_covers}</span></label>
  <label for="tab-jobs">🎯 Opportunities<span class="tcount">{len(live_rows)}</span></label>
  <label for="tab-network">🤝 Network</label>
</div>

<div class="tabpanel panel-week">
  <h2>📅 This week — calls &amp; deadlines</h2>
  <div class="sub" style="margin:-6px 0 10px"><strong>What lives here:</strong> commitments already scheduled. Nothing here needs a decision — if it needs one, it\u2019s on Your Move instead.</div>
  <div class="card">{thisweek_html or '<div class="sub">Nothing scheduled this week.</div>'}</div>
  <div class="note"><strong>Meeting times are verified from the invite\u2019s <code>.ics</code>, not from recall.</strong>
  Download it through Chrome (Gmail renders an event card, and the attachment has a Download link),
  then run <code>python3 scripts/parse_ics.py &lt;file&gt;</code>. A calendar receipt only proves what was
  booked when it was sent — confirm anything that may have been rescheduled.</div>
</div>

<div class="tabpanel panel-actions">
  <div class="sub" style="margin:0 0 12px;font-size:1.02em"><strong>Everything that needs you, in one place &mdash; {n_move + n_needs + n_drafts + n_covers} open.</strong>
  {n_move} job-search {"action" if n_move == 1 else "actions"} &middot; {n_needs} system {"item" if n_needs == 1 else "items"} &middot;
  {n_drafts} {"draft" if n_drafts == 1 else "drafts"} to approve &middot;
  {n_covers} cover {"letter" if n_covers == 1 else "letters"}.
  Each stays here until the work is actually <em>done</em> (sent, applied, accepted, or answered), not merely decided.</div>
  <h2>⚡ Decisions &amp; actions waiting on you</h2>
  <div class="sub" style="margin:-6px 0 10px"><strong>What lives here:</strong> job-search actions blocked on you — each line is a question or an ask. Once it\u2019s answered it leaves this list entirely, rather than becoming a \u201cdone\u201d note. System and tooling items now sit in their own group just below, not on a separate tab.</div>
  <div class="ym-card"><div class="ym-head">Nothing here moves without you</div>{your_move_html}</div>
  <h2 style="font-size:16px;margin-top:22px">⚙️ System &amp; tooling — needs you <span class="tcount">{n_needs}</span></h2>
  <div class="sub" style="margin:-6px 0 10px">Decisions about the tracker, scripts, credentials, or tooling that only you can make. Same rule: each stays until it is done.</div>
  <div class="ym-card"><div class="ym-head">Needs your input</div>{needs_html or '<div class="sub" style="padding:8px 0">Nothing here needs you right now.</div>'}</div>
  <h2>✉️ Pending drafts — awaiting your approval to send</h2>
  <div class="sub" style="margin:-6px 0 10px">Nothing is ever sent without your explicit approval.</div>
  <div class="card">{drafts_html}</div>
  {'<h2 style="font-size:16px;margin-top:22px">⏳ Waiting on someone else <span class="tcount">' + str(n_blocked) + '</span></h2><div class="sub" style="margin:-6px 0 10px">Written and ready, but blocked until the other person acts. <strong>Not yours to do</strong> — shown so you know it exists, and it moves to the list above by itself once the precondition is met.</div><div class="card">' + blocked_html + '</div>' if n_blocked else ''}
  <h2>📄 Cover letters — for applications you submit yourself</h2>
  <div class="sub" style="margin:-6px 0 10px">The “why this role is a fit” message that goes with an ATS application. Every claim traces to resume.md. You paste and submit these yourself — nothing is applied on your behalf.</div>
  <div class="card">{covers_html}</div>
</div>

<div class="tabpanel panel-jobs">
  <h2>🎯 Opportunities — where each role stands, and what happens next</h2>
  <div class="sub" style="margin:-6px 0 10px"><strong>What lives here:</strong> every live role,
  once. The bar under each title is the pipeline stage it has actually reached; the coloured
  segment is where it is now. Filter to narrow the list — a role never moves to another section,
  because there are no other sections.</div>

  <input type="radio" name="oppf" id="of-all" class="oppfilter" checked>
  <input type="radio" name="oppf" id="of-you" class="oppfilter">
  <input type="radio" name="oppf" id="of-app" class="oppfilter">
  <input type="radio" name="oppf" id="of-per" class="oppfilter">
  <input type="radio" name="oppf" id="of-non" class="oppfilter">
  <div class="oppbar">
    <label for="of-all">All ({opp_counts["all"]})</label>
    <label for="of-you">Waiting on you ({opp_counts["you"]})</label>
    <label for="of-app">Applied ({opp_counts["applied"]})</label>
    <label for="of-per">In play through a person ({opp_counts["person"]})</label>
    <label for="of-non">Nothing sent ({opp_counts["nothing"]})</label>
  </div>
  <div class="card opp-list">{opp_list_html}</div>

  <div class="note" style="margin-top:14px"><strong>Only &ldquo;nothing sent&rdquo; is a gap.</strong>
  Applied and in-play-through-a-person are both covered; a role carried with nothing sent is the
  hole. <strong>Cover letter</strong> appears under a role only when it was confirmed —
  <code>unrecorded</code> means nobody asked, and it is never guessed.</div>
</div>

<div class="tabpanel panel-network">
  <h2>🤝 Search-firm &amp; PE relationships</h2>
  <div class="card">{render_table(fh, frows, status_cols=(2,))}
  <div class="sub" style="margin:12px 0 6px">Retained firms to register with (1–2/week):</div>
  <div class="pill-row">{pills}</div></div>
  <h2>👥 Alumni &amp; warm network</h2>
  <div class="card">{render_table(ah, arows, status_cols=())}</div>
  <div class="note"><strong>Channel reality check:</strong> public job boards fill roughly 15% of executive seats.
  Retained-firm relationships and warm intros first; board scanning second.</div>
</div>

</div>"""

    doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html.escape(_dashboard_title())}</title><style>{css}</style></head><body>
{body_inner}
</body></html>"""
    (ROOT / "dashboard.html").write_text(doc, encoding="utf-8")

    # Body-only variant for publishing via the Artifact tool, which supplies
    # its own <!doctype>/<html>/<head>/<body> wrapper.
    artifact_doc = f"""<title>{html.escape(_dashboard_title())}</title>
<style>{css}</style>
{body_inner}"""
    (ROOT / "dashboard_artifact.html").write_text(artifact_doc, encoding="utf-8")

    print(f"Wrote dashboard.html ({len(doc)} bytes) and dashboard_artifact.html ({len(artifact_doc)} bytes), "
          f"{len(focus)} opportunity focus items + {len(process_focus)} process focus items, "
          f"{len(srows2)} sourced ({len(live_rows)} active / {len(closed_rows)} closed), "
          f"{len(frows)} firm rows, {len(arows)} alumni rows")


if __name__ == "__main__":
    main()
