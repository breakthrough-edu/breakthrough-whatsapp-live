#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wa_intake.py · the intake buffer: things someone asked of the owner (or the owner promised), captured by the brain,
waiting to be placed by the owner.

The brain may return up to two `captures` per run, of five kinds: a dated request, a promise not yet delivered, an
appointment, money, a decision the other side stated. append_captures() adds them to the owner's buffer note (a
Markdown file in their vault at `intake.buffer`), deduplicated per conversation and quote in the state database table
`intake_seen`. Placing an entry (a task, a calendar event, a decision, dropping it) happens only in a session with the
owner, through their command-base intake mode, which calls --list-open and --mark-placed. The daemon and the evening
job never change an entry's status.

Buffer file (created by the install wizard with the owner's yes; this module never creates a missing folder):
  <any preamble the owner wants>
  ### 2026-09-15 · [wa] · [[Note-Name]] · what is asked
  - 🗣 09-14 16:53「the exact quote」
  - <owner> 这边: 期限 09-20
  - 机器猜: (<world> · <kind>) <guess>
  - 深挖: `dossier.py prepare --name Note-Name`
  - 状态: open
  ## 已归
  <placed entries, their status line rewritten to "- 状态: 已归 → <where>">
English buffers (board.lang en) write "- status: open", "## Placed" and "- status: placed → <where>"; reading accepts
both languages in the same file.

Safety (without it, two writers could overwrite each other and lose an entry already marked seen):
- append and mark-placed hold an exclusive fcntl.flock on the sidecar `.<buffer name>.lock` next to the buffer, read
  the file inside the lock, and replace it atomically only if its modification time did not change in between (an
  edit saved from Obsidian during the write makes it read again and redo the insert);
- `intake.enabled=false` turns append_captures into a no-op that returns 0. The switch comes from `cfg=` when given,
  else from the config.json next to the state database (the daemon's live folder), so every caller honours it.

CLI
  wa_intake.py [--config P | --buffer P] --count-open
  wa_intake.py [--config P | --buffer P] --list-open                      numbered from 1
  wa_intake.py [--config P | --buffer P] --mark-placed N --to "task [[Project]]"   (command-base intake mode only)
  wa_intake.py [--config P] --evening-status                               JSON for templates/intake-evening.mjs
  wa_intake.py --selftest                                                  temp files only
Python 3.9 standard library only.
"""

import argparse
import contextlib
import datetime as dt
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
           "pending": "待归位", "nonote": "%s (无档)", "none": "无"},
    "en": {"placed_hdr": "## Placed", "open": "- status: open", "placed": "- status: placed → %s", "owner": "%s side",
           "owner_none": "owner side", "guess": "machine guess", "dig": "dig deeper", "promise": "promised, not delivered",
           "deadline": "due %s", "pending": "to place", "nonote": "%s (no note)", "none": "none"},
}
_PLACED_RE = re.compile(r"^## (?:已归|Placed)[ \t]*$", re.M)
_ENTRY_RE = re.compile(r"^### ", re.M)
OPEN_LINES = {"zh": MARKS["zh"]["open"], "en": MARKS["en"]["open"]}

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


def render_entry(cap, jid, name, world, people_note=None, source="wa", today=None, owner_label="", lang="zh",
                 world_label=None):
    L = _lang(lang)
    M = MARKS[L]
    today = today or dt.date.today().isoformat()
    who, dossier_name = _who(name, people_note, L)
    at = _fmt_at(cap.get("quote_at"))
    side = []
    if cap["kind"] == "promise_undelivered":
        side.append(M["promise"])
    if cap.get("deadline"):
        side.append(M["deadline"] % cap["deadline"])
    if not side:
        side.append(M["pending"])
    owner = (M["owner"] % owner_label.strip()) if str(owner_label or "").strip() else M["owner_none"]
    quote = ("「%s」" % cap["quote"]) if L == "zh" else ('"%s"' % cap["quote"])
    if at:
        said = "- 🗣 %s%s%s" % (at, "" if L == "zh" else " ", quote)
    else:
        said = "- 🗣 %s" % quote
    head = "(%s · %s)" % (world_label or world or "-", KIND_LABELS[L].get(cap["kind"], cap["kind"]))
    return "\n".join([
        "### %s · [%s] · %s · %s" % (today, source, who, cap["text"]),
        said,
        "- %s: %s" % (owner, ", ".join(side)),
        "- %s: %s %s" % (M["guess"], head, cap.get("guess") or M["none"]),
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
    conn.execute("CREATE TABLE IF NOT EXISTS intake_seen (jid TEXT, quote_hash TEXT, at TEXT, PRIMARY KEY (jid, quote_hash))")


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


def append_captures(state_db, buffer_path, jid, name, world, captures, source="wa", people_note=None, today=None, cfg=None):
    """Validated captures appended before the placed zone; each (jid, quote) lands once (intake_seen). Returns how
    many were added. A no-op returning 0 when intake is disabled, the capture list is empty, or no buffer is set.
    Raises FileNotFoundError when the buffer's folder does not exist. Existing entries and statuses never change."""
    caps = validate_captures(captures)
    if not caps:
        return 0
    st = intake_settings(state_db, cfg)
    if st["enabled"] is False:
        return 0
    p = _path(buffer_path) or _path(st["buffer"])
    if p is None:
        return 0
    if not p.parent.is_dir():
        raise FileNotFoundError("intake buffer folder not found: %s" % p.parent)
    world_label = W.vocab_label(st["cfg"], "world", world, st["lang"]) if (st["cfg"] and world) else None
    conn = sqlite3.connect(str(state_db), timeout=10)
    added = 0
    try:
        _ensure_seen_table(conn)
        with _locked(p):
            for cap in caps:
                h = quote_hash(jid, cap["quote"])
                if conn.execute("SELECT 1 FROM intake_seen WHERE jid=? AND quote_hash=?", (jid, h)).fetchone():
                    continue
                entry = render_entry(cap, jid, name, world, people_note=people_note, source=source, today=today,
                                     owner_label=st["owner_label"], lang=st["lang"], world_label=world_label)
                _rewrite(p, lambda text: (_insert_before_placed(text, entry, st["lang"]), None))
                conn.execute("INSERT OR IGNORE INTO intake_seen (jid, quote_hash, at) VALUES (?, ?, ?)",
                             (jid, h, dt.datetime.now().isoformat(timespec="seconds")))
                conn.commit()
                added += 1
    finally:
        conn.close()
    return added


# ---------------------------------------------------------------- reading and placing

def list_entries(buffer):
    """Every `### ` section before the placed zone: [{index (0 based), title, lines, open, lang}]."""
    p = _path(buffer)
    if p is None or not p.exists():
        return []
    open_zone, _ = _zones(_read(p))
    out = []
    for i, (s, e) in enumerate(_entry_spans(open_zone)):
        lines = open_zone[s:e].rstrip("\n").split("\n")
        stripped = [x.strip() for x in lines]
        lang = "zh" if OPEN_LINES["zh"] in stripped else ("en" if OPEN_LINES["en"] in stripped else None)
        out.append({"index": i, "title": lines[0][4:].strip(), "lines": lines[1:], "open": lang is not None, "lang": lang})
    return out


def list_open(buffer):
    """Open entries only: [{title, lines, index}] (index as in list_entries, 0 based)."""
    return [{"title": e["title"], "lines": e["lines"], "index": e["index"]} for e in list_entries(buffer) if e["open"]]


def count_open(buffer):
    """Sections before the placed zone that carry an open status line; 0 when the file does not exist.
    The one rule for counting (the digest and the command-base morning line call this)."""
    return sum(1 for e in list_entries(buffer) if e["open"])


def format_listing(entries):
    out = []
    for e in entries:
        out.append("%d. %s" % (e["index"] + 1, e["title"]))
        for line in e["lines"]:
            out.append("    " + line)
    return "\n".join(out)


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

    def transform(text):
        open_zone, placed = _zones(text)
        spans = _entry_spans(open_zone)
        if n < 0 or n >= len(spans):
            raise IndexError("the intake buffer has no entry %d before the placed zone (it has %d)" % (n, len(spans)))
        s, e = spans[n]
        block = open_zone[s:e]
        lang = None
        for L in ("zh", "en"):
            if re.search(r"^[ \t]*%s[ \t]*$" % re.escape(OPEN_LINES[L]), block, re.M):
                lang = L
                break
        if lang is None:
            raise ValueError("entry %d is not open, it cannot be placed again" % n)
        moved = re.sub(r"^([ \t]*)%s[ \t]*$" % re.escape(OPEN_LINES[lang]),
                       lambda m: m.group(1) + MARKS[lang]["placed"] % where, block.rstrip("\n"), count=1, flags=re.M)
        new_open = open_zone[:s] + open_zone[e:]
        pl = placed if placed is not None else MARKS[lang]["placed_hdr"] + "\n"
        if not pl.endswith("\n"):
            pl += "\n"
        return new_open + pl + "\n" + moved + "\n", moved
    with _locked(p):
        return _rewrite(p, transform)


# ---------------------------------------------------------------- evening push support

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
    """Everything templates/intake-evening.mjs needs, as one JSON-able dict (defaults already applied here, so the
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
    elif not it.get("evening_push"):
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
            {"kind": "request_dated", "text": "wants three partner codes set up before the 20th",
             "quote": "can you open three codes before the 20th", "quote_at": "2026-09-14T16:53:00", "deadline": "09-20",
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

        n1 = append_captures(db, buf, "60120000001@s.whatsapp.net", "Alex Rivera", "work", caps,
                             people_note=Path("/x/People/Alex-Rivera.md"), today="2026-09-15")
        check(n1 == 2, "append: two added (no config next to the database, so enabled is not known and the call proceeds)")
        n2 = append_captures(db, buf, "60120000001@s.whatsapp.net", "Alex Rivera", "work", caps,
                             people_note=Path("/x/People/Alex-Rivera.md"), today="2026-09-15")
        check(n2 == 0, "append: the same quotes again add nothing (dedupe)")
        n3 = append_captures(db, buf, "60120000002@s.whatsapp.net", "Blake Chen", "work",
                             [{"kind": "promise_undelivered", "text": "owner promised a proposal", "quote": "ok waiting for your proposal"}],
                             today="2026-09-15")
        check(n3 == 1, "append: another conversation adds its own entry")
        text = buf.read_text(encoding="utf-8")
        check(text.startswith(preamble + old_entry.rstrip("\n")), "existing preamble and entry untouched at the top")
        check(text.rstrip("\n").endswith("## 已归"), "placed header created at the end")
        check("### 2026-09-15 · [wa] · [[Alex-Rivera]] · wants three partner codes set up before the 20th" in text, "with a note: [[Note]] title")
        check("- 🗣 09-14 16:53「can you open three codes before the 20th」" in text, "quote_at ISO becomes MM-DD HH:MM")
        check("- 我这边: 期限 09-20" in text and "- 我这边: 答应了没交" in text, "owner line without owner_label, deadline and promise")
        check("- 机器猜: (work · 有日期的请求) task under the partner project" in text, "guess line with world and kind label")
        check("### 2026-09-15 · [wa] · Blake Chen (无档) · owner promised a proposal" in text and "- 机器猜: (work · 答应了没交) 无" in text,
              "without a note: plain name, guess none")
        check("- 🗣 「ok waiting for your proposal」" in text, "no quote_at: no time")
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
                              today="2026-09-15") == 1, "enabled in the live config: appended")
        et = ebuf.read_text(encoding="utf-8")
        check("- Alex Rivera side: to place" in et and "- machine guess: (Shop · money) none" in et and "- status: open" in et
              and '- 🗣 09-14 09:05 "deposit by friday"' in et and et.rstrip().endswith("## Placed"),
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
        rows = conn.execute("SELECT COUNT(*) FROM intake_seen").fetchone()[0]
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        conn.close()
        check(rows == 3 and tables == ["intake_seen"], "state database holds only intake_seen, three rows")

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
        src = Path(__file__).read_text(encoding="utf-8")
        check(chr(0x2014) not in src and chr(0x2013) not in src, "no em dash or en dash in this file")
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)
    return check.done()


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description="intake buffer: count, list, place; evening push status")
    ap.add_argument("--config", default=None, help="live/config.json (default: env WA_LIVE_CONFIG or ~/WhatsApp-Live/live/config.json)")
    ap.add_argument("--buffer", default=None, help="buffer path (default: intake.buffer from the config)")
    ap.add_argument("--selftest", action="store_true", help="temp files only")
    ap.add_argument("--count-open", action="store_true", help="print the number of open entries")
    ap.add_argument("--list-open", action="store_true", help="list open entries, numbered from 1")
    ap.add_argument("--mark-placed", type=int, metavar="N", help="command-base intake mode only: mark entry N (from 1) placed")
    ap.add_argument("--to", default="", help="with --mark-placed: where it went ('task [[Project]]', 'calendar 2026-09-19 15:00', 'drop')")
    ap.add_argument("--evening-status", action="store_true", help="JSON for the evening push job")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
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
    if a.list_open:
        listing = format_listing(list_open(buffer))
        if listing:
            print(listing)
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
