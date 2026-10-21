#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wa_intake.py · the intake buffer: things someone asked of the owner (or the owner promised), captured by the brain,
waiting to be placed by the owner.

The brain may return up to two `captures` per run, of five kinds: a dated request, a promise not yet delivered, an
appointment, money, a decision the other side stated. append_captures() adds them to the owner's buffer note (a
Markdown file in their vault at `intake.buffer`), deduplicated per conversation and quote in the state database table
`intake_seen`. Placing an entry (a task, a calendar event, a decision, dropping it) happens only in a session with the
owner, through their command-base intake mode, which calls --list-open and --mark-placed. The daemon never changes an
entry's status.

Buffer file (created by the install wizard with the owner's yes; this module never creates a missing folder):
  <any preamble the owner wants>
  ### 2026-09-15 · [wa] · [[Note-Name]] · what is asked
  - 🗣 09-14 16:53「the exact words, found by the program in the run's own pack」
  - <owner> 这边: 期限 09-20
  - 机器猜: (<world> · <kind>) <guess>
  - 深挖: `dossier.py prepare --name Note-Name`
  - 状态: open
  ## 已归
  <placed entries, their status line rewritten to "- 状态: 已归 → <where>">
English buffers (board.lang en) write "- status: open", "## Placed" and "- status: placed → <where>"; reading accepts
both languages in the same file.

The quote line (the second line of an entry) says how far its words can be trusted (resolve_quote decides):
  - 🗣 09-14 16:53「…」              found in a message's text or caption (en: - 🗣 09-14 16:53 "…")
  - 🎙 09-14 16:53 语音转字「…」      found in a voice note's transcript (en: voice transcript "…")
  - 📄 09-14 16:53 文件内文「…」      found in a document's text (en: document text "…")
  - ≈ 09-14 16:53 大意, 非逐字「…」   not found: the brain's words, a gist (en: gist, not verbatim "…")
  - ≈ 09-14 16:53 未核对「…」         not checked against any pack (en: not checked "…")
Resolved marks carry the program's stored text, never the brain's words. A message the person edited after a run judged
it gets a new entry whose title starts "✏️ 对方改了讯息" (en "✏️ they edited the message") and whose third line is
"- 改之前: 「old words」 …" (en "- before the edit: …"); the older entry stays byte for byte as it was.

Entry keys: entry_key() is the sha1 of the title line and the quote line. Neither changes after an entry is written (placing
rewrites only the status line), so a key names the same entry before and after placing, whatever its number.
append_under_key() adds one line as the last line of an entry found by its key, in either zone, under the same lock;
the daemon's vault write records an undo under the entries it had placed this way (they stay placed).

Safety (without it, two writers could overwrite each other and lose an entry already marked seen):
- append and mark-placed hold an exclusive fcntl.flock on the sidecar `.<buffer name>.lock` next to the buffer, read
  the file inside the lock, and replace it atomically only if its modification time did not change in between (an
  edit saved from Obsidian during the write makes it read again and redo the insert);
- `intake.enabled=false` turns append_captures into a no-op that returns 0. The switch comes from `cfg=` when given,
  else from the config.json next to the state database (the daemon's live folder), so every caller honours it.

The board's Today brief (read through the board's API, never written by this module): with --with-brief URL
(the board's /api/today, e.g. http://127.0.0.1:<port>/api/today) --list-open fetches the brief once and prints, under
each entry, the board's cards for that entry ("board: <project> · <kind> <task> · <status>", the proposal, the
owner's note, the notes about placing and replies), or "board: no card, judge it as before"; after the list come the
K cards, the board's open cards that have no intake entry (oldest first, with id and rev). The cards are a starting
point, not an answer: the owner still places each entry. When the board cannot be read, one line says why and the
listing is the usual one. --settle-card closes a K card once the owner has handled it (POST <URL>/settle).

CLI
  wa_intake.py [--config P | --buffer P] --count-open
  wa_intake.py [--config P | --buffer P] --list-open [--json]               numbered from 1, each with its key
  wa_intake.py [--config P | --buffer P] --list-open [--json] --with-brief URL
                                                   the board's cards under each entry, then the K cards; --json gives
                                                   {"entries": [... "cards"], "k_cards", "brief"} instead of the list
  wa_intake.py --settle-card ID --rev N --to "…" --with-brief URL
                                                   a K card handled: prints "settled" or the refusal; exit 0 settled, 1 not
  wa_intake.py [--config P | --buffer P] --list-placed [--json]             the placed zone, read only
  wa_intake.py [--config P | --buffer P] --mark-placed N --to "task [[Project]]"   (command-base intake mode only)
  wa_intake.py [--config P | --buffer P] --mark-placed-key KEY --to "…"     KEY: a full key or a unique prefix of 8+ hex
  wa_intake.py [--config P] --evening-status                               retired: JSON for an evening job installed
                                                                           before v1.3.0
  wa_intake.py --selftest                                                  temp files only
The evening push is retired in v1.3.0 (the board's Today brief replaces it). evening_prompt, evening_status and
--evening-status are kept so an evening job the owner chose to keep through an update does not break; they are removed
in a later release. They read the retired key intake.evening_push with a default (false), since wa_common no longer
fills it.

Python 3.9 standard library only.
"""

import argparse
import contextlib
import datetime as dt
import difflib
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wa_common as W  # noqa: E402

VERSION = "1.4.0"

KINDS = ("request_dated", "promise_undelivered", "appointment", "money", "decision")
KIND_LABELS = {
    "zh": {"request_dated": "有日期的请求", "promise_undelivered": "答应了没交", "appointment": "约时间地点",
           "money": "钱", "decision": "对方明说的决定"},
    "en": {"request_dated": "dated request", "promise_undelivered": "promised, not delivered", "appointment": "time and place",
           "money": "money", "decision": "their stated decision"},
}
MAX_PER_RUN = 2
TEXT_MAX, QUOTE_MAX, GUESS_MAX, DEADLINE_MAX, AT_MAX = 120, 200, 80, 40, 40
LOCK_WAIT_SECONDS = 30

MARKS = {
    "zh": {"placed_hdr": "## 已归", "open": "- 状态: open", "placed": "- 状态: 已归 → %s", "owner": "%s 这边",
           "owner_none": "我这边", "guess": "机器猜", "dig": "深挖", "promise": "答应了没交", "deadline": "期限 %s",
           "pending": "待归位", "nonote": "%s (无档)", "none": "无",
           "lbl_transcript": "语音转字", "lbl_doc": "文件内文", "lbl_gist": "大意, 非逐字", "lbl_unchecked": "未核对",
           "edited": "✏️ 对方改了讯息", "before_same": "- 改之前: %s (同一封讯息; 照旧版写的条目以这条为准)",
           "stale_title": "这行标题是照旧版写的, 以下面的原话为准: %s", "before_stale": "- 改之前: %s (%s)",
           "stale_open": "旧条目还开着", "stale_status": "旧条目状态: %s", "recheck": "旧条目的日期与期限照新原话重看",
           "correction": "更正", "merge": "不是新的事, 归位时跟旧条目并成一件"},
    "en": {"placed_hdr": "## Placed", "open": "- status: open", "placed": "- status: placed → %s", "owner": "%s side",
           "owner_none": "owner side", "guess": "machine guess", "dig": "dig deeper", "promise": "promised, not delivered",
           "deadline": "due %s", "pending": "to place", "nonote": "%s (no note)", "none": "none",
           "lbl_transcript": "voice transcript", "lbl_doc": "document text", "lbl_gist": "gist, not verbatim",
           "lbl_unchecked": "not checked", "edited": "✏️ they edited the message",
           "before_same": "- before the edit: %s (same message; this entry replaces any entry written from the old version)",
           "stale_title": "this title was written from the old version, trust the quote below: %s",
           "before_stale": "- before the edit: %s (%s)", "stale_open": "the older entry is still open",
           "stale_status": "older entry status: %s", "recheck": "recheck the older entry's date and deadline against the new words",
           "correction": "correction", "merge": "not a new item; merge it with the older entry when placing"},
}
_PLACED_RE = re.compile(r"^## (?:已归|Placed)[ \t]*$", re.M)
_ENTRY_RE = re.compile(r"^### ", re.M)
OPEN_LINES = {"zh": MARKS["zh"]["open"], "en": MARKS["en"]["open"]}
# the quote of a quote line, both languages, every mark: group "q" (「…」) or "e" ("…")
QUOTE_LINE_RE = re.compile(
    r'^[ \t]*-[ \t]*(?P<mark>🗣|🎙|📄|≈)[ \t]*(?P<at>\d{2}-\d{2} \d{2}:\d{2})?[ \t]*'
    r'(?:语音转字|文件内文|大意, 非逐字|未核对|voice transcript|document text|gist, not verbatim|not checked)?[ \t]*'
    r'(?:「(?P<q>.*)」|"(?P<e>.*)")[ \t]*$')
_STATUS_RE = re.compile(r"^[ \t]*-[ \t]*(?:状态|status):[ \t]*(?P<v>.*?)[ \t]*$", re.M)
_PLACED_TO_RE = re.compile(r"^[ \t]*-[ \t]*(?:状态:[ \t]*已归|status:[ \t]*placed)[ \t]*→[ \t]*(?P<to>.*?)[ \t]*$", re.M)

EVENING_LABELS = {
    "zh": {"title": "🗂 intake 预分类 · %d 条等归位", "reply": "在任何 Claude Code session 说「intake」开始归位。",
           "fallback": "🗂 intake · %d 条等归位 (预分类没生出来, 先给原清单)", "ask": "问",
           "breach": "⚠️ 预分类的模型载入了不该有的工具: %s"},
    "en": {"title": "🗂 intake pre-sort · %d open", "reply": "Say \"intake\" in any Claude Code session to place them.",
           "fallback": "🗂 intake · %d open (the pre-sort did not come back, raw list below)", "ask": "ask",
           "breach": "⚠️ the pre-sort model loaded tools it should not have: %s"},
}
DEFAULT_PLACES = ("a task under one of the owner's projects", "a calendar event (date and time)", "a decision",
                  "a project or method note", "just a capture in the daily journal", "drop it")


# ---------------------------------------------------------------- captures from the brain

def _one_line(s, limit):
    return re.sub(r"\s+", " ", str(s or "")).strip()[:limit]


def validate_captures(captures):
    """The brain's `captures` (maybe not a list, maybe more than 2, maybe unknown kinds) to at most 2 legal ones:
    {kind, text (120), quote (200), quote_at (40), deadline (40), guess (80)}. Unknown kind or empty text or quote: dropped."""
    if not isinstance(captures, list):
        return []
    out = []
    for c in captures:
        if not isinstance(c, dict):
            continue
        kind = str(c.get("kind") or "").strip()
        if kind not in KINDS:
            continue
        text = _one_line(c.get("text"), TEXT_MAX)
        quote = _one_line(c.get("quote"), QUOTE_MAX)
        if not text or not quote:
            continue
        out.append({"kind": kind, "text": text, "quote": quote, "quote_at": _one_line(c.get("quote_at"), AT_MAX),
                    "deadline": _one_line(c.get("deadline"), DEADLINE_MAX), "guess": _one_line(c.get("guess"), GUESS_MAX)})
        if len(out) >= MAX_PER_RUN:
            break
    return out


def quote_hash(jid, quote):
    return hashlib.sha1((str(jid) + "|" + str(quote).strip().lower()).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- quotes checked by code (the one rule)

_DROP_RE = re.compile(r"[\s\W_]", re.U)
_ELLIPSIS_RE = re.compile(r"\.{3,}|…")
_TRAIL = set("?!.)？！。）")
NEAR_MIN, NEAR_COVER, NEAR_SPAN, NEAR_SOURCE_MAX = 8, 0.9, 1.15, 20000
STITCH_MIN, STITCH_MAX = 2, 4
RESOLVED = ("exact", "excerpt", "stitched", "near")
SOURCE_FIELDS = (("text", "text"), ("caption", "text"), ("transcript", "transcript"), ("doc_text", "doc"))


def _norm_map(s):
    """(normalized, positions): lowercase, every whitespace, punctuation and _ removed; positions[i] is the index in s of
    the character that normalized[i] came from."""
    out, pos = [], []
    for i, ch in enumerate(str(s or "")):
        if _DROP_RE.match(ch):
            continue
        for low in ch.lower():
            out.append(low)
            pos.append(i)
    return "".join(out), pos


def _norm(s):
    return _norm_map(s)[0]


def quote_hash_norm(jid, text):
    """The dedupe key that ignores spacing, punctuation and case."""
    return hashlib.sha1((str(jid) + "|n:" + _norm(text)).encode("utf-8")).hexdigest()


def _cut(s, limit=QUOTE_MAX):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= limit else s[:limit - 1] + "…"


def _alnum(ch):
    return ch.isascii() and ch.isalnum()


def _span(orig, pos, i, j):
    """Stored characters behind normalized [i, j); a Latin word cut at either edge is completed (4 letters at most),
    trailing ?!.) and their full width forms carried along."""
    if j <= i or not pos:
        return ""
    a, b = pos[i], pos[j - 1] + 1
    k = 0
    while a > 0 and k < 4 and _alnum(orig[a - 1]) and _alnum(orig[a]):
        a, k = a - 1, k + 1
    k = 0
    while b < len(orig) and k < 4 and _alnum(orig[b]) and _alnum(orig[b - 1]):
        b, k = b + 1, k + 1
    while b < len(orig) and orig[b] in _TRAIL:
        b += 1
    return orig[a:b]


def _sources(m):
    """[(src, original text)] of one message: text and caption are src text, transcript, doc_text is src doc."""
    out = []
    for field, src in SOURCE_FIELDS:
        v = m.get(field) if isinstance(m, dict) else None
        if v and str(v).strip():
            out.append((src, str(v)))
    return out


def _near_span(nq, ns):
    """(i, j) in ns that covers at least 90% of nq in order, one block at least a quarter of nq, span not much longer than
    nq; None otherwise. difflib, autojunk off, searched in a window around the longest common block."""
    if len(nq) < NEAR_MIN or not ns or len(ns) > NEAR_SOURCE_MAX:
        return None
    sm = difflib.SequenceMatcher(None, nq, ns, autojunk=False)
    a, b, size = sm.find_longest_match(0, len(nq), 0, len(ns))
    if size * 4 < len(nq):
        return None
    slack = len(nq) // 4 + 2
    lo, hi = max(0, b - a - slack), min(len(ns), b + (len(nq) - a) + slack)
    blocks = [x for x in difflib.SequenceMatcher(None, nq, ns[lo:hi], autojunk=False).get_matching_blocks() if x.size]
    covered = sum(x.size for x in blocks)
    need = NEAR_COVER * len(nq)
    while len(blocks) > 1 and blocks[0].size < 3 and covered - blocks[0].size >= need:      # stray letters at the edges
        covered -= blocks[0].size
        blocks = blocks[1:]
    while len(blocks) > 1 and blocks[-1].size < 3 and covered - blocks[-1].size >= need:
        covered -= blocks[-1].size
        blocks = blocks[:-1]
    if not blocks or covered < need or max(x.size for x in blocks) * 4 < len(nq):
        return None
    i, j = lo + blocks[0].b, lo + blocks[-1].b + blocks[-1].size
    if (j - i) > len(nq) * NEAR_SPAN + 2:
        return None
    return i, j


def _cand(how, src, text, msgs, idx):
    first = msgs[0]
    return {"how": how, "src": src, "text": _cut(text), "pks": [m.get("pk") for m in msgs], "at": str(first.get("at") or ""),
            "me": bool(first.get("me")), "_idx": idx, "_ats": [_fmt_at(m.get("at")) for m in msgs]}


def _stitch_field(m, kind):
    if kind == "transcript":
        return str(m.get("transcript") or "")
    return str(m.get("text") or m.get("caption") or "")


def _stitched(nq, messages):
    out = []
    for kind in ("text", "transcript"):
        for i in range(len(messages)):
            for w in range(STITCH_MIN, STITCH_MAX + 1):
                win = messages[i:i + w]
                if len(win) < w:
                    break
                m0 = win[0]
                if any(bool(m.get("me")) != bool(m0.get("me")) or str(m.get("speaker") or "") != str(m0.get("speaker") or "")
                       for m in win):
                    break
                parts = [(_stitch_field(m, kind),) + _norm_map(_stitch_field(m, kind)) for m in win]
                if any(not p[1] for p in parts):
                    break
                offs, total = [], 0
                for p in parts:
                    offs.append(total)
                    total += len(p[1])
                concat = "".join(p[1] for p in parts)
                start = concat.find(nq)
                while start >= 0:
                    end = start + len(nq)
                    if start < offs[0] + len(parts[0][1]) and end > offs[-1]:     # every message contributes
                        spans = []
                        for k, p in enumerate(parts):
                            a, b = max(start, offs[k]) - offs[k], min(end, offs[k] + len(p[1])) - offs[k]
                            spans.append(_span(p[0], p[2], a, b).strip())
                        out.append(_cand("stitched", kind, " ⏎ ".join(spans), win, i + w - 1))
                        break
                    start = concat.find(nq, start + 1)
    return out


def resolve_quote(quote, messages, quote_at=""):
    """The brain's quote against one conversation's messages (time order, each {pk, at, me, speaker, text, caption,
    transcript, doc_text}). Read only. Returns {"how", "src", "text", "pks", "at", "me"}:
    how exact | excerpt | stitched | near carry the stored characters in text (cut to 200 with …); how none carries the
    brain's own words and src None. Among several candidates of the first level that matches, the one whose time is
    quote_at's MM-DD HH:MM wins, else the newest."""
    q = str(quote or "")
    msgs = [m for m in (messages or []) if isinstance(m, dict)]
    none = {"how": "none", "src": None, "text": _cut(q), "pks": [], "at": "", "me": False}
    nq = _norm(q)
    if not nq or not msgs:
        return none
    segs = [_norm(x) for x in _ELLIPSIS_RE.split(q)]
    segs = [x for x in segs if x]
    levels = []
    exact, excerpt, near = [], [], []
    for idx, m in enumerate(msgs):
        for src, orig in _sources(m):
            ns, pos = _norm_map(orig)
            k = ns.find(nq)
            if k >= 0:
                exact.append(_cand("exact", src, _span(orig, pos, k, k + len(nq)), [m], idx))
                continue
            if len(segs) >= 2:
                cur, spans = 0, []
                for sg in segs:
                    k = ns.find(sg, cur)
                    if k < 0:
                        spans = None
                        break
                    spans.append(_span(orig, pos, k, k + len(sg)).strip())
                    cur = k + len(sg)
                if spans:
                    excerpt.append(_cand("excerpt", src, " … ".join(spans), [m], idx))
                    continue
            ij = _near_span(nq, ns)
            if ij:
                near.append(_cand("near", src, _span(orig, pos, ij[0], ij[1]), [m], idx))
    levels = [exact, excerpt, _stitched(nq, msgs) if not (exact or excerpt) else [], near]
    want = _fmt_at(quote_at) if str(quote_at or "").strip() else ""
    for cands in levels:
        if not cands:
            continue
        hit = [c for c in cands if want and want in c["_ats"]]
        best = max(hit or cands, key=lambda c: c["_idx"])
        return {k: v for k, v in best.items() if not k.startswith("_")}
    return none


def quote_line(resolved, quote, quote_at="", lang="zh"):
    """The second line of an entry. resolved None: not checked (≈ 未核对); how none: a gist (≈ 大意, 非逐字);
    resolved: 🗣 (text or caption), 🎙 (voice transcript) or 📄 (document text) with the stored text."""
    L = _lang(lang)
    M = MARKS[L]
    if resolved is None:
        mark, label, words, at = "≈", M["lbl_unchecked"], quote, _fmt_at(quote_at)
    elif resolved.get("how") not in RESOLVED:
        mark, label, words, at = "≈", M["lbl_gist"], quote, _fmt_at(quote_at)
    else:
        src = resolved.get("src")
        mark, label = {"transcript": ("🎙", M["lbl_transcript"]), "doc": ("📄", M["lbl_doc"])}.get(src, ("🗣", ""))
        words = resolved.get("text") or ""
        at = _fmt_at(resolved.get("at")) or _fmt_at(quote_at)
    words = _cut(words)
    q = ("「%s」" % words) if L == "zh" else ('"%s"' % words)
    head = " ".join([x for x in (mark, at, label) if x])
    if L == "zh" and (at or label):
        return "- %s%s" % (head, q)
    return "- %s %s" % (head, q)


def quote_of_line(line):
    """The words of a quote line (any mark, either language), or None."""
    m = QUOTE_LINE_RE.match(str(line or ""))
    if not m:
        return None
    return m.group("q") if m.group("q") is not None else m.group("e")


# ---------------------------------------------------------------- entry keys

def _key_title(s):
    s = str(s or "").strip()
    if s.startswith("###"):
        s = s[3:].strip()
    return "### " + s


def entry_key(entry):
    """sha1 of the title line and the quote line (the two lines that never change after an entry is written). Takes a
    list_entries dict (title, lines) or a block of text. The only place a key is computed."""
    if isinstance(entry, dict):
        title = "### " + str(entry.get("title") or "")
        lines = entry.get("lines") or []
        second = lines[0] if lines else ""
    else:
        ls = str(entry or "").lstrip("\n").split("\n")
        title = ls[0] if ls else ""
        second = ls[1] if len(ls) > 1 else ""
    return hashlib.sha1((_key_title(title) + "\n" + str(second).strip()).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- one entry

def _fmt_at(quote_at):
    """"2026-09-14T16:53:21", "2026-09-14 16:53" or "09-14 16:53" to "MM-DD HH:MM"; anything else unchanged."""
    s = str(quote_at or "").strip()
    if not s:
        return ""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})", s)
    if m:
        return "%s-%s %s:%s" % (m.group(2), m.group(3), m.group(4), m.group(5))
    m = re.search(r"(\d{2})-(\d{2})\s+(\d{2}):(\d{2})", s)
    if m:
        return "%s-%s %s:%s" % m.groups()
    return s


def _lang(lang):
    return "en" if str(lang or "").lower().startswith("en") else "zh"


def _who(name, people_note, lang):
    """With a note: [[Note-Name]]; without: "Name (no note)", never a link."""
    if people_note:
        stem = Path(str(people_note)).name
        if stem.endswith(".md"):
            stem = stem[:-3]
        if stem:
            return "[[%s]]" % stem, stem
    nm = str(name or "?").strip() or "?"
    return MARKS[_lang(lang)]["nonote"] % nm, str(name or "").strip()


def _owner(owner_label, M):
    return (M["owner"] % owner_label.strip()) if str(owner_label or "").strip() else M["owner_none"]


def _quoted(words, L):
    return ("「%s」" % words) if L == "zh" else ('"%s"' % words)


def render_entry(cap, jid, name, world, people_note=None, source="wa", today=None, owner_label="", lang="zh",
                 world_label=None, resolved=None, edit=None):
    """One entry. resolved: resolve_quote's result, None when the quote was not checked (the line then says so).
    edit: {old, ...} when the capture's message was edited after an earlier run judged it (the ✏️ title and the
    before line)."""
    L = _lang(lang)
    M = MARKS[L]
    today = today or dt.date.today().isoformat()
    who, dossier_name = _who(name, people_note, L)
    side = []
    if cap["kind"] == "promise_undelivered":
        side.append(M["promise"])
    if cap.get("deadline"):
        side.append(M["deadline"] % cap["deadline"])
    if not side:
        side.append(M["pending"])
    head = "(%s · %s)" % (world_label or world or "-", KIND_LABELS[L].get(cap["kind"], cap["kind"]))
    title = cap["text"] if not edit else "%s · %s" % (M["edited"], cap["text"])
    lines = ["### %s · [%s] · %s · %s" % (today, source, who, title),
             quote_line(resolved, cap["quote"], cap.get("quote_at"), L)]
    if edit:
        lines.append(M["before_same"] % _quoted(_cut(edit.get("old")), L))
    lines += [
        "- %s: %s" % (_owner(owner_label, M), ", ".join(side)),
        "- %s: %s %s" % (M["guess"], head, cap.get("guess") or M["none"]),
        "- %s: `dossier.py prepare --name %s`" % (M["dig"], dossier_name or name or "?"),
        M["open"],
    ]
    return "\n".join(lines)


def _blocks(text):
    """Every `### ` section of the whole buffer, both zones: [{zone, index (within the zone), title, lines, block}]."""
    open_zone, placed = _zones(text)
    out = []
    for zone, part in (("open", open_zone), ("placed", placed or "")):
        for i, (s, e) in enumerate(_entry_spans(part)):
            block = part[s:e].rstrip("\n")
            ls = block.split("\n")
            out.append({"zone": zone, "index": i, "title": ls[0][4:].strip(), "lines": ls[1:], "block": block})
    return out


def _status_of(block):
    m = None
    for m in _STATUS_RE.finditer(block):
        pass
    return m.group("v") if m else ""


def find_stale_entry(text, who, old, new):
    """The last entry (open or placed zone) of the same person (`who` as the title writes it) whose quote words
    (normalized, at least 6 characters) are inside the old text and not inside the new one; None when there is none.
    Returns {zone, index, title, title_text, lines, quote, status, open, key}."""
    n_old, n_new = _norm(old), _norm(new)
    found = None
    for b in _blocks(text):
        if (" · %s · " % who) not in (" " + b["title"] + " "):
            continue
        q = quote_of_line(b["lines"][0] if b["lines"] else "")
        nq = _norm(q) if q is not None else ""
        if len(nq) < 6 or nq not in n_old or nq in n_new:
            continue
        status = _status_of(b["block"])
        tail = b["title"].split(" · %s · " % who, 1)
        found = {"zone": b["zone"], "index": b["index"], "title": b["title"], "title_text": tail[1] if len(tail) > 1 else b["title"],
                 "lines": b["lines"], "quote": q, "status": status,
                 "open": b["zone"] == "open" and status in ("open",), "key": entry_key(b)}
    return found


def _base_title(title_text):
    """A title text without any correction prefix in front of it, so a correction of a correction says the original
    one line once instead of stacking "✏️ ... · this title was written from the old version ...: ✏️ ..."."""
    t = str(title_text or "").strip()
    heads = []
    for L in ("zh", "en"):
        heads.append(MARKS[L]["edited"] + " · ")
        heads.append(MARKS[L]["stale_title"].split("%s")[0])
    changed = True
    while changed:
        changed = False
        for h in heads:
            if h and t.startswith(h):
                t = t[len(h):].lstrip()
                changed = True
    return t


def render_correction(stale, edit, jid, name, world, people_note=None, source="wa", today=None, owner_label="", lang="zh",
                      world_label=None):
    """The program written entry for an edit no capture of the run covered: the older entry's title, the message's
    current stored text (🗣, its own time), the older quote, and an open status. The older entry is never touched."""
    L = _lang(lang)
    M = MARKS[L]
    today = today or dt.date.today().isoformat()
    who, dossier_name = _who(name, people_note, L)
    where = M["stale_open"] if stale.get("open") else M["stale_status"] % (stale.get("status") or M["none"])
    current = {"how": "exact", "src": "text", "text": _cut(edit.get("new")), "at": str(edit.get("at") or "")}
    return "\n".join([
        "### %s · [%s] · %s · %s · %s" % (today, source, who, M["edited"], M["stale_title"] % _one_line(_base_title(stale.get("title_text")), TEXT_MAX)),
        quote_line(current, "", "", L),
        M["before_stale"] % (_quoted(_cut(stale.get("quote")), L), where),
        "- %s: %s" % (_owner(owner_label, M), M["recheck"]),
        "- %s: (%s · %s) %s" % (M["guess"], world_label or world or "-", M["correction"], M["merge"]),
        "- %s: `dossier.py prepare --name %s`" % (M["dig"], dossier_name or name or "?"),
        M["open"],
    ])


# ---------------------------------------------------------------- file plumbing (locked, checked, atomic)

def _path(buffer):
    """A buffer path, or a cfg dict (its intake.buffer). Empty string when none."""
    if isinstance(buffer, dict):
        buffer = ((buffer.get("intake") or {}).get("buffer")) or ""
    return Path(os.path.expanduser(str(buffer))) if buffer else None


@contextlib.contextmanager
def _locked(buffer_path):
    p = Path(buffer_path)
    lock = p.parent / (".%s.lock" % p.name)
    fd = os.open(str(lock), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.time() + LOCK_WAIT_SECONDS
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                    raise
                if time.time() > deadline:
                    raise TimeoutError("intake buffer is locked by another writer for %ds: %s" % (LOCK_WAIT_SECONDS, lock))
                time.sleep(0.05)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _stat(p):
    try:
        st = os.stat(str(p))
        return st.st_mtime_ns, st.st_size, st.st_mode & 0o777
    except FileNotFoundError:
        return None, None, None


def _read(buffer_path):
    p = Path(buffer_path)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _replace_checked(p, new_text, expect):
    """Write new_text over p only if p's (mtime, size) is still `expect`. True when replaced."""
    p = Path(p)
    fd, tmp = tempfile.mkstemp(prefix="." + p.name + ".", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_text)
            f.flush()
            os.fsync(f.fileno())
        mtime, size, mode = _stat(p)
        os.chmod(tmp, mode if mode is not None else 0o600)
        if (mtime, size) != expect:
            os.unlink(tmp)
            return False
        os.replace(tmp, str(p))
        return True
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _rewrite(p, transform, attempts=5):
    """Read, transform, checked replace; retry when someone else changed the file meanwhile. Returns transform's extra."""
    for _ in range(attempts):
        mtime, size, _mode = _stat(p)
        text = _read(p)
        new_text, extra = transform(text)
        if _replace_checked(p, new_text, (mtime, size)):
            return extra
        time.sleep(0.2)
    raise RuntimeError("intake buffer kept changing while writing, gave up: %s" % p)


def _zones(text):
    """(open_zone, placed_zone or None): the placed zone starts at the `## 已归` / `## Placed` line."""
    m = _PLACED_RE.search(text)
    if not m:
        return text, None
    return text[:m.start()], text[m.start():]


def _entry_spans(open_zone):
    starts = [m.start() for m in _ENTRY_RE.finditer(open_zone)]
    return [(s, starts[i + 1] if i + 1 < len(starts) else len(open_zone)) for i, s in enumerate(starts)]


def _insert_before_placed(text, entry, lang):
    open_zone, placed = _zones(text)
    if placed is None:
        open_zone, placed = text, MARKS[_lang(lang)]["placed_hdr"] + "\n"
    open_zone = open_zone.rstrip("\n")
    return (open_zone + "\n\n" if open_zone else "") + entry.rstrip("\n") + "\n\n" + placed


def _ensure_seen_table(conn):
    """The table as earlier versions made it, then the columns this version adds when missing: key_kind (raw, norm,
    resolved, edit; NULL on older rows) and entry_key (the key of the entry the row's capture produced). Never drops,
    never rebuilds."""
    conn.execute("CREATE TABLE IF NOT EXISTS intake_seen (jid TEXT, quote_hash TEXT, at TEXT, PRIMARY KEY (jid, quote_hash))")
    have = set(r[1] for r in conn.execute("PRAGMA table_info(intake_seen)"))
    for col in ("key_kind", "entry_key"):
        if col not in have:
            conn.execute("ALTER TABLE intake_seen ADD COLUMN %s TEXT" % col)
    conn.commit()


def _seen_any(conn, jid, keys):
    return any(conn.execute("SELECT 1 FROM intake_seen WHERE jid=? AND quote_hash=?", (jid, h)).fetchone() for _k, h in keys)


def _record(conn, jid, keys, ekey):
    now = dt.datetime.now().isoformat(timespec="seconds")
    for kind, h in keys:
        conn.execute("INSERT OR IGNORE INTO intake_seen (jid, quote_hash, at, key_kind, entry_key) VALUES (?, ?, ?, ?, ?)",
                     (jid, h, now, kind, ekey))
    conn.commit()


def intake_settings(state_db=None, cfg=None):
    """{"enabled": True/False/None (unknown), "lang", "owner_label", "buffer", "cfg"} from cfg, else from the
    config.json next to the state database."""
    c = cfg
    if c is None and state_db:
        sib = Path(os.path.expanduser(str(state_db))).parent / "config.json"
        if sib.is_file():
            try:
                c = W.load_config(sib)
            except (OSError, ValueError):
                c = None
    if c is None:
        return {"enabled": None, "lang": "zh", "owner_label": "", "buffer": "", "cfg": None}
    it = c.get("intake") or {}
    return {"enabled": bool(it.get("enabled")), "lang": W.owner_lang(c), "owner_label": str(c.get("owner_label") or ""),
            "buffer": str(it.get("buffer") or ""), "cfg": c}


def _clean_edits(edits):
    """[{pk, old, new, at}] with text; an edit whose normalized old equals its normalized new (spacing, a comma) is
    no edit at all."""
    out = []
    for e in edits if isinstance(edits, list) else []:
        if not isinstance(e, dict):
            continue
        old, new = str(e.get("old") or ""), str(e.get("new") or "")
        if not new.strip() or _norm(old) == _norm(new):
            continue
        out.append({"pk": e.get("pk"), "old": old, "new": new, "at": str(e.get("at") or "")})
    return out


def append_captures(state_db, buffer_path, jid, name, world, captures, source="wa", people_note=None, today=None, cfg=None,
                    messages=None, edits=None):
    """Validated captures appended before the placed zone. Returns how many entries were added (corrections included).
    A no-op returning 0 when intake is disabled, there is nothing to write, or no buffer is set. Raises
    FileNotFoundError when the buffer's folder does not exist. Existing entries and statuses never change.

    messages: the run's own pack for this conversation (resolve_quote's shape); None means the quotes are written as
    not checked. edits: [{pk, old, new, at}] messages the person edited after an earlier run judged them; a capture
    whose message is one of them is written with the ✏️ title and a before line, an edit no capture covers gets a
    correction entry when an older entry of the same person was written from the old words (find_stale_entry), and
    nothing otherwise. Corrections do not count against the brain's two per run.
    Dedupe: the raw quote key (as earlier versions wrote it), the normalized quote key, and when resolved the
    normalized stored text key; any one seen skips the capture, all are recorded after."""
    caps = validate_captures(captures)
    eds = _clean_edits(edits)
    if not caps and not eds:
        return 0
    st = intake_settings(state_db, cfg)
    if st["enabled"] is False:
        return 0
    p = _path(buffer_path) or _path(st["buffer"])
    if p is None:
        return 0
    if not p.parent.is_dir():
        raise FileNotFoundError("intake buffer folder not found: %s" % p.parent)
    lang = st["lang"]
    world_label = W.vocab_label(st["cfg"], "world", world, lang) if (st["cfg"] and world) else None
    verify = isinstance(messages, list)
    by_pk = dict((str(e["pk"]), e) for e in eds)
    conn = sqlite3.connect(str(state_db), timeout=10)
    added = 0
    try:
        _ensure_seen_table(conn)
        with _locked(p):
            covered = set()
            for cap in caps:
                res = None
                if verify:
                    try:
                        res = resolve_quote(cap["quote"], messages, cap.get("quote_at"))
                    except Exception:
                        res = None          # the check raised: written as not checked, never as found
                keys = [("raw", quote_hash(jid, cap["quote"])), ("norm", quote_hash_norm(jid, cap["quote"]))]
                edit = None
                if res is not None and res["how"] in RESOLVED:
                    keys.append(("resolved", quote_hash_norm(jid, res["text"])))
                    for pk in res["pks"]:
                        if str(pk) in by_pk:
                            covered.add(str(pk))
                            edit = edit or by_pk[str(pk)]
                if edit is not None:
                    keys.append(("edit", quote_hash_norm(jid, "edit:%s:%s" % (edit["pk"], edit["new"]))))
                if _seen_any(conn, jid, keys):
                    continue
                entry = render_entry(cap, jid, name, world, people_note=people_note, source=source, today=today,
                                     owner_label=st["owner_label"], lang=lang, world_label=world_label, resolved=res, edit=edit)
                _rewrite(p, lambda text: (_insert_before_placed(text, entry, lang), None))
                _record(conn, jid, keys, entry_key(entry))
                added += 1
            who = _who(name, people_note, lang)[0]
            for e in eds:
                if str(e["pk"]) in covered:
                    continue
                keys = [("edit", quote_hash_norm(jid, "edit:%s:%s" % (e["pk"], e["new"])))]
                if _seen_any(conn, jid, keys):
                    continue
                stale = find_stale_entry(_read(p), who, e["old"], e["new"])
                if stale is None:
                    continue
                entry = render_correction(stale, e, jid, name, world, people_note=people_note, source=source, today=today,
                                          owner_label=st["owner_label"], lang=lang, world_label=world_label)
                _rewrite(p, lambda text: (_insert_before_placed(text, entry, lang), None))
                _record(conn, jid, keys, entry_key(entry))
                added += 1
    finally:
        conn.close()
    return added


# ---------------------------------------------------------------- reading and placing

def list_entries(buffer):
    """Every `### ` section before the placed zone: [{index (0 based), title, lines, open, lang, key}]."""
    p = _path(buffer)
    if p is None or not p.exists():
        return []
    open_zone, _ = _zones(_read(p))
    out = []
    for i, (s, e) in enumerate(_entry_spans(open_zone)):
        lines = open_zone[s:e].rstrip("\n").split("\n")
        stripped = [x.strip() for x in lines]
        lang = "zh" if OPEN_LINES["zh"] in stripped else ("en" if OPEN_LINES["en"] in stripped else None)
        d = {"index": i, "title": lines[0][4:].strip(), "lines": lines[1:], "open": lang is not None, "lang": lang}
        d["key"] = entry_key(d)
        out.append(d)
    return out


def list_open(buffer):
    """Open entries only: [{title, lines, index, key}] (index as in list_entries, 0 based)."""
    return [{"title": e["title"], "lines": e["lines"], "index": e["index"], "key": e["key"]} for e in list_entries(buffer) if e["open"]]


def list_placed(buffer):
    """The placed zone, read only: [{index (0 based within the zone), title, lines, placed_to, key}]; placed_to from
    "状态: 已归 → X" or "status: placed → X", else ""."""
    p = _path(buffer)
    if p is None or not p.exists():
        return []
    out = []
    for b in _blocks(_read(p)):
        if b["zone"] != "placed":
            continue
        m = _PLACED_TO_RE.search(b["block"])
        out.append({"index": b["index"], "title": b["title"], "lines": b["lines"], "placed_to": m.group("to") if m else "",
                    "key": entry_key(b)})
    return out


def _find_key(text, key):
    """(zone, index, block dict) of the entry with this full key in a buffer text, else None."""
    k = str(key or "").strip().lower()
    for b in _blocks(text):
        if entry_key(b) == k:
            return b
    return None


def _is_open_block(block):
    return any(re.search(r"^[ \t]*%s[ \t]*$" % re.escape(OPEN_LINES[L]), block, re.M) for L in ("zh", "en"))


def classify_keys(buffer, keys):
    """{key: (state, placed_to)} with state open | placed | missing. Read only, no lock. An entry above the placed
    header that carries a placed status line counts as placed."""
    p = _path(buffer)
    text = _read(p) if (p is not None and p.exists()) else ""
    by_key = {}
    for b in _blocks(text):
        by_key.setdefault(entry_key(b), b)
    out = {}
    for key in keys or []:
        b = by_key.get(str(key or "").strip().lower())
        if b is None:
            out[key] = ("missing", "")
            continue
        if b["zone"] == "open" and _is_open_block(b["block"]):
            out[key] = ("open", "")
            continue
        m = _PLACED_TO_RE.search(b["block"])
        out[key] = ("placed", m.group("to") if m else "") if (b["zone"] == "placed" or m) else ("missing", "")
    return out


def count_open(buffer):
    """Sections before the placed zone that carry an open status line; 0 when the file does not exist.
    The one rule for counting (the digest and the command-base morning line call this)."""
    return sum(1 for e in list_entries(buffer) if e["open"])


def format_listing(entries, keys=False):
    out = []
    for e in entries:
        out.append("%d. %s" % (e["index"] + 1, e["title"]))
        for line in e["lines"]:
            out.append("    " + line)
        if keys and e.get("key"):
            out.append("    key: %s" % e["key"][:12])
    return "\n".join(out)


def _mark_placed_in_text(text, index, to):
    """Pure: entry `index` (0 based, before the placed zone) gets the placed status and moves under the placed header.
    Returns (new_text, moved). IndexError when there is no such entry, ValueError when it is not open. Index mode and
    key mode both go through here, so they cannot disagree."""
    where = str(to or "").strip()
    open_zone, placed = _zones(text)
    spans = _entry_spans(open_zone)
    if index < 0 or index >= len(spans):
        raise IndexError("the intake buffer has no entry %d before the placed zone (it has %d)" % (index, len(spans)))
    s, e = spans[index]
    block = open_zone[s:e]
    lang = None
    for L in ("zh", "en"):
        if re.search(r"^[ \t]*%s[ \t]*$" % re.escape(OPEN_LINES[L]), block, re.M):
            lang = L
            break
    if lang is None:
        raise ValueError("entry number %d is not open, it cannot be placed again" % (index + 1))
    moved = re.sub(r"^([ \t]*)%s[ \t]*$" % re.escape(OPEN_LINES[lang]),
                   lambda m: m.group(1) + MARKS[lang]["placed"] % where, block.rstrip("\n"), count=1, flags=re.M)
    new_open = open_zone[:s] + open_zone[e:]
    pl = placed if placed is not None else MARKS[lang]["placed_hdr"] + "\n"
    if not pl.endswith("\n"):
        pl += "\n"
    return new_open + pl + "\n" + moved + "\n", moved


def mark_placed(buffer, n, to):
    """Command-base intake mode only, never the daemon. n is the 0 based index from list_open()/list_entries()
    (the CLI's --mark-placed takes the 1 based number and subtracts one). The entry's open status becomes placed and the
    whole section moves under the placed header; nothing else changes. Returns the moved text."""
    p = _path(buffer)
    if p is None or not p.exists():
        raise FileNotFoundError("intake buffer not found: %s" % p)
    where = str(to or "").strip()
    if not where:
        raise ValueError("mark_placed needs a destination (to)")
    with _locked(p):
        return _rewrite(p, lambda text: _mark_placed_in_text(text, n, where))


def _key_state(text, key):
    b = _find_key(text, key)
    if b is None:
        return {"status": "not_found"}
    if b["zone"] == "placed":
        m = _PLACED_TO_RE.search(b["block"])
        return {"status": "already_placed", "placed_to": m.group("to") if m else ""}
    if not _is_open_block(b["block"]):
        m = _PLACED_TO_RE.search(b["block"])
        if m:
            return {"status": "already_placed", "placed_to": m.group("to")}
        return {"status": "not_open", "index": b["index"]}
    return {"status": "open", "index": b["index"]}


def mark_placed_by_key(buffer, key, to):
    """mark_placed by entry key (a full key): the index is resolved inside the same lock as the write, so a buffer
    that shifted since the listing cannot move the wrong entry. Returns {"status": "moved", "index", "moved"} |
    {"status": "already_placed", "placed_to"} | {"status": "not_open", "index"} | {"status": "not_found"}."""
    p = _path(buffer)
    if p is None or not p.exists():
        raise FileNotFoundError("intake buffer not found: %s" % p)
    where = str(to or "").strip()
    if not where:
        raise ValueError("mark_placed_by_key needs a destination (to)")
    with _locked(p):
        first = _key_state(_read(p), key)
        if first["status"] != "open":
            return first

        def transform(text):
            stt = _key_state(text, key)
            if stt["status"] != "open":
                return text, stt
            new_text, moved = _mark_placed_in_text(text, stt["index"], where)
            return new_text, {"status": "moved", "index": stt["index"], "moved": moved}
        return _rewrite(p, transform)


def _append_in_text(text, key, line):
    """Pure: `line` becomes the last line of the entry block with this full key, in either zone. Returns
    (new_text, {"status": "appended", "zone", "index"}) or (text, {"status": "not_found"}). Blank lines after the block
    and every other byte stay as they were; the entry's key does not change (only its title and quote line make it)."""
    k = str(key or "").strip().lower()
    open_zone, placed = _zones(text)
    for zone, base, part in (("open", 0, open_zone), ("placed", len(open_zone), placed or "")):
        for i, (s, e) in enumerate(_entry_spans(part)):
            block = part[s:e].rstrip("\n")
            if entry_key(block) != k:
                continue
            if "\n" not in block:
                raise ValueError("the entry has no quote line; a line appended to it would change its key")
            at = base + s + len(block)
            return text[:at] + "\n" + line + text[at:], {"status": "appended", "zone": zone, "index": i}
    return text, {"status": "not_found"}


def append_under_key(buffer, key, line):
    """One line added as the last line of the entry with this full key, open or placed, under the buffer lock with the
    key resolved inside it (the undo of a vault write records itself this way; the entry stays where it is). `line` is
    one line: a newline inside it, or a line that would start a new section (`#`), is a ValueError. Returns
    {"status": "appended"} | {"status": "not_found"} (the file is untouched when not found)."""
    p = _path(buffer)
    if p is None or not p.exists():
        raise FileNotFoundError("intake buffer not found: %s" % p)
    one = str(line if line is not None else "").rstrip()
    if not one.strip() or "\n" in one or "\r" in one:
        raise ValueError("append_under_key takes one line that is not empty")
    if one.lstrip().startswith("#"):
        raise ValueError("append_under_key: a line starting with # would start a new section of the buffer")
    with _locked(p):
        if _find_key(_read(p), key) is None:
            return {"status": "not_found"}

        def transform(text):
            new_text, res = _append_in_text(text, key, one)
            return new_text, res
        res = _rewrite(p, transform)
    return {"status": res["status"]}


def all_keys(buffer):
    """Every entry key in the buffer, both zones, file order (for prefix lookup)."""
    p = _path(buffer)
    if p is None or not p.exists():
        return []
    return [entry_key(b) for b in _blocks(_read(p))]


def resolve_key_prefix(buffer, prefix):
    """A full key from a full key or a unique prefix of at least 8 hex. ValueError naming the count otherwise."""
    pre = str(prefix or "").strip().lower()
    if not re.match(r"^[0-9a-f]{8,40}$", pre):
        raise ValueError("a key is at least 8 hex characters: %r" % prefix)
    hits = sorted(set(k for k in all_keys(buffer) if k.startswith(pre)))
    if len(hits) != 1:
        raise ValueError("key %s matches %d entries (run --list-open again)" % (pre, len(hits)))
    return hits[0]


# ---------------------------------------------------------------- the board's Today brief (read through its API)

BRIEF_TIMEOUT = 8
BRIEF_PATH = "/api/today"
CARD_LIVE = ("pending", "ok")
NOW_MAX = 160
BRIEF_LABELS = {
    "zh": {"board": "看板", "proposal": "提议", "note": "你的备注", "none": "看板: 这条没有卡, 照旧自己判断",
           "unread": "看板: 读不到简报 (%s), 照旧逐条判断", "k_head": "看板另有 %d 张卡没有对应的 intake 条目 (最早 %s):",
           "status": {"pending": "等你按", "ok": "你按了好", "no": "你按了不用", "superseded": "已收起", "expired": "过期"},
           "replied": "已收起 (你回过讯息)",
           "kinds": {"update": "更新任务", "create": "新任务", "seed": "新 Project 苗头", "drop": "不用归"},
           "unassigned": "还没 Project"},
    "en": {"board": "board", "proposal": "proposal", "note": "your note", "none": "board: no card, judge it as before",
           "unread": "board: could not read the brief (%s); judging entry by entry as before",
           "k_head": "The board has %d more card(s) with no intake entry (oldest %s):",
           "status": {"pending": "waiting for your press", "ok": "you pressed OK", "no": "you pressed No",
                      "superseded": "retired", "expired": "expired"},
           "replied": "retired (you replied)",
           "kinds": {"update": "update task", "create": "new task", "seed": "project seed", "drop": "drop"},
           "unassigned": "no project yet"},
}
SETTLE_WHY = {
    "rev": "the card changed since you listed it%s; run --list-open --with-brief again and look at it before settling",
    "keys": "this card belongs to intake entries; it retires by itself within a minute when its entries are placed",
    "ambiguous": "that id prefix matches more than one card; give more of the id",
    "not_found": "no card with that id",
    "to": "--to is empty; say where it went",
    "status": "the card is not open any more (already retired or expired)",
    "disabled": "the Today brief is off on this board (today.enabled is false)",
    "dry_run": "the daemon runs in dry run mode; the brief is off",
}


def brief_url_ok(url):
    """The board's brief URL with its trailing slash removed. ValueError unless it is http(s) and ends in /api/today."""
    u = str(url or "").strip().rstrip("/")
    if not re.match(r"^https?://[^/\s]+", u):
        raise ValueError("--with-brief needs the board's http address, like http://127.0.0.1:<port>/api/today: %r" % url)
    if not u.endswith(BRIEF_PATH):
        raise ValueError("--with-brief must end in %s (the board's brief), got %r" % (BRIEF_PATH, url))
    return u


def settle_url(brief_url):
    """<.../api/today>/settle; ValueError (before any request) when the URL is not the brief's."""
    return brief_url_ok(brief_url) + "/settle"


def _http_json(url, body=None, timeout=BRIEF_TIMEOUT):
    """(status, parsed JSON or None, reason). GET, or POST with a JSON body. No proxy (the board is local or on the
    tailnet). An HTTP error that carries a JSON body is returned like an answer, so a refusal keeps its reason."""
    import urllib.error
    import urllib.request
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET",
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            status, raw = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, (e.read() or b"")
    except Exception as e:      # refused, timeout, DNS: one short reason
        reason = getattr(e, "reason", None) or e
        return None, None, "%s: %s" % (type(e).__name__, str(reason)[:120])
    try:
        js = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return status, None, "HTTP %d, not JSON" % status
    return status, js, "" if (status or 0) < 400 else "HTTP %d" % status


def fetch_brief(url, timeout=BRIEF_TIMEOUT):
    """(payload, "") or (None, reason). Fetched once; anything that is not the brief's JSON object is a reason."""
    try:
        u = brief_url_ok(url)
    except ValueError as e:
        return None, str(e)
    status, js, why = _http_json(u, timeout=timeout)
    if js is None:
        return None, why
    if not isinstance(js, dict) or js.get("ok") is not True or not isinstance(js.get("projects", []), list):
        return None, why or "the answer is not the brief (%s)" % (str(js.get("reason") or js.get("error") or "no projects")
                                                                    if isinstance(js, dict) else type(js).__name__)
    return js, ""


def brief_cards(payload):
    """Every card of a brief payload, flattened: [card dict + "block" (the block's name)]."""
    out = []
    for blk in (payload or {}).get("projects") or []:
        if not isinstance(blk, dict):
            continue
        name = str(blk.get("name") or blk.get("id") or "").strip()
        for c in blk.get("props") or []:
            if isinstance(c, dict):
                d = dict(c)
                d["block"] = name
                d["block_id"] = str(blk.get("id") or "")
                out.append(d)
    return out


def _card_keys(card):
    return [str(k).strip().lower() for k in (card.get("entry_keys") or []) if str(k or "").strip()]


def _key_match(card_key, entry_key_):
    a, b = str(card_key or "").lower(), str(entry_key_ or "").lower()
    if not a or not b:
        return False
    return a == b or (len(a) >= 8 and b.startswith(a)) or (len(b) >= 8 and a.startswith(b))


def _retired_replied_today(card, day):
    if card.get("status") != "superseded" or card.get("retired_why") != "replied":
        return False
    when = str(card.get("retired_at") or card.get("date") or "")
    return bool(day) and when[:10] == day


def match_cards(entries, payload):
    """(by_entry, k_cards): by_entry maps an entry's key to the cards whose entry_keys hold it (live cards, and cards
    retired today because the owner replied); k_cards are the live cards with no entry keys, oldest first."""
    cards = brief_cards(payload)
    day = str((payload or {}).get("date") or "")
    by_entry = {}
    for e in entries:
        k = e.get("key")
        by_entry[k] = [c for c in cards if (c.get("status") in CARD_LIVE or _retired_replied_today(c, day))
                       and any(_key_match(ck, k) for ck in _card_keys(c))]
    k_cards = [c for c in cards if c.get("status") in CARD_LIVE and not _card_keys(c)]
    k_cards.sort(key=lambda c: (str(c.get("date") or ""), str(c.get("id") or "")))
    return by_entry, k_cards


def _mmdd(day):
    m = re.match(r"^\d{4}-(\d{2})-(\d{2})", str(day or ""))
    return "%s-%s" % m.groups() if m else (str(day or "") or "?")


def _card_status(card, B):
    if card.get("status") == "superseded" and card.get("retired_why") == "replied":
        return B["replied"]
    return B["status"].get(str(card.get("status") or ""), str(card.get("status") or ""))


def _card_head(card, B):
    block = card.get("block") or ""
    if not block or card.get("block_id") == "unassigned":
        block = block if block and block != "unassigned" else B["unassigned"]
    kind = str(card.get("kindL") or B["kinds"].get(str(card.get("kind") or ""), str(card.get("kind") or "")))
    what = " ".join(x for x in (kind, str(card.get("task") or "").strip()) if x)
    return block, what


def _card_body(card, B):
    out = []
    now = _one_line(card.get("now"), 10000)
    if now:
        out.append("      %s: %s" % (B["proposal"], now if len(now) <= NOW_MAX else now[:NOW_MAX - 1] + "…"))
    note = _one_line(card.get("note"), 500)
    if note:
        out.append("      %s: %s" % (B["note"], note))
    for k in ("placed_note", "replied_note", "close_note"):
        v = _one_line(card.get(k), 300)
        if v:
            out.append("      " + v)
    return out


def format_brief_listing(entries, payload, lang="zh", why=""):
    """The --list-open --with-brief text: the normal listing with the board's cards under each entry, then the K cards.
    payload None: one line saying why the brief could not be read, then the normal listing."""
    B = BRIEF_LABELS[_lang(lang)]
    if payload is None:
        head = B["unread"] % (why or "?")
        listing = format_listing(entries, keys=True)
        return head + ("\n" + listing if listing else "")
    by_entry, k_cards = match_cards(entries, payload)
    out = []
    for e in entries:
        out.append(format_listing([e], keys=True))
        cards = by_entry.get(e.get("key")) or []
        if not cards:
            out.append("    " + B["none"])
        for c in cards:
            block, what = _card_head(c, B)
            out.append("    %s: %s · %s · %s" % (B["board"], block, what, _card_status(c, B)))
            out.extend(_card_body(c, B))
    if k_cards:
        out.append("")
        out.append(B["k_head"] % (len(k_cards), _mmdd(k_cards[0].get("date"))))
        for i, c in enumerate(k_cards):
            block, what = _card_head(c, B)
            out.append("K%d. %s · %s · %s · %s · id %s rev %s" % (i + 1, _mmdd(c.get("date")), block, what, _card_status(c, B),
                                                               str(c.get("id") or "")[:8], c.get("rev")))
            out.extend(_card_body(c, B))
    return "\n".join(out)


def brief_json(entries, payload, why=""):
    """The --list-open --json --with-brief object: {"entries": [entry + n + cards], "k_cards": [...], "brief": {...}}."""
    by_entry, k_cards = match_cards(entries, payload) if payload is not None else ({}, [])
    return {"entries": [dict(e, n=e["index"] + 1, cards=by_entry.get(e.get("key")) or []) for e in entries],
            "k_cards": k_cards,
            "brief": {"read": payload is not None, "why": why, "date": (payload or {}).get("date"),
                      "stale": bool((payload or {}).get("stale")), "built_at": (payload or {}).get("built_at")}}


def settle_card(brief_url, card_id, rev, to, timeout=BRIEF_TIMEOUT):
    """POST <brief_url>/settle {id, rev, to}. Returns (settled bool, one answer line). ValueError for a URL that is not
    the brief's (raised before any request)."""
    url = settle_url(brief_url)
    status, js, why = _http_json(url, {"id": str(card_id), "rev": rev, "to": str(to)}, timeout=timeout)
    if js is None:
        return False, "refused: could not reach the board (%s)" % why
    if not isinstance(js, dict):
        return False, "refused: the board answered something that is not an object"
    if js.get("ok") is True:
        return True, "settled: card %s → %s" % (str(js.get("id") or card_id)[:8], to)
    reason = str(js.get("reason") or js.get("error") or why or "unknown")
    msg = SETTLE_WHY.get(reason, "")
    if reason == "rev":
        cur = js.get("current") if isinstance(js.get("current"), dict) else {}
        msg = msg % ((" (it is at rev %s now)" % cur.get("rev")) if cur.get("rev") is not None else "")
    return False, "refused: %s%s" % (reason, (": " + msg) if msg else "")


# ---------------------------------------------------------------- evening push support (retired in v1.3.0)

def evening_prompt(cfg, listing, n_open, rules="", lang="zh", today=None):
    owner = str((cfg or {}).get("owner_label") or "").strip() or "the owner"
    places = rules.strip() if rules and rules.strip() else "\n".join("- " + x for x in DEFAULT_PLACES)
    out_lang = "Simplified Chinese" if _lang(lang) == "zh" else "English"
    return (
        "Today is %s. Below are the %d open entries in %s's intake buffer: things captured from WhatsApp that someone "
        "asked of %s, or that %s promised.\n"
        "Do one thing only: pre-sort them. Suggest one place for each entry. Do not create or edit any file; nothing is "
        "placed until %s answers in a session.\n\n"
        "Where an entry can go:\n%s\n\n"
        "Entries (keep these numbers):\n%s\n\n"
        "Output exactly one JSON object and nothing before or after it:\n"
        "{\"items\": [{\"n\": 1, \"who\": \"name\", \"what\": \"what it is, at most 12 words\", "
        "\"suggest\": \"one place\", \"why\": \"at most 10 words\"}]}\n"
        "Rules: one suggestion per entry; when unsure write \"%s\"; never invent a date or an amount that is not in the "
        "entry; write who, what, suggest and why in %s; no em dash, no double hyphen, no spaced hyphen used as a separator."
    ) % (today or dt.date.today().isoformat(), n_open, owner, owner, owner, owner, places, listing,
         EVENING_LABELS[_lang(lang)]["ask"], out_lang)


def evening_status(cfg):
    """Retired with the evening push (kept for an evening job installed before v1.3.0; intake.evening_push is read with
    a default because wa_common no longer fills it). Everything the evening job's script needs, as one JSON-able dict (defaults already applied here, so the
    node script never guesses): run and why, the open count and listing, the prompt, the claude binary, model,
    settings path, cwd, PATH, and the labels for the pushed message."""
    it = cfg.get("intake") or {}
    lang = W.owner_lang(cfg)
    live = W.live_dir(cfg)
    try:   # launchd opened the evening log with the default umask (0644) before this ran; it names entries, so owner only
        os.chmod(str(live / "intake-evening.log"), 0o600)
    except OSError:
        pass
    settings = live / "claude-settings.json"
    out = {"run": True, "why": "", "open": 0, "listing": "", "prompt": "", "buffer": str(it.get("buffer") or ""),
           "claude": W.resolve_bin(cfg, "claude"), "model": str((cfg.get("brain") or {}).get("model") or "claude-sonnet-5"),
           "settings": str(settings), "settings_exists": settings.is_file(),
           "cwd": str((cfg.get("brain") or {}).get("cwd") or W.home_dir(cfg)), "path_env": W.subprocess_env(cfg)["PATH"],
           "max_turns": 4, "timeout_seconds": 300, "max_items": 12, "lang": lang, "labels": EVENING_LABELS[lang],
           "outlet": str((cfg.get("notify") or {}).get("outlet") or "none"), "mode": str((cfg.get("notify") or {}).get("mode") or "errors")}
    if not it.get("enabled"):
        out.update(run=False, why="intake.enabled is false, evening push skipped")
    elif not it.get("evening_push", False):     # retired key, read with its default
        out.update(run=False, why="intake.evening_push is false, evening push skipped")
    elif not out["buffer"]:
        out.update(run=False, why="intake.buffer is not set, evening push skipped")
    elif not Path(out["buffer"]).is_file():
        out.update(run=False, why="intake buffer not found: %s" % out["buffer"])
    elif out["outlet"] == "none" or not W.notify_wanted(cfg, "digest"):
        out.update(run=False, why="notify.outlet is none or notify.mode is off, evening push skipped")
    if not out["run"]:
        return out
    entries = list_open(out["buffer"])
    out["open"] = len(entries)
    out["listing"] = format_listing(entries)
    rules = ""
    rf = str(it.get("rules_file") or "")
    if rf:
        try:
            rules = Path(rf).read_text(encoding="utf-8")[:6000]
        except OSError as e:
            out["why"] = "intake.rules_file unreadable (%s), default places used" % e
    out["prompt"] = evening_prompt(cfg, out["listing"], out["open"], rules, lang)
    return out


# ---------------------------------------------------------------- selftest

def _selftest():
    check = W._Checks("wa_intake")
    tmp = Path(tempfile.mkdtemp(prefix="wa_intake_selftest."))
    try:
        buf = tmp / "intake-buffer.md"
        db = tmp / "state.sqlite"
        preamble = "Machine intake for the selftest (fake file).\n\n"
        old_entry = ("### 2026-09-13 · [wa] · Dana Park (无档) · an older entry\n- 🗣 09-13 10:00「older words」\n"
                     "- 我这边: 待归位\n- 机器猜: (other · 钱) 无\n- 深挖: `dossier.py prepare --name Dana Park`\n- 状态: open\n")
        buf.write_text(preamble + old_entry, encoding="utf-8")
        raw = [
            {"kind": "request_dated", "text": "wants two seats held for the October class before the 20th",
             "quote": "can you hold two seats for me before the 20th", "quote_at": "2026-09-14T16:53:00", "deadline": "09-20",
             "guess": "task under the partner project"},
            {"kind": "gossip", "text": "not a legal kind", "quote": "just chatting"},
            {"kind": "money", "text": "", "quote": "empty text must be dropped"},
            {"kind": "appointment", "text": "meet on the 18th at 3pm", "quote": "18th 3pm at the cafe?", "quote_at": "09-14 17:02"},
            {"kind": "decision", "text": "a third legal one is cut", "quote": "we decided not to come"},
        ]
        caps = validate_captures(raw)
        check(len(caps) == 2 and [c["kind"] for c in caps] == ["request_dated", "appointment"],
              "validate: five in, one bad kind and one empty text dropped, cut to two")
        check(validate_captures("not a list") == [] and validate_captures(None) == [], "validate: not a list gives []")
        check(len(validate_captures([{"kind": "money", "text": "x" * 300, "quote": "y" * 300}])[0]["text"]) == 120, "validate: text cut to 120")

        alex_pack = [{"pk": 11, "at": "2026-09-14 16:53:00", "me": False, "speaker": "Alex", "text": "hey, can you hold two seats for me before the 20th"},
                     {"pk": 12, "at": "2026-09-14 17:02:00", "me": False, "speaker": "Alex", "text": "18th 3pm at the cafe?"}]
        n1 = append_captures(db, buf, "60120000001@s.whatsapp.net", "Alex Rivera", "work", caps,
                             people_note=Path("/x/People/Alex-Rivera.md"), today="2026-09-15", messages=alex_pack)
        check(n1 == 2, "append: two added (no config next to the database, so enabled is not known and the call proceeds)")
        n2 = append_captures(db, buf, "60120000001@s.whatsapp.net", "Alex Rivera", "work", caps,
                             people_note=Path("/x/People/Alex-Rivera.md"), today="2026-09-15", messages=alex_pack)
        check(n2 == 0, "append: the same quotes again add nothing (dedupe)")
        n3 = append_captures(db, buf, "60120000002@s.whatsapp.net", "Blake Chen", "work",
                             [{"kind": "promise_undelivered", "text": "owner promised a proposal", "quote": "ok waiting for your proposal"}],
                             today="2026-09-15")
        check(n3 == 1, "append: another conversation adds its own entry")
        text = buf.read_text(encoding="utf-8")
        check(text.startswith(preamble + old_entry.rstrip("\n")), "existing preamble and entry untouched at the top")
        check(text.rstrip("\n").endswith("## 已归"), "placed header created at the end")
        check("### 2026-09-15 · [wa] · [[Alex-Rivera]] · wants two seats held for the October class before the 20th" in text, "with a note: [[Note]] title")
        check("- 🗣 09-14 16:53「can you hold two seats for me before the 20th」" in text, "quote_at ISO becomes MM-DD HH:MM")
        check("- 我这边: 期限 09-20" in text and "- 我这边: 答应了没交" in text, "owner line without owner_label, deadline and promise")
        check("- 机器猜: (work · 有日期的请求) task under the partner project" in text, "guess line with world and kind label")
        check("### 2026-09-15 · [wa] · Blake Chen (无档) · owner promised a proposal" in text and "- 机器猜: (work · 答应了没交) 无" in text,
              "without a note: plain name, guess none")
        check("- ≈ 未核对「ok waiting for your proposal」" in text, "no quote_at and no pack: no time, the not checked mark")
        check(count_open(buf) == 4 and len(list_open(buf)) == 4 and list_open(buf)[1]["title"].startswith("2026-09-15 · [wa] · [[Alex-Rivera]]"),
              "count_open and list_open see four")

        moved = mark_placed(buf, 1, "task [[Partner-Codes]]")
        after = buf.read_text(encoding="utf-8")
        check("- 状态: 已归 → task [[Partner-Codes]]" in moved and "- 状态: open" not in moved, "mark_placed rewrites the status")
        check(after.index("## 已归") < after.index("- 状态: 已归 → task [[Partner-Codes]]") and count_open(buf) == 3,
              "placed entry moved under the header, three open")
        check(after.startswith(preamble + old_entry.rstrip("\n")), "top untouched after mark_placed")
        try:
            mark_placed(buf, 99, "drop")
            check(False, "out of range raises")
        except IndexError:
            check(buf.read_text(encoding="utf-8") == after, "out of range raises IndexError and leaves the file alone")

        # the switch: explicit cfg, then the config.json next to the state database
        off = {"intake": {"enabled": False, "buffer": str(buf)}}
        check(append_captures(db, buf, "60120000003@s.whatsapp.net", "Casey Ng", "work",
                              [{"kind": "money", "text": "deposit due", "quote": "deposit by friday"}], cfg=off) == 0,
              "intake.enabled=false via cfg: no-op returning 0")
        live = tmp / "live"
        live.mkdir()
        (live / "config.json").write_text(json.dumps({"intake": {"enabled": False}}), encoding="utf-8")
        check(append_captures(live / "state.sqlite", buf, "60120000003@s.whatsapp.net", "Casey Ng", "work",
                              [{"kind": "money", "text": "deposit due", "quote": "deposit by friday"}]) == 0
              and not (live / "state.sqlite").exists(), "intake.enabled=false in the live config next to the database: no-op, nothing touched")
        (live / "config.json").write_text(json.dumps({"intake": {"enabled": True}, "owner_label": "Alex Rivera", "board": {"lang": "en"},
                                                      "vocab": {"worlds": [{"key": "shop", "label": "店", "label_en": "Shop", "business": True}]}}),
                                          encoding="utf-8")
        ebuf = tmp / "en-buffer.md"
        ebuf.write_text("# Intake\n", encoding="utf-8")
        check(append_captures(live / "state.sqlite", ebuf, "60120000003@s.whatsapp.net", "Casey Ng", "shop",
                              [{"kind": "money", "text": "deposit due", "quote": "deposit by friday", "quote_at": "2026-09-14 09:05"}],
                              today="2026-09-15", messages=[{"pk": 31, "at": "2026-09-14 09:05:00", "me": False, "speaker": "Casey",
                                                             "text": "Deposit by Friday."}]) == 1, "enabled in the live config: appended")
        et = ebuf.read_text(encoding="utf-8")
        check("- Alex Rivera side: to place" in et and "- machine guess: (Shop · money) none" in et and "- status: open" in et
              and '- 🗣 09-14 09:05 "Deposit by Friday."' in et and et.rstrip().endswith("## Placed"),
              "English buffer: owner label, world label from vocab, English markers")
        mark_placed(ebuf, 0, "calendar 2026-09-19")
        check("- status: placed → calendar 2026-09-19" in ebuf.read_text(encoding="utf-8") and count_open(ebuf) == 0, "English mark_placed")
        try:
            append_captures(db, tmp / "no-folder" / "b.md", "j", "n", "w", [{"kind": "money", "text": "t", "quote": "q"}])
            check(False, "missing folder raises")
        except FileNotFoundError:
            check(not (tmp / "no-folder").exists(), "missing buffer folder raises FileNotFoundError and creates nothing")
        check(count_open(tmp / "absent.md") == 0 and list_open(None) == [], "count_open on a missing file is 0")

        # concurrent writers: the lock keeps every entry
        cbuf = tmp / "concurrent.md"
        cbuf.write_text("", encoding="utf-8")
        cdb = tmp / "concurrent.sqlite"
        errors = []

        def writer(tag):
            try:
                for i in range(8):
                    append_captures(cdb, cbuf, "jid-%s" % tag, "Writer %s" % tag, "work",
                                    [{"kind": "money", "text": "item %s %d" % (tag, i), "quote": "quote %s %d" % (tag, i)}])
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=writer, args=(t,)) for t in ("a", "b", "c")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        check(not errors and count_open(cbuf) == 24, "three concurrent writers, 24 entries, none lost")
        check((tmp / ".concurrent.md.lock").exists() and not [p for p in tmp.iterdir() if p.name.startswith(".concurrent.md.")
                                                              and not p.name.endswith(".lock")], "sidecar lock file, no temp left")

        # an edit between read and replace makes the write retry instead of overwriting it
        rbuf = tmp / "race.md"
        rbuf.write_text("start\n", encoding="utf-8")
        before = _stat(rbuf)
        time.sleep(0.01)
        rbuf.write_text("start\nedited in the editor\n", encoding="utf-8")
        check(_replace_checked(rbuf, "stale write\n", (before[0], before[1])) is False
              and rbuf.read_text(encoding="utf-8") == "start\nedited in the editor\n", "checked replace refuses when the file changed")

        conn = sqlite3.connect(str(db))
        rows = conn.execute("SELECT COUNT(DISTINCT entry_key) FROM intake_seen").fetchone()[0]
        unkinded = conn.execute("SELECT COUNT(*) FROM intake_seen WHERE key_kind IS NULL OR entry_key IS NULL").fetchone()[0]
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        conn.close()
        check(rows == 3 and unkinded == 0 and tables == ["intake_seen"],
              "state database holds only intake_seen, three entries, every row with key_kind and entry_key")

        # CLI and evening status
        me = str(Path(__file__).resolve())
        r = subprocess.run([sys.executable, me, "--buffer", str(buf), "--list-open"], capture_output=True, timeout=60)
        out = r.stdout.decode("utf-8")
        check(r.returncode == 0 and out.startswith("1. 2026-09-13 · [wa] · Dana Park") and "\n    - 状态: open" in out, "CLI --list-open numbered from 1")
        r = subprocess.run([sys.executable, me, "--buffer", str(buf), "--mark-placed", "1", "--to", "drop"], capture_output=True, timeout=60)
        check(r.returncode == 0 and count_open(buf) == 2, "CLI --mark-placed 1 places the first entry")
        cfgp = live / "config.json"
        cfgp.write_text(json.dumps({"intake": {"enabled": True, "buffer": str(buf), "evening_push": False},
                                    "notify": {"outlet": "command", "command": "cat > /dev/null"}}), encoding="utf-8")
        r = subprocess.run([sys.executable, me, "--config", str(cfgp), "--evening-status"], capture_output=True, timeout=60)
        st = json.loads(r.stdout.decode("utf-8"))
        check(st["run"] is False and "evening_push" in st["why"], "evening status: evening_push false means run false with a reason")
        cfgp.write_text(json.dumps({"intake": {"enabled": True, "buffer": str(buf), "evening_push": True}, "owner_label": "Alex Rivera",
                                    "notify": {"outlet": "command", "command": "cat > /dev/null"}}), encoding="utf-8")
        r = subprocess.run([sys.executable, me, "--config", str(cfgp), "--evening-status"], capture_output=True, timeout=60)
        st = json.loads(r.stdout.decode("utf-8"))
        check(st["run"] is True and st["open"] == 2 and "Alex Rivera" in st["prompt"] and st["listing"] in st["prompt"]
              and st["labels"]["title"].startswith("🗂"), "evening status: run, open count, prompt with listing and owner")
        r = subprocess.run([sys.executable, me, "--config", str(cfgp), "--count-open"], capture_output=True, timeout=60)
        check(r.stdout.decode("utf-8").strip() == "2", "CLI --count-open reads intake.buffer from the config")
        # ---- v1.2.0 · quotes checked by code against the run's own pack (invented chat, invented words)
        pack = [
            {"pk": 101, "at": "2026-10-02 09:10:00", "me": False, "speaker": "Robin", "text": "Could you hold the walnut table until Saturday? I can pay the deposit on Friday."},
            {"pk": 102, "at": "2026-10-02 09:12:00", "me": False, "speaker": "Robin", "text": "", "transcript": "I will come with my sister to see the lamps at four"},
            {"pk": 103, "at": "2026-10-02 09:14:00", "me": False, "speaker": "Robin", "text": "The second invoice"},
            {"pk": 104, "at": "2026-10-02 09:14:30", "me": False, "speaker": "Robin", "text": "needs the new address please"},
            {"pk": 105, "at": "2026-10-02 09:20:00", "me": False, "speaker": "Robin", "text": "Also the delivery van must arrive before noon on the twelfth, the building closes after that."},
            {"pk": 106, "at": "2026-10-02 09:25:00", "me": False, "speaker": "Robin", "text": "The sofa cushions should be dark green with piping on every edge"},
            {"pk": 107, "at": "2026-10-02 09:30:00", "me": True, "speaker": "", "text": "noted, thank you"},
        ]
        six = [("exact", "hold the walnut table until Saturday"), ("voice", "come with my sister to see the lamps at four"),
               ("stitched", "The second invoice needs the new address"),
               ("excerpt", "the delivery van must arrive before noon... the building closes after that"),
               ("near", "the sofa cushions should be dark green with pipping on every edge"),
               ("paraphrase", "she wants the lamp viewing moved to the afternoon")]
        hows = [resolve_quote(q, pack) for _n, q in six]
        check([(h["how"], h["src"]) for h in hows] == [("exact", "text"), ("exact", "transcript"), ("stitched", "text"),
                                                       ("excerpt", "text"), ("near", "text"), ("none", None)],
              "resolve: exact, voice transcript, stitched, excerpt, near, paraphrase; got %s" % [(h["how"], h["src"]) for h in hows])
        check(hows[0]["text"] == "hold the walnut table until Saturday?" and hows[0]["pks"] == [101] and hows[0]["at"].startswith("2026-10-02 09:10"),
              "resolve exact: the stored characters with the trailing ? carried, its pk and time")
        check(hows[2]["text"] == "The second invoice ⏎ needs the new address" and hows[2]["pks"] == [103, 104],
              "resolve stitched: two messages of the same sender joined with ⏎; got %r" % hows[2]["text"])
        check(hows[3]["text"] == "the delivery van must arrive before noon … the building closes after that." ,
              "resolve excerpt: stored spans joined with …; got %r" % hows[3]["text"])
        check(hows[4]["text"] == "The sofa cushions should be dark green with piping on every edge",
              "resolve near: the stored span, never the brain's words; got %r" % hows[4]["text"])
        check(hows[5]["text"] == six[5][1] and hows[5]["pks"] == [], "resolve none: the brain's words, no pks")
        check(resolve_quote("hold the table until", pack)["how"] == "none"
              and resolve_quote("cushions should be dark green with piping on every edg", pack)["text"].endswith("on every edge"),
              "resolve near: a skipped word is no match, a word cut at the edge is completed from the stored text")
        check(resolve_quote("I can pay the deposit on Friday", [dict(m, me=(m["pk"] == 103)) for m in pack[2:4]])["how"] != "stitched"
              and resolve_quote("The second invoice needs the new address", [dict(m, me=(m["pk"] == 103)) for m in pack[2:4]])["how"] == "none",
              "resolve stitched: two different senders never stitch")
        check(resolve_quote("The second invoice needs", pack)["how"] == "stitched"
              and resolve_quote("invoice", [pack[2], pack[2]])["how"] == "exact", "resolve stitched: every message of the window contributes")
        mbuf = tmp / "marks.md"
        mbuf.write_text("", encoding="utf-8")
        mdb = tmp / "marks.sqlite"
        mj = "60120000009@s.whatsapp.net"
        for i in range(0, 6, 2):
            append_captures(mdb, mbuf, mj, "Robin Vale", "shop",
                            [{"kind": "request_dated", "text": "item %s" % six[k][0], "quote": six[k][1]} for k in (i, i + 1)],
                            today="2026-10-02", messages=pack)
        marks = [e["lines"][0][2:].split(" ")[0] for e in list_entries(mbuf)]
        check(marks == ["🗣", "🎙", "🗣", "🗣", "🗣", "≈"],
              "pack marks: exact, voice, stitched, excerpt, near, paraphrase give 🗣 🎙 🗣 🗣 🗣 ≈; got %s" % marks)
        mt = mbuf.read_text(encoding="utf-8")
        check("- 🎙 10-02 09:12 语音转字「come with my sister to see the lamps at four」" in mt
              and "- ≈ 大意, 非逐字「she wants the lamp viewing moved to the afternoon」" in mt
              and "- 🗣 10-02 09:14「The second invoice ⏎ needs the new address」" in mt, "pack marks: the zh lines in full")
        cap_pack = [{"pk": 120, "at": "2026-10-02 10:00:00", "me": False, "speaker": "Robin", "text": "", "caption": "Pick up the frame at the north door"}]
        rc = resolve_quote("pick up the frame at the north door", cap_pack)
        check(rc["how"] == "exact" and rc["src"] == "text" and quote_line(rc, "x").startswith("- 🗣 10-02 10:00「Pick up"),
              "resolve: a caption counts as text (🗣)")
        twice = [{"pk": 130, "at": "2026-10-02 10:00:00", "me": False, "speaker": "R", "text": "see you at the gate"},
                 {"pk": 131, "at": "2026-10-02 11:00:00", "me": False, "speaker": "R", "text": "see you at the gate"}]
        check(resolve_quote("see you at the gate", twice, "2026-10-02T10:00:12")["pks"] == [130]
              and resolve_quote("see you at the gate", twice)["pks"] == [131] and resolve_quote("see you at the gate", twice, "10-02 23:59")["pks"] == [131],
              "resolve: quote_at prefers the matching message, else the newest")
        doc_r = {"how": "exact", "src": "doc", "text": "clause four", "at": "2026-10-02 10:00"}
        check(quote_line(hows[0], "q", lang="en") == '- 🗣 10-02 09:10 "hold the walnut table until Saturday?"'
              and quote_line(hows[1], "q", lang="en") == '- 🎙 10-02 09:12 voice transcript "come with my sister to see the lamps at four"'
              and quote_line(doc_r, "q", lang="en") == '- 📄 10-02 10:00 document text "clause four"'
              and quote_line(hows[5], hows[5]["text"], "10-02 09:40", lang="en") == '- ≈ 10-02 09:40 gist, not verbatim "she wants the lamp viewing moved to the afternoon"'
              and quote_line(None, "brain words", "", lang="en") == '- ≈ not checked "brain words"'
              and quote_line(doc_r, "q") == "- 📄 10-02 10:00 文件内文「clause four」",
              "English marks: 🗣 🎙 📄 ≈ gist and ≈ not checked; zh 📄")
        check(quote_line(None, "brain words", "2026-10-02 09:40") == "- ≈ 10-02 09:40 未核对「brain words」", "not checked mark (zh) with the brain's time")
        lines = [quote_line(hows[0], "q"), quote_line(hows[1], "q", lang="en"), quote_line(doc_r, "q"), quote_line(hows[5], "gist words", lang="en"),
                 quote_line(None, "unchecked words"), '- 🗣 「old style」']
        check([quote_of_line(x) for x in lines] == ["hold the walnut table until Saturday?", "come with my sister to see the lamps at four",
                                                     "clause four", "gist words", "unchecked words", "old style"],
              "QUOTE_LINE_RE reads the quote of every mark in both languages")

        class _Raising(dict):
            def get(self, *a, **k):
                raise RuntimeError("broken pack row")
        nbuf = tmp / "unchecked.md"
        nbuf.write_text("", encoding="utf-8")
        append_captures(tmp / "unchecked.sqlite", nbuf, "j-un", "Quinn Hale", "shop",
                        [{"kind": "money", "text": "no pack", "quote": "the rent is due", "quote_at": "10-02 08:00"}], messages=None)
        append_captures(tmp / "unchecked.sqlite", nbuf, "j-un", "Quinn Hale", "shop",
                        [{"kind": "money", "text": "check raised", "quote": "the tiles arrive late"}], messages=[_Raising(text="x")])
        ut = nbuf.read_text(encoding="utf-8")
        check("- ≈ 10-02 08:00 未核对「the rent is due」" in ut and "- ≈ 未核对「the tiles arrive late」" in ut and "🗣" not in ut,
              "not checked mark: no pack, or the check raised, never 🗣")

        # three dedupe keys: an extra comma, a different brain spelling of the same message
        dbuf = tmp / "dedupe.md"
        dbuf.write_text("", encoding="utf-8")
        ddb = tmp / "dedupe.sqlite"
        dj = "60120000010@s.whatsapp.net"
        d1 = append_captures(ddb, dbuf, dj, "Robin Vale", "shop", [{"kind": "money", "text": "hold", "quote": "Could you hold the walnut table until Saturday?"}], messages=pack)
        d2 = append_captures(ddb, dbuf, dj, "Robin Vale", "shop", [{"kind": "money", "text": "hold", "quote": "Could you, hold the walnut table until Saturday"}], messages=pack)
        d3 = append_captures(ddb, dbuf, dj, "Robin Vale", "shop", [{"kind": "money", "text": "hold", "quote": "Could you hold the walnut tabel until Saturday?"}], messages=pack)
        check((d1, d2, d3) == (1, 0, 0) and count_open(dbuf) == 1,
              "three keys dedupe: an extra comma and a different brain spelling of the same message are not written twice; got %s" % [d1, d2, d3])
        conn = sqlite3.connect(str(ddb))
        kinds = sorted(r[0] for r in conn.execute("SELECT key_kind FROM intake_seen"))
        ekeys = set(r[0] for r in conn.execute("SELECT entry_key FROM intake_seen"))
        conn.close()
        check(kinds == ["norm", "raw"] and ekeys == {list_open(dbuf)[0]["key"]},
              "dedupe rows carry key_kind and the entry's key (an exact resolve shares the norm key); got %s" % kinds)

        # an edit covered by a capture of this run
        ebuf2 = tmp / "edits.md"
        ebuf2.write_text("", encoding="utf-8")
        edb = tmp / "edits.sqlite"
        ej = "60120000011@s.whatsapp.net"
        epack = [{"pk": 201, "at": "2026-10-03 08:00:00", "me": False, "speaker": "Sky", "text": "Can we move the fitting to Thursday 5pm instead"}]
        edit = {"pk": 201, "old": "Can we move the fitting to Wednesday 5pm instead", "new": epack[0]["text"], "at": "2026-10-03 08:00:00"}
        n_cov = append_captures(edb, ebuf2, ej, "Sky Moreno", "shop", [{"kind": "appointment", "text": "fitting moves to Thursday 5pm",
                                                                        "quote": "move the fitting to Thursday 5pm"}],
                                today="2026-10-03", messages=epack, edits=[edit])
        et2 = ebuf2.read_text(encoding="utf-8")
        check(n_cov == 1 and "### 2026-10-03 · [wa] · Sky Moreno (无档) · ✏️ 对方改了讯息 · fitting moves to Thursday 5pm\n- 🗣 10-03 08:00「move the fitting to Thursday 5pm」\n"
              "- 改之前: 「Can we move the fitting to Wednesday 5pm instead」 (同一封讯息; 照旧版写的条目以这条为准)\n" in et2,
              "edit covered by a capture: the ✏️ capture with its before line, one entry")
        check(append_captures(edb, ebuf2, ej, "Sky Moreno", "shop", [], messages=epack, edits=[edit]) == 0,
              "edit covered earlier: the same edit again writes nothing")
        append_captures(edb, ebuf2, "60120000013@s.whatsapp.net", "Sky Moreno", "shop",
                        [{"kind": "appointment", "text": "comma only", "quote": "move the fitting to Thursday 5pm"}],
                        today="2026-10-03", messages=epack, edits=[dict(edit, old=edit["new"] + " ,")])
        check(list_entries(ebuf2)[-1]["title"].endswith("· comma only") and "✏️" not in list_entries(ebuf2)[-1]["title"],
              "an edit that only changed a comma is ignored (no ✏️ on the capture)")

        # an edit no capture covers: the stale entry is found, a correction is appended, the old entry untouched
        sbuf = tmp / "stale.md"
        sbuf.write_text("# intake\n", encoding="utf-8")
        sdb = tmp / "stale.sqlite"
        sj = "60120000012@s.whatsapp.net"
        old_pack = [{"pk": 301, "at": "2026-10-03 09:00:00", "me": False, "speaker": "Sky", "text": "ok the deposit is 450 by Monday"}]
        append_captures(sdb, sbuf, sj, "Sky Moreno", "shop", [{"kind": "money", "text": "deposit 450 by Monday", "quote": "the deposit is 450 by Monday"}],
                        today="2026-10-03", messages=old_pack)
        old_lines = hashlib.md5("\n".join(list_entries(sbuf)[0]["lines"]).encode("utf-8")).hexdigest()
        old_key = list_entries(sbuf)[0]["key"]
        sedit = {"pk": 301, "old": "ok the deposit is 450 by Monday", "new": "ok the deposit is 540 by Tuesday", "at": "2026-10-03 09:20:00"}
        n_fix = append_captures(sdb, sbuf, sj, "Sky Moreno", "shop", [], today="2026-10-04", messages=None, edits=[sedit])
        se = list_entries(sbuf)
        check(n_fix == 1 and len(se) == 2 and se[0]["key"] == old_key
              and hashlib.md5("\n".join(se[0]["lines"]).encode("utf-8")).hexdigest() == old_lines,
              "edit not covered: a correction appended, the old entry's lines md5 unchanged")
        check(se[1]["title"] == "2026-10-04 · [wa] · Sky Moreno (无档) · ✏️ 对方改了讯息 · 这行标题是照旧版写的, 以下面的原话为准: deposit 450 by Monday"
              and se[1]["lines"][:3] == ["- 🗣 10-03 09:20「ok the deposit is 540 by Tuesday」", "- 改之前: 「the deposit is 450 by Monday」 (旧条目还开着)",
                                         "- 我这边: 旧条目的日期与期限照新原话重看"]
              and se[1]["lines"][3] == "- 机器猜: (shop · 更正) 不是新的事, 归位时跟旧条目并成一件" and se[1]["open"],
              "edit not covered: the correction's title, current words with their time, before line, owner and guess lines")
        check(append_captures(sdb, sbuf, sj, "Sky Moreno", "shop", [], edits=[sedit]) == 0, "the same correction is never written twice")
        sedit_b = {"pk": 301, "old": "ok the deposit is 540 by Tuesday", "new": "ok the deposit is 600 by Wednesday", "at": "2026-10-03 09:20:00"}
        n_fix2 = append_captures(sdb, sbuf, sj, "Sky Moreno", "shop", [], today="2026-10-04", messages=None, edits=[sedit_b])
        se2 = list_entries(sbuf)
        check(n_fix2 == 1 and len(se2) == 3 and se2[2]["title"].count("✏️") == 1
              and se2[2]["title"].endswith("以下面的原话为准: deposit 450 by Monday") and se2[1]["lines"] == se[1]["lines"],
              "a correction of a correction keeps one prefix and the original title (%s)" % (se2[2]["title"] if len(se2) > 2 else se2))
        mark_placed(sbuf, 0, "drop")
        sedit2 = {"pk": 302, "old": "ok the deposit is 540 by Tuesday", "new": "ok the deposit is 560 by Tuesday", "at": "2026-10-03 09:40:00"}
        before_place = sbuf.read_text(encoding="utf-8")
        check(append_captures(sdb, sbuf, sj, "Sky Moreno", "shop", [], edits=[sedit2]) == 1
              and "- 改之前: 「ok the deposit is 540 by Tuesday」 (旧条目还开着)" in sbuf.read_text(encoding="utf-8")
              and sbuf.read_text(encoding="utf-8").startswith(_zones(before_place)[0].rstrip("\n")),
              "edit not covered, edited twice: the last entry of that person is the stale one")
        pbuf = tmp / "stale-placed.md"
        pbuf.write_text("", encoding="utf-8")
        append_captures(tmp / "sp.sqlite", pbuf, sj, "Sky Moreno", "shop", [{"kind": "money", "text": "deposit 450", "quote": "the deposit is 450 by Monday"}],
                        messages=old_pack)
        mark_placed(pbuf, 0, "task [[Deposits]]")
        append_captures(tmp / "sp.sqlite", pbuf, sj, "Sky Moreno", "shop", [], edits=[sedit])
        check("- 改之前: 「the deposit is 450 by Monday」 (旧条目状态: 已归 → task [[Deposits]])" in pbuf.read_text(encoding="utf-8"),
              "edit not covered: a placed stale entry is named with its status")
        quiet = tmp / "no-stale.md"
        quiet.write_text("# intake\n", encoding="utf-8")
        q0 = quiet.read_bytes()
        check(append_captures(tmp / "quiet.sqlite", quiet, sj, "Sky Moreno", "shop", [], edits=[sedit]) == 0 and quiet.read_bytes() == q0,
              "edit with no stale entry: nothing written")
        check(find_stale_entry(sbuf.read_text(encoding="utf-8"), "Other Person (无档)", sedit["old"], sedit["new"]) is None,
              "find_stale_entry: another person's entry is never the stale one")

        # intake_seen from an earlier version gains its columns and keeps its rows
        odb = tmp / "old.sqlite"
        conn = sqlite3.connect(str(odb))
        conn.execute("CREATE TABLE intake_seen (jid TEXT, quote_hash TEXT, at TEXT, PRIMARY KEY (jid, quote_hash))")
        conn.execute("INSERT INTO intake_seen VALUES (?, ?, ?)", (dj, quote_hash(dj, "an older quote"), "2026-09-01T10:00:00"))
        conn.commit()
        conn.close()
        obuf = tmp / "old-buffer.md"
        obuf.write_text("", encoding="utf-8")
        o1 = append_captures(odb, obuf, dj, "Robin Vale", "shop", [{"kind": "money", "text": "older", "quote": "an older quote"}])
        o2 = append_captures(odb, obuf, dj, "Robin Vale", "shop", [{"kind": "money", "text": "new", "quote": "a newer quote"}])
        conn = sqlite3.connect(str(odb))
        cols = [r[1] for r in conn.execute("PRAGMA table_info(intake_seen)")]
        orows = conn.execute("SELECT quote_hash, at, key_kind, entry_key FROM intake_seen ORDER BY at").fetchall()
        conn.close()
        check(o1 == 0 and o2 == 1 and cols == ["jid", "quote_hash", "at", "key_kind", "entry_key"] and orows[0][1] == "2026-09-01T10:00:00"
              and orows[0][2] is None and all(r[2] and r[3] for r in orows[1:]),
              "old intake_seen table gains key_kind and entry_key, keeps its rows, and its raw key still dedupes")

        # entry keys, key mode, classify
        kbuf = tmp / "keys.md"
        shutil.copy(str(mbuf), str(kbuf))
        before = list_open(kbuf)
        k2 = before[2]["key"]
        check(len(k2) == 40 and entry_key(before[2]) == entry_key("### " + before[2]["title"] + "\n" + "\n".join(before[2]["lines"])),
              "entry_key: the same from a list_entries dict and from the block text")
        mark_placed(kbuf, 2, "task [[Invoices]]")
        lp = list_placed(kbuf)
        check([x["key"] for x in lp] == [k2] and lp[0]["placed_to"] == "task [[Invoices]]" and k2 not in [x["key"] for x in list_open(kbuf)],
              "entry_key stable across mark_placed, list_placed reads placed_to")
        cbuf1, cbuf2 = tmp / "idx.md", tmp / "key.md"
        shutil.copy(str(mbuf), str(cbuf1))
        shutil.copy(str(mbuf), str(cbuf2))
        mark_placed(cbuf1, 4, "drop")
        r = mark_placed_by_key(cbuf2, list_open(mbuf)[4]["key"], "drop")
        check(r["status"] == "moved" and r["index"] == 4 and cbuf1.read_bytes() == cbuf2.read_bytes(),
              "index mode and key mode place the same entry, byte for byte")
        check(mark_placed_by_key(cbuf2, list_open(mbuf)[4]["key"], "drop") == {"status": "already_placed", "placed_to": "drop"}
              and mark_placed_by_key(cbuf2, "0" * 40, "drop") == {"status": "not_found"}, "mark_placed_by_key: already placed and not found")
        nb = tmp / "notopen.md"
        nb.write_text("### 2026-10-02 · [wa] · Kim Ito (无档) · parked\n- 🗣 「parked words」\n- 状态: 搁着\n", encoding="utf-8")
        nk = list_entries(nb)[0]["key"]
        n_before = nb.read_bytes()
        check(mark_placed_by_key(nb, nk, "drop") == {"status": "not_open", "index": 0} and nb.read_bytes() == n_before,
              "mark_placed_by_key: an entry that is not open is refused, file untouched")
        try:
            mark_placed(nb, 0, "drop")
            msg_np = ""
        except ValueError as e:
            msg_np = str(e)
        check("entry number 1 is not open" in msg_np and nb.read_bytes() == n_before,
              "mark_placed on an entry that is not open names it by its number from 1 (%s)" % msg_np)
        ok_open = list_open(kbuf)[0]["key"]
        cl = classify_keys(kbuf, [ok_open, k2, "f" * 40])
        check(cl == {ok_open: ("open", ""), k2: ("placed", "task [[Invoices]]"), "f" * 40: ("missing", "")},
              "classify_keys: open, placed and missing; got %s" % cl)

        # append_under_key (the undo line of a vault write): last line of the block, either zone, key unchanged
        abuf = tmp / "append.md"
        shutil.copy(str(kbuf), str(abuf))
        a_open = list_open(abuf)
        a_last = a_open[-1]["key"]
        a_first = a_open[0]["key"]
        a_placed = list_placed(abuf)[0]["key"]
        a_before = abuf.read_text(encoding="utf-8")
        r1 = append_under_key(abuf, a_first, "- 撤回 10-21 12:00: the task change was given back")
        r2 = append_under_key(abuf, a_placed, "- undo 10-21 12:01: the task file was moved to its backup")
        r3 = append_under_key(abuf, a_last, "- 撤回 10-21 12:02: last open entry")
        a_after = abuf.read_text(encoding="utf-8")
        blocks = dict((entry_key(b), b) for b in _blocks(a_after))
        nob = {"lines": [""], "zone": ""}
        b_first, b_placed, b_last = (blocks.get(x, nob) for x in (a_first, a_placed, a_last))
        check(r1 == {"status": "appended"} and r2 == {"status": "appended"} and r3 == {"status": "appended"}
              and b_first["lines"][-1] == "- 撤回 10-21 12:00: the task change was given back"
              and b_placed["lines"][-1] == "- undo 10-21 12:01: the task file was moved to its backup"
              and b_placed["zone"] == "placed" and b_last["lines"][-1] == "- 撤回 10-21 12:02: last open entry"
              and [x["key"] for x in list_open(abuf)] == [x["key"] for x in a_open]
              and [x["key"] for x in list_placed(abuf)] == [x["key"] for x in list_placed(kbuf)],
              "append_under_key: the line lands as the last line of the keyed entry, open or placed, keys and zones unchanged")
        a_strip = "".join(ln for ln in a_after.splitlines(True) if "10-21 12:0" not in ln)
        check(a_strip == a_before and len(a_after) - len(a_before) == sum(len(x) + 1 for x in (
              "- 撤回 10-21 12:00: the task change was given back", "- undo 10-21 12:01: the task file was moved to its backup",
              "- 撤回 10-21 12:02: last open entry")),
              "append_under_key: every other byte of the buffer is untouched")
        a_mid = abuf.read_bytes()
        nf = append_under_key(abuf, "0" * 40, "- 撤回 never written")
        bad_lines = []
        for bl in ("two\nlines", "", "   ", "### a new section", "## 已归"):
            try:
                append_under_key(abuf, a_first, bl)
            except ValueError:
                bad_lines.append(bl)
        check(nf == {"status": "not_found"} and abuf.read_bytes() == a_mid and len(bad_lines) == 5,
              "append_under_key: an unknown key is not_found and a bad line is refused, the file untouched (%s)" % len(bad_lines))
        # the CLI for keys
        me = str(Path(__file__).resolve())
        r = subprocess.run([sys.executable, me, "--buffer", str(kbuf), "--list-open"], capture_output=True, timeout=60)
        out = r.stdout.decode("utf-8")
        check(r.returncode == 0 and ("\n    key: %s\n" % ok_open[:12]) in out, "CLI --list-open prints each entry's key (12 hex)")
        r = subprocess.run([sys.executable, me, "--buffer", str(kbuf), "--list-open", "--json"], capture_output=True, timeout=60)
        js = json.loads(r.stdout.decode("utf-8"))
        check(r.returncode == 0 and js[0]["key"] == ok_open and js[0]["n"] == 1, "CLI --list-open --json carries full keys")
        r = subprocess.run([sys.executable, me, "--buffer", str(kbuf), "--mark-placed-key", ok_open[:8], "--to", "task [[Tables]]"],
                           capture_output=True, timeout=60)
        check(r.returncode == 0 and r.stdout.decode("utf-8").startswith("placed → task [[Tables]]\n### ")
              and classify_keys(kbuf, [ok_open])[ok_open] == ("placed", "task [[Tables]]"), "CLI --mark-placed-key with an 8 hex prefix")
        r = subprocess.run([sys.executable, me, "--buffer", str(kbuf), "--mark-placed-key", "0123abcd", "--to", "drop"], capture_output=True, timeout=60)
        check(r.returncode != 0 and "matches 0 entries" in r.stderr.decode("utf-8"), "CLI --mark-placed-key: an unknown prefix is an error naming the count")
        r = subprocess.run([sys.executable, me, "--buffer", str(kbuf), "--list-placed"], capture_output=True, timeout=60)
        check(r.returncode == 0 and "task [[Tables]]" in r.stdout.decode("utf-8") and ("key: %s" % k2[:12]) in r.stdout.decode("utf-8"),
              "CLI --list-placed lists the placed zone with keys")
        # ---- v1.3.0 · the board's Today brief under each entry, K cards, settle (stub board on 127.0.0.1, OS chosen port)
        check(VERSION == "1.4.0", "VERSION is 1.4.0")
        cfgp.write_text(json.dumps({"intake": {"enabled": True, "buffer": str(buf)}, "owner_label": "Alex Rivera",
                                    "notify": {"outlet": "command", "command": "cat > /dev/null"}}), encoding="utf-8")
        r = subprocess.run([sys.executable, me, "--config", str(cfgp), "--evening-status"], capture_output=True, timeout=60)
        try:
            st = json.loads(r.stdout.decode("utf-8"))
        except ValueError:
            st = {}
        check(r.returncode == 0 and st.get("run") is False and "evening_push" in str(st.get("why")),
              "retired evening status: a config without the retired evening keys reads them as off, no crash")
        bbuf = tmp / "brief-buffer.md"
        ent = ("### 2026-10-05 · [wa] · %s (no note) · %s\n- 🗣 10-05 09:%s \"%s\"\n- owner side: to place\n"
               "- machine guess: (Work · dated request) none\n- dig deeper: `dossier.py prepare --name %s`\n- status: open\n\n")
        bbuf.write_text("# Intake\n\n"
                        + ent % ("Robin Vale", "wants two seats held for the spring bake class", "10", "can you hold two seats", "Robin Vale")
                        + ent % ("Sky Moreno", "oven repair visit on Thursday", "20", "the technician comes Thursday", "Sky Moreno")
                        + ent % ("Quinn Hale", "asks for the sourdough recipe sheet", "30", "send me the recipe sheet", "Quinn Hale")
                        + "## Placed\n", encoding="utf-8")
        bents = list_open(bbuf)
        k0, k1, k2 = [e["key"] for e in bents]
        long_now = "Add the two held seats to the class list and tell Robin by Friday " + "x" * 160

        def card(cid, status, kind, task, keys, date="2026-10-05", rev=1, **kw):
            d = {"id": cid, "rev": rev, "status": status, "date": date, "kind": kind, "task": task, "now": "", "fm_set": {},
                 "ev": [], "jids": [], "entry_keys": keys, "note": "", "newer": None, "retired_note": "", "placed_note": "",
                 "replied_at": "", "replied_note": "", "close_note": "", "retired_why": ""}
            d.update(kw)
            return d
        brief = {"ok": True, "date": "2026-10-05", "empty": False, "stale": False, "built_at": "2026-10-05T11:00:00", "projects": [
            {"id": "Deliver/Spring-Bake-Class", "name": "Spring Bake Class", "lane": "Deliver", "props": [
                card("a1a1a1a1a1a1a1a1", "pending", "update", "Hold-Seats.md", [k0], rev=3, kindL="update task", now=long_now),
                card("b2b2b2b2b2b2b2b2", "ok", "create", "Seat-List.md", [k0[:12]], note="check the room size first",
                     replied_note="you replied after this card was made"),
                card("c3c3c3c3c3c3c3c3", "no", "update", "Pressed-No.md", [k0]),
                card("d4d4d4d4d4d4d4d4", "superseded", "update", "Oven-Visit.md", [k1], retired_why="replied",
                     close_note="the brain read your reply as the answer"),
                card("e5e5e5e5e5e5e5e5", "superseded", "update", "Placed-Before.md", [k1], retired_why="placed_elsewhere"),
                card("f6f6f6f6f6f6f6f6", "pending", "create", "Deposit-Refund.md", [], date="2026-10-04", rev=2,
                     now="Refund the deposit for the cancelled tart workshop")]},
            {"id": "unassigned", "name": "", "lane": "", "props": [
                card("0e0e0e0e0e0e0e0e", "ok", "seed", "", [], date="2026-10-03", now="A weekend kids class keeps coming up")]}]}
        text = format_brief_listing(bents, brief, "en")
        tl = text.split("\n")
        i1, i2, i3 = (tl.index("%d. %s" % (n + 1, bents[n]["title"])) for n in range(3))
        under0, under1, under2 = tl[i1:i2], tl[i2:i3], tl[i3:]
        check("    board: Spring Bake Class · update task Hold-Seats.md · waiting for your press" in under0
              and "    board: Spring Bake Class · new task Seat-List.md · you pressed OK" in under0
              and "      your note: check the room size first" in under0 and "      you replied after this card was made" in under0
              and not any("Pressed-No.md" in x for x in tl) and not any("Hold-Seats.md" in x for x in under1 + under2),
              "brief lines: the matching entry's live cards under it (a key prefix counts), a card pressed No left out")
        prop = [x for x in under0 if x.startswith("      proposal: ")]
        check(len(prop) == 1 and len(prop[0]) == len("      proposal: ") + 160 and prop[0].endswith("…")
              and under0.index(prop[0]) == under0.index("    board: Spring Bake Class · update task Hold-Seats.md · waiting for your press") + 1,
              "brief lines: the proposal right under its card, cut to 160 characters")
        check("    board: Spring Bake Class · update task Oven-Visit.md · retired (you replied)" in under1
              and "      the brain read your reply as the answer" in under1 and not any("Placed-Before.md" in x for x in tl),
              "brief lines: a card retired today because you replied still shows, one retired because it was placed does not")
        check("    board: no card, judge it as before" in under2 and "    board: no card, judge it as before" not in under0 + under1,
              "brief lines: an entry with no card gets the no-card line")
        kh = [n for n, x in enumerate(tl) if x.startswith("The board has ")]
        check(kh and tl[kh[0]] == "The board has 2 more card(s) with no intake entry (oldest 10-03):"
              and tl[kh[0] + 1] == "K1. 10-03 · no project yet · project seed · you pressed OK · id 0e0e0e0e rev 1"
              and tl[kh[0] + 2] == "      proposal: A weekend kids class keeps coming up"
              and tl[kh[0] + 3] == "K2. 10-04 · Spring Bake Class · new task Deposit-Refund.md · waiting for your press · id f6f6f6f6 rev 2",
              "K cards: live cards without entries after the list, oldest first, with id and rev (%s)" % tl[kh[0]:] if kh else tl[-6:])
        zh = format_brief_listing(bents, brief, "zh")
        check("    看板: 这条没有卡, 照旧自己判断" in zh and "看板另有 2 张卡没有对应的 intake 条目 (最早 10-03):" in zh
              and "    看板: Spring Bake Class · update task Hold-Seats.md · 等你按" in zh, "brief lines in Chinese for board.lang zh")
        none_left = format_brief_listing(bents, dict(brief, projects=[]), "en")
        check(none_left.count("    board: no card, judge it as before") == 3 and "The board has" not in none_left,
              "an empty brief: every entry gets the no-card line, no K list")

        import http.server
        board = {"brief": brief, "posts": []}

        class _Board(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="application/json"):
                data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/api/today":
                    self._send(200, board["brief"])
                elif self.path == "/html/api/today":
                    self._send(200, b"<html>not the brief</html>", "text/html")
                else:
                    self._send(404, {"ok": False, "error": "not found"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode("utf-8"))
                board["posts"].append((self.path, body))
                if self.path != "/api/today/settle":
                    return self._send(404, {"ok": False, "error": "not found"})
                if str(body.get("id")).startswith("b2b2"):
                    return self._send(200, {"ok": False, "reason": "keys"})
                if body.get("rev") != 2:
                    return self._send(409, {"ok": False, "reason": "rev", "current": {"id": "f6f6f6f6f6f6f6f6", "rev": 2}})
                return self._send(200, {"ok": True, "id": "f6f6f6f6f6f6f6f6", "status": "superseded"})
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Board)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        try:
            url = "http://127.0.0.1:%d/api/today" % srv.server_address[1]
            r = subprocess.run([sys.executable, me, "--buffer", str(bbuf), "--list-open", "--with-brief", url], capture_output=True, timeout=60)
            check(r.returncode == 0 and r.stdout.decode("utf-8") == text + "\n",
                  "CLI --list-open --with-brief prints the cards under the entries and the K list (English buffer, English words)")
            r = subprocess.run([sys.executable, me, "--buffer", str(bbuf), "--list-open", "--json", "--with-brief", url],
                               capture_output=True, timeout=60)
            js = json.loads(r.stdout.decode("utf-8"))
            check(r.returncode == 0 and [c["id"] for c in js["entries"][0]["cards"]] == ["a1a1a1a1a1a1a1a1", "b2b2b2b2b2b2b2b2"]
                  and js["entries"][2]["cards"] == [] and js["entries"][0]["n"] == 1 and js["entries"][0]["key"] == k0
                  and [c["id"] for c in js["k_cards"]] == ["0e0e0e0e0e0e0e0e", "f6f6f6f6f6f6f6f6"] and js["brief"]["read"] is True,
                  "CLI --list-open --json --with-brief: cards per entry and k_cards")
            sock = __import__("socket").socket()
            sock.bind(("127.0.0.1", 0))
            dead = "http://127.0.0.1:%d/api/today" % sock.getsockname()[1]
            sock.close()
            plain = format_listing(bents, keys=True)
            for label, u, why in (("unreachable", dead, "could not read the brief ("), ("not JSON", url.replace("/api/today", "/html/api/today"), "not JSON")):
                r = subprocess.run([sys.executable, me, "--buffer", str(bbuf), "--list-open", "--with-brief", u], capture_output=True, timeout=60)
                o = r.stdout.decode("utf-8").split("\n", 1)
                check(r.returncode == 0 and o[0].startswith("board: could not read the brief (") and why in o[0]
                      and o[0].endswith("; judging entry by entry as before") and o[1] == plain + "\n",
                      "a brief that cannot be read (%s): one line saying why, then the usual listing" % label)
            check(settle_url("http://127.0.0.1:8/api/today/") == "http://127.0.0.1:8/api/today/settle"
                  and settle_url("https://board.example.ts.net/api/today") == "https://board.example.ts.net/api/today/settle",
                  "settle URL: <brief URL>/settle")
            bad_urls = []
            for u in ("http://127.0.0.1:8/api/todays", "http://127.0.0.1:8/api/today/refresh", "file:///tmp/api/today", "127.0.0.1:8/api/today"):
                try:
                    settle_url(u)
                    bad_urls.append(u)
                except ValueError:
                    pass
            check(not bad_urls, "settle URL: anything not ending in /api/today, or not http, is refused (%s)" % bad_urls)
            n_posts = len(board["posts"])
            base_cmd = [sys.executable, me, "--settle-card"]
            r_ok = subprocess.run(base_cmd + ["f6f6f6", "--rev", "2", "--to", "task [[Deposits]]", "--with-brief", url], capture_output=True, timeout=60)
            r_rev = subprocess.run(base_cmd + ["f6f6f6", "--rev", "1", "--to", "task [[Deposits]]", "--with-brief", url], capture_output=True, timeout=60)
            r_keys = subprocess.run(base_cmd + ["b2b2b2b2", "--rev", "1", "--to", "drop", "--with-brief", url], capture_output=True, timeout=60)
            check(r_ok.returncode == 0 and r_ok.stdout.decode("utf-8") == "settled: card f6f6f6f6 → task [[Deposits]]\n"
                  and board["posts"][n_posts] == ("/api/today/settle", {"id": "f6f6f6", "rev": 2, "to": "task [[Deposits]]"}),
                  "CLI --settle-card: POST <URL>/settle {id, rev, to}, prints settled, exit 0")
            check(r_rev.returncode == 1 and r_rev.stdout.decode("utf-8").startswith("refused: rev: the card changed")
                  and "rev 2 now" in r_rev.stdout.decode("utf-8") and r_keys.returncode == 1
                  and r_keys.stdout.decode("utf-8").startswith("refused: keys: ") and "within a minute" in r_keys.stdout.decode("utf-8"),
                  "CLI --settle-card: a wrong rev and a card with entries are refused with the reason, exit 1")
            n_posts = len(board["posts"])
            r_url = subprocess.run(base_cmd + ["f6f6f6", "--rev", "2", "--to", "drop", "--with-brief", url + "/refresh"], capture_output=True, timeout=60)
            r_norev = subprocess.run(base_cmd + ["f6f6f6", "--to", "drop", "--with-brief", url], capture_output=True, timeout=60)
            r_dead = subprocess.run(base_cmd + ["f6f6f6", "--rev", "2", "--to", "drop", "--with-brief", dead], capture_output=True, timeout=60)
            check(r_url.returncode == 1 and "must end in /api/today" in r_url.stderr.decode("utf-8") and r_norev.returncode == 1
                  and len(board["posts"]) == n_posts and r_dead.returncode == 1
                  and r_dead.stdout.decode("utf-8").startswith("refused: could not reach the board"),
                  "CLI --settle-card: a wrong URL or no --rev exits 1 before any request; an unreachable board exits 1")
        finally:
            srv.shutdown()
            srv.server_close()
        src = Path(__file__).read_text(encoding="utf-8")
        check(chr(0x2014) not in src and chr(0x2013) not in src, "no em dash or en dash in this file")
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)
    return check.done()


# ---------------------------------------------------------------- CLI

def _cli_settle(a):
    """--settle-card: exit 0 when the board settled the card, 1 for anything else (the URL is checked first)."""
    missing = [flag for flag, v in (("--with-brief", a.with_brief), ("--rev", a.rev), ("--to", (a.to or "").strip() or None))
               if v is None]
    if missing:
        sys.stderr.write("--settle-card needs %s\n" % ", ".join(missing))
        return 1
    try:
        settle_url(a.with_brief)
    except ValueError as e:
        sys.stderr.write("%s\n" % e)
        return 1
    ok, line = settle_card(a.with_brief, a.settle_card, a.rev, a.to.strip())
    print(line)
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="intake buffer: count, list, place; the board's brief cards under each entry; settle a card")
    ap.add_argument("--config", default=None, help="live/config.json (default: env WA_LIVE_CONFIG or ~/WhatsApp-Live/live/config.json)")
    ap.add_argument("--buffer", default=None, help="buffer path (default: intake.buffer from the config)")
    ap.add_argument("--selftest", action="store_true", help="temp files only")
    ap.add_argument("--count-open", action="store_true", help="print the number of open entries")
    ap.add_argument("--list-open", action="store_true", help="list open entries, numbered from 1, each with its key")
    ap.add_argument("--list-placed", action="store_true", help="list the placed zone (read only)")
    ap.add_argument("--json", action="store_true", help="with --list-open or --list-placed: JSON with full keys")
    ap.add_argument("--mark-placed", type=int, metavar="N", help="command-base intake mode only: mark entry N (from 1) placed")
    ap.add_argument("--mark-placed-key", metavar="KEY", help="command-base intake mode only: mark the entry with this key "
                                                              "(full, or a unique prefix of 8+ hex) placed")
    ap.add_argument("--to", default="", help="with --mark-placed or --mark-placed-key: where it went ('task [[Project]]', "
                                             "'calendar 2026-09-19 15:00', 'drop')")
    ap.add_argument("--with-brief", metavar="URL", default=None,
                    help="the board's brief, http://127.0.0.1:<port>/api/today: with --list-open, its cards under each entry "
                         "and the K cards; with --settle-card, where to settle")
    ap.add_argument("--settle-card", metavar="ID", default=None, help="settle a K card (id or a unique prefix of 6+ hex) "
                                                                      "with --rev, --to and --with-brief")
    ap.add_argument("--rev", type=int, default=None, help="with --settle-card: the rev the listing showed")
    ap.add_argument("--evening-status", action="store_true", help="retired: JSON for an evening job installed before v1.3.0")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if a.settle_card is not None:
        return _cli_settle(a)
    cfg = None
    if a.evening_status or not a.buffer:
        try:
            cfg = W.load_config(a.config)
        except (FileNotFoundError, ValueError) as e:
            if a.evening_status:
                print(json.dumps({"run": False, "why": str(e)}, ensure_ascii=False))
                return 2
            ap.error("%s (or pass --buffer)" % e)
    if a.evening_status:
        print(json.dumps(evening_status(cfg), ensure_ascii=False))
        return 0
    buffer = a.buffer or ((cfg.get("intake") or {}).get("buffer") if cfg else "")
    if not buffer:
        ap.error("intake.buffer is not set in the config (or pass --buffer)")
    if a.count_open:
        print(count_open(buffer))
        return 0
    if a.list_open and a.with_brief:
        entries = list_open(buffer)
        if cfg is not None:
            lang = W.owner_lang(cfg)
        else:
            langs = [e["lang"] for e in list_entries(buffer) if e.get("lang")]
            lang = langs[0] if langs else "zh"
        payload, why = fetch_brief(a.with_brief)
        if a.json:
            print(json.dumps(brief_json(entries, payload, why), ensure_ascii=False, indent=1))
            return 0
        text = format_brief_listing(entries, payload, lang, why)
        if text:
            print(text)
        return 0
    if a.list_open or a.list_placed:
        entries = list_open(buffer) if a.list_open else list_placed(buffer)
        if a.json:
            print(json.dumps([dict(e, n=e["index"] + 1) for e in entries], ensure_ascii=False, indent=1))
            return 0
        listing = format_listing(entries, keys=True)
        if listing:
            print(listing)
        return 0
    if a.mark_placed_key is not None:
        if not a.to.strip():
            ap.error("--mark-placed-key needs --to")
        try:
            key = resolve_key_prefix(buffer, a.mark_placed_key)
            r = mark_placed_by_key(buffer, key, a.to)
        except (ValueError, FileNotFoundError) as e:
            ap.error(str(e))
        if r["status"] == "already_placed":
            ap.error("entry %s is already placed → %s" % (key[:12], r.get("placed_to") or "?"))
        if r["status"] == "not_open":
            ap.error("entry %s (number %d) is not open, it cannot be placed" % (key[:12], r["index"] + 1))
        if r["status"] != "moved":
            ap.error("no entry with key %s (run --list-open again)" % key[:12])
        print("placed → %s\n%s" % (a.to, r["moved"].splitlines()[0] if r.get("moved") else ""))
        return 0
    if a.mark_placed is not None:
        if not a.to.strip():
            ap.error("--mark-placed needs --to")
        try:
            moved = mark_placed(buffer, a.mark_placed - 1, a.to)
        except IndexError:
            ap.error("the buffer has %d entries before the placed zone; there is no %d (run --list-open first)"
                     % (len(list_entries(buffer)), a.mark_placed))
        except (ValueError, FileNotFoundError) as e:
            ap.error(str(e))
        print("placed → %s\n%s" % (a.to, moved.splitlines()[0] if moved else ""))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
