#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wa_backup.py · the nightly backup (interface 5 of 5, see references/interfaces.md): copies what is worth keeping from the live
folder into <backup.dest>/<YYYY-MM-DD>/ and keeps the newest `backup.keep` dated folders.

Destination (`backup.dest`): any folder, such as an iCloud Drive, Google Drive or Dropbox folder synced by its client,
an external disk or a NAS mount. Choose one only you can see: the backup holds personal data (names, phone numbers,
conversation summaries, drafts). The folder itself is created when missing, its parent never is (an unmounted disk
or a stopped sync client must fail, not grow a local copy at that path).
`backup.require_process`: a process that must be running before anything is written, typically the sync client
("Google Drive", "Dropbox"); empty means no check. A plain name is matched exactly (pgrep -x) and then as the last
part of an executable path; a value containing a slash is matched against full command lines.

Copied (a missing item is skipped, not an error):
  state.sqlite           SQLite online backup from a read only connection; PRAGMA integrity_check on the copy must say ok
  config.json            secret looking values redacted: keys whose names contain token, secret, passw, pwd, credential,
                         api key, access key, private key, client key, app key, auth, cookie, bearer, webhook or signing;
                         and inside any string, bot tokens, bearer tokens, sk-/ghp_/xox tokens and passwords in URLs
  summaries.md · facts.json · voice-pairs.jsonl · second-source-whitelist.json ·
  topic-map.json · seeds.json · acceptance.json · prompts/ · digest/ · dossier-backups/ · adapters/ (the owner's own)
Never copied: telegram.json · runs/ · commands/ · variants/ · dossier-cli/ · calendar/ · *.log · claude-settings.json
  (rendered again by wa_setup.py init) · backup-last.json · second-source-last.json
Every copied file is read back (same size as its source). Rotation deletes only YYYY-MM-DD folders directly under the
destination and never today's. Result: <live>/backup-last.json with seven fixed keys
  {"at", "ok", "dest", "bytes", "files", "error", "dry_run"}
A dry run writes nothing at all, not even that file.

CLI
  wa_backup.py --dry-run [--dest P]                       print the plan, write nothing
  wa_backup.py --run [--dest P] [--no-process-check]      back up now
  wa_backup.py --selftest                                 a fake live folder in a temp dir
  --config P                                              live/config.json (default: env WA_LIVE_CONFIG or ~/WhatsApp-Live/live/config.json)
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
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wa_common as W  # noqa: E402

DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SECRET_KEY_RE = re.compile(r"(token|secret|passw|pwd|credential|api[_-]?key|access[_-]?key|private[_-]?key|client[_-]?key|"
                           r"app[_-]?key|auth|cookie|bearer|webhook|signing)", re.I)
REDACTED = "<redacted>"
_VALUE_PATTERNS = [
    (re.compile(r"(?<![0-9])\d{6,}:[A-Za-z0-9_-]{30,}"), REDACTED),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"), "Bearer " + REDACTED),
    (re.compile(r"\b(?:sk|rk)-[A-Za-z0-9_-]{16,}\b"), REDACTED),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), REDACTED),
    (re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}\b"), REDACTED),
    (re.compile(r"://[^/\s:@]+:[^/\s@]+@"), "://" + REDACTED + "@"),
]
COPY_FILES = ("summaries.md", "facts.json", "topic-map.json")
# owner records that are not in the lists above: kept too (they are small and hard to rebuild)
EXTRA_FILES = ("seeds.json", "acceptance.json")
COPY_DIRS = ("prompts", "digest", "dossier-backups", "adapters")
NEVER = ("telegram.json", "runs/", "commands/", "variants/", "dossier-cli/", "calendar/", "*.log",
         "claude-settings.json (rendered by wa_setup.py init)", "backup-last.json", "second-source-last.json",
         "people-write.lock (an empty lock file)")


# ---------------------------------------------------------------- pieces

def _redact_string(s):
    for rx, rep in _VALUE_PATTERNS:
        s = rx.sub(rep, s)
    return s


def redact(obj):
    """Recursively: a value under a secret looking key becomes "<redacted>"; token shapes inside strings too."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            out[k] = REDACTED if SECRET_KEY_RE.search(str(k)) else redact(v)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    if isinstance(obj, str):
        return _redact_string(obj)
    return obj


def _ere_escape(s):
    return re.sub(r"([.\[\](){}*+?|^$\\])", r"\\\1", s)


def process_running(name):
    """Is a process with this name running? Exact name first, then the last part of an executable path; a name with a
    slash is matched against full command lines. pgrep never matches itself."""
    name = str(name or "").strip()
    if not name:
        return True
    try:
        if "/" in name:
            r = subprocess.run(["/usr/bin/pgrep", "-f", _ere_escape(name)], capture_output=True, timeout=10)
            return r.returncode == 0 and bool(r.stdout.strip())
        r = subprocess.run(["/usr/bin/pgrep", "-x", name], capture_output=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return True
        r = subprocess.run(["/usr/bin/pgrep", "-f", "/%s( |$)" % _ere_escape(name)], capture_output=True, timeout=10)
        return r.returncode == 0 and bool(r.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def backup_sqlite(src, dst):
    """Online backup (never a copy of a live database file), written to a temp name, integrity checked, then renamed."""
    dst = Path(dst)
    tmp = dst.with_name("." + dst.name + ".partial")
    if tmp.exists():
        tmp.unlink()
    s = sqlite3.connect("file:%s?mode=ro" % urllib.parse.quote(str(src)), uri=True, timeout=30)
    d = sqlite3.connect(str(tmp))
    try:
        s.backup(d)
        row = d.execute("PRAGMA integrity_check").fetchone()
    finally:
        s.close()
        d.close()
    res = str(row[0]) if row else "no result"
    if res != "ok":
        tmp.unlink()
        raise RuntimeError("state.sqlite copy failed integrity_check: %r" % res)
    os.chmod(str(tmp), 0o600)
    os.replace(str(tmp), str(dst))


def dir_size(p):
    total, n = 0, 0
    for root, _dirs, files in os.walk(str(p)):
        for f in files:
            if f.endswith(".log"):
                continue
            try:
                total += (Path(root) / f).stat().st_size
                n += 1
            except OSError:
                pass
    return total, n


def date_dirs(dest):
    """YYYY-MM-DD folders directly under dest, oldest first."""
    dest = Path(dest)
    if not dest.is_dir():
        return []
    return sorted(p for p in dest.iterdir() if p.is_dir() and DATE_DIR.match(p.name))


def plan(cfg, dest, now):
    """What a run would do; the dry run and the real run share this plan."""
    live = W.live_dir(cfg)
    day = now.date().isoformat()
    target = Path(dest) / day
    items = []
    later = []     # known items that do not exist yet (voice-pairs.jsonl before the first paired reply, for example)

    def add(kind, src, rel, expected=True):
        src = Path(os.path.expanduser(str(src)))
        if (kind == "dir" and src.is_dir()) or (kind != "dir" and src.is_file()):
            items.append((kind, src, rel))
        elif expected:
            later.append((kind, src, rel))
    add("sqlite", live / "state.sqlite", "state.sqlite")
    cp = Path(str(cfg.get("config_path") or "")) if cfg.get("config_path") else live / "config.json"
    add("config", cp if cp.is_file() else live / "config.json", "config.json")
    for name in COPY_FILES[:2]:
        add("file", live / name, name)
    add("file", ((cfg.get("voice") or {}).get("pairs")) or (live / "voice-pairs.jsonl"), "voice-pairs.jsonl")
    ss = cfg.get("second_source") or {}
    src_cfg = ss.get(str(ss.get("source") or "")) if isinstance(ss.get(str(ss.get("source") or "")), dict) else {}
    wl = src_cfg.get("whitelist") or ss.get("whitelist") or ((ss.get("lark") or {}).get("whitelist")) or (live / "second-source-whitelist.json")
    add("file", wl, "second-source-whitelist.json", expected=str(ss.get("source") or "none") != "none")
    add("file", live / COPY_FILES[2], COPY_FILES[2])
    for name in EXTRA_FILES:
        add("file", live / name, name)
    for name in COPY_DIRS:
        add("dir", live / name, name, expected=(name != "adapters"))
    keep = max(1, int((cfg.get("backup") or {}).get("keep") or 7))
    existing = [p for p in date_dirs(dest) if p.name != day]
    survivors = sorted(existing + [target], key=lambda p: p.name)
    to_delete = [p for p in (survivors[:-keep] if len(survivors) > keep else []) if p.name != day]
    return {"live": live, "day": day, "target": target, "items": items, "later": later, "to_delete": to_delete, "keep": keep}


def describe(pl, proc=""):
    lines = ["live folder: %s" % pl["live"], "destination: %s" % pl["target"]]
    how = {"sqlite": "online backup and integrity_check", "config": "secrets redacted", "file": "copied"}
    tag = {"sqlite": "[db]    ", "config": "[config]", "file": "[file]  ", "dir": "[dir]   "}
    for kind, src, rel in pl["items"]:
        if kind == "dir":
            b, n = dir_size(src)
            lines.append("  %s %-30s %9.1f KB  %d files" % (tag[kind], rel + "/", b / 1024.0, n))
        else:
            lines.append("  %s %-30s %9.1f KB  %s" % (tag[kind], rel, src.stat().st_size / 1024.0, how[kind]))
    if not pl["items"]:
        lines.append("  (nothing to copy yet)")
    for kind, _src, rel in pl.get("later") or []:
        lines.append("  [later]  %-30s not there yet, copied once it exists" % (rel + ("/" if kind == "dir" else "")))
    lines.append("never copied: %s" % " · ".join(NEVER))
    lines.append("rotation: keep the newest %d dated folders; would delete: %s" % (
        pl["keep"], ", ".join(p.name for p in pl["to_delete"]) or "none"))
    lines.append("required process: %s" % ((proc + " (checked only by a real run)") if proc else "none"))
    if not Path(pl["target"]).parent.parent.is_dir():
        lines.append("warning: the destination's parent folder is missing, a real run would fail")
    return "\n".join(lines)


# ---------------------------------------------------------------- main entry

def run_backup(cfg, dest=None, dry_run=False, now=None, require_process=None, process_check=None, out=print):
    """Back up now. Returns the backup-last.json record; a dry run returns the same shape and writes
    nothing. require_process overrides backup.require_process ("" skips the check); process_check(name) -> bool and
    out(line) are injectable (selftest, daemon log)."""
    now = now or dt.datetime.now()
    b = cfg.get("backup") or {}
    live = W.live_dir(cfg)
    last_path = live / "backup-last.json"

    def finish(ok, target, nbytes, nfiles, error):
        rec = {"at": now.isoformat(timespec="seconds"), "ok": bool(ok), "dest": str(target), "bytes": int(nbytes),
               "files": int(nfiles), "error": (str(error)[:300] if error else None), "dry_run": bool(dry_run)}
        if not dry_run:
            W.write_json_atomic(last_path, rec)
        return rec

    dest = str(dest or b.get("dest") or "").strip()
    if not dest:
        out("backup.dest is not set; nothing %s" % ("planned" if dry_run else "backed up"))
        return finish(False, "", 0, 0, "backup.dest is not set")
    dest = os.path.expanduser(dest)
    pl = plan(cfg, dest, now)
    proc = str(b.get("require_process") or "") if require_process is None else str(require_process or "")
    if dry_run:
        out(describe(pl, proc))
        out("(dry run: nothing written, backup-last.json not written)")
        return finish(True, pl["target"], 0, 0, None)
    target = pl["target"]
    if proc and not (process_check or process_running)(proc):
        rec = finish(False, target, 0, 0, "required process not running: %s" % proc)
        out("not backed up: %s (wrote %s)" % (rec["error"], last_path))
        return rec
    if not Path(dest).parent.is_dir():
        rec = finish(False, target, 0, 0, "destination not reachable, its parent folder is missing: %s" % Path(dest).parent)
        out("not backed up: %s" % rec["error"])
        return rec
    try:
        Path(dest).mkdir(exist_ok=True)
        target.mkdir(exist_ok=True)
        os.chmod(str(target), 0o700)
        written = []
        for kind, src, rel in pl["items"]:
            dst = target / rel
            if kind == "sqlite":
                backup_sqlite(src, dst)
                written.append((dst, None))
            elif kind == "config":
                raw = json.loads(src.read_text(encoding="utf-8"))
                dst.write_text(json.dumps(redact(raw), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
                written.append((dst, None))
            elif kind == "file":
                shutil.copyfile(str(src), str(dst))
                written.append((dst, src.stat().st_size))
            else:
                shutil.copytree(str(src), str(dst), dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.log", ".DS_Store"))
                for root, dirs, files in os.walk(str(src)):
                    rel_root = Path(root).relative_to(src)
                    for f in files:
                        if f.endswith(".log") or f == ".DS_Store":
                            continue
                        s = Path(root) / f
                        written.append((dst / rel_root / f, s.stat().st_size))
        nbytes = 0
        for p, expect in written:
            size = p.stat().st_size
            if expect is not None and size != expect:
                raise RuntimeError("read back: %s is %d bytes, the source had %d" % (p, size, expect))
            if expect is None and size <= 0:
                raise RuntimeError("read back: %s is empty" % p)
            os.chmod(str(p), 0o600)
            nbytes += size
        for root, dirs, _files in os.walk(str(target)):
            for d in dirs:
                os.chmod(os.path.join(root, d), 0o700)
        deleted = []
        for old in pl["to_delete"]:
            if DATE_DIR.match(old.name) and old.name != pl["day"] and old.parent == Path(dest):
                shutil.rmtree(str(old))
                deleted.append(old.name)
        rec = finish(True, target, nbytes, len(written), None)
        out("backed up %s: %d files, %.1f KB; deleted old folders: %s" % (target, len(written), nbytes / 1024.0,
                                                                         ", ".join(deleted) or "none"))
        return rec
    except Exception as e:
        rec = finish(False, target, 0, 0, str(e))
        out("backup failed: %s (wrote %s)" % (rec["error"], last_path))
        return rec


# ---------------------------------------------------------------- selftest

def selftest():
    check = W._Checks("wa_backup")
    tmp = Path(tempfile.mkdtemp(prefix="wa_backup_selftest."))
    quiet = []
    try:
        live = tmp / "home #1?" / "live"
        live.mkdir(parents=True)
        c = sqlite3.connect(str(live / "state.sqlite"))
        c.execute("CREATE TABLE conversations(jid TEXT, name TEXT)")
        c.executemany("INSERT INTO conversations VALUES (?, ?)", [("60120000001@s.whatsapp.net", "Alex Rivera"), ("100000000000002@lid", "Blake Chen")])
        c.commit()
        c.close()
        (live / "config.json").write_text(json.dumps({
            "bot_token": "abc", "telegram": {"api_key": "x"}, "my_phone": "60120000000", "brain": {"model": "m"},
            "list": [{"Secret": "zzz", "keep": 1}], "vocab": {"worlds": [{"key": "work", "label": "Work"}]},
            "notify": {"outlet": "command", "command": "curl -s -d @- https://push.example.com/bot1234567:AAabcdefghijklmnopqrstuvwxyz0123456789/send"},
            "facts": {"csv": {"path": "https://reader:hunter2@files.example.com/x.csv"}}}), encoding="utf-8")
        (live / "summaries.md").write_text("# summaries\nhello\n", encoding="utf-8")
        (live / "voice-pairs.jsonl").write_text("", encoding="utf-8")
        (live / "second-source-whitelist.json").write_text('{"chats": []}', encoding="utf-8")
        (live / "topic-map.json").write_text("{}", encoding="utf-8")
        for d, f, body in (("digest", "2026-09-13.md", "quiet day\n"), ("prompts", "brain-prompt.md", "prompt\n"),
                           ("dossier-backups", "Alex-Rivera/2026-09-12.md", "old note\n"), ("digest", "noise.log", "log\n")):
            (live / d / f).parent.mkdir(parents=True, exist_ok=True)
            (live / d / f).write_text(body, encoding="utf-8")
        (live / "telegram.json").write_text('{"bot_token": "SECRET"}', encoding="utf-8")
        for d in ("runs", "commands", "calendar"):
            (live / d).mkdir()
            (live / d / "x.json").write_text("{}", encoding="utf-8")
        (live / "wa_live.log").write_text("log\n", encoding="utf-8")
        cfg = W.apply_defaults({"backup": {"enabled": True, "keep": 7}}, live / "config.json")
        dest = tmp / "cloud" / "backups"
        for i in range(9):
            d = dest / ("2026-08-%02d" % (i + 1))
            d.mkdir(parents=True)
            (d / "x").write_text("x", encoding="utf-8")
        (dest / "notes-keep-me").mkdir()
        now = dt.datetime(2026, 9, 14, 3, 30, 0)

        rec = run_backup(cfg, dest=str(dest), dry_run=True, now=now, out=quiet.append)
        check(rec["dry_run"] is True and rec["ok"] and not (live / "backup-last.json").exists() and not (dest / "2026-09-14").exists()
              and len(date_dirs(dest)) == 9, "dry run writes nothing, not even backup-last.json, deletes nothing")
        check(any("would delete: 2026-08-01, 2026-08-02, 2026-08-03" in x for x in quiet)
              and any("[file]   voice-pairs.jsonl" in x for x in quiet), "dry run plan lists voice-pairs.jsonl and the rotation")
        fresh = tmp / "fresh" / "live"
        fresh.mkdir(parents=True)
        (fresh / "config.json").write_text("{}", encoding="utf-8")
        fresh_txt = describe(plan(W.apply_defaults({}, fresh / "config.json"), tmp / "fresh-dest", now))
        check("[later]  voice-pairs.jsonl" in fresh_txt and "second-source-whitelist" not in fresh_txt and "adapters" not in fresh_txt
              and "people-write.lock" in fresh_txt,
              "fresh install plan: voice-pairs.jsonl shows as a [later] line, no whitelist or adapters line, people-write.lock never copied")

        rec = run_backup(cfg, dest=str(dest), now=now, require_process="", out=quiet.append)
        t = dest / "2026-09-14"
        check(rec["ok"] is True and rec["error"] is None, "real run ok (path with # and ? in the live folder)")
        names = sorted(str(p.relative_to(t)) for p in t.rglob("*") if p.is_file())
        check(names == ["config.json", "digest/2026-09-13.md", "dossier-backups/Alex-Rivera/2026-09-12.md", "prompts/brain-prompt.md",
                        "second-source-whitelist.json", "state.sqlite", "summaries.md", "topic-map.json", "voice-pairs.jsonl"],
              "copied exactly the listed items (empty voice-pairs.jsonl included, logs and never-list excluded)")
        cj = json.loads((t / "config.json").read_text(encoding="utf-8"))
        text = (t / "config.json").read_text(encoding="utf-8")
        check(cj["bot_token"] == REDACTED and cj["telegram"]["api_key"] == REDACTED and cj["list"][0]["Secret"] == REDACTED
              and "abc" not in text and "zzz" not in text, "secret keys redacted")
        check(cj["my_phone"] == "60120000000" and cj["list"][0]["keep"] == 1 and cj["vocab"]["worlds"][0]["key"] == "work",
              "ordinary keys kept, including vocab key and keep")
        check("AAabcdefghijklmnopqrstuvwxyz0123456789" not in text and "hunter2" not in text
              and "https://" + REDACTED + "@files.example.com" in text, "token shapes inside strings redacted")
        c = sqlite3.connect(str(t / "state.sqlite"))
        check(c.execute("PRAGMA integrity_check").fetchone()[0] == "ok" and c.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 2,
              "database copy passes integrity_check with both rows")
        c.close()
        modes = set(oct(p.stat().st_mode & 0o777) for p in t.rglob("*") if p.is_file())
        check(modes == {"0o600"} and oct(t.stat().st_mode & 0o777) == "0o700", "copied files 0600, dated folder 0700")
        dd = [p.name for p in date_dirs(dest)]
        check(len(dd) == 7 and dd[-1] == "2026-09-14" and "2026-08-03" not in dd and "2026-08-04" in dd and (dest / "notes-keep-me").is_dir(),
              "rotation keeps 7 dated folders, other folders untouched")
        last = json.loads((live / "backup-last.json").read_text(encoding="utf-8"))
        check(sorted(last) == ["at", "bytes", "dest", "dry_run", "error", "files", "ok"] and last["ok"] and last["files"] == 9
              and last["bytes"] > 0 and last["dest"] == str(t), "backup-last.json has the seven keys")
        rec2 = run_backup(cfg, dest=str(dest), now=now, require_process="", out=quiet.append)
        check(rec2["ok"] and len(date_dirs(dest)) == 7, "same day again overwrites the same folder")

        rec3 = run_backup(cfg, dest=str(dest), now=now, require_process="Sync Client", process_check=lambda n: False, out=quiet.append)
        last = json.loads((live / "backup-last.json").read_text(encoding="utf-8"))
        check(rec3["ok"] is False and rec3["error"] == "required process not running: Sync Client" and last["ok"] is False,
              "a required process that is not running fails and is recorded")
        cfg_proc = W.apply_defaults({"backup": {"dest": str(dest), "require_process": "Sync Client"}}, live / "config.json")
        seen = []
        rec4 = run_backup(cfg_proc, now=now, process_check=lambda n: seen.append(n) or True, out=quiet.append)
        check(rec4["ok"] and seen == ["Sync Client"], "backup.require_process from the config is checked")
        gone = tmp / "unmounted" / "disk" / "backups"
        rec5 = run_backup(cfg, dest=str(gone), now=now, require_process="", out=quiet.append)
        check(rec5["ok"] is False and "parent folder is missing" in rec5["error"] and not (tmp / "unmounted").exists(),
              "an unreachable destination fails without creating folders")
        rec6 = run_backup(W.apply_defaults({}, live / "config.json"), now=now, out=quiet.append)
        check(rec6["ok"] is False and rec6["error"] == "backup.dest is not set", "no destination configured is an error")
        check(process_running("") is True and _ere_escape("a.b (c)") == "a\\.b \\(c\\)", "process check helpers")
        r = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--config", str(live / "config.json"), "--dry-run",
                            "--dest", str(dest)], capture_output=True, timeout=60)
        check(r.returncode == 0 and b"never copied: telegram.json" in r.stdout, "CLI --dry-run prints the plan")
        src = Path(__file__).read_text(encoding="utf-8")
        check(chr(0x2014) not in src and chr(0x2013) not in src, "no em dash or en dash in this file")
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)
    return check.done()


def main(argv=None):
    ap = argparse.ArgumentParser(description="WhatsApp Live nightly backup")
    ap.add_argument("--config", default=None, help="live/config.json (default: env WA_LIVE_CONFIG or ~/WhatsApp-Live/live/config.json)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    g.add_argument("--run", action="store_true", help="back up now")
    g.add_argument("--selftest", action="store_true", help="fake live folder in a temp dir")
    ap.add_argument("--dest", default=None, help="override backup.dest")
    ap.add_argument("--no-process-check", "--no-drive-check", dest="no_process_check", action="store_true",
                    help="skip the backup.require_process check")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    try:
        cfg = W.load_config(a.config)
    except (FileNotFoundError, ValueError) as e:
        print(e)
        return 2
    rec = run_backup(cfg, dest=a.dest, dry_run=a.dry_run, require_process=("" if a.no_process_check else None))
    print(json.dumps(rec, ensure_ascii=False))
    return 0 if rec["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
