#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wa_digest.py · the morning digest: yesterday (00:00 to 24:00 local time) in at most 12 lines, written to
<live>/digest/<date>.md (the board's "yesterday" card and GET /api/digest read the newest one) and pushed in a short
form through the one push outlet.

Blocks, each only when it has something (the health line always):
  1 volume       messages through the gate · runs (ok, failed, unfinished; split by kind when runs.kind exists) ·
                 bursts cancelled because the owner had already replied
  2 open         conversations still open that need the owner and were judged before the day ended (by the start of
                 their last run, so a nightly stage lift that touches updated_at no longer hides them); ordered by the
                 vocabulary's priority order, newest first; at most 5, each with the first sentence of its situation
  3 stage moves  stage changed that day, previous stage from conversations.stage_prev, else from run outputs
  4 notes        person note jobs finished that day: opened (a new note), rewritten, bounced
  5 facts        the facts source's mismatch lists, titled with the source label; marked when facts.json was not
                 written on the send day; an unreadable person notes folder is said out loud
  6 health       WhatsApp database activity · facts source · nightly backup · second source pull · open intake entries ·
                 minutes the brain could not be called (brain_outages rows overlapping the day, an open row up to now);
                 a block that is not configured is left out, and a result from an earlier night says so instead of
                 passing for fresh (fix)
The acceptance question (<live>/acceptance.json {"questions": {"YYYY-MM-DD": "..."}}, keyed by the send day, which is
the digest day plus one) adds two lines outside the 12. A quiet day still gets a digest: silence must not look like a
dead daemon.

Labels follow board.lang (zh or en) and the owner's vocabulary; the intake count is wa_intake.count_open (one rule).
The push is kind "digest": delivered unless notify.mode is off, and only when digest.push is true.
Read only on state.sqlite, the WhatsApp database folder and the vault; the only file written is <live>/digest/<date>.md.

CLI
  wa_digest.py [--for YYYY-MM-DD] [--dry-run]    print the text and the data; writes and pushes nothing (the default)
  wa_digest.py [--for YYYY-MM-DD] --write        write digest/<date>.md only
  wa_digest.py [--for YYYY-MM-DD] --send         write, then push the short form
  wa_digest.py --selftest                        fake state database, fake files, a fake push command
  --config P                                     live/config.json (default: env WA_LIVE_CONFIG or ~/WhatsApp-Live/live/config.json)
"""

import argparse
import datetime as dt
import html
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wa_common as W  # noqa: E402

MAX_LINES = 12
MAX_OPEN = 5
SNIP = 40
OPEN_REASON_PREFIXES = ("开新档", "事实表", "new note", "open note", "open a note", "facts:")

TXT = {
    "zh": {
        "header": "📋 WhatsApp Live 日报 · %s", "quiet": "昨天安静, daemon 活着 (库 %s 小时前有动)",
        "volume": "昨天 %d 封进闸 · %d 个 run (成功 %d / 失败 %s) · 省了 %d 个", "unfinished": " 含没收尾 %d",
        "open_head": "还挂着要回 (%d%s): ", "listed": ", 列 %d", "no_situation": "(没写状况)",
        "moves_head": "阶段动了 (%d%s): ", "notes": "人物档: 开档 %d · 重写 %d · 退回 %d",
        "facts_title": "%s 对不上", "facts_stale": " (事实表停在 %s)", "facts_never": "%s 对不上: 事实表还没跑过",
        "facts_only_stale": "%s: 事实表停在 %s, 今天没更新", "facts_noidx": "人物档房读不到, 没排开档",
        "seg_paid_no_chat": "付了钱没对话", "seg_unparsable": "电话解析不了", "seg_queued": "排队开档",
        "seg_silent": "另 %d 人有对话但 %d 天没话, 没开 (%s)",
        "health": "健康: ", "wal": "WhatsApp 库 %s 小时前有动", "wal_missing": "WhatsApp 库读不到 (WAL 不在)",
        "state_missing": "状态库不在", "src_ok": "%s ✓ (%s)", "src_bad": "%s ✗ (%s)", "src_never": "%s 还没跑过",
        "stale_at": "停在 %s", "backup": "夜备", "backup_never": "夜备还没跑过", "backup_ok": "夜备 ✓ (%s, %.1f MB)",
        "backup_bad": "夜备 ✗ (%s, %s)", "no_reason": "没写原因", "pull": "%s 拉", "pull_never": "%s 拉还没跑过",
        "pull_ok": "%s 拉 ✓ (%s, %s 会话 %s 封)", "pull_bad": "%s 拉 ✗ (%s)", "pull_stale": "最后一次拉的是 %s, 不是 %s",
        "intake": "intake 里 %d 条 open", "intake_missing": "intake buffer 不在", "seed_fallback": "⚠️ 主人副本不在, 正用 payload 种子: %s", "intake_unset": "intake.buffer 没设",
        "acceptance": "🧪 今天验收 (%s):", "brain_down": "脑叫不动 %d 分钟 (%s)", "s_brain_down": "脑停 %d 分",
        "kinds": {"triage": "分诊", "dossier": "人物档", "second_source": "第二来源", "backfill": "回填", "intake_evening": "intake 晚间"},
        "s_quiet": "昨天安静, daemon 活着", "s_volume": "%d 封 · %d run%s · 省 %d", "s_fail": " (败 %d)",
        "s_open": "要回 %d", "s_moves": "阶段动 %d", "s_more": "+%d 个, 板上看", "s_notes": "人物档", "s_notes_line": "开 %d · 重写 %d%s",
        "s_bounced": " · 退 %d", "s_facts": "%s 对不上", "s_facts_line": "没对话 %d · 电话坏 %d · 排开档 %d%s", "s_silent": " (另 %d 没话)",
        "s_health": "健康", "s_db": "库 %sh", "s_db_missing": "库 ?", "s_never": "未跑", "s_stale": "旧", "s_intake_missing": "未建",
        "s_accept": "🧪 今天验收", "s_footer_board": "全文在板上「昨天」格", "s_footer_file": "全文在 digest/%s.md",
    },
    "en": {
        "header": "📋 WhatsApp Live digest · %s", "quiet": "Quiet yesterday, daemon alive (database changed %s hours ago)",
        "volume": "Yesterday %d messages through the gate · %d runs (ok %d / failed %s) · %d saved", "unfinished": ", %d unfinished",
        "open_head": "Still waiting on you (%d%s): ", "listed": ", showing %d", "no_situation": "(no situation written)",
        "moves_head": "Stage moves (%d%s): ", "notes": "Person notes: opened %d · rewritten %d · bounced %d",
        "facts_title": "%s mismatches", "facts_stale": " (facts stopped at %s)", "facts_never": "%s mismatches: the facts job has not run yet",
        "facts_only_stale": "%s: facts stopped at %s, not refreshed today", "facts_noidx": "person notes unreadable, nobody queued",
        "seg_paid_no_chat": "paid, no chat", "seg_unparsable": "phone unparsable", "seg_queued": "queued for a note",
        "seg_silent": "plus %d with a chat but silent %d days, not opened (%s)",
        "health": "Health: ", "wal": "WhatsApp database changed %s hours ago", "wal_missing": "WhatsApp database unreadable (no WAL file)",
        "state_missing": "state database missing", "src_ok": "%s ✓ (%s)", "src_bad": "%s ✗ (%s)", "src_never": "%s has not run yet",
        "stale_at": "stopped at %s", "backup": "backup", "backup_never": "backup has not run yet", "backup_ok": "backup ✓ (%s, %.1f MB)",
        "backup_bad": "backup ✗ (%s, %s)", "no_reason": "no reason given", "pull": "%s pull", "pull_never": "%s pull has not run yet",
        "pull_ok": "%s pull ✓ (%s, %s chats %s messages)", "pull_bad": "%s pull ✗ (%s)", "pull_stale": "last pull was %s, not %s",
        "intake": "intake has %d open", "intake_missing": "intake buffer missing", "seed_fallback": "⚠️ owner copy missing, running on the payload seed: %s", "intake_unset": "intake.buffer not set",
        "acceptance": "🧪 Today's check (%s):", "brain_down": "brain was down %d minutes (%s)", "s_brain_down": "brain down %dm",
        "kinds": {"triage": "triage", "dossier": "notes", "second_source": "second source", "backfill": "backfill", "intake_evening": "intake evening"},
        "s_quiet": "Quiet yesterday, daemon alive", "s_volume": "%d msgs · %d runs%s · %d saved", "s_fail": " (%d failed)",
        "s_open": "Waiting on you %d", "s_moves": "Stage moves %d", "s_more": "+%d more on the board", "s_notes": "Person notes",
        "s_notes_line": "opened %d · rewritten %d%s", "s_bounced": " · bounced %d", "s_facts": "%s mismatches",
        "s_facts_line": "no chat %d · bad phone %d · queued %d%s", "s_silent": " (plus %d silent)",
        "s_health": "Health", "s_db": "db %sh", "s_db_missing": "db ?", "s_never": "not run", "s_stale": "stale", "s_intake_missing": "missing",
        "s_accept": "🧪 Today's check", "s_footer_board": "Full text in the board's yesterday card", "s_footer_file": "Full text in digest/%s.md",
    },
}


# ---------------------------------------------------------------- small helpers

def _ro(path):
    c = sqlite3.connect("file:%s?mode=ro" % urllib.parse.quote(str(path)), uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    return c


def _bounds(day):
    """Local ISO strings, compared directly with the state database's local timestamps."""
    return day.isoformat() + "T00:00:00", (day + dt.timedelta(days=1)).isoformat() + "T00:00:00"


def first_sentence(text, limit=SNIP):
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if not s:
        return ""
    m = re.match(r"^(.+?[。！？!?；;])", s)
    s = (m.group(1) if m else s).rstrip("。！？!?；;")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def short(text, limit=SNIP):
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _has_table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _has_col(conn, table, col):
    try:
        return any(row[1] == col for row in conn.execute("PRAGMA table_info(%s)" % table))
    except sqlite3.Error:
        return False


def _date_of(iso):
    try:
        return dt.date.fromisoformat(str(iso or "")[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------- blocks (all read only)

def _inbound(conn, lo, hi):
    out = {"gate": 0, "saved": 0, "by_decision": {}}
    if not _has_table(conn, "inbound_log"):
        return out
    for r in conn.execute("SELECT decision, COUNT(*) AS n FROM inbound_log WHERE seen_at >= ? AND seen_at < ? GROUP BY decision", (lo, hi)):
        d, n = str(r["decision"] or ""), int(r["n"])
        out["by_decision"][d] = n
        if d.startswith("pass:"):
            out["gate"] += n
        if d == "outbound:replied:burst_cancelled":
            out["saved"] += n
    return out


def _runs(conn, lo, hi):
    out = {"total": 0, "ok": 0, "failed": 0, "unfinished": 0, "by_kind": {}}
    if not _has_table(conn, "runs"):
        return out
    kind_col = "kind" if _has_col(conn, "runs", "kind") else "NULL"
    for r in conn.execute("SELECT ok, %s AS kind, COUNT(*) AS n FROM runs WHERE started_at >= ? AND started_at < ? GROUP BY ok, %s"
                          % (kind_col, kind_col), (lo, hi)):
        n = int(r["n"])
        out["total"] += n
        if r["ok"] == 1:
            out["ok"] += n
        elif r["ok"] is None:
            out["unfinished"] += n
        k = str(r["kind"] or "triage")
        out["by_kind"][k] = out["by_kind"].get(k, 0) + n
    out["failed"] = out["total"] - out["ok"]
    return out


def _open_needs_me(conn, hi, cfg):
    out = {"total": 0, "rows": []}
    if not _has_table(conn, "conversations"):
        return out
    join = _has_table(conn, "runs") and _has_col(conn, "conversations", "last_run_id")
    sql = ("SELECT c.name, c.jid, c.priority, c.situation, c.updated_at, %s AS judged_at FROM conversations c %s "
           "WHERE c.tag='open' AND c.needs_me=1 AND COALESCE(%s, c.updated_at, '') < ?"
           % ("r.started_at" if join else "NULL", "LEFT JOIN runs r ON r.id = c.last_run_id" if join else "",
              "r.started_at" if join else "NULL"))
    rows = []
    for r in conn.execute(sql, (hi,)):
        rows.append({"name": r["name"] or r["jid"], "jid": r["jid"], "priority": r["priority"] or "",
                     "situation": first_sentence(r["situation"]), "at": r["judged_at"] or r["updated_at"] or ""})
    rank = dict((k, i) for i, k in enumerate(W.vocab(cfg)["priority_keys"]))
    rows.sort(key=lambda x: x["at"], reverse=True)
    rows.sort(key=lambda x: rank.get(x["priority"], 99))
    out["total"], out["rows"] = len(rows), rows
    return out


def _prev_stage(conn, jid, changed_at, cur):
    """Previous stage from run outputs: of the two newest successful runs before the change, the one before the run
    that wrote the current stage."""
    try:
        rs = conn.execute("SELECT output FROM runs WHERE jid=? AND ok=1 AND started_at < ? ORDER BY id DESC LIMIT 2",
                          (jid, changed_at)).fetchall()
    except sqlite3.Error:
        return None
    stages = []
    for r in rs:
        try:
            stages.append((json.loads(r["output"] or "{}") or {}).get("stage"))
        except (ValueError, AttributeError):
            stages.append(None)
    if not stages:
        return None
    if stages[0] == cur:
        return stages[1] if len(stages) > 1 else None
    return stages[0]


def _stage_moves(conn, lo, hi):
    out = []
    if not (_has_table(conn, "conversations") and _has_col(conn, "conversations", "stage_changed_at")):
        return out
    has_prev = _has_col(conn, "conversations", "stage_prev")
    cols = "jid, name, stage, reason, stage_changed_at" + (", stage_auto" if _has_col(conn, "conversations", "stage_auto") else "") \
        + (", stage_prev" if has_prev else "")
    for r in conn.execute("SELECT %s FROM conversations WHERE stage_changed_at >= ? AND stage_changed_at < ? ORDER BY stage_changed_at" % cols, (lo, hi)):
        if not r["stage"]:
            continue
        cur = r["stage"]
        prev = (r["stage_prev"] or None) if has_prev else None
        if not prev and _has_table(conn, "runs"):
            prev = _prev_stage(conn, r["jid"], r["stage_changed_at"], cur)
        if prev == cur:
            prev = None
        out.append({"name": r["name"] or r["jid"], "jid": r["jid"], "from": prev, "to": cur,
                    "auto": bool(r["stage_auto"]) if "stage_auto" in r.keys() else None,
                    "reason": short(r["reason"]), "at": r["stage_changed_at"]})
    return out


def _dossier(conn, lo, hi):
    """Opened = a successful job that created a note: the was_new column when the daemon records it, else a reason
    starting with one of OPEN_REASON_PREFIXES (jobs the facts worker queues count as opens, not rewrites)."""
    out = {"opened": 0, "rewritten": 0, "bounced": 0, "total": 0, "names": {"opened": [], "rewritten": [], "bounced": []}}
    if not _has_table(conn, "dossier_jobs"):
        return out
    new_col = "was_new" if _has_col(conn, "dossier_jobs", "was_new") else "NULL"
    for r in conn.execute("SELECT name, jid, reason, ok, %s AS was_new FROM dossier_jobs WHERE finished_at >= ? AND finished_at < ? ORDER BY id"
                          % new_col, (lo, hi)):
        nm = r["name"] or r["jid"]
        if r["ok"] == 1:
            reason = str(r["reason"] or "").strip().lower()
            opened = bool(r["was_new"]) if r["was_new"] is not None else reason.startswith(OPEN_REASON_PREFIXES)
            key = "opened" if opened else "rewritten"
        else:
            key = "bounced"
        out[key] += 1
        out["names"][key].append(nm)
    out["total"] = out["opened"] + out["rewritten"] + out["bounced"]
    return out


def _names(rows):
    out = []
    for x in rows or []:
        out.append(str(x.get("name") or x.get("raw") or x.get("phone") or "?") if isinstance(x, dict) else str(x))
    return out


def _facts(cfg, day):
    fc = cfg.get("facts") or {}
    on = bool(fc.get("enabled")) and str(fc.get("source") or "none").lower() != "none"
    label = W.source_label(cfg, "facts") or "facts"
    out = {"enabled": on, "label": label, "exists": False, "stale": False, "written": None, "people_index_error": None,
           "paid_no_chat": [], "unparsable_phone": [], "paid_chat_no_note_queued": [], "paid_chat_stale_no_note": [],
           "recent_days": int(fc.get("recent_days") or 30)}
    if not on:
        return out
    j = W.read_json(W.live_dir(cfg) / "facts.json", None)
    if not isinstance(j, dict):
        return out
    mm = j.get("mismatch") or {}
    wd = _date_of(j.get("written_at"))
    out.update(exists=True, written=str(j.get("written_at") or ""), stale=(wd is None or wd < day + dt.timedelta(days=1)),
               people_index_error=j.get("people_index_error"),
               paid_no_chat=_names(mm.get("paid_no_chat")), unparsable_phone=_names(mm.get("unparsable_phone")),
               paid_chat_no_note_queued=_names(mm.get("paid_chat_no_note_queued")),
               paid_chat_stale_no_note=_names(mm.get("paid_chat_stale_no_note")))
    return out


def _health(cfg, day, state_exists=True):
    live = W.live_dir(cfg)
    send_day = day + dt.timedelta(days=1)
    h = {"state_db": bool(state_exists)}
    wal = (cfg.get("doorbell") or {}).get("wal_path") or str(W.wa_root() / "ChatStorage.sqlite-wal")
    try:
        h["wal_age_hours"] = round((dt.datetime.now().timestamp() - os.stat(wal).st_mtime) / 3600.0, 1)
    except OSError:
        h["wal_age_hours"] = None
    fc = cfg.get("facts") or {}
    h["facts_source"] = None
    if fc.get("enabled") and str(fc.get("source") or "none").lower() != "none":
        try:
            import wa_facts
            got = wa_facts.source_health(cfg)
        except Exception as e:
            got = (False, str(e)[:120])
        h["facts_source"] = {"ok": got[0], "why": got[1]} if got else None
    b = cfg.get("backup") or {}
    h["backup"] = None
    if b.get("enabled"):
        rec = W.read_json(live / "backup-last.json", None)
        if isinstance(rec, dict):
            ad = _date_of(rec.get("at"))
            h["backup"] = dict(rec, stale=(ad is None or ad < send_day))
        else:
            h["backup"] = {"never": True}
    ss = cfg.get("second_source") or {}
    h["second_source"] = None
    if ss.get("enabled") and str(ss.get("source") or "none").lower() != "none":
        rec = W.read_json(live / "second-source-last.json", None)
        if isinstance(rec, dict):
            h["second_source"] = dict(rec, stale=(str(rec.get("day") or "") != day.isoformat()))
        else:
            h["second_source"] = {"never": True}
    h["seed_fallback"] = []
    if state_exists:
        # the daemon records meta seed_fallback:<what> while an owner copy (prompt, playbook, topic map) is missing
        try:
            c = _ro(live / "state.sqlite")
            try:
                h["seed_fallback"] = sorted(r[0].split(":", 1)[1] for r in c.execute("SELECT key FROM meta WHERE key LIKE 'seed_fallback:%'"))
            finally:
                c.close()
        except Exception:
            h["seed_fallback"] = []
    it = cfg.get("intake") or {}
    h["intake"] = None
    if it.get("enabled"):
        buf = str(it.get("buffer") or "")
        if not buf:
            h["intake"] = {"state": "unset"}
        elif not Path(buf).is_file():
            h["intake"] = {"state": "missing"}
        else:
            try:
                import wa_intake
                h["intake"] = {"state": "ok", "open": int(wa_intake.count_open(buf))}
            except Exception as e:
                h["intake"] = {"state": "error", "why": str(e)[:120]}
    return h


def _brain_outages(conn, lo, hi, now=None):
    """Minutes the brain could not be called during the day: brain_outages rows overlapping [lo, hi), an open row counting
    up to now. Returns {"minutes", "kinds"} or None when zero."""
    if not _has_table(conn, "brain_outages"):
        return None
    now = now or dt.datetime.now()
    a, b = dt.datetime.fromisoformat(lo), dt.datetime.fromisoformat(hi)
    seconds, kinds = 0.0, []
    for r in conn.execute("SELECT kind, started_at, ended_at FROM brain_outages WHERE started_at < ? AND (ended_at IS NULL OR ended_at > ?)",
                          (hi, lo)):
        try:
            st = dt.datetime.fromisoformat(str(r["started_at"])[:19])
            en = dt.datetime.fromisoformat(str(r["ended_at"])[:19]) if r["ended_at"] else now
        except ValueError:
            continue
        span = (min(en, b) - max(st, a)).total_seconds()
        if span <= 0:
            continue
        seconds += span
        k = str(r["kind"] or "?")
        if k not in kinds:
            kinds.append(k)
    if seconds <= 0:
        return None
    return {"minutes": max(1, int(round(seconds / 60.0))), "kinds": kinds}


# ---------------------------------------------------------------- lines

def _fmt_health(h, facts, cfg, day, lang):
    t = TXT[lang]
    parts = []
    if not h.get("state_db"):
        parts.append(t["state_missing"])
    parts.append(t["wal"] % h["wal_age_hours"] if h.get("wal_age_hours") is not None else t["wal_missing"])
    if facts.get("enabled"):
        fs = h.get("facts_source")
        if fs and not fs.get("ok"):
            parts.append(t["src_bad"] % (facts["label"], short(fs.get("why") or t["no_reason"], 30)))
        elif not facts.get("exists"):
            parts.append(t["src_never"] % facts["label"])
        elif facts.get("stale"):
            parts.append(t["src_bad"] % (facts["label"], t["stale_at"] % (facts.get("written") or "?")[:10]))
        else:
            parts.append(t["src_ok"] % (facts["label"], (facts.get("written") or "")[11:16] or "?"))
    b = h.get("backup")
    if b is not None:
        if b.get("never"):
            parts.append(t["backup_never"])
        elif b.get("ok") and not b.get("stale"):
            parts.append(t["backup_ok"] % (str(b.get("at") or "")[11:16] or "?", (b.get("bytes") or 0) / 1e6))
        elif b.get("ok"):
            parts.append(t["src_bad"] % (t["backup"], t["stale_at"] % str(b.get("at") or "?")[:10]))
        else:
            parts.append(t["backup_bad"] % (str(b.get("at") or "")[11:16] or "?", short(b.get("error") or t["no_reason"], 30)))
    s = h.get("second_source")
    if s is not None:
        label = W.source_label(cfg, "second_source")
        if s.get("never"):
            parts.append(t["pull_never"] % label)
        elif s.get("stale"):
            parts.append(t["pull_bad"] % (label, t["pull_stale"] % (s.get("day") or "?", day.isoformat())))
        elif s.get("ok"):
            parts.append(t["pull_ok"] % (label, s.get("day") or "?", s.get("n_chats", 0), s.get("n_msgs", 0)))
        else:
            parts.append(t["pull_bad"] % (label, short("; ".join(str(x) for x in (s.get("errors") or [])) or t["no_reason"], 30)))
    if h.get("seed_fallback"):
        parts.append(t["seed_fallback"] % ", ".join(h["seed_fallback"]))
    i = h.get("intake")
    if i is not None:
        if i["state"] == "ok":
            parts.append(t["intake"] % i["open"])
        elif i["state"] == "unset":
            parts.append(t["intake_unset"])
        elif i["state"] == "missing":
            parts.append(t["intake_missing"])
        else:
            parts.append("intake ✗ (%s)" % short(i.get("why"), 30))
    bd = h.get("brain_down")
    if bd:
        parts.append(t["brain_down"] % (bd["minutes"], "|".join(bd.get("kinds") or ["?"])))
    return t["health"] + " · ".join(parts)


def _block_open(rows, keep, lang):
    if not rows:
        return [], 0
    t = TXT[lang]
    keep = max(1, min(keep, len(rows)))
    dropped = len(rows) - keep
    head = t["open_head"] % (len(rows), t["listed"] % keep if dropped else "")
    items = ["%s · %s" % (r["name"], r["situation"] or t["no_situation"]) for r in rows[:keep]]
    return [head + items[0]] + ["· " + x for x in items[1:]], dropped


def _block_moves(moves, keep, cfg, lang):
    if not moves:
        return [], 0
    t = TXT[lang]
    keep = max(1, min(keep, len(moves)))
    dropped = len(moves) - keep
    head = t["moves_head"] % (len(moves), t["listed"] % keep if dropped else "")
    items = []
    for m in moves[:keep]:
        to = W.vocab_label(cfg, "stage", m["to"], lang)
        arrow = ("%s → %s" % (W.vocab_label(cfg, "stage", m["from"], lang), to)) if m["from"] else ("→ %s" % to)
        items.append("%s %s%s" % (m["name"], arrow, (" (%s)" % m["reason"]) if m["reason"] else ""))
    return [head + items[0]] + ["· " + x for x in items[1:]], dropped


def _block_facts(facts, lang):
    t = TXT[lang]
    if not facts.get("enabled"):
        return []
    if not facts.get("exists"):
        return [t["facts_never"] % facts["label"]]
    n = len(facts["paid_no_chat"]) + len(facts["unparsable_phone"]) + len(facts["paid_chat_no_note_queued"])
    stale = (t["facts_stale"] % (facts.get("written") or "?")[:10]) if facts.get("stale") else ""
    tail = []
    if facts.get("paid_chat_stale_no_note"):
        silent = facts["paid_chat_stale_no_note"]
        tail.append(t["seg_silent"] % (len(silent), facts.get("recent_days") or 30, " · ".join(silent)))
    if facts.get("people_index_error"):
        tail.append(t["facts_noidx"])
    if not n and not tail:
        return [t["facts_only_stale"] % (facts["label"], (facts.get("written") or "?")[:10])] if stale else []

    def seg(label, names):
        return "%s (%d%s)" % (label, len(names), (": " + " · ".join(names)) if names else "")
    segs = [seg(t["seg_paid_no_chat"], facts["paid_no_chat"]), seg(t["seg_unparsable"], facts["unparsable_phone"]),
            seg(t["seg_queued"], facts["paid_chat_no_note_queued"])] + tail
    return [(t["facts_title"] % facts["label"]) + stale + ": " + " · ".join(segs)]


def _acceptance_block(cfg, day, lang="zh"):
    try:
        q = (W.read_json(W.live_dir(cfg) / "acceptance.json", {}) or {}).get("questions") or {}
    except AttributeError:
        return []
    send_day = (day + dt.timedelta(days=1)).isoformat()
    text = q.get(send_day) if isinstance(q, dict) else None
    return [TXT[lang]["acceptance"] % send_day, str(text).strip()] if text else []


def build_digest(cfg, day):
    """{"date", "text", "lines", "empty", "data", "text_short"} for `day` (a date or "YYYY-MM-DD")."""
    if isinstance(day, str):
        day = dt.date.fromisoformat(day)
    lang = W.owner_lang(cfg)
    t = TXT[lang]
    lo, hi = _bounds(day)
    state = Path(str(cfg.get("state_db") or (W.live_dir(cfg) / "state.sqlite")))
    inbound, runs = {"gate": 0, "saved": 0, "by_decision": {}}, {"total": 0, "ok": 0, "failed": 0, "unfinished": 0, "by_kind": {}}
    open_, moves, dossier = {"total": 0, "rows": []}, [], {"opened": 0, "rewritten": 0, "bounced": 0, "total": 0, "names": {}}
    outages = None
    if state.is_file():
        conn = _ro(state)
        try:
            inbound = _inbound(conn, lo, hi)
            runs = _runs(conn, lo, hi)
            open_ = _open_needs_me(conn, hi, cfg)
            moves = _stage_moves(conn, lo, hi)
            dossier = _dossier(conn, lo, hi)
            outages = _brain_outages(conn, lo, hi)
        finally:
            conn.close()
    facts = _facts(cfg, day)
    health = _health(cfg, day, state.is_file())
    health["brain_down"] = outages
    empty = (inbound["gate"] == 0 and runs["total"] == 0 and open_["total"] == 0 and not moves and dossier["total"] == 0)
    date_s = day.isoformat()
    header = t["header"] % date_s
    health_line = _fmt_health(health, facts, cfg, day, lang)
    b5 = _block_facts(facts, lang)
    data = {"date": date_s, "lang": lang, "inbound": inbound, "runs": runs, "open": open_, "stage_moves": moves,
            "dossier": dossier, "facts": facts, "health": health}
    accept = _acceptance_block(cfg, day, lang)
    if empty:
        wal_s = health["wal_age_hours"] if health["wal_age_hours"] is not None else "?"
        lines = [header, t["quiet"] % wal_s] + b5 + [health_line] + accept
        out = {"date": date_s, "text": "\n".join(lines), "lines": lines, "empty": True, "data": data}
        out["text_short"] = compact_text(out, cfg, day)
        return out

    b1 = []
    if inbound["gate"] or runs["total"] or inbound["saved"]:
        fail = "%d" % runs["failed"] + (t["unfinished"] % runs["unfinished"] if runs["unfinished"] else "")
        line = t["volume"] % (inbound["gate"], runs["total"], runs["ok"], fail, inbound["saved"])
        kinds = runs.get("by_kind") or {}
        if set(kinds) - {"triage"}:
            line += "; " + " · ".join("%s %d" % (t["kinds"].get(k, k), n) for k, n in sorted(kinds.items(), key=lambda kv: -kv[1]))
        b1 = [line]
    b4 = [t["notes"] % (dossier["opened"], dossier["rewritten"], dossier["bounced"])] if dossier["total"] else []
    fixed = 1 + len(b1) + len(b4) + len(b5) + 1
    keep2, keep3 = min(MAX_OPEN, open_["total"]), len(moves)

    def total_lines():
        return fixed + keep2 + keep3
    while total_lines() > MAX_LINES and keep2 > 2:
        keep2 -= 1
    while total_lines() > MAX_LINES and keep3 > 1:
        keep3 -= 1
    while total_lines() > MAX_LINES and keep2 > 1:
        keep2 -= 1
    b2, drop2 = _block_open(open_["rows"], keep2, lang)
    b3, drop3 = _block_moves(moves, keep3, cfg, lang)
    lines = [header] + b1 + b2 + b3 + b4 + b5 + [health_line] + accept
    data["trimmed"] = {"open_dropped": drop2, "moves_dropped": drop3}
    out = {"date": date_s, "text": "\n".join(lines), "lines": lines, "empty": False, "data": data}
    out["text_short"] = compact_text(out, cfg, day)
    return out


def compact_text(d, cfg=None, day=None):
    """The pushed short form: blocks with bold titles, a blank line between blocks, names and numbers only
    (Telegram HTML: <b> and <i>, content escaped). The full text stays in digest/<date>.md and on the board."""
    E = html.escape
    x = d.get("data") or {}
    lang = x.get("lang") or (W.owner_lang(cfg) if cfg else "zh")
    t = TXT[lang]
    inb, runs, op, mv = x.get("inbound") or {}, x.get("runs") or {}, x.get("open") or {}, x.get("stage_moves") or []
    do, fa, h = x.get("dossier") or {}, x.get("facts") or {}, x.get("health") or {}
    date_s = d.get("date") or ""
    mm_dd = date_s[5:].replace("-", "/") if len(date_s) >= 10 else date_s
    blocks = []
    if d.get("empty"):
        blocks.append("<b>📋 WhatsApp Live · %s</b>\n%s" % (E(mm_dd), E(t["s_quiet"])))
    else:
        fail = runs.get("failed") or 0
        blocks.append("<b>📋 WhatsApp Live · %s</b>\n%s" % (E(mm_dd), E(t["s_volume"] % (
            inb.get("gate") or 0, runs.get("total") or 0, (t["s_fail"] % fail) if fail else " ✓", inb.get("saved") or 0))))
        rows = op.get("rows") or []
        if rows:
            names = [E(r.get("name") or "?") for r in rows[:4]]
            blocks.append("<b>%s</b>\n%s%s" % (E(t["s_open"] % len(rows)), " · ".join(names), " …" if len(rows) > 4 else ""))
        if mv:
            arrows = ["%s → %s" % (E(m.get("name") or "?"), E(W.vocab_label(cfg or {}, "stage", m.get("to"), lang) or "?")) for m in mv[:4]]
            more = ("\n" + E(t["s_more"] % (len(mv) - 4))) if len(mv) > 4 else ""
            blocks.append("<b>%s</b>\n%s%s" % (E(t["s_moves"] % len(mv)), "\n".join(arrows), more))
        if do.get("total"):
            blocks.append("<b>%s</b>\n%s" % (E(t["s_notes"]), E(t["s_notes_line"] % (
                do.get("opened") or 0, do.get("rewritten") or 0, (t["s_bounced"] % do["bounced"]) if do.get("bounced") else ""))))
    if fa.get("enabled") and fa.get("exists"):
        n1, n2, n3 = len(fa.get("paid_no_chat") or []), len(fa.get("unparsable_phone") or []), len(fa.get("paid_chat_no_note_queued") or [])
        n4 = len(fa.get("paid_chat_stale_no_note") or [])
        if n1 or n2 or n3:
            title = t["s_facts"] % fa.get("label") + ((" (%s)" % t["s_stale"]) if fa.get("stale") else "")
            blocks.append("<b>%s</b>\n%s" % (E(title), E(t["s_facts_line"] % (n1, n2, n3, (t["s_silent"] % n4) if n4 else ""))))
    hp = [t["s_db"] % h.get("wal_age_hours") if h.get("wal_age_hours") is not None else t["s_db_missing"]]
    if fa.get("enabled"):
        fs = h.get("facts_source") or {}
        mark = "✗" if (fs and fs.get("ok") is False) or not fa.get("exists") or fa.get("stale") else "✓"
        hp.append("%s %s" % (fa.get("label"), mark if fa.get("exists") else t["s_never"]))
    b = h.get("backup")
    if b is not None:
        hp.append("%s %s" % (t["backup"], t["s_never"] if b.get("never") else ("✓" if b.get("ok") and not b.get("stale") else "✗")))
    s = h.get("second_source")
    if s is not None:
        label = W.source_label(cfg or {}, "second_source") if cfg else "second source"
        hp.append("%s %s" % (t["pull"] % label, t["s_never"] if s.get("never") else ("✓" if s.get("ok") and not s.get("stale") else "✗")))
    i = h.get("intake")
    if i is not None:
        hp.append("intake %s" % (i.get("open") if i.get("state") == "ok" else t["s_intake_missing"]))
    if h.get("brain_down"):
        hp.append(t["s_brain_down"] % h["brain_down"]["minutes"])
    blocks.append("<b>%s</b>\n%s" % (E(t["s_health"]), E(" · ".join(str(p) for p in hp))))
    q = _acceptance_block(cfg, day, lang) if (cfg is not None and day is not None) else []
    if q:
        blocks.append("<b>%s</b>\n%s" % (E(t["s_accept"]), E(q[1])))
    board_on = bool(((cfg or {}).get("board") or {}).get("enabled", True))
    blocks.append("<i>%s</i>" % E(t["s_footer_board"] if board_on else (t["s_footer_file"] % date_s)))
    return "\n\n".join(blocks)


# ---------------------------------------------------------------- write, latest, send

def digest_dir(cfg):
    return W.live_dir(cfg) / "digest"


def write_digest(cfg, d):
    """Write <live>/digest/<date>.md (the long text) atomically, 0600. Returns the path."""
    dd = digest_dir(cfg)
    dd.mkdir(parents=True, exist_ok=True)
    target = dd / ("%s.md" % d["date"])
    fd, tmp = tempfile.mkstemp(prefix=target.name + ".", dir=str(dd))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(d["text"].rstrip("\n") + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, str(target))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target


def latest_digest(cfg):
    """The newest digest/YYYY-MM-DD.md as {"date", "text", "lines"}, or None (GET /api/digest)."""
    dd = digest_dir(cfg)
    if not dd.is_dir():
        return None
    files = sorted(p for p in dd.glob("????-??-??.md") if p.is_file())
    if not files:
        return None
    try:
        text = files[-1].read_text(encoding="utf-8").rstrip("\n")
    except OSError:
        return None
    return {"date": files[-1].stem, "text": text, "lines": text.splitlines()}


def send_digest(cfg, d):
    """Write the file, then push the short form through the one outlet. Returns (ok, why)."""
    write_digest(cfg, d)
    if not (cfg.get("digest") or {}).get("push", True):
        return False, "digest.push is false (written, not pushed)"
    if not W.notify_wanted(cfg, "digest"):
        return False, "notify.mode is off (written, not pushed)"
    if d.get("text_short"):
        return W.notify_deliver(cfg, d["text_short"], parse_mode="HTML")
    return W.notify_deliver(cfg, d["text"])


# ---------------------------------------------------------------- selftest

def _selftest():
    check = W._Checks("wa_digest")
    tmp = Path(tempfile.mkdtemp(prefix="wa_digest_selftest."))
    try:
        live = tmp / "home" / "live"
        live.mkdir(parents=True)
        wal = tmp / "wa" / "ChatStorage.sqlite-wal"
        wal.parent.mkdir()
        wal.write_bytes(b"")
        two_h = dt.datetime.now().timestamp() - 2 * 3600
        os.utime(str(wal), (two_h, two_h))
        csv_file = tmp / "sales.csv"
        csv_file.write_text("Phone\n+60 12-000 0001\n", encoding="utf-8")
        buf = tmp / "vault" / "intake-buffer.md"
        buf.parent.mkdir()
        buf.write_text("### 2026-09-13 · [wa] · Alex Rivera (无档) · x\n- 状态: open\n\n### 2026-09-13 · [wa] · Blake Chen (no note) · y\n"
                       "- status: open\n\n### 2026-09-12 · [wa] · Casey Ng (无档) · z\n- 状态 :open\n\n## 已归\n\n"
                       "### 2026-09-10 · [wa] · Dana Park (无档) · w\n- 状态: 已归 → drop\n", encoding="utf-8")
        pushed = tmp / "pushed.txt"
        raw = {"board": {"lang": "zh"}, "doorbell": {"wal_path": str(wal)},
               "facts": {"enabled": True, "source": "csv", "label": "Sales sheet", "csv": {"path": str(csv_file), "columns": {"phone": "Phone"}}},
               "backup": {"enabled": True, "dest": str(tmp / "backups")},
               "second_source": {"enabled": True, "source": "lark"},
               "intake": {"enabled": True, "buffer": str(buf)},
               "notify": {"outlet": "command", "mode": "errors", "command": "cat > %s" % shlex.quote(str(pushed))}}
        (live / "config.json").write_text(json.dumps(raw), encoding="utf-8")
        cfg = W.load_config(live / "config.json")
        day = dt.date(2026, 9, 13)
        D, N = "2026-09-13", "2026-09-14"
        c = sqlite3.connect(str(live / "state.sqlite"))
        c.executescript("""
            CREATE TABLE inbound_log(pk INTEGER PRIMARY KEY, seen_at TEXT, decision TEXT);
            CREATE TABLE runs(id INTEGER PRIMARY KEY, started_at TEXT, jid TEXT, ok INTEGER, output TEXT, kind TEXT);
            CREATE TABLE conversations(jid TEXT PRIMARY KEY, name TEXT, tag TEXT, needs_me INTEGER, priority TEXT, situation TEXT,
                updated_at TEXT, last_run_id INTEGER, stage TEXT, stage_auto INTEGER, reason TEXT, stage_changed_at TEXT, stage_prev TEXT);
            CREATE TABLE dossier_jobs(id INTEGER PRIMARY KEY, jid TEXT, name TEXT, reason TEXT, ok INTEGER, finished_at TEXT);
            CREATE TABLE brain_outages(id INTEGER PRIMARY KEY, kind TEXT, started_at TEXT, ended_at TEXT, detail TEXT);
        """)
        c.executemany("INSERT INTO brain_outages(kind, started_at, ended_at, detail) VALUES (?, ?, ?, ?)", [
            ("limit", D + "T10:00:00", D + "T10:45:00", "usage limit reached"), ("auth", "2026-09-12T23:50:00", D + "T00:05:00", "not logged in"),
            ("auth", "2026-09-10T08:00:00", "2026-09-10T09:00:00", "not logged in")])
        c.executemany("INSERT INTO inbound_log(seen_at, decision) VALUES (?, ?)",
                      [(D + "T09:00:00", "pass:all")] * 3 + [(D + "T09:10:00", "pass:mention"), (D + "T09:20:00", "skip:policy"),
                                                             (D + "T09:30:00", "outbound:replied:burst_cancelled"), (N + "T07:00:00", "pass:all")])
        c.executemany("INSERT INTO runs(id, started_at, jid, ok, output, kind) VALUES (?, ?, ?, ?, ?, ?)", [
            (1, D + "T10:00:00", "a@lid", 1, '{"stage": "won"}', "triage"), (2, D + "T11:00:00", "b@lid", 0, None, "triage"),
            (3, D + "T12:00:00", "c@lid", None, None, None), (4, D + "T02:40:00", "a@lid", 1, None, "dossier"),
            (5, N + "T07:00:00", "e@lid", 1, None, "triage")])
        c.executemany("INSERT INTO conversations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("a@lid", "Alex Rivera", "open", 1, "soon", "问下周能不能上门。其他细节", N + "T03:00:00", 1, "won", 1, "付款到了", D + "T10:05:00", "offered"),
            ("b@lid", "Blake Chen", "open", 1, "now", "wants a quote today! more", D + "T11:01:00", 2, "talking", 0, "", D + "T11:05:00", None),
            ("e@lid", "Evan Tan", "open", 1, "now", "judged this morning", N + "T07:01:00", 5, None, None, None, None, None),
            ("d@lid", "Dana Park", "replied", 1, "low", "", D + "T08:00:00", None, None, None, None, D + "T08:00:00", None)])
        c.executemany("INSERT INTO dossier_jobs(jid, name, reason, ok, finished_at) VALUES (?, ?, ?, ?, ?)", [
            ("a@lid", "Alex Rivera", "开新档: work / talking", 1, D + "T02:50:00"), ("b@lid", "Blake Chen", "夜批重写", 1, D + "T02:55:00"),
            ("f@lid", "Farah Lee", "事实表: 付过钱有对话没档", 1, D + "T02:58:00"), ("g@lid", "Gina Ho", "夜批重写", 0, D + "T03:00:00")])
        c.commit()
        c.close()
        W.write_json_atomic(live / "facts.json", {"written_at": D + "T03:00:12+08:00", "people_index_error": None, "mismatch": {
            "paid_no_chat": [{"name": "Evan Tan"}], "unparsable_phone": [{"name": "Casey Ng"}, {"raw": "12345678"}],
            "paid_chat_no_note_queued": [], "paid_chat_stale_no_note": [{"name": "Hana Yu"}]}})
        W.write_json_atomic(live / "backup-last.json", {"at": D + "T03:30:05", "ok": True, "dest": "x", "bytes": 2500000, "files": 9,
                                                        "error": None, "dry_run": False})
        W.write_json_atomic(live / "second-source-last.json", {"at": D + "T03:10:00", "day": D, "ok": True, "n_chats": 3, "n_msgs": 12, "errors": []})
        W.write_json_atomic(live / "acceptance.json", {"questions": {N: "Open the board on your phone."}})

        d = build_digest(cfg, day)
        L = d["lines"]
        print("\n".join("    | " + x for x in L))
        check(L[0] == "📋 WhatsApp Live 日报 · 2026-09-13" and not d["empty"], "header and a busy day")
        check(L[1] == "昨天 4 封进闸 · 4 个 run (成功 2 / 失败 2 含没收尾 1) · 省了 1 个; 分诊 3 · 人物档 1",
              "block 1: gate, runs with unfinished, saved, kinds (a run without kind counts as triage)")
        check(L[2] == "还挂着要回 (2): Blake Chen · wants a quote today" and L[3] == "· Alex Rivera · 问下周能不能上门",
              "block 2: priority order from vocab, judged before midnight even though a nightly lift touched updated_at, morning judgment excluded")
        check(L[4] == "阶段动了 (2): Alex Rivera 已报价 → 已成交 (付款到了)" and L[5] == "· Blake Chen → 在谈",
              "block 3: stage labels from the vocabulary, previous stage from stage_prev")
        check(L[6] == "人物档: 开档 2 · 重写 1 · 退回 1", "block 4: a job queued by the facts worker counts as opened")
        check(L[7].startswith("Sales sheet 对不上 (事实表停在 2026-09-13): 付了钱没对话 (1: Evan Tan) · 电话解析不了 (2: Casey Ng · 12345678)")
              and "另 1 人有对话但 30 天没话, 没开 (Hana Yu)" in L[7], "block 5: source label, stale facts marked, silent people")
        check(L[8].startswith("健康: WhatsApp 库 2.0 小时前有动 · Sales sheet ✗ (停在 2026-09-13) · 夜备 ✗ (停在 2026-09-13) · Lark 拉 ✓ (2026-09-13, 3 会话 12 封) · intake 里 2 条 open"),
              "block 6: stale facts and backup say so, second source fresh, intake count from wa_intake.count_open")
        check(L[9:] == ["🧪 今天验收 (2026-09-14):", "Open the board on your phone."] and len(L) <= MAX_LINES + 2, "acceptance lines outside the 12")
        check(L[8].endswith(" · 脑叫不动 50 分钟 (limit|auth)") and d["data"]["health"]["brain_down"] == {"minutes": 50, "kinds": ["limit", "auth"]},
              "brain outage: rows overlapping the day add up (a row from the night before counts from midnight), both kinds named")
        s = d["text_short"]
        check(s.startswith("<b>📋 WhatsApp Live · 09/13</b>") and "<b>要回 2</b>\nBlake Chen · Alex Rivera" in s and "<b>阶段动 2</b>" in s
              and "Sales sheet ✗" in s and "夜备 ✗" in s and "intake 2" in s and "脑停 50 分" in s and s.rstrip().endswith("</i>"), "short form blocks")
        ok, why = send_digest(cfg, d)
        got = pushed.read_text(encoding="utf-8")
        check(ok and (live / "digest" / "2026-09-13.md").is_file() and got.startswith("📋 WhatsApp Live · 09/13") and "<b>" not in got,
              "send: file written, short form delivered through the command outlet as plain text")
        check(latest_digest(cfg)["date"] == "2026-09-13" and latest_digest(cfg)["lines"][0] == L[0], "latest_digest reads it back")
        check(oct((live / "digest" / "2026-09-13.md").stat().st_mode & 0o777) == "0o600", "digest file 0600")

        W.write_json_atomic(live / "facts.json", {"written_at": N + "T03:00:12+08:00", "people_index_error": "people folder not found: /x",
                                                  "mismatch": {"paid_no_chat": [], "unparsable_phone": [], "paid_chat_no_note_queued": []}})
        W.write_json_atomic(live / "backup-last.json", {"at": N + "T03:30:05", "ok": True, "dest": "x", "bytes": 2500000, "files": 9,
                                                        "error": None, "dry_run": False})
        W.write_json_atomic(live / "second-source-last.json", {"day": "2026-09-12", "ok": True, "n_chats": 1, "n_msgs": 2})
        q = build_digest(cfg, "2026-09-01")
        check(q["empty"] and q["lines"][1] == "昨天安静, daemon 活着 (库 2.0 小时前有动)", "quiet day still has a digest")
        check(q["lines"][2] == "Sales sheet 对不上: 付了钱没对话 (0) · 电话解析不了 (0) · 排队开档 (0) · 人物档房读不到, 没排开档",
              "fix: unreadable person notes folder said out loud")
        check("Lark 拉 ✗ (最后一次拉的是 2026-09-12, 不是 2026-09-01)" in q["lines"][3] and "夜备 ✓ (03:30, 2.5 MB)" in q["lines"][3]
              and "Sales sheet ✓ (03:00)" in q["lines"][3], "fresh backup and facts pass, an old pull is stale")
        check("脑叫不动" not in q["lines"][3] and q["data"]["health"]["brain_down"] is None and "脑停" not in q["text_short"],
              "brain outage: nothing on a day no outage overlaps")
        c = sqlite3.connect(str(live / "state.sqlite"))
        c.execute("INSERT INTO brain_outages(kind, started_at, ended_at, detail) VALUES ('auth', '2026-09-05T23:30:00', NULL, 'not logged in')")
        c.commit()
        c.close()
        o5 = build_digest(cfg, "2026-09-05")
        check(any(x.endswith("脑叫不动 30 分钟 (auth)") for x in o5["lines"]), "brain outage: an open row counts up to now (here the end of the day)")

        en_cfg = W.load_config(live / "config.json")
        en_cfg["board"]["lang"] = "en"
        en_cfg["digest"]["push"] = False
        e = build_digest(en_cfg, "2026-09-13")
        check(e["lines"][0] == "📋 WhatsApp Live digest · 2026-09-13" and e["lines"][4] == "Stage moves (2): Alex Rivera Offered → Won (付款到了)"
              and e["lines"][2].startswith("Still waiting on you (2): Blake Chen"), "English labels and vocabulary")
        check(any(x.endswith(" · brain was down %d minutes (limit|auth)" % e["data"]["health"]["brain_down"]["minutes"]) for x in e["lines"]), "brain outage line in the English table")
        pushed.unlink()
        ok, why = send_digest(en_cfg, e)
        check(not ok and "digest.push is false" in why and not pushed.exists(), "digest.push false: written, not pushed")
        off = W.load_config(live / "config.json")
        for k in ("facts", "backup", "second_source", "intake"):
            off[k]["enabled"] = False
        (live / "state.sqlite").rename(live / "state.moved")
        o = build_digest(off, "2026-09-13")
        check(o["empty"] and [x for x in o["lines"] if x.startswith("健康")] == ["健康: 状态库不在 · WhatsApp 库 2.0 小时前有动"],
              "blocks not configured are left out; a missing state database is said")
        check(all(chr(0x2014) not in x and " -- " not in x for x in L + q["lines"] + e["lines"]), "no em dash or double hyphen in the output")
        before = (live / "digest" / "2026-09-13.md").read_text(encoding="utf-8")
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--config", str(live / "config.json"), "--for", "2026-09-13"],
                           capture_output=True, timeout=60)
        check(r.returncode == 0 and b"empty=True" in r.stdout and (live / "digest" / "2026-09-13.md").read_text(encoding="utf-8") == before,
              "CLI default is a dry run: prints, writes nothing")
        src = Path(__file__).read_text(encoding="utf-8")
        check(chr(0x2014) not in src and chr(0x2013) not in src, "no em dash or en dash in this file")
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)
    return check.done()


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description="WhatsApp Live morning digest")
    ap.add_argument("--config", default=None, help="live/config.json (default: env WA_LIVE_CONFIG or ~/WhatsApp-Live/live/config.json)")
    ap.add_argument("--for", dest="day", default=None, help="YYYY-MM-DD, default yesterday")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="print text and data, write nothing, push nothing (default)")
    g.add_argument("--write", action="store_true", help="write digest/<date>.md only")
    g.add_argument("--send", action="store_true", help="write, then push the short form")
    g.add_argument("--selftest", action="store_true", help="offline checks with fakes")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    try:
        cfg = W.load_config(a.config)
    except (FileNotFoundError, ValueError) as e:
        print(e)
        return 2
    day = dt.date.fromisoformat(a.day) if a.day else (dt.date.today() - dt.timedelta(days=1))
    d = build_digest(cfg, day)
    if a.write:
        p = write_digest(cfg, d)
        print(d["text"])
        print("→ wrote %s (%d lines%s)" % (p, len(d["lines"]), ", quiet day" if d["empty"] else ""))
        return 0
    if a.send:
        ok, why = send_digest(cfg, d)
        print(d["text"])
        print("→ wrote %s; push %s%s" % (digest_dir(cfg) / ("%s.md" % d["date"]), "delivered" if ok else "not delivered",
                                         (" (%s)" % why) if why else ""))
        return 0 if ok else 2
    print(d["text"])
    print("---- %d lines, empty=%s ----" % (len(d["lines"]), d["empty"]))
    print(json.dumps(d["data"], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
