#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wa_common.py · the shared base every WhatsApp Live module imports (contract: DESIGN.md §12).

  config        DEFAULT_CONFIG · DEFAULTS · load_config · apply_defaults · payload_dir · home_dir · live_dir ·
                owner_lang · source_label
  vocabulary    vocab · world_is_business · vocab_label
  binaries      resolve_bin · subprocess_env
  WhatsApp      wa_root · CONTENT_TYPES · AUDIO_TYPE · norm_phone · phone_kind · lid_of_phone · phone_to_jid ·
                phone_of_jid · jid_forms · chat_message_count · transcript_key · invalidate_wa_cache
  person notes  parse_frontmatter · fm_list · people_index · people_note_for
  files         now_iso · read_json · write_json_atomic · repair_inner_quotes
  push outlet   notify_wanted · notify_send · notify_deliver

`load_config` is the one place defaults are filled: every module, the daemon, the CLIs and the evening job see the
same values for the same file. Values keep the owner's key names; `<home>` `<live>` `<payload>` `<vault>` and `~`
are resolved, `<auto>` binaries are looked up. Derived keys added at load (never written back): `config_path`,
`live_dir`, `state_db`, `sweep_work_dir`, `_load_warnings`.

The WhatsApp Desktop databases are only ever opened read only. The only file this module writes is the one a caller
hands to write_json_atomic. The environment variable WA_LIVE_WA_ROOT points every WhatsApp lookup at another folder
(selftests use it with fake databases).

CLI
  wa_common.py --selftest                              temp dirs and fakes only, never the real WhatsApp database,
                                                       vault, network or push outlet
  wa_common.py --notify [--config P] [--html] [--kind error|needs_me|digest|info]
                                                       send the text on stdin through the configured outlet
                                                       (the evening intake job uses this); exit 0 when delivered
  wa_common.py --print-defaults                        every default as JSON (feeds references/config-reference.md)

Python 3.9 standard library only (the daemon runs on /usr/bin/python3).
"""

import argparse
import copy
import datetime as dt
import glob
import hashlib
import html
import json
import logging
import os
import re
import shlex
import shutil
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_log = logging.getLogger("wa_live.common")

CONFIG_ENV = "WA_LIVE_CONFIG"
WA_ROOT_ENV = "WA_LIVE_WA_ROOT"
DEFAULT_HOME = "~/WhatsApp-Live"
DEFAULT_CONFIG = Path(os.environ.get(CONFIG_ENV) or (DEFAULT_HOME + "/live/config.json")).expanduser()
WA_ROOT_DEFAULT = "~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared"
CORE_EPOCH_UTC = dt.datetime(2001, 1, 1, tzinfo=dt.timezone.utc)

# One table for "which WhatsApp message types are content" (the doorbell, the reader, backfill, voice all agree).
# Types not listed are system messages: logged, never gated in.
CONTENT_TYPES = {
    0: "文字", 1: "图片", 2: "影片", 3: "语音", 5: "位置", 7: "文字",
    8: "文件", 11: "GIF", 14: "名片", 15: "贴图", 20: "图片", 33: "图片",
    41: "文字", 42: "频道图文",
}
CONTENT_TYPES_EN = {
    0: "text", 1: "image", 2: "video", 3: "voice", 5: "location", 7: "text",
    8: "file", 11: "GIF", 14: "contact card", 15: "sticker", 20: "image", 33: "image",
    41: "text", 42: "channel post",
}
AUDIO_TYPE = 3
TEXT_TYPES = (0, 7, 41)
IMAGE_TYPES = (1, 20, 33, 42)
VIDEO_TYPES = (2, 11)

MIN_PHONE_DIGITS = 8

# ---------------------------------------------------------------- defaults (DESIGN.md §4)

DEFAULT_WORLDS = [
    {"key": "personal", "label": "私人", "label_en": "Personal", "business": False},
    {"key": "work", "label": "工作", "label_en": "Work", "business": True},
    {"key": "other", "label": "其他", "label_en": "Other", "business": False},
]
DEFAULT_STAGES = [
    {"key": "new", "label": "新", "label_en": "New"},
    {"key": "talking", "label": "在谈", "label_en": "Talking"},
    {"key": "offered", "label": "已报价", "label_en": "Offered"},
    {"key": "won", "label": "已成交", "label_en": "Won"},
    {"key": "active", "label": "服务中", "label_en": "Active"},
    {"key": "done", "label": "结束", "label_en": "Done"},
    {"key": "parked", "label": "搁置", "label_en": "Parked"},
]
DEFAULT_PRIORITIES = [
    {"key": "now", "label": "现在", "label_en": "Now"},
    {"key": "soon", "label": "这几天", "label_en": "Soon"},
    {"key": "low", "label": "不急", "label_en": "Low"},
]
DEFAULT_PARK_FROM = ["new", "talking", "offered"]
DEFAULT_FLOOR = {"paid": "won", "paid_attended": "active", "via_attended": "active"}

DEFAULTS = {
    "version": 1,
    "my_phone": "",
    "my_names": [],
    "owner_label": "",
    "owner_marker": "",
    "direct_policy": "all",
    "group_policies": {},
    "unlisted_group_policy": "skip",
    "ignored_jids": [],
    "debounce": {"quiet_seconds": 45, "max_wait_seconds": 180, "global_concurrency": 2},
    "doorbell": {"enabled": True, "wal_path": "<auto>", "poll_seconds": 2, "stale_alert_hours": 6,
                 "full_rescan_seconds": 60, "lookback": 300, "max_message_age_hours": 48},
    "brain": {"model": "claude-sonnet-5", "cwd": "<home>/brain-cwd", "context_days": 7, "group_context_days": 3,
              "run_timeout_seconds": 600, "retries": 1, "sweep_timeout_seconds": 1500, "max_turns": 12,
              "warm_days": 2, "ground_every_days": 7,
              "whisper_model": "~/.cache/whisper-models/openai_whisper-large-v3-v20240930",
              "whisper_language": "", "transcribe": True},
    "bin": {"claude": "<auto>", "python": "/usr/bin/python3", "node": "<auto>", "ffmpeg": "<auto>",
            "whisperkit": "<auto>", "lark_cli": "<auto>", "path_env": "<auto>"},
    "paths": {"home": DEFAULT_HOME, "live_dir": "<home>/live", "vault": "", "people_dir": "", "doctrine": "",
              "person_playbook": "", "intake_buffer": "", "filing_log": "", "decisions_dir": "",
              "reader_dir": "<payload>", "dossier_dir": "<payload>"},
    "vocab": {"worlds": DEFAULT_WORLDS, "stages": DEFAULT_STAGES, "park_from": DEFAULT_PARK_FROM,
              "priorities": DEFAULT_PRIORITIES, "business_context": ""},
    "stage_rules": {"parked_after_days": 21, "group_date_done": False},
    "board": {"enabled": True, "bind": "127.0.0.1", "port": 8787, "others_days": 3, "others_limit": 120,
              "lang": "zh", "show_usd": False},
    "notify": {"outlet": "none", "mode": "errors", "telegram_file": "<live>/telegram.json", "command": ""},
    "dossier": {"enabled": False, "model": "claude-opus-5", "auto_open_worlds": [], "auto_open_min_stage": "talking",
                "pack_days": 30, "nightly_at": "02:30", "nightly_min_msgs": 5, "nightly_cap": 8, "stale_days": 14,
                "min_gap_hours": 6, "recent_lines": 12, "max_turns": 24, "run_timeout_seconds": 1200},
    "facts": {"enabled": False, "source": "none", "at": "03:00", "phone_default_cc": "", "phone_valid": [],
              "label": "", "currency": "", "recent_days": 30, "floor": DEFAULT_FLOOR,
              "csv": {"path": "", "columns": {}, "day_first": True},
              "lark": {"people_tables": [], "payment_tables": []}},
    "second_source": {"enabled": False, "source": "none", "at": "03:10",
                      "lark": {"whitelist": "<live>/second-source-whitelist.json"}},
    "calendar": {"destination": "none", "lark": {"calendar_id": ""}},
    "backup": {"enabled": False, "dest": "", "require_process": "", "at": "03:30", "keep": 7},
    "digest": {"enabled": True, "at": "08:00", "push": True},
    "intake": {"enabled": False, "buffer": "", "evening_push": False, "evening_at": "20:00", "rules_file": ""},
    "voice": {"enabled": True, "examples": 12, "pairs": "<live>/voice-pairs.jsonl"},
    "backfill": {"days": 30, "min_msgs": 10, "batch_size": 10, "respect_gate": True, "max_turns": 20, "max_images": 4},
    "launchd": {"label": "local.whatsapp-live", "intake_label": "local.whatsapp-live.intake"},
}

# Keys whose string values are owner text, never paths: placeholders and ~ are not touched inside them.
_TEXT_KEYS = {"vocab", "group_policies", "my_names", "owner_label", "owner_marker", "my_phone", "ignored_jids"}

BIN_NAMES = {"claude": "claude", "python": "python3", "node": "node", "ffmpeg": "ffmpeg",
             "whisperkit": "whisperkit-cli", "lark_cli": "lark-cli", "tailscale": "tailscale", "sqlite3": "sqlite3"}
STANDARD_PATH = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]


def now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


def payload_dir():
    """The installed payload root (the folder that holds daemon/, skills/, templates)."""
    return Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- config

def load_config(path=None):
    """Read live/config.json and fill every default. Path order: the argument, env WA_LIVE_CONFIG,
    ~/WhatsApp-Live/live/config.json. Raises FileNotFoundError (missing) or ValueError (not valid JSON)."""
    p = Path(os.path.expanduser(str(path or os.environ.get(CONFIG_ENV) or DEFAULT_CONFIG)))
    if not p.exists():
        raise FileNotFoundError("config not found: %s (run the install wizard station C2, or pass --config)" % p)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError("config is not valid JSON: %s (line %d column %d: %s)" % (p, e.lineno, e.colno, e.msg))
    if not isinstance(raw, dict):
        raise ValueError("config must be a JSON object: %s" % p)
    return apply_defaults(raw, p)


def _merge_defaults(cfg, defaults, warnings, where=""):
    for k, dv in defaults.items():
        if k not in cfg or cfg[k] is None:
            cfg[k] = copy.deepcopy(dv)
            continue
        if isinstance(dv, dict):
            if not isinstance(cfg[k], dict):
                warnings.append("%s%s should be an object; the default was used" % (where, k))
                cfg[k] = copy.deepcopy(dv)
            else:
                _merge_defaults(cfg[k], dv, warnings, where + k + ".")
        elif isinstance(dv, list) and not isinstance(cfg[k], list):
            warnings.append("%s%s should be a list; the default was used" % (where, k))
            cfg[k] = copy.deepcopy(dv)


def _walk_strings(obj, fn, skip_top=True):
    """Apply fn to every string value, skipping owner text keys at the top level."""
    if isinstance(obj, dict):
        for k in list(obj.keys()):
            if skip_top and k in _TEXT_KEYS:
                continue
            v = obj[k]
            if isinstance(v, str):
                obj[k] = fn(v)
            elif isinstance(v, (dict, list)):
                _walk_strings(v, fn, skip_top=False)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                obj[i] = fn(v)
            elif isinstance(v, (dict, list)):
                _walk_strings(v, fn, skip_top=False)


def _initials(label):
    words = re.findall(r"[A-Za-z]+", str(label or ""))
    if len(words) >= 2:
        m = "".join(w[0] for w in words)[:6]
    elif len(words) == 1:
        m = words[0][:2]
    else:
        m = ""
    m = m.upper()
    return m if len(m) >= 2 else "ME"


def apply_defaults(cfg, config_path=None):
    """Fill every §4 default into cfg (in place, also returned), resolve placeholders and `~`, look up `<auto>` bins,
    and add the derived keys. Idempotent: applying it twice gives the same dict.
    paths.home defaults to the parent of the config folder when that folder is named `live`, else ~/WhatsApp-Live;
    paths.live_dir defaults to the folder that holds config.json (normally <home>/live)."""
    if not isinstance(cfg, dict):
        raise ValueError("config must be a JSON object")
    warnings = []
    raw_paths = cfg.get("paths") if isinstance(cfg.get("paths"), dict) else {}
    user_home = bool(str(raw_paths.get("home") or "").strip())
    user_live = bool(str(raw_paths.get("live_dir") or "").strip())

    # legacy shapes: top level notify command string and notify_mode
    if isinstance(cfg.get("notify"), str):
        cmd = cfg.pop("notify")
        cfg["notify"] = {"outlet": "command" if cmd.strip() else "none", "command": cmd}
        warnings.append("notify was a command string; read as notify.outlet=command")
    if "notify_mode" in cfg and isinstance(cfg.get("notify"), dict):
        cfg["notify"].setdefault("mode", cfg.pop("notify_mode"))
    for k in ("_load_warnings",):
        cfg.pop(k, None)
    _merge_defaults(cfg, DEFAULTS, warnings)

    cp = Path(os.path.expanduser(str(config_path))) if config_path else None
    paths = cfg["paths"]
    if not user_home and cp is not None and cp.parent.name == "live":
        home = cp.parent.parent
    else:
        h = str(paths.get("home") or DEFAULT_HOME)
        home = Path(os.path.expanduser(h if not h.startswith("<") else DEFAULT_HOME))
    if not user_live and cp is not None:
        live = cp.parent
    else:
        lv = str(paths.get("live_dir") or "<home>/live").replace("<home>", str(home))
        live = Path(os.path.expanduser(lv))
    paths["home"] = str(home)
    paths["live_dir"] = str(live)
    vault = str(paths.get("vault") or "")

    subs = [("<home>", str(home)), ("<live>", str(live)), ("<payload>", str(payload_dir()))]
    if vault and not vault.startswith("<vault>"):
        subs.append(("<vault>", os.path.expanduser(vault)))

    def resolve(s):
        for tok, val in subs:
            if tok in s:
                s = s.replace(tok, val)
        if s == "~" or s.startswith("~/"):
            s = os.path.expanduser(s)
        return s
    _walk_strings(cfg, resolve)

    # vault paths: a relative value is relative to paths.vault (the same rule dossier_core uses), and the two files every
    # my-second-brain vault has get defaults when a vault is set, so every module reads the same absolute path
    if vault:
        vroot = Path(os.path.expanduser(resolve(vault)))
        for key, default in (("doctrine", "99_Meta/structure-doctrine.md"), ("filing_log", "99_Meta/filing-log.md"),
                             ("people_dir", ""), ("person_playbook", ""), ("decisions_dir", ""), ("intake_buffer", "")):
            val = str(paths.get(key) or "").strip()
            if not val and default:
                val = default
            if val and not os.path.isabs(os.path.expanduser(val)):
                val = str(vroot / val)
            if val:
                paths[key] = val
        ibuf = str(cfg["intake"].get("buffer") or "").strip()
        if ibuf and not os.path.isabs(os.path.expanduser(ibuf)):
            cfg["intake"]["buffer"] = str(vroot / ibuf)

    # the intake buffer has two historic spellings; intake.buffer is the one modules read
    ib, pb = str(cfg["intake"].get("buffer") or ""), str(paths.get("intake_buffer") or "")
    if ib and pb and ib != pb:
        warnings.append("intake.buffer and paths.intake_buffer differ; intake.buffer is used")
    if not ib and pb:
        cfg["intake"]["buffer"] = pb
    elif ib and not pb:
        paths["intake_buffer"] = ib

    if str(cfg["doorbell"].get("wal_path") or "").startswith("<auto") or not cfg["doorbell"].get("wal_path"):
        cfg["doorbell"]["wal_path"] = str(wa_root() / "ChatStorage.sqlite-wal")
    for name in list(cfg["bin"].keys()):
        if name == "path_env":
            continue
        v = cfg["bin"].get(name)
        if not isinstance(v, str) or not v or v.startswith("<auto"):
            cfg["bin"][name] = resolve_bin(cfg, name) or ""
    pe = cfg["bin"].get("path_env")
    if not isinstance(pe, str) or not pe or pe.startswith("<auto"):
        cfg["bin"]["path_env"] = _auto_path_env(cfg)

    marker = str(cfg.get("owner_marker") or "").strip()
    if not marker:
        cfg["owner_marker"] = _initials(cfg.get("owner_label"))
    elif not re.fullmatch(r"[A-Za-z]{2,6}", marker):
        warnings.append("owner_marker should be 2 to 6 ASCII letters")

    if cfg["board"].get("lang") not in ("zh", "en"):
        warnings.append("board.lang should be zh or en")

    cfg["config_path"] = str(cp) if cp is not None else str(cfg.get("config_path") or "")
    cfg["live_dir"] = str(live)
    cfg["state_db"] = str(live / "state.sqlite")
    cfg["sweep_work_dir"] = str(home)
    cfg["_load_warnings"] = warnings
    return cfg


def home_dir(cfg):
    p = ((cfg or {}).get("paths") or {}).get("home") or DEFAULT_HOME
    return Path(os.path.expanduser(str(p)))


def live_dir(cfg):
    c = cfg or {}
    p = (c.get("paths") or {}).get("live_dir") or c.get("live_dir")
    if not p and c.get("state_db"):
        return Path(str(c["state_db"])).parent
    if not p:
        return home_dir(c) / "live"
    return Path(os.path.expanduser(str(p)))


def owner_lang(cfg):
    """The owner's language for machine written text (digest, intake entries, facts lines): zh or en (board.lang)."""
    lang = str(((cfg or {}).get("board") or {}).get("lang") or "zh").strip().lower()
    return "en" if lang.startswith("en") else "zh"


_SOURCE_NAMES = {"csv": "CSV", "lark": "Lark", "ics": "ICS", "telegram": "Telegram", "command": "command", "none": ""}


def source_label(cfg, section):
    """A human label for the implementation behind a config block (facts, second_source, calendar, notify):
    the block's `label` when set, else the implementation name ("csv" becomes "CSV")."""
    sec = (cfg or {}).get(section) or {}
    lab = str(sec.get("label") or "").strip() if isinstance(sec, dict) else ""
    if lab:
        return lab
    src = ""
    if isinstance(sec, dict):
        src = str(sec.get("source") or sec.get("destination") or sec.get("outlet") or "").strip().lower()
    return _SOURCE_NAMES.get(src, src.upper() if len(src) <= 4 else src.title())


# ---------------------------------------------------------------- vocabulary

def _norm_entries(raw, default, business=False, stage=False):
    out, seen = [], set()
    for e in (raw if isinstance(raw, list) else []):
        if isinstance(e, str):
            e = {"key": e}
        if not isinstance(e, dict):
            continue
        k = str(e.get("key") or "").strip()
        if not k or k in seen:
            continue
        seen.add(k)
        lab = str(e.get("label") or k)
        item = {"key": k, "label": lab, "label_en": str(e.get("label_en") or lab)}
        if business:
            item["business"] = bool(e.get("business"))
        if stage and "side" in e:
            item["side"] = bool(e.get("side"))
        out.append(item)
    if not out and default is not None:
        return _norm_entries(default, None, business, stage)
    return out


def vocab(cfg):
    """Normalized vocabulary: entry lists, key lists, label maps (zh `label`, en `label_en`), the one way stage order
    and the side stage (an entry with "side": true, else the key "parked"), business worlds, park_from."""
    v = (cfg or {}).get("vocab") or {}
    worlds = _norm_entries(v.get("worlds"), DEFAULT_WORLDS, business=True)
    stages = _norm_entries(v.get("stages"), DEFAULT_STAGES, stage=True)
    prios = _norm_entries(v.get("priorities"), DEFAULT_PRIORITIES)
    stage_keys = [s["key"] for s in stages]
    if any("side" in s for s in stages):
        side = [s["key"] for s in stages if s.get("side")]
    else:
        side = ["parked"] if "parked" in stage_keys else []
    order = [k for k in stage_keys if k not in side]
    pf = v.get("park_from") if isinstance(v.get("park_from"), list) else DEFAULT_PARK_FROM
    park_from = [k for k in pf if k in order]

    def labels(items, field):
        return dict((x["key"], x[field]) for x in items)
    return {
        "worlds": worlds, "stages": stages, "priorities": prios, "park_from": park_from,
        "world_keys": [w["key"] for w in worlds], "stage_keys": stage_keys, "priority_keys": [p["key"] for p in prios],
        "stage_order": order, "side_stages": side, "parked": side[0] if side else None,
        "business_worlds": [w["key"] for w in worlds if w.get("business")],
        "labels": {"world": labels(worlds, "label"), "stage": labels(stages, "label"), "priority": labels(prios, "label")},
        "labels_en": {"world": labels(worlds, "label_en"), "stage": labels(stages, "label_en"),
                      "priority": labels(prios, "label_en")},
        "business_context": str(v.get("business_context") or ""),
    }


def world_is_business(cfg, key):
    return bool(key) and key in vocab(cfg)["business_worlds"]


def vocab_label(cfg, kind, key, lang=None):
    """Label of a world, stage or priority key in the owner's language; the key itself when unknown."""
    if not key:
        return ""
    v = vocab(cfg)
    table = v["labels_en"] if (lang or owner_lang(cfg)) == "en" else v["labels"]
    return (table.get(kind) or {}).get(key) or str(key)


# ---------------------------------------------------------------- binaries and environment

def _is_exec(p):
    return bool(p) and os.path.isfile(p) and os.access(p, os.X_OK)


def _version_key(path):
    m = re.search(r"v(\d+)\.(\d+)\.(\d+)", path)
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def _search_dirs():
    home = Path.home()
    dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    dirs += ["/opt/homebrew/bin", "/usr/local/bin", str(home / ".npm-global/bin"), str(home / ".local/bin"),
             str(home / ".claude/local"), str(home / ".bun/bin"), str(home / ".volta/bin")]
    dirs += sorted(glob.glob(str(home / ".nvm/versions/node/*/bin")), key=_version_key, reverse=True)
    dirs += ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    out, seen = [], set()
    for d in dirs:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _which(exe):
    for d in _search_dirs():
        p = os.path.join(d, exe)
        if _is_exec(p):
            return p
    return None


def resolve_bin(cfg, name):
    """Absolute path of a tool, or None. A configured absolute path wins while it exists; a bare name is looked up;
    `<auto>` or a path that disappeared falls back to a search of PATH, Homebrew, npm global, ~/.local/bin,
    ~/.claude/local and nvm (newest first)."""
    val = (((cfg or {}).get("bin") or {}).get(name))
    if isinstance(val, str) and val.strip() and not val.startswith("<auto"):
        v = os.path.expanduser(val.strip())
        if os.sep in v:
            if _is_exec(v):
                return v
        else:
            found = _which(v)
            if found:
                return found
    if name == "python" and _is_exec("/usr/bin/python3"):
        return "/usr/bin/python3"
    return _which(BIN_NAMES.get(name, name))


def _auto_path_env(cfg):
    dirs = []
    for n in ("node", "claude", "ffmpeg", "whisperkit", "lark_cli", "python"):
        p = resolve_bin(cfg, n)
        if p:
            dirs.append(os.path.dirname(p))
    dirs += STANDARD_PATH
    out, seen = [], set()
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return os.pathsep.join(out)


# Every headless `claude -p` the kit starts runs without Claude Code's auto-memory and without CLAUDE.md files:
# otherwise an owner's memory index and the CLAUDE.md files above the working folder load into every run,
# and the brain spends its turns opening memory files instead of reading the conversation.
# Proof is the harness's own word: the run's init event carries no `memory_paths` (probe-claude checks it).
CLAUDE_QUIET_ENV = {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1"}


def subprocess_env(cfg):
    """Environment for child processes started by the daemon under launchd (whose PATH is minimal):
    PATH from bin.path_env, HOME, LANG, WA_LIVE_CONFIG (so child scripts read the same config), WA_WHISPER_MODEL,
    and CLAUDE_QUIET_ENV (Claude memory and CLAUDE.md files off for every headless run)."""
    env = dict(os.environ)
    env.update(CLAUDE_QUIET_ENV)
    pe = ((cfg or {}).get("bin") or {}).get("path_env")
    if not isinstance(pe, str) or not pe or pe.startswith("<auto"):
        pe = _auto_path_env(cfg or {})
    env["PATH"] = pe
    env.setdefault("HOME", str(Path.home()))
    if not env.get("LANG"):
        env["LANG"] = "en_US.UTF-8"
    if (cfg or {}).get("config_path"):
        env[CONFIG_ENV] = str(cfg["config_path"])
    wm = ((cfg or {}).get("brain") or {}).get("whisper_model")
    if wm:
        env["WA_WHISPER_MODEL"] = os.path.expanduser(str(wm))
    return env


# ---------------------------------------------------------------- WhatsApp Desktop databases (read only)

def wa_root():
    return Path(os.path.expanduser(os.environ.get(WA_ROOT_ENV) or WA_ROOT_DEFAULT))


def _ro(path, timeout=5):
    return sqlite3.connect("file:%s?mode=ro" % urllib.parse.quote(str(path)), uri=True, timeout=timeout)


def _digits(s):
    return re.sub(r"\D", "", str(s or "").split("@")[0])


WA_CACHE_SECONDS = 600
_WA_LOCK = threading.Lock()
_WA = {"root": None, "loaded_at": 0.0, "phone_to_lid": None, "lid_to_phone": None, "sessions": None, "errors": []}


def invalidate_wa_cache():
    """The next lookup reloads the tables (the facts job calls this before a run). Readers holding a snapshot keep
    working: maps are replaced, never mutated or cleared."""
    with _WA_LOCK:
        _WA["loaded_at"] = 0.0


def _wa_load(force=False, max_age=WA_CACHE_SECONDS):
    """A snapshot of the phone to LID map, the LID to phone map and the set of chat jids, read in one go and kept for
    max_age seconds (reloaded after that, so a chat opened after the daemon started is seen). Thread safe: every caller gets its own shallow copy of the cache entry."""
    root = wa_root()
    with _WA_LOCK:
        fresh = (_WA["phone_to_lid"] is not None and _WA["root"] == str(root)
                 and time.time() - _WA["loaded_at"] < max_age)
        if fresh and not force:
            return dict(_WA)
        p2l, l2p, sessions, errors = {}, {}, set(), []

        def put(lid, phone, override=True):
            d = _digits(phone)
            lid = str(lid or "").split("@")[0].strip()
            if not lid or len(d) < MIN_PHONE_DIGITS:
                return
            if override or d not in p2l:
                p2l[d] = lid
            if override or lid not in l2p:
                l2p[lid] = d
        try:
            c = _ro(root / "LID.sqlite")
            try:
                for lid, ph in c.execute("SELECT ZIDENTIFIER, ZPHONENUMBER FROM ZWAZACCOUNT"):
                    put(lid, ph)
                try:
                    for lid, ph in c.execute("SELECT ZLID, ZPHONENUMBER FROM ZWAPHONENUMBERLIDPAIR ORDER BY ZTIMESTAMP"):
                        put(lid, ph)
                except sqlite3.Error:
                    pass
            finally:
                c.close()
        except sqlite3.Error as e:
            errors.append("LID.sqlite: %s" % e)
        try:
            c = _ro(root / "ContactsV2.sqlite")
            try:
                for lid, ph, wid in c.execute("SELECT ZLID, ZPHONENUMBER, ZWHATSAPPID FROM ZWAADDRESSBOOKCONTACT"):
                    put(lid, ph or wid, override=False)
            finally:
                c.close()
        except sqlite3.Error as e:
            errors.append("ContactsV2.sqlite: %s" % e)
        try:
            c = _ro(root / "ChatStorage.sqlite")
            try:
                for (jid,) in c.execute("SELECT ZCONTACTJID FROM ZWACHATSESSION"):
                    if jid:
                        sessions.add(jid)
            finally:
                c.close()
        except sqlite3.Error as e:
            errors.append("ChatStorage.sqlite: %s" % e)
        _WA.update(root=str(root), loaded_at=time.time(), phone_to_lid=p2l, lid_to_phone=l2p,
                   sessions=sessions, errors=errors)
        return dict(_WA)


def wa_lookup_errors():
    """Why the last WhatsApp lookup load could not read a database (empty list when all three opened)."""
    return list(_wa_load()["errors"])


def norm_phone(s, cfg=None):
    """Any spelling of a phone number to digits with country code, or None.
    "+60 12-000 0001" becomes "60120000001"; "0060120000001" drops the 00; a leading 0 gets facts.phone_default_cc
    (without a configured code it stays as written). A jid ("<digits>@s.whatsapp.net") gives its digits."""
    if s is None:
        return None
    raw = str(s).strip()
    d = re.sub(r"\D", "", raw.split("@")[0])
    if not d:
        return None
    if raw.startswith("+"):
        return d
    if d.startswith("00"):
        return d[2:] or None
    cc = re.sub(r"\D", "", str(((cfg or {}).get("facts") or {}).get("phone_default_cc") or ""))
    if cc and d.startswith("0"):
        return cc + d[1:]
    return d


def _phone_rules(cfg):
    rules = []
    for r in (((cfg or {}).get("facts") or {}).get("phone_valid") or []):
        cc, lo, hi, label = "", MIN_PHONE_DIGITS, 15, None
        if isinstance(r, str):
            m = re.fullmatch(r"\s*\+?(\d+)\s*(?::\s*(\d+)\s*(?:-\s*(\d+))?)?\s*", r)
            if not m:
                continue
            cc = m.group(1)
            if m.group(2):
                lo = int(m.group(2))
                hi = int(m.group(3) or m.group(2))
        elif isinstance(r, dict):
            cc = re.sub(r"\D", "", str(r.get("cc") or ""))
            if r.get("len"):
                lo = hi = int(r["len"])
            lo = int(r.get("min_len") or lo)
            hi = int(r.get("max_len") or hi)
            label = r.get("label")
        else:
            continue
        if cc:
            rules.append({"cc": cc, "min": lo, "max": hi, "label": str(label or cc)})
    rules.sort(key=lambda x: -len(x["cc"]))
    return rules


def phone_kind(digits, cfg=None):
    """Country check for the "phone unparsable" list only (never a gate for matching).
    facts.phone_valid entries: "60:11-12", "65:10", "44" or {"cc": "60", "min_len": 11, "max_len": 12, "label": "MY"};
    lengths count every digit including the country code. With no rules any 8 to 15 digit number is "any"."""
    d = re.sub(r"\D", "", str(digits or ""))
    if not d:
        return None
    rules = _phone_rules(cfg)
    if not rules:
        return "any" if MIN_PHONE_DIGITS <= len(d) <= 15 else None
    for r in rules:
        if d.startswith(r["cc"]) and r["min"] <= len(d) <= r["max"]:
            return r["label"]
    return None


def lid_of_phone(phone, max_age=WA_CACHE_SECONDS):
    """Phone to the WhatsApp LID identifier (without @lid), whether or not there is a chat; None when unknown."""
    d = _digits(phone)
    if not d:
        return None
    return _wa_load(max_age=max_age)["phone_to_lid"].get(d)


def phone_to_jid(phone, refresh=False):
    """{"jid": the one to one chat jid or None, "lid": the known LID or None, "has_chat": bool,
    "how": "phone-chat" | "lid-chat" | "known-no-chat" | "unknown"}. The chat on the phone jid is tried first,
    then the chat on the LID jid. The lookup tables reload after 10 minutes or when refresh=True."""
    out = {"jid": None, "lid": None, "has_chat": False, "how": "unknown"}
    d = _digits(phone)
    if not d:
        return out
    c = _wa_load(force=bool(refresh))
    lid = c["phone_to_lid"].get(d)
    if lid:
        out["lid"] = lid
    pj = d + "@s.whatsapp.net"
    if pj in c["sessions"]:
        out.update(jid=pj, has_chat=True, how="phone-chat")
        return out
    if lid:
        lj = lid + "@lid"
        if lj in c["sessions"]:
            out.update(jid=lj, has_chat=True, how="lid-chat")
        else:
            out["how"] = "known-no-chat"
    return out


def phone_of_jid(jid):
    """One to one jid to phone digits (a phone jid carries them, a LID jid goes through the lookup tables); groups None."""
    if not jid or str(jid).endswith("@g.us"):
        return None
    j = str(jid)
    head = j.split("@")[0]
    if j.endswith("@s.whatsapp.net") and head.isdigit():
        return head
    if j.endswith("@lid"):
        return _wa_load()["lid_to_phone"].get(head)
    return None


def jid_forms(jid):
    """Both spellings of one person: {"<lid>@lid", "<phone>@s.whatsapp.net"} (the input always included); groups
    return only themselves."""
    if not jid:
        return set()
    j = str(jid)
    forms = {j}
    if j.endswith("@g.us"):
        return forms
    head = j.split("@")[0]
    if j.endswith("@lid"):
        ph = phone_of_jid(j)
        if ph:
            forms.add(ph + "@s.whatsapp.net")
    elif j.endswith("@s.whatsapp.net") and head.isdigit():
        lid = lid_of_phone(head)
        if lid:
            forms.add(lid + "@lid")
    return forms


def chat_message_count(jid, days=30, content_only=True):
    """Messages in this chat in the last `days` days, both directions; with content_only only CONTENT_TYPES count
    (system notices never make a silent chat look active). 0 when unreadable."""
    if not jid:
        return 0
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days) - CORE_EPOCH_UTC).total_seconds()
    sql = ("SELECT COUNT(*) FROM ZWAMESSAGE m JOIN ZWACHATSESSION s ON s.Z_PK = m.ZCHATSESSION "
           "WHERE s.ZCONTACTJID = ? AND m.ZMESSAGEDATE >= ?")
    if content_only:
        sql += " AND m.ZMESSAGETYPE IN (%s)" % ",".join(str(t) for t in sorted(CONTENT_TYPES))
    try:
        c = _ro(wa_root() / "ChatStorage.sqlite")
        try:
            n = c.execute(sql, (jid, since)).fetchone()[0]
        finally:
            c.close()
        return int(n or 0)
    except sqlite3.Error:
        return 0


def transcript_key(media_path):
    """Cache key of a voice transcript: sha1 of "<real absolute path>|<size on disk>". The reader and the board
    both use this rule, so a transcript written by one is found by the other. None when the file is missing."""
    try:
        real = os.path.realpath(os.path.expanduser(str(media_path)))
        size = os.stat(real).st_size
    except (OSError, TypeError, ValueError):
        return None
    return hashlib.sha1(("%s|%s" % (real, size)).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- person notes

def _unquote(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        inner = v[1:-1]
        if v[0] == '"':
            return inner.replace('\\"', '"').replace("\\\\", "\\")
        return inner.replace("''", "'")
    return v


def _split_flow(v):
    s = v.strip()[1:-1]
    items, cur, q = [], [], None
    for ch in s:
        if q:
            cur.append(ch)
            if ch == q:
                q = None
            continue
        if ch in "\"'":
            q = ch
            cur.append(ch)
        elif ch == ",":
            items.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    items.append("".join(cur))
    return [_unquote(x) for x in items if x.strip()]


def fm_list(v):
    """A frontmatter value to a list of strings: a Python list, "[a, b]", '["a", "b"]', or a single "a"."""
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v).strip()
    if not s:
        return []
    if s.startswith("[") and s.endswith("]"):
        try:
            got = json.loads(s)
            if isinstance(got, list):
                return [str(x).strip() for x in got if str(x).strip()]
        except ValueError:
            pass
        return [x for x in _split_flow(s) if x]
    return [_unquote(s)]


def parse_frontmatter(text, lists=False):
    """(fm, body). Reads the block between the first two `---` lines without PyYAML.
    Scalars: quoted or bare, quotes removed. Lists: flow (`roles: [a, b]`) or block (`roles:` then `  - a` lines).
    With lists=False (default, same as the original modules) list values stay strings and a block list is rewritten
    in flow form "[a, b]"; use fm_list() to read them. With lists=True list values come back as Python lists."""
    if not text or not text.lstrip("\ufeff").startswith("---"):
        return {}, text or ""
    lines = text.lstrip("\ufeff").split("\n")
    if lines[0].strip() != "---":
        return {}, text
    end = None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r").strip() == "---":
            end = i
            break
    if end is None:
        return {}, text
    head = [x.rstrip("\r") for x in lines[1:end]]
    body = "\n".join(lines[end + 1:])
    fm = {}
    i = 0
    while i < len(head):
        line = head[i]
        if not line.strip() or line.lstrip().startswith("#") or line[:1] in (" ", "\t") or ":" not in line:
            i += 1
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if not k:
            i += 1
            continue
        if v == "":
            items, j = [], i + 1
            while j < len(head) and re.match(r"^\s*-(\s+|$)", head[j]):
                item = re.sub(r"^\s*-\s*", "", head[j]).strip()
                if item:
                    items.append(_unquote(item))
                j += 1
            if items:
                fm[k] = items if lists else "[%s]" % ", ".join(items)
                i = j
                continue
            fm[k] = [] if (lists and j > i + 1) else ""
            i += 1
            continue
        if v.startswith("[") and v.endswith("]"):
            fm[k] = fm_list(v) if lists else v
        else:
            fm[k] = _unquote(v)
        i += 1
    return fm, body


def people_index(cfg):
    """Index of the person notes in paths.people_dir, frontmatter only:
    {"by_jid": {wa_jid: Path}, "by_phone": {digits: Path}, "by_lid": {lid digits: Path}, "error": str or None,
     "dir", "files", "unreadable", "conflicts"}. `error` is set when the folder is not configured, missing or
    unreadable (an empty index returned silently would make every payer look noteless).
    Files starting with "_" are skipped; wa_jid may hold one jid or a list of them; wa_phone any spelling."""
    idx = {"by_jid": {}, "by_phone": {}, "by_lid": {}, "error": None, "dir": None, "files": 0, "unreadable": 0,
           "conflicts": []}
    pd = str(((cfg or {}).get("paths") or {}).get("people_dir") or "").strip()
    if not pd:
        idx["error"] = "paths.people_dir is not set"
        return idx
    d = Path(os.path.expanduser(pd))
    idx["dir"] = str(d)
    try:
        if not d.is_dir():
            idx["error"] = "people folder not found: %s" % d
            return idx
        files = sorted(d.glob("*.md"))
    except OSError as e:
        idx["error"] = "people folder unreadable: %s (%s)" % (d, e)
        return idx
    for p in files:
        if p.name.startswith("_"):
            continue
        try:
            with p.open("r", encoding="utf-8", errors="replace") as f:
                head = f.read(4000)
        except OSError:
            idx["unreadable"] += 1
            continue
        idx["files"] += 1
        fm, _ = parse_frontmatter(head, lists=True)
        for j in fm_list(fm.get("wa_jid")):
            if j in idx["by_jid"] and idx["by_jid"][j] != p:
                idx["conflicts"].append([j, idx["by_jid"][j].name, p.name])
                continue
            idx["by_jid"][j] = p
            h = j.split("@")[0]
            if j.endswith("@s.whatsapp.net") and h.isdigit():
                idx["by_phone"].setdefault(h, p)
            elif j.endswith("@lid"):
                idx["by_lid"].setdefault(h, p)
        for ph in fm_list(fm.get("wa_phone")):
            n = norm_phone(ph, cfg)
            if n:
                idx["by_phone"].setdefault(n, p)
    return idx


def people_note_for(phone_or_jid, idx=None, cfg=None):
    """Person note Path for a phone or a one to one jid, trying both jid spellings and the phone; None when there is
    none, for groups, or when the index could not be built. idx comes from people_index(cfg)."""
    if not phone_or_jid:
        return None
    if idx is None:
        if cfg is None:
            return None
        idx = people_index(cfg)
    if idx.get("error"):
        return None
    s = str(phone_or_jid).strip()
    if s.endswith("@g.us") or s.endswith("@broadcast"):
        return None
    if "@" in s:
        forms = [s] + sorted(f for f in jid_forms(s) if f != s)
        for f in forms:
            if f in idx["by_jid"]:
                return idx["by_jid"][f]
        for f in forms:
            h = f.split("@")[0]
            if f.endswith("@lid") and h in idx["by_lid"]:
                return idx["by_lid"][h]
            if f.endswith("@s.whatsapp.net") and h.isdigit() and h in idx["by_phone"]:
                return idx["by_phone"][h]
        return None
    d = norm_phone(s, cfg)
    if not d:
        return None
    if d in idx["by_phone"]:
        return idx["by_phone"][d]
    if d + "@s.whatsapp.net" in idx["by_jid"]:
        return idx["by_jid"][d + "@s.whatsapp.net"]
    if len(d) < MIN_PHONE_DIGITS:
        return None
    lid = lid_of_phone(d)
    if lid:
        if lid in idx["by_lid"]:
            return idx["by_lid"][lid]
        if lid + "@lid" in idx["by_jid"]:
            return idx["by_jid"][lid + "@lid"]
    return None


# ---------------------------------------------------------------- files

def repair_inner_quotes(text):
    """Escape English double quotes that a model wrote unescaped inside a JSON string, so json.loads can read it.
    A quote closes the string only when the next non space character is } ] : or a comma followed by " { [ ] } or the
    end; any other quote is content and becomes \\". Callers use it only after parsing the original text failed."""
    s = text or ""
    out = []
    in_str = False
    esc = False
    n = len(s)
    for i, c in enumerate(s):
        if not in_str:
            out.append(c)
            if c == '"':
                in_str = True
            continue
        if esc:
            out.append(c)
            esc = False
            continue
        if c == "\\":
            out.append(c)
            esc = True
            continue
        if c != '"':
            out.append(c)
            continue
        k = i + 1
        while k < n and s[k] in " \t\r\n":
            k += 1
        nxt = s[k] if k < n else ""
        closes = nxt in ("", "}", "]", ":")
        if nxt == ",":
            k2 = k + 1
            while k2 < n and s[k2] in " \t\r\n":
                k2 += 1
            closes = k2 >= n or s[k2] in '"{[]}'
        if closes:
            out.append(c)
            in_str = False
        else:
            out.append('\\"')
    return "".join(out)


def read_json(path, default=None):
    try:
        return json.loads(Path(os.path.expanduser(str(path))).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def write_json_atomic(path, obj, mode=0o600):
    """Write to a temp file in the same folder, fsync, chmod, rename: readers never see half a file."""
    path = Path(os.path.expanduser(str(path)))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


# ---------------------------------------------------------------- push outlet (DESIGN.md §5.4)

NOTIFY_OUTLETS = ("none", "telegram", "command")
NOTIFY_KINDS = ("error", "needs_me", "digest", "info")
NOTIFY_MODES = {"off": (), "errors": ("error", "digest"), "needs_me": ("error", "digest", "needs_me"),
                "all": NOTIFY_KINDS}
NOTIFY_MAX_CHARS = 3800
NOTIFY_COMMAND_TIMEOUT = 30
_NOTIFY_LOCK = threading.Lock()
_NOTIFY_LAST = {}
_NOTIFY_INFLIGHT = set()
_HTTP_POST = None       # selftest hook: callable(url, fields, timeout) -> dict


def _notify_mode(cfg):
    m = str(((cfg or {}).get("notify") or {}).get("mode") or "errors").strip().lower()
    return m if m in NOTIFY_MODES else "errors"


def notify_wanted(cfg, kind):
    """Does notify.mode let this kind through?  off: nothing · errors: error and digest (the scheduled summaries,
    the morning digest and the evening intake push) · needs_me: plus judgments that need the owner · all: everything."""
    k = str(kind or "info").strip().lower()
    if k == "errors":
        k = "error"
    if k not in NOTIFY_KINDS:
        k = "info"
    return k in NOTIFY_MODES[_notify_mode(cfg)]


def html_to_plain(text):
    s = re.sub(r"</?(b|strong|i|em|u|ins|s|strike|del|code|pre|a|tg-spoiler|span|blockquote)(\s[^>]*)?>", "",
               str(text or ""), flags=re.I)
    return html.unescape(s)


def _first_line(text):
    return (str(text or "").strip().split("\n") or [""])[0][:80]


def _http_post_form(url, fields, timeout=20):
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        try:
            return json.loads(body)
        except ValueError:
            return {"ok": False, "description": "HTTP %s %s" % (e.code, body[:200])}
    except (urllib.error.URLError, ssl.SSLError) as e:
        if "CERTIFICATE" in str(e).upper() or "SSL" in str(e).upper():
            return _curl_post_form(url, fields, timeout)
        raise


def _curl_quote(s):
    return '"' + (str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
                  .replace("\r", "\\r").replace("\t", "\\t")) + '"'


def _curl_post_form(url, fields, timeout=20):
    """Fallback when Python's certificate chain is broken. The URL (which carries a bot token) and the fields go to
    curl through a config on stdin, so they never appear in the process list."""
    conf = ["url = " + _curl_quote(url), 'request = "POST"']
    conf += ["data-urlencode = " + _curl_quote("%s=%s" % (k, v)) for k, v in fields.items()]
    r = subprocess.run(["/usr/bin/curl", "-s", "-m", str(int(timeout)), "-K", "-"],
                       input=("\n".join(conf) + "\n").encode("utf-8"), capture_output=True, timeout=timeout + 10)
    out = r.stdout.decode("utf-8", "replace")
    try:
        return json.loads(out or "{}")
    except ValueError:
        return {"ok": False, "description": (r.stderr.decode("utf-8", "replace") or out or "curl returned no JSON")[-300:]}


def _telegram(cfg, text, parse_mode):
    tf = ((cfg or {}).get("notify") or {}).get("telegram_file") or str(live_dir(cfg) / "telegram.json")
    t = read_json(tf, None)
    if not isinstance(t, dict):
        return False, "telegram file missing or unreadable: %s" % tf
    tok, chat = str(t.get("bot_token") or "").strip(), str(t.get("chat_id") or "").strip()
    if not tok or not chat or "PASTE" in tok.upper():
        return False, "telegram file has no bot_token or chat_id yet: %s" % tf
    url = "https://api.telegram.org/bot%s/sendMessage" % tok
    post = _HTTP_POST or _http_post_form

    def scrub(s):
        return str(s or "").replace(tok, "<token>")[:300]

    def attempt(body, pm):
        fields = {"chat_id": chat, "text": body, "disable_web_page_preview": "true"}
        if pm:
            fields["parse_mode"] = pm
        try:
            j = post(url, fields, 20)
            return j if isinstance(j, dict) else {"ok": False, "description": "no JSON from Telegram"}
        except Exception as e:
            return {"ok": False, "description": scrub(e)}
    j = attempt(text, parse_mode)
    if not j.get("ok") and parse_mode:
        first = scrub(j.get("description"))
        j = attempt(html_to_plain(text), None)
        if j.get("ok"):
            return True, "sent as plain text after the formatted send failed (%s)" % first
    return bool(j.get("ok")), scrub(j.get("description"))


def _command(cfg, text, parse_mode):
    cmd = str(((cfg or {}).get("notify") or {}).get("command") or "").strip()
    if not cmd:
        return False, "notify.command is empty"
    body = html_to_plain(text) if parse_mode else str(text)
    if not body.endswith("\n"):   # a command that appends to a file would glue the next push onto this one's last line
        body += "\n"
    try:
        r = subprocess.run(["/bin/sh", "-c", cmd], input=body.encode("utf-8"), capture_output=True,
                           timeout=NOTIFY_COMMAND_TIMEOUT, env=subprocess_env(cfg or {}))
    except subprocess.TimeoutExpired:
        return False, "notify.command timed out after %ds" % NOTIFY_COMMAND_TIMEOUT
    except OSError as e:
        return False, "notify.command could not start: %s" % e
    if r.returncode == 0:
        return True, ""
    tail = (r.stderr or r.stdout or b"").decode("utf-8", "replace").strip()[-300:]
    return False, "notify.command exit %d: %s" % (r.returncode, tail)


def notify_deliver(cfg, text, parse_mode=None):
    """Raw outlet dispatch, no mode check and no dedupe: returns (ok, why). Never raises.
    none: logs the first line, not delivered · telegram: notify.telegram_file {bot_token, chat_id}; a formatted
    (HTML) send that fails is retried once as plain text · command: `/bin/sh -c notify.command` with the text on
    stdin (plain text, HTML tags removed), exit 0 means delivered, 30 second timeout."""
    outlet = str(((cfg or {}).get("notify") or {}).get("outlet") or "none").strip().lower()
    text = str(text or "")
    if len(text) > NOTIFY_MAX_CHARS:
        text = text[:NOTIFY_MAX_CHARS - 1] + "…"
    try:
        if outlet == "none":
            _log.info("notify (outlet none, not delivered): %s", _first_line(text))
            return False, "notify.outlet is none"
        if outlet == "telegram":
            return _telegram(cfg, text, parse_mode)
        if outlet == "command":
            return _command(cfg, text, parse_mode)
        return False, "unknown notify.outlet %r (use none, telegram or command)" % outlet
    except Exception as e:
        return False, "notify error: %s" % str(e)[:300]


def _notify(cfg, text, parse_mode=None, key=None, min_gap=0, kind=None):
    if _notify_mode(cfg) == "off":
        _log.info("notify off (notify.mode=off): %s", _first_line(text))
        return False, "notify.mode is off"
    if kind is not None and not notify_wanted(cfg, kind):
        _log.info("notify skipped (%s not wanted in mode %s): %s", kind, _notify_mode(cfg), _first_line(text))
        return False, "kind %s is not wanted in notify.mode %s" % (kind, _notify_mode(cfg))
    gated = bool(key) and bool(min_gap)
    if gated:
        with _NOTIFY_LOCK:
            if key in _NOTIFY_INFLIGHT or time.time() - _NOTIFY_LAST.get(key, 0) < float(min_gap):
                _log.info("notify suppressed (%s, gap not reached)", key)
                return False, "suppressed: %s sent less than %ss ago" % (key, min_gap)
            _NOTIFY_INFLIGHT.add(key)
    ok, why = False, ""
    try:
        ok, why = notify_deliver(cfg, text, parse_mode)
    finally:
        if gated:
            with _NOTIFY_LOCK:
                _NOTIFY_INFLIGHT.discard(key)
                if ok:
                    _NOTIFY_LAST[key] = time.time()
    if ok:
        _log.info("notify delivered: %s%s", _first_line(text), (" (%s)" % why) if why else "")
    elif why != "notify.outlet is none":
        _log.warning("notify failed: %s", why)
    return ok, why


def notify_send(cfg, text, parse_mode=None, key=None, min_gap=0, kind=None):
    """The one push outlet for the daemon, the CLIs and the evening job. Returns True when delivered.
    notify.mode=off blocks everything; with kind given, notify_wanted(cfg, kind) must pass too.
    key plus min_gap: the same key is pushed at most once per min_gap seconds, and only a successful send starts the
    gap (a failed push never silences the next one)."""
    return _notify(cfg, text, parse_mode, key, min_gap, kind)[0]


# ---------------------------------------------------------------- selftest helpers (also used by other modules)

def fake_wa_root(root, lid_pairs=(), contacts=(), chats=(), messages=()):
    """Build fake WhatsApp Desktop databases under `root` for selftests.
    lid_pairs: [(lid, phone)] · contacts: [(lid, phone)] · chats: [jid] · messages: [(jid, days_ago, mtype)]."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(root / "LID.sqlite"))
    c.execute("CREATE TABLE ZWAZACCOUNT (Z_PK INTEGER PRIMARY KEY, ZIDENTIFIER VARCHAR, ZPHONENUMBER VARCHAR)")
    c.execute("CREATE TABLE ZWAPHONENUMBERLIDPAIR (Z_PK INTEGER PRIMARY KEY, ZLID VARCHAR, ZPHONENUMBER VARCHAR, ZTIMESTAMP REAL)")
    for i, (lid, ph) in enumerate(lid_pairs):
        c.execute("INSERT INTO ZWAPHONENUMBERLIDPAIR (ZLID, ZPHONENUMBER, ZTIMESTAMP) VALUES (?, ?, ?)",
                  (lid + "@lid", ph + "@s.whatsapp.net", float(i)))
    c.commit()
    c.close()
    c = sqlite3.connect(str(root / "ContactsV2.sqlite"))
    c.execute("CREATE TABLE ZWAADDRESSBOOKCONTACT (Z_PK INTEGER PRIMARY KEY, ZLID VARCHAR, ZPHONENUMBER VARCHAR, ZWHATSAPPID VARCHAR)")
    for lid, ph in contacts:
        c.execute("INSERT INTO ZWAADDRESSBOOKCONTACT (ZLID, ZPHONENUMBER, ZWHATSAPPID) VALUES (?, ?, ?)",
                  (lid + "@lid", ph, None))
    c.commit()
    c.close()
    c = sqlite3.connect(str(root / "ChatStorage.sqlite"))
    c.execute("CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZCONTACTJID VARCHAR, ZPARTNERNAME VARCHAR)")
    c.execute("CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZMESSAGEDATE REAL, "
              "ZMESSAGETYPE INTEGER, ZISFROMME INTEGER, ZTEXT VARCHAR)")
    pk = {}
    for j in chats:
        cur = c.execute("INSERT INTO ZWACHATSESSION (ZCONTACTJID) VALUES (?)", (j,))
        pk[j] = cur.lastrowid
    now = (dt.datetime.now(dt.timezone.utc) - CORE_EPOCH_UTC).total_seconds()
    for j, days_ago, mtype in messages:
        c.execute("INSERT INTO ZWAMESSAGE (ZCHATSESSION, ZMESSAGEDATE, ZMESSAGETYPE, ZISFROMME, ZTEXT) VALUES (?, ?, ?, 0, 'x')",
                  (pk[j], now - days_ago * 86400.0, mtype))
    c.commit()
    c.close()
    (root / "ChatStorage.sqlite-wal").write_bytes(b"")
    invalidate_wa_cache()
    return root


class _Checks(object):
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


def _selftest():
    check = _Checks("wa_common")
    tmp = Path(tempfile.mkdtemp(prefix="wa_common_selftest."))
    saved_env = {k: os.environ.get(k) for k in (WA_ROOT_ENV, CONFIG_ENV)}
    try:
        os.environ[WA_ROOT_ENV] = str(tmp / "no-whatsapp-here")
        os.environ.pop(CONFIG_ENV, None)
        invalidate_wa_cache()

        # defaults and placeholders
        live = tmp / "home" / "live"
        live.mkdir(parents=True)
        (live / "config.json").write_text(json.dumps({"owner_label": "Alex Rivera", "board": {"lang": "en"},
                                                      "notify": {"outlet": "command"}}), encoding="utf-8")
        cfg = load_config(live / "config.json")
        check(cfg["debounce"]["quiet_seconds"] == 45 and cfg["brain"]["model"] == "claude-sonnet-5",
              "defaults: debounce and brain filled")
        check(cfg["paths"]["home"] == str(tmp / "home") and cfg["paths"]["live_dir"] == str(live),
              "defaults: home is the parent of live/, live_dir is the config folder")
        check(cfg["state_db"] == str(live / "state.sqlite") and cfg["live_dir"] == str(live), "derived: state_db and live_dir")
        check(cfg["notify"]["telegram_file"] == str(live / "telegram.json") and cfg["notify"]["mode"] == "errors",
              "placeholders: <live> resolved inside notify, mode default errors")
        check(cfg["brain"]["cwd"] == str(tmp / "home" / "brain-cwd"), "placeholders: <home> resolved in brain.cwd")
        check(cfg["paths"]["reader_dir"] == str(payload_dir()), "placeholders: <payload> resolved")
        check(cfg["brain"]["whisper_model"].startswith(str(Path.home())), "tilde expanded in brain.whisper_model")
        check(cfg["owner_marker"] == "AR", "owner_marker defaults to initials (AR)")
        check(cfg["facts"]["floor"] == DEFAULT_FLOOR and cfg["facts"]["csv"]["columns"] == {}, "nested facts defaults")
        check(cfg["doorbell"]["wal_path"].endswith("no-whatsapp-here/ChatStorage.sqlite-wal"), "doorbell.wal_path <auto> follows wa_root()")
        check(cfg["bin"]["python"] == "/usr/bin/python3" and cfg["bin"]["path_env"], "bin.python default and path_env computed")
        again = apply_defaults(copy.deepcopy(cfg), cfg["config_path"])
        again.pop("_load_warnings", None)
        first = dict(cfg)
        first.pop("_load_warnings", None)
        check(again == first, "apply_defaults is idempotent")
        legacy = apply_defaults({"notify": "cat > /dev/null", "notify_mode": "all"}, None)
        check(legacy["notify"]["outlet"] == "command" and legacy["notify"]["mode"] == "all"
              and legacy["paths"]["live_dir"] == str(Path(os.path.expanduser(DEFAULT_HOME)) / "live"),
              "legacy notify string read as command outlet; no config path means <home>/live")
        both = apply_defaults({"paths": {"intake_buffer": "~/x/buffer.md"}}, None)
        check(both["intake"]["buffer"] == os.path.expanduser("~/x/buffer.md"), "paths.intake_buffer mirrors into intake.buffer")
        try:
            load_config(tmp / "missing.json")
            check(False, "missing config raises")
        except FileNotFoundError:
            check(True, "missing config raises FileNotFoundError")
        (tmp / "bad.json").write_text("{nope", encoding="utf-8")
        try:
            load_config(tmp / "bad.json")
            check(False, "bad JSON raises")
        except ValueError:
            check(True, "bad JSON raises ValueError")
        check(owner_lang(cfg) == "en" and owner_lang({}) == "zh", "owner_lang")
        check(source_label({"facts": {"source": "csv"}}, "facts") == "CSV"
              and source_label({"facts": {"source": "lark", "label": "Sales base"}}, "facts") == "Sales base", "source_label")

        # vocabulary
        v = vocab(cfg)
        check(v["stage_order"] == ["new", "talking", "offered", "won", "active", "done"] and v["parked"] == "parked",
              "vocab: default order and side stage")
        check(v["business_worlds"] == ["work"] and world_is_business(cfg, "work") and not world_is_business(cfg, "personal"),
              "vocab: business worlds")
        custom = {"vocab": {"worlds": [{"key": "shop", "label": "店", "label_en": "Shop", "business": True}, "family", "family"],
                            "stages": ["lead", {"key": "paid"}, {"key": "hold", "side": True}], "park_from": ["lead", "nope"]},
                  "board": {"lang": "zh"}}
        cv = vocab(custom)
        check(cv["world_keys"] == ["shop", "family"] and cv["stage_order"] == ["lead", "paid"] and cv["parked"] == "hold"
              and cv["park_from"] == ["lead"], "vocab: strings, duplicates, side flag, park_from filtered")
        check(vocab_label(custom, "world", "shop") == "店" and vocab_label(custom, "world", "shop", "en") == "Shop"
              and vocab_label(custom, "stage", "unknown") == "unknown", "vocab_label by language")
        check(vocab({"vocab": {"worlds": []}})["world_keys"] == ["personal", "work", "other"], "vocab: empty list falls back to defaults")

        # phones
        pc = {"facts": {"phone_default_cc": "60", "phone_valid": ["60:11-12", {"cc": "65", "len": 10, "label": "SG"}]}}
        check(norm_phone("+60 12-000 0001") == "60120000001" and norm_phone("012-000 0001", pc) == "60120000001",
              "norm_phone: plus sign and default country code")
        check(norm_phone("012-000 0001") == "0120000001", "norm_phone: no configured code keeps the leading 0")
        check(norm_phone("0065 9000 0001") == "6590000001" and norm_phone("") is None and norm_phone(None) is None,
              "norm_phone: 00 prefix, empty")
        check(norm_phone("60120000001@s.whatsapp.net") == "60120000001", "norm_phone: jid digits")
        check(phone_kind("60120000001", pc) == "60" and phone_kind("6590000001", pc) == "SG"
              and phone_kind("601200000011111", pc) is None, "phone_kind: rules by code and length")
        check(phone_kind("12345678") == "any" and phone_kind("1234567") is None and phone_kind("1" * 16) is None,
              "phone_kind: without rules any 8 to 15 digits")

        # transcript key
        audio = tmp / "voice.opus"
        audio.write_bytes(b"12345")
        link = tmp / "voice-link.opus"
        os.symlink(str(audio), str(link))
        k1 = transcript_key(audio)
        check(k1 == hashlib.sha1(("%s|5" % os.path.realpath(str(audio))).encode("utf-8")).hexdigest(),
              "transcript_key: sha1 of real path and size")
        check(transcript_key(link) == k1 and transcript_key(tmp / "gone.opus") is None, "transcript_key: symlink same key, missing None")
        audio.write_bytes(b"123456")
        check(transcript_key(audio) != k1, "transcript_key: size change changes the key")

        # frontmatter
        note = ('---\ntype: person\nroles: [member, "vip client"]\nwa_jid: "100000000000001@lid"\ntags:\n  - one\n'
                "  - 'two'\nwa_phone: +60 12-000 0002\nempty:\n---\n# Body\n")
        fm, body = parse_frontmatter(note)
        check(fm["type"] == "person" and fm["wa_jid"] == "100000000000001@lid" and fm["wa_phone"] == "+60 12-000 0002",
              "parse_frontmatter: bare and quoted scalars")
        check(fm["roles"] == '[member, "vip client"]' and fm["tags"] == "[one, two]" and fm["empty"] == "",
              "parse_frontmatter: flow list kept, block list rewritten to flow form")
        check(fm_list(fm["roles"]) == ["member", "vip client"] and fm_list(fm["tags"]) == ["one", "two"], "fm_list reads both")
        fl, _ = parse_frontmatter(note, lists=True)
        check(fl["roles"] == ["member", "vip client"] and fl["tags"] == ["one", "two"], "parse_frontmatter lists=True")
        check(body.startswith("# Body") and parse_frontmatter("no frontmatter")[0] == {}
              and parse_frontmatter("---\nopen: never closed\n")[0] == {}, "parse_frontmatter: body and edge cases")

        # people index and matching on fake WhatsApp databases
        idx = people_index({"paths": {"people_dir": str(tmp / "no-such-people")}})
        check(bool(idx["error"]) and "not found" in idx["error"] and not idx["by_jid"], "people_index: missing folder sets error")
        check(people_index({})["error"] == "paths.people_dir is not set", "people_index: unset folder sets error")
        check(people_note_for("60120000001", idx) is None, "people_note_for: errored index returns None")
        people = tmp / "People"
        people.mkdir()
        (people / "Alex-Rivera.md").write_text(note, encoding="utf-8")
        (people / "Blake-Chen.md").write_text("---\ntype: person\nwa_jid: 60120000003@s.whatsapp.net\n---\n", encoding="utf-8")
        (people / "Casey-Ng.md").write_text("---\ntype: person\nwa_phone: 012-000 0004\n---\n", encoding="utf-8")
        (people / "_People-Guide.md").write_text("---\nwa_jid: 60120000009@s.whatsapp.net\n---\n", encoding="utf-8")
        wroot = fake_wa_root(tmp / "wa", lid_pairs=[("100000000000001", "60120000002"), ("100000000000003", "60120000003")],
                             contacts=[("100000000000005", "+60 12-000 0005")],
                             chats=["100000000000001@lid", "60120000003@s.whatsapp.net", "60120000004@s.whatsapp.net"],
                             messages=[("100000000000001@lid", 1, 0), ("100000000000001@lid", 2, 3), ("100000000000001@lid", 3, 6),
                                       ("100000000000001@lid", 45, 0)])
        os.environ[WA_ROOT_ENV] = str(wroot)
        invalidate_wa_cache()
        pcfg = {"paths": {"people_dir": str(people)}, "facts": {"phone_default_cc": "60"}}
        idx = people_index(pcfg)
        check(idx["error"] is None and idx["files"] == 3 and "60120000009@s.whatsapp.net" not in idx["by_jid"],
              "people_index: three notes, underscore file skipped")
        check(idx["by_lid"].get("100000000000001").name == "Alex-Rivera.md" and idx["by_phone"].get("60120000004").name == "Casey-Ng.md",
              "people_index: LID key and normalized wa_phone")
        check(phone_to_jid("+60 12-000 0002") == {"jid": "100000000000001@lid", "lid": "100000000000001", "has_chat": True, "how": "lid-chat"},
              "phone_to_jid: chat on the LID jid")
        r3 = phone_to_jid("60120000003")
        check(r3["how"] == "phone-chat" and r3["lid"] == "100000000000003", "phone_to_jid: chat on the phone jid, LID still filled")
        check(phone_to_jid("60120000005")["how"] == "known-no-chat" and phone_to_jid("60129999999")["how"] == "unknown",
              "phone_to_jid: known without chat, unknown")
        check(lid_of_phone("60120000002") == "100000000000001" and phone_of_jid("100000000000001@lid") == "60120000002",
              "lid_of_phone and phone_of_jid")
        check(jid_forms("60120000003@s.whatsapp.net") == {"60120000003@s.whatsapp.net", "100000000000003@lid"}
              and jid_forms("120000000000000001@g.us") == {"120000000000000001@g.us"}, "jid_forms both ways, groups alone")
        check(people_note_for("60120000002@s.whatsapp.net", idx, pcfg).name == "Alex-Rivera.md", "people_note_for: phone jid finds a LID note")
        check(people_note_for("100000000000003@lid", idx, pcfg).name == "Blake-Chen.md", "people_note_for: LID jid finds a phone jid note")
        check(people_note_for("012-000 0004", idx, pcfg).name == "Casey-Ng.md", "people_note_for: formatted phone finds wa_phone")
        check(people_note_for("120000000000000001@g.us", idx, pcfg) is None, "people_note_for: groups never match")
        check(chat_message_count("100000000000001@lid", 30) == 2 and chat_message_count("100000000000001@lid", 30, content_only=False) == 3,
              "chat_message_count: content only skips system type 6 and the 45 day old message")
        check(wa_lookup_errors() == [], "fake databases open read only without errors")
        race_errors = []

        def reader():
            try:
                for _ in range(300):
                    phone_to_jid("60120000002")
                    lid_of_phone("60120000003")
                    phone_of_jid("100000000000001@lid")
            except Exception as e:
                race_errors.append(e)

        def invalidator():
            for _ in range(300):
                invalidate_wa_cache()
        workers = [threading.Thread(target=reader), threading.Thread(target=invalidator), threading.Thread(target=reader)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
        check(not race_errors and lid_of_phone("60120000002") == "100000000000001",
              "lookups stay safe while another thread invalidates the cache")
        later = fake_wa_root(tmp / "wa2", chats=["60120000007@s.whatsapp.net"])
        os.environ[WA_ROOT_ENV] = str(later)
        check(phone_to_jid("60120000007")["has_chat"], "cache follows a changed WhatsApp root")

        # files
        jp = write_json_atomic(tmp / "sub" / "x.json", {"a": "引号"})
        check((os.stat(str(jp)).st_mode & 0o777) == 0o600 and read_json(jp) == {"a": "引号"}
              and not [p for p in (tmp / "sub").iterdir() if p.name != "x.json"], "write_json_atomic: 0600, readable, no temp left")
        check(read_json(tmp / "nope.json", {"d": 1}) == {"d": 1}, "read_json default")
        broken = '{"summary": "she said "see you at 3" and left", "n": 1}'
        check(json.loads(repair_inner_quotes(broken))["summary"] == 'she said "see you at 3" and left',
              "repair_inner_quotes fixes unescaped inner quotes")

        # binaries
        bindir = tmp / "bin"
        bindir.mkdir()
        fake = bindir / "fake-tool"
        fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        os.chmod(str(fake), 0o755)
        bcfg = {"bin": {"tool": str(fake), "gone": str(tmp / "missing-tool"), "path_env": "/opt/x:/usr/bin"}}
        check(resolve_bin(bcfg, "tool") == str(fake) and resolve_bin({}, "python") == "/usr/bin/python3",
              "resolve_bin: configured path, python default")
        check(resolve_bin(bcfg, "gone") is None, "resolve_bin: missing configured path with no fallback gives None")
        env = subprocess_env(dict(bcfg, config_path="/tmp/c.json", brain={"whisper_model": "~/m"}))
        check(env["PATH"] == "/opt/x:/usr/bin" and env[CONFIG_ENV] == "/tmp/c.json"
              and env["WA_WHISPER_MODEL"] == os.path.expanduser("~/m"), "subprocess_env: PATH, config and whisper model")
        check(all(env.get(k) == v for k, v in CLAUDE_QUIET_ENV.items()),
              "subprocess_env: Claude memory and CLAUDE.md files switched off for every child")

        # push outlet
        check([notify_wanted({"notify": {"mode": m}}, k) for m in ("off", "errors", "needs_me", "all")
               for k in ("error", "needs_me", "digest", "info")] ==
              [False, False, False, False, True, False, True, False, True, True, True, False, True, True, True, True],
              "notify_wanted: mode table")
        out_file = tmp / "pushed.txt"
        ncfg = {"notify": {"outlet": "command", "mode": "errors", "command": "cat >> %s" % shlex.quote(str(out_file))}}
        check(notify_send(ncfg, "<b>Hello</b> &amp; world", parse_mode="HTML") and out_file.read_text(encoding="utf-8") == "Hello & world\n",
              "command outlet: text on stdin, HTML reduced to plain text, ending with a newline")
        check(notify_send(ncfg, "second push", kind="error") and out_file.read_text(encoding="utf-8") == "Hello & world\nsecond push\n",
              "command outlet: a second push into the same file starts on its own line")
        check(notify_send(ncfg, "first", key="k1", min_gap=3600) and not notify_send(ncfg, "second", key="k1", min_gap=3600),
              "dedupe: a successful send starts the gap")
        failing = {"notify": {"outlet": "command", "mode": "errors", "command": "exit 3"}}
        check(not notify_send(failing, "x", key="k2", min_gap=3600) and notify_send(ncfg, "after failure", key="k2", min_gap=3600),
              "dedupe: a failed send does not start the gap")
        check(not notify_send(dict(ncfg, notify=dict(ncfg["notify"], mode="off")), "silent") and "silent" not in out_file.read_text(encoding="utf-8"),
              "mode off delivers nothing")
        check(not notify_send(ncfg, "chatter", kind="info") and notify_send(ncfg, "digest ok", kind="digest"),
              "kind filter: info blocked in errors mode, digest passes")
        check(notify_deliver({"notify": {"outlet": "none"}}, "x") == (False, "notify.outlet is none"), "outlet none: logged only")
        check(not notify_deliver({"notify": {"outlet": "pigeon"}}, "x")[0], "unknown outlet refused")
        tg_file = tmp / "telegram.json"
        tcfg = {"notify": {"outlet": "telegram", "mode": "all", "telegram_file": str(tg_file)}}
        check(not notify_send(tcfg, "x") and "missing" in notify_deliver(tcfg, "x")[1], "telegram: missing file refused")
        tg_file.write_text(json.dumps({"bot_token": "123456:FAKE-token-for-selftest", "chat_id": "42"}), encoding="utf-8")
        calls = []

        def fake_post(url, fields, timeout):
            calls.append((url, dict(fields)))
            if fields.get("parse_mode"):
                return {"ok": False, "description": "Bad Request: can't parse entities"}
            return {"ok": True}
        global _HTTP_POST
        _HTTP_POST = fake_post
        try:
            ok, why = notify_deliver(tcfg, "<b>Digest</b> 1 &lt; 2", parse_mode="HTML")
        finally:
            _HTTP_POST = None
        check(ok and len(calls) == 2 and calls[0][1]["parse_mode"] == "HTML" and "parse_mode" not in calls[1][1]
              and calls[1][1]["text"] == "Digest 1 < 2" and "plain text" in why, "telegram: HTML refused, plain text retry delivered")
        check(calls[0][0].endswith("/sendMessage") and "FAKE-token" not in why, "telegram: token never in the reason text")

        # CLI --notify, as the evening job calls it
        cli_cfg = tmp / "cli" / "live" / "config.json"
        cli_cfg.parent.mkdir(parents=True)
        cli_out = tmp / "cli-pushed.txt"
        cli_cfg.write_text(json.dumps({"notify": {"outlet": "command", "command": "cat > %s" % shlex.quote(str(cli_out))}}), encoding="utf-8")
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--config", str(cli_cfg), "--notify", "--kind", "digest"],
                           input="evening 🗂 push".encode("utf-8"), capture_output=True, timeout=60,
                           env=dict(os.environ, **{WA_ROOT_ENV: str(tmp / "no-whatsapp-here")}))
        check(r.returncode == 0 and cli_out.read_text(encoding="utf-8") == "evening 🗂 push\n", "CLI --notify delivers stdin through the outlet")
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--config", str(cli_cfg), "--notify", "--kind", "info"],
                           input=b"x", capture_output=True, timeout=60)
        check(r.returncode == 1 and b"not wanted" in r.stdout, "CLI --notify exits 1 when the mode blocks the kind")
        src = Path(__file__).read_text(encoding="utf-8")
        check(chr(0x2014) not in src and chr(0x2013) not in src, "no em dash or en dash in this file")
    finally:
        for k, val in saved_env.items():
            if val is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = val
        invalidate_wa_cache()
        shutil.rmtree(str(tmp), ignore_errors=True)
    return check.done()


# ---------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description="WhatsApp Live shared base: push outlet CLI, defaults, selftest")
    ap.add_argument("--config", default=None, help="live/config.json (default: env WA_LIVE_CONFIG or ~/WhatsApp-Live/live/config.json)")
    ap.add_argument("--selftest", action="store_true", help="offline checks in temp dirs")
    ap.add_argument("--notify", action="store_true", help="send the text on stdin through the configured outlet")
    ap.add_argument("--html", action="store_true", help="with --notify: the text is Telegram HTML")
    ap.add_argument("--kind", default=None, choices=NOTIFY_KINDS, help="with --notify: apply notify.mode for this kind")
    ap.add_argument("--print-defaults", action="store_true", help="print every default as JSON")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if a.print_defaults:
        print(json.dumps(DEFAULTS, ensure_ascii=False, indent=1))
        return 0
    if a.notify:
        try:
            cfg = load_config(a.config)
        except (FileNotFoundError, ValueError) as e:
            print("notify: not sent (%s)" % e)
            return 2
        data = sys.stdin.buffer.read().decode("utf-8", "replace")
        if not data.strip():
            print("notify: not sent (empty text on stdin)")
            return 2
        ok, why = _notify(cfg, data, parse_mode="HTML" if a.html else None, kind=a.kind)
        print("notify: %s%s" % ("delivered" if ok else "not sent", (" (%s)" % why) if why else ""))
        return 0 if ok else 1
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
