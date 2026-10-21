#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wa_vault_write.py · the optional write-to-vault button of the Today page (station O7).

A background program that writes into your notes must be able to prove what it changed and give it back. So this
module writes only a Today card the owner pressed OK on, only when the owner switched the button on
(`today.write_mode` direct or queue), and only when the owner's own doctrine schema reader (my-second-brain's
`doctrine_schema.py`, read in place, never copied) and PyYAML both load. Otherwise nothing is written and the reason
is said (fail closed: no file is written before every check passes, and a failure after the write restores the files).

What it writes, by program and never by the model:
  update   one existing task file in the card's project: frontmatter keys from the card (status, due, waiting_on,
           priority, the doctrine's status list only) set in place, `updated:` when the doctrine's task family has it,
           and one section appended at the end; the body above that section stays byte for byte as it was
  create   one new task file `<project>/Tasks/<Name>.md` with the doctrine task family's required keys
  drop     no note at all: the card's intake entries are placed `→ drop`
  seed     never (seeds are placed in the owner's own session)
Other people's words never enter a task file: every text is scrubbed against the quotes of the entries the card cites
and the messages of its conversations (wa_today's reply scrub, the one rule in one place). The evidence lines of a
card are never written.

Order of one apply (files whole or nothing; an entry placed before a later failure keeps its placed line and gains a rollback line; under `<live>/today/write.lock`):
  availability → card (status ok, rev, pin = the content hash pinned at the press) → kind → keys placed elsewhere →
  sources and scrub → render → schema check → backup (manifest first) → atomic write → md5 read back → entries placed
  by key → filing log line → card row `written`. A failure after the backup restores every file from the backup.
Undo restores a file only when it is exactly what the write left (md5), moves a created file into the backup folder
(never deletes), and appends one undo line under each entry the write had placed (they stay placed).

Backups: `<home>/vault-write-backups/<card id>/<timestamp>/manifest.json` plus a copy of every file before the change.
Queue mode: a press appends to `<live>/today/pending-writes.json`; the owner's session applies it with
  wa_vault_write.py --apply-pending --config <config>

CLI
  wa_vault_write.py --availability --config C [--lang en]   the availability dict; exit 0 when ok, else 2
  wa_vault_write.py --apply-pending --config C              apply the queue (the owner's session only)
  wa_vault_write.py --undo <card id> --config C             give one write back
  wa_vault_write.py --selftest                              temp files only, synthetic data only
Python 3.9 standard library plus the kit's modules; PyYAML only inside load_yaml(), the one optional exception.
"""

import argparse
import contextlib
import copy
import datetime as dt
import errno
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wa_common as WC  # noqa: E402
import wa_intake as WI  # noqa: E402
import wa_today as WT  # noqa: E402

VERSION = "1.4.0"

WRITE_MODES = ("off", "direct", "queue")
WRITE_KINDS = ("update", "create", "drop")
SECTION_TAG = "WhatsApp Live"
DROP_TO = "drop"
BACKUPS = "vault-write-backups"
PENDING = "pending-writes.json"
LOCK_WAIT_SECONDS = 10
GENERIC_HINT = "%s -m pip install --user pyyaml" % (sys.executable or "/usr/bin/python3")
READER_REL = (".claude", "skills", "my-second-brain", "scripts", "doctrine_schema.py")
TASK_MARKER_DEFAULT = "cb: task"
TASK_REQUIRED_DEFAULT = ("cb", "status", "created")
FIRST_STATUS = "not-started"
SCRUB_REF = "(…)"
NAME_MAX = 60
CODES = ("off", "today_off", "no_vault", "no_doctrine", "no_reader", "reader_error", "no_yaml", "schema_error",
         "no_filing_log", "no_backup_dir", "module_error", "ok")

L = {
    "zh": {
        "avail": {
            "off": "写进 vault 的按钮没开 (today.write_mode 是 off)",
            "today_off": "Today 页没开 (today.enabled 是 false), 按钮不出现",
            "no_vault": "paths.vault 没设或不是资料夹",
            "no_doctrine": "paths.doctrine 读不到, 不知道任务档的规矩",
            "no_reader": "找不到 my-second-brain 的 doctrine_schema.py (paths.schema_reader 或两个预设位置)",
            "reader_error": "doctrine_schema.py 载入失败: %s",
            "no_yaml": "这个 Python 没有 PyYAML, 读不了宪法第 8 节; 在你的 session 里同意后再装: %s",
            "schema_error": "宪法第 8 节读不出任务的规矩: %s",
            "no_filing_log": "paths.filing_log 没设或它的资料夹不在, 写了也留不下记录",
            "no_backup_dir": "备份资料夹建不起来: %s",
            "module_error": "检查写入条件时出错: %s",
            "ok": "可以写 (%s)",
        },
        "refuse": {
            "busy": "另一笔写入正在进行, 稍后再按",
            "not_found": "找不到这张卡",
            "status": "这张卡现在不是「好」的状态 (%s), 不写",
            "rev": "卡的内容在你按之后变了 (版本 %s), 重新看过再按",
            "pin": "卡的内容跟你按「好」时不一样了, 整张不写",
            "kind": "这种卡不由板子写 (%s)",
            "seed": "新 Project 种子在你自己的 session 里处理",
            "no_keys": "「不用归」的卡没有条目",
            "no_project": "卡的 Project 在 vault 里对不上 (%s)",
            "no_task": "任务档不在或不是一般档案 (%s)",
            "same_name": "vault 里已经有同名的档 (%s), 不新建",
            "quote_in_name": "档名里有别人的原话, 不新建",
            "no_buffer": "intake buffer 读不到, 条目无法核对, 不写",
            "foreign": "有条目已经归到别处: %s",
            "render": "任务档的内容排不出来: %s",
            "schema": "改完的任务档不合宪法第 8 节: %s",
            "backup": "备份做不出来, 一个字都没写: %s",
            "changed": "任务档在写之前被改过, 已还原, 重新按",
            "write": "写档失败, 已从备份还原: %s",
            "readback": "写完读回的内容对不上, 已从备份还原",
            "intake": "条目标记失败, 已从备份还原: %s",
            "filing_log": "filing log 写不进去, 已从备份还原: %s",
            "state": "卡的状态存不进去, 已从备份还原: %s",
            "not_written": "这张卡没有写过, 没有可撤回的",
            "changed_since": "档在写入之后被改过 (%s), 不撤回",
            "no_vault": "paths.vault 没设或不是资料夹",
        },
        "written": "已写进 vault: %s",
        "dropped_note": "已标为不用归: %d 条",
        "undone": "已撤回: %s",
        "source": "来源 (WhatsApp Live): %s",
        "source_keys": "条目 %s",
        "sep": "、",
        "fm_drop_key": "%s 不在可改的栏位里, 拿掉",
        "fm_drop_status": "状态 %s 不在任务状态表里, 拿掉",
        "log_update": "更新任务",
        "log_create": "新建任务",
        "log_drop": "条目标为不用归 (%d 条)",
        "log_write": "- %s · 板子写入: %s `%s` · 卡 %s 第 %s 版 · 依据: 你在 Today 页按了好, 程序照卡写入, 不经模型, 先做了备份 · 备份: %s\n",
        "log_undo": "- %s · 板子撤回: %s `%s` · 卡 %s · 依据: 你按了撤回, 程序照备份还原 · 备份: %s\n",
        "undo_line": "- 撤回 %s: 板子写进 %s 的那次已撤回; 这条仍算已归, 要不要重新归位在你的 session 决定",
        "rollback_line": "- 撤回 %s: 板子写进 %s 没写成, 已还原; 这条仍算已归, 要不要重新归位在你的 session 决定",
        "queued": "已排队, 在你的 session 里跑 --apply-pending 写入",
    },
    "en": {
        "avail": {
            "off": "the write to vault button is off (today.write_mode is off)",
            "today_off": "the Today page is off (today.enabled is false), so there is no button",
            "no_vault": "paths.vault is not set or is not a folder",
            "no_doctrine": "paths.doctrine cannot be read, so the rules of a task note are unknown",
            "no_reader": "my-second-brain's doctrine_schema.py was not found (paths.schema_reader or the two default places)",
            "reader_error": "doctrine_schema.py could not be loaded: %s",
            "no_yaml": "this Python has no PyYAML, so section 8 of the doctrine cannot be read; install it in your session on your yes: %s",
            "schema_error": "section 8 of the doctrine does not give the task rules: %s",
            "no_filing_log": "paths.filing_log is not set or its folder is missing, so a write would leave no record",
            "no_backup_dir": "the backup folder cannot be created: %s",
            "module_error": "checking whether a write can run failed: %s",
            "ok": "ready to write (%s)",
        },
        "refuse": {
            "busy": "another write is running, press again in a moment",
            "not_found": "this card was not found",
            "status": "this card is not in the OK state (%s), nothing written",
            "rev": "the card changed after you pressed it (version %s), look again and press",
            "pin": "the card is not what it was when you pressed OK, nothing written",
            "kind": "the board does not write this kind of card (%s)",
            "seed": "seeds are placed in your own session",
            "no_keys": "this drop card cites no entry",
            "no_project": "the card's project is not in the vault (%s)",
            "no_task": "the task note is missing or not a plain file (%s)",
            "same_name": "a note of that name already exists in the vault (%s), nothing created",
            "quote_in_name": "the file name repeats someone's words, nothing created",
            "no_buffer": "the intake buffer cannot be read, the entries cannot be checked, nothing written",
            "foreign": "some entries are already placed elsewhere: %s",
            "render": "the task note could not be rendered: %s",
            "schema": "the changed task note breaks section 8 of the doctrine: %s",
            "backup": "the backup could not be made, nothing written: %s",
            "changed": "the task note changed just before the write; restored, press again",
            "write": "the write failed and was restored from the backup: %s",
            "readback": "what was read back is not what was written; restored from the backup",
            "intake": "placing the entries failed; restored from the backup: %s",
            "filing_log": "the filing log line could not be written; restored from the backup: %s",
            "state": "the card state could not be saved; restored from the backup: %s",
            "not_written": "this card was not written, there is nothing to undo",
            "changed_since": "the note changed since the write (%s), not undone",
            "no_vault": "paths.vault is not set or is not a folder",
        },
        "written": "written into the vault: %s",
        "dropped_note": "marked as no placing needed: %d entries",
        "undone": "undone: %s",
        "source": "Source (WhatsApp Live): %s",
        "source_keys": "entries %s",
        "sep": ", ",
        "fm_drop_key": "%s is not a key the board may change, dropped",
        "fm_drop_status": "status %s is not in the task status list, dropped",
        "log_update": "task updated",
        "log_create": "task created",
        "log_drop": "entries dropped (%d)",
        "log_write": "- %s · board write: %s `%s` · card %s version %s · rule: the owner pressed OK on the Today card; written by program, not by the model, after a backup · backup: %s\n",
        "log_undo": "- %s · board undo: %s `%s` · card %s · rule: the owner pressed Undo; restored by program from the backup · backup: %s\n",
        "undo_line": "- undone %s: the board write into %s was undone; this entry stays placed, whether to place it again is decided in your session",
        "rollback_line": "- undone %s: the board write into %s did not complete and was restored; this entry stays placed, whether to place it again is decided in your session",
        "queued": "queued; run --apply-pending in your session to write it",
    },
}


def _L(lang):
    return L["en" if str(lang or "").lower().startswith("en") else "zh"]


class VaultWriteUnavailable(RuntimeError):
    """The write cannot run at all (PyYAML or the schema reader missing). Carries the hint text for the owner."""


class Refused(Exception):
    """One refused check; the whole card is refused."""

    def __init__(self, code, detail=""):
        Exception.__init__(self, code)
        self.code, self.detail = code, str(detail or "")


class _Step(Exception):
    """A failure after the backup: the files are restored before the refusal is returned."""

    def __init__(self, code, detail=""):
        Exception.__init__(self, code)
        self.code, self.detail = code, str(detail or "")


# ---------------------------------------------------------------- small helpers

def _now(now=None):
    if isinstance(now, dt.datetime):
        return now.replace(microsecond=0, tzinfo=None)
    if now:
        try:
            return dt.datetime.fromisoformat(str(now).strip().replace(" ", "T")[:19])
        except ValueError:
            pass
    return dt.datetime.now().replace(microsecond=0)


def _md5(b):
    return hashlib.md5(b).hexdigest()


def _md5_file(p):
    try:
        with open(str(p), "rb") as f:
            return _md5(f.read())
    except OSError:
        return None


def _x(p):
    return Path(os.path.expanduser(str(p or "")))


def _paths(cfg):
    return (cfg or {}).get("paths") or {}


def _tc(cfg):
    t = (cfg or {}).get("today")
    return t if isinstance(t, dict) else {}


def write_mode(cfg):
    m = str(_tc(cfg).get("write_mode") or "off").strip().lower()
    return m if m in WRITE_MODES else "off"


def _home(cfg):
    return WC.home_dir(cfg)


def backups_root(cfg):
    return _home(cfg) / BACKUPS


def _rel(vault, p):
    return Path(p).resolve().relative_to(Path(vault).resolve()).as_posix()


def _inside(vault, p):
    try:
        Path(p).resolve().relative_to(Path(vault).resolve())
        return True
    except (ValueError, OSError):
        return False


def _prop(row):
    """A card row with its JSON columns read (fm_set dict, entry_keys and jids lists)."""
    r = dict(row or {})
    for k, d in (("fm_set", {}), ("entry_keys", []), ("jids", [])):
        v = r.get(k)
        if isinstance(v, str) or v is None:
            r[k] = WT._jl(v, d)
    return r


def _ensure_cols(conn):
    """The two v1.4.0 columns on an older table (the same 'old database gains a column' pattern as wa_today)."""
    have = set(r.get("name") for r in WT._rows(conn, "PRAGMA table_info(today_proposals)"))
    if not have:
        return
    for col in ("written_json", "pin_hash"):
        if col not in have:
            conn.execute("ALTER TABLE today_proposals ADD COLUMN %s TEXT" % col)
    conn.commit()


def _card(conn, pid):
    r = WT._rows(conn, "SELECT * FROM today_proposals WHERE id=?", (str(pid or ""),))
    return r[0] if r else None


@contextlib.contextmanager
def _flock(path, wait=LOCK_WAIT_SECONDS):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = time.time() + wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
                    raise
                if time.time() > deadline:
                    raise Refused("busy")
                time.sleep(0.05)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _write_bytes(path, data, mode, create=False):
    """Atomic write: a temp file in the same folder, fsync, chmod, then os.replace (update) or a hard link that
    fails when the name exists (create, so a note that appeared meanwhile is never overwritten)."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        if create:
            os.link(tmp, str(path))
            os.unlink(tmp)
        else:
            os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------- PyYAML and the owner's schema reader

def load_yaml():
    """The yaml module of the daemon's interpreter, or VaultWriteUnavailable. The only place this module imports it."""
    try:
        import yaml  # noqa: F401
    except ImportError:
        raise VaultWriteUnavailable(GENERIC_HINT)
    if yaml is None:
        raise VaultWriteUnavailable(GENERIC_HINT)
    return yaml


def reader_path(cfg):
    """(path or None, configured): paths.schema_reader when set, else the two default places in order."""
    p = _paths(cfg)
    set_to = str(p.get("schema_reader") or "").strip()
    if set_to:
        x = _x(set_to)
        return (x if x.is_file() else None), True
    cands = []
    if str(p.get("vault") or "").strip():
        cands.append(_x(p.get("vault")).joinpath(*READER_REL))
    cands.append(Path(os.path.expanduser("~")).joinpath(*READER_REL))
    for c in cands:
        if c.is_file():
            return c, False
    return None, False


def load_reader(path):
    """The owner's doctrine_schema.py as a module, loaded from where it lives (never copied)."""
    path = Path(path)
    st = os.stat(str(path))
    name = "wa_vw_reader_" + hashlib.sha1(("%s|%s" % (path, st.st_mtime_ns)).encode("utf-8")).hexdigest()[:12]
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError("not a Python module: %s" % path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for attr in ("load_schema", "validate", "SchemaError"):
        if not hasattr(mod, attr):
            raise ImportError("doctrine_schema.py has no %s" % attr)
    return mod


def _hint(mod):
    try:
        h = mod.install_hint()
        return str(h[0] if isinstance(h, (list, tuple)) else h)
    except Exception:
        return GENERIC_HINT


def _schema_args(vault, doctrine):
    """(meta_dir, doctrine_name) so the reader reads paths.doctrine when it lies inside the vault."""
    try:
        rel = Path(doctrine).resolve().relative_to(Path(vault).resolve())
        parent = rel.parent.as_posix()
        return (parent if parent != "." else ""), rel.name
    except (ValueError, OSError, TypeError):
        return "99_Meta", "structure-doctrine.md"


class Law(object):
    """The doctrine's section 8, read once through the owner's reader; check_task(text) judges one task note."""

    def __init__(self, vault, schema_reader_path, doctrine=None):
        self.vault = str(_x(vault))
        self.module = load_reader(schema_reader_path)
        load_yaml()
        meta, name = _schema_args(self.vault, doctrine) if doctrine else ("99_Meta", "structure-doctrine.md")
        unavailable = getattr(self.module, "SchemaUnavailable", None)
        try:
            self.schema = self.module.load_schema(self.vault, meta_dir=meta, doctrine_name=name)
        except Exception as e:
            if unavailable is not None and isinstance(e, unavailable):
                raise VaultWriteUnavailable(str(e))
            raise
        mk = getattr(self.schema, "marker_key", None) or TASK_MARKER_DEFAULT.split(":")[0]
        by = getattr(self.schema, "by_marker", {}) or {}
        self.spec = by.get("%s: task" % mk) or by.get(TASK_MARKER_DEFAULT)
        if self.spec is None:
            raise self.module.SchemaError("section 8 declares no task family (marker %s: task)" % mk)
        self.marker = str(getattr(self.spec, "marker", "") or "%s: task" % mk)
        self.required = list(getattr(self.spec, "required", []) or TASK_REQUIRED_DEFAULT)
        self.keys = self.required + [k for k in (getattr(self.spec, "optional", []) or []) if k not in self.required]

    def check_task(self, text):
        """(ok, reason): the frontmatter parses as YAML, claims the task family, and validate() finds nothing."""
        try:
            fm, _body = WC.parse_frontmatter(text, lists=True)
            if not fm:
                return False, "no_frontmatter"
            head = _head_text(text)
            loaded = load_yaml().safe_load(head)
            if not isinstance(loaded, dict):
                return False, "yaml: the frontmatter is not a mapping"
            spec = self.schema.spec_for(fm)
            if spec is None:
                return False, "no_family"
            if spec is not self.spec:
                return False, "not_task: %s" % getattr(spec, "name", "?")
            problems = self.module.validate(fm, spec, self.schema)
            if problems:
                return False, "; ".join(str(m[1] if isinstance(m, (list, tuple)) and len(m) > 1 else m) for m in problems)
            return True, ""
        except VaultWriteUnavailable as e:
            return False, "no_yaml: %s" % e
        except Exception as e:
            return False, "%s: %s" % (type(e).__name__, e)


def check_with_schema(text, law):
    """(ok, reason) for one rendered task note (any SchemaError or problem is a refusal)."""
    if law is None:
        return False, "no_law"
    return law.check_task(text)


# ---------------------------------------------------------------- availability

def availability(cfg, lang="zh"):
    """{ok, code, reason, schema_reader, yaml, mode, install_hint}. Pure read except creating the backup folder.
    Never raises: anything unforeseen is code `module_error` with the exception text in `reason`."""
    try:
        return _availability(cfg, lang)
    except BaseException as e:
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        return {"ok": False, "code": "module_error", "reason": _L(lang)["avail"]["module_error"] % ("%s: %s" % (type(e).__name__, e)),
                "schema_reader": "", "yaml": False, "mode": write_mode(cfg) if isinstance(cfg, dict) else "off", "install_hint": ""}


def _availability(cfg, lang):
    A = _L(lang)["avail"]
    mode = write_mode(cfg)
    out = {"ok": False, "code": "", "reason": "", "schema_reader": "", "yaml": False, "mode": mode, "install_hint": ""}

    def done(code, *args):
        out["code"] = code
        msg = A[code]
        out["reason"] = (msg % args) if args else msg.replace("%s", "").strip()
        out["ok"] = code == "ok"
        return out

    if mode not in ("direct", "queue"):
        return done("off")
    if _tc(cfg).get("enabled") is not True:
        return done("today_off")
    p = _paths(cfg)
    vault = str(p.get("vault") or "").strip()
    if not vault or not _x(vault).is_dir():
        return done("no_vault")
    doctrine = str(p.get("doctrine") or "").strip()
    if not doctrine or not _x(doctrine).is_file():
        return done("no_doctrine")
    rp, _configured = reader_path(cfg)
    if rp is None:
        return done("no_reader")
    out["schema_reader"] = str(rp)
    try:
        mod = load_reader(rp)
    except BaseException as e:   # a broken reader file may raise anything, SyntaxError included
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        return done("reader_error", "%s: %s" % (type(e).__name__, e))
    try:
        load_yaml()
        out["yaml"] = True
    except VaultWriteUnavailable:
        out["install_hint"] = _hint(mod)
        return done("no_yaml", out["install_hint"])
    try:
        Law(vault, rp, doctrine)
    except VaultWriteUnavailable as e:
        out["yaml"] = False
        out["install_hint"] = _hint(mod)
        return done("no_yaml", out["install_hint"])
    except Exception as e:
        return done("schema_error", str(e))
    flog = str(p.get("filing_log") or "").strip()
    if not flog or not _x(flog).parent.is_dir():
        return done("no_filing_log")
    b = backups_root(cfg)
    try:
        b.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not b.is_dir():
            raise OSError("not a folder: %s" % b)
        os.chmod(str(b), 0o700)
    except OSError as e:
        return done("no_backup_dir", str(e))
    return done("ok", mode)


# ---------------------------------------------------------------- the pin, the scrub, the sources

def pin_hash(row):
    """The card's content hash exactly as wa_today computes it (wa_today._chash on the row's content)."""
    return WT._chash(_prop(row))


def scrub_for_vault(text, sources, min_chars):
    """Every stretch of `text` that repeats a source (min_chars CJK characters, or 4 Latin words) becomes `(…)`.
    wa_today's reply scrub (built on _reply_index_cached, _hit_spans and _stream): one rule in one place."""
    if not text:
        return text or ""
    srcs = [str(s) for s in (sources or []) if str(s or "").strip()]
    if not srcs:
        return str(text)
    return WT.scrub_replies(str(text), srcs, int(min_chars), SCRUB_REF)


_SEG_RE = re.compile("「([^「」\n]*)」|\"([^\"\n]*)\"")


def _entry_blocks(buffer_path, keys):
    """{key: entry dict} for the given keys, open zone and placed zone (wa_intake's own readers)."""
    want = set(keys or [])
    out = {}
    if not want or not str(buffer_path or "").strip() or not _x(buffer_path).is_file():
        return out
    for e in list(WI.list_entries(buffer_path)) + list(WI.list_placed(buffer_path)):
        k = e.get("key") or WI.entry_key(e)
        if k in want and k not in out:
            out[k] = e
    return out


def _pack_texts(sweep_json, jid):
    """The words of the other people's messages in one run's own pack (sweep.json)."""
    doc = json.loads(Path(sweep_json).read_text(encoding="utf-8"))
    chats = [c for c in (doc.get("chats") or []) if isinstance(c, dict)]
    pick = [c for c in chats if c.get("jid") == jid] or (chats if len(chats) == 1 else [])
    out = []
    for c in pick:
        for m in c.get("messages") or []:
            if not isinstance(m, dict) or m.get("dir") == "out":
                continue
            md = m.get("media") if isinstance(m.get("media"), dict) else {}
            for v in (m.get("text"), md.get("title"), md.get("transcript"), md.get("doc_text")):
                if str(v or "").strip():
                    out.append(str(v))
    return out


def sources_for(conn, cfg, row, buffer_path, live_dir):
    """The texts a card's written words are scrubbed against: the quotes of every entry it cites (any mark, and any
    quoted words on the entry's other lines) plus the other people's messages of its conversations in their latest
    runs' packs. [] when nothing is readable; never raises."""
    out = []
    p = _prop(row)
    try:
        for e in _entry_blocks(buffer_path, p.get("entry_keys")).values():
            for line in e.get("lines") or []:
                q = WI.quote_of_line(line)
                if q:
                    out.append(q)
                for m in _SEG_RE.finditer(str(line)):
                    s = m.group(1) if m.group(1) is not None else m.group(2)
                    if s and s.strip():
                        out.append(s)
    except Exception:
        pass
    try:
        live = _x(live_dir)
        for jid in p.get("jids") or []:
            rs = WT._rows(conn, "SELECT run_dir FROM runs WHERE ok=1 AND jid=? ORDER BY started_at DESC, id DESC LIMIT 1", (jid,))
            if not rs or not str(rs[0].get("run_dir") or "").strip():
                continue
            rd = _x(rs[0]["run_dir"])
            if not rd.is_absolute():
                rd = live / rd
            try:
                out.extend(_pack_texts(rd / "sweep.json", jid))
            except Exception:
                continue
    except Exception:
        pass
    return list(dict.fromkeys(out))


def _labels(conn, row, buffer_path):
    """Chat labels for the source line: conversation names of the card's jids, else the entries' people. Never a quote."""
    p = _prop(row)
    out = []
    for jid in p.get("jids") or []:
        r = WT._rows(conn, "SELECT name FROM conversations WHERE jid=?", (jid,))
        nm = WT._cut((r[0].get("name") if r else "") or "", 60)
        if nm:
            out.append(nm)
    if not out:
        try:
            for e in _entry_blocks(buffer_path, p.get("entry_keys")).values():
                parts = [x.strip() for x in str(e.get("title") or "").split(" · ")]
                if len(parts) >= 3:
                    who = re.sub(r"\s*\((?:无档|no note)\)\s*$", "", parts[2].replace("[[", "").replace("]]", "")).strip()
                    if who:
                        out.append(WT._cut(who, 60))
        except Exception:
            pass
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------- rendering

def _yv(v):
    """One frontmatter value as YAML: plain when that is unambiguous, else a double-quoted string."""
    s = WT._one(v)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _./+-]*", s) and s.lower() not in (
            "yes", "no", "true", "false", "null", "on", "off", "y", "n") and not s.endswith(" "):
        return s
    return json.dumps(s, ensure_ascii=False)


def _split_fm(text):
    """(bom, head lines, rest) where rest starts at the closing `---` line; None when there is no frontmatter."""
    bom = "﻿" if text.startswith("﻿") else ""
    t = text[len(bom):]
    lines = t.split("\n")
    if not lines or lines[0].rstrip("\r") != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r").strip() == "---":
            return bom, lines[1:i], "\n".join(lines[i:])
    return None


def _head_text(text):
    s = _split_fm(text)
    return "\n".join(s[1]) if s else ""


def _set_key(head, key, value):
    """head lines with `key: value` set in place (a block list under the key goes with its old value), else added."""
    line = "%s: %s" % (key, value)
    rx = re.compile(r"^%s[ \t]*:" % re.escape(key))
    for i, ln in enumerate(head):
        if rx.match(ln):
            j = i + 1
            while j < len(head) and (re.match(r"^[ \t]+\S", head[j]) or re.match(r"^-(\s|$)", head[j])):
                j += 1
            return head[:i] + [line] + head[j:]
    return head + [line]


def _fm_value(head, key):
    rx = re.compile(r"^%s[ \t]*:[ \t]*(.*?)[ \t]*$" % re.escape(key))
    for ln in head:
        m = rx.match(ln)
        if m:
            return m.group(1)
    return None


def clean_fm_set(fm_set, statuses, lang="zh"):
    """(kept, dropped): keys limited to wa_today.FM_KEYS, status limited to the doctrine's list, due to YYYY-MM-DD."""
    T = _L(lang)
    kept, dropped = {}, []
    for k, v in (fm_set or {}).items() if isinstance(fm_set, dict) else []:
        if k not in WT.FM_KEYS:
            dropped.append({"key": k, "why": T["fm_drop_key"] % k})
            continue
        s = WT._one(v)
        if not s:
            continue
        if k == "status" and statuses and s not in statuses:
            dropped.append({"key": k, "why": T["fm_drop_status"] % s})
            continue
        if k == "due" and not re.match(r"^\d{4}-\d{2}-\d{2}$", s):
            dropped.append({"key": k, "why": k})
            continue
        kept[k] = s
    return kept, dropped


def _section(today, now_line, labels, keys, lang):
    T = _L(lang)
    bits = []
    if labels:
        bits.append(T["sep"].join(labels))
    if keys:
        bits.append(T["source_keys"] % ", ".join(str(k)[:8] for k in keys))
    src = T["source"] % (" · ".join(bits) if bits else "-")
    return "\n\n## %s · %s\n\n%s\n%s\n" % (today, SECTION_TAG, WT._house(now_line), src)


def render_update(old_text, row, now_line, today, statuses, lang, task_keys=None, labels=None):
    """(new_text, fm_changes). The card's fm_set keys set in place (FM_KEYS and the doctrine's status list only),
    `updated: <today>` when the task family declares `updated` (task_keys), one section appended at the end. Above that
    section the body is byte for byte the old body. fm_changes = {"set": {key: [old, new]}, "dropped": [...]}."""
    p = _prop(row)
    s = _split_fm(old_text)
    if s is None:
        raise Refused("render", "no frontmatter")
    bom, head, rest = s
    kept, dropped = clean_fm_set(p.get("fm_set"), statuses, lang)
    changes = {"set": {}, "dropped": dropped}
    for k, v in kept.items():
        val = _yv(v)
        changes["set"][k] = [_fm_value(head, k), val]
        head = _set_key(head, k, val)
    if task_keys and "updated" in task_keys:
        changes["set"]["updated"] = [_fm_value(head, "updated"), today]
        head = _set_key(head, "updated", today)
    new = bom + "\n".join(["---"] + head) + "\n" + rest + _section(today, now_line, labels or [], p.get("entry_keys"), lang)
    return new, changes


_CREATE_RE = re.compile(r"^[A-Z][A-Za-z0-9]*(-[A-Za-z0-9]+)+$")


def task_slug(row):
    """The new note's name without `.md`: the card's task when it is a proper name, else built from the first
    NAME_MAX characters of `now` (words joined by hyphens, first letter capital, no path characters)."""
    p = _prop(row)
    t = WT._one(p.get("task")).replace("\\", "/").split("/")[-1].strip().strip("`")
    if t.endswith(".md"):
        t = t[:-3]
    if t and _CREATE_RE.match(t):
        return t
    base = t or WT._one(p.get("now"))[:NAME_MAX]
    words = [w for w in re.split(r"[^\w]+", base, flags=re.U) if w][:8]
    slug = "-".join(words)[:NAME_MAX].strip("-")
    if slug and slug[0].isalpha():
        slug = slug[0].upper() + slug[1:]
    return slug


def same_name_exists(vault, name):
    """A file of this name anywhere under the vault (folders whose name starts with `.` skipped). Its path, else ""."""
    v = _x(vault)
    for dirpath, dirnames, filenames in os.walk(str(v)):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        if name in filenames:
            return _rel(v, Path(dirpath) / name)
    return ""


def render_create(row, now_line, today, project_info, statuses, lang, required=None, marker=None, labels=None,
                  vault=None):
    """(rel_path, text): `<project>/Tasks/<slug>.md` with the task family's required keys (`cb: task`, the first
    status of the doctrine's list, `created: <today>`), then the card's fm_set keys; a `# <title>` line and the section
    as in an update. With vault given, a file of that name anywhere in the vault refuses it (same_name)."""
    p = _prop(row)
    slug = task_slug(p)
    if not slug:
        raise Refused("render", "no name")
    name = slug + ".md"
    tasks_dir = str((project_info or {}).get("tasks_dir") or (str(p.get("project") or "") + "/Tasks")).strip("/")
    rel = tasks_dir + "/" + name
    if vault is not None:
        hit = same_name_exists(vault, name)
        if hit:
            raise Refused("same_name", hit)
    mk = str(marker or TASK_MARKER_DEFAULT)
    mkey, mval = [x.strip() for x in mk.split(":", 1)] if ":" in mk else ("cb", "task")
    sts = list(statuses or [])
    first = FIRST_STATUS if FIRST_STATUS in sts or not sts else sts[0]
    kept, _dropped = clean_fm_set(p.get("fm_set"), statuses, lang)
    fixed = {mkey: mval, "status": kept.get("status", first), "created": today}
    head = []
    for k in list(required or TASK_REQUIRED_DEFAULT):
        if k in fixed:
            head.append("%s: %s" % (k, fixed[k]))
        elif k in kept:
            head.append("%s: %s" % (k, _yv(kept[k])))
    for k, v in kept.items():
        if not any(h.startswith(k + ":") for h in head):
            head.append("%s: %s" % (k, _yv(v)))
    title = slug.replace("-", " ")
    text = "\n".join(["---"] + head + ["---", "", "# " + title]) + _section(today, now_line, labels or [], p.get("entry_keys"), lang)
    return rel, text


# ---------------------------------------------------------------- entries

def key_is_foreign(buffer, keys, own_to):
    """(foreign, detail): True when any key is already placed to a target other than own_to (classify_keys)."""
    detail = []
    if not keys:
        return False, detail
    for k, (state, to) in WI.classify_keys(buffer, list(keys)).items():
        if state == "placed" and str(to or "").strip() != str(own_to or "").strip():
            detail.append({"key": k, "to": to})
    return bool(detail), detail


def _append_line(buffer, key, line):
    fn = getattr(WI, "append_under_key", None)
    if fn is None:
        return "unavailable"
    try:
        return str((fn(buffer, key, line) or {}).get("status") or "")
    except Exception as e:
        return "error: %s" % e


def _filing(cfg, line):
    flog = _x(_paths(cfg).get("filing_log"))
    with open(str(flog), "a", encoding="utf-8") as f:
        f.write(line)


# ---------------------------------------------------------------- apply

def _refuse(lang, code, detail="", step=None, **extra):
    R = _L(lang)["refuse"]
    A = _L(lang)["avail"]
    msg = R.get(code) or A.get(code) or code
    reason = (msg % detail) if "%s" in msg else msg
    if "%s" not in msg and detail and code not in R and code not in A:
        reason = "%s: %s" % (code, detail)
    out = {"ok": False, "code": code, "reason": reason, "written": {}, "step": step or code}
    out.update(extra)
    return out


def _new_backup_dir(cfg, pid, now_d):
    base = backups_root(cfg) / str(pid)
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(str(base), 0o700)
    stem = now_d.strftime("%Y%m%d-%H%M%S")
    for i in range(100):
        d = base / (stem if i == 0 else "%s-%d" % (stem, i))
        try:
            d.mkdir(mode=0o700)
            return d
        except FileExistsError:
            continue
    raise OSError("no free backup folder under %s" % base)


def apply(conn, cfg, pid, rev, pin, buffer_path, live_dir, now=None, lang="zh", law=None):
    """Write one card the owner pressed OK on. {"ok", "reason", "code", "written": {...}}; whole or nothing."""
    return _apply(conn, cfg, pid, rev, pin, buffer_path, live_dir, now, lang, law, ("ok",))


def _apply(conn, cfg, pid, rev, pin, buffer_path, live_dir, now, lang, law, allow):
    try:
        with _flock(_x(live_dir) / "today" / "write.lock"):
            return _apply_locked(conn, cfg, pid, rev, pin, buffer_path, live_dir, now, lang, law, allow)
    except Refused as e:
        return _refuse(lang, e.code, e.detail)


def _apply_locked(conn, cfg, pid, rev, pin, buffer_path, live_dir, now, lang, law, allow):
    T = _L(lang)
    av = availability(cfg, lang)
    if not av["ok"]:
        return dict(_refuse(lang, av["code"], step="availability"), reason=av["reason"])
    _ensure_cols(conn)
    row = _card(conn, pid)
    if row is None:
        return _refuse(lang, "not_found")
    if row.get("status") not in allow:
        return _refuse(lang, "status", row.get("status"))
    try:
        rv = int(rev)
    except (TypeError, ValueError):
        rv = None
    if rv != row.get("rev"):
        return _refuse(lang, "rev", row.get("rev"))
    p = _prop(row)
    h = pin_hash(p)
    if str(pin or "") != h or (str(row.get("pin_hash") or "") and str(row.get("pin_hash")) != str(pin or "")):
        return _refuse(lang, "pin")
    kind = str(p.get("kind") or "")
    if kind == "seed":
        return _refuse(lang, "seed")
    if kind not in WRITE_KINDS:
        return _refuse(lang, "kind", kind)
    paths = _paths(cfg)
    vault = _x(paths.get("vault"))
    doctrine = str(paths.get("doctrine") or "")
    keys = list(p.get("entry_keys") or [])
    d = _now(now)
    today = d.date().isoformat()
    at = d.isoformat(timespec="seconds")
    target = None
    pinfo = None
    if kind == "drop":
        if not keys:
            return _refuse(lang, "no_keys")
        own_to = DROP_TO
    else:
        index, _notes = WT.project_index(str(vault), doctrine or None, lang)
        pinfo = index.get(str(p.get("project") or ""))
        if pinfo is None:
            return _refuse(lang, "no_project", p.get("project"))
        try:
            law = law or Law(str(vault), av["schema_reader"], doctrine)
        except VaultWriteUnavailable as e:
            return _refuse(lang, "no_yaml", str(e))
        except Exception as e:
            return _refuse(lang, "schema_error", str(e))
        if kind == "update":
            task = WT._one(p.get("task")).replace("\\", "/").split("/")[-1]
            target = vault / pinfo["tasks_dir"] / task
            if not task or target.is_symlink() or not target.is_file() or not _inside(vault, target):
                return _refuse(lang, "no_task", task)
            own_to = _rel(vault, target)
        else:
            slug = task_slug(p)
            own_to = (pinfo["tasks_dir"].strip("/") + "/" + slug + ".md") if slug else ""
    if keys and not _x(buffer_path).is_file():
        return _refuse(lang, "no_buffer")
    foreign, detail = key_is_foreign(buffer_path, keys, own_to)
    if foreign:
        return _refuse(lang, "foreign", ", ".join("%s → %s" % (x["key"][:8], x["to"]) for x in detail))
    srcs = sources_for(conn, cfg, p, buffer_path, live_dir)
    mc = WT.tcfg(cfg)["quote_min_chars"]
    now_line = scrub_for_vault(WT._house(p.get("now")), srcs, mc)
    clean = dict(p)
    clean["fm_set"] = dict((k, scrub_for_vault(WT._one(v), srcs, mc)) for k, v in (p.get("fm_set") or {}).items())
    labels = _labels(conn, p, buffer_path)
    statuses = WT.task_statuses(doctrine)
    new_bytes = None
    old_bytes = None
    fm_changes = {}
    created = kind == "create"
    if kind in ("update", "create"):
        try:
            if kind == "update":
                old_bytes = target.read_bytes()
                new_text, fm_changes = render_update(old_bytes.decode("utf-8"), clean, now_line, today, statuses, lang,
                                                     task_keys=law.keys, labels=labels)
            else:
                slug = task_slug(p)
                if not slug or scrub_for_vault(slug.replace("-", " "), srcs, mc) != slug.replace("-", " "):
                    return _refuse(lang, "quote_in_name")
                rel, new_text = render_create(clean, now_line, today, pinfo, statuses, lang, required=law.required,
                                              marker=law.marker, labels=labels, vault=str(vault))
                target = vault / rel
                if not _inside(vault, target) or not target.parent.is_dir():
                    return _refuse(lang, "no_task", rel)
                fm_changes = {"set": {}, "dropped": clean_fm_set(p.get("fm_set"), statuses, lang)[1]}
        except Refused as e:
            return _refuse(lang, e.code, e.detail)
        except (OSError, UnicodeDecodeError, ValueError) as e:
            return _refuse(lang, "render", str(e))
        ok, why = check_with_schema(new_text, law)
        if not ok:
            return _refuse(lang, "schema", why)
        new_bytes = new_text.encode("utf-8")
    # ---- backup: the manifest first, then a copy of every file before the change
    files = []
    try:
        bdir = _new_backup_dir(cfg, row["id"], d)
        if target is not None:
            files.append({"path": _rel(vault, target), "existed": not created,
                          "md5_before": _md5(old_bytes) if old_bytes is not None else None, "copy": "files/0" if not created else ""})
        manifest = {"pid": row["id"], "rev": row.get("rev"), "pin": h, "kind": kind, "at": at, "mode": av["mode"],
                    "vault": str(vault), "files": files, "keys": keys, "state": "pending", "version": VERSION}
        WC.write_json_atomic(bdir / "manifest.json", manifest, mode=0o600)
        if not created and old_bytes is not None:
            (bdir / "files").mkdir(mode=0o700, exist_ok=True)
            cp = bdir / "files" / "0"
            cp.write_bytes(old_bytes)
            os.chmod(str(cp), 0o600)
            if _md5_file(cp) != files[0]["md5_before"]:
                raise OSError("the backup copy does not read back")
    except (OSError, ValueError) as e:
        return _refuse(lang, "backup", str(e))
    mrel = bdir.relative_to(backups_root(cfg)).as_posix()
    placed, already, missing = [], [], []
    try:
        if target is not None:
            if not created:
                st_mode = os.stat(str(target)).st_mode & 0o777
                if _md5_file(target) != files[0]["md5_before"]:
                    raise _Step("changed")
            else:
                st_mode = 0o644
            try:
                _write_bytes(target, new_bytes, st_mode, create=created)
            except FileExistsError:
                raise _Step("same_name", _rel(vault, target))
            except OSError as e:
                raise _Step("write", str(e))
            files[0]["md5_after"] = _md5_file(target)
            if files[0]["md5_after"] != _md5(new_bytes):
                raise _Step("readback")
        for k in keys:
            try:
                r = WI.mark_placed_by_key(buffer_path, k, own_to)
            except Exception as e:
                raise _Step("intake", str(e))
            st = r.get("status")
            if st == "moved":
                placed.append(k)
            elif st == "already_placed" and str(r.get("placed_to") or "").strip() == own_to:
                already.append(k)
            elif st == "already_placed":
                raise _Step("foreign", "%s → %s" % (k[:8], r.get("placed_to")))
            else:
                missing.append(k)
        what = T["log_drop"] % len(keys) if kind == "drop" else (T["log_create"] if created else T["log_update"])
        where = own_to if kind != "drop" else os.path.basename(str(buffer_path))
        try:
            _filing(cfg, T["log_write"] % (today, what, where, row["id"][:8], row.get("rev"), "%s/%s" % (BACKUPS, mrel)))
        except OSError as e:
            raise _Step("filing_log", str(e))
        written = {"at": at, "mode": av["mode"], "kind": kind,
                   "files": [{"path": f["path"], "md5_before": f["md5_before"], "md5_after": f.get("md5_after")} for f in files],
                   "manifest": mrel, "placed": placed, "already": already, "missing": missing, "created": created,
                   "to": own_to, "fm": fm_changes}
        manifest.update(state="written", files=files, placed=placed, written_at=at)
        try:
            WC.write_json_atomic(bdir / "manifest.json", manifest, mode=0o600)
            conn.execute("UPDATE today_proposals SET status='written', written_json=?, pin_hash=?, updated_at=? WHERE id=?",
                         (WT._jd(written), h, at, row["id"]))
            conn.commit()
        except (OSError, sqlite3.Error) as e:
            raise _Step("state", str(e))
    except _Step as e:
        _rollback(cfg, vault, bdir, files, placed, buffer_path, own_to, today, lang, manifest)
        return _refuse(lang, e.code, e.detail, step=e.code)
    except Exception as e:   # anything unforeseen after the backup is a failed write too
        _rollback(cfg, vault, bdir, files, placed, buffer_path, own_to, today, lang, manifest)
        return _refuse(lang, "write", "%s: %s" % (type(e).__name__, e))
    note = (T["dropped_note"] % len(keys)) if kind == "drop" else (T["written"] % own_to)
    return {"ok": True, "code": "written", "reason": note, "written": written}


def _rollback(cfg, vault, bdir, files, placed, buffer_path, own_to, today, lang, manifest):
    """Put every file back as the backup holds it (a created one moves into the backup folder) and add the undo line
    under the entries this write had already placed. Best effort: each step is tried."""
    T = _L(lang)
    for f in files:
        p = vault / f["path"]
        try:
            if f.get("existed"):
                data = (bdir / f["copy"]).read_bytes()
                if _md5_file(p) != _md5(data):
                    mode = os.stat(str(p)).st_mode & 0o777 if p.exists() else 0o644
                    _write_bytes(p, data, mode)
            elif p.exists() and "md5_after" in f:   # created by this write (the key is set right after the write)
                dest = bdir / "rolled-back"
                dest.mkdir(mode=0o700, exist_ok=True)
                shutil.move(str(p), str(dest / p.name))
        except OSError:
            pass
    for k in placed:
        _append_line(buffer_path, k, T["rollback_line"] % (today, own_to))
    try:
        manifest.update(state="rolled_back")
        WC.write_json_atomic(bdir / "manifest.json", manifest, mode=0o600)
    except OSError:
        pass


# ---------------------------------------------------------------- queue

def _pending_path(live_dir):
    return _x(live_dir) / "today" / PENDING


def _read_pending(path):
    got = WC.read_json(path, [])
    return [x for x in got if isinstance(x, dict) and x.get("pid")] if isinstance(got, list) else []


def _open_state(cfg):
    conn = sqlite3.connect(str((cfg or {}).get("state_db") or (WC.live_dir(cfg) / "state.sqlite")), timeout=10)
    return conn


def enqueue(cfg, live_dir, pid, rev, pin, now=None, conn=None, lang="zh"):
    """Queue mode: the press appends {pid, rev, pin, at} to pending-writes.json (under its lock) and the card goes
    `queued`. The same checks as a write's first steps refuse a stale press. {"ok": True, "queued": n}."""
    own = conn is None
    conn = conn or _open_state(cfg)
    try:
        _ensure_cols(conn)
        row = _card(conn, pid)
        if row is None:
            return _refuse(lang, "not_found")
        if row.get("status") != "ok":
            return _refuse(lang, "status", row.get("status"))
        try:
            rv = int(rev)
        except (TypeError, ValueError):
            rv = None
        if rv != row.get("rev"):
            return _refuse(lang, "rev", row.get("rev"))
        if str(pin or "") != pin_hash(row) or (str(row.get("pin_hash") or "") and str(row["pin_hash"]) != str(pin or "")):
            return _refuse(lang, "pin")
        if row.get("kind") not in WRITE_KINDS:
            return _refuse(lang, "seed" if row.get("kind") == "seed" else "kind", row.get("kind"))
        path = _pending_path(live_dir)
        at = _now(now).isoformat(timespec="seconds")
        with _flock(path.parent / (".%s.lock" % PENDING)):
            items = [x for x in _read_pending(path) if x.get("pid") != row["id"]]
            items.append({"pid": row["id"], "rev": row.get("rev"), "pin": str(pin), "at": at})
            WC.write_json_atomic(path, items, mode=0o600)
            conn.execute("UPDATE today_proposals SET status='queued', pin_hash=?, updated_at=? WHERE id=?", (str(pin), at, row["id"]))
            conn.commit()
        return {"ok": True, "queued": len(items), "reason": _L(lang)["queued"]}
    except Refused as e:
        return _refuse(lang, e.code, e.detail)
    finally:
        if own:
            conn.close()


def apply_pending(conn, cfg, buffer_path, live_dir, lang="zh", now=None):
    """Apply every queued press. Applied and refused entries leave the file (a refused card goes back to `ok` with
    written_json {"refused": reason}); an entry refused only because another write was running stays queued.
    Returns {"ok", "applied", "refused", "kept", "availability"}."""
    av = availability(cfg, lang)
    out = {"ok": av["ok"], "applied": [], "refused": [], "kept": 0, "availability": av}
    if not av["ok"]:
        out["kept"] = len(_read_pending(_pending_path(live_dir)))
        return out
    path = _pending_path(live_dir)
    _ensure_cols(conn)
    try:
        with _flock(path.parent / (".%s.lock" % PENDING)):
            items = _read_pending(path)
            keep = []
            for it in items:
                res = _apply(conn, cfg, it["pid"], it.get("rev"), it.get("pin"), buffer_path, live_dir, now, lang, None,
                             ("queued", "ok"))
                if res.get("ok"):
                    out["applied"].append(it["pid"])
                    continue
                if res.get("code") == "busy":
                    keep.append(it)
                    continue
                out["refused"].append({"pid": it["pid"], "code": res.get("code"), "reason": res.get("reason")})
                row = _card(conn, it["pid"])
                if row is not None and row.get("status") == "queued":
                    conn.execute("UPDATE today_proposals SET status='ok', written_json=? WHERE id=?",
                                 (WT._jd({"refused": res.get("reason"), "code": res.get("code"),
                                          "at": _now(now).isoformat(timespec="seconds")}), it["pid"]))
                    conn.commit()
            WC.write_json_atomic(path, keep, mode=0o600)
            out["kept"] = len(keep)
    except Refused as e:
        out["ok"] = False
        out["refused"].append({"pid": "", "code": e.code, "reason": _refuse(lang, e.code)["reason"]})
    return out


# ---------------------------------------------------------------- undo

def undo(conn, cfg, pid, buffer_path, live_dir, now=None, lang="zh"):
    """Give one write back: every file must be exactly what the write left (md5) or nothing is undone; an updated
    file gets its backup copy, a created file moves into the backup folder; one undo line under each entry the write
    placed; one filing log line; the card goes back to `ok` (pin kept, written_json.undone_at set)."""
    T = _L(lang)
    try:
        with _flock(_x(live_dir) / "today" / "write.lock"):
            _ensure_cols(conn)
            row = _card(conn, pid)
            if row is None:
                return _refuse(lang, "not_found")
            wj = WT._jl(row.get("written_json"), {}) or {}
            if row.get("status") != "written" or not wj.get("manifest"):
                return _refuse(lang, "not_written")
            vault = _x(_paths(cfg).get("vault"))
            if not str(_paths(cfg).get("vault") or "").strip() or not vault.is_dir():
                return _refuse(lang, "no_vault")
            bdir = backups_root(cfg) / wj["manifest"]
            man = WC.read_json(bdir / "manifest.json", {}) or {}
            mfiles = dict((f.get("path"), f) for f in man.get("files") or [])
            for f in wj.get("files") or []:
                if _md5_file(vault / f["path"]) != f.get("md5_after"):
                    return _refuse(lang, "changed_since", f["path"])
            for f in wj.get("files") or []:
                p = vault / f["path"]
                mf = mfiles.get(f["path"]) or {}
                if wj.get("created") or not mf.get("existed", True):
                    dest = bdir / "undone"
                    dest.mkdir(mode=0o700, exist_ok=True)
                    shutil.move(str(p), str(dest / p.name))
                else:
                    data = (bdir / (mf.get("copy") or "files/0")).read_bytes()
                    if _md5(data) != f.get("md5_before"):
                        return _refuse(lang, "backup", "the backup copy does not match its record")
                    _write_bytes(p, data, os.stat(str(p)).st_mode & 0o777)
            d = _now(now)
            today, at = d.date().isoformat(), d.isoformat(timespec="seconds")
            to = wj.get("to") or (wj["files"][0]["path"] if wj.get("files") else DROP_TO)
            lines = {}
            for k in wj.get("placed") or []:
                lines[k] = _append_line(buffer_path, k, T["undo_line"] % (today, to))
            what = (T["log_drop"] % len(wj.get("placed") or [])) if wj.get("kind") == "drop" else (
                T["log_create"] if wj.get("created") else T["log_update"])
            try:
                _filing(cfg, T["log_undo"] % (today, what, to, row["id"][:8], "%s/%s" % (BACKUPS, wj["manifest"])))
            except OSError:
                pass
            wj["undone_at"] = at
            wj["undo_lines"] = lines
            conn.execute("UPDATE today_proposals SET status='ok', written_json=?, updated_at=? WHERE id=?",
                         (WT._jd(wj), at, row["id"]))
            conn.commit()
            man.update(state="undone", undone_at=at)
            try:
                WC.write_json_atomic(bdir / "manifest.json", man, mode=0o600)
            except OSError:
                pass
            return {"ok": True, "code": "undone", "reason": T["undone"] % to, "written": wj}
    except Refused as e:
        return _refuse(lang, e.code, e.detail)
    except OSError as e:
        return _refuse(lang, "write", str(e))


# ---------------------------------------------------------------- selftest

class _Check(object):
    def __init__(self):
        self.n, self.fails = 0, []

    def __call__(self, cond, msg, detail=None):
        self.n += 1
        print(("  ok   " if cond else "  FAIL ") + msg + ("" if cond or detail is None else "  [%s]" % str(detail)[:300]))
        if not cond:
            self.fails.append(msg)


_SELF_DOCTRINE = """# Structure doctrine (selftest, invented)

## 8 · Record schema

```yaml
# Reading rule: at the top level a key is read by its VALUE. A mapping of mappings holds the families.
# Inside a family, `required` `optional` `marker` `multi` `extends` `cardinality` are reserved
# keys; every other key is a field name whose value is a closed list or that family's subtypes.
families:
  brief:      {required: [type, status, updated],
               optional: [due, stage, priority],
               status: [active, done, killed]}
  recipe:     {required: [type, date]}
  record:
    decision: {marker: "cb: decision", required: [cb, date, status, lane],
               status: [active, superseded, closed]}
    task:     {marker: "cb: task", required: [cb, status, created],
               optional: [start, due, waiting_on, depends_on, priority],
               status: [not-started, in-progress, waiting, blocked, done]}
lane: [deliver, grow, run, build]
open_keys: {tags: "legal on every note"}
```
"""

_FAKE_READER = r'''
import os, re
class SchemaError(Exception):
    pass
class SchemaUnavailable(SchemaError):
    pass
def install_hint():
    return ("python3 -m pip install --user pyyaml (fake reader hint)", "", False)
class Spec(object):
    def __init__(self, name, required, optional, enums, marker):
        self.name, self.required, self.optional, self.enums, self.marker = name, required, optional, enums, marker
        self.multi = []
    def declares(self, f):
        return f in self.required or f in self.optional
class Schema(object):
    def __init__(self):
        self.by_marker, self.marker_key, self.specs = {}, "cb", {}
    def spec_for(self, fm):
        v = fm.get("cb")
        if isinstance(v, str) and v.strip():
            return self.by_marker.get("cb: " + v.strip())
        return None
def _l(s):
    return [x.strip() for x in s.split(",") if x.strip()]
def load_schema(vault, meta_dir="99_Meta", doctrine_name="structure-doctrine.md"):
    try:
        import yaml
    except ImportError:
        raise SchemaUnavailable("PyYAML is not installed (fake reader)")
    path = os.path.join(vault, meta_dir, doctrine_name)
    if not os.path.isfile(path):
        raise SchemaError("no doctrine at %s" % path)
    text = open(path, encoding="utf-8").read()
    m = re.search(r'task:\s*\{marker: "cb: task", required: \[(.*?)\],\s*optional: \[(.*?)\],\s*status: \[(.*?)\]\}', text, re.S)
    if not m:
        raise SchemaError("no task family in section 8 (fake reader)")
    s = Schema()
    sp = Spec("record.task", _l(m.group(1)), _l(m.group(2)), {"status": _l(m.group(3))}, "cb: task")
    s.by_marker["cb: task"] = sp
    s.specs["record.task"] = sp
    return s
def validate(fm, spec, schema):
    out = []
    miss = [k for k in spec.required if k not in fm or fm.get(k) in ("", None, [])]
    if miss:
        out.append(("missing", "missing required key(s): %s" % ", ".join(miss)))
    for f, allowed in spec.enums.items():
        v = fm.get(f)
        if v in ("", None, []):
            continue
        if str(v).strip() not in allowed:
            out.append(("value", "'%s: %s' is not in the closed list" % (f, v)))
    return out
'''


def _fake_yaml():
    m = types.ModuleType("yaml")

    class YAMLError(Exception):
        pass

    def safe_load(s):
        d = {}
        for line in str(s).split("\n"):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if ":" not in line:
                raise YAMLError("not a mapping line: %r" % line)
            k, v = line.split(":", 1)
            d[k.strip()] = v.strip().strip('"')
        return d
    m.YAMLError = YAMLError
    m.safe_load = safe_load
    return m


def _fake_append_under_key(buffer, key, line):
    """Stands in for wa_intake.append_under_key until it ships: the line goes last in the entry block with that key."""
    p = Path(buffer)
    with WI._locked(p):
        text = p.read_text(encoding="utf-8")
        for b in WI._blocks(text):
            if WI.entry_key(b) == key:
                blk = b["block"]
                i = text.find(blk)
                p.write_text(text[:i] + blk + "\n" + line + text[i + len(blk):], encoding="utf-8")
                return {"status": "appended"}
    return {"status": "not_found"}


def _snapshot(root, skip=()):
    out = {}
    for dp, dn, fn in os.walk(str(root)):
        for f in fn:
            p = Path(dp) / f
            rel = p.relative_to(root).as_posix()
            if any(rel.endswith(s) for s in skip) or f.startswith("."):
                continue
            out[rel] = _md5_file(p)
    return out


def _real_check(chk, td):
    """Against the owner's real reader and the real PyYAML, when both are on this machine."""
    saved = sys.modules.pop("yaml", None)
    try:
        try:
            import yaml as _real  # noqa: F401
        except ImportError:
            print("  SKIP real reader check: PyYAML is not importable on this Python")
            return
        rp = Path(os.path.expanduser("~")).joinpath(*READER_REL)
        if not rp.is_file():
            print("  SKIP real reader check: no doctrine_schema.py at the default place")
            return
        v = Path(td) / "realvault"
        (v / "99_Meta").mkdir(parents=True)
        (v / "99_Meta" / "structure-doctrine.md").write_text(_SELF_DOCTRINE, encoding="utf-8")
        try:
            law = Law(str(v), rp, str(v / "99_Meta" / "structure-doctrine.md"))
        except Exception as e:
            chk(False, "real reader loads the invented doctrine's task family", e)
            return
        good = "---\ncb: task\nstatus: not-started\ncreated: 2026-01-05\n---\n\n# Sample\n"
        bad = "---\ncb: task\nstatus: someday\ncreated: 2026-01-05\n---\n\n# Sample\n"
        chk(law.check_task(good)[0] and not law.check_task(bad)[0] and "created" in law.required,
            "real reader + real yaml: a good task passes, a bad status is refused", (law.check_task(good), law.check_task(bad)))
    finally:
        sys.modules.pop("yaml", None)
        if saved is not None:
            sys.modules["yaml"] = saved


def _selftest():
    chk = _Check()
    td = tempfile.mkdtemp(prefix="wa_vault_write.")
    saved_yaml = sys.modules.get("yaml")
    had_append = hasattr(WI, "append_under_key")
    real_write = globals()["_write_bytes"]
    try:
        _real_check(chk, td)
        sys.modules["yaml"] = _fake_yaml()
        if not had_append:
            WI.append_under_key = _fake_append_under_key
            print("  note: wa_intake.append_under_key is not there yet; the selftest uses its own stand in")
        root = Path(td)
        vault = root / "vault"
        meta = vault / "99_Meta"
        meta.mkdir(parents=True)
        (meta / "structure-doctrine.md").write_text(_SELF_DOCTRINE, encoding="utf-8")
        (meta / "filing-log.md").write_text("", encoding="utf-8")
        wing = vault / WT._TEST_WING / "02_Work" / "Deliver" / "Weekend-Classes"
        (wing / "Tasks").mkdir(parents=True)
        (wing / "_Weekend-Classes-Brief.md").write_text("---\ntype: brief\nstatus: active\nupdated: 2026-01-02\n---\n\n# Weekend Classes\n",
                                                         encoding="utf-8")
        oven = wing / "Tasks" / "Book-Oven-Service.md"
        oven_text = ("---\ncb: task\nstatus: in-progress\ncreated: 2026-01-03\ndue: 2026-02-01\npriority: soon\n---\n\n"
                     "# Book oven service\n\nCall the service company before the spring term.\n\n- [ ] get two quotes\n")
        oven.write_text(oven_text, encoding="utf-8")
        broken = wing / "Tasks" / "Fix-Mixer.md"
        broken.write_text("---\ncb: task\nstatus: someday\ncreated: 2026-01-03\n---\n\n# Fix mixer\n", encoding="utf-8")
        shop = vault / "03_Personal-Wing" / "Personal-Projects" / "Shop-Fitout"
        (shop / "Tasks").mkdir(parents=True)
        (shop / "_Shop-Fitout-Brief.md").write_text("---\ntype: brief\nstatus: active\nupdated: 2026-01-02\n---\n\n# Shop fitout\n",
                                                    encoding="utf-8")
        (vault / "98_Archive").mkdir()
        (vault / "98_Archive" / "Order-Tiles.md").write_text("# old\n", encoding="utf-8")
        reader = root / "reader" / "doctrine_schema.py"
        reader.parent.mkdir()
        reader.write_text(_FAKE_READER, encoding="utf-8")
        buf = meta / "intake-buffer.md"
        quote1 = "please bring the oven door seal tomorrow morning"
        buf.write_text(
            "Intake notes (selftest)\n\n"
            "### 2026-01-05 · [wa] · Aiko Tan (无档) · oven service visit\n"
            "- 🗣 01-05 10:12「%s」\n- 状态: open\n\n"
            "### 2026-01-05 · [wa] · Ravi Kumar (无档) · tile sample\n"
            "- ≈ 01-05 11:00 大意, 非逐字「wants a tile sample」\n- 状态: open\n\n"
            "### 2026-01-05 · [wa] · Mei Ling (无档) · nothing to do\n"
            "- ≈ 01-05 12:00 大意, 非逐字「said thanks」\n- 状态: open\n\n## 已归\n" % quote1, encoding="utf-8")
        ents = WI.list_open(str(buf))
        E1, E2, E3 = [e["key"] for e in ents]
        home = root / "home"
        live = home / "live"
        live.mkdir(parents=True)
        cfgp = live / "config.json"
        raw = {"paths": {"home": str(home), "vault": str(vault), "doctrine": "99_Meta/structure-doctrine.md",
                         "filing_log": "99_Meta/filing-log.md", "schema_reader": str(reader)},
               "intake": {"enabled": True, "buffer": str(buf)},
               "today": {"enabled": True, "write_mode": "direct"}, "board": {"lang": "zh"}}
        cfgp.write_text(json.dumps(raw), encoding="utf-8")
        cfg = WC.load_config(str(cfgp))
        cfg["paths"]["schema_reader"] = str(reader)
        cfg["today"]["write_mode"] = "direct"

        # ---- 1 · availability, one code at a time
        def av(mut, code, name):
            c = copy.deepcopy(cfg)
            mut(c)
            got = availability(c)
            chk(got["code"] == code and got["ok"] == (code == "ok"), "avail %s: %s" % (code, name), got)
        av(lambda c: c["today"].update(write_mode="off"), "off", "write_mode off")
        av(lambda c: c["today"].update(enabled=False), "today_off", "today.enabled false")
        av(lambda c: c["paths"].update(vault=str(root / "nowhere")), "no_vault", "vault missing")
        av(lambda c: c["paths"].update(doctrine=str(root / "nowhere.md")), "no_doctrine", "doctrine missing")
        av(lambda c: c["paths"].update(schema_reader=str(root / "missing.py")), "no_reader", "reader path set but missing")
        bad_reader = root / "reader" / "broken_reader.py"
        bad_reader.write_text("def broken(:\n", encoding="utf-8")
        av(lambda c: c["paths"].update(schema_reader=str(bad_reader)), "reader_error", "reader does not import")
        sys.modules["yaml"] = None
        av(lambda c: None, "no_yaml", "PyYAML not importable")
        got = availability(cfg)
        chk("fake reader hint" in got.get("install_hint", "") and "fake reader hint" in got["reason"],
            "avail no_yaml carries the reader's own install hint", got)
        sys.modules["yaml"] = _fake_yaml()
        v2 = root / "vault2"
        (v2 / "99_Meta").mkdir(parents=True)
        (v2 / "99_Meta" / "structure-doctrine.md").write_text("# doctrine\n\n## 8 · Record schema\n\nnothing here\n", encoding="utf-8")
        (v2 / "99_Meta" / "filing-log.md").write_text("", encoding="utf-8")
        av(lambda c: c["paths"].update(vault=str(v2), doctrine=str(v2 / "99_Meta" / "structure-doctrine.md"),
                                       filing_log=str(v2 / "99_Meta" / "filing-log.md")), "schema_error", "no task family")
        av(lambda c: c["paths"].update(filing_log=str(root / "nodir" / "filing-log.md")), "no_filing_log", "filing log folder missing")
        h2 = root / "home2"
        h2.mkdir()
        (h2 / BACKUPS).write_text("a file where the folder should be", encoding="utf-8")
        av(lambda c: c["paths"].update(home=str(h2)), "no_backup_dir", "backup folder cannot be made")
        av(lambda c: None, "ok", "everything in place")
        odd = {"today": {"enabled": True, "write_mode": "direct"}, "paths": 5}
        chk(availability(odd)["code"] == "module_error" and availability(odd)["ok"] is False,
            "avail module_error: availability never raises")
        chk(oct(os.stat(str(backups_root(cfg))).st_mode & 0o777) == oct(0o700), "avail ok: backup folder is 0700")
        law = Law(str(vault), reader, str(meta / "structure-doctrine.md"))

        # ---- 2 · render units
        statuses = WT.task_statuses(str(meta / "structure-doctrine.md"))
        row_u = {"kind": "update", "task": "Book-Oven-Service.md", "now": "Visit booked", "entry_keys": [E1],
                 "fm_set": {"status": "waiting", "waiting_on": "service company", "owner": "Aiko", "priority": "now"}}
        new_text, fmc = render_update(oven_text, row_u, "Visit booked", "2026-01-06", statuses, "en", task_keys=law.keys,
                                      labels=["Aiko Tan"])
        ofm, obody = WC.parse_frontmatter(oven_text)
        nfm, nbody = WC.parse_frontmatter(new_text)
        chk(nbody.startswith(obody) and nbody[len(obody):].startswith("\n\n## 2026-01-06 · WhatsApp Live\n\nVisit booked\n"),
            "update keeps the body byte-identical above the appended section", repr(nbody[len(obody):]))
        others_same = all(nfm.get(k) == ofm.get(k) for k in ofm if k not in ("status", "waiting_on", "priority"))
        chk(nfm.get("status") == "waiting" and nfm.get("waiting_on") == "service company" and nfm.get("priority") == "now"
            and others_same and "owner" not in nfm and "updated" not in nfm,
            "update changes only the allowed keys", nfm)
        chk(any(x["key"] == "owner" for x in fmc["dropped"]), "a disallowed fm_set key is dropped and noted", fmc)
        _t2, fmc2 = render_update(oven_text, dict(row_u, fm_set={"status": "someday"}), "x", "2026-01-06", statuses, "en")
        chk("status" not in fmc2["set"] and any(x["key"] == "status" for x in fmc2["dropped"]),
            "a status outside the doctrine list is dropped and noted", fmc2)
        t3, _ = render_update(oven_text, row_u, "x", "2026-01-06", statuses, "en", task_keys=law.keys + ["updated"])
        chk(WC.parse_frontmatter(t3)[0].get("updated") == "2026-01-06", "update sets updated when the task family has it")
        chk(not key_is_foreign(str(buf), [E1], "x")[0], "key_is_foreign: an open key is not foreign")

        # ---- 3 · scrub
        s = scrub_for_vault("Aiko asked: please bring the oven door seal tomorrow, we confirmed.", [quote1], 6)
        chk("oven door seal" not in s and SCRUB_REF in s and s.startswith("Aiko asked") and "we confirmed" in s,
            "scrub removes an overlapping quoted phrase and leaves plain text", s)
        chk(scrub_for_vault("Booked the visit for Friday.", [quote1], 6) == "Booked the visit for Friday.",
            "scrub leaves text with no overlap untouched")

        # ---- the state database and the cards
        conn = sqlite3.connect(str(cfg["state_db"]))
        WT.ensure_tables(conn)
        conn.execute("CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, started_at TEXT, jid TEXT, name TEXT, run_dir TEXT, "
                     "output TEXT, kind TEXT, ok INTEGER)")
        conn.execute("CREATE TABLE IF NOT EXISTS conversations(jid TEXT PRIMARY KEY, name TEXT, kind TEXT, last_out_at TEXT)")
        J1 = "60100000001@s.whatsapp.net"
        conn.execute("INSERT INTO conversations(jid, name, kind) VALUES (?,?,?)", (J1, "Aiko Tan", "direct"))
        rd = live / "runs" / "r1"
        rd.mkdir(parents=True)
        (rd / "sweep.json").write_text(json.dumps({"chats": [{"jid": J1, "messages": [
            {"pk": 1, "t": "2026-01-05T10:20:00", "dir": "in", "text": "the technician can only come after lunch on friday"},
            {"pk": 2, "t": "2026-01-05T10:21:00", "dir": "out", "text": "ok noted"}]}]}), encoding="utf-8")
        conn.execute("INSERT INTO runs(id, started_at, jid, name, run_dir, output, kind, ok) VALUES (1,?,?,?,?,?,?,1)",
                     ("2026-01-05T10:22:00", J1, "Aiko Tan", str(rd), "{}", "direct"))
        conn.commit()
        wpid = (WT._TEST_WING + "/02_Work/Deliver/Weekend-Classes")
        spid = "03_Personal-Wing/Personal-Projects/Shop-Fitout"
        props = {
            "upd": {"project": wpid, "kind": "update", "task": "Book-Oven-Service.md",
                    "now": "Aiko asked to please bring the oven door seal tomorrow; the technician can only come after lunch on friday",
                    "fm_set": {"status": "waiting", "waiting_on": "service company", "owner": "Aiko"}, "entry_keys": [E1], "jids": [J1]},
            "create": {"project": spid, "kind": "create", "task": "Order-Tiles-Sample.md", "now": "Order a tile sample for the counter",
                       "fm_set": {"due": "2026-01-20"}, "entry_keys": [E2], "jids": []},
            "same": {"project": spid, "kind": "create", "task": "Order-Tiles.md", "now": "Order tiles", "fm_set": {},
                     "entry_keys": [], "jids": []},
            "bad": {"project": wpid, "kind": "update", "task": "Fix-Mixer.md", "now": "Mixer part ordered", "fm_set": {},
                    "entry_keys": [], "jids": []},
            "drop": {"project": "unassigned", "kind": "drop", "task": "", "now": "Only a thank you", "fm_set": {},
                     "entry_keys": [E3], "jids": []},
            "foreign": {"project": spid, "kind": "create", "task": "Call-Tiler.md", "now": "Call the tiler", "fm_set": {},
                        "entry_keys": [E3], "jids": []},
            "seed": {"project": "seed:Delivery van", "kind": "seed", "task": "Delivery van", "now": "Maybe a van", "fm_set": {},
                     "entry_keys": [], "jids": []},
        }
        ids = {}
        for k, pr in props.items():
            ids[k], _a = WT.upsert_proposal(conn, "2026-01-06", pr, "2026-01-06T09:00:00")

        def card(k):
            return _card(conn, ids[k])

        def go(k, pin=None, rev=None, now="2026-01-06T10:00:00", c=None):
            r = card(k)
            return apply(conn, c or cfg, ids[k], r["rev"] if rev is None else rev, pin_hash(r) if pin is None else pin,
                         str(buf), str(live), now=now, lang="en")

        res = go("upd")
        chk(not res["ok"] and res["code"] == "status", "status not ok refused (a pending card is not written)", res)
        _ensure_cols(conn)
        for k in ids:
            r = card(k)
            conn.execute("UPDATE today_proposals SET status='ok', pin_hash=? WHERE id=?", (pin_hash(r), r["id"]))
        conn.commit()
        chk(pin_hash(card("upd")) == card("upd")["content_hash"], "pin_hash equals wa_today's content hash of the row")

        # ---- pin mismatch
        before = _snapshot(vault)
        res = go("upd", pin="0" * 40)
        chk(not res["ok"] and res["code"] == "pin" and _snapshot(vault) == before and not (backups_root(cfg) / ids["upd"]).exists(),
            "pin mismatch refused whole (nothing written, no backup)", res)
        res = go("upd", rev=99)
        chk(not res["ok"] and res["code"] == "rev", "rev mismatch refused", res)

        # ---- md5 read-back mismatch restores the backup
        calls = []

        def bad_write(path, data, mode, create=False):   # the first write lands wrong; the restore write is honest
            calls.append(path)
            real_write(path, data + (b"\nnoise\n" if len(calls) == 1 else b""), mode, create)
        globals()["_write_bytes"] = bad_write
        try:
            res = go("upd")
        finally:
            globals()["_write_bytes"] = real_write
        chk(not res["ok"] and res["code"] == "readback" and _md5_file(oven) == _md5(oven_text.encode("utf-8"))
            and WI.classify_keys(str(buf), [E1])[E1][0] == "open",
            "md5 read-back mismatch restores the backup", res)

        # ---- the update
        flog = meta / "filing-log.md"
        n_log = len(flog.read_text(encoding="utf-8").splitlines())
        res = go("upd")
        after = oven.read_text(encoding="utf-8")
        afm, abody = WC.parse_frontmatter(after)
        chk(res["ok"] and res["code"] == "written", "update written", res)
        chk(abody.startswith(obody) and afm.get("status") == "waiting" and afm.get("due") == "2026-02-01" and "owner" not in afm,
            "written update: body kept, only allowed keys changed", after)
        chk("oven door seal" not in after and "after lunch on friday" not in after and SCRUB_REF in after,
            "written update: the quoted phrase and the pack message are scrubbed", after)
        chk("Aiko Tan" in after and E1[:8] in after, "written update: the source line names the chat label and the entry key")
        rel_upd = wpid + "/Tasks/Book-Oven-Service.md"
        st = WI.classify_keys(str(buf), [E1])[E1]
        chk(st == ("placed", rel_upd), "entries placed by key with the task path", st)
        lines = flog.read_text(encoding="utf-8").splitlines()
        chk(len(lines) == n_log + 1 and rel_upd in lines[-1] and ids["upd"][:8] in lines[-1], "filing-log line appended", lines[-1:])
        wj = WT._jl(card("upd")["written_json"], {})
        chk(card("upd")["status"] == "written" and wj["files"][0]["md5_after"] == _md5_file(oven)
            and (backups_root(cfg) / wj["manifest"] / "manifest.json").is_file(),
            "card row written with files, md5 and manifest", wj)
        res = go("upd")
        chk(not res["ok"] and res["code"] == "status", "a second apply of a written proposal is refused", res)

        # ---- schema failure refuses before any write
        bsnap = _md5_file(broken)
        res = go("bad")
        bdir_bad = backups_root(cfg) / ids["bad"]
        chk(not res["ok"] and res["code"] == "schema" and _md5_file(broken) == bsnap
            and (not bdir_bad.exists() or not any(bdir_bad.iterdir())),
            "schema failure refuses before any write (backup dir empty)", res)

        # ---- create
        res = go("same")
        chk(not res["ok"] and res["code"] == "same_name", "create refused on same name", res)
        res = go("create")
        created = shop / "Tasks" / "Order-Tiles-Sample.md"
        ctext = created.read_text(encoding="utf-8") if created.exists() else ""
        cfm = WC.parse_frontmatter(ctext)[0]
        chk(res["ok"] and law.check_task(ctext)[0] and cfm.get("status") == "not-started" and cfm.get("created") == "2026-01-06"
            and cfm.get("due") == "2026-01-20" and "# Order Tiles Sample" in ctext,
            "create validated by the schema (required keys, first status, created)", ctext)
        chk(oct(os.stat(str(created)).st_mode & 0o777) == oct(0o644) if created.exists() else False, "created note is 0644")
        chk(WI.classify_keys(str(buf), [E2])[E2] == ("placed", spid + "/Tasks/Order-Tiles-Sample.md"),
            "create places its entry with the new task path")

        # ---- drop
        snap = _snapshot(vault, skip=("intake-buffer.md", "filing-log.md"))
        res = go("drop")
        chk(res["ok"] and _snapshot(vault, skip=("intake-buffer.md", "filing-log.md")) == snap
            and WI.classify_keys(str(buf), [E3])[E3] == ("placed", DROP_TO),
            "drop places without writing", res)
        res = go("foreign")
        chk(not res["ok"] and res["code"] == "foreign" and not (shop / "Tasks" / "Call-Tiler.md").exists()
            and not (backups_root(cfg) / ids["foreign"]).exists(), "foreign key refused (before any backup)", res)
        res = go("seed")
        chk(not res["ok"] and res["code"] == "seed", "seed is never written", res)

        # ---- undo
        created.write_text(ctext + "\nedited by hand\n", encoding="utf-8")
        res = undo(conn, cfg, ids["create"], str(buf), str(live), now="2026-01-06T11:00:00", lang="en")
        chk(not res["ok"] and res["code"] == "changed_since" and created.exists(), "undo refuses when the file changed since", res)
        created.write_text(ctext, encoding="utf-8")
        res = undo(conn, cfg, ids["create"], str(buf), str(live), now="2026-01-06T11:00:00", lang="en")
        wjc = WT._jl(card("create")["written_json"], {})
        moved = backups_root(cfg) / wjc.get("manifest", "x") / "undone" / "Order-Tiles-Sample.md"
        chk(res["ok"] and not created.exists() and moved.is_file(), "undo moves a created file into the backup folder", res)
        n_log = len(flog.read_text(encoding="utf-8").splitlines())
        res = undo(conn, cfg, ids["upd"], str(buf), str(live), now="2026-01-06T11:05:00", lang="en")
        btext = buf.read_text(encoding="utf-8")
        blk = [b for b in WI._blocks(btext) if WI.entry_key(b) == E1]
        chk(res["ok"] and _md5_file(oven) == _md5(oven_text.encode("utf-8")), "undo restores the md5", res)
        chk(bool(blk) and "undone 2026-01-06" in blk[0]["block"].split("\n")[-1], "undo appends the undo line under the entry",
            blk[0]["block"] if blk else btext)
        chk(card("upd")["status"] == "ok" and WT._jl(card("upd")["written_json"], {}).get("undone_at")
            and card("upd")["pin_hash"] == pin_hash(card("upd")), "undo: card back to ok, undone_at set, pin kept")
        chk(len(flog.read_text(encoding="utf-8").splitlines()) == n_log + 1, "undo appends one filing-log line")
        res = undo(conn, cfg, ids["upd"], str(buf), str(live), lang="en")
        chk(not res["ok"] and res["code"] == "not_written", "undo of a card not written is refused", res)

        # ---- queue
        cq = copy.deepcopy(cfg)
        cq["today"]["write_mode"] = "queue"
        r = card("upd")
        q = enqueue(cq, str(live), ids["upd"], r["rev"], pin_hash(r), conn=conn)
        pend = live / "today" / PENDING
        chk(q.get("ok") and q.get("queued") == 1 and card("upd")["status"] == "queued" and len(_read_pending(pend)) == 1,
            "queue: enqueue appends and marks the card queued", q)
        s = apply_pending(conn, cq, str(buf), str(live), lang="en", now="2026-01-06T12:00:00")
        chk(s["applied"] == [ids["upd"]] and _read_pending(pend) == [] and card("upd")["status"] == "written"
            and WC.parse_frontmatter(oven.read_text(encoding="utf-8"))[0].get("status") == "waiting",
            "queue: apply_pending writes and empties the file", s)
        conn.close()
    except Exception as e:
        import traceback
        traceback.print_exc()
        chk(False, "selftest raised %s: %s" % (type(e).__name__, e))
    finally:
        globals()["_write_bytes"] = real_write
        if not had_append and hasattr(WI, "append_under_key"):
            del WI.append_under_key
        if saved_yaml is not None:
            sys.modules["yaml"] = saved_yaml
        else:
            sys.modules.pop("yaml", None)
        shutil.rmtree(td, ignore_errors=True)
    if chk.fails:
        print("wa_vault_write selftest FAIL (%d of %d checks failed)" % (len(chk.fails), chk.n))
        return 1
    print("wa_vault_write selftest PASS (%d checks)" % chk.n)
    return 0


# ---------------------------------------------------------------- CLI

def _lang_of(cfg, given):
    if given:
        return given
    return str(((cfg or {}).get("board") or {}).get("lang") or "zh")


def main(argv=None):
    ap = argparse.ArgumentParser(description="the Today page's write to vault button (optional, station O7)")
    ap.add_argument("--config")
    ap.add_argument("--lang")
    ap.add_argument("--availability", action="store_true")
    ap.add_argument("--apply-pending", action="store_true")
    ap.add_argument("--undo", metavar="ID")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if not (a.availability or a.apply_pending or a.undo):
        ap.print_help()
        return 2
    cfg = WC.load_config(a.config)
    lang = _lang_of(cfg, a.lang)
    if a.availability:
        got = availability(cfg, lang)
        print(json.dumps(got, ensure_ascii=False, indent=1))
        return 0 if got["ok"] else 2
    conn = _open_state(cfg)
    try:
        buffer_path = str(((cfg.get("intake") or {}).get("buffer")) or "")
        live = str(WC.live_dir(cfg))
        if a.apply_pending:
            got = apply_pending(conn, cfg, buffer_path, live, lang)
        else:
            got = undo(conn, cfg, a.undo, buffer_path, live, lang=lang)
        print(json.dumps(got, ensure_ascii=False, indent=1))
        return 0 if got.get("ok") and not got.get("refused") else 2
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
