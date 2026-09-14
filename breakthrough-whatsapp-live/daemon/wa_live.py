#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wa_live.py · 私人 WhatsApp 实时逐封分诊 daemon (住一台常驻 Mac, launchd KeepAlive)

三层, 每层比前一层贵:
  1. 门铃 (零 LLM): 每 N 秒 stat WhatsApp Desktop 的 ChatStorage.sqlite-wal; 有动就 read-only 开 live 库,
     查 ZWAMESSAGE 里 Z_PK 大于水位的行。水位持久化在 state.sqlite, 重启不重跑。
  2. 闸 (零 LLM, 纯规则): 限时动态 / 广播 / ignored_jids 丢; 一对一照 direct_policy; 群按 config 的白名单
     (all / mention / skip); per-chat debounce (静默 quiet 秒, 或从第一封起算 max_wait 秒强制起跑)。
     主人自己发出去的 (ZISFROMME=1) 把那条对话标成 replied, 并取消该对话还没跑的 burst。
  3. 脑 (claude -p, 每个 burst 一次): 读取器 wa_sweep.py --only-jid 拉对话包 + wa_context.py 查第二大脑 +
     上一次的 state, 喂 live/prompts/brain-prompt.md, 吐 JSON 契约
     {world, stage, priority, needs_me, draft, reason, confidence, situation, stake, summary, timeline, refs, captures, calendar_hint}。

⛔ 它不发送任何东西。⛔ 绝不静默丢: 每封 inbound 在 inbound_log 留一行 (过 / 不过 / 为什么),
每个 run 在 runs 留一行 (成 / 败 / 花多少), 失败还会走推播出口推一句。

用法:
  python3 wa_live.py                  daemon (launchd 用这个)
  python3 wa_live.py --status         看水位 / pending / 最近 runs
  python3 wa_live.py --scan-once      门铃 + 闸跑一遍, 不起脑 (会写状态库, 验收用抛弃式 config)
  python3 wa_live.py --dry-run        daemon 整条线, 但不叫 claude、不推播、不写 vault, 排班跑试跑模式
  python3 wa_live.py --selftest       假库 + 假设定跑一遍自测, 只写暂存夹
命令档 (丢进 live/commands/ 给跑着的 daemon 下指令; headless claude 只在 launchd 那条路底下拿得到登入与权限):
  test-run.<jid>.json          拿那个对话最近 3 封 inbound 当 burst 立刻跑一次
  probe-claude.json            用脑的笼子参数叫一次最小 claude, 90 秒没回就抓堆叠
  notify-test.json             经推播出口推一句
  prewarm-<天>.json            把 N 天窗的语音先转进快取
  facts-now.json · second-source-now.json · backup-now.json · digest-now.json
                               手动叫排班, 都认 {"dry_run": true}; digest-now 另认 {"for": "YYYY-MM-DD", "send": false}
  backfill-summaries.json      摘要回填, 可带 {"limit": N, "dry_run": true}
"""

import argparse
import contextlib
import datetime as dt
import hashlib
import importlib.util
import inspect
import json
import logging
import os
import platform
import re
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

VERSION = "1.0.0"
HOME = Path.home()
DAEMON_DIR = Path(__file__).resolve().parent
PAYLOAD = DAEMON_DIR.parent                     # 安装包根 (daemon/ 的上一层)
TEMPLATES = DAEMON_DIR / "templates"            # 种子: 主人副本不在时才退回这里, 而且会大声讲

log = logging.getLogger("wa_live")

# 旁支模组各自载入, 有一个 import 失败 daemon 照样起 (那一块功能停), 不拖垮门铃与脑。
# ⚠️ 这时 log 还没设好: 失败原因先记在 _IMPORT_ERRORS, Daemon.__init__ 设好 log 之后再写进 wa_live.log (不只进 launchd 的 stderr)。
sys.path.insert(0, str(DAEMON_DIR))
_MOD = {}
_IMPORT_ERRORS = {}
for _name in ("wa_common", "wa_facts", "wa_digest", "wa_backup", "wa_intake", "wa_backfill", "wa_voice", "wa_calendar", "wa_second_source"):
    try:
        _MOD[_name] = __import__(_name)
    except Exception as _e:
        _MOD[_name] = None
        _IMPORT_ERRORS[_name] = "%s: %s" % (type(_e).__name__, str(_e)[:300])
WC = _MOD["wa_common"]; WF = _MOD["wa_facts"]; WD = _MOD["wa_digest"]; WB = _MOD["wa_backup"]; WI = _MOD["wa_intake"]
WBF = _MOD["wa_backfill"]; WV = _MOD["wa_voice"]; WCAL = _MOD["wa_calendar"]; WSS = _MOD["wa_second_source"]


def now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


CORE_EPOCH_UTC = dt.datetime(2001, 1, 1, tzinfo=dt.timezone.utc)


def core_to_iso(v):
    """Core Data 的秒数是 UTC; 转成本地时间 (跟读取器阅读包同一个时区), 否则脑看到的 burst 跟对话包差几个小时。"""
    try:
        return (CORE_EPOCH_UTC + dt.timedelta(seconds=float(v))).astimezone().replace(tzinfo=None).isoformat(timespec="seconds")
    except Exception:
        return "?"


def core_now_minus(days=0, hours=0):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days, hours=hours) - CORE_EPOCH_UTC).total_seconds()


def safe_name(jid):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", jid or "")[:60]


def _kw_ok(fn, name):
    """fn 收不收这个关键字参数 (旁支模组照 §12 契约加了 cfg=None 这类参数, 旧版没有; 两种都要跑得动)。"""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind == p.VAR_KEYWORD for p in params.values())


def call_opt(fn, *args, **kw):
    """叫 fn, 只把它认得的关键字参数传进去。"""
    return fn(*args, **{k: v for k, v in kw.items() if _kw_ok(fn, k)})


# 内容类讯息才算「一封」, 表只住 wa_common (CONTENT_TYPES · AUDIO_TYPE); 模组没载入时退回同一张表的副本, 只为 --help 与自测起得来。
_FALLBACK_CONTENT_TYPES = {
    0: "文字", 1: "图片", 2: "影片", 3: "语音", 5: "位置", 7: "文字",
    8: "文件", 11: "GIF", 14: "名片", 15: "贴图", 20: "图片", 33: "图片",
    41: "文字", 42: "频道图文",
}


def content_types():
    t = getattr(WC, "CONTENT_TYPES", None) if WC is not None else None
    return t if isinstance(t, dict) and t else _FALLBACK_CONTENT_TYPES


def audio_type():
    v = getattr(WC, "AUDIO_TYPE", None) if WC is not None else None
    return v if isinstance(v, int) else 3


# ⛔ --allowedTools 只加不减 (实证); 真正关门的是这份黑名单 + settings 的 deny + 下面的 cage check。
# settings 模板 (templates/claude-settings.json) 的工具 deny 跟这份一致; daemon 起跑时两份取并集, 少了哪个在 log 喊。
DENY_TOOLS = [
    "Bash", "Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch", "Task", "Agent", "Skill",
    "Workflow", "CronCreate", "CronDelete", "CronList",
    "SendMessage", "ListAgents", "RemoteTrigger", "PushNotification",
    "Monitor", "TaskOutput", "TaskStop", "ScheduleWakeup",
    "EnterWorktree", "ExitWorktree", "DesignSync", "ReportFindings", "ToolSearch", "Artifact",
]
ALLOW_TOOLS = ["Read", "Glob", "Grep"]
EXPECTED_TOOLS = set(ALLOW_TOOLS) | {"TodoWrite"}
APPEND_SYSTEM = ("你在 headless 模式跑, 没有人可以问, 自己判断做完。只吐一个 JSON 物件, 不要 markdown 栅栏, "
                 "不要任何前后文字。可以用 Read 打开阅读包里列出的图片路径与第二大脑候选档路径; 除此之外不要探索档案系统。")
DOSSIER_APPEND_SYSTEM = ("你在 headless 模式跑, 没有人可以问, 自己判断做完。只输出档的全文 (第一行 <!-- file: X.md -->, "
                         "第二行起从 --- 档头开始), 不要栅栏, 不要前后说明。可以用 Read 开输入里列出的 vault 档与图片路径; 除此之外不要探索档案系统。")


# ---------------------------------------------------------------- 路径与设定值 (一律从 config 来, 码里不写任何人的路径)

def _cfg_path(v):
    """config 里的路径值 → Path; 空、没解开的 <auto> / <home> 这类占位回 None。"""
    s = str(v or "").strip()
    if not s or (s.startswith("<") and s.endswith(">")):
        return None
    return Path(os.path.expanduser(s))


def _paths(cfg):
    p = (cfg or {}).get("paths")
    return p if isinstance(p, dict) else {}


def home_dir(cfg):
    if WC is not None and hasattr(WC, "home_dir"):
        try:
            return Path(WC.home_dir(cfg)).expanduser()
        except Exception:
            pass
    return _cfg_path(_paths(cfg).get("home")) or (HOME / "WhatsApp-Live")


def live_dir(cfg):
    if WC is not None and hasattr(WC, "live_dir"):
        try:
            return Path(WC.live_dir(cfg)).expanduser()
        except Exception:
            pass
    return _cfg_path(_paths(cfg).get("live_dir")) or _cfg_path((cfg or {}).get("live_dir")) or (home_dir(cfg) / "live")


def state_db_path(cfg):
    return _cfg_path((cfg or {}).get("state_db")) or (live_dir(cfg) / "state.sqlite")


def voice_pairs_path(cfg):
    return _cfg_path(((cfg or {}).get("voice") or {}).get("pairs")) or (live_dir(cfg) / "voice-pairs.jsonl")


def vault_dir(cfg):
    return _cfg_path(_paths(cfg).get("vault"))


def people_dir(cfg):
    return _cfg_path(_paths(cfg).get("people_dir"))


def intake_buffer_path(cfg):
    # intake.buffer 是模组读的那个键 (wa_common.apply_defaults 把 paths.intake_buffer 对齐进来)
    return _cfg_path(((cfg or {}).get("intake") or {}).get("buffer")) or _cfg_path(_paths(cfg).get("intake_buffer"))


def person_playbook_path(cfg):
    return _cfg_path(_paths(cfg).get("person_playbook"))


def owner_label(cfg):
    s = str((cfg or {}).get("owner_label") or "").strip()
    if s:
        return s
    names = [str(n).strip() for n in ((cfg or {}).get("my_names") or []) if str(n).strip()]
    return names[0] if names else "主人"


def facts_source_label(cfg):
    f = (cfg or {}).get("facts") or {}
    src = str(f.get("source") or "none").strip()
    if not f.get("enabled") or src in ("", "none"):
        return "记录系统 (还没接)"
    if WC is not None and hasattr(WC, "source_label"):
        try:
            lab = str(WC.source_label(cfg, "facts") or "").strip()
            if lab:
                return lab
        except Exception:
            pass
    return str(f.get("label") or "").strip() or {"csv": "CSV", "lark": "Lark"}.get(src, src)


def wa_root():
    """WhatsApp Desktop 的本机资料夹 (标准位置住 wa_common.wa_root)。"""
    if WC is not None and hasattr(WC, "wa_root"):
        try:
            return Path(WC.wa_root())
        except Exception:
            pass
    return HOME / "Library" / "Group Containers" / "group.net.whatsapp.WhatsApp.shared"


def _ro(path, timeout=5):
    c = sqlite3.connect("file:%s?mode=ro" % urllib.parse.quote(str(path)), uri=True, timeout=timeout)
    c.row_factory = sqlite3.Row
    return c


def _fm(text):
    """档头 → (dict, 正文)。解析规矩住 wa_common.parse_frontmatter (直列清单 · 引号 · 裸值都认); 模组没载入才退回最简单那种。"""
    if WC is not None and hasattr(WC, "parse_frontmatter"):
        try:
            got = WC.parse_frontmatter(text or "")
            if isinstance(got, tuple) and got and isinstance(got[0], dict):
                return got[0], (got[1] if len(got) > 1 else "")
            if isinstance(got, dict):
                return got, text or ""
        except Exception:
            pass
    text = text or ""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("\n---", 1)
    fm = {}
    for line in parts[0].splitlines()[1:]:
        if ":" in line and not line.startswith(" "):
            k, v = line.split(":", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            fm[k.strip()] = v
    return fm, (parts[1] if len(parts) > 1 else text)


_SEED_WARNED = set()


def owner_or_seed(owner, seed, what, state=None):
    """主人副本优先; 不在就退回 payload 种子, 并大声讲: wa_live.log 一行 WARNING (每个进程每种一次) + meta 键 seed_fallback:<what> (给日报健康行)。
    回 (Path 或 None, 是不是退回种子)。"""
    owner = Path(owner) if owner else None
    if owner is not None and owner.is_file():
        if state is not None and what in _SEED_WARNED:
            try:
                state.del_meta("seed_fallback:" + what)
            except Exception:
                pass
            _SEED_WARNED.discard(what)
        return owner, False
    seed = Path(seed)
    if what not in _SEED_WARNED:
        log.warning("WARNING: %s 的主人副本不在 (%s), 退回 payload 种子 %s; 跑 wa_setup.py seed-status 查, 种子会随 skills update 被换掉",
                    what, owner or "config 没写路径", seed if seed.is_file() else "(种子也不在)")
        _SEED_WARNED.add(what)
        if state is not None:
            try:
                state.set_meta("seed_fallback:" + what, "%s %s" % (now_iso(), seed))
            except Exception:
                pass
    return (seed if seed.is_file() else None), True


def find_scripts_dir(cfg, key, suffix, marker):
    """读取器 / 人物档核心的 scripts 夹: paths.<key> 给了就用 (给的是那个夹、它的上一层、或安装包根都认), 否则安装包里 skills/*<suffix>/scripts。"""
    cands = []
    v = _cfg_path(_paths(cfg).get(key))
    if v is not None:
        cands += [v, v / "scripts"] + sorted((v / "skills").glob("*%s/scripts" % suffix))
    cands += sorted((PAYLOAD / "skills").glob("*%s/scripts" % suffix))
    for c in cands:
        if (c / marker).is_file():
            return c
    return cands[0] if cands else (PAYLOAD / "skills" / ("whatsapp%s" % suffix) / "scripts")


# ---------------------------------------------------------------- 词表 (config 的 vocab, 板子 · 提示 · normalize · 时间规则 · 付款地板 · 开档读同一份)

DEFAULT_VOCAB = {
    "worlds": [{"key": "personal", "label": "私人", "label_en": "Personal", "business": False},
               {"key": "work", "label": "工作", "label_en": "Work", "business": True},
               {"key": "other", "label": "其他", "label_en": "Other", "business": False}],
    "stages": [{"key": "new", "label": "新", "label_en": "New"}, {"key": "talking", "label": "在谈", "label_en": "Talking"},
               {"key": "offered", "label": "已报价", "label_en": "Offered"}, {"key": "won", "label": "已成交", "label_en": "Won"},
               {"key": "active", "label": "服务中", "label_en": "Active"}, {"key": "done", "label": "结束", "label_en": "Done"},
               {"key": "parked", "label": "搁置", "label_en": "Parked"}],
    "park_from": ["new", "talking", "offered"],
    "priorities": [{"key": "now", "label": "现在", "label_en": "Now"}, {"key": "soon", "label": "这几天", "label_en": "Soon"},
                   {"key": "low", "label": "不急", "label_en": "Low"}],
    "business_context": "",
}


def _as_entries(items):
    out = []
    for e in items or []:
        if isinstance(e, dict):
            k = str(e.get("key") or "").strip()
            if not k:
                continue
            d = dict(e)
            d["key"] = k
            d["label"] = str(d.get("label") or k)
            d["label_en"] = str(d.get("label_en") or d["label"])
            out.append(d)
        elif isinstance(e, str) and e.strip():
            out.append({"key": e.strip(), "label": e.strip(), "label_en": e.strip()})
    return out


class Vocab(object):
    """一份 config 的词表。先问 wa_common.vocab(cfg) (唯一的正规化), 形状不认得就读 cfg["vocab"] 原文, 再没有才用默认。
    world 第一次判后黏住 · stage 只准往前 (ladder), parked 只从 park_from 进 · priority 由急到缓, 最后一格是「不急」。"""

    def __init__(self, cfg=None):
        cfg = cfg if isinstance(cfg, dict) else {}
        raw = None
        if WC is not None and hasattr(WC, "vocab") and cfg:
            try:
                raw = WC.vocab(cfg)
            except Exception as e:
                log.debug("wa_common.vocab 失败, 读 config 原文: %s", e)
        src = cfg.get("vocab") if isinstance(cfg.get("vocab"), dict) else {}

        def pick(name):
            for d in (raw, src):
                if isinstance(d, dict) and isinstance(d.get(name), (list, tuple)) and len(d.get(name)):
                    return list(d[name])
            return list(DEFAULT_VOCAB[name])

        self.worlds = _as_entries(pick("worlds")) or _as_entries(DEFAULT_VOCAB["worlds"])
        self.stages = _as_entries(pick("stages")) or _as_entries(DEFAULT_VOCAB["stages"])
        self.priorities = _as_entries(pick("priorities")) or _as_entries(DEFAULT_VOCAB["priorities"])
        # wa_common 的正规化只留 key / label / label_en / business; 给提示的判准 (criteria) 从 config 原文按 key 补回来
        for items, name in ((self.worlds, "worlds"), (self.stages, "stages"), (self.priorities, "priorities")):
            extra = dict((e.get("key"), e) for e in (src.get(name) or []) if isinstance(e, dict))
            for e in items:
                crit = (extra.get(e["key"]) or {}).get("criteria")
                if crit and not e.get("criteria"):
                    e["criteria"] = crit
        self.world_keys = tuple(w["key"] for w in self.worlds)
        self.stage_keys = tuple(s["key"] for s in self.stages)
        self.priority_keys = tuple(p["key"] for p in self.priorities)
        if isinstance(raw, dict) and "parked" in raw:
            self.parked = raw.get("parked") or None                              # wa_common 定的旁支格 (side: true, 否则 key parked)
        else:
            parked = [s["key"] for s in self.stages if s.get("side") is True]
            parked = parked or [s["key"] for s in self.stages if s["key"] == "parked"]
            self.parked = parked[0] if parked else None
        order = raw.get("stage_order") if isinstance(raw, dict) else None
        if isinstance(order, (list, tuple)) and order:
            self.ladder = tuple(k for k in order if k in self.stage_keys and k != self.parked)
        else:
            self.ladder = tuple(k for k in self.stage_keys if k != self.parked)
        pf = [str(x.get("key") if isinstance(x, dict) else x).strip() for x in pick("park_from")]
        self.park_from = tuple(k for k in pf if k in self.ladder)
        business_worlds = raw.get("business_worlds") if isinstance(raw, dict) else None
        self._business = {}
        for w in self.worlds:
            k = w["key"]
            b = None
            if isinstance(business_worlds, (list, tuple)):
                b = k in business_worlds
            elif WC is not None and hasattr(WC, "world_is_business") and cfg:
                try:
                    b = bool(WC.world_is_business(cfg, k))
                except Exception:
                    b = None
            if b is None:
                b = bool(w.get("business")) if "business" in w else (k not in ("personal", "other"))
            self._business[k] = b
        nb = [k for k in self.world_keys if not self._business.get(k)]
        self.fallback_world = "other" if "other" in self.world_keys else (nb[-1] if nb else self.world_keys[-1])
        self.lowest_priority = self.priority_keys[-1]
        self.return_from_park = self.ladder[1] if len(self.ladder) > 1 else (self.ladder[0] if self.ladder else None)
        self.parkable_timeout = self.park_from[-1] if self.park_from else None     # 「报价后 N 天没动静」自动搁置的那一格
        last_pf = self.ladder.index(self.park_from[-1]) if self.park_from else -1
        self.waiting_stages = tuple(self.ladder[:last_pf + 2]) if last_pf >= 0 else ()   # 等对方: 可搁置的几格加成交那一格
        self.done_stage = "done" if "done" in self.ladder else (self.ladder[-1] if self.ladder else None)
        bc = raw.get("business_context") if isinstance(raw, dict) else None
        self.business_context = str(bc if bc is not None else (src.get("business_context") or "")).strip()

    def is_business(self, world):
        return bool(world) and bool(self._business.get(world))

    def stage_index(self, stage):
        return self.ladder.index(stage) if stage in self.ladder else -1

    def ui(self):
        """板子 ui.vocab: 标签照 config (label 给 zh, label_en 给 en)。"""
        return {"worlds": [{"key": w["key"], "label": w["label"], "label_en": w["label_en"], "business": self.is_business(w["key"])} for w in self.worlds],
                "stages": [{"key": s["key"], "label": s["label"], "label_en": s["label_en"], "parked": s["key"] == self.parked} for s in self.stages],
                "priorities": [{"key": p["key"], "label": p["label"], "label_en": p["label_en"]} for p in self.priorities],
                "park_from": list(self.park_from)}


def _cell(s):
    return re.sub(r"\s+", " ", str(s or "")).replace("|", "/").strip()


_WORLD_PRIVATE_HINT = "家人 / 朋友 / 同学群 / 邻居 / 房东租客这类私人往来; 对方 30 天内一句没提生意、客户、项目、发票"
_WORLD_BUSINESS_HINT = "谈的是「%s」这门生意的事 (见上面的生意说明): 客户、询价、报价、订单、交付、合作, 或第二大脑说他是这门生意的客户 / 伙伴, 或群名带这门生意的名字"
_WORLD_FALLBACK_HINT = "频道广播、别人的课程或社群、认不出的号码、跟{owner}的生意无关的第三家公司。⛔ 这一格不等于不理: priority 照判"
_STAGE_HINTS = {
    "new": ("第一次来, 只打招呼", "群刚建", "对话包里还没有{owner}的讯息, 第二大脑零命中, 对方没问具体事"),
    "talking": ("问了价钱 / 日期 / 能不能做", "需求在谈", "讯息里有价钱 / 日期 / 怎么买或怎么报名的问句, 但{owner}还没报价、没给付款方式"),
    "offered": ("{owner}给了报价 / 优惠 / 付款方式", "提案或报价出去了", "{owner}的讯息里有报价数字、优惠码、付款链接或报价单, 对方还没付。⭐ 口头说会买但钱没到, 仍是这一格"),
    "won": ("付了 / 下单了 / 报了名", "定了日期 / 付了订金 / 签了", "对方说付了 / 下单, 或第二大脑 / 系统记录说收到款; 服务或交付还没开始"),
    "active": ("客户在用在问", "项目在做在改", "对方在人物档或客户名单里, 或在谈已交付的东西怎么用"),
    "done": ("那件事已交付", "活动或项目已经结束", "对方或{owner}明说结束了, 或项目 brief 已结案"),
    "parked": ("明说不来 / 不合适", "项目停了", "对方明说的 (太贵 · 没时间 · 以后再说)"),
}
_PRIORITY_HINTS = (
    "对方要{owner}一个决定 / 东西 / 回覆而且 48 小时内 (讯息里有今天 / 明天 / 两天内的日期, 或钱在等); 或已付钱的客户说卡住 / 坏了; 或家人要他今天做一件事",
    "问了 / 要了但没时限; 或{owner}答应过的还没交 (包里他说「我发你 / 我改 / 我查」之后没见交付); 或报价出去超过 3 天对方没动静",
    "没有对{owner}的问句或请求; 公告 / 转发 / 贴图 / 闲聊; {owner}已回而对方只说谢谢 / ok",
)


def render_worlds_block(V, owner):
    lines = ["| 值 | 板上叫 | 判准 |", "|---|---|---|"]
    for w in V.worlds:
        k = w["key"]
        crit = str(w.get("criteria") or "").strip()
        if not crit:
            if V.is_business(k):
                crit = _WORLD_BUSINESS_HINT % w["label"]
            elif k == V.fallback_world:
                crit = _WORLD_FALLBACK_HINT
            else:
                crit = _WORLD_PRIVATE_HINT
        lines.append("| `%s` | %s | %s |" % (k, _cell(w["label"]), _cell(crit.replace("{owner}", owner))))
    return "\n".join(lines)


def render_stages_block(V, owner, parked_after_days):
    lines = ["| 值 | 板上叫 | 人 | 群 / 项目 | 机器判准 |", "|---|---|---|---|---|"]
    for s in V.stages:
        k = s["key"]
        person, group, rule = _STAGE_HINTS.get(k, ("", "", ""))
        crit = str(s.get("criteria") or "").strip()
        if crit:
            person, group, rule = "", "", crit
        if not (person or group or rule):
            rule = "照上面的生意说明判"
        if k == V.parked and V.parkable_timeout and int(parked_after_days or 0) > 0:
            rule += "。「`%s` 之后 %s 天双方零讯息」由系统用时间算, 不是你判" % (V.parkable_timeout, parked_after_days)
        elif k == V.parked:
            rule += "。自动搁置关着 (stage_rules.parked_after_days 是 0): 这一格只在对方明说暂停或主人在板上按时才进"
        if V.parked and k in V.park_from:
            rule += " (可以从这一格进 `%s`)" % V.parked
        lines.append("| `%s` | %s | %s | %s | %s |" % (k, _cell(s["label"]), _cell(person.replace("{owner}", owner)),
                                                     _cell(group.replace("{owner}", owner)), _cell(rule.replace("{owner}", owner))))
    return "\n".join(lines)


def render_priorities_block(V, owner):
    lines = ["| 值 | 板上叫 | 判准 |", "|---|---|---|"]
    n = len(V.priorities)
    for i, p in enumerate(V.priorities):
        crit = str(p.get("criteria") or "").strip()
        if not crit:
            crit = _PRIORITY_HINTS[0] if i == 0 else (_PRIORITY_HINTS[2] if i == n - 1 else _PRIORITY_HINTS[1])
        lines.append("| `%s` | %s | %s |" % (p["key"], _cell(p["label"]), _cell(crit.replace("{owner}", owner))))
    return "\n".join(lines)


# ---------------------------------------------------------------- 电话 · 媒体 · 转写快取

_phone_cache = {}
_phone_miss = {}
PHONE_MISS_TTL = 600


def resolve_phone(jid):
    """一对一 jid → 纯数字电话 (给 whatsapp://send?phone= 用)。@s.whatsapp.net 直接拆; @lid (WhatsApp 的隐私 id)
    查 LID.sqlite 的 ZWAZACCOUNT / ZWAPHONENUMBERLIDPAIR, 再退到 ContactsV2 的通讯录。群回 None (网址格式开不了群)。
    解到的记住; 解不到的 10 分钟内不重查 (板子每 30 秒刷新, 否则每行都开四次库)。"""
    if not jid or jid.endswith("@g.us"):
        return None
    if jid in _phone_cache:
        return _phone_cache[jid]
    head = jid.split("@")[0]
    if jid.endswith("@s.whatsapp.net") and head.isdigit():
        _phone_cache[jid] = head
        return head
    if not jid.endswith("@lid"):
        return None
    miss = _phone_miss.get(jid)
    if miss and time.time() - miss < PHONE_MISS_TTL:
        return None
    root = wa_root()
    phone = None
    for db, sql in ((root / "LID.sqlite", "SELECT ZPHONENUMBER FROM ZWAZACCOUNT WHERE ZIDENTIFIER=?"),
                    (root / "LID.sqlite", "SELECT ZPHONENUMBER FROM ZWAPHONENUMBERLIDPAIR WHERE ZLID=? ORDER BY ZTIMESTAMP DESC"),
                    (root / "ContactsV2.sqlite", "SELECT ZPHONENUMBER FROM ZWAADDRESSBOOKCONTACT WHERE ZLID=?"),
                    (root / "ContactsV2.sqlite", "SELECT ZWHATSAPPID FROM ZWAADDRESSBOOKCONTACT WHERE ZLID=?")):
        if not db.exists():
            continue
        try:
            c = _ro(db, timeout=3)
            try:
                r = c.execute(sql, (jid,)).fetchone()
            finally:
                c.close()
        except Exception as e:
            log.debug("resolve_phone %s: %s", db.name, e)
            continue
        if r and r[0]:
            digits = re.sub(r"\D", "", str(r[0]).split("@")[0])
            if len(digits) >= 8:
                phone = digits
                break
    if phone:
        _phone_cache[jid] = phone
        _phone_miss.pop(jid, None)
    else:
        _phone_miss[jid] = time.time()
    return phone


def wa_link(phone, text):
    return "whatsapp://send?phone=%s&text=%s" % (phone, urllib.parse.quote(text or "")) if phone else None


# 板子能直接显示的媒体: 图片 / 贴图 / 缩图当 <img>, mp4 当 <video>, 语音原档给播放钮。
# WhatsApp 的 .thumb 档其实是 JPEG; GIF 类讯息存的是 mp4; 语音是 .opus。别的 (pdf / 文件) 不送, 板上只显示种类。
MEDIA_CTYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
                ".heic": "image/heic", ".thumb": "image/jpeg", ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime"}
AUDIO_CTYPES = {".opus": "audio/ogg", ".ogg": "audio/ogg", ".oga": "audio/ogg", ".m4a": "audio/mp4", ".aac": "audio/aac",
                ".mp3": "audio/mpeg", ".wav": "audio/wav", ".caf": "audio/x-caf", ".amr": "audio/amr"}
MEDIA_MAX_BYTES = 40 * 1024 * 1024
MEDIA_CHUNK = 256 * 1024


def resolve_media(rel):
    """ZMEDIALOCALPATH / ZXMPPTHUMBPATH 的相对路径 → 真档 (在 WhatsApp 的 Group Containers 里的才算, 挡 ../ 与捷径逃逸)。"""
    if not rel:
        return None
    base_root = wa_root()
    try:
        root = base_root.resolve()
    except Exception:
        return None
    for base in (base_root / "Message", base_root):
        try:
            p = (base / rel).resolve()
        except Exception:
            continue
        if str(p).startswith(str(root) + os.sep) and p.is_file():
            return p
    return None


def media_view(mpath, thumb, audio=False):
    """板子要的两样: (档, content-type)。语音只给原档; 其他原档能显示就给原档, 原档不在或不是能显示的种类退到缩图。"""
    if audio:
        p = resolve_media(mpath)
        ct = AUDIO_CTYPES.get(p.suffix.lower()) if p else None
        return (p, ct) if ct else (None, None)
    for rel in (mpath, thumb):
        p = resolve_media(rel)
        if p:
            ct = MEDIA_CTYPES.get(p.suffix.lower())
            if ct:
                return p, ct
    return None, None


def transcript_key(media_path):
    """语音转写快取的钥匙, 算法只住 wa_common.transcript_key (读取器与板子同一条: sha1("<真实绝对路径>|<磁碟上的大小>"))。"""
    if WC is not None and hasattr(WC, "transcript_key"):
        try:
            return WC.transcript_key(media_path)
        except Exception as e:
            log.debug("transcript_key: %s", e)
    p = Path(media_path).resolve()
    return hashlib.sha1(("%s|%s" % (p, p.stat().st_size)).encode("utf-8")).hexdigest()


def parse_range(header, size):
    """HTTP Range (只认单段 bytes=a-b / a- / -n) → (start, end) · "ignore" (不认得的形状, 回整档) · None (416)。"""
    h = (header or "").strip()
    if not h.lower().startswith("bytes="):
        return "ignore"
    spec = h[6:].strip()
    if "," in spec:
        return "ignore"
    a, sep, b = spec.partition("-")
    if not sep:
        return None
    a, b = a.strip(), b.strip()
    try:
        if a == "":
            n = int(b)
            if n <= 0 or size <= 0:
                return None
            return max(0, size - n), size - 1
        start = int(a)
        end = int(b) if b else size - 1
    except ValueError:
        return None
    if start < 0 or start >= size or end < start:
        return None
    return start, min(end, size - 1)


# ---------------------------------------------------------------- config

def default_config_path():
    if WC is not None and getattr(WC, "DEFAULT_CONFIG", None):
        return Path(str(WC.DEFAULT_CONFIG)).expanduser()
    env = os.environ.get("WA_LIVE_CONFIG")
    return Path(env).expanduser() if env else HOME / "WhatsApp-Live" / "live" / "config.json"


def load_config(path):
    """默认值只住 wa_common (load_config / apply_defaults, 每个模组读到同一套); 这里只做 daemon 自己的两件事:
    建脑的空白工作夹 (brain.cwd, 不准在 Documents 底下) 与命令夹 live/commands。"""
    path = Path(path).expanduser()
    if not path.exists():
        sys.exit("config 不在: %s (先照安装向导跑 wa_setup.py init, 再按 C2 站落 config)" % path)
    if WC is None:
        sys.exit("wa_common 载入失败, daemon 起不来: %s" % _IMPORT_ERRORS.get("wa_common", "?"))
    cfg = WC.load_config(str(path))
    b = cfg.setdefault("brain", {})
    cwd = _cfg_path(b.get("cwd")) or (home_dir(cfg) / "brain-cwd")
    b["cwd"] = str(cwd)
    cwd.mkdir(parents=True, exist_ok=True)
    lv = live_dir(cfg)
    lv.mkdir(parents=True, exist_ok=True)
    (lv / "commands").mkdir(parents=True, exist_ok=True)
    _load_dossier_core(cfg)
    return cfg


# ---------------------------------------------------------------- state

_GROUP_DATE_RE = re.compile(r"(?:^|(?<=[\s(\[（【]))(\d{1,2})/(\d{1,2})(?=$|[\s)\]）】,，.。!！])")


def group_name_date(name, today=None):
    """群名里的「日/月」→ date (给 stage_rules.group_date_done, 默认关)。前后要是空白、括号或字串头尾才认;
    「24/7」是全天候不是日期; 没有年份, 离今天超过半年就算去年或明年 (十二月建的一月活动群不会一建好就被标 done)。"""
    today = today or dt.date.today()
    for m in _GROUP_DATE_RE.finditer(name or ""):
        day, mon = int(m.group(1)), int(m.group(2))
        if (day, mon) == (24, 7):
            continue
        try:
            d = dt.date(today.year, mon, day)
        except ValueError:
            continue
        try:
            if (d - today).days > 183:
                d = dt.date(today.year - 1, mon, day)
            elif (today - d).days > 183:
                d = dt.date(today.year + 1, mon, day)
        except ValueError:
            continue
        return d
    return None


class State(object):
    def __init__(self, path, cfg=None):
        self.path = Path(path)
        self.cfg = cfg if isinstance(cfg, dict) else {}
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=15)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS conversations(
          jid TEXT PRIMARY KEY, name TEXT, kind TEXT, tag TEXT, needs_me INTEGER, draft TEXT,
          reason TEXT, confidence REAL, last_in_pk INTEGER, last_in_at TEXT, last_out_at TEXT,
          last_run_id INTEGER, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS runs(
          id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, jid TEXT, name TEXT,
          burst_pks TEXT, burst_n INTEGER, model TEXT, cost_usd REAL, duration_ms INTEGER, num_turns INTEGER,
          ok INTEGER, error TEXT, output TEXT, cage_breach TEXT, denials TEXT, run_dir TEXT, kind TEXT);
        CREATE TABLE IF NOT EXISTS pending(
          jid TEXT PRIMARY KEY, name TEXT, kind TEXT, first_at REAL, last_at REAL, items TEXT);
        CREATE TABLE IF NOT EXISTS inbound_log(
          pk INTEGER PRIMARY KEY, seen_at TEXT, msg_at TEXT, jid TEXT, name TEXT, kind TEXT, mtype INTEGER,
          decision TEXT, policy TEXT);
        """)
        self.conn.commit()
        # 旧库用 ALTER 补栏, 不重建 (新库一样走这里, 升级路径只有一条)
        def add_cols(table, cols):
            have = {r[1] for r in self.conn.execute("PRAGMA table_info(%s)" % table).fetchall()}
            for col, typ in cols:
                if col not in have:
                    self.conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, typ))
        add_cols("conversations", (("situation", "TEXT"), ("timeline", "TEXT"),
                                   ("world", "TEXT"), ("stage", "TEXT"), ("stage_auto", "INTEGER"), ("priority", "TEXT"),
                                   ("stage_changed_at", "TEXT"), ("stake", "TEXT"),
                                   ("summary", "TEXT"), ("grounded_at", "TEXT"),      # 滚动摘要 + 上次全量接地的时间
                                   ("refs", "TEXT"),                                  # 可借鉴 (JSON 阵列, 最多三条)
                                   ("stage_prev", "TEXT"),                            # 日报「阶段动了 a → b」要的前一格
                                   ("calendar_hint", "TEXT"), ("calendar_done", "TEXT"),
                                   ("draft_owner_at", "TEXT"),                        # 主人在板上改过草稿的时间 (有值 = 新判断不盖他的字)
                                   ("draft_brain", "TEXT")))                          # 主人改过时, 脑这次写的草稿另放这里给板上选
        add_cols("runs", (("input_tokens", "INTEGER"), ("output_tokens", "INTEGER"),
                          ("cache_read_tokens", "INTEGER"), ("cache_create_tokens", "INTEGER")))
        add_cols("inbound_log", (("speaker_jid", "TEXT"),))                           # 群里谁说的 (夜批计数按发言人算)
        self.conn.commit()
        try:
            os.chmod(str(self.path), 0o600)                                           # 状态库里是别人的讯息与判断
        except Exception:
            pass

    def vocab(self):
        return Vocab(self.cfg)

    def get_meta(self, key, default=None):
        with self.lock:
            r = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_meta(self, key, value):
        with self.lock:
            self.conn.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                              (key, str(value)))
            self.conn.commit()

    def del_meta(self, key):
        with self.lock:
            self.conn.execute("DELETE FROM meta WHERE key=?", (key,))
            self.conn.commit()

    def log_inbound(self, pk, msg_at, jid, name, kind, mtype, decision, policy, speaker_jid=None):
        with self.lock:
            self.conn.execute("""INSERT OR REPLACE INTO inbound_log(pk,seen_at,msg_at,jid,name,kind,mtype,decision,policy,speaker_jid)
                VALUES(?,?,?,?,?,?,?,?,?,?)""", (pk, now_iso(), msg_at, jid, name, kind, mtype, decision, policy, speaker_jid))
            self.conn.commit()

    def logged_pks(self, lo):
        """门铃往回看时用: inbound_log 里 pk > lo 的都已处理过 (consider 的每条路都会 log), 回集合。"""
        with self.lock:
            return {r[0] for r in self.conn.execute("SELECT pk FROM inbound_log WHERE pk > ?", (lo,)).fetchall()}

    def inbound_empty(self):
        with self.lock:
            return self.conn.execute("SELECT 1 FROM inbound_log LIMIT 1").fetchone() is None

    def add_pending(self, jid, name, kind, item):
        with self.lock:
            r = self.conn.execute("SELECT * FROM pending WHERE jid=?", (jid,)).fetchone()
            now = time.time()
            if r:
                items = json.loads(r["items"]); items.append(item)
                self.conn.execute("UPDATE pending SET last_at=?, items=?, name=? WHERE jid=?",
                                  (now, json.dumps(items, ensure_ascii=False), name, jid))
            else:
                self.conn.execute("INSERT INTO pending VALUES(?,?,?,?,?,?)",
                                  (jid, name, kind, now, now, json.dumps([item], ensure_ascii=False)))
            self.conn.commit()

    def pending_all(self):
        with self.lock:
            return [dict(r) for r in self.conn.execute("SELECT * FROM pending ORDER BY first_at").fetchall()]

    def pop_pending(self, jid):
        with self.lock:
            r = self.conn.execute("SELECT * FROM pending WHERE jid=?", (jid,)).fetchone()
            if r:
                self.conn.execute("DELETE FROM pending WHERE jid=?", (jid,)); self.conn.commit()
            return dict(r) if r else None

    def consume_pending(self, jid, pks):
        """run 收尾才把处理过的讯息从 pending 拿掉 (daemon 中途重启, 没跑完的 burst 还在, 会重跑)。
        run 期间又进来的新讯息留下来当一个新 burst。"""
        pks = set(pks)
        with self.lock:
            r = self.conn.execute("SELECT * FROM pending WHERE jid=?", (jid,)).fetchone()
            if not r:
                return
            left = [it for it in json.loads(r["items"]) if it.get("pk") not in pks]
            if left:
                now = time.time()
                self.conn.execute("UPDATE pending SET first_at=?, last_at=?, items=? WHERE jid=?",
                                  (now, now, json.dumps(left, ensure_ascii=False), jid))
            else:
                self.conn.execute("DELETE FROM pending WHERE jid=?", (jid,))
            self.conn.commit()

    def mark_replied(self, jid, name, kind, pk, msg_at):
        """主人回了: tag 进 replied, needs_me 清掉, priority 自动最不急那格; world / stage 不动。
        主人改过的草稿这时已经发出去或过时了, draft_owner_at 清掉, 下一个判断照常给新草稿。"""
        low = self.vocab().lowest_priority
        with self.lock:
            self.conn.execute("""INSERT INTO conversations(jid,name,kind,tag,needs_me,priority,last_out_at,updated_at)
                VALUES(?,?,?,'replied',0,?,?,?)
                ON CONFLICT(jid) DO UPDATE SET tag='replied', needs_me=0, priority=excluded.priority, last_out_at=excluded.last_out_at,
                updated_at=excluded.updated_at, name=COALESCE(excluded.name, conversations.name), draft_owner_at=NULL""",
                              (jid, name, kind, low, msg_at, now_iso()))
            self.conn.commit()

    def packet(self, jid):
        """板上「带去 Claude Code」: 一份自足的文字包 (谁 · 脑的判断 · 记忆 · 时间线 · 草稿 · 可借鉴 · 最近 30 封原文 · 给那个 session 的开场白),
        贴进任何 Claude Code session 用更强的模型深挖再写草稿。⛔ 只带这条对话自己的原文, 别人的对话只有摘要表里那一行的程度 (refs)。"""
        cfg = self.cfg
        owner = owner_label(cfg)
        c = self.get_conversation(jid) or {}
        msgs = self.thread(jid, 30)
        name = c.get("name") or (msgs and msgs[0].get("speaker")) or jid
        kind = c.get("kind") or ("group" if jid.endswith("@g.us") else "direct")
        phone = resolve_phone(jid) if kind == "direct" else None
        run_dir = ""
        if c.get("last_run_id"):
            with self.lock:
                rr = self.conn.execute("SELECT run_dir FROM runs WHERE id=?", (c["last_run_id"],)).fetchone()
            run_dir = (rr["run_dir"] if rr else "") or ""
        vault = vault_dir(cfg)
        people = people_dir(cfg)
        where = ""
        if vault is not None:
            where = "深挖时 vault 在 `%s`%s。" % (vault, (" (人物档在 `%s`)" % people) if people is not None else "")
        if run_dir:
            where += "这次 run 的完整材料 (prompt.md / sweep.json / context.md) 在这台 Mac 的 `%s`。" % run_dir
        L = ["# WhatsApp 对话交棒包 · %s" % name, "",
             "> 这是 %s 私人 WhatsApp 的一条对话, 从他的实时分诊板复制出来的 (%s)。规矩跟 WhatsApp Live 一样: **只出草稿, 永不发送**, 发送权在 %s; "
             "草稿用对方的语言, 短, 像 WhatsApp; 该 %s 填的留 [中括号]; 不替他承诺钱 / 日期 / 合约。要更深的原文, 在 Claude Code 里用 WhatsApp 读取器 skill 读这条对话。%s"
             % (owner, now_iso(), owner, owner, where), ""]
        L += ["## 这是谁", "", "- %s · %s%s" % (name, "群" if kind == "group" else "一对一", (" · 电话 %s" % phone) if phone else ""),
              "- world / stage / priority: %s / %s / %s%s" % (c.get("world") or "-", c.get("stage") or "-", c.get("priority") or "-", " (系统自动改的 stage)" if c.get("stage_auto") else ""),
              "- 球在: %s · 把握度 %s" % (owner if c.get("needs_me") else "对方 / 不用回", ("%.0f%%" % (float(c.get("confidence") or 0) * 100)) if c.get("confidence") is not None else "-"), ""]
        L += ["## 脑的判断", "", "**最新状况**: %s" % (c.get("situation") or "(还没判过)"), "", "**对 %s 的利害**: %s" % (owner, c.get("stake") or "-"), "",
              "**滚动摘要 (记忆)**: %s" % (c.get("summary") or "-"), ""]
        try:
            tl = json.loads(c.get("timeline") or "[]")
        except Exception:
            tl = []
        L += ["**时间线**:"] + ["- %s" % t for t in tl] + ([""] if tl else ["- (无)", ""])
        try:
            refs = json.loads(c.get("refs") or "[]")
        except Exception:
            refs = []
        if refs:
            L += ["**可借鉴**:"] + ["- %s · %s · %s" % (r.get("what", ""), r.get("where", ""), r.get("why", "")) for r in refs] + [""]
        L += ["**现在的草稿**%s: %s" % (" (%s 在板上改过)" % owner if c.get("draft_owner_at") else "", c.get("draft") or "(空)"), ""]
        if c.get("draft_owner_at") and (c.get("draft_brain") or "").strip():
            L += ["**脑这次另写的草稿**: %s" % c.get("draft_brain"), ""]
        L += ["## 最近 %d 封原文 (最旧在前; 语音是转字; 图片只有路径)" % len(msgs), ""]
        for m in msgs:
            who = owner if m["from_me"] else ((m.get("speaker") or name) if kind == "group" else name)
            body = m.get("text") or ""
            if m.get("transcript"):
                body = (body + "\n" if body else "") + "🎤 " + m["transcript"]
            elif m.get("type") not in ("文字", "text") and not body:
                body = "[%s%s]" % (m.get("type"), (" " + m["media"]) if m.get("media") else " 不在本机")
            L.append("- %s **%s**: %s" % (m["at"].replace("T", " ")[:16], who, body.replace("\n", " ⏎ ")))
        L += ["", "## 请你做的事", "", "读完上面, 用 vault 里查得到的事实, 写一封 %s 可以直接发的回覆草稿 (或告诉他为什么不用回); 有不确定的先列出来问他。⛔ 不发送。" % owner]
        return "\n".join(L) + "\n"

    def write_summaries(self, path, gate=None):
        """给下一轮「可借鉴」用的 summary 表: 每条判过的对话一行, 脑读这张表不读原文。每次 run 收尾重写。
        gate(jid, kind) 给了 (backfill.respect_gate): 只有回填摘要、闸又不放行的对话 (skip 群、ignored) 不列, 免得挡掉的群经摘要表进每个判断。"""
        with self.lock:
            # 判过的对话排前面, 只有回填摘要的行 (last_run_id 空) 排后面, 免得挤掉第 5 节的字数上限
            rows = self.conn.execute("""SELECT jid, name, kind, world, stage, priority, summary, updated_at, last_run_id FROM conversations
                WHERE summary IS NOT NULL AND TRIM(summary) != '' ORDER BY (last_run_id IS NULL), updated_at DESC""").fetchall()
        if gate is not None:
            kept = []
            for r in rows:
                if r["last_run_id"] is None:
                    try:
                        if not gate(r["jid"] or "", r["kind"] or ("group" if (r["jid"] or "").endswith("@g.us") else "direct"))[0]:
                            continue
                    except Exception:
                        pass
                kept.append(r)
            rows = kept
        lines = ["# 对话摘要表 (daemon 生成, %s, %d 条)" % (now_iso(), len(rows)), "",
                 "每行: 名字 · 群/一对一 · world / stage / priority · 最后判断时间 · 滚动摘要。⛔ 别人对话的内容可以影响判断, 不准出现在草稿文字里。", ""]
        for r in rows:
            lines.append("- **%s** · %s · %s / %s / %s · %s · %s" % (r["name"], "群" if r["kind"] == "group" else "一对一", r["world"] or "-",
                                                                 r["stage"] or "-", r["priority"] or "-", (r["updated_at"] or "")[:16],
                                                                 (r["summary"] or "").replace("\n", " ")))
        try:
            p = Path(path)
            fd, tmp = tempfile.mkstemp(prefix=p.name + ".", dir=str(p.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            os.replace(tmp, str(p))
        except Exception as e:
            log.warning("summaries.md 写不了: %s", e)

    def set_field(self, jid, field, value):
        """板上改 world / stage (主人是唯一能把 stage 往回拨的人); 值要在 config 词表里。改过的 stage 标 stage_auto=0 并记前一格。
        world 改成非生意世界时, 后端当场把 stage 清掉 (不等下一个 run), 板子刷新不会把旧 stage 带回来。回改了几行。"""
        V = self.vocab()
        value = str(value or "").strip()
        now = now_iso()
        with self.lock:
            cur = self.conn.execute("SELECT world, stage FROM conversations WHERE jid=?", (jid,)).fetchone()
            if cur is None:
                return 0
            if field == "world":
                if value not in V.world_keys:
                    return 0
                if V.is_business(value):
                    c = self.conn.execute("UPDATE conversations SET world=?, updated_at=? WHERE jid=?", (value, now, jid))
                else:
                    c = self.conn.execute("""UPDATE conversations SET world=?, stage=NULL, stage_auto=0,
                        stage_prev=CASE WHEN stage IS NOT NULL THEN stage ELSE stage_prev END,
                        stage_changed_at=CASE WHEN stage IS NOT NULL THEN ? ELSE stage_changed_at END,
                        updated_at=? WHERE jid=?""", (value, now, now, jid))
            elif field == "stage":
                if value and value not in V.stage_keys:
                    return 0
                if value and cur["world"] and not V.is_business(cur["world"]):
                    return 0
                c = self.conn.execute("""UPDATE conversations SET stage=?, stage_auto=0,
                    stage_prev=CASE WHEN COALESCE(stage,'') != ? THEN stage ELSE stage_prev END,
                    stage_changed_at=?, updated_at=? WHERE jid=?""", (value or None, value, now, now, jid))
            else:
                return 0
            self.conn.commit()
            return c.rowcount

    def stage_rules(self, parked_after_days=21, group_date_done=False):
        """零 LLM 的时间规则: 可自动搁置的那一格 (park_from 最后一格, 默认 offered) 之后 N 天双方都没动静 → parked;
        群名带日期且过了 → done (stage_rules.group_date_done, 默认关, 只碰生意世界的群)。两条都记前一格、标系统自动改的。回传改了几行。"""
        V = self.vocab()
        changed = 0
        now = now_iso()
        with self.lock:
            # parked_after_days 0 或负数 = 关掉这条规则 (设定说明与 check-config 都这样讲; 以前 0 会把那一格全部立刻搁置)
            if V.parked and V.parkable_timeout and int(parked_after_days or 0) > 0:
                cut = (dt.datetime.now() - dt.timedelta(days=parked_after_days)).isoformat(timespec="seconds")
                cur = self.conn.execute("""UPDATE conversations SET stage_prev=stage, stage=?, stage_auto=1, stage_changed_at=?
                    WHERE stage=? AND MAX(COALESCE(last_in_at,''), COALESCE(last_out_at,''), COALESCE(stage_changed_at,'')) < ?""",
                                        (V.parked, now, V.parkable_timeout, cut))
                changed += cur.rowcount
            if group_date_done and V.done_stage:
                rows = self.conn.execute("""SELECT jid, name, world FROM conversations WHERE kind='group' AND name IS NOT NULL
                    AND (stage IS NULL OR stage != ?)""", (V.done_stage,)).fetchall()
                today = dt.date.today()
                for r in rows:
                    if not V.is_business(r["world"]):
                        continue
                    d = group_name_date(r["name"], today)
                    if d is not None and d < today - dt.timedelta(days=1):
                        self.conn.execute("UPDATE conversations SET stage_prev=stage, stage=?, stage_auto=1, stage_changed_at=? WHERE jid=?",
                                          (V.done_stage, now, r["jid"]))
                        changed += 1
            self.conn.commit()
        return changed

    def get_conversation(self, jid):
        with self.lock:
            r = self.conn.execute("SELECT * FROM conversations WHERE jid=?", (jid,)).fetchone()
        return dict(r) if r else None

    def replied_after(self, jid, last_at):
        """脑跑的这几十秒里主人自己在 WhatsApp 回了没。看 conversations.last_out_at (门铃看到 ZISFROMME=1 时由 mark_replied 写)
        有没有晚于这个 burst 最后一封的时间。两边都是 core_to_iso 的本地 ISO 字串, 直接比字串。"""
        if not last_at:
            return False
        with self.lock:
            r = self.conn.execute("SELECT last_out_at FROM conversations WHERE jid=?", (jid,)).fetchone()
        return bool(r and r["last_out_at"] and str(r["last_out_at"]) > str(last_at))

    def upsert_judgment(self, jid, name, kind, j, last_pk, last_at, run_id, grounded=False, keep_replied=False):
        """tag 只写系统值 open; world / stage / priority 由 normalize() 已经套过黏住与单向规则。
        keep_replied = run 落地时主人已经回了: tag 保 replied、needs_me 0、priority 最不急, 其余判断照落, 板上那行不重开。
        主人在板上改过草稿 (draft_owner_at 有值): 他的字不盖, 脑这次的草稿放进 draft_brain 给板上选。"""
        low = self.vocab().lowest_priority
        tag = "replied" if keep_replied else "open"
        needs_me = 0 if keep_replied else (1 if j["needs_me"] else 0)
        priority = low if keep_replied else j.get("priority")
        with self.lock:
            prev = self.conn.execute("SELECT stage, draft_owner_at FROM conversations WHERE jid=?", (jid,)).fetchone()
            stage_changed = (prev is None) or ((prev["stage"] or None) != (j.get("stage") or None))
            owner_kept = bool(prev is not None and prev["draft_owner_at"])
            self.conn.execute("INSERT OR IGNORE INTO conversations(jid,name,kind,updated_at) VALUES(?,?,?,?)", (jid, name, kind, now_iso()))
            self.conn.execute("UPDATE conversations SET summary=?%s WHERE jid=?" % (", grounded_at=?" if grounded else ""),
                              ((j.get("summary") or "", now_iso(), jid) if grounded else (j.get("summary") or "", jid)))
            self.conn.execute("UPDATE conversations SET refs=? WHERE jid=?", (json.dumps(j.get("refs") or [], ensure_ascii=False), jid))
            # 日历钮: 这次的 hint 照落 (没给就清掉); 跟「建过的那个」(calendar_done.hint_key) 比, 键不同才清 calendar_done 让钮重亮
            hint = j.get("calendar_hint")
            if hint:
                old = self.conn.execute("SELECT calendar_done FROM conversations WHERE jid=?", (jid,)).fetchone()
                try:
                    done_key = (json.loads(old["calendar_done"]) or {}).get("hint_key") if (old and old["calendar_done"]) else None
                except Exception:
                    done_key = None
                new_key = WCAL.hint_key(hint) if WCAL is not None else None
                clear = bool(done_key) and done_key != new_key
                self.conn.execute("UPDATE conversations SET calendar_hint=?%s WHERE jid=?" % (", calendar_done=NULL" if clear else ""),
                                  (json.dumps(hint, ensure_ascii=False), jid))
            else:
                self.conn.execute("UPDATE conversations SET calendar_hint=NULL WHERE jid=?", (jid,))
            self.conn.execute("""INSERT INTO conversations(jid,name,kind,tag,needs_me,draft,reason,confidence,
                last_in_pk,last_in_at,last_run_id,updated_at,situation,timeline,world,stage,stage_auto,priority,stage_changed_at,stake,draft_brain)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(jid) DO UPDATE SET name=excluded.name, kind=excluded.kind, tag=excluded.tag,
                needs_me=excluded.needs_me,
                draft=CASE WHEN conversations.draft_owner_at IS NOT NULL THEN conversations.draft ELSE excluded.draft END,
                draft_brain=excluded.draft_brain, reason=excluded.reason,
                confidence=excluded.confidence, last_in_pk=excluded.last_in_pk, last_in_at=excluded.last_in_at,
                last_run_id=excluded.last_run_id, updated_at=excluded.updated_at,
                situation=excluded.situation, timeline=excluded.timeline,
                world=excluded.world, stage=excluded.stage, stage_auto=excluded.stage_auto, priority=excluded.priority,
                stage_changed_at=COALESCE(excluded.stage_changed_at, conversations.stage_changed_at), stake=excluded.stake""",
                              (jid, name, kind, tag, needs_me, j["draft"], j["reason"],
                               j["confidence"], last_pk, last_at, run_id, now_iso(),
                               j.get("situation") or "", json.dumps(j.get("timeline") or [], ensure_ascii=False),
                               j.get("world"), j.get("stage"), 0, priority, now_iso() if stage_changed else None,
                               j.get("stake") or "", (j["draft"] if owner_kept else None)))
            if stage_changed and prev is not None:
                self.conn.execute("UPDATE conversations SET stage_prev=? WHERE jid=?", (prev["stage"], jid))
            self.conn.commit()

    def start_run(self, jid, name, kind, pks, model, run_dir):
        """runs.kind: 分诊 run 写对话种类 (direct / group, 跟旧库一样); 别种 run 写 dossier / second_source / backfill, 用量读数才完整。"""
        with self.lock:
            cur = self.conn.execute("INSERT INTO runs(started_at,jid,name,burst_pks,burst_n,model,run_dir,kind) VALUES(?,?,?,?,?,?,?,?)",
                                    (now_iso(), jid, name, json.dumps(list(pks or [])), len(pks or []), model, str(run_dir), kind))
            self.conn.commit()
            return cur.lastrowid

    def finish_run(self, run_id, ok, **kw):
        cols = ["finished_at", "ok"]; vals = [now_iso(), 1 if ok else 0]
        for k in ("error", "output", "cost_usd", "duration_ms", "num_turns", "cage_breach", "denials",
                  "input_tokens", "output_tokens", "cache_read_tokens", "cache_create_tokens"):
            if k in kw:
                cols.append(k); v = kw[k]
                vals.append(json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
        with self.lock:
            self.conn.execute("UPDATE runs SET %s WHERE id=?" % ", ".join("%s=?" % c for c in cols), vals + [run_id])
            self.conn.commit()

    def board_rows(self):
        """板子读的东西: 每条跑过脑的对话 (只被 outbound 标 replied、从没判过的不列), 要回的排前面。"""
        V = self.vocab()
        with self.lock:
            rows = self.conn.execute("""SELECT * FROM conversations WHERE last_run_id IS NOT NULL
                ORDER BY CASE WHEN tag IN ('replied','done') THEN 1 ELSE 0 END, needs_me DESC, updated_at DESC LIMIT 300""").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d.pop("flow", None)                                   # 旧库才有的退役栏
            phone = resolve_phone(d["jid"] or "")
            d["phone"] = phone
            d["link"] = wa_link(phone, d.get("draft") or "")
            d["needs_me"] = bool(d.get("needs_me"))
            if d.get("tag") not in ("replied", "done"):          # tag 只剩 open / replied / done; waiting 是算出来的
                d["tag"] = "open"
            d["stage_auto"] = bool(d.get("stage_auto"))
            d["draft_owner_edited"] = bool(d.get("draft_owner_at"))
            d["waiting"] = bool((d.get("last_out_at") or "") > (d.get("last_in_at") or "")) and (d.get("stage") in V.waiting_stages)
            for k in ("timeline", "refs"):
                try:
                    d[k] = json.loads(d.get(k) or "[]")
                except Exception:
                    d[k] = []
            for k in ("calendar_hint", "calendar_done"):
                try:
                    d[k] = json.loads(d[k]) if d.get(k) else None
                except Exception:
                    d[k] = None
            d["run"] = None
            d["judged_at"] = d.get("updated_at")
            if d.get("last_run_id"):
                with self.lock:
                    rr = self.conn.execute("""SELECT duration_ms, num_turns, input_tokens, output_tokens, cache_read_tokens, cache_create_tokens,
                        model, cost_usd, finished_at FROM runs WHERE id=?""", (d["last_run_id"],)).fetchone()
                if rr:
                    d["run"] = dict(rr)
                    d["judged_at"] = d["run"].pop("finished_at", None) or d.get("updated_at")
            out.append(d)
        return out

    def thread(self, jid, n=40):
        """板子右边那条对话: 直接读 live 库最近 n 封 (read-only), 语音有快取就带转字 (快取夹 = paths.home/transcripts-cache, 跟读取器同一个)。"""
        CT = content_types()
        AUD = audio_type()
        conn = _ro(wa_root() / "ChatStorage.sqlite")
        try:
            rows = conn.execute("""SELECT m.Z_PK pk, m.ZISFROMME fromme, m.ZMESSAGETYPE mtype, m.ZTEXT text, m.ZMESSAGEDATE d,
                    m.ZPUSHNAME pushname, mi.ZMEDIALOCALPATH mpath, mi.ZXMPPTHUMBPATH thumb, mi.ZTITLE mtitle
                FROM ZWAMESSAGE m JOIN ZWACHATSESSION s ON s.Z_PK=m.ZCHATSESSION
                LEFT JOIN ZWAMEDIAITEM mi ON mi.ZMESSAGE=m.Z_PK
                WHERE s.ZCONTACTJID=? AND m.ZMESSAGETYPE IN (%s) ORDER BY m.Z_PK DESC LIMIT ?"""
                                % ",".join(str(int(t)) for t in CT), (jid, n)).fetchall()
        finally:
            conn.close()
        cache_dir = home_dir(self.cfg) / "transcripts-cache"
        out = []
        for r in reversed(rows):
            item = {"pk": r["pk"], "from_me": bool(r["fromme"]), "at": core_to_iso(r["d"]), "type": CT.get(r["mtype"], "?"),
                    "text": r["text"] or "", "speaker": _clean_pushname(r["pushname"] or ""), "media_local": False, "transcript": None,
                    "media_url": None, "media_ctype": None, "media_is_thumb": False}
            p = resolve_media(r["mpath"])
            if p:
                item["media_local"] = True       # 只说档在不在本机; 绝对路径 (Mac 帐号名、对话内部 id) 不送去浏览器, 浏览器只拿 media_url
            if r["mtype"] == AUD:
                if p:
                    try:
                        c = cache_dir / (transcript_key(p) + ".json")
                        if c.exists():
                            item["transcript"] = json.loads(c.read_text(encoding="utf-8")).get("text")
                    except Exception:
                        pass
                v, ct = media_view(r["mpath"], None, audio=True)
                if v:
                    item["media_url"] = "/media?pk=%d" % r["pk"]
                    item["media_ctype"] = ct
                    item["audio_url"] = item["media_url"]          # 板上语音播放钮 (/media 支援 Range)
            else:
                v, ct = media_view(r["mpath"], r["thumb"])
                if v:
                    item["media_url"] = "/media?pk=%d" % r["pk"]
                    item["media_ctype"] = ct
                    item["media_is_thumb"] = (p is None) or (v != p)
            if r["mtitle"] and not item["text"]:
                item["text"] = r["mtitle"]
            out.append(item)
        return out

    def media_file(self, pk):
        """GET /media?pk= 用: 那封讯息的可显示档 (原档或缩图; 语音只给原档) 与 content-type。daemon 有 Full Disk Access 所以读得到; 浏览器拿的是这里送出去的 bytes。"""
        conn = _ro(wa_root() / "ChatStorage.sqlite")
        try:
            r = conn.execute("""SELECT mi.ZMEDIALOCALPATH mpath, mi.ZXMPPTHUMBPATH thumb, m.ZMESSAGETYPE mtype
                FROM ZWAMEDIAITEM mi LEFT JOIN ZWAMESSAGE m ON m.Z_PK = mi.ZMESSAGE WHERE mi.ZMESSAGE=?""", (pk,)).fetchone()
        finally:
            conn.close()
        if not r:
            return None, None
        if r["mtype"] == audio_type():
            return media_view(r["mpath"], None, audio=True)
        return media_view(r["mpath"], r["thumb"])

    def set_draft(self, jid, draft, name=None, kind=None):
        """板上改草稿: 存字; 字真的变了才记 draft_owner_at (下一个判断落地不盖他的字; 板子离开框就存, 没改的字不算主人改过)。
        还没判过的「其他对话」也能写: 先立一行, tag 留空, 不算判断。"""
        now = now_iso()
        with self.lock:
            cur = self.conn.execute("""UPDATE conversations SET draft_owner_at=CASE WHEN COALESCE(draft,'') != ? THEN ? ELSE draft_owner_at END,
                draft=? WHERE jid=?""", (draft or "", now, draft, jid))
            if cur.rowcount == 0:
                self.conn.execute("INSERT INTO conversations(jid,name,kind,draft,draft_owner_at,needs_me,updated_at) VALUES(?,?,?,?,?,0,?)",
                                  (jid, name or jid, kind or ("group" if jid.endswith("@g.us") else "direct"), draft, now, now))
            self.conn.commit()
            return cur.rowcount or 1

    def recent_chats(self, days=3, limit=120, exclude=None, with_total=False):
        """左栏「全部」那一格: 最近 N 天有动静的每一个对话, 先剔掉 exclude (板上判过的) 再取 limit 个, 判过的不占名额。
        with_total=True 回 (清单, 剔掉后的总数), 板子才能写「只列前 N 个」。直接读 live 库。"""
        exclude = set(exclude or ())
        CT = content_types()
        conn = _ro(wa_root() / "ChatStorage.sqlite")
        cut = core_now_minus(days)
        try:
            rows = conn.execute("""SELECT s.ZCONTACTJID jid, s.ZPARTNERNAME name, s.ZSESSIONTYPE stype, s.ZLASTMESSAGEDATE d, s.ZUNREADCOUNT unread,
                    m.ZTEXT text, m.ZISFROMME fromme, m.ZMESSAGETYPE mtype
                FROM ZWACHATSESSION s LEFT JOIN ZWAMESSAGE m ON m.Z_PK = s.ZLASTMESSAGE
                WHERE s.ZSESSIONTYPE IN (0,1) AND s.ZLASTMESSAGEDATE >= ? AND s.ZCONTACTJID NOT LIKE '%@status' AND s.ZCONTACTJID NOT LIKE '%@broadcast'
                ORDER BY s.ZLASTMESSAGEDATE DESC LIMIT ?""", (cut, int(limit) + len(exclude) + 1)).fetchall()
        finally:
            conn.close()
        out = []
        total = 0
        for r in rows:
            jid = r["jid"] or ""
            if jid in exclude:
                continue
            total += 1
            if len(out) >= int(limit):
                continue
            snippet = (r["text"] or "").strip().replace("\n", " ")
            if not snippet and r["mtype"] in CT and r["mtype"] != 0:
                snippet = "[%s]" % CT[r["mtype"]]
            out.append({"jid": jid, "name": r["name"] or jid, "kind": "group" if jid.endswith("@g.us") else "direct",
                        "last_at": core_to_iso(r["d"]), "last_from_me": bool(r["fromme"]), "unread": r["unread"] or 0,
                        "snippet": snippet[:120], "phone": resolve_phone(jid)})
        return (out, total) if with_total else out

    def set_calendar_done(self, jid, done):
        with self.lock:
            cur = self.conn.execute("UPDATE conversations SET calendar_done=? WHERE jid=?", (json.dumps(done, ensure_ascii=False), jid))
            self.conn.commit()
            return cur.rowcount

    def dismiss(self, jid):
        with self.lock:
            cur = self.conn.execute("UPDATE conversations SET tag='done', needs_me=0, updated_at=? WHERE jid=?", (now_iso(), jid))
            self.conn.commit()
            return cur.rowcount

    def board_status(self):
        """页脚读数: 今天所有 run (分诊、档案、第二来源、回填都算) 与 token 合计, 另给按 kind 分开的数。"""
        today = dt.date.today().isoformat()
        with self.lock:
            rows = self.conn.execute("""SELECT COALESCE(kind,'') k, COUNT(*) n, COALESCE(SUM(input_tokens),0) ti, COALESCE(SUM(output_tokens),0) to_,
                COALESCE(SUM(cache_read_tokens),0) tcr, COALESCE(SUM(cache_create_tokens),0) tcc, COALESCE(SUM(cost_usd),0) usd
                FROM runs WHERE started_at LIKE ? GROUP BY COALESCE(kind,'')""", (today + "%",)).fetchall()
            pending = self.conn.execute("SELECT COUNT(*) FROM pending").fetchone()[0]
        tot = {"input": 0, "output": 0, "cache_read": 0, "cache_create": 0, "cost_usd": 0.0}
        by_kind = {}
        n_all = 0
        for r in rows:
            k = r["k"] if r["k"] not in ("direct", "group", "") else "triage"
            e = by_kind.setdefault(k, {"runs": 0, "input": 0, "output": 0, "cache_read": 0, "cache_create": 0, "cost_usd": 0.0})
            e["runs"] += r["n"]; e["input"] += r["ti"]; e["output"] += r["to_"]; e["cache_read"] += r["tcr"]; e["cache_create"] += r["tcc"]
            e["cost_usd"] = round(e["cost_usd"] + float(r["usd"] or 0), 4)
            n_all += r["n"]; tot["input"] += r["ti"]; tot["output"] += r["to_"]; tot["cache_read"] += r["tcr"]; tot["cache_create"] += r["tcc"]
            tot["cost_usd"] = round(tot["cost_usd"] + float(r["usd"] or 0), 4)     # 板子只在 ui.show_usd 时显示 (订阅跑的美元是虚的)
        return {"last_pk": self.get_meta("last_pk"), "runs_today": n_all, "pending": pending, "tokens_today": tot, "runs_by_kind": by_kind}

    def recent_runs(self, n=10):
        with self.lock:
            return [dict(r) for r in self.conn.execute("""SELECT id,started_at,finished_at,jid,name,kind,model,burst_n,ok,error,duration_ms,
                input_tokens,output_tokens,cache_read_tokens FROM runs ORDER BY id DESC LIMIT ?""", (n,)).fetchall()]


# ---------------------------------------------------------------- notify

class Notifier(object):
    """推播出口的薄壳。规矩与实作只住 wa_common: notify_wanted(cfg, kind) 决定这一类推不推 (notify.mode: off · errors · needs_me · all),
    notify_send(cfg, text, parse_mode, key, min_gap) 真的送 (telegram · command · none), 去重只算送成功的。
    daemon 这边只多两件事: dry-run 一律不推; 模组没载入就只写 log。kind: error · needs_me · digest · info。"""

    def __init__(self, cfg, dry_run=False):
        self.cfg = cfg
        self.dry_run = dry_run
        n = cfg.get("notify") if isinstance(cfg.get("notify"), dict) else {}
        self.outlet = str(n.get("outlet") or "none")
        self.mode = str(n.get("mode") or "errors")
        log.info("notify 出口: %s (mode=%s)%s", self.outlet, self.mode, " · dry-run 一律不推" if dry_run else "")

    def wants(self, kind):
        if self.dry_run or WC is None or not hasattr(WC, "notify_wanted"):
            return False
        try:
            return bool(WC.notify_wanted(self.cfg, kind))
        except Exception as e:
            log.warning("notify_wanted 例外 (当不推): %s", e)
            return False

    def send(self, text, key=None, min_gap=0, parse_mode=None, kind="error"):
        first = (text or "").split("\n")[0][:80]
        if self.dry_run:
            log.info("notify (dry-run 不推, %s): %s", kind, first)
            return False
        if WC is None or not hasattr(WC, "notify_send"):
            log.warning("notify 没推 (wa_common 没载入): %s", first)
            return False
        if not self.wants(kind):
            log.info("notify 不推 (mode=%s, 这一则是 %s): %s", self.mode, kind, first)
            return False
        try:
            ok = bool(WC.notify_send(self.cfg, (text or "")[:3800], parse_mode=parse_mode, key=key, min_gap=min_gap))
        except Exception as e:
            log.warning("notify 例外: %s", e)
            return False
        if ok:
            log.info("notify 已推 (%s): %s", self.outlet, first)
        else:
            log.warning("notify 没推出去 (出口 %s 回失败或去重间隔未到): %s", self.outlet, first)
        return ok


# ---------------------------------------------------------------- 门铃 + 闸

def _row_get(r, key):
    """sqlite3.Row 与 dict 都认。"""
    try:
        return r[key] if key in r.keys() else None
    except Exception:
        return None


def speaker_jid_of(jid, member_jid, from_jid):
    """群讯息是谁说的。群里 ZWAGROUPMEMBER.ZMEMBERJID 是正解 (实测都是 @lid 形); 没有才退 ZWAMESSAGE.ZFROMJID,
    但群讯息的 ZFROMJID 常常就是群号本身, 那种不算。一对一回 None。"""
    if not (jid or "").endswith("@g.us"):
        return None
    for cand in (member_jid, from_jid):
        c = (cand or "").strip()
        if c and not c.endswith("@g.us") and "@" in c:
            return c
    return None


_B64ISH = re.compile(r"^[A-Za-z0-9+/]{8,}={0,2}$")


def _looks_encoded(s):
    """这版 WhatsApp 的推名栏有时是编码串。只丢「像 base64 而且带数字、+ / =、或内部两个以上大写字母、或 20 字以上」的,
    单字真名 (Alexander · McDonald) 留着。"""
    s = str(s or "").strip()
    if not _B64ISH.match(s):
        return False
    if re.search(r"[0-9+/=]", s) or len(s) >= 20:
        return True
    return len(re.findall(r"[A-Z]", s[1:])) >= 2


def _clean_pushname(pushname):
    p = str(pushname or "").strip()
    return "" if (not p or _looks_encoded(p)) else p


_speaker_cache = {}


def speaker_name(jid, pushname=""):
    """群发言人 jid → 名字。跟读取器同一个梯子的前两级: 通讯录 ZFULLNAME (LID 或电话) → WhatsApp 名字表 ZWAPROFILEPUSHNAME;
    都没有才退讯息上的推名 (只有这一级过编码串过滤)。"""
    if not jid:
        return _clean_pushname(pushname)
    if jid in _speaker_cache:
        return _speaker_cache[jid]
    name = None
    root = wa_root()
    phone = resolve_phone(jid) if jid.endswith("@lid") else (jid.split("@")[0] if jid.endswith("@s.whatsapp.net") else None)
    for db, sql, args in ((root / "ContactsV2.sqlite", "SELECT ZFULLNAME FROM ZWAADDRESSBOOKCONTACT WHERE ZLID=? OR ZWHATSAPPID=?", (jid, jid)),
                          (root / "ContactsV2.sqlite", "SELECT ZFULLNAME FROM ZWAADDRESSBOOKCONTACT WHERE ZWHATSAPPID=?", ("%s@s.whatsapp.net" % phone,) if phone else None),
                          (root / "ChatStorage.sqlite", "SELECT ZPUSHNAME FROM ZWAPROFILEPUSHNAME WHERE ZJID=?", (jid,)),
                          (root / "ChatStorage.sqlite", "SELECT ZPUSHNAME FROM ZWAPROFILEPUSHNAME WHERE ZJID=?", ("%s@s.whatsapp.net" % phone,) if phone else None)):
        if args is None or not db.exists():
            continue
        try:
            c = _ro(db, timeout=3)
            try:
                r = c.execute(sql, args).fetchone()
            finally:
                c.close()
        except Exception:
            continue
        if r and r[0] and not _looks_encoded(str(r[0])):
            name = str(r[0]).strip()
            break
    if not name:
        name = _clean_pushname(pushname)
    if name:
        _speaker_cache[jid] = name
    return name


class Doorbell(object):
    LOOKBACK = 300   # 每次扫描往回看几个 Z_PK 找「晚写进库」的讯息 (doorbell.lookback 可调)

    def __init__(self, cfg, state, notifier=None):
        self.cfg = cfg; self.state = state; self.notifier = notifier
        db = cfg.get("doorbell") if isinstance(cfg.get("doorbell"), dict) else {}
        self.enabled = bool(db.get("enabled", True))
        self.db_path = wa_root() / "ChatStorage.sqlite"
        wp = _cfg_path(db.get("wal_path"))
        self.wal_path = wp if wp is not None else Path(str(self.db_path) + "-wal")
        try:
            self.lookback = max(0, int(db.get("lookback", self.LOOKBACK)))
        except (TypeError, ValueError):
            self.lookback = self.LOOKBACK
        try:
            self.max_age_hours = float(db.get("max_message_age_hours") or 0)
        except (TypeError, ValueError):
            self.max_age_hours = 0.0
        self.full_rescan = float(db.get("full_rescan_seconds") or 60)
        self.sig = None
        self.last_full = 0
        self.my_phone = re.sub(r"\D", "", str(cfg.get("my_phone") or ""))
        self.my_names = [str(n).strip().lower() for n in (cfg.get("my_names") or []) if str(n).strip()]
        # 名字比对: 纯 ASCII 的写法照词边界比 (两个字母的昵称不会误中别的字), 中文这类照子字串比
        self._name_res = [(n, re.compile(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(n)) if re.match(r"^[\x00-\x7f]+$", n) else None)
                          for n in self.my_names]
        self.ignored = set(cfg.get("ignored_jids") or [])
        self.stale_notified = 0
        self.fresh_mark = None          # 这个进程刚定 (或重定) 的水位: 往回看的旧行记 skip:before_install, 不送闸
        self.fresh_kind = None          # "install" (新库 / 还原过的库) · "reset" (库重建)
        self.started_core = core_now_minus()
        self._my_lid = None
        self._my_lid_at = 0.0
        self._policy_warned = set()

    def signature(self):
        parts = []
        for p in (self.wal_path, self.db_path):
            try:
                st = p.stat(); parts.append((st.st_mtime, st.st_size))
            except FileNotFoundError:
                parts.append(None)
        return tuple(parts)

    def wal_age(self):
        try:
            return time.time() - self.wal_path.stat().st_mtime
        except FileNotFoundError:
            try:
                return time.time() - self.db_path.stat().st_mtime
            except FileNotFoundError:
                return None

    def connect(self):
        return _ro(self.db_path)

    def max_pk(self):
        conn = self.connect()
        try:
            return conn.execute("SELECT MAX(Z_PK) FROM ZWAMESSAGE").fetchone()[0] or 0
        finally:
            conn.close()

    def ensure_watermark(self):
        if self.state.get_meta("last_pk") is None:
            mx = self.max_pk()
            self.state.set_meta("last_pk", mx)
            self.fresh_mark = mx
            self.fresh_kind = "install"
            log.info("首跑: 水位定在 Z_PK=%s, 从现在开始, 不补旧讯息 (往回看的 %d 个编号记成 skip:before_install)", mx, self.lookback)

    def arrived_after_start(self, core_seconds):
        """讯息时间晚于 daemon 起跑前两分钟: 新装或还原后第一次扫描时, 这种是真的刚进来的, 照常送闸。"""
        try:
            return core_seconds is not None and float(core_seconds) >= self.started_core - 120
        except (TypeError, ValueError):
            return False

    def reset_watermark(self, old, mx):
        """水位比库里最大编号还大 = 库重建或重新 link 过。水位重定到当下, 旧纪录里编号更大的行搬进 inbound_log_archive
        (不然新讯息撞到旧编号会被当成处理过而静默丢), 推一句错误。"""
        with self.state.lock:
            c = self.state.conn
            c.execute("""CREATE TABLE IF NOT EXISTS inbound_log_archive(
                pk INTEGER, seen_at TEXT, msg_at TEXT, jid TEXT, name TEXT, kind TEXT, mtype INTEGER, decision TEXT, policy TEXT,
                speaker_jid TEXT, archived_at TEXT)""")
            c.execute("""INSERT INTO inbound_log_archive(pk,seen_at,msg_at,jid,name,kind,mtype,decision,policy,speaker_jid,archived_at)
                SELECT pk,seen_at,msg_at,jid,name,kind,mtype,decision,policy,speaker_jid,? FROM inbound_log WHERE pk > ?""", (now_iso(), mx))
            n = c.execute("DELETE FROM inbound_log WHERE pk > ?", (mx,)).rowcount
            c.commit()
        self.state.set_meta("last_pk", mx)
        self.fresh_mark = mx
        self.fresh_kind = "reset"
        msg = ("⚠️ [WA live] 水位 %s 比 WhatsApp 库最大编号 %s 还大 (库可能重建或重新 link 过), 水位重定到 %s; "
               "旧纪录 %d 行搬进 inbound_log_archive, 这之前的讯息不补跑。" % (old, mx, mx, n))
        log.error(msg)
        if self.notifier is not None:
            self.notifier.send(msg, key="watermark_reset", min_gap=6 * 3600, kind="error")

    def policy_for(self, jid, kind):
        if kind == "direct":
            p = self.cfg.get("direct_policy", "all")
        else:
            ent = (self.cfg.get("group_policies") or {}).get(jid)
            if isinstance(ent, dict):
                p = ent.get("policy") or self.cfg.get("unlisted_group_policy", "skip")
            elif isinstance(ent, str):
                p = ent
            else:
                p = self.cfg.get("unlisted_group_policy", "skip")
        if p not in ("all", "mention", "skip") and jid not in self._policy_warned:
            self._policy_warned.add(jid)
            log.warning("闸: %s 的档位 %r 不是 all / mention / skip, 当 skip", jid, p)
        return p

    def gate_for(self, jid, kind):
        """整条对话的闸 (板上「现在判断」与摘要表用): (放不放, 为什么)。mention 群当放 (主人手动叫的就是要看)。"""
        if jid.endswith("@status") or jid.endswith("@broadcast"):
            return False, "skip:status_or_broadcast"
        if jid in self.ignored:
            return False, "skip:ignored"
        p = self.policy_for(jid, kind)
        if p in ("all", "mention"):
            return True, "pass:" + p
        return False, "skip:policy"

    def my_lid(self):
        """主人自己的 LID: 这版 WhatsApp 群里 @ 人时文字里可能放的是 LID 编号不是电话。查不到 10 分钟后再查。"""
        if self._my_lid or not self.my_phone or WC is None or not hasattr(WC, "lid_of_phone"):
            return self._my_lid
        if time.time() - self._my_lid_at < 600:
            return None
        self._my_lid_at = time.time()
        try:
            self._my_lid = WC.lid_of_phone(self.my_phone) or None
        except Exception:
            self._my_lid = None
        return self._my_lid

    def mentions_me(self, text):
        t = (text or "").lower()
        if not t:
            return False
        if self.my_phone and ("@" + self.my_phone) in t:
            return True
        lid = self.my_lid()
        if lid and ("@" + str(lid).split("@")[0].lower()) in t:
            return True
        for n, rx in self._name_res:
            if (rx.search(t) if rx is not None else (n in t)):
                return True
        return False

    def too_old(self, core_seconds):
        if self.max_age_hours <= 0 or core_seconds is None:
            return False
        try:
            return float(core_seconds) < core_now_minus(hours=self.max_age_hours)
        except (TypeError, ValueError):
            return False

    def scan(self, force=False):
        """回传 (新行数, 过闸数)。有变化或 force 才查库。"""
        sig = self.signature()
        if not force and sig == self.sig and time.time() - self.last_full < self.full_rescan:
            return 0, 0
        self.sig = sig; self.last_full = time.time()
        last_pk = int(self.state.get_meta("last_pk") or 0)
        conn = self.connect()
        try:
            mx = conn.execute("SELECT MAX(Z_PK) FROM ZWAMESSAGE").fetchone()[0] or 0
            if last_pk > mx:
                self.reset_watermark(last_pk, mx)
                last_pk = mx
            elif self.fresh_mark is None and last_pk < mx and self.state.inbound_empty():
                # inbound_log 是空的 = 新状态库, 或备援机还原时清过 last_pk / inbound_log / pending 的库: 水位的编号不属于这个 WhatsApp 库,
                # 对齐到库里当下最大的编号, 旧讯息一封都不补 (不然两个水位之间的几千封全送脑)
                log.info("门铃: inbound_log 是空的 (新状态库或还原过的库), 水位从 %s 对齐到当下的 %s, 旧讯息记 skip:before_install", last_pk, mx)
                self.state.set_meta("last_pk", mx)
                last_pk = mx
                self.fresh_mark = mx
                self.fresh_kind = "install"
            # 往回看 lookback 个编号, 纪录里没有的补处理 (实锤过: 陌生人新对话的第一封比同一秒的两则系统通知晚写进库,
            # 门铃先看到后面两则就把水位推过去, 那一封从此静默丢了)。已经记过 inbound_log 的跳过, 不重跑。
            lo = max(0, last_pk - self.lookback)
            rows = conn.execute("""SELECT m.Z_PK pk, m.ZISFROMME fromme, m.ZMESSAGETYPE mtype, m.ZTEXT text,
                    m.ZMESSAGEDATE d, m.ZPUSHNAME pushname, s.ZCONTACTJID jid, s.ZPARTNERNAME name, s.ZSESSIONTYPE stype,
                    g.ZMEMBERJID memberjid, m.ZFROMJID fromjid
                FROM ZWAMESSAGE m JOIN ZWACHATSESSION s ON s.Z_PK = m.ZCHATSESSION
                LEFT JOIN ZWAGROUPMEMBER g ON g.Z_PK = m.ZGROUPMEMBER
                WHERE m.Z_PK > ? ORDER BY m.Z_PK LIMIT 2000""", (lo,)).fetchall()
        finally:
            conn.close()
        seen = self.state.logged_pks(lo) if rows else set()
        rows = [r for r in rows if r["pk"] not in seen]
        if not rows:
            return 0, 0
        # 全新或还原过的状态库 (inbound_log 空), 或这个进程刚定 / 重定过水位: 往回看到的旧讯息只记 skip:before_install,
        # 不标 replied、不进 pending、不叫脑。新装或还原时, daemon 起跑之后才进来的讯息照常送闸。
        fresh, kind = self.fresh_mark, self.fresh_kind
        if fresh is None and self.state.inbound_empty():
            fresh, kind = last_pk, "install"
        passed = 0
        before = 0
        for r in rows:
            if fresh is not None and r["pk"] <= fresh and not (kind == "install" and self.arrived_after_start(r["d"])):
                jid = r["jid"] or ""
                self.state.log_inbound(r["pk"], core_to_iso(r["d"]), jid, r["name"] or jid, "group" if jid.endswith("@g.us") else "direct",
                                       r["mtype"], "skip:before_install", "")
                before += 1
                continue
            if r["pk"] <= last_pk:
                log.warning("门铃: 补到一封晚写进库的讯息 pk=%s (%s), 水位之前没看到", r["pk"], r["name"] or r["jid"])
            passed += self.consider(r)
        if before:
            log.info("门铃: 往回看的 %d 封记成 skip:before_install, 不送脑", before)
        self.fresh_mark = None
        self.fresh_kind = None
        self.state.set_meta("last_pk", max(last_pk, rows[-1]["pk"]))
        return len(rows), passed

    def consider(self, r):
        jid = r["jid"] or ""; pk = r["pk"]; msg_at = core_to_iso(r["d"])
        name = r["name"] or jid
        kind = "group" if jid.endswith("@g.us") else "direct"
        mtype = r["mtype"]
        CT = content_types()
        if r["stype"] not in (0, 1) or jid.endswith("@status") or jid.endswith("@broadcast"):
            self.state.log_inbound(pk, msg_at, jid, name, kind, mtype, "skip:status_or_broadcast", "")
            return 0
        if jid in self.ignored:
            self.state.log_inbound(pk, msg_at, jid, name, kind, mtype, "skip:ignored", "")
            return 0
        if self.too_old(r["d"]):
            # 年龄闸: WhatsApp Desktop 重新 link 或补同步时, 旧讯息会拿到新编号一口气涌进来; 旧的只记一行, 不标 replied 也不叫脑
            self.state.log_inbound(pk, msg_at, jid, name, kind, mtype, "skip:too_old", "")
            return 0
        speaker_jid = speaker_jid_of(jid, _row_get(r, "memberjid"), _row_get(r, "fromjid"))
        if mtype not in CT:
            # 系统讯息 (含主人自己改群设定那种) 先挡: 不算他回了, 也不取消 burst
            self.state.log_inbound(pk, msg_at, jid, name, kind, mtype, "skip:system", "", speaker_jid)
            return 0
        if r["fromme"]:
            self.state.mark_replied(jid, name, kind, pk, msg_at)
            dropped = self.state.pop_pending(jid)
            self.state.log_inbound(pk, msg_at, jid, name, kind, mtype, "outbound:replied" + (":burst_cancelled" if dropped else ""), "")
            if dropped:
                log.info("主人回了 %s, 取消该对话待跑的 burst (%d 封)", name, len(json.loads(dropped["items"])))
            # 语气回路: 板上有新近草稿时, 这封实发跟草稿配成一对 (⚠️ 要在 log_inbound 之后叫, wa_voice 用 inbound_log 判草稿被抢先)
            if WV is not None and (self.cfg.get("voice") or {}).get("enabled", True):
                try:
                    v = WV.on_outbound(str(state_db_path(self.cfg)), str(voice_pairs_path(self.cfg)), jid, r["text"] or "", mtype, msg_at)
                    if v:
                        log.info("语气对 %s: %s", name, v)
                except Exception as e:
                    log.warning("语气对失败 %s (继续): %s", name, e)
            return 0
        policy = self.policy_for(jid, kind)
        text = r["text"] or ""
        if not self.enabled:
            ok, why = False, "skip:doorbell_off"          # doorbell.enabled=false: 照记照标 replied, 但不自动叫脑 (命令档与板子照常)
        elif policy == "all":
            ok, why = True, "pass:all"
        elif policy == "mention":
            ok = self.mentions_me(text); why = "pass:mention" if ok else "skip:no_mention"
        else:
            ok, why = False, "skip:policy"
        self.state.log_inbound(pk, msg_at, jid, name, kind, mtype, why, policy, speaker_jid)
        if not ok:
            return 0
        item = {"pk": pk, "at": msg_at, "type": CT[mtype], "text": text[:600],
                "speaker": speaker_name(speaker_jid, r["pushname"] or "") if kind == "group" else _clean_pushname(r["pushname"]),
                "speaker_jid": speaker_jid}
        self.state.add_pending(jid, name, kind, item)
        log.info("过闸 %s [%s] %s", kind, CT[mtype], name)
        return 1

    def burst_for_test(self, jid, n=3):
        CT = content_types()
        conn = self.connect()
        try:
            rows = conn.execute("""SELECT m.Z_PK pk, m.ZMESSAGETYPE mtype, m.ZTEXT text, m.ZMESSAGEDATE d, m.ZPUSHNAME pushname,
                    s.ZCONTACTJID jid, s.ZPARTNERNAME name, g.ZMEMBERJID memberjid, m.ZFROMJID fromjid
                FROM ZWAMESSAGE m JOIN ZWACHATSESSION s ON s.Z_PK=m.ZCHATSESSION
                LEFT JOIN ZWAGROUPMEMBER g ON g.Z_PK = m.ZGROUPMEMBER
                WHERE s.ZCONTACTJID=? AND m.ZISFROMME=0 AND m.ZMESSAGETYPE IN (%s) ORDER BY m.Z_PK DESC LIMIT ?"""
                                % ",".join(str(int(t)) for t in CT), (jid, n)).fetchall()
        finally:
            conn.close()
        if not rows:
            return None
        rows = list(reversed(rows))
        name = rows[0]["name"] or jid
        kind = "group" if jid.endswith("@g.us") else "direct"
        items = []
        for r in rows:
            sj = speaker_jid_of(jid, r["memberjid"], r["fromjid"])
            items.append({"pk": r["pk"], "at": core_to_iso(r["d"]), "type": CT[r["mtype"]], "text": (r["text"] or "")[:600],
                          "speaker": speaker_name(sj, r["pushname"] or "") if sj else _clean_pushname(r["pushname"]), "speaker_jid": sj})
        return {"jid": jid, "name": name, "kind": kind, "first_at": time.time(), "last_at": time.time(),
                "items": json.dumps(items, ensure_ascii=False)}


# ---------------------------------------------------------------- 脑

class RunError(Exception):
    pass


def extract_json(text):
    """扫第一个成对大括号 (跳过字串里的假括号); ⛔ 别信它会吐裸 JSON。全部解不开时修一次字串里没转义的双引号再扫。"""
    if not text:
        return None
    i = text.find("{")
    while i != -1:
        depth = 0; in_str = False; esc = False
        for j in range(i, len(text)):
            c = text[j]
            if in_str:
                if esc: esc = False
                elif c == "\\": esc = True
                elif c == '"': in_str = False
                continue
            if c == '"': in_str = True
            elif c == "{": depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[i:j + 1])
                    except Exception:
                        break
        i = text.find("{", i + 1)
    if WC is not None and hasattr(WC, "repair_inner_quotes"):
        fixed = WC.repair_inner_quotes(text)
        if fixed != text:
            return extract_json(fixed)
    return None


def normalize(j, prev=None, cfg=None):
    """脑的 JSON → 落库的判断。规矩在这里硬套, 词表来自 config: world 黏住 (prev 有就不听脑的), stage 只准往前
    (脑往回就保留旧值并在 reason 注明), parked 只从 park_from 进, 从 parked 回来只能到顺序里第二格; 非生意世界没有 stage。"""
    if not isinstance(j, dict):
        raise RunError("脑没有吐出 JSON 物件")
    V = Vocab(cfg)
    prev = prev or {}
    notes = []
    world = str(j.get("world", "")).strip()
    if world not in V.world_keys:
        notes.append("词表外 world=%r" % world); world = V.fallback_world
    if prev.get("world") in V.world_keys and prev["world"] != world:
        notes.append("world 黏住 %s (脑说 %s)" % (prev["world"], world)); world = prev["world"]
    stage = str(j.get("stage") or "").strip() or None
    if stage is not None and stage not in V.stage_keys:
        notes.append("词表外 stage=%r" % stage); stage = None
    if not V.is_business(world):
        stage = None
    else:
        ps = prev.get("stage") if prev.get("stage") in V.stage_keys else None
        if ps and stage:
            if ps == V.parked:
                if stage != V.return_from_park:
                    stage = ps if stage == V.parked else V.return_from_park
            elif stage == V.parked:
                if ps not in V.park_from:
                    notes.append("stage 不准从 %s 进 %s" % (ps, V.parked)); stage = ps
            elif V.stage_index(stage) < V.stage_index(ps):
                notes.append("stage 保留 %s (脑想退到 %s, 退回只有主人能按)" % (ps, stage)); stage = ps
        elif ps and not stage:
            stage = ps
    priority = str(j.get("priority", "")).strip()
    if priority not in V.priority_keys:
        notes.append("词表外 priority=%r" % priority); priority = V.lowest_priority
    out = {
        "world": world, "stage": stage, "priority": priority,
        "needs_me": bool(j.get("needs_me")),
        "draft": str(j.get("draft") or "").strip(),
        "reason": str(j.get("reason") or "").strip()[:200],
        "tag": "open",
    }
    try:
        out["confidence"] = max(0.0, min(1.0, float(j.get("confidence", 0))))
    except Exception:
        out["confidence"] = 0.0
    out["situation"] = str(j.get("situation") or "").strip()[:800]
    out["stake"] = str(j.get("stake") or "").strip()[:500]        # 对主人的利害 (板上一张小卡)
    out["summary"] = str(j.get("summary") or "").strip()[:1500]   # 滚动摘要 = 脑的记忆 (增量对话包靠它记更早的事)
    refs = []
    for r in (j.get("refs") or [])[:3] if isinstance(j.get("refs"), list) else []:
        if isinstance(r, dict) and (r.get("what") or r.get("why")):
            refs.append({"what": str(r.get("what") or "").strip()[:160], "where": str(r.get("where") or "").strip()[:200], "why": str(r.get("why") or "").strip()[:200]})
    out["refs"] = refs
    tl = j.get("timeline") or []
    # 最旧在前; 超过 8 条留最新的 8 条 (丢的是最旧的, 提示也这样要求)
    out["timeline"] = [str(x).strip()[:160] for x in tl if str(x).strip()][-8:] if isinstance(tl, list) else []
    try:
        out["calendar_hint"] = WCAL.validate_hint(j.get("calendar_hint")) if (WCAL is not None and j.get("calendar_hint")) else None
    except Exception as e:
        notes.append("calendar_hint 解析失败: %s" % str(e)[:60]); out["calendar_hint"] = None
    try:
        out["captures"] = list(WI.validate_captures(j.get("captures")) or []) if WI is not None else []
    except Exception as e:
        notes.append("captures 解析失败: %s" % str(e)[:60]); out["captures"] = []
    if notes:
        out["reason"] = "[" + "; ".join(notes) + "] " + out["reason"]
    return out


def reader_config_from(cfg):
    """读取器 config 的退路形状 (正本由 wa_setup.py sync-reader-config 从 live config 生成; 只有 <home>/config.json 不在时 daemon 才照这里写一份)。"""
    b = cfg.get("brain") or {}

    def binpath(name):
        if WC is not None and hasattr(WC, "resolve_bin"):
            try:
                return str(WC.resolve_bin(cfg, name) or "")
            except Exception:
                return ""
        return ""
    pd = people_dir(cfg)
    return {"days": 3, "ignored_jids": list(cfg.get("ignored_jids") or []), "my_phone": str(cfg.get("my_phone") or ""),
            "my_names": list(cfg.get("my_names") or []), "transcribe": bool(b.get("transcribe", True)),
            "whisper_model": str(b.get("whisper_model") or ""), "whisper_language": str(b.get("whisper_language") or ""),
            "whisperkit_bin": binpath("whisperkit"), "ffmpeg_bin": binpath("ffmpeg"),
            "person_dirs": [str(pd)] if pd is not None else [], "index_dirs": [], "stop_words": []}


_PLACEHOLDER_RE = re.compile(r"\{([A-Z][A-Z0-9_]{1,40})\}")


class Brain(object):
    def __init__(self, cfg, state, notifier, dry_run=False):
        self.cfg = cfg; self.state = state; self.notifier = notifier; self.dry_run = dry_run
        self.live = live_dir(cfg)
        self.runs_dir = self.live / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(str(self.runs_dir), 0o700)                  # run 夹里是完整对话包
        except Exception:
            pass
        self.settings = self.live / "claude-settings.json"
        self.owner_prompt = self.live / "prompts" / "brain-prompt.md"
        self.seed_prompt = TEMPLATES / "brain-prompt.md"
        self.py = sys.executable
        self.home = home_dir(cfg)
        self.reader_dir = find_scripts_dir(cfg, "reader_dir", "-whatsapp-read", "wa_sweep.py")
        self.deny_tools = list(DENY_TOOLS)
        self.dossier = None
        self.gate = None                                          # Daemon 接上 Doorbell.gate_for (摘要表按闸过滤)
        self.prewarm_busy = False
        self._placeholder_warned = set()
        self.check_settings()
        cwd = str((cfg.get("brain") or {}).get("cwd") or "")
        for bad in ("Documents", "Desktop", "Downloads"):
            if cwd and str(HOME / bad) in cwd:
                log.warning("brain.cwd 在 ~/%s 底下 (%s): launchd 底下的 claude 开 TCC 保护的夹会卡死, 换到家目录下的一个空夹", bad, cwd)

    def check_settings(self):
        """claude-settings.json 由 wa_setup 渲染: 不在、还有没渲染的占位、工具 deny 跟 DENY_TOOLS 不一致, 都在 log 喊。两份工具 deny 取并集。"""
        if not self.settings.is_file():
            log.error("claude-settings.json 不在 (%s): 先跑 wa_setup.py init 渲染, 不然每个 run 都会失败", self.settings)
            return
        try:
            text = self.settings.read_text(encoding="utf-8")
            j = json.loads(text)
        except Exception as e:
            log.error("claude-settings.json 读不了 (%s): %s", self.settings, e)
            return
        left = sorted(set(_PLACEHOLDER_RE.findall(text)))
        if left:
            log.error("claude-settings.json 还有没渲染的占位 %s: 重跑 wa_setup.py init", left)
        deny = ((j.get("permissions") or {}).get("deny") or []) if isinstance(j, dict) else []
        names = [d for d in deny if isinstance(d, str) and "(" not in d]
        extra = [d for d in names if d not in self.deny_tools]
        missing = [t for t in DENY_TOOLS if t not in names]
        if extra:
            self.deny_tools += extra
            log.info("claude-settings.json 多挡了 %s, 命令列一起挡", extra)
        if missing:
            log.warning("claude-settings.json 的 deny 少了 %s (命令列照挡; 下次 wa_setup.py init 补齐)", missing)
        if not any(isinstance(d, str) and d.startswith("Read(") for d in deny):
            log.warning("claude-settings.json 没有任何 Read(...) 路径 deny: 脑读得到推播钥匙与设定档, 重跑 wa_setup.py init")

    def claude_bin(self):
        if WC is not None and hasattr(WC, "resolve_bin"):
            try:
                b = WC.resolve_bin(self.cfg, "claude")
                if b:
                    return str(b)
            except Exception as e:
                log.warning("resolve_bin claude: %s", e)
        return shutil.which("claude") or "claude"

    def child_env(self):
        env = None
        if WC is not None and hasattr(WC, "subprocess_env"):
            try:
                env = dict(WC.subprocess_env(self.cfg))
            except Exception as e:
                log.warning("subprocess_env: %s", e)
        env = env or dict(os.environ)
        env.setdefault("HOME", str(HOME)); env.setdefault("LANG", "en_US.UTF-8")
        return env

    def claude_base_args(self, model, max_turns):
        """脑、档案 run、probe 共用的一份笼子旗标 (改一处三处跟)。"""
        return [self.claude_bin(), "-p", "--output-format", "stream-json", "--verbose",
                "--model", model, "--settings", str(self.settings), "--strict-mcp-config",
                "--no-session-persistence", "--permission-mode", "default",
                "--allowedTools", " ".join(ALLOW_TOOLS), "--disallowedTools", " ".join(self.deny_tools),
                # 不给 add-dir, cwd 也不放 Documents: launchd 底下开 TCC 路径会卡死。脑用 Read 开对话包里列的图片路径是通的 (node 当家长时实证)。
                "--max-turns", str(int(max_turns))]

    @staticmethod
    def parse_stream(stdout):
        """stream-json → (result 事件或 None, 载入了不该有的工具清单)。
        设定档的 deny 规则挡下的读取不会进 result 的 permission_denials (那个清单永远是空的), 只看得到工具回的错:
        这里把「denied by your permission settings」那几次 (工具名 + 路径) 补进 done["permission_denials"], runs.denials 才记得到,
        call_claude 也会记一行 WARNING (对话里有人想叫脑去读秘密档, 就是这样被挡下来的)。"""
        done = None; breach = []; uses = {}; denied = []
        for line in (stdout or "").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                j = json.loads(line)
            except Exception:
                continue
            if j.get("type") == "system" and j.get("subtype") == "init" and isinstance(j.get("tools"), list):
                breach = [t for t in j["tools"] if t not in EXPECTED_TOOLS]
            content = (j.get("message") or {}).get("content") if isinstance(j.get("message"), dict) else None
            for c in content if isinstance(content, list) else []:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_use":
                    inp = c.get("input") if isinstance(c.get("input"), dict) else {}
                    uses[c.get("id")] = "%s %s" % (c.get("name"), inp.get("file_path") or inp.get("path") or inp.get("pattern") or "")
                elif c.get("type") == "tool_result" and c.get("is_error") and "denied by your permission settings" in str(c.get("content")):
                    denied.append(uses.get(c.get("tool_use_id"), "denied read").strip())
            if j.get("type") == "result":
                done = j
        if done is not None and denied:
            done["permission_denials"] = list(done.get("permission_denials") or []) + denied
        return done, breach

    def run_script(self, args, timeout, label):
        env = self.child_env()
        # whisper model 的副本住 ~/.cache (不在 Documents), 否则 whisperkit-cli 在 launchd 底下开 model 会卡在 TCC 的 open()
        wm = _cfg_path((self.cfg.get("brain") or {}).get("whisper_model"))
        if wm is not None and wm.exists():
            env["WA_WHISPER_MODEL"] = str(wm)
        try:
            r = subprocess.run([self.py] + args, capture_output=True, text=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            raise RunError("%s 超时 %ds (语音多的对话第一次要转很久; 先丢 commands/prewarm-%s.json 把快取喂饱)" % (label, timeout, (self.cfg.get("brain") or {}).get("context_days", 7))) from None
        if r.returncode != 0:
            raise RunError("%s exit %d: %s" % (label, r.returncode, (r.stderr or r.stdout)[-400:].strip()))
        return r

    def _sync_config(self, work):
        """把读取器根 config (<home>/config.json) 刷进一个工作夹; 工作夹自己的 last_sweep_* 留着。回 1 = 真的改了。"""
        root = self.home / "config.json"
        dst = Path(work) / "config.json"
        try:
            src = json.loads(root.read_text(encoding="utf-8"))
        except Exception:
            return 0
        try:
            old = json.loads(dst.read_text(encoding="utf-8")) if dst.is_file() else {}
        except Exception:
            old = {}
        merged = {k: v for k, v in (old or {}).items() if str(k).startswith("last_sweep")}
        merged.update(src)
        if merged == old:
            return 0
        try:
            dst.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
            os.chmod(str(dst), 0o600)
            return 1
        except Exception as e:
            log.warning("读取器 config 刷不进 %s: %s", work, e)
            return 0

    def refresh_reader_configs(self):
        """daemon 起跑时刷新读取器 config (以前只在建夹时复制一次, 改了根设定旧夹不跟)。
        规矩只住 wa_setup.do_sync_reader_config (重生 <home>/config.json, 刷每个 runs-work/<key>/ 与 prewarm/ 的副本);
        wa_setup 载入不了或回错才退回这里: 根 config 不在就照 live config 写一份并喊一声, 再把副本对齐。"""
        try:
            import wa_setup as WSU
            rep = WSU.Report(quiet=True)
            WSU.do_sync_reader_config(self.cfg, self.cfg.get("config_path") or str(live_dir(self.cfg) / "config.json"), rep)
            for w in rep.warnings:
                log.warning("读取器 config: %s", w)
            for e in rep.errors:
                log.error("读取器 config: %s", e)
            if rep.done:
                log.info("读取器 config: wa_setup 刷新了 %d 个档", len(rep.done))
            if not rep.errors:
                return
        except Exception as e:
            log.warning("wa_setup.do_sync_reader_config 用不了 (%s), 退回 daemon 自己刷", e)
        root = self.home / "config.json"
        if not root.is_file():
            try:
                self.home.mkdir(parents=True, exist_ok=True)
                root.write_text(json.dumps(reader_config_from(self.cfg), ensure_ascii=False, indent=1), encoding="utf-8")
                os.chmod(str(root), 0o600)
                log.warning("读取器 config 不在 (%s), daemon 先照 live config 写一份; 正本由 wa_setup.py sync-reader-config 生成", root)
            except Exception as e:
                log.error("读取器 config 写不了 (%s): %s", root, e)
        n = 0
        dirs = sorted(p for p in (self.home / "runs-work").glob("*") if p.is_dir()) if (self.home / "runs-work").is_dir() else []
        for d in dirs + ([self.home / "prewarm"] if (self.home / "prewarm").is_dir() else []):
            n += self._sync_config(d)
        if n:
            log.info("读取器 config: 刷新了 %d 个工作夹", n)

    def sweep_workdir(self, key):
        """每个 run 自己的 work_dir (快照档不互踩, 所以不用锁), transcripts-cache 用捷径共用, config 每次跟根设定对齐。"""
        base = self.home
        work = base / "runs-work" / safe_name(key)
        work.mkdir(parents=True, exist_ok=True)
        (base / "transcripts-cache").mkdir(parents=True, exist_ok=True)
        link = work / "transcripts-cache"
        if not link.is_symlink():
            if link.exists():
                link.rename(work / ("transcripts-cache.old-%d" % int(time.time())))
            link.symlink_to(base / "transcripts-cache")
        self._sync_config(work)
        return work

    def sweep(self, args, timeout, label="wa_sweep", key="shared"):
        work = self.sweep_workdir(key)
        extra = [] if (self.cfg.get("brain") or {}).get("transcribe", True) else ["--no-transcribe"]
        self.run_script([str(self.reader_dir / "wa_sweep.py"), "--work-dir", str(work)] + args + extra, timeout, label)
        return work

    def context(self, sweep_json, run_dir, work=None):
        """第二大脑候选: wa_context.py 拿对话包里的名字、电话、群名去 vault 找档, 出 context.md。"""
        args = [str(self.reader_dir / "wa_context.py"), "--sweep", str(sweep_json), "--out", str(Path(run_dir) / "context.json")]
        v = vault_dir(self.cfg)
        if v is not None:
            args += ["--vault", str(v)]
        if work is not None and (Path(work) / "config.json").is_file():
            args += ["--config", str(Path(work) / "config.json")]
        self.run_script(args, 600, "wa_context")
        return (Path(run_dir) / "context.md").read_text(encoding="utf-8")

    def prewarm(self, days):
        """把 N 天窗内所有对话的语音转进 transcripts-cache, 之后每个 run 只转新的。
        跑很久, 用自己的 work_dir (快照档不跟 live run 互踩), 只把 transcripts-cache 用捷径共用; 同时只跑一个。"""
        if self.prewarm_busy:
            log.warning("prewarm 已经在跑, 这次指令略过 (两个同时跑会共用 prewarm/ 互删快照)")
            return
        self.prewarm_busy = True
        try:
            base = self.home
            work = base / "prewarm"; work.mkdir(parents=True, exist_ok=True)
            cache_link = work / "transcripts-cache"
            (base / "transcripts-cache").mkdir(parents=True, exist_ok=True)
            if not cache_link.is_symlink():
                if cache_link.exists():
                    cache_link.rename(work / ("transcripts-cache.old-%d" % int(time.time())))
                cache_link.symlink_to(base / "transcripts-cache")
            self._sync_config(work)
            out = self.runs_dir / ("prewarm-%s-%dd.json" % (dt.datetime.now().strftime("%Y%m%d-%H%M%S"), days))
            log.info("prewarm 开始: %d 天全窗 (语音全转), 独立 work_dir %s, 不挡 live run", days, work)
            t0 = time.time()
            try:
                r = self.run_script([str(self.reader_dir / "wa_sweep.py"), "--work-dir", str(work), "--days", str(days), "--out", str(out)],
                                    7200, "prewarm")
                log.info("prewarm 完成 %.0fs: %s", time.time() - t0, (r.stdout or "").strip()[-300:])
            except Exception as e:
                log.warning("prewarm 失败 %.0fs: %s", time.time() - t0, e)
        finally:
            self.prewarm_busy = False

    def pack_window(self, kind, prev, ground=False):
        """对话包要拉几天: 有记忆 (上次判断带 summary) 就只拉「上次判断之后 + 最近 warm_days 天」;
        没记忆 / 板上按全量重判 / 距上次接地超过 ground_every_days 天 → 全量 (context_days, 群是 group_context_days) 并标接地。
        回传 (days, mode, ground)。"""
        b = self.cfg.get("brain") or {}
        full = int(b.get("group_context_days", 3)) if kind == "group" else int(b.get("context_days", 7))
        warm = bool(prev and (prev.get("summary") or "").strip())
        forced = bool(ground)
        if warm and not ground:
            try:
                last = dt.datetime.fromisoformat(prev.get("grounded_at") or prev.get("updated_at"))
                if (dt.datetime.now() - last).total_seconds() > float(b.get("ground_every_days", 7)) * 86400:
                    ground = True
            except Exception:
                ground = True
        if warm and not ground:
            try:
                since = dt.datetime.fromisoformat(prev["updated_at"])
                gap_days = int((dt.datetime.now() - since).total_seconds() // 86400) + 1
            except Exception:
                gap_days = full
            days = max(int(b.get("warm_days", 2)), gap_days)
            days = min(days, full)
            return days, "增量 (上次判断之后的新讯息 + 最近 %d 天原文; 更早的事看 summary)" % days, False
        why = ", 板上要求全量重判" if forced else (", 距上次接地超过 %s 天, 例行重写记忆" % b.get("ground_every_days", 7) if warm else ", 这条对话还没有记忆")
        return full, "全量接地 (最近 %d 天全文%s)" % (full, why), True

    def topic_candidates(self, text):
        """主题词 → vault 档 (主人副本 live/topic-map.json, 每个 run 现读; 不在就退回种子并喊): 回传 [(相对路径, 第一行标题)], 只列存在的档, 最多 6 份。"""
        path, _ = owner_or_seed(self.live / "topic-map.json", TEMPLATES / "topic-map.example.json", "topic-map", self.state)
        vault = vault_dir(self.cfg)
        if path is None or vault is None:
            return []
        try:
            tm = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("topic-map 读不了 (%s): %s", path, e)
            return []
        low = (text or "").lower()
        out = []; seen = set()
        for rule in tm.get("rules", []) if isinstance(tm, dict) else []:
            if any(str(k).lower() in low for k in rule.get("keys", []) if str(k).strip()):
                for rel in rule.get("files", []):
                    if rel in seen:
                        continue
                    p = vault / rel
                    if p.is_file():
                        title = ""
                        try:
                            for line in p.read_text(encoding="utf-8").splitlines():
                                if line.startswith("# "):
                                    title = line[2:].strip(); break
                        except Exception:
                            pass
                        out.append((rel, title)); seen.add(rel)
                    if len(out) >= 6:
                        return out
        return out

    def prompt_values(self):
        cfg = self.cfg
        V = Vocab(cfg)
        b = cfg.get("brain") or {}
        sr = cfg.get("stage_rules") or {}
        owner = owner_label(cfg)
        parked_days = int(sr.get("parked_after_days", 21))
        return {
            "OWNER_LABEL": owner,
            "WORLDS_BLOCK": render_worlds_block(V, owner),
            "STAGES_BLOCK": render_stages_block(V, owner, parked_days),
            "PRIORITIES_BLOCK": render_priorities_block(V, owner),
            "BUSINESS_CONTEXT": V.business_context or "(还没写生意说明: 生意世界只凭对话内容判, 把握度要老实降低)",
            "WORLD_KEYS": " · ".join("`%s`" % k for k in V.world_keys),
            "STAGE_KEYS": " → ".join("`%s`" % k for k in V.ladder) + ((", 另有 `%s`" % V.parked) if V.parked else ""),
            "PRIORITY_KEYS": " · ".join("`%s`" % k for k in V.priority_keys),
            "CONTEXT_DAYS": str(int(b.get("context_days", 7))),
            "GROUP_CONTEXT_DAYS": str(int(b.get("group_context_days", 3))),
            "WARM_DAYS": str(int(b.get("warm_days", 2))),
            "PARKED_AFTER_DAYS": str(parked_days) if int(parked_days or 0) > 0 else "(自动搁置关着, 不会)",
            "FACTS_SOURCE_LABEL": facts_source_label(cfg),
        }

    def fill_placeholders(self, text, values, where):
        for k, v in values.items():
            text = text.replace("{%s}" % k, v)
        unknown = sorted(set(_PLACEHOLDER_RE.findall(text)) - set(values))
        for name in unknown:
            if (where, name) not in self._placeholder_warned:
                self._placeholder_warned.add((where, name))
                log.warning("%s 有认不得的占位 {%s}, 原样留着 (这个警告每个进程只印一次)", where, name)
        return text

    def prompt_head(self):
        """提示前半: 主人副本 live/prompts/brain-prompt.md 优先, 不在就退回 payload 种子并喊。
        「主人副本, 否则种子加 WARNING」这条规矩只住 wa_backfill.prompt_template (第二讯息来源也用它); 模组没载入才退回这里的同一条。
        去掉开头给维护者看的注解与种子戳记, 填占位。退回种子时另记 meta seed_fallback:brain-prompt 给日报。"""
        text = None
        if WBF is not None and hasattr(WBF, "prompt_template"):
            try:
                got = call_opt(WBF.prompt_template, self.cfg, "brain-prompt.md", log=lambda m: log.warning("%s", m), who="brain")
                text, path, used_seed = got[0], got[1], bool(got[2])
                if used_seed:
                    self.state.set_meta("seed_fallback:brain-prompt", "%s %s" % (now_iso(), path))
                elif self.state.get_meta("seed_fallback:brain-prompt"):
                    self.state.del_meta("seed_fallback:brain-prompt")
            except Exception as e:
                log.warning("wa_backfill.prompt_template 读不到 brain-prompt.md (%s), 退回 daemon 自己找", e)
                text = None
        if text is None:
            path, _ = owner_or_seed(self.owner_prompt, self.seed_prompt, "brain-prompt", self.state)
            if path is None:
                raise RunError("brain-prompt.md 读不到 (主人副本 %s 与种子 %s 都不在)" % (self.owner_prompt, self.seed_prompt))
            text = path.read_text(encoding="utf-8")
        text = re.sub(r"\A(\s*<!--.*?-->)+\s*", "", text, flags=re.S)
        return self.fill_placeholders(text, self.prompt_values(), "brain-prompt")

    def build_prompt(self, p, items, pack_md, ctx_md, prev, pack_note=None):
        cfg = self.cfg
        owner = owner_label(cfg)
        head = self.prompt_head()
        today = dt.datetime.now()
        off = time.strftime("%z") or "+0000"
        tz = "%s:%s" % (off[:3], off[3:]) if len(off) == 5 else off
        lines = ["", "---", "", "# 输入", "",
                 "今天: %s (星期%s), 时区 %s (burst 里的「明天」「下礼拜二」照那句话发出的日期推, 这一行给你对星期几)" % (today.date().isoformat(), "一二三四五六日"[today.weekday()], tz),
                 "", "## 1 · 这个 burst (新进来的, 要判断的) · 对话: %s (%s)" % (p["name"], p["kind"]), ""]
        for it in items:
            who = (" " + it["speaker"]) if it.get("speaker") else ""
            lines.append("- [%s]%s (%s) %s" % (it["at"], who, it["type"], it["text"] or "(无文字, 看对话包里同一时间的媒体路径)"))
        lines += ["", "## 2 · 这条对话上一次的判断 (含 summary = 你的记忆, 对话包没涵盖的日子靠它)", ""]
        # 只有回填摘要、还没被判过的行 (没 world 没 tag) 也要把 summary 给脑, 否则按「有记忆」只拉两天, 记忆却没进 prompt
        if prev and (prev.get("world") or prev.get("tag") or (prev.get("summary") or "").strip()):
            keep = {k: prev.get(k) for k in ("world", "stage", "stage_auto", "priority", "needs_me", "draft", "reason", "situation", "stake", "summary", "updated_at", "last_out_at", "grounded_at")}
            if prev.get("draft_owner_at"):
                keep["draft_note"] = "草稿是主人在板上改过的 (%s 的字, %s)" % (owner, str(prev.get("draft_owner_at"))[:16])
            if not prev.get("world"):
                keep["note"] = ("旧版判断, world / stage 还没有, 这次由你第一次判" if prev.get("tag")
                                else "这条对话只有回填的摘要 (一次写的阅读包摘要), 还没判过 world / stage, 这次由你第一次判")
            try:
                keep["timeline"] = json.loads(prev.get("timeline") or "[]")
            except Exception:
                keep["timeline"] = []
            lines.append("```json\n%s\n```" % json.dumps(keep, ensure_ascii=False, indent=1))
        else:
            lines.append("(无, 这是这条对话第一次跑)")
        cap = 40000 if p.get("kind") == "group" else 70000
        if len(pack_md) > cap:
            pack_md = "(对话包太长, 只给最后 %d 字)\n\n" % cap + pack_md[-cap:]
        lines += ["", "## 3 · 对话包 (wa_sweep) · %s" % (pack_note or "最近 %s 天" % (cfg.get("brain") or {}).get("context_days", 7)), "", pack_md]
        if len(ctx_md) > 30000:
            ctx_md = ctx_md[:30000] + "\n(第二大脑候选太长, 截断)"
        lines += ["", "## 4 · 第二大脑候选 (wa_context)", "", ctx_md or "(第二大脑查找没有产出)"]
        # 5 · 别的对话摘要表 (可借鉴的第一来源): 整张给, 不给原文; 自己这条标出来
        sm = ""
        try:
            sm = (self.live / "summaries.md").read_text(encoding="utf-8")
        except Exception:
            pass
        if sm:
            if len(sm) > 20000:
                sm = sm[:20000] + "\n(摘要表太长, 截断)"
            sm = sm.replace("- **%s**" % p["name"], "- **%s** (就是这条)" % p["name"], 1)
        lines += ["", "## 5 · 别的对话的滚动摘要表 (可借鉴的第一来源; ⛔ 别人对话的内容只准影响判断, 不准写进草稿)", "", sm or "(还没有别的对话有摘要)"]
        # 6 · 主题词拉的 vault 档候选 (可借鉴的第二来源)
        scan = " ".join((it.get("text") or "") for it in items) + " " + str((prev or {}).get("situation") or "") + " " + str((prev or {}).get("summary") or "")
        cands = self.topic_candidates(scan)
        vault = vault_dir(cfg)
        lines += ["", "## 6 · 主题相关的 vault 档 (可借鉴的第二来源; 候选, 自己决定开不开, 最多用 Read 开 2 份; 路径相对 %s/)" % (vault or "vault"), ""]
        lines += ["- `%s` %s" % (rel, ("· " + t) if t else "") for rel, t in cands] or ["(burst 里没有对得上主题词的东西)"]
        # 7 · 系统记录: 事实来源里这个人的事实 ≤3 行; 群没有; 只当事实用
        f7 = []
        if p.get("kind") == "direct" and WF is not None:
            facts = self.dossier.load_facts() if self.dossier is not None else None
            if facts is not None:
                try:
                    f7 = list(WF.facts_lines(facts, p["jid"]) or [])[:3]
                except Exception as e:
                    log.warning("facts_lines 失败 (继续): %s", e); f7 = []
        lines += ["", "## 7 · 系统记录 (%s, 事实 worker 每晚拉; 只准当事实用, 判 stage 与写草稿的日期 / 金额以它为准)" % facts_source_label(cfg), ""]
        lines += (["- " + x for x in f7] if f7 else ["系统记录: 无"])
        # 8 · 语气范本: 同 world 最近几对「草稿 vs 主人实发」; ⛔ 世界之间不混, 范本里的事实不准搬进草稿
        v8 = "(还没有范本)"
        vo = cfg.get("voice") or {}
        if WV is not None and vo.get("enabled", True):
            w = (prev or {}).get("world")
            if w:
                try:
                    v8 = WV.render_examples(WV.examples_for(str(voice_pairs_path(cfg)), w, n=int(vo.get("examples", 12)))) or v8
                except Exception as e:
                    log.warning("语气范本读不了 (继续): %s", e)
        lines += ["", "## 8 · 语气范本 (同一个世界里, 你上次的草稿 vs %s 实际发出去的; 学他的长短、语域、拆几封, ⛔ 不准搬里面的事实)" % owner, "", v8]
        return head.rstrip("\n") + "\n" + "\n".join(lines) + "\n"

    def call_claude(self, prompt, run_dir, attempt=0, timeout=None, model=None, max_turns=None, append_system=None):
        if self.dry_run:
            raise RunError("dry-run: 不叫 claude")
        if not self.settings.is_file():
            raise RunError("claude-settings.json 不在 (%s): 先跑 wa_setup.py init" % self.settings)
        b = self.cfg.get("brain") or {}
        timeout = int(timeout or b.get("run_timeout_seconds", 600))
        args = self.claude_base_args(model or b.get("model", "claude-sonnet-5"), max_turns or b.get("max_turns", 12))
        args += ["--append-system-prompt", append_system or APPEND_SYSTEM]
        # prompt 走 argv 不走 stdin (实证能跑); stdin 关死, 免得 claude 等输入。
        args.append(prompt)
        env = self.child_env()
        (Path(run_dir) / "claude.cmd.txt").write_text(" ".join(shlex.quote(a) for a in args[:-1]) + " <prompt.md>\n", encoding="utf-8")
        t0 = time.time()
        sfx = "" if attempt == 0 else ".attempt%d" % (attempt + 1)

        def dump(out, err):
            (Path(run_dir) / ("claude%s.stdout.jsonl" % sfx)).write_text(out or "", encoding="utf-8")
            if err:
                (Path(run_dir) / ("claude%s.stderr.txt" % sfx)).write_text(err, encoding="utf-8")

        try:
            r = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                               timeout=timeout, cwd=b.get("cwd") or str(self.home / "brain-cwd"), env=env)
        except subprocess.TimeoutExpired as te:
            out = te.stdout.decode("utf-8", "replace") if isinstance(te.stdout, bytes) else (te.stdout or "")
            err = te.stderr.decode("utf-8", "replace") if isinstance(te.stderr, bytes) else (te.stderr or "")
            dump(out, err)
            # from None: TimeoutExpired 的讯息带整个 argv, 最后一格是提示 (别人的对话原文), 不准跟着例外链进 wa_live.log
            raise RunError("claude -p 超时 %ds (半途输出 %d 字, 看 claude.stdout.jsonl)" % (timeout, len(out))) from None
        dump(r.stdout, r.stderr)
        done, breach = self.parse_stream(r.stdout)
        if breach:
            log.warning("CAGE BREACH: 载入了不该有的工具: %s", ", ".join(breach))
        if done is None:
            tail = (r.stderr or r.stdout or "")[-400:].strip()
            raise RunError("claude -p 没有回 result (rc=%s): %s" % (r.returncode, tail))
        denials = [d.get("tool_name", d) if isinstance(d, dict) else d for d in (done.get("permission_denials") or [])]
        if denials:
            log.warning("设定档挡下了脑的读取 (%s): %s", Path(run_dir).name, "; ".join(str(x) for x in denials)[:400])
        u = done.get("usage") or {}
        text, is_err = done.get("result") or "", bool(done.get("is_error"))
        reason = ""
        if is_err:
            # error_max_turns 这类失败 result 是空的, 原因只在 subtype 与 errors: 一定要带出来, 不然 log 与 runs.error 都是空白
            errs = "; ".join(str(x) for x in (done.get("errors") or []) if str(x).strip())
            reason = (text.strip()[:300] or ": ".join(x for x in (str(done.get("subtype") or "").strip(), errs) if x)[:300]
                      or "is_error with no text and no reason")
        return {"text": text, "is_error": is_err, "error": reason, "subtype": done.get("subtype"),
                "cost_usd": done.get("total_cost_usd"), "duration_ms": done.get("duration_ms") or int((time.time() - t0) * 1000),
                "num_turns": done.get("num_turns"), "breach": breach, "denials": denials,
                "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
                "cache_read_tokens": u.get("cache_read_input_tokens"), "cache_create_tokens": u.get("cache_creation_input_tokens")}

    def call_claude_recorded(self, prompt, run_dir, run_kind, jid=None, name=None, timeout=None, max_turns=None):
        """非分诊的模型呼叫 (第二讯息来源 · 摘要回填) 也进 runs 表 (kind 记是哪一种), 用量读数才完整; 破笼一样推。
        max_turns: 这种 run 自己的回合上限 (摘要回填用 backfill.max_turns), 没给就用 brain.max_turns。"""
        model = (self.cfg.get("brain") or {}).get("model", "claude-sonnet-5")
        run_id = self.state.start_run(jid, name or run_kind, run_kind, [], model, run_dir)
        res = None
        try:
            res = self.call_claude(prompt, run_dir, timeout=timeout, max_turns=max_turns)
            usage = {k: res.get(k) for k in ("cost_usd", "duration_ms", "num_turns", "input_tokens", "output_tokens", "cache_read_tokens", "cache_create_tokens")}
            self.state.finish_run(run_id, not res.get("is_error"), cage_breach=res.get("breach") or [], denials=res.get("denials") or [],
                                  error=((res.get("error") or str(res.get("text") or "")[:300] or "is_error") if res.get("is_error") else None), **usage)
            if res.get("breach"):
                self.notifier.send("🚨 [WA live] %s run #%d cage breach: 载入了不该有的工具 %s" % (run_kind, run_id, ", ".join(res["breach"])),
                                   key="breach", min_gap=1800, kind="error")
            return res
        except Exception as e:
            self.state.finish_run(run_id, False, error=str(e)[:800])
            raise

    def run(self, p, running, source="live"):
        cfg = self.cfg
        jid = p["jid"]; name = p["name"]; kind = p["kind"]
        items = json.loads(p["items"]); pks = [it["pk"] for it in items]
        run_dir = self.runs_dir / ("%s-%s" % (dt.datetime.now().strftime("%Y%m%d-%H%M%S"), safe_name(jid)))
        run_dir.mkdir(parents=True, exist_ok=True)
        model = "dry-run" if self.dry_run else (cfg.get("brain") or {}).get("model", "claude-sonnet-5")
        run_id = self.state.start_run(jid, name, kind, pks, model, run_dir)
        log.info("run #%d 开始 %s (%d 封, %s)", run_id, name, len(items), source)
        V = Vocab(cfg)
        try:
            sweep_json = run_dir / "sweep.json"
            # 对话包窗口: 有 summary 记忆就只拉上次判断之后 + 最近几天, 定期或板上要求时全量接地。群的全窗本来就短 (语音全转会让一个 run 跑很久)。
            prev = self.state.get_conversation(jid)
            days, pack_note, grounded = self.pack_window(kind, prev, ground=bool(p.get("ground")))
            log.info("run #%d 对话包 %d 天 · %s", run_id, days, "接地" if grounded else "增量")
            work = self.sweep(["--only-jid", jid, "--days", str(days), "--out", str(sweep_json)],
                              int((cfg.get("brain") or {}).get("sweep_timeout_seconds", 1500)), key=jid)
            pack_md = sweep_json.with_suffix(".md").read_text(encoding="utf-8")
            try:
                ctx_md = self.context(sweep_json, run_dir, work)
            except Exception as e:
                ctx_md = "(第二大脑查找失败: %s)" % e
                log.warning("run #%d wa_context 失败, 继续: %s", run_id, e)
            prompt = self.build_prompt(p, items, pack_md, ctx_md, prev, pack_note=pack_note)
            (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
            if self.dry_run:
                fake = {"world": (prev or {}).get("world") or V.fallback_world, "stage": (prev or {}).get("stage"), "priority": V.lowest_priority,
                        "needs_me": False, "draft": "", "reason": "dry-run, 没叫 claude", "confidence": 0}
                res = {"text": json.dumps(fake, ensure_ascii=False), "is_error": False, "cost_usd": 0, "duration_ms": 0, "num_turns": 0, "breach": [], "denials": []}
            else:
                last_err = None
                res = None
                for attempt in range(1 + int((cfg.get("brain") or {}).get("retries", 1))):
                    try:
                        res = self.call_claude(prompt, run_dir, attempt=attempt)
                        if res["is_error"]:
                            raise RunError("claude 回 is_error: %s" % (res.get("error") or res["text"][:300]))
                        break
                    except RunError as e:
                        last_err = e; log.warning("run #%d 第 %d 次失败: %s", run_id, attempt + 1, e)
                        res = None
                if res is None:
                    raise last_err
            j = normalize(extract_json(res["text"]), prev, cfg)
            # 付款地板也套在板上的 stage: 有付款记录的人, 生意世界里 stage 不低于地板 (成交 / 服务中); 只往上, 不碰 parked / done。
            try:
                floor = self.dossier.has_payment(jid) if (self.dossier is not None and kind == "direct") else False
                if (floor and V.is_business(j.get("world")) and j.get("stage") in V.ladder and floor in V.ladder
                        and V.stage_index(j["stage"]) < V.stage_index(floor)):
                    j["reason"] = "[%s 有付款记录, stage 地板 %s (脑说 %s)] " % (facts_source_label(cfg), floor, j["stage"]) + j["reason"]
                    j["stage"] = floor
            except Exception as e:
                log.warning("run #%d 付款地板判断失败 (继续): %s", run_id, e)
            (run_dir / "judgment.json").write_text(json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")
            if self.dry_run:
                # ⛔ 试跑的假判断不盖真状态、不写 vault、不进 intake、不推: 只落 run 夹的 judgment.json
                log.info("run #%d dry-run: 判断只落 run 夹 (%s), 不写状态库、不写 vault、不推", run_id, run_dir / "judgment.json")
                self.state.finish_run(run_id, True, output=j, duration_ms=0, num_turns=0)
                return
            # 脑跑的这几十秒里主人回了 → 板上那行保 replied 不重开 (其余判断照落)
            keep_replied = self.state.replied_after(jid, items[-1]["at"])
            if keep_replied:
                log.info("run #%d 落地时主人已回 %s, 保 replied 不重开", run_id, name)
                j["needs_me"] = False; j["priority"] = V.lowest_priority
            self.state.upsert_judgment(jid, name, kind, j, pks[-1], items[-1]["at"], run_id, grounded=grounded, keep_replied=keep_replied)
            self.state.write_summaries(self.live / "summaries.md", gate=self.gate)
            if self.dossier is not None:
                try:
                    self.dossier.after_judgment(jid, name, kind, j, prev)
                except Exception as e:
                    log.warning("run #%d dossier 触发失败 (继续): %s", run_id, e)
                if kind == "group":
                    # 群里有档的发言人, 近况各 +1 行 (只写档的近况块, 不开档不动 stage)
                    try:
                        k = self.dossier.after_group_run(name, items, world=j.get("world"))
                        if k:
                            log.info("run #%d 群发言人近况 +%d 份档 (%s)", run_id, k, name)
                    except Exception as e:
                        log.warning("run #%d 群发言人近况失败 (继续): %s", run_id, e)
            # intake buffer: 脑抓到的「要主人做的事」追加进 vault 的进料口 (去重, 只 append); 群的也收 (脑只认对主人讲的)
            it_cfg = cfg.get("intake") or {}
            buffer = intake_buffer_path(cfg)
            if WI is not None and j.get("captures") and it_cfg.get("enabled") and buffer is not None:
                try:
                    note = self.dossier.note_for_speaker(jid) if (self.dossier is not None and kind == "direct") else None
                    n_added = call_opt(WI.append_captures, str(state_db_path(cfg)), str(buffer), jid, name, j.get("world"), j["captures"],
                                       source="wa", people_note=(note.stem if note is not None else None), cfg=cfg)
                    if n_added:
                        log.info("run #%d intake +%d (%s)", run_id, n_added, name)
                except Exception as e:
                    log.warning("run #%d intake 追加失败 (继续): %s", run_id, e)
            self.state.finish_run(run_id, True, output=j, cost_usd=res["cost_usd"], duration_ms=res["duration_ms"],
                                  num_turns=res["num_turns"], cage_breach=res["breach"], denials=res["denials"],
                                  input_tokens=res.get("input_tokens"), output_tokens=res.get("output_tokens"),
                                  cache_read_tokens=res.get("cache_read_tokens"), cache_create_tokens=res.get("cache_create_tokens"))
            # 板子与 log 只报 token 与秒: 订阅跑的 claude -p 回报的美元是「走 API 会多少钱」, 不是真的扣款。
            log.info("run #%d 完成 %s: %s/%s/%s needs_me=%s 读 %s / 写 %s tokens %sms", run_id, name, j["world"], j["stage"], j["priority"], j["needs_me"],
                     (res.get("input_tokens") or 0) + (res.get("cache_read_tokens") or 0) + (res.get("cache_create_tokens") or 0),
                     res.get("output_tokens") or 0, res["duration_ms"])
            push_kind = "needs_me" if j["needs_me"] else "info"
            pushed = False
            if self.notifier.wants(push_kind):
                pushed = self.notifier.send(self.format_notify(jid, name, kind, j, res["breach"], run_id, res), kind=push_kind)
            if res["breach"] and not pushed:
                # 判断本身不推的模式 (默认 errors), 笼子破了照样要喊
                self.notifier.send("🚨 [WA live] run #%d (%s) cage breach: 载入了不该有的工具 %s" % (run_id, name, ", ".join(res["breach"])),
                                   key="breach", min_gap=1800, kind="error")
        except Exception as e:
            if isinstance(e, RunError):   # 预期中的失败 (超时、is_error): 一行就够, 不印堆叠
                log.error("run #%d 失败 %s: %s", run_id, name, str(e)[:300])
            else:
                log.exception("run #%d 失败 %s", run_id, name)
            self.state.finish_run(run_id, False, error=str(e)[:800])
            self.notifier.send("⚠️ [WA live] run #%d 失败 (%s): %s\n看 %s" % (run_id, name, str(e)[:300], run_dir),
                               key="run_fail:" + jid, min_gap=1800, kind="error")
        finally:
            self.state.consume_pending(jid, pks)
            running.discard(jid)

    def format_notify(self, jid, name, kind, j, breach, run_id=None, res=None):
        icon = "💬" if j["needs_me"] else "👀"
        head = "%s [WA live #%s] %s · %s / %s / %s (%.0f%%)\n%s" % (icon, run_id or "?", name, j.get("world"), j.get("stage") or "-",
                                                               j.get("priority"), j["confidence"] * 100, j["reason"])
        if res and res.get("duration_ms"):
            head += "\n(%.0fs)" % ((res.get("duration_ms") or 0) / 1000.0)
        body = "\n\n草稿:\n%s" % j["draft"] if j["draft"] else ("" if j["needs_me"] else "\n(不用回)")
        link = ""
        if kind == "direct" and j["draft"]:
            phone = resolve_phone(jid)
            if phone:
                # 推播端多半只把 http(s) 变成可点的链接; wa.me 在手机上一样直接开 WhatsApp 带字
                link = "\n\nhttps://wa.me/%s?text=%s" % (phone, urllib.parse.quote(j["draft"]))
        warn = "\n\n🚨 cage breach: %s" % ", ".join(breach) if breach else ""
        return (head + body + link + warn)[:3800]


# ---------------------------------------------------------------- 档案 run (人物档的 worker)

# 档头规矩 · 验收 · stage 裁决 · 提示组装 · 放档 · 写档锁 住人物档 skill 的 scripts/dossier_core.py (daemon 与命令行共用一份, 改规矩只改那边, ⛔ 不抄进这里)。
# 默认从安装包 skills/*-person-dossier/scripts/ 载入; config 的 paths.dossier_dir 指别处时, load_config 之后重载一次。
DC = None


def _load_dossier_core(cfg=None):
    global DC
    d = find_scripts_dir(cfg or {}, "dossier_dir", "-person-dossier", "dossier_core.py")
    f = d / "dossier_core.py"
    if not f.is_file():
        if DC is None:
            _IMPORT_ERRORS["dossier_core"] = "找不到 %s (人物档 skill 不在?)" % f
        return DC
    try:
        if DC is not None and Path(getattr(DC, "__file__", "") or "").resolve() == f.resolve():
            return DC
    except Exception:
        pass
    try:
        if str(d) not in sys.path:
            sys.path.insert(0, str(d))
        spec = importlib.util.spec_from_file_location("dossier_core", str(f))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["dossier_core"] = mod
        spec.loader.exec_module(mod)
        DC = mod
        _IMPORT_ERRORS.pop("dossier_core", None)
    except Exception as e:
        sys.modules.pop("dossier_core", None)
        DC = None
        _IMPORT_ERRORS["dossier_core"] = "%s: %s" % (type(e).__name__, str(e)[:300])
    return DC


_load_dossier_core()


def _dc(name):
    f = getattr(DC, name, None) if DC is not None else None
    return f if callable(f) else None


def _dossier_error(e):
    return DC is not None and isinstance(e, getattr(DC, "DossierError", ()))


class Dossier(object):
    """人物档的 worker: 低频、另一支 claude (dossier.model)、只输出档全文, dossier_core 验档头再写 vault。
    触发: 开新档 = 一对一、world 在 dossier.auto_open_worlds、stage 到 auto_open_min_stage 以上而档不存在; 已有的档一律等夜批重写;
    板上按随时跑; 事实表上付过钱有对话没档的也开。一次只跑一个 (排队), 同一人 min_gap_hours 内最多一次 (失败也算, 重启中断的不算)。
    dossier.enabled=false 时整块停: 不排队、不开档、不重写、也不追加近况行。"""

    def __init__(self, cfg, state, brain, notifier, dry_run=False):
        self.cfg = cfg; self.state = state; self.brain = brain; self.notifier = notifier; self.dry_run = dry_run
        self.d = cfg.get("dossier") if isinstance(cfg.get("dossier"), dict) else {}
        self.enabled = bool(self.d.get("enabled", False))
        self.vault = vault_dir(cfg)
        self.people = people_dir(cfg)
        self.backups = live_dir(cfg) / "dossier-backups"
        self.backups.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(str(self.backups), 0o700)
        except Exception:
            pass
        self.busy = None
        self.note_lock = threading.RLock()        # 同一个进程里的写档者 (近况追加 · 放档) 先排这把, 跨进程的靠 dossier_core.people_lock
        self._idx_cache = (None, None)
        self._forms_cache = {}
        self._forms_miss = {}
        self._warned = set()
        self._mem = {}
        with self.state.lock:
            self.state.conn.executescript("""
            CREATE TABLE IF NOT EXISTS dossier_jobs(
              id INTEGER PRIMARY KEY AUTOINCREMENT, jid TEXT, name TEXT, reason TEXT, requested_at TEXT, started_at TEXT,
              finished_at TEXT, ok INTEGER, error TEXT, note_path TEXT, run_dir TEXT, gaps INTEGER, was_new INTEGER);
            """)
            cols = {r[1] for r in self.state.conn.execute("PRAGMA table_info(dossier_jobs)")}
            if "was_new" not in cols:   # 旧库补栏: 日报用它分「开新档」与「重写」, 不再猜理由字串
                self.state.conn.execute("ALTER TABLE dossier_jobs ADD COLUMN was_new INTEGER")
            self.state.conn.commit()
        if not dry_run:
            # 上一个进程跑到一半被停的 job (kickstart 几乎一定会打断): 记成失败, 那个人才排得回队列; 标「中断:」, 间隔规则不算它
            with self.state.lock:
                n = self.state.conn.execute("""UPDATE dossier_jobs SET finished_at=?, ok=0, error='中断: daemon 重启时这个 job 没跑完'
                    WHERE started_at IS NOT NULL AND finished_at IS NULL""", (now_iso(),)).rowcount
                self.state.conn.commit()
            if n:
                log.warning("dossier: %d 个跑到一半被重启打断的 job 记成失败, 那几个人可以重新排队", n)
        # 首次启动 (或白天重启) 不补跑今天的夜批: 已经过了 nightly_at 就把今天记成跑过, 等下一个点
        hh, mm = self._hhmm(self.d.get("nightly_at", "02:30"))
        now = dt.datetime.now()
        if (now.hour, now.minute) >= (hh, mm) and self._mark_get("dossier_nightly_last") != now.date().isoformat():
            self._mark_set("dossier_nightly_last", now.date().isoformat())

    @staticmethod
    def _hhmm(s, default=(2, 30)):
        try:
            hh, mm = [int(x) for x in str(s).split(":")]
            if 0 <= hh < 24 and 0 <= mm < 60:
                return hh, mm
        except Exception:
            pass
        log.warning("时刻 %r 看不懂, 当 %02d:%02d", s, default[0], default[1])
        return default

    def _mark_get(self, key):
        return self._mem.get(key) if self.dry_run else self.state.get_meta(key)

    def _mark_set(self, key, value):
        if self.dry_run:
            self._mem[key] = value           # 试跑不动真排程的「今天跑过没」
        else:
            self.state.set_meta(key, value)

    def _warn_once(self, key, msg, *args):
        if key not in self._warned:
            self._warned.add(key)
            log.warning(msg, *args)

    def writes_allowed(self):
        return self.enabled and not self.dry_run and self.people is not None

    def people_lock(self):
        """跨进程的写人物档锁 (dossier_core.people_lock): 近况追加与放档 (daemon 或命令行) 不互盖。模组没有这把锁就不锁。"""
        f = _dc("people_lock")
        return f(self.cfg) if f is not None else contextlib.nullcontext()

    def load_facts(self):
        if WF is None:
            return None
        try:
            if hasattr(WF, "load_facts"):
                return WF.load_facts(self.cfg)
        except Exception as e:
            log.warning("load_facts 失败: %s", e)
            return None
        try:
            return json.loads((live_dir(self.cfg) / "facts.json").read_text(encoding="utf-8"))
        except Exception:
            return None

    def count_gaps(self, body):
        f = _dc("count_gaps")
        if f is None:
            return None
        try:
            return call_opt(f, body, cfg=self.cfg)
        except Exception:
            return None

    def how_to_talk(self, body):
        f = _dc("how_to_talk_from")
        if f is None:
            return None
        try:
            return call_opt(f, body, cfg=self.cfg)
        except Exception:
            return None

    # ---- People 房索引 (wa_common.people_index 唯一的扫法; 房里有档改过才重扫)
    def _room_sig(self):
        if self.people is None or not self.people.is_dir():
            return None
        try:
            files = [p for p in self.people.glob("*.md")]
            return (self.people.stat().st_mtime, len(files), max([p.stat().st_mtime for p in files] or [0]))
        except Exception:
            return None

    def index(self):
        empty = {"by_jid": {}, "by_phone": {}, "by_lid": {}, "error": None}
        if self.people is None:
            empty["error"] = "paths.people_dir 没设"
            return empty
        sig = self._room_sig()
        if sig is None:
            empty["error"] = "People 房不在或读不了: %s" % self.people
            self._warn_once("people_missing", "dossier: %s", empty["error"])
            return empty
        if self._idx_cache[0] == sig and self._idx_cache[1] is not None:
            return self._idx_cache[1]
        idx = None
        if WC is not None and hasattr(WC, "people_index"):
            try:
                idx = WC.people_index(self.cfg)
            except Exception as e:
                log.warning("people_index 失败: %s", e)
        if not isinstance(idx, dict):
            idx = {"by_jid": {}, "by_phone": {}, "by_lid": {}, "error": "wa_common.people_index 不在"}
        for k in ("by_jid", "by_phone", "by_lid"):
            if not isinstance(idx.get(k), dict):
                idx[k] = {}
        idx.setdefault("error", None)
        if idx.get("error"):
            self._warn_once("people_err", "dossier: People 房索引有错: %s", idx["error"])
        self._idx_cache = (sig, idx)
        return idx

    def notes_by_jid(self):
        return self.index()["by_jid"]

    def jid_forms(self, jid):
        """同一个人的两种 jid: `<lid>@lid` 与 `<电话>@s.whatsapp.net` 互解, 回集合 (含原值)。群号回只含自己。解不开的 10 分钟内不重查。"""
        if not jid or jid.endswith("@g.us"):
            return {jid} if jid else set()
        if jid in self._forms_cache:
            return set(self._forms_cache[jid])
        miss = self._forms_miss.get(jid)
        if miss and time.time() - miss < PHONE_MISS_TTL:
            return {jid}
        forms = {jid}
        head = jid.split("@")[0]
        if jid.endswith("@lid"):
            ph = resolve_phone(jid)
            if ph:
                forms.add(ph + "@s.whatsapp.net")
        elif jid.endswith("@s.whatsapp.net") and head.isdigit() and WC is not None and hasattr(WC, "lid_of_phone"):
            try:
                lid = WC.lid_of_phone(head)
                if lid:
                    forms.add(str(lid).split("@")[0] + "@lid")
            except Exception as e:
                log.debug("jid_forms %s: %s", jid, e)
        if len(forms) > 1:
            self._forms_cache[jid] = frozenset(forms)
        else:
            self._forms_miss[jid] = time.time()
        return forms

    def note_for_speaker(self, jid):
        """jid → 人物档 Path 或 None。⭐ 唯一的对人算法: 一对一 run、板上档资讯、档案 run、intake 带档名、群发言人、夜批、事实表排开档全叫它。
        档头 wa_jid 两种形都试 (LID 形 / 电话形), 再退档头 wa_phone。"""
        if not jid or jid.endswith("@g.us"):
            return None
        idx = self.index()
        forms = self.jid_forms(jid)
        for f in sorted(forms):
            p = idx["by_jid"].get(f)
            if p:
                return Path(p)
        for f in sorted(forms):
            head = f.split("@")[0]
            if f.endswith("@lid") and head in idx["by_lid"]:
                return Path(idx["by_lid"][head])
        phones = sorted(f.split("@")[0] for f in forms if f.endswith("@s.whatsapp.net") and f.split("@")[0].isdigit())
        for ph in phones:
            if ph in idx["by_phone"]:
                return Path(idx["by_phone"][ph])
            if WC is not None and hasattr(WC, "people_note_for") and not idx.get("error"):
                try:
                    p = call_opt(WC.people_note_for, ph, idx, cfg=self.cfg)
                    if p:
                        return Path(p)
                except Exception as e:
                    log.debug("people_note_for %s: %s", ph, e)
        return None

    def chat_jid_for(self, note_jid):
        """档头的 jid 形 → 有对话的那一形 (读取器 --only-jid 是完全比对, 形不同时对话包是空的)。"""
        forms = self.jid_forms(note_jid)
        with self.state.lock:
            for f in sorted(forms):
                if self.state.conn.execute("SELECT 1 FROM conversations WHERE jid=? AND last_run_id IS NOT NULL", (f,)).fetchone():
                    return f
        try:
            c = _ro(wa_root() / "ChatStorage.sqlite", timeout=3)
            try:
                for f in sorted(forms):
                    if c.execute("SELECT 1 FROM ZWACHATSESSION WHERE ZCONTACTJID=?", (f,)).fetchone():
                        return f
            finally:
                c.close()
        except Exception:
            pass
        return note_jid

    def rel_to_vault(self, p):
        if self.vault is None:
            return Path(p).name
        try:
            return str(Path(p).resolve().relative_to(self.vault.resolve()))
        except Exception:
            return Path(p).name

    def note_info(self, jid):
        """板上要的: 档名 · vault 相对路径 · 上次写档日期 · 待问几个 · stage · roles · 怎么跟他讲。没档回 None。"""
        p = self.note_for_speaker(jid)
        if not p:
            return None
        base = {"file": p.name, "path": self.rel_to_vault(p)}
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            base.update({"updated": None, "gaps": None})
            return base
        fm, body = _fm(text)
        upd = fm.get("dossier_updated")
        if not upd and _dc("dossier_marker") is not None:
            try:
                upd = DC.dossier_marker(text)[1]
            except Exception:
                upd = None
        roles = fm.get("roles")
        if _dc("fm_list") is not None:
            roles = DC.fm_list(roles)
        elif WC is not None and hasattr(WC, "fm_list"):
            roles = WC.fm_list(roles)
        base.update({"updated": upd or fm.get("last_contact"), "gaps": self.count_gaps(body), "dossier": "v1" if upd else None,
                     "stage": fm.get("stage") or None, "roles": roles if isinstance(roles, list) else [], "how_to_talk": self.how_to_talk(body)})
        return base

    def after_group_run(self, group_name, items, world=None):
        """群 run 收尾: burst 里每个有 speaker_jid 的发言人, 有档才在档的近况块加一行
        `- YYYY-MM-DD · 在「<群名>」说: <他那句前 90 字>`; 同一人同一 run 只写最新一句。⛔ 不开档、不动 stage / priority / world、不写 intake。回写了几份。"""
        if not self.writes_allowed():
            return 0
        V = Vocab(self.cfg)
        if world and not V.is_business(world) and world != V.fallback_world:
            return 0   # 私人群里的话不进人物档
        latest = {}
        for it in sorted(items or [], key=lambda x: str(x.get("at") or "")):   # 按时间, 不按 pk (真库 Z_PK 与时间不同序)
            sj = it.get("speaker_jid")
            if sj and len(re.sub(r"\s+", "", it.get("text") or "")) >= 4:   # 没文字 ([图片] [语音]) 与「ok」这种不写
                latest[sj] = it
        n = 0
        seen_notes = set()
        for sj, it in latest.items():
            note = self.note_for_speaker(sj)
            if note is None or note in seen_notes:
                continue
            seen_notes.add(note)
            said = re.sub(r"\s+", " ", (it.get("text") or "").strip())
            day = str(it.get("at") or "")[:10] or dt.date.today().isoformat()
            try:
                # 同人同群同一天只留一行, 新的盖旧的
                if self.append_recent(note, "- %s · 在「%s」说: %s" % (day, group_name, said[:90]), replace_prefix="- %s · 在「%s」说:" % (day, group_name)):
                    n += 1
            except Exception as e:
                log.warning("dossier 群发言近况追加失败 %s: %s", note.name, e)
        return n

    def stage_past_min(self, stage):
        V = Vocab(self.cfg)
        if not stage:
            return False
        if stage == V.parked:
            return True
        mn = str(self.d.get("auto_open_min_stage") or "talking")
        if mn not in V.ladder:
            mn = V.ladder[1] if len(V.ladder) > 1 else (V.ladder[0] if V.ladder else stage)
        return V.stage_index(stage) >= V.stage_index(mn) >= 0

    # ---- 分诊 run 收尾时叫: 追加近况一行 + 看要不要排开档
    def after_judgment(self, jid, name, kind, j, prev):
        if kind != "direct" or not self.writes_allowed():
            return
        note = self.note_for_speaker(jid)
        if note is not None:
            try:
                line = self.recent_line_from_judgment(j)
                if line:
                    self.append_recent(note, line)
            except Exception as e:
                log.warning("dossier 近况追加失败 %s: %s", note.name, e)
        # 事件触发只剩「开新档」; 已有的档一律等夜批 (白天跟着讯息重写会拿到半截对话, 也会撞到主人手改)
        world = j.get("world"); stage = j.get("stage")
        auto = [str(w) for w in (self.d.get("auto_open_worlds") or [])]
        if note is None and world in auto and self.stage_past_min(stage):
            self.enqueue(jid, name, "开新档: %s / %s" % (world, stage))

    def traffic_count(self, note_jid, since_iso):
        """夜批的「来往封数」: 一对一过闸 inbound + 主人的 outbound, 加上他在过闸群里说的话 (inbound_log.speaker_jid, LID / 电话两种形都算)。"""
        forms = sorted(self.jid_forms(note_jid))
        qs = ",".join("?" * len(forms))
        sql = ("SELECT COUNT(*) FROM inbound_log WHERE (jid IN (%s) OR speaker_jid IN (%s)) AND (decision LIKE 'pass%%' OR decision LIKE 'outbound%%') AND msg_at >= ?"
               % (qs, qs))
        with self.state.lock:
            return self.state.conn.execute(sql, forms + forms + [since_iso]).fetchone()[0]

    def nightly_candidates(self, now=None):
        """夜批挑谁: 有档的人里, 最近 24 小时来往 ≥ nightly_min_msgs 封, 或 stage 在 24 小时内变过;
        另加「档超过 stale_days 没重写, 但这段时间内有任何动静」的补一次。按 priority 再按封数排, 最多 nightly_cap。排队用有对话的那一形 jid。"""
        now = now or dt.datetime.now()
        V = Vocab(self.cfg)
        since24 = (now - dt.timedelta(hours=24)).isoformat(timespec="seconds")
        stale_days = int(self.d.get("stale_days", 14))
        since_stale = (now - dt.timedelta(days=stale_days)).isoformat(timespec="seconds")
        prio = dict((k, i) for i, k in enumerate(V.priority_keys))
        idx = self.index()
        pairs = []
        seen = set()
        for hj, note in sorted(idx["by_jid"].items()):
            if hj.endswith("@g.us") or str(Path(note)) in seen:
                continue
            pairs.append((hj, Path(note)))
            seen.add(str(Path(note)))
        for ph, note in sorted(idx["by_phone"].items()):
            if str(Path(note)) not in seen:
                pairs.append((ph + "@s.whatsapp.net", Path(note)))
                seen.add(str(Path(note)))
        picks = []
        for hj, note in pairs:
            try:
                head = note.read_text(encoding="utf-8")[:3000]
                upd = DC.note_updated(head) if _dc("note_updated") is not None else _fm(head)[0].get("dossier_updated")
            except Exception:
                upd = None
            n24 = self.traffic_count(hj, since24)
            n_stale = self.traffic_count(hj, since_stale)
            chat = self.chat_jid_for(hj)
            with self.state.lock:
                c = None
                for f in [chat] + sorted(self.jid_forms(hj) - {chat}):
                    c = self.state.conn.execute("SELECT name, priority, stage, stage_changed_at FROM conversations WHERE jid=?", (f,)).fetchone()
                    if c:
                        break
            name = (c["name"] if c else None) or note.stem
            stage_moved = bool(c and c["stage_changed_at"] and c["stage_changed_at"] >= since24)
            reason = None
            if n24 >= int(self.d.get("nightly_min_msgs", 5)):
                reason = "夜批: 24 小时内来往 %d 封" % n24
            elif stage_moved:
                reason = "夜批: stage 24 小时内变成 %s" % c["stage"]
            elif n_stale and (not upd or str(upd) < since_stale[:10]):
                reason = "夜批: 超过 %s 天没重写, 期间有 %d 封动静" % (stale_days, n_stale)
            if reason:
                picks.append((prio.get(c["priority"] if c else None, len(prio)), -n24, chat, name, reason))
        picks.sort()
        return [(jid, name, reason) for _, _, jid, name, reason in picks[:int(self.d.get("nightly_cap", 8))]]

    def nightly(self):
        """daemon 约每分钟叫, 自己判断时间到了没: 本地时间过了 nightly_at 且今天还没跑过。"""
        if not self.enabled or self.dry_run:
            return
        now = dt.datetime.now()
        hh, mm = self._hhmm(self.d.get("nightly_at", "02:30"))
        if (now.hour, now.minute) < (hh, mm):
            return
        today = now.date().isoformat()
        if self._mark_get("dossier_nightly_last") == today:
            return
        self._mark_set("dossier_nightly_last", today)
        picks = self.nightly_candidates(now)
        for jid, name, reason in picks:
            self.enqueue(jid, name, reason)
        log.info("dossier 夜批: 挑了 %d 份 (%s)", len(picks), ", ".join(n for _, n, _ in picks) or "无")

    @staticmethod
    def recent_line_from_judgment(j):
        """一对一 run 的近况行: situation 第一句, 90 字。"""
        sit = (j.get("situation") or "").strip()
        first = re.split(r"[。；;!！?？]", sit)[0].strip() if sit else ""
        return ("- %s · %s" % (dt.date.today().isoformat(), first[:90])) if first else None

    def append_recent(self, note, line, replace_prefix=None):
        """在 <!-- wa-live:begin --> 与 end 之间加一行 (`- YYYY-MM-DD · 一件事`), 最新在上, 最多 recent_lines 行。
        唯一的写近况函数: 一对一 run · 群发言人 · 第二讯息来源夜拉都叫它。没有那对标记的档不动。
        同一件事 (「·」后面的字) 已经在块里就不重复; replace_prefix 给了就先拿掉以它开头的旧行。回 True = 真的写了。
        写的时候拿 dossier_core.people_lock (放档也拿同一把), 近况追加与放档不互盖。"""
        if not self.writes_allowed():
            return False
        line = (line or "").strip()
        if not line.startswith("- ") or "·" not in line:
            return False
        note = Path(note)
        with self.note_lock:
            with self.people_lock():
                text = note.read_text(encoding="utf-8")
                if "<!-- wa-live:begin" not in text or "<!-- wa-live:end -->" not in text:
                    return False
                what = line.split("·", 1)[1].strip()
                head, rest = text.split("<!-- wa-live:begin", 1)
                marker_end = rest.find("-->") + 3
                block, tail = rest[marker_end:].split("<!-- wa-live:end -->", 1)
                lines = [l for l in block.strip().splitlines() if l.strip().startswith("- ")]
                if any(l.split("·", 1)[-1].strip() == what for l in lines):
                    return False
                if replace_prefix:
                    lines = [l for l in lines if not l.strip().startswith(replace_prefix)]
                lines = ([line] + lines)[:int(self.d.get("recent_lines", 12))]
                new = head + "<!-- wa-live:begin" + rest[:marker_end] + "\n" + "\n".join(lines) + "\n" + "<!-- wa-live:end -->" + tail
                note.write_text(new, encoding="utf-8")
        log.info("dossier: %s 近况 +1", note.name)
        return True

    def enqueue(self, jid, name, reason, force=False):
        if self.dry_run:
            log.info("dossier: dry-run 不排队 (%s, %s)", name, reason)
            return False
        if not self.enabled:
            log.info("dossier: 没开 (dossier.enabled=false), 不排队 %s (%s)", name, reason)
            return False
        with self.state.lock:
            open_ = self.state.conn.execute("SELECT id FROM dossier_jobs WHERE jid=? AND finished_at IS NULL", (jid,)).fetchone()
            if open_:
                return False
            # 间隔算成功的也算失败的 (开档被退后, 对方每来一封讯息就再排一次, 一小时烧一次模型加一则推播); 重启中断的不算; 板上按照样马上跑
            last = self.state.conn.execute("""SELECT finished_at FROM dossier_jobs WHERE jid=? AND finished_at IS NOT NULL
                AND COALESCE(error,'') NOT LIKE '中断:%' ORDER BY id DESC LIMIT 1""", (jid,)).fetchone()
            if last and last["finished_at"] and not (force or reason.startswith("板上")):
                try:
                    if (dt.datetime.now() - dt.datetime.fromisoformat(last["finished_at"])).total_seconds() < float(self.d.get("min_gap_hours", 6)) * 3600:
                        return False
                except Exception:
                    pass
            self.state.conn.execute("INSERT INTO dossier_jobs(jid,name,reason,requested_at) VALUES(?,?,?,?)", (jid, name, reason, now_iso()))
            self.state.conn.commit()
        log.info("dossier: 排队 %s (%s)", name, reason)
        return True

    def tick(self):
        """daemon 主循环每轮叫一次: 没在跑就捡下一个 job 起线程。"""
        if self.busy is not None or not self.enabled or self.dry_run:
            return
        with self.state.lock:
            job = self.state.conn.execute("SELECT * FROM dossier_jobs WHERE started_at IS NULL ORDER BY id LIMIT 1").fetchone()
        if not job:
            return
        job = dict(job)
        self.busy = job["jid"]
        threading.Thread(target=self.run_job, args=(job,), daemon=True).start()

    def prompt_template(self):
        return find_scripts_dir(self.cfg, "dossier_dir", "-person-dossier", "dossier_core.py").parent / "assets" / "dossier-prompt.md"

    def compose(self, template, name, note_jid, c, job, note, existing, facts_block, pack, ctx_md):
        """档案 run 的提示: 填法只住 dossier_core.compose_prompt (daemon 与命令行同一份)。playbook 先经 playbook_source 读
        (主人副本优先, 退回种子时大声讲), 退回种子另记 meta seed_fallback:person-playbook 给日报。"""
        dlog = lambda m: log.warning("dossier: %s", m)
        spec = None
        if _dc("playbook_source") is not None:
            got = call_opt(DC.playbook_source, self.cfg, log=dlog)
            spec, ppath, is_seed, perr = (list(got) + [None, None, None, None])[:4]
            if spec is None:
                raise RunError(perr or "人物档 playbook 读不到")
            if is_seed:
                self.state.set_meta("seed_fallback:person-playbook", "%s %s" % (now_iso(), ppath))
            elif self.state.get_meta("seed_fallback:person-playbook"):
                self.state.del_meta("seed_fallback:person-playbook")
        if _dc("compose_prompt") is None:
            raise RunError("dossier_core 没有 compose_prompt (人物档 skill 太旧?), 不组提示")
        return call_opt(DC.compose_prompt, self.cfg, template, name, note_jid, board=c, reason=job["reason"],
                        existing_name=(note.name if note is not None else None), existing=existing, facts_block=facts_block,
                        pack=pack, ctx_md=ctx_md, spec=spec, kind="一对一", log=dlog)

    def run_job(self, job):
        jid = job["jid"]; name = job["name"]
        cfg = self.cfg
        model = self.d.get("model", "claude-opus-5")
        run_dir = self.brain.runs_dir / ("dossier-%s-%s" % (dt.datetime.now().strftime("%Y%m%d-%H%M%S"), safe_name(jid)))
        run_dir.mkdir(parents=True, exist_ok=True)
        with self.state.lock:
            self.state.conn.execute("UPDATE dossier_jobs SET started_at=?, run_dir=? WHERE id=?", (now_iso(), str(run_dir), job["id"]))
            self.state.conn.commit()
        run_id = self.state.start_run(jid, name, "dossier", [], model, run_dir)
        log.info("dossier run #%d 开始 %s (%s)", job["id"], name, job["reason"])
        res = None
        target = None
        existing = ""
        try:
            if DC is None:
                raise RunError("dossier_core 没载入 (%s), 不验不放" % _IMPORT_ERRORS.get("dossier_core", "?"))
            if self.people is None or not self.people.is_dir():
                raise RunError("People 房不在 (paths.people_dir=%s), 不写档" % self.people)
            note = self.note_for_speaker(jid)
            existing = note.read_text(encoding="utf-8") if note else ""
            note_jid = jid
            if note is not None:
                hj = str(_fm(existing)[0].get("wa_jid") or "").strip()
                if hj:
                    note_jid = hj              # 重写: 档头原来那一形 jid 不准改 (验档器比的是它)
            sweep_json = run_dir / "sweep.json"
            work = self.brain.sweep(["--only-jid", jid, "--days", str(int(self.d.get("pack_days", 30))), "--out", str(sweep_json)],
                                    int((cfg.get("brain") or {}).get("sweep_timeout_seconds", 1500)), key="dossier-" + jid)
            pack = sweep_json.with_suffix(".md").read_text(encoding="utf-8")
            try:
                ctx_md = self.brain.context(sweep_json, run_dir, work)
            except Exception as e:
                ctx_md = "(第二大脑查找失败: %s)" % e
            c = self.state.get_conversation(jid) or {}
            tpl_path = self.prompt_template()
            if not tpl_path.is_file():
                raise RunError("档案 run 提示模板不在: %s" % tpl_path)
            facts = self.load_facts()
            facts_block = ""
            if WF is not None and facts is not None:
                try:
                    facts_block = call_opt(WF.facts_section, facts, jid, cfg=cfg) or ""
                except Exception as e:
                    log.warning("dossier: facts_section 失败 (继续): %s", e); facts_block = ""
            prompt = self.compose(tpl_path.read_text(encoding="utf-8"), name, note_jid, c, job, note, existing, facts_block, pack, ctx_md)
            (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
            res = self.call_claude(prompt, run_dir)
            text = res["text"].strip()
            if _dc("parse_file_header") is not None:
                header_name, body = DC.parse_file_header(text)
            else:
                m = re.match(r"\s*<!--\s*file:\s*(.+?)\s*-->[ \t]*\n?", text)
                header_name, body = (m.group(1).strip(), text[m.end():]) if m else (None, text)
            if not header_name:
                raise RunError("输出第一行不是 <!-- file: X.md -->: %r" % text[:120])
            body = body.lstrip()
            if note is not None:
                fname = note.name                  # 重写不准改档名, 模型那一行不看
            else:
                if _dc("check_note_filename") is None:
                    raise RunError("dossier_core 没有 check_note_filename, 不开新档")
                fname = DC.check_note_filename(header_name)      # 档名规矩只住 dossier_core (任何文字的字母、数字、空格、点、连字号、底线)
                if fname.startswith("_"):
                    raise RunError("开新档的档名 %r 底线开头: People 索引会跳过这种档, 下次又当没档" % fname)
            if not body.startswith("---"):
                raise RunError("档不是从 --- 档头开始")
            body, info = call_opt(DC.finalize, body, note_jid, existing, c.get("stage"), self.has_payment(jid),
                                  log=lambda s: log.info("dossier: %s %s", name, s),
                                  facts_block=(facts_block if facts is not None else None), cfg=cfg)
            for w in (call_opt(DC.warnings_for, body, cfg=cfg) if _dc("warnings_for") is not None else []):
                log.warning("dossier: %s ⚠ %s", name, w)
            if _dc("quotes_check") is not None:
                try:
                    _rows, hits, nq = DC.quotes_check(body, [pack, existing])
                    if nq and hits < nq * 0.7:
                        log.warning("dossier: %s ⚠ 抽 %d 句「原话」回对话包只找到 %d 句, 看 run 夹 note.md", name, nq, hits)
                except Exception as e:
                    log.debug("quotes_check: %s", e)
            target = note if note is not None else (self.people / fname)
            with self.note_lock:
                # ⭐ 放档的安全规矩 (重读目标档、开新档撞到现档就退、只有近况块变了就带进新行、其他变了就退、先备份) 只住 dossier_core.place_note
                placed = call_opt(DC.place_note, cfg, fname, body, existing, self.backups, job["reason"],
                                  "档案 run (whatsapp-live daemon, %s)" % model, log=lambda s: log.info("dossier: %s %s", name, s))
            (run_dir / "note.md").write_text(body, encoding="utf-8")
            gaps = info.get("gaps") if isinstance(info, dict) else None
            if gaps is None:
                gaps = self.count_gaps(body)
            self._idx_cache = (None, None)
            with self.state.lock:
                self.state.conn.execute("UPDATE dossier_jobs SET finished_at=?, ok=1, note_path=?, gaps=?, was_new=? WHERE id=?",
                                        (now_iso(), str(placed or target), gaps, 0 if note is not None else 1, job["id"]))
                self.state.conn.commit()
            self._finish_run(run_id, True, res)
            log.info("dossier run #%d 完成 %s → %s (%s, 待问 %s, 读 %s / 写 %s tokens, %ss)", job["id"], name, fname, "重写" if note else "新开", gaps,
                     (res.get("input_tokens") or 0) + (res.get("cache_read_tokens") or 0) + (res.get("cache_create_tokens") or 0), res.get("output_tokens"), (res.get("duration_ms") or 0) // 1000)
        except Exception as e:
            if isinstance(e, RunError) or _dossier_error(e):
                log.error("dossier run #%d 失败 %s: %s", job["id"], name, str(e)[:300])
            else:
                log.exception("dossier run #%d 失败 %s", job["id"], name)
            err = str(e)[:800]
            # 放档被退而档在 run 期间被手改过 (近况块以外的地方变了): 不盖, 用现档重排一次
            requeue = False
            if _dossier_error(e) and existing and target is not None:
                try:
                    requeue = target.exists() and target.read_text(encoding="utf-8") != existing and not str(job["reason"]).startswith("重排")
                except Exception:
                    requeue = False
            with self.state.lock:
                self.state.conn.execute("UPDATE dossier_jobs SET finished_at=?, ok=0, error=? WHERE id=?", (now_iso(), err, job["id"]))
                if requeue:
                    self.state.conn.execute("INSERT INTO dossier_jobs(jid,name,reason,requested_at) VALUES(?,?,?,?)",
                                            (jid, name, "重排 (档在 run 期间被手改): " + str(job["reason"]), now_iso()))
                self.state.conn.commit()
            self._finish_run(run_id, False, res, error=err)
            self.notifier.send("⚠️ [WA live] 档案 run #%d 失败 (%s): %s\n看 %s" % (job["id"], name, str(e)[:300], run_dir),
                               key="dossier_fail:" + jid, min_gap=1800, kind="error")
        finally:
            self.busy = None

    def _finish_run(self, run_id, ok, res, error=None):
        kw = {}
        if res:
            kw = {k: res.get(k) for k in ("cost_usd", "duration_ms", "num_turns", "input_tokens", "output_tokens", "cache_read_tokens", "cache_create_tokens")}
            kw["cage_breach"] = res.get("breach") or []
            kw["denials"] = res.get("denials") or []
        if error:
            kw["error"] = error
        try:
            self.state.finish_run(run_id, ok, **kw)
        except Exception as e:
            log.warning("dossier: runs 记帐失败: %s", e)

    def has_payment(self, jid):
        """付款地板: 唯一的算法住 wa_facts.stage_floor (daemon 与命令行各算一次会不一致)。回地板 stage 字串或 False。"""
        facts = self.load_facts()
        if facts is None or WF is None:
            return False
        try:
            return call_opt(WF.stage_floor, facts, jid, cfg=self.cfg)
        except Exception as e:
            log.warning("stage_floor 失败 %s: %s", jid, e)
            return False

    def call_claude(self, prompt, run_dir):
        """档案 run 的 claude: 跟分诊脑同一套笼子旗标; 读 init 的工具清单做破笼检查, 被挡的工具呼叫记下来, 用量进 runs 表 (kind dossier)。"""
        if self.dry_run:
            raise RunError("dry-run: 不叫 claude")
        if not self.brain.settings.is_file():
            raise RunError("claude-settings.json 不在 (%s): 先跑 wa_setup.py init" % self.brain.settings)
        b = self.cfg.get("brain") or {}
        timeout = int(self.d.get("run_timeout_seconds", 1200))
        args = self.brain.claude_base_args(self.d.get("model", "claude-opus-5"), int(self.d.get("max_turns", 24)))
        args += ["--append-system-prompt", DOSSIER_APPEND_SYSTEM, prompt]
        t0 = time.time()
        try:
            r = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout,
                               cwd=b.get("cwd") or str(self.brain.home / "brain-cwd"), env=self.brain.child_env())
        except subprocess.TimeoutExpired as te:
            out = te.stdout.decode("utf-8", "replace") if isinstance(te.stdout, bytes) else (te.stdout or "")
            (Path(run_dir) / "claude.stdout.jsonl").write_text(out or "", encoding="utf-8")
            raise RunError("档案 run 超时 %ds" % timeout) from None
        (Path(run_dir) / "claude.stdout.jsonl").write_text(r.stdout or "", encoding="utf-8")
        if r.stderr:
            (Path(run_dir) / "claude.stderr.txt").write_text(r.stderr, encoding="utf-8")
        done, breach = self.brain.parse_stream(r.stdout)
        if breach:
            log.warning("CAGE BREACH (档案 run): 载入了不该有的工具: %s", ", ".join(breach))
            self.notifier.send("🚨 [WA live] 档案 run cage breach: 载入了不该有的工具 %s" % ", ".join(breach), key="breach", min_gap=1800, kind="error")
        if done is None:
            raise RunError("claude -p 没有回 result (rc=%s): %s" % (r.returncode, (r.stderr or r.stdout or "")[-300:]))
        if done.get("is_error"):
            errs = "; ".join(str(x) for x in (done.get("errors") or []) if str(x).strip())
            raise RunError("claude 回 is_error: %s" % ((str(done.get("result") or "").strip()
                                                       or ": ".join(x for x in (str(done.get("subtype") or ""), errs) if x) or "no reason")[:300]))
        denied = [d.get("tool_name", d) if isinstance(d, dict) else d for d in (done.get("permission_denials") or [])]
        if denied:
            log.warning("设定档挡下了档案 run 的读取 (%s): %s", Path(run_dir).name, "; ".join(str(x) for x in denied)[:400])
        u = done.get("usage") or {}
        return {"text": done.get("result") or "", "duration_ms": done.get("duration_ms") or int((time.time() - t0) * 1000),
                "num_turns": done.get("num_turns"), "cost_usd": done.get("total_cost_usd"), "breach": breach,
                "denials": [d.get("tool_name", d) if isinstance(d, dict) else d for d in (done.get("permission_denials") or [])],
                "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
                "cache_read_tokens": u.get("cache_read_input_tokens"), "cache_create_tokens": u.get("cache_creation_input_tokens")}


# ---------------------------------------------------------------- 板子 (只绑本机 port, 由 tailscale serve 挂进主人自己的 tailnet)

_CAL_LABELS = {"ics": "Apple / Google / Outlook 日历 (.ics)", "lark": "Lark 日历", "none": ""}


def make_board_handler(daemon):
    import http.server

    class H(http.server.BaseHTTPRequestHandler):
        server_version = "wa_live/" + VERSION

        def log_message(self, fmt, *args):
            log.debug("board %s", fmt % args)

        def _query(self):
            return urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")

        def ui_payload(self):
            cfg = daemon.cfg
            V = Vocab(cfg)
            b = cfg.get("board") or {}
            br = cfg.get("brain") or {}
            cal = str((cfg.get("calendar") or {}).get("destination") or "none")
            if WCAL is None:
                cal = "none"
            vault = vault_dir(cfg)
            return {"lang": str(b.get("lang") or "zh"), "show_usd": bool(b.get("show_usd")), "vocab": V.ui(),
                    "calendar": cal, "calendar_label": _CAL_LABELS.get(cal, cal),
                    "features": {"dossier": bool(daemon.dossier.enabled and DC is not None),
                                 "digest": bool((cfg.get("digest") or {}).get("enabled", True) and WD is not None),
                                 "intake": bool((cfg.get("intake") or {}).get("enabled") and WI is not None),
                                 "voice": bool((cfg.get("voice") or {}).get("enabled", True) and WV is not None)},
                    "owner_label": owner_label(cfg), "vault_name": vault.name if vault is not None else "",
                    "context_days": int(br.get("context_days", 7)), "group_context_days": int(br.get("group_context_days", 3)),
                    "others_days": int(b.get("others_days", 3)), "others_limit": int(b.get("others_limit", 120)),
                    "stale_alert_hours": float((cfg.get("doorbell") or {}).get("stale_alert_hours", 6)),
                    "dry_run": bool(daemon.dry_run), "doorbell": bool(daemon.bell.enabled)}

        def board_payload(self):
            cfg = daemon.cfg
            b = cfg.get("board") or {}
            st = daemon.state.board_status()
            age = daemon.bell.wal_age()
            st["wal_age_hours"] = (age / 3600.0) if age is not None else None
            st["running"] = len(daemon.running)
            st["running_jids"] = sorted(daemon.running)
            rows = daemon.state.board_rows()
            for r in rows:
                try:
                    r["dossier"] = daemon.dossier.note_info(r["jid"]) if r.get("kind") == "direct" else None
                    r["dossier_busy"] = (daemon.dossier.busy == r["jid"])
                except Exception:
                    r["dossier"] = None; r["dossier_busy"] = False
            st["dossier_busy"] = daemon.dossier.busy
            seen = {r["jid"] for r in rows}
            others, total = [], 0
            try:
                others, total = daemon.state.recent_chats(int(b.get("others_days", 3)), int(b.get("others_limit", 120)), exclude=seen, with_total=True)
                for c in others:
                    conv = daemon.state.get_conversation(c["jid"])
                    c["draft"] = (conv or {}).get("draft") or ""
            except Exception as e:
                log.warning("recent_chats 失败: %s", e); others = []
            st["others_total"] = total
            return {"generated_at": now_iso(), "rows": rows, "others": others, "status": st, "ui": self.ui_payload()}

        def _send(self, code, body, ctype="application/json; charset=utf-8", head_only=False, headers=None):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if not head_only:
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def _page(self, page):
            try:
                html = Path(page).read_text(encoding="utf-8")
            except Exception as e:
                self._send(500, "board 页面读不到: %s" % e, "text/plain; charset=utf-8"); return
            # 首屏数据直接塞进页面: 手机上的内容拦截器或没有 fetch 的环境也能看; 之后的刷新才走 /api/board。
            boot = json.dumps(self.board_payload(), ensure_ascii=False, default=str).replace("</", "<\\/")
            html = html.replace("<!--BOOT-->", '<script id="boot" type="application/json">%s</script>' % boot, 1)
            self._send(200, html, "text/html; charset=utf-8")

        def _media(self, head_only=False):
            """图片 / 影片 / 语音: python (有 Full Disk Access) 读 Group Containers 里的档送出去。支援 HTTP Range (iPhone Safari 播 mp4 要 206) 与 HEAD。"""
            q = self._query()
            try:
                pk = int((q.get("pk") or ["0"])[0])
            except ValueError:
                pk = 0
            p, ct = daemon.state.media_file(pk) if pk else (None, None)
            if not p:
                self._send(404, "no media", "text/plain; charset=utf-8", head_only=head_only); return
            try:
                size = p.stat().st_size
            except Exception as e:
                self._send(500, "stat failed: %s" % e, "text/plain; charset=utf-8", head_only=head_only); return
            if size > MEDIA_MAX_BYTES:
                self._send(413, "too big (%d bytes)" % size, "text/plain; charset=utf-8", head_only=head_only); return
            start, end, code = 0, size - 1, 200
            rng = self.headers.get("Range")
            if rng:
                got = parse_range(rng, size)
                if got is None:
                    self._send(416, "range not satisfiable", "text/plain; charset=utf-8", head_only=head_only,
                               headers={"Content-Range": "bytes */%d" % size, "Accept-Ranges": "bytes"})
                    return
                if got != "ignore":
                    start, end = got
                    code = 206
            length = max(0, end - start + 1)
            self.send_response(code)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "private, max-age=86400")   # 媒体档不会变, 让手机快取
            if code == 206:
                self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
            self.end_headers()
            if head_only or length == 0:
                return
            try:
                with open(str(p), "rb") as f:
                    f.seek(start)
                    left = length
                    while left > 0:
                        chunk = f.read(min(MEDIA_CHUNK, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                log.warning("media %s 送到一半失败: %s", pk, e)

        def _calendar(self, path, head_only=False):
            """GET /calendar/<key>.ics: ics 日历去处建的 event 档 (Apple / Google / Outlook 都吃)。key 只准英数、底线、连字号。"""
            m = re.fullmatch(r"/calendar/([A-Za-z0-9_-]{1,100})\.ics", path)
            if not m:
                self._send(404, "not found", "text/plain; charset=utf-8", head_only=head_only); return
            key = m.group(1)
            p = None
            if WCAL is not None and hasattr(WCAL, "ics_file"):
                try:
                    p = WCAL.ics_file(daemon.cfg, key)
                except Exception as e:
                    log.warning("ics_file %s: %s", key, e)
            p = Path(p) if p else (live_dir(daemon.cfg) / "calendar" / (key + ".ics"))
            if not p.is_file():
                self._send(404, "no such event", "text/plain; charset=utf-8", head_only=head_only); return
            self._send(200, p.read_bytes(), "text/calendar; charset=utf-8", head_only=head_only,
                       headers={"Content-Disposition": 'inline; filename="%s.ics"' % key})

        def do_HEAD(self):
            path = self.path.split("?")[0]
            if path == "/health":
                self._send(200, "ok", "text/plain; charset=utf-8", head_only=True)
            elif path == "/media":
                self._media(head_only=True)
            elif path.startswith("/calendar/"):
                self._calendar(path, head_only=True)
            else:
                self._send(405, "", "text/plain; charset=utf-8", head_only=True, headers={"Allow": "GET"})

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                self._page(DAEMON_DIR / "board.html")
            elif path == "/api/board":
                self._send(200, json.dumps(self.board_payload(), ensure_ascii=False, default=str))
            elif path.startswith("/v/"):
                # 板子变体: live/variants/<name>.html, 跟正版同一份数据同一套 API, 手机开 /v/<name> 就能比; /v/ 列出有哪些。
                vdir = live_dir(daemon.cfg) / "variants"
                name = re.sub(r"[^A-Za-z0-9._-]", "", path[3:])
                if not name:
                    items = sorted(p.stem for p in vdir.glob("*.html")) if vdir.exists() else []
                    html = "<meta charset=utf-8><title>variants</title><h2>板子变体</h2><ul>" + "".join(
                        '<li><a href="/v/%s">%s</a></li>' % (n, n) for n in items) + "</ul><p><a href='/'>正版</a></p>"
                    self._send(200, html, "text/html; charset=utf-8"); return
                page = vdir / (name if name.endswith(".html") else name + ".html")
                if not page.exists() or ".." in name:
                    self._send(404, "no such variant", "text/plain; charset=utf-8"); return
                self._page(page)
            elif path == "/dismiss":
                # fetch 被挡时的退路: GET /dismiss?jid=… 然后跳回首页
                jid = (self._query().get("jid") or [""])[0]
                if jid:
                    k = daemon.state.dismiss(jid); log.info("板子: 已处理 %s (%d 行, GET)", jid, k)
                self.send_response(303); self.send_header("Location", "/"); self.send_header("Content-Length", "0"); self.end_headers()
            elif path == "/api/thread":
                q = self._query()
                jid = (q.get("jid") or [""])[0]
                try:
                    n = max(5, min(200, int((q.get("n") or ["40"])[0])))
                except ValueError:
                    n = 40
                if not jid:
                    self._send(400, json.dumps({"error": "jid?"})); return
                try:
                    self._send(200, json.dumps({"jid": jid, "messages": daemon.state.thread(jid, n)}, ensure_ascii=False))
                except Exception as e:
                    self._send(500, json.dumps({"error": str(e)[:300]}))
            elif path == "/media":
                self._media()
            elif path.startswith("/calendar/"):
                self._calendar(path)
            elif path == "/api/packet":
                # 板上「带去 Claude Code」: 这条对话的自足文字包 (text/plain), 板子拿去复制
                jid = (self._query().get("jid") or [""])[0]
                if not jid:
                    self._send(400, "jid?", "text/plain; charset=utf-8"); return
                try:
                    self._send(200, daemon.state.packet(jid), "text/plain; charset=utf-8")
                    log.info("板子: 交棒包 %s", jid)
                except Exception as e:
                    self._send(500, "packet failed: %s" % e, "text/plain; charset=utf-8")
            elif path == "/api/runs":
                self._send(200, json.dumps(daemon.state.recent_runs(30), ensure_ascii=False, default=str))
            elif path == "/api/digest":
                # 板上「昨天」一格读最近一份日报; 没有回 404 整格不显示
                d = None
                try:
                    d = WD.latest_digest(daemon.cfg) if WD is not None else None
                except Exception as e:
                    log.warning("latest_digest 失败: %s", e)
                if d:
                    self._send(200, json.dumps(d, ensure_ascii=False, default=str))
                else:
                    self._send(404, "no digest yet", "text/plain; charset=utf-8")
            elif path == "/health":
                self._send(200, "ok", "text/plain; charset=utf-8")
            else:
                # ⛔ 没有 GET /draft: 会改状态的 GET 不留 (存草稿一律走 POST /api/draft)
                self._send(404, "not found", "text/plain; charset=utf-8")

        def do_POST(self):
            path = self.path.split("?")[0]
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            raw = self.rfile.read(min(n, 1024 * 1024)) if n else b""
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                body = {}
            if not isinstance(body, dict):
                body = {}
            if path == "/api/dismiss" and body.get("jid"):
                k = daemon.state.dismiss(body["jid"])
                log.info("板子: 已处理 %s (%d 行)", body["jid"], k)
                self._send(200, json.dumps({"ok": True, "updated": k}))
            elif path == "/api/draft" and body.get("jid") is not None:
                draft = str(body.get("draft") or "")[:4000]
                k = daemon.state.set_draft(body["jid"], draft, name=body.get("name"), kind=body.get("kind"))
                log.info("板子: 改草稿 %s (%d 字)", body["jid"], len(draft))
                self._send(200, json.dumps({"ok": True, "updated": k, "link": wa_link(resolve_phone(body["jid"]), draft)}))
            elif path == "/api/dossier" and body.get("jid"):
                # 板上「开档 / 重写档」: 排一个档案 run (跳过同一人的间隔)
                jid = body["jid"]
                if not daemon.dossier.enabled:
                    self._send(200, json.dumps({"ok": False, "queued": False, "error": "人物档没开 (dossier.enabled=false)"}, ensure_ascii=False)); return
                if daemon.dry_run:
                    self._send(200, json.dumps({"ok": False, "queued": False, "error": "dry-run: 档案 run 不排队"}, ensure_ascii=False)); return
                c = daemon.state.get_conversation(jid) or {}
                # 钮在没档的人身上叫「开档」: 理由也写开档, 不然 filing log 与 dossier_jobs 把新档记成重写
                try:
                    has_note = daemon.dossier.note_for_speaker(jid) is not None
                except Exception:
                    has_note = True
                ok = daemon.dossier.enqueue(jid, c.get("name") or jid, "板上要求重写" if has_note else "板上要求开档")
                self._send(200, json.dumps({"ok": True, "queued": bool(ok), "note": "已在排队或正在跑" if not ok else "排进去了, 几分钟后板上会更新"}, ensure_ascii=False))
            elif path == "/api/field" and body.get("jid") and body.get("field") in ("world", "stage"):
                # 板上改 world / stage: 主人是唯一能把 stage 往回拨的人; 改成非生意世界时后端当场清 stage
                k = daemon.state.set_field(body["jid"], body["field"], str(body.get("value") or "").strip())
                c = daemon.state.get_conversation(body["jid"]) or {}
                log.info("板子: 改 %s=%s %s (%d 行)", body["field"], body.get("value"), body["jid"], k)
                self._send(200 if k else 400, json.dumps({"ok": bool(k), "updated": k, "world": c.get("world"), "stage": c.get("stage")}, ensure_ascii=False))
            elif path == "/api/calendar" and body.get("jid"):
                # 板上「加进日历」: 板 → daemon → 日历去处 (ics 档 / Lark), ⛔ 不经 LLM。同一个约建过就回原链接不重建。
                jid = body["jid"]
                if WCAL is None:
                    self._send(200, json.dumps({"ok": False, "error": "wa_calendar 模组没载入"}, ensure_ascii=False)); return
                c = daemon.state.get_conversation(jid) or {}
                try:
                    hint = json.loads(c.get("calendar_hint") or "null")
                    done = json.loads(c.get("calendar_done") or "null")
                except Exception:
                    hint, done = None, None
                key = WCAL.hint_key(hint) if hint else None
                name = c.get("name") or jid
                if done and key and done.get("hint_key") == key:
                    log.info("板子: 加进日历 %s → already (同一个约建过)", name)
                    self._send(200, json.dumps({"ok": True, "url": done.get("url"), "already": True}, ensure_ascii=False)); return
                form = WCAL.validate_hint({"title": body.get("title"), "date": body.get("date"), "time": body.get("time"),
                                           "duration_min": body.get("duration_min"), "place": body.get("place")})
                if not form:
                    self._send(200, json.dumps({"ok": False, "error": "标题或日期不合法"}, ensure_ascii=False)); return
                # 按「建」那一刻脑刚好清掉提示时, 用表单自己的键记「建过」, 免得存 hint_key=null 之后钮永远不再亮;
                # 没有提示时一样拿表单的键比「建过的那个」, 同一个日期钟点再按一次回原链接, 不再叫日历去处
                key = key or WCAL.hint_key(form)
                if done and key and done.get("hint_key") == key:
                    log.info("板子: 加进日历 %s %s %s → already (同一个约建过)", name, form["date"], form.get("time") or "(全天)")
                    self._send(200, json.dumps({"ok": True, "url": done.get("url"), "already": True}, ensure_ascii=False)); return
                phone = resolve_phone(jid) if not jid.endswith("@g.us") else None
                note = WCAL.compose_description(str(body.get("note") or "")[:500], name, phone or "")
                time_raw = str(body.get("time") or "").strip()
                try:
                    # 钟点传主人在表单里打的原字: 打错了 create_event 回错, 不静默变全天 (对脑宽容, 对人不宽容)
                    idem = str(uuid.uuid5(uuid.NAMESPACE_URL, "wa-live|%s|%s|%s" % (jid, form["date"], time_raw)))
                    res = WCAL.create_event(daemon.cfg, form["title"], form["date"], time_raw, duration_min=form["duration_min"],
                                            place=form.get("place") or "", note=note, dry_run=bool(daemon.dry_run), idempotency_key=idem)
                except Exception as e:
                    res = {"ok": False, "error": str(e)[:300]}
                if res.get("ok") and not daemon.dry_run:
                    daemon.state.set_calendar_done(jid, {"at": now_iso(), "url": res.get("url"), "title": form["title"], "hint_key": key, "event_id": res.get("event_id")})
                outcome = ("already" if res.get("already") else "ok") if res.get("ok") else res.get("error")
                log.info("板子: 加进日历 %s %s %s → %s", name, form["date"], form.get("time") or "(全天)", outcome)
                reply = {"ok": bool(res.get("ok")), "url": res.get("url"), "error": res.get("error")}
                if res.get("ok") and res.get("already"):
                    reply["already"] = True
                self._send(200, json.dumps(reply, ensure_ascii=False))
            elif path == "/api/run" and body.get("jid"):
                # 板上「现在判断」: 拿这条对话最近 3 封 inbound 当 burst 跑一次 (跟 commands/test-run 同一条路)。
                # 闸不放行的对话 (skip 群、没列的群、ignored) 先回 needs_confirm, 板上确认后带 confirm: true 再送才跑。
                jid = body["jid"]
                if jid in daemon.running:
                    self._send(200, json.dumps({"ok": True, "already": True})); return
                kind = "group" if jid.endswith("@g.us") else "direct"
                ok_gate, why = daemon.bell.gate_for(jid, kind)
                if not ok_gate and not body.get("confirm"):
                    self._send(200, json.dumps({"ok": False, "needs_confirm": True, "gate": why,
                                                "error": "这条对话不在闸内 (%s), 确认要判才会送去脑" % why}, ensure_ascii=False)); return
                p = daemon.bell.burst_for_test(jid)
                if not p:
                    self._send(200, json.dumps({"ok": False, "error": "这条对话没有 inbound 讯息"}, ensure_ascii=False)); return
                if body.get("full"):
                    p["ground"] = True   # 板上「全量重判」: 不信 summary, 用全窗原文重写记忆
                daemon.dispatch(p, source="board-full" if body.get("full") else "board")
                log.info("板子: 手动跑 %s%s%s", p["name"], " (全量重判)" if body.get("full") else "", " (闸外, 主人确认)" if not ok_gate else "")
                self._send(200, json.dumps({"ok": True}))
            else:
                self._send(400, json.dumps({"ok": False, "error": "bad request"}))

    return H


def start_board(daemon):
    import http.server
    b = daemon.cfg.get("board") or {}
    if not b.get("enabled", True):
        log.info("板子关闭 (config board.enabled=false)"); return None
    bind = str(b.get("bind") or "127.0.0.1")
    try:
        srv = http.server.ThreadingHTTPServer((bind, int(b.get("port", 8787))), make_board_handler(daemon))
    except OSError as e:
        log.error("板子起不来 (%s:%s): %s", bind, b.get("port"), e); return None
    srv.daemon_threads = True
    daemon.board_port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    if bind not in ("127.0.0.1", "localhost", "::1"):
        log.warning("板子绑在 %s 不是本机位址: 板子没有登入, 同一个网路的人都进得来; 改回 127.0.0.1 再用 tailscale serve", bind)
    log.info("板子在 http://%s:%s/ (tailscale serve 把它挂上 tailnet)", bind, daemon.board_port)
    return srv


# ---------------------------------------------------------------- daemon

class Daemon(object):
    def __init__(self, cfg, dry_run=False):
        self.cfg = cfg
        self.dry_run = dry_run
        # 模组 import 发生在 log 设好之前: 失败原因这里再写进 wa_live.log, 看 log 就知道哪块停了
        for name, err in sorted(_IMPORT_ERRORS.items()):
            log.error("模组 %s import 失败 (那一块功能停): %s", name, err)
        missing = [n for n, m in _MOD.items() if m is None] + (["dossier_core"] if DC is None else [])
        log.info("模组: %s", ("缺 " + ", ".join(missing)) if missing else "全部载入")
        self.state = State(state_db_path(cfg), cfg)
        self.notifier = Notifier(cfg, dry_run=dry_run)
        self.bell = Doorbell(cfg, self.state, self.notifier)
        self.brain = Brain(cfg, self.state, self.notifier, dry_run=dry_run)
        self.dossier = Dossier(cfg, self.state, self.brain, self.notifier, dry_run=dry_run)
        self.brain.dossier = self.dossier
        if (cfg.get("backfill") or {}).get("respect_gate", True):
            self.brain.gate = self.bell.gate_for
        self.running = set(); self.threads = []
        self.stop = threading.Event()
        self.board = None
        self.board_port = None
        self.commands_dir = live_dir(cfg) / "commands"
        self.commands_dir.mkdir(parents=True, exist_ok=True)
        self._mem = {}
        # 夜里排班: 名字 · 几点 · 做什么 · 开没开。启动时已经过了点就把今天记成跑过, 不补跑 (要现在跑就丢 commands/<名>-now.json)。
        f = cfg.get("facts") or {}; ss = cfg.get("second_source") or {}; bk = cfg.get("backup") or {}; dg = cfg.get("digest") or {}
        self.daily = [("facts", f.get("at", "03:00"), self.job_facts, bool(f.get("enabled", False)) and WF is not None),
                      ("second_source", ss.get("at", "03:10"), self.job_second_source, self._second_source_enabled()),
                      ("backup", bk.get("at", "03:30"), self.job_backup, bool(bk.get("enabled", False)) and WB is not None),
                      ("digest", dg.get("at", "08:00"), self.job_digest, bool(dg.get("enabled", True)) and WD is not None)]
        self.daily.sort(key=lambda x: self._hhmm(x[1]))
        self.daily_busy = set()
        self.backfill_busy = False
        now = dt.datetime.now()
        for name, at, _, enabled in self.daily:
            hh, mm = self._hhmm(at)
            if (now.hour, now.minute) >= (hh, mm) and self._mark_get("daily_%s_last" % name) != now.date().isoformat():
                self._mark_set("daily_%s_last" % name, now.date().isoformat())
                if enabled:
                    log.info("排班 %s: 启动时已过 %s, 今天记成跑过 (要现在跑就丢 commands/%s-now.json)", name, at, name.replace("_", "-"))
            if not enabled:
                log.info("排班 %s 没开 (config enabled=false 或模组没载入)", name)
        try:
            self.brain.refresh_reader_configs()
        except Exception as e:
            log.warning("读取器 config 刷新失败 (继续): %s", e)

    def _second_source_enabled(self):
        if WSS is None:
            return False
        try:
            return bool(WSS.enabled(self.cfg)) if hasattr(WSS, "enabled") else bool((self.cfg.get("second_source") or {}).get("enabled"))
        except Exception:
            return False

    @staticmethod
    def _hhmm(s):
        try:
            hh, mm = [int(x) for x in str(s).split(":")]
            if 0 <= hh < 24 and 0 <= mm < 60:
                return hh, mm
        except Exception:
            pass
        log.warning("排班时刻 %r 看不懂, 当 03:00", s)
        return 3, 0

    def _mark_get(self, key):
        return self._mem.get(key) if self.dry_run else self.state.get_meta(key)

    def _mark_set(self, key, value):
        if self.dry_run:
            self._mem[key] = value          # ⛔ 试跑不动真状态库的「今天跑过没」, 不然真 daemon 当天的排班会被吃掉
        else:
            self.state.set_meta(key, value)

    def daily_tick(self):
        """约每分钟叫一次: 到点且今天没跑过的排班, 按时刻顺序在同一条线程里一个接一个跑 (机器睡过几个点, 醒来也照 事实 → 第二来源 → 夜备 → 日报 的先后)。"""
        now = dt.datetime.now(); today = now.date().isoformat()
        due = []
        for name, at, fn, enabled in self.daily:
            if not enabled or name in self.daily_busy:
                continue
            hh, mm = self._hhmm(at)
            if (now.hour, now.minute) < (hh, mm) or self._mark_get("daily_%s_last" % name) == today:
                continue
            self._mark_set("daily_%s_last" % name, today)
            due.append((name, fn))
        if due:
            self.run_chain(due)

    def _run_one(self, name, fn, spec):
        t0 = time.time()
        try:
            fn(spec or {})
            log.info("排班 %s 完成 (%.0fs)", name, time.time() - t0)
        except Exception as e:
            log.exception("排班 %s 失败", name)
            self.notifier.send("⚠️ [WA live] 排班 %s 失败: %s" % (name, str(e)[:300]), key="daily_fail:" + name, min_gap=3600, kind="error")
        finally:
            self.daily_busy.discard(name)

    def run_chain(self, jobs):
        for name, _ in jobs:
            self.daily_busy.add(name)

        def _go():
            for name, fn in jobs:
                self._run_one(name, fn, {})
        threading.Thread(target=_go, daemon=True).start()

    def run_daily(self, name, fn, spec):
        if name in self.daily_busy:
            log.warning("排班 %s 正在跑, 略过", name); return
        self.daily_busy.add(name)
        threading.Thread(target=self._run_one, args=(name, fn, spec), daemon=True).start()

    # ---- 事实 worker: 事实来源 → live/facts.json; 付过钱有对话没档的排进开档队列; 付款地板抬板上已判过的行
    def job_facts(self, spec):
        if WF is None:
            raise RuntimeError("wa_facts 模组没载入")
        dry = bool(spec.get("dry_run")) or self.dry_run
        facts = WF.build_facts(self.cfg)
        mm = facts.get("mismatch") or {}
        queue = list(mm.get("paid_chat_no_note_queued") or [])
        if dry:
            log.info("facts: dry-run, 不写 facts.json、不排开档、不抬 stage · people %d · 付款没对话 %d · 付款有对话没档 %d",
                     len(facts.get("people") or {}), len(mm.get("paid_no_chat") or []), len(queue))
            return
        path = live_dir(self.cfg) / "facts.json"
        if WC is not None and hasattr(WC, "write_json_atomic"):
            WC.write_json_atomic(path, facts)
        else:
            path.write_text(json.dumps(facts, ensure_ascii=False, indent=1), encoding="utf-8")
        queued = 0
        cap = int((self.cfg.get("dossier") or {}).get("nightly_cap", 8))   # 一晚有上限, 不然十个连跑撞上夜备
        idx_err = self.dossier.index().get("error") if self.dossier.enabled else None
        if idx_err:
            log.error("facts: People 房读不到 (%s), 这一轮不排开档", idx_err)
        elif self.dossier.enabled:
            for p in queue:
                if queued >= cap:
                    log.info("facts: 排队开档到上限 %d, 其余 %d 人明晚再排", cap, len(queue) - queued); break
                jid = p.get("wa_jid")
                if not jid:
                    continue
                if self.dossier.note_for_speaker(jid) is not None:
                    log.info("facts: %s 其实有档 (档头是另一种 jid 形或电话), 不排开档", p.get("name") or jid)
                    continue
                if self.dossier.enqueue(jid, p.get("name") or jid, "开新档: 事实表, 付过钱有对话没档"):
                    queued += 1
        log.info("facts: people %d · paid %d · 对不上 %d/%d/%d · 排队开档 %d",
                 len(facts.get("people") or {}), sum(1 for v in (facts.get("people") or {}).values() if isinstance(v, dict) and v.get("paid")),
                 len(mm.get("paid_no_chat") or []), len(mm.get("unparsable_phone") or []), len(queue), queued)
        # 付款地板套到板上已判过的行 (不等下一封讯息): 生意世界 · stage 在地板之下 → 抬上去, stage_auto=1, 记 stage_prev; updated_at 不动 (日报的「要回」不漏)
        V = Vocab(self.cfg)
        lifted = 0
        with self.state.lock:
            rows = self.state.conn.execute("SELECT jid, name, stage, world FROM conversations WHERE kind='direct' AND stage IS NOT NULL").fetchall()
        for r in rows:
            if not V.is_business(r["world"]):
                continue
            try:
                floor = call_opt(WF.stage_floor, facts, r["jid"], cfg=self.cfg)
                if floor and r["stage"] in V.ladder and floor in V.ladder and V.stage_index(r["stage"]) < V.stage_index(floor):
                    with self.state.lock:
                        self.state.conn.execute("UPDATE conversations SET stage_prev=stage, stage=?, stage_auto=1, stage_changed_at=? WHERE jid=?",
                                                (floor, now_iso(), r["jid"]))
                        self.state.conn.commit()
                    lifted += 1
                    log.info("facts: %s 板上 stage %s → %s (%s 有付款记录)", r["name"], r["stage"], floor, facts_source_label(self.cfg))
            except Exception as e:
                log.warning("facts: 地板套用失败 %s: %s", r["jid"], e)
        if lifted:
            log.info("facts: 付款地板抬了 %d 行", lifted)

    # ---- 夜备: live/ 的状态到主人指定的夹, 留几份; 结果落 live/backup-last.json 给日报
    def job_backup(self, spec):
        if WB is None:
            raise RuntimeError("wa_backup 模组没载入")
        dry = bool(spec.get("dry_run")) or self.dry_run
        r = call_opt(WB.run_backup, self.cfg, dest=spec.get("dest"), dry_run=dry, out=lambda m: log.info("backup: %s", m))
        if not r.get("ok"):
            raise RuntimeError(r.get("error") or "夜备失败 (没给原因)")
        log.info("backup: %s · %d 档 · %.1f MB%s", r.get("dest"), r.get("files") or 0, (r.get("bytes") or 0) / 1e6, " (dry-run)" if dry else "")

    # ---- 日报: 唯一的排程推播例外, 全空也发一行「昨天安静」; dry-run 只组不写不推
    def job_digest(self, spec):
        if WD is None:
            raise RuntimeError("wa_digest 模组没载入")
        day = dt.date.fromisoformat(spec["for"]) if spec.get("for") else (dt.date.today() - dt.timedelta(days=1))
        d = WD.build_digest(self.cfg, day)
        nlines = len(d.get("lines") or [])
        if bool(spec.get("dry_run")) or self.dry_run:
            log.info("digest %s: dry-run, 只组不写不推 (%d 行)", d.get("date"), nlines)
            return
        dg = self.cfg.get("digest") or {}
        if not spec.get("send", True) or not dg.get("push", True) or not self.notifier.wants("digest"):
            WD.write_digest(self.cfg, d)
            log.info("digest %s: 只写档不推 (%d 行; send=%s · digest.push=%s · notify.mode=%s)", d.get("date"), nlines,
                     spec.get("send", True), dg.get("push", True), self.notifier.mode)
            return
        ok = False
        if hasattr(WD, "send_digest"):
            try:
                r = WD.send_digest(self.cfg, d)          # 写档再经唯一的出口推短版
                ok = bool(r[0]) if isinstance(r, (tuple, list)) else bool(r)
            except Exception as e:
                log.warning("digest: send_digest 例外: %s", e)
        else:
            WD.write_digest(self.cfg, d)
            ok = self.notifier.send(d.get("text_short") or d.get("text") or "", parse_mode=("HTML" if d.get("text_short") else None), kind="digest")
        log.info("digest %s: %s (%d 行%s)", d.get("date"), "已推" if ok else "没推出去 (看出口的 log)", nlines, ", 安静日" if d.get("empty") else "")

    # ---- 第二讯息来源夜拉: 白名单会话昨天的讯息 → 一次模型 → 有来源 id 的人物档近况 + intake; ⛔ 不上板
    def job_second_source(self, spec):
        if WSS is None:
            raise RuntimeError("wa_second_source 模组没载入")
        if bool(spec.get("dry_run")) or self.dry_run:
            fetched = WSS.fetch_day(self.cfg, day=spec.get("day"))
            log.info("second_source: 拉 %d 会话 %d 封 · dry-run 不叫模型 (errors %d)", fetched.get("n_chats", 0), fetched.get("n_msgs", 0), len(fetched.get("errors") or []))
            return
        run_dir = self.brain.runs_dir / ("second-source-%s" % dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
        run_dir.mkdir(parents=True, exist_ok=True)

        def call(prompt):
            (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
            res = self.brain.call_claude_recorded(prompt, run_dir, "second_source")
            if res.get("is_error"):
                raise RunError("claude 回 is_error: %s" % str(res.get("text"))[:300])
            (run_dir / "result.txt").write_text(res.get("text") or "", encoding="utf-8")
            return res.get("text") or ""

        it = self.cfg.get("intake") or {}
        if WI is not None and it.get("enabled") and intake_buffer_path(self.cfg) is not None:
            captures_fn = WI.append_captures
        else:
            captures_fn = (lambda *a, **k: 0)          # intake 关着: 第二来源的 captures 也不落
        s = call_opt(WSS.run_nightly, self.cfg, call, self.dossier.append_recent, captures_fn, day=spec.get("day"),
                     log=lambda m: log.info("second_source: %s", m))
        log.info(WSS.log_line(s) if hasattr(WSS, "log_line") else "second_source: %s" % s)
        if not s.get("ok"):
            raise RuntimeError("第二讯息来源夜拉 ✗: %s" % "; ".join(str(x) for x in (s.get("errors") or []))[:300])

    # ---- 摘要回填 (一次性, 指令触发): commands/backfill-summaries.json 可带 {"limit": N, "dry_run": true}
    def start_backfill(self, spec):
        if self.backfill_busy:
            log.warning("backfill: 已经在跑, 这次指令略过"); return
        if WBF is None:
            log.error("backfill: wa_backfill 模组没载入"); return
        self.backfill_busy = True
        bcfg = self.cfg.get("backfill") or {}
        brain_cfg = self.cfg.get("brain") or {}
        timeout = int(brain_cfg.get("sweep_timeout_seconds", 1500))
        dry = bool(spec.get("dry_run")) or self.dry_run

        def sweep_fn(jid, days, out_json):
            self.brain.sweep(["--only-jid", jid, "--days", str(days), "--out", str(out_json)], timeout, label="backfill-sweep", key="backfill")
            return Path(out_json).with_suffix(".md").read_text(encoding="utf-8")

        def call(prompt, run_dir):
            if dry:
                raise RunError("dry-run: 不叫 claude")
            # 一批约 7 万字, 给至少 900 秒; 回合上限用 backfill.max_turns (开图一张一回合, brain 的 12 回合不够一批)
            return self.brain.call_claude_recorded(prompt, run_dir, "backfill", timeout=max(int(brain_cfg.get("run_timeout_seconds", 600)), 900),
                                                   max_turns=int(bcfg.get("max_turns", 20)))

        def _go():
            t0 = time.time()
            try:
                r = WBF.run_backfill(self.cfg, call, sweep_fn, days=int(bcfg.get("days", 30)), min_msgs=int(bcfg.get("min_msgs", 10)),
                                     limit=spec.get("limit"), batch_size=int(bcfg.get("batch_size", 10)), dry_run=dry,
                                     after_batch=(None if dry else (lambda: self.state.write_summaries(self.brain.live / "summaries.md", gate=self.brain.gate))),
                                     log=lambda m: log.info("backfill: %s", m))
                log.info("backfill 完成 (%.0fs): 候选 %s · %s 批 · 写 %s 条 · 失败 %d 条%s", time.time() - t0, r.get("candidates"), r.get("batches"), r.get("written"),
                         len(r.get("failed_jids") or []), " (dry-run)" if dry else "")
            except Exception:
                log.exception("backfill 失败")
                self.notifier.send("⚠️ [WA live] 摘要回填失败, 看 wa_live.log", key="backfill_fail", min_gap=3600, kind="error")
            finally:
                self.backfill_busy = False
        threading.Thread(target=_go, daemon=True).start()

    def due(self, p, now):
        d = self.cfg.get("debounce") or {}
        return (now - p["last_at"] >= float(d.get("quiet_seconds", 45))) or (now - p["first_at"] >= float(d.get("max_wait_seconds", 180)))

    def dispatch(self, p, source="live"):
        self.running.add(p["jid"])
        t = threading.Thread(target=self.brain.run, args=(p, self.running, source), daemon=True)
        t.start(); self.threads.append(t)
        self.threads = [x for x in self.threads if x.is_alive()]

    def schedule(self):
        now = time.time()
        for p in self.state.pending_all():
            if len(self.running) >= int((self.cfg.get("debounce") or {}).get("global_concurrency", 2)):
                return
            if p["jid"] in self.running or not self.due(p, now):
                continue
            # ⛔ 不在这里 pop: pending 行留到 run 收尾 (consume_pending) 才清, daemon 中途重启不会把 burst 弄丢。
            self.dispatch(p)

    def probe_claude(self, spec=None):
        """commands/probe-claude.json: 用跟正式 run 同一套笼子旗标 (但 --max-turns 2、没有 --append-system-prompt) 叫一次最小的 claude -p,
        90 秒没回就 sample 它的堆叠再杀掉。专治「claude 在 launchd 底下卡死零输出」。
        指令档可带 {"drop": ["--flag", ...], "add": ["--flag", "value", ...], "label": "x"} 做二分。"""
        if self.dry_run:
            log.info("probe-claude: dry-run 不叫 claude"); return
        spec = spec or {}
        b = self.cfg.get("brain") or {}
        run_dir = self.brain.runs_dir / ("probe-%s%s" % (dt.datetime.now().strftime("%Y%m%d-%H%M%S"),
                                                        ("-" + safe_name(spec["label"])) if spec.get("label") else ""))
        run_dir.mkdir(parents=True, exist_ok=True)
        base = self.brain.claude_base_args(b.get("model", "claude-sonnet-5"), 2)
        drop = set(spec.get("drop") or [])
        args = []; skip_value = False
        for a in base:
            if skip_value:
                skip_value = False
                if not a.startswith("--"):
                    continue
            if a in drop:
                skip_value = True
                continue
            args.append(a)
        args += [str(x) for x in (spec.get("add") or [])]
        args.append("只回一个 JSON 物件 {\"ok\": true, \"model\": \"你是哪个模型\"}, 不要别的字。")
        (run_dir / "cmd.txt").write_text(" ".join(shlex.quote(a) for a in args) + "\n", encoding="utf-8")
        log.info("probe-claude 开始 (%s)", run_dir.name)
        out_f = open(str(run_dir / "stdout.jsonl"), "w"); err_f = open(str(run_dir / "stderr.txt"), "w")
        try:
            p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=out_f, stderr=err_f, cwd=b.get("cwd") or str(self.brain.home / "brain-cwd"),
                                 env=self.brain.child_env())
        except Exception as e:
            out_f.close(); err_f.close()
            log.error("probe-claude 起不来: %s", e); return
        t0 = time.time(); rc = None
        while time.time() - t0 < 90:
            rc = p.poll()
            if rc is not None:
                break
            time.sleep(1)
        if rc is None:
            try:
                s = subprocess.run(["/usr/bin/sample", str(p.pid), "3"], capture_output=True, text=True, timeout=40)
                (run_dir / "sample.txt").write_text(s.stdout or s.stderr or "", encoding="utf-8")
            except Exception as e:
                (run_dir / "sample.txt").write_text("sample 失败: %s" % e, encoding="utf-8")
            p.kill(); rc = "killed after 90s"
        out_f.close(); err_f.close()
        log.info("probe-claude 结束 rc=%s, %.0fs, stdout %d 字 -> %s", rc, time.time() - t0,
                 (run_dir / "stdout.jsonl").stat().st_size, run_dir)

    def _take(self, pattern):
        """认得的命令档: 读参数 (坏 JSON 当 {}), 马上删掉, 回 [(档名, spec)]。"""
        out = []
        for f in sorted(self.commands_dir.glob(pattern)):
            try:
                spec = json.loads(f.read_text(encoding="utf-8") or "{}")
            except Exception:
                spec = {}
            try:
                f.unlink()
            except Exception:
                pass
            out.append((f.name, spec if isinstance(spec, dict) else {}))
        return out

    def commands(self):
        for _, spec in self._take("backfill-summaries*.json"):
            log.info("backfill-summaries 指令: %s", spec or "{}")
            self.start_backfill(spec)
        for name, fn in (("facts", self.job_facts), ("second_source", self.job_second_source), ("backup", self.job_backup), ("digest", self.job_digest)):
            for _, spec in self._take("%s-now*.json" % name.replace("_", "-")):
                log.info("%s-now 指令: %s", name.replace("_", "-"), spec or "{}")
                self.run_daily(name, fn, spec)
        for _, spec in self._take("probe-claude*.json"):
            threading.Thread(target=self.probe_claude, args=(spec,), daemon=True).start()
        for _ in self._take("notify-test*.json"):
            # 验收出口: echo '{}' > live/commands/notify-test.json → 手机该收到一句 (照 notify.mode 判, off 时不发)
            ok = self.notifier.send("🧪 [WA live] notify-test: 出口通了 (%s)" % now_iso(), kind="error")
            log.info("notify-test: %s", "已发" if ok else "没发出去 (mode=off、dry-run 或出口没设好, 看上面的 log)")
        for fname, _ in self._take("prewarm-*.json"):
            try:
                days = int(fname[len("prewarm-"):-len(".json")])
            except ValueError:
                days = int((self.cfg.get("brain") or {}).get("context_days", 7))
            threading.Thread(target=self.brain.prewarm, args=(days,), daemon=True).start()
        for fname, _ in self._take("test-run.*.json"):
            jid = fname[len("test-run."):-len(".json")]
            p = self.bell.burst_for_test(jid)
            if not p:
                log.warning("test-run: %s 没有 inbound 讯息", jid); continue
            if jid in self.running:
                log.warning("test-run: %s 正在跑, 略过", jid); continue
            log.info("test-run 指令: %s", p["name"])
            self.dispatch(p, source="test-run")

    def stale_check(self):
        age = self.bell.wal_age()
        hours = float((self.cfg.get("doorbell") or {}).get("stale_alert_hours", 6))
        if age is not None and age > hours * 3600 and time.time() - self.bell.stale_notified > 6 * 3600:
            self.bell.stale_notified = time.time()
            host = (platform.node() or "").split(".")[0] or "这台 Mac"
            msg = "⚠️ [WA live] WhatsApp 的数据库已经 %.1f 小时没动, %s 上的 WhatsApp Desktop 可能掉线了。" % (age / 3600, host)
            log.warning(msg); self.notifier.send(msg, key="stale", kind="error")

    def minute_tick(self):
        self.dossier.nightly()
        self.daily_tick()
        self.stale_check()
        if WV is not None and (self.cfg.get("voice") or {}).get("enabled", True):
            try:
                k = WV.close_stale(str(state_db_path(self.cfg)), str(voice_pairs_path(self.cfg)))   # 开对超过 30 分钟就关
                if k:
                    log.info("语气对: 关了 %d 个 (30 分钟到)", k)
            except Exception as e:
                log.warning("语气对 close_stale 失败: %s", e)

    def rules_tick(self):
        if self.dry_run:
            return                       # 试跑不改真状态库的 stage
        sr = self.cfg.get("stage_rules") or {}
        n_ = self.state.stage_rules(int(sr.get("parked_after_days", 21)), bool(sr.get("group_date_done", False)))
        if n_:
            log.info("stage 时间规则改了 %d 行", n_)

    def loop(self):
        d = self.cfg.get("debounce") or {}
        log.info("wa_live %s 起跑 · model=%s · debounce %ss/%ss · 并发 %s · dry_run=%s · 门铃%s", VERSION, (self.cfg.get("brain") or {}).get("model"),
                 d.get("quiet_seconds", 45), d.get("max_wait_seconds", 180), d.get("global_concurrency", 2), self.dry_run,
                 "开" if self.bell.enabled else "关 (不自动叫脑, 命令档与板子照常)")
        self.bell.ensure_watermark()
        self.board = start_board(self)
        poll = float((self.cfg.get("doorbell") or {}).get("poll_seconds", 2) or 2)
        last_minute = None
        last_rules = None
        while not self.stop.is_set():
            try:
                n, passed = self.bell.scan()
                if n:
                    log.info("门铃: 新行 %d, 过闸 %d, 水位 %s", n, passed, self.state.get_meta("last_pk"))
                self.schedule()
                self.commands()
                self.dossier.tick()
                mono = time.monotonic()
                # 慢节拍照秒数算, 跟 poll_seconds 脱钩: 约每分钟 (夜批 · 排班 · 库太久没动 · 关语气对), 每十分钟 (stage 时间规则)
                if last_minute is None or mono - last_minute >= 60:
                    last_minute = mono
                    self.minute_tick()
                if last_rules is None or mono - last_rules >= 600:
                    last_rules = mono
                    self.rules_tick()
            except Exception:
                log.exception("主循环例外 (继续)")
            self.stop.wait(poll)
        log.info("收到停止讯号, 等 %d 个 run 收尾", len(self.running))
        for t in self.threads:
            t.join(timeout=60)


# ---------------------------------------------------------------- cli

class _PrivateLogHandler(RotatingFileHandler):
    """wa_live.log 里有对话名: 每次开档 (含轮替出来的新档) 都设 0600。"""

    def _open(self):
        stream = RotatingFileHandler._open(self)
        try:
            os.chmod(self.baseFilename, 0o600)
        except Exception:
            pass
        return stream


def setup_logging(live, stream=None):
    live = Path(live)
    live.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    h = _PrivateLogHandler(str(live / "wa_live.log"), maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    h.setFormatter(fmt)
    log.setLevel(logging.INFO)
    log.addHandler(h)
    # 终端机里跑才同时印到 stderr; launchd 底下不重复印 (stdout / stderr 档不轮替, 会无限长)
    use_stream = stream if stream is not None else sys.stderr.isatty()
    if use_stream:
        s = logging.StreamHandler(sys.stderr); s.setFormatter(fmt)
        log.addHandler(s)
    return h


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None, help="live/config.json 的路径 (默认: 环境变数 WA_LIVE_CONFIG, 再来 ~/WhatsApp-Live/live/config.json)")
    ap.add_argument("--status", action="store_true", help="印水位、待跑的 burst、最近的 run, 不起 daemon")
    ap.add_argument("--scan-once", action="store_true", help="门铃 + 闸跑一遍, 不起脑 (会写状态库, 验收用抛弃式 config)")
    ap.add_argument("--dry-run", action="store_true", help="daemon 整条线, 但不叫 claude、不推播、不写 vault, 排班跑试跑模式")
    ap.add_argument("--selftest", action="store_true", help="假 WhatsApp 库 + 假状态库 + 假设定跑自测, 只写暂存夹")
    args = ap.parse_args(argv)
    # 这个进程写的每个档 (run 夹里的对话包与提示、状态库、log、摘要表) 都含别人的对话: 一律只有本人读得到 (档 0600, 夹 0700)
    os.umask(0o077)
    if args.selftest:
        return selftest()
    cfg = load_config(Path(args.config).expanduser() if args.config else default_config_path())
    setup_logging(live_dir(cfg))
    # launchd 在任何程式码跑之前就用预设 umask 开好这两个 log (0644), 这里补成 0600 (傍晚那支 wa_intake 每次跑也补自己的)
    for _log_name in ("stderr.log", "intake-evening.log"):
        with contextlib.suppress(OSError):
            os.chmod(str(Path(live_dir(cfg)) / _log_name), 0o600)
    if args.status:
        st = State(state_db_path(cfg), cfg)
        with st.lock:
            needs = [dict(r) for r in st.conn.execute("""SELECT jid,name,world,stage,priority,reason,updated_at FROM conversations
                WHERE needs_me=1 ORDER BY updated_at DESC LIMIT 20""").fetchall()]
        print(json.dumps({"version": VERSION, "last_pk": st.get_meta("last_pk"), "pending": st.pending_all(),
                          "recent_runs": st.recent_runs(10), "needs_me": needs}, ensure_ascii=False, indent=1, default=str))
        return 0
    if args.scan_once:
        d = Daemon(cfg, dry_run=True)
        d.bell.ensure_watermark()
        n, passed = d.bell.scan(force=True)
        print(json.dumps({"new_rows": n, "passed": passed, "last_pk": d.state.get_meta("last_pk"), "pending": d.state.pending_all()},
                         ensure_ascii=False, indent=1))
        return 0
    d = Daemon(cfg, dry_run=args.dry_run)

    def _stop(signum, frame):
        d.stop.set()
    signal.signal(signal.SIGTERM, _stop); signal.signal(signal.SIGINT, _stop)
    d.loop()
    return 0


# ---------------------------------------------------------------- selftest

class _FakeCommon(object):
    """selftest 用的假 wa_common: 只做 §12 契约里 daemon 叫得到的那几个函数, 行为取最简单的那种。"""
    CONTENT_TYPES = dict(_FALLBACK_CONTENT_TYPES)
    AUDIO_TYPE = 3
    DEFAULT_CONFIG = None

    def __init__(self, root):
        self.root = Path(root)
        self.sent = []
        self.lids = {}

    def wa_root(self):
        return self.root

    def load_config(self, path=None):
        p = Path(path)
        return self.apply_defaults(json.loads(p.read_text(encoding="utf-8")), p)

    def apply_defaults(self, cfg, config_path):
        cfg.setdefault("paths", {}).setdefault("live_dir", str(Path(config_path).parent))
        return cfg

    def home_dir(self, cfg):
        return Path(cfg["paths"]["home"])

    def live_dir(self, cfg):
        return Path(cfg["paths"]["live_dir"])

    def payload_dir(self):
        return PAYLOAD

    def now_iso(self):
        return now_iso()

    def resolve_bin(self, cfg, name):
        return (cfg.get("bin") or {}).get(name) or ""

    def subprocess_env(self, cfg):
        return {"PATH": "/usr/bin:/bin", "HOME": str(HOME)}

    def norm_phone(self, s, cfg=None):
        d = re.sub(r"\D", "", str(s or ""))
        return d or None

    def lid_of_phone(self, phone):
        return self.lids.get(str(phone))

    def parse_frontmatter(self, text):
        text = text or ""
        if not text.startswith("---"):
            return {}, text
        head, _, body = text[3:].partition("\n---")
        fm = {}
        for line in head.splitlines():
            if ":" in line and not line.startswith(" "):
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip().strip('"')
        return fm, body

    def people_index(self, cfg):
        idx = {"by_jid": {}, "by_phone": {}, "by_lid": {}, "error": None}
        d = Path(cfg["paths"]["people_dir"])
        if not d.is_dir():
            idx["error"] = "people dir missing"
            return idx
        for p in sorted(d.glob("*.md")):
            fm, _ = self.parse_frontmatter(p.read_text(encoding="utf-8"))
            j = fm.get("wa_jid") or ""
            if j:
                idx["by_jid"][j] = p
                if j.endswith("@lid"):
                    idx["by_lid"][j.split("@")[0]] = p
                elif j.split("@")[0].isdigit():
                    idx["by_phone"].setdefault(j.split("@")[0], p)
            ph = re.sub(r"\D", "", fm.get("wa_phone") or "")
            if ph:
                idx["by_phone"].setdefault(ph, p)
        return idx

    def people_note_for(self, phone_or_jid, idx):
        return idx["by_phone"].get(re.sub(r"\D", "", str(phone_or_jid).split("@")[0]))

    def repair_inner_quotes(self, text):
        return text

    def read_json(self, path, default=None):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            return default

    def write_json_atomic(self, path, obj, mode=0o600):
        Path(path).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    def notify_send(self, cfg, text, parse_mode=None, key=None, min_gap=0):
        self.sent.append(text)
        return True

    def notify_wanted(self, cfg, kind):
        mode = (cfg.get("notify") or {}).get("mode", "errors")
        if mode == "off":
            return False
        if kind in ("error", "digest"):
            return True
        if kind == "needs_me":
            return mode in ("needs_me", "all")
        return mode == "all"

    def transcript_key(self, media_path):
        p = Path(media_path).resolve()
        return hashlib.sha1(("%s|%s" % (p, p.stat().st_size)).encode("utf-8")).hexdigest()

    def vocab(self, cfg):
        v = cfg.get("vocab") or {}
        return {k: v.get(k) for k in ("worlds", "stages", "park_from", "priorities", "business_context")}

    def world_is_business(self, cfg, key):
        for w in (cfg.get("vocab") or {}).get("worlds") or []:
            if isinstance(w, dict) and w.get("key") == key:
                return bool(w.get("business"))
        return False


_FAKE_DOSSIER_CORE = '''
\"\"\"selftest 的假 dossier_core: 照 §12 契约最简单的样子 (验档一律过; 放档照契约重读目标档、撞到现档就退、只有近况块变就带进新行、别处变就退)。\"\"\"
import contextlib
import pathlib
import re
CALLS = []
LOCKS = []
RECENT = re.compile(r"(<!-- wa-live:begin[^\\n]*?-->)(.*?)(<!-- wa-live:end -->)", re.S)


class DossierError(Exception):
    pass


def playbook_source(cfg=None, log=None):
    p = pathlib.Path(cfg["paths"]["person_playbook"])
    return p.read_text(encoding="utf-8"), p, False, None


def compose_prompt(cfg, template, name, jid, board=None, reason="", existing_name=None, existing="", facts_block="",
                   pack="", ctx_md="", spec=None, kind="一对一", log=None):
    CALLS.append(("compose_prompt", jid, existing_name))
    out = template.replace("{NAME}", name).replace("{JID}", jid).replace("{REASON}", reason).replace("{ROLES}", "- client")
    return out.replace("{SPEC}", spec or "") + "\\n# 现有的档\\n" + (existing or "(无)") + "\\n# 对话包\\n" + pack


def parse_file_header(text):
    m = re.match(r"\\s*<!--\\s*file:\\s*(.+?)\\s*-->[ \\t]*\\n?", text or "")
    return (m.group(1).strip(), text[m.end():]) if m else (None, text)


def check_note_filename(fname):
    if "/" in fname or ".." in fname or not fname.endswith(".md"):
        raise DossierError("unsafe note file name %r" % fname)
    return fname


@contextlib.contextmanager
def people_lock(cfg=None, timeout=60):
    LOCKS.append("lock")
    yield


def count_gaps(body, cfg=None):
    return 1


def how_to_talk_from(text, limit=160, cfg=None):
    m = re.search(r"下一封怎么开口\\*?\\*?\\s*[:：]\\s*(.+)", text or "")
    return m.group(1).strip() if m else None


def finalize(body, jid, existing="", board_stage=None, has_payment=False, log=None, facts_block=None, cfg=None):
    CALLS.append(("finalize", jid))
    return body, {"gaps": 1}


def warnings_for(body, cfg=None):
    return []


def place_note(cfg, fname, body, existing, backups_dir, reason, who, log=None):
    CALLS.append(("place_note", fname, existing))
    target = pathlib.Path(cfg["paths"]["people_dir"]) / check_note_filename(fname)
    current = target.read_text(encoding="utf-8") if target.exists() else None
    if current is not None and not existing:
        raise DossierError("%s already exists but this run treated the person as new; refusing to overwrite" % fname)
    if current is not None and current != existing:
        strip = lambda t: RECENT.sub(lambda m: m.group(1) + m.group(3), t)
        if strip(current) != strip(existing):
            raise DossierError("%s changed on disk since this run read it; not overwriting" % fname)
        block = RECENT.search(current).group(2)
        body = RECENT.sub(lambda m: m.group(1) + block + m.group(3), body, count=1)
    target.write_text(body, encoding="utf-8")
    return target
'''


def selftest():
    """假 WhatsApp 库 + 假状态库 + 假设定 + 假旁支模组 (monkeypatch), 只写暂存夹。⛔ 不叫 claude、不连外网 (板子测试只连本机 127.0.0.1)。"""
    import http.client
    import types
    global WC, WF, WD, WB, WI, WBF, WV, WCAL, WSS, DC
    saved = (WC, WF, WD, WB, WI, WBF, WV, WCAL, WSS, DC)
    saved_errors = dict(_IMPORT_ERRORS)
    saved_modules = sys.modules.get("dossier_core")
    saved_run, saved_popen = subprocess.run, subprocess.Popen
    saved_handlers = list(log.handlers)
    saved_level = log.level
    tmp = Path(tempfile.mkdtemp(prefix="wa_live_selftest_"))
    results = []
    servers = []

    def check(name, cond, detail=""):
        results.append((name, bool(cond)))
        print("%s %s%s" % ("✓" if cond else "✗", name, ("  (%s)" % str(detail)[:300]) if (detail and not cond) else ""))

    try:
        # ---- 假 WhatsApp Desktop 资料夹 (只建门铃与 State.thread 读的表与栏)
        root = tmp / "wa"
        media = root / "Message" / "Media"
        media.mkdir(parents=True)
        (media / "pic.jpg").write_bytes(b"\xff\xd8\xff" + b"p" * 997)
        (media / "clip.mp4").write_bytes(b"v" * 5000)
        (media / "note.opus").write_bytes(b"o" * 300)
        cs = sqlite3.connect(str(root / "ChatStorage.sqlite"))
        cs.executescript("""
        CREATE TABLE ZWACHATSESSION(Z_PK INTEGER PRIMARY KEY, ZCONTACTJID TEXT, ZPARTNERNAME TEXT, ZSESSIONTYPE INTEGER,
          ZLASTMESSAGEDATE REAL, ZUNREADCOUNT INTEGER, ZLASTMESSAGE INTEGER);
        CREATE TABLE ZWAMESSAGE(Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZISFROMME INTEGER, ZMESSAGETYPE INTEGER, ZTEXT TEXT,
          ZMESSAGEDATE REAL, ZPUSHNAME TEXT, ZGROUPMEMBER INTEGER, ZFROMJID TEXT);
        CREATE TABLE ZWAGROUPMEMBER(Z_PK INTEGER PRIMARY KEY, ZMEMBERJID TEXT);
        CREATE TABLE ZWAMEDIAITEM(Z_PK INTEGER PRIMARY KEY, ZMESSAGE INTEGER, ZMEDIALOCALPATH TEXT, ZXMPPTHUMBPATH TEXT, ZTITLE TEXT);
        CREATE TABLE ZWAPROFILEPUSHNAME(Z_PK INTEGER PRIMARY KEY, ZJID TEXT, ZPUSHNAME TEXT);
        """)
        D1, D2, D3, I1 = "15550001111@s.whatsapp.net", "15550003333@s.whatsapp.net", "444000@lid", "15550002222@s.whatsapp.net"
        G1, G2, G3, G4 = "120363000000000011@g.us", "120363000000000012@g.us", "120363000000000013@g.us", "120363000000000014@g.us"
        chats = [(1, D1, "Casey Sample", 0), (2, G1, "Bread Club", 1), (3, G2, "Market Friends", 1), (4, G3, "Noisy Group", 1),
                 (5, G4, "Unlisted Group", 1), (6, I1, "Ignored Person", 0), (7, "status@broadcast", "Status", 0),
                 (8, D2, "Old Friend", 0), (9, D3, "Lid Person", 0)]
        for pk, jid, name, stype in chats:
            cs.execute("INSERT INTO ZWACHATSESSION VALUES(?,?,?,?,?,?,?)", (pk, jid, name, stype, core_now_minus(), 0, None))
        cs.execute("INSERT INTO ZWAGROUPMEMBER VALUES(1, '777000@lid')")
        cs.commit()

        def add_msg(pk, chat, text, mtype=0, fromme=0, minutes_ago=10.0, member=None, media_rel=None):
            d = core_now_minus() - minutes_ago * 60
            cs.execute("INSERT INTO ZWAMESSAGE VALUES(?,?,?,?,?,?,?,?,?)", (pk, chat, fromme, mtype, text, d, "", member, None))
            cs.execute("UPDATE ZWACHATSESSION SET ZLASTMESSAGE=?, ZLASTMESSAGEDATE=? WHERE Z_PK=?", (pk, d, chat))
            if media_rel:
                cs.execute("INSERT INTO ZWAMEDIAITEM(ZMESSAGE, ZMEDIALOCALPATH) VALUES(?,?)", (pk, media_rel))
            cs.commit()

        for pk in range(1, 6):
            add_msg(pk, 1, "旧讯息 %d" % pk)
        lid = sqlite3.connect(str(root / "LID.sqlite"))
        lid.executescript("CREATE TABLE ZWAZACCOUNT(ZIDENTIFIER TEXT, ZPHONENUMBER TEXT); CREATE TABLE ZWAPHONENUMBERLIDPAIR(ZLID TEXT, ZPHONENUMBER TEXT, ZTIMESTAMP REAL);")
        lid.execute("INSERT INTO ZWAZACCOUNT VALUES('444000@lid', '15550006666')")
        lid.commit(); lid.close()

        # ---- 假 home / live / vault / 人物档 skill
        home = tmp / "home"; live = home / "live"; vault = tmp / "vault"; people = vault / "People"
        for p in (live, people, vault / "Playbooks", tmp / "dossier" / "scripts", tmp / "dossier" / "assets"):
            p.mkdir(parents=True, exist_ok=True)
        (tmp / "dossier" / "scripts" / "dossier_core.py").write_text(_FAKE_DOSSIER_CORE, encoding="utf-8")
        (tmp / "dossier" / "assets" / "dossier-prompt.md").write_text("档案 run: {NAME} ({JID}) {REASON}\n{EXISTING_NOTE_LINE}\n{FACTS}\n{ROLES}\n\n{SPEC}\n", encoding="utf-8")
        playbook = vault / "Playbooks" / "person-dossier-playbook.md"
        playbook.write_text("# 假 playbook\n\n骨架里写 {NOT_A_PLACEHOLDER} 也不会被当占位换掉。\n", encoding="utf-8")
        fake_wc = _FakeCommon(root)
        fake_wc.lids = {"15550004444": "222000"}
        WC = fake_wc
        WF = WD = WB = WI = WBF = WV = WCAL = WSS = None
        _IMPORT_ERRORS.clear()
        _IMPORT_ERRORS["wa_selftest_fake"] = "ImportError: 自测故意放的一行"
        cfg_obj = {
            "version": 1, "my_phone": "15550009999", "my_names": ["Kit Owner", "老板"], "owner_label": "Kit Owner", "owner_marker": "KO",
            "direct_policy": "all", "unlisted_group_policy": "skip", "ignored_jids": [I1],
            "group_policies": {G1: {"policy": "all", "name": "Bread Club"}, G2: {"policy": "mention", "name": "Market Friends"},
                               G3: {"policy": "skip", "name": "Noisy Group"}},
            "debounce": {"quiet_seconds": 45, "max_wait_seconds": 180, "global_concurrency": 2},
            "doorbell": {"enabled": True, "poll_seconds": 2, "stale_alert_hours": 6, "full_rescan_seconds": 60, "lookback": 300, "max_message_age_hours": 48},
            "brain": {"model": "claude-sonnet-5", "cwd": str(home / "brain-cwd"), "context_days": 7, "group_context_days": 3, "warm_days": 2,
                      "run_timeout_seconds": 5, "retries": 0, "transcribe": True},
            "bin": {"claude": str(tmp / "no-such-claude")},
            "paths": {"home": str(home), "live_dir": str(live), "vault": str(vault), "people_dir": str(people), "person_playbook": str(playbook),
                      "intake_buffer": "", "reader_dir": str(tmp / "reader"), "dossier_dir": str(tmp / "dossier")},
            "vocab": {"worlds": DEFAULT_VOCAB["worlds"], "stages": DEFAULT_VOCAB["stages"], "park_from": DEFAULT_VOCAB["park_from"],
                      "priorities": DEFAULT_VOCAB["priorities"], "business_context": "假生意: 周末面包课。"},
            "stage_rules": {"parked_after_days": 21, "group_date_done": False},
            "board": {"enabled": True, "bind": "127.0.0.1", "port": 0, "others_days": 3, "others_limit": 120, "lang": "zh"},
            "notify": {"outlet": "command", "mode": "errors"},
            "dossier": {"enabled": True, "model": "claude-opus-5", "auto_open_worlds": ["work"], "auto_open_min_stage": "talking",
                        "nightly_at": "23:59", "min_gap_hours": 6, "recent_lines": 12},
            "facts": {"enabled": False, "source": "none"}, "second_source": {"enabled": False}, "calendar": {"destination": "ics"},
            "backup": {"enabled": False}, "digest": {"enabled": True, "at": "23:59", "push": True}, "intake": {"enabled": False},
            "voice": {"enabled": False}, "backfill": {"respect_gate": True},
        }
        cfg_path = live / "config.json"
        cfg_path.write_text(json.dumps(cfg_obj, ensure_ascii=False), encoding="utf-8")
        # claude-settings 模板渲染一次 (照 wa_setup 的规矩换两个占位), 顺便验模板本身
        tpl = TEMPLATES / "claude-settings.json"
        tpl_ok = tpl.is_file()
        rendered = None
        if tpl_ok:
            rendered = json.loads(tpl.read_text(encoding="utf-8").replace("{LIVE_DIR}", str(live)).replace("{HOME}", str(HOME)))
            rendered.pop("_comment", None)
            (live / "claude-settings.json").write_text(json.dumps(rendered), encoding="utf-8")
        deny = ((rendered or {}).get("permissions") or {}).get("deny") or []
        check("claude-settings 模板: 工具 deny 与 DENY_TOOLS 一致, 秘密档有 Read(//绝对路径) deny",
              tpl_ok and all(t in deny for t in DENY_TOOLS)
              and ("Read(/%s/telegram.json)" % live) in deny and ("Read(/%s/config.json)" % live) in deny
              and ("Read(/%s/.ssh/**)" % HOME) in deny and bool((rendered or {}).get("disableAllHooks")), deny[-6:])
        setup_logging(live, stream=False)
        cfg = load_config(cfg_path)
        check("load_config: 默认值交给 wa_common, daemon 只建 brain cwd 与 commands 夹",
              (home / "brain-cwd").is_dir() and (live / "commands").is_dir() and DC is not None and hasattr(DC, "CALLS"))
        d = Daemon(cfg)
        logtext = (live / "wa_live.log").read_text(encoding="utf-8")
        check("模组 import 失败写进 wa_live.log", "wa_selftest_fake" in logtext and "import 失败" in logtext)

        def decisions(state, lo=0):
            with state.lock:
                return dict((r[0], r[1]) for r in state.conn.execute("SELECT pk, decision FROM inbound_log WHERE pk > ?", (lo,)).fetchall())

        # ---- 1 · 全新状态库: 往回看的旧讯息只记 skip:before_install, 不送闸
        d.bell.ensure_watermark()
        n, passed = d.bell.scan(force=True)
        dec = decisions(d.state)
        check("全新状态库首扫: 往回看的 5 封记 skip:before_install, 不进 pending",
              n == 5 and passed == 0 and set(dec.values()) == {"skip:before_install"} and not d.state.pending_all()
              and d.state.get_meta("last_pk") == "5", (n, passed, dec))
        # ---- 1b · 还原过的库: last_pk 在 (比库里最大编号小或相等), inbound_log 与 pending 清空
        for name_, last in (("restored_lower", "2"), ("restored_equal", "5")):
            st = State(live / ("%s.sqlite" % name_), cfg)
            st.set_meta("last_pk", last)
            bell = Doorbell(cfg, st, d.notifier)
            n2, p2 = bell.scan(force=True)
            dec2 = decisions(st)
            check("还原过的库 (last_pk=%s, inbound_log 空): 旧讯息记 skip:before_install, 水位对齐到 5" % last,
                  p2 == 0 and set(dec2.values()) == {"skip:before_install"} and not st.pending_all() and st.get_meta("last_pk") == "5", (n2, p2, dec2))
        add_msg(6, 1, "你好, 周末面包课还有位吗?", minutes_ago=0)
        st = State(live / "restored_new_msg.sqlite", cfg)
        st.set_meta("last_pk", "2")
        bell = Doorbell(cfg, st, d.notifier)
        bell.scan(force=True)
        dec3 = decisions(st)
        check("还原过的库: daemon 起跑后才进来的那封照常过闸", dec3.get(6) == "pass:all" and dec3.get(5) == "skip:before_install"
              and len(st.pending_all()) == 1, dec3)

        # ---- 2 · 闸: 新讯息 · 群三档 · 没列的群 · ignored · 广播 · 系统讯息 · 年龄闸
        add_msg(7, 2, "hello everyone", minutes_ago=1, member=1)
        add_msg(8, 3, "hi all", minutes_ago=1, member=1)
        add_msg(9, 3, "@15550009999 你看这个", minutes_ago=1, member=1)
        add_msg(10, 3, "Kit Owner 明天有空吗", minutes_ago=1, member=1)
        add_msg(11, 4, "吵闹群的一句", minutes_ago=1, member=1)
        add_msg(12, 5, "没列的群", minutes_ago=1, member=1)
        add_msg(13, 6, "被忽略的人", minutes_ago=1)
        add_msg(14, 7, "限时动态", minutes_ago=1)
        add_msg(15, 1, "", mtype=6, minutes_ago=1)
        add_msg(16, 8, "三天前的讯息补同步进来", minutes_ago=72 * 60)
        d.bell.scan(force=True)
        dec = decisions(d.state, 5)
        want = {6: "pass:all", 7: "pass:all", 8: "skip:no_mention", 9: "pass:mention", 10: "pass:mention", 11: "skip:policy",
                12: "skip:policy", 13: "skip:ignored", 14: "skip:status_or_broadcast", 15: "skip:system"}
        check("闸: all / mention (电话与名字) / skip / 没列的群 / ignored / 广播 / 系统讯息", all(dec.get(k) == v for k, v in want.items()),
              dict((k, dec.get(k)) for k in want))
        check("年龄闸: 超过 max_message_age_hours 的讯息记 skip:too_old", dec.get(16) == "skip:too_old", dec.get(16))
        check("提到我: 名字照词边界比, 中文照子字串比", d.bell.mentions_me("hi kit owner!") and not d.bell.mentions_me("kit owners party")
              and d.bell.mentions_me("老板你看"))
        # ---- 3 · 主人回了: 标 replied, 取消 burst
        had = any(p["jid"] == D1 for p in d.state.pending_all())
        add_msg(17, 1, "有的, 我发你报名链接", fromme=1, minutes_ago=0)
        d.bell.scan(force=True)
        conv = d.state.get_conversation(D1) or {}
        check("主人回了: outbound:replied:burst_cancelled, tag replied, priority 最不急, pending 清掉",
              had and decisions(d.state, 16).get(17) == "outbound:replied:burst_cancelled" and conv.get("tag") == "replied"
              and conv.get("priority") == "low" and not any(p["jid"] == D1 for p in d.state.pending_all()), conv)
        # ---- 4 · 水位比库大: 重定 + 推一则错误
        sent0 = len(fake_wc.sent)
        d.state.set_meta("last_pk", "999")
        d.bell.scan(force=True)
        d.bell.scan(force=True)
        check("水位大于 MAX(Z_PK): 重定到库的最大编号, 只推一则错误",
              d.state.get_meta("last_pk") == "17" and len(fake_wc.sent) == sent0 + 1 and "水位" in fake_wc.sent[-1], (d.state.get_meta("last_pk"), fake_wc.sent[sent0:]))

        # ---- 5 · normalize 照 config 词表: world 黏住 · stage 只准往前 · parked 规矩 · 词表外 · timeline 留最新
        cfg_v = {"vocab": {"worlds": [{"key": "home", "label": "家", "business": False}, {"key": "shop", "label": "店", "business": True},
                                      {"key": "other", "label": "其他", "business": False}],
                           "stages": [{"key": "lead"}, {"key": "quoted"}, {"key": "paid"}, {"key": "serving"}, {"key": "closed"}, {"key": "parked"}],
                           "park_from": ["lead", "quoted"], "priorities": [{"key": "urgent"}, {"key": "later"}, {"key": "idle"}]}}
        base = {"needs_me": True, "draft": "x", "reason": "r", "confidence": 0.9, "priority": "urgent"}
        a = normalize(dict(base, world="shop", stage="quoted"), {"world": "shop", "stage": "paid"}, cfg_v)
        b = normalize(dict(base, world="home", stage="lead"), {"world": "shop", "stage": "quoted"}, cfg_v)
        c = normalize(dict(base, world="shop", stage="parked"), {"world": "shop", "stage": "paid"}, cfg_v)
        e = normalize(dict(base, world="shop", stage="paid"), {"world": "shop", "stage": "parked"}, cfg_v)
        f = normalize(dict(base, world="mars", stage="lead", priority="yesterday", timeline=["t%d" % i for i in range(10)]), None, cfg_v)
        g = normalize(dict(base, world="home", stage="paid"), None, cfg_v)
        check("normalize 照 config 词表: stage 只准往前", a["stage"] == "paid" and "stage 保留" in a["reason"], a)
        check("normalize: world 黏住 · parked 只从 park_from 进 · 从 parked 回来落第二格", b["world"] == "shop" and b["stage"] == "quoted"
              and c["stage"] == "paid" and e["stage"] == "quoted", (b, c, e))
        check("normalize: 词表外 world 落 other · priority 落最不急 · 非生意世界没 stage · timeline 留最新 8 条",
              f["world"] == "other" and f["stage"] is None and f["priority"] == "idle" and f["timeline"] == ["t%d" % i for i in range(2, 10)]
              and g["stage"] is None, (f, g))
        check("extract_json: 前后有字也抽得出第一个物件", extract_json('好的 {"a": "x}y"} 完') == {"a": "x}y"})

        # ---- 6 · 提示模板: 占位全填, 没有认不得的; 主人副本不在就退回种子并留 meta
        seed_ok = (TEMPLATES / "brain-prompt.md").is_file()
        head = d.brain.prompt_head() if seed_ok else ""
        need = ("{OWNER_LABEL}", "{WORLDS_BLOCK}", "{STAGES_BLOCK}", "{PRIORITIES_BLOCK}", "{BUSINESS_CONTEXT}", "{WORLD_KEYS}", "{STAGE_KEYS}",
                "{PRIORITY_KEYS}", "{CONTEXT_DAYS}", "{GROUP_CONTEXT_DAYS}", "{WARM_DAYS}", "{PARKED_AFTER_DAYS}", "{FACTS_SOURCE_LABEL}")
        tpl_text = (TEMPLATES / "brain-prompt.md").read_text(encoding="utf-8") if seed_ok else ""
        check("brain-prompt 模板: 13 个占位都在, 填完一个不剩, 维护者注解不进提示",
              seed_ok and all(x in tpl_text for x in need) and not _PLACEHOLDER_RE.findall(head) and "Kit Owner" in head
              and "假生意" in head and not head.lstrip().startswith("<!--"), _PLACEHOLDER_RE.findall(head))
        check("主人副本不在: 退回种子并记 meta seed_fallback:brain-prompt", bool(d.state.get_meta("seed_fallback:brain-prompt")))

        # ---- 7 · 对人: 另一种 jid 形与电话都找得到同一份档
        (people / "Mira-Example.md").write_text("---\ntype: person\nwa_jid: 222000@lid\n---\n# Mira\n", encoding="utf-8")
        (people / "Oskar-Example.md").write_text("---\ntype: person\nwa_phone: +1 555 000 5555\n---\n# Oskar\n<!-- wa-live:begin -->\n- 2026-09-01 · 旧近况\n<!-- wa-live:end -->\n", encoding="utf-8")
        (people / "Hanna-Example.md").write_text("---\ntype: person\nwa_jid: 15550006666@s.whatsapp.net\n---\n# Hanna\n<!-- wa-live:begin -->\n- 2026-09-01 · 旧近况\n<!-- wa-live:end -->\n", encoding="utf-8")
        dz = d.dossier
        m1 = dz.note_for_speaker("15550004444@s.whatsapp.net")
        m2 = dz.note_for_speaker("15550005555@s.whatsapp.net")
        m3 = dz.note_for_speaker(D3)
        check("对人: 电话形 jid 找到档头写 LID 形的档", m1 is not None and m1.name == "Mira-Example.md", m1)
        check("对人: 只写 wa_phone 的档用电话找得到", m2 is not None and m2.name == "Oskar-Example.md", m2)
        check("对人: LID 形 jid 找到档头写电话形的档", m3 is not None and m3.name == "Hanna-Example.md", m3)
        info = dz.note_info(D3) or {}
        check("板上档资讯带 vault 相对路径", info.get("path") == "People/Hanna-Example.md" and info.get("file") == "Hanna-Example.md", info)
        queued = []
        real_enqueue = dz.enqueue
        dz.enqueue = lambda jid, name, reason, force=False: queued.append((jid, reason)) or True
        try:
            nobody = "15550007070@s.whatsapp.net"
            dz.after_judgment(nobody, "No Note", "direct", {"world": "work", "stage": "talking"}, None)
            dz.after_judgment(nobody, "No Note", "direct", {"world": "personal", "stage": "talking"}, None)
            dz.after_judgment(nobody, "No Note", "direct", {"world": "work", "stage": "new"}, None)
            dz.after_judgment("15550004444@s.whatsapp.net", "Mira", "direct", {"world": "work", "stage": "offered"}, None)
        finally:
            dz.enqueue = real_enqueue
        check("开新档只给 auto_open_worlds 里的世界、stage 到 auto_open_min_stage 以上、而且还没有档的人 (personal · new · 已有档都不排)",
              queued == [(nobody, "开新档: work / talking")], queued)

        # ---- 8 · 档案 run: 开新档撞到同名现档要退; 档名规矩; 重写期间近况块多了新行要带进去; 别处被手改要退并重排
        (people / "Casey-Sample.md").write_text("---\ntype: person\n---\n# Casey (主人手写的档, 档头没有 jid)\n", encoding="utf-8")
        md5_before = hashlib.md5((people / "Casey-Sample.md").read_bytes()).hexdigest()
        replies = {}

        def fake_sweep(args, timeout, label="wa_sweep", key="shared"):
            out = Path(args[args.index("--out") + 1])
            out.with_suffix(".md").write_text("# 假对话包\n- 10:00 对方: 你好\n", encoding="utf-8")
            return tmp
        d.brain.sweep = fake_sweep
        d.brain.context = lambda sweep_json, run_dir, work=None: "(假第二大脑)"

        def fake_call(prompt, run_dir):
            r = replies.pop("next")
            if callable(r):
                r = r()
            return {"text": r, "duration_ms": 1, "input_tokens": 1, "output_tokens": 1, "breach": [], "denials": []}
        dz.call_claude = fake_call

        def run_job_for(jid, name):
            dz.enqueue(jid, name, "板上要求重写")
            with d.state.lock:
                job = dict(d.state.conn.execute("SELECT * FROM dossier_jobs WHERE jid=? AND started_at IS NULL ORDER BY id DESC LIMIT 1", (jid,)).fetchone())
            dz.run_job(job)
            with d.state.lock:
                return dict(d.state.conn.execute("SELECT * FROM dossier_jobs WHERE id=?", (job["id"],)).fetchone())

        note_body = "---\ntype: person\nroles: [client]\nwa_jid: %s\n---\n# 新档\n<!-- wa-live:begin -->\n<!-- wa-live:end -->\n"
        replies["next"] = "<!-- file: Casey-Sample.md -->\n" + note_body % D1
        job = run_job_for(D1, "Casey Sample")
        with d.state.lock:
            drun = d.state.conn.execute("SELECT kind, ok FROM runs WHERE kind='dossier' ORDER BY id DESC LIMIT 1").fetchone()
        composed = [x for x in DC.CALLS if x[0] == "compose_prompt"]
        check("开新档撞到同名现档 (档头没对上的现档): place_note 退回, job 失败、档一个字没变",
              job["ok"] == 0 and "already exists" in (job["error"] or "")
              and hashlib.md5((people / "Casey-Sample.md").read_bytes()).hexdigest() == md5_before, job["error"])
        check("档案 run 的提示交给 dossier_core.compose_prompt 组 (开新档没有现档名)", bool(composed) and composed[-1][1:] == (D1, None), composed[-1:])
        check("档案 run 进 runs 表 (kind dossier)", drun is not None and drun["ok"] == 0, drun and dict(drun))
        replies["next"] = "<!-- file: ../evil.md -->\n" + note_body % D2
        job = run_job_for(D2, "Old Friend")
        check("开新档的档名走 dossier_core.check_note_filename: 有 .. 或斜线就退, 没写到房外",
              job["ok"] == 0 and "unsafe note file name" in (job["error"] or "") and not (vault / "evil.md").exists(), job["error"])
        replies["next"] = "<!-- file: _Sneaky.md -->\n" + note_body % "15550004545@s.whatsapp.net"
        job = run_job_for("15550004545@s.whatsapp.net", "Sneaky")
        check("开新档的档名底线开头: 退 (People 索引会跳过这种档)", job["ok"] == 0 and "底线" in (job["error"] or "") and not (people / "_Sneaky.md").exists(), job["error"])
        hanna = people / "Hanna-Example.md"

        def rewrite_with_concurrent_recent():
            dz.append_recent(hanna, "- 2026-09-14 · 跑的期间进来的新近况")
            return "<!-- file: whatever.md -->\n---\ntype: person\nroles: [client]\nwa_jid: 15550006666@s.whatsapp.net\n---\n# Hanna 重写\n<!-- wa-live:begin -->\n- 2026-09-01 · 旧近况\n<!-- wa-live:end -->\n"
        replies["next"] = rewrite_with_concurrent_recent
        DC.CALLS[:] = []
        DC.LOCKS[:] = []
        job = run_job_for(D3, "Lid Person")
        final = hanna.read_text(encoding="utf-8")
        fin_jids = [x[1] for x in DC.CALLS if x[0] == "finalize"]
        placed = [x for x in DC.CALLS if x[0] == "place_note"]
        check("重写: 跑的期间近况块多的新行带进新档, 用原档名, 验档用档头原来那一形 jid",
              job["ok"] == 1 and "跑的期间进来的新近况" in final and "Hanna 重写" in final and fin_jids == ["15550006666@s.whatsapp.net"]
              and placed and placed[-1][1] == "Hanna-Example.md", (job["error"], fin_jids, placed[-1:]))
        check("近况追加拿 dossier_core.people_lock (跟放档同一把)", bool(DC.LOCKS), DC.LOCKS)
        oskar = people / "Oskar-Example.md"

        def rewrite_with_hand_edit():
            oskar.write_text(oskar.read_text(encoding="utf-8").replace("# Oskar", "# Oskar (主人刚手改)"), encoding="utf-8")
            return "<!-- file: Oskar-Example.md -->\n---\ntype: person\nroles: [client]\n---\n# Oskar 重写\n<!-- wa-live:begin -->\n<!-- wa-live:end -->\n"
        replies["next"] = rewrite_with_hand_edit
        job = run_job_for("15550005555@s.whatsapp.net", "Oskar")
        with d.state.lock:
            requeued = d.state.conn.execute("SELECT reason FROM dossier_jobs WHERE jid=? AND started_at IS NULL", ("15550005555@s.whatsapp.net",)).fetchone()
        check("重写期间档被手改 (不只近况块): 不盖、job 失败、重排一次", job["ok"] == 0 and "changed on disk" in (job["error"] or "")
              and "主人刚手改" in oskar.read_text(encoding="utf-8") and requeued is not None and requeued["reason"].startswith("重排"), job["error"])
        # 重启时跑到一半的 job
        with d.state.lock:
            d.state.conn.execute("DELETE FROM dossier_jobs WHERE started_at IS NULL")
            d.state.conn.execute("INSERT INTO dossier_jobs(jid,name,reason,requested_at,started_at) VALUES(?,?,?,?,?)", ("15550007777@s.whatsapp.net", "Stuck", "夜批: x", now_iso(), now_iso()))
            d.state.conn.commit()
        Dossier(cfg, d.state, d.brain, d.notifier)
        with d.state.lock:
            stuck = dict(d.state.conn.execute("SELECT ok, error, finished_at FROM dossier_jobs WHERE jid=?", ("15550007777@s.whatsapp.net",)).fetchone())
        check("起跑时把跑到一半的档案 job 记成失败, 那个人马上排得回队列",
              stuck["ok"] == 0 and (stuck["error"] or "").startswith("中断:") and dz.enqueue("15550007777@s.whatsapp.net", "Stuck", "夜批: y"), stuck)

        # ---- 9 · 草稿: 主人改过的字新判断不盖, 脑的新草稿另放; 主人回了之后恢复
        j1 = normalize({"world": "work", "stage": "talking", "priority": "now", "needs_me": True, "draft": "脑的新草稿", "reason": "r", "confidence": 0.8}, None, cfg)
        d.state.upsert_judgment(G1, "Bread Club", "group", j1, 7, now_iso(), 1)
        d.state.set_draft(G1, "脑的新草稿")
        untouched = d.state.get_conversation(G1)["draft_owner_at"] is None
        d.state.set_draft(G1, "主人改的字")
        d.state.upsert_judgment(G1, "Bread Club", "group", dict(j1, draft="脑的第二版"), 7, now_iso(), 1)
        cv = d.state.get_conversation(G1)
        d.state.mark_replied(G1, "Bread Club", "group", 18, now_iso())
        d.state.upsert_judgment(G1, "Bread Club", "group", dict(j1, draft="脑的第三版"), 7, now_iso(), 1)
        cv2 = d.state.get_conversation(G1)
        check("主人改过的草稿: 新判断不盖, 脑的草稿进 draft_brain; 没改字的存档不算改过; 主人回了之后恢复照常",
              untouched and cv["draft"] == "主人改的字" and cv["draft_brain"] == "脑的第二版" and cv2["draft"] == "脑的第三版"
              and cv2["draft_owner_at"] is None, (untouched, cv, cv2))

        # ---- 10 · dry-run: 不叫 claude、不推、不写 vault、假判断不盖真状态、排班只跑试跑
        calls_sub = []

        def no_sub(*a, **k):
            calls_sub.append(a[0] if a else k.get("args"))
            raise RuntimeError("selftest: 不准起子程序")
        written = {"digest_write": 0, "digest_send": 0}
        WD = types.SimpleNamespace(build_digest=lambda cfg_, day: {"date": str(day), "lines": ["昨天安静"], "text": "昨天安静"},
                                   write_digest=lambda cfg_, dd_: written.__setitem__("digest_write", written["digest_write"] + 1),
                                   send_digest=lambda cfg_, dd_: written.__setitem__("digest_send", written["digest_send"] + 1),
                                   latest_digest=lambda cfg_: None)
        WF = types.SimpleNamespace(build_facts=lambda cfg_: {"people": {}, "mismatch": {"paid_chat_no_note_queued": [{"wa_jid": D2, "name": "x"}]}},
                                   stage_floor=lambda facts, jid, cfg=None: False, load_facts=lambda cfg_: None,
                                   facts_lines=lambda facts, jid: [], facts_section=lambda facts, jid, cfg=None: "")
        vault_before = sorted((str(p), p.stat().st_mtime, p.stat().st_size) for p in vault.rglob("*") if p.is_file())
        before_conv = d.state.get_conversation(D1)
        sent0 = len(fake_wc.sent)
        subprocess.run, subprocess.Popen = no_sub, no_sub
        try:
            dd = Daemon(cfg, dry_run=True)
            dd.brain.sweep = fake_sweep
            dd.brain.context = lambda sweep_json, run_dir, work=None: "(假第二大脑)"
            burst = dd.bell.burst_for_test(D1)
            dd.brain.run(burst, dd.running, "selftest")
            dd.probe_claude({})
            dd.job_digest({})
            dd.job_facts({})
            dd.notifier.send("⚠️ 自测推播", kind="error")
            q = dd.dossier.enqueue(D2, "Old Friend", "板上要求重写")
            wrote = dd.dossier.append_recent(hanna, "- 2026-09-15 · dry-run 不该写进来")
        finally:
            subprocess.run, subprocess.Popen = saved_run, saved_popen
        vault_after = sorted((str(p), p.stat().st_mtime, p.stat().st_size) for p in vault.rglob("*") if p.is_file())
        after_conv = d.state.get_conversation(D1)
        dry_runs = sorted(p for p in dd.brain.runs_dir.glob("*-%s" % safe_name(D1)) if p.name[:1].isdigit())
        check("dry-run: 没起任何子程序 (没叫 claude)", not calls_sub, calls_sub)
        check("dry-run: 没推、没写 vault、没排档案 run", len(fake_wc.sent) == sent0 and vault_before == vault_after and not q and not wrote)
        check("dry-run: 假判断只落 run 夹, 不盖状态库的真判断", bool(dry_runs) and (dry_runs[-1] / "judgment.json").is_file()
              and (before_conv or {}).get("reason") == (after_conv or {}).get("reason") and (after_conv or {}).get("last_run_id") == (before_conv or {}).get("last_run_id"))
        check("dry-run: 排班跑试跑模式 (日报不写不推、facts.json 不写)", written == {"digest_write": 0, "digest_send": 0} and not (live / "facts.json").exists(), written)
        WD = WF = None

        # ---- 11 · 时间规则: 可搁置那一格 21 天没动静 → parked 并记前一格; 群名日期规则默认关
        old = (dt.datetime.now() - dt.timedelta(days=30)).isoformat(timespec="seconds")
        with d.state.lock:
            d.state.conn.execute("INSERT INTO conversations(jid,name,kind,world,stage,last_in_at,last_out_at,stage_changed_at,last_run_id) VALUES(?,?,?,?,?,?,?,?,1)",
                                 ("15550008888@s.whatsapp.net", "Quiet Lead", "direct", "work", "offered", old, old, old))
            d.state.conn.execute("INSERT INTO conversations(jid,name,kind,world,stage,last_run_id) VALUES(?,?,?,?,?,1)",
                                 ("120363000000000099@g.us", "Workshop 1/1", "group", "work", "active"))
            d.state.conn.commit()
        d.state.stage_rules(21, False)
        q1 = d.state.get_conversation("15550008888@s.whatsapp.net")
        q2 = d.state.get_conversation("120363000000000099@g.us")
        check("时间规则: offered 21 天没动静 → parked 且记 stage_prev; 群名日期规则默认关", q1["stage"] == "parked" and q1["stage_prev"] == "offered"
              and q2["stage"] == "active", (q1["stage"], q2["stage"]))
        check("群名日期: 「24/7」不是日期; 十二月看到的「1/1」算明年",
              group_name_date("Support 24/7", dt.date(2026, 9, 14)) is None and group_name_date("Workshop 12/1", dt.date(2026, 12, 20)) == dt.date(2027, 1, 12))
        others, total = d.state.recent_chats(3, 2, exclude={D1}, with_total=True)
        check("其他对话: 先剔掉判过的再取上限, 回剔掉后的总数", len(others) == 2 and D1 not in [o["jid"] for o in others] and total >= 2, (len(others), total))
        check("编码串过滤只丢像编码的推名", not _looks_encoded("Alexander") and _looks_encoded("QmFzZTY0SnVua1VzZXJJZDEyMzQ1") and _clean_pushname("Mira") == "Mira")
        check("HTTP Range 解析", parse_range("bytes=0-99", 5000) == (0, 99) and parse_range("bytes=-10", 5000) == (4990, 4999)
              and parse_range("bytes=6000-", 5000) is None and parse_range("items=1-2", 5000) == "ignore")

        # ---- 12 · 板子: ui · /media Range 206 与 HEAD · 没有 GET /draft · /calendar/<key>.ics · world 改非生意世界清 stage · 闸外「现在判断」要确认
        add_msg(20, 1, "", mtype=1, minutes_ago=0, media_rel="Media/pic.jpg")
        add_msg(21, 1, "", mtype=2, minutes_ago=0, media_rel="Media/clip.mp4")
        add_msg(22, 1, "", mtype=3, minutes_ago=0, media_rel="Media/note.opus")
        tc = home / "transcripts-cache"
        tc.mkdir(parents=True, exist_ok=True)
        (tc / (fake_wc.transcript_key(media / "note.opus") + ".json")).write_text(json.dumps({"text": "语音转出来的字"}), encoding="utf-8")
        (live / "calendar").mkdir(exist_ok=True)
        (live / "calendar" / "abc123.ics").write_text("BEGIN:VCALENDAR\nEND:VCALENDAR\n", encoding="utf-8")
        d.state.upsert_judgment(G2, "Market Friends", "group", normalize({"world": "work", "stage": "talking", "priority": "soon", "needs_me": False,
                                                                           "draft": "", "reason": "r", "confidence": 0.7}, None, cfg), 9, now_iso(), 1)
        dispatched = []
        d.dispatch = lambda p, source="live": dispatched.append((p["jid"], source))
        srv = start_board(d)
        servers.append(srv)
        port = d.board_port

        def req(method, path, body=None, headers=None):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            try:
                conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers or {})
                r = conn.getresponse()
                return r.status, dict((k.lower(), v) for k, v in r.getheaders()), r.read()
            finally:
                conn.close()
        s, h, raw = req("GET", "/api/board")
        payload = json.loads(raw.decode("utf-8")) if s == 200 else {}
        ui = payload.get("ui") or {}
        check("板子 payload 有 ui (lang · vocab · calendar · features · owner_label · vault_name · 天数)",
              s == 200 and ui.get("lang") == "zh" and [w["key"] for w in ui.get("vocab", {}).get("worlds", [])] == ["personal", "work", "other"]
              and ui.get("calendar") == "none" and ui.get("owner_label") == "Kit Owner" and ui.get("vault_name") == "vault"
              and ui.get("context_days") == 7 and "features" in ui and "cost_usd" in (payload.get("status") or {}).get("tokens_today", {}), ui)
        s, h, raw = req("GET", "/media?pk=21", headers={"Range": "bytes=0-99"})
        check("/media 回 Range 请求 206 与 Content-Range", s == 206 and h.get("content-range") == "bytes 0-99/5000" and len(raw) == 100
              and h.get("accept-ranges") == "bytes", (s, h))
        s416 = req("GET", "/media?pk=21", headers={"Range": "bytes=90000-"})[0]
        s, h, raw = req("HEAD", "/media?pk=20")
        sh = req("HEAD", "/health")[0]
        check("/media 超出范围回 416; HEAD /media 与 /health 回 200 不带内容", s416 == 416 and s == 200 and h.get("content-length") == "1000"
              and raw == b"" and sh == 200, (s416, s, h, sh))
        draft_before = (d.state.get_conversation(D1) or {}).get("draft")
        sd = req("GET", "/draft?jid=%s&text=hack" % urllib.parse.quote(D1))[0]
        check("GET /draft 已拿掉 (404, 状态没被改)", sd == 404 and (d.state.get_conversation(D1) or {}).get("draft") == draft_before, sd)
        s, h, raw = req("GET", "/calendar/abc123.ics")
        sbad = req("GET", "/calendar/..%2Fconfig.ics")[0]
        check("GET /calendar/<key>.ics 回 text/calendar, 怪 key 回 404", s == 200 and h.get("content-type", "").startswith("text/calendar")
              and b"VCALENDAR" in raw and sbad == 404, (s, h, sbad))
        s, h, raw = req("POST", "/api/field", {"jid": G2, "field": "world", "value": "personal"})
        g2 = d.state.get_conversation(G2)
        check("板上把 world 改成非生意世界: 后端当场清 stage 并记前一格", s == 200 and json.loads(raw.decode("utf-8")).get("stage") is None
              and g2["stage"] is None and g2["stage_prev"] == "talking", (s, raw[:200]))
        s1, _, r1 = req("POST", "/api/run", {"jid": G3})
        s2, _, r2 = req("POST", "/api/run", {"jid": G3, "confirm": True})
        check("闸外的「现在判断」先回 needs_confirm, 带 confirm 才送", json.loads(r1.decode("utf-8")).get("needs_confirm") is True
              and json.loads(r2.decode("utf-8")).get("ok") is True and dispatched == [(G3, "board")], (r1[:200], r2[:200], dispatched))
        s, h, raw = req("GET", "/api/thread?jid=%s&n=40" % urllib.parse.quote(D1))
        msgs = json.loads(raw.decode("utf-8")).get("messages") if s == 200 else []
        voice = [m for m in msgs if m.get("pk") == 22]
        pic = [m for m in msgs if m.get("pk") == 20]
        check("对话区: 语音带转写 (快取在 paths.home) 与播放网址, 图片带 /media 网址",
              bool(voice) and voice[0].get("transcript") == "语音转出来的字" and voice[0].get("audio_url") == "/media?pk=22"
              and bool(pic) and pic[0].get("media_url") == "/media?pk=20", (voice, pic))
        check("对话区: 不把本机绝对路径送去浏览器 (没有 media 键, 只有 media_local 真假)",
              bool(msgs) and all("media" not in m for m in msgs) and voice and voice[0].get("media_local") is True
              and not any(str(media) in json.dumps(m, ensure_ascii=False) for m in msgs), [sorted(m) for m in msgs[:2]])

        # 设定档挡下的读取: 真 claude 2.x stream 的样子 (2026-09-15 金丝雀实跑抄的键), result 的 permission_denials 是空的
        stream = "\n".join(json.dumps(x) for x in [
            {"type": "system", "subtype": "init", "tools": ["Read", "Glob", "Grep"]},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_1", "name": "Read", "caller": "x",
                                                           "input": {"file_path": "/tmp/secret-probe/credentials-probe.txt"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_1", "is_error": True,
                                                      "content": "<tool_use_error>File is in a directory that is denied by your permission settings.</tool_use_error>"}]}},
            {"type": "result", "subtype": "success", "is_error": False, "result": "can't", "permission_denials": []}])
        pdone, pbreach = Brain.parse_stream(stream)
        check("设定档挡下的读取记进 permission_denials (工具名 + 路径), 没多载工具不算破笼",
              pdone and pdone.get("permission_denials") == ["Read /tmp/secret-probe/credentials-probe.txt"] and pbreach == [],
              (pdone or {}).get("permission_denials"))

        # 日历钮按两次 (对话上没有提示): 第二次回 already 且不再叫日历去处; 假日历模组, 不写档不连外
        cal_calls = []

        def fake_create(cfg_, title, date, time_, duration_min=60, place="", note="", dry_run=False, idempotency_key=None, **kw):
            cal_calls.append((date, time_))
            return {"ok": True, "url": "/calendar/probe.ics", "event_id": "probe", "already": len(cal_calls) > 1, "error": ""}
        WCAL = types.SimpleNamespace(
            hint_key=lambda h: ("k%s%s" % (h.get("date"), h.get("time") or "")) if isinstance(h, dict) and h.get("date") else "",
            validate_hint=lambda h: ({"title": h["title"], "date": h["date"], "time": h.get("time") or "", "duration_min": 60, "place": ""}
                                     if isinstance(h, dict) and h.get("title") and h.get("date") else None),
            compose_description=lambda note, name, phone: note, create_event=fake_create)
        cal_jid = "15550007777@s.whatsapp.net"
        with d.state.lock:
            d.state.conn.execute("INSERT OR IGNORE INTO conversations(jid,name,kind) VALUES(?,?,?)", (cal_jid, "Calendar Probe", "direct"))
            d.state.conn.commit()
        form = {"jid": cal_jid, "title": "Probe", "date": "2026-09-20", "time": "15:00"}
        c1 = json.loads(req("POST", "/api/calendar", form)[2].decode("utf-8"))
        c2 = json.loads(req("POST", "/api/calendar", form)[2].decode("utf-8"))
        check("日历钮没提示时按两次: 第一次 ok 不带 already, 第二次 ok 带 already, 日历去处只叫一次",
              c1.get("ok") is True and not c1.get("already") and c2.get("ok") is True and c2.get("already") is True and len(cal_calls) == 1,
              (c1, c2, cal_calls))
        WCAL = None

        # 板上「开档 / 重写档」: 没档的人理由记开档, 有档的人记重写
        new_jid = "15550009999@s.whatsapp.net"
        with d.state.lock:
            d.state.conn.execute("INSERT OR IGNORE INTO conversations(jid,name,kind) VALUES(?,?,?)", (new_jid, "No Note Yet", "direct"))
            d.state.conn.commit()
        has_note_jid = "15550004444@s.whatsapp.net"
        if d.dossier.enabled and not d.dry_run and d.dossier.note_for_speaker(new_jid) is None:
            req("POST", "/api/dossier", {"jid": new_jid})
            req("POST", "/api/dossier", {"jid": has_note_jid})
            with d.state.lock:
                reasons = dict((r["jid"], r["reason"]) for r in d.state.conn.execute(
                    "SELECT jid, reason FROM dossier_jobs WHERE jid IN (?, ?) ORDER BY id", (new_jid, has_note_jid)).fetchall())
            want_old = "板上要求重写" if d.dossier.note_for_speaker(has_note_jid) is not None else "板上要求开档"
            check("板上开档: 没档的人 dossier_jobs 理由是「板上要求开档」, 有档的人是「板上要求重写」",
                  reasons.get(new_jid) == "板上要求开档" and reasons.get(has_note_jid) == want_old, reasons)
        else:
            check("板上开档: 自测的 daemon 要开人物档且不是试跑", False, (d.dossier.enabled, d.dry_run))
    except Exception:
        import traceback
        traceback.print_exc()
        check("自测跑到一半丢了例外", False)
    finally:
        for srv in servers:
            try:
                srv.shutdown(); srv.server_close()
            except Exception:
                pass
        subprocess.run, subprocess.Popen = saved_run, saved_popen
        WC, WF, WD, WB, WI, WBF, WV, WCAL, WSS, DC = saved
        _IMPORT_ERRORS.clear(); _IMPORT_ERRORS.update(saved_errors)
        if saved_modules is not None:
            sys.modules["dossier_core"] = saved_modules
        else:
            sys.modules.pop("dossier_core", None)
        for hd in list(log.handlers):
            if hd not in saved_handlers:
                log.removeHandler(hd)
                try:
                    hd.close()
                except Exception:
                    pass
        log.setLevel(saved_level)
    n_ok = sum(1 for _, ok in results if ok)
    if n_ok == len(results) and results:
        shutil.rmtree(str(tmp), ignore_errors=True)
    else:
        print("暂存夹留着给你查: %s" % tmp)
    print("selftest: %d/%d 过" % (n_ok, len(results)))
    return 0 if (results and n_ok == len(results)) else 1


if __name__ == "__main__":
    sys.exit(main())
