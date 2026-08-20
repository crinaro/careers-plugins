#!/usr/bin/env python3
"""Turn a drafted cover letter into a deliverable: a Google Doc, or a local .docx.

⭐ WHY THERE ARE TWO MODES. The letter is written in `cover_letters.md` and the candidate needs it as a
document the candidate can print or attach. That used to mean Google Drive, unconditionally — which quietly
made a Google account a requirement for using this plugin at all. `local_docx` removes that: it
writes a real .docx with the stdlib alone (a .docx is a zip of XML), so someone with no Drive, no
connector, or a workplace that blocks it still gets a document.

    ~/.claude/jobsearch/run letter_out.py --status
    ~/.claude/jobsearch/run letter_out.py --set-mode local_docx
    ~/.claude/jobsearch/run letter_out.py --render "<an employer>" [--out /path/file.docx]

⚠️ WHAT THIS SCRIPT WILL NOT DO: it does not create the Google Doc. The document connector is
CREATE-ONLY — no update, no delete — so a document must be pushed exactly once, when the text is
final, and always with the job-search folder as `parentId` (the connector cannot move a file
afterwards, and the only fix for one landing in My Drive root is a second copy for the candidate to
delete). In `drive` mode this script prints the parentId to use and stops. Creating it is the
session's job, deliberately, so it happens once and on purpose.

⭐ THE BODY COMES FROM `> `-QUOTED LINES ONLY, exactly as the dashboard parsers read it. A letter
whose body was written as plain text published EMPTY once and only the candidate noticed — the source
file reads perfectly, so every constraint check passes while the deliverable is blank. This script
fails loudly on an unquoted body rather than writing an empty document.
"""

import argparse
import json
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _root import profile_root
import profile as prof

MODES = ("drive", "local_docx")
DEFAULT_MODE = "drive"


# ---------------------------------------------------------------- config

def _config_path():
    return os.path.join(profile_root(), "config.json")


def _load_config():
    with open(_config_path(), encoding="utf-8") as fh:
        return json.load(fh)


def output_settings():
    """The letter-output block, with defaults applied. Never invents a folder id."""
    cfg = _load_config()
    w = cfg.get("writing", {})
    out = dict(w.get("cover_letter_output") or {})
    out.setdefault("mode", DEFAULT_MODE)
    out.setdefault("local_dir", "letters")
    drive = cfg.get("drive", {}) or {}
    out["drive_folder_id"] = drive.get("job_search_folder_id")
    out["drive_folder_name"] = drive.get("job_search_folder_name")
    return out


def set_mode(mode):
    if mode not in MODES:
        print("Unknown mode %r. Choose one of: %s" % (mode, ", ".join(MODES)))
        return 2
    cfg = _load_config()
    w = cfg.setdefault("writing", {})
    block = w.setdefault("cover_letter_output", {})
    block["mode"] = mode
    block.setdefault("local_dir", "letters")
    block["_why"] = ("drive = push to the job-search Drive folder (needs a Google account and the "
                     "documents connector). local_docx = write a .docx next to the repo, no "
                     "Google account required.")
    p = _config_path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, p)          # atomic — a partial write would destroy the whole config
    print("Cover-letter output mode set to %r." % mode)
    return status()


# ---------------------------------------------------------------- reading the draft

def _letters_path():
    return os.path.join(profile_root(), "cover_letters.md")


def find_entry(needle):
    """Return (heading, body_lines) for the entry whose heading matches `needle`."""
    p = _letters_path()
    if not os.path.exists(p):
        raise SystemExit("No cover_letters.md at %s" % p)
    with open(p, encoding="utf-8") as fh:
        lines = fh.read().splitlines()

    entries, cur = [], None
    for line in lines:
        if re.match(r"^##\s+\S", line):
            cur = {"heading": line.lstrip("# ").strip(), "body": []}
            entries.append(cur)
        elif cur is not None and line.startswith(">"):
            cur["body"].append(line[1:].lstrip() if len(line) > 1 else "")
    if not entries:
        raise SystemExit("No entries found in cover_letters.md")

    matches = [e for e in entries if needle.lower() in e["heading"].lower()]
    if not matches:
        raise SystemExit("No entry matching %r. Entries:\n  %s"
                         % (needle, "\n  ".join(e["heading"] for e in entries)))
    if len(matches) > 1:
        raise SystemExit("%r matches %d entries; be more specific:\n  %s"
                         % (needle, len(matches), "\n  ".join(e["heading"] for e in matches)))
    e = matches[0]
    if not any(l.strip() for l in e["body"]):
        raise SystemExit(
            "⛔ %r has NO `> `-quoted body.\n"
            "The body must be blockquoted or it publishes EMPTY — and an empty body is\n"
            "indistinguishable from 'not drafted yet'. Fix cover_letters.md, then re-run."
            % e["heading"])
    return e["heading"], e["body"]


def check_text(body_lines):
    """Report the constraints that make a letter shippable. Advisory, never silent."""
    cfg = _load_config()
    w = cfg.get("writing", {})
    text = "\n".join(body_lines)
    problems = []
    banned = w.get("banned_characters") or []
    for ch in banned:
        if ch and ch in text:
            problems.append("banned character %r appears %d time(s)" % (ch, text.count(ch)))
    words = len(re.findall(r"\S+", text))
    target = w.get("cover_letter_target_body_words")
    if isinstance(target, int) and words > target * 1.25:
        problems.append("body is %d words against a target of %d — the one-page rule is at risk"
                        % (words, target))
    us = w.get("us_english") or {}
    pairs = us.get("prefer") if isinstance(us, dict) else None
    if isinstance(pairs, dict):
        for brit, amer in pairs.items():
            if re.search(r"\b%s\b" % re.escape(brit), text, re.I):
                problems.append("British spelling %r — use %r" % (brit, amer))
    return words, problems


# ---------------------------------------------------------------- .docx

def _xml_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def _para(text, bold=False, size_half_pt=22, space_after=160, align=None):
    runs = ""
    if text:
        rpr = "<w:rPr>%s<w:sz w:val=\"%d\"/></w:rPr>" % (
            "<w:b/>" if bold else "", size_half_pt)
        runs = ('<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>'
                % (rpr, _xml_escape(text)))
    jc = '<w:jc w:val="%s"/>' % align if align else ""
    return ('<w:p><w:pPr>%s<w:spacing w:after="%d" w:line="240" w:lineRule="auto"/></w:pPr>%s</w:p>'
            % (jc, space_after, runs))


def write_docx(path, header_lines, body_lines, font="Times New Roman", margin_twips=1080):
    """Write a real .docx using only the stdlib. A .docx is a zip of XML parts."""
    paras = []
    for i, line in enumerate(header_lines):
        paras.append(_para(line, bold=(i == 0), size_half_pt=24 if i == 0 else 20,
                           space_after=40, align="center"))
    paras.append(_para("", space_after=200))
    for line in body_lines:
        paras.append(_para(line, space_after=160 if line.strip() else 80))

    sect = ('<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
            '<w:pgMar w:top="%d" w:right="%d" w:bottom="%d" w:left="%d" '
            'w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
            % (margin_twips, margin_twips, margin_twips, margin_twips))

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body>%s%s</w:body></w:document>' % ("".join(paras), sect))

    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:docDefaults><w:rPrDefault><w:rPr>'
        '<w:rFonts w:ascii="%s" w:hAnsi="%s" w:cs="%s"/><w:sz w:val="22"/>'
        '</w:rPr></w:rPrDefault></w:docDefaults></w:styles>' % (font, font, font))

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
        'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.wordprocessingml.styles+xml"/></Types>')

    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/officeDocument" Target="word/document.xml"/></Relationships>')

    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/styles" Target="styles.xml"/></Relationships>')

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
        z.writestr("word/styles.xml", styles)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
    return path


# ---------------------------------------------------------------- commands

def _header_lines():
    """The canonical header, rendered from config × user.json. Never retyped."""
    try:
        rendered = prof.cover_letter_header()
        if isinstance(rendered, (list, tuple)):
            return [str(x) for x in rendered]
        return str(rendered).splitlines()
    except Exception:
        u = prof.user().get("identity", {})
        return [u.get("full_name", ""),
                " • ".join(x for x in (u.get("city"), u.get("linkedin"),
                                       u.get("primary_email"), u.get("phone")) if x)]


def status():
    s = output_settings()
    print("COVER-LETTER OUTPUT")
    print("=" * 74)
    print("  Mode        : %s" % s["mode"])
    if s["mode"] == "drive":
        if s["drive_folder_id"]:
            print("  Drive folder: %s (%s)" % (s["drive_folder_name"], s["drive_folder_id"]))
            print()
            print("  ⭐ Pass that id as `parentId` when creating the document. The connector")
            print("     CANNOT MOVE A FILE, so a document created without a parent lands in My")
            print("     Drive root and the only fix is a second copy for you to delete.")
        else:
            print("  Drive folder: NOT CONFIGURED")
            print()
            print("  ⚠️ Mode is `drive` but config.json has no drive.job_search_folder_id, so a")
            print("     document would land in My Drive root and could not be moved.")
            print("     Set the folder id, or switch to a local file:")
            print("       ~/.claude/jobsearch/run letter_out.py --set-mode local_docx")
    else:
        print("  Writes to  : %s/" % os.path.join(profile_root(), s["local_dir"]))
        print("  No Google account, connector or network needed.")
    print()
    print("  Switch:  ~/.claude/jobsearch/run letter_out.py --set-mode %s"
          % ("local_docx" if s["mode"] == "drive" else "drive"))
    return 0


def _send_hold(heading):
    """The entry's send-precondition state, or None. dev #169: this script is an outward path —
    it turns a letter into the deliverable — and it used to consult no precondition at all, the
    same asymmetry that let a held letter render READY on the dashboard. Advisory here (the
    human may legitimately prepare the file ahead), but never silent."""
    try:
        import precondition as _pre
        for r in _pre.report(profile_root(), filenames=("cover_letters.md",)):
            if r["title"] == heading and r["state"] in _pre.NOT_SENDABLE:
                return r
    except Exception:
        pass                     # advisory: a broken resolver must not block a render
    return None


def render(needle, out_path=None):
    heading, body = find_entry(needle)
    words, problems = check_text(body)
    s = output_settings()

    print("Entry : %s" % heading)
    print("Body  : %d words" % words)
    hold = _send_hold(heading)
    if hold:
        print("\n⏳ SEND-HOLD ON THIS LETTER [%s]: %s" % (hold["state"], hold["why"]))
        print("   Rendering anyway — but this letter is NOT ready to submit until the")
        print("   precondition resolves (dev #169).")
    if problems:
        print("\n⚠️ FIX BEFORE SENDING:")
        for p in problems:
            print("   - %s" % p)
    print()

    if s["mode"] == "drive":
        print("Mode is `drive` — this script does NOT create the document.")
        print("The connector is CREATE-ONLY, so push exactly once, when the text is final:")
        print()
        print("   parentId: %s" % (s["drive_folder_id"] or "!! NOT CONFIGURED !!"))
        print()
        print("Then READ THE DOCUMENT BACK to verify it — the create response's reported size is")
        print("meaningless for a native Google Doc.")
        return 1 if not s["drive_folder_id"] else 0

    header = _header_lines()
    # ⭐ The drafted body often already carries the canonical header, because the writer follows
    # the same template. Prepending it again produced a document with the name and contact block
    # printed twice — caught on the first real render. Detect and skip.
    first_real = next((l for l in body if l.strip()), "")
    name = (prof.user().get("identity", {}) or {}).get("full_name", "")
    if name and name.strip() and name.strip() in first_real:
        header = []

    if not out_path:
        slug = re.sub(r"[^A-Za-z0-9]+", "-", heading).strip("-").lower()[:60]
        out_path = os.path.join(profile_root(), s["local_dir"], "%s.docx" % slug)
    write_docx(out_path, header, body)
    print("Wrote %s (%d bytes)" % (out_path, os.path.getsize(out_path)))
    print()
    print("⭐ OPEN IT AND CHECK THE PAGE COUNT before sending. The one-page rule is a real")
    print("   constraint — a second page carrying only a signature is a defect. If it spills,")
    print("   cut margins before cutting substance.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--set-mode", metavar="MODE", choices=MODES)
    ap.add_argument("--render", metavar="ENTRY")
    ap.add_argument("--out", metavar="PATH")
    a = ap.parse_args()
    if a.set_mode:
        return set_mode(a.set_mode)
    if a.render:
        return render(a.render, a.out)
    return status()


if __name__ == "__main__":
    sys.exit(main())
