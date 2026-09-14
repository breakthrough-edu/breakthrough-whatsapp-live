#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wa_backfill.py · summary backfill: conversations with enough recent traffic but no `summary` in state.conversations yet
get one, ten per model run, so the brain has a memory from day one.

A batch of reading packs goes to one model call that returns {"<jid>": "summary"}; only summary and grounded_at are
written. Batches never run in parallel; a failed batch or a jid the model left out is simply picked up next time,
because candidates are recomputed from "summary still empty".

- candidates(cfg, days, min_msgs)    who gets a summary (the one rule; the daemon and the CLI both call it)
- run_backfill(cfg, call, sweep_fn, days, min_msgs, ...)   the whole line; the caller passes how to call the model and
                                                           how to run the reader
- parse_result / apply_result        tolerant JSON reading, and a write that never overwrites a summary

Rules:
- The WhatsApp database is opened read only. state.sqlite: only conversations.summary and grounded_at are written
  (a new row also carries jid, name, kind, updated_at); world, stage, priority, tag, draft and last_run_id are never touched.
- A row that already has a summary is never overwritten (the live brain may have written one minutes ago).
- With backfill.respect_gate (default true) the candidates follow the doorbell gate: one to one chats only when
  direct_policy lets them through, groups only when their group_policies entry (or unlisted_group_policy) is all or
  mention, ignored_jids never. Status and broadcast chats never.
- "Enough messages" counts content messages only (wa_common.CONTENT_TYPES), both directions.
- The prompt is the owner copy <live>/prompts/backfill-prompt.md; when it is missing the payload seed is used and a
  WARNING says so.
- Opening an image costs the model one turn, and a model told "at most 4" has opened twelve. So the cap is mechanical:
  a batch's packs keep the local path on at most backfill.max_images image or file lines (newest first, taking turns
  across the batch's chats); every other media line keeps its label and caption but loses its path. The batch runs with
  its own turn limit, backfill.max_turns, and a failed call is logged with its reason (subtype and errors when the
  model returned no text).

Usage:
  /usr/bin/python3 wa_backfill.py --list [--config PATH]           candidate count and the first 15 (read only)
  /usr/bin/python3 wa_backfill.py --dry-run [--no-transcribe]      runs the reader for the first batch, builds the prompt,
                                                                   no model call, nothing written to state
  /usr/bin/python3 wa_backfill.py --run --limit N                  calls the model and writes (normally the daemon does
                                                                   this on commands/backfill-summaries.json)
  /usr/bin/python3 wa_backfill.py --selftest                       fake WhatsApp database, fake state, fake reader and model

Python 3.9 standard library only (the daemon runs on /usr/bin/python3). Never imports wa_live except for --run.
"""

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wa_common as W  # noqa: E402

CORE_EPOCH_UTC = dt.datetime(2001, 1, 1, tzinfo=dt.timezone.utc)
SESSION_DIRECT, SESSION_GROUP = 0, 1
PACK_CAP = {"direct": 8000, "group": 6000}   # one to one packs are cut to 8k characters, groups to 6k, newest end kept
SUMMARY_MAX = 1500                           # same limit as the brain's summary
PROMPT_NAME = "backfill-prompt.md"
PASS_POLICIES = ("all", "mention")
MEDIA_PATH_RE = re.compile(r" → `(/[^`\n]+)`")    # the reader writes a media line's local path as  → `/abs/path`
OPENABLE_ICONS = ("🖼", "📎")                     # image and file lines: the only media a Read can use in a summary
PATH_DROPPED = " (路径略)"
_SEED_RE = re.compile(r"^<!-- seed: [^\n]*-->\n?", re.M)
_SEED_WARNED = set()


def now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


def safe_name(jid):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", jid)[:60]


def _ro(path):
    return sqlite3.connect("file:%s?mode=ro" % urllib.parse.quote(str(path)), uri=True, timeout=5)


def core_cutoff(days, now=None):
    """Core Data seconds (since 2001-01-01 UTC) of N days ago."""
    now = now or dt.datetime.now(dt.timezone.utc)
    return (now - dt.timedelta(days=days) - CORE_EPOCH_UTC).total_seconds()


def _section(cfg, name):
    v = (cfg or {}).get(name)
    return v if isinstance(v, dict) else {}


def state_db_path(cfg):
    """state.sqlite: cfg["state_db"] when a caller set it, else <live>/state.sqlite."""
    return Path(cfg["state_db"]) if (cfg or {}).get("state_db") else Path(W.live_dir(cfg)) / "state.sqlite"


def wa_db_path(wa_db=None):
    return Path(wa_db) if wa_db else Path(W.wa_root()) / "ChatStorage.sqlite"


def prompt_template(cfg, name=PROMPT_NAME, log=None, who="backfill"):
    """Owner copy <live>/prompts/<name>, else the payload seed daemon/templates/<name> with a loud WARNING
    (wa_second_source uses this same function). Returns (text without the seed stamp line, path used, used_seed)."""
    owner = Path(W.live_dir(cfg)) / "prompts" / name
    seed = Path(W.payload_dir()) / "daemon" / "templates" / name
    if owner.exists():
        path, used_seed = owner, False
    else:
        path, used_seed = seed, True
        if log and (who, name) not in _SEED_WARNED:   # once per process, not every run
            _SEED_WARNED.add((who, name))
            log("WARNING %s: owner prompt copy missing (%s), using the payload seed %s" % (who, owner, seed))
    return _SEED_RE.sub("", path.read_text(encoding="utf-8"), count=1), path, used_seed


# ---------------------------------------------------------------- who gets a backfill (the one rule)

def policy_for(cfg, jid, kind):
    """The doorbell's policy for a chat: direct_policy for one to one; for groups the group_policies entry
    (a dict with "policy" or a bare string), else unlisted_group_policy."""
    if kind == "direct":
        return str(cfg.get("direct_policy") or "all")
    unlisted = str(cfg.get("unlisted_group_policy") or "skip")
    ent = (cfg.get("group_policies") or {}).get(jid)
    if isinstance(ent, dict):
        return str(ent.get("policy") or unlisted)
    if isinstance(ent, str) and ent.strip():
        return ent.strip()
    return unlisted


def gate_allows(cfg, jid, kind):
    """Would the doorbell ever let this chat through: not ignored and its policy is all or mention."""
    if jid in set((cfg or {}).get("ignored_jids") or []):
        return False
    return policy_for(cfg, jid, kind) in PASS_POLICIES


def candidates(cfg, days=None, min_msgs=None, wa_db=None, stats=None):
    """Who gets a backfill (daemon and CLI both call only this):
    chats of the WhatsApp database with >= min_msgs content messages (wa_common.CONTENT_TYPES, both directions) in the
    last `days` days, one to one and groups, never @status or @broadcast or ignored_jids, and with
    backfill.respect_gate also only chats the doorbell gate would let through; and state.conversations has no row for
    them, or the row's summary is NULL or blank. Returns [{jid, name, kind, n, policy}] by n descending.
    `stats` (a dict) receives counts: gate_skipped, ignored, have_summary."""
    bf = _section(cfg, "backfill")
    days = int(days if days is not None else bf.get("days", 30))
    min_msgs = int(min_msgs if min_msgs is not None else bf.get("min_msgs", 10))
    respect_gate = bool(bf.get("respect_gate", True))
    types = sorted(int(t) for t in W.CONTENT_TYPES)
    con = _ro(wa_db_path(wa_db))
    try:
        rows = con.execute(
            """SELECT s.ZCONTACTJID AS jid, s.ZPARTNERNAME AS name, s.ZSESSIONTYPE AS stype, COUNT(m.Z_PK) AS n
               FROM ZWACHATSESSION s JOIN ZWAMESSAGE m ON m.ZCHATSESSION = s.Z_PK
               WHERE s.ZSESSIONTYPE IN (0,1) AND m.ZMESSAGEDATE >= ? AND m.ZMESSAGETYPE IN (%s)
                 AND s.ZCONTACTJID IS NOT NULL
                 AND s.ZCONTACTJID NOT LIKE '%%@status' AND s.ZCONTACTJID NOT LIKE '%%@broadcast'
               GROUP BY s.Z_PK""" % ",".join(str(t) for t in types),
            (core_cutoff(days),)).fetchall()
    finally:
        con.close()
    have = {}
    sp = state_db_path(cfg)
    if sp.exists():
        sc = _ro(sp)
        try:
            for jid, summary in sc.execute("SELECT jid, summary FROM conversations").fetchall():
                have[jid] = summary
        except sqlite3.OperationalError:
            have = {}
        finally:
            sc.close()
    ignored = set(cfg.get("ignored_jids") or [])
    counts = {}
    for jid, name, stype, n in rows:       # the same jid in two sessions (rare): add the counts up before the threshold
        c = counts.get(jid)
        if c:
            c["n"] += int(n)
        else:
            counts[jid] = {"jid": jid, "name": name or jid, "kind": "group" if stype == SESSION_GROUP else "direct", "n": int(n)}
    st = {"gate_skipped": 0, "ignored": 0, "have_summary": 0}
    out = []
    for c in counts.values():
        if c["n"] < min_msgs:
            continue
        if c["jid"] in ignored:
            st["ignored"] += 1
            continue
        c["policy"] = policy_for(cfg, c["jid"], c["kind"])
        if respect_gate and c["policy"] not in PASS_POLICIES:
            st["gate_skipped"] += 1
            continue
        if c["jid"] in have and str(have[c["jid"]] or "").strip():
            st["have_summary"] += 1
            continue
        out.append(c)
    if isinstance(stats, dict):
        stats.update(st)
    return sorted(out, key=lambda c: (-c["n"], c["jid"]))


# ---------------------------------------------------------------- building the prompt

def trim_pack(md, kind):
    """Reading pack markdown -> the part fed to the model: drop the whole window header (the table of contents), keep the
    media count line and the warnings block; over the cap keep the first lines of the chat plus the newest end, with a
    note saying how much was cut. Relies on the reader's markers `## [1] `, `- 媒体:` and `## ⚠️`."""
    md = md or ""
    cap = PACK_CAP.get(kind, PACK_CAP["direct"])
    i = md.find("\n## [1] ")
    meta = []
    if i != -1:
        head, body = md[:i], md[i + 1:]
        in_warn = False
        for line in head.splitlines():
            if line.startswith("- 媒体:"):
                meta.append(line)
            elif line.startswith("## ⚠️"):
                in_warn = True
                meta.append(line)
            elif in_warn:
                if line.startswith("## ") or line.startswith("⛔"):
                    in_warn = False
                elif line.strip():
                    meta.append(line)
    else:
        body = md
    text = ("\n".join(meta) + "\n\n" + body) if meta else body
    if len(text) <= cap:
        return text.strip("\n")
    j = body.find("\n### ")
    chat_head = body[:j + 1] if 0 <= j < 1200 else ""
    room = max(1000, cap - len(chat_head) - len("\n".join(meta)) - 80)
    tail = body[-room:]
    nl = tail.find("\n")
    if 0 <= nl < 400:
        tail = tail[nl + 1:]
    cut = len(body) - len(chat_head) - len(tail)
    note = "(包太长: 前面 %d 字没给你, 只留最新的 %d 字; 更早的事看不到就别写)" % (cut, len(tail))
    parts = [p for p in ("\n".join(meta), chat_head.strip("\n"), note, tail.strip("\n")) if p]
    return "\n\n".join(parts)


def max_images(cfg):
    try:
        return max(0, int(_section(cfg, "backfill").get("max_images", 4)))
    except (TypeError, ValueError):
        return 4


def cap_media_paths(items, limit):
    """Keep the local path on at most `limit` image or file lines across the batch: each pack's newest first, taking
    turns across packs; every other media path (the rest of the images, voice, video, stickers) becomes (路径略).
    Rewrites it["pack"] in place. Returns (kept, dropped)."""
    per = []
    for it in items:
        pack = it.get("pack") or ""
        openable = []
        for m in MEDIA_PATH_RE.finditer(pack):
            line_head = pack[pack.rfind("\n", 0, m.start()) + 1:m.start()]
            if any(icon in line_head for icon in OPENABLE_ICONS):
                openable.append(m.start())
        per.append(list(reversed(openable)))          # newest (the end of the pack) first
    keep = [set() for _ in items]
    kept, rnd = 0, 0
    while kept < limit and any(rnd < len(p) for p in per):
        for k, p in enumerate(per):
            if kept >= limit:
                break
            if rnd < len(p):
                keep[k].add(p[rnd])
                kept += 1
        rnd += 1
    dropped = 0
    for k, it in enumerate(items):
        pack = it.get("pack") or ""
        parts, last = [], 0
        for m in MEDIA_PATH_RE.finditer(pack):
            if m.start() in keep[k]:
                continue
            parts.append(pack[last:m.start()])
            parts.append(PATH_DROPPED)
            last = m.end()
            dropped += 1
        if parts:
            parts.append(pack[last:])
            it["pack"] = "".join(parts)
    return kept, dropped


def owner_label(cfg):
    lab = str((cfg or {}).get("owner_label") or "").strip()
    if lab:
        return lab
    names = [str(x).strip() for x in ((cfg or {}).get("my_names") or []) if str(x).strip()]
    return names[0] if names else "主人"


def build_prompt(template, items, days, cfg=None):
    """items: [{jid, name, kind, n, pack}] -> the whole prompt. Placeholders: {N} {DAYS} {JID_LIST} {CONVERSATIONS}
    {OWNER_LABEL} {WARM_DAYS} {MAX_IMAGES}."""
    secs = []
    for k, it in enumerate(items, 1):
        secs.append("<<<对话 %d / %d 开始 · jid=%s · %s · %s · 近 %d 天 %d 封>>>\n\n%s\n\n<<<对话 %d 结束 · jid=%s>>>" % (
            k, len(items), it["jid"], it["name"], "群" if it["kind"] == "group" else "一对一", days, it["n"],
            (it.get("pack") or "(阅读包是空的)").strip("\n"), k, it["jid"]))
    warm = int(_section(cfg, "brain").get("warm_days", 2) or 2)
    return (template.replace("{OWNER_LABEL}", owner_label(cfg)).replace("{WARM_DAYS}", str(warm))
            .replace("{MAX_IMAGES}", str(max_images(cfg)))
            .replace("{N}", str(len(items))).replace("{DAYS}", str(days))
            .replace("{JID_LIST}", " · ".join("`%s`" % it["jid"] for it in items))
            .replace("{CONVERSATIONS}", "\n\n".join(secs)))


# ---------------------------------------------------------------- reading what the model returns

def _json_values(text):
    """Every {...} or [...] in text that json.loads accepts (skipping brackets inside strings; strict=False tolerates raw
    newlines inside strings), in order. Only when nothing parses, wa_common.repair_inner_quotes fixes unescaped inner
    double quotes once and the scan runs again."""
    out = []
    if not text:
        return out
    starts = [m.start() for m in re.finditer(r"[\{\[]", text)]
    for i in starts:
        opener = text[i]
        closer = "}" if opener == "{" else "]"
        depth = 0
        in_str = False
        esc = False
        for j in range(i, len(text)):
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c in "{[":
                depth += 1
            elif c in "}]":
                depth -= 1
                if depth == 0:
                    if c == closer:
                        try:
                            out.append(json.loads(text[i:j + 1], strict=False))
                        except Exception:
                            pass
                    break
    if not out and text:
        fixed = W.repair_inner_quotes(text)
        if fixed != text:
            return _json_values(fixed)
    return out


def _key_matcher(batch_jids):
    exact = {j.lower(): j for j in batch_jids}
    heads = {}
    for j in batch_jids:
        heads.setdefault(j.split("@")[0].lower(), []).append(j)

    def match(key):
        k = str(key or "").strip().strip("`'\"<> ").lower()
        if k in exact:
            return exact[k]
        h = k.split("@")[0].lstrip("+").strip()
        cand = heads.get(h) or []
        return cand[0] if len(cand) == 1 else None
    return match


def _coerce_summary(v):
    if isinstance(v, str):
        s = v
    elif isinstance(v, bool) or v is None:
        s = ""
    elif isinstance(v, (int, float)):
        s = str(v)
    elif isinstance(v, dict):
        s = _coerce_summary(v.get("summary") if "summary" in v else v.get("摘要"))
    elif isinstance(v, list):
        s = " ".join(_coerce_summary(x) for x in v)
    else:
        s = ""
    return s.strip()[:SUMMARY_MAX]


def _pairs(obj):
    """The legal shapes a model writes -> [(key, value)]: {jid: s} · {"summaries": {jid: s}} · [{jid, summary}] ·
    {"summaries": [{jid, summary}]} (also results, conversations, data)."""
    if isinstance(obj, list):
        out = []
        for x in obj:
            if isinstance(x, dict):
                key = x.get("jid") or x.get("id") or x.get("chat")
                if key is not None:
                    out.append((key, x.get("summary") if "summary" in x else x.get("摘要")))
        return out
    if isinstance(obj, dict):
        for wrap in ("summaries", "results", "conversations", "data"):
            if wrap in obj and isinstance(obj[wrap], (dict, list)):
                return _pairs(obj[wrap])
        return list(obj.items())
    return []


def parse_result(text, batch_jids):
    """Model text -> {jid: summary}. Tolerates fences, chatter before and after, raw newlines inside strings, keys with or
    without the @ suffix, non string values, an extra wrapper; keeps only this batch's jids, drops empty summaries, cuts
    each to 1500. With several JSON values the one matching the most jids wins. Nothing readable gives {}."""
    match = _key_matcher(list(batch_jids))
    best = {}
    for obj in _json_values(text):
        got = {}
        for key, val in _pairs(obj):
            jid = match(key)
            if not jid or jid in got:
                continue
            s = _coerce_summary(val)
            if s:
                got[jid] = s
        if len(got) > len(best):
            best = got
    return best


# ---------------------------------------------------------------- writing

def apply_result(state_db, batch, summaries):
    """Write summary and grounded_at only. No row: INSERT OR IGNORE (jid, name, kind, summary, grounded_at, updated_at);
    a row: UPDATE only while its summary is still empty (never over what the live brain just wrote). Returns rows written."""
    info = {c["jid"]: c for c in batch}
    con = sqlite3.connect(str(state_db), timeout=15)
    written = 0
    try:
        for jid, summary in summaries.items():
            if jid not in info or not str(summary or "").strip():
                continue
            ts = now_iso()
            cur = con.execute("INSERT OR IGNORE INTO conversations(jid, name, kind, summary, grounded_at, updated_at) VALUES(?,?,?,?,?,?)",
                              (jid, info[jid].get("name"), info[jid].get("kind"), summary, ts, ts))
            if cur.rowcount == 1:
                written += 1
                continue
            cur = con.execute("UPDATE conversations SET summary=?, grounded_at=? WHERE jid=? AND (summary IS NULL OR TRIM(summary, ' ' || char(9) || char(10) || char(13))='')",
                              (summary, ts, jid))
            written += cur.rowcount
        con.commit()
    finally:
        con.close()
    return written


# ---------------------------------------------------------------- the whole line

def run_backfill(cfg, call, sweep_fn, days=None, min_msgs=None, limit=None, batch_size=10, dry_run=False, after_batch=None, log=None,
                 wa_db=None):
    """Backfill (batches never in parallel). call(prompt, run_dir) -> dict with text and is_error (like Brain.call_claude)
    or plain text; sweep_fn(jid, days, out_json_path) -> reading pack markdown; after_batch() after each written batch
    (the daemon rewrites summaries.md there). days and min_msgs default to backfill.days and backfill.min_msgs.
    A failed sweep puts that jid in failed_jids and the batch goes on; a model error or nothing parsed fails the whole
    batch; jids the model left out fail too. Nothing is retried now: the next call recomputes candidates.
    dry_run: sweeps run and prompt.md is written into the run folder, no model call, nothing written to state."""
    log = log or print
    bf = _section(cfg, "backfill")
    days = int(days if days is not None else bf.get("days", 30))
    min_msgs = int(min_msgs if min_msgs is not None else bf.get("min_msgs", 10))
    batch_size = max(1, int(batch_size or 10))
    stats = {}
    cands = candidates(cfg, days=days, min_msgs=min_msgs, wa_db=wa_db, stats=stats)
    out = {"candidates": len(cands), "batches": 0, "written": 0, "failed_jids": [], "prompt_sizes": [],
           "packs": [], "run_dirs": [], "dry_run": bool(dry_run), "gate_skipped": stats.get("gate_skipped", 0),
           "prompt_seed_fallback": False}
    if limit is not None:
        cands = cands[:max(0, int(limit))]
    if not cands:
        log("backfill: nothing to backfill")
        return out
    template, _path, used_seed = prompt_template(cfg, log=log)
    out["prompt_seed_fallback"] = used_seed
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    nb = (len(cands) + batch_size - 1) // batch_size
    log("backfill: %d candidates, processing %d in %d batches%s" % (out["candidates"], len(cands), nb, " (dry run)" if dry_run else ""))
    state_db = state_db_path(cfg)
    for bi in range(nb):
        batch = cands[bi * batch_size:(bi + 1) * batch_size]
        run_dir = Path(W.live_dir(cfg)) / "runs" / ("backfill-%s-b%d" % (stamp, bi + 1))
        run_dir.mkdir(parents=True, exist_ok=True)
        out["run_dirs"].append(str(run_dir))
        items = []
        for c in batch:
            t0 = time.time()
            try:
                md = sweep_fn(c["jid"], days, run_dir / ("sweep-%s.json" % safe_name(c["jid"])))
            except Exception as e:
                log("backfill b%d: reader failed for %s (%s): %s" % (bi + 1, c["name"], c["jid"], str(e)[:200]))
                out["failed_jids"].append(c["jid"])
                continue
            if not (md or "").strip():
                log("backfill b%d: empty reading pack for %s (%s)" % (bi + 1, c["name"], c["jid"]))
                out["failed_jids"].append(c["jid"])
                continue
            pack = trim_pack(md, c["kind"])
            it = dict(c)
            it["pack"] = pack
            items.append(it)
            out["packs"].append({"batch": bi + 1, "jid": c["jid"], "name": c["name"], "kind": c["kind"], "n": c["n"],
                                 "raw_chars": len(md), "pack_chars": len(pack), "sweep_s": round(time.time() - t0, 1)})
        if not items:
            log("backfill b%d: no usable reading pack in this batch, skipped" % (bi + 1))
            continue
        kept, dropped = cap_media_paths(items, max_images(cfg))
        if dropped:
            log("backfill b%d: media paths kept %d, dropped %d (backfill.max_images %d)" % (bi + 1, kept, dropped, max_images(cfg)))
        prompt = build_prompt(template, items, days, cfg)
        (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
        out["batches"] += 1
        out["prompt_sizes"].append(len(prompt))
        log("backfill b%d: %d reading packs, prompt %d chars in %s" % (bi + 1, len(items), len(prompt), run_dir))
        if dry_run:
            continue
        jids = [it["jid"] for it in items]
        try:
            res = call(prompt, run_dir)
            if isinstance(res, dict):
                text = res.get("text") or ""
                is_err = bool(res.get("is_error"))
                reason = str(res.get("error") or "").strip() or text.strip()[:200] or "no text and no reason"
            else:
                text = str(res or "")
                is_err = False
            (run_dir / "result.txt").write_text(text, encoding="utf-8")
            if is_err:
                raise RuntimeError("model returned is_error: %s" % reason[:300])
            summaries = parse_result(text, jids)
            (run_dir / "parsed.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=1), encoding="utf-8")
            if not summaries:
                raise ValueError("no summary could be parsed (%d chars returned)" % len(text))
        except Exception as e:
            log("backfill b%d: failed, all %d retried next time: %s" % (bi + 1, len(jids), str(e)[:300]))
            out["failed_jids"].extend(jids)
            continue
        missing = [j for j in jids if j not in summaries]
        if missing:
            log("backfill b%d: the model left out %d, retried next time: %s" % (bi + 1, len(missing), ", ".join(missing)))
            out["failed_jids"].extend(missing)
        w = apply_result(state_db, items, summaries)
        out["written"] += w
        if w < len(summaries):
            log("backfill b%d: %d already had a summary (written by the live brain), not overwritten" % (bi + 1, len(summaries) - w))
        log("backfill b%d: wrote %d" % (bi + 1, w))
        if after_batch is not None:
            try:
                after_batch()
            except Exception as e:
                log("backfill b%d: after_batch failed (continuing): %s" % (bi + 1, e))
    log("backfill: done, %d batches, wrote %d, failed %d" % (out["batches"], out["written"], len(out["failed_jids"])))
    return out


# ---------------------------------------------------------------- the CLI's reader

def sweep_script(cfg):
    """wa_sweep.py of the reader: paths.reader_dir may point at the scripts folder itself or at a payload root."""
    rd = Path(str(_section(cfg, "paths").get("reader_dir") or W.payload_dir())).expanduser()
    tried = [rd / "wa_sweep.py"] + sorted(rd.glob("skills/*-whatsapp-read/scripts/wa_sweep.py"))
    tried += sorted(Path(W.payload_dir()).glob("skills/*-whatsapp-read/scripts/wa_sweep.py"))
    for p in tried:
        if p.exists():
            return p
    raise FileNotFoundError("wa_sweep.py not found under paths.reader_dir (%s) or the payload" % rd)


def default_sweep_fn(cfg, transcribe=True, work_name="backfill-cli"):
    """The CLI's sweep_fn, shaped like the daemon's: its own work dir <home>/runs-work/<work_name> (snapshots never collide
    with live runs), the shared transcript cache <home>/transcripts-cache linked in, the reader config refreshed from
    <home>/config.json on every start."""
    base = Path(W.home_dir(cfg))
    work = base / "runs-work" / work_name
    work.mkdir(parents=True, exist_ok=True)
    (base / "transcripts-cache").mkdir(parents=True, exist_ok=True)
    link = work / "transcripts-cache"
    if not link.is_symlink():
        if link.exists():
            link.rename(work / ("transcripts-cache.old-%d" % int(time.time())))
        link.symlink_to(base / "transcripts-cache")
    src = base / "config.json"
    if src.exists():
        text = src.read_text(encoding="utf-8")
        dst = work / "config.json"
        if not dst.exists() or dst.read_text(encoding="utf-8") != text:
            dst.write_text(text, encoding="utf-8")
            os.chmod(str(dst), 0o600)
    b = _section(cfg, "brain")
    timeout = int(b.get("sweep_timeout_seconds", 1500))
    env = dict(W.subprocess_env(cfg))            # PATH for launchd, WA_LIVE_CONFIG and WA_WHISPER_MODEL from config
    python = W.resolve_bin(cfg, "python") or "/usr/bin/python3"
    script = sweep_script(cfg)
    transcribe = transcribe and bool(b.get("transcribe", True))

    def sweep(jid, days, out_json):
        args = [str(python), str(script), "--work-dir", str(work), "--only-jid", jid, "--days", str(days), "--out", str(out_json)]
        if not transcribe:
            args.append("--no-transcribe")
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            raise RuntimeError("wa_sweep timed out after %ds" % timeout)
        if r.returncode != 0:
            raise RuntimeError("wa_sweep exit %d: %s" % (r.returncode, (r.stderr or r.stdout)[-300:].strip()))
        return Path(out_json).with_suffix(".md").read_text(encoding="utf-8")
    return sweep


# ---------------------------------------------------------------- selftest

def _fake_world(tmp):
    """Fake WhatsApp database (ZWACHATSESSION / ZWAMESSAGE) and fake state database (conversations with the real columns)."""
    wa = tmp / "ChatStorage.sqlite"
    c = sqlite3.connect(str(wa))
    c.executescript("""
    CREATE TABLE ZWACHATSESSION(Z_PK INTEGER PRIMARY KEY, ZCONTACTJID TEXT, ZPARTNERNAME TEXT, ZSESSIONTYPE INTEGER);
    CREATE TABLE ZWAMESSAGE(Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZMESSAGEDATE REAL, ZMESSAGETYPE INTEGER, ZISFROMME INTEGER);
    """)
    sessions = [
        (1, "test-alice@lid", "Alice Test", 0, 12),         # >=10, no row: candidate
        (2, "test-bob@s.whatsapp.net", "Bob Test", 0, 9),   # <10: not counted
        (3, "test-carol@lid", "Carol Test", 0, 15),         # has a summary: not a candidate
        (4, "test-team@g.us", "Test Team Group", 1, 20),    # summary empty string, policy all: candidate (group)
        (5, "test-dan@lid", "Dan Test", 0, 11),             # summary NULL: candidate
        (6, "status@broadcast", "Status", 0, 30),           # @broadcast: excluded
        (7, "test-old@lid", "Old Test", 0, 40),             # all older than 30 days: not counted
        (8, "test-sys@lid", "System Test", 0, 25),          # only system messages (type 6): not counted
        (9, "test-ghost@lid", "Ghost Test", 0, 10),         # exactly 10, row with a blank summary: candidate
        (10, "test-eve@lid", "Eve Test", 0, 10),            # exactly 10, no row: candidate (tests INSERT)
        (11, "test-skipgrp@g.us", "Skipped Group", 1, 25),  # policy skip: excluded by the gate
        (12, "test-unlisted@g.us", "Unlisted Group", 1, 18),  # not listed, unlisted_group_policy skip: excluded by the gate
        (13, "test-mention@g.us", "Mention Group", 1, 30),  # policy mention (bare string): candidate
        (14, "test-mixed@lid", "Mixed Test", 0, 14),        # 14 rows but only 7 content messages: not counted
    ]
    now = core_cutoff(0)
    pk = 0
    for spk, jid, name, stype, n in sessions:
        c.execute("INSERT INTO ZWACHATSESSION VALUES(?,?,?,?)", (spk, jid, name, stype))
        for k in range(n):
            pk += 1
            date = now - 86400 * 45 if jid == "test-old@lid" else now - 3600 * (k + 1)
            if jid == "test-sys@lid":
                mtype = 6
            elif jid == "test-mixed@lid":
                mtype = 6 if k % 2 else 0
            else:
                mtype = 3 if k % 4 == 0 else 0
            c.execute("INSERT INTO ZWAMESSAGE VALUES(?,?,?,?,?)", (pk, spk, date, mtype, k % 2))
    for k in range(3):   # Bob has 3 more outside the 30 days: they must not lift him over the line
        pk += 1
        c.execute("INSERT INTO ZWAMESSAGE VALUES(?,?,?,?,?)", (pk, 2, now - 86400 * 40, 0, 0))
    c.commit()
    c.close()
    st = tmp / "state.sqlite"
    c = sqlite3.connect(str(st))
    c.execute("""CREATE TABLE conversations(jid TEXT PRIMARY KEY, name TEXT, kind TEXT, tag TEXT, flow TEXT, needs_me INTEGER, draft TEXT,
        reason TEXT, confidence REAL, last_in_pk INTEGER, last_in_at TEXT, last_out_at TEXT, last_run_id INTEGER, updated_at TEXT,
        situation TEXT, timeline TEXT, world TEXT, stage TEXT, stage_auto INTEGER, priority TEXT, stage_changed_at TEXT, stake TEXT,
        summary TEXT, grounded_at TEXT, refs TEXT, stage_prev TEXT)""")
    c.execute("INSERT INTO conversations(jid,name,kind,world,stage,summary,updated_at) VALUES('test-carol@lid','Carol Test','direct','work','talking','old summary','2026-09-13T10:00:00')")
    c.execute("INSERT INTO conversations(jid,name,kind,world,summary,draft,updated_at) VALUES('test-team@g.us','Test Team Group','group','work','','draft must stay','2026-09-13T10:00:00')")
    c.execute("INSERT INTO conversations(jid,name,kind,tag,updated_at) VALUES('test-dan@lid','Dan Test','direct','replied','2026-09-13T10:00:00')")
    c.execute("INSERT INTO conversations(jid,name,kind,world,summary,updated_at) VALUES('test-ghost@lid','Ghost Test','direct','personal','   ','2026-09-13T10:00:00')")
    c.commit()
    c.close()
    return wa, st


def selftest_cfg(tmp, extra=None):
    """A real cfg for the selftest: <tmp>/live/config.json with every path inside tmp, loaded through
    wa_common.load_config; refuses (RuntimeError) when the live dir resolves outside tmp."""
    tmp = Path(tmp)
    live = tmp / "live"
    live.mkdir(parents=True, exist_ok=True)
    raw = {"version": 1, "owner_label": "Test Owner",
           "paths": {"home": str(tmp), "live_dir": str(live), "reader_dir": str(tmp / "reader")},
           "brain": {"cwd": str(tmp / "brain-cwd"), "warm_days": 2},
           "direct_policy": "all", "unlisted_group_policy": "skip", "ignored_jids": [],
           "group_policies": {"test-team@g.us": {"policy": "all", "name": "Test Team Group"},
                              "test-skipgrp@g.us": {"policy": "skip", "name": "Skipped Group"},
                              "test-mention@g.us": "mention"},
           "backfill": {"days": 30, "min_msgs": 10, "batch_size": 10, "respect_gate": True}}
    raw.update(extra or {})
    p = live / "config.json"
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    cfg = W.load_config(str(p))
    got = Path(W.live_dir(cfg)).resolve()
    if not str(got).startswith(str(tmp.resolve())):
        raise RuntimeError("selftest refuses to run: live dir resolved to %s, outside the temp folder" % got)
    return cfg


def _selftest():
    tmp = Path(tempfile.mkdtemp(prefix="wa_backfill_selftest_"))
    fails = []
    total = [0]

    def check(cond, msg):
        total[0] += 1
        print(("  ok   " if cond else "  FAIL ") + msg)
        if not cond:
            fails.append(msg)

    try:
        wa, st = _fake_world(tmp)
        cfg = selftest_cfg(tmp)
        check(state_db_path(dict(cfg, state_db=str(st))) == st, "state_db_path honours cfg['state_db']")
        cfg["state_db"] = str(st)
        live = Path(W.live_dir(cfg))
        check(str(live.resolve()).startswith(str(tmp.resolve())), "wa_common.load_config resolves the live dir inside the temp folder")

        # 1 · the candidate rule
        stats = {}
        cs = candidates(cfg, wa_db=wa, stats=stats)
        got = [c["jid"] for c in cs]
        check(got == ["test-mention@g.us", "test-team@g.us", "test-alice@lid", "test-dan@lid", "test-eve@lid", "test-ghost@lid"],
              "candidates: >=10 content messages with no row / empty / NULL / blank summary, gate respected, by n then jid; got %s" % got)
        check(cs[0]["kind"] == "group" and cs[2]["kind"] == "direct" and cs[1]["n"] == 20, "candidates: kind and n right")
        check(stats.get("gate_skipped") == 2 and stats.get("have_summary") == 1, "candidates: stats count 2 gate skips and 1 existing summary; got %s" % stats)
        check("test-mixed@lid" not in got, "candidates: only content messages count (14 rows, 7 of them content)")
        check("test-dan@lid" not in [c["jid"] for c in candidates(dict(cfg, ignored_jids=["test-dan@lid"]), wa_db=wa)], "candidates: ignored_jids excluded")
        m12 = [c["jid"] for c in candidates(cfg, min_msgs=12, wa_db=wa)]
        check(m12 == ["test-mention@g.us", "test-team@g.us", "test-alice@lid"],
              "candidates: min_msgs=12 keeps the two groups and Alice (her voice notes count as content); got %s" % m12)
        nogate = dict(cfg, backfill=dict(cfg.get("backfill") or {}, respect_gate=False))
        ng = [c["jid"] for c in candidates(nogate, wa_db=wa)]
        check("test-skipgrp@g.us" in ng and "test-unlisted@g.us" in ng, "candidates: respect_gate=false brings skip and unlisted groups back")
        check("test-unlisted@g.us" in [c["jid"] for c in candidates(dict(cfg, unlisted_group_policy="mention"), wa_db=wa)],
              "candidates: unlisted groups follow unlisted_group_policy")
        nodirect = [c["kind"] for c in candidates(dict(cfg, direct_policy="skip"), wa_db=wa)]
        check(nodirect and all(k == "group" for k in nodirect), "candidates: direct_policy=skip leaves only groups")
        check(policy_for(cfg, "x@g.us", "group") == "skip" and policy_for(cfg, "test-mention@g.us", "group") == "mention"
              and policy_for(cfg, "y@lid", "direct") == "all", "policy_for mirrors the doorbell (dict entry, bare string, unlisted, direct)")

        # 2 · parse_result: what a model really writes (chatter + fence + raw newline + key without @ suffix + a key outside the batch)
        raw = ('好的, 以下是这批两条对话的摘要:\n\n```json\n{\n  "test-team@g.us": "测试群 9/12 定了 3,500 的报价,\n等 Alice 9/15 回覆。",\n'
               '  "test-dan": "Dan 是学员, 9/10 付了 2,888。另有 3 条语音没转字。",\n  "test-eve@lid": "not in the batch, must not be taken"\n}\n```\n\n以上。')
        p = parse_result(raw, ["test-team@g.us", "test-dan@lid"])
        check(set(p) == {"test-team@g.us", "test-dan@lid"}, "parse: fence + chatter + key without @lid, the out of batch key ignored; got %s" % sorted(p))
        check("\n" in p.get("test-team@g.us", "") and "3,500" in p.get("test-team@g.us", ""), "parse: raw newline inside a string kept (strict=False), hard facts intact")
        p2 = parse_result('{"summaries": [{"jid": "test-alice@lid", "summary": 12345}, {"jid": "test-ghost@lid", "summary": "  "}]}',
                          ["test-alice@lid", "test-ghost@lid"])
        check(p2 == {"test-alice@lid": "12345"}, "parse: wrapper + array + numeric value becomes a string + blank summary dropped; got %s" % p2)
        p_q = parse_result('{"test-team@g.us": "对方说"明天再约"然后下线了, 周五再跟进", "test-dan@lid": "他说"ok"就走了"}',
                           ["test-team@g.us", "test-dan@lid"])
        check(len(p_q) == 2 and "明天再约" in p_q["test-team@g.us"], "unescaped double quotes inside a string are repaired (the shape a model really writes)")
        p3 = parse_result('先给个草稿 {"test-team@g.us": "短"} 再给正式的 {"test-team@g.us": "长一点的", "test-alice@lid": "x" * 3}',
                          ["test-team@g.us", "test-alice@lid"])
        check(p3.get("test-alice@lid") is None and p3.get("test-team@g.us") == "短", "parse: illegal JSON ignored, the legal one taken")
        check(len(parse_result(json.dumps({"test-alice@lid": "字" * 2000}), ["test-alice@lid"])["test-alice@lid"]) == 1500, "parse: each cut to 1500")
        check(parse_result("抱歉我读不懂", ["test-alice@lid"]) == {}, "parse: no JSON gives {}")

        # 3 · run_backfill: batch 1 (fence + chatter + key without suffix) succeeds, one row was just written by the live brain
        #     and stays; batch 2 fails to parse and goes to failed_jids; batch 3 inserts new rows
        sweeps = []

        def fake_sweep(jid, days, out_json):
            sweeps.append((jid, days, Path(out_json).name))
            body = "\n".join("- 09-%02d 10:00 某人: 讲了第 %d 句话 %d 块" % (1 + k % 28, k, k * 10) for k in range(900))
            return ("# WhatsApp 全窗阅读包 · 30 天\n\n- 媒体: 语音 3 (本机 3) · 语音转字 2 条 (快取 0, 失败 1)\n\n## 目录\n\n| 表 |\n\n"
                    "## [1] %s · 一对一\n\n- jid `%s`\n\n### 2026-09-01 (Mon)\n%s\n" % (jid, jid, body))

        calls = []

        def fake_model(prompt, run_dir):
            calls.append(prompt)
            if "`test-mention@g.us`" in prompt:
                return {"text": '这是这批两条的结果:\n\n```json\n{\n  "test-mention@g.us": "提及群 9/12 定下场地。",\n  "test-team": "测试群 9/12 报价 3,500。"\n}\n```\n', "is_error": False}
            if "`test-alice@lid`" in prompt:
                return {"text": '{"test-alice@lid": "missing the closing quote, cannot parse}', "is_error": False}
            return {"text": '{"test-ghost@lid": "Ghost 空白那行。", "test-eve@lid": "Eve 新行。"}', "is_error": False}

        def sweep_then_brain_writes(jid, days, out_json):
            if jid == "test-mention@g.us":   # while the backfill runs, the live brain judges this chat and writes a summary
                c = sqlite3.connect(str(st))
                c.execute("INSERT INTO conversations(jid,name,kind,tag,summary,updated_at) VALUES('test-mention@g.us','Mention Group','group','open','written by the live brain','2026-09-14T09:00:00')")
                c.commit()
                c.close()
            return fake_sweep(jid, days, out_json)

        after = []
        logs = []
        res = run_backfill(cfg, fake_model, sweep_then_brain_writes, batch_size=2, after_batch=lambda: after.append(1), log=logs.append, wa_db=wa)
        check(res["candidates"] == 6 and res["batches"] == 3, "run: 6 candidates in 3 batches; got %s / %s" % (res["candidates"], res["batches"]))
        check(res["failed_jids"] == ["test-alice@lid", "test-dan@lid"], "run: the unparseable batch goes to failed_jids, the rest is written; got %s" % res["failed_jids"])
        check(res["written"] == 3, "run: 3 written (team, ghost, eve; the live brain's summary kept); got %d" % res["written"])
        check(len(after) == 2, "run: after_batch only after the two written batches; got %d" % len(after))
        check(all(d == 30 for _, d, _ in sweeps) and len(sweeps) == 6, "run: every sweep uses 30 days")
        check(len(res["prompt_sizes"]) == 3 and all(pk["pack_chars"] <= 8000 for pk in res["packs"]), "run: three prompts, one to one packs <= 8000")
        grp = [pk for pk in res["packs"] if pk["kind"] == "group"][0]
        check(grp["pack_chars"] <= 6000, "run: group pack <= 6000; got %d" % grp["pack_chars"])
        check("第 899 句话" in calls[0] and "第 0 句话" not in calls[0] and "包太长" in calls[0], "trim: newest end kept, oldest cut, with a note")
        check("失败 1" in calls[0] and "## 目录" not in calls[0], "trim: media count line kept, table of contents dropped")
        check("{CONVERSATIONS}" not in calls[0] and "`test-mention@g.us` · `test-team@g.us`" in calls[0], "prompt: placeholders replaced, jid list in order")
        check("Test Owner" in calls[0] and "{OWNER_LABEL}" not in calls[0] and "{WARM_DAYS}" not in calls[0], "prompt: owner label and warm days filled from config")
        check(res["prompt_seed_fallback"] is True and any(x.startswith("WARNING backfill: owner prompt copy missing") for x in logs),
              "prompt: no owner copy -> payload seed used and a WARNING logged")
        check("<!-- seed:" not in calls[0], "prompt: the seed stamp line is not sent to the model")
        c = sqlite3.connect(str(st))
        c.row_factory = sqlite3.Row
        rows = {r["jid"]: dict(r) for r in c.execute("SELECT * FROM conversations")}
        c.close()
        check(rows["test-team@g.us"]["summary"] == "测试群 9/12 报价 3,500。" and rows["test-team@g.us"]["grounded_at"], "apply: the empty string row gets summary + grounded_at")
        check(rows["test-team@g.us"]["draft"] == "draft must stay" and rows["test-team@g.us"]["world"] == "work"
              and rows["test-team@g.us"]["updated_at"] == "2026-09-13T10:00:00", "apply: draft / world / updated_at untouched")
        check(rows["test-mention@g.us"]["summary"] == "written by the live brain" and rows["test-mention@g.us"]["grounded_at"] is None,
              "apply: the live brain's summary is not overwritten (INSERT ignored, UPDATE condition blocks)")
        check(rows["test-carol@lid"]["summary"] == "old summary", "apply: rows outside the batch untouched")
        e = rows["test-eve@lid"]
        check(e["summary"] == "Eve 新行。" and e["name"] == "Eve Test" and e["kind"] == "direct" and e["grounded_at"] and e["updated_at"],
              "apply: a new row carries name / kind / summary / grounded_at / updated_at")
        check(all(e[k] is None for k in ("world", "stage", "priority", "tag", "draft", "last_run_id")),
              "apply: a new row has no world / stage / priority / tag / draft / last_run_id")
        check(any("retried next time" in x for x in logs), "run: the failed batch is logged")

        # 4 · second call: candidates recomputed from "summary still empty", only the unfinished ones remain
        res2 = run_backfill(cfg, lambda pr, rd: {"text": '{"test-alice@lid": "Alice 重跑。", "test-dan@lid": "Dan 重跑。"}'}, fake_sweep,
                            log=logs.append, wa_db=wa)
        check(res2["candidates"] == 2 and res2["written"] == 2 and res2["failed_jids"] == [],
              "run again: 2 candidates left, both written; got %s" % {k: res2[k] for k in ("candidates", "written", "failed_jids")})
        c = sqlite3.connect(str(st))
        c.row_factory = sqlite3.Row
        d5 = dict(c.execute("SELECT * FROM conversations WHERE jid='test-dan@lid'").fetchone())
        g = dict(c.execute("SELECT * FROM conversations WHERE jid='test-ghost@lid'").fetchone())
        c.close()
        check(d5["summary"] == "Dan 重跑。" and d5["tag"] == "replied", "apply: the NULL summary row is written, tag untouched")
        check(g["summary"] == "Ghost 空白那行。" and g["world"] == "personal", "apply: a blank only summary counts as empty, world kept")
        check(candidates(cfg, wa_db=wa) == [], "no candidates left after the second run")

        # 5 · dry_run writes nothing and calls no model, prompt.md still lands in the run folder
        c = sqlite3.connect(str(st))
        c.execute("UPDATE conversations SET summary=NULL WHERE jid IN ('test-alice@lid','test-team@g.us')")
        c.commit()
        before = c.execute("SELECT jid, summary, grounded_at FROM conversations ORDER BY jid").fetchall()
        c.close()

        def boom(prompt, run_dir):
            raise AssertionError("dry_run must not call the model")
        res3 = run_backfill(cfg, boom, fake_sweep, dry_run=True, log=logs.append, wa_db=wa)
        c = sqlite3.connect(str(st))
        after_rows = c.execute("SELECT jid, summary, grounded_at FROM conversations ORDER BY jid").fetchall()
        c.close()
        check(res3["written"] == 0 and res3["failed_jids"] == [] and before == after_rows, "dry_run: nothing written, no model call")
        check(len(res3["prompt_sizes"]) == 1 and (Path(res3["run_dirs"][0]) / "prompt.md").exists(), "dry_run: prompt.md in the run folder, sizes %s" % res3["prompt_sizes"])
        check(str(Path(res3["run_dirs"][0]).resolve()).startswith(str(live.resolve())), "dry_run: run folders live under <live>/runs")

        # 6 · a raising or is_error model fails its batch; a raising sweep fails only its jid
        def bad_sweep(jid, days, out_json):
            if jid == "test-alice@lid":
                raise RuntimeError("wa_sweep exit 1")
            return fake_sweep(jid, days, out_json)
        res4 = run_backfill(cfg, lambda pr, rd: {"text": "Error: rate limited", "is_error": True}, bad_sweep, log=logs.append, wa_db=wa)
        check(sorted(res4["failed_jids"]) == ["test-alice@lid", "test-team@g.us"] and res4["written"] == 0,
              "run: the failed sweep and the is_error batch both land in failed_jids; got %s" % res4["failed_jids"])
        res5 = run_backfill(cfg, lambda pr, rd: {"text": '{"test-team@g.us": "群。"}'}, fake_sweep, limit=1, log=logs.append, wa_db=wa)
        check(res5["candidates"] == 2 and len(res5["packs"]) == 1 and res5["written"] == 1, "run: limit=1 processes one, candidates still reports the total")

        # 7 · the owner copy wins over the seed; the seed has every placeholder and follows the writing rule
        (live / "prompts").mkdir(parents=True, exist_ok=True)
        (live / "prompts" / PROMPT_NAME).write_text("OWNER COPY {N} {DAYS} {JID_LIST}\n{CONVERSATIONS}", encoding="utf-8")
        tpl, path_used, used_seed = prompt_template(cfg)
        check(not used_seed and tpl.startswith("OWNER COPY"), "prompt_template: the owner copy in <live>/prompts wins")
        seed = Path(W.payload_dir()) / "daemon" / "templates" / PROMPT_NAME
        stxt = seed.read_text(encoding="utf-8") if seed.exists() else ""
        check(all(ph in stxt for ph in ("{N}", "{DAYS}", "{JID_LIST}", "{CONVERSATIONS}", "{OWNER_LABEL}", "{WARM_DAYS}", "{MAX_IMAGES}")),
              "daemon/templates/backfill-prompt.md exists with all seven placeholders")
        check(stxt.startswith("<!-- seed: ") and "\u2014" not in stxt and "\u2013" not in stxt and " -- " not in stxt,
              "seed carries its stamp and has no em dash, en dash or double hyphen")

        # 8 · the reader script is found through paths.reader_dir (scripts folder or payload root)
        rd = tmp / "reader"
        rd.mkdir(parents=True, exist_ok=True)
        (rd / "wa_sweep.py").write_text("# fake\n", encoding="utf-8")
        check(sweep_script(cfg).resolve() == (rd / "wa_sweep.py").resolve(), "sweep_script: paths.reader_dir pointing at the scripts folder")
        root = tmp / "payload-root"
        (root / "skills" / "x-whatsapp-read" / "scripts").mkdir(parents=True, exist_ok=True)
        (root / "skills" / "x-whatsapp-read" / "scripts" / "wa_sweep.py").write_text("# fake\n", encoding="utf-8")
        cfg_root = dict(cfg, paths=dict(cfg.get("paths") or {}, reader_dir=str(root)))
        check(sweep_script(cfg_root).resolve() == (root / "skills" / "x-whatsapp-read" / "scripts" / "wa_sweep.py").resolve(),
              "sweep_script: paths.reader_dir pointing at a payload root")

        # 9 · the image cap is mechanical: at most max_images image or file paths per batch, newest first, taking turns
        def pic(k, icon="🖼️ 图片"):
            return "- 10:%02d 某人: %s → `/tmp/media/%d.jpg`" % (k, icon, k)
        a_pack = "\n".join([pic(1), pic(2), "- 10:03 某人: 🎤 语音 5s → `/tmp/media/v.opus`", pic(4), pic(5), pic(6)])
        b_pack = "\n".join([pic(7, "📎 文件"), pic(8)])
        batch = [{"jid": "a", "pack": a_pack}, {"jid": "b", "pack": b_pack}]
        kept, dropped = cap_media_paths(batch, 4)
        a_left = re.findall(r"/tmp/media/(\w+)\.", batch[0]["pack"])
        b_left = re.findall(r"/tmp/media/(\w+)\.", batch[1]["pack"])
        check(kept == 4 and dropped == 4, "cap: 7 openable paths and 1 voice path, 4 kept, 4 dropped; got %s / %s" % (kept, dropped))
        check(a_left == ["5", "6"] and b_left == ["7", "8"],
              "cap: newest first, taking turns across chats (a keeps 6 and 5, b keeps 8 and 7); got %s %s" % (a_left, b_left))
        check("v.opus" not in batch[0]["pack"] and batch[0]["pack"].count("(路径略)") == 4 and "🎤 语音 5s" in batch[0]["pack"],
              "cap: the voice path goes, its label stays, dropped lines say (路径略)")
        none_batch = [{"jid": "a", "pack": a_pack}]
        check(cap_media_paths(none_batch, 0) == (0, 6) and "/tmp/media/" not in none_batch[0]["pack"], "cap: max_images 0 drops every path")
        check(build_prompt("最多 {MAX_IMAGES} 张", [], 30, dict(cfg, backfill={"max_images": 2})) == "最多 2 张",
              "prompt: {MAX_IMAGES} filled from backfill.max_images")
        logs7 = []
        res7 = run_backfill(cfg, lambda pr, rd: {"text": "", "is_error": True, "error": "error_max_turns: Reached maximum number of turns (20)"},
                            fake_sweep, limit=1, log=logs7.append, wa_db=wa)
        check(res7["failed_jids"] and any("error_max_turns: Reached maximum number of turns" in x for x in logs7),
              "run: a model error with no text is logged with its reason, not an empty line")
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)
    print("selftest: %d/%d passed" % (total[0] - len(fails), total[0]))
    return 0 if not fails else 1


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description="Summary backfill for conversations without a summary")
    ap.add_argument("--config", default=None, help="live config.json (default: env WA_LIVE_CONFIG or ~/WhatsApp-Live/live/config.json)")
    ap.add_argument("--list", action="store_true", help="print the candidate count and the first 15 (read only)")
    ap.add_argument("--dry-run", action="store_true", help="run the reader for the first batch and build the prompt; no model, nothing written to state")
    ap.add_argument("--run", action="store_true", help="call the model and write summaries")
    ap.add_argument("--limit", type=int, default=None, help="process at most this many conversations")
    ap.add_argument("--batch-size", type=int, default=None, help="conversations per model call (default backfill.batch_size)")
    ap.add_argument("--days", type=int, default=None, help="window in days (default backfill.days)")
    ap.add_argument("--min-msgs", type=int, default=None, help="minimum content messages in the window (default backfill.min_msgs)")
    ap.add_argument("--no-transcribe", action="store_true", help="the reader skips voice transcription (to measure prompt size)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    os.umask(0o077)   # what this writes holds other people's messages: owner only, like the daemon's own files
    if not (a.list or a.dry_run or a.run):
        ap.print_help()
        return 0
    try:
        cfg = W.load_config(a.config)
    except (OSError, ValueError) as e:
        print("config: %s" % e)
        return 2
    bf = _section(cfg, "backfill")
    days = a.days if a.days is not None else int(bf.get("days", 30))
    min_msgs = a.min_msgs if a.min_msgs is not None else int(bf.get("min_msgs", 10))
    batch_size = a.batch_size if a.batch_size is not None else int(bf.get("batch_size", 10))
    if a.list:
        stats = {}
        cs = candidates(cfg, days=days, min_msgs=min_msgs, stats=stats)
        gate = "respected" if bf.get("respect_gate", True) else "ignored (backfill.respect_gate=false)"
        print("%d candidates (groups %d · one to one %d; %d days, >= %d content messages, summary empty; gate %s, %d chats kept out by the gate)" % (
            len(cs), sum(1 for c in cs if c["kind"] == "group"), sum(1 for c in cs if c["kind"] == "direct"), days, min_msgs,
            gate, stats.get("gate_skipped", 0)))
        for c in cs[:15]:
            print("  %4d  %-6s  %s  (%s)" % (c["n"], "group" if c["kind"] == "group" else "direct", c["name"], c["jid"]))
        return 0
    if a.dry_run:
        t0 = time.time()
        res = run_backfill(cfg, None, default_sweep_fn(cfg, transcribe=not a.no_transcribe), days=days, min_msgs=min_msgs,
                           limit=a.limit or batch_size, batch_size=batch_size, dry_run=True)
        for pk in res["packs"]:
            print("  b%d  %-6s  %4d msgs  raw pack %6d chars -> fed %5d chars  reader %5.1fs  %s" % (
                pk["batch"], "group" if pk["kind"] == "group" else "direct", pk["n"], pk["raw_chars"], pk["pack_chars"], pk["sweep_s"], pk["name"]))
        print("%d candidates · %d this time · prompt %s chars · reader failures %d · %.0fs · run_dir %s" % (
            res["candidates"], len(res["packs"]), res["prompt_sizes"], len(res["failed_jids"]), time.time() - t0, res["run_dirs"]))
        return 0
    try:
        import wa_live   # only imported when the model is really called
        state = wa_live.State(str(state_db_path(cfg)))
        brain = wa_live.Brain(cfg, state, None)
    except Exception as e:
        print("--run needs the daemon module (wa_live.State and wa_live.Brain): %s" % e)
        return 1
    timeout = max(int(_section(cfg, "brain").get("run_timeout_seconds", 600)), 900)   # a batch of ten packs is far bigger than one run
    turns = int(bf.get("max_turns", 20))

    def call(prompt, run_dir):
        return brain.call_claude(prompt, run_dir, timeout=timeout, max_turns=turns)
    res = run_backfill(cfg, call, default_sweep_fn(cfg, transcribe=not a.no_transcribe), days=days, min_msgs=min_msgs, limit=a.limit,
                       batch_size=batch_size, after_batch=lambda: state.write_summaries(Path(W.live_dir(cfg)) / "summaries.md"))
    print(json.dumps({k: res[k] for k in ("candidates", "batches", "written", "failed_jids", "prompt_sizes")}, ensure_ascii=False))
    return 0 if not res["failed_jids"] else 2


if __name__ == "__main__":
    sys.exit(main())
