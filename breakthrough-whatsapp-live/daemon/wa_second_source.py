#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wa_second_source.py · the second message source: once a night, yesterday's messages from another place the owner talks
(a team chat tool) become one line of news on the person notes that carry that source's id, plus captures into the
intake buffer. Nothing goes on the board, nothing is ever sent.

The interface and the nightly runner live here; each source is an implementation module in adapters/ (see SOURCES).
- enabled(cfg)                                     second_source.enabled and a source other than none
- fetch_day(cfg, day=None)                         -> {day, source, chats: [{chat_id, title, kind, world_guess,
                                                   messages: [{at, sender_id, sender_name, is_owner, text}]}], n_chats,
                                                   n_msgs, errors, fatal, skipped, owner_ids, pullable, failed_chats}
- run_nightly(cfg, call, append_recent, append_captures, day=None)   one model call per night at most, then writes
- log_line(summary)                                the daemon's one log line
- members(cfg, chat_ids=None)                      optional capability of an implementation (for the id mapping)

Rules (one rule, one function):
- chat_skip_reason: only whitelist entries with pull true are pulled; config skip ids and built in helper chats never.
  A missing or unreadable whitelist, or one with nothing to pull, is an error (ok=false), never a silent zero.
- world_for_chat: second_source.world_by_chat > the whitelist entry's world > second_source.world_keywords in the
  title > other. Only a machine guess on the intake entry; the owner files it.
- No message from anyone but the owner that day: no prompt, no model call.
- clean_result: people must be senders who really spoke that day (never the owner); a capture quote must be found in
  that chat's messages by wa_intake.resolve_quote (the same rule the WhatsApp intake uses: exact, excerpt, stitched or
  near; a paraphrase is dropped) and the capture keeps the stored text it resolved to; kind, text and quote legality is
  wa_intake.validate_captures.
- source_id_index: a person note is found by its source id key (second_source.id_key, default the implementation's
  ID_KEY) only when exactly one note carries that id; an id on two notes is reported in the summary errors and the
  log, and neither note is written. A missing people dir is an error, not an empty index.
- apply_result: the news line goes through the daemon's append_recent (it owns the recent block); captures go through
  wa_intake.append_captures two at a time with source=<source> and messages= that chat's messages (so the entries carry
  the same quote marks); intake.enabled=false appends nothing.
- write_last: <live>/second-source-last.json for the morning digest health line.
- The prompt is the owner copy <live>/prompts/second-source-prompt.md, else the payload seed with a WARNING.

Python 3.9 standard library only (the daemon runs on /usr/bin/python3). This module never calls a model itself (the
daemon passes `call`) and writes only second-source-last.json.
"""

import argparse
import datetime as dt
import importlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wa_common as W  # noqa: E402
import wa_backfill as WBF  # noqa: E402  (prompt_template, state_db_path, owner_label: one function per rule)

VERSION = "1.2.0"

SOURCES = {"lark": "adapters.second_source_lark"}
NONE = "none"
LAST_FILE = "second-source-last.json"
WHITELIST_FILE = "second-source-whitelist.json"
PROMPT_NAME = "second-source-prompt.md"
PROMPT_MAX_CHARS = 60000
LINE_MAX = 90
MAX_CAPTURES = 6
CAPTURES_PER_CALL = 2          # wa_intake.validate_captures keeps at most two per call
MAX_LAST_ERRORS = 20


# ---------------------------------------------------------------- config

def _ss(cfg):
    v = (cfg or {}).get("second_source")
    return v if isinstance(v, dict) else {}


def source_name(cfg):
    return str(_ss(cfg).get("source") or NONE).strip().lower() or NONE


def enabled(cfg):
    return bool(_ss(cfg).get("enabled")) and source_name(cfg) != NONE


def implementation(name_or_cfg):
    """The implementation module of a source name (or of cfg's source). ValueError for none or an unknown source."""
    cfg = name_or_cfg if isinstance(name_or_cfg, dict) else None
    name = source_name(name_or_cfg) if cfg is not None else str(name_or_cfg or NONE).strip().lower()
    if cfg is not None and name and name != NONE:
        import adapters as A   # the owner's own <live>/adapters/second_source_<name>.py wins and survives kit updates
        own = A.owner_module(cfg, "second_source_" + name)
        if own is not None:
            return own
    mod = SOURCES.get(name)
    if not mod:
        raise ValueError("unknown second source: %s (use one of: none, %s, or your own adapters/second_source_%s.py in the live folder)" % (name, ", ".join(sorted(SOURCES)), name))
    return importlib.import_module(mod)


def _impl_attr(cfg, attr, default=None):
    try:
        return getattr(implementation(cfg), attr, default)
    except Exception:
        return default


def opt(cfg, key, default=None):
    """second_source.<source>.<key>, else second_source.<key>, else default (None and '' count as unset)."""
    ss = _ss(cfg)
    sub = ss.get(source_name(cfg))
    if isinstance(sub, dict) and sub.get(key) not in (None, ""):
        return sub[key]
    if ss.get(key) not in (None, ""):
        return ss[key]
    return default


def _live(cfg):
    return Path(W.live_dir(cfg))


def _expand(cfg, value):
    s = str(value or "")
    if s.startswith("<live>"):
        s = str(_live(cfg)) + s[len("<live>"):]
    return Path(s).expanduser()


def whitelist_path(cfg):
    v = opt(cfg, "whitelist")
    return _expand(cfg, v) if v else _live(cfg) / WHITELIST_FILE


def last_path(cfg):
    return _live(cfg) / LAST_FILE


def id_key(cfg):
    return str(_ss(cfg).get("id_key") or _impl_attr(cfg, "ID_KEY", "") or "").strip()


def owner_tag(cfg):
    return str((cfg or {}).get("owner_marker") or "").strip() or "ME"


def world_keys(cfg):
    worlds = None
    try:
        v = W.vocab(cfg)
        if isinstance(v, dict):
            worlds = v.get("worlds")
    except Exception:
        worlds = None
    if worlds is None:
        worlds = ((cfg or {}).get("vocab") or {}).get("worlds")
    keys = []
    for w in worlds or []:
        if isinstance(w, dict) and w.get("key"):
            keys.append(str(w["key"]))
        elif isinstance(w, str):
            keys.append(w)
    return keys


def intake_buffer(cfg):
    it = (cfg or {}).get("intake") or {}
    return str(it.get("buffer") or ((cfg or {}).get("paths") or {}).get("intake_buffer") or "").strip()


def intake_enabled(cfg):
    return bool(((cfg or {}).get("intake") or {}).get("enabled", False))


# ---------------------------------------------------------------- 1 · which chats

def read_whitelist(cfg):
    """(chats, error). The file: {"chats": [{"chat_id", "name" or "title", "kind", "pull", "world"?}], "rule", "decided_at"}
    or a bare list. A missing file, bad JSON or no chats list is an error string."""
    p = whitelist_path(cfg)
    if not p.exists():
        return [], "whitelist not found: %s" % p
    try:
        j = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return [], "whitelist unreadable: %s (%s)" % (p, str(e)[:120])
    chats = j.get("chats") if isinstance(j, dict) else j
    if not isinstance(chats, list):
        return [], "whitelist has no chats list: %s" % p
    return [c for c in chats if isinstance(c, dict) and c.get("chat_id")], None


def load_whitelist(cfg):
    chats, err = read_whitelist(cfg)
    if err:
        raise ValueError(err)
    return chats


def _title(chat):
    return str(chat.get("title") or chat.get("name") or chat.get("chat_id") or "")


def chat_skip_reason(chat, cfg):
    """The one rule for a whitelist entry. None = pull it; a string = why not.
    pull not true (string true, 1, yes accepted) -> pull=false; listed in skip_chat_ids -> config skip_chat_ids;
    title contains a built in helper pattern (system_name_patterns, default the implementation's) -> system helper."""
    pull = chat.get("pull")
    if isinstance(pull, str):
        pull = pull.strip().lower() in ("true", "1", "yes")
    if pull is not True:
        return "pull=false"
    if chat.get("chat_id") in set(opt(cfg, "skip_chat_ids", []) or []):
        return "config skip_chat_ids"
    name = _title(chat).lower()
    patterns = opt(cfg, "system_name_patterns") or _impl_attr(cfg, "SYSTEM_NAME_PATTERNS", ()) or ()
    for pat in patterns:
        if str(pat).lower() and str(pat).lower() in name:
            return "system helper (%s)" % pat
    return None


def pullable_chats(cfg, whitelist=None):
    wl = whitelist if whitelist is not None else load_whitelist(cfg)
    return [c for c in wl if chat_skip_reason(c, cfg) is None]


def excluded_chats(cfg, whitelist=None):
    """[(chat, reason)] for the dry run."""
    wl = whitelist if whitelist is not None else load_whitelist(cfg)
    out = []
    for c in wl:
        r = chat_skip_reason(c, cfg)
        if r:
            out.append((c, r))
    return out


def world_for_chat(chat, cfg):
    """World guess for intake entries: second_source.world_by_chat[chat_id] > the whitelist entry's world >
    second_source.world_keywords ({keyword: world}) found in the title > other. Only worlds in the owner's vocab count."""
    keys = world_keys(cfg)

    def valid(w):
        return bool(w) and isinstance(w, str) and (not keys or w in keys)
    w = (opt(cfg, "world_by_chat", {}) or {}).get(chat.get("chat_id"))
    if valid(w):
        return w
    if valid(chat.get("world")):
        return chat["world"]
    title = _title(chat).lower()
    for kw, w in (opt(cfg, "world_keywords", {}) or {}).items():
        if str(kw).strip() and str(kw).lower() in title and valid(w):
            return w
    return "other" if ("other" in keys or not keys) else ""


# ---------------------------------------------------------------- 2 · one day

def yesterday():
    return (dt.date.today() - dt.timedelta(days=1)).isoformat()


def fetch_day(cfg, day=None, runner=None):
    """One local day (default yesterday) of the pullable whitelist chats, through the configured implementation.
    Zero message chats are left out; one failing chat is an error and the pull goes on; a whitelist problem is fatal."""
    day = day or yesterday()
    src = source_name(cfg)
    out = {"day": day, "source": src, "chats": [], "n_chats": 0, "n_msgs": 0, "errors": [], "fatal": [], "skipped": [],
           "owner_ids": [], "pullable": 0, "failed_chats": 0}

    def fatal(msg):
        out["errors"].append(msg)
        out["fatal"].append(msg)
        return out
    if src == NONE:
        return fatal("second source is none")
    try:
        impl = implementation(cfg if source_name(cfg) == src else src)
    except Exception as e:
        return fatal(str(e))
    wl, err = read_whitelist(cfg)
    if err:
        return fatal(err)
    entries = {c["chat_id"]: c for c in wl}
    chats = [c for c in wl if chat_skip_reason(c, cfg) is None]
    out["pullable"] = len(chats)
    if not chats:
        return fatal("whitelist has 0 pullable chats: %s" % whitelist_path(cfg))
    try:
        res = impl.fetch_day(cfg, chats, day, runner=runner)
    except Exception as e:
        out["errors"].append("%s fetch failed: %s" % (src, str(e)[:200]))
        out["failed_chats"] = len(chats)
        return out
    for c in res.get("chats") or []:
        msgs = [m for m in (c.get("messages") or []) if isinstance(m, dict)]
        if not msgs or not c.get("chat_id"):
            continue
        entry = entries.get(c["chat_id"]) or {}
        title = str(c.get("title") or _title(entry) or c["chat_id"])
        chat = {"chat_id": c["chat_id"], "title": title, "kind": str(c.get("kind") or entry.get("kind") or ""), "messages": msgs}
        chat["world_guess"] = world_for_chat(dict(entry, chat_id=c["chat_id"], title=title), cfg)
        out["chats"].append(chat)
    out["errors"] += [str(e) for e in (res.get("errors") or [])]
    out["failed_chats"] = int(res.get("failed_chats") or 0)
    out["skipped"] = list(res.get("skipped") or [])
    out["owner_ids"] = [str(x) for x in (res.get("owner_ids") or [])]
    out["n_chats"] = len(out["chats"])
    out["n_msgs"] = sum(len(c["messages"]) for c in out["chats"])
    return out


def others_count(fetched):
    return sum(1 for c in fetched.get("chats") or [] for m in c.get("messages") or [] if not m.get("is_owner"))


# ---------------------------------------------------------------- 3 · prompt

def _labels(chat, tag):
    """sender_id -> display label; two people with the same name get #2; the owner gets (<tag>)."""
    by_name, labels = {}, {}
    for m in chat["messages"]:
        sid = m["sender_id"]
        if sid in labels:
            continue
        base = m["sender_name"]
        n = by_name.get(base, 0) + 1
        by_name[base] = n
        lab = base if n == 1 else "%s#%d" % (base, n)
        labels[sid] = lab + (" (%s)" % tag if m.get("is_owner") else "")
    return labels


def _chat_parts(chat, tag):
    labels = _labels(chat, tag)
    who = " · ".join("%s = %s" % (lab, sid) for sid, lab in labels.items())
    head = "### 会话「%s」 · %s · %s\n发言人: %s" % (chat["title"], chat.get("kind") or "-", chat["chat_id"], who)
    lines = []
    for m in chat["messages"]:
        at = str(m.get("at") or "")
        hm = at[11:16] if len(at) >= 16 else at
        lines.append("%s %s: %s" % (hm, labels[m["sender_id"]], re.sub(r"\s*\n[\s\n]*", " / ", str(m["text"]))))
    return head, lines


def render_messages(fetched, max_chars=PROMPT_MAX_CHARS, tag="ME"):
    """The message area, at most max_chars. Over the limit every chat gets a budget in proportion to its size and keeps
    its newest messages, with a note saying it was cut."""
    parts = [_chat_parts(c, tag) for c in fetched.get("chats") or []]
    full = "\n\n".join(h + "\n" + "\n".join(ls) for h, ls in parts)
    if len(full) <= max_chars:
        return full
    note = "(讯息区截过: 原 %d 字超过 %d 字上限, 每个会话按比例只留最新的讯息)" % (len(full), max_chars)
    total = sum(len(h) + 1 + sum(len(x) + 1 for x in ls) for h, ls in parts) or 1
    avail = max_chars - len(note) - 2 - 40 * len(parts)
    blocks = []
    for h, ls in parts:
        size = len(h) + 1 + sum(len(x) + 1 for x in ls)
        budget = int(avail * size / float(total)) - len(h) - 1
        kept, used = [], 0
        for x in reversed(ls):
            if used + len(x) + 1 > budget:
                break
            kept.append(x)
            used += len(x) + 1
        kept.reverse()
        cut = len(ls) - len(kept)
        blocks.append(h + ("\n(前面截掉 %d 封)" % cut if cut else "") + ("\n" + "\n".join(kept) if kept else ""))
    out = note + "\n\n" + "\n\n".join(blocks)
    return out[:max_chars]


def build_prompt(fetched, template_path=None, max_chars=PROMPT_MAX_CHARS, cfg=None, log=None):
    """Template placeholders: {OWNER_LABEL} {OWNER_TAG} {SOURCE_NAME} {SENDER_ID_EXAMPLE} {CHAT_ID_EXAMPLE} {DAY}
    {N_CHATS} {N_MSGS} {MESSAGES}. Without template_path: owner copy, else payload seed (WARNING)."""
    cfg = cfg or {}
    if template_path:
        tpl = WBF._SEED_RE.sub("", Path(template_path).read_text(encoding="utf-8"), count=1)
    else:
        tpl = WBF.prompt_template(cfg, PROMPT_NAME, log=log, who="second source")[0]
    src = fetched.get("source") or source_name(cfg)
    impl = None
    try:
        impl = implementation(cfg if source_name(cfg) == src else src)
    except Exception:
        impl = None
    tag = owner_tag(cfg)
    try:
        name = W.source_label(cfg, "second_source") if source_name(cfg) == src else ""
    except Exception:
        name = ""
    name = name or str(getattr(impl, "SOURCE_NAME", src))
    return (tpl.replace("{OWNER_LABEL}", WBF.owner_label(cfg)).replace("{OWNER_TAG}", tag)
               .replace("{SOURCE_NAME}", name)
               .replace("{SENDER_ID_EXAMPLE}", str(getattr(impl, "SENDER_ID_EXAMPLE", "<id>")))
               .replace("{CHAT_ID_EXAMPLE}", str(getattr(impl, "CHAT_ID_EXAMPLE", "<id>")))
               .replace("{DAY}", str(fetched.get("day")))
               .replace("{N_CHATS}", str(fetched.get("n_chats", 0)))
               .replace("{N_MSGS}", str(fetched.get("n_msgs", 0)))
               .replace("{MESSAGES}", render_messages(fetched, max_chars, tag)))


# ---------------------------------------------------------------- 4 · reading what the model returns

def extract_json(text):
    """Model text -> dict. Tolerates fences, chatter, several JSON blocks (the first one with people or captures wins).
    Only when nothing parses, wa_common.repair_inner_quotes runs once. Raises ValueError when there is no object."""
    s = str(text or "").strip()
    try:
        j = json.loads(s)
        if isinstance(j, dict):
            return j
    except Exception:
        pass
    dec = json.JSONDecoder()
    first = None
    for m in re.finditer(r"\{", s):
        try:
            j, _ = dec.raw_decode(s[m.start():])
        except Exception:
            continue
        if isinstance(j, dict):
            if "people" in j or "captures" in j:
                return j
            if first is None:
                first = j
    if first is not None:
        return first
    fixed = W.repair_inner_quotes(s)
    if fixed != s:
        return extract_json(fixed)
    raise ValueError("no JSON object in the model output: %r" % s[:120])


_PREFIX_RE = re.compile(r"^\s*(?:\[[\w.-]+\]\s*)?(?:在\s*「[^」]*」\s*(?:里|上|中)?\s*说\s*[:：]\s*|in\s+[\"“][^\"”]*[\"”]\s*:\s*)", re.I)


def person_line_text(line):
    """The news sentence: one line · the model's own "in chat X said:" prefix removed · surrounding quotes removed · 90 max."""
    s = re.sub(r"\s+", " ", str(line or "")).strip()
    s = _PREFIX_RE.sub("", s).strip().strip("「」\"'“”").strip()
    return s[:LINE_MAX]


def chat_messages(chat):
    """The adapter's messages of one chat ({at, sender_id, sender_name, is_owner, text}) in wa_intake.resolve_quote's
    shape: me = is_owner, speaker = sender_id, the time as the adapter gives it."""
    out = []
    for i, m in enumerate((chat or {}).get("messages") or []):
        if not isinstance(m, dict):
            continue
        out.append({"pk": m.get("pk") or m.get("message_id") or m.get("id") or "m%d" % i,
                    "at": str(m.get("at") or m.get("time") or m.get("create_time") or ""),
                    "me": bool(m.get("is_owner")), "speaker": str(m.get("sender_id") or ""),
                    "text": str(m.get("text") or ""), "caption": "", "transcript": "", "doc_text": ""})
    return out


def _resolve(quote, chat):
    """wa_intake.resolve_quote against this chat, or None when wa_intake cannot be imported (never accepted unchecked)."""
    try:
        import wa_intake as WI
        fn = WI.resolve_quote
    except Exception:
        return None
    return fn(quote, chat_messages(chat))


def quote_in_chat(quote, chat):
    """Anti fabrication, one rule with the WhatsApp intake: wa_intake.resolve_quote finds the quote in that chat's
    messages (exact, excerpt, stitched or near). False when it does not, or when wa_intake cannot be imported."""
    r = _resolve(quote, chat)
    return r is not None and r["how"] != "none"


def _as_list(x, key_name):
    """people may come as a list, as {id: {...}} or as {id: "line"}."""
    if isinstance(x, list):
        return [p for p in x if isinstance(p, dict)]
    if isinstance(x, dict):
        out = []
        for k, v in x.items():
            if isinstance(v, dict):
                d = dict(v)
                d.setdefault(key_name, k)
                out.append(d)
            elif isinstance(v, str):
                out.append({key_name: k, "line": v})
        return out
    return []


def _sid(d):
    return str(d.get("sender_id") or d.get("open_id") or d.get("id") or "").strip()


def clean_result(result, fetched, cfg=None):
    """The one place for anti fabrication and shape. Returns {"people", "captures", "dropped": [reasons]}.
    people: sender_id must be someone other than the owner who spoke that day; first entry per person wins; line through
    person_line_text, empty dropped; a chat_id not pulled that day becomes None.
    captures: the chat must be one pulled that day (chat_id, else the title in chat / chat_name / title); legality through
    wa_intake.validate_captures; quote_in_chat; sender_id kept only for a speaker or the owner; at most 6."""
    try:
        import wa_intake as WI
        validate = WI.validate_captures
    except Exception:
        validate = None
    chats = {c["chat_id"]: c for c in fetched.get("chats") or []}
    by_title = {}
    for c in chats.values():
        by_title.setdefault(str(c.get("title") or "").strip().lower(), c["chat_id"])
    owner = set(fetched.get("owner_ids") or [])
    speakers = {}
    for c in chats.values():
        for m in c["messages"]:
            if not m.get("is_owner"):
                speakers.setdefault(m["sender_id"], m["sender_name"])
    dropped = []
    people, seen = [], set()
    for p in _as_list((result or {}).get("people"), "sender_id"):
        sid = _sid(p)
        if sid not in speakers:
            dropped.append("people: sender %s did not speak that day (%s)" % (sid or "empty", str(p.get("name") or "")[:20]))
            continue
        if sid in seen:
            continue
        line = person_line_text(p.get("line"))
        if not line:
            dropped.append("people: empty line for %s" % speakers[sid])
            continue
        seen.add(sid)
        cid = str(p.get("chat_id") or "").strip()
        people.append({"sender_id": sid, "name": str(p.get("name") or speakers[sid]).strip() or speakers[sid],
                       "chat_id": cid if cid in chats else None, "line": line})
    caps = []
    raw_caps = (result or {}).get("captures")
    for cap in (raw_caps if isinstance(raw_caps, list) else []):
        if not isinstance(cap, dict):
            continue
        cid = str(cap.get("chat_id") or "").strip()
        if cid not in chats:
            cid = by_title.get(str(cap.get("chat") or cap.get("chat_name") or cap.get("title") or "").strip().lower(), "")
        if cid not in chats:
            dropped.append("captures: chat %s was not pulled that day" % (cap.get("chat_id") or cap.get("chat") or "empty"))
            continue
        if validate is None:
            dropped.append("captures: wa_intake is not importable, capture not validated")
            continue
        ok = validate([cap])
        if not ok:
            dropped.append("captures: kind / text / quote not legal (%s)" % str(cap.get("kind"))[:20])
            continue
        r = _resolve(ok[0]["quote"], chats[cid])
        if r is None or r["how"] == "none":
            dropped.append("captures: quote not in the messages of %s (%s)" % (chats[cid]["title"], ok[0]["quote"][:30]))
            continue
        c2 = dict(ok[0])
        c2["quote"] = r["text"] or c2["quote"]      # the stored words, never the model's
        sid = _sid(cap)
        c2["chat_id"] = cid
        c2["sender_id"] = sid if (sid in speakers or sid in owner) else None
        caps.append(c2)
        if len(caps) >= MAX_CAPTURES:
            break
    return {"people": people, "captures": caps, "dropped": dropped}


def parse_result(text, fetched=None, cfg=None):
    """Model text -> {"people", "captures", "dropped"}. With fetched, clean_result applies; without, only JSON and list
    shapes. No JSON raises ValueError."""
    j = extract_json(text)
    if fetched is None:
        return {"people": _as_list(j.get("people"), "sender_id"),
                "captures": [c for c in (j.get("captures") or []) if isinstance(c, dict)] if isinstance(j.get("captures"), list) else [],
                "dropped": []}
    return clean_result(j, fetched, cfg)


# ---------------------------------------------------------------- 5 · writing

def _frontmatter(text):
    """wa_common.parse_frontmatter with list values as lists; {} when there is no frontmatter."""
    try:
        try:
            r = W.parse_frontmatter(text, lists=True)
        except TypeError:
            r = W.parse_frontmatter(text)
    except Exception:
        return {}
    if isinstance(r, tuple):
        r = r[0] if r else {}
    return r if isinstance(r, dict) else {}


def _ids(v):
    """A frontmatter id value (one id, or a list of them) to a list of clean ids."""
    return [x.strip().strip("'\"").strip() for x in W.fm_list(v) if x.strip().strip("'\"").strip()]


def source_id_index(cfg, key=None):
    """{"by_id": {id: Path}, "conflicts": {id: [file names]}, "error": str or None, "key": key}.
    Scans paths.people_dir (files starting with _ skipped) for the frontmatter key (one id or a list); an id on exactly
    one note counts."""
    key = key or id_key(cfg)
    out = {"by_id": {}, "conflicts": {}, "error": None, "key": key}
    pdir = str(((cfg or {}).get("paths") or {}).get("people_dir") or "").strip()
    if not key:
        out["error"] = "no person note id key for source %s (set second_source.id_key)" % source_name(cfg)
        return out
    if not pdir:
        out["error"] = "paths.people_dir is not set: news lines cannot be written"
        return out
    d = Path(pdir).expanduser()
    if not d.is_dir():
        out["error"] = "people dir missing: %s" % d
        return out
    try:
        files = sorted(d.glob("*.md"))
    except OSError as e:
        out["error"] = "people dir unreadable: %s (%s)" % (d, e)
        return out
    seen = {}
    for p in files:
        if p.name.startswith("_"):
            continue
        try:
            fm = _frontmatter(p.read_text(encoding="utf-8")[:4000])
        except Exception:
            continue
        for v in _ids(fm.get(key)):
            if p not in seen.setdefault(v, []):
                seen[v].append(p)
    out["by_id"] = {k: v[0] for k, v in seen.items() if len(v) == 1}
    out["conflicts"] = {k: [p.name for p in v] for k, v in seen.items() if len(v) > 1}
    return out


def notes_by_source_id(cfg, key=None):
    return source_id_index(cfg, key)["by_id"]


def source_id_conflicts(cfg, key=None):
    return source_id_index(cfg, key)["conflicts"]


def chat_for_person(fetched, sender_id, chat_id_hint=None):
    """Which chat the news line names: the model's chat when the person spoke there; else the chat where they spoke most
    that day (ties go to the one where they spoke last)."""
    chats = fetched.get("chats") or []
    for c in chats:
        if c["chat_id"] == chat_id_hint and any(m["sender_id"] == sender_id for m in c["messages"]):
            return c
    best, rank = None, None
    for c in chats:
        mine = [m for m in c["messages"] if m["sender_id"] == sender_id]
        if not mine:
            continue
        k = (len(mine), mine[-1].get("at") or "")
        if rank is None or k > rank:
            best, rank = c, k
    return best


def recent_line(day, chat_title, line, label, lang="zh"):
    """The one shape of a person note news line (append_recent needs "- " and "·")."""
    title = re.sub(r"\s+", " ", str(chat_title or "")).strip()
    if str(lang or "").lower().startswith("en"):
        return "- %s · [%s] in \"%s\": %s" % (day, label, title, person_line_text(line))
    return "- %s · [%s] 在「%s」说: %s" % (day, label, title, person_line_text(line))


def apply_result(cfg, fetched, result, append_recent, append_captures, today=None, index=None):
    """result: parse_result's output (an uncleaned dict works too: clean_result runs again, same outcome).
    append_recent(note_path, line) comes from the daemon (it owns the recent block, its cap and dedupe; False = not
    written); append_captures has wa_intake.append_captures' signature.
    Returns {"recent", "captures", "captures_skipped", "no_id": [names], "dropped", "errors"}."""
    res = clean_result(result or {}, fetched, cfg)
    idx = index if index is not None else source_id_index(cfg)
    errors = []
    if idx.get("error"):
        errors.append(idx["error"])
    chats = {c["chat_id"]: c for c in fetched.get("chats") or []}
    day = fetched.get("day")
    label = fetched.get("source") or source_name(cfg)
    lang = W.owner_lang(cfg)
    recent, n_caps, skipped, no_id = 0, 0, 0, []
    for p in res["people"]:
        note = idx["by_id"].get(p["sender_id"])
        if not note:
            if p["sender_id"] not in idx.get("conflicts", {}) and p["name"] not in no_id:
                no_id.append(p["name"])
            continue
        chat = chat_for_person(fetched, p["sender_id"], p.get("chat_id"))
        if not chat:
            continue
        try:
            r = append_recent(note, recent_line(day, chat["title"], p["line"], label, lang))
            if r is not False:
                recent += 1
        except Exception as e:
            errors.append("append_recent %s: %s" % (Path(str(note)).name, str(e)[:120]))
    if res["captures"]:
        buffer = intake_buffer(cfg)
        if not intake_enabled(cfg):
            skipped = len(res["captures"])
        elif not buffer:
            errors.append("intake.enabled is true but no intake buffer path is set: %d captures not written" % len(res["captures"]))
        else:
            state_db = str(WBF.state_db_path(cfg))
            extra = {"cfg": cfg} if _accepts_kw(append_captures, "cfg") else {}
            with_msgs = _accepts_kw(append_captures, "messages")
            groups, order = {}, []
            for cap in res["captures"]:
                note = idx["by_id"].get(cap.get("sender_id") or "")
                k = (cap["chat_id"], str(note) if note else "")
                if k not in groups:
                    groups[k] = []
                    order.append(k)
                groups[k].append(cap)
            for k in order:
                chat = chats[k[0]]
                caps = groups[k]
                kw = dict(extra, messages=chat_messages(chat)) if with_msgs else extra
                for i in range(0, len(caps), CAPTURES_PER_CALL):
                    chunk = [{kk: vv for kk, vv in c.items() if kk not in ("chat_id", "sender_id")} for c in caps[i:i + CAPTURES_PER_CALL]]
                    try:
                        n_caps += int(append_captures(state_db, buffer, chat["chat_id"], chat["title"], chat.get("world_guess") or world_for_chat(chat, cfg),
                                                      chunk, source=label, people_note=(Path(k[1]) if k[1] else None), today=today, **kw) or 0)
                    except Exception as e:
                        errors.append("append_captures %s: %s" % (chat["title"], str(e)[:120]))
    return {"recent": recent, "captures": n_caps, "captures_skipped": skipped, "no_id": no_id, "dropped": res["dropped"], "errors": errors}


def _accepts_kw(fn, name):
    """Does a callable take this keyword (wa_intake.append_captures takes cfg and messages; a caller's fake may not)."""
    try:
        import inspect
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == name or p.kind == p.VAR_KEYWORD for p in params)


def write_last(cfg, summary):
    """<live>/second-source-last.json = {at, day, source, ok, n_chats, n_msgs, recent, captures, errors (20 max)} plus
    no_id, claude_called, dropped, id_conflicts, captures_skipped, pullable when present. Returns what was written."""
    s = dict(summary or {})
    errs = [str(e) for e in (s.get("errors") or [])]
    out = {"at": W.now_iso(), "day": s.get("day"), "source": s.get("source"), "ok": bool(s["ok"]) if "ok" in s else not errs,
           "n_chats": int(s.get("n_chats") or 0), "n_msgs": int(s.get("n_msgs") or 0),
           "recent": int(s.get("recent") or 0), "captures": int(s.get("captures") or 0), "errors": errs[:MAX_LAST_ERRORS]}
    for k in ("no_id", "claude_called", "dropped", "id_conflicts", "captures_skipped", "pullable"):
        if k in s:
            out[k] = s[k]
    W.write_json_atomic(last_path(cfg), out)
    return out


def log_line(summary):
    """The daemon's one log line for a night."""
    s = summary or {}
    line = "second source %s: pulled %d chats %d msgs · recent +%d · captures +%d" % (
        s.get("source") or "?", int(s.get("n_chats") or 0), int(s.get("n_msgs") or 0), int(s.get("recent") or 0), int(s.get("captures") or 0))
    if s.get("id_conflicts"):
        line += " · id conflicts %d" % len(s["id_conflicts"])
    if s.get("errors"):
        line += " · errors %d" % len(s["errors"])
    if "ok" in s and not s["ok"]:
        line += " · NOT OK"
    return line


def run_ok(fetched, claude_error, n_pullable=None):
    """ok for second-source-last.json (the digest's check mark): a model or writing failure is not ok; a whitelist
    problem is not ok; every pullable chat failing (an expired login) is not ok; some chats failing stays ok with errors."""
    if claude_error:
        return False
    if fetched.get("fatal"):
        return False
    pullable = int(n_pullable if n_pullable is not None else fetched.get("pullable") or 0)
    failed = int(fetched.get("failed_chats") or 0)
    return not (pullable > 0 and failed >= pullable)


def run_nightly(cfg, call, append_recent, append_captures, day=None, runner=None, template_path=None, today=None, log=None):
    """The daemon's nightly job: pull, then (only when someone other than the owner spoke) prompt, call(prompt) -> text,
    parse, apply, and always write second-source-last.json. Returns the summary (log_line takes it).
    A disabled source returns ok=false and writes nothing."""
    src = source_name(cfg)
    summary = {"day": day or yesterday(), "source": src, "n_chats": 0, "n_msgs": 0, "recent": 0, "captures": 0,
               "no_id": [], "errors": [], "claude_called": False, "ok": False}
    if not enabled(cfg):
        summary["errors"].append("second source is not enabled (second_source.enabled and second_source.source)")
        return summary
    fetched = fetch_day(cfg, day=day, runner=runner)
    summary.update(day=fetched["day"], n_chats=fetched["n_chats"], n_msgs=fetched["n_msgs"], pullable=fetched["pullable"])
    summary["errors"] = list(fetched["errors"])
    idx = source_id_index(cfg)
    if idx["error"]:
        summary["errors"].append(idx["error"])
    if idx["conflicts"]:
        summary["id_conflicts"] = idx["conflicts"]
        for k, names in sorted(idx["conflicts"].items()):
            msg = "person note id conflict: %s %s is on %s (neither note gets news lines)" % (idx["key"], k, ", ".join(names))
            summary["errors"].append(msg)
            if log:
                log("WARNING second source: " + msg)
    claude_error = None
    if others_count(fetched) > 0 and not fetched["fatal"]:
        try:
            max_chars = int(opt(cfg, "prompt_max_chars", PROMPT_MAX_CHARS) or PROMPT_MAX_CHARS)
            prompt = build_prompt(fetched, template_path, max_chars, cfg, log=log)
            summary["claude_called"] = True
            text = call(prompt)
            parsed = parse_result(text, fetched, cfg)
            applied = apply_result(cfg, fetched, parsed, append_recent, append_captures, today=today, index=idx)
            summary.update(recent=applied["recent"], captures=applied["captures"], no_id=applied["no_id"],
                           dropped=applied["dropped"], captures_skipped=applied["captures_skipped"])
            summary["errors"] += [e for e in applied["errors"] if e not in summary["errors"]]
        except Exception as e:
            claude_error = str(e)[:300]
            summary["errors"].append("model or writing failed: %s" % claude_error)
    summary["ok"] = run_ok(fetched, claude_error)
    write_last(cfg, summary)
    if log:
        log(log_line(summary))
    return summary


# ---------------------------------------------------------------- id mapping helpers (read only)

def members(cfg, chat_ids=None, runner=None):
    """Members of the whitelist chats (or of chat_ids) through the implementation's optional members()."""
    try:
        impl = implementation(cfg)
    except Exception as e:
        return {"users": {}, "bots": {}, "errors": [str(e)]}
    if not hasattr(impl, "members"):
        return {"users": {}, "bots": {}, "errors": ["source %s has no members capability" % source_name(cfg)]}
    wl, err = read_whitelist(cfg)
    chats = wl
    if chat_ids:
        known = {c["chat_id"] for c in wl}
        chats = [c for c in wl if c["chat_id"] in chat_ids] + [{"chat_id": x, "title": x} for x in chat_ids if x not in known]
    res = impl.members(cfg, chats, runner=runner)
    if err and not chat_ids:
        res.setdefault("errors", []).insert(0, err)
    return res


def map_targets(cfg):
    """Person notes to map to source ids: with second_source.map_roles, notes whose roles meet that list; otherwise
    every note without the id key. Returns [{file, title, roles, id}]."""
    pdir = str(((cfg or {}).get("paths") or {}).get("people_dir") or "").strip()
    key = id_key(cfg)
    want = set(str(x) for x in (opt(cfg, "map_roles", []) or []))
    d = Path(pdir).expanduser() if pdir else None
    out = []
    for p in (sorted(d.glob("*.md")) if d and d.is_dir() else []):
        if p.name.startswith("_"):
            continue
        text = p.read_text(encoding="utf-8")
        fm = _frontmatter(text[:4000])
        roles = W.fm_list(fm.get("roles"))
        current = ", ".join(_ids(fm.get(key))) if key else ""
        if want and not (set(roles) & want):
            continue
        if not want and current:
            continue
        m = re.search(r"(?m)^# (.+)$", text)
        out.append({"file": p.name, "title": m.group(1).strip() if m else p.stem, "roles": roles, "id": current})
    return out


def _tokens(s):
    return set(t for t in re.split(r"[^0-9a-z一-鿿]+", str(s or "").lower()) if len(t) >= 2)


def name_candidates(target, users, stopwords=()):
    """A person note -> members with a similar name [(score, id, name)], score = shared words of file name and title.
    Printed for a person to decide; never written."""
    mine = _tokens(target["file"][:-3].replace("-", " ")) | _tokens(target["title"])
    mine -= set(str(x).lower() for x in stopwords or ())
    out = []
    for uid, u in users.items():
        common = mine & _tokens(u.get("name"))
        if common:
            out.append((len(common), uid, u.get("name")))
    return sorted(out, key=lambda x: -x[0])[:5]


# ---------------------------------------------------------------- selftest

def selftest_cfg(tmp, extra=None):
    """A real cfg for the selftest: <tmp>/live/config.json with every path inside tmp, loaded through
    wa_common.load_config; refuses (RuntimeError) when the live dir resolves outside tmp."""
    tmp = Path(tmp)
    live = tmp / "live"
    live.mkdir(parents=True, exist_ok=True)
    people = tmp / "People"
    buffer = tmp / "vault" / "intake-buffer.md"
    raw = {"version": 1, "owner_label": "Test Owner", "owner_marker": "TO",
           "paths": {"home": str(tmp), "live_dir": str(live), "people_dir": str(people), "intake_buffer": str(buffer)},
           "brain": {"cwd": str(tmp / "brain-cwd")},
           "vocab": {"worlds": [{"key": "personal", "label": "私人", "label_en": "Personal", "business": False},
                                {"key": "work", "label": "工作", "label_en": "Work", "business": True},
                                {"key": "side", "label": "副业", "label_en": "Side", "business": True},
                                {"key": "other", "label": "其他", "label_en": "Other", "business": False}]},
           "board": {"lang": "zh"},
           "intake": {"enabled": True, "buffer": str(buffer)},
           "second_source": {"enabled": True, "source": "lark", "at": "03:10", "id_key": "lark_id",
                             "world_by_chat": {"oc_test_fin": "side", "oc_test_team": "not-a-world"},
                             "world_keywords": {"maple": "work"},
                             "lark": {"whitelist": str(live / WHITELIST_FILE), "skip_chat_ids": ["oc_test_skip"]}}}
    raw.update(extra or {})
    p = live / "config.json"
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    cfg = W.load_config(str(p))
    got = Path(W.live_dir(cfg)).resolve()
    if not str(got).startswith(str(tmp.resolve())):
        raise RuntimeError("selftest refuses to run: live dir resolved to %s, outside the temp folder" % got)
    return cfg


def _selftest():
    import shutil
    import tempfile
    fails, n = [], [0]

    def check(cond, msg):
        n[0] += 1
        print(("  ok   " if cond else "  FAIL ") + msg)
        if not cond:
            fails.append(msg)

    tmp = Path(tempfile.mkdtemp(prefix="wa_second_source_selftest_"))
    try:
        import adapters.second_source_lark as L
        try:
            import wa_intake as WI
        except Exception:
            WI = None
        check(WI is not None, "wa_intake importable (captures are validated and appended through it)")
        fx = L.selftest_fixture()
        ids, day = fx["ids"], fx["day"]
        O, A, M, S = ids["owner"], ids["alex"], ids["maya"], ids["sam"]
        people = tmp / "People"
        people.mkdir()
        (people / "Alex-Test.md").write_text("---\ntype: person\nroles: [team]\nlark_id: ou_test_alex\n---\n\n# Alex Test\n\n"
                                             "<!-- wa-live:begin -->\n<!-- wa-live:end -->\n", encoding="utf-8")
        (people / "Maya-Test.md").write_text("---\ntype: person\nroles: [collaborator]\n---\n\n# Maya Test\n", encoding="utf-8")
        (people / "Dup-A.md").write_text("---\ntype: person\nroles: [network]\nlark_id: \"ou_test_dup\"\n---\n\n# Dup A\n", encoding="utf-8")
        (people / "Dup-B.md").write_text("---\ntype: person\nroles: [network]\nlark_id: ou_test_dup\n---\n\n# Dup B\n", encoding="utf-8")
        (people / "_People-Guide.md").write_text("---\nlark_id: ou_test_alex\n---\n", encoding="utf-8")
        (tmp / "vault").mkdir()
        cfg = selftest_cfg(tmp)
        live = _live(cfg)
        wl_file = live / WHITELIST_FILE
        wl_file.write_text(json.dumps(fx["whitelist"], ensure_ascii=False), encoding="utf-8")

        # 1 · config, whitelist, worlds
        check(enabled(cfg), "enabled: second_source.enabled with source lark")
        check(not enabled(dict(cfg, second_source=dict(_ss(cfg), source="none"))) and not enabled(dict(cfg, second_source=dict(_ss(cfg), enabled=False))),
              "enabled: source none or enabled false is off")
        check(whitelist_path(cfg).resolve() == wl_file.resolve() and last_path(cfg) == live / LAST_FILE and id_key(cfg) == "lark_id",
              "paths: whitelist, last file and id key come from config")
        check(whitelist_path(dict(cfg, second_source=dict(_ss(cfg), lark={"whitelist": "<live>/w.json"}))) == live / "w.json",
              "whitelist path tolerates an unresolved <live> prefix")
        pc = [c["chat_id"] for c in pullable_chats(cfg)]
        check(pc == ["oc_test_team", "oc_test_fin", "oc_test_err", "oc_test_empty", "oc_test_botp2p"],
              "pullable_chats: pull=false, helper chats and skip ids left out, string \"true\" accepted; got %s" % pc)
        ex = dict((c["chat_id"], r) for c, r in excluded_chats(cfg))
        check(ex.get("oc_test_support", "").startswith("system helper") and ex.get("oc_test_mybot") == "pull=false"
              and ex.get("oc_test_skip") == "config skip_chat_ids", "excluded_chats: reasons right; got %s" % ex)
        check(world_for_chat({"chat_id": "oc_test_fin", "title": "Maple Finance Sync"}, cfg) == "side", "world_for_chat: world_by_chat wins")
        check(world_for_chat({"chat_id": "oc_x", "title": "Maple Stuff", "world": "personal"}, cfg) == "personal", "world_for_chat: the entry's world beats keywords")
        check(world_for_chat({"chat_id": "oc_test_team", "title": "Project Maple Team"}, cfg) == "work", "world_for_chat: an unknown world is ignored, the keyword matches")
        check(world_for_chat({"chat_id": "oc_y", "title": "Unrelated"}, cfg) == "other", "world_for_chat: nothing matches gives other")

        # 2 · fetch_day through the Lark implementation and a fake runner
        calls = []
        runner = fx["make_runner"](calls)
        f = fetch_day(cfg, day=day, runner=runner)
        check([c["chat_id"] for c in f["chats"]] == ["oc_test_team", "oc_test_fin"] and f["n_chats"] == 2 and f["n_msgs"] == 6,
              "fetch_day: two chats, six messages; got %s / %s" % ([c["chat_id"] for c in f["chats"]], f["n_msgs"]))
        check([c["world_guess"] for c in f["chats"]] == ["work", "side"] and f["chats"][0]["title"] == "Project Maple Team",
              "fetch_day: every chat has title and world_guess")
        check(f["pullable"] == 5 and f["failed_chats"] == 1 and not f["fatal"] and f["owner_ids"] == [O]
              and any("token expired" in e for e in f["errors"]), "fetch_day: pullable, failed chats, errors, owner ids reported")
        check(set(f["chats"][0]["messages"][0]) == {"at", "sender_id", "sender_name", "is_owner", "text"}, "fetch_day: the contract message keys")

        # 3 · a missing, unreadable or empty whitelist is an error, never a silent zero
        wl_text = wl_file.read_text(encoding="utf-8")
        wl_file.unlink()
        fm = fetch_day(cfg, day=day, runner=runner)
        check(fm["errors"][:1] and fm["errors"][0].startswith("whitelist not found") and fm["fatal"] and fm["n_chats"] == 0,
              "missing whitelist: fatal error; got %s" % fm["errors"])

        def must_not_call(prompt):
            raise AssertionError("the model must not be called")
        sm = run_nightly(cfg, must_not_call, lambda *a: True, lambda *a, **k: 0, day=day, runner=runner)
        last = json.loads(last_path(cfg).read_text(encoding="utf-8")) if last_path(cfg).exists() else {}
        check(sm["ok"] is False and sm["claude_called"] is False and last.get("ok") is False
              and any("whitelist not found" in e for e in last.get("errors") or []), "missing whitelist: run_nightly ok=false, no model call, last file says why")
        wl_file.write_text("{not json", encoding="utf-8")
        check(fetch_day(cfg, day=day, runner=runner)["errors"][0].startswith("whitelist unreadable"), "unreadable whitelist: fatal error")
        wl_file.write_text(json.dumps({"chats": [{"chat_id": "oc_test_a", "name": "A", "pull": False}]}), encoding="utf-8")
        fz = fetch_day(cfg, day=day, runner=runner)
        check(fz["fatal"] and "0 pullable" in fz["errors"][0] and run_ok(fz, None) is False, "whitelist with nothing to pull: fatal, not ok")
        wl_file.write_text(wl_text, encoding="utf-8")

        # 4 · prompt
        logs = []
        prompt = build_prompt(f, cfg=cfg, log=logs.append)
        check(not re.search(r"\{(OWNER_LABEL|OWNER_TAG|SOURCE_NAME|SENDER_ID_EXAMPLE|CHAT_ID_EXAMPLE|DAY|N_CHATS|N_MSGS|MESSAGES)\}", prompt),
              "build_prompt: every placeholder replaced")
        check("Lark" in prompt and "Test Owner" in prompt and "### 会话「Project Maple Team」 · 群 · oc_test_team" in prompt
              and "Alex Test = ou_test_alex" in prompt and "11:46 Owner Test (TO): [语音 20s]" in prompt,
              "build_prompt: source name, owner label, chat header, speaker ids, owner tag")
        check("<!-- seed:" not in prompt and any(x.startswith("WARNING second source: owner prompt copy missing") for x in logs),
              "build_prompt: no owner copy -> seed used with a WARNING, stamp not sent")
        tpl = tmp / "tpl.md"
        tpl.write_text("DAY={DAY} N={N_CHATS}/{N_MSGS}\n{MESSAGES}\nEND", encoding="utf-8")
        check(build_prompt(f, tpl, cfg=cfg).startswith("DAY=2026-09-13 N=2/6"), "build_prompt: an explicit template path works")
        big = {"day": day, "n_chats": 2, "n_msgs": 4000, "chats": []}
        for cid, cnt in (("oc_test_a", 3000), ("oc_test_b", 1000)):
            msgs = [{"at": "2026-09-13 %02d:%02d" % (i // 60 % 24, i % 60), "sender_id": A, "sender_name": "Alex Test", "is_owner": False,
                     "text": "第%05d封 %s" % (i, "字" * 30)} for i in range(cnt)]
            big["chats"].append({"chat_id": cid, "title": cid, "kind": "群", "messages": msgs})
        rm = render_messages(big, PROMPT_MAX_CHARS)
        check(len(rm) <= PROMPT_MAX_CHARS and "讯息区截过" in rm, "render_messages: cut to <= 60000 with a note (%d chars)" % len(rm))
        check("第02999封" in rm and "第00000封" not in rm and "第00999封" in rm and "前面截掉" in rm, "render_messages: every chat keeps its newest, says how many were cut")
        ka = rm.split("### 会话「oc_test_b」")[0].count("封 字")
        kb = rm.split("### 会话「oc_test_b」")[1].count("封 字")
        check(ka > kb * 2, "render_messages: budget in proportion (a=%d lines, b=%d lines)" % (ka, kb))

        # 5 · parse_result: chatter + fence + an invented sender + the owner + a model added prefix + invented quotes
        model_text = ("好的, 这是昨天的整理:\n\n```json\n{\n  \"people\": [\n"
                      "    {\"sender_id\": \"ou_test_alex\", \"name\": \"Alex Test\", \"chat_id\": \"oc_test_team\", \"line\": \"在「Project Maple Team」说: 问朋友的团队一起报名是不是也算 800, 之后回 Can。%s\"},\n"
                      "    {\"sender_id\": \"ou_test_fake99\", \"name\": \"Ghost\", \"line\": \"invented person\"},\n"
                      "    {\"open_id\": \"ou_test_owner\", \"name\": \"Owner Test\", \"line\": \"the owner never goes into people\"},\n"
                      "    {\"sender_id\": \"ou_test_maya\", \"name\": \"Maya Test\", \"chat_id\": \"oc_test_nope\", \"line\": \"renewal list backfill is done\"},\n"
                      "    {\"sender_id\": \"ou_test_sam\", \"name\": \"Sam Test\", \"line\": \"只发了一张图\"}\n  ],\n  \"captures\": [\n"
                      "    {\"chat_id\": \"oc_test_team\", \"sender_id\": \"ou_test_alex\", \"kind\": \"money\", \"text\": \"Alex 问朋友的团队一起报名是不是也收 800\", \"quote\": \"Hi, my friend's team wants to join too, same price of 800?\", \"quote_at\": \"09-13 11:44\", \"deadline\": \"\", \"guess\": \"决定\"},\n"
                      "    {\"chat\": \"Project Maple Team\", \"sender_id\": \"ou_test_owner\", \"kind\": \"promise_undelivered\", \"text\": \"Owner 答应今晚发 10 月的日期\", \"quote\": \"ok 我今晚把 10 月的日期发你\", \"quote_at\": \"09-13 12:10\", \"deadline\": \"09-13\", \"guess\": \"task\"},\n"
                      "    {\"chat_id\": \"oc_test_team\", \"kind\": \"decision\", \"text\": \"Alex 说可以\", \"quote\": \"Can\", \"quote_at\": \"09-13 12:04\"},\n"
                      "    {\"chat_id\": \"oc_test_team\", \"kind\": \"money\", \"text\": \"invented quote\", \"quote\": \"900 also fine?\"},\n"
                      "    {\"chat_id\": \"oc_test_ghost\", \"kind\": \"money\", \"text\": \"invented chat\", \"quote\": \"x\"},\n"
                      "    {\"chat_id\": \"oc_test_fin\", \"kind\": \"gossip\", \"text\": \"illegal kind\", \"quote\": \"backfill needed\"}\n"
                      "  ],\n  \"note\": \"one extra key\"\n}\n```\n以上。") % ("字" * 100)
        pr = parse_result(model_text, f, cfg)
        check([p["sender_id"] for p in pr["people"]] == [A, M, S], "parse_result: the invented sender and the owner dropped; got %s" % [p["sender_id"] for p in pr["people"]])
        check(pr["people"][0]["line"].startswith("问朋友的团队") and len(pr["people"][0]["line"]) == LINE_MAX, "parse_result: model added prefix removed, cut to 90")
        check(pr["people"][1]["chat_id"] is None, "parse_result: a chat_id not pulled that day becomes None")
        kinds = [c["kind"] for c in pr["captures"]]
        check(kinds == ["money", "promise_undelivered", "decision"],
              "parse_result: 3 captures kept (curly vs straight apostrophe matches · chat title resolves · invented quote, chat and kind dropped); got %s" % kinds)
        check(pr["captures"][1]["chat_id"] == "oc_test_team" and pr["captures"][1]["sender_id"] == O if len(pr["captures"]) > 1 else False,
              "parse_result: a title alone resolves the chat; the owner's sender id may stay on a capture")
        check(len(pr["dropped"]) == 5, "parse_result: 5 dropped reasons (2 people + 3 captures); got %d" % len(pr["dropped"]))
        check([p["line"] for p in parse_result('{"people": {"ou_test_alex": "问报名价钱"}, "captures": []}', f, cfg)["people"]] == ["问报名价钱"],
              "parse_result: people as {sender_id: line} accepted")
        try:
            parse_result("我读不懂这批讯息", f, cfg)
            check(False, "parse_result: no JSON must raise")
        except ValueError:
            check(True, "parse_result: no JSON raises ValueError")
        nine = {"people": [], "captures": [{"chat_id": "oc_test_team", "kind": "decision", "text": "t%d" % i, "quote": "Can"} for i in range(9)]}
        check(len(clean_result(nine, f, cfg)["captures"]) == MAX_CAPTURES, "clean_result: at most 6 captures")
        # 5b · one quote rule with the WhatsApp intake (wa_intake.resolve_quote), invented chat and words
        check(VERSION == "1.2.0", "VERSION is 1.2.0")
        rq = {"chat_id": "oc_test_rq", "title": "Rq", "messages": [
            {"at": "2026-09-13 09:00", "sender_id": M, "sender_name": "Maya Test", "is_owner": False, "text": "the venue deposit"},
            {"at": "2026-09-13 09:01", "sender_id": M, "sender_name": "Maya Test", "is_owner": False, "text": "is due on the fifth"},
            {"at": "2026-09-13 09:05", "sender_id": O, "sender_name": "Owner Test", "is_owner": True, "text": "Please send the floor plan before lunch tomorrow"}]}
        cm = chat_messages(rq)
        check([(x["me"], x["speaker"], x["at"]) for x in cm] == [(False, M, "2026-09-13 09:00"), (False, M, "2026-09-13 09:01"), (True, O, "2026-09-13 09:05")]
              and all(x["pk"] for x in cm), "chat_messages: is_owner becomes me, sender_id becomes speaker, the adapter's time kept")
        trio = [("stitched", "the venue deposit is due on the fifth"), ("near", "please send the flor plan before lunch tomorow"),
                ("paraphrase", "Maya says the hall money is needed soon")]
        same = [(quote_in_chat(q, rq), WI.resolve_quote(q, cm)["how"] != "none" if WI else None) for _n, q in trio]
        check(same == [(True, True), (True, True), (False, False)],
              "second source uses resolve_quote: a stitched or near quote is accepted the same way in both paths, a paraphrase rejected; got %s" % same)
        fq = dict(f, chats=f["chats"] + [rq])
        cq = clean_result({"captures": [{"chat_id": "oc_test_rq", "kind": "money", "text": "deposit due", "quote": trio[0][1]},
                                        {"chat_id": "oc_test_rq", "kind": "decision", "text": "plan", "quote": trio[1][1]},
                                        {"chat_id": "oc_test_rq", "kind": "money", "text": "gist", "quote": trio[2][1]}]}, fq, cfg)
        check([c["quote"] for c in cq["captures"]] == ["the venue deposit ⏎ is due on the fifth", "Please send the floor plan before lunch tomorrow"]
              and len(cq["dropped"]) == 1, "clean_result keeps the resolved stored text as the quote, drops the paraphrase; got %s" % [c["quote"] for c in cq["captures"]])
        check(pr["captures"][0]["quote"] == "Hi, my friend’s team wants to join too, same price of 800?",
              "clean_result: the model's straight apostrophe becomes the stored curly one")
        saved_wi = sys.modules.get("wa_intake")
        sys.modules["wa_intake"] = None
        try:
            no_wi = (quote_in_chat(trio[0][1], rq), clean_result({"captures": [{"chat_id": "oc_test_rq", "kind": "money", "text": "t", "quote": trio[0][1]}]}, fq, cfg))
        finally:
            if saved_wi is not None:
                sys.modules["wa_intake"] = saved_wi
            else:
                sys.modules.pop("wa_intake", None)
        check(no_wi[0] is False and no_wi[1]["captures"] == [] and no_wi[1]["dropped"], "wa_intake not importable: the quote is never accepted unchecked")
        check(person_line_text('in "Project Maple Team": said the venue is booked') == "said the venue is booked"
              and person_line_text("[lark] 在「群」说: 场地订好了") == "场地订好了", "person_line_text: English and Chinese prefixes removed")

        # 6 · person notes by source id
        idx = source_id_index(cfg)
        check(set(idx["by_id"]) == {A} and idx["by_id"][A].name == "Alex-Test.md" and idx["error"] is None,
              "source_id_index: an id on exactly one note counts, _ files skipped")
        check(idx["conflicts"] == {"ou_test_dup": ["Dup-A.md", "Dup-B.md"]}, "source_id_index: an id on two notes is a conflict (quoted and bare values)")
        miss = source_id_index(dict(cfg, paths=dict(cfg.get("paths") or {}, people_dir=str(tmp / "nope"))))
        check(miss["by_id"] == {} and "people dir missing" in (miss["error"] or ""), "source_id_index: a missing people dir is an error")

        # 7 · apply_result
        lines, cap_calls = [], []

        def fake_append_recent(note, line):
            lines.append((Path(str(note)).name, line))
            return True

        def spy_append_captures(state_db, buffer, jid, name, world, caps, **kw):
            cap_calls.append((jid, name, world, len(caps), kw.get("source"), kw.get("people_note")))
            return WI.append_captures(state_db, buffer, jid, name, world, caps, **kw) if WI else len(caps)

        ar = apply_result(cfg, f, pr, fake_append_recent, spy_append_captures, today="2026-09-14")
        check(ar["recent"] == 1 and lines == [("Alex-Test.md", "- 2026-09-13 · [lark] 在「Project Maple Team」说: " + pr["people"][0]["line"])],
              "apply_result: one news line on the note with the id, in the one shape; got %s" % lines)
        check(ar["no_id"] == ["Maya Test", "Sam Test"], "apply_result: people without a note are listed, never guessed; got %s" % ar["no_id"])
        check(ar["captures"] == 3, "apply_result: 3 captures appended; got %d" % ar["captures"])
        check([(c[0], c[3], c[4]) for c in cap_calls] == [("oc_test_team", 1, "lark"), ("oc_test_team", 2, "lark")],
              "apply_result: grouped by chat and speaker note, two per call, source lark; got %s" % [(c[0], c[3], c[4]) for c in cap_calls])
        check(cap_calls[0][5] is not None and Path(str(cap_calls[0][5])).name == "Alex-Test.md" and cap_calls[1][5] is None and cap_calls[0][2] == "work",
              "apply_result: the speaker's note and the chat's world guess go to append_captures")
        buf = Path(intake_buffer(cfg))
        check(buf.exists() and "Alex 问朋友的团队一起报名是不是也收 800" in buf.read_text(encoding="utf-8"), "apply_result: the capture landed in the intake buffer")
        check("- 🗣 09-13 11:44「Hi, my friend’s team wants to join too, same price of 800?」" in buf.read_text(encoding="utf-8")
              and "未核对" not in buf.read_text(encoding="utf-8"),
              "apply_result passes the chat's messages: second source entries carry the 🗣 mark with the stored text")
        ar2 = apply_result(cfg, f, pr, fake_append_recent, WI.append_captures if WI else (lambda *a, **k: 0), today="2026-09-14")
        check(ar2["captures"] == 0, "apply_result again: intake deduplicates, 0 appended")
        n_calls = len(cap_calls)
        off = dict(cfg, intake=dict(cfg.get("intake") or {}, enabled=False))
        ar3 = apply_result(off, f, pr, fake_append_recent, spy_append_captures)
        check(ar3["captures"] == 0 and ar3["captures_skipped"] == 3 and len(cap_calls) == n_calls,
              "apply_result: intake.enabled=false appends nothing and never calls append_captures")
        nobuf = dict(cfg, intake={"enabled": True, "buffer": ""}, paths=dict(cfg.get("paths") or {}, intake_buffer=""))
        ar4 = apply_result(nobuf, f, pr, fake_append_recent, spy_append_captures)
        check(ar4["captures"] == 0 and any("no intake buffer" in e for e in ar4["errors"]), "apply_result: enabled intake without a buffer path is an error")

        # 8 · run_nightly
        model_calls, logs = [], []

        def fake_model(p):
            model_calls.append(p)
            return model_text
        s1 = run_nightly(cfg, fake_model, fake_append_recent, WI.append_captures if WI else (lambda *a, **k: 0), day=day, runner=runner,
                         today="2026-09-14", log=logs.append)
        check(len(model_calls) == 1 and len(model_calls[0]) < 20000 and s1["ok"] is True and s1["recent"] == 1 and s1["claude_called"] is True,
              "run_nightly: one model call, one failing chat still ok, news line written")
        check(s1.get("id_conflicts") == {"ou_test_dup": ["Dup-A.md", "Dup-B.md"]} and any("person note id conflict" in e for e in s1["errors"])
              and any(x.startswith("WARNING second source: person note id conflict") for x in logs),
              "run_nightly: an id conflict is reported in the summary and the log")
        last = json.loads(last_path(cfg).read_text(encoding="utf-8"))
        check({"at", "day", "source", "ok", "n_chats", "n_msgs", "recent", "captures", "errors", "claude_called", "id_conflicts"} <= set(last)
              and last["ok"] is True and last["day"] == day, "run_nightly: second-source-last.json has the contract keys")
        check((last_path(cfg).stat().st_mode & 0o777) == 0o600, "second-source-last.json is 0600")
        ll = log_line(s1)
        check(ll.startswith("second source lark: pulled 2 chats 6 msgs · recent +1 · captures +0") and "id conflicts 1" in ll and logs[-1] == ll,
              "log_line: one line with counts and the conflict count; got %s" % ll)
        quiet = run_nightly(cfg, fake_model, fake_append_recent, spy_append_captures, day="2026-09-01",
                            runner=fx["make_runner"](messages_override={}))
        check(len(model_calls) == 1 and quiet["claude_called"] is False and quiet["n_msgs"] == 0 and quiet["ok"] is True,
              "run_nightly: a day without messages calls no model")
        own_only = {"oc_test_team": [L._fake_msg("om_x", "2026-09-02 09:00", O, "Owner Test", "@Status Bot /sync")]}
        s_own = run_nightly(cfg, fake_model, fake_append_recent, spy_append_captures, day="2026-09-02",
                            runner=fx["make_runner"](messages_override=own_only))
        check(len(model_calls) == 1 and s_own["claude_called"] is False and s_own["n_msgs"] == 1, "run_nightly: only the owner spoke, no model call")
        s3 = run_nightly(cfg, lambda p: "读不懂", fake_append_recent, spy_append_captures, day=day, runner=runner)
        check(s3["ok"] is False and json.loads(last_path(cfg).read_text(encoding="utf-8"))["ok"] is False, "run_nightly: output without JSON is ok=false and still writes the last file")
        s4 = run_nightly(cfg, fake_model, fake_append_recent, spy_append_captures, day=day, runner=fx["make_runner"](fail_all=True))
        check(s4["ok"] is False and s4["claude_called"] is False and len(model_calls) == 1, "run_nightly: every chat failing (expired login) is ok=false")
        n_calls = len(cap_calls)
        s5 = run_nightly(off, fake_model, fake_append_recent, spy_append_captures, day=day, runner=runner)
        check(s5["captures"] == 0 and s5.get("captures_skipped") == 3 and len(cap_calls) == n_calls, "run_nightly: intake.enabled=false appends no captures")
        s6 = run_nightly(dict(cfg, second_source=dict(_ss(cfg), enabled=False)), fake_model, fake_append_recent, spy_append_captures, day=day, runner=runner)
        check(s6["ok"] is False and s6["claude_called"] is False, "run_nightly: a disabled source does nothing")

        # 9 · id mapping helpers
        pm = members(cfg, ["oc_test_team"], runner=runner)
        check(set(pm["users"]) == {A, M}, "members: through the implementation")
        tg = map_targets(cfg)
        check([t["file"] for t in tg] == ["Maya-Test.md"], "map_targets: notes without the id key; got %s" % [t["file"] for t in tg])
        check(name_candidates(tg[0], pm["users"])[0][1] == M if tg else False, "name_candidates: Maya Test matches Maya Test")

        # 10 · the seed template and every shipped implementation
        seed = Path(W.payload_dir()) / "daemon" / "templates" / PROMPT_NAME
        stxt = seed.read_text(encoding="utf-8") if seed.exists() else ""
        check(all(ph in stxt for ph in ("{OWNER_LABEL}", "{OWNER_TAG}", "{SOURCE_NAME}", "{SENDER_ID_EXAMPLE}", "{CHAT_ID_EXAMPLE}",
                                        "{DAY}", "{N_CHATS}", "{N_MSGS}", "{MESSAGES}")), "daemon/templates/second-source-prompt.md has every placeholder")
        check(stxt.startswith("<!-- seed: ") and "\u2014" not in stxt and "\u2013" not in stxt and " -- " not in stxt and "Lark" not in stxt,
              "seed carries its stamp, no em dash, en dash or double hyphen, no source name written in")
        L._selftest(lambda name, cond: check(cond, name), tmp)
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)
    print("selftest: %d/%d passed" % (n[0] - len(fails), n[0]))
    return 1 if fails else 0


# ---------------------------------------------------------------- CLI

def _dry_run(cfg, day):
    src = source_name(cfg)
    print("second source: %s (enabled=%s) · whitelist %s" % (src, enabled(cfg), whitelist_path(cfg)))
    wl, err = read_whitelist(cfg)
    if err:
        print("! " + err)
        return 1
    ex = excluded_chats(cfg, wl)
    pc = pullable_chats(cfg, wl)
    print("whitelist %d · pullable %d · excluded %d" % (len(wl), len(pc), len(ex)))
    reasons = {}
    for c, r in ex:
        reasons.setdefault(r, []).append(_title(c))
    for r, names in reasons.items():
        print("  excluded [%s] %d: %s" % (r, len(names), ", ".join(names)))
    f = fetch_day(cfg, day=day)
    print("day %s · %d chats %d messages · owner id %s" % (f["day"], f["n_chats"], f["n_msgs"], ",".join(f["owner_ids"]) or "?"))
    for c in f["chats"]:
        n_own = sum(1 for m in c["messages"] if m.get("is_owner"))
        speakers = sorted(set(m["sender_name"] for m in c["messages"] if not m.get("is_owner")))
        print("  %-40s %3d msgs (owner %d) · %s · world %s" % (c["title"], len(c["messages"]), n_own, ", ".join(speakers) or "-", c["world_guess"]))
    for s in f["skipped"]:
        print("  skipped %s: %s" % (s.get("title"), s.get("why")))
    conflicts = source_id_conflicts(cfg)
    for k, names in sorted(conflicts.items()):
        print("  ! person note id conflict: %s %s on %s" % (id_key(cfg), k, ", ".join(names)))
    print("errors %d" % len(f["errors"]))
    for e in f["errors"]:
        print("  ! " + e)
    if others_count(f) and not f["fatal"]:
        p = build_prompt(f, cfg=cfg, log=print)
        print("prompt %d chars (message area limit %d): the nightly run would call the model once; nothing written now" % (
            len(p), int(opt(cfg, "prompt_max_chars", PROMPT_MAX_CHARS) or PROMPT_MAX_CHARS)))
    else:
        print("nobody but the owner spoke: the nightly run would not call the model")
    return 0


def _probe(cfg, chat_ids):
    pm = members(cfg, chat_ids)
    print("members (chats up to 300 people): users %d · bots %d" % (len(pm.get("users") or {}), len(pm.get("bots") or {})))
    for uid, u in sorted((pm.get("users") or {}).items(), key=lambda x: str(x[1].get("name")).lower()):
        print("  %s · %s · %s" % (uid, u.get("name"), "; ".join((u.get("chats") or [])[:6])))
    print("bots:")
    for uid, b in (pm.get("bots") or {}).items():
        print("  %s · %s · %s" % (uid, b.get("name"), "; ".join((b.get("chats") or [])[:3])))
    for e in pm.get("errors") or []:
        print("  ! " + e)
    key = id_key(cfg)
    print("\nperson notes to map (%s) and name candidates; nothing is written, a person adds %s to the note:" % (
        "roles in second_source.map_roles" if opt(cfg, "map_roles") else "notes without %s" % key, key))
    stop = opt(cfg, "name_stopwords", []) or []
    for t in map_targets(cfg):
        print("  %s · %s · %s · %s=%s" % (t["file"], t["title"], ",".join(t["roles"]), key, t["id"] or "-"))
        for sc, uid, nm in name_candidates(t, pm.get("users") or {}, stop):
            print("      candidate %d · %s · %s · %s" % (sc, uid, nm, "; ".join(((pm.get("users") or {}).get(uid) or {}).get("chats", [])[:4])))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Second message source: yesterday's messages -> person note news lines + intake captures (the daemon calls run_nightly).")
    ap.add_argument("--config", default=None, help="live config.json (default: env WA_LIVE_CONFIG or ~/WhatsApp-Live/live/config.json)")
    ap.add_argument("--dry-run", action="store_true", help="really pull one day and print counts, exclusions, errors; no model, nothing written")
    ap.add_argument("--day", default=None, help="YYYY-MM-DD, default yesterday")
    ap.add_argument("--probe-members", action="store_true", help="print chat members and person note candidates for the id mapping (read only)")
    ap.add_argument("--chat-id", action="append", default=None, help="with --probe-members: only these chats")
    ap.add_argument("--selftest", action="store_true", help="fake runner, temp folder and fake notes; never touches the source or real files")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if a.day and not re.match(r"^\d{4}-\d{2}-\d{2}$", a.day):
        ap.error("--day must be YYYY-MM-DD")
    if not (a.dry_run or a.probe_members):
        ap.print_help()
        return 0
    try:
        cfg = W.load_config(a.config)
    except (OSError, ValueError) as e:
        print("config: %s" % e)
        return 2
    if source_name(cfg) == NONE:
        print("second_source.source is none: nothing to pull")
        return 0
    if a.dry_run:
        return _dry_run(cfg, a.day)
    return _probe(cfg, a.chat_id)


if __name__ == "__main__":
    sys.exit(main())
