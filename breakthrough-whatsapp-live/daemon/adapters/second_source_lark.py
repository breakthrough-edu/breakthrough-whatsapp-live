#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
adapters/second_source_lark.py · second message source `lark`: one day of Lark messages from the whitelisted chats,
flattened to plain text with placeholders, owner and bots told apart.

Behind wa_second_source.py, which owns the whitelist, the world guess, the prompt, the anti fabrication checks, the
person note lines, the intake captures and second-source-last.json. This module only talks to Lark (read only:
auth status, +chat-messages-list, +chat-members-list) through lark-cli.

Implementation contract (what wa_second_source calls):
- NAME, SOURCE_NAME, ID_KEY (person note key holding this source's sender ids), SENDER_ID_EXAMPLE, CHAT_ID_EXAMPLE,
  SYSTEM_NAME_PATTERNS (chat names of built in helpers that are never pulled)
- fetch_day(cfg, chats, day, runner=None) -> {"chats": [{chat_id, title, kind, messages: [{at, sender_id, sender_name,
  is_owner, text}]}], "errors": [str], "failed_chats": int, "skipped": [{chat_id, title, why}], "owner_ids": [str]}
  chats = the pullable whitelist entries; zero message chats are left out; one failing chat is an error, never a raise.
- members(cfg, chats, runner=None, max_users=300) -> {"users": {id: {name, chats}}, "bots": {id: {name, app_id, chats}}, "errors"}
- runner(args) -> (returncode, stdout, stderr), args being everything after the lark-cli executable.

Config (`second_source.lark`): whitelist (read by the interface), owner_ids (optional list; empty asks `auth status`),
page_limit (50), timeout_seconds (120), skip_chat_ids and system_name_patterns (read by the interface).

Known pits (lark-cli 1.0.89):
- open_id values are per app: the ids written into person notes must come from the same lark-cli profile that pulls.
- +chat-messages-list with --order desc returns an empty first page for some chats: always --order asc.
- create_time is a local time string; the day window is local midnight to local midnight with the offset.
- +chat-members-list always with --page-all.
- stdout may print progress lines before the JSON; launchd has no node on PATH (both handled in calendar_lark's plumbing).
- Voice messages carry no transcript, only the duration placeholder.
- On a failure (rc 1) the JSON envelope may arrive on stderr instead of stdout: both are read.
- An encrypted chat answers API error 231203: the Lark API cannot read it at all, so it is skipped with that reason,
  never counted as a failed chat (it would fail every night and hide real failures).

Python 3.9 standard library only. Never calls a model, never writes a file.
"""

import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

_DAEMON = Path(__file__).resolve().parent.parent
if str(_DAEMON) not in sys.path:
    sys.path.insert(0, str(_DAEMON))
from adapters.calendar_lark import cli_path, lark_env, parse_json_out  # noqa: E402  (shared lark-cli plumbing)

NAME = "lark"
SOURCE_NAME = "Lark"
ID_KEY = "lark_id"
SENDER_ID_EXAMPLE = "ou_<编号>"
CHAT_ID_EXAMPLE = "oc_<编号>"
SYSTEM_NAME_PATTERNS = ("customer support", "lark assistant", "飞书助手", "飞书小助手", "小助手", "服务台", "helpdesk")
DIRECT_KINDS = ("一对一", "p2p", "direct")
MSG_TEXT_MAX = 2000
MAX_MEMBERS = 300
ENCRYPTED_CHAT_CODE = 231203
ENCRYPTED_CHAT_WHY = "encrypted chat, the Lark API cannot read it"

json_from_output = parse_json_out


def options(cfg):
    ss = (cfg or {}).get("second_source") or {}
    lk = ss.get("lark") if isinstance(ss.get("lark"), dict) else {}
    return {"owner_ids": [str(x) for x in (lk.get("owner_ids") or []) if x],
            "page_limit": int(lk.get("page_limit") or 50),
            "timeout_seconds": int(lk.get("timeout_seconds") or 120)}


def default_runner(cfg, timeout=None):
    """runner(args) -> (rc, stdout, stderr) for `lark-cli <args>`, with node on PATH."""
    timeout = int(timeout or options(cfg)["timeout_seconds"])
    cli = cli_path(cfg)
    env = lark_env(cfg)

    def run(args):
        if not cli:
            return 127, "", "lark-cli not found (set bin.lark_cli)"
        try:
            r = subprocess.run([cli] + list(args), capture_output=True, text=True, timeout=timeout, env=env)
            return r.returncode, r.stdout or "", r.stderr or ""
        except subprocess.TimeoutExpired:
            return 124, "", "lark-cli timed out after %ds" % timeout
        except Exception as e:
            return 127, "", str(e)
    return run


def owner_ids(cfg, runner=None):
    """The owner's own sender ids: second_source.lark.owner_ids when set, else `lark-cli auth status`
    identities.user.openId. An empty set when neither answers."""
    ids = set(options(cfg)["owner_ids"])
    if ids:
        return ids
    run = runner or default_runner(cfg, timeout=30)
    try:
        rc, out, _ = run(["auth", "status"])
    except Exception:
        return set()
    j = json_from_output(out) or {}
    oid = ((j.get("identities") or {}).get("user") or {}).get("openId")
    return {str(oid)} if oid else set()


my_open_ids = owner_ids


# ---------------------------------------------------------------- one day

def _tz_suffix(day):
    d = dt.datetime.strptime(day, "%Y-%m-%d")
    z = d.astimezone().strftime("%z") or "+0000"
    return z[:3] + ":" + z[3:]


def day_window(day):
    """(start, end) ISO 8601 with the local offset; end = next day 00:00."""
    nxt = (dt.datetime.strptime(day, "%Y-%m-%d") + dt.timedelta(days=1)).strftime("%Y-%m-%d")
    return "%sT00:00:00%s" % (day, _tz_suffix(day)), "%sT00:00:00%s" % (nxt, _tz_suffix(nxt))


def messages_list_args(chat_id, day, page_limit=50):
    start, end = day_window(day)
    return ["im", "+chat-messages-list", "--chat-id", chat_id, "--start", start, "--end", end,
            "--order", "asc", "--page-all", "--page-limit", str(page_limit), "--no-reactions", "--format", "json", "--as", "user"]


_AT_RE = re.compile(r"<at\b[^>]*>(.*?)</at>", re.S)
_IMG_MD_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_IMG_BR_RE = re.compile(r"\[Image:[^\]]*\]", re.I)
_AUDIO_RE = re.compile(r"<audio\b[^>]*>(?:\s*</audio>)?", re.S)
_VIDEO_RE = re.compile(r"<(?:video|media)\b[^>]*/?>", re.S)
_FILE_RE = re.compile(r"<file\b[^>]*>(?:\s*</file>)?", re.S)
_ATTR_RE = re.compile(r"\b(duration|name)=\"([^\"]*)\"")
_CARD_RE = re.compile(r"<card\b[^>]*?title=\"([^\"]*)\"[^>]*>.*?(?:</card>|$)", re.S)
_TAG_RE = re.compile(r"</?(?:p|br|span|b|i|u|s|div)\b[^>]*>", re.I)


def _attr(tag, key):
    for k, v in _ATTR_RE.findall(tag or ""):
        if k == key:
            return v
    return ""


def _audio_ph(tag):
    d = _attr(tag, "duration")
    return "[语音 %s]" % d if d else "[语音]"


def _file_ph(tag):
    nm = _attr(tag, "name")
    return "[档案: %s]" % nm if nm else "[档案]"


def plain_text(msg):
    """One message -> plain text. Stickers, images, voice, video, files and cards become placeholders, mentions become
    @name. An empty string means nothing to read."""
    t = str(msg.get("msg_type") or "")
    c = str(msg.get("content") or "")
    if t == "image":
        return "[图片]"
    if t == "sticker":
        return "[贴图]"
    if t == "audio":
        return _audio_ph(c)
    if t in ("video", "media"):
        return "[影片]"
    if t == "file":
        return _file_ph(c)
    if t == "interactive":
        m = _CARD_RE.search(c)
        return "[卡片: %s]" % m.group(1) if m else "[卡片]"
    if t == "merge_forward":
        return "[转发的聊天记录]"
    if t in ("share_chat", "share_user", "location", "vote", "todo", "calendar", "share_calendar_event", "video_chat", "hongbao"):
        return "[%s]" % t
    c = _AT_RE.sub(lambda m: "@" + m.group(1).strip(), c)
    c = _IMG_MD_RE.sub("[图片]", c)
    c = _IMG_BR_RE.sub("[图片]", c)
    c = _AUDIO_RE.sub(lambda m: _audio_ph(m.group(0)), c)
    c = _VIDEO_RE.sub("[影片]", c)
    c = _FILE_RE.sub(lambda m: _file_ph(m.group(0)), c)
    c = c.replace("[Sticker]", "[贴图]")
    c = _TAG_RE.sub("", c)
    c = c.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", "\"")
    c = re.sub(r"[ \t]+\n", "\n", c).strip()
    if not c and t:
        return "[%s]" % t
    return c[:MSG_TEXT_MAX]


def flatten_messages(raw):
    """Top level messages plus their thread_replies (each reply has its own create_time), deduplicated by message_id."""
    out, seen = [], set()
    for m in raw or []:
        if not isinstance(m, dict):
            continue
        for x in [m] + [r for r in (m.get("thread_replies") or []) if isinstance(r, dict)]:
            mid = x.get("message_id")
            if mid and mid in seen:
                continue
            if mid:
                seen.add(mid)
            out.append(x)
    return out


def keep_message(msg, day):
    """Keep a message only when: not recalled · sent by a person (sender_type user; bots are app, system messages are
    empty) · create_time falls on `day`."""
    if msg.get("deleted") is True or str(msg.get("deleted")).lower() == "true":
        return False
    snd = msg.get("sender") or {}
    if str(snd.get("sender_type") or "") != "user":
        return False
    return str(msg.get("create_time") or "").startswith(day)


def is_bot_chat(chat, raw_messages, owner):
    """A one to one chat whose other side is a bot: an app wrote that day and no person other than the owner did.
    (A bot chat switched to pull by mistake must not turn the owner's commands into things said to a person.)"""
    if str(chat.get("kind") or "") not in DIRECT_KINDS:
        return False
    has_app, has_other_human = False, False
    for m in raw_messages:
        snd = m.get("sender") or {}
        st = str(snd.get("sender_type") or "")
        if st == "app":
            has_app = True
        elif st == "user" and snd.get("id") not in owner:
            has_other_human = True
    return has_app and not has_other_human


def output_json(stdout, stderr):
    """The lark-cli JSON envelope: from stdout, else from stderr (a failing call may print it there). None when neither
    holds one."""
    j = json_from_output(stdout)
    if isinstance(j, dict):
        return j
    j = json_from_output(stderr)
    return j if isinstance(j, dict) else None


def error_code(j):
    """The numeric API error code of a failed envelope (error.code, error.detail.code or a top level code), else None."""
    if not isinstance(j, dict):
        return None
    err = j.get("error") if isinstance(j.get("error"), dict) else {}
    detail = err.get("detail") if isinstance(err.get("detail"), dict) else {}
    for v in (err.get("code"), detail.get("code"), j.get("code")):
        try:
            if v is not None and str(v).strip() != "":
                return int(str(v).strip())
        except ValueError:
            continue
    return None


def _sender_name(snd):
    name = str(snd.get("name") or "").strip()
    i18n = snd.get("sender_i18n_names") or {}
    if (not name or "@" in name) and (i18n.get("en_us") or i18n.get("zh_cn")):
        name = i18n.get("en_us") or i18n.get("zh_cn")
    return name or str(snd.get("id") or "?")


def fetch_day(cfg, chats, day, runner=None):
    """One local day of the given (pullable) chats. See the module contract for the shape."""
    opts = options(cfg)
    run = runner or default_runner(cfg)
    errors, out, skipped = [], [], []
    failed = 0
    owner = owner_ids(cfg, run)
    if not owner:
        errors.append("owner id unknown (set second_source.lark.owner_ids or log lark-cli in as the owner): "
                      "the owner's messages will look like other people's")
    for c in chats or []:
        cid = str(c.get("chat_id") or "")
        title = str(c.get("title") or c.get("name") or cid)
        if not cid:
            continue
        try:
            rc, stdout, stderr = run(messages_list_args(cid, day, opts["page_limit"]))
        except Exception as e:
            errors.append("%s: runner raised %s" % (title, str(e)[:160]))
            failed += 1
            continue
        j = output_json(stdout, stderr)
        if not isinstance(j, dict):
            errors.append("%s: lark-cli returned no JSON (rc=%s) %s" % (title, rc, (stderr or stdout or "")[-160:].strip()))
            failed += 1
            continue
        if not j.get("ok") and error_code(j) == ENCRYPTED_CHAT_CODE:
            skipped.append({"chat_id": cid, "title": title, "why": ENCRYPTED_CHAT_WHY})
            continue
        if not j.get("ok"):
            e = j.get("error") or j.get("msg") or j
            errors.append("%s: ok=false %s" % (title, json.dumps(e, ensure_ascii=False)[:160]))
            failed += 1
            continue
        raw = flatten_messages((j.get("data") or {}).get("messages") or [])
        pag = (j.get("meta") or {}).get("pagination") or {}
        if pag and pag.get("complete") is False:
            errors.append("%s: pagination incomplete (pages=%s, raise second_source.lark.page_limit)" % (title, pag.get("pages")))
        if is_bot_chat(c, raw, owner):
            skipped.append({"chat_id": cid, "title": title, "why": "one to one chat with a bot"})
            continue
        msgs = []
        for m in raw:
            if not keep_message(m, day):
                continue
            text = plain_text(m)
            if not text:
                continue
            snd = m.get("sender") or {}
            sid = str(snd.get("id") or "")
            msgs.append({"at": str(m.get("create_time") or ""), "sender_id": sid, "sender_name": _sender_name(snd),
                         "is_owner": sid in owner, "text": text})
        msgs.sort(key=lambda x: x["at"])      # stable: within the same minute the API order stays
        if msgs:
            out.append({"chat_id": cid, "title": title, "kind": str(c.get("kind") or ""), "messages": msgs})
    return {"chats": out, "errors": errors, "failed_chats": failed, "skipped": skipped, "owner_ids": sorted(owner)}


# ---------------------------------------------------------------- members (read only, for the id mapping)

def members_args(chat_id):
    return ["im", "+chat-members-list", "--chat-id", chat_id, "--page-all", "--page-limit", "0", "--format", "json", "--as", "user"]


def members(cfg, chats, runner=None, max_users=MAX_MEMBERS):
    """Members of the given chats: people and bots apart. Chats with more than max_users people (public communities)
    are left out so strangers do not flood the mapping."""
    run = runner or default_runner(cfg)
    users, bots, errors = {}, {}, []
    for c in chats or []:
        cid = str(c.get("chat_id") or "")
        title = str(c.get("title") or c.get("name") or cid)
        try:
            rc, stdout, stderr = run(members_args(cid))
        except Exception as e:
            errors.append("%s: runner raised %s" % (title, str(e)[:160]))
            continue
        j = json_from_output(stdout)
        if not isinstance(j, dict) or not j.get("ok"):
            errors.append("%s: %s" % (title, (stderr or stdout or "")[-160:].strip()))
            continue
        d = j.get("data") or {}
        if d.get("truncations"):
            errors.append("%s: member list truncated %s" % (title, json.dumps(d["truncations"], ensure_ascii=False)))
        us = d.get("users") or []
        if len(us) > max_users:
            errors.append("%s: %d people, too large, left out" % (title, len(us)))
            continue
        for u in us:
            e = users.setdefault(u.get("member_id"), {"name": u.get("name"), "chats": []})
            e["chats"].append(title)
        for b in d.get("bots") or []:
            e = bots.setdefault(b.get("member_id"), {"name": b.get("name"), "app_id": b.get("app_id"), "chats": []})
            e["chats"].append(title)
    return {"users": users, "bots": bots, "errors": errors}


# ---------------------------------------------------------------- selftest (invented data only)

def _fake_msg(mid, time, sid, name, content, msg_type="text", sender_type="user", **kw):
    m = {"chat_id": "oc_test_x", "content": content, "create_time": time, "deleted": False, "message_id": mid, "msg_type": msg_type,
         "sender": {"id": sid, "id_type": "open_id" if sender_type == "user" else ("app_id" if sender_type == "app" else ""),
                    "name": name, "sender_type": sender_type, "tenant_key": "tenant_test"}, "updated": False}
    m.update(kw)
    return m


def _envelope(msgs, complete=True):
    """Same shape as lark-cli 1.0.89 stdout: ok · identity · data.messages · meta.pagination · _notice."""
    return json.dumps({"ok": True, "identity": "user",
                       "data": {"has_more": False, "messages": msgs, "page_token": "", "total": len(msgs)},
                       "meta": {"pagination": {"complete": complete, "pages": 1, "items": len(msgs)}},
                       "_notice": {"update": {"command": "lark-cli update", "current": "1.0.89", "latest": "1.0.95"}}},
                      ensure_ascii=False, indent=2)


def selftest_fixture():
    """Invented whitelist, messages and a fake runner shared by this selftest and wa_second_source's."""
    ids = {"owner": "ou_test_owner", "alex": "ou_test_alex", "maya": "ou_test_maya", "sam": "ou_test_sam", "bot": "cli_test_bot"}
    O, A, M, S, B = ids["owner"], ids["alex"], ids["maya"], ids["sam"], ids["bot"]
    whitelist = {"rule": "selftest", "decided_at": "2026-09-14", "chats": [
        {"chat_id": "oc_test_support", "name": "Workspace Customer Support", "kind": "群", "pull": True},
        {"chat_id": "oc_test_mybot", "name": "My Assistant Bot", "kind": "一对一", "pull": False},
        {"chat_id": "oc_test_team", "name": "Project Maple Team", "kind": "群", "pull": True},
        {"chat_id": "oc_test_fin", "name": "Maple Finance Sync", "kind": "群", "pull": "true"},
        {"chat_id": "oc_test_err", "name": "Broken Chat", "kind": "一对一", "pull": True},
        {"chat_id": "oc_test_empty", "name": "Quiet Contact", "kind": "一对一", "pull": True},
        {"chat_id": "oc_test_botp2p", "name": "Ledger Bot", "kind": "一对一", "pull": True},
        {"chat_id": "oc_test_skip", "name": "Skip Me", "kind": "群", "pull": True},
    ]}
    team = [
        _fake_msg("om_0", "2026-09-12 23:59", A, "Alex Test", "the day before does not count"),
        _fake_msg("om_1", "2026-09-13 11:44", A, "Alex Test", "Hi, my friend’s team wants to join too, same price of 800?"),
        _fake_msg("om_2", "2026-09-13 11:46", O, "Owner Test", "<audio key=\"file_test_1\" duration=\"20s\"/>", msg_type="audio",
                  update_time="2026-09-13 11:46", updated=True),
        _fake_msg("om_3", "2026-09-13 11:50", B, "Status Bot", "<card title=\"project status\">x</card>", msg_type="interactive", sender_type="app"),
        _fake_msg("om_4", "2026-09-13 11:51", "", "", "Owner Test invited Status Bot to the group.", msg_type="system", sender_type=""),
        _fake_msg("om_5", "2026-09-13 11:52", A, "Alex Test", "recalled", deleted=True),
        _fake_msg("om_6", "2026-09-13 12:04", A, "Alex Test", "Can", thread_id="omt_1",
                  thread_replies=[_fake_msg("om_7", "2026-09-13 12:10", O, "Owner Test", "ok 我今晚把 10 月的日期发你", thread_id="omt_1")]),
    ]
    fin = [_fake_msg("om_f1", "2026-09-13 10:39", M, "Maya Test", "Update for the renewal list:\n- backfill needed, completed", msg_type="post"),
           _fake_msg("om_f2", "2026-09-13 10:40", S, "Sam Test", "[Image: img_test_x]", msg_type="image")]
    botp2p = [_fake_msg("om_b1", "2026-09-13 09:00", O, "Owner Test", "log lunch 12"),
              _fake_msg("om_b2", "2026-09-13 09:00", "cli_test_ledger", "Ledger Bot", "logged", sender_type="app")]
    mem = json.dumps({"ok": True, "data": {"bots": [{"app_id": "cli_test_app", "member_id": "ou_test_botmember", "name": "Status Bot"}],
                                           "truncations": [], "user_total": 2,
                                           "users": [{"member_id": A, "name": "Alex Test", "tenant_key": "t"},
                                                     {"member_id": M, "name": "Maya Test", "tenant_key": "t"}]}})

    def make_runner(calls=None, messages_override=None, fail_all=False):
        def runner(args):
            if calls is not None:
                calls.append(list(args))
            if args[:2] == ["auth", "status"]:
                return 0, json.dumps({"identities": {"user": {"openId": O, "available": True}}}), ""
            if args[:2] == ["im", "+chat-members-list"]:
                return 0, "[page 1] fetching...\n" + mem, ""
            if fail_all:
                return 1, json.dumps({"ok": False, "error": {"message": "need_user_authorization"}}), ""
            cid = args[args.index("--chat-id") + 1]
            if messages_override is not None:
                return 0, _envelope(messages_override.get(cid, [])), ""
            if cid == "oc_test_team":
                return 0, _envelope(team), "[page 1] fetching...\n"
            if cid == "oc_test_fin":
                return 0, "[page 1] fetching...\n" + _envelope(fin, complete=False), ""
            if cid == "oc_test_err":
                return 1, json.dumps({"ok": False, "identity": "user", "error": {"type": "auth", "message": "token expired"}}), ""
            if cid == "oc_test_botp2p":
                return 0, _envelope(botp2p), ""
            return 0, _envelope([]), ""
        return runner

    return {"ids": ids, "whitelist": whitelist, "day": "2026-09-13", "make_runner": make_runner, "team": team, "fin": fin}


def _selftest(check=None, tmp=None):
    own = check is None
    fails, n = [], [0]
    if own:
        def check(name, cond):
            n[0] += 1
            print(("  ok   " if cond else "  FAIL ") + name)
            if not cond:
                fails.append(name)
    fx = selftest_fixture()
    ids, day = fx["ids"], fx["day"]
    cfg = {"second_source": {"enabled": True, "source": "lark", "lark": {"page_limit": 50}}}

    check("lark: voice keeps its duration", plain_text({"msg_type": "audio", "content": "<audio key=\"file_test\" duration=\"20s\"/>"}) == "[语音 20s]")
    check("lark: image placeholder", plain_text({"msg_type": "image", "content": "[Image: img_test]"}) == "[图片]")
    check("lark: file keeps its name", plain_text({"msg_type": "file", "content": "<file key=\"file_test\" name=\"Quote (draft).pdf\"/>"}) == "[档案: Quote (draft).pdf]")
    check("lark: image inside a post", plain_text({"msg_type": "post", "content": "@Sam \n![Image](img_test)"}) == "@Sam\n[图片]")
    check("lark: <at> becomes @name", plain_text({"msg_type": "post", "content": "<at user_id=\"ou_test_1\">Sam Test</at> hello"}) == "@Sam Test hello")
    check("lark: card keeps its title", plain_text({"msg_type": "interactive", "content": "<card title=\"weekly status\" subtitle=\"x\">\nbody</card>"}) == "[卡片: weekly status]")
    check("lark: long text cut to 2000", len(plain_text({"msg_type": "text", "content": "x" * 3000})) == MSG_TEXT_MAX)

    calls = []
    runner = fx["make_runner"](calls)
    wl = fx["whitelist"]["chats"]
    pull = [c for c in wl if c["chat_id"] in ("oc_test_team", "oc_test_fin", "oc_test_err", "oc_test_empty", "oc_test_botp2p")]
    f = fetch_day(cfg, pull, day, runner=runner)
    check("lark: zero message, failing and bot chats are left out; got %s" % [c["chat_id"] for c in f["chats"]],
          [c["chat_id"] for c in f["chats"]] == ["oc_test_team", "oc_test_fin"])
    team = f["chats"][0] if f["chats"] else {"messages": []}
    check("lark: drops the day before, bot cards, system messages and recalls; flattens thread replies",
          [m["text"] for m in team["messages"]] == ["Hi, my friend’s team wants to join too, same price of 800?", "[语音 20s]", "Can", "ok 我今晚把 10 月的日期发你"])
    check("lark: owner messages marked is_owner (id from auth status)", [m["is_owner"] for m in team["messages"]] == [False, True, False, True])
    check("lark: normalized message keys", set(team["messages"][0].keys()) == {"at", "sender_id", "sender_name", "is_owner", "text"} if team["messages"] else False)
    check("lark: title and kind carried", team.get("title") == "Project Maple Team" and team.get("kind") == "群")
    check("lark: a failing chat is an error and counts as failed", f["failed_chats"] == 1 and any("Broken Chat" in e and "token expired" in e for e in f["errors"]))
    check("lark: incomplete pagination is an error but not a failed chat", any("pagination incomplete" in e for e in f["errors"]))
    check("lark: a one to one bot chat is skipped", f["skipped"] == [{"chat_id": "oc_test_botp2p", "title": "Ledger Bot", "why": "one to one chat with a bot"}])
    check("lark: owner ids reported", f["owner_ids"] == [ids["owner"]])
    ml = [c for c in calls if c[:2] == ["im", "+chat-messages-list"]]
    check("lark: always --order asc", ml and all(c[c.index("--order") + 1] == "asc" for c in ml))
    a0 = ml[0] if ml else []
    check("lark: local day window, --page-all, --page-limit 50, --as user",
          bool(a0) and a0[a0.index("--start") + 1].startswith("2026-09-13T00:00:00") and a0[a0.index("--end") + 1].startswith("2026-09-14T00:00:00")
          and "--page-all" in a0 and a0[a0.index("--page-limit") + 1] == "50" and a0[a0.index("--as") + 1] == "user")
    cfg_ids = {"second_source": {"lark": {"owner_ids": ["ou_test_configured"]}}}
    check("lark: configured owner_ids win over auth status", owner_ids(cfg_ids, runner=lambda a: (_ for _ in ()).throw(AssertionError("not called"))) == {"ou_test_configured"})
    check("lark: no owner id is reported as an error, the pull goes on",
          any("owner id unknown" in e for e in fetch_day(cfg, pull[:1], day, runner=lambda a: (0, "{}", "") if a[:2] == ["auth", "status"] else runner(a))["errors"]))
    boom = fetch_day(cfg, pull[:1], day, runner=lambda a: runner(a) if a[:2] == ["auth", "status"] else (_ for _ in ()).throw(OSError("spawn failed")))
    check("lark: a raising runner becomes an error, never an exception", boom["failed_chats"] == 1 and any("spawn failed" in e for e in boom["errors"]))
    check("lark: progress text before the JSON is skipped", json_from_output("[page 1] fetching {x}\n{\"ok\": true}") == {"ok": True})

    def err_runner(where, code):
        """Every chat fails with rc 1 and the envelope on `where` (stdout or stderr), API error `code`."""
        env = json.dumps({"ok": False, "identity": "user", "error": {"type": "api", "code": code, "message": "invented failure"}})

        def run(a):
            if a[:2] == ["auth", "status"]:
                return runner(a)
            return (1, env, "") if where == "stdout" else (1, "", "[page 1] fetching...\n" + env)
        return run
    one = [{"chat_id": "oc_test_err", "name": "Broken Chat", "kind": "一对一", "pull": True}]
    se = fetch_day(cfg, one, day, runner=err_runner("stderr", 99991663))
    check("lark: JSON on stderr is read (the API error is reported, not 'no JSON')",
          se["failed_chats"] == 1 and any("99991663" in e for e in se["errors"]) and not any("no JSON" in e for e in se["errors"]))
    for where in ("stdout", "stderr"):
        enc = fetch_day(cfg, one, day, runner=err_runner(where, ENCRYPTED_CHAT_CODE))
        check("lark: an encrypted chat (code 231203, JSON on %s) is skipped, not an error, not a failed chat" % where,
              enc["failed_chats"] == 0 and not [e for e in enc["errors"] if "Broken Chat" in e]
              and enc["skipped"] == [{"chat_id": "oc_test_err", "title": "Broken Chat", "why": "encrypted chat, the Lark API cannot read it"}])
    check("lark: error code read from error.detail.code and a string code",
          error_code({"ok": False, "error": {"detail": {"code": "231203"}}}) == 231203 and error_code({"ok": False}) is None)

    pm = members(cfg, [{"chat_id": "oc_test_team", "name": "Project Maple Team"}], runner=runner)
    check("lark: members keeps people and bots apart", set(pm["users"]) == {ids["alex"], ids["maya"]} and "ou_test_botmember" in pm["bots"])
    big = json.dumps({"ok": True, "data": {"users": [{"member_id": "ou_test_%d" % i, "name": "p%d" % i} for i in range(301)]}})
    pm2 = members(cfg, [{"chat_id": "oc_test_big", "name": "Big Community"}], runner=lambda a: (0, big, ""))
    check("lark: a chat over 300 people is left out", pm2["users"] == {} and any("too large" in e for e in pm2["errors"]))
    if own:
        print("selftest: %d/%d passed" % (n[0] - len(fails), n[0]))
        return 1 if fails else 0
    return True


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(_selftest())
    print("usage: second_source_lark.py --selftest   (normally used through wa_second_source.py)")
    sys.exit(0)
