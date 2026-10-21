#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wa_today.py · the Today brief: per project, what today's judged conversations and the open intake entries mean.

Read only toward the owner's vault. The board shows the brief; every proposal on it is a card the owner presses
(OK / No); placing happens later in the owner's own session through the intake mode. This module never calls a model
itself (the daemon hands `build` a `call` function), never imports the daemon, never writes into the vault, and never
writes the owner's own reply text anywhere except the brief run's `prompt.md` (the run folder also keeps the model's raw
answer); before anything else is stored, `scrub_replies` replaces any stretch of the model's answer that repeats a reply
(at least `today.quote_min_chars` CJK characters, or 4 Latin words with 6 letters or digits in all).

Inputs of one brief:
  - today's latest successful triage run per conversation (state tables `runs` and `conversations`)
  - the open intake entries (wa_intake.list_open), each tied to a conversation when the person's name matches
  - the owner's projects, discovered from the vault's shape (wings, lanes, `_<Project>-Brief.md`, `Tasks/`; see BUSINESS_WING)
  - the owner's replies after the last judgment (through the daemon's `outbound_since` hook, prompt only)

What it keeps (all in the daemon's own state database, created here, never dropped):
  today_brief      one row per build (the brief JSON, its input fingerprint, usage), latest 48 per day
  today_proposals  one row per card (stable id from project, kind and target; rev counts content changes)
  today_assign     which conversation belongs to which project (auto from a brief, negative from the owner)
and `<live>/today/<date>.json` (the latest brief of that day, 0600).

CLI
  wa_today.py --config P --build --dry-run       print the prompt and the candidates; never calls a model, writes nothing
  wa_today.py --config P --api                   print what GET /api/today would answer
  wa_today.py --fixture-check DIR [--vault V]    replay a run folder (result.txt + inputs.json) through the validator
  wa_today.py --selftest                         temp files only, synthetic data only
Python 3.9 standard library only.
"""

import argparse
import datetime as dt
import difflib
import hashlib
import html.entities as html_entities
import inspect
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unicodedata
import urllib.parse as urllib_parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import wa_common as WC  # noqa: E402
except Exception:  # the brief degrades (no vocabulary, local helpers) instead of failing to load
    WC = None
try:
    import wa_intake as WI  # noqa: E402
except Exception:  # without the intake module there are no entries and every quote is a gist
    WI = None

VERSION = "1.3.0"
PAYLOAD = Path(__file__).resolve().parent.parent
# the my-second-brain product names a business wing folder with this suffix (its scaffold and its deck find projects this way)
BUSINESS_WING = "-Business-Wing"
_TEST_WING = "04_Crumb" + BUSINESS_WING          # the invented school of the selftest and fixtures

KINDS = ("update", "create", "seed", "drop")
STATS = ("move", "ball", "stuck", "quiet", "seed")
STATUSES = ("pending", "ok", "no", "superseded", "expired")
LIVE = ("pending", "ok")
DECIDE_ACTIONS = ("ok", "no", "note", "pending")
UNASSIGNED = "unassigned"
EXPIRE_DAYS = 7
HISTORY_KEEP = 48
TASK_LIST_MAX = 30
MATERIAL_MAX_CHARS = 1500

NOW_MAX = 400
NOTE_MAX = 500
MEAN_MAX = 600
REPLY_MAX = 200
REPLIES_PER_SOURCE = 3
FM_KEYS = ("status", "due", "waiting_on", "priority")
FALLBACK_TASK_STATUSES = ["not-started", "in-progress", "waiting", "blocked", "done"]
CLOSED_TASK = ("done", "killed", "cancelled", "dropped")
STAT_ORDER = ("stuck", "ball", "move", "seed", "quiet")
TRIAGE_KINDS = ("direct", "group")
VERIFIED_MARKS = ("🗣", "🎙", "📄")

DEFAULTS = {
    "enabled": False,
    "model": "claude-sonnet-5",
    "every_minutes": 30,
    "manual_min_gap_seconds": 60,
    "max_reads": 6,
    "quote_min_chars": 6,
    "extra_rules": "",
}
_RANGES = {"every_minutes": (5, 1440), "manual_min_gap_seconds": (10, 3600), "max_reads": (0, 20), "quote_min_chars": (2, 40)}
EXTRA_RULES_MAX = 2000

L = {
    "zh": {
        "kind": {"update": "改任务", "create": "新任务", "seed": "新 Project 种子", "drop": "不用归"},
        "stat": {"move": "推进", "ball": "等你", "stuck": "卡住", "quiet": "顺利", "seed": "还没 Project 在管"},
        "status": {"pending": "等你按", "ok": "好", "no": "不用", "superseded": "收起", "expired": "过期"},
        "unassigned": "还没对上 Project",
        "ok": "好", "no": "不用",
        "gist": "大意: ",
        "retired_placed": "条目已归到 %s, 这张卡收起了",
        "retired_placed_none": "条目已归位或不在了, 这张卡收起了",
        "retired_settled": "你已处理: %s",
        "retired_replied": "你已经回了, 收起: %s",
        "retired_expired": "放了 %d 天没按, 收起了",
        "placed_note": "其中 %d 条已归到 %s",
        "placed_note_none": "其中 %d 条已归位",
        "replied_note": "你 %s 回过这个对话, 卡上的建议可能已经过时",
        "close_note": "看了你的回复, 这件可能已经办了: %s",
        "tail_live": "%d 张卡等你按",
        "tail_rejected": "%d 条建议被程式挡下 (没有来源或对不上)",
        "tail_partial": "这次只重算了 %d 个 Project, 其余照上一版",
        "tail_reads": "这次自己打开了 %d 个档",
        "note_reads_over": "模型打开了 %d 个档, 超过上限 %d",
        "note_doctrine": "宪法里读不到任务状态, 先用默认五个",
        "note_no_doctrine": "paths.doctrine 没设, 任务状态先用默认五个",
        "note_shape": "vault 不是 my-second-brain 的形状, 改为搜 _*-Brief.md",
        "note_vault": "vault 路径不在, 没有 Project 可对",
        "note_stat": "%s 的 stat 不认得 (%s), 当 顺利",
        "note_fm_key": "%s 的 fm_set 里 %s 不收",
        "note_fm_status": "%s 的状态 %s 不在任务状态表里, 拿掉",
        "note_fm_due": "%s 的期限 %s 不是 YYYY-MM-DD, 拿掉",
        "note_close": "收卡 %s 跳过: 对不上一张你已回过的卡",
        "note_not_here": "%s 里拿掉 %s: 你说过它不属于这个 Project",
        "reply_ref": "(你的回覆, 不抄)",
        "note_no_inputs": "没有 inputs.json: 每个代号都当不认得",
        "settle_keys": "这张卡有 intake 条目, 条目归位后一分钟内会自己收起",
        "settle_rev": "这张卡变了, 再看一次",
        "settle_ambiguous": "这个编号对到不止一张卡, 多给几位",
        "settle_not_found": "找不到这张卡",
        "settle_status": "这张卡已经不在等你了",
        "settle_to": "要说归到哪里",
    },
    "en": {
        "kind": {"update": "update task", "create": "new task", "seed": "project seed", "drop": "no placing needed"},
        "stat": {"move": "moving", "ball": "your move", "stuck": "stuck", "quiet": "quiet", "seed": "no project yet"},
        "status": {"pending": "waiting for you", "ok": "OK", "no": "No", "superseded": "retired", "expired": "expired"},
        "unassigned": "not matched to a project",
        "ok": "OK", "no": "No",
        "gist": "gist: ",
        "retired_placed": "its entries were placed in %s; this card is retired",
        "retired_placed_none": "its entries were placed or are gone; this card is retired",
        "retired_settled": "you handled it: %s",
        "retired_replied": "you already replied, retired: %s",
        "retired_expired": "nobody pressed it for %d days, retired",
        "placed_note": "%d of its entries were placed in %s",
        "placed_note_none": "%d of its entries were placed",
        "replied_note": "you replied in this conversation at %s; the proposal may be out of date",
        "close_note": "from your reply, this may already be done: %s",
        "tail_live": "%d cards wait for you",
        "tail_rejected": "%d proposals stopped by the program (no source, or no match)",
        "tail_partial": "only %d projects were recomputed this time; the rest is the previous version",
        "tail_reads": "the run opened %d files on its own",
        "note_reads_over": "the model opened %d files, over the limit of %d",
        "note_doctrine": "the doctrine has no readable task statuses; using the five defaults",
        "note_no_doctrine": "paths.doctrine is not set; using the five default task statuses",
        "note_shape": "the vault does not have the my-second-brain shape; searched for _*-Brief.md instead",
        "note_vault": "the vault path does not exist; no projects to match",
        "note_stat": "stat of %s not known (%s), read as quiet",
        "note_fm_key": "fm_set of %s: key %s not accepted",
        "note_fm_status": "status %s of %s is not in the task status list, dropped",
        "note_fm_due": "due %s of %s is not YYYY-MM-DD, dropped",
        "note_close": "close %s skipped: it does not match one card you replied to",
        "note_not_here": "%s: dropped %s, you said it does not belong to this project",
        "reply_ref": "(your reply, not copied)",
        "note_no_inputs": "no inputs.json: every code counts as unknown",
        "settle_keys": "this card has intake entries; it retires by itself within a minute when its entries are placed",
        "settle_rev": "this card changed, look again",
        "settle_ambiguous": "this id matches more than one card, give more characters",
        "settle_not_found": "no such card",
        "settle_status": "this card is no longer waiting for you",
        "settle_to": "say where it went",
    },
}

STAT_HELP = {"move": "the project moved forward today", "ball": "the next step is the owner's",
             "stuck": "something blocks it and the owner has to unblock it", "quiet": "nothing needs the owner",
             "seed": "work that has no project yet"}
KIND_HELP = {"update": "change one existing task file in that project's Tasks/ (name the file exactly as listed)",
             "create": "a new task file in that project's Tasks/ (File-Name.md: capitalised words joined by hyphens, no date)",
             "seed": "work that deserves a project that does not exist yet (give it a short name in `task`)",
             "drop": "an open intake entry that needs no placing (cite its E code)"}


# ---------------------------------------------------------------- small helpers

def _lang(lang):
    return "en" if str(lang or "").lower().startswith("en") else "zh"


def _T(lang):
    return L[_lang(lang)]


def _sha1(s):
    return hashlib.sha1(str(s).encode("utf-8")).hexdigest()


def _jd(v):
    return json.dumps(v, ensure_ascii=False, sort_keys=True)


def _jl(v, default):
    if v is None or v == "":
        return default
    try:
        got = json.loads(v)
    except (TypeError, ValueError):
        return default
    return got if got is not None else default


def _now_dt(now=None):
    if now is None:
        return dt.datetime.now().replace(microsecond=0)
    if isinstance(now, dt.datetime):
        return now.replace(microsecond=0, tzinfo=None)
    if isinstance(now, dt.date):
        return dt.datetime(now.year, now.month, now.day)
    s = str(now).strip().replace(" ", "T")
    try:
        return dt.datetime.fromisoformat(s[:19])
    except ValueError:
        return dt.datetime.now().replace(microsecond=0)


def _iso(d):
    return d.isoformat(timespec="seconds")


def _ts(s):
    """A stored time as a comparable 'YYYY-MM-DDTHH:MM:SS' string ('' when empty)."""
    return str(s or "").strip().replace(" ", "T")[:19]


def _one(s):
    """One line of text. A list or an object the model put where text belongs becomes its values joined by spaces, so
    no brackets or keys sit between the words (the reply scrub sees the words as written)."""
    if isinstance(s, dict):
        s = " ".join(_one(v) for v in s.values())
    elif isinstance(s, (list, tuple)):
        s = " ".join(_one(v) for v in s)
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _cut(s, limit):
    s = _one(s)
    return s if len(s) <= limit else s[:limit - 1] + "…"


_DASH_RE = re.compile("\\s*(?:\u2014|\u2013|-{2,})+\\s*")


def _house(s):
    """House punctuation: em dash, en dash and double hyphen become a comma."""
    s = _DASH_RE.sub(", ", _one(s) if isinstance(s, (dict, list, tuple)) else str(s or ""))
    s = re.sub(r",\s*,", ",", s)
    return s.strip().lstrip(",").strip()


def _fm(text):
    if WC is not None:
        return WC.parse_frontmatter(text)
    if not text or not text.startswith("---"):
        return {}, text or ""
    lines = text.split("\n")
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            fm = {}
            for ln in lines[1:i]:
                if ":" in ln and not ln[:1].isspace():
                    k, v = ln.split(":", 1)
                    fm[k.strip()] = v.strip().strip("'\"")
            return fm, "\n".join(lines[i + 1:])
    return {}, text


def _write_json(path, obj, mode=0o600):
    if WC is not None:
        return WC.write_json_atomic(path, obj, mode=mode)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.chmod(str(tmp), mode)
    os.replace(str(tmp), str(path))
    return path


def _read_text(p):
    try:
        return Path(p).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None


def _rows(conn, sql, args=()):
    """Rows as dicts whatever the connection's row factory; [] when a table is missing."""
    if conn is None:
        return []
    try:
        cur = conn.execute(sql, args)
    except sqlite3.Error:
        return []
    cols = [c[0] for c in (cur.description or [])]
    return [dict(zip(cols, tuple(r))) for r in cur.fetchall()]


def _has_table(conn, name):
    return bool(_rows(conn, "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)))


# ---------------------------------------------------------------- config and tables

def tcfg(cfg):
    """The `today` section with defaults filled; a value of the wrong type or out of range falls back to the default."""
    raw = (cfg or {}).get("today") if isinstance(cfg, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    out = dict(DEFAULTS)
    if isinstance(raw.get("enabled"), bool):
        out["enabled"] = raw["enabled"]
    if isinstance(raw.get("model"), str) and raw["model"].strip():
        out["model"] = raw["model"].strip()
    for k, (lo, hi) in _RANGES.items():
        v = raw.get(k)
        if isinstance(v, bool) or not isinstance(v, int):
            continue
        if lo <= v <= hi:
            out[k] = v
    if isinstance(raw.get("extra_rules"), str):
        out["extra_rules"] = raw["extra_rules"][:EXTRA_RULES_MAX]
    return out


_COLS = {
    "today_brief": (("date", "TEXT"), ("built_at", "TEXT"), ("inputs_sig", "TEXT"), ("run_dir", "TEXT"),
                    ("usage_json", "TEXT"), ("json", "TEXT")),
    "today_proposals": (("id", "TEXT"), ("date", "TEXT"), ("rev", "INTEGER"), ("content_hash", "TEXT"), ("project", "TEXT"),
                        ("kind", "TEXT"), ("task", "TEXT"), ("now", "TEXT"), ("fm_set", "TEXT"), ("ev", "TEXT"),
                        ("jids", "TEXT"), ("entry_keys", "TEXT"), ("status", "TEXT"), ("note", "TEXT"),
                        ("newer_json", "TEXT"), ("decided_at", "TEXT"), ("updated_at", "TEXT"), ("retired_json", "TEXT"),
                        ("placed_json", "TEXT"), ("replied_json", "TEXT"), ("close_json", "TEXT")),
    "today_assign": (("key", "TEXT"), ("project", "TEXT"), ("kind", "TEXT"), ("since", "TEXT")),
}


def ensure_tables(conn):
    """Create the three tables when missing; add later columns with ALTER TABLE; never drop anything."""
    conn.execute("CREATE TABLE IF NOT EXISTS today_brief(date TEXT, built_at TEXT, inputs_sig TEXT, run_dir TEXT, "
                 "usage_json TEXT, json TEXT)")
    conn.execute("CREATE INDEX IF NOT EXISTS today_brief_date ON today_brief(date, built_at)")
    conn.execute("CREATE TABLE IF NOT EXISTS today_proposals(id TEXT PRIMARY KEY, date TEXT, rev INTEGER, content_hash TEXT, "
                 "project TEXT, kind TEXT, task TEXT, now TEXT, fm_set TEXT, ev TEXT, jids TEXT, entry_keys TEXT, status TEXT, "
                 "note TEXT, newer_json TEXT, decided_at TEXT, updated_at TEXT, retired_json TEXT, placed_json TEXT, "
                 "replied_json TEXT, close_json TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS today_assign(key TEXT, project TEXT, kind TEXT, since TEXT, "
                 "PRIMARY KEY (key, project, kind))")
    for table, cols in _COLS.items():
        have = set(r["name"] for r in _rows(conn, "PRAGMA table_info(%s)" % table))
        for col, typ in cols:
            if col not in have:
                conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, typ))
    conn.commit()


# ---------------------------------------------------------------- the owner's projects (found the way my-second-brain's deck finds them)

def _subdirs(p):
    try:
        return sorted([x for x in Path(p).iterdir() if x.is_dir() and not x.name.startswith(".")], key=lambda x: x.name)
    except OSError:
        return []


def _find_brief(folder):
    want = folder / ("_%s-Brief.md" % folder.name)
    if want.is_file():
        return want
    try:
        cands = sorted(x for x in folder.glob("_*-Brief.md") if x.is_file())
    except OSError:
        cands = []
    return cands[0] if cands else None


def _skip_walk(name):
    return name.startswith(".") or name.startswith("98_") or name.startswith("99_")


def _walk_brief_folders(v, max_depth=6):
    out = []
    for dirpath, dirnames, filenames in os.walk(str(v)):
        rel = Path(dirpath).relative_to(v)
        depth = 0 if str(rel) == "." else len(rel.parts)
        dirnames[:] = sorted(d for d in dirnames if not _skip_walk(d) and depth < max_depth)
        if depth == 0:
            continue
        if any(re.match(r"^_.+-Brief\.md$", f) for f in filenames):
            out.append(Path(dirpath))
    return out


def _brief_title(body):
    for line in str(body or "").split("\n"):
        m = re.match(r"^#\s+(.+?)\s*$", line)
        if m:
            return m.group(1).strip()
    return ""


def _task_statuses(path):
    """(statuses, ok): ok False when the doctrine could not be read or holds no task status line."""
    if not str(path or "").strip():
        return list(FALLBACK_TASK_STATUSES), False
    text = _read_text(Path(os.path.expanduser(str(path))))
    if text is None:
        return list(FALLBACK_TASK_STATUSES), False
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if "cb: task" not in line:
            continue
        for j in range(i, min(i + 3, len(lines))):
            m = re.search(r"status:\s*\[([^\]]*)\]", lines[j])
            if m:
                vals = [x.strip().strip("`'\"").strip() for x in m.group(1).split(",")]
                vals = [x for x in vals if x]
                if vals:
                    return vals, True
    return list(FALLBACK_TASK_STATUSES), False


def task_statuses(doctrine_path):
    """The task status list from the doctrine's `task:` line (`status: [a, b, c]` on the line holding `cb: task` or
    one of the two after it); the product's five when it cannot be read."""
    return _task_statuses(doctrine_path)[0]


def project_index(vault, doctrine=None, lang="zh"):
    """({pid: {pid, name, lane, folder, brief, tasks_dir}}, notes). pid is the project folder relative to the vault.
    Wings and lanes come from the vault's own folder names (a top folder ending in BUSINESS_WING, then `02_Work/<Lane>/<Project>/`;
    `*Personal-Wing/Personal-Projects/<Project>/`); a vault without that shape falls back to every `_*-Brief.md` at
    depth 6 or less. Briefs whose status is done or killed are left out."""
    T = _T(lang)
    notes = []
    if doctrine is not None and not _task_statuses(doctrine)[1]:
        notes.append(T["note_doctrine"])
    v = Path(os.path.expanduser(str(vault or "")))
    if not str(vault or "").strip() or not v.is_dir():
        notes.append(T["note_vault"])
        return {}, notes
    tops = _subdirs(v)
    biz = [t for t in tops if t.name.endswith(BUSINESS_WING)]
    pers = [t for t in tops if t.name.endswith("Personal-Wing")]
    folders = []
    if biz or pers:
        for w in biz:
            for work in _subdirs(w):
                if work.name == "02_Work" or work.name.endswith("_Work"):
                    for lane in _subdirs(work):
                        for proj in _subdirs(lane):
                            folders.append((proj, lane.name))
        for w in pers:
            for pp in _subdirs(w):
                if pp.name.endswith("Personal-Projects"):
                    for proj in _subdirs(pp):
                        folders.append((proj, "Personal-Projects"))
    else:
        notes.append(T["note_shape"])
        folders = [(f, "") for f in _walk_brief_folders(v)]
    index = {}
    for folder, lane in folders:
        brief = _find_brief(folder)
        fm, body = ({}, "")
        if brief is not None:
            fm, body = _fm(_read_text(brief) or "")
        status = str(fm.get("status") or "").strip().lower()
        if status in ("done", "killed"):
            continue
        pid = folder.relative_to(v).as_posix()
        index[pid] = {"pid": pid, "name": _brief_title(body) or folder.name, "lane": lane, "folder": folder.name,
                      "brief": brief.relative_to(v).as_posix() if brief is not None else "",
                      "tasks_dir": pid + "/Tasks"}
    return index, notes


_HEAD_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_STATE_NAMES = ("current state", "next step", "next steps", "status")


def _demote(line):
    m = _HEAD_RE.match(line)
    return ("#### " + m.group(2)) if m else line


def _state_sections(body):
    lines = str(body or "").split("\n")
    out, level = [], 0
    for line in lines:
        m = _HEAD_RE.match(line)
        if m:
            lv = len(m.group(1))
            name = m.group(2).strip().rstrip(":").strip().lower()
            if level and lv <= level:
                level = 0
            if not level and name in _STATE_NAMES:
                level = lv
                out.append(_demote(line))
                continue
        if level:
            out.append(_demote(line))
    return "\n".join(out).strip()


def _task_files(vault, pinfo):
    d = Path(os.path.expanduser(str(vault or ""))) / pinfo.get("tasks_dir", "")
    try:
        return sorted(x.name for x in d.iterdir() if x.is_file() and x.name.endswith(".md") and not x.name.startswith("."))
    except OSError:
        return []


def project_material(vault, pinfo, statuses):
    """{"fm", "state", "tasks", "n_tasks"}: the brief's status, stage, due and priority; its Current state / Next step /
    Status sections (else the start of the body), headings demoted to ####; task notes from Tasks/, open ones first."""
    v = Path(os.path.expanduser(str(vault or "")))
    fm, body = {}, ""
    if pinfo.get("brief"):
        fm, body = _fm(_read_text(v / pinfo["brief"]) or "")
    fmo = dict((k, str(fm.get(k)).strip()) for k in ("status", "stage", "due", "priority") if str(fm.get(k) or "").strip())
    state = _state_sections(body)
    if not state:
        state = "\n".join(_demote(x) for x in str(body or "").strip().split("\n"))
    state = state[:MATERIAL_MAX_CHARS].strip()
    tasks = []
    known = set(statuses or [])
    for name in _task_files(vault, pinfo):
        tfm, tbody = _fm(_read_text(v / pinfo["tasks_dir"] / name) or "")
        if str(tfm.get("cb") or "").strip() != "task" and not str(tfm.get("status") or "").strip():
            continue
        first = ""
        for line in str(tbody or "").split("\n"):
            s = line.strip()
            if s and not s.startswith("#") and s != "---":
                first = _cut(s, 120)
                break
        st = str(tfm.get("status") or "").strip()
        tasks.append({"name": name, "status": st, "due": str(tfm.get("due") or "").strip(),
                      "waiting_on": str(tfm.get("waiting_on") or "").strip(), "first": first,
                      "known_status": (st in known) if known else True})
    tasks.sort(key=lambda x: (x["status"].lower() in CLOSED_TASK, x["name"]))
    return {"fm": fmo, "state": state, "tasks": tasks[:TASK_LIST_MAX], "n_tasks": len(tasks)}


# ---------------------------------------------------------------- what happened today

def _latest_runs(conn, day=None):
    """{jid: run row} of the latest ok triage run per conversation (that day, or any day when day is None)."""
    sql = ("SELECT id, started_at, jid, name, run_dir, output, kind FROM runs WHERE ok=1 AND kind IN ('direct','group') "
           "AND jid IS NOT NULL")
    args = ()
    if day:
        sql += " AND substr(started_at,1,10)=?"
        args = (day,)
    out = {}
    for r in _rows(conn, sql + " ORDER BY started_at, id", args):
        out[r["jid"]] = r
    return out


def today_sources(conn, day):
    """Today's latest ok triage run per conversation, joined with its conversation row."""
    runs = _latest_runs(conn, day)
    convs = dict((c.get("jid"), c) for c in _rows(conn, "SELECT * FROM conversations"))
    out = []
    for jid, r in sorted(runs.items(), key=lambda kv: (_ts(kv[1].get("started_at")), kv[1].get("id") or 0)):
        c = convs.get(jid) or {}
        j = _jl(r.get("output"), {})
        j = j if isinstance(j, dict) else {}
        caps = []
        for cp in (j.get("captures") or []):
            if isinstance(cp, dict) and _one(cp.get("text")):
                caps.append({"kind": _one(cp.get("kind")), "text": _cut(cp.get("text"), 160)})

        def pick(k):
            v = c.get(k)
            return v if v not in (None, "") else j.get(k)
        nm = pick("needs_me")
        out.append({"key": jid, "name": _one(c.get("name") or r.get("name") or jid), "kind": _one(c.get("kind") or r.get("kind")),
                    "world": _one(pick("world")), "needs_me": bool(nm) and str(nm) not in ("0", "false", "False"),
                    "situation": _one(pick("situation")), "stake": _one(pick("stake")), "summary": _one(pick("summary")),
                    "captures": caps, "run_id": r.get("id"), "run_dir": str(r.get("run_dir") or ""),
                    "last_out_at": _ts(c.get("last_out_at")), "started_at": _ts(r.get("started_at"))})
    return out


def _norm_name(s):
    s = str(s or "").strip()
    s = s.replace("[[", "").replace("]]", "")
    if "|" in s:
        s = s.split("|", 1)[0]
    s = re.sub(r"\s*\((?:无档|no note)\)\s*$", "", s)
    s = re.sub(r"[-_\s]+", " ", s).strip().lower()
    return s


def _parse_title(title):
    parts = str(title or "").split(" · ", 3)
    while len(parts) < 4:
        parts.append("")
    date, src, who, text = parts
    return date.strip(), src.strip().strip("[]"), who.strip(), text.strip()


def _quote_parts(line):
    if WI is None:
        return "", "", ""
    m = WI.QUOTE_LINE_RE.match(str(line or ""))
    if not m:
        return "", "", ""
    q = m.group("q") if m.group("q") is not None else m.group("e")
    return m.group("mark") or "", m.group("at") or "", q or ""


def open_entries(buffer_path, conn):
    """The open intake entries, each with the source key of its conversation: the jid of a conversation whose name
    matches the entry's person, else "<src>:<name>"."""
    if WI is None or not str(buffer_path or "").strip():
        return []
    try:
        lst = WI.list_open(buffer_path)
    except Exception:
        return []
    names = {}
    for c in _rows(conn, "SELECT jid, name FROM conversations ORDER BY jid"):
        n = _norm_name(c.get("name"))
        if n and n not in names:
            names[n] = c.get("jid")
    out = []
    for e in lst:
        date, src, who, text = _parse_title(e.get("title"))
        name = re.sub(r"\s*\((?:无档|no note)\)\s*$", "", who.replace("[[", "").replace("]]", "")).strip()
        lines = e.get("lines") or []
        mark, at, quote = _quote_parts(lines[0] if lines else "")
        key = names.get(_norm_name(who)) or "%s:%s" % (src or "wa", name)
        out.append({"ekey": e.get("key") or (WI.entry_key(e) if WI else ""), "index": e.get("index"), "date": date,
                    "src": src, "who": who, "name": name, "text": text, "mark": mark, "at": at, "quote": quote,
                    "verified": mark in VERIFIED_MARKS, "key": key, "title": str(e.get("title") or ""),
                    "quote_line": str(lines[0]).strip() if lines else ""})
    return out


def _live_rows(conn):
    return _rows(conn, "SELECT * FROM today_proposals WHERE status IN ('pending','ok') ORDER BY date, id")


def _last_out(conn, jids):
    out = {}
    for j in sorted(set(jids)):
        r = _rows(conn, "SELECT last_out_at FROM conversations WHERE jid=?", (j,))
        out[j] = _ts(r[0].get("last_out_at")) if r else ""
    return out


def _neg_rows(conn):
    return _rows(conn, "SELECT key, project, since FROM today_assign WHERE kind='negative' ORDER BY since, key, project")


def inputs_sig(conn, day, buffer_path):
    """(sig, parts): sha1 over today's latest ok triage run per conversation, the open entry keys, the latest negative
    assignment and the owner's last reply time on every conversation a live card or a source names."""
    runs = dict((j, r.get("id")) for j, r in _latest_runs(conn, day).items())
    keys = []
    if WI is not None and str(buffer_path or "").strip():
        try:
            keys = [e.get("key") for e in WI.list_open(buffer_path)]
        except Exception:
            keys = []
    open_h = _sha1("\n".join(sorted(str(k) for k in keys)))
    neg = _neg_rows(conn)
    neg_at = max([_ts(r.get("since")) for r in neg] or [""])
    jids = set(runs)
    for c in _live_rows(conn):
        jids.update(_jl(c.get("jids"), []))
    rep = _last_out(conn, jids)
    replies = _sha1("\n".join("%s|%s" % (j, rep[j]) for j in sorted(rep)))
    parts = {"day": day, "runs": dict((k, runs[k]) for k in sorted(runs)), "open": open_h, "neg": neg_at, "replies": replies}
    return _sha1(_jd(parts)), parts


def _latest_build(conn, day=None):
    sql = "SELECT rowid AS rid, date, built_at, inputs_sig, run_dir, usage_json, json FROM today_brief"
    args = ()
    if day:
        sql += " WHERE date=?"
        args = (day,)
    rows = _rows(conn, sql + " ORDER BY built_at DESC, rowid DESC LIMIT 1", args)
    return rows[0] if rows else None


def decide_tick(conn, cfg, day, now, last_sig, buffer_path, gate_down=False, busy=False, manual=False, running_n=0,
                concurrency=2):
    """Whether a brief should be built now: {"go", "why", "sig", "parts"}. why: disabled, already, busy, same_sig,
    too_soon, brain_down, queued, go."""
    t = tcfg(cfg)
    try:
        sig, parts = inputs_sig(conn, day, buffer_path)
    except Exception:
        sig, parts = "", {}

    def out(why):
        return {"go": why == "go", "why": why, "sig": sig, "parts": parts}
    if not t["enabled"]:
        return out("disabled")
    if busy:
        return out("already" if manual else "busy")
    n = _now_dt(now)
    last = _latest_build(conn)
    age = None
    if last and last.get("built_at"):
        age = (n - _now_dt(last["built_at"])).total_seconds()
    if manual and age is not None and age < t["manual_min_gap_seconds"]:
        return out("already")
    if not manual:
        if sig and (sig == last_sig or (last and last.get("inputs_sig") == sig)):
            return out("same_sig")
        if age is not None and age < t["every_minutes"] * 60:
            return out("too_soon")
    if gate_down:
        return out("brain_down")
    if running_n >= concurrency:
        return out("queued")
    return out("go")


# ---------------------------------------------------------------- candidates (zero model)

def _load_topic_rules(path):
    if not path:
        return []
    doc = _jl(_read_text(Path(os.path.expanduser(str(path)))), None)
    if not isinstance(doc, dict):
        return []
    rules = []
    for r in doc.get("rules") or []:
        if isinstance(r, dict):
            keys = [str(k).lower() for k in (r.get("keys") or []) if str(k).strip()]
            files = [str(f).strip().lstrip("/") for f in (r.get("files") or []) if str(f).strip()]
            if keys and files:
                rules.append((keys, files))
    return rules


def _terms(pinfo):
    words = [w.lower() for w in re.split(r"[-_\s]+", str(pinfo.get("folder") or "")) if re.match(r"^[A-Za-z]{4,}$", w)]
    title = str(pinfo.get("name") or "").strip().lower()
    return words, (title if len(title) >= 4 else "")


def _texts(sources, entries):
    out = {}
    for s in sources:
        bits = [s.get("name"), s.get("situation"), s.get("stake"), s.get("summary")]
        bits += [c.get("text") for c in s.get("captures") or []]
        out.setdefault(s["key"], []).extend(bits)
    for e in entries:
        out.setdefault(e["key"], []).extend([e.get("name"), e.get("text"), e.get("quote")])
    return dict((k, " ".join(_one(b) for b in v if b).lower()) for k, v in out.items())


def candidates(conn, sources, entries, index, vault, topic_map_path):
    """{source key: [pid]} with no model: auto assignments, project names in the text, topic map rules that point into
    a project folder; minus the owner's negative assignments. Nothing matched: [UNASSIGNED]."""
    texts = _texts(sources, entries)
    neg = set((r["key"], r["project"]) for r in _neg_rows(conn))
    auto = {}
    for r in _rows(conn, "SELECT key, project FROM today_assign WHERE kind='auto' ORDER BY since, project"):
        auto.setdefault(r["key"], []).append(r["project"])
    rules = _load_topic_rules(topic_map_path)
    terms = dict((pid, _terms(p)) for pid, p in index.items())
    out = {}
    for key in sorted(texts):
        text = texts[key]
        got = []
        for pid in auto.get(key, []):
            if pid in index:
                got.append(pid)
        for pid in sorted(index):
            words, title = terms[pid]
            hit = any(re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(w), text) for w in words)
            if hit or (title and title in text):
                got.append(pid)
        for keys, files in rules:
            if any(k in text for k in keys):
                for f in files:
                    for pid in sorted(index):
                        if f == pid or f.startswith(pid + "/"):
                            got.append(pid)
        seen, final = set(), []
        for pid in got:
            if pid not in seen and (key, pid) not in neg:
                seen.add(pid)
                final.append(pid)
        out[key] = final or [UNASSIGNED]
    return out


def compute_scope(prev, sources, entries, cands, neg_rows, replies):
    """{"full", "changed", "projects"} (plus "entries": the entry keys not seen before). Full when there is no previous
    brief; else the conversations whose run moved, entries the previous build did not see, replies that moved and new
    negative rows; projects = their candidates plus the previous blocks that cited them."""
    if not prev:
        changed = sorted(set([s["key"] for s in sources] + [e["key"] for e in entries]))
        projects = set()
        for k in changed:
            projects.update(cands.get(k) or [UNASSIGNED])
        return {"full": True, "changed": changed, "entries": sorted(e["ekey"] for e in entries), "projects": sorted(projects)}
    seen = prev.get("seen") or {}
    s_runs = seen.get("runs") or {}
    s_entries = set(seen.get("entries") or [])
    s_replies = seen.get("replies") or {}
    s_neg = set(seen.get("neg") or [])
    changed, new_e = set(), []
    for s in sources:
        if s_runs.get(s["key"]) != s.get("run_id"):
            changed.add(s["key"])
    for e in entries:
        if e["ekey"] not in s_entries:
            changed.add(e["key"])
            new_e.append(e["ekey"])
    for j, at in (replies or {}).items():
        if _ts(at) != _ts(s_replies.get(j)):
            changed.add(j)
    for r in neg_rows or []:
        if "%s|%s" % (r["key"], r["project"]) not in s_neg:
            changed.add(r["key"])
    projects = set()
    for k in changed:
        projects.update(cands.get(k) or [UNASSIGNED])
    for b in prev.get("projects") or []:
        if set(b.get("sources") or []) & changed:
            projects.add(b.get("id"))
    projects.discard(None)
    return {"full": False, "changed": sorted(changed), "entries": sorted(new_e), "projects": sorted(projects)}


# ---------------------------------------------------------------- quotes in the brain's text

_QSEG_RE = re.compile("「([^「」\n]*)」|『([^『』\n]*)』|\u201c([^\u201c\u201d\n]*)\u201d|\"([^\"\n]*)\"")
_CJK_RE = re.compile("[\u3400-\u9fff\uf900-\ufaff]")
_TAG_RE = re.compile(r"<[^<>\n]{0,200}>")
GATE_KEEP = ("exact", "excerpt", "stitched")


class QuoteGate(object):
    """Every quote-like segment in a text (「」 『』 curly or straight double quotes; at least min_chars CJK characters or
    4 latin words) is checked against the real words: found exactly, as an excerpt or stitched → 🗣「stored text」;
    anything else loses its quote marks and is marked as a gist. Tags are stripped first."""

    def __init__(self, messages, min_chars, lang="zh"):
        groups, loose = [], []
        for m in messages or []:
            if isinstance(m, list):
                groups.append([x for x in m if isinstance(x, dict)])
            elif isinstance(m, dict):
                loose.append(m)
        if loose:
            groups.append(loose)
        self.groups = [g for g in groups if g]
        self.min_chars = int(min_chars or DEFAULTS["quote_min_chars"])
        self.gist = _T(lang)["gist"]
        self.checked = self.kept = self.rewritten = 0

    def quote_like(self, s):
        return len(_CJK_RE.findall(s)) >= self.min_chars or len(re.findall(r"[A-Za-z0-9']+", s)) >= 4

    def resolve(self, q):
        if WI is None:
            return None
        for g in self.groups:
            try:
                r = WI.resolve_quote(q, g)
            except Exception:
                continue
            if r.get("how") in GATE_KEEP and r.get("text"):
                return r
        return None

    def __call__(self, text):
        s = _TAG_RE.sub("", str(text or ""))

        def rep(m):
            inner = next(g for g in m.groups() if g is not None)
            if s[:m.start()].rstrip().endswith("🗣") or not self.quote_like(inner):
                return m.group(0)
            self.checked += 1
            r = self.resolve(inner)
            if r:
                self.kept += 1
                return "🗣「%s」" % r["text"]
            self.rewritten += 1
            return "≈ %s%s" % (self.gist, inner.strip())
        return _QSEG_RE.sub(rep, s)

    def stats(self):
        return {"checked": self.checked, "kept": self.kept, "rewritten": self.rewritten}


# ---------------------------------------------------------------- the prompt

def _worlds_block(cfg, hooks):
    fn = (hooks or {}).get("worlds_block")
    if fn:
        try:
            got = fn()
            if got:
                return str(got)
        except Exception:
            pass
    lines = ["| key | label | business |", "|---|---|---|"]
    if WC is not None:
        v = WC.vocab(cfg or {})
        for w in v["worlds"]:
            lines.append("| `%s` | %s / %s | %s |" % (w["key"], w["label"], w["label_en"], "yes" if w.get("business") else "no"))
    return "\n".join(lines)


def build_prompt(ctx, t, lang):
    """The brief prompt: English, label words in the owner's language. Plain text, vault relative paths only."""
    T = _T(lang)
    owner = ctx.get("owner") or "the owner"
    out = []
    a = out.append
    a("# Today brief for %s" % owner)
    a("")
    a("You read what happened in %s's WhatsApp today (every conversation below was already judged by an earlier run) and "
      "the intake entries still open, and you say, per project, what it means. You change nothing: every proposal becomes "
      "a card on %s's board, and %s presses %s or %s. Answer with one JSON object and nothing else." %
      (owner, owner, owner, T["ok"], T["no"]))
    a("")
    a("## %s's worlds" % owner)
    a(ctx.get("worlds_block") or "")
    a("")
    a("## Words")
    a("stat, one per block:")
    for s in STATS:
        a("- `%s` (%s): %s" % (s, T["stat"][s], STAT_HELP[s]))
    a("kind, one per proposal:")
    for k in KINDS:
        a("- `%s` (%s): %s" % (k, T["kind"][k], KIND_HELP[k]))
    a("task statuses for `fm_set.status`: %s" % ", ".join(ctx.get("statuses") or []))
    a("`fm_set` keys: status, due (YYYY-MM-DD), waiting_on, priority. Nothing else.")
    a("")
    a("## Reading")
    if int(t.get("max_reads") or 0) > 0:
        a("You may open at most %d vault files on your own, and only the brief and task files named below (paths are "
          "relative to the vault). The material below is usually enough; open a file only when a proposal depends on it."
          % int(t["max_reads"]))
    else:
        a("Do not open any file; the material below is all you have.")
    a("")
    a("## Rules")
    a("- A source may belong to no project: `unassigned` is a legal answer.")
    a("- Do not force a match to an existing task; when nothing fits, propose `create` or say nothing.")
    a("- Every proposal names its sources (C or E codes); no source, no proposal.")
    a("- Quoted words only through sources; in `mean`, `now`, `one`, `short` write a gist, never a quote.")
    a("- `update` and `create` only in a P block. `seed` only in an `unassigned` or `seed:<name>` block.")
    a("- `drop` cites the E code of the entry that needs no placing.")
    a("- `close` only lists cards marked replied, when %s's reply shows the card's work is done." % owner)
    a("- An open card already covers its entries: do not propose a second card for the same E code.")
    a("- A source marked `not P3` was taken out of P3 by %s: never place it there again." % owner)
    a("- Never repeat %s's own reply words; say what the reply settled in your own words." % owner)
    a("- Punctuation: commas, colons, periods, parentheses; no dashes used as separators.")
    extra = str(t.get("extra_rules") or "").strip()
    if extra:
        a("")
        a("## Owner's own rules")
        a(extra)
    a("")
    a("## Output (one JSON object)")
    a('{"one": [["Project name", "one line"]] (at most 4), "short": "one line", '
      '"projects": [{"project": "P1 | unassigned | seed:<short name>", "stat": "move", "mean": "one or two sentences", '
      '"sources": ["C1", "E2"], "props": [{"kind": "update", "task": "File-Name.md", "now": "what to do now", '
      '"fm_set": {"status": "in-progress"}, "src": ["E2"]}]}], "close": [{"id": "<card id prefix, 8+>", "why": "one sentence"}]}')
    a("")
    a("## 1 · Projects in scope")
    scope = ctx.get("scope_list") or []
    if not scope:
        a("(none)")
    for p in scope:
        a("")
        a("### %s · %s · %s · `%s`" % (p["code"], p["name"], p["lane"] or "-", p["pid"]))
        mat = p.get("material") or {}
        fm = mat.get("fm") or {}
        if p.get("brief"):
            a("brief: `%s` (%s)" % (p["brief"], ", ".join("%s %s" % (k, v) for k, v in sorted(fm.items())) or "no frontmatter"))
        if mat.get("state"):
            a(mat["state"])
        a("tasks in `%s/` (%d in all):" % (p["tasks_dir"], int(mat.get("n_tasks") or 0)))
        for tk in mat.get("tasks") or []:
            bits = [tk["status"] or "no status"]
            if tk.get("due"):
                bits.append("due %s" % tk["due"])
            if tk.get("waiting_on"):
                bits.append("waiting on %s" % tk["waiting_on"])
            if tk.get("first"):
                bits.append(tk["first"])
            a("  - `%s` · %s" % (tk["name"], " · ".join(bits)))
        if not mat.get("tasks"):
            a("  (no task notes)")
        cards = p.get("cards") or []
        a("open cards:" if cards else "open cards: none")
        for c in cards:
            a("  - card %s · %s · %s · %s · now: %s%s" % (c["id"][:8], c["kind"], c.get("task") or "-", c["status"],
                                                        _cut(c.get("now"), 160), " · replied" if c.get("replied") else ""))
    a("")
    a("## 2 · Conversations that changed")
    srcs = ctx.get("source_list") or []
    if not srcs:
        a("(none)")
    for s in srcs:
        a("")
        a("%s · %s · %s · %s · needs %s: %s%s" % (s["code"], s["name"], s.get("kind") or "-", s.get("world") or "-", owner,
                                                "yes" if s.get("needs_me") else "no",
                                                (" · not %s" % ", ".join(s["not_in"])) if s.get("not_in") else ""))
        for k in ("situation", "stake", "summary"):
            if s.get(k):
                a("  %s: %s" % (k, s[k]))
        for i, c in enumerate(s.get("captures") or []):
            a("  %s.%d %s · %s" % (s["code"], i + 1, c.get("kind") or "-", c.get("text") or ""))
        for r in (ctx.get("replies") or {}).get(s["key"], [])[:REPLIES_PER_SOURCE]:
            a('  - %s replied %s: "%s"' % (owner, r["at"], r["text"]))
    a("")
    a("## 3 · Open intake entries")
    ents = ctx.get("entry_list") or []
    if not ents:
        a("(none)")
    for e in ents:
        a("%s · %s · [%s] · %s · %s%s" % (e["code"], e.get("date") or "-", e.get("src") or "wa", e.get("name") or "?", e.get("text") or "",
                                          (" · not %s" % ", ".join(e["not_in"])) if e.get("not_in") else ""))
        if e.get("verified"):
            a("  " + (e.get("quote_line") or ""))
        elif e.get("quote"):
            a("  ≈ gist, not verbatim: %s" % e["quote"])
    a("")
    a("## 4 · The brief so far")
    prev = ctx.get("prev_lines") or []
    if not prev:
        a("(no brief yet today)")
    for line in prev:
        a("- " + line)
    a("")
    a("## 5 · Other active projects (not in scope)")
    others = ctx.get("other_list") or []
    if not others:
        a("(none)")
    for p in others:
        a("%s · %s · %s · `%s`" % (p["code"], p["name"], p["lane"] or "-", p["pid"]))
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- the brain's answer, line by line

_CREATE_RE = re.compile(r"^[A-Z][A-Za-z0-9]*(-[A-Za-z0-9]+)+(\.md)?$")
_DATE_RE = re.compile(r"\d{4}-\d{2}")
_DUE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _codes(v):
    if isinstance(v, str):
        v = re.split(r"[,\s]+", v)
    out = []
    for x in v if isinstance(v, list) else []:
        m = re.match(r"^\s*([CEce]\d+)", str(x or ""))
        if m and m.group(1).upper() not in out:
            out.append(m.group(1).upper())
    return out


def _task_file(v):
    s = _one(v).replace("\\", "/").split("/")[-1].strip().strip("`")
    if s and not s.endswith(".md"):
        s += ".md"
    return s


def _seed_name(v):
    return _cut(_house(re.sub(r"[`\[\]]", "", _one(v))), 60)


def proposal_id(project, kind, target):
    """sha1 (16 hex) of project, kind and target: the task file (update, create), the seed name (seed), or the sorted
    entry keys joined (drop). The same proposal keeps its id across rebuilds."""
    return _sha1("%s|%s|%s" % (project, kind, target))[:16]


def _evidence(codes, ctx):
    ev, jids, runs, ekeys = [], [], {}, []
    for c in codes:
        if c in ctx["ccodes"]:
            s = ctx["ccodes"][c]
            jids.append(s["key"])
            if s.get("run_id") is not None:
                runs[s["key"]] = s["run_id"]
            caps = s.get("captures") or []
            for cp in caps:
                ev.append({"key": s["key"], "line": ctx["gate"](_house("%s · %s: %s" % (s.get("name"), cp.get("kind"), cp.get("text"))))})
            if not caps:
                ev.append({"key": s["key"], "line": ctx["gate"](_house("%s · %s" % (s.get("name"), s.get("situation") or "")))})
        elif c in ctx["ecodes"]:
            e = ctx["ecodes"][c]
            ekeys.append(e["ekey"])
            if "@" in str(e.get("key") or ""):
                jids.append(e["key"])
            ev.append({"key": e["ekey"], "line": e.get("title") or e.get("text") or ""})
            if e.get("quote_line"):
                ev.append({"key": e["ekey"], "line": e["quote_line"]})
    return ev, sorted(set(jids)), runs, sorted(set(ekeys))


def _fm_set(v, statuses, notes, T, label):
    out = {}
    if not isinstance(v, dict):
        return out
    for k, val in v.items():
        k = str(k).strip()
        s = _cut(_house(val), 80)
        if k not in FM_KEYS:
            notes.append(T["note_fm_key"] % (label, k))
            continue
        if k == "status" and s not in statuses:
            notes.append(T["note_fm_status"] % ((s, label) if T is L["en"] else (label, s)))
            continue
        if k == "due" and not _DUE_RE.match(s):
            notes.append(T["note_fm_due"] % ((s, label) if T is L["en"] else (label, s)))
            continue
        if s:
            out[k] = s
    return out


_CJK_RE = re.compile(u"[\u3400-\u9fff\uf900-\ufaff]")
_WORDS_RE = re.compile(r"[A-Za-z0-9]+")


_ESC_BODY = (
    r"\\+u(d[89ab][0-9a-f]{2})\\+u(d[c-f][0-9a-f]{2})"                       # 1, 2: a surrogate pair
    r"|\\+u\{0*([0-9a-f]{1,6})\}|\\+(?-i:U)([0-9a-f]{8})|\\+u([0-9a-f]{4})|\\+x([0-9a-f]{2})|%u([0-9a-f]{4})"   # 3 to 7
    r"|\\+([0-7]{1,3})|\\+(?-i:N)\{([^{}\n]{1,60})\}"                          # 8: octal, 9: a named character
    r"|\\+([nrtbfvae0/\"'\\]?)"                                                # 10: a one-letter escape, or a bare backslash
    r"|&#0*(\d+);?|&#x0*([0-9a-f]+);?|&([a-z][a-z0-9]{1,31});?"                # 11, 12, 13: HTML entities
    r"|((?:%(?:25)*[0-9a-f]{2})+)"                                              # 14: percent codes (%2520 is one)
    r"|<!--[\s\S]*?-->")                                                       # a comment
_ESC_RE = re.compile(_ESC_BODY + r"|<(?:[^<>\"']|\"[^\"]*\"|'[^']*')*>", re.I)          # a tag, quoted values may hold >
_ESC_RE_LOOSE = re.compile(_ESC_BODY + r"|<[a-z!/?][^>]*>", re.I)                      # a tag as HTML reads a malformed one
SCRUB_MAX = 1000   # a string of the raw answer is cut to this before the scrub (the longest stored field is 200)
ANSWER_MAX = 60000  # a model answer longer than this is cut before it is read (a brief is a few thousand characters)
_FILLERS = u"\u115f\u1160\u3164\uffa0"   # Hangul fillers: letters to Unicode, blanks on screen


def _cp(v, base):
    cp = int(v, base)
    return " " if 0xD800 <= cp <= 0xDFFF or cp > 0x10FFFF else chr(cp)


def _esc_text(m, keep_letter=False):
    """(text, end) for one escape, entity, percent run or tag at m: what it stands for (a space for a line break, tab,
    quote, slash, backslash or tag) and where it ends; None when it is not one after all (an unknown entity name).
    keep_letter: a backslash before a letter keeps the letter (the reading where a backslash is a plain separator)."""
    g, end = m.groups(), m.end()
    try:
        if g[0]:
            return chr(0x10000 + ((int(g[0], 16) - 0xD800) << 10) + (int(g[1], 16) - 0xDC00)), end
        for k in (2, 3, 4, 5, 6):
            if g[k]:
                return _cp(g[k], 16), end
        if g[7]:
            return (g[7] if keep_letter else _cp(g[7], 8)), end
        if g[8]:
            try:
                return unicodedata.lookup(g[8]), end
            except KeyError:
                return " ", end
        if g[9] is not None:
            return (g[9] if keep_letter and g[9].isalpha() else " "), end
        if g[10]:
            return _cp(g[10], 10), end
        if g[11]:
            return _cp(g[11], 16), end
        if g[12]:
            name, h = g[12], html_entities.html5
            if m.group(0).endswith(";") and name + ";" in h:
                return h[name + ";"], end
            for n in range(len(name), 1, -1):   # a legacy entity with no semicolon reads like a browser: longest known prefix
                if name[:n] in h:
                    return h[name[:n]], m.start() + 1 + n
            return None
        if g[13]:
            t = g[13]
            for _ in range(4):   # %2520 and the like: decode until nothing changes
                u = urllib_parse.unquote(t)
                if u == t:
                    break
                t = u
            return t, end
    except (ValueError, OverflowError):
        return " ", end
    return " ", end


def _decode_units(s, keep_letter=False, loose=False):
    """[(char, start, end)] of s with every escape, entity, percent run and tag read as what it stands for, in rounds
    until nothing is left to read (what one escape stands for can itself be an escape, an entity or a tag: `&lt;br&gt;`,
    `&amp;nbsp;`), each
    character mapped back to the span of the original text it came from."""
    units = [(c, i, i + 1) for i, c in enumerate(s)]
    rx = _ESC_RE_LOOSE if loose else _ESC_RE
    for _ in range(len(s) + 1):   # until nothing is left to read: every round makes the text shorter
        txt = "".join(u[0] for u in units)
        if not any(c in txt for c in "\\&%<"):
            break
        out, k, changed = [], 0, False
        while k < len(units):
            if txt[k] in "\\&%<":
                m = rx.match(txt, k)
                r = _esc_text(m, keep_letter) if m else None
                if r is not None and r[1] > k:
                    t, e = r
                    a, b = units[k][1], units[e - 1][2]
                    out.extend((c, a, b) for c in t)
                    k, changed = e, True
                    continue
            out.append(units[k])
            k += 1
        units = out
        if not changed:
            break
    return units


_CHAR_SCRIPT_RE = re.compile(u"[\u3005\u3007\u3040-\u30ff\u3130-\u318f\u3400-\u9fff\uac00-\ud7af\uf900-\ufaff\U00020000-\U0003134f]")


def _stream(s, decode=True, keep_letter=False, loose=False):
    """[(char, start, end, word)] of `s`: full-width forms folded (NFKC), case folded, letters and digits only, so
    spacing, punctuation and full-width letters never split a match ("3 pm" and "3pm", "don't" and "dont", "ｂｒｉｎｇ"
    and "bring" compare alike). decode: escapes, entities, percent runs and tags read as what they stand for
    (`_decode_units`); without it, every character reads as itself. word: which Latin or digit word of `s` the
    character belongs to (None for a character counted on its own)."""
    s = str(s)
    units = _decode_units(s, keep_letter, loose) if decode else [(c, i, i + 1) for i, c in enumerate(s)]
    out, w, in_word = [], -1, False
    for ch, a, b in units:
        kept = False
        for c in unicodedata.normalize("NFKC", ch).casefold():
            if not c.isalnum() or c in _FILLERS:
                continue
            kept = True
            if _CHAR_SCRIPT_RE.match(c):   # Chinese, Japanese kana, Korean: each character counts on its own
                out.append((c, a, b, None))
                in_word = False
            else:
                if not in_word:
                    w += 1
                in_word = True
                out.append((c, a, b, w))
        if not kept:
            in_word = False
    return out


def _reply_index(r):
    """What scrub_replies needs from one reply, built once: its letters, where each letter occurs, and prefix counts of
    CJK characters and Latin word starts."""
    rs = _stream(r, decode=False)
    pos = {}
    for j, c in enumerate(rs):
        pos.setdefault(c[0], []).append(j)
    cjk = [0] * (len(rs) + 1)
    chg = [0] * (len(rs) + 1)
    for j, c in enumerate(rs):
        cjk[j + 1] = cjk[j] + (1 if c[3] is None else 0)
        chg[j + 1] = chg[j] + (1 if c[3] is not None and (j == 0 or rs[j - 1][3] != c[3]) else 0)
    return rs, pos, cjk, chg


_IDX_CACHE = {}


def _reply_index_cached(r):
    """_reply_index, built once per reply text (a brief scrubs hundreds of strings against the same few replies)."""
    if r not in _IDX_CACHE:
        if len(_IDX_CACHE) > 256:
            _IDX_CACHE.clear()
        _IDX_CACHE[r] = _reply_index(r)
    return _IDX_CACHE[r]


def _hit_spans(ts, idx, min_chars):
    """[(start, end)] of the text (as read in `ts`) that repeats a stretch of the reply `idx`."""
    rs, pos, cjk, chg = idx
    hit = [False] * len(ts)
    nxt = {}
    for i in range(len(ts) - 1, -1, -1):          # longest common run starting at i in the text and j in the reply
        cur = {}
        for j in pos.get(ts[i][0], ()):
            n = nxt.get(j + 1, 0) + 1
            cur[j] = n
            if i and j and ts[i - 1][0] == rs[j - 1][0]:
                continue                           # not the left end of this run; the longer run covers it
            words = chg[j + n] - chg[j + 1] + (1 if rs[j][3] is not None else 0)
            if cjk[j + n] - cjk[j] >= int(min_chars) or (words >= 4 and n >= 6):
                for k in range(i, i + n):
                    hit[k] = True
        nxt = cur
    spans, k = [], 0
    while k < len(ts):
        if not hit[k]:
            k += 1
            continue
        a = ts[k][1]
        while k < len(ts) and hit[k]:
            b = ts[k][2]
            k += 1
        spans.append((a, b))
    return spans


def scrub_replies(text, replies, min_chars, ref):
    """The owner's reply words never reach storage: every stretch of the model's text that also appears, in the same
    order, in one of the owner's replies, with at least `min_chars` CJK characters or touching 4 Latin words of the
    reply (6 letters or digits or more in all), becomes `ref`. Compared letter by letter with case, spacing,
    punctuation and full-width forms ignored, on up to four readings of the text: escapes, entities, percent codes and
    tags decoded (a backslash before a letter or digit read once as an escape and once as a plain separator, a tag read
    strictly and the loose way HTML reads a malformed one), and every character as itself; a stretch any reading
    matches is replaced, nothing else is rewritten. The replies stay in memory only (a small cache of their indexes,
    never written anywhere)."""
    if not text or not replies:
        return text
    out = str(text)
    idxs = [x for x in (_reply_index_cached(r) for r in dict.fromkeys(str(r) for r in replies)) if x[0]]
    if not idxs:
        return out
    readings = [_stream(out)]
    if any(c in out for c in "\\&%<"):
        readings.append(_stream(out, decode=False))
    if "\\" in out:   # a bare backslash between words: also read it as a plain separator, every other escape decoded
        readings.append(_stream(out, keep_letter=True))
    if "<" in out:    # a malformed tag (`<a<b>`, `<i title=it's>`): also read tags the loose way HTML does
        readings.append(_stream(out, loose=True))
    spans = []
    for ts in readings:
        if ts:
            for idx in idxs:
                spans.extend(_hit_spans(ts, idx, min_chars))
    if not spans:
        return out
    merged = []
    for a, b in sorted(spans):
        if merged and (a <= merged[-1][1] or not any(ch.isalnum() for ch in out[merged[-1][1]:a])):
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    pieces, last = [], 0
    for a, b in merged:
        lead = " " if a > 0 and not out[a - 1].isspace() else ""
        pieces.append(out[last:a] + lead + ref)
        last = b
    pieces.append(out[last:])
    return "".join(pieces)


def _scrub_tree(v, replies, min_chars, ref, cap=None):
    """scrub_replies over every string of the model's answer (every field it writes, labels and task names included).
    cap: cut each string to this many characters first (the raw answer only: no stored field is that long)."""
    if isinstance(v, str):
        return scrub_replies(v[:cap] if cap else v, replies, min_chars, ref)
    if isinstance(v, (list, tuple)):
        return type(v)(_scrub_tree(x, replies, min_chars, ref, cap) for x in v)
    if isinstance(v, dict):   # keys too: an unknown key can reach a note
        return dict((_scrub_tree(k, replies, min_chars, ref, cap), _scrub_tree(x, replies, min_chars, ref, cap)) for k, x in v.items())
    return v


def validate(raw, ctx):
    """(blocks, props, rejected, one, short, notes, closes). Every rejection is {"where", "why", "kind", "task", "now"}.
    The owner's reply words are scrubbed twice: from the model's answer before it is read, and from everything this
    returns, because joining a list or stripping brackets can put a reply back together."""
    res = _validate(raw, ctx)
    reps = ctx.get("reply_texts") or []
    if not reps:
        return res
    return _scrub_tree(res, reps, ctx.get("quote_min_chars") or DEFAULTS["quote_min_chars"], _T(ctx.get("lang"))["reply_ref"])


def _validate(raw, ctx):
    T = _T(ctx.get("lang"))
    gate = ctx["gate"]
    blocks, props, rejected, notes, closes, one = [], [], [], [], [], []
    short = ""

    reps = ctx.get("reply_texts") or []
    rmin = ctx.get("quote_min_chars") or DEFAULTS["quote_min_chars"]

    def g(s, limit):   # a list joined by _one can be longer than SCRUB_MAX: cut first, the stored field is shorter still
        return _cut(scrub_replies(gate(_house(_one(s)))[:SCRUB_MAX], reps, rmin, T["reply_ref"]), limit)

    def key_of(c):
        return (ctx["ccodes"].get(c) or ctx["ecodes"].get(c) or {}).get("key")

    def not_here(codes, bid, label):
        neg = ctx.get("neg") or set()
        out = [c for c in codes if (key_of(c), bid) not in neg]
        gone = [c for c in codes if c not in out]
        if gone and T["note_not_here"] % (label, ", ".join(gone)) not in notes:
            notes.append(T["note_not_here"] % (label, ", ".join(gone)))
        return out

    def rej(where, why, p=None):
        p = p if isinstance(p, dict) else {}
        rejected.append({"where": where, "why": why, "kind": _cut(p.get("kind"), 20), "task": _cut(p.get("task"), 120),
                         "now": g(p.get("now"), 200)})
    if not isinstance(raw, dict):
        rej("output", "not_object")
        return blocks, props, rejected, one, short, notes, closes
    if reps:   # the owner's reply words never reach storage, whichever field the model put them in
        raw = _scrub_tree(raw, reps, rmin, T["reply_ref"], cap=SCRUB_MAX)
    for i, b in enumerate(raw.get("projects") if isinstance(raw.get("projects"), list) else []):
        where = "block %d" % (i + 1)
        if not isinstance(b, dict):
            rej(where, "not_object")
            continue
        pr = _one(b.get("project"))
        if pr.upper() in ctx["pcodes"]:
            bid, typ = ctx["pcodes"][pr.upper()], "p"
        elif pr.lower() == UNASSIGNED:
            bid, typ = UNASSIGNED, "u"
        elif pr.lower().startswith("seed:") and _seed_name(pr[5:]):
            bid, typ = "seed:" + _seed_name(pr[5:]), "s"
        else:
            rej(where, "unknown_project", {"task": pr})
            continue
        stat = _one(b.get("stat")).lower()
        if typ == "s":
            stat = "seed"
        if stat not in STATS:
            notes.append(T["note_stat"] % (pr, stat or "-"))
            stat = "quiet"
        known = [c for c in _codes(b.get("sources")) if c in ctx["ccodes"] or c in ctx["ecodes"]]
        if typ == "p":
            known = not_here(known, bid, pr.upper())
        block = {"id": bid, "type": typ, "code": pr.upper() if typ == "p" else "", "stat": stat, "mean": g(b.get("mean"), MEAN_MAX),
                 "src": known, "props": []}
        for k, p in enumerate(b.get("props") if isinstance(b.get("props"), list) else []):
            pw = "%s prop %d" % (where, k + 1)
            if not isinstance(p, dict):
                rej(pw, "not_object")
                continue
            kind = _one(p.get("kind")).lower()
            if kind not in KINDS:
                rej(pw, "bad_kind", p)
                continue
            src = [c for c in _codes(p.get("src")) if c in ctx["ccodes"] or c in ctx["ecodes"]]
            if not src:
                rej(pw, "no_source", p)
                continue
            if typ == "p":
                src = not_here(src, bid, pr.upper())
                if not src:
                    rej(pw, "not_this_project", p)
                    continue
            if kind in ("update", "create") and typ != "p":
                rej(pw, "outside_project", p)
                continue
            if kind == "seed" and typ == "p":
                rej(pw, "seed_in_project", p)
                continue
            task = ""
            if kind in ("update", "create"):
                task = _task_file(p.get("task"))
                have = ctx["tasks"].get(bid) or set()
                if kind == "update" and task not in have:
                    rej(pw, "no_such_task", p)
                    continue
                if kind == "create":
                    if not _CREATE_RE.match(task) or _DATE_RE.search(task):
                        rej(pw, "bad_task_name", p)
                        continue
                    if task in have:
                        rej(pw, "task_exists", p)
                        continue
            elif kind == "seed":
                task = _seed_name(p.get("task") or (bid[5:] if typ == "s" else ""))
                if not task:
                    rej(pw, "no_seed_name", p)
                    continue
            ecodes = [c for c in src if c in ctx["ecodes"]]
            if kind == "drop" and not ecodes:
                rej(pw, "drop_without_entry", p)
                continue
            now = g(p.get("now"), NOW_MAX)
            if not now:
                rej(pw, "empty_now", p)
                continue
            ev, jids, runs, ekeys = _evidence(src, ctx)
            target = "|".join(ekeys) if kind == "drop" else task
            pid = proposal_id(bid, kind, target)
            if any(ctx["live_by_ekey"].get(x) not in (None, pid) for x in ekeys):
                rej(pw, "already_has_card", p)
                continue
            fm_set = _fm_set(p.get("fm_set"), ctx["statuses"], notes, T, task or pr) if kind in ("update", "create") else {}
            prop = {"id": pid, "project": bid, "kind": kind, "task": task, "now": now, "fm_set": fm_set, "ev": ev, "jids": jids,
                    "entry_keys": ekeys, "runs": runs, "src": src}
            props.append(prop)
            block["props"].append(prop)
        blocks.append(block)
    for x in raw.get("one") if isinstance(raw.get("one"), list) else []:
        if isinstance(x, (list, tuple)) and len(x) >= 2 and _one(x[1]):
            one.append([_cut(_house(x[0]), 80), g(x[1], 200)])
        if len(one) >= 4:
            break
    short = g(raw.get("short"), 200)
    for c in raw.get("close") if isinstance(raw.get("close"), list) else []:
        if not isinstance(c, dict):
            continue
        pre = _one(c.get("id")).lower()
        hits = [x for x in sorted(ctx.get("closable") or []) if len(pre) >= 8 and re.match(r"^[0-9a-f]+$", pre) and x.startswith(pre)]
        if len(hits) != 1:
            notes.append(T["note_close"] % (pre[:16] or "-"))
            continue
        closes.append({"id": hits[0], "why_text": g(c.get("why"), 300)})
    return blocks, props, rejected, one, short, notes, closes


# ---------------------------------------------------------------- cards

def _content(p):
    return {"kind": p.get("kind"), "task": p.get("task") or "", "now": p.get("now") or "", "fm_set": p.get("fm_set") or {},
            "entry_keys": sorted(p.get("entry_keys") or [])}


def _chash(p):
    return _sha1(_jd(_content(p)))


def _get_card(conn, pid):
    r = _rows(conn, "SELECT * FROM today_proposals WHERE id=?", (pid,))
    return r[0] if r else None


def _runs_of(conn, jids):
    return dict((j, r.get("id")) for j, r in _latest_runs(conn).items() if j in set(jids or []))


def _retire_json(conn, row, why, at, **extra):
    d = {"why": why, "prev_status": row.get("status"), "at": at, "keys": _jl(row.get("entry_keys"), []),
         "runs": _runs_of(conn, _jl(row.get("jids"), []))}
    d.update(extra)
    return _jd(d)


def upsert_proposal(conn, day, p, now_s):
    """(id, action). New → pending rev 1 ("inserted"); pending with changed content → rev+1 ("updated"); a card the
    owner pressed (ok / no) is left alone except newer_json ("newer"); a retired card is revived only when the proposal
    brings an entry key or a source run the retired row did not have ("revived", else "left")."""
    target = "|".join(sorted(p.get("entry_keys") or [])) if p.get("kind") == "drop" else (p.get("task") or "")
    pid = p.get("id") or proposal_id(p.get("project"), p.get("kind"), target)
    h = _chash(p)
    row = _get_card(conn, pid)
    vals = (p.get("project"), p.get("kind"), p.get("task") or "", p.get("now") or "", _jd(p.get("fm_set") or {}),
            _jd(p.get("ev") or []), _jd(sorted(p.get("jids") or [])), _jd(sorted(p.get("entry_keys") or [])), h)
    if row is None:
        conn.execute("INSERT INTO today_proposals(id, date, rev, project, kind, task, now, fm_set, ev, jids, entry_keys, "
                     "content_hash, status, updated_at) VALUES (?,?,1,?,?,?,?,?,?,?,?,?,'pending',?)", (pid, day) + vals + (now_s,))
        conn.commit()
        return pid, "inserted"
    st = row.get("status")
    if st == "pending":
        if row.get("content_hash") == h:
            return pid, "same"
        conn.execute("UPDATE today_proposals SET project=?, kind=?, task=?, now=?, fm_set=?, ev=?, jids=?, entry_keys=?, "
                     "content_hash=?, rev=rev+1, updated_at=? WHERE id=?", vals + (now_s, pid))
        conn.commit()
        return pid, "updated"
    if st in ("ok", "no"):
        newer = _jl(row.get("newer_json"), {}) or {}
        if row.get("content_hash") == h or newer.get("hash") == h:
            return pid, "same"
        nj = dict(_content(p), hash=h, at=now_s)
        conn.execute("UPDATE today_proposals SET newer_json=? WHERE id=?", (_jd(nj), pid))
        conn.commit()
        return pid, "newer"
    ret = _jl(row.get("retired_json"), {}) or {}
    old_keys = set(ret.get("keys") or []) | set(_jl(row.get("entry_keys"), []))
    old_runs = ret.get("runs") or {}
    new_key = any(k not in old_keys for k in p.get("entry_keys") or [])
    new_run = any(int(rid or 0) > int(old_runs.get(j) or 0) for j, rid in (p.get("runs") or {}).items())
    if not (new_key or new_run):
        return pid, "left"
    conn.execute("UPDATE today_proposals SET project=?, kind=?, task=?, now=?, fm_set=?, ev=?, jids=?, entry_keys=?, "
                 "content_hash=?, rev=rev+1, updated_at=?, date=?, status='pending', note=NULL, newer_json=NULL, "
                 "decided_at=NULL, retired_json=NULL, placed_json=NULL, replied_json=NULL, close_json=NULL WHERE id=?",
                 vals + (now_s, day, pid))
    conn.commit()
    return pid, "revived"


def expire_old(conn, day):
    """Pending cards that nothing changed for more than EXPIRE_DAYS days become expired. Returns their ids."""
    cut = (dt.date.fromisoformat(day) - dt.timedelta(days=EXPIRE_DAYS)).isoformat()
    at = _iso(_now_dt())
    out = []
    for r in _rows(conn, "SELECT * FROM today_proposals WHERE status='pending'"):
        last = max(str(r.get("date") or "")[:10], _ts(r.get("updated_at"))[:10])
        if last and last < cut:
            conn.execute("UPDATE today_proposals SET status='expired', retired_json=?, updated_at=? WHERE id=?",
                         (_retire_json(conn, r, "expired", at), at, r["id"]))
            out.append(r["id"])
    conn.commit()
    return out


def public_prop(row, day, lang):
    """A card as the board and the API see it; notes in the owner's language."""
    T = _T(lang)
    r = row or {}
    try:
        age = (dt.date.fromisoformat(str(day)[:10]) - dt.date.fromisoformat(str(r.get("date") or day)[:10])).days
    except ValueError:
        age = 0
    ev = [x.get("line") if isinstance(x, dict) else str(x) for x in _jl(r.get("ev"), [])]
    ret = _jl(r.get("retired_json"), None)
    placed = _jl(r.get("placed_json"), None)
    replied = _jl(r.get("replied_json"), None)
    close = _jl(r.get("close_json"), None)
    newer = _jl(r.get("newer_json"), None)
    rnote = ""
    why = ""
    if isinstance(ret, dict):
        why = str(ret.get("why") or "")
        to = ", ".join(x for x in (ret.get("placed_to") or []) if x)
        if why == "placed_elsewhere":
            rnote = (T["retired_placed"] % to) if to else T["retired_placed_none"]
        elif why == "settled":
            rnote = T["retired_settled"] % (to or "-")
        elif why == "replied":
            rnote = T["retired_replied"] % (ret.get("why_text") or "-")
        elif why == "expired":
            rnote = T["retired_expired"] % EXPIRE_DAYS
    pnote = ""
    if isinstance(placed, dict) and placed.get("keys"):
        to = ", ".join(x for x in (placed.get("placed_to") or []) if x)
        pnote = (T["placed_note"] % (len(placed["keys"]), to)) if to else (T["placed_note_none"] % len(placed["keys"]))
    rat = str(replied.get("at") or "") if isinstance(replied, dict) else ""
    kind = str(r.get("kind") or "")
    return {"id": r.get("id"), "rev": r.get("rev"), "status": r.get("status"), "date": r.get("date"), "age": age, "kind": kind,
            "kindL": T["kind"].get(kind, kind), "task": r.get("task") or "", "now": r.get("now") or "",
            "fm_set": _jl(r.get("fm_set"), {}), "ev": ev, "jids": _jl(r.get("jids"), []), "entry_keys": _jl(r.get("entry_keys"), []),
            "note": r.get("note") or "", "newer": ({k: newer.get(k) for k in ("kind", "task", "now", "fm_set", "at")}
                                                    if isinstance(newer, dict) else None),
            "retired_note": rnote, "placed_note": pnote, "replied_at": rat,
            "replied_note": (T["replied_note"] % rat.replace("T", " ")[5:16]) if rat else "",
            "close_note": (T["close_note"] % (close.get("why_text") or "-")) if isinstance(close, dict) else "",
            "retired_why": why, "retired_at": str(ret.get("at") or "") if isinstance(ret, dict) else "",
            "project": r.get("project") or ""}


def decide(conn, pid, rev, action, note=None, now=None, lang="zh"):
    """The owner presses a card: ok, no, pending (take a press back) or note. Never changes rev."""
    row = _get_card(conn, str(pid or ""))
    if row is None:
        return {"ok": False, "reason": "not_found"}
    day = _now_dt(now).date().isoformat()
    try:
        rv = int(rev)
    except (TypeError, ValueError):
        rv = None
    if rv != row.get("rev"):
        return {"ok": False, "reason": "rev", "current": public_prop(row, day, lang)}
    if action not in DECIDE_ACTIONS:
        return {"ok": False, "reason": "action"}
    st = row.get("status")
    if not (st in LIVE or (st == "no" and action in ("pending", "note"))):
        return {"ok": False, "reason": "status", "current": public_prop(row, day, lang)}
    at = _iso(_now_dt(now))
    if action == "note":
        conn.execute("UPDATE today_proposals SET note=?, decided_at=?, updated_at=? WHERE id=?",
                     (_cut(note, NOTE_MAX), at, at, row["id"]))
    else:
        conn.execute("UPDATE today_proposals SET status=?, decided_at=?, updated_at=? WHERE id=?", (action, at, at, row["id"]))
    conn.commit()
    return {"ok": True, "current": public_prop(_get_card(conn, row["id"]), day, lang)}


def unassign(conn, key, project, now=None):
    """The owner says this conversation is not this project: a negative row; the auto row goes."""
    at = _iso(_now_dt(now))
    conn.execute("INSERT OR REPLACE INTO today_assign(key, project, kind, since) VALUES (?,?,'negative',?)", (key, project, at))
    conn.execute("DELETE FROM today_assign WHERE key=? AND project=? AND kind='auto'", (key, project))
    conn.commit()
    return {"ok": True}


def retire_placed(conn, buffer_path, now=None):
    """Cards whose intake entries were placed in the owner's session: every key placed or gone → superseded; some,
    card pending → those keys removed, rev+1; some, card ok → placed_json only. Cards without entry keys untouched."""
    out = {"superseded": [], "trimmed": [], "placed": [], "skipped": ""}
    if WI is None:
        out["skipped"] = "intake module not loaded"
        return out
    p = Path(os.path.expanduser(str(buffer_path or "")))
    if not str(buffer_path or "").strip() or not p.is_file():
        out["skipped"] = "buffer missing"
        return out
    text = _read_text(p)
    if text is None:
        out["skipped"] = "buffer unreadable"
        return out
    if not text.strip():
        out["skipped"] = "buffer empty"
        return out
    cards = [c for c in _live_rows(conn) if _jl(c.get("entry_keys"), [])]
    allk = sorted(set(k for c in cards for k in _jl(c.get("entry_keys"), [])))
    if not allk:
        return out
    try:
        cls = WI.classify_keys(str(p), allk)
    except Exception as e:
        out["skipped"] = "buffer unreadable (%s)" % type(e).__name__
        return out
    at = _iso(_now_dt(now))
    for c in cards:
        keys = _jl(c.get("entry_keys"), [])
        gone = [k for k in keys if (cls.get(k) or ("missing", ""))[0] != "open"]
        if not gone:
            continue
        to = []
        for k in gone:
            t = (cls.get(k) or ("missing", ""))[1]
            if t and t not in to:
                to.append(t)
        if len(gone) == len(keys):
            conn.execute("UPDATE today_proposals SET status='superseded', retired_json=?, updated_at=? WHERE id=?",
                         (_retire_json(conn, c, "placed_elsewhere", at, placed_to=to), at, c["id"]))
            out["superseded"].append(c["id"])
        elif c.get("status") == "pending":
            left = [k for k in keys if k not in gone]
            ev = [x for x in _jl(c.get("ev"), []) if not (isinstance(x, dict) and x.get("key") in gone)]
            nc = dict(_content({"kind": c.get("kind"), "task": c.get("task"), "now": c.get("now"),
                                "fm_set": _jl(c.get("fm_set"), {}), "entry_keys": left}))
            conn.execute("UPDATE today_proposals SET entry_keys=?, ev=?, content_hash=?, placed_json=?, rev=rev+1, updated_at=? "
                         "WHERE id=?", (_jd(sorted(left)), _jd(ev), _sha1(_jd(nc)),
                                        _jd({"keys": sorted(gone), "placed_to": to, "at": at}), at, c["id"]))
            out["trimmed"].append(c["id"])
        else:
            prev = _jl(c.get("placed_json"), {}) or {}
            if sorted(prev.get("keys") or []) == sorted(gone):
                continue
            conn.execute("UPDATE today_proposals SET placed_json=? WHERE id=?",
                         (_jd({"keys": sorted(gone), "placed_to": to, "at": at}), c["id"]))
            out["placed"].append(c["id"])
    conn.commit()
    return out


def settle(conn, pid, rev, to, now=None, lang="zh"):
    """A card with no intake entry (a K card) handled in the owner's session: superseded with where it went."""
    T = _T(lang)
    day = _now_dt(now).date().isoformat()
    pre = str(pid or "").strip().lower()
    if not re.match(r"^[0-9a-f]{6,16}$", pre):
        return {"ok": False, "reason": "not_found", "msg": T["settle_not_found"]}
    rows = _rows(conn, "SELECT * FROM today_proposals WHERE id LIKE ?", (pre + "%",))
    if not rows:
        return {"ok": False, "reason": "not_found", "msg": T["settle_not_found"]}
    if len(rows) > 1:
        return {"ok": False, "reason": "ambiguous", "msg": T["settle_ambiguous"], "n": len(rows)}
    row = rows[0]
    try:
        rv = int(rev)
    except (TypeError, ValueError):
        rv = None
    if rv != row.get("rev"):
        return {"ok": False, "reason": "rev", "msg": T["settle_rev"], "current": public_prop(row, day, lang)}
    where = _cut(to, 200)
    if not where:
        return {"ok": False, "reason": "to", "msg": T["settle_to"], "current": public_prop(row, day, lang)}
    if row.get("status") not in LIVE:
        return {"ok": False, "reason": "status", "msg": T["settle_status"], "current": public_prop(row, day, lang)}
    if _jl(row.get("entry_keys"), []):
        return {"ok": False, "reason": "keys", "msg": T["settle_keys"], "current": public_prop(row, day, lang)}
    at = _iso(_now_dt(now))
    conn.execute("UPDATE today_proposals SET status='superseded', retired_json=?, updated_at=? WHERE id=?",
                 (_retire_json(conn, row, "settled", at, placed_to=[where]), at, row["id"]))
    conn.commit()
    return {"ok": True, "id": row["id"], "current": public_prop(_get_card(conn, row["id"]), day, lang)}


def reply_stamp(conn, now=None):
    """Zero model: a live card whose conversation the owner replied in after its latest judgment gets
    replied_json = {jid, at}. Only times and ids; never text."""
    runs = _latest_runs(conn)
    stamped = []
    for c in _live_rows(conn):
        best = None
        for j in _jl(c.get("jids"), []):
            r = runs.get(j)
            lo = _last_out(conn, [j]).get(j) or ""
            if r and lo and lo > _ts(r.get("started_at")):
                if best is None or lo > best["at"]:
                    best = {"jid": j, "at": lo}
        if best is None:
            continue
        prev = _jl(c.get("replied_json"), {}) or {}
        if prev and _ts(prev.get("at")) >= best["at"]:
            continue
        conn.execute("UPDATE today_proposals SET replied_json=? WHERE id=?", (_jd(best), c["id"]))
        stamped.append(c["id"])
    conn.commit()
    return {"stamped": stamped}


def apply_close(conn, closes, now=None):
    """The brain read the owner's reply and judged a replied card done: pending → superseded; ok → close_json only."""
    at = _iso(_now_dt(now))
    out = {"superseded": [], "noted": []}
    for c in closes or []:
        row = _get_card(conn, c.get("id"))
        if row is None or row.get("status") not in LIVE:
            continue
        if row["status"] == "pending":
            conn.execute("UPDATE today_proposals SET status='superseded', retired_json=?, updated_at=? WHERE id=?",
                         (_retire_json(conn, row, "replied", at, why_text=c.get("why_text") or ""), at, row["id"]))
            out["superseded"].append(row["id"])
        else:
            conn.execute("UPDATE today_proposals SET close_json=? WHERE id=?", (_jd({"why_text": c.get("why_text") or "", "at": at}), row["id"]))
            out["noted"].append(row["id"])
    conn.commit()
    return out


# ---------------------------------------------------------------- one build

def _extract_json(text):
    """First `{` to its matching `}` (strings respected), code fences stripped; the next `{` when that does not parse."""
    s = re.sub(r"```(?:json|JSON)?", "", str(text or ""))
    i = s.find("{")
    while i != -1:
        depth, in_str, esc = 0, False, False
        for j in range(i, len(s)):
            ch = s[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[i:j + 1])
                    except ValueError:
                        break
        i = s.find("{", i + 1)
    return None


def _ctx_from_inputs(inp, groups=None, reply_texts=None):
    """The validator's context from an inputs dict (the same shape a run folder's inputs.json holds)."""
    inp = inp if isinstance(inp, dict) else {}
    lang = _lang(inp.get("lang"))
    projects = [p for p in inp.get("projects") or [] if isinstance(p, dict) and p.get("code") and p.get("pid")]
    sources = [s for s in inp.get("sources") or [] if isinstance(s, dict) and s.get("code")]
    entries = [e for e in inp.get("entries") or [] if isinstance(e, dict) and e.get("code")]
    live_by_ekey = {}
    for c in inp.get("cards") or []:
        if isinstance(c, dict) and c.get("status") in LIVE:
            for k in c.get("entry_keys") or []:
                live_by_ekey.setdefault(k, c.get("id"))
    msgs = [[{"pk": None, "at": e.get("at") or "", "me": False, "speaker": "", "text": e.get("quote") or ""}]
            for e in entries if e.get("verified") and e.get("quote")]
    msgs += [g for g in (inp.get("messages") or []) if isinstance(g, list)]
    msgs += groups or []
    return {"lang": lang, "statuses": list(inp.get("statuses") or FALLBACK_TASK_STATUSES),
            "pcodes": dict((p["code"].upper(), p["pid"]) for p in projects),
            "tasks": dict((p["pid"], set(p.get("tasks") or [])) for p in projects),
            "ccodes": dict((s["code"].upper(), s) for s in sources), "ecodes": dict((e["code"].upper(), e) for e in entries),
            "live_by_ekey": live_by_ekey, "closable": set(inp.get("closable") or []),
            "neg": set((str(k), str(pj)) for k, pj in (inp.get("neg") or []) if k and pj),
            "reply_texts": [str(x) for x in (reply_texts or []) if str(x or "").strip()],
            "quote_min_chars": inp.get("quote_min_chars") or DEFAULTS["quote_min_chars"],
            "gate": QuoteGate(msgs, inp.get("quote_min_chars") or DEFAULTS["quote_min_chars"], lang=lang)}


def _latest_brief_json(conn, day):
    r = _latest_build(conn, day)
    if not r:
        return None
    j = _jl(r.get("json"), None)
    return j if isinstance(j, dict) else None


def _prepare(conn, cfg, day, buffer_path, vault, live_dir, hooks=None, force=False, lang="zh"):
    """Every input of one build, the prompt context and the validator context. Reads only."""
    hooks = hooks or {}
    T = _T(lang)
    t = tcfg(cfg)
    paths = (cfg or {}).get("paths") or {}
    doctrine = str(paths.get("doctrine") or "").strip()
    index, notes = project_index(vault, doctrine or None, lang=lang)
    statuses, sok = _task_statuses(doctrine)
    if not doctrine:
        notes.append(T["note_no_doctrine"])
    sources = today_sources(conn, day)
    entries = open_entries(buffer_path, conn)
    sig, parts = inputs_sig(conn, day, buffer_path)
    prev = _latest_brief_json(conn, day)
    neg = _neg_rows(conn)
    topic = Path(os.path.expanduser(str(live_dir))) / "topic-map.json" if live_dir else None
    cands = candidates(conn, sources, entries, index, vault, topic)
    live = _live_rows(conn)
    jids = set(s["key"] for s in sources)
    for c in live:
        jids.update(_jl(c.get("jids"), []))
    replies = _last_out(conn, jids)
    scope = compute_scope(None if force else prev, sources, entries, cands, neg, replies)
    changed = set(scope["changed"])
    in_scope = [pid for pid in scope["projects"] if pid in index]
    order = sorted(in_scope, key=lambda x: (index[x]["lane"], index[x]["name"].lower(), x))
    order += sorted([pid for pid in index if pid not in in_scope], key=lambda x: (index[x]["lane"], index[x]["name"].lower(), x))
    pcode = dict((pid, "P%d" % (i + 1)) for i, pid in enumerate(order))
    by_project = {}
    for c in live:
        by_project.setdefault(c.get("project"), []).append(c)
    scope_list = []
    for pid in order[:len(in_scope)]:
        p = index[pid]
        cards = [{"id": c["id"], "kind": c.get("kind"), "task": c.get("task"), "status": c.get("status"), "now": c.get("now"),
                  "replied": bool(_jl(c.get("replied_json"), None))} for c in by_project.get(pid, [])]
        scope_list.append(dict(p, code=pcode[pid], material=project_material(vault, p, statuses), cards=cards))
    other_list = [dict(index[pid], code=pcode[pid]) for pid in order[len(in_scope):]]
    scope_sources = [s for s in sources if scope["full"] or s["key"] in changed]
    negp = {}
    for r in neg:
        if r["project"] in pcode:
            negp.setdefault(r["key"], []).append(pcode[r["project"]])
    source_list = [dict(s, code="C%d" % (i + 1), not_in=sorted(negp.get(s["key"], []))) for i, s in enumerate(scope_sources)]
    entry_list = [dict(e, code="E%d" % (i + 1), not_in=sorted(negp.get(e.get("key"), []))) for i, e in enumerate(entries)]
    rep_text = {}
    fn = hooks.get("outbound_since")
    for s in source_list:
        if not fn:
            break
        try:
            got = fn(s["key"], s["started_at"], n=REPLIES_PER_SOURCE) or []
        except Exception:
            got = []
        lines = []
        for r in got[-REPLIES_PER_SOURCE:]:
            if not isinstance(r, dict):
                continue
            words = _cut(r.get("text"), REPLY_MAX)
            txt = words or "[%s]" % (_one(r.get("type")) or "media")   # the program's own placeholder for a voice note or a photo
            lines.append({"at": _ts(r.get("at")).replace("T", " ")[5:16], "text": txt.replace('"', "'"), "words": words})
        if lines:
            rep_text[s["key"]] = lines
    groups = []
    pk = hooks.get("pack_messages")
    for s in source_list:
        if not pk or not s.get("run_dir"):
            continue
        try:
            got = pk(str(Path(s["run_dir"]) / "sweep.json"), s["key"])
            if got:
                groups.append(list(got))
        except Exception:
            pass
    closable = []
    for c in live:
        if not _jl(c.get("replied_json"), None):
            continue
        if c.get("project") in scope["projects"] or set(_jl(c.get("jids"), [])) & changed:
            closable.append(c["id"])
    inputs = {"version": VERSION, "date": day, "lang": lang, "quote_min_chars": t["quote_min_chars"], "statuses": statuses,
              "projects": [{"code": pcode[pid], "pid": pid, "name": index[pid]["name"], "lane": index[pid]["lane"],
                            "tasks": _task_files(vault, index[pid])} for pid in order],
              "sources": [{"code": s["code"], "key": s["key"], "name": s["name"], "run_id": s["run_id"],
                           "situation": s["situation"], "captures": s["captures"]} for s in source_list],
              "entries": [dict((k, e.get(k)) for k in ("code", "ekey", "key", "name", "text", "title", "quote_line", "quote",
                                                     "mark", "at", "verified")) for e in entry_list],
              "cards": [{"id": c["id"], "project": c.get("project"), "status": c.get("status"),
                         "entry_keys": _jl(c.get("entry_keys"), []), "replied": bool(_jl(c.get("replied_json"), None))} for c in live],
              "neg": [[r["key"], r["project"]] for r in neg], "closable": sorted(closable), "scope": {"full": scope["full"], "projects": scope["projects"], "changed": scope["changed"]},
              "messages": []}
    vctx = _ctx_from_inputs(inputs, groups=groups,
                            reply_texts=[r["words"] for ls in rep_text.values() for r in ls if r.get("words")])
    prev_lines = []
    for b in (prev or {}).get("projects") or []:
        prev_lines.append("%s · %s · %s" % (b.get("name"), b.get("stat"), _cut(b.get("mean"), 200)))
    owner = _one((cfg or {}).get("owner_label")) or "the owner"
    pctx = {"owner": owner, "worlds_block": _worlds_block(cfg, hooks), "statuses": statuses, "scope_list": scope_list,
            "source_list": source_list, "entry_list": entry_list, "replies": rep_text, "prev_lines": prev_lines,
            "other_list": other_list}
    return {"t": t, "index": index, "notes": notes, "statuses": statuses, "sources": sources, "entries": entries, "sig": sig,
            "parts": parts, "prev": prev, "neg": neg, "cands": cands, "scope": scope, "replies": replies, "pctx": pctx,
            "inputs": inputs, "vctx": vctx, "source_list": source_list, "entry_list": entry_list, "lang": lang}


def _count_reads(run_dir):
    n = 0
    for f in sorted(Path(run_dir).glob("claude*.stdout.jsonl")):
        for line in (_read_text(f) or "").split("\n"):
            try:
                j = json.loads(line)
            except ValueError:
                continue
            if not isinstance(j, dict) or j.get("type") != "assistant":
                continue
            for c in ((j.get("message") or {}).get("content") or []):
                if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") == "Read":
                    n += 1
    return n


def _block_cards(conn, bid, day, lang):
    rows = _rows(conn, "SELECT * FROM today_proposals WHERE project=? ORDER BY date, id", (bid,))
    out = []
    for r in rows:
        last = max(str(r.get("decided_at") or "")[:10], _ts(r.get("updated_at"))[:10], str(r.get("date") or "")[:10])
        if r.get("status") in LIVE or last == day:
            out.append(public_prop(r, day, lang))
    return out


def _sort_blocks(blocks):
    def k(b):
        if b["id"] == UNASSIGNED:
            return (len(STAT_ORDER) + 1, 0)
        return (STAT_ORDER.index(b["stat"]) if b["stat"] in STAT_ORDER else len(STAT_ORDER), 0)
    return sorted(blocks, key=k)


def _assemble(conn, prep, blocks, one, short, rejected, notes, gate_stats, usage, run_name, day, now_s, lang):
    T = _T(lang)
    index = prep["index"]
    vctx = prep["vctx"]
    prev = prep["prev"] or {}
    scope = prep["scope"]
    final, ids = [], set()
    for b in blocks:
        if b["id"] in ids:
            continue
        ids.add(b["id"])
        happen, keys = [], []
        for c in b["src"]:
            if c in vctx["ccodes"]:
                s = vctx["ccodes"][c]
                full = next((x for x in prep["source_list"] if x["code"] == c), s)
                keys.append(s["key"])
                happen.append({"key": s["key"], "name": s.get("name"), "at": _ts(full.get("started_at")).replace("T", " ")[11:16],
                               "line": _cut(vctx["gate"](_house(full.get("situation") or full.get("summary") or "")), 300)})
            elif c in vctx["ecodes"]:
                e = vctx["ecodes"][c]
                keys.append(e["key"])
                happen.append({"key": e["key"], "name": e.get("name"), "at": e.get("date") or "", "line": _cut(e.get("text"), 300)})
        if b["type"] == "p":
            name, lane = index[b["id"]]["name"], index[b["id"]]["lane"]
        elif b["type"] == "u":
            name, lane = T["unassigned"], ""
        else:
            name, lane = b["id"][5:], ""
        final.append({"id": b["id"], "name": name, "lane": lane, "stat": b["stat"], "statL": T["stat"][b["stat"]], "mean": b["mean"],
                      "sources": list(dict.fromkeys(keys)), "happen": happen})
    if not scope["full"]:
        for pb in prev.get("projects") or []:
            if pb.get("id") not in ids:
                ids.add(pb.get("id"))
                final.append(dict((k, v) for k, v in pb.items() if k != "props"))
    for c in _live_rows(conn):
        pid = c.get("project")
        if pid and pid not in ids:
            ids.add(pid)
            nm = index[pid]["name"] if pid in index else (T["unassigned"] if pid == UNASSIGNED else str(pid).replace("seed:", "", 1))
            st = "seed" if str(pid).startswith("seed:") else "quiet"
            final.append({"id": pid, "name": nm, "lane": index[pid]["lane"] if pid in index else "", "stat": st,
                          "statL": T["stat"][st], "mean": "", "sources": [], "happen": []})
    for b in final:
        b["props"] = _block_cards(conn, b["id"], day, lang)
    final = [b for b in final if b.get("mean") or b.get("happen") or b.get("props")]
    final = _sort_blocks(final)
    if not one and not scope["full"]:
        one = prev.get("one") or []
    if not short and not scope["full"]:
        short = prev.get("short") or ""
    n_live = len(_live_rows(conn))
    tail = [T["tail_live"] % n_live]
    if rejected:
        tail.append(T["tail_rejected"] % len(rejected))
    if not scope["full"]:
        tail.append(T["tail_partial"] % len(scope["projects"]))
    if usage.get("reads"):
        tail.append(T["tail_reads"] % usage["reads"])
    seen = {"runs": dict((s["key"], s["run_id"]) for s in prep["sources"]), "entries": sorted(e["ekey"] for e in prep["entries"]),
            "replies": prep["replies"], "neg": sorted("%s|%s" % (r["key"], r["project"]) for r in prep["neg"])}
    return {"date": day, "built_at": now_s, "inputs_sig": prep["sig"], "one": one, "short": short,
            "meta": {"version": VERSION, "lang": lang, "n_sources": len(prep["sources"]), "n_entries": len(prep["entries"]),
                     "n_projects": len(index)},
            "projects": final, "tail": tail, "rejected": rejected, "notes": notes, "quote_gate": gate_stats,
            "usage": {"read": usage.get("read") or 0, "output": usage.get("output") or 0}, "run_dir": run_name,
            "scope": {"full": scope["full"], "projects": scope["projects"], "n_changed": len(scope["changed"])}, "seen": seen}


def _public_brief(brief):
    return dict((k, v) for k, v in brief.items() if k != "seen")


def save_brief(conn, brief, live_dir):
    """The table row (latest HISTORY_KEEP per day kept) and <live>/today/<date>.json (0600, without seen)."""
    day = brief["date"]
    conn.execute("INSERT INTO today_brief(date, built_at, inputs_sig, run_dir, usage_json, json) VALUES (?,?,?,?,?,?)",
                 (day, brief["built_at"], brief["inputs_sig"], brief.get("run_dir") or "", _jd(brief.get("usage") or {}), _jd(brief)))
    conn.execute("DELETE FROM today_brief WHERE date=? AND rowid NOT IN (SELECT rowid FROM today_brief WHERE date=? "
                 "ORDER BY built_at DESC, rowid DESC LIMIT ?)", (day, day, HISTORY_KEEP))
    conn.commit()
    if live_dir:
        d = Path(os.path.expanduser(str(live_dir))) / "today"
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(str(d), 0o700)
        except OSError:
            pass
        _write_json(d / ("%s.json" % day), _public_brief(brief), mode=0o600)


def _new_run_dir(live_dir, now_d):
    base = Path(os.path.expanduser(str(live_dir))) / "runs"
    base.mkdir(parents=True, exist_ok=True)
    stem = now_d.strftime("%Y%m%d-%H%M%S")
    for i in range(100):
        d = base / ("%s-today" % stem if i == 0 else "%s-%d-today" % (stem, i))
        try:
            d.mkdir(mode=0o700)
            os.chmod(str(d), 0o700)
            return d
        except FileExistsError:
            continue
    raise RuntimeError("could not make a run folder under %s" % base)


def _write_private(path, text):
    Path(path).write_text(text, encoding="utf-8")
    os.chmod(str(path), 0o600)


def build(conn, cfg, day, buffer_path, vault, live_dir, call=None, hooks=None, dry_run=False, force=False, now=None, lang="zh"):
    """One brief. dry_run: the prompt and the candidates, nothing written, no model. Otherwise `call(prompt, run_dir)`
    (the daemon's Brain.call_claude wrapper) is the only model call; its answer is validated line by line, cards are
    upserted, the brief is saved. Returns {"ok", ...}."""
    hooks = hooks or {}
    now_d = _now_dt(now)
    now_s = _iso(now_d)
    lang = _lang(lang)
    T = _T(lang)
    if not dry_run:
        ensure_tables(conn)
        expire_old(conn, day)
        reply_stamp(conn, now_d)
    prep = _prepare(conn, cfg, day, buffer_path, vault, live_dir, hooks=hooks, force=force, lang=lang)
    prompt = build_prompt(prep["pctx"], prep["t"], lang)
    if dry_run:
        return {"ok": True, "dry_run": True, "prompt": prompt, "candidates": prep["cands"], "sig": prep["sig"]}
    prev = prep["prev"]
    if not force and prev and prev.get("inputs_sig") == prep["sig"]:
        return {"ok": True, "skipped": True, "why": "same_sig", "sig": prep["sig"]}
    if call is None:
        raise RuntimeError("build needs a model call (the daemon passes one); use dry_run=True to see the prompt")
    run_dir = _new_run_dir(live_dir, now_d)
    _write_private(run_dir / "prompt.md", prompt)
    _write_json(run_dir / "inputs.json", prep["inputs"], mode=0o600)
    reps = prep["vctx"].get("reply_texts") or []

    def scrub_err(x):   # a failed call's error can carry the model's raw output (escaped stream-json): scrubbed like the answer
        return scrub_replies(str(x or "")[:SCRUB_MAX], reps, prep["vctx"].get("quote_min_chars") or DEFAULTS["quote_min_chars"], T["reply_ref"])

    def err_text(e):
        return _cut(scrub_err(e), 300)

    try:
        takes = "scrub" in inspect.signature(call).parameters
    except (TypeError, ValueError):
        takes = False
    try:
        res = (call(prompt, str(run_dir), scrub=scrub_err) if takes else call(prompt, str(run_dir))) or {}
    except Exception as e:
        return {"ok": False, "why": "model_error", "error": err_text(e), "run_dir": run_dir.name, "sig": prep["sig"]}
    if res.get("is_error"):
        return {"ok": False, "why": "model_error", "error": err_text(res.get("error")), "run_dir": run_dir.name, "sig": prep["sig"]}
    try:   # whatever fails from here on, its message may hold the model's text: scrubbed before the daemon logs it
        text = str(res.get("text") or "")[:ANSWER_MAX]   # a longer answer cannot be one brief: it fails as no JSON
        _write_private(run_dir / "result.txt", text)
        ex = hooks.get("extract_json") or _extract_json
        try:
            raw = ex(text)
        except Exception:
            raw = None
        if not isinstance(raw, dict):
            return {"ok": False, "why": "no_json", "run_dir": run_dir.name, "sig": prep["sig"]}
        blocks, props, rejected, one, short, vnotes, closes = validate(raw, prep["vctx"])
        notes = list(prep["notes"]) + vnotes
        actions = dict((k, 0) for k in ("inserted", "updated", "same", "newer", "revived", "left"))
        for p in props:
            _pid, act = upsert_proposal(conn, day, p, now_s)
            actions[act] = actions.get(act, 0) + 1
        closed = apply_close(conn, closes, now_d)
        retired = retire_placed(conn, buffer_path, now_d)
        neg = set((r["key"], r["project"]) for r in _neg_rows(conn))
        vctx = prep["vctx"]
        for b in blocks:
            if b["type"] != "p":
                continue
            for c in b["src"]:
                key = (vctx["ccodes"].get(c) or vctx["ecodes"].get(c) or {}).get("key")
                if key and (key, b["id"]) not in neg:
                    conn.execute("INSERT OR IGNORE INTO today_assign(key, project, kind, since) VALUES (?,?,'auto',?)", (key, b["id"], now_s))
        conn.commit()
        reads = _count_reads(run_dir)
        if reads > prep["t"]["max_reads"]:
            notes.append(T["note_reads_over"] % (reads, prep["t"]["max_reads"]))
        usage = {"read": int(res.get("input_tokens") or 0) + int(res.get("cache_read_tokens") or 0) + int(res.get("cache_create_tokens") or 0),
                 "output": int(res.get("output_tokens") or 0), "reads": reads}
        brief = _assemble(conn, prep, blocks, one, short, rejected, notes, vctx["gate"].stats(), usage, run_dir.name, day, now_s, lang)
        save_brief(conn, brief, live_dir)
        _write_json(run_dir / "today_brief.json", _public_brief(brief), mode=0o600)
        counts = {"blocks": len(brief["projects"]), "props": len(props), "rejected": len(rejected), "closed": len(closed["superseded"]),
                  "retired": len(retired["superseded"]), "reads": reads}
        counts.update(actions)
        return {"ok": True, "brief": _public_brief(brief), "counts": counts, "run_dir": run_dir.name, "sig": prep["sig"]}
    except Exception as e:
        return {"ok": False, "why": "exception", "error": "%s: %s" % (type(e).__name__, err_text(e)), "run_dir": run_dir.name,
                "sig": prep["sig"]}


# ---------------------------------------------------------------- reading it back

def _refresh(conn, brief, day, lang):
    b = _public_brief(brief)
    b["projects"] = [dict(p, props=_block_cards(conn, p.get("id"), b.get("date") or day, lang)) for p in b.get("projects") or []]
    b["run_dir"] = Path(str(b.get("run_dir") or "")).name
    tail = list(b.get("tail") or [])
    if tail:   # the first tail line counts live cards: count them now, a card may have retired since the build
        tail[0] = _T(lang)["tail_live"] % len(_live_rows(conn))
    b["tail"] = tail
    return b


def api_payload(conn, day, lang="zh"):
    """What GET /api/today answers: today's latest brief (cards refreshed from the table), else yesterday's marked
    stale, else an empty answer. Always ok; never the seen block."""
    lang = _lang(lang)
    j = _latest_brief_json(conn, day)
    stale = False
    if j is None:
        y = (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()
        j = _latest_brief_json(conn, y)
        stale = j is not None
    if j is None:
        return {"ok": True, "date": day, "empty": True, "stale": True, "projects": [], "one": [], "short": "", "tail": []}
    b = _refresh(conn, j, day, lang)
    b["ok"] = True
    b["stale"] = stale
    b["empty"] = not (b.get("projects") or b.get("one") or b.get("short"))
    return b


def stats(conn, days=10):
    """Counts only: builds and read tokens per day, cards by status."""
    rows = _rows(conn, "SELECT date, COUNT(*) AS n FROM today_brief GROUP BY date ORDER BY date DESC LIMIT ?", (int(days),))
    out = {"builds": {}, "read_tokens": {}, "proposals": {}}
    for r in rows:
        out["builds"][r["date"]] = r["n"]
        tok = 0
        for u in _rows(conn, "SELECT usage_json FROM today_brief WHERE date=?", (r["date"],)):
            tok += int((_jl(u.get("usage_json"), {}) or {}).get("read") or 0)
        out["read_tokens"][r["date"]] = tok
    for r in _rows(conn, "SELECT status, COUNT(*) AS n FROM today_proposals GROUP BY status"):
        out["proposals"][r["status"]] = r["n"]
    return out


def yesterday_line_data(conn, day):
    """For the digest: {builds, cards, ok, no, retired, read_tokens} of that date; None without tables or builds."""
    if not _has_table(conn, "today_brief") or not _has_table(conn, "today_proposals"):
        return None
    b = _rows(conn, "SELECT usage_json FROM today_brief WHERE date=?", (day,))
    if not b:
        return None
    cards = _rows(conn, "SELECT status FROM today_proposals WHERE date=?", (day,))
    n = dict((s, 0) for s in STATUSES)
    for c in cards:
        n[c.get("status")] = n.get(c.get("status"), 0) + 1
    return {"builds": len(b), "cards": len(cards), "ok": n["ok"], "no": n["no"], "retired": n["superseded"] + n["expired"],
            "read_tokens": sum(int((_jl(u.get("usage_json"), {}) or {}).get("read") or 0) for u in b)}


# ---------------------------------------------------------------- fixtures

def fixture_check(fdir, vault=None):
    """Replay a run folder (result.txt, inputs.json, optional expect.json) through extract and validate; print counts
    per stage. Returns {"ok", "blocks", "props", "rejected", "kinds", "gate", "mismatch"}."""
    d = Path(fdir)
    name = d.name
    out = {"ok": False, "blocks": 0, "props": 0, "rejected": {}, "kinds": {}, "gate": {}, "mismatch": []}
    text = _read_text(d / "result.txt")
    if text is None:
        print("fixture %s: no result.txt" % name)
        return out
    inp = _jl(_read_text(d / "inputs.json"), None)
    if not isinstance(inp, dict):
        print("fixture %s: %s" % (name, L["en"]["note_no_inputs"]))
        inp = {}
    if vault:
        for p in inp.get("projects") or []:
            p["tasks"] = _task_files(vault, {"tasks_dir": "%s/Tasks" % p.get("pid")})
    raw = _extract_json(text)
    if not isinstance(raw, dict):
        print("fixture %s: result.txt holds no JSON object" % name)
        return out
    ctx = _ctx_from_inputs(inp)
    blocks, props, rejected, one, short, notes, closes = validate(raw, ctx)
    rej, kinds = {}, {}
    for r in rejected:
        rej[r["why"]] = rej.get(r["why"], 0) + 1
    for p in props:
        kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
    gs = ctx["gate"].stats()
    out.update({"ok": True, "blocks": len(blocks), "props": len(props), "rejected": rej, "kinds": kinds, "gate": gs,
                "closes": len(closes), "notes": notes})
    print("fixture %s: parsed" % name)
    print("  blocks %d · props %d (%s) · closes %d" % (len(blocks), len(props),
                                                     ", ".join("%s %d" % (k, kinds[k]) for k in sorted(kinds)) or "none", len(closes)))
    print("  rejected %d (%s)" % (len(rejected), ", ".join("%s %d" % (k, rej[k]) for k in sorted(rej)) or "none"))
    print("  gate checked %d · kept %d · rewritten %d" % (gs["checked"], gs["kept"], gs["rewritten"]))
    exp = _jl(_read_text(d / "expect.json"), None)
    if isinstance(exp, dict):
        got = {"blocks": len(blocks), "props": len(props), "rejected": rej, "kinds": kinds, "gate": gs}
        for k, v in exp.items():
            if k in got and got[k] != v:
                out["mismatch"].append("%s: expected %s, got %s" % (k, _jd(v), _jd(got[k])))
        if out["mismatch"]:
            out["ok"] = False
            for m in out["mismatch"]:
                print("  expect MISMATCH %s" % m)
        else:
            print("  expect: all match")
    return out


def _fixture_sets():
    """Three synthetic run folders (invented baking school, invented people): every kind, every rejection reason,
    a fenced answer with a paraphrased quote."""
    weekend, corp, kitchen = (_TEST_WING + "/02_Work/Deliver/Weekend-Class",
                              _TEST_WING + "/02_Work/Grow/Corporate-Workshops",
                              "03_Personal-Wing/Personal-Projects/Kitchen-Refit")
    e1 = "a1" * 20
    e2 = "b2" * 20
    e3 = "c3" * 20
    base = {
        "version": VERSION, "date": "2026-10-21", "lang": "en", "quote_min_chars": 6,
        "statuses": ["not-started", "in-progress", "waiting", "blocked", "parked", "done"],
        "projects": [{"code": "P1", "pid": weekend, "name": "Weekend Class", "lane": "Deliver",
                      "tasks": ["Book-Oven-Technician.md", "Send-Class-Reminder.md"]},
                     {"code": "P2", "pid": corp, "name": "Corporate Workshops", "lane": "Grow", "tasks": ["Quote-For-Harbour-Bank.md"]},
                     {"code": "P3", "pid": kitchen, "name": "Kitchen Refit", "lane": "Personal-Projects", "tasks": ["Choose-Tiles.md"]}],
        "sources": [{"code": "C1", "key": "60120000001@s.whatsapp.net", "name": "Mei Tan", "run_id": 41,
                     "situation": "asks to move her seat to the November weekend class", "captures": [
                         {"kind": "request_dated", "text": "move her seat to the November class"}]},
                    {"code": "C2", "key": "60120000002@s.whatsapp.net", "name": "Rafi Osman", "run_id": 42,
                     "situation": "waiting for the corporate workshop quote for 40 staff", "captures": []},
                    {"code": "C3", "key": "120363000000000001@g.us", "name": "Bread Club", "run_id": 43,
                     "situation": "members ask about a monthly bread box", "captures": []}],
        "entries": [{"code": "E1", "ekey": e1, "key": "60120000001@s.whatsapp.net", "name": "Mei-Tan",
                     "text": "move her seat to the November class", "title": "2026-10-21 · [wa] · [[Mei-Tan]] · move her seat to the November class",
                     "quote_line": "- 🗣 10-21 09:12 \"can I move my seat to the November weekend class please\"",
                     "quote": "can I move my seat to the November weekend class please", "mark": "🗣", "at": "10-21 09:12", "verified": True},
                    {"code": "E2", "ekey": e2, "key": "wa:Dana Wong", "name": "Dana Wong", "text": "said thanks for the recipe",
                     "title": "2026-10-21 · [wa] · Dana Wong (no note) · said thanks for the recipe",
                     "quote_line": "- ≈ 10-21 10:40 gist, not verbatim \"thanks for the recipe\"",
                     "quote": "thanks for the recipe", "mark": "≈", "at": "10-21 10:40", "verified": False},
                    {"code": "E3", "ekey": e3, "key": "60120000002@s.whatsapp.net", "name": "Rafi Osman",
                     "text": "needs the quote before Friday", "title": "2026-10-21 · [wa] · Rafi Osman (no note) · needs the quote before Friday",
                     "quote_line": "- 🗣 10-21 11:05 \"we need the quote before Friday for 40 staff\"",
                     "quote": "we need the quote before Friday for 40 staff", "mark": "🗣", "at": "10-21 11:05", "verified": True}],
        "cards": [],
        "closable": [],
        "messages": [[{"pk": 501, "at": "2026-10-21 11:05:00", "me": False, "speaker": "Rafi Osman",
                       "text": "Hi, we need the quote before Friday for 40 staff, thanks!"}]],
    }
    clean = {
        "one": [["Corporate Workshops", "the bank still waits for the quote"], ["Weekend Class", "Mei wants to move to November"]],
        "short": "a quote and a seat change wait for you",
        "projects": [
            {"project": "P2", "stat": "stuck", "mean": "Rafi needs the quote before Friday for 40 staff.", "sources": ["C2", "E3"],
             "props": [{"kind": "update", "task": "Quote-For-Harbour-Bank.md", "now": "send the quote for 40 staff before Friday",
                        "fm_set": {"status": "in-progress", "due": "2026-10-24"}, "src": ["C2", "E3"]}]},
            {"project": "P1", "stat": "ball", "mean": "Mei asks to move her seat to November.", "sources": ["C1", "E1"],
             "props": [{"kind": "create", "task": "Move-Mei-Seat-To-November.md", "now": "move Mei to the November weekend class",
                        "fm_set": {"status": "not-started"}, "src": ["E1"]}]},
            {"project": "unassigned", "stat": "quiet", "mean": "the bread club talks about a monthly box; Dana said thanks.",
             "sources": ["C3", "E2"],
             "props": [{"kind": "seed", "task": "Bread Box Subscription", "now": "three members asked for a monthly bread box",
                        "src": ["C3"]},
                       {"kind": "drop", "now": "a thank you, nothing to place", "src": ["E2"]}]}],
        "close": []}
    every = {
        "one": [], "short": "test of every rejection",
        "projects": [
            {"project": "P9", "stat": "move", "mean": "no such project", "sources": ["C1"]},
            {"project": "P1", "stat": "sideways", "mean": "the stat is not known", "sources": ["C1"],
             "props": [
                 {"kind": "rename", "task": "Book-Oven-Technician.md", "now": "kind not known", "src": ["C1"]},
                 {"kind": "update", "task": "Book-Oven-Technician.md", "now": "no source given", "src": []},
                 {"kind": "update", "task": "Book-Oven-Technician.md", "now": "unknown source code", "src": ["C9"]},
                 {"kind": "seed", "task": "Pastry Club", "now": "a seed inside a project", "src": ["C1"]},
                 {"kind": "update", "task": "Order-Flour.md", "now": "this task does not exist", "src": ["C1"]},
                 {"kind": "create", "task": "order flour", "now": "lower case name", "src": ["C1"]},
                 {"kind": "create", "task": "Class-2026-11-Plan.md", "now": "a date in the name", "src": ["C1"]},
                 {"kind": "create", "task": "Send-Class-Reminder.md", "now": "already exists", "src": ["C1"]},
                 {"kind": "drop", "now": "a drop without an entry", "src": ["C1"]},
                 {"kind": "update", "task": "Book-Oven-Technician.md", "now": "   ", "src": ["C1"]},
                 {"kind": "update", "task": "Book-Oven-Technician.md", "now": "E3 already has a card", "src": ["E3"]},
                 {"kind": "update", "task": "Book-Oven-Technician.md", "now": "book the technician for Monday",
                  "fm_set": {"status": "someday", "due": "next week", "colour": "red", "waiting_on": "the technician"}, "src": ["C1.1"]}]},
            {"project": "unassigned", "stat": "quiet", "mean": "update outside a project", "sources": ["C3"],
             "props": [{"kind": "update", "task": "Choose-Tiles.md", "now": "outside a project", "src": ["C3"]},
                       {"kind": "seed", "task": "", "now": "a seed without a name", "src": ["C3"]}]},
            "not a block"],
        "close": [{"id": "deadbeef", "why": "no such card"}]}
    every_inputs = json.loads(json.dumps(base))
    every_inputs["cards"] = [{"id": "f00dfeedf00dfeed", "project": corp, "status": "pending", "entry_keys": [e3], "replied": False}]
    fenced_text = ("Here is today's brief.\n\n```json\n" + json.dumps({
        "one": [["Corporate Workshops", "quote due Friday"]], "short": "one quote is urgent",
        "projects": [{"project": "P2", "stat": "ball",
                      "mean": "Rafi wrote \"we need the quote before Friday for 40 staff\" and added \"the board will sign it off next week\".",
                      "sources": ["C2"],
                      "props": [{"kind": "update", "task": "Quote-For-Harbour-Bank.md",
                                 "now": "send it today, they said \u201cthe whole team is keen to start soon\u201d",
                                 "src": ["C2"]}]}],
        "close": []}, ensure_ascii=False, indent=1) + "\n```\n")
    return {
        "clean-four-kinds": {"inputs": base, "result": json.dumps(clean, ensure_ascii=False, indent=1),
                             "expect": {"blocks": 3, "props": 4, "rejected": {}, "kinds": {"create": 1, "drop": 1, "seed": 1, "update": 1}}},
        "every-rejection": {"inputs": every_inputs, "result": json.dumps(every, ensure_ascii=False, indent=1),
                            "expect": {"blocks": 2, "props": 1, "kinds": {"update": 1},
                                       "rejected": {"already_has_card": 1, "bad_kind": 1, "bad_task_name": 2, "drop_without_entry": 1,
                                                    "empty_now": 1, "no_seed_name": 1, "no_source": 2, "no_such_task": 1,
                                                    "not_object": 1, "outside_project": 1, "seed_in_project": 1, "task_exists": 1,
                                                    "unknown_project": 1}}},
        "fenced-paraphrase": {"inputs": base, "result": fenced_text,
                              "expect": {"blocks": 1, "props": 1, "rejected": {}, "kinds": {"update": 1},
                                         "gate": {"checked": 3, "kept": 1, "rewritten": 2}}},
    }


def write_fixture(fdir, spec):
    d = Path(fdir)
    d.mkdir(parents=True, exist_ok=True)
    (d / "inputs.json").write_text(json.dumps(spec["inputs"], ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (d / "result.txt").write_text(spec["result"], encoding="utf-8")
    (d / "expect.json").write_text(json.dumps(spec["expect"], ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return d


# ---------------------------------------------------------------- selftest

def _sel_pack(sweep_json, jid):
    """The selftest's reader of a sweep.json (the reader's shape), standing in for the daemon's hook."""
    doc = json.loads(Path(sweep_json).read_text(encoding="utf-8"))
    out = []
    for c in doc.get("chats") or []:
        if c.get("jid") != jid:
            continue
        for m in c.get("messages") or []:
            out.append({"pk": m.get("pk"), "at": m.get("t") or "", "me": m.get("dir") == "out", "speaker": m.get("speaker") or "",
                        "text": m.get("text") or "", "caption": "", "transcript": "", "doc_text": ""})
    return out


class _Check(object):
    def __init__(self, name):
        self.name, self.n, self.fails = name, 0, []

    def __call__(self, cond, msg):
        self.n += 1
        print(("  ok   " if cond else "  FAIL ") + msg)
        if not cond:
            self.fails.append(msg)

    def done(self):
        if self.fails:
            print("%s selftest FAIL (%d of %d checks failed)" % (self.name, len(self.fails), self.n))
            return 1
        print("%s selftest PASS (%d checks)" % (self.name, self.n))
        return 0


def _codes_in(prompt):
    """Selftest helper: the codes a prompt assigned, by project folder, conversation name and entry name."""
    P, C, E, K = {}, {}, {}, []
    for line in prompt.split("\n"):
        m = re.match(r"^(?:### )?(P\d+) · .* · `([^`]+)`$", line)
        if m:
            P[m.group(2).split("/")[-1]] = m.group(1)
        m = re.match(r"^(C\d+) · (.+?) · ", line)
        if m:
            C[m.group(2)] = m.group(1)
        m = re.match(r"^(E\d+) · \S+ · \[\w+\] · (.+?) · ", line)
        if m:
            E[m.group(2)] = m.group(1)
        m = re.match(r"^  - card ([0-9a-f]{8}) ", line)
        if m:
            K.append(m.group(1))
    return P, C, E, K


def _selftest():
    print("python %s" % sys.version.split()[0])
    check = _Check("wa_today")
    tmp = Path(tempfile.mkdtemp(prefix="wa_today_selftest."))
    try:
        day = dt.date.today().isoformat()
        yday = (dt.date.today() - dt.timedelta(days=1)).isoformat()

        # ---- config defaults
        if WC is not None and isinstance((WC.DEFAULTS or {}).get("today"), dict):
            check(WC.DEFAULTS["today"] == DEFAULTS, "defaults agree with wa_common.DEFAULTS['today']")
        else:
            check(WC is not None, "defaults: wa_common has no today block yet (lane D adds it); module defaults stand alone")
        t = tcfg({"today": {"enabled": True, "every_minutes": 2, "max_reads": 7, "model": "", "extra_rules": "x" * 3000}})
        check(t["enabled"] is True and t["every_minutes"] == 30 and t["max_reads"] == 7 and t["model"] == DEFAULTS["model"]
              and len(t["extra_rules"]) == EXTRA_RULES_MAX, "tcfg: out of range back to default, empty model default, rules cut")

        # ---- a synthetic vault in the product's shape (an invented baking school and a personal project)
        V = tmp / "vault"
        wing = V / _TEST_WING / "02_Work"
        files = {
            _TEST_WING + "/02_Work/Deliver/Weekend-Class/_Weekend-Class-Brief.md":
                "---\nstatus: active\nstage: running\ndue: 2026-12-01\n---\n# Weekend Class\n\nIntro line.\n\n## Current state\n"
                "Two cohorts a month, ovens booked.\n### Detail\nsub detail\n## History\nold things\n",
            _TEST_WING + "/02_Work/Deliver/Weekend-Class/Tasks/Book-Oven-Technician.md":
                "---\ncb: task\nstatus: in-progress\ndue: 2026-10-25\n---\nCall the technician about oven two.\n",
            _TEST_WING + "/02_Work/Deliver/Weekend-Class/Tasks/Send-Class-Reminder.md":
                "---\ncb: task\nstatus: done\n---\nSent.\n",
            _TEST_WING + "/02_Work/Grow/Corporate-Workshops/_Corporate-Workshops-Brief.md":
                "---\nstatus: active\n---\n# Corporate Workshops\n\n## Next step\nSend the bank a quote.\n",
            _TEST_WING + "/02_Work/Grow/Corporate-Workshops/Tasks/Quote-For-Harbour-Bank.md":
                "---\ncb: task\nstatus: waiting\nwaiting_on: their headcount\n---\nQuote for the bank workshop.\n",
            _TEST_WING + "/02_Work/Run/Old-Popup/_Old-Popup-Brief.md": "---\nstatus: done\n---\n# Old Popup\n",
            "03_Personal-Wing/Personal-Projects/Kitchen-Refit/_Kitchen-Refit-Brief.md":
                "---\nstatus: active\n---\n# Kitchen Refit\nNew floor this month.\n",
            "03_Personal-Wing/Personal-Projects/Kitchen-Refit/Tasks/Choose-Tiles.md": "---\ncb: task\nstatus: not-started\n---\nPick tiles.\n",
            "99_Meta/structure-doctrine.md": "# Doctrine\n## 8 · Families\n- brief: `status: [active, done, killed]`\n"
                                             "- task (`cb: task`):\n  `status: [not-started, in-progress, waiting, blocked, parked, done]`\n",
        }
        for rel, body in files.items():
            (V / rel).parent.mkdir(parents=True, exist_ok=True)
            (V / rel).write_text(body, encoding="utf-8")
        (wing / "Build").mkdir(parents=True, exist_ok=True)
        doctrine = V / "99_Meta" / "structure-doctrine.md"
        WK, CW, KR = (_TEST_WING + "/02_Work/Deliver/Weekend-Class", _TEST_WING + "/02_Work/Grow/Corporate-Workshops",
                      "03_Personal-Wing/Personal-Projects/Kitchen-Refit")
        idx, notes = project_index(V, doctrine, lang="en")
        check(sorted(idx) == sorted([WK, CW, KR]) and idx[WK]["lane"] == "Deliver" and idx[CW]["lane"] == "Grow"
              and idx[KR]["lane"] == "Personal-Projects" and idx[WK]["name"] == "Weekend Class" and not notes,
              "index finds exactly the three active projects with lanes (done project left out)")
        check(idx[WK]["brief"] == WK + "/_Weekend-Class-Brief.md" and idx[WK]["tasks_dir"] == WK + "/Tasks", "index: brief and Tasks paths vault relative")
        V2 = tmp / "plain-vault"
        for rel in ("Projects/Alpha-Bakery/_Alpha-Bakery-Brief.md", "Clients/Beta/_Beta-Brief.md", "99_Meta/Hidden/_Hidden-Brief.md",
                    ".trash/Gone/_Gone-Brief.md", "a/b/c/d/e/f/g/_Deep-Brief.md", "Projects/Closed/_Closed-Brief.md"):
            (V2 / rel).parent.mkdir(parents=True, exist_ok=True)
            (V2 / rel).write_text("---\nstatus: %s\n---\n# T\n" % ("done" if "Closed" in rel else "active"), encoding="utf-8")
        idx2, notes2 = project_index(V2, None, lang="en")
        check(sorted(idx2) == ["Clients/Beta", "Projects/Alpha-Bakery"] and all(p["lane"] == "" for p in idx2.values())
              and any("shape" in n for n in notes2), "fallback walk on a non-product vault (99_, dot, too deep and done skipped)")
        check(task_statuses(doctrine) == ["not-started", "in-progress", "waiting", "blocked", "parked", "done"], "doctrine statuses read")
        _i, n3 = project_index(V, tmp / "no-doctrine.md", lang="en")
        check(task_statuses(tmp / "no-doctrine.md") == FALLBACK_TASK_STATUSES and any("doctrine" in n for n in n3),
              "doctrine unreadable falls back to the five statuses, said in notes")
        mat = project_material(V, idx[WK], task_statuses(doctrine))
        check(mat["state"].startswith("#### Current state") and "sub detail" in mat["state"] and "old things" not in mat["state"]
              and [x["name"] for x in mat["tasks"]] == ["Book-Oven-Technician.md", "Send-Class-Reminder.md"] and mat["n_tasks"] == 2
              and mat["fm"].get("stage") == "running", "project_material: state section, headings demoted, open tasks first")

        # ---- a fake state database, fake run folders, a fake buffer
        live = tmp / "live"
        (live / "runs").mkdir(parents=True)
        db = live / "state.sqlite"
        conn = sqlite3.connect(str(db))
        conn.executescript("""
        CREATE TABLE conversations(jid TEXT PRIMARY KEY, name TEXT, kind TEXT, needs_me INTEGER, last_out_at TEXT, world TEXT,
          situation TEXT, stake TEXT, summary TEXT);
        CREATE TABLE runs(id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, jid TEXT, name TEXT, ok INTEGER, kind TEXT,
          run_dir TEXT, output TEXT);
        """)
        J1, J2, J3, J4 = ("60120000001@s.whatsapp.net", "60120000002@s.whatsapp.net", "60120000003@s.whatsapp.net",
                          "120363000000000001@g.us")
        convs = [(J1, "Mei Tan", "direct", 1, None, "work", "asks to move her seat to the November weekend class", "a returning student", ""),
                 (J2, "Rafi Osman", "direct", 1, None, "work", "waiting for the corporate workshop quote for 40 staff", "a bank team day", ""),
                 (J3, "Uncle Lim", "direct", 0, None, "personal", "tile samples arrive Thursday for the new floor", "", ""),
                 (J4, "Bread Club", "group", 0, None, "other", "members chat about a monthly bread box", "", "")]
        conn.executemany("INSERT INTO conversations VALUES (?,?,?,?,?,?,?,?,?)", convs)
        packs = {J1: [{"pk": 11, "t": day + " 09:12:00", "dir": "in", "speaker": "Mei Tan", "text": "can I move my seat to the November weekend class please"}],
                 J2: [{"pk": 21, "t": day + " 11:05:00", "dir": "in", "speaker": "Rafi Osman", "text": "Hi, we need the quote before Friday for 40 staff, thanks!"}],
                 J3: [{"pk": 31, "t": day + " 12:00:00", "dir": "in", "speaker": "Uncle Lim", "text": "samples come Thursday"}],
                 J4: [{"pk": 41, "t": day + " 13:00:00", "dir": "in", "speaker": "Ana", "text": "a bread box every month would be lovely"}]}

        def add_run(jid, at, ok=1, kind="direct", caps=None):
            rd = live / "runs" / ("triage-%s-%s" % (jid.split("@")[0], at.replace(":", "").replace(" ", "-")))
            rd.mkdir(parents=True, exist_ok=True)
            (rd / "sweep.json").write_text(json.dumps({"chats": [{"jid": jid, "messages": packs[jid]}]}), encoding="utf-8")
            cur = conn.execute("INSERT INTO runs(started_at, jid, name, ok, kind, run_dir, output) VALUES (?,?,?,?,?,?,?)",
                               (at, jid, "x", ok, kind, str(rd), json.dumps({"captures": caps or []})))
            conn.commit()
            return cur.lastrowid
        add_run(J1, yday + "T20:00:00")
        add_run(J1, day + "T08:00:00")
        r_j1 = add_run(J1, day + "T09:15:00", caps=[{"kind": "request_dated", "text": "move her seat to the November class"}])
        add_run(J2, day + "T11:10:00", ok=0)
        r_j2 = add_run(J2, day + "T11:06:00")
        add_run(J3, day + "T12:01:00")
        add_run(J4, day + "T13:01:00", kind="group")
        add_run(J4, day + "T13:30:00", kind="dossier")
        buf = tmp / "intake-buffer.md"
        buf.write_text("Machine intake (fake file).\n\n"
                       "### %s · [wa] · [[Mei-Tan]] · move her seat to the November class\n"
                       "- 🗣 %s 09:12「can I move my seat to the November weekend class please」\n- 我这边: 待归位\n"
                       "- 机器猜: (work · 有日期的请求) 无\n- 深挖: `dossier.py prepare --name Mei-Tan`\n- 状态: open\n\n"
                       "### %s · [wa] · Dana Wong (无档) · said thanks for the recipe\n"
                       "- ≈ %s 10:40 大意, 非逐字「thanks for the recipe」\n- 我这边: 待归位\n- 机器猜: (other · 钱) 无\n"
                       "- 深挖: `dossier.py prepare --name Dana Wong`\n- 状态: open\n\n## 已归\n"
                       % (day, day[5:], day, day[5:]), encoding="utf-8")
        srcs = today_sources(conn, day)
        check([s["key"] for s in srcs] == [J1, J2, J3, J4] and srcs[0]["run_id"] == r_j1 and srcs[1]["run_id"] == r_j2
              and srcs[0]["captures"][0]["kind"] == "request_dated" and srcs[0]["needs_me"] is True,
              "today_sources: latest ok triage run per conversation today (failed, yesterday and dossier runs ignored)")
        ents = open_entries(buf, conn)
        check(len(ents) == 2 and ents[0]["key"] == J1 and ents[0]["verified"] and ents[0]["name"] == "Mei-Tan"
              and ents[1]["key"] == "wa:Dana Wong" and not ents[1]["verified"] and ents[1]["mark"] == "≈",
              "open_entries: name matches a conversation (brackets stripped), else wa:<name>; marks read")

        # ---- candidates
        ensure_tables(conn)
        (live / "topic-map.json").write_text(json.dumps({"rules": [{"keys": ["tile samples"], "files": [KR + "/_Kitchen-Refit-Brief.md"]},
                                                                  {"keys": ["nothing here"], "files": ["Elsewhere/x.md"]}]}), encoding="utf-8")
        cands = candidates(conn, srcs, ents, idx, V, live / "topic-map.json")
        check(cands[J1] == [WK] and cands[J2] == [CW], "candidates by name (folder words and brief title)")
        check(cands[J3] == [KR], "candidates by topic map rule pointing into a project folder")
        check(cands[J4] == [UNASSIGNED] and cands["wa:Dana Wong"] == [UNASSIGNED], "candidates: nothing matched gives unassigned")
        conn.execute("INSERT INTO today_assign VALUES (?,?,'auto',?)", (J4, CW, day + "T07:00:00"))
        conn.commit()
        c2 = candidates(conn, srcs, ents, idx, V, live / "topic-map.json")
        check(c2[J4] == [CW], "candidates by auto assign")
        unassign(conn, J4, CW, now=day + "T07:30:00")
        c3 = candidates(conn, srcs, ents, idx, V, live / "topic-map.json")
        na = _rows(conn, "SELECT kind FROM today_assign WHERE key=? AND project=?", (J4, CW))
        unassign(conn, J2, CW, now=day + "T07:31:00")
        c4 = candidates(conn, srcs, ents, idx, V, live / "topic-map.json")
        conn.execute("DELETE FROM today_assign WHERE key=? AND kind='negative'", (J2,))
        conn.commit()
        check(c3[J4] == [UNASSIGNED] and [r["kind"] for r in na] == ["negative"] and c4[J2] == [UNASSIGNED],
              "candidates minus negative assign (unassign removes the auto row, a negative row beats a name match)")

        # ---- decide_tick, every why
        cfg = {"owner_label": "Sam Lee", "board": {"lang": "en"}, "paths": {"doctrine": str(doctrine)},
               "today": {"enabled": True, "every_minutes": 30, "manual_min_gap_seconds": 60, "max_reads": 6,
                         "extra_rules": "Never propose anything for the Bread Club."}}
        tick_db = sqlite3.connect(":memory:")
        tick_db.executescript("CREATE TABLE runs(id INTEGER, started_at TEXT, jid TEXT, ok INTEGER, kind TEXT, run_dir TEXT, output TEXT, name TEXT);"
                              "CREATE TABLE conversations(jid TEXT, name TEXT, last_out_at TEXT);")
        ensure_tables(tick_db)
        nowd = dt.datetime.combine(dt.date.today(), dt.time(15, 0))
        sig0, _p = inputs_sig(tick_db, day, buf)

        def tick(**kw):
            a = dict(conn=tick_db, cfg=cfg, day=day, now=nowd, last_sig="", buffer_path=buf)
            a.update(kw)
            return decide_tick(**a)["why"]

        def put_build(minutes_ago, sig="other"):
            tick_db.execute("DELETE FROM today_brief")
            tick_db.execute("INSERT INTO today_brief(date, built_at, inputs_sig, json) VALUES (?,?,?,?)",
                            (day, _iso(nowd - dt.timedelta(minutes=minutes_ago)), sig, "{}"))
            tick_db.commit()
        check(tick(cfg={"today": {"enabled": False}}) == "disabled", "decide_tick: disabled")
        check(tick(busy=True) == "busy" and tick(busy=True, manual=True) == "already", "decide_tick: busy, and already when manual")
        put_build(0.5)
        check(tick(manual=True) == "already", "decide_tick: manual within the gap answers already")
        put_build(90)
        check(tick(last_sig=sig0) == "same_sig", "decide_tick: same_sig against the last seen sig")
        put_build(90, sig=sig0)
        check(tick() == "same_sig", "decide_tick: same_sig against the latest brief's inputs_sig")
        put_build(10)
        check(tick() == "too_soon" and tick(manual=True) == "go", "decide_tick: too_soon (manual exempt)")
        put_build(90)
        check(tick(gate_down=True) == "brain_down", "decide_tick: brain_down")
        check(tick(running_n=2, concurrency=2) == "queued", "decide_tick: queued at the concurrency limit")
        check(tick() == "go", "decide_tick: go")

        # ---- the fake brain
        calls = []
        REPLY = "REPLY-SECRET-77 see you on Saturday"

        def outbound(jid, since, n=3):
            return [{"at": day + "T09:40:00", "type": "text", "text": REPLY}, {"at": day + "T09:41:00", "type": "voice", "text": ""}] if jid == J1 else []
        hooks = {"pack_messages": _sel_pack, "outbound_since": outbound}
        plans = []

        def fake(prompt, run_dir):
            calls.append(prompt)
            P, C, E, K = _codes_in(prompt)
            raw = plans[0](P, C, E, K)
            lines = []
            for i in range(8):
                lines.append(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "x%d" % i}}]}}))
            Path(run_dir, "claude.stdout.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
            return {"text": "```json\n" + json.dumps(raw, ensure_ascii=False) + "\n```", "is_error": False, "error": "",
                    "input_tokens": 1000, "output_tokens": 300, "cache_read_tokens": 500, "cache_create_tokens": 0}

        def plan_a(P, C, E, K):
            return {"one": [["Corporate Workshops", "the bank waits for the quote"]], "short": "a quote and a seat change wait for you",
                    "projects": [
                        {"project": P["Corporate-Workshops"], "stat": "stuck",
                         "mean": "Rafi wrote 「we need the quote before Friday for 40 staff」 and 「they will cancel everything tomorrow」.",
                         "sources": [C["Rafi Osman"]],
                         "props": [{"kind": "update", "task": "Quote-For-Harbour-Bank.md", "now": "send the quote for 40 staff before Friday",
                                    "fm_set": {"status": "in-progress", "colour": "red", "due": "next week"}, "src": [C["Rafi Osman"]]}]},
                        {"project": P["Weekend-Class"], "stat": "ball", "mean": "Mei wants her seat moved to November.",
                         "sources": [C["Mei Tan"], E["Mei-Tan"]],
                         "props": [{"kind": "create", "task": "Move-Mei-Seat-To-November.md",
                                    "now": "move Mei to the November class \u2014 confirm by Friday", "fm_set": {"status": "not-started", "due": "2026-10-30"},
                                    "src": [E["Mei-Tan"], C["Mei Tan"]]}]},
                        {"project": P["Kitchen-Refit"], "stat": "move", "mean": "tile samples arrive Thursday", "sources": [C["Uncle Lim"]],
                         "props": [{"kind": "update", "task": "Choose-Tiles.md", "now": "pick from the samples on Thursday", "src": [C["Uncle Lim"]]}]},
                        {"project": "unassigned", "stat": "quiet", "mean": "club chatter and a thank you", "sources": [C["Bread Club"], E["Dana Wong"]],
                         "props": [{"kind": "drop", "now": "a thank you, nothing to place", "src": [E["Dana Wong"]]},
                                   {"kind": "seed", "task": "Bread Box Subscription", "now": "members asked for a monthly bread box",
                                    "src": [C["Bread Club"]]}]},
                        {"project": "P99", "stat": "move", "mean": "no such project", "sources": [C["Mei Tan"]]}],
                    "close": []}
        plans[:] = [plan_a]

        # dry run: a prompt, no call, nothing written
        def counts():
            return [conn.execute("SELECT COUNT(*) FROM %s" % tb).fetchone()[0] for tb in ("today_brief", "today_proposals", "today_assign")]
        before, runs_before = counts(), sorted(os.listdir(str(live / "runs")))
        dr = build(conn, cfg, day, buf, V, live, call=fake, hooks=hooks, dry_run=True, now=nowd, lang="en")
        check(dr.get("dry_run") and "# Today brief for Sam Lee" in dr["prompt"] and not calls and counts() == before
              and sorted(os.listdir(str(live / "runs"))) == runs_before and not (live / "today").exists()
              and dr["candidates"][J3] == [KR], "dry run builds a prompt and calls nothing, writes nothing")
        pr = dr["prompt"]
        check("Never propose anything for the Bread Club." in pr and "## Owner's own rules" in pr and "| `work` |" in pr
              and "parked" in pr and "`unassigned` is a legal answer" in pr and "at most 6 vault files" in pr,
              "prompt: owner label, worlds table, statuses from the doctrine, max reads, rules, owner's own rules")
        check(('- Sam Lee replied %s 09:40: "%s"' % (day[5:], REPLY)) in pr and ("replied %s 09:41: \"[voice]\"" % day[5:]) in pr
              and "C1.1 request_dated · move her seat" in pr and "≈ gist, not verbatim: thanks for the recipe" in pr,
              "prompt: owner replies (non-text as [voice]), capture codes, gist entries")
        pb = _prepare(conn, cfg, day, buf, V, live, force=True, lang="en", hooks={"outbound_since": lambda j, s_, n=3: (
            [{"at": day + "T09:40:00", "type": "text", "text": "[urgent] " + REPLY}, {"at": day + "T09:41:00", "type": "voice", "text": ""}]
            if j == J1 else [])})
        check(pb["vctx"]["reply_texts"] == ["[urgent] " + REPLY], "scrub list holds a reply that starts with a bracket, never the [voice] placeholder")
        try:
            build(conn, cfg, day, buf, V, live, call=None, hooks=hooks, now=nowd, lang="en")
            raised = False
        except RuntimeError:
            raised = True
        check(raised, "build without call and without dry run raises")

        # full build
        r1 = build(conn, cfg, day, buf, V, live, call=fake, hooks=hooks, now=nowd, lang="en")
        b1 = r1.get("brief") or {}
        nb, npp, na_ = counts()
        check(r1.get("ok") and len(calls) == 1 and nb == 1 and npp == 5 and na_ >= 4, "full build with a fake call writes three tables")
        check([p["id"] for p in b1.get("projects", [])] == [CW, WK, KR, UNASSIGNED] and b1["projects"][0]["statL"] == "stuck",
              "brief has the right blocks in order stuck, ball, move, unassigned last")
        corp = b1["projects"][0]
        check("🗣「we need the quote before Friday for 40 staff」" in corp["mean"] and "≈ gist: they will cancel everything tomorrow" in corp["mean"]
              and b1["quote_gate"]["kept"] >= 1 and b1["quote_gate"]["rewritten"] >= 1, "QuoteGate kept and rewritten in a build")
        cprop = corp["props"][0]
        wprop = b1["projects"][1]["props"][0]
        check(cprop["fm_set"] == {"status": "in-progress"} and "\u2014" not in wprop["now"] and ", confirm by Friday" in wprop["now"]
              and wprop["fm_set"] == {"status": "not-started", "due": "2026-10-30"}, "validator: fm_set filtered, house punctuation normalized")
        check(any(r["why"] == "unknown_project" for r in b1["rejected"]) and any("8" in n and "6" in n for n in b1["notes"]),
              "build: unknown project rejected, reads over max_reads noted")
        check(b1["run_dir"] == r1["run_dir"] and "/" not in b1["run_dir"] and b1["usage"] == {"read": 1500, "output": 300},
              "brief: run folder name only, usage read and output")
        ap = api_payload(conn, day, "en")
        check(ap["ok"] and not ap["empty"] and not ap["stale"] and ap["built_at"].startswith(day) and "seen" not in ap,
              "api_payload not empty and built_at today, no seen")
        tf = live / "today" / ("%s.json" % day)
        check(tf.is_file() and (os.stat(str(tf)).st_mode & 0o777) == 0o600 and "seen" not in json.loads(tf.read_text(encoding="utf-8")),
              "live/today/<date>.json written 0600 without seen")
        rd1 = live / "runs" / r1["run_dir"]
        blob = []
        for tb in ("today_brief", "today_proposals", "today_assign"):
            for row in _rows(conn, "SELECT * FROM %s" % tb):
                blob.append(_jd(row))
        blob.append(tf.read_text(encoding="utf-8"))
        blob.append((rd1 / "today_brief.json").read_text(encoding="utf-8"))
        blob.append((rd1 / "inputs.json").read_text(encoding="utf-8"))
        check(REPLY in (rd1 / "prompt.md").read_text(encoding="utf-8") and not any("REPLY-SECRET" in x for x in blob),
              "owner reply text only in prompt.md: grep every column, the brief files and inputs.json: absent")
        check((os.stat(str(rd1)).st_mode & 0o777) == 0o700, "run folder 0700")
        auto = set((r["key"], r["project"]) for r in _rows(conn, "SELECT key, project FROM today_assign WHERE kind='auto'"))
        check({(J1, WK), (J2, CW), (J3, KR)} <= auto and (J4, CW) not in auto, "auto assign rows for P block sources")

        r_same = build(conn, cfg, day, buf, V, live, call=fake, hooks=hooks, now=nowd + dt.timedelta(minutes=40), lang="en")
        check(r_same.get("skipped") and r_same.get("why") == "same_sig" and len(calls) == 1, "same inputs give same_sig without calling")

        # ---- validator, every rejection reason, against a real context
        prep = _prepare(conn, cfg, day, buf, V, live, hooks=hooks, force=True, lang="en")
        P, C, E, K = _codes_in(build_prompt(prep["pctx"], prep["t"], "en"))
        vctx = prep["vctx"]
        b_, p_, rj, o_, s_, n_, cl_ = validate(["not", "a", "dict"], vctx)
        check(len(rj) == 1 and rj[0]["why"] == "not_object" and not b_ and not p_, "validator rejects: not an object")
        pw, pc, pk = P["Weekend-Class"], P["Corporate-Workshops"], P["Kitchen-Refit"]
        raw = {"projects": [
            {"project": "P77", "stat": "move"},
            {"project": pw, "stat": "sideways", "sources": [C["Mei Tan"], "C99"], "props": [
                {"kind": "rename", "task": "Book-Oven-Technician.md", "now": "x", "src": [C["Mei Tan"]]},
                {"kind": "update", "task": "Book-Oven-Technician.md", "now": "x", "src": []},
                {"kind": "update", "task": "Book-Oven-Technician.md", "now": "x", "src": ["C99"]},
                {"kind": "seed", "task": "Pastry Club", "now": "x", "src": [C["Mei Tan"]]},
                {"kind": "update", "task": "Order-Flour.md", "now": "x", "src": [C["Mei Tan"]]},
                {"kind": "create", "task": "order flour", "now": "x", "src": [C["Mei Tan"]]},
                {"kind": "create", "task": "Class-2026-11-Plan", "now": "x", "src": [C["Mei Tan"]]},
                {"kind": "create", "task": "Send-Class-Reminder.md", "now": "x", "src": [C["Mei Tan"]]},
                {"kind": "drop", "now": "x", "src": [C["Mei Tan"]]},
                {"kind": "update", "task": "Book-Oven-Technician.md", "now": " ", "src": [C["Mei Tan"]]},
                {"kind": "update", "task": "Book-Oven-Technician", "now": "y" * 600, "src": [C["Mei Tan"] + ".1"]}]},
            {"project": pc, "stat": "ball", "props": [
                {"kind": "update", "task": "Quote-For-Harbour-Bank.md", "now": "x", "src": [E["Dana Wong"]]}]},
            {"project": "unassigned", "props": [{"kind": "update", "task": "Choose-Tiles.md", "now": "x", "src": [C["Uncle Lim"]]}]}]}
        b_, p_, rj, o_, s_, n_, cl_ = validate(raw, vctx)
        why = [r["why"] for r in rj]
        for reason in ("unknown_project", "bad_kind", "no_source", "seed_in_project", "no_such_task", "bad_task_name", "task_exists",
                       "drop_without_entry", "empty_now", "already_has_card", "outside_project"):
            check(reason in why, "validator rejects: %s" % reason)
        check(why.count("no_source") == 2 and why.count("bad_task_name") == 2, "validator: empty and unknown sources, lower case and dated names")
        check(len(p_) == 1 and len(p_[0]["now"]) == NOW_MAX and p_[0]["task"] == "Book-Oven-Technician.md" and b_[0]["stat"] == "quiet"
              and b_[0]["src"] == [C["Mei Tan"]] and any("sideways" in n for n in n_), "validator: now cut to 400, .md added, unknown stat read as quiet")

        # ---- "not this project" binds the answer; the owner's reply words never reach storage
        nctx = dict(vctx)
        mei = vctx["ccodes"][C["Mei Tan"]]["key"]
        nctx["neg"] = set([(mei, vctx["pcodes"][pw])])
        raw_n = {"projects": [{"project": pw, "stat": "move", "sources": [C["Mei Tan"]], "props": [
            {"kind": "update", "task": "Book-Oven-Technician.md", "now": "x", "src": [C["Mei Tan"]]}]}]}
        b_, p_, rj, o_, s_, n_, cl_ = validate(raw_n, nctx)
        check(not p_ and [r["why"] for r in rj] == ["not_this_project"] and b_[0]["src"] == [] and any(C["Mei Tan"] in n for n in n_),
              "validator: a source the owner took out of a project cannot come back there (not_this_project)")
        b_, p_, rj, o_, s_, n_, cl_ = validate(raw_n, vctx)
        check(len(p_) == 1 and not rj, "validator: the same answer without the negative row is accepted")
        rctx = dict(vctx)
        rctx["reply_texts"] = ["sure, bring the zebra striped cake tins on Monday morning"]
        raw_r = {"short": "Sam said bring the zebra striped cake tins on Monday", "one": [["Weekend Class", "ok"], ["the zebra striped cake tins", "x"]],
                 "projects": [{"project": pw, "stat": "move", "mean": "Agreed: bring the zebra striped cake tins, done",
                               "props": [{"kind": "update", "task": "Book-Oven-Technician.md",
                                          "now": "Reply said bring the Zebra Striped Cake Tins on Monday", "src": [C["Mei Tan"]],
                                          "fm_set": {"waiting_on": "bring the zebra striped cake tins"}},
                                         {"kind": "create", "task": "Bring-The-Zebra-Striped-Cake-Tins.md", "now": "x", "src": [C["Mei Tan"]]},
                                         {"kind": "create", "task": "the zebra striped cake tins", "now": "x", "src": [C["Mei Tan"]]}]}]}
        b_, p_, rj, o_, s_, n_, cl_ = validate(raw_r, rctx)
        stored = json.dumps([b_, p_, s_, o_, rj, n_], ensure_ascii=False).lower()
        check("zebra" not in stored and "(your reply, not copied)" in stored,
              "validator: the owner's reply words in the model's text are replaced before anything is stored")
        check(scrub_replies("bring tins", ["sure, bring the tins"], 6, "(r)") == "bring tins",
              "scrub_replies leaves a short overlap alone")
        ref = "(r)"
        long_r = "sure, bring the zebra striped cake tins on monday morning"
        u_ = scrub_replies("[urgent] bring the zebra striped cake tins", ["[urgent] bring the zebra striped cake tins"], 6, ref)
        check("urgent" not in u_ and "zebra" not in u_ and "(r)" in u_,
              "scrub_replies: a reply starting with a bracket is scrubbed like any other")
        many = "Sam said bring the zebra striped " + " ".join(["(bring the zebra striped cake tins on monday)"] * 25)
        check("zebra" not in scrub_replies(many, [long_r], 6, ref), "scrub_replies: no cap on how many stretches are replaced")
        check("明天" not in scrub_replies("他说明天下午，三点带蛋糕，模具过来", ["明天下午三点带蛋糕模具过来"], 6, ref)
              and "明" not in scrub_replies("明 天 下 午 三 点 带 蛋 糕 模 具", ["明天下午三点带蛋糕模具"], 6, ref),
              "scrub_replies: CJK spacing and punctuation are ignored")
        check(scrub_replies("Bringthezebra tins, then bring the zebra striped cake later", [long_r], 6, ref)
              == "Bringthezebra tins, then (r) later", "scrub_replies: a real copy is found even when a longer near match sits in one word")
        check(_scrub_tree({"bring the zebra striped cake": 1}, [long_r], 6, ref) == {"(r)": 1}, "_scrub_tree scrubs dict keys")
        split = ["bring the zebra", "striped cake tins"]
        raw_j = {"one": [["Weekend Class", split]], "short": split,
                 "projects": [{"project": pw, "stat": split, "mean": split, "props": [
                     {"kind": "update", "task": "Book-Oven-Technician.md", "now": "x", "src": [C["Mei Tan"]],
                      "fm_set": {"waiting_on": split, "priority": split, "status": split, "due": split}},
                     {"kind": split, "task": {"a": "bring the zebra", "b": "striped cake tins"}, "now": "x"},
                     {"kind": "create", "task": split, "now": split, "src": [C["Mei Tan"]]}]},
                     {"project": "seed:bri[ng th]e zeb[ra stri]ped cake", "stat": "move", "mean": "x",
                      "props": [{"kind": "seed", "task": "br`ing th`e zeb`ra str`iped", "now": "x", "src": [C["Mei Tan"]]}]},
                     {"project": split, "props": [{"kind": "update", "task": split, "now": "x"}]}]}
        b_, p_, rj, o_, s_, n_, cl_ = validate(raw_j, rctx)
        stored = json.dumps([b_, p_, s_, o_, rj, n_, cl_], ensure_ascii=False).lower()
        check("zebra" not in stored and "(your reply, not copied)" in stored,
              "validator: a reply split across a list, or broken by brackets and backticks, is still replaced (last pass on what is stored)")
        check(all("zebra" not in scrub_replies(x, [long_r], 6, ref).lower() for x in ("bring the ze-bra striped cake", "bring the zebra str'iped cake"))
              and scrub_replies("\uff42\uff52\uff49\uff4e\uff47 \uff54\uff48\uff45 \uff5a\uff45\uff42\uff52\uff41 \uff53\uff54\uff52\uff49\uff50\uff45\uff44", [long_r], 6, ref) == ref
              and scrub_replies("ok at 3 pm dont be late", ["ok at 3pm, don't be late!"], 6, ref) == ref
              and "\u660e\u5929" not in scrub_replies("\u4ed6\u8bf4\u660e\u5929\u4e0b\u5348\uff13\u70b9\u6211\u4f1a\u5230", ["\u660e\u5929\u4e0b\u5348" + "3" + "\u70b9\u6211\u4f1a\u5230"], 6, ref),
              "scrub_replies: spacing or punctuation inside a word and full-width letters and digits do not hide a copy")
        rb = "the new robot takes rain from the bin first, then the van"
        enc = ["bring&#10;the&#10;zebra&#10;striped&#10;cake", "bring%0Athe%0Azebra%0Astriped%0Acake", "bring%2520the%2520zebra%2520striped",
               "bring&ensp;the&#x20;zebra&nbsp striped", "bring\\U00000020the\\040zebra\\u{20}striped", "bring\\u005cnthe\\u005cnzebra\\u005cnstriped",
               "bring<br>the<br/>zebra</p><p>striped", "&#26126;&#22825;&#19979;&#21320;&#19977;&#28857;", "%E6%98%8E%E5%A4%A9%E4%B8%8B%E5%8D%88%E4%B8%89%E7%82%B9"]
        rs_ = [long_r, "\u660e\u5929\u4e0b\u5348\u4e09\u70b9\u5e26\u86cb\u7cd5", rb]
        check(all(scrub_replies(x, rs_, 6, ref) == ref for x in enc)
              and scrub_replies("the\\new\\robot\\takes\\rain\\from", rs_, 6, ref) == ref,
              "scrub_replies: HTML entities, percent codes, other escape forms and tags between the words do not hide a copy, nor does a bare backslash")
        nest = ["the\\new&#32;robot\\takes&#32;rain\\from&#32;the", "the\\new%0Arobot\\takes%0Arain\\from", "bring&lt;br&gt;the&lt;br&gt;zebra&lt;br&gt;striped",
                "bring\\u003cbr\\u003ethe\\u003cbr\\u003ezebra\\u003cbr\\u003estriped", "bring%3Cbr%3Ethe%3Cbr%3Ezebra%3Cbr%3Estriped",
                "bring&amp;nbsp;the&amp;nbsp;zebra&amp;nbsp;striped"]
        check(all(scrub_replies(x, rs_, 6, ref) == ref for x in nest),
              "scrub_replies: escapes read in rounds (an escaped tag, a double-encoded entity) and a bare backslash next to an encoding do not hide a copy")
        ent = ["bring&nbspthe&nbspzebra&nbspstriped", "bring&ampthe&ampzebra&ampstriped", "bring&#00000032;the&#x0000020;zebra&#032;striped",
               "bring\\0the\\azebra\\N{SPACE}striped\\u{0000020}cake", "bring%u0020the%u0020zebra%u0020striped",
               "bring\u3164the\u3164zebra\u3164striped"]
        check(all(scrub_replies(x, rs_, 6, ref) == ref for x in ent)
              and scrub_replies("\u0398\u0391 \u03a6\u0388\u03a1\u0395\u0399\u03a3 \u03a4\u039f\u03a5\u03a3 \u0394\u038a\u03a3\u039a\u039f\u03a5\u03a3",
                                ["\u03b8\u03b1 \u03c6\u03ad\u03c1\u03b5\u03b9\u03c2 \u03c4\u03bf\u03c5\u03c2 \u03b4\u03af\u03c3\u03ba\u03bf\u03c5\u03c2"], 6, ref) == ref,
              "scrub_replies: legacy entities with no semicolon, leading zeros, rarer escapes, Hangul fillers and capitals of another script do not hide a copy")
        edge = ["bring\\40the\\12zebra\\7striped\\00cake", "bring\\u005cu005cu005cu005cu005cnthe\\u005cu005cu005cu005cu005cnzebra\\u005cu005cu005cu005cu005cnstriped",
                "bring&amp;amp;amp;amp;amp;nbsp;the&amp;amp;amp;amp;amp;nbsp;zebra&amp;amp;amp;amp;amp;nbsp;striped",
                "bring&#99999999;the&#x1234567;zebra&#10000032;striped", "bring\\x20the\\x20zebra\\x20striped",
                'bring<span title="a>b">the<span title="' + "x" * 90 + '">zebra<br\n/>striped<!-- ' + "note " * 30 + '-->cake']
        check(all(scrub_replies(x, rs_, 6, ref) == ref for x in edge),
              "scrub_replies: one or two digit octal, chains of any depth, long numeric references and long, multi-line or quoted tags do not hide a copy")
        deep = ["bring the zebra\\" + "x5c" * 70 + "nstriped cake", "bring the zebra&" + "amp;" * 70 + "nbsp;striped cake",
                'bring the zebra<i title="a>' + "x" * 401 + '">striped cake', "bring<i title=it's>the<i title=it's>zebra<i title=it's>striped",
                "bring<a<b>the<a<b>zebra<a<b>striped<a<b>cake"]
        check(all(scrub_replies(x, rs_, 6, ref) == ref for x in deep)
              and scrub_replies(r"\u660e\\\u5929\\\u4e0b\\\u5348\\3\\\u70b9\\\u6211", ["\u660e\u5929\u4e0b\u53483\u70b9\u6211\u4f1a\u5230"], 6, ref) == ref,
              "scrub_replies: chains 70 deep, long quoted values, malformed tags read the HTML way and a backslash before a digit do not hide a copy")
        check(scrub_replies("ok: use a%20b%20c as the folder name for the shoot", ["use a%20b%20c as the folder name for the shoot"], 6, ref) == "ok: " + ref,
              "scrub_replies: a reply that itself holds an escape-like run (a percent code) is still found (the plain reading)")
        plain = ["C:\\new\\table\\bring\\notes.md", "see https://x.test/a%20b?q=zebra%20striped", "Price&nbsp;RM&nbsp;50 &amp; more, line<br>two",
                 "regex \\bword\\b \\d+", "\\u0041\\u0042 and \\x41"]
        check(all(scrub_replies(x, rs_, 6, ref) == x for x in plain),
              "scrub_replies: text with no copy of a reply is stored exactly as written (paths, URLs, entities, escapes)")
        raw_e = {"short": "bring\\nthe\\nzebra\\nstriped\\ncake", "one": [["Weekend Class", "bring\\tthe\\tzebra\\tstriped"]],
                 "projects": [{"project": pw, "stat": "move", "mean": "bring%20the%20zebra%20striped",
                               "props": [{"kind": "update", "task": "Book-Oven-Technician.md", "src": [C["Mei Tan"]],
                                          "now": "bring\\\\nthe\\\\nzebra\\\\nstriped \\u0063ake"}]}]}
        b_, p_, rj, o_, s_, n_, cl_ = validate(raw_e, rctx)
        stored = json.dumps([b_, p_, s_, o_, rj, n_, cl_], ensure_ascii=False).lower()
        check("zebra" not in stored and stored.count("(your reply, not copied)") >= 4,
              "validator: escapes the model wrote out inside a field (a backslash n or t, any depth, an encoded space) do not hide a copy")
        check(scrub_replies("\u5979\u8bf4\u3042\u308a\u304c\u3068\u3046\u3054\u3056\u3044\u307e\u3059", ["\u3042\u308a\u304c\u3068\u3046\u3054\u3056\u3044\u307e\u3059"], 6, ref)
              == "\u5979\u8bf4 " + ref, "scrub_replies: kana count character by character like Chinese")
        pr_n = dict(prep["pctx"])
        pr_n["source_list"] = [dict(x, not_in=["P2"]) if i == 0 else x for i, x in enumerate(pr_n["source_list"])]
        check("· not P2" in build_prompt(pr_n, prep["t"], "en"), "prompt marks a source the owner took out of a project (not P2)")

        # ---- QuoteGate standalone
        g = QuoteGate([[{"pk": 1, "at": "", "me": False, "speaker": "A", "text": "我们下个月想办一场三十人的团体课"}]], 6, lang="zh")
        out_k = g("她说「我们下个月想办一场三十人的团体课」")
        out_r = g("她说「下个月大概要来四十个人左右吧」, 又说「好」 <b>bold</b>")
        check(out_k == "她说🗣「我们下个月想办一场三十人的团体课」", "QuoteGate keeps a real quote with the stored text")
        check(out_r == "她说≈ 大意: 下个月大概要来四十个人左右吧, 又说「好」 bold" and g.stats() == {"checked": 2, "kept": 1, "rewritten": 1},
              "QuoteGate rewrites a paraphrase as a gist (short segment and tags left alone)")
        check(g(out_k) == out_k, "QuoteGate leaves an already checked quote alone")

        # ---- cards: decide, upsert, newer
        corp_id = cprop["id"]
        kit_id = b1["projects"][2]["props"][0]["id"]
        week_id = wprop["id"]
        check(proposal_id(CW, "update", "Quote-For-Harbour-Bank.md") == corp_id and len(corp_id) == 16, "proposal_id stable (16 hex)")
        dm = decide(conn, corp_id, 9, "ok", lang="en")
        check(dm["ok"] is False and dm.get("reason") == "rev" and dm["current"]["rev"] == 1, "decide rev mismatch refused with current")
        check(decide(conn, corp_id, 1, "maybe").get("reason") == "action", "decide: unknown action refused")
        do = decide(conn, corp_id, 1, "ok", lang="en")
        check(do["ok"] and do["current"]["status"] == "ok" and do["current"]["rev"] == 1, "decide ok sets the status, never the rev")
        dn = decide(conn, corp_id, 1, "note", note="call them first", lang="en")
        check(dn["ok"] and dn["current"]["note"] == "call them first", "decide note")

        # second build: two conversations moved, a partial scope
        r_j2b = add_run(J2, day + "T15:30:00")
        add_run(J3, day + "T15:31:00")

        def plan_b(P, C, E, K):
            return {"one": [], "short": "",
                    "projects": [
                        {"project": P["Corporate-Workshops"], "stat": "ball", "mean": "the bank asked again", "sources": [C["Rafi Osman"]],
                         "props": [{"kind": "update", "task": "Quote-For-Harbour-Bank.md", "now": "send the revised quote today",
                                    "src": [C["Rafi Osman"]]}]},
                        {"project": P["Kitchen-Refit"], "stat": "move", "mean": "samples confirmed", "sources": [C["Uncle Lim"]],
                         "props": [{"kind": "update", "task": "Choose-Tiles.md", "now": "pick two tiles on Thursday morning",
                                    "src": [C["Uncle Lim"]]}]}],
                    "close": []}
        plans[:] = [plan_b]
        r2 = build(conn, cfg, day, buf, V, live, call=fake, hooks=hooks, now=nowd + dt.timedelta(minutes=45), lang="en")
        b2 = r2.get("brief") or {}
        check(r2.get("ok") and b2["scope"]["full"] is False and b2["scope"]["projects"] == sorted([CW, KR])
              and "C1 · Mei Tan" not in calls[-1] and WK in [p["id"] for p in b2["projects"]],
              "incremental build: only moved conversations in scope, the other blocks carried")
        crow = _get_card(conn, corp_id)
        krow = _get_card(conn, kit_id)
        check(crow["status"] == "ok" and crow["rev"] == 1 and crow["now"] == "send the quote for 40 staff before Friday"
              and _jl(crow["newer_json"], {}).get("now") == "send the revised quote today", "upsert: pressed card untouched with newer")
        check(krow["status"] == "pending" and krow["rev"] == 2 and krow["now"] == "pick two tiles on Thursday morning",
              "upsert: pending rev+1 on changed content")
        pn = public_prop(crow, day, "en")
        check(pn["newer"]["now"] == "send the revised quote today" and pn["note"] == "call them first", "public_prop: newer and note")

        # ---- replies: stamp only after the latest run, never text
        conn.execute("UPDATE conversations SET last_out_at=? WHERE jid=?", (day + "T09:00:00", J1))
        conn.commit()
        rs0 = reply_stamp(conn, nowd)
        conn.execute("UPDATE conversations SET last_out_at=? WHERE jid=?", (day + "T16:10:00", J1))
        conn.commit()
        rs1 = reply_stamp(conn, nowd)
        wrow = _get_card(conn, week_id)
        check(week_id not in rs0["stamped"] and rs1["stamped"] == [week_id]
              and _jl(wrow["replied_json"], {}) == {"jid": J1, "at": day + "T16:10:00"}, "reply_stamp stamps only after the latest run")
        check(reply_stamp(conn, nowd)["stamped"] == [], "reply_stamp: an older or equal reply is not stamped twice")

        # third build: the brain closes the replied card; a close for a card not replied is skipped
        def plan_c(P, C, E, K):
            return {"one": [["Weekend Class", "Mei is sorted"]], "short": "Mei is sorted",
                    "projects": [{"project": P["Weekend-Class"], "stat": "quiet", "mean": "you told Mei it is done", "sources": [C["Mei Tan"]]}],
                    "close": [{"id": week_id[:8], "why": "you confirmed the November seat"}, {"id": kit_id[:8], "why": "not replied"}]}
        plans[:] = [plan_c]
        r3 = build(conn, cfg, day, buf, V, live, call=fake, hooks=hooks, now=nowd + dt.timedelta(minutes=90), lang="en")
        wrow = _get_card(conn, week_id)
        check(r3.get("ok") and wrow["status"] == "superseded" and _jl(wrow["retired_json"], {}).get("why") == "replied"
              and _get_card(conn, kit_id)["status"] == "pending" and any(kit_id[:8] in n for n in r3["brief"]["notes"]),
              "close only for replied cards")
        check(public_prop(wrow, day, "en")["retired_note"].startswith("you already replied"), "public_prop: retired note in plain words")
        seen = {}

        def fake_err(prompt, run_dir, scrub=None):
            seen["prompt"], seen["scrub"] = prompt, scrub
            raise RuntimeError("claude -p gave no result: you said " + REPLY + " ok")
        r_err = build(conn, cfg, day, buf, V, live, call=fake_err, hooks=hooks, force=True, now=nowd + dt.timedelta(minutes=120), lang="en")
        esc = '{"text": "' + REPLY.replace(" ", "\\n") + '"}'

        def fake_esc(prompt, run_dir, scrub=None):
            raise RuntimeError("no result: " + esc)
        r_esc = build(conn, cfg, day, buf, V, live, call=fake_esc, hooks=hooks, force=True, now=nowd + dt.timedelta(minutes=121), lang="en")

        def boom(*a, **k):
            raise ValueError("bad field: " + REPLY)
        saved_assemble = globals()["_assemble"]
        globals()["_assemble"] = boom
        plans[:] = [plan_c]
        try:
            r_exc = build(conn, cfg, day, buf, V, live, call=fake, hooks=hooks, force=True, now=nowd + dt.timedelta(minutes=122), lang="en")
        finally:
            globals()["_assemble"] = saved_assemble
        check("secret" not in str(r_esc.get("error")).lower() and "(your reply, not copied)" in str(r_esc.get("error"))
              and r_exc.get("why") == "exception" and "secret" not in str(r_exc.get("error")).lower()
              and "(your reply, not copied)" in str(r_exc.get("error")),
              "an escaped line break in a raw output tail, and an exception after the call, do not carry the owner's reply words")
        check(REPLY in seen.get("prompt", "") and callable(seen.get("scrub")) and not r_err.get("ok")
              and "secret" not in str(r_err.get("error")).lower() and "(your reply, not copied)" in str(r_err.get("error"))
              and "secret" not in seen["scrub"]("tail: " + REPLY).lower(),
              "a failed call's error text and the scrub handed to the call both replace the owner's reply words")

        # revive: a retired card comes back only with a new entry key or a newer source run
        same = {"project": WK, "kind": "create", "task": "Move-Mei-Seat-To-November.md", "now": "again", "fm_set": {},
                "entry_keys": _jl(wrow["entry_keys"], []), "jids": [J1], "runs": {J1: r_j1}, "ev": []}
        a1 = upsert_proposal(conn, day, same, _iso(nowd))[1]
        newer = dict(same, runs={J1: r_j1 + 100})
        a2 = upsert_proposal(conn, day, newer, _iso(nowd))[1]
        check(a1 == "left" and a2 == "revived" and _get_card(conn, week_id)["status"] == "pending", "upsert: retired card revived only by a new run or key")

        # ---- settle (K cards)
        seed_id = proposal_id(UNASSIGNED, "seed", "Bread Box Subscription")
        drop_id = [p["id"] for p in b1["projects"][3]["props"] if p["kind"] == "drop"][0]
        check(settle(conn, seed_id, 7, "x", lang="en").get("reason") == "rev", "settle wrong rev refused")
        check(settle(conn, drop_id, 1, "drop", lang="en").get("reason") == "keys", "settle keys card refused")
        check(settle(conn, seed_id, 1, "  ", lang="en").get("reason") == "to", "settle: empty to refused")
        for i in (1, 2):
            conn.execute("INSERT INTO today_proposals(id, date, rev, status, project, kind, entry_keys, jids) VALUES (?,?,1,'pending','x','seed','[]','[]')",
                         ("abcdef000000000%d" % i, day))
        conn.commit()
        check(settle(conn, "abcdef", 1, "x").get("reason") == "ambiguous" and settle(conn, "0123456789", 1, "x").get("reason") == "not_found",
              "settle ambiguous prefix and unknown id")
        ss = settle(conn, seed_id[:6], 1, "project [[Bread-Box]]", lang="en")
        srow = _get_card(conn, seed_id)
        check(ss["ok"] and srow["status"] == "superseded" and _jl(srow["retired_json"], {}).get("placed_to") == ["project [[Bread-Box]]"]
              and _jl(srow["retired_json"], {}).get("why") == "settled", "settle by a 6 hex prefix: superseded with placed_to")
        check(settle(conn, seed_id, 1, "again").get("reason") == "status", "settle: a retired card refused")

        # ---- retire placed entries
        rconn = sqlite3.connect(":memory:")
        ensure_tables(rconn)
        rbuf = tmp / "retire-buffer.md"
        blocks_txt = ""
        for i in range(1, 7):
            blocks_txt += ("### %s · [wa] · Person %d (无档) · item %d\n- 🗣 10-21 09:0%d「words number %d」\n- 我这边: 待归位\n"
                           "- 机器猜: (work · 钱) 无\n- 深挖: `dossier.py prepare --name Person %d`\n- 状态: open\n\n" % (day, i, i, i, i, i))
        rbuf.write_text("Fake buffer.\n\n" + blocks_txt + "## 已归\n", encoding="utf-8")
        ks = [e["key"] for e in WI.list_open(rbuf)] if WI else ["k%d" % i for i in range(6)]

        def card(proj, keys, status="pending"):
            p = {"project": proj, "kind": "drop", "task": "", "now": "n " + proj, "fm_set": {}, "entry_keys": keys, "jids": [],
                 "ev": [{"key": k, "line": "e"} for k in keys]}
            cid = upsert_proposal(rconn, day, p, day + "T10:00:00")[0]
            if status != "pending":
                rconn.execute("UPDATE today_proposals SET status=? WHERE id=?", (status, cid))
                rconn.commit()
            return cid
        cx, cy, cz = card("Run/X", [ks[0], ks[1]]), card("Run/Y", [ks[2], ks[3]]), card("Run/Z", [ks[4], ks[5]], "ok")
        ck = upsert_proposal(rconn, day, {"project": "Run/K", "kind": "seed", "task": "K", "now": "k", "entry_keys": [], "jids": []},
                             day + "T10:00:00")[0]
        miss = retire_placed(rconn, tmp / "no-such-buffer.md")
        (tmp / "empty-buffer.md").write_text("", encoding="utf-8")
        empty = retire_placed(rconn, tmp / "empty-buffer.md")
        check(miss["skipped"] and empty["skipped"] and _get_card(rconn, cx)["status"] == "pending", "retire: buffer missing or empty skipped, nothing changed")
        if WI is not None:
            WI.mark_placed_by_key(rbuf, ks[0], "task [[Run-X]]")
            WI.mark_placed_by_key(rbuf, ks[1], "calendar 10-24 10:00")
            WI.mark_placed_by_key(rbuf, ks[2], "drop")
            WI.mark_placed_by_key(rbuf, ks[4], "decision")
        rr = retire_placed(rconn, rbuf, now=day + "T11:00:00")
        x, y, z, kk = (_get_card(rconn, i) for i in (cx, cy, cz, ck))
        check(x["status"] == "superseded" and sorted(_jl(x["retired_json"], {}).get("placed_to") or []) == ["calendar 10-24 10:00", "task [[Run-X]]"]
              and _jl(x["retired_json"], {}).get("why") == "placed_elsewhere" and cx in rr["superseded"],
              "retire: all placed → superseded with placed_to")
        check(y["status"] == "pending" and y["rev"] == 2 and _jl(y["entry_keys"], []) == [ks[3]] and cy in rr["trimmed"]
              and [e["key"] for e in _jl(y["ev"], [])] == [ks[3]], "retire: partial pending trimmed rev+1")
        check(z["status"] == "ok" and z["rev"] == 1 and _jl(z["placed_json"], {}).get("keys") == [ks[4]] and cz in rr["placed"],
              "retire: partial ok gets placed_json, status and rev untouched")
        check(kk["status"] == "pending" and kk["rev"] == 1, "retire: a card without entry keys is never touched")
        rr2 = retire_placed(rconn, rbuf, now=day + "T11:01:00")
        check(rr2["superseded"] == [] and rr2["trimmed"] == [] and rr2["placed"] == [], "retire: a second pass changes nothing")
        check("task [[Run-X]]" in public_prop(x, day, "en")["retired_note"]
              and "1 of its entries were placed in decision" == public_prop(z, day, "en")["placed_note"], "public_prop: placed notes")

        # ---- apply_close on an ok card, expire
        apply_close(rconn, [{"id": cz, "why_text": "done in chat"}])
        z = _get_card(rconn, cz)
        check(z["status"] == "ok" and _jl(z["close_json"], {}).get("why_text") == "done in chat", "close on an ok card only notes it")
        old8 = (dt.date.today() - dt.timedelta(days=8)).isoformat()
        old6 = (dt.date.today() - dt.timedelta(days=6)).isoformat()
        for cid, d in (("e8e8e8e8e8e8e8e8", old8), ("e6e6e6e6e6e6e6e6", old6)):
            rconn.execute("INSERT INTO today_proposals(id, date, rev, status, updated_at, entry_keys, jids) VALUES (?,?,1,'pending',?,'[]','[]')",
                          (cid, d, d + "T09:00:00"))
        rconn.commit()
        ex = expire_old(rconn, day)
        check(ex == ["e8e8e8e8e8e8e8e8"] and _get_card(rconn, "e6e6e6e6e6e6e6e6")["status"] == "pending", "expire after 7 days")

        # ---- stats, digest line, api fallbacks
        yl = yesterday_line_data(conn, day)
        check(yl and yl["builds"] == 3 and yl["cards"] >= 5 and yl["ok"] == 1 and yl["read_tokens"] == 4500,
              "yesterday_line_data: builds, cards, ok and read tokens of that day")
        check(yesterday_line_data(sqlite3.connect(":memory:"), day) is None and yesterday_line_data(conn, "2000-01-01") is None,
              "yesterday_line_data: None without tables or builds")
        st = stats(conn)
        check(st["builds"].get(day) == 3 and st["read_tokens"].get(day) == 4500 and st["proposals"].get("ok") == 1, "stats: counts only")
        tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        sp = api_payload(conn, tomorrow, "en")
        ep = api_payload(sqlite3.connect(":memory:"), day, "zh")
        check(sp["stale"] and not sp["empty"] and sp["date"] == day, "api_payload: yesterday's brief marked stale")
        check(ep == {"ok": True, "date": day, "empty": True, "stale": True, "projects": [], "one": [], "short": "", "tail": []},
              "api_payload: empty answer without any brief")

        # ---- fixtures
        fx = tmp / "fixtures"
        for name, spec in _fixture_sets().items():
            r = fixture_check(write_fixture(fx / name, spec))
            check(r["ok"] and not r["mismatch"], "fixture_check: %s" % name)
        (fx / "broken").mkdir()
        (fx / "broken" / "result.txt").write_text("no json here", encoding="utf-8")
        check(fixture_check(fx / "broken")["ok"] is False, "fixture_check: a result without JSON is not ok")
        devfx = PAYLOAD.parent / "dev" / "fixtures" / "today"
        if devfx.is_dir():
            for d in sorted(devfx.iterdir()):
                if d.is_dir():
                    check(fixture_check(d)["ok"], "fixture_check (dev): %s" % d.name)

        # ---- CLI and source rules
        me = str(Path(__file__).resolve())
        (live / "config.json").write_text(json.dumps({"paths": {"home": str(tmp), "live_dir": str(live), "vault": str(V),
                                                                "doctrine": str(doctrine), "intake_buffer": str(buf)},
                                                      "intake": {"buffer": str(buf)}, "owner_label": "Sam Lee",
                                                      "bin": {"claude": "/bin/false", "node": "/bin/false", "ffmpeg": "/bin/false",
                                                              "whisperkit": "/bin/false", "lark_cli": "/bin/false", "path_env": "/usr/bin:/bin"}}),
                                          encoding="utf-8")
        rb = subprocess.run([sys.executable, me, "--config", str(live / "config.json"), "--build"], capture_output=True, text=True, timeout=120)
        check(rb.returncode == 2 and "only the daemon calls the model" in (rb.stdout + rb.stderr), "CLI: --build without --dry-run refused")
        rd = subprocess.run([sys.executable, me, "--config", str(live / "config.json"), "--build", "--dry-run"],
                            capture_output=True, text=True, timeout=120)
        check(rd.returncode == 0 and "# Today brief for Sam Lee" in rd.stdout and "candidates" in rd.stdout, "CLI: --build --dry-run prints prompt and candidates")
        src = Path(__file__).read_text(encoding="utf-8")
        check(chr(0x2014) not in src and chr(0x2013) not in src, "no em dash or en dash in this file")
        check(("import " + "wa_live") not in src and ("from " + "wa_live") not in src, "never imports the daemon module")
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)
    return check.done()


# ---------------------------------------------------------------- CLI

def _state_db(cfg):
    live = WC.live_dir(cfg) if WC is not None else Path(os.path.expanduser("~/WhatsApp-Live/live"))
    return live, live / "state.sqlite"


def _ro_conn(db):
    if Path(db).is_file():
        return sqlite3.connect("file:%s?mode=ro" % Path(db), uri=True)
    return sqlite3.connect(":memory:")


def main(argv=None):
    ap = argparse.ArgumentParser(description="the Today brief: dry run, API answer, fixture replay, selftest")
    ap.add_argument("--config", default=None, help="live/config.json (default: env WA_LIVE_CONFIG or ~/WhatsApp-Live/live/config.json)")
    ap.add_argument("--build", action="store_true", help="with --dry-run: print the prompt and the candidates")
    ap.add_argument("--dry-run", action="store_true", help="never calls a model, writes nothing")
    ap.add_argument("--api", action="store_true", help="print what GET /api/today answers")
    ap.add_argument("--day", default=None, help="YYYY-MM-DD (default today)")
    ap.add_argument("--fixture-check", metavar="DIR", help="replay a run folder through the validator")
    ap.add_argument("--vault", default=None, help="with --fixture-check: read task lists from this vault")
    ap.add_argument("--selftest", action="store_true", help="temp files only")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if a.fixture_check:
        return 0 if fixture_check(a.fixture_check, a.vault)["ok"] else 1
    if a.build and not a.dry_run:
        print("refused: only the daemon calls the model for a brief; use --build --dry-run to see the prompt", file=sys.stderr)
        return 2
    if not (a.build or a.api):
        ap.print_help()
        return 0
    if WC is None:
        print("wa_common could not be loaded", file=sys.stderr)
        return 2
    try:
        cfg = WC.load_config(a.config)
    except (FileNotFoundError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 2
    day = a.day or dt.date.today().isoformat()
    live, db = _state_db(cfg)
    conn = _ro_conn(db)
    lang = WC.owner_lang(cfg)
    if a.api:
        print(json.dumps(api_payload(conn, day, lang), ensure_ascii=False, indent=1))
        return 0
    paths = cfg.get("paths") or {}
    buffer = (cfg.get("intake") or {}).get("buffer") or paths.get("intake_buffer") or ""
    r = build(conn, cfg, day, buffer, paths.get("vault") or "", live, dry_run=True, lang=lang)
    print(r["prompt"])
    print("## candidates")
    print(json.dumps(r["candidates"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
