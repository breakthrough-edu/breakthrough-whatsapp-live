#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wa_sweep.py · full-window WhatsApp reader (breakthrough-whatsapp-live, companion skill breakthrough-whatsapp-read)

Reads the local database WhatsApp Desktop keeps on this Mac and writes one reading pack: markdown for a
session or a model to read whole, JSON for code. Read only: it never sends anything, never opens the live
database for reading or writing, and never touches the WhatsApp app.

What it does, in order:
  1. Snapshots ChatStorage, ContactsV2 and LID with the SQLite online backup API into the work dir, so the
     newest messages still in the write-ahead log come along and the app's own files are only ever opened
     read-only. ChatStorage is required (exit 3 without it); the other two only improve names and phones.
     The snapshots are full copies of the chat history, so they are deleted after the read unless the
     reader config says keep_snapshot: true.
  2. Takes every content message of every one-to-one and group chat in the window, oldest first, nothing cut.
     Status updates and broadcast lists are skipped; system messages (joins, renames) are counted per chat.
  3. Names every speaker and records which source answered, trying six in order: address book (ContactsV2),
     the name they set for themselves (push name), the chat's display name (one-to-one only, when it is not
     a phone number), the name inside the group, the phone number, and last "unresolved".
  4. Media counts only when the file is really on disk; images, videos and documents are listed with absolute
     paths for the reader to open. Voice notes are transcribed with whisperkit-cli (ffmpeg only ever touches
     a temporary copy) into <work-dir>/transcripts-cache/<key>.json, key = sha1 of "<real absolute path>|<size
     on disk>", the same rule the board uses. Cached transcripts are used even when transcription is off.
     Empty results are not cached, so the next read retries them.
  5. Gaps are always counted and printed, zero included.

Reader config: <work-dir>/config.json, generated from the live config by `wa_setup.py sync-reader-config`.
Keys (all optional): days (3), ignored_jids ([]), my_phone (digits with country code; guessed from group ids
with a warning when empty), my_names ([]), owner_label (default: first of my_names), transcribe (true),
whisper_model, whisper_language ("" = let whisper detect), whisperkit_bin and ffmpeg_bin ("" = PATH, then
/opt/homebrew/bin and /usr/local/bin), keep_snapshot (false). The env var WA_WHISPER_MODEL overrides
whisper_model when the path it names exists. No other tool's config file is read.

Outputs: <work-dir>/sweeps/sweep-<date>-<N>d.json and .md (or --out plus the same name with .md),
<work-dir>/last-sweep.json, and one JSON object as the last line of stdout.
Exit codes: 0 ok · 2 unreadable config or --days below 1 · 3 WhatsApp data missing or snapshot failed ·
4 cannot write the outputs.

Examples:
  /usr/bin/python3 wa_sweep.py                              # the config's window (3 days), every chat
  /usr/bin/python3 wa_sweep.py --only-jid <jid> --days 30    # one chat (either jid form matches)
  /usr/bin/python3 wa_sweep.py --include-jid <jid>          # also pull one chat from beyond the window
  /usr/bin/python3 wa_sweep.py --no-transcribe              # no new transcription (cached ones still show)
  /usr/bin/python3 wa_sweep.py --selftest                   # fake database in a temp dir, no whisper, no real data
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.parse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

VERSION = "1.0.0"
HERE = Path(__file__).resolve().parent
DEFAULT_WA_ROOT = Path.home() / "Library" / "Group Containers" / "group.net.whatsapp.WhatsApp.shared"
DEFAULT_HOME = Path.home() / "WhatsApp-Live"
HOMEBREW_BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")
FFMPEG_TIMEOUT = 300
WHISPER_TIMEOUT = 900

CORE_DATA_EPOCH = 978307200
SESSION_GROUP = 1
SNAPSHOT_DBS = (("ChatStorage", True), ("ContactsV2", False), ("LID", False))
EXIT_OK, EXIT_CONFIG, EXIT_SOURCE, EXIT_WRITE = 0, 2, 3, 4

# Content messages (ZWAMESSAGE.ZMESSAGETYPE). Anything else is a system message: not listed, counted by type.
# The one shared table lives in daemon/wa_common.py (CONTENT_TYPES); this copy is used only when that module
# cannot be imported, and its labels are what the reading pack prints.
LOCAL_CONTENT_TYPES = {
    0: "文字", 1: "图片", 2: "影片", 3: "语音", 5: "位置", 7: "文字",
    8: "文件", 11: "GIF", 14: "名片", 15: "贴图", 20: "图片", 33: "图片",
    41: "文字", 42: "频道图文",
}
# Attachments that really download to disk; media gaps are counted on these only. 7 (link preview) never has a file.
MEDIA_TYPES = frozenset((1, 2, 3, 8, 11, 15, 20, 33, 42))
ICON = {1: "🖼️", 2: "🎬", 3: "🎤", 5: "📍", 8: "📎", 11: "🎞️", 14: "👤", 15: "🩹", 20: "🖼️", 33: "🖼️", 42: "🖼️"}
SYSTEM_LABEL = {6: "群事件", 10: "群事件/改名", 4: "系统", 12: "系统", 13: "系统", 19: "系统", 38: "系统",
                43: "系统", 46: "系统", 57: "系统", 59: "系统", 60: "系统", 66: "系统", 75: "系统"}

READER_DEFAULTS = {
    "days": 3, "ignored_jids": [], "my_phone": "", "my_names": [], "owner_label": "", "transcribe": True,
    "whisper_model": "", "whisper_language": "", "whisperkit_bin": "", "ffmpeg_bin": "", "keep_snapshot": False,
}


def _content_types():
    """(labels by type, audio type, where the type list came from). Uses daemon/wa_common.CONTENT_TYPES when the
    payload has it, so the reader, the doorbell and the backfill agree on what counts as a message."""
    daemon = HERE.parent.parent.parent / "daemon"
    if (daemon / "wa_common.py").is_file():
        try:
            if str(daemon) not in sys.path:
                sys.path.append(str(daemon))
            import wa_common  # noqa: E402
            table = getattr(wa_common, "CONTENT_TYPES", None)
            audio = getattr(wa_common, "AUDIO_TYPE", 3)
            if isinstance(table, dict) and table and all(isinstance(k, int) for k in table):
                labels = dict((k, LOCAL_CONTENT_TYPES.get(k) or str(v)) for k, v in table.items())
                return labels, int(audio), "wa_common"
        except Exception:
            pass
    return dict(LOCAL_CONTENT_TYPES), 3, "local"


CONTENT_TYPES, AUDIO_TYPE, CONTENT_TYPES_SOURCE = _content_types()


def fail(code, msg):
    sys.stderr.write("wa_sweep: " + msg + "\n")
    sys.exit(code)


# ---------------------------------------------------------------- time

def core_to_dt(v):
    return None if v is None else datetime.fromtimestamp(float(v) + CORE_DATA_EPOCH)


def dt_to_core(d):
    return d.timestamp() - CORE_DATA_EPOCH


def iso(d):
    return None if d is None else d.strftime("%Y-%m-%dT%H:%M:%S")


def short(d):
    return None if d is None else d.strftime("%m-%d %H:%M")


# ---------------------------------------------------------------- files and config

def ensure_private_dir(p):
    p = Path(p)
    if not p.exists():
        p.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(str(p), 0o700)
        except OSError:
            pass
    return p


def write_private(path, text):
    """Atomic write, mode 0600 (every output here holds conversation content)."""
    path = Path(path)
    ensure_private_dir(path.parent)
    tmp = path.with_name(".%s.tmp-%d" % (path.name, os.getpid()))
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(str(tmp), 0o600)
    os.replace(str(tmp), str(path))


def load_config(path):
    cfg = {}
    if path.exists():
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(cfg, dict):
                raise ValueError("the top level is not a JSON object")
        except Exception as e:
            fail(EXIT_CONFIG, "cannot read %s: %s" % (path, e))
    for k, v in READER_DEFAULTS.items():
        if cfg.get(k) is None:
            cfg[k] = list(v) if isinstance(v, list) else v
    return cfg


def default_work_dir():
    """paths.home of the live config named by WA_LIVE_CONFIG, else ~/WhatsApp-Live."""
    env = os.environ.get("WA_LIVE_CONFIG")
    if env:
        try:
            home = (json.loads(Path(env).expanduser().read_text(encoding="utf-8")).get("paths") or {}).get("home")
            if home and "<" not in home:
                return Path(home).expanduser()
        except Exception:
            pass
    return DEFAULT_HOME


def owner_label_of(cfg):
    names = [n.strip() for n in (cfg.get("my_names") or []) if isinstance(n, str) and n.strip()]
    return (cfg.get("owner_label") or "").strip() or (names[0] if names else "") or "主人"


# ---------------------------------------------------------------- snapshot

def db_uri(path):
    return "file:%s?mode=ro" % urllib.parse.quote(Path(path).as_posix())


def _side_files(p):
    return [p.with_name(p.name + s) for s in ("-wal", "-shm", "-journal")]


def snapshot(wa_root, work_dir, warnings):
    """Consistent snapshots through the backup API. ChatStorage is required; ContactsV2 and LID fail soft."""
    if not wa_root.is_dir():
        fail(EXIT_SOURCE, "WhatsApp Desktop data folder not found: %s (WhatsApp Desktop is not installed or not linked on this Mac)" % wa_root)
    out = {}
    for name, required in SNAPSHOT_DBS:
        src = wa_root / ("%s.sqlite" % name)
        dst = work_dir / ("%s.sqlite" % name)
        if not src.exists():
            if required:
                fail(EXIT_SOURCE, "%s not found" % src)
            warnings.append("%s.sqlite 不在, 相关的名字 / 号码解析会退级。" % name)
            out[name] = None
            continue
        s = d = None
        try:
            s = sqlite3.connect(db_uri(src), uri=True)
            for stale in [dst] + _side_files(dst):
                if stale.exists():
                    stale.unlink()
            os.close(os.open(str(dst), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600))
            d = sqlite3.connect(str(dst))
            s.backup(d)
            # The copy inherits WAL mode; a read-only connection ATTACHing a WAL database fails on the system
            # python's sqlite ("unable to open database file"), so the snapshot is turned into a plain journal.
            d.execute("PRAGMA journal_mode=DELETE")
            d.close()
            d = None
            for side in _side_files(dst):
                if side.exists():
                    side.unlink()
            os.chmod(str(dst), 0o600)
            out[name] = dst
        except Exception as e:
            if required:
                fail(EXIT_SOURCE, "%s snapshot failed: %s" % (name, e))
            warnings.append("%s 快照失败 (%s), 相关解析退级。" % (name, e))
            out[name] = None
        finally:
            for c in (d, s):
                if c is not None:
                    try:
                        c.close()
                    except Exception:
                        pass
    return out


def remove_snapshots(work_dir):
    for name, _ in SNAPSHOT_DBS:
        p = work_dir / ("%s.sqlite" % name)
        for f in [p] + _side_files(p):
            try:
                if f.exists():
                    f.unlink()
            except OSError:
                pass


def connect(paths, warnings):
    conn = sqlite3.connect(db_uri(paths["ChatStorage"]), uri=True)
    conn.row_factory = sqlite3.Row
    attached = []
    for alias, key in (("lid", "LID"), ("cts", "ContactsV2")):
        if paths.get(key):
            try:
                conn.execute("ATTACH DATABASE ? AS " + alias, (db_uri(paths[key]),))
                attached.append(alias)
            except sqlite3.Error as e:
                warnings.append("%s 快照接不上 (%s), 相关解析退级。" % (key, e))
    return conn, attached


# ---------------------------------------------------------------- names and phones

PHONE_RE = re.compile(r"^\d{7,15}$")
BIDI = re.compile(r"[‎‏‪-‮⁦-⁩ ]")


class Resolver:
    """Speaker names with their source. ⛔ Reading only ZWAGROUPMEMBER.ZCONTACTNAME leads to the wrong
    conclusion that group speakers cannot be named."""

    def __init__(self, conn, attached):
        self.conn = conn
        self.has_lid = "lid" in attached
        self.has_cts = "cts" in attached
        self.cache = {}
        self.stats = Counter()

    def q1(self, sql, args):
        try:
            row = self.conn.execute(sql, args).fetchone()
        except sqlite3.Error:
            return None
        return row[0] if row and row[0] else None

    def phone_for(self, jid):
        if not jid:
            return None
        if jid.endswith("@s.whatsapp.net"):
            p = jid.split("@")[0]
            return p if PHONE_RE.match(p) else None
        if jid.endswith("@lid"):
            p = None
            if self.has_lid:
                p = self.q1("SELECT ZPHONENUMBER FROM lid.ZWAZACCOUNT WHERE ZIDENTIFIER = ?", (jid,))
            if not p and self.has_cts:
                p = self.q1("SELECT ZPHONENUMBER FROM cts.ZWAADDRESSBOOKCONTACT WHERE ZLID = ?", (jid,))
            if p:
                p = re.sub(r"\D", "", str(p))
                return p if PHONE_RE.match(p) else None
        return None

    def lid_for_phone(self, phone):
        phone = re.sub(r"\D", "", phone or "")
        if not PHONE_RE.match(phone):
            return None
        lid = None
        if self.has_cts:
            lid = self.q1("SELECT ZLID FROM cts.ZWAADDRESSBOOKCONTACT WHERE ZWHATSAPPID = ? AND ZLID LIKE '%@lid'",
                          ("%s@s.whatsapp.net" % phone,))
        if not lid and self.has_lid:
            lid = self.q1("SELECT ZIDENTIFIER FROM lid.ZWAZACCOUNT "
                          "WHERE REPLACE(REPLACE(REPLACE(ZPHONENUMBER, '+', ''), ' ', ''), '-', '') = ?", (phone,))
        return str(lid) if lid and str(lid).endswith("@lid") else None

    def jid_forms(self, jid):
        """One person's two ids: `<lid>@lid` and `<phone>@s.whatsapp.net`. Groups and unknown forms map to themselves."""
        forms = set([jid]) if jid else set()
        if not jid:
            return forms
        if jid.endswith("@lid"):
            p = self.phone_for(jid)
            if p:
                forms.add(p + "@s.whatsapp.net")
        elif jid.endswith("@s.whatsapp.net"):
            lid = self.lid_for_phone(jid.split("@")[0])
            if lid:
                forms.add(lid)
        return forms

    def resolve(self, jid, contact_name=None, first_name=None, partner_name=None):
        """-> (name, source, phone or None)."""
        if not jid and not partner_name and not (contact_name or first_name):
            self.stats["未解析"] += 1
            return "⚠️ 未解析", "未解析", None
        key = jid or partner_name or ("group-name:%s" % (contact_name or first_name))
        if key in self.cache:
            name, source, phone = self.cache[key]
            self.stats[source] += 1
            return name, source, phone
        phone = self.phone_for(jid)
        name = source = None
        if self.has_cts and jid:
            full = self.q1("SELECT ZFULLNAME FROM cts.ZWAADDRESSBOOKCONTACT WHERE ZLID = ? OR ZWHATSAPPID = ?", (jid, jid))
            if not full and phone:
                full = self.q1("SELECT ZFULLNAME FROM cts.ZWAADDRESSBOOKCONTACT WHERE ZWHATSAPPID = ?", ("%s@s.whatsapp.net" % phone,))
            if full:
                name, source = full, "通讯录"
        if not name and jid:
            push = self.q1("SELECT ZPUSHNAME FROM ZWAPROFILEPUSHNAME WHERE ZJID = ?", (jid,))
            if not push and phone:
                push = self.q1("SELECT ZPUSHNAME FROM ZWAPROFILEPUSHNAME WHERE ZJID = ?", ("%s@s.whatsapp.net" % phone,))
            if push:
                name, source = push, "自称"
        if not name and partner_name and not PHONE_RE.match(re.sub(r"\D", "", partner_name) or "x"):
            name, source = partner_name, "对话名"
        if not name and (contact_name or first_name):
            name, source = (contact_name or first_name), "群内名"
        if not name and phone:
            name, source = "+" + phone, "号码"
        if not name:
            name, source = (jid or partner_name), "未解析"
        self.cache[key] = (name, source, phone)
        self.stats[source] += 1
        return name, source, phone


def clean_name(text):
    return BIDI.sub(" ", text or "").replace("  ", " ").strip() or None


def looks_like_phone(text):
    return (bool(text) and PHONE_RE.match(re.sub(r"\D", "", text) or "x") is not None
            and re.sub(r"[\d\s+()\-‑]", "", text) == "")


def detect_my_phone(conn):
    """Fallback only: old group ids look like <creator phone>-<timestamp>@g.us; the most frequent creator is
    probably the owner. Wrong for people who mostly join groups others made, and new group ids carry no phone."""
    rows = conn.execute("SELECT ZCONTACTJID FROM ZWACHATSESSION WHERE ZCONTACTJID LIKE '%-%@g.us'").fetchall()
    c = Counter()
    for r in rows:
        m = re.match(r"^(\d{7,15})-\d+@g\.us$", r[0] or "")
        if m:
            c[m.group(1)] += 1
    return c.most_common(1)[0][0] if c else None


# ---------------------------------------------------------------- voice

def find_bin(name, configured=None):
    """Configured path first, then PATH, then the Homebrew locations (launchd and ssh shells often lack them)."""
    c = (configured or "").strip()
    if c and c != "<auto>":
        p = Path(c).expanduser()
        if p.is_file() and os.access(str(p), os.X_OK):
            return str(p)
    found = shutil.which(name)
    if found:
        return found
    for d in HOMEBREW_BIN_DIRS:
        p = Path(d) / name
        if p.is_file() and os.access(str(p), os.X_OK):
            return str(p)
    return None


def whisper_setup(cfg):
    """-> (model, language, whisperkit bin, ffmpeg bin, error or None, notes). Only this reader's config and the
    WA_WHISPER_MODEL env var are read. The model copy should live outside Documents, Desktop and Downloads:
    under launchd, whisperkit-cli opening a model there waits forever on a privacy prompt nobody answers."""
    notes = []
    lang = (cfg.get("whisper_language") or "").strip()
    model = None
    env_model = (os.environ.get("WA_WHISPER_MODEL") or "").strip()
    if env_model:
        p = Path(env_model).expanduser()
        if p.exists():
            model = str(p)
        else:
            notes.append("环境变数 WA_WHISPER_MODEL 指的路径不存在 (%s), 改用 config 的 whisper_model。" % env_model)
    if model is None:
        cm = (cfg.get("whisper_model") or "").strip()
        if not cm or "<" in cm:
            return None, lang, None, None, "没有设 whisper 模型 (config 的 whisper_model, 或环境变数 WA_WHISPER_MODEL)", notes
        p = Path(cm).expanduser()
        if not p.exists():
            return None, lang, None, None, "config 的 whisper_model 路径不存在: %s" % cm, notes
        model = str(p)
    wk = find_bin("whisperkit-cli", cfg.get("whisperkit_bin"))
    if not wk:
        return model, lang, None, None, "找不到 whisperkit-cli (config 的 whisperkit_bin、PATH、/opt/homebrew/bin、/usr/local/bin 都没有)", notes
    ff = find_bin("ffmpeg", cfg.get("ffmpeg_bin"))
    if not ff:
        return model, lang, wk, None, "找不到 ffmpeg (config 的 ffmpeg_bin、PATH、/opt/homebrew/bin、/usr/local/bin 都没有)", notes
    return model, lang, wk, ff, None, notes


def transcript_key(media_path):
    """Transcript cache key: sha1 of "<real absolute path>|<size on disk>". The board computes the same key
    (wa_common.transcript_key) from the same shared folder; change both or neither."""
    real = os.path.realpath(str(media_path))
    return hashlib.sha1(("%s|%d" % (real, os.path.getsize(real))).encode("utf-8")).hexdigest()


NOISE = re.compile(r"^(loading|compil|downloading|model|tokenizer|audio|warm|progress|real\s?time|"
                   r"transcription|elapsed|took|\d+(\.\d+)?\s*(s|ms|it/s)\b|-{3,}|={3,})", re.I)


def clean_whisper(out):
    lines = []
    for raw in (out or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^\[[\d.:]+\s*-+>\s*[\d.:]+\]\s*(.*)$", line)
        if m:
            if m.group(1).strip():
                lines.append(m.group(1).strip())
            continue
        if NOISE.match(line):
            continue
        lines.append(line)
    return " ".join(lines).strip() or None


class Transcriber:
    def __init__(self, cache_dir, cfg, enabled):
        self.cache_dir = Path(cache_dir)
        self.enabled = bool(enabled)
        self.done = self.hit = self.failed = 0
        self.notes = []
        self.model = self.whisperkit = self.ffmpeg = None
        self.lang = ""
        self.err = None
        if self.enabled:
            self.model, self.lang, self.whisperkit, self.ffmpeg, self.err, self.notes = whisper_setup(cfg)
        else:
            self.err = "转写关闭: config 的 transcribe 是 false, 或带了 --no-transcribe"

    def available(self):
        return self.enabled and self.err is None and self.model is not None

    def cached(self, key):
        try:
            text = json.loads((self.cache_dir / (key + ".json")).read_text(encoding="utf-8")).get("text")
        except Exception:
            return None
        return text if isinstance(text, str) and text.strip() else None

    def run(self, abs_path):
        try:
            key = transcript_key(abs_path)
        except OSError as e:
            self.failed += 1
            return None, "读不到语音档: %s" % e
        text = self.cached(key)
        if text:
            self.hit += 1
            return text, None
        if not self.available():
            return None, self.err
        with tempfile.TemporaryDirectory(prefix="wa-sweep-audio.") as td:
            wav = Path(td) / "a.wav"
            # ⛔ ffmpeg never opens WhatsApp's Group Containers folder: under launchd that is macOS's "data of other
            # apps" privacy gate and ffmpeg waits on a prompt nobody answers. This python (with Full Disk Access)
            # copies the file first; ffmpeg only touches the copy.
            src = Path(td) / ("src" + (Path(str(abs_path)).suffix or ".bin"))
            try:
                shutil.copyfile(str(abs_path), str(src))
            except Exception as e:
                self.failed += 1
                return None, "读不到语音档: %s" % e
            try:
                p = subprocess.run([self.ffmpeg, "-nostdin", "-y", "-loglevel", "error", "-i", str(src),
                                    "-ar", "16000", "-ac", "1", str(wav)], capture_output=True, timeout=FFMPEG_TIMEOUT)
            except subprocess.TimeoutExpired:
                self.failed += 1
                return None, "ffmpeg 超时 %ds" % FFMPEG_TIMEOUT
            if p.returncode != 0 or not wav.exists():
                self.failed += 1
                return None, "ffmpeg 转档失败"
            cmd = [self.whisperkit, "transcribe", "--audio-path", str(wav), "--model-path", self.model]
            if self.lang:
                cmd += ["--language", self.lang]
            try:
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=WHISPER_TIMEOUT)
            except subprocess.TimeoutExpired:
                self.failed += 1
                return None, "whisperkit-cli 超时 %ds" % WHISPER_TIMEOUT
            if p.returncode != 0:
                self.failed += 1
                return None, "whisperkit-cli 失败: " + (p.stderr or "").strip()[:160]
            text = clean_whisper(p.stdout)
        if not text:
            self.failed += 1
            return None, "转写结果是空的 (没写快取, 下次会重试)"
        self.done += 1
        try:
            real = os.path.realpath(str(abs_path))
            write_private(self.cache_dir / (key + ".json"),
                          json.dumps({"text": text, "path": real, "size": os.path.getsize(real), "model": self.model,
                                      "at": iso(datetime.now())}, ensure_ascii=False))
        except Exception:
            pass
        return text, None


# ---------------------------------------------------------------- reading

MSG_SQL = (
    "SELECT m.Z_PK, m.ZMESSAGEDATE, m.ZISFROMME, m.ZMESSAGETYPE, m.ZTEXT, m.ZGROUPEVENTTYPE, "
    "       m.ZFROMJID, m.ZSTARRED, "
    "       g.ZMEMBERJID, g.ZCONTACTNAME, g.ZFIRSTNAME, "
    "       mi.ZMEDIALOCALPATH, mi.ZMOVIEDURATION, mi.ZTITLE, mi.ZFILESIZE, "
    "       mi.ZLATITUDE, mi.ZLONGITUDE, mi.ZVCARDNAME, mi.ZXMPPTHUMBPATH "
    "FROM ZWAMESSAGE m "
    "LEFT JOIN ZWAGROUPMEMBER g ON g.Z_PK = m.ZGROUPMEMBER "
    "LEFT JOIN ZWAMEDIAITEM mi ON mi.ZMESSAGE = m.Z_PK "
    "WHERE m.ZCHATSESSION = ? AND m.ZMESSAGEDATE >= ? AND m.ZMESSAGEDATE <= ? "
    "ORDER BY m.ZMESSAGEDATE ASC, m.Z_PK ASC"
)


def abs_media(media_root, local):
    if not local:
        return None
    p = media_root / local
    return p if p.exists() else None


def read_session(conn, resolver, transcriber, s, from_dt, to_dt, my_phone, my_names, owner_label, media_root):
    is_group = s["ZSESSIONTYPE"] == SESSION_GROUP
    jid = s["ZCONTACTJID"]
    rows = conn.execute(MSG_SQL, (s["Z_PK"], dt_to_core(from_dt), dt_to_core(to_dt))).fetchall()
    partner_name, partner_source, partner_phone = (None, None, None)
    if not is_group:
        partner_name, partner_source, partner_phone = resolver.resolve(jid, partner_name=s["ZPARTNERNAME"])

    items, system = [], Counter()
    media = defaultdict(lambda: {"total": 0, "have": 0, "thumb_only": 0})
    participants = {}
    mention_hits = 0
    last = last_in = last_out = None
    n_in = n_out = 0
    names_low = [n.lower() for n in my_names if n]

    for r in rows:
        mtype = r["ZMESSAGETYPE"]
        if mtype not in CONTENT_TYPES:
            system["%s (type %s%s)" % (SYSTEM_LABEL.get(mtype, "系统"), mtype,
                                       "/ev %s" % r["ZGROUPEVENTTYPE"] if r["ZGROUPEVENTTYPE"] else "")] += 1
            continue
        when = core_to_dt(r["ZMESSAGEDATE"])
        from_me = bool(r["ZISFROMME"])
        if from_me:
            speaker, source, sphone = owner_label, "自己", my_phone
            n_out += 1
            last_out = when if (last_out is None or when > last_out) else last_out
        elif is_group:
            speaker, source, sphone = resolver.resolve(r["ZMEMBERJID"] or r["ZFROMJID"], r["ZCONTACTNAME"], r["ZFIRSTNAME"])
            n_in += 1
            last_in = when if (last_in is None or when > last_in) else last_in
            pk = r["ZMEMBERJID"] or r["ZFROMJID"] or speaker
            participants.setdefault(pk, {"name": speaker, "source": source, "phone": sphone,
                                         "jid": pk if "@" in str(pk) else None, "n": 0})["n"] += 1
        else:
            speaker, source, sphone = partner_name, partner_source, partner_phone
            n_in += 1
            last_in = when if (last_in is None or when > last_in) else last_in
        last = when

        text = (r["ZTEXT"] or "").strip()
        if not from_me and text:
            low = text.lower()
            if (my_phone and ("@" + my_phone) in text) or any(n in low for n in names_low):
                mention_hits += 1

        item = {"pk": r["Z_PK"], "t": iso(when), "dir": "out" if from_me else "in", "speaker": speaker,
                "source": source, "type": mtype, "kind": CONTENT_TYPES[mtype], "text": text or None}
        if r["ZSTARRED"]:
            item["starred"] = True

        if mtype in MEDIA_TYPES or mtype in (5, 14):
            ap = abs_media(media_root, r["ZMEDIALOCALPATH"])
            thumb = abs_media(media_root, r["ZXMPPTHUMBPATH"]) if not ap else None
            m = {"kind": CONTENT_TYPES[mtype], "on_disk": ap is not None, "path": str(ap) if ap else None}
            if r["ZMEDIALOCALPATH"] and not ap:
                m["ghost"] = True  # the database has a path but the file is gone
            if thumb:
                m["thumb"] = str(thumb)
            if r["ZTITLE"]:
                m["title"] = r["ZTITLE"]
            if r["ZMOVIEDURATION"]:
                m["duration_s"] = int(r["ZMOVIEDURATION"])
            if r["ZFILESIZE"]:
                m["bytes"] = int(r["ZFILESIZE"])
            if mtype == 5:
                m["lat"], m["lng"] = r["ZLATITUDE"], r["ZLONGITUDE"]
            if mtype == 14 and r["ZVCARDNAME"]:
                m["vcard"] = r["ZVCARDNAME"]
            if mtype in MEDIA_TYPES:
                st = media[CONTENT_TYPES[mtype]]
                st["total"] += 1
                if ap:
                    st["have"] += 1
                elif thumb:
                    st["thumb_only"] += 1
            if mtype == AUDIO_TYPE:
                if ap:
                    txt, err = transcriber.run(ap)
                    m["transcript"] = txt
                    if err:
                        m["transcript_error"] = err
                else:
                    m["transcript_error"] = "档案不在本机"
            item["media"] = m
        items.append(item)

    if not items:
        return None

    in_since_out = sum(1 for i in items if i["dir"] == "in" and (last_out is None or datetime.fromisoformat(i["t"]) > last_out))
    media_missing = sum(v["total"] - v["have"] for v in media.values())

    if is_group:
        reply_mode, href = "copy", None
    elif partner_phone:
        reply_mode, href = "link", "whatsapp://send?phone=%s&text=" % partner_phone   # prefill only; nothing is ever sent
    else:
        reply_mode, href = "copy", None

    shown = clean_name(s["ZPARTNERNAME"])
    if is_group:
        name = shown or jid
    elif partner_source == "通讯录":
        name = partner_name
        if shown and shown != partner_name and not looks_like_phone(shown):
            name += " (WA 显示 %s)" % shown
    elif shown and not looks_like_phone(shown):
        name = shown
        if partner_source == "自称" and partner_name and partner_name != shown:
            name += " (自称 %s)" % partner_name
    elif partner_source == "自称" and partner_name:
        name = "%s (自称, +%s)" % (partner_name, partner_phone or "?")
    else:
        name = shown or partner_name or jid

    return {
        "jid": jid,
        "kind": "group" if is_group else "direct",
        "name": name,
        "name_source": ("群名" if is_group else partner_source),
        "phone": None if is_group else partner_phone,
        "reply_mode": reply_mode,
        "href_prefix": href,
        "archived": bool(s["ZARCHIVED"]),
        "wa_unread": int(s["ZUNREADCOUNT"] or 0),
        "counts": {"total": len(items), "in": n_in, "out": n_out, "system_skipped": sum(system.values()),
                   "media": dict((k, dict(v)) for k, v in media.items()), "media_missing": media_missing,
                   "mentions_me": mention_hits},
        "last": {"at": iso(last), "direction": items[-1]["dir"]},
        "last_in_at": iso(last_in),
        "last_out_at": iso(last_out),
        "in_since_last_out": in_since_out,
        "participants": sorted(participants.values(), key=lambda p: -p["n"]),
        "system_skipped": dict(system),
        "messages": items,
    }


def beyond_window(conn, from_dt, active_pks, eligible_pks):
    """How many eligible chats (not ignored, inside any --only-jid scope) have content but none in the window,
    and when the newest of them stopped."""
    types = ",".join(str(t) for t in sorted(CONTENT_TYPES))
    rows = conn.execute("SELECT m.ZCHATSESSION, MAX(m.ZMESSAGEDATE) FROM ZWAMESSAGE m "
                        "WHERE m.ZMESSAGETYPE IN (%s) GROUP BY m.ZCHATSESSION" % types).fetchall()
    newest = None
    n = 0
    fc = dt_to_core(from_dt)
    for pk, mx in rows:
        if pk not in eligible_pks or pk in active_pks:
            continue
        if mx is not None and mx < fc:
            n += 1
            if newest is None or mx > newest:
                newest = mx
    return {"chats": n, "newest_at": iso(core_to_dt(newest)) if newest else None}


# ---------------------------------------------------------------- markdown reading pack

def fmt_dur(s):
    if not s:
        return ""
    s = int(s)
    return "%d:%02d" % (s // 60, s % 60) if s >= 60 else "%ds" % s


def render_md(doc):
    a = []
    w = doc["window"]
    su = doc["summary"]
    owner = doc["source"].get("owner_label") or "主人"
    a.append("# WhatsApp 全窗阅读包 · %s 天 · %s 起" % (w["days"], w["from"][:16].replace("T", " ")))
    a.append("")
    a.append("- 快照 %s · 产生 %s · wa_sweep.py %s" % (doc["snapshot_at"][:19].replace("T", " "), doc["generated_at"][:19].replace("T", " "), VERSION))
    a.append("- 对话 **%d** (群 %d · 一对一 %d, 其中可开链接 %d) · 讯息 **%d** (进 %d · 出 %d) · 系统讯息另计 %d 条未列" %
             (su["active_chats"], su["groups"], su["directs"], su["linkable_directs"], su["messages"], su["inbound"], su["outbound"], su["system_skipped"]))
    med = su["media"]
    parts = []
    for k, v in (med or {}).items():
        gap = v["total"] - v["have"]
        parts.append("%s %d (本机 %d%s)" % (k, v["total"], v["have"], (", 缺 %d" % gap) if gap else ""))
    # gaps are always counted and printed, zero included: a pack with no media still says so
    a.append("- 媒体: " + (" · ".join(parts) if parts else "无") + " · 语音转字 %d 条 (快取 %d, 失败 %d)" %
             (su["voice"]["transcribed"], su["voice"]["cache_hits"], su["voice"]["failed"]))
    a.append("- 窗外: 还有 %d 个对话窗内零讯息, 最近的一个停在 %s" % (su["beyond_window"]["chats"], (su["beyond_window"]["newest_at"] or "?")[:16].replace("T", " ")))
    a.append("- 只有系统讯息 (改名 / 进出群) 的对话 %d 个, 不算活动, 没列" % su["system_only_chats"])
    if doc["warnings"]:
        a.append("")
        a.append("## ⚠️ 警告")
        for x in doc["warnings"]:
            a.append("- " + x)
    a.append("")
    a.append("⛔ 语音 transcript 是 raw 未校正; 图片 / 文件 / 影片列的是本机路径, 判断前自己打开看。缺口都已数出来, 静默漏掉才是问题。")
    a.append("")
    a.append("## 目录")
    a.append("")
    a.append("| # | 对话 | 类型 | 条 | 进/出 | 最后 | 你之后进 | WA 未读 | 媒体缺 |")
    a.append("|---|---|---|---|---|---|---|---|---|")
    for i, c in enumerate(doc["chats"], 1):
        a.append("| %d | %s | %s | %d | %d/%d | %s | %d | %d | %d |" % (
            i, c["name"], "群" if c["kind"] == "group" else "一对一", c["counts"]["total"], c["counts"]["in"], c["counts"]["out"],
            short(datetime.fromisoformat(c["last"]["at"])), c["in_since_last_out"], c["wa_unread"], c["counts"]["media_missing"]))
    a.append("")

    for i, c in enumerate(doc["chats"], 1):
        kind = "群 (%d 人发言)" % len(c["participants"]) if c["kind"] == "group" else "一对一"
        a.append("## [%d] %s · %s" % (i, c["name"], kind))
        a.append("")
        last_dir = "对方" if c["last"]["direction"] == "in" else "你"
        a.append("- jid `%s` · %d 条 (进 %d / 出 %d) · 最后 %s (%s) · 你上次发 %s, 之后进 %d 条 · WA 未读 %d%s%s" % (
            c["jid"], c["counts"]["total"], c["counts"]["in"], c["counts"]["out"],
            short(datetime.fromisoformat(c["last"]["at"])), last_dir,
            short(datetime.fromisoformat(c["last_out_at"])) if c["last_out_at"] else "窗内没发过",
            c["in_since_last_out"], c["wa_unread"], " · 已封存" if c["archived"] else "",
            (" · 窗外拉进来, 从 %s 起" % c["window_from"][:10]) if c.get("included_beyond_window") else ""))
        a.append("- 回覆: %s%s" % ("链接 `%s`" % c["href_prefix"] if c["reply_mode"] == "link" else "复制 (%s)" % ("群没有号码" if c["kind"] == "group" else "号码解析不出"),
                                  " · 提到你 %d 次" % c["counts"]["mentions_me"] if c["counts"]["mentions_me"] else ""))
        if c["counts"]["media"]:
            parts = []
            for k, v in c["counts"]["media"].items():
                gap = v["total"] - v["have"]
                parts.append("%s %d%s" % (k, v["total"], (" (缺 %d%s)" % (gap, ", 有缩图 %d" % v["thumb_only"] if v["thumb_only"] else "")) if gap else ""))
            a.append("- 媒体: " + " · ".join(parts))
        if c["kind"] == "group" and c["participants"]:
            a.append("- 发言人: " + " · ".join("%s (%s, %d)" % (p["name"], p["source"], p["n"]) for p in c["participants"][:12]) +
                     (" · 另 %d 人" % (len(c["participants"]) - 12) if len(c["participants"]) > 12 else ""))
        if c["system_skipped"]:
            a.append("- 未列的系统讯息: " + " · ".join("%s×%d" % (k, v) for k, v in c["system_skipped"].items()))
        a.append("")
        day = None
        for m in c["messages"]:
            t = datetime.fromisoformat(m["t"])
            if t.date() != day:
                day = t.date()
                a.append("### %s" % day.strftime("%Y-%m-%d (%a)"))
            who = ("**%s**" % owner) if m["dir"] == "out" else ("%s (%s)" % (m["speaker"], m["source"]) if c["kind"] == "group" else m["speaker"])
            line = "- %s %s: " % (t.strftime("%H:%M"), who)
            md = m.get("media")
            if md:
                icon = ICON.get(m["type"], "📎")
                if m["type"] == AUDIO_TYPE:
                    line += "%s 语音 %s" % (icon, fmt_dur(md.get("duration_s")))
                    if md.get("transcript"):
                        line += " → raw:「%s」" % md["transcript"]
                    else:
                        line += " → ⛔ 读不到 (%s)" % md.get("transcript_error", "?")
                elif m["type"] == 5:
                    line += "📍 位置 %s,%s" % (md.get("lat"), md.get("lng"))
                elif m["type"] == 14:
                    line += "👤 名片 %s" % (md.get("vcard") or "")
                else:
                    line += "%s %s" % (icon, md["kind"])
                    if md.get("title"):
                        line += "「%s」" % md["title"]
                    if md.get("duration_s"):
                        line += " %s" % fmt_dur(md["duration_s"])
                    if md.get("on_disk"):
                        line += " → `%s`" % md["path"]
                    elif md.get("thumb"):
                        line += " → ⚠️ 原档不在本机, 只有缩图 `%s`" % md["thumb"]
                    else:
                        line += " → ⛔ 不在本机, 读不到"
                if m.get("text"):
                    line += " · 说明: %s" % m["text"].replace("\n", " ⏎ ")
            else:
                line += (m.get("text") or "(空)").replace("\n", " ⏎ ")
            if m.get("starred"):
                line += " ⭐"
            a.append(line)
        a.append("")
    return "\n".join(a) + "\n"


# ---------------------------------------------------------------- main

def build_parser():
    p = argparse.ArgumentParser(
        prog="wa_sweep.py",
        description="Full-window reader for the WhatsApp Desktop database on this Mac. Read only: writes a reading pack "
                    "(markdown and JSON) into the work dir and never sends anything.",
        epilog="Exit codes: 0 ok, 2 bad config or --days below 1, 3 WhatsApp data missing or snapshot failed, 4 cannot write. "
               "The last stdout line is a JSON summary with the paths of the json and md files.")
    p.add_argument("--work-dir", default=None, help="work dir holding config.json, snapshots, sweeps/ and transcripts-cache/ "
                                                     "(default: paths.home of $WA_LIVE_CONFIG, else ~/WhatsApp-Live)")
    p.add_argument("--days", type=int, default=None, help="window in days (default: the config's days, 3). Widen it only when the owner asks")
    p.add_argument("--now", default=None, help="ISO time to treat as now (tests)")
    p.add_argument("--include-jid", action="append", default=[], help="also pull this chat from beyond the window (repeatable; either jid form)")
    p.add_argument("--include-days", type=int, default=30, help="how far back --include-jid reaches (default 30)")
    p.add_argument("--only-jid", action="append", default=[], help="read only these chats, window unchanged (repeatable; the lid and phone forms of a person both match)")
    p.add_argument("--no-transcribe", action="store_true", help="no new voice transcription (cached transcripts still show)")
    p.add_argument("--out", default=None, help="output JSON path (the md gets the same name); default sweeps/sweep-<date>-<N>d.json")
    p.add_argument("--wa-root", default=None, help="WhatsApp Desktop data folder (tests and unusual installs; default $WA_LIVE_WA_ROOT, else the standard Group Containers folder)")
    p.add_argument("--selftest", action="store_true", help="run the built-in test on a fake database in a temp dir and exit")
    return p


def next_free(base_json):
    if not base_json.exists():
        return base_json
    k = 2
    while True:
        cand = base_json.with_name("%s-%d.json" % (base_json.stem, k))
        if not cand.exists():
            return cand
        k += 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        return selftest()
    args = build_parser().parse_args(argv)
    os.umask(0o077)   # what this writes holds other people's messages: owner only, like the daemon's own files
    if args.work_dir and ("<" in args.work_dir or ">" in args.work_dir or not Path(args.work_dir).expanduser().is_absolute()):
        # an unfilled placeholder or a relative path would quietly create a stray work folder wherever this runs
        fail(EXIT_CONFIG, "--work-dir must be an absolute path with every placeholder filled in: %r" % args.work_dir)
    work = Path(args.work_dir).expanduser() if args.work_dir else default_work_dir()
    ensure_private_dir(work)
    cfg = load_config(work / "config.json")
    try:
        days = args.days if args.days is not None else int(cfg.get("days") or 3)
    except (TypeError, ValueError):
        fail(EXIT_CONFIG, "config days is not a number: %r" % cfg.get("days"))
    if days < 1:
        fail(EXIT_CONFIG, "--days must be at least 1")
    now = datetime.fromisoformat(args.now) if args.now else datetime.now()
    from_dt = now - timedelta(days=days)
    warnings = []
    wa_root = Path(args.wa_root or os.environ.get("WA_LIVE_WA_ROOT") or str(DEFAULT_WA_ROOT)).expanduser()
    owner_label = owner_label_of(cfg)

    paths = snapshot(wa_root, work, warnings)
    snap_at = datetime.now()
    try:
        conn, attached = connect(paths, warnings)
        resolver = Resolver(conn, attached)
        transcriber = Transcriber(work / "transcripts-cache", cfg, bool(cfg.get("transcribe", True)) and not args.no_transcribe)
        warnings.extend(transcriber.notes)
        if not transcriber.available():
            warnings.append("语音没有现转: %s。快取里转过的照用, 其余语音算缺口。" % transcriber.err)

        my_phone = re.sub(r"\D", "", str(cfg.get("my_phone") or "")) or None
        if not my_phone:
            my_phone = detect_my_phone(conn)
            if my_phone:
                warnings.append("config 没有 my_phone, 用群 id 猜的 +%s (常被拉进别人建的群时会猜错); 在 config.json 填 my_phone。" % my_phone)
            else:
                warnings.append("config 没有 my_phone 也猜不出来, @提及 只认 my_names; 在 config.json 填 my_phone。")
        my_names = [n for n in (cfg.get("my_names") or []) if isinstance(n, str) and n.strip()]

        def expand(jids):
            out = set()
            for j in jids or []:
                if isinstance(j, str) and j.strip():
                    out |= resolver.jid_forms(j.strip())
            return out

        ignored = expand(cfg.get("ignored_jids"))
        only = expand(args.only_jid)
        includes = expand(args.include_jid)
        media_root = wa_root / "Message"

        sessions = conn.execute("SELECT Z_PK, ZSESSIONTYPE, ZPARTNERNAME, ZCONTACTJID, ZARCHIVED, ZUNREADCOUNT, ZLASTMESSAGEDATE "
                                "FROM ZWACHATSESSION WHERE ZSESSIONTYPE IN (0,1)").fetchall()
        chats, active_pks, eligible_pks = [], set(), set()
        system_only = 0
        pruned = {"ignored": 0, "status_or_broadcast": 0}
        for s in sessions:
            jid = s["ZCONTACTJID"] or ""
            if jid.endswith("@status") or jid.endswith("@broadcast"):
                pruned["status_or_broadcast"] += 1
                continue
            if jid in ignored:
                pruned["ignored"] += 1
                continue
            if only and jid not in only and jid not in includes:
                continue
            eligible_pks.add(s["Z_PK"])
            start = from_dt
            if jid in includes:
                start = now - timedelta(days=args.include_days)
            if s["ZLASTMESSAGEDATE"] is not None and core_to_dt(s["ZLASTMESSAGEDATE"]) < start:
                continue
            rec = read_session(conn, resolver, transcriber, s, start, now, my_phone, my_names, owner_label, media_root)
            if rec is None:
                # rows in the window but no content message: only renames, joins and the like
                n_sys = conn.execute("SELECT COUNT(*) FROM ZWAMESSAGE WHERE ZCHATSESSION=? AND ZMESSAGEDATE>=?",
                                     (s["Z_PK"], dt_to_core(start))).fetchone()[0]
                if n_sys:
                    system_only += 1
                continue
            if jid in includes:
                rec["included_beyond_window"] = True
                rec["window_from"] = iso(start)
            chats.append(rec)
            active_pks.add(s["Z_PK"])

        chats.sort(key=lambda c: c["last"]["at"], reverse=True)
        beyond = beyond_window(conn, from_dt, active_pks, eligible_pks)
        conn.close()
    finally:
        if not cfg.get("keep_snapshot"):
            remove_snapshots(work)

    media_tot = defaultdict(lambda: {"total": 0, "have": 0, "thumb_only": 0})
    for c in chats:
        for k, v in c["counts"]["media"].items():
            for f in ("total", "have", "thumb_only"):
                media_tot[k][f] += v[f]
    summary = {
        "active_chats": len(chats),
        "groups": sum(1 for c in chats if c["kind"] == "group"),
        "directs": sum(1 for c in chats if c["kind"] == "direct"),
        "linkable_directs": sum(1 for c in chats if c["reply_mode"] == "link"),
        "messages": sum(c["counts"]["total"] for c in chats),
        "inbound": sum(c["counts"]["in"] for c in chats),
        "outbound": sum(c["counts"]["out"] for c in chats),
        "system_skipped": sum(c["counts"]["system_skipped"] for c in chats),
        "system_only_chats": system_only,
        "media": dict((k, dict(v)) for k, v in media_tot.items()),
        "media_missing": sum(v["total"] - v["have"] for v in media_tot.values()),
        "voice": {"transcribed": transcriber.done + transcriber.hit, "fresh": transcriber.done,
                  "cache_hits": transcriber.hit, "failed": transcriber.failed},
        "speaker_resolution": dict(resolver.stats),
        "pruned": pruned,
        "beyond_window": beyond,
    }
    doc = {
        "schema": "wa-sweep/1",
        "generated_at": iso(datetime.now()),
        "snapshot_at": iso(snap_at),
        "window": {"days": days, "from": iso(from_dt), "to": iso(now)},
        "source": {"db": str(wa_root / "ChatStorage.sqlite"), "contacts": bool(paths.get("ContactsV2")), "lid": bool(paths.get("LID")),
                   "my_phone": my_phone, "owner_label": owner_label, "content_types": CONTENT_TYPES_SOURCE},
        "summary": summary,
        "warnings": warnings,
        "chats": chats,
    }

    if args.out:
        out_json = Path(args.out).expanduser()
    else:
        out_json = next_free(ensure_private_dir(work / "sweeps") / ("sweep-%s-%dd.json" % (now.strftime("%Y-%m-%d"), days)))
    out_md = out_json.with_suffix(".md")
    try:
        write_private(out_json, json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
        write_private(out_md, render_md(doc))
    except Exception as e:
        fail(EXIT_WRITE, "cannot write the outputs: %s" % e)
    try:
        # its own small file: config.json is generated by wa_setup and never rewritten here
        write_private(work / "last-sweep.json", json.dumps({"at": iso(datetime.now()), "days": days, "json": str(out_json),
                                                              "md": str(out_md)}, ensure_ascii=False) + "\n")
    except Exception:
        pass

    print(json.dumps({"ok": True, "json": str(out_json), "md": str(out_md), "md_bytes": out_md.stat().st_size,
                      "chats": len(chats), "messages": summary["messages"], "days": days,
                      "media_missing": summary["media_missing"], "voice": summary["voice"],
                      "beyond_window": beyond, "warnings": len(warnings)}, ensure_ascii=False))
    return EXIT_OK


# ---------------------------------------------------------------- selftest (fake database, fake tools, temp dir only)

def _fake_whatsapp(wa, now):
    """A small WhatsApp Desktop data folder with invented people. Returns the media paths it created."""
    (wa / "Message" / "Media").mkdir(parents=True)
    core = lambda d: dt_to_core(d)  # noqa: E731
    chat = sqlite3.connect(str(wa / "ChatStorage.sqlite"))
    chat.execute("PRAGMA journal_mode=WAL")
    chat.executescript("""
        CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, ZSESSIONTYPE INTEGER, ZPARTNERNAME TEXT, ZCONTACTJID TEXT,
            ZARCHIVED INTEGER, ZUNREADCOUNT INTEGER, ZLASTMESSAGEDATE REAL);
        CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZMESSAGEDATE REAL, ZISFROMME INTEGER,
            ZMESSAGETYPE INTEGER, ZTEXT TEXT, ZGROUPEVENTTYPE INTEGER, ZFROMJID TEXT, ZPUSHNAME TEXT, ZSTARRED INTEGER, ZGROUPMEMBER INTEGER);
        CREATE TABLE ZWAGROUPMEMBER (Z_PK INTEGER PRIMARY KEY, ZMEMBERJID TEXT, ZCONTACTNAME TEXT, ZFIRSTNAME TEXT);
        CREATE TABLE ZWAMEDIAITEM (Z_PK INTEGER PRIMARY KEY, ZMESSAGE INTEGER, ZMEDIALOCALPATH TEXT, ZMEDIAURL TEXT, ZMOVIEDURATION REAL,
            ZTITLE TEXT, ZFILESIZE INTEGER, ZLATITUDE REAL, ZLONGITUDE REAL, ZVCARDNAME TEXT, ZXMPPTHUMBPATH TEXT);
        CREATE TABLE ZWAPROFILEPUSHNAME (Z_PK INTEGER PRIMARY KEY, ZJID TEXT, ZPUSHNAME TEXT);
    """)
    h = lambda n: now - timedelta(hours=n)  # noqa: E731
    sessions = [
        (1, 0, "Aiko", "15550100001@s.whatsapp.net", 0, 1, core(h(1))),
        (2, 1, "Weekend Hikers", "15550100000-1700000000@g.us", 0, 0, core(h(2))),
        (3, 0, "", "0@status", 0, 0, core(h(1))),
        (4, 0, "Ignored Ivan", "15550100004@s.whatsapp.net", 0, 0, core(now - timedelta(days=9))),
        (5, 0, "Old Olga", "15550100005@s.whatsapp.net", 0, 0, core(now - timedelta(days=10))),
        (6, 0, "", "333@lid", 1, 0, core(h(5))),
        (7, 1, "Quiet Group", "15550100000-1700000001@g.us", 0, 0, core(h(3))),
    ]
    chat.executemany("INSERT INTO ZWACHATSESSION VALUES (?,?,?,?,?,?,?)", sessions)
    chat.executemany("INSERT INTO ZWAGROUPMEMBER VALUES (?,?,?,?)", [
        (1, "222@lid", "Benny", None), (2, "15550100003@s.whatsapp.net", None, None), (3, "444@lid", "Dev", None)])
    msgs = [
        (1, 1, core(h(3)), 0, 0, "Hi, is the Saturday class still on?", None, None, None, 0, None),
        (2, 1, core(h(2.9)), 1, 0, "Yes, 10am", None, None, None, 1, None),
        (3, 1, core(h(2.5)), 0, 1, None, None, None, None, 0, None),
        (4, 1, core(h(2.0)), 0, 3, None, None, None, None, 0, None),
        (5, 1, core(h(1.5)), 0, 3, None, None, None, None, 0, None),
        (6, 2, core(h(4)), 0, 0, "Who is bringing snacks?", None, "222@lid", None, 0, 1),
        (7, 2, core(h(3.5)), 0, 0, "Mika can you confirm the route", None, None, None, 0, 2),
        (8, 2, core(h(3.2)), 0, 6, None, 1, None, None, 0, None),
        (9, 2, core(h(3.0)), 0, 0, "I will bring water", None, None, None, 0, 3),
        (10, 2, core(h(2.0)), 1, 0, "Route is the east trail", None, None, None, 0, None),
        (11, 4, core(now - timedelta(days=9)), 0, 0, "old ignored hello", None, None, None, 0, None),
        (12, 5, core(now - timedelta(days=10)), 0, 0, "old hello", None, None, None, 0, None),
        (13, 6, core(h(5)), 0, 0, "Hello from a lid chat", None, None, None, 0, None),
        (14, 3, core(h(1)), 0, 0, "status text", None, None, None, 0, None),
        (15, 7, core(h(3)), 0, 10, None, 2, None, None, 0, None),
    ]
    chat.executemany("INSERT INTO ZWAMESSAGE VALUES (?,?,?,?,?,?,?,?,?,?,?)", msgs)
    media = wa / "Message" / "Media"
    files = {}
    for name, data in (("photo.jpg", b"\xff\xd8fakejpeg"), ("voice-cached.opus", b"OggS-cached"), ("voice-new.opus", b"OggS-new-voice")):
        (media / name).write_bytes(data)
        files[name] = media / name
    chat.executemany("INSERT INTO ZWAMEDIAITEM VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
        (1, 3, "Media/photo.jpg", None, None, None, 10, None, None, None, None),
        (2, 4, "Media/voice-cached.opus", None, 7, None, 11, None, None, None, None),
        (3, 5, "Media/voice-new.opus", None, 12, None, 14, None, None, None, None),
    ])
    chat.executemany("INSERT INTO ZWAPROFILEPUSHNAME VALUES (?,?,?)", [(1, "15550100003@s.whatsapp.net", "Cara")])
    chat.commit()
    chat.close()
    cts = sqlite3.connect(str(wa / "ContactsV2.sqlite"))
    cts.executescript("CREATE TABLE ZWAADDRESSBOOKCONTACT (Z_PK INTEGER PRIMARY KEY, ZLID TEXT, ZWHATSAPPID TEXT, ZFULLNAME TEXT, ZPHONENUMBER TEXT);")
    cts.executemany("INSERT INTO ZWAADDRESSBOOKCONTACT VALUES (?,?,?,?,?)", [
        (1, None, "15550100001@s.whatsapp.net", "Aiko Tan", "+1 555 0100 001"),
        (2, None, "15550100002@s.whatsapp.net", "Ben Lim", "+15550100002"),
    ])
    cts.commit()
    cts.close()
    lid = sqlite3.connect(str(wa / "LID.sqlite"))
    lid.executescript("CREATE TABLE ZWAZACCOUNT (Z_PK INTEGER PRIMARY KEY, ZIDENTIFIER TEXT, ZPHONENUMBER TEXT);")
    lid.executemany("INSERT INTO ZWAZACCOUNT VALUES (?,?,?)", [(1, "222@lid", "+15550100002"), (2, "333@lid", "15550100006")])
    lid.commit()
    lid.close()
    return files


def selftest():
    results = []

    def check(name, ok, detail=""):
        results.append(bool(ok))
        print(("  ok    " if ok else "  FAIL  ") + name + ("" if ok else "   (%s)" % detail))

    def run(argv):
        import contextlib
        import io
        buf = io.StringIO()
        code = 0
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                code = main(argv)
        except SystemExit as e:
            code = e.code if isinstance(e.code, int) else 1
        lines = [l for l in buf.getvalue().splitlines() if l.strip()]
        out = None
        if lines:
            try:
                out = json.loads(lines[-1])
            except ValueError:
                out = None
        return code, out

    saved_env = dict((k, os.environ.get(k)) for k in ("WA_WHISPER_MODEL", "WA_LIVE_CONFIG", "WA_LIVE_WA_ROOT"))
    for k in saved_env:
        os.environ.pop(k, None)
    try:
        with tempfile.TemporaryDirectory(prefix="wa-sweep-selftest.") as td:
            td = Path(td)
            wa = td / "Group Containers" / "fake.whatsapp.shared"
            now = datetime(2026, 3, 10, 12, 0, 0)
            files = _fake_whatsapp(wa, now)
            work = td / "work"
            work.mkdir()
            model = td / "whisper-model"
            model.mkdir()
            whisper_out = td / "whisper-out.txt"
            whisper_out.write_text("", encoding="utf-8")
            fake_ffmpeg = td / "fake-ffmpeg"
            fake_ffmpeg.write_text("#!/bin/sh\nfor a in \"$@\"; do last=\"$a\"; done\nprintf 'RIFF' > \"$last\"\n", encoding="utf-8")
            fake_whisper = td / "fake-whisperkit"
            fake_whisper.write_text("#!/bin/sh\ncat '%s'\n" % whisper_out, encoding="utf-8")
            for f in (fake_ffmpeg, fake_whisper):
                os.chmod(str(f), 0o755)
            cfg = {"days": 3, "ignored_jids": ["15550100004@s.whatsapp.net"], "my_phone": "15550100000", "my_names": ["Mika"],
                   "owner_label": "Mika", "transcribe": True, "whisper_model": str(model), "whisper_language": "",
                   "whisperkit_bin": str(fake_whisper), "ffmpeg_bin": str(fake_ffmpeg), "keep_snapshot": False}
            cfg_text = json.dumps(cfg, indent=2)
            (work / "config.json").write_text(cfg_text, encoding="utf-8")
            cache = work / "transcripts-cache"
            cache.mkdir()
            key_cached = transcript_key(files["voice-cached.opus"])
            real = os.path.realpath(str(files["voice-cached.opus"]))
            check("transcript_key is sha1 of '<real path>|<size on disk>'",
                  key_cached == hashlib.sha1(("%s|%d" % (real, os.path.getsize(real))).encode("utf-8")).hexdigest())
            (cache / (key_cached + ".json")).write_text(json.dumps({"text": "see you at ten"}), encoding="utf-8")
            key_new = transcript_key(files["voice-new.opus"])
            base = ["--work-dir", str(work), "--wa-root", str(wa), "--now", now.isoformat()]

            code, out = run(base + ["--out", str(td / "out" / "sweep.json")])
            check("run 1 exits 0 with the JSON summary as the last stdout line", code == 0 and out and out.get("ok"), "code %s out %s" % (code, out))
            doc = json.loads((td / "out" / "sweep.json").read_text(encoding="utf-8")) if code == 0 else {"chats": [], "summary": {}, "warnings": []}
            md = (td / "out" / "sweep.md").read_text(encoding="utf-8") if code == 0 else ""
            by = dict((c["jid"], c) for c in doc["chats"])
            check("window holds the direct chat, the group and the lid chat (status, ignored, old and system-only left out)",
                  sorted(by) == sorted(["15550100001@s.whatsapp.net", "15550100000-1700000000@g.us", "333@lid"]), sorted(by))
            su = doc.get("summary") or {}
            check("pruned counts status and ignored", su.get("pruned") == {"ignored": 1, "status_or_broadcast": 1}, su.get("pruned"))
            check("beyond window counts the old chat but not the ignored one", (su.get("beyond_window") or {}).get("chats") == 1, su.get("beyond_window"))
            check("a chat with only system messages is counted, not listed", su.get("system_only_chats") == 1, su.get("system_only_chats"))
            g = by.get("15550100000-1700000000@g.us") or {}
            srcs = sorted(set(p["source"] for p in g.get("participants", [])))
            check("speaker ladder: address book via lid, push name, group name", srcs == sorted(["通讯录", "自称", "群内名"]), srcs)
            check("group counts the system message and the mention of my_names", g.get("counts", {}).get("system_skipped") == 1
                  and g.get("counts", {}).get("mentions_me") == 1, g.get("counts"))
            a = by.get("15550100001@s.whatsapp.net") or {}
            check("direct chat is named from the address book with the WA display name", a.get("name") == "Aiko Tan (WA 显示 Aiko)", a.get("name"))
            voices = [m["media"] for m in a.get("messages", []) if m["type"] == AUDIO_TYPE]
            check("cached transcript is used", len(voices) == 2 and voices[0].get("transcript") == "see you at ten", voices[:1])
            check("empty whisper result is reported and not cached", len(voices) == 2 and not voices[1].get("transcript")
                  and "空" in (voices[1].get("transcript_error") or "") and not (cache / (key_new + ".json")).exists(), voices[1:])
            check("voice counters: 1 cache hit, 1 failed", su.get("voice", {}).get("cache_hits") == 1 and su.get("voice", {}).get("failed") == 1, su.get("voice"))
            img = [m["media"] for m in a.get("messages", []) if m["type"] == 1]
            check("image on disk is listed with its absolute path", img and img[0].get("on_disk") and Path(img[0]["path"]).exists(), img)
            own_lines = [l for l in md.splitlines() if re.match(r"^- \d\d:\d\d \*\*", l)]
            check("the owner's own messages carry the configured owner label and nothing else",
                  len(own_lines) == 2 and all(l.split(": ", 1)[0].endswith("**Mika**") for l in own_lines), own_lines)
            check("snapshots are deleted after the read (keep_snapshot false)", not (work / "ChatStorage.sqlite").exists())
            check("outputs are private (0600)", oct(os.stat(str(td / "out" / "sweep.md")).st_mode & 0o777) == "0o600")
            check("config.json is left untouched; last-sweep.json records the run",
                  (work / "config.json").read_text(encoding="utf-8") == cfg_text and (work / "last-sweep.json").exists())

            whisper_out.write_text("[0.00 --> 2.00] ok see you then\nTook 1.2 s\n", encoding="utf-8")
            code, out = run(base + ["--out", str(td / "out2" / "sweep.json")])
            doc2 = json.loads((td / "out2" / "sweep.json").read_text(encoding="utf-8")) if code == 0 else {"chats": [], "summary": {}}
            a2 = dict((c["jid"], c) for c in doc2["chats"]).get("15550100001@s.whatsapp.net") or {}
            v2 = [m["media"] for m in a2.get("messages", []) if m["type"] == AUDIO_TYPE]
            cached_new = {}
            try:
                cached_new = json.loads((cache / (key_new + ".json")).read_text(encoding="utf-8"))
            except Exception:
                pass
            check("the empty one retries next run, and a real result is cleaned and cached",
                  code == 0 and len(v2) == 2 and v2[1].get("transcript") == "ok see you then" and cached_new.get("text") == "ok see you then",
                  "code %s %s %s" % (code, v2[1:], cached_new))

            code, out = run(base + ["--only-jid", "15550100006@s.whatsapp.net", "--out", str(td / "out3" / "sweep.json"), "--no-transcribe"])
            doc3 = json.loads((td / "out3" / "sweep.json").read_text(encoding="utf-8")) if code == 0 else {"chats": []}
            check("--only-jid with the phone form finds the chat stored under its lid", [c["jid"] for c in doc3["chats"]] == ["333@lid"],
                  [c["jid"] for c in doc3["chats"]])
            if code == 0:
                no_media = json.loads(json.dumps(doc3))
                no_media["summary"]["media"] = {}
                check("a pack with no media still prints its media line, zero included",
                      "\n- 媒体: 无 · 语音转字 " in render_md(no_media), [l for l in render_md(no_media).splitlines() if "媒体" in l][:2])
            else:
                check("a pack with no media still prints its media line, zero included", False, "the --only-jid run failed")

            cfg2 = dict(cfg, my_phone="", keep_snapshot=True, whisper_model=str(td / "missing-model"))
            (work / "config.json").write_text(json.dumps(cfg2), encoding="utf-8")
            os.environ["WA_WHISPER_MODEL"] = str(model)
            code, out = run(base + ["--out", str(td / "out4" / "sweep.json")])
            doc4 = json.loads((td / "out4" / "sweep.json").read_text(encoding="utf-8")) if code == 0 else {"warnings": [], "source": {}}
            check("empty my_phone is guessed from group ids, with a warning",
                  doc4["source"].get("my_phone") == "15550100000" and any("my_phone" in w for w in doc4["warnings"]), doc4.get("warnings"))
            check("WA_WHISPER_MODEL overrides a missing config model (no 'no model' warning)",
                  not any("whisper_model" in w for w in doc4["warnings"]), doc4.get("warnings"))
            check("keep_snapshot true keeps the snapshot as a plain journal database",
                  (work / "ChatStorage.sqlite").exists() and not (work / "ChatStorage.sqlite-wal").exists())
            os.environ["WA_WHISPER_MODEL"] = str(td / "nowhere")
            code, out = run(base + ["--out", str(td / "out5" / "sweep.json"), "--no-transcribe"])
            doc5 = json.loads((td / "out5" / "sweep.json").read_text(encoding="utf-8")) if code == 0 else {"warnings": []}
            check("--no-transcribe says why voice was not transcribed", any("没有现转" in w for w in doc5["warnings"]), doc5.get("warnings"))
            os.environ.pop("WA_WHISPER_MODEL", None)

            code, _ = run(base + ["--days", "0"])
            check("--days 0 exits 2", code == EXIT_CONFIG, code)
            code, _ = run(["--work-dir", str(work), "--wa-root", str(td / "no-such-folder")])
            check("missing WhatsApp data folder exits 3", code == EXIT_SOURCE, code)
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print("content types from: %s" % CONTENT_TYPES_SOURCE)
    print("wa_sweep selftest: %d/%d ok" % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
