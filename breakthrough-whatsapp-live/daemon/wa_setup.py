#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wa_setup.py · install and maintenance helper for breakthrough-whatsapp-live (the wizard stations run it)

It renders files, checks them and reports. It never judges a message and never sends anything.

  init --home <dir>     create the working tree (folders 0700), write live/config.json from the example with the
                        tools found on this Mac, copy the prompt and topic map seeds into live/, render
                        live/claude-settings.json, record every copied seed in live/seeds.json.
                        Never overwrites an existing file: it reports it and moves on.
  doctor                read only prerequisite report: macOS, WhatsApp Desktop database present, fresh and readable,
                        node, python, claude, tailscale, ffmpeg, whisperkit and its model, lark-cli when chosen.
                        Counts and ages only, never message text or names. Always exits 0 with one verdict line.
  check-config          unknown keys, bad values, vocabulary keys that do not exist, enabled stations without paths.
  render-launchd        write <home>/launchd/run.mjs and <home>/launchd/<label>.plist from the templates
                        (--intake: the evening intake job instead).
  install-launchd       copy the rendered plist into ~/Library/LaunchAgents, enable it and bootstrap it (a loaded daemon
                        is booted out first, then any python of it left running is stopped, as restart does).
  uninstall-launchd     bootout, stop any python left running as restart does, disable, and remove the plist.
  restart               restart the daemon so it loads new code or settings: SIGTERM the python child, SIGKILL it
                        when it has not exited after ExitTimeOut (a frozen one never does), let launchd start a fresh
                        one, then prove exactly one python of this install runs and holds board.port.
                        Never `launchctl kickstart -k`: that stops only the node parent and can orphan a frozen python.
  sync-reader-config    write <home>/config.json for the reader from live/config.json, refresh runs-work copies.
  seed-status           each owner copy against live/seeds.json and the payload seed.
  copy-seed             copy one payload seed (file or folder) to its owner location, never overwriting, and record it.
  measure --days 3      inbound traffic counted on a snapshot copy of the WhatsApp database: one to one, groups,
                        top groups by inbound. Group names and counts only. Used to build the group whitelist.
  tailscale-hint        the tailscale serve command for the board port and the address it gives. Runs nothing.
  --selftest            all of the above against a fake home, fake payload and fake WhatsApp database in a temp
                        folder. Never calls launchctl, tailscale or claude, never reads the real database.

Every subcommand takes --config (default: env WA_LIVE_CONFIG, else ~/WhatsApp-Live/live/config.json). Every
subcommand that writes takes --dry-run: it prints the plan and writes nothing.

Python 3.9 standard library only (macOS /usr/bin/python3).
"""

import argparse
import copy
import inspect
import datetime as dt
import hashlib
import json
import os
import platform
import plistlib
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAYLOAD = HERE.parent
TEMPLATES_REL = "daemon/templates"
HOME_DEFAULT = "~/WhatsApp-Live"
PROMPT_SEEDS =("brain-prompt.md", "second-source-prompt.md", "backfill-prompt.md")
STAMP_RE = re.compile(r"seed:\s*breakthrough-whatsapp-live\s+(\S+?)\s+v(\d+(?:\.\d+){0,3})")
PLACEHOLDER_RE = re.compile(r"\{[A-Z][A-Z0-9_]*\}")
TREE_DIRS = ("runs-work", "prewarm", "transcripts-cache", "brain-cwd", "launchd", "live", "live/prompts",
             "live/digest", "live/runs", "live/commands", "live/dossier-backups", "live/variants")
STANDARD_PATH = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
LOOPBACK = ("127.0.0.1", "localhost", "::1")
CORE_EPOCH_UTC = dt.datetime(2001, 1, 1, tzinfo=dt.timezone.utc)
FALLBACK_CONTENT_TYPES = {0: "text", 1: "image", 2: "video", 3: "voice", 5: "location", 7: "text", 8: "file",
                          11: "GIF", 14: "contact card", 15: "sticker", 20: "image", 33: "image", 41: "text",
                          42: "channel post"}
# SHA-256 of launchd label prefixes (ending in a dot) reserved for an install this tool must never touch.
# install-launchd, uninstall-launchd and restart refuse every label that starts with one of them, so a test install
# on the build machine can never stop or replace another daemon there. Only hashes are kept, so no account name ships in the kit.
RESERVED_LABEL_PREFIX_SHA256 = ["5f452dbef74d9f306ae8219d7c7e2a5e00680b3bf71bfc417cc2fdfe44476821"]
GUARDED_TOOLS = ("launchctl", "tailscale", "claude", "lark-cli")

sys.path.insert(0, str(HERE))
try:
    import wa_common as WC  # the one place that fills config defaults (DESIGN §4, §12)
    WC_ERROR = ""
except Exception as _e:  # doctor still has to run on a broken payload
    WC = None
    WC_ERROR = "%s: %s" % (type(_e).__name__, _e)

_SELFTEST = {"active": False}


class SetupError(Exception):
    pass


class Refused(Exception):
    pass


# ---------------------------------------------------------------- small helpers

def say(line=""):
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


def tilde(path):
    """A path for printing, with the account folder shown as ~."""
    try:
        text = str(path)
        home = str(Path.home())
        if text == home or text.startswith(home + os.sep):
            return "~" + text[len(home):]
        return text
    except Exception:
        return str(path)


def expand(path):
    return Path(os.path.expanduser(str(path)))


def dget(d, dotted, default=None):
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def reserved_label(label):
    """True when any dot ended prefix of label hashes to a reserved prefix."""
    label = str(label or "")
    reserved = set(RESERVED_LABEL_PREFIX_SHA256)
    for i, ch in enumerate(label):
        if ch == "." and hashlib.sha256(label[:i + 1].encode("utf-8")).hexdigest() in reserved:
            return True
    return False


def default_runner(argv, timeout=30):
    """Run a command, return (exit code, stdout, stderr). Inside the selftest the guarded tools never run."""
    name = os.path.basename(str(argv[0]))
    if _SELFTEST["active"] and name in GUARDED_TOOLS:
        raise Refused("%s is never run inside the selftest" % name)
    try:
        r = subprocess.run([str(a) for a in argv], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout)
        return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")
    except FileNotFoundError:
        return 127, "", "not found: %s" % argv[0]
    except subprocess.TimeoutExpired:
        return 124, "", "timed out after %ss" % timeout


def write_text_atomic(path, text, mode=0o600):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
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


def json_text(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def read_json_file(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(str(path), "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def read_stamp(path):
    """(seed relative path, version) from the seed stamp near the top of a file, or (None, None)."""
    try:
        with open(str(path), "rb") as f:
            head = f.read(8192).decode("utf-8", "replace")
    except OSError:
        return None, None
    m = STAMP_RE.search(head)
    return (m.group(1), m.group(2)) if m else (None, None)


def vtuple(version):
    nums = tuple(int(x) for x in re.findall(r"\d+", str(version or "")))
    return nums or (0,)


def mode_of(path):
    try:
        return stat.S_IMODE(os.stat(str(path)).st_mode)
    except OSError:
        return None


def protected_roots(user_home=None):
    h = Path(user_home) if user_home else Path.home()
    return [h / "Documents", h / "Desktop", h / "Downloads", h / "Library" / "Mobile Documents",
            h / "Library" / "CloudStorage"]


def under_protected(path, user_home=None):
    """The macOS privacy protected folder (Documents, Desktop, Downloads, iCloud Drive, cloud storage) that holds
    path, or None. Children of launchd jobs stall on privacy prompts inside these folders."""
    if not path:
        return None
    try:
        p = str(expand(path).resolve())
    except Exception:
        return None
    for root in protected_roots(user_home):
        try:
            r = str(root.resolve())
        except Exception:
            continue
        if p == r or p.startswith(r + os.sep):
            return root
    return None


def is_exec(p):
    return bool(p) and os.path.isfile(str(p)) and os.access(str(p), os.X_OK)


def default_config_path():
    if WC is not None:
        return Path(WC.DEFAULT_CONFIG)
    return expand(os.environ.get("WA_LIVE_CONFIG") or "~/WhatsApp-Live/live/config.json")


def need_wc():
    if WC is None:
        raise SetupError("daemon/wa_common.py could not be imported (%s). Reinstall or update the kit." % WC_ERROR)


def load_cfg(path):
    """The defaults applied config, through wa_common.load_config (the single place defaults are filled)."""
    need_wc()
    try:
        return WC.load_config(str(path))
    except FileNotFoundError:
        raise SetupError("config not found: %s (create it with: wa_setup.py init)" % tilde(path))
    except ValueError as e:
        raise SetupError(str(e))


def resolve_bin(cfg, name):
    if WC is not None:
        try:
            p = WC.resolve_bin(cfg, name)
            return p if is_exec(p) else ""
        except Exception:
            return ""
    val = dget(cfg, "bin." + name) or ""
    if val and not str(val).startswith("<") and is_exec(expand(val)):
        return str(expand(val))
    return shutil.which({"whisperkit": "whisperkit-cli", "lark_cli": "lark-cli"}.get(name, name)) or ""


def wa_root_default():
    if WC is not None:
        try:
            return Path(WC.wa_root())
        except Exception:
            pass
    return expand(os.environ.get("WA_LIVE_WA_ROOT") or "~/Library/Group Containers/group.net.whatsapp.WhatsApp.shared")


def content_types():
    table = getattr(WC, "CONTENT_TYPES_EN", None) if WC is not None else None
    if not isinstance(table, dict):
        table = getattr(WC, "CONTENT_TYPES", None) if WC is not None else None
    return table if isinstance(table, dict) and table else dict(FALLBACK_CONTENT_TYPES)


class Report(object):
    """Collects what a command did (or would do under --dry-run); prints as it goes unless quiet."""

    def __init__(self, dry_run=False, quiet=False):
        self.dry_run = dry_run
        self.quiet = quiet
        self.lines = []
        self.done = []
        self.kept = []
        self.warnings = []
        self.errors = []
        self.data = {}

    def line(self, text=""):
        self.lines.append(text)
        if not self.quiet:
            say(text)

    def did(self, verb, path, note=""):
        self.done.append((verb, str(path)))
        self.line("  %s %s%s" % (("would " + verb) if self.dry_run else verb, tilde(path),
                                  ("  (%s)" % note) if note else ""))

    def keep(self, path, note="already there, left as it is"):
        self.kept.append(str(path))
        self.line("  kept %s  (%s)" % (tilde(path), note))

    def warn(self, text):
        self.warnings.append(text)
        self.line("  WARNING %s" % text)

    def error(self, text):
        self.errors.append(text)
        self.line("  ERROR %s" % text)

    def text(self):
        return "\n".join(self.lines)


# ---------------------------------------------------------------- seeds bookkeeping (DESIGN §6)

def seeds_file(live):
    return Path(live) / "seeds.json"


def load_seeds(live):
    data = read_json_file(seeds_file(live), None)
    if not isinstance(data, dict):
        data = {}
    data.setdefault("version", 1)
    if not isinstance(data.get("seeds"), dict):
        data["seeds"] = {}
    if not isinstance(data.get("rendered"), dict):
        data["rendered"] = {}
    return data


def record_key(live, dest):
    """Owner copies inside live/ are recorded relative to it (they survive a moved home); others absolutely."""
    try:
        return Path(dest).resolve().relative_to(Path(live).resolve()).as_posix()
    except ValueError:
        return str(Path(dest).resolve())


def record_path(live, key):
    return Path(key) if os.path.isabs(key) else Path(live) / key


def save_seed_records(live, new_seeds, new_rendered, rep):
    """Add records to live/seeds.json without touching the records already there. Nothing new, nothing written."""
    if not new_seeds and not new_rendered:
        return
    data = load_seeds(live)
    data["seeds"].update(new_seeds)
    data["rendered"].update(new_rendered)
    data["payload"] = str(PAYLOAD) if not rep.data.get("payload") else rep.data["payload"]
    target = seeds_file(live)
    if not rep.dry_run:
        write_text_atomic(target, json_text(data), 0o600)
    rep.did("record", target, "%d seed record(s) added" % (len(new_seeds) + len(new_rendered)))


def confirm_frontmatter(data):
    """Owner said yes at install: in the frontmatter only, confirmed_by_owner false becomes true and updated becomes
    today, so the recorded hash is the hash of what landed (seed-status then reports "unchanged", not "edited")."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    if not text.startswith("---\n"):
        return data
    end = text.find("\n---", 4)
    if end < 0:
        return data
    head, rest = text[:end], text[end:]
    head = re.sub(r"(?m)^confirmed_by_owner:\s*false\s*$", "confirmed_by_owner: true", head)
    head = re.sub(r"(?m)^updated:\s*\S+\s*$", "updated: %s" % dt.date.today().isoformat(), head)
    return (head + rest).encode("utf-8")


def copy_seed_file(seed, seed_rel, dest, live, rep, records, mode=0o600, confirmed=False, dir_mode=0o700):
    """Copy one seed keeping its stamp (byte for byte unless confirmed: then confirm_frontmatter). Never overwrites.
    Folders it has to create get dir_mode: 0700 inside the working folder, 0755 in the vault (like every other vault
    folder, so a sync or backup tool treats them the same)."""
    dest = Path(dest)
    if dest.exists():
        rep.keep(dest)
        return False
    if not Path(seed).is_file():
        rep.warn("seed %s is not in the payload yet, %s not created" % (seed_rel, tilde(dest)))
        return False
    stamp_rel, version = read_stamp(seed)
    if not version:
        rep.warn("seed %s carries no seed stamp; seed-status cannot tell its version" % seed_rel)
    data = Path(seed).read_bytes()
    if confirmed and Path(seed).suffix == ".md":
        data = confirm_frontmatter(data)
    if not rep.dry_run:
        missing, p = [], dest.parent
        while not p.exists():
            missing.append(p)
            p = p.parent
        dest.parent.mkdir(mode=dir_mode, parents=True, exist_ok=True)
        for d in missing:
            os.chmod(str(d), dir_mode)
        fd, tmp = tempfile.mkstemp(prefix="." + dest.name + ".", dir=str(dest.parent))
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, mode)
        if dest.exists():  # appeared meanwhile: still never overwrite
            os.unlink(tmp)
            rep.keep(dest)
            return False
        os.replace(tmp, str(dest))
    records[record_key(live, dest)] = {"seed": seed_rel, "seed_version": version or "", "sha256": sha256_bytes(data),
                                       "copied_at": now_iso()}
    rep.did("copy", dest, "seed %s%s" % (seed_rel, (" v" + version) if version else ""))
    return True


def render_placeholders(text, values, json_escape=False, xml_escape=False):
    """Replace {NAME} placeholders whose NAME is in values. Returns (text, leftover placeholder names).
    json_escape: the value sits inside a JSON string; xml_escape: inside plist XML."""
    def repl(m):
        name = m.group(0)[1:-1]
        if name not in values:
            return m.group(0)
        v = str(values[name])
        if json_escape:
            v = json.dumps(v)[1:-1]
        if xml_escape:
            v = v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return v
    out = PLACEHOLDER_RE.sub(repl, text)
    leftover = sorted(set(m.group(0) for m in PLACEHOLDER_RE.finditer(out)))
    return out, leftover


# ---------------------------------------------------------------- init

BIN_EXES = (("claude", "claude"), ("node", "node"), ("ffmpeg", "ffmpeg"), ("whisperkit", "whisperkit-cli"),
            ("lark_cli", "lark-cli"))


def detect_bins(which=None):
    """{name: absolute path or ""} for the tools the daemon calls."""
    found = {}
    for name, exe in BIN_EXES:
        if which is not None:
            p = which(exe)
        elif WC is not None:
            try:
                p = WC.resolve_bin({"bin": {name: "<auto>"}}, name)
            except Exception:
                p = None
        else:
            p = shutil.which(exe)
        found[name] = str(p) if (p and os.path.isabs(str(p)) and is_exec(p)) else ""
    return found


def detect_whisper_model(models_dir):
    """The one model folder under models_dir, or the newest large-v3 one when there are several, else ""."""
    d = expand(models_dir)
    try:
        subs = sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return ""
    if len(subs) == 1:
        return str(subs[0])
    large = [p for p in subs if "large-v3" in p.name]
    return str(large[-1]) if large else ""


def tilde_str(path):
    t = tilde(path)
    return t if t.startswith("~") else str(path)


def do_init(home, rep, config_path=None, payload=None, which=None, models_dir="~/.cache/whisper-models",
            user_home=None):
    payload = Path(payload) if payload else PAYLOAD
    rep.data["payload"] = str(payload)
    home = expand(home).absolute()
    live = home / "live"
    templates = payload / TEMPLATES_REL
    rep.line("init: home %s%s" % (tilde(home), "  (dry run, nothing is written)" if rep.dry_run else ""))
    if config_path and expand(config_path).absolute() != live / "config.json":
        rep.error("--config %s does not match --home (init writes %s). Pass only --home, or a --config "
                  "that ends in <home>/live/config.json." % (tilde(config_path), tilde(live / "config.json")))
        return rep
    prot = under_protected(home, user_home)
    if prot:
        rep.error("%s is inside %s. Processes started by launchd stall on privacy prompts there. "
                  "Choose a home such as ~/WhatsApp-Live." % (tilde(home), tilde(prot)))
        return rep

    for rel in ("",) + TREE_DIRS:
        d = home / rel if rel else home
        if d.is_dir():
            m = mode_of(d)
            if m != 0o700:
                rep.warn("%s is mode %o, expected 700 (chmod 700 %s)" % (tilde(d), m or 0, tilde(d)))
            rep.kept.append(str(d))
            continue
        if d.exists():
            rep.error("%s exists and is not a folder" % tilde(d))
            return rep
        if not rep.dry_run:
            d.mkdir(mode=0o700, parents=(rel == ""), exist_ok=False)
            os.chmod(str(d), 0o700)
        rep.did("create", d, "folder 0700")

    cfg_file = live / "config.json"
    if cfg_file.exists():
        rep.keep(cfg_file)
    else:
        example_path = templates / "config.example.json"
        try:
            example = json.loads(example_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            rep.error("cannot read %s: %s" % (tilde(example_path), e))
            return rep
        example.pop("_help", None)
        example.setdefault("paths", {})["home"] = tilde_str(home)
        bins = detect_bins(which)
        for name, p in bins.items():
            if p:
                example.setdefault("bin", {})[name] = p
        missing = [exe for name, exe in BIN_EXES if not bins[name] and name != "lark_cli"]
        model = detect_whisper_model(models_dir)
        if model:
            example.setdefault("brain", {})["whisper_model"] = tilde_str(model)
        if not rep.dry_run:
            write_text_atomic(cfg_file, json_text(example), 0o600)
        found = [exe for name, exe in BIN_EXES if bins[name]]
        rep.did("write", cfg_file, "0600; tools found: %s" % (", ".join(found) or "none"))
        if missing:
            rep.warn("not found on this Mac: %s. Their bin entries stay <auto> and are looked up again at every "
                     "start; wa_setup.py doctor shows how to install them." % ", ".join(missing))
        if not model:
            rep.warn("no whisper model folder found under %s; brain.whisper_model keeps the recommended path "
                     "(station C1 downloads the model)" % tilde(expand(models_dir)))

    new_seeds, new_rendered = {}, {}
    for name in PROMPT_SEEDS:
        copy_seed_file(templates / name, TEMPLATES_REL + "/" + name, live / "prompts" / name, live, rep, new_seeds)
    copy_seed_file(templates / "topic-map.example.json", TEMPLATES_REL + "/topic-map.example.json",
                   live / "topic-map.json", live, rep, new_seeds)

    settings_tmpl = templates / "claude-settings.json"
    settings = live / "claude-settings.json"
    if settings.exists():
        rep.keep(settings)
    elif not settings_tmpl.is_file():
        rep.warn("template %s/claude-settings.json is not in the payload yet, %s not created"
                 % (TEMPLATES_REL, tilde(settings)))
    else:
        raw = settings_tmpl.read_text(encoding="utf-8")
        text, leftover = render_placeholders(raw, {"LIVE_DIR": str(live), "HOME": str(user_home or Path.home()),
                                                   "HOME_DIR": str(user_home or Path.home()), "WA_HOME": str(home),
                                                   "PAYLOAD": str(payload)}, json_escape=True)
        try:
            json.loads(text)
        except ValueError as e:
            rep.error("claude-settings.json does not parse after rendering: %s" % e)
            text = None
        if text is not None and leftover:
            rep.error("claude-settings.json template has unknown placeholders: %s" % ", ".join(leftover))
            text = None
        if text is not None:
            if not rep.dry_run:
                write_text_atomic(settings, text, 0o600)
            new_rendered["claude-settings.json"] = {"template": TEMPLATES_REL + "/claude-settings.json",
                                                    "template_sha256": sha256_bytes(raw.encode("utf-8")),
                                                    "sha256": sha256_bytes(text.encode("utf-8")),
                                                    "rendered_at": now_iso()}
            rep.did("render", settings, "deny rules point at %s" % tilde(live))

    save_seed_records(live, new_seeds, new_rendered, rep)
    rep.line("init: %d created, %d already there, %d warning(s), %d error(s)"
             % (len(rep.done), len(rep.kept), len(rep.warnings), len(rep.errors)))
    if not rep.errors and not rep.dry_run:
        rep.line("next: wa_setup.py doctor --config %s" % tilde(cfg_file))
    return rep


# ---------------------------------------------------------------- check-config

ENUMS = {
    "direct_policy": ("all", "skip"),
    "unlisted_group_policy": ("all", "mention", "skip"),
    "notify.outlet": ("none", "telegram", "command"),
    "notify.mode": ("off", "errors", "needs_me", "all"),
    "board.lang": ("zh", "en"),
    "facts.source": ("none", "csv", "lark"),
    "second_source.source": ("none", "lark"),
    "calendar.destination": ("none", "ics", "lark"),
}
GROUP_POLICIES = ("all", "mention", "skip")
FREE_MAPS = ("group_policies", "facts.csv.columns", "facts.floor", "second_source.world_by_chat", "second_source.world_keywords")   # keys chosen by the owner, not by the kit
LIST_OR_STRING = ("facts.csv.path",)                                 # one source or a list of them
TIME_KEYS = ("dossier.nightly_at", "facts.at", "second_source.at", "backup.at", "digest.at", "intake.evening_at")
VOCAB_ENTRY_KEYS = {"worlds": ("key", "label", "label_en", "business", "criteria"), "stages": ("key", "label", "label_en", "criteria", "side"),
                    "priorities": ("key", "label", "label_en", "criteria")}


def example_config(payload=None):
    path = (Path(payload) if payload else PAYLOAD) / TEMPLATES_REL / "config.example.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("_help", None)
    return data


def merge_known(a, b):
    """Union of two key trees (values from a win)."""
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        if k not in out:
            out[k] = copy.deepcopy(v)
        elif isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = merge_known(out[k], v)
    return out


def known_tree(payload=None):
    tree = example_config(payload)
    defaults = getattr(WC, "DEFAULTS", None) if WC is not None else None
    if isinstance(defaults, dict):
        tree = merge_known(tree, defaults)
    return tree


def unknown_keys(raw, known, prefix=""):
    out = []
    for k, v in raw.items():
        dotted = prefix + k
        if k.startswith("_") or dotted in FREE_MAPS:
            continue
        if k not in known:
            out.append(dotted)
        elif isinstance(v, dict) and isinstance(known[k], dict):
            out.extend(unknown_keys(v, known[k], dotted + "."))
    return out


def type_issues(raw, example, prefix=""):
    """Values whose JSON type differs from the example (booleans are not numbers)."""
    out = []
    for k, ev in example.items():
        if k not in raw or raw[k] is None:
            continue
        v, dotted = raw[k], prefix + k
        if isinstance(ev, dict):
            if not isinstance(v, dict):
                out.append((dotted, "should be an object"))
            elif dotted not in FREE_MAPS:
                out.extend(type_issues(v, ev, dotted + "."))
        elif isinstance(ev, bool):
            if not isinstance(v, bool):
                out.append((dotted, "should be true or false"))
        elif isinstance(ev, (int, float)):
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                out.append((dotted, "should be a number"))
            elif v < 0:
                out.append((dotted, "should not be negative"))
        elif isinstance(ev, str):
            if not isinstance(v, str) and not (dotted in LIST_OR_STRING and isinstance(v, list)):
                out.append((dotted, "should be a string"))
        elif isinstance(ev, list):
            if not isinstance(v, list):
                out.append((dotted, "should be a list"))
    return out


def vocab_keys(section, errors):
    """Keys of a vocab list, reporting malformed entries into errors."""
    keys = []
    for i, e in enumerate(section[1] if isinstance(section[1], list) else []):
        where = "vocab.%s[%d]" % (section[0], i)
        if not isinstance(e, dict):
            errors.append(("error", where, "should be an object with key, label, label_en"))
            continue
        k = e.get("key")
        if not isinstance(k, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", k):
            errors.append(("error", where, "key should be lowercase letters, digits or underscore"))
            continue
        if k in keys:
            errors.append(("error", where, "duplicate key %s" % k))
        keys.append(k)
        for field in ("label", "label_en"):
            if not isinstance(e.get(field), str) or not e.get(field).strip():
                errors.append(("warning", where, "%s is empty (the board shows the key instead)" % field))
        extra = sorted(set(e) - set(VOCAB_ENTRY_KEYS[section[0]]))
        if extra:
            errors.append(("warning", where, "unknown field(s): %s" % ", ".join(extra)))
        if section[0] == "worlds" and not isinstance(e.get("business"), bool):
            errors.append(("error", where, "business should be true or false"))
    return keys


def path_of(cfg, dotted):
    """A resolved path value, or None when empty or still a placeholder."""
    v = dget(cfg, dotted)
    if not isinstance(v, str) or not v.strip() or "<" in v:
        return None
    return expand(v.strip())


def check_config(raw, cfg, payload=None, user_home=None):
    """[(level, dotted key, message)] for a raw config dict and its defaults applied twin (cfg may be None)."""
    issues = []
    add = lambda level, key, msg: issues.append((level, key, msg))
    try:
        example = example_config(payload)
    except (OSError, ValueError) as e:
        add("error", "config.example.json", "cannot read the payload example: %s" % e)
        return issues
    owner_blocks = []   # the config block of an owner's own adapter (facts.<name>, second_source.<name>, calendar.<name>)
    for sect, sel, prefix in (("facts", "source", "facts_"), ("second_source", "source", "second_source_"), ("calendar", "destination", "calendar_")):
        name = (raw.get(sect) or {}).get(sel) if isinstance(raw.get(sect), dict) else None
        live_here = dget(cfg if isinstance(cfg, dict) else raw, "paths.live_dir") or ""
        if isinstance(name, str) and name and live_here and (Path(expand(live_here)) / "adapters" / ("%s%s.py" % (prefix, name))).is_file():
            owner_blocks.append("%s.%s" % (sect, name))
    for key in unknown_keys(raw, known_tree(payload)):
        if any(key == b or key.startswith(b + ".") for b in owner_blocks):
            continue
        add("error", key, "unknown key (not in config.example.json); a typo is silently ignored by the daemon")
    for key, msg in type_issues(raw, example):
        add("error", key, msg)
    eff = cfg if isinstance(cfg, dict) else merge_known(raw, example)

    if eff.get("version") != 1:
        add("error", "version", "should be 1")
    owner_prefix = {"facts.source": "facts_", "second_source.source": "second_source_", "calendar.destination": "calendar_"}
    for key, allowed in ENUMS.items():
        v = dget(eff, key)
        if v in allowed:
            continue
        if key in owner_prefix and isinstance(v, str) and v and (Path(expand(dget(eff, "paths.live_dir") or "")) / "adapters" / ("%s%s.py" % (owner_prefix[key], v))).is_file():
            continue   # the owner's own adapter in <live>/adapters/ (survives kit updates)
        add("error", key, "is %r, allowed: %s (or your own <live>/adapters/%s%s.py)" % (v, " | ".join(allowed), owner_prefix.get(key, ""), v) if key in owner_prefix
            else "is %r, allowed: %s" % (v, " | ".join(allowed)))
    for key in TIME_KEYS:
        v = dget(eff, key)
        if not (isinstance(v, str) and re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v)):
            add("error", key, "should be a 24 hour time like 03:00, is %r" % (v,))

    phone = str(eff.get("my_phone") or "")
    if not phone:
        add("warning", "my_phone", "empty; groups set to mention cannot see @ mentions of you (station C1 asks)")
    elif not re.fullmatch(r"\d{7,15}", phone):
        add("error", "my_phone", "digits only with country code, no plus sign or spaces")
    names = eff.get("my_names") if isinstance(eff.get("my_names"), list) else []
    if not names:
        add("warning", "my_names", "empty; mention detection only sees your number")
    for n in names:
        if not isinstance(n, str):
            add("error", "my_names", "every entry should be a string")
        elif len(n.strip()) < 3:
            add("warning", "my_names", "%d letter entry matches inside other words" % len(n.strip()))
    marker = str((raw.get("owner_marker") if isinstance(raw.get("owner_marker"), str) else "") or "")
    if marker and not re.fullmatch(r"[A-Za-z]{2,6}", marker):
        add("error", "owner_marker", "should be 2 to 6 ASCII letters")
    if not str(eff.get("owner_label") or "").strip():
        add("warning", "owner_label", "empty; prompts and person notes will not know what to call you")

    gp = eff.get("group_policies") if isinstance(eff.get("group_policies"), dict) else {}
    for jid, ent in gp.items():
        pol = ent.get("policy") if isinstance(ent, dict) else ent
        if pol not in GROUP_POLICIES:
            add("error", "group_policies.%s" % jid, "policy %r, allowed: all | mention | skip (a typo acts as skip)" % (pol,))
        if not str(jid).endswith("@g.us"):
            add("warning", "group_policies.%s" % jid, "does not look like a group jid (ends in @g.us)")
        if isinstance(ent, dict):
            extra = sorted(set(ent) - {"policy", "name", "in30", "defaulted"})
            if extra:
                add("warning", "group_policies.%s" % jid, "unknown field(s): %s" % ", ".join(extra))

    vocab = eff.get("vocab") if isinstance(eff.get("vocab"), dict) else {}
    worlds = vocab_keys(("worlds", vocab.get("worlds")), issues)
    stages = vocab_keys(("stages", vocab.get("stages")), issues)
    vocab_keys(("priorities", vocab.get("priorities")), issues)
    if not worlds:
        add("error", "vocab.worlds", "needs at least one world")
    if not stages:
        add("error", "vocab.stages", "needs at least one stage")
    for s in (vocab.get("park_from") or []):
        if s not in stages:
            add("error", "vocab.park_from", "stage %r does not exist in vocab.stages" % (s,))
    business = [e.get("key") for e in (vocab.get("worlds") or []) if isinstance(e, dict) and e.get("business") is True]
    if business and not str(vocab.get("business_context") or "").strip():
        add("warning", "vocab.business_context", "empty; the brain does not know what your business sells (station C2)")
    for w in (dget(eff, "dossier.auto_open_worlds") or []):
        if w not in worlds:
            add("error", "dossier.auto_open_worlds", "world %r does not exist in vocab.worlds" % (w,))
    mst = dget(eff, "dossier.auto_open_min_stage")
    if mst not in stages:
        add("error", "dossier.auto_open_min_stage", "stage %r does not exist in vocab.stages" % (mst,))
    rules = eff.get("stage_rules") if isinstance(eff.get("stage_rules"), dict) else {}
    for k, v in rules.items():
        vals = v if isinstance(v, list) else [v]
        pool = stages if re.search(r"stages?$", k) else worlds if re.search(r"worlds?$", k) else None
        if pool is not None:
            for x in vals:
                if x not in pool:
                    add("error", "stage_rules.%s" % k, "%r does not exist in vocab" % (x,))
    raw_stages = [s for s in (dget(eff, "vocab.stages") or []) if isinstance(s, dict)]
    has_side = any(s.get("side") for s in raw_stages) if any("side" in s for s in raw_stages) else ("parked" in stages)
    if isinstance(rules.get("parked_after_days"), (int, float)) and rules.get("parked_after_days") > 0 and not has_side:
        add("error", "stage_rules.parked_after_days", "needs a side stage in vocab.stages (an entry with side true, or the key parked), or set 0")
    if rules.get("group_date_done") is True and "done" not in stages:
        add("error", "stage_rules.group_date_done", "needs a stage with key done in vocab.stages")
    floor = dget(eff, "facts.floor")
    if isinstance(floor, dict):
        for k, v in floor.items():
            if v and v not in stages:
                add("warning", "facts.floor.%s" % k, "stage %r does not exist in vocab.stages, so that case gets no floor" % (v,))

    port = dget(eff, "board.port")
    if not (isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535):
        add("error", "board.port", "should be a port number 1 to 65535")
    if dget(eff, "board.bind") not in LOOPBACK:
        add("error", "board.bind", "the board has no login; keep 127.0.0.1 and reach it through tailscale serve")
    labels = (dget(eff, "launchd.label"), dget(eff, "launchd.intake_label"))
    for key, lab in zip(("launchd.label", "launchd.intake_label"), labels):
        if not (isinstance(lab, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", lab)):
            add("error", key, "should be a reverse domain label like local.whatsapp-live")
    if labels[0] == labels[1]:
        add("error", "launchd.intake_label", "must differ from launchd.label")

    for key in ("paths.home", "brain.cwd"):
        p = path_of(eff, key)
        prot = under_protected(p, user_home) if p else None
        if prot:
            add("error", key, "is inside %s; processes started by launchd stall on privacy prompts there" % tilde(prot))

    def need_dir(key, level="error"):
        p = path_of(eff, key)
        if p is None:
            add(level, key, "is empty")
        elif not p.is_dir():
            add(level, key, "folder not found: %s" % tilde(p))
        return p

    def need_file(key, level="error", why=""):
        p = path_of(eff, key)
        if p is None:
            add(level, key, "is empty" + why)
        elif not p.is_file():
            add(level, key, "file not found: %s%s" % (tilde(p), why))
        return p

    def need_bin(name, why):
        if not resolve_bin(eff, name):
            add("error", "bin." + name, "not found (%s)" % why)

    need_bin("node", "the launchd parent")
    need_bin("claude", "every judgment run")
    if dget(eff, "brain.transcribe") is True:
        model = path_of(eff, "brain.whisper_model")
        if model is None or not model.is_dir():
            add("warning", "brain.whisper_model", "model folder not found; voice notes stay untranscribed")
        elif under_protected(model, user_home):
            add("error", "brain.whisper_model", "inside a privacy protected folder; copy it under ~/.cache")
        for name in ("ffmpeg", "whisperkit"):
            if not resolve_bin(eff, name):
                add("warning", "bin." + name, "not found; voice notes stay untranscribed")
    if dget(eff, "doorbell.enabled") is True:
        wal = path_of(eff, "doorbell.wal_path")
        if wal is None or not wal.parent.is_dir():
            add("warning", "doorbell.wal_path", "WhatsApp Desktop folder not found (is it installed and linked?)")

    if dget(eff, "dossier.enabled") is True:
        need_dir("paths.vault")
        need_dir("paths.people_dir")
        need_file("paths.person_playbook", why=" (person notes read it every run)")
        need_file("paths.doctrine", why=" (roles and required keys come from it)")
        if path_of(eff, "paths.filing_log") is None:
            add("warning", "paths.filing_log", "empty; person note rewrites leave no filing log line")
    if dget(eff, "intake.enabled") is True:
        buf = path_of(eff, "intake.buffer") or path_of(eff, "paths.intake_buffer")
        if buf is None:
            add("error", "intake.buffer", "is empty while intake.enabled is true")
        elif not buf.parent.is_dir():
            add("error", "intake.buffer", "folder not found: %s" % tilde(buf.parent))
        elif not buf.is_file():
            add("warning", "intake.buffer", "file not there yet: %s (created with your yes in station O7)" % tilde(buf))
    elif dget(eff, "intake.evening_push") is True:
        add("warning", "intake.evening_push", "true while intake.enabled is false; the evening job stays off")
    ib, pb = str(dget(raw, "intake.buffer") or ""), str(dget(raw, "paths.intake_buffer") or "")
    if ib and pb and ib != pb:
        add("warning", "paths.intake_buffer", "differs from intake.buffer; intake.buffer is the one used")

    if dget(eff, "facts.enabled") is True:
        src = dget(eff, "facts.source")
        if src == "none":
            add("error", "facts.source", "is none while facts.enabled is true")
        elif src == "csv":
            v = dget(eff, "facts.csv.path")
            sources = [str(x) for x in (v if isinstance(v, list) else [v]) if x]
            if not sources:
                add("error", "facts.csv.path", "is empty")
            for s in sources:
                if not re.match(r"https?://", s) and not expand(s).is_file():
                    add("error", "facts.csv.path", "file not found: %s" % tilde(expand(s)))
            cols = dget(eff, "facts.csv.columns") or {}
            if not (isinstance(cols, dict) and cols.get("phone")):
                add("error", "facts.csv.columns", "needs the phone column (the header your CSV uses for phone numbers)")
        elif src == "lark":
            if not (dget(eff, "facts.lark.people_tables") or dget(eff, "facts.lark.payment_tables")):
                add("error", "facts.lark", "people_tables and payment_tables are both empty")
            need_bin("lark_cli", "facts.source lark")
    if dget(eff, "second_source.enabled") is True:
        src = dget(eff, "second_source.source")
        if src == "none":
            add("error", "second_source.source", "is none while second_source.enabled is true")
        elif src == "lark":
            need_file("second_source.lark.whitelist", why=" (a missing whitelist is an error, not zero chats)")
            need_bin("lark_cli", "second_source.source lark")
    if dget(eff, "calendar.destination") == "lark":
        if not str(dget(eff, "calendar.lark.calendar_id") or ""):
            add("error", "calendar.lark.calendar_id", "is empty while calendar.destination is lark")
        need_bin("lark_cli", "calendar.destination lark")
    if dget(eff, "backup.enabled") is True:
        need_dir("backup.dest")
    outlet = dget(eff, "notify.outlet")
    if outlet == "telegram":
        p = need_file("notify.telegram_file")
        if p is not None and p.is_file() and mode_of(p) not in (0o600, 0o400):
            add("warning", "notify.telegram_file", "holds a bot token; chmod 600 %s" % tilde(p))
    elif outlet == "command" and not str(dget(eff, "notify.command") or "").strip():
        add("error", "notify.command", "is empty while notify.outlet is command")
    if dget(eff, "digest.enabled") is True and dget(eff, "digest.push") is True and outlet == "none":
        add("warning", "digest.push", "true but notify.outlet is none; the digest is only written to live/digest")
    return issues


def cmd_check_config(args):
    path = expand(args.config) if args.config else default_config_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        say("check-config: config not found: %s (create it with: wa_setup.py init)" % tilde(path))
        return 2
    except ValueError as e:
        say("check-config: %s is not valid JSON: %s" % (tilde(path), e))
        return 2
    if not isinstance(raw, dict):
        say("check-config: %s should hold a JSON object" % tilde(path))
        return 2
    cfg = None
    if WC is not None:
        try:
            cfg = load_cfg(path)
        except SetupError as e:
            say("check-config: %s" % e)
            return 2
    else:
        say("check-config: WARNING wa_common could not be imported (%s); defaults and paths are approximated" % WC_ERROR)
    issues = check_config(raw, cfg)
    for level, key, msg in issues:
        say("%s %s: %s" % ("ERROR" if level == "error" else "WARN ", key, msg))
    n_err = sum(1 for i in issues if i[0] == "error")
    say("check-config: %d error(s), %d warning(s) in %s" % (n_err, len(issues) - n_err, tilde(path)))
    return 1 if n_err else 0


# ---------------------------------------------------------------- launchd (render, install, uninstall)

LAUNCHCTL = "/bin/launchctl"
NOT_LOADED_CODES = (3, 36, 113)   # "No such process", "Operation now in progress" style answers for a missing service


def home_dir(cfg):
    return Path(WC.home_dir(cfg)) if WC is not None else expand(dget(cfg, "paths.home") or "~/WhatsApp-Live")


def live_dir(cfg):
    return Path(WC.live_dir(cfg)) if WC is not None else home_dir(cfg) / "live"


def label_for(cfg, intake):
    return str(dget(cfg, "launchd.intake_label" if intake else "launchd.label") or "")


def refuse_reason(label, plist_label=None):
    for lab in (label, plist_label):
        if lab and reserved_label(lab):
            return ("label %s is reserved for another install on this Mac; "
                    "wa_setup.py never loads, stops or removes it" % lab)
    return ""


def parse_hhmm(text, default=(20, 0)):
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", str(text or ""))
    return (int(m.group(1)), int(m.group(2))) if m else default


def launchd_values(cfg, config_path, intake=False, payload=None, user_home=None):
    payload = Path(payload) if payload else PAYLOAD
    home, live = home_dir(cfg), live_dir(cfg)
    ld = home / "launchd"
    hour, minute = parse_hhmm(dget(cfg, "intake.evening_at"))
    log = str(live / ("intake-evening.log" if intake else "stderr.log"))
    script = str(ld / ("intake-evening.mjs" if intake else "run.mjs"))
    user = str(user_home or Path.home())
    return {
        # daemon.plist
        "LABEL": label_for(cfg, intake),
        "NODE": resolve_bin(cfg, "node"),
        "RUN_MJS": script,
        "PYTHON": resolve_bin(cfg, "python") or "/usr/bin/python3",
        "WA_LIVE": str(payload / "daemon" / "wa_live.py"),
        "CONFIG": str(expand(config_path).absolute()),
        "WORKDIR": str(ld),
        "STDERR_LOG": log,
        "PATH_ENV": str(dget(cfg, "bin.path_env") or ":".join(STANDARD_PATH)),
        # intake-evening.plist and intake-evening.mjs (HOME_DIR is the account home: that plist sets HOME from it)
        "SCRIPT": script,
        "HOME_DIR": user,
        "LOG": log,
        "HOUR": hour,
        "MINUTE": minute,
        "DAEMON_DIR": str(payload / "daemon"),
        # spare names for templates written later
        "HOME": user,
        "WA_HOME": str(home),
        "LIVE_DIR": str(live),
        "PAYLOAD": str(payload),
        "STDOUT_LOG": log,
        "INTAKE_MJS": str(ld / "intake-evening.mjs"),
        "WA_INTAKE": str(payload / "daemon" / "wa_intake.py"),
        "CLAUDE": resolve_bin(cfg, "claude"),
        "EVENING_AT": "%02d:%02d" % (hour, minute),
    }


def write_if_changed(path, data, mode, rep, note=""):
    path = Path(path)
    if path.is_file() and path.read_bytes() == data:
        rep.keep(path, "unchanged")
        return False
    verb = "update" if path.exists() else "write"
    if not rep.dry_run:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, str(path))
    rep.did(verb, path, note)
    return True


def do_render_launchd(cfg, config_path, rep, intake=False, payload=None, user_home=None):
    payload = Path(payload) if payload else PAYLOAD
    templates = payload / TEMPLATES_REL
    what = "evening intake job" if intake else "daemon"
    rep.line("render-launchd: %s%s" % (what, "  (dry run, nothing is written)" if rep.dry_run else ""))
    values = launchd_values(cfg, config_path, intake, payload, user_home)
    label = values["LABEL"]
    if intake and not (dget(cfg, "intake.enabled") is True and dget(cfg, "intake.evening_push") is True):
        rep.warn("intake.enabled and intake.evening_push are not both true: rendered anyway, "
                 "install-launchd --intake will refuse until they are")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", label):
        rep.error("launchd label %r is not a valid label" % label)
    if not values["NODE"]:
        rep.error("node not found: install node, or set bin.node to its absolute path (that binary needs Full Disk Access)")
    plist_tmpl = templates / ("intake-evening.plist" if intake else "daemon.plist")
    mjs_tmpl = templates / ("intake-evening.mjs" if intake else "run.mjs")
    for t in (plist_tmpl, mjs_tmpl):
        if not t.is_file():
            rep.error("template %s/%s is not in the payload" % (TEMPLATES_REL, t.name))
    prot = under_protected(values["WORKDIR"], user_home)
    if prot:
        rep.error("working folder %s is inside %s; move paths.home out of it" % (tilde(values["WORKDIR"]), tilde(prot)))
    if rep.errors:
        return rep
    script = values["WA_INTAKE"] if intake else values["WA_LIVE"]
    if not Path(script).is_file():
        rep.warn("%s is not in the payload" % tilde(script))
    for key in ("CONFIG",):
        if under_protected(values[key], user_home):
            rep.warn("%s is inside a privacy protected folder; launchd children may stall there" % tilde(values[key]))
    if values["CLAUDE"] and os.path.dirname(values["CLAUDE"]) not in values["PATH_ENV"].split(":"):
        rep.warn("bin.path_env does not contain the folder of claude (%s)" % tilde(os.path.dirname(values["CLAUDE"])))

    text, leftover = render_placeholders(plist_tmpl.read_text(encoding="utf-8"), values, xml_escape=True)
    if leftover:
        rep.error("%s has placeholders wa_setup does not know: %s" % (plist_tmpl.name, ", ".join(leftover)))
        return rep
    try:
        pl = plistlib.loads(text.encode("utf-8"))
    except Exception as e:
        rep.error("rendered %s is not a valid plist: %s" % (plist_tmpl.name, e))
        return rep
    args = pl.get("ProgramArguments") or []
    problems = []
    if pl.get("Label") != label:
        problems.append("Label is %r" % pl.get("Label"))
    if not args or args[0] != values["NODE"]:
        problems.append("the program is not node")
    if not intake:
        want = {"KeepAlive": True, "RunAtLoad": True, "ThrottleInterval": 10, "ExitTimeOut": 90,
                "StandardOutPath": "/dev/null", "WorkingDirectory": values["WORKDIR"]}
        problems += ["%s is %r, expected %r" % (k, pl.get(k), v) for k, v in want.items() if pl.get(k) != v]
    else:
        when = {"Hour": values["HOUR"], "Minute": values["MINUTE"]}
        if pl.get("StartCalendarInterval") != when:
            problems.append("StartCalendarInterval is %r, expected %r" % (pl.get("StartCalendarInterval"), when))
        if pl.get("RunAtLoad") is True or pl.get("KeepAlive"):
            problems.append("an evening job must not run at load or stay alive")
    if under_protected(pl.get("WorkingDirectory") or "", user_home):
        problems.append("WorkingDirectory is inside a privacy protected folder")
    if problems:
        rep.error("rendered plist fails its own checks: %s" % "; ".join(problems))
        return rep
    ld = Path(values["WORKDIR"])
    if not rep.dry_run and not ld.is_dir():
        ld.mkdir(mode=0o700, parents=True, exist_ok=True)
    mjs_out = ld / mjs_tmpl.name
    plist_out = ld / (label + ".plist")
    mjs_text = mjs_tmpl.read_text(encoding="utf-8")
    for name in ("CONFIG", "DAEMON_DIR"):   # only the quoted constants; comments that name them stay as written
        v = values[name].replace("\\", "\\\\").replace("'", "\\'")
        mjs_text = mjs_text.replace("'{%s}'" % name, "'%s'" % v)
    write_if_changed(mjs_out, mjs_text.encode("utf-8"), 0o644, rep, "from %s/%s" % (TEMPLATES_REL, mjs_tmpl.name))
    write_if_changed(plist_out, text.encode("utf-8"), 0o644, rep, "label %s" % label)
    rep.data.update({"plist": str(plist_out), "mjs": str(mjs_out), "plist_dict": pl})
    rep.line("  program: %s" % " ".join(tilde(a) for a in args))
    rep.line("  stderr: %s" % tilde(pl.get("StandardErrorPath") or ""))
    if not rep.errors:
        installed = (Path(user_home) if user_home else Path.home()) / "Library" / "LaunchAgents" / (label + ".plist")
        try:
            same = (not rep.dry_run) and installed.is_file() and installed.read_bytes() == plist_out.read_bytes()
        except OSError:
            same = False
        if same:
            rep.line("next: nothing to reinstall, the installed LaunchAgent already matches this plist")
        else:
            rep.line("next: wa_setup.py install-launchd%s --config %s" % (" --intake" if intake else "", tilde(values["CONFIG"])))
    return rep


def _launchd_targets(cfg, intake, launch_agents):
    label = label_for(cfg, intake)
    domain = "gui/%d" % os.getuid()
    agents = Path(launch_agents) if launch_agents else Path.home() / "Library" / "LaunchAgents"
    return label, domain, domain + "/" + label, agents / (label + ".plist"), home_dir(cfg) / "launchd" / (label + ".plist")


def do_install_launchd(cfg, rep, intake=False, runner=None, launch_agents=None, wait_seconds=15, kill=None, sleep=None):
    label, domain, target, dest, staged = _launchd_targets(cfg, intake, launch_agents)
    rep.line("install-launchd: %s%s" % (label, "  (dry run, nothing runs)" if rep.dry_run else ""))
    staged_label = None
    if staged.is_file():
        try:
            staged_label = plistlib.loads(staged.read_bytes()).get("Label")
        except Exception as e:
            rep.error("%s is not a valid plist (%s); run render-launchd again" % (tilde(staged), e))
            return rep
    why = refuse_reason(label, staged_label)
    if why:
        rep.error("refused: " + why)
        rep.data["refused"] = True
        return rep
    if intake and not (dget(cfg, "intake.enabled") is True and dget(cfg, "intake.evening_push") is True):
        rep.error("refused: intake.enabled and intake.evening_push must both be true before the evening job is installed")
        return rep
    if staged_label is None:
        rep.error("%s not found; run wa_setup.py render-launchd%s first" % (tilde(staged), " --intake" if intake else ""))
        return rep
    if staged_label != label:
        rep.error("%s has Label %s but the config says %s; run render-launchd again" % (tilde(staged), staged_label, label))
        return rep
    pl = plistlib.loads(staged.read_bytes())
    for p in pl.get("ProgramArguments") or []:   # a missing file here would crash loop under KeepAlive
        if str(p).startswith("/") and not Path(p).exists():
            rep.error("file named in the plist is missing: %s" % tilde(p))
    if rep.errors:
        return rep
    plan = ["copy %s to %s" % (tilde(staged), tilde(dest)),
            "%s enable %s" % (LAUNCHCTL, target),
            "%s bootout %s (only when it is already loaded)" % (LAUNCHCTL, target),
            "%s bootstrap %s %s" % (LAUNCHCTL, domain, tilde(dest)),
            "%s print %s (state and pid)" % (LAUNCHCTL, target)]
    if rep.dry_run:
        for step in plan:
            rep.line("  would " + step)
        return rep
    if _SELFTEST["active"]:
        rep.error("refused: the selftest never runs launchctl")
        rep.data["refused"] = True
        return rep
    run = runner or default_runner
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + dest.name + ".", dir=str(dest.parent))
    with os.fdopen(fd, "wb") as f:
        f.write(staged.read_bytes())
    os.chmod(tmp, 0o644)
    os.replace(tmp, str(dest))
    rep.did("copy", dest)
    rc, out, err = run([LAUNCHCTL, "enable", target])
    if rc != 0:
        rep.warn("launchctl enable exit %d: %s" % (rc, (err or out).strip()[-200:]))
    rc, out, err = run([LAUNCHCTL, "print", target])
    if rc == 0:
        rep.line("  %s is loaded, booting it out first (running jobs get ExitTimeOut to finish)" % label)
        run([LAUNCHCTL, "bootout", target], timeout=120)
        deadline = time.time() + wait_seconds
        while time.time() < deadline and run([LAUNCHCTL, "print", target])[0] == 0:
            (sleep or time.sleep)(0.5)
        if not intake:
            # bootout stops the node parent; a frozen python never runs its stop handler and stays, holding the port
            _, _, ok = stop_daemon_pythons(cfg, cfg.get("config_path"), rep, run, kill or os.kill, sleep or time.sleep,
                                           int(pl.get("ExitTimeOut") or 90), None)
            if not ok:
                return rep
    rc, out, err = run([LAUNCHCTL, "bootstrap", domain, str(dest)])
    if rc != 0:
        rep.error("launchctl bootstrap exit %d: %s" % (rc, (err or out).strip()[-300:]))
        return rep
    rep.did("bootstrap", target)
    rc, out, err = run([LAUNCHCTL, "print", target])
    for ln in out.splitlines():
        if re.match(r"\s*(state|pid|program|last exit code) = ", ln):
            rep.line("  " + ln.strip())
    if intake:
        rep.line("proof: with one open entry in the buffer, launchctl kickstart -k %s; %s/intake-evening.log gets a line "
                 "and one push arrives (station O7)" % (target, tilde(live_dir(cfg))))
    else:
        rep.line("proof: the daemon log says it started, and %s/commands/probe-claude.json gets an answer (station C2)"
                 % tilde(live_dir(cfg)))
    return rep


def do_uninstall_launchd(cfg, rep, intake=False, runner=None, launch_agents=None, kill=None, sleep=None):
    label, domain, target, dest, staged = _launchd_targets(cfg, intake, launch_agents)
    rep.line("uninstall-launchd: %s%s" % (label, "  (dry run, nothing runs)" if rep.dry_run else ""))
    why = refuse_reason(label)
    if not why and dest.is_file():
        try:
            why = refuse_reason(None, plistlib.loads(dest.read_bytes()).get("Label"))
        except Exception:
            pass
    if why:
        rep.error("refused: " + why)
        rep.data["refused"] = True
        return rep
    plan = ["%s bootout %s" % (LAUNCHCTL, target),
            "%s disable %s (stays off after a restart until install-launchd enables it)" % (LAUNCHCTL, target),
            "remove %s%s" % (tilde(dest), "" if dest.exists() else " (not there)")]
    if rep.dry_run:
        for step in plan:
            rep.line("  would " + step)
        rep.line("  the rendered copy %s and everything in %s stay" % (tilde(staged), tilde(live_dir(cfg))))
        return rep
    if _SELFTEST["active"]:
        rep.error("refused: the selftest never runs launchctl")
        rep.data["refused"] = True
        return rep
    run = runner or default_runner
    wait = 90
    if dest.is_file():
        try:
            wait = int(plistlib.loads(dest.read_bytes()).get("ExitTimeOut") or 90)
        except Exception:
            pass
    rc, out, err = run([LAUNCHCTL, "bootout", target], timeout=120)
    if rc == 0:
        rep.did("bootout", target)
    elif rc in NOT_LOADED_CODES or "No such process" in (err + out) or "Could not find" in (err + out):
        rep.line("  %s was not loaded" % label)
    else:
        rep.warn("launchctl bootout exit %d: %s" % (rc, (err or out).strip()[-200:]))
    if not intake:
        # bootout stops the node parent; a frozen python never runs its stop handler and stays, holding the port
        _, _, ok = stop_daemon_pythons(cfg, cfg.get("config_path"), rep, run, kill or os.kill, sleep or time.sleep, wait, None)
        if not ok:
            return rep
    rc, out, err = run([LAUNCHCTL, "disable", target])
    if rc == 0:
        rep.did("disable", target)
    else:
        rep.warn("launchctl disable exit %d: %s" % (rc, (err or out).strip()[-200:]))
    if dest.exists():
        dest.unlink()
        rep.did("remove", dest)
    rep.line("kept: %s and %s (state, logs, config)" % (tilde(staged), tilde(live_dir(cfg))))
    return rep


# ---------------------------------------------------------------- restart

PS = "/bin/ps"
LSOF = "/usr/sbin/lsof"
DAEMON_ONE_SHOT_FLAGS = ("--selftest", "--status", "--scan-once")


def launchd_pid(run, target):
    """(pid of the job's top process or None, loaded?) from launchctl print."""
    rc, out, err = run([LAUNCHCTL, "print", target])
    if rc != 0:
        return None, False
    m = re.search(r"^\s*pid = (\d+)", out, re.M)
    return (int(m.group(1)) if m else None), True


def process_rows(run):
    """[(pid, ppid, command)] for every process of this user (ps)."""
    rc, out, err = run([PS, "-axo", "pid=,ppid=,command="])
    rows = []
    for ln in out.splitlines():
        parts = ln.strip().split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            rows.append((int(parts[0]), int(parts[1]), parts[2] if len(parts) > 2 else ""))
    return rows


def port_listeners(run, port):
    """pids listening on TCP port (lsof); [] when nothing listens or lsof is missing."""
    rc, out, err = run([LSOF, "-nP", "-iTCP:%d" % int(port), "-sTCP:LISTEN", "-t"])
    return sorted({int(x) for x in out.split() if x.isdigit()})


def daemon_pythons(rows, config_path, listeners=()):
    """[(pid, ppid)] of the long running wa_live.py processes of this install: the ones started with this config, plus
    any wa_live.py holding this install's board port (a symlinked path may print differently)."""
    cp = str(config_path)
    out = []
    for pid, ppid, cmd in rows:
        if not is_daemon_python(cmd):
            continue
        if ("--config " + cp) in cmd or pid in listeners:
            out.append((pid, ppid))
    return out


def is_daemon_python(cmd):
    """True for the python running wa_live.py as the daemon. The node parent's command line names the same script and
    config (node run.mjs <python> <wa_live.py> --config <path>), so the test is the process's own program: a python
    (macOS shows /usr/bin/python3 as .../Python.app/Contents/MacOS/Python) whose script argument is wa_live.py."""
    toks = cmd.split()
    if len(toks) < 2 or not os.path.basename(toks[0]).lower().startswith("python"):
        return False
    if not toks[1].endswith("wa_live.py"):
        return False
    return not any(f in toks for f in DAEMON_ONE_SHOT_FLAGS)


def _alive(kill, pid):
    try:
        kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def stop_daemon_pythons(cfg, config_path, rep, run, kill, sleep, wait, node_pid):
    """Stop every long running wa_live.py of this install (the child of node_pid and any orphan): SIGTERM, wait up to
    `wait` seconds, SIGKILL what is still alive (a frozen python never runs its stop handler). Used before every
    restart, reinstall and uninstall, because launchd stopping the node parent alone can leave a frozen python
    holding the board port. Returns (stopped pids, killed pids, ok)."""
    import signal as _signal
    port = int(dget(cfg, "board.port") or 0) if dget(cfg, "board.enabled", True) else 0
    rows = process_rows(run)
    listeners = port_listeners(run, port) if port else []
    pys = daemon_pythons(rows, expand(config_path), listeners) if config_path else []
    victims = [pid for pid, _ in pys]
    for pid, ppid in pys:
        try:
            kill(pid, _signal.SIGTERM)
            rep.line("  SIGTERM python %d%s" % (pid, "" if ppid == node_pid else
                     (" (left running after launchd let go of it)" if node_pid is None else " (orphan from an earlier restart)")))
        except ProcessLookupError:
            pass
    waited = 0
    while waited < wait and any(_alive(kill, pid) for pid in victims):
        sleep(1)
        waited += 1
    stuck = [pid for pid in victims if _alive(kill, pid)]
    for pid in stuck:
        rep.warn("python %d did not exit %ds after SIGTERM (frozen, its stop handler never ran): SIGKILL" % (pid, wait))
        try:
            kill(pid, _signal.SIGKILL)
        except ProcessLookupError:
            pass
    if stuck:
        for _ in range(10):
            if not any(_alive(kill, pid) for pid in stuck):
                break
            sleep(1)
        still = [pid for pid in stuck if _alive(kill, pid)]
        if still:
            rep.error("python %s survived SIGKILL; look at it with ps before anything else" % still)
            return victims, stuck, False
    return victims, stuck, True


def do_restart(cfg, config_path, rep, runner=None, kill=None, sleep=None, wait_seconds=None, respawn_seconds=60,
               launch_agents=None, http_get=None):
    """Restart the daemon of this install and prove the result. See the module docstring, `restart`."""
    label, domain, target, dest, staged = _launchd_targets(cfg, False, launch_agents)
    rep.line("restart: %s%s" % (label, "  (dry run, nothing runs)" if rep.dry_run else ""))
    installed_label = None
    if dest.is_file():
        try:
            installed_label = plistlib.loads(dest.read_bytes()).get("Label")
        except Exception:
            pass
    why = refuse_reason(label, installed_label)
    if why:
        rep.error("refused: " + why)
        rep.data["refused"] = True
        return rep
    exit_timeout = 90
    if dest.is_file():
        try:
            exit_timeout = int(plistlib.loads(dest.read_bytes()).get("ExitTimeOut") or 90)
        except Exception:
            pass
    wait = int(wait_seconds if wait_seconds is not None else exit_timeout)
    port = int(dget(cfg, "board.port") or 0) if dget(cfg, "board.enabled", True) else 0
    plan = ["%s print %s (the node parent's pid)" % (LAUNCHCTL, target),
            "ps and lsof: the python child of that node, any wa_live.py of this config left from an earlier restart%s"
            % (", and who holds port %d" % port if port else ""),
            "SIGTERM each of them; wait up to %ds (ExitTimeOut) for running jobs to finish" % wait,
            "SIGKILL any that is still alive after that (a frozen python never runs its stop handler)",
            "wait for launchd to start a fresh node and python (KeepAlive); after 20s, %s kickstart %s (-k only when the old node is still up)" % (LAUNCHCTL, target),
            "prove: one python of this install, child of the new node%s" % (", and the only listener on port %d" % port if port else "")]
    if rep.dry_run:
        for step in plan:
            rep.line("  would " + step)
        return rep
    if _SELFTEST["active"] and runner is None:
        rep.error("refused: the selftest never runs launchctl")
        rep.data["refused"] = True
        return rep
    run = runner or default_runner
    kill = kill or os.kill
    sleep = sleep or time.sleep

    node_pid, loaded = launchd_pid(run, target)
    if not loaded:
        rep.error("%s is not loaded; install it with wa_setup.py install-launchd" % label)
        return rep
    rows = process_rows(run)
    cmd_of = dict((pid, cmd) for pid, _, cmd in rows)
    listeners = port_listeners(run, port) if port else []
    pys = daemon_pythons(rows, expand(config_path), listeners)
    py_pids = set(pid for pid, _ in pys)
    foreign = [pid for pid in listeners if pid not in py_pids]
    if foreign:
        rep.error("port %d is held by pid %s (%s), which is not this daemon; free the port or change board.port, "
                  "then restart" % (port, foreign[0], (cmd_of.get(foreign[0]) or "?")[:120]))
        rep.data["refused"] = True
        return rep
    rep.line("  before: node pid %s, python %s%s" % (node_pid or "none",
             ", ".join("%d (parent %d%s)" % (pid, ppid, "" if ppid == node_pid else ", ORPHAN") for pid, ppid in pys) or "none",
             (", port %d held by %s" % (port, listeners or "nobody")) if port else ""))
    if not pys and node_pid:
        rep.line("  no python child found; the node parent %d is restarted instead" % node_pid)
    victims, stuck, ok = stop_daemon_pythons(cfg, config_path, rep, run, kill, sleep, wait, node_pid)
    if not ok:
        return rep
    rep.data["stopped"] = victims
    rep.data["killed"] = stuck

    # launchd restarts the job when node exits (KeepAlive); nudge it only when that has not happened
    new_node = None
    for i in range(respawn_seconds):
        pid, loaded = launchd_pid(run, target)
        if pid and pid != node_pid:
            new_node = pid
            break
        if i == 20:
            if pid == node_pid and pid:
                rep.line("  the old node %d is still up 20s after its python stopped: %s kickstart -k (no python left to orphan)"
                         % (pid, LAUNCHCTL))
                run([LAUNCHCTL, "kickstart", "-k", target])
            else:
                rep.line("  launchd has not started it again within 20s: %s kickstart" % LAUNCHCTL)
                run([LAUNCHCTL, "kickstart", target])
        sleep(1)
    if not new_node:
        rep.error("no new daemon process within %ds; look at %s/stderr.log" % (respawn_seconds, tilde(live_dir(cfg))))
        return rep

    # proof: one python of this install, child of the new node, alone on the port
    proof = None
    for _ in range(respawn_seconds):
        rows = process_rows(run)
        listeners = port_listeners(run, port) if port else []
        pys = daemon_pythons(rows, expand(config_path), listeners)
        children = [pid for pid, ppid in pys if ppid == new_node]
        if len(pys) == 1 and children and (not port or listeners == children):
            proof = children[0]
            break
        sleep(1)
    if proof is None:
        extra = [pid for pid, ppid in pys if ppid != new_node]
        rep.error("after the restart: python %s, port %d held by %s%s" % (
            [pid for pid, _ in pys] or "none", port, listeners or "nobody",
            ("; python %s is not a child of the new node %d (orphan): check it with ps -o pid,ppid,command -p %s, "
             "stop it with kill, then restart again" % (extra, new_node, ",".join(str(x) for x in extra))) if extra else ""))
        return rep
    rep.data["new"] = {"node": new_node, "python": proof}
    rep.line("  after: node pid %d, python %d%s" % (new_node, proof, (", the only listener on port %d" % port) if port else ""))
    if port:
        get = http_get or _http_get
        code, body = get("http://127.0.0.1:%d/health" % port)
        rep.data["health"] = (code, body)
        rep.line("  /health: %s %s" % (code, (body or "").strip()[:80]))
    rep.line("proof: one daemon, the new code is loaded (its first line in %s/wa_live.log names the version)" % tilde(live_dir(cfg)))
    return rep


def _http_get(url, timeout=5):
    import urllib.request
    import urllib.error
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, "%s" % e


# ---------------------------------------------------------------- sync-reader-config

def reader_config(cfg, config_path):
    """The keys the reader (wa_sweep, wa_context) takes from the live config. Everything else in a reader config
    file (days, keep_snapshot, last_sweep_*) is left as it is."""
    brain = cfg.get("brain") if isinstance(cfg.get("brain"), dict) else {}
    names = [n.strip() for n in (cfg.get("my_names") or []) if isinstance(n, str) and n.strip()]
    stop = set()
    for n in names + [str(cfg.get("owner_label") or "")]:
        for tok in re.findall(r"\w+", n.lower(), re.UNICODE):
            if len(tok) >= 2 and not tok.isdigit():
                stop.add(tok)
    people, decisions = path_of(cfg, "paths.people_dir"), path_of(cfg, "paths.decisions_dir")
    model = brain.get("whisper_model")
    rd = cfg.get("reader") if isinstance(cfg.get("reader"), dict) else {}

    def dirs(key, base=()):
        extra = [str(expand(d)) for d in (rd.get(key) or []) if isinstance(d, str) and d.strip()]
        return list(dict.fromkeys([str(p) for p in base if p] + extra))
    stop.update(w.strip().lower() for w in (rd.get("stop_words") or []) if isinstance(w, str) and w.strip())
    out_extra = {}
    for key in ("exclude_dirs", "low_priority_dirs"):
        if isinstance(rd.get(key), list) and rd.get(key):   # absent or empty means the reader's own defaults apply
            out_extra[key] = dirs(key)
    if "keep_snapshot" in rd:
        out_extra["keep_snapshot"] = bool(rd.get("keep_snapshot"))
    return dict({
        "ignored_jids": [j for j in (cfg.get("ignored_jids") or []) if isinstance(j, str)],
        "my_phone": str(cfg.get("my_phone") or ""),
        "my_names": names,
        "owner_label": str(cfg.get("owner_label") or ""),
        "transcribe": bool(brain.get("transcribe", True)),
        "whisper_model": str(expand(model)) if isinstance(model, str) and model and "<" not in model else "",
        "whisper_language": str(brain.get("whisper_language") or ""),
        "whisperkit_bin": resolve_bin(cfg, "whisperkit"),
        "ffmpeg_bin": resolve_bin(cfg, "ffmpeg"),
        "vault": str(path_of(cfg, "paths.vault") or ""),
        "people_dir": str(people or ""),
        "person_dirs": dirs("person_dirs", (people, decisions)),
        "index_dirs": dirs("index_dirs"),
        "stop_words": sorted(stop),
        "_generated": {"by": "wa_setup.py sync-reader-config", "from": str(expand(config_path).absolute()),
                       "note": "regenerated from live/config.json (the reader block and the keys above); edit that file, not this one"},
    }, **out_extra)


def do_sync_reader_config(cfg, config_path, rep):
    home = home_dir(cfg)
    rep.line("sync-reader-config: %s%s" % (tilde(home), "  (dry run, nothing is written)" if rep.dry_run else ""))
    if not home.is_dir():
        rep.error("%s does not exist; run wa_setup.py init first" % tilde(home))
        return rep
    managed = reader_config(cfg, config_path)
    targets = [home / "config.json"]
    work = home / "runs-work"
    if work.is_dir():
        targets += sorted(d / "config.json" for d in work.iterdir() if d.is_dir() and not d.is_symlink())
    if (home / "prewarm").is_dir():
        targets.append(home / "prewarm" / "config.json")
    for t in targets:
        existing = {}
        if t.exists():
            existing = read_json_file(t, None)
            if not isinstance(existing, dict):
                rep.warn("%s was not a JSON object; replaced" % tilde(t))
                existing = {}
        new = dict(existing)
        new.update(managed)
        new.setdefault("days", 3)
        changed = write_if_changed(t, json_text(new).encode("utf-8"), 0o600, rep, "reader config")
        if not changed and mode_of(t) not in (0o600, None):
            if not rep.dry_run:
                os.chmod(str(t), 0o600)
            rep.did("chmod 600", t, "it holds your number and names")
    rep.line("sync-reader-config: %d file(s), %d changed" % (len(targets), len(rep.done)))
    return rep


# ---------------------------------------------------------------- seed-status and copy-seed (DESIGN §6)

def seed_rows(cfg, payload=None):
    payload = Path(payload) if payload else PAYLOAD
    live = live_dir(cfg)
    data = load_seeds(live)
    entries = dict(data["seeds"])
    standard = dict(("prompts/" + n, TEMPLATES_REL + "/" + n) for n in PROMPT_SEEDS)
    standard["topic-map.json"] = TEMPLATES_REL + "/topic-map.example.json"
    for key, seed_rel in standard.items():
        if key not in entries:
            entries[key] = {"seed": seed_rel, "unrecorded": True}
    pp = path_of(cfg, "paths.person_playbook")
    if pp is not None and pp.is_file() and record_key(live, pp) not in entries:
        entries[record_key(live, pp)] = {"seed": read_stamp(pp)[0] or "", "unrecorded": True}
    rows = []
    for key in sorted(entries):
        rec = entries[key]
        copy_path = record_path(live, key)
        seed_rel = str(rec.get("seed") or "")
        seed_path = payload / seed_rel if seed_rel else None
        seed_v = read_stamp(seed_path)[1] if seed_path is not None and seed_path.is_file() else None
        row = {"key": key, "copy": str(copy_path), "seed": seed_rel, "recorded": not rec.get("unrecorded"),
               "copy_version": None, "seed_version": seed_v, "kind": "seed"}
        if not copy_path.is_file():
            row["status"] = "owner copy missing (code falls back to the payload seed and says so)"
            rows.append(row)
            continue
        copy_v = read_stamp(copy_path)[1]
        row["copy_version"] = copy_v
        current = sha256_file(copy_path)
        if rec.get("sha256"):
            edited = current != rec["sha256"]
        elif seed_v and copy_v == seed_v:
            edited = current != sha256_file(seed_path)
        else:
            edited = None
        flags = []
        if edited:
            flags.append("edited by owner")
        if seed_v and copy_v and vtuple(seed_v) > vtuple(copy_v):
            flags.append("seed newer")
        if seed_path is None or not seed_path.is_file():
            flags.append("seed no longer in the payload")
        if not copy_v:
            flags.append("stamp removed from the copy")
        if not flags:
            flags.append("unchanged" if edited is False else "not recorded, cannot tell")
        row["status"] = ", ".join(flags)
        rows.append(row)
    for key in sorted(data["rendered"]):
        rec = data["rendered"][key]
        path = record_path(live, key)
        tmpl = payload / str(rec.get("template") or "")
        row = {"key": key, "copy": str(path), "seed": rec.get("template"), "recorded": True, "kind": "rendered",
               "copy_version": None, "seed_version": None}
        if not path.is_file():
            row["status"] = "rendered file missing (wa_setup.py init renders it again)"
        else:
            flags = []
            if sha256_file(path) != rec.get("sha256"):
                flags.append("edited by owner")
            if tmpl.is_file() and sha256_file(tmpl) != rec.get("template_sha256"):
                flags.append("template newer (move the file aside, run init to render it again, merge your edits)")
            row["status"] = ", ".join(flags) or "unchanged"
        rows.append(row)
    return rows


def cmd_seed_status(args):
    cfg = load_cfg(expand(args.config) if args.config else default_config_path())
    rows = seed_rows(cfg)
    if args.json:
        say(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    say("seed-status: %d owner copies (live folder %s)" % (len(rows), tilde(live_dir(cfg))))
    for r in rows:
        versions = ""
        if r["kind"] == "seed":
            versions = "  copy v%s, payload v%s" % (r["copy_version"] or "?", r["seed_version"] or "?")
        say("  %s: %s%s" % (tilde(r["copy"]), r["status"], versions))
    moved = [r for r in rows if "newer" in r["status"]]
    say("seed-status: %d need a merge (install/update.md walks through it section by section)" % len(moved))
    return 0


def do_copy_seed(cfg, seed_rel, to, rep, payload=None, confirmed=False):
    payload = (Path(payload) if payload else PAYLOAD).resolve()
    live = live_dir(cfg)
    src = (payload / seed_rel).resolve()
    rep.data["payload"] = str(payload)
    rep.line("copy-seed: %s to %s%s" % (seed_rel, tilde(expand(to)), "  (dry run, nothing is written)" if rep.dry_run else ""))
    if src != payload and not str(src).startswith(str(payload) + os.sep):
        rep.error("%s is outside the payload" % seed_rel)
        return rep
    records = {}
    if src.is_dir():
        dest_root = expand(to)
        files = sorted(p for p in src.rglob("*") if p.is_file() and not p.name.startswith("."))
        if not files:
            rep.error("%s holds no files" % seed_rel)
            return rep
        for f in files:
            rel = f.relative_to(payload).as_posix()
            copy_seed_file(f, rel, dest_root / f.relative_to(src), live, rep, records, mode=0o644, confirmed=confirmed, dir_mode=0o755)
    elif src.is_file():
        dest = expand(to)
        if dest.is_dir():
            dest = dest / src.name
        copy_seed_file(src, src.relative_to(payload).as_posix(), dest, live, rep, records, mode=0o644, confirmed=confirmed, dir_mode=0o755)
    else:
        rep.error("%s is not in the payload" % seed_rel)
        return rep
    save_seed_records(live, records, {}, rep)
    return rep


# ---------------------------------------------------------------- measure

FDA_REMEDY = ("macOS blocked this read. Open System Settings, Privacy and Security, Full Disk Access; switch on the "
              "app you run this from (Terminal, iTerm, your editor), quit it completely and open it again.")


def ro_uri(path):
    return "file:%s?mode=ro" % urllib.parse.quote(str(path))


def snapshot_db(src, dest_dir):
    """Consistent copy of a live SQLite database (write ahead log included) through the backup API.
    The source is opened read only; the copy is switched to a plain journal."""
    dst = Path(dest_dir) / Path(src).name
    s = sqlite3.connect(ro_uri(src), uri=True, timeout=10)
    try:
        d = sqlite3.connect(str(dst))
        try:
            s.backup(d)
            d.execute("PRAGMA journal_mode=DELETE")
        finally:
            d.close()
    finally:
        s.close()
    return dst


def policy_of(policies, jid, unlisted):
    ent = (policies or {}).get(jid)
    if isinstance(ent, dict):
        return ent.get("policy") or unlisted
    if isinstance(ent, str):
        return ent
    return None


def measure_counts(db, days=3, ignored=(), policies=None, unlisted="skip", now=None, types=None):
    """Inbound content messages in the last `days`, counted per chat. Reads no message text at all."""
    types = types or content_types()
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = (now - dt.timedelta(days=days) - CORE_EPOCH_UTC).total_seconds()
    ignored = set(ignored or ())
    conn = sqlite3.connect(ro_uri(db), uri=True, timeout=10)
    try:
        rows = conn.execute(
            "SELECT s.ZCONTACTJID, s.ZPARTNERNAME, s.ZSESSIONTYPE, m.ZMESSAGETYPE, COUNT(*) "
            "FROM ZWAMESSAGE m JOIN ZWACHATSESSION s ON s.Z_PK = m.ZCHATSESSION "
            "WHERE m.ZISFROMME = 0 AND m.ZMESSAGEDATE >= ? AND m.ZMESSAGETYPE IN (%s) "
            "GROUP BY m.ZCHATSESSION, m.ZMESSAGETYPE" % ",".join(str(int(t)) for t in types), (cutoff,)).fetchall()
    finally:
        conn.close()
    direct_msgs, direct_chats, groups, by_type = 0, set(), {}, {}
    skipped = {"status_or_broadcast": 0, "ignored": 0}
    for jid, name, stype, mtype, n in rows:
        jid = jid or ""
        if stype not in (0, 1) or jid.endswith("@status") or jid.endswith("@broadcast"):
            skipped["status_or_broadcast"] += n
            continue
        if jid in ignored:
            skipped["ignored"] += n
            continue
        label = str(types.get(mtype, mtype))
        by_type[label] = by_type.get(label, 0) + n
        if jid.endswith("@g.us"):
            g = groups.setdefault(jid, {"jid": jid, "name": "", "inbound": 0})
            g["inbound"] += n
            g["name"] = g["name"] or (name or "")
        else:
            direct_msgs += n
            direct_chats.add(jid)
    ranked = sorted(groups.values(), key=lambda g: (-g["inbound"], g["name"]))
    for g in ranked:
        g["policy"] = policy_of(policies, g["jid"], unlisted) or ("unlisted (%s)" % unlisted)
    return {"days": days, "measured_at": now.astimezone().isoformat(timespec="seconds"),
            "direct": {"inbound": direct_msgs, "chats": len(direct_chats)},
            "groups": {"inbound": sum(g["inbound"] for g in ranked), "chats": len(ranked)},
            "by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
            "skipped": skipped, "top_groups": ranked}


def do_measure(rep, days=3, top=15, cfg=None, wa_root=None, now=None):
    root = Path(wa_root) if wa_root else wa_root_default()
    src = root / "ChatStorage.sqlite"
    cfg = cfg or {}
    if days < 1:
        rep.error("--days must be 1 or more")
        return rep
    try:
        present = src.is_file()
    except PermissionError:
        present = False
    if not present:
        rep.error("WhatsApp Desktop database not found at %s (install WhatsApp Desktop from the App Store, link it "
                  "to your phone, keep it open), or it is not readable: %s" % (tilde(src), FDA_REMEDY))
        return rep
    tmp = tempfile.mkdtemp(prefix="wa-measure-")
    try:
        try:
            snap = snapshot_db(src, tmp)
            res = measure_counts(snap, days, cfg.get("ignored_jids") or (), cfg.get("group_policies") or {},
                                 cfg.get("unlisted_group_policy") or "skip", now)
        except (sqlite3.Error, OSError) as e:
            rep.error("could not read the WhatsApp database (%s). %s" % (e, FDA_REMEDY))
            return rep
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    rep.data["result"] = res
    shown = res["top_groups"] if top <= 0 else res["top_groups"][:top]
    rep.line("measure: inbound content messages in the last %d day(s), counted on a snapshot copy (deleted again)" % days)
    rep.line("  one to one inbound: %d messages in %d chats" % (res["direct"]["inbound"], res["direct"]["chats"]))
    rep.line("  group inbound: %d messages in %d groups" % (res["groups"]["inbound"], res["groups"]["chats"]))
    rep.line("  by type: %s" % (", ".join("%s %d" % kv for kv in res["by_type"].items()) or "none"))
    if res["skipped"]["ignored"] or res["skipped"]["status_or_broadcast"]:
        rep.line("  not counted: %d from ignored chats, %d status or broadcast"
                 % (res["skipped"]["ignored"], res["skipped"]["status_or_broadcast"]))
    rep.line("  top %d groups by inbound (name, messages, current policy):" % len(shown))
    for i, g in enumerate(shown, 1):
        rep.line("  %3d. %6d  %s  [%s]" % (i, g["inbound"], g["name"] or "(no name)", g["policy"]))
    per_day = (res["direct"]["inbound"] + res["groups"]["inbound"]) / float(max(days, 1))
    rep.line("measure: about %.0f inbound messages a day before the gate; --json gives the jids for group_policies" % per_day)
    return rep


# ---------------------------------------------------------------- tailscale-hint

def read_serve_status(runner=None, which=None, app="/Applications/Tailscale.app/Contents/MacOS/Tailscale"):
    """`tailscale serve status --json`, read only. Returns (dict, "") or (None, why it could not be read)."""
    runner = runner or default_runner
    ts = (which or shutil.which)("tailscale") or (app if is_exec(app) else "")
    if not ts:
        return None, "tailscale is not installed"
    rc, o, e = runner([ts, "serve", "status", "--json"], timeout=10)
    if rc != 0:
        return None, "tailscale serve status --json exit %d (is Tailscale running and signed in?)" % rc
    o = (o or "").strip()
    if not o:
        return {}, ""
    try:
        d = json.loads(o)
    except ValueError:
        return None, "tailscale serve status --json did not print JSON"
    return (d if isinstance(d, dict) else {}), ""


def serve_ports_in_use(status):
    """Parsed serve status -> {tailnet port: [what that port serves, ...]} (web handlers and TCP forwards)."""
    used = {}
    if not isinstance(status, dict):
        return used
    for port, spec in (status.get("TCP") or {}).items():
        try:
            p = int(port)
        except (TypeError, ValueError):
            continue
        used.setdefault(p, [])
        if isinstance(spec, dict) and spec.get("TCPForward"):
            used[p].append("tcp %s" % spec["TCPForward"])
    for hostport, web in (status.get("Web") or {}).items():
        try:
            p = int(str(hostport).rsplit(":", 1)[1])
        except (IndexError, ValueError):
            continue
        for path, h in ((web or {}).get("Handlers") or {}).items():
            h = h if isinstance(h, dict) else {}
            used.setdefault(p, []).append("%s %s" % (path, h.get("Proxy") or h.get("Path") or ("text" if "Text" in h else "?")))
    return used


def tailscale_hint_lines(cfg, serve_port=None, https=False, status="unchecked", status_why=""):
    """The serve command for the board. status: the parsed `tailscale serve status --json`; with it the hint never
    prints a command that would replace another handler (it moves to the first free port from 8443 up), and says so
    when this board is served already. None: the status could not be read (status_why says why)."""
    port = dget(cfg, "board.port") or 8787
    bind = str(dget(cfg, "board.bind") or "127.0.0.1")
    scheme = "https" if https else "http"
    sport = serve_port or (443 if https else 80)
    target = "http://127.0.0.1:%s" % port
    used = serve_ports_in_use(status) if isinstance(status, dict) else {}
    mine = sorted(p for p, what in used.items()
                  if any(w.endswith(" " + target) or w.endswith(" http://localhost:%s" % port) for w in what))
    warn = []
    if mine:
        spec = ((status.get("TCP") or {}).get(str(mine[0])) or {}) if isinstance(status, dict) else {}
        on = "https" if isinstance(spec, dict) and spec.get("HTTPS") else "http"
        return ["tailscale-hint: this board (%s) is already on your tailnet, tailnet port %s. Nothing to run." % (target, mine[0]),
                "Open it from a phone or laptop signed in to the same tailnet: %s://<this-mac>.<your-tailnet>.ts.net%s/"
                % (on, "" if (on, mine[0]) in (("http", 80), ("https", 443)) else ":%s" % mine[0]),
                "Check:  tailscale serve status",
                "Remove: tailscale serve --%s=%s off" % (on, mine[0])]
    if sport in used:
        free = next(p for p in range(8443, 8600) if p not in used)
        warn.append("WARNING: tailnet port %s already serves %s. A serve command on that port replaces it and takes it off "
                    "your tailnet, so the command below uses port %s instead." % (sport, " · ".join(used[sport]) or "something", free))
        sport = free
    default_port = (https and sport == 443) or (not https and sport == 80)
    suffix = "" if default_port else ":%s" % sport
    lines = ["tailscale-hint: the board listens on %s (this Mac only). Nothing below has been run." % target]
    if status is None:
        warn.append("WARNING: could not read tailscale serve status (%s). Run tailscale serve status yourself first: if the "
                    "port below already serves something, the command replaces it." % (status_why or "unknown reason"))
    lines += warn
    lines += [
        "",
        "Put it on your tailnet (run this yourself on this Mac):",
        "  tailscale serve --bg --%s=%s %s" % (scheme, sport, target),
        "",
        "Open it from a phone or laptop signed in to the same tailnet:",
        "  %s://<this-mac>.<your-tailnet>.ts.net%s/" % (scheme, suffix),
        "  (tailscale status prints this Mac's name on its first line; with MagicDNS the short %s://<this-mac>%s/ works too)" % (scheme, suffix),
        "",
        "Check:  tailscale serve status    (every address should say tailnet only)",
        "Remove: tailscale serve --%s=%s off" % (scheme, sport),
    ]
    if not https:
        lines.append("HTTPS: enable HTTPS certificates in the Tailscale admin console, then use --https=443 instead "
                     "(clipboard buttons on the board work more reliably over https).")
    lines.append("Never: tailscale funnel. It publishes the board, which has no login, to the whole internet.")
    if status == "unchecked" and sport in (80, 443):
        lines.append("Another service already served on that tailnet port? Pass --serve-port 8443 (or any free port).")
    if bind not in LOOPBACK:
        lines.append("WARNING: board.bind is %s. Set it back to 127.0.0.1: anyone on the same network could open the board." % bind)
    return lines


DEFAULT_LITERALS_RE = re.compile(r"~/WhatsApp-Live|\$HOME/WhatsApp-Live|local\.whatsapp-live|8787")
RUNNABLE_FENCES = ("", "sh", "bash", "zsh", "shell")


def default_literals_in_runnable_blocks(payload):
    """Lines of runnable code blocks (fence info empty, sh, bash, zsh or shell) in SKILL.md, install/*.md and
    references/*.md that carry the default home, label or port. Every step block has to use $HOME_DIR, $LIVE, $LABEL,
    $ILABEL and $PORT from the progress file header: a session on a second install pastes the block as it is. A line
    that means the default on purpose carries the comment `# default on purpose`. Returns ["file:line: text"]."""
    payload = Path(payload)
    files = [payload / "SKILL.md"] + sorted((payload / "install").glob("*.md")) + sorted((payload / "references").glob("*.md"))
    hits = []
    for f in files:
        if not f.is_file():
            continue
        inside = runnable = False
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            m = re.match(r"^\s*(```+|~~~+)\s*([A-Za-z0-9_-]*)", line)
            if m:
                inside, runnable = (True, m.group(2).lower() in RUNNABLE_FENCES) if not inside else (False, False)
                continue
            if inside and runnable and DEFAULT_LITERALS_RE.search(line) and "# default on purpose" not in line:
                hits.append("%s:%d: %s" % (f.relative_to(payload), n, line.strip()[:120]))
    return hits


KICKSTART_K_RE = re.compile(r"kickstart\s+-k")
KICKSTART_OK_RE = re.compile(r"ILABEL|intake|never|\bnot\b|instead|⛔|orphan", re.I)


def daemon_kickstart_lines(payload):
    """Lines in SKILL.md, install/, references/ and playbooks/ that still tell someone to restart the daemon with
    `launchctl kickstart -k`, which stops only the node parent and can leave a frozen python holding the port.
    `wa_setup.py restart` is the daemon's restart. Allowed: the evening job ($ILABEL, one shot, no child to orphan)
    and lines that explain why not. Returns ["file:line: text"]."""
    payload = Path(payload)
    files = ([payload / "SKILL.md"] + sorted((payload / "install").glob("*.md")) + sorted((payload / "references").glob("*.md"))
             + sorted((payload / "playbooks").rglob("*.md")))
    hits = []
    for f in files:
        if not f.is_file() or f.name == "CHANGELOG.md":
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if KICKSTART_K_RE.search(line) and not KICKSTART_OK_RE.search(line):
                hits.append("%s:%d: %s" % (f.relative_to(payload), n, line.strip()[:120]))
    return hits


def cmd_tailscale_hint(args):
    cfg = load_cfg(expand(args.config) if args.config else default_config_path())
    status, why = read_serve_status()
    for ln in tailscale_hint_lines(cfg, args.serve_port, args.https, status=status, status_why=why):
        say(ln)
    return 0


# ---------------------------------------------------------------- doctor

def human_age(seconds):
    if seconds is None:
        return "unknown"
    seconds = max(0.0, float(seconds))
    if seconds < 90:
        return "%d s" % seconds
    if seconds < 5400:
        return "%d min" % (seconds / 60)
    if seconds < 172800:
        return "%.1f h" % (seconds / 3600)
    return "%.1f d" % (seconds / 86400)


def human_size(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def file_line(path, now=None):
    """exists with size and age, not found, or permission denied. Never the content."""
    now = now or time.time()
    try:
        st = os.stat(str(path))
    except FileNotFoundError:
        return "not found"
    except PermissionError:
        return "PERMISSION DENIED"
    except OSError as e:
        return "unreadable (%s)" % e.strerror
    if stat.S_ISDIR(st.st_mode):
        try:
            count = len(os.listdir(str(path)))
            return "folder, %d item(s), mode %o, changed %s ago" % (count, stat.S_IMODE(st.st_mode), human_age(now - st.st_mtime))
        except PermissionError:
            return "folder, PERMISSION DENIED when listing"
    return "%s, mode %o, changed %s ago" % (human_size(st.st_size), stat.S_IMODE(st.st_mode), human_age(now - st.st_mtime))


class DoctorContext(object):
    def __init__(self, config_path=None, wa_root=None, runner=None, which=None, user_home=None, launch_agents=None,
                 payload=None, tailscale_app="/Applications/Tailscale.app/Contents/MacOS/Tailscale"):
        self.config_path = expand(config_path) if config_path else default_config_path()
        self.wa_root = Path(wa_root) if wa_root else wa_root_default()
        self.runner = runner or default_runner
        self.which = which or shutil.which
        self.user_home = user_home
        self.launch_agents = Path(launch_agents) if launch_agents else Path.home() / "Library" / "LaunchAgents"
        self.payload = Path(payload) if payload else PAYLOAD
        self.tailscale_app = tailscale_app


def doctor(ctx, out=say):
    st = {"blockers": [], "notes": [], "cfg": None, "raw": None}
    now = time.time()

    def block(text):
        st["blockers"].append(text)
        out("  BLOCKER: " + text)

    def note(text):
        st["notes"].append(text)
        out("  note: " + text)

    def section(title, fn, from_config=False):
        out("")
        out("[%s]" % title)
        if from_config and st.get("no_config"):
            out("  (defaults, no config yet: these lines describe the default home, label and port, not the ones a second "
                "install chose; its own values show here after station C1 writes the config)")
        try:
            fn()
        except Exception as e:
            out("  section failed: %s: %s (the rest of the report follows)" % (type(e).__name__, e))
            st["notes"].append("the %s section failed" % title)

    def s_system():
        out("  system: %s %s, %s" % (platform.system(), platform.mac_ver()[0] or platform.release(), platform.machine()))
        out("  python running this report: %s at %s" % (platform.python_version(), tilde(sys.executable)))
        out("  local time: %s" % dt.datetime.now().astimezone().isoformat(timespec="seconds"))
        if platform.system() != "Darwin":
            block("this kit reads WhatsApp Desktop for Mac; other systems have no route")

    def s_payload():
        out("  payload: %s" % tilde(ctx.payload))
        if WC is None:
            block("daemon/wa_common.py does not import (%s); reinstall the kit" % WC_ERROR)
        else:
            out("  wa_common: imports")
        wanted = ["daemon/wa_live.py", "daemon/board.html", "daemon/templates/daemon.plist", "daemon/templates/run.mjs",
                  "daemon/templates/config.example.json", "daemon/templates/claude-settings.json",
                  "daemon/templates/brain-prompt.md", "skills/breakthrough-whatsapp-read/scripts/wa_sweep.py",
                  "skills/breakthrough-person-dossier/scripts/dossier_core.py"]
        missing = [w for w in wanted if not (ctx.payload / w).is_file()]
        out("  key files: %d of %d present" % (len(wanted) - len(missing), len(wanted)))
        for m in missing:
            note("payload file missing: %s" % m)

    def s_config():
        path = ctx.config_path
        out("  config: %s (%s)" % (tilde(path), file_line(path, now)))
        if not path.is_file():
            note("no config yet; station C1 creates it with wa_setup.py init")
            st["no_config"] = True
            if WC is not None:
                st["cfg"] = WC.apply_defaults({}, None)
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            block("live/config.json is not valid JSON (%s); fix it, then run check-config" % e)
            return
        if not isinstance(raw, dict):
            block("live/config.json should hold a JSON object")
            return
        st["raw"] = raw
        m = mode_of(path)
        if m not in (0o600, 0o400):
            note("config.json is mode %o; chmod 600 it (it holds your number and group list)" % (m or 0))
        if WC is None:
            return
        st["cfg"] = WC.load_config(str(path))
        issues = check_config(raw, st["cfg"], ctx.payload, ctx.user_home)
        n_err = sum(1 for i in issues if i[0] == "error")
        out("  check-config: %d error(s), %d warning(s)" % (n_err, len(issues) - n_err))
        if n_err:
            note("check-config reports %d error(s); run wa_setup.py check-config for the list" % n_err)

    def s_whatsapp():
        root = ctx.wa_root
        cfg = st["cfg"] or {}
        try:
            exists = root.is_dir()
        except PermissionError:
            exists = True
        out("  group container: %s" % ("present" if exists else "not found"))
        if not exists:
            block("WhatsApp Desktop is not installed or never linked on this Mac (App Store, then link it to your phone)")
            return
        db, wal = root / "ChatStorage.sqlite", root / "ChatStorage.sqlite-wal"
        out("  ChatStorage.sqlite: %s" % file_line(db, now))
        out("  ChatStorage.sqlite-wal: %s" % file_line(wal, now))
        for extra in ("LID.sqlite", "ContactsV2.sqlite"):
            try:
                seen = "present" if (root / extra).is_file() else "not found"
            except PermissionError:
                seen = "PERMISSION DENIED"
            out("  %s: %s" % (extra, seen))
        ages = []
        for p in (wal, db):
            try:
                ages.append(now - p.stat().st_mtime)
            except OSError:
                pass
        if not ages:
            block("the WhatsApp database is not there or not visible: %s" % FDA_REMEDY)
            return
        age = min(ages)
        stale_h = float(dget(cfg, "doorbell.stale_alert_hours") or 6)
        out("  last database write: %s ago" % human_age(age))
        if age > 48 * 3600:
            block("WhatsApp Desktop has not written its database for %s; open it and check it is still linked" % human_age(age))
        elif age > stale_h * 3600:
            note("no database write for %s (quiet hours, or WhatsApp Desktop is closed)" % human_age(age))
        try:
            with open(str(db), "rb") as f:
                head = f.read(16)
            readable = head.startswith(b"SQLite format 3")
        except PermissionError:
            block("this process cannot read the WhatsApp database. %s" % FDA_REMEDY)
            return
        except OSError as e:
            block("ChatStorage.sqlite cannot be opened (%s)" % (e.strerror or e))
            return
        out("  readable by this process: %s" % ("yes" if readable else "no (not an SQLite file)"))
        if not readable:
            block("ChatStorage.sqlite is not an SQLite database; WhatsApp Desktop may have changed its storage")
            return
        try:
            conn = sqlite3.connect(ro_uri(db), uri=True, timeout=5)
            try:
                mx = conn.execute("SELECT MAX(Z_PK) FROM ZWAMESSAGE").fetchone()[0]
                out("  message table readable: yes (%d message ids)" % (mx or 0))
            finally:
                conn.close()
        except sqlite3.Error as e:
            block("the WhatsApp database does not read as expected (%s)" % e)
        rc, _, _ = ctx.runner(["/usr/bin/pgrep", "-x", "WhatsApp"], timeout=10)
        out("  WhatsApp app running: %s" % ("yes" if rc == 0 else "no"))
        if rc != 0:
            note("WhatsApp Desktop is not running; the daemon only sees messages while it is open")

    def s_tools():
        cfg = st["cfg"] or {"bin": {}}
        node = resolve_bin(cfg, "node")
        if node:
            rc, o, e = ctx.runner([node, "--version"], timeout=10)
            out("  node: %s (%s)" % (tilde(node), (o or e).strip() or "exit %d" % rc))
            out("  Full Disk Access: grant it to exactly %s (the launchd parent) and to your terminal app;" % tilde(node))
            out("    this report can only test the terminal's access, the daemon's is proven by probe-claude after C2")
            real = os.path.realpath(node)
            for other in ("/opt/homebrew/bin/node", "/usr/local/bin/node"):
                if is_exec(other) and os.path.realpath(other) != real:
                    out("  another node on this Mac: %s (if that one already holds Full Disk Access, set bin.node to it "
                        "after init instead of granting a second one)" % other)
            if "/.nvm/" in node:
                note("node resolves inside ~/.nvm, a version folder that changes with nvm install or uninstall; set bin.node "
                     "to a stable node (for example /opt/homebrew/bin/node or /usr/local/bin/node) and grant that one")
        else:
            block("node not found (install it, for example brew install node)")
        python = resolve_bin(cfg, "python") or ("/usr/bin/python3" if is_exec("/usr/bin/python3") else "")
        if python:
            rc, o, e = ctx.runner([python, "--version"], timeout=10)
            out("  python for the daemon: %s (%s)" % (tilde(python), (o or e).strip() or "exit %d" % rc))
        else:
            block("/usr/bin/python3 not found (xcode-select --install)")
        claude = resolve_bin(cfg, "claude")
        if claude:
            out("  claude: %s (not run here; its login is proven from launchd with commands/probe-claude.json)" % tilde(claude))
        else:
            block("claude (Claude Code) not found on PATH or in the usual install folders")
        for name, why in (("ffmpeg", "voice notes"), ("whisperkit", "voice notes")):
            p = resolve_bin(cfg, name)
            out("  %s: %s" % ({"whisperkit": "whisperkit-cli"}.get(name, name), tilde(p) if p else "not found"))
            if not p:
                note("%s not found; %s stay untranscribed (brew install %s)"
                     % ({"whisperkit": "whisperkit-cli"}.get(name, name), why, {"whisperkit": "whisperkit-cli"}.get(name, name)))
        model = path_of(cfg, "brain.whisper_model")
        if model is not None and model.is_dir():
            n_files = sum(1 for _ in model.rglob("*") if _.is_file())
            out("  whisper model folder: present, %d file(s)" % n_files)
            if under_protected(model, ctx.user_home):
                block("the whisper model sits in a privacy protected folder; copy it under ~/.cache/whisper-models")
        else:
            out("  whisper model folder: not found")
            note("no whisper model folder yet (station C1 downloads one under ~/.cache/whisper-models)")
        brew = ctx.which("brew") or next((p for p in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew") if is_exec(p)), "")
        out("  Homebrew: %s" % (tilde(brew) if brew else "not found"))
        uses_lark = "lark" in (dget(cfg, "facts.source"), dget(cfg, "second_source.source"), dget(cfg, "calendar.destination"))
        if uses_lark:
            lark = resolve_bin(cfg, "lark_cli")
            out("  lark-cli: %s" % (tilde(lark) if lark else "not found"))
            if not lark:
                block("the config selects a Lark adapter but lark-cli is not found")

    def s_tailscale():
        cfg = st["cfg"] or {}
        ts = ctx.which("tailscale") or (ctx.tailscale_app if is_exec(ctx.tailscale_app) else "")
        if not ts:
            out("  tailscale: not found")
            note("Tailscale is not installed; the board opens only on this Mac until it is (station C3)")
            return
        out("  tailscale: %s" % tilde(ts))
        rc, o, e = ctx.runner([ts, "serve", "status"], timeout=10)
        if rc != 0:
            out("  serve status: not readable (exit %d)" % rc)
            note("tailscale serve status did not answer; is Tailscale running and signed in?")
            return
        port = dget(cfg, "board.port") or 8787
        entries = [ln for ln in o.splitlines() if "proxy" in ln]
        served = any(("127.0.0.1:%s" % port) in ln or ("localhost:%s" % port) in ln for ln in entries)
        out("  serve status: readable, %d proxy entr%s, board port %s served: %s"
            % (len(entries), "y" if len(entries) == 1 else "ies", port, "yes" if served else "no"))
        if not served:
            note("the board is not on your tailnet yet (wa_setup.py tailscale-hint prints the command)")

    def s_home():
        cfg = st["cfg"]
        if not cfg:
            out("  no config, skipped")
            return
        home, live = home_dir(cfg), live_dir(cfg)
        out("  home %s: %s" % (tilde(home), file_line(home, now)))
        if not home.is_dir():
            note("the working folder does not exist yet (wa_setup.py init)")
            return
        if under_protected(home, ctx.user_home):
            block("paths.home is inside a privacy protected folder; launchd children stall there")
        if mode_of(home) != 0o700:
            note("%s is mode %o; chmod 700 it" % (tilde(home), mode_of(home) or 0))
        for rel in ("state.sqlite", "wa_live.log", "stderr.log", "claude-settings.json", "topic-map.json", "seeds.json"):
            out("  live/%s: %s" % (rel, file_line(live / rel, now)))
        tg = live / "telegram.json"
        if tg.exists():
            m = mode_of(tg)
            out("  live/telegram.json: present, mode %o" % (m or 0))
            if m not in (0o600, 0o400):
                note("telegram.json holds a bot token; chmod 600 it")
        prompts = live / "prompts"
        out("  live/prompts: %d file(s)" % (len([p for p in prompts.glob("*.md")]) if prompts.is_dir() else 0))
        runs = live / "runs"
        out("  live/runs: %d run folder(s)" % (len([p for p in runs.iterdir() if p.is_dir()]) if runs.is_dir() else 0))
        cmds = live / "commands"
        pending = len([p for p in cmds.glob("*.json")]) if cmds.is_dir() else 0
        out("  live/commands: %d waiting" % pending)
        if pending:
            note("%d command file(s) wait in live/commands; the daemon picks them up only while it runs" % pending)

    def s_launchd():
        cfg = st["cfg"]
        if not cfg:
            out("  no config, skipped")
            return
        label = label_for(cfg, False)
        staged = home_dir(cfg) / "launchd" / (label + ".plist")
        installed = ctx.launch_agents / (label + ".plist")
        out("  label: %s" % label)
        out("  rendered plist: %s" % ("present" if staged.is_file() else "not rendered yet"))
        out("  run.mjs: %s" % ("present" if (home_dir(cfg) / "launchd" / "run.mjs").is_file() else "not rendered yet"))
        if dget(cfg, "intake.enabled") is True and dget(cfg, "intake.evening_push") is True:
            ilabel = label_for(cfg, True)
            out("  evening intake job %s: %s" % (ilabel, "installed" if (ctx.launch_agents / (ilabel + ".plist")).is_file()
                                                 else "not installed (install-launchd --intake)"))
        if not installed.is_file():
            out("  LaunchAgents plist: not installed (station C2)")
            return
        pl = plistlib.loads(installed.read_bytes())
        args = pl.get("ProgramArguments") or []
        checks = [("Label matches", pl.get("Label") == label),
                  ("program is an existing node", bool(args) and is_exec(args[0]) and os.path.basename(args[0]).startswith("node")),
                  ("run.mjs exists", len(args) > 1 and Path(args[1]).is_file()),
                  ("stdout to /dev/null", pl.get("StandardOutPath") == "/dev/null"),
                  ("ExitTimeOut 90", pl.get("ExitTimeOut") == 90),
                  ("same as the rendered copy", staged.is_file() and staged.read_bytes() == installed.read_bytes())]
        out("  LaunchAgents plist: installed, %d of %d checks pass" % (sum(1 for _, ok in checks if ok), len(checks)))
        for name, ok in checks:
            if not ok:
                note("installed plist: %s fails (render-launchd, then install-launchd again)" % name)

    out("WhatsApp Live doctor · %s" % dt.datetime.now().astimezone().isoformat(timespec="seconds"))
    out("Safe to paste: counts, ages and paths only. No message text, no names, no numbers, no tokens.")
    section("system", s_system)
    section("payload", s_payload)
    section("config", s_config)
    section("whatsapp desktop", s_whatsapp)
    section("tools", s_tools)
    section("tailscale", s_tailscale, from_config=True)
    section("working folder", s_home, from_config=True)
    section("launchd", s_launchd, from_config=True)
    out("")
    b, n = st["blockers"], st["notes"]
    if b:
        verdict = "VERDICT: NOT READY, first blocker: %s%s" % (b[0], (" (and %d more blocker(s))" % (len(b) - 1)) if len(b) > 1 else "")
    elif n:
        verdict = "VERDICT: READY, with %d note(s) above" % len(n)
    else:
        verdict = "VERDICT: READY"
    out(verdict)
    return {"blockers": b, "notes": n, "verdict": verdict}


def cmd_doctor(args):
    try:
        doctor(DoctorContext(config_path=args.config))
    except Exception as e:  # the report itself must never crash the caller
        say("")
        say("VERDICT: NOT READY, first blocker: the doctor crashed (%s: %s)" % (type(e).__name__, e))
    return 0


# ---------------------------------------------------------------- selftest

def _tree_signature(root):
    sig = {}
    for dirpath, dirnames, filenames in os.walk(str(root)):
        for name in sorted(dirnames) + sorted(filenames):
            p = os.path.join(dirpath, name)
            s = os.lstat(p)
            digest = sha256_file(p) if stat.S_ISREG(s.st_mode) else ""
            sig[os.path.relpath(p, str(root))] = (stat.S_IMODE(s.st_mode), s.st_size, s.st_mtime_ns, digest)
    return sig


def _leaf_keys(d, prefix=""):
    out = []
    for k, v in d.items():
        if k.startswith("_"):
            continue
        dotted = prefix + k
        if isinstance(v, dict) and dotted not in FREE_MAPS:
            out.extend(_leaf_keys(v, dotted + "."))
        else:
            out.append(dotted)
    return out


def _capture(fn, *a, **kw):
    import io
    buf, old = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        result = fn(*a, **kw)
    finally:
        sys.stdout = old
    return result, buf.getvalue()


def _fake_whatsapp(db_path, now):
    """A WhatsApp Desktop shaped database in WAL mode. The returned connection stays open so the recent rows
    are still in the write ahead log when the snapshot is taken."""
    w = sqlite3.connect(str(db_path))
    w.execute("PRAGMA journal_mode=WAL")
    w.execute("PRAGMA wal_autocheckpoint=0")
    w.executescript("""
        CREATE TABLE ZWACHATSESSION(Z_PK INTEGER PRIMARY KEY, ZCONTACTJID TEXT, ZPARTNERNAME TEXT, ZSESSIONTYPE INTEGER);
        CREATE TABLE ZWAMESSAGE(Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZMESSAGEDATE REAL, ZMESSAGETYPE INTEGER,
                                ZISFROMME INTEGER, ZTEXT TEXT);
    """)
    sessions = [(1, "60100000001@s.whatsapp.net", "Canary Person Alpha", 0), (2, "12345@lid", "Canary Person Beta", 0),
                (3, "111@g.us", "Canary Group Zeta", 1), (4, "222@g.us", "Quiet Group", 1),
                (5, "status@broadcast", "Status", 3), (6, "333@g.us", "Ignored Group", 1), (7, "111@g.us", "", 1)]
    w.executemany("INSERT INTO ZWACHATSESSION VALUES (?,?,?,?)", sessions)
    recent = (now - dt.timedelta(hours=1) - CORE_EPOCH_UTC).total_seconds()
    old = (now - dt.timedelta(days=5) - CORE_EPOCH_UTC).total_seconds()
    t = "canary-text-7781"
    msgs = [(1, recent, 0, 0), (1, recent, 0, 0), (1, recent, 0, 0), (1, recent, 0, 1), (1, recent, 6, 0), (1, old, 0, 0),
            (2, recent, 3, 0), (2, recent, 3, 0),
            (3, recent, 0, 0), (3, recent, 1, 0), (3, recent, 2, 0), (3, recent, 0, 0), (7, recent, 0, 0),
            (4, recent, 0, 0), (5, recent, 0, 0), (5, recent, 1, 0), (6, recent, 0, 0), (6, recent, 0, 0), (6, recent, 0, 0)]
    w.executemany("INSERT INTO ZWAMESSAGE(ZCHATSESSION, ZMESSAGEDATE, ZMESSAGETYPE, ZISFROMME, ZTEXT) VALUES (?,?,?,?,?)",
                  [(s, d, ty, me, t) for s, d, ty, me in msgs])
    w.commit()
    return w


def selftest():
    _SELFTEST["active"] = True
    shipped_reserved = list(RESERVED_LABEL_PREFIX_SHA256)
    tally = {"pass": 0, "fail": 0}

    def check(name, ok, detail=""):
        if ok:
            tally["pass"] += 1
            say("  ok    %s" % name)
        else:
            tally["fail"] += 1
            say("  FAIL  %s%s" % (name, (": %s" % (detail,)) if detail != "" else ""))

    say("wa_setup.py selftest (fake home, fake payload, fake WhatsApp database; launchctl, tailscale and claude never run)")
    if WC is None:
        say("  FAIL  wa_common imports: %s" % WC_ERROR)
        say("--- selftest FAIL (1 fail) ---")
        return 1
    base = Path(tempfile.mkdtemp(prefix="wa-setup-selftest-"))
    writer = None
    try:
        uhome = base / "user"
        agents = uhome / "Library" / "LaunchAgents"
        agents.mkdir(parents=True)
        payload = base / "payload"
        tdir = payload / TEMPLATES_REL
        tdir.mkdir(parents=True)
        real_t = PAYLOAD / TEMPLATES_REL
        for name in ("config.example.json", "topic-map.example.json", "daemon.plist", "run.mjs"):
            shutil.copyfile(str(real_t / name), str(tdir / name))
        for name in ("intake-evening.plist", "intake-evening.mjs"):
            if (real_t / name).is_file():
                shutil.copyfile(str(real_t / name), str(tdir / name))
        stamp = "<!-- seed: breakthrough-whatsapp-live %s v%s -->\n"
        for name in PROMPT_SEEDS:
            (tdir / name).write_text(stamp % (TEMPLATES_REL + "/" + name, "1.0.0") + "# fake %s for {OWNER_LABEL}\n" % name,
                                     encoding="utf-8")
        (tdir / "claude-settings.json").write_text(json.dumps({"disableAllHooks": True, "permissions": {
            "allow": ["Read", "Grep", "Glob"],
            "deny": ["Bash", "Read(/{LIVE_DIR}/telegram.json)", "Read(/{LIVE_DIR}/config.json)", "Read(/{HOME}/.ssh/**)"]}},
            indent=2), encoding="utf-8")
        for rel in ("daemon/wa_live.py", "daemon/wa_intake.py"):
            (payload / rel).write_text("# fake\n", encoding="utf-8")
        pb = payload / "playbooks" / "person-dossier"
        pb.mkdir(parents=True)
        for name in ("_Person-Dossier-Guide.md", "person-dossier-playbook.md"):
            (pb / name).write_text("---\ntype: playbook\n---\n" + stamp % ("playbooks/person-dossier/" + name, "1.0.0")
                                   + "# fake\n", encoding="utf-8")
        bindir = base / "bin"
        bindir.mkdir()
        for exe in ("node", "claude", "ffmpeg", "whisperkit-cli", "tailscale"):
            (bindir / exe).write_text("#!/bin/sh\necho v0.0.0-fake\n", encoding="utf-8")
            os.chmod(str(bindir / exe), 0o755)
        fake_which = lambda exe: str(bindir / exe) if (bindir / exe).is_file() else None
        models = base / "models" / "openai_whisper-large-v3-fake"
        models.mkdir(parents=True)
        (models / "weights.bin").write_bytes(b"0")
        wa = base / "wa"
        wa.mkdir()
        now = dt.datetime.now(dt.timezone.utc)
        writer = _fake_whatsapp(wa / "ChatStorage.sqlite", now)
        home = base / "WhatsApp-Live"
        live = home / "live"
        cfg_file = live / "config.json"
        init_kw = dict(payload=payload, which=fake_which, models_dir=base / "models", user_home=uhome)

        say("[init]")
        r1 = do_init(home, Report(quiet=True), **init_kw)
        check("init runs without errors", not r1.errors, r1.errors)
        check("every folder of the tree is 0700", all(mode_of(home / d) == 0o700 for d in ("",) + TREE_DIRS))
        raw = read_json_file(cfg_file, {})
        check("config.json is 0600, has the detected node and model, no _help",
              mode_of(cfg_file) == 0o600 and dget(raw, "bin.node") == str(bindir / "node")
              and dget(raw, "brain.whisper_model") == str(models) and "_help" not in raw, raw.get("bin"))
        check("prompt and topic map copies keep their seed stamps",
              read_stamp(live / "prompts" / "brain-prompt.md")[1] == "1.0.0" and read_stamp(live / "topic-map.json")[1] == "1.0.0")
        settings = (live / "claude-settings.json").read_text(encoding="utf-8")
        check("claude-settings.json rendered with absolute deny paths",
              ("Read(/%s/telegram.json)" % live) in settings and ("Read(/%s/.ssh/**)" % uhome) in settings
              and "{LIVE_DIR}" not in settings)
        seeds = load_seeds(live)
        check("seeds.json records four seeds and the rendered settings",
              len(seeds["seeds"]) == 4 and "claude-settings.json" in seeds["rendered"], sorted(seeds["seeds"]))
        before = _tree_signature(home)
        r2 = do_init(home, Report(quiet=True), **init_kw)
        check("a second init changes nothing and reports what it kept",
              _tree_signature(home) == before and not r2.done and len(r2.kept) >= 5, r2.done)
        r3 = do_init(base / "dry-home", Report(dry_run=True, quiet=True), **init_kw)
        check("init --dry-run prints a plan and writes nothing", r3.done and not (base / "dry-home").exists())
        r4 = do_init(uhome / "Documents" / "WA", Report(quiet=True), **init_kw)
        check("init refuses a home inside Documents", r4.errors and not (uhome / "Documents").exists())

        say("[check-config]")
        raw.update({"my_phone": "60100000009", "my_names": ["Mika Tan", "Mika"], "owner_label": "Mika"})
        raw["vocab"]["business_context"] = "An invented baking school."
        raw["doorbell"]["wal_path"] = str(wa / "ChatStorage.sqlite-wal")
        write_text_atomic(cfg_file, json_text(raw), 0o600)
        cfg = WC.load_config(str(cfg_file))
        issues = check_config(raw, cfg, payload, uhome)
        check("a clean config has no errors", not [i for i in issues if i[0] == "error"], issues)
        bad = copy.deepcopy(raw)
        bad["bogus_key"] = 1
        bad["notify"]["mode"] = "loud"
        bad["board"]["bind"] = "0.0.0.0"
        bad["digest"]["at"] = "8am"
        bad["group_policies"] = {"111@g.us": {"policy": "sometimes", "name": "x", "in30": 3, "defaulted": False}}
        bad["dossier"]["enabled"] = True
        bad["dossier"]["auto_open_worlds"] = ["nowhere"]
        bad["stage_rules"]["watch_stages"] = ["talking", "ghost"]
        bad_issues = check_config(bad, WC.apply_defaults(copy.deepcopy(bad), cfg_file), payload, uhome)
        bad_keys = set(k for level, k, m in bad_issues if level == "error")
        want = {"bogus_key", "notify.mode", "board.bind", "digest.at", "group_policies.111@g.us",
                "dossier.auto_open_worlds", "paths.vault", "paths.person_playbook"}
        check("check-config catches a bad key, bad values and missing station paths", want <= bad_keys, sorted(want - bad_keys))
        vocab_msgs = [(k, m) for level, k, m in bad_issues if level == "error" and "does not exist" in m]
        check("check-config names vocabulary keys that do not exist",
              ("dossier.auto_open_worlds", "world 'nowhere' does not exist in vocab.worlds") in vocab_msgs
              and any(k == "stage_rules.watch_stages" and "'ghost'" in m for k, m in vocab_msgs), vocab_msgs)
        csv_file = base / "sales.csv"
        csv_file.write_text("Phone,Name\n60100000009,Mika\n", encoding="utf-8")
        csv_cfg = copy.deepcopy(raw)
        csv_cfg["facts"].update({"enabled": True, "source": "csv"})
        csv_cfg["facts"]["csv"] = {"path": [str(csv_file), "https://example.com/published.csv"], "columns": {"phone": "Phone"}}
        csv_issues = [i for i in check_config(csv_cfg, WC.apply_defaults(copy.deepcopy(csv_cfg), cfg_file), payload, uhome)
                      if i[1].startswith("facts")]
        check("a CSV facts source with a list of paths and only a phone column passes",
              not [i for i in csv_issues if i[0] == "error"], csv_issues)
        bad_file = base / "bad-config" / "config.json"
        write_text_atomic(bad_file, json_text(bad), 0o600)
        code, _ = _capture(cmd_check_config, argparse.Namespace(config=str(bad_file)))
        check("check-config exits 1 when there are errors", code == 1, code)

        say("[render-launchd]")
        rr = do_render_launchd(cfg, cfg_file, Report(quiet=True), payload=payload, user_home=uhome)
        check("render-launchd runs without errors", not rr.errors, rr.errors)
        plist_path = home / "launchd" / "local.whatsapp-live.plist"
        try:
            pl = plistlib.loads(plist_path.read_bytes())
        except Exception as e:
            pl = {"error": str(e)}
        args_ = pl.get("ProgramArguments") or []
        check("the rendered plist is valid for plistlib and has the launchd shape",
              pl.get("Label") == "local.whatsapp-live" and args_[:2] == [str(bindir / "node"), str(home / "launchd" / "run.mjs")]
              and args_[2:] == [dget(cfg, "bin.python"), str(payload / "daemon" / "wa_live.py"), "--config", str(cfg_file)]
              and pl.get("KeepAlive") is True and pl.get("RunAtLoad") is True and pl.get("ThrottleInterval") == 10
              and pl.get("ExitTimeOut") == 90 and pl.get("StandardOutPath") == "/dev/null"
              and pl.get("StandardErrorPath") == str(live / "stderr.log")
              and pl.get("WorkingDirectory") == str(home / "launchd")
              and (pl.get("EnvironmentVariables") or {}).get("WA_LIVE_CONFIG") == str(cfg_file), pl)
        check("run.mjs is installed byte for byte",
              (home / "launchd" / "run.mjs").read_bytes() == (tdir / "run.mjs").read_bytes())
        rr2 = do_render_launchd(cfg, cfg_file, Report(quiet=True), payload=payload, user_home=uhome)
        check("a second render leaves both files unchanged", not rr2.done and not rr2.errors, rr2.done)
        if (tdir / "intake-evening.plist").is_file() and (tdir / "intake-evening.mjs").is_file():
            ri = do_render_launchd(cfg, cfg_file, Report(quiet=True), intake=True, payload=payload, user_home=uhome)
            ipl = {}
            try:
                ipl = plistlib.loads((home / "launchd" / "local.whatsapp-live.intake.plist").read_bytes())
            except Exception as e:
                ipl = {"error": str(e)}
            mjs_installed = home / "launchd" / "intake-evening.mjs"
            mjs_text = mjs_installed.read_text(encoding="utf-8") if mjs_installed.is_file() else ""
            check("render-launchd --intake renders a valid evening plist and fills the script's config and daemon paths",
                  not ri.errors and ipl.get("Label") == "local.whatsapp-live.intake"
                  and (ipl.get("ProgramArguments") or [])[:2] == [str(bindir / "node"), str(mjs_installed)]
                  and ipl.get("StartCalendarInterval") == {"Hour": 20, "Minute": 0} and ipl.get("RunAtLoad") is not True
                  and ("'%s'" % cfg_file) in mjs_text and ("'%s'" % (payload / "daemon")) in mjs_text
                  and "'{CONFIG}'" not in mjs_text and "'{DAEMON_DIR}'" not in mjs_text, ri.errors or ipl)
        else:
            say("  skip  render-launchd --intake (intake-evening templates are not in the payload yet)")

        say("[install and uninstall refusals]")
        calls = []

        def rec_runner(argv, timeout=30):
            calls.append([str(a) for a in argv])
            return 0, "", ""
        check("the shipped reserved prefix list holds SHA-256 hashes only",
              RESERVED_LABEL_PREFIX_SHA256 and all(re.fullmatch(r"[0-9a-f]{64}", h) for h in RESERVED_LABEL_PREFIX_SHA256))
        prefix = "selftest.reserved."   # stands in for the real reserved prefix, which only exists as a hash
        RESERVED_LABEL_PREFIX_SHA256.append(hashlib.sha256(prefix.encode("utf-8")).hexdigest())
        check("a label under a reserved prefix is recognised, a neighbour label is not",
              reserved_label(prefix + "whatsapp-live") and not reserved_label("selftest.reservedx.whatsapp-live")
              and not reserved_label("local.whatsapp-live"))
        cfg_r = copy.deepcopy(cfg)
        cfg_r["launchd"]["label"] = prefix + "whatsapp-live"
        a = do_install_launchd(cfg_r, Report(quiet=True), runner=rec_runner, launch_agents=agents)
        b = do_uninstall_launchd(cfg_r, Report(quiet=True), runner=rec_runner, launch_agents=agents)
        c = do_install_launchd(cfg_r, Report(dry_run=True, quiet=True), runner=rec_runner, launch_agents=agents)
        check("install and uninstall refuse the reserved label, even in a dry run, and run nothing",
              a.data.get("refused") and b.data.get("refused") and c.data.get("refused") and not calls)
        cfg_o = copy.deepcopy(cfg)
        cfg_o["launchd"]["label"] = "local.other"
        (home / "launchd" / "local.other.plist").write_bytes(plistlib.dumps({"Label": prefix + "x", "ProgramArguments": ["/bin/true"]}))
        d = do_install_launchd(cfg_o, Report(quiet=True), runner=rec_runner, launch_agents=agents)
        check("install refuses a rendered plist that carries the reserved label", d.data.get("refused") and not calls)
        e = do_install_launchd(cfg, Report(quiet=True), runner=rec_runner, launch_agents=agents)
        f = do_uninstall_launchd(cfg, Report(quiet=True), runner=rec_runner, launch_agents=agents)
        check("install and uninstall refuse to act inside the selftest",
              e.data.get("refused") and f.data.get("refused") and not calls and not list(agents.iterdir()))
        g = do_install_launchd(cfg, Report(dry_run=True, quiet=True), runner=rec_runner, launch_agents=agents)
        check("install-launchd --dry-run prints enable, bootstrap and print steps",
              not g.errors and any("bootstrap" in ln for ln in g.lines) and not calls, g.errors)
        try:
            default_runner([LAUNCHCTL, "print", "gui/0/none"])
            guarded = False
        except Refused:
            guarded = True
        check("the command runner itself refuses launchctl inside the selftest", guarded)

        say("[restart]")

        class FakeMac(object):
            """launchd, ps, lsof and kill for one daemon: node parent, python child holding the port, KeepAlive."""

            def __init__(self, cfg_path, port, frozen=False, orphan=False, foreign=False, keepalive=True):
                self.cfg_path, self.port, self.keepalive = str(cfg_path), port, keepalive
                self.procs = {}        # pid -> [ppid, command, frozen]
                self.next = 500
                self.listen = {}       # pid -> port
                self.node = None
                self.signals = []
                self.kickstarts = []
                self.respawn_in = None
                self._spawn(frozen)
                if orphan:
                    o = self._pid(); self.procs[o] = [1, self._pycmd(), True]
                    self.listen = {o: port}      # the orphan still holds the port, so the child never bound it
                if foreign:
                    f = self._pid(); self.procs[f] = [1, "/usr/bin/nc -l %d" % port, False]
                    self.listen = {f: port}

            def _pid(self):
                self.next += 1
                return self.next

            def _pycmd(self):
                # what ps really shows: /usr/bin/python3 runs as the framework's Python binary
                return ("/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/Resources/"
                        "Python.app/Contents/MacOS/Python /kit/daemon/wa_live.py --config " + self.cfg_path)

            def _spawn(self, frozen=False):
                # the node parent's argv names the same script and config (the rendered plist)
                self.node = self._pid()
                self.procs[self.node] = [1, "/usr/local/bin/node /home/launchd/run.mjs /usr/bin/python3 /kit/daemon/wa_live.py "
                                            "--config " + self.cfg_path, False]
                py = self._pid(); self.procs[py] = [self.node, self._pycmd(), frozen]
                self.listen[py] = self.port

            def tick(self):
                if self.respawn_in is not None:
                    self.respawn_in -= 1
                    if self.respawn_in <= 0:
                        self.respawn_in = None
                        self._spawn()

            def _gone(self, pid):
                self.procs.pop(pid, None); self.listen.pop(pid, None)
                if pid != self.node and self.node in self.procs and not any(v[0] == self.node for v in self.procs.values()):
                    self.procs.pop(self.node, None)          # run.mjs exits after its python exits
                    self.node = None
                    if self.keepalive:
                        self.respawn_in = 10                # ThrottleInterval

            def kill(self, pid, sig):
                if pid not in self.procs:
                    raise ProcessLookupError(pid)
                if sig == 0:
                    return
                self.signals.append((pid, int(sig)))
                if int(sig) == 9 or not self.procs[pid][2]:
                    self._gone(pid)

            def run(self, argv, timeout=30):
                argv = [str(a) for a in argv]
                self.tick()
                if argv[0] == LAUNCHCTL and argv[1] == "print":
                    return (0, "state = running\n\tpid = %d\n" % self.node if self.node else "state = waiting\n", "")
                if argv[0] == LAUNCHCTL and argv[1] == "kickstart":
                    self.kickstarts.append(argv[2:])
                    if self.node is None or "-k" in argv:
                        if self.node in self.procs:
                            self.procs.pop(self.node, None)
                        self._spawn()
                    return 0, "", ""
                if argv[0] == PS:
                    return 0, "\n".join("%d %d %s" % (k, v[0], v[1]) for k, v in sorted(self.procs.items())), ""
                if argv[0] == LSOF:
                    return 0, "\n".join(str(k) for k, v in self.listen.items() if v == self.port), ""
                return 1, "", "unexpected %s" % argv

        rcfg = copy.deepcopy(cfg)
        rcfg.setdefault("board", {})["port"] = 8799
        rcfg["board"]["enabled"] = True
        rpath = live / "config.json"

        def restart_on(mac, wait=5):
            return do_restart(rcfg, rpath, Report(quiet=True), runner=mac.run, kill=mac.kill, sleep=lambda s: mac.tick(),
                              wait_seconds=wait, launch_agents=agents, http_get=lambda url: (200, "ok (last scan 1s ago)"))

        m1 = FakeMac(rpath, 8799)
        old_node = m1.node
        r1 = restart_on(m1)
        check("restart: a healthy daemon gets one SIGTERM, launchd starts a fresh node, one python holds the port",
              not r1.errors and m1.signals == [(old_node + 1, 15)] and r1.data["new"]["node"] != old_node
              and list(m1.listen) == [r1.data["new"]["python"]] and not m1.kickstarts and r1.data["health"][0] == 200,
              (r1.errors, m1.signals, r1.data))
        m2 = FakeMac(rpath, 8799, frozen=True)
        r2 = restart_on(m2)
        check("restart: a frozen python that ignores SIGTERM is SIGKILLed after the wait, then the proof holds",
              not r2.errors and [sg for _, sg in m2.signals] == [15, 9] and r2.data.get("killed") and r2.data.get("new"),
              (r2.errors, m2.signals))
        m3 = FakeMac(rpath, 8799, orphan=True)
        r3 = restart_on(m3)
        check("restart: an orphan python of this config holding the port is stopped too, and only the new one listens",
              not r3.errors and len(r3.data.get("stopped") or []) == 2 and len(m3.listen) == 1
              and list(m3.listen)[0] == r3.data["new"]["python"], (r3.errors, r3.data, m3.listen))
        m4 = FakeMac(rpath, 8799, foreign=True)
        r4 = restart_on(m4)
        check("restart: a port held by something that is not this daemon is refused before any signal",
              r4.errors and "not this daemon" in r4.errors[0] and r4.data.get("refused") and not m4.signals, r4.errors)
        m5 = FakeMac(rpath, 8799, keepalive=False)
        r5 = restart_on(m5)
        check("restart: when launchd does not start it again by itself, one kickstart (without -k) does",
              not r5.errors and m5.kickstarts == [["gui/%d/%s" % (os.getuid(), label_for(rcfg, False))]], (r5.errors, m5.kickstarts))
        m9 = FakeMac(rpath, 8799, frozen=True, orphan=True)
        st9, kl9, ok9 = stop_daemon_pythons(rcfg, rpath, Report(quiet=True), m9.run, m9.kill, lambda s: m9.tick(), 5, m9.node)
        check("stop step shared by restart, install-launchd and uninstall-launchd: child and orphan stopped, the frozen one killed",
              ok9 and len(st9) == 2 and kl9 and not any(k for k, v in m9.procs.items() if "wa_live.py" in v[1]), (st9, kl9, m9.procs))
        isrc, usrc = inspect.getsource(do_install_launchd), inspect.getsource(do_uninstall_launchd)
        check("install-launchd and uninstall-launchd boot out first, then stop any python left behind (daemon only)",
              isrc.index('"bootout", target') < isrc.index("stop_daemon_pythons(")
              and usrc.index('"bootout", target') < usrc.index("stop_daemon_pythons("))
        check("only the python running wa_live.py counts as the daemon, never its node parent or a one shot command",
              is_daemon_python(m1._pycmd()) and is_daemon_python("/usr/bin/python3 /k/wa_live.py --config /c")
              and not is_daemon_python("/usr/local/bin/node /h/run.mjs /usr/bin/python3 /k/wa_live.py --config /c")
              and not is_daemon_python("/usr/bin/python3 /k/wa_live.py --status --config /c")
              and not is_daemon_python("grep wa_live.py"))
        r6 = do_restart(rcfg, rpath, Report(dry_run=True, quiet=True), launch_agents=agents)
        r7 = do_restart(rcfg, rpath, Report(quiet=True), launch_agents=agents)
        rcfg_r = copy.deepcopy(rcfg); rcfg_r["launchd"]["label"] = prefix + "whatsapp-live"
        m8 = FakeMac(rpath, 8799)
        r8 = do_restart(rcfg_r, rpath, Report(quiet=True), runner=m8.run, kill=m8.kill, launch_agents=agents)
        check("restart: --dry-run prints the SIGKILL step and runs nothing; the selftest never reaches launchctl; reserved label refused",
              not r6.errors and any("SIGKILL" in ln for ln in r6.lines) and r7.data.get("refused")
              and r8.data.get("refused") and not m8.signals, (r6.errors, r7.errors, r8.errors))

        say("[seed-status and copy-seed]")
        rows = dict((r["key"], r) for r in seed_rows(cfg, payload))
        check("fresh owner copies read unchanged",
              rows["prompts/brain-prompt.md"]["status"] == "unchanged" and rows["topic-map.json"]["status"] == "unchanged"
              and rows["claude-settings.json"]["status"] == "unchanged", dict((k, v["status"]) for k, v in rows.items()))
        with open(str(live / "prompts" / "brain-prompt.md"), "a", encoding="utf-8") as fh:
            fh.write("\nOne rule of my own.\n")
        (tdir / "backfill-prompt.md").write_text(stamp % (TEMPLATES_REL + "/backfill-prompt.md", "1.1.0") + "# changed\n",
                                                 encoding="utf-8")
        rows = dict((r["key"], r) for r in seed_rows(cfg, payload))
        check("seed-status detects an owner edit", rows["prompts/brain-prompt.md"]["status"] == "edited by owner",
              rows["prompts/brain-prompt.md"]["status"])
        check("seed-status detects a newer payload seed", rows["prompts/backfill-prompt.md"]["status"] == "seed newer",
              rows["prompts/backfill-prompt.md"]["status"])
        vault_pb = base / "vault" / "Playbooks" / "person-dossier"
        c1 = do_copy_seed(cfg, "playbooks/person-dossier", vault_pb, Report(quiet=True), payload)
        c2 = do_copy_seed(cfg, "playbooks/person-dossier", vault_pb, Report(quiet=True), payload)
        c3 = do_copy_seed(cfg, "../outside", vault_pb, Report(quiet=True), payload)
        check("copy-seed copies a playbook folder once and never overwrites",
              not c1.errors and len([x for x in c1.done if x[0] == "copy"]) == 2 and not [x for x in c2.done if x[0] == "copy"]
              and len(c2.kept) == 2 and c3.errors, (c1.errors, c1.done, c2.done))
        pb_files = [p for p in vault_pb.rglob("*") if p.is_file()]
        check("copy-seed makes vault folders 0755 (Playbooks and the playbook folder) and files 0644",
              mode_of(vault_pb) == 0o755 and mode_of(vault_pb.parent) == 0o755 and pb_files
              and all(mode_of(p) == 0o644 for p in pb_files), (oct(mode_of(vault_pb) or 0), oct(mode_of(vault_pb.parent) or 0)))
        rows = dict((r["key"], r) for r in seed_rows(cfg, payload))
        vault_rows = [r for r in rows.values() if r["copy"].endswith("person-dossier-playbook.md")]
        check("seed-status lists the vault copy", vault_rows and vault_rows[0]["status"] == "unchanged",
              [r["status"] for r in vault_rows])

        say("[measure]")
        raw["ignored_jids"] = ["333@g.us"]
        raw["group_policies"] = {"111@g.us": {"policy": "all", "name": "Canary Group Zeta", "in30": 40, "defaulted": False}}
        write_text_atomic(cfg_file, json_text(raw), 0o600)
        cfg = WC.load_config(str(cfg_file))
        tmp_before = set(Path(tempfile.gettempdir()).glob("wa-measure-*"))
        m = do_measure(Report(quiet=True), days=3, top=15, cfg=cfg, wa_root=wa, now=now)
        res = m.data.get("result") or {}
        check("measure runs on a snapshot of a WAL mode database", not m.errors and res, m.errors)
        check("measure counts one to one and group inbound content messages",
              res.get("direct") == {"inbound": 5, "chats": 2} and res.get("groups") == {"inbound": 6, "chats": 2}, res)
        top = res.get("top_groups") or [{}]
        check("measure ranks groups, sums duplicate sessions and shows the policy",
              top[0].get("name") == "Canary Group Zeta" and top[0].get("inbound") == 5 and top[0].get("policy") == "all"
              and top[-1].get("policy") == "unlisted (skip)", top)
        check("measure skips status, broadcast and ignored chats",
              res.get("skipped") == {"status_or_broadcast": 2, "ignored": 3}, res.get("skipped"))
        check("measure counts content types only", sum((res.get("by_type") or {}).values()) == 11, res.get("by_type"))
        mtext = m.text()
        check("measure prints group names and counts, never message text or person names",
              "Canary Group Zeta" in mtext and "canary-text-7781" not in mtext and "Canary Person" not in mtext)
        check("measure deletes its snapshot", set(Path(tempfile.gettempdir()).glob("wa-measure-*")) == tmp_before)
        m2 = do_measure(Report(quiet=True), days=3, cfg=cfg, wa_root=base / "no-whatsapp", now=now)
        check("measure reports a missing database instead of crashing", m2.errors)

        say("[doctor]")
        dcalls = []

        def doc_runner(argv, timeout=30):
            argv = [str(x) for x in argv]
            dcalls.append(argv)
            name = os.path.basename(argv[0])
            if name in ("launchctl", "claude", "lark-cli"):
                return 1, "", "not allowed"
            if name == "pgrep":
                return 0, "123\n", ""
            if name == "tailscale":
                return 0, "http://fake-host.example.ts.net (tailnet only)\n|-- / proxy http://127.0.0.1:8787\n", ""
            return 0, "v0.0.0-fake\n", ""
        ctx = DoctorContext(config_path=cfg_file, wa_root=wa, runner=doc_runner, which=fake_which, user_home=uhome,
                            launch_agents=agents, payload=payload, tailscale_app=str(base / "no-app"))
        lines = []
        dres = doctor(ctx, out=lines.append)
        dtext = "\n".join(lines)
        check("doctor ends with exactly one verdict line",
              lines and lines[-1].startswith("VERDICT: ") and sum(1 for ln in lines if ln.startswith("VERDICT")) == 1, lines[-1:])
        check("doctor reads the fake database: present, fresh, readable",
              "readable by this process: yes" in dtext and "message table readable: yes" in dtext, dtext)
        check("doctor prints no message text, names, number or tailnet host",
              not any(s in dtext for s in ("canary-text-7781", "Canary", "Mika", "60100000009", "fake-host")))
        check("doctor never runs launchctl or claude",
              not any(os.path.basename(c[0]) in ("launchctl", "claude") for c in dcalls), dcalls)
        check("doctor reads tailscale serve status and sees the board port",
              any(c[1:3] == ["serve", "status"] for c in dcalls) and "board port 8787 served: yes" in dtext)
        denied = base / "wa-denied"
        denied.mkdir()
        (denied / "ChatStorage.sqlite").write_bytes(b"SQLite format 3\x00" + b"\x00" * 84)
        os.chmod(str(denied / "ChatStorage.sqlite"), 0)
        lines2 = []
        try:
            dres2 = doctor(DoctorContext(config_path=cfg_file, wa_root=denied, runner=doc_runner, which=fake_which,
                                         user_home=uhome, launch_agents=agents, payload=payload,
                                         tailscale_app=str(base / "no-app")), out=lines2.append)
        finally:
            os.chmod(str(denied / "ChatStorage.sqlite"), 0o600)
        check("doctor names Full Disk Access as the blocker when the database cannot be read",
              dres2["blockers"] and "Full Disk Access" in "\n".join(lines2) and lines2[-1].startswith("VERDICT: NOT READY"),
              lines2[-3:])
        lines3 = []
        doctor(DoctorContext(config_path=base / "nowhere" / "config.json", wa_root=base / "no-whatsapp", runner=doc_runner,
                             which=fake_which, user_home=uhome, launch_agents=agents, payload=payload,
                             tailscale_app=str(base / "no-app")), out=lines3.append)
        check("doctor still reports when there is no config and no WhatsApp", lines3[-1].startswith("VERDICT: NOT READY"), lines3[-1:])
        broken = base / "broken" / "live" / "config.json"
        write_text_atomic(broken, '{"my_phone": "60100000009",,}', 0o600)
        lines4 = []
        dres4 = doctor(DoctorContext(config_path=broken, wa_root=wa, runner=doc_runner, which=fake_which, user_home=uhome,
                                     launch_agents=agents, payload=payload, tailscale_app=str(base / "no-app")),
                       out=lines4.append)
        check("doctor turns an unparseable config into a blocker without printing its content",
              any("not valid JSON" in x for x in dres4["blockers"]) and "60100000009" not in "\n".join(lines4), dres4["blockers"])
        code, out_text = _capture(cmd_init, argparse.Namespace(home=HOME_DEFAULT, config=str(base / "cfg-home" / "live" / "config.json"),
                                                               dry_run=True))
        check("init --config alone names the home", code == 0 and ("would create %s" % tilde(base / "cfg-home")) in out_text
              and not (base / "cfg-home").exists(), out_text[-300:])

        say("[sync-reader-config]")
        people = base / "vault" / "People"
        people.mkdir(parents=True)
        raw["paths"]["vault"] = str(base / "vault")
        raw["paths"]["people_dir"] = str(people)
        write_text_atomic(cfg_file, json_text(raw), 0o600)
        cfg = WC.load_config(str(cfg_file))
        work = home / "runs-work" / "abc"
        work.mkdir()
        (work / "config.json").write_text(json.dumps({"last_sweep_at": "2026-01-01T00:00:00", "my_names": ["old"]}),
                                          encoding="utf-8")
        s1 = do_sync_reader_config(cfg, cfg_file, Report(quiet=True))
        hc = read_json_file(home / "config.json", {})
        wc = read_json_file(work / "config.json", {})
        check("sync-reader-config writes <home>/config.json 0600 with the reader keys",
              not s1.errors and mode_of(home / "config.json") == 0o600 and hc.get("my_names") == ["Mika Tan", "Mika"]
              and "mika" in hc.get("stop_words", []) and hc.get("days") == 3 and hc.get("person_dirs") == [str(people)]
              and hc.get("whisperkit_bin") == str(bindir / "whisperkit-cli"), hc)
        check("sync-reader-config refreshes runs-work copies and keeps the reader's own keys",
              wc.get("my_names") == ["Mika Tan", "Mika"] and wc.get("last_sweep_at") == "2026-01-01T00:00:00", wc)
        s2 = do_sync_reader_config(cfg, cfg_file, Report(quiet=True))
        check("a second sync changes nothing", not s2.done, s2.done)

        say("[tailscale-hint]")
        hint = tailscale_hint_lines(cfg)
        check("tailscale-hint prints the serve command for the board port",
              "  tailscale serve --bg --http=80 http://127.0.0.1:8787" in hint and any("funnel" in ln for ln in hint))
        hint2 = tailscale_hint_lines(cfg, 8443, True)
        check("tailscale-hint handles https on another tailnet port",
              "  tailscale serve --bg --https=8443 http://127.0.0.1:8787" in hint2 and any(".ts.net:8443/" in ln for ln in hint2))
        other = {"TCP": {"80": {"HTTP": True}, "8443": {"HTTP": True}},
                 "Web": {"mac.example.ts.net:80": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8797"}}},
                         "mac.example.ts.net:8443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:3000"}}}}}
        hint3 = tailscale_hint_lines(cfg, status=other)
        check("tailscale-hint never prints a command that replaces another handler (port 80 and 8443 taken, 8444 used)",
              "  tailscale serve --bg --http=8444 http://127.0.0.1:8787" in hint3 and not any("--http=80 " in ln for ln in hint3)
              and any(ln.startswith("WARNING: tailnet port 80 already serves") for ln in hint3), hint3)
        mine = {"TCP": {"80": {"HTTP": True}}, "Web": {"mac.example.ts.net:80": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8787"}}}}}
        hint4 = tailscale_hint_lines(cfg, status=mine)
        check("tailscale-hint says the board is served already and prints no serve command",
              "already on your tailnet" in hint4[0] and not any("tailscale serve --bg" in ln for ln in hint4), hint4)
        hint5 = tailscale_hint_lines(cfg, status=None, status_why="tailscale is not installed")
        check("tailscale-hint warns when the serve status could not be read",
              any(ln.startswith("WARNING: could not read tailscale serve status") for ln in hint5), hint5)
        hint6 = tailscale_hint_lines(cfg, status={})
        check("tailscale-hint with nothing served prints the plain port 80 command",
              "  tailscale serve --bg --http=80 http://127.0.0.1:8787" in hint6 and not any(ln.startswith("WARNING") for ln in hint6), hint6)
        fake_ts = lambda args, timeout=10: (0, json.dumps(other), "")
        st_read, _why = read_serve_status(runner=fake_ts, which=lambda n: "/usr/local/bin/tailscale")
        check("read_serve_status parses the JSON and serve_ports_in_use names both ports",
              sorted(serve_ports_in_use(st_read)) == [80, 8443], st_read)

        say("[shipped templates]")
        ex = json.loads((real_t / "config.example.json").read_text(encoding="utf-8"))
        help_keys = set(ex.get("_help") or {}) - {"_about"}
        leaves = set(_leaf_keys(dict((k, v) for k, v in ex.items() if k != "_help")))
        check("every config.example.json key has a _help line", not (leaves - help_keys), sorted(leaves - help_keys))
        check("every _help line names a real key", not (help_keys - leaves), sorted(help_keys - leaves))
        extra = sorted(set(_leaf_keys(getattr(WC, "DEFAULTS", {}) or {})) - leaves)
        if extra:
            say("  note  keys wa_common fills that config.example.json does not list: %s" % ", ".join(extra))
        tm = read_json_file(real_t / "topic-map.example.json", {})
        check("topic-map.example.json parses, carries a seed stamp and keys/files rules",
              read_stamp(real_t / "topic-map.example.json")[1] and tm.get("rules")
              and all(set(r) == {"keys", "files"} for r in tm["rules"]))
        known = {"{LABEL}", "{NODE}", "{RUN_MJS}", "{PYTHON}", "{WA_LIVE}", "{CONFIG}", "{WORKDIR}", "{STDERR_LOG}", "{PATH_ENV}"}
        used = set(PLACEHOLDER_RE.findall((real_t / "daemon.plist").read_text(encoding="utf-8")))
        check("daemon.plist uses exactly the agreed placeholders", used == known, sorted(used ^ known))

        say("[shipped pages]")
        hits = default_literals_in_runnable_blocks(PAYLOAD)
        check("no runnable block in SKILL.md, install/ or references/ hard-codes the default home, label or port "
              "(a second install would copy it and touch the first one)", not hits, hits[:12])
        kicks = daemon_kickstart_lines(PAYLOAD)
        check("no doc or seed restarts the daemon with launchctl kickstart -k (wa_setup.py restart does it)", not kicks, kicks[:12])
    except Exception as e:
        import traceback
        tally["fail"] += 1
        say("  FAIL  selftest crashed: %s: %s" % (type(e).__name__, e))
        for ln in traceback.format_exc().splitlines():
            say("        " + ln)
    finally:
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass
        shutil.rmtree(str(base), ignore_errors=True)
        RESERVED_LABEL_PREFIX_SHA256[:] = shipped_reserved
        _SELFTEST["active"] = False
    say("--- selftest %s (%d pass, %d fail) ---" % ("PASS" if not tally["fail"] else "FAIL", tally["pass"], tally["fail"]))
    return 0 if not tally["fail"] else 1


# ---------------------------------------------------------------- command line

def _config_arg(args):
    return expand(args.config) if args.config else default_config_path()


def cmd_init(args):
    home = args.home
    for given in (home, args.config):
        # an unfilled placeholder ("<paths.home ...>") or a relative path would quietly create a stray folder here
        if given and ("<" in str(given) or ">" in str(given) or not Path(os.path.expanduser(str(given))).is_absolute()):
            say("init: refused: %r is not an absolute path with every placeholder filled in" % str(given))
            return 1
    if args.config and home == HOME_DEFAULT and expand(args.config).parent.name == "live":
        home = str(expand(args.config).absolute().parent.parent)   # --config alone names the home too
    rep = do_init(home, Report(dry_run=args.dry_run), config_path=args.config)
    return 1 if rep.errors else 0


def cmd_render_launchd(args):
    path = _config_arg(args)
    rep = do_render_launchd(load_cfg(path), path, Report(dry_run=args.dry_run), intake=args.intake)
    return 1 if rep.errors else 0


def cmd_install_launchd(args):
    rep = do_install_launchd(load_cfg(_config_arg(args)), Report(dry_run=args.dry_run), intake=args.intake)
    return 3 if rep.data.get("refused") else (1 if rep.errors else 0)


def cmd_uninstall_launchd(args):
    rep = do_uninstall_launchd(load_cfg(_config_arg(args)), Report(dry_run=args.dry_run), intake=args.intake)
    return 3 if rep.data.get("refused") else (1 if rep.errors else 0)


def cmd_restart(args):
    path = _config_arg(args)
    rep = do_restart(load_cfg(path), path, Report(dry_run=args.dry_run), wait_seconds=args.wait)
    return 3 if rep.data.get("refused") else (1 if rep.errors else 0)


def cmd_sync_reader_config(args):
    path = _config_arg(args)
    rep = do_sync_reader_config(load_cfg(path), path, Report(dry_run=args.dry_run))
    return 1 if rep.errors else 0


def cmd_copy_seed(args):
    rep = do_copy_seed(load_cfg(_config_arg(args)), args.seed, args.to, Report(dry_run=args.dry_run), confirmed=args.confirmed_by_owner)
    return 1 if rep.errors else 0


def cmd_measure(args):
    cfg = {}
    path = _config_arg(args)
    if path.is_file() and WC is not None:
        try:
            cfg = load_cfg(path)
        except SetupError as e:
            say("measure: config not used (%s)" % e)
    rep = do_measure(Report(quiet=args.json), args.days, args.top, cfg, args.wa_root)
    if args.json:
        say(json.dumps(rep.data.get("result") or {"errors": rep.errors}, ensure_ascii=False, indent=1))
    return 1 if rep.errors else 0


DISPATCH = {
    "init": cmd_init, "doctor": cmd_doctor, "check-config": cmd_check_config, "render-launchd": cmd_render_launchd,
    "install-launchd": cmd_install_launchd, "uninstall-launchd": cmd_uninstall_launchd, "restart": cmd_restart,
    "sync-reader-config": cmd_sync_reader_config, "seed-status": cmd_seed_status, "copy-seed": cmd_copy_seed,
    "measure": cmd_measure, "tailscale-hint": cmd_tailscale_hint,
}


def build_parser():
    p = argparse.ArgumentParser(
        prog="wa_setup.py", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Install and maintenance helper for WhatsApp Live. The install wizard stations call it; "
                    "it renders, checks and reports, and never sends anything.",
        epilog="Usual order: doctor and measure (C0), init and sync-reader-config (C1), check-config, "
               "render-launchd and install-launchd (C2), tailscale-hint (C3), seed-status after an update.\n"
               "Exit codes: 0 fine, 1 errors found, 2 cannot start (config missing or unreadable), 3 refused.")
    p.add_argument("--selftest", action="store_true", help="run every command against fakes in a temp folder")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    def add(name, text, writes=False):
        sp = sub.add_parser(name, help=text, description=text)
        sp.add_argument("--config", metavar="PATH",
                        help="live config (default: env WA_LIVE_CONFIG, else ~/WhatsApp-Live/live/config.json)")
        if writes:
            sp.add_argument("--dry-run", action="store_true", help="print the plan and write nothing")
        return sp

    sp = add("init", "create the working tree, config, prompt and topic map copies, claude settings, seeds.json; "
                     "never overwrites", writes=True)
    sp.add_argument("--home", default=HOME_DEFAULT, metavar="DIR",
                    help="paths.home (default %s; a --config ending in <home>/live/config.json also names it)" % HOME_DEFAULT)
    add("doctor", "read only prerequisite report with one verdict line; always exits 0")
    add("check-config", "unknown keys, bad values, unknown vocabulary keys, enabled stations missing paths")
    for name, text in (("render-launchd", "write <home>/launchd/run.mjs and <home>/launchd/<label>.plist"),
                       ("install-launchd", "copy the rendered plist to ~/Library/LaunchAgents, enable, bootstrap"),
                       ("uninstall-launchd", "bootout, disable, remove the plist from ~/Library/LaunchAgents")):
        sp = add(name, text, writes=True)
        sp.add_argument("--intake", action="store_true", help="the evening intake job instead of the daemon")
    sp = add("restart", "restart the daemon: SIGTERM its python, SIGKILL it after ExitTimeOut if frozen, let launchd "
                        "start it again, prove one python holds the board port (never kickstart -k)", writes=True)
    sp.add_argument("--wait", type=int, default=None, metavar="S",
                    help="seconds to wait after SIGTERM before SIGKILL (default: ExitTimeOut from the installed plist, 90)")
    add("sync-reader-config", "write <home>/config.json for the reader and refresh <home>/runs-work/*/config.json", writes=True)
    sp = add("seed-status", "owner copies against live/seeds.json and the payload seeds")
    sp.add_argument("--json", action="store_true", help="machine readable rows")
    sp = add("copy-seed", "copy a payload seed file or folder to its owner location and record it", writes=True)
    sp.add_argument("--seed", required=True, metavar="REL", help="path inside the payload, e.g. playbooks/person-dossier")
    sp.add_argument("--to", required=True, metavar="PATH", help="destination file or folder")
    sp.add_argument("--confirmed-by-owner", action="store_true", help="the owner said yes: write confirmed_by_owner true and today's updated into the copies' frontmatter")
    sp = add("measure", "inbound traffic from a snapshot copy of the WhatsApp database (counts and group names only)")
    sp.add_argument("--days", type=int, default=3, help="window in days (default 3)")
    sp.add_argument("--top", type=int, default=15, help="groups shown in the text report, 0 for all (default 15)")
    sp.add_argument("--json", action="store_true", help="machine readable, every group with its jid (for group_policies)")
    sp.add_argument("--wa-root", metavar="DIR", help="WhatsApp group container folder (default: WhatsApp Desktop's)")
    sp = add("tailscale-hint", "print the tailscale serve command and address for the board port; runs nothing")
    sp.add_argument("--serve-port", type=int, default=None, metavar="N", help="tailnet port (default 80, or 443 with --https)")
    sp.add_argument("--https", action="store_true", help="serve over https (needs HTTPS certificates on the tailnet)")
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.command:
        parser.print_help()
        return 2
    try:
        return DISPATCH[args.command](args)
    except SetupError as e:
        say("%s: %s" % (args.command, e))
        return 2
    except Refused as e:
        say("%s: refused: %s" % (args.command, e))
        return 3


if __name__ == "__main__":
    sys.exit(main())
