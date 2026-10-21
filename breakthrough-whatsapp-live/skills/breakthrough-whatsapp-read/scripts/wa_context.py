#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wa_context.py · second-brain lookup (breakthrough-whatsapp-live, companion skill breakthrough-whatsapp-read)

For every chat in a wa_sweep reading pack, lists the notes in the owner's vault that may be about that person,
group or matter, so a session opens and reads them before judging instead of guessing from memory. It finds and
never judges: a hit is not proof of relevance, and a chat with no hits says so plainly. Read only.

What it indexes:
  - every .md file under the vault, or only under index_dirs when that list is set;
  - minus exclude_dirs (default 98_Archive/, 99_Meta/, .obsidian/, .trash/) and anything inside node_modules;
  - per note: the file name, the first "# " heading, the full body, and these frontmatter keys: wa_jid and
    wa_phone (hard match), wa_name and aliases (name match), type or cb and stage or status (only shown).

How it scores (per note, summed): wa_jid equals the chat or a speaker 100 · a phone matches wa_jid or wa_phone
100 · the phone appears in the body 30 · wa_name equals a full name 80 · file name or title is the full name 60 ·
an alias equals it 60 · file name or title contains it 45 · the body mentions it 25 · a single word in the file
name or title 20 · in the body 6 · group name and file name contain each other 50. Notes under low_priority_dirs
(default 00_Inbox/, 01_Daily/) count 0.4, folder guides (_*.md) 0.5. When nothing scores 20 or more, weak hits
under 10 are kept only from person_dirs. ⭐ marks 60 or more. The owner's own names are never used as keys.

Config (reader config, --config; default <sweep folder>/../config.json, then $WA_LIVE_CONFIG): my_names,
vault (or paths.vault; used when --vault is absent), person_dirs (default: people_dir or paths.people_dir),
index_dirs, exclude_dirs, low_priority_dirs, stop_words (added to a short generic list). Any of these may also
sit under a "context" object. Folders are vault-relative (absolute paths inside the vault are accepted).

Outputs: context-<...>.json and .md beside the sweep (or --out plus the same name with .md), and one JSON line on
stdout. Exit codes: 0 ok · 2 sweep or --config unreadable · 3 no vault folder.
"""

import argparse
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path

VERSION = "1.0.0"
DEFAULT_EXCLUDE = ("98_Archive/", "99_Meta/", ".obsidian/", ".trash/")
DEFAULT_LOW_PRIORITY = ("00_Inbox/", "01_Daily/")
# Words that say nothing about who someone is when they stand alone: forms of address, filler, months,
# company suffixes. The owner's brand and company words go in the config's stop_words.
GENERIC_STOP = {
    "the", "and", "you", "me", "my", "our", "bro", "boss", "sir", "miss", "mr", "mrs", "ms", "dr", "madam",
    "team", "group", "main", "project", "general", "family", "official", "chat", "admin", "info", "new", "plan",
    "day", "week", "month", "vol", "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept", "oct",
    "nov", "dec", "ltd", "llc", "inc", "sdn", "bhd", "company", "enterprise", "management", "production",
    "services", "solutions", "trading", "global", "international",
}
CJK = re.compile(r"[一-鿿]{2,}")
LATIN = re.compile(r"[A-Za-z][A-Za-z.'\-]{1,}")
BIDI = re.compile(r"[‎‏‪-‮⁦-⁩ ]")


def norm(text):
    text = BIDI.sub(" ", text or "")
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def alnum(text):
    return re.sub(r"[^0-9a-z一-鿿]", "", norm(text))


def digits(text):
    return re.sub(r"\D", "", str(text or ""))


def read_frontmatter(text):
    """Frontmatter as {key: str or list}; understands `key: value`, `key: [a, b]` and indented `- item` lists."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    data, cur = {}, None
    for line in text[3:end].splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            cur = m.group(1)
            v = m.group(2).strip()
            data[cur] = v if v.startswith("[") else v.strip('"').strip("'")
            continue
        b = re.match(r"^\s+-\s+(.*)$", line)
        if b and cur:
            prev = data.get(cur)
            items = prev if isinstance(prev, list) else ([prev] if prev else [])
            items.append(b.group(1).strip().strip('"').strip("'"))
            data[cur] = items
    return data, text[end + 4:]


def fm_values(fm, key):
    v = fm.get(key)
    if isinstance(v, list):
        return [x for x in v if x]
    v = (v or "").strip()
    if not v:
        return []
    if v.startswith("["):
        return [x.strip().strip('"').strip("'") for x in v.strip("[]").split(",") if x.strip().strip('"').strip("'")]
    return [v]


# ---------------------------------------------------------------- config

def cfg_get(cfg, key, default=None):
    if key in cfg and cfg[key] not in (None, ""):
        return cfg[key]
    ctx = cfg.get("context") if isinstance(cfg.get("context"), dict) else {}
    if key in ctx and ctx[key] not in (None, ""):
        return ctx[key]
    return default


def load_reader_config(explicit, sweep_path):
    """-> (cfg dict, path used or None, error or None). An explicit --config must be readable."""
    if explicit:
        p = Path(explicit).expanduser()
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(cfg, dict):
                raise ValueError("the top level is not a JSON object")
            return cfg, p, None
        except Exception as e:
            return {}, p, "cannot read --config %s: %s" % (p, e)
    cands = [sweep_path.parent.parent / "config.json", sweep_path.parent / "config.json"]
    if os.environ.get("WA_LIVE_CONFIG"):
        cands.append(Path(os.environ["WA_LIVE_CONFIG"]).expanduser())
    for p in cands:
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(cfg, dict):
                return cfg, p, None
        except Exception:
            continue
    return {}, None, None


def vault_from(cfg):
    for v in (cfg_get(cfg, "vault"), (cfg.get("paths") or {}).get("vault") if isinstance(cfg.get("paths"), dict) else None):
        if isinstance(v, str) and v.strip() and "<" not in v:
            return Path(v).expanduser()
    return None


def rel_dirs(values, vault):
    out = []
    for v in values or []:
        if not isinstance(v, str) or not v.strip() or "<" in v:
            continue
        p = v.strip()
        if os.path.isabs(os.path.expanduser(p)):
            try:
                p = Path(os.path.expanduser(p)).resolve().relative_to(vault.resolve()).as_posix()
            except ValueError:
                continue
        p = p.strip("/")
        if p:
            out.append(p + "/")
    return tuple(out)


def people_dir_of(cfg):
    v = cfg_get(cfg, "people_dir") or ((cfg.get("paths") or {}).get("people_dir") if isinstance(cfg.get("paths"), dict) else None)
    return [v] if isinstance(v, str) and v.strip() else []


# ---------------------------------------------------------------- index

class Index:
    def __init__(self, vault, index_dirs=(), exclude_dirs=DEFAULT_EXCLUDE, low_dirs=DEFAULT_LOW_PRIORITY):
        self.vault = vault
        self.notes = []
        self.by_jid = {}
        self.by_phone = defaultdict(list)
        self.by_alnum_title = defaultdict(list)
        for p in sorted(vault.rglob("*.md")):
            rel = p.relative_to(vault).as_posix()
            if index_dirs and not rel.startswith(index_dirs):
                continue
            if rel.startswith(exclude_dirs) or "node_modules/" in rel:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            fm, body = read_frontmatter(text)
            m = re.search(r"^#\s+(.+)$", body, re.M)
            title = (m.group(1).strip() if m else p.stem)
            note = {
                "rel": rel, "stem": p.stem, "title": title, "fm": fm,
                "low": rel.startswith(low_dirs) if low_dirs else False,
                "is_guide": p.stem.startswith("_"),
                "lines": body.splitlines(),
                "body_lower": norm(body),
                "stem_lower": norm(p.stem.replace("-", " ")),
                "title_lower": norm(title),
                "aliases": [norm(a) for a in fm_values(fm, "aliases")],
            }
            self.notes.append(note)
            for j in fm_values(fm, "wa_jid"):
                self.by_jid[j.strip()] = note
                head = j.split("@")[0]
                if head.isdigit():
                    self.by_phone[head].append(note)
            for ph in fm_values(fm, "wa_phone"):
                if digits(ph):
                    self.by_phone[digits(ph)].append(note)
            self.by_alnum_title[alnum(p.stem)].append(note)
            self.by_alnum_title[alnum(title)].append(note)
        self.count = len(self.notes)

    def find_lines(self, note, needle, limit=2):
        out = []
        nl = needle.lower()
        for i, line in enumerate(note["lines"]):
            if nl in norm(line):
                s = line.strip()
                if len(s) > 160:
                    k = norm(s).find(nl)
                    s = s[max(0, k - 60): k + 100].strip()
                out.append("L%d: %s" % (i + 1, s))
                if len(out) >= limit:
                    break
        return out


# ---------------------------------------------------------------- keys and scoring

def name_keys(raw, my_names, stop=GENERIC_STOP):
    """One display name -> (strong keys: the full name, weak keys: single words)."""
    own = set(n.lower() for n in my_names if n)
    raw = norm(raw)
    raw = re.sub(r"\(.*?\)|【.*?】|\[.*?\]|｜.*$", " ", raw).strip()
    raw = re.sub(r"[^\w\s一-鿿@.'\-]", " ", raw).strip()
    if not raw or raw in own:
        return [], []
    if re.fullmatch(r"[+\d\s\-()]+", raw):
        return [], []
    strong, weak = [], []
    if len(alnum(raw)) >= 3:
        strong.append(raw)
    for w in LATIN.findall(raw):
        w = w.strip(".'-").lower()
        if len(w) >= 3 and w not in stop and w not in own:
            weak.append(w)
    for w in CJK.findall(raw):
        if w not in own and w not in stop:
            weak.append(w)
    weak = [w for w in dict.fromkeys(weak) if w != raw]
    return strong, weak


def lookup(index, chat, my_names, max_hits, stop=GENERIC_STOP, person_dirs=()):
    scores = defaultdict(lambda: {"score": 0, "why": [], "lines": []})

    def hit(note, score, why, needle=None):
        if note["is_guide"]:
            score = score * 0.5
        if note["low"]:
            score = score * 0.4
        e = scores[note["rel"]]
        e["score"] += score
        if why not in e["why"]:
            e["why"].append(why)
        if needle and len(e["lines"]) < 2:
            for ln in index.find_lines(note, needle):
                if ln not in e["lines"] and len(e["lines"]) < 2:
                    e["lines"].append(ln)
        e["note"] = note

    # 1 hard matches: jid and phone
    jids = [chat.get("jid")] + [p.get("jid") for p in chat.get("participants", []) if p.get("jid")]
    phones = [chat.get("phone")] + [p.get("phone") for p in chat.get("participants", []) if p.get("phone")]
    for j in jids:
        if j and j in index.by_jid:
            hit(index.by_jid[j], 100, "档头 wa_jid 就是这个对话")
    for ph in [digits(p) for p in phones if p]:
        if not ph:
            continue
        seen_notes = set()
        for note in index.by_phone.get(ph, []):
            if note["rel"] not in seen_notes:
                seen_notes.add(note["rel"])
                hit(note, 100, "档头 wa_jid / wa_phone 的号码对上 +%s" % ph)
        for note in index.notes:
            if ph in note["body_lower"]:
                hit(note, 30, "正文出现号码 +%s" % ph, ph)

    # 2 names: the chat name plus the speakers
    names = [chat.get("name") or ""]
    if chat.get("kind") == "direct":
        for extra in re.findall(r"\((?:自称|WA 显示)\s*([^,)]+)", chat.get("name") or ""):
            names.append(extra)
    else:
        for p in chat.get("participants", [])[:12]:
            names.append(p.get("name") or "")
    seen = set()
    for raw in names:
        strong, weak = name_keys(raw, my_names, stop)
        for key in strong:
            if key in seen:
                continue
            seen.add(key)
            for note in index.by_alnum_title.get(alnum(key), []):
                hit(note, 60, "档名或标题就是「%s」" % key)
            for note in index.notes:
                if key in note["stem_lower"] or key in note["title_lower"]:
                    hit(note, 45, "档名或标题含「%s」" % key)
                elif key in note["body_lower"]:
                    hit(note, 25, "正文提到「%s」" % key, key)
                for wn in fm_values(note["fm"], "wa_name"):
                    if norm(wn) == key:
                        hit(note, 80, "档头 wa_name 就是「%s」" % key)
                if key in note["aliases"]:
                    hit(note, 60, "档头 aliases 有「%s」" % key)
        for key in weak:
            if key in seen:
                continue
            seen.add(key)
            rx = re.compile(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(key))
            for note in index.notes:
                if rx.search(note["stem_lower"]) or rx.search(note["title_lower"]):
                    hit(note, 20, "档名或标题含「%s」" % key)
                elif rx.search(note["body_lower"]):
                    hit(note, 6, "正文有「%s」" % key, key)

    # 3 group name against project or event note names (symbols removed, either contains the other)
    if chat.get("kind") == "group":
        g = alnum(chat.get("name") or "")
        if len(g) >= 6:
            for note in index.notes:
                st = alnum(note["stem"])
                if len(st) >= 6 and (g in st or st in g):
                    hit(note, 50, "群名跟档名对得上")

    ranked = sorted(scores.values(), key=lambda e: -e["score"])
    # single-word hits are noisy: with a real hit in hand keep 10 and up; otherwise keep 10 and up or notes in person_dirs
    if ranked and ranked[0]["score"] >= 20:
        ranked = [e for e in ranked if e["score"] >= 10]
    else:
        ranked = [e for e in ranked if e["score"] >= 10 or (person_dirs and e["note"]["rel"].startswith(person_dirs))]
    out = []
    for e in ranked[:max_hits]:
        n = e["note"]
        out.append({
            "path": n["rel"], "title": n["title"], "type": n["fm"].get("type") or n["fm"].get("cb"),
            "status": n["fm"].get("stage") or n["fm"].get("status"), "score": round(e["score"], 1), "why": e["why"], "lines": e["lines"],
        })
    hard = [h for h in out if h["score"] >= 60]
    return out, hard


# ---------------------------------------------------------------- output

def render_md(doc):
    a = [
        "# 第二大脑查找 · 对应 %s" % Path(doc["sweep"]).name,
        "",
        "- vault `%s` · 索引 %d 份档 · 产生 %s · wa_context.py %s" % (doc["vault"], doc["indexed"], doc["generated_at"][:16].replace("T", " "), VERSION),
        "- 硬配 (档头 wa_jid / wa_phone / wa_name 或档名就是这个名字) %d 个对话 · 有任何命中 %d 个 · 零命中 %d 个" % (
            doc["summary"]["hard_chats"], doc["summary"]["any_hit_chats"], doc["summary"]["no_hit_chats"]),
    ]
    for w in doc.get("warnings") or []:
        a.append("- ⚠️ " + w)
    a += ["", "⛔ 命中不等于相关: 打开档读过才算引用。零命中的对话, 就写「第二大脑没有记录」, 不补。", ""]
    for i, c in enumerate(doc["chats"], 1):
        a.append("## [%d] %s" % (i, c["name"]))
        if not c["hits"]:
            a.append("- (零命中)")
            a.append("")
            continue
        for h in c["hits"]:
            tag = "⭐ " if h["score"] >= 60 else ""
            meta = " · ".join(x for x in (h.get("type"), h.get("status")) if x)
            a.append("- %s`%s`%s · %s (分 %s)" % (tag, h["path"], (" (%s)" % meta) if meta else "", "; ".join(h["why"]), h["score"]))
            for ln in h["lines"]:
                a.append("    - %s" % ln.replace("\n", " "))
        a.append("")
    return "\n".join(a) + "\n"


def write_private(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(".%s.tmp-%d" % (path.name, os.getpid()))
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(str(tmp), 0o600)
    os.replace(str(tmp), str(path))


def build_parser():
    p = argparse.ArgumentParser(
        prog="wa_context.py",
        description="For each chat in a wa_sweep JSON, list the vault notes that may be about it (hard matches on "
                    "wa_jid and wa_phone, then names, then group names). Read only; it finds and never judges.",
        epilog="Exit codes: 0 ok, 2 sweep or --config unreadable, 3 no vault folder. See the module docstring for what is indexed and how hits score.")
    p.add_argument("--sweep", required=True, help="the JSON written by wa_sweep.py")
    p.add_argument("--vault", default=None, help="vault root (default: vault or paths.vault from the config)")
    p.add_argument("--config", default=None, help="reader config with my_names, vault, person_dirs, index_dirs, stop_words "
                                                  "(default: <sweep folder>/../config.json, then $WA_LIVE_CONFIG)")
    p.add_argument("--max-hits", type=int, default=8, help="hits listed per chat (default 8)")
    p.add_argument("--out", default=None, help="output JSON path (the md gets the same name); default context-<...>.json beside the sweep")
    p.add_argument("--selftest", action="store_true", help="run the built-in test on a fake vault in a temp dir and exit")
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        return selftest()
    args = build_parser().parse_args(argv)
    os.umask(0o077)   # what this writes holds other people's messages: owner only, like the daemon's own files
    sweep_path = Path(args.sweep).expanduser()
    try:
        sweep = json.loads(sweep_path.read_text(encoding="utf-8"))
        chats = list(sweep["chats"])
    except Exception as e:
        sys.stderr.write("wa_context: cannot read the sweep: %s\n" % e)
        return 2
    cfg, cfg_path, cfg_err = load_reader_config(args.config, sweep_path)
    if cfg_err:
        sys.stderr.write("wa_context: %s\n" % cfg_err)
        return 2
    vault = Path(args.vault).expanduser() if args.vault else vault_from(cfg)
    if vault is None:
        sys.stderr.write("wa_context: no vault given: pass --vault or set vault (or paths.vault) in the config\n")
        return 3
    if not vault.is_dir():
        sys.stderr.write("wa_context: vault folder not found: %s\n" % vault)
        return 3

    warnings = []
    my_names = [n.strip() for n in (cfg_get(cfg, "my_names") or []) if isinstance(n, str) and n.strip()]
    if not my_names:
        warnings.append("config 没有 my_names: 主人自己的名字不会被过滤, 可能撞出一堆假命中")
    stop = set(GENERIC_STOP) | set(w.strip().lower() for w in (cfg_get(cfg, "stop_words") or []) if isinstance(w, str) and w.strip())
    person_dirs = rel_dirs(list(cfg_get(cfg, "person_dirs") or []) + people_dir_of(cfg), vault)
    index_dirs = rel_dirs(cfg_get(cfg, "index_dirs") or [], vault)
    ex = cfg_get(cfg, "exclude_dirs")
    exclude_dirs = rel_dirs(ex, vault) if isinstance(ex, list) else DEFAULT_EXCLUDE
    lo = cfg_get(cfg, "low_priority_dirs")
    low_dirs = rel_dirs(lo, vault) if isinstance(lo, list) else DEFAULT_LOW_PRIORITY

    index = Index(vault, index_dirs, exclude_dirs, low_dirs)
    chats_out = []
    hard_n = any_n = none_n = 0
    for c in chats:
        hits, hard = lookup(index, c, my_names, args.max_hits, stop, person_dirs)
        if hard:
            hard_n += 1
        if hits:
            any_n += 1
        else:
            none_n += 1
        chats_out.append({"jid": c.get("jid"), "name": c.get("name"), "kind": c.get("kind"), "hits": hits, "hard": bool(hard)})

    doc = {
        "schema": "wa-context/1", "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "sweep": str(sweep_path), "vault": str(vault), "config": str(cfg_path) if cfg_path else None, "indexed": index.count,
        "summary": {"chats": len(chats_out), "hard_chats": hard_n, "any_hit_chats": any_n, "no_hit_chats": none_n},
        "warnings": warnings,
        "chats": chats_out,
    }
    out_json = Path(args.out).expanduser() if args.out else sweep_path.with_name(
        sweep_path.name.replace("sweep-", "context-", 1) if sweep_path.name.startswith("sweep-") else "context-" + sweep_path.name)
    out_md = out_json.with_suffix(".md")
    try:
        write_private(out_json, json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
        write_private(out_md, render_md(doc))
    except Exception as e:
        sys.stderr.write("wa_context: cannot write the outputs: %s\n" % e)
        return 4
    print(json.dumps({"ok": True, "json": str(out_json), "md": str(out_md), "indexed": index.count,
                      "chats": len(chats_out), "hard": hard_n, "any": any_n, "none": none_n, "warnings": len(warnings)}, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------- selftest (fake vault, fake sweep, temp dir only)

def selftest():
    import contextlib
    import io
    import tempfile
    results = []

    def check(name, ok, detail=""):
        results.append(bool(ok))
        print(("  ok    " if ok else "  FAIL  ") + name + ("" if ok else "   (%s)" % detail))

    def run(argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            try:
                code = main(argv)
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
        return code

    def note(vault, rel, text):
        p = vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    saved = os.environ.pop("WA_LIVE_CONFIG", None)
    try:
        with tempfile.TemporaryDirectory(prefix="wa-context-selftest.") as td:
            td = Path(td)
            vault = td / "vault"
            note(vault, "People/Aiko-Tan.md", "---\ntype: person\nroles: [lead]\nwa_jid: 15550100001@s.whatsapp.net\n---\n\n# Aiko Tan\n\nAsked about the Saturday class.\n")
            note(vault, "People/Quinn Hale.md", "---\ntype: person\nwa_phone: \"+1 555 0100 002\"\n---\n\n# Quinn Hale\n")
            note(vault, "People/Cara-Chen.md", "---\ntype: person\naliases:\n  - Cara C\n---\n\n# Cara Chen\n")
            note(vault, "Work/Projects/Weekend-Hikers-Trip.md", "# Weekend Hikers trip\n\nRoute planning.\n")
            note(vault, "01_Daily/2026-03-09.md", "# 2026-03-09\n\nMet Aiko Tan at the cafe.\n")
            note(vault, "99_Meta/filing-log.md", "- Aiko Tan note filed\n")
            note(vault, "Notes/Mika-diary.md", "# Mika diary\n\nMika wrote this.\n")
            note(vault, "Notes/Hikers-gear.md", "# Hikers gear list\n")
            note(vault, "People/Quentin.md", "# Quentin\n\nA jack of all trades.\n")
            note(vault, "Notes/Random.md", "# Random\n\nThe jack is broken.\n")
            sweep = {"schema": "wa-sweep/1", "chats": [
                {"jid": "15550100001@s.whatsapp.net", "kind": "direct", "name": "Aiko Tan (WA 显示 Aiko)", "phone": "15550100001", "participants": []},
                {"jid": "15550100000-1700000000@g.us", "kind": "group", "name": "Weekend Hikers", "phone": None, "participants": [
                    {"name": "Quinn Hale", "source": "通讯录", "phone": "15550100002", "jid": "222@lid", "n": 1},
                    {"name": "Cara C", "source": "自称", "phone": "15550100003", "jid": "15550100003@s.whatsapp.net", "n": 1},
                    {"name": "Mika", "source": "群内名", "phone": None, "jid": "444@lid", "n": 1}]},
                {"jid": "999@lid", "kind": "direct", "name": "Nobody Known", "phone": None, "participants": []},
            ]}
            work = td / "work"
            (work / "sweeps").mkdir(parents=True)
            sweep_path = work / "sweeps" / "sweep-2026-03-10-3d.json"
            sweep_path.write_text(json.dumps(sweep, ensure_ascii=False), encoding="utf-8")
            (work / "config.json").write_text(json.dumps({"my_names": ["Mika"], "vault": str(vault), "person_dirs": ["People"],
                                                          "stop_words": ["hikers"]}), encoding="utf-8")

            code = run(["--sweep", str(sweep_path)])
            out = work / "sweeps" / "context-2026-03-10-3d.json"
            check("runs with the config found beside the sweep and the vault taken from it", code == 0 and out.exists(), code)
            doc = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {"chats": [], "indexed": 0}
            by = dict((c["jid"], c) for c in doc["chats"])
            a = by.get("15550100001@s.whatsapp.net", {}).get("hits", [])
            check("wa_jid hard match ranks first with 100 or more", a and a[0]["path"] == "People/Aiko-Tan.md" and a[0]["score"] >= 100, a[:1])
            daily = [h for h in a if h["path"].startswith("01_Daily/")]
            check("a daily note hit is kept but discounted", daily and daily[0]["score"] < 25, daily)
            check("excluded 99_Meta is not indexed", all(not h["path"].startswith("99_Meta/") for h in a)
                  and doc["indexed"] == 9, doc["indexed"])
            g = by.get("15550100000-1700000000@g.us", {}).get("hits", [])
            paths = [h["path"] for h in g]
            check("wa_phone matches a speaker's phone", any(h["path"] == "People/Quinn Hale.md" and h["score"] >= 100 for h in g), g)
            check("aliases match a speaker name", any(h["path"] == "People/Cara-Chen.md" and "aliases" in "; ".join(h["why"]) for h in g), g)
            check("group name matches a project note", "Work/Projects/Weekend-Hikers-Trip.md" in paths, paths)
            check("the owner's own name is never a key", "Notes/Mika-diary.md" not in paths, paths)
            check("config stop_words keep single-word noise out", "Notes/Hikers-gear.md" not in paths, paths)
            check("a chat with no match reports zero hits", by.get("999@lid", {}).get("hits") == [], by.get("999@lid"))
            md = out.with_suffix(".md").read_text(encoding="utf-8") if out.exists() else ""
            check("markdown marks hard hits with a star and lists zero-hit chats", "⭐ `People/Aiko-Tan.md`" in md and "(零命中)" in md)

            unit = name_keys("Mika", ["Mika"]) == ([], []) and name_keys("+60 12-345 6789", []) == ([], [])
            check("name_keys drops the owner's names and bare phone numbers", unit)

            weak_only = {"chats": [{"jid": "888@lid", "kind": "direct", "name": "Lumber Jack", "phone": None, "participants": []}]}
            wpath = td / "weak.json"
            wpath.write_text(json.dumps(weak_only), encoding="utf-8")
            cfg2 = td / "reader.json"
            cfg2.write_text(json.dumps({"my_names": ["Mika"], "paths": {"people_dir": str(vault / "People")}}), encoding="utf-8")
            code = run(["--sweep", str(wpath), "--config", str(cfg2), "--vault", str(vault), "--out", str(td / "weak-out.json")])
            wdoc = json.loads((td / "weak-out.json").read_text(encoding="utf-8")) if code == 0 else {"chats": [{}]}
            check("explicit --config and --vault; weak-only hits survive only inside the people dir (absolute paths.people_dir)",
                  code == 0 and [h["path"] for h in wdoc["chats"][0].get("hits", [])] == ["People/Quentin.md"], wdoc["chats"][0])
            cfg3 = td / "reader3.json"
            cfg3.write_text(json.dumps({"my_names": ["Mika"], "person_dirs": ["Elsewhere"]}), encoding="utf-8")
            code = run(["--sweep", str(wpath), "--config", str(cfg3), "--vault", str(vault), "--out", str(td / "weak-out3.json")])
            wdoc3 = json.loads((td / "weak-out3.json").read_text(encoding="utf-8")) if code == 0 else {"chats": [{"hits": None}]}
            check("weak-only hits outside person_dirs are dropped", code == 0 and wdoc3["chats"][0].get("hits") == [], wdoc3["chats"][0])

            code = run(["--sweep", str(td / "missing.json"), "--vault", str(vault)])
            check("unreadable sweep exits 2", code == 2, code)
            code = run(["--sweep", str(sweep_path), "--config", str(td / "nope.json")])
            check("unreadable --config exits 2", code == 2, code)
            code = run(["--sweep", str(wpath), "--config", str(cfg2)])
            check("no vault anywhere exits 3", code == 3, code)
            code = run(["--sweep", str(wpath), "--vault", str(td / "no-vault")])
            check("missing vault folder exits 3", code == 3, code)
    finally:
        if saved is not None:
            os.environ["WA_LIVE_CONFIG"] = saved
    print("wa_context selftest: %d/%d ok" % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
