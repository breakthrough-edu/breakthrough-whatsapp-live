#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dossier_core.py · the shared core of person notes (breakthrough-whatsapp-live, companion skill breakthrough-person-dossier)

Frontmatter rules, the checks a candidate note must pass, the stage ruling and the safe placement, in one module both
writers import: the daemon's note run (daemon/wa_live.py, class Dossier) and this skill's CLI (dossier.py). Change a
rule here and both follow; never copy a rule into a caller.

Where every rule comes from (config first; nothing is found by walking up from this folder):
  paths.doctrine         the owner's vault law (default <vault>/99_Meta/structure-doctrine.md). Its `person:` family
                         gives the required and optional frontmatter keys, the closed lists (roles, stage) and one
                         meaning per role, read from comment lines shaped `#   <role>  <meaning>`.
  paths.person_playbook  the owner's vault copy of the person dossier playbook. The `## ` headings inside the first
                         fence after its skeleton heading are the sections every note must have; a heading whose note
                         says OPTIONAL_MARK may be left out. When the owner copy is missing, the payload seed is used
                         and the log says so.
  person.section_roles   the names code relies on: gaps, notes, owner_notes, recent, facts (section headings),
                         next_message (a label inside the talking section) and privacy_hint (text). Every non-empty
                         heading must exist in the playbook skeleton or the checks refuse loudly, so renaming a section
                         in the playbook can never silently switch off the guard behind it. "" turns a guard off on purpose.
  owner_marker           2 to 6 ASCII letters: `🗣 <MARKER> <date>` tags and `<!-- <marker> -->` lock segments. The seed
                         playbook writes the literal placeholder `<marker>`; every reader here substitutes the configured
                         marker for it (playbook text and its section names, section roles, the prompt, candidate notes).
  vocab.stages, vocab.park_from, person.roles_with_stage, person.paid_stages
                         the forward-only stage ruling.
  paths.people_dir, paths.filing_log, dossier.backups_keep
                         where notes are placed, the one filing line per placement, how many backups stay.

CLI
  dossier_core.py --selftest                  fake doctrine, fake playbook, fake notes, temp dirs only
  dossier_core.py --check-live [--config P]   read the owner's real doctrine, playbook and people folder and report what
                                              the checks will use (key, role and section names, counts; no note content)

Python 3.9 standard library only.
"""

import contextlib
import datetime as dt
import fcntl
import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

VERSION = "1.0.1"
_HERE = Path(__file__).resolve()
PAYLOAD = _HERE.parents[3] if len(_HERE.parents) > 3 else _HERE.parent
SEED_PLAYBOOK = PAYLOAD / "playbooks" / "person-dossier" / "person-dossier-playbook.md"
DEFAULT_CONFIG = "~/WhatsApp-Live/live/config.json"

FACTS_BEGIN, FACTS_END = "<!-- facts:begin -->", "<!-- facts:end -->"
RECENT_BEGIN, RECENT_END = "<!-- wa-live:begin", "<!-- wa-live:end -->"
SKELETON_HEADING = "骨架"            # the playbook heading whose first fence holds the note skeleton
OPTIONAL_MARK = "这节可以没有"        # written under a skeleton heading: that section may be absent
DEFAULT_SECTION_ROLES = {
    "gaps": "## ⬜ 下次见面要问的",
    "notes": "## 这份档的备注",
    "owner_notes": "## <marker> 手记",
    "recent": "## WhatsApp 近况",
    "facts": "## 系统记录",
    "next_message": "下一封怎么开口",
    "privacy_hint": "私事",
}
HEADING_ROLES = ("gaps", "notes", "owner_notes", "recent", "facts")
DEFAULT_STAGE_KEYS = ("new", "talking", "offered", "won", "active", "done", "parked")
DEFAULT_PARK_FROM = ("new", "talking", "offered")
DEFAULT_ROLES_WITH_STAGE = ("lead", "member", "client")
DEFAULT_PAID_STAGES = ("won", "active", "done")
PARKED = "parked"
HARMLESS_EXTRA = ("tags", "aliases", "email", "phone", "company", "title")
DOCTRINE_RESERVED = ("required", "optional", "marker", "multi", "extends", "cardinality")
WARN_MIN_LINES, WARN_MAX_LINES = 35, 250
PACK_LIMIT, CONTEXT_LIMIT = 80000, 30000
DEFAULT_BACKUPS_KEEP = 3

# Names older callers import. They are fallbacks only: the live values come from config and the owner's doctrine.
DOSSIER_KEYS = ("type", "roles", "roles_remark", "stage", "stage_since", "last_contact", "since", "wa_jid", "wa_phone",
                "dossier_updated")
ROLES = ("lead", "client", "partner", "vendor", "network", "team")
ROLES_WITH_STAGE = DEFAULT_ROLES_WITH_STAGE
PAID_STAGES = DEFAULT_PAID_STAGES


class DossierError(Exception):
    pass


# ---------------------------------------------------------------- config and paths

_CFG_CACHE = {"key": None, "cfg": None}


def _wa_common():
    """daemon/wa_common.py of this payload, or None when it cannot be imported."""
    d = PAYLOAD / "daemon"
    if not (d / "wa_common.py").is_file():
        return None
    try:
        if str(d) not in sys.path:
            sys.path.append(str(d))
        import wa_common  # noqa: E402
        return wa_common
    except Exception:
        return None


def _initials(label):
    """Same rule as wa_common: initials of a multi-word label, first two letters of one word, else ME."""
    words = re.findall(r"[A-Za-z]+", str(label or ""))
    m = "".join(w[0] for w in words)[:6] if len(words) >= 2 else (words[0][:2] if words else "")
    m = m.upper()
    return m if len(m) >= 2 else "ME"


def _fallback_load(p):
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise DossierError("cannot read config %s: %s" % (p, e))
    if not isinstance(raw, dict):
        raise DossierError("config must be a JSON object: %s" % p)
    paths = raw.setdefault("paths", {})
    home = str(paths.get("home") or "")
    if not home or home.startswith("<"):
        home = str(p.parent.parent) if p.parent.name == "live" else os.path.expanduser("~/WhatsApp-Live")
    paths["home"] = os.path.expanduser(home)
    live = str(paths.get("live_dir") or "")
    paths["live_dir"] = os.path.expanduser(live.replace("<home>", paths["home"])) if live and "<" not in live.replace("<home>", "") else str(p.parent)
    raw.setdefault("live_dir", paths["live_dir"])
    raw.setdefault("config_path", str(p))
    if not re.fullmatch(r"[A-Za-z]{2,6}", str(raw.get("owner_marker") or "").strip()):
        raw["owner_marker"] = _initials(raw.get("owner_label"))
    return raw


def load_cfg(path=None):
    """The live config through wa_common.load_config (the same defaults every module sees); a minimal reader is used
    only when that module cannot be imported. Path order: the argument, $WA_LIVE_CONFIG, ~/WhatsApp-Live/live/config.json."""
    p = Path(os.path.expanduser(str(path or os.environ.get("WA_LIVE_CONFIG") or DEFAULT_CONFIG)))
    try:
        st = p.stat()
    except OSError:
        raise DossierError("config not found: %s (pass --config or set WA_LIVE_CONFIG)" % p)
    key = (str(p), st.st_mtime, st.st_size)
    if _CFG_CACHE["key"] == key:
        return _CFG_CACHE["cfg"]
    wc = _wa_common()
    if wc is not None:
        try:
            cfg = wc.load_config(str(p))
        except (OSError, ValueError) as e:
            raise DossierError(str(e))
    else:
        cfg = _fallback_load(p)
    _CFG_CACHE.update(key=key, cfg=cfg)
    return cfg


def _cfg(cfg):
    return cfg if cfg is not None else load_cfg()


def _paths(cfg):
    p = (cfg or {}).get("paths")
    return p if isinstance(p, dict) else {}


def _expand(value, cfg):
    """A config path with `~` and the placeholders the config itself defines resolved; None when a placeholder has no
    value in this config (never a guessed default under the home folder)."""
    s = str(value or "").strip()
    if not s:
        return None
    paths = _paths(cfg)
    subs = (("<home>", paths.get("home")), ("<live>", paths.get("live_dir") or (cfg or {}).get("live_dir")),
            ("<vault>", paths.get("vault")), ("<payload>", str(PAYLOAD)))
    for tok, val in subs:
        if tok in s:
            if not val or "<" in str(val):
                return None
            s = s.replace(tok, str(val))
    if "<" in s and ">" in s:
        return None
    return Path(os.path.expanduser(s))


def vault_dir(cfg=None):
    return _expand(_paths(_cfg(cfg)).get("vault"), cfg)


def _vault_path(cfg, key, default_rel=None):
    cfg = _cfg(cfg)
    raw = _paths(cfg).get(key) or default_rel
    p = _expand(raw, cfg)
    if p is None:
        return None
    if not p.is_absolute():
        v = vault_dir(cfg)
        if v is None:
            return None
        p = v / p
    return p


def people_dir(cfg=None):
    return _vault_path(cfg, "people_dir")


def doctrine_path(cfg=None):
    return _vault_path(cfg, "doctrine", "99_Meta/structure-doctrine.md")


def playbook_path(cfg=None):
    return _vault_path(cfg, "person_playbook")


def filing_log_path(cfg=None):
    return _vault_path(cfg, "filing_log")


def live_path(cfg=None):
    """The live folder this config names: paths.live_dir, the loaded live_dir, else the folder holding config_path.
    None when the config names none; never a default under the home folder."""
    cfg = _cfg(cfg)
    for v in (_paths(cfg).get("live_dir"), cfg.get("live_dir")):
        p = _expand(v, cfg)
        if p is not None and p.is_absolute():
            return p
    cp = str(cfg.get("config_path") or "").strip()
    return Path(os.path.expanduser(cp)).parent if cp else None


def _rel(p, base):
    """p relative to base when inside it, else with the home folder shown as ~ (no user name in vault text)."""
    if p is None:
        return "?"
    if base:
        try:
            return Path(p).relative_to(base).as_posix()
        except ValueError:
            pass
    s, h = str(p), os.path.expanduser("~")
    return "~" + s[len(h):] if h and h != "/" and s.startswith(h + os.sep) else s


def owner_label(cfg=None):
    cfg = _cfg(cfg)
    names = [n.strip() for n in (cfg.get("my_names") or []) if isinstance(n, str) and n.strip()]
    return str(cfg.get("owner_label") or "").strip() or (names[0] if names else "") or "主人"


def owner_marker(cfg=None):
    cfg = _cfg(cfg)
    m = str(cfg.get("owner_marker") or "").strip()
    return m.upper() if re.fullmatch(r"[A-Za-z]{2,6}", m) else _initials(cfg.get("owner_label"))


MARKER_PLACEHOLDER_RE = re.compile(r"<marker>|\{MARKER\}", re.I)


def marker_subst(text, cfg=None):
    """The seed's literal `<marker>` (and `{MARKER}`) replaced by the configured owner marker."""
    if not text or ("<" not in text and "{" not in text):
        return text
    marker = owner_marker(cfg)
    return MARKER_PLACEHOLDER_RE.sub(lambda _m: marker, text)


def _person_cfg(cfg):
    p = (cfg or {}).get("person")
    return p if isinstance(p, dict) else {}


def facts_source_label(cfg=None):
    cfg = _cfg(cfg)
    wc = _wa_common()
    if wc is not None and hasattr(wc, "source_label"):
        try:
            lab = wc.source_label(cfg, "facts")
            if lab:
                return lab
        except Exception:
            pass
    f = cfg.get("facts") if isinstance(cfg.get("facts"), dict) else {}
    src = str(f.get("label") or f.get("source") or "").strip()
    return {"csv": "CSV", "lark": "Lark", "none": "", "": ""}.get(src.lower(), src) or "记录系统"


# ---------------------------------------------------------------- doctrine: the person family

def _strip_comment(line):
    """(code, comment or None): a `#` at the start or after whitespace, outside quotes, starts a comment."""
    q = None
    for i, ch in enumerate(line):
        if q:
            if ch == q:
                q = None
            continue
        if ch in "\"'":
            q = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i], line[i:]
    return line, None


def _ws(s, i):
    while i < len(s) and s[i] in " \t\r\n":
        i += 1
    return i


def _flow_scalar(s, i, stops):
    i = _ws(s, i)
    if i < len(s) and s[i] in "\"'":
        q, j = s[i], i + 1
        buf = []
        while j < len(s) and s[j] != q:
            buf.append(s[j])
            j += 1
        return "".join(buf), j + 1
    j = i
    while j < len(s) and s[j] not in stops:
        j += 1
    return s[i:j].strip(), j


def _flow_value(s, i):
    """A tiny YAML flow reader: {k: v, ...}, [a, b], bare or quoted scalars. -> (value, next index)."""
    i = _ws(s, i)
    if i >= len(s):
        return None, i
    if s[i] == "{":
        d, i = {}, i + 1
        while True:
            i = _ws(s, i)
            if i >= len(s):
                raise ValueError("unclosed {")
            if s[i] == "}":
                return d, i + 1
            if s[i] == ",":
                i += 1
                continue
            key, i = _flow_scalar(s, i, ":,}")
            i = _ws(s, i)
            val = None
            if i < len(s) and s[i] == ":":
                val, i = _flow_value(s, i + 1)
            if key:
                d[key] = val
    if s[i] == "[":
        lst, i = [], i + 1
        while True:
            i = _ws(s, i)
            if i >= len(s):
                raise ValueError("unclosed [")
            if s[i] == "]":
                return lst, i + 1
            if s[i] == ",":
                i += 1
                continue
            v, i = _flow_value(s, i)
            if v not in (None, ""):
                lst.append(v)
    return _flow_scalar(s, i, ",]}")


def parse_person_family(text):
    """The `person:` family of a doctrine (any indentation, flow mapping over several lines) ->
    {"required", "optional", "multi", "closed": {field: [values]}, "meaning": {role: text}}, or None when absent.
    Raises ValueError when the family is there but cannot be read."""
    lines = (text or "").splitlines()
    start = None
    for i, line in enumerate(lines):
        code, _ = _strip_comment(line)
        if re.match(r"^\s*person\s*:\s*\{", code):
            start = i
            break
    if start is None:
        return None
    flow, comments, depth, q, end = [], [], 0, None, None
    for j in range(start, len(lines)):
        code, comment = _strip_comment(lines[j])
        if j == start:
            code = code[code.index("{"):]
        if comment:
            comments.append(comment)
        for ch in code:
            if q:
                if ch == q:
                    q = None
                continue
            if ch in "\"'":
                q = ch
            elif ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
        flow.append(code)
        if depth <= 0:
            end = j
            break
    if end is None:
        raise ValueError("the person family's braces never close")
    for j in range(end + 1, len(lines)):
        s = lines[j].strip()
        if not s.startswith("#"):
            break
        comments.append(s)
    value, _ = _flow_value("\n".join(flow), 0)
    if not isinstance(value, dict):
        raise ValueError("the person family is not a mapping")

    def as_list(v):
        return [str(x).strip() for x in v if str(x).strip()] if isinstance(v, list) else []

    closed = dict((k, as_list(v)) for k, v in value.items() if k not in DOCTRINE_RESERVED and isinstance(v, list))
    roles = closed.get("roles") or []
    # Role lines: `#   <role> <meaning>`, all at one indentation of two or more spaces. A deeper line continues the
    # meaning above it, even when it starts with a role word (so it is checked first).
    meaning, cur, col = {}, None, None
    for c in comments:
        m = re.match(r"^#(\s+)(\S.*)$", c)
        if not m:
            cur = None
            continue
        indent, text = len(m.group(1)), m.group(2)
        if cur and indent > col + len(cur):
            meaning[cur] += " " + text.strip()
            continue
        rm = re.match(r"^([a-z][a-z0-9_-]*)\s+(\S.*)$", text)
        if rm and rm.group(1) in roles and indent >= 2 and (col is None or indent == col):
            cur, col = rm.group(1), indent
            meaning[cur] = rm.group(2).strip()
            continue
        cur = None
    return {"required": as_list(value.get("required")), "optional": as_list(value.get("optional")),
            "multi": as_list(value.get("multi")), "closed": closed, "meaning": meaning}


_DOCTRINE_CACHE = {"key": None, "value": None}


def person_schema(cfg=None):
    """-> (schema or None, doctrine path or None, error or None). Re-read when the file changes."""
    cfg = _cfg(cfg)
    p = doctrine_path(cfg)
    if p is None:
        return None, None, "no doctrine: paths.doctrine is not set and paths.vault is empty"
    try:
        st = p.stat()
    except OSError:
        return None, p, "doctrine not found: %s (paths.doctrine)" % p
    key = (str(p), st.st_mtime, st.st_size)
    if _DOCTRINE_CACHE["key"] != key:
        try:
            schema = parse_person_family(p.read_text(encoding="utf-8"))
            val = (schema, None) if schema else (None, "the doctrine %s has no person family (apply the person family proposal first)" % p)
        except (OSError, ValueError) as e:
            val = (None, "cannot read the person family in %s: %s" % (p, e))
        _DOCTRINE_CACHE.update(key=key, value=val)
    schema, err = _DOCTRINE_CACHE["value"]
    return schema, p, err


def doctrine_person_roles(cfg=None):
    """(roles tuple or None, {role: meaning}) from the owner's doctrine."""
    schema, _, _ = person_schema(cfg)
    if not schema or not schema["closed"].get("roles"):
        return None, {}
    return tuple(schema["closed"]["roles"]), dict(schema["meaning"])


def roles_allowed(cfg=None):
    """The closed roles list from the doctrine; an empty tuple when it cannot be read (finalize then refuses)."""
    r, _ = doctrine_person_roles(cfg)
    return r or ()


def roles_prompt_block(cfg=None):
    """The {ROLES} block for the model: one line per role with its meaning, as the doctrine words it."""
    cfg = _cfg(cfg)
    r, meaning = doctrine_person_roles(cfg)
    if r:
        src = "从主人 vault 的宪法 `%s` 的 person 族现读" % _rel(doctrine_path(cfg), vault_dir(cfg))
    else:
        src = "⚠️ 宪法的 person 族读不到, 下面只是退路清单, 验档一定退回; 先修 paths.doctrine"
    lines = ["`roles` 闭合清单 (%s; 只准用这些值, 可多选, 一个都不对就选最接近的并把细节写进 `roles_remark`, ⛔ 不准自创新值):" % src]
    for v in (r or ROLES):
        lines.append("- `%s`: %s" % (v, meaning.get(v, "(宪法没写说明)")))
    return "\n".join(lines)


def fill_doctrine(prompt, cfg=None):
    """Every prompt that asks for a note calls this after assembling: the doctrine's live values go into {ROLES}."""
    return prompt.replace("{ROLES}", roles_prompt_block(cfg))


# ---------------------------------------------------------------- playbook: section names

def section_key(heading):
    """A heading line to its section name: whitespace squeezed, a trailing parenthetical note removed."""
    h = re.sub(r"\s+", " ", (heading or "").strip())
    return re.sub(r"\s*[(（][^()（）]*[)）]\s*$", "", h)


def parse_playbook_sections(text):
    """Playbook text -> (required section names, optional section names); (None, None) when the skeleton is missing or
    has fewer than five sections. Reads the `## ` headings inside the first fence after the skeleton heading."""
    m = re.search(r"^#{2,4}\s*%s[^\n]*\n.*?^```[^\n]*\n(.*?)^```" % re.escape(SKELETON_HEADING), text or "", re.S | re.M)
    if not m:
        return None, None
    req, opt, cur, cur_opt = [], [], None, False
    for line in m.group(1).splitlines() + ["## (end)"]:
        if line.startswith("## "):
            if cur:
                (opt if cur_opt else req).append(cur)
            cur, cur_opt = section_key(line), False
        elif cur and OPTIONAL_MARK in line:
            cur_opt = True
    if len(req) < 5:
        return None, None
    return req, opt


_PLAYBOOK_CACHE = {}


def playbook_source(cfg=None, log=None):
    """-> (text or None, path, is_seed, error). The owner copy (paths.person_playbook) is the source of truth; the
    payload seed is used only when the owner copy is missing, and `log` hears about it. The text comes back with
    `<marker>` already replaced by the owner marker, so section names, the prompt and the checks all see one spelling."""
    cfg = _cfg(cfg)
    p = playbook_path(cfg)
    cands = []
    if p is not None:
        cands.append((p, False))
    cands.append((SEED_PLAYBOOK, True))
    tried = []
    for path, is_seed in cands:
        try:
            st = path.stat()
        except OSError:
            tried.append(str(path))
            continue
        key = (st.st_mtime, st.st_size)
        hit = _PLAYBOOK_CACHE.get(str(path))
        if not hit or hit[0] != key:
            try:
                hit = (key, path.read_text(encoding="utf-8"))
            except OSError as e:
                tried.append("%s (%s)" % (path, e))
                continue
            _PLAYBOOK_CACHE[str(path)] = hit
        if is_seed and log:
            log("WARNING: the owner copy of the person dossier playbook is missing (%s); using the payload seed %s"
                % (p if p is not None else "paths.person_playbook is not set", path))
        return marker_subst(hit[1], cfg), path, is_seed, None
    return None, p, False, "person dossier playbook not found (tried %s)" % "; ".join(tried)


def playbook_sections(cfg=None, log=None):
    """(required, optional) section names from the playbook skeleton, or (None, None)."""
    text, _, _, _ = playbook_source(cfg, log)
    return parse_playbook_sections(text) if text else (None, None)


def required_sections(cfg=None):
    r, _ = playbook_sections(cfg)
    return list(r or [])


def optional_sections(cfg=None):
    r, o = playbook_sections(cfg)
    return list(o or []) if r else []


def section_roles(cfg=None):
    """person.section_roles over the defaults, `<marker>` (or {MARKER}) and {OWNER} filled; headings normalized like
    section_key."""
    cfg = _cfg(cfg)
    raw = dict(DEFAULT_SECTION_ROLES)
    conf = _person_cfg(cfg).get("section_roles")
    if isinstance(conf, dict):
        for k, v in conf.items():
            if k in raw and isinstance(v, str):
                raw[k] = v
    label = owner_label(cfg)
    out = {}
    for k, v in raw.items():
        v = marker_subst(v, cfg).replace("{OWNER}", label).strip()
        out[k] = section_key(v) if (k in HEADING_ROLES and v) else v
    return out


def skeleton(cfg=None, log=None):
    """(required, optional, roles) with every configured heading role proven to exist in the skeleton. Raises
    DossierError when the playbook is unreadable or a role names a section the skeleton does not have."""
    cfg = _cfg(cfg)
    text, path, _, err = playbook_source(cfg, log)
    if text is None:
        raise DossierError(err)
    req, opt = parse_playbook_sections(text)
    if req is None:
        raise DossierError("the playbook %s has no readable skeleton (a heading containing %r followed by a fence with at least five `## ` sections)" % (path, SKELETON_HEADING))
    roles = section_roles(cfg)
    names = set(req) | set(opt)
    missing = ["%s=%r" % (k, roles[k]) for k in HEADING_ROLES if roles[k] and roles[k] not in names]
    if missing:
        raise DossierError("person.section_roles names sections the playbook skeleton (%s) does not have: %s. "
                           "Rename them in config to match the playbook, or set a role to \"\" to switch that guard off on purpose."
                           % (path, ", ".join(missing)))
    if log:
        for k in ("next_message", "privacy_hint"):
            if roles[k] and roles[k] not in text:
                log("WARNING: person.section_roles.%s %r does not appear in the playbook; the warning that relies on it will fire on every note" % (k, roles[k]))
    return req, opt, roles


# ---------------------------------------------------------------- frontmatter (no PyYAML)

def normalize_block_lists(text):
    """Frontmatter block lists (`roles:` then indented `- member`) rewritten as one-line flow lists (`roles: [member]`).
    Both are legal YAML; the checks accept both and notes keep one spelling."""
    if not text.startswith("---"):
        return text
    parts = text.split("\n---", 1)
    if len(parts) < 2:
        return text
    lines = parts[0].splitlines()
    out = [lines[0]]
    i = 1
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^([A-Za-z_][\w-]*):\s*$", line)
        if m:
            items = []
            j = i + 1
            while j < len(lines) and re.match(r"^\s+-\s+", lines[j]):
                items.append(re.sub(r"^\s+-\s+", "", lines[j]).strip().strip("\"'"))
                j += 1
            if items:
                out.append("%s: [%s]" % (m.group(1), ", ".join(items)))
                i = j
                continue
        out.append(line)
        i += 1
    return "\n".join(out) + "\n---" + parts[1]


def parse_frontmatter(text):
    if not text.startswith("---"):
        return {}, text
    text = normalize_block_lists(text)
    parts = text.split("\n---", 1)
    if len(parts) < 2:
        return {}, text
    fm = {}
    for line in parts[0].splitlines()[1:]:
        if ":" in line and not line.startswith((" ", "\t")):
            k, v = line.split(":", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'" and not v.startswith("["):
                v = v[1:-1]   # a quoted value is legal YAML; the value itself has no quotes
            fm[k.strip()] = v
    return fm, parts[1]


def fm_list(v):
    """A multi-value field: `[a, b]`, `["a", "b"]` or `a` -> list of strings."""
    v = (v or "").strip()
    if not v:
        return []
    if v.startswith("["):
        try:
            got = json.loads(v)
            if isinstance(got, list):
                return [str(x).strip() for x in got if str(x).strip()]
        except ValueError:
            pass
        v = v.strip("[]")
    return [x.strip().strip("\"'") for x in v.split(",") if x.strip().strip("\"'")]


def set_fm_key(body, key, value):
    head, sep, rest = body[3:].partition("\n---")
    lines = head.split("\n")
    line = "%s: %s" % (key, value)
    for i, l in enumerate(lines):
        if l.startswith(key + ":"):
            lines[i] = line
            break
    else:
        lines.append(line)
    return "---" + "\n".join(lines) + sep + rest


def drop_fm_key(body, key):
    head, sep, rest = body[3:].partition("\n---")
    lines = [l for l in head.split("\n") if not l.startswith(key + ":")]
    return "---" + "\n".join(lines) + sep + rest


def dossier_marker(text):
    """A legacy `<!-- dossier: v1 · updated: YYYY-MM-DD -->` line -> (version, updated) or (None, None)."""
    m = re.search(r"<!--\s*dossier:\s*(v\d+)\s*·\s*updated:\s*(\d{4}-\d{2}-\d{2})\s*-->", text[:3000])
    return (m.group(1), m.group(2)) if m else (None, None)


def strip_marker(body):
    return re.sub(r"\n<!--\s*dossier:\s*v\d+\s*·\s*updated:\s*\d{4}-\d{2}-\d{2}\s*-->\s*\n", "\n", body, count=1)


def note_updated(text):
    fm, _ = parse_frontmatter(text)
    return fm.get("dossier_updated") or dossier_marker(text)[1]


FILE_HEADER_RE = re.compile(r"\s*<!--\s*file:\s*(.+?)\s*-->[ \t]*\n?")


def parse_file_header(text):
    """A model's `<!-- file: Name.md -->` first line -> (name or None, the rest). Validate the name with check_note_filename."""
    m = FILE_HEADER_RE.match(text or "")
    if not m:
        return None, text
    return m.group(1).strip(), text[m.end():]


def check_note_filename(fname):
    """A safe note file name: letters in any script, digits, spaces, dot, dash, underscore, ending in .md; no path
    separators, no `..`, no leading dot or underscore (the People room index skips `_` files: doors and drawers).
    Returns the name or raises DossierError."""
    name = (fname or "").strip()
    stem = name[:-3] if name.endswith(".md") else ""
    ok = bool(stem.strip()) and "/" not in name and "\\" not in name and ".." not in name and not name.startswith((".", "_"))
    if ok:
        for ch in stem:
            if ch in " ._-":
                continue
            if unicodedata.category(ch)[0] not in "LMN":
                ok = False
                break
    if not ok:
        raise DossierError("unsafe note file name %r (use letters, digits, spaces, dot, dash or underscore, end in .md, no path separators or ..)" % fname)
    return name


# ---------------------------------------------------------------- sections and owner marks

def mark_regexes(cfg=None):
    """(lock rx, any rx, lock comment rx). Locked tags are `🗣 <MARKER> 2026-09-14` or `🗣 <MARKER> 9/14`; a tag with a
    clock time (`🗣 <MARKER> 09-12 13:09`) quotes the owner inside a conversation and is not locked. New notes may also
    write `🗣 <MARKER> 09-14`. The marker matches in any letter case."""
    letters = "".join("[%s%s]" % (c.upper(), c.lower()) if c.isalpha() else re.escape(c) for c in owner_marker(cfg))
    head = "🗣️?\\s*" + letters + "(?![A-Za-z])\\s*[:：(（,，]?\\s*"
    lock = re.compile(head + r"(?:(\d{4})[-/](\d{1,2})[-/](\d{1,2})|(\d{1,2})/(\d{1,2}))(?![\d])(?!\s*\d{1,2}:\d{2})")
    anyr = re.compile(head + r"(?:(\d{4})[-/](\d{1,2})[-/](\d{1,2})|(\d{1,2})/(\d{1,2})|(\d{1,2})-(\d{1,2}))(?![\d])")
    comment = re.compile(r"<!--\s*" + letters + r"\s*-->")
    return lock, anyr, comment


def mark_key(m):
    """A matched tag's date -> `MM-DD` (the year is not compared: 2026-09-14 and 9/14 are the same day)."""
    g = m.groups()
    mo, d = (g[1], g[2]) if g[0] else ((g[3], g[4]) if g[3] else (g[5], g[6]))
    return "%02d-%02d" % (int(mo), int(d))


def _squash(s):
    """Compare text without whitespace and without a leading list marker (`- ` `* ` `+ ` `1. `)."""
    s = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", s)
    return re.sub(r"\s+", "", s)


def is_placeholder_line(line):
    """Placeholder lines inside the owner notes or a lock segment, which a rewrite may replace: `⬜ …`, `(⬜ …)`, a
    whole line in brackets saying 还没, or a markdown rule."""
    s = line.strip()
    if not s or re.fullmatch(r"[-*_]{3,}", s):
        return True
    if re.match(r"^[(（]?\s*⬜", s):
        return True
    return bool(re.fullmatch(r"[(（].*还没.*[)）]", s))


def _heading_name(line):
    return re.sub(r"\s+", " ", line.strip())


def section_blocks(text, cfg=None):
    """Body split at `## ` headings -> [(section name, [lines])]; text before the first heading is "(head)". A heading
    that starts with a skeleton or role name gets that name (a trailing note after it still counts as the section)."""
    cfg = _cfg(cfg)
    _, rest = parse_frontmatter(text) if text.startswith("---") else ({}, text)
    r, o = playbook_sections(cfg)
    roles = section_roles(cfg)
    known = list(r or []) + list(o or []) + [roles[k] for k in HEADING_ROLES if roles[k]]
    known.sort(key=len, reverse=True)
    out, name, cur = [], "(head)", []
    for line in rest.splitlines():
        if line.startswith("## "):
            out.append((name, cur))
            h = _heading_name(line)
            name = next((k for k in known if h.startswith(k)), h)
            cur = []
        else:
            cur.append(line)
    out.append((name, cur))
    return out


def lock_segments(text, cfg=None):
    """Segments that start with the `<!-- <marker> -->` line and run to the next blank line."""
    _, _, comment = mark_regexes(cfg)
    rx = re.compile(r"^" + comment.pattern + r"[^\n]*\n(?:[^\n]+\n)*", re.M)
    return [m.group(0).rstrip("\n") for m in rx.finditer(text + "\n")]


def _canon_lines(text, comment):
    lines = [_squash(comment.sub("<!--LOCK-->", l)) for l in text.splitlines() if not is_placeholder_line(l)]
    return [l for l in lines if l]


def check_lock_segments(body, existing, cfg=None):
    """Every lock segment of the old note is still one contiguous run in the new note, tolerating whitespace, replaced
    or removed placeholder lines and blank lines inside the segment."""
    _, _, comment = mark_regexes(cfg)
    body_lines = _canon_lines(body, comment)
    for seg in lock_segments(existing, cfg):
        locked = _canon_lines(seg, comment)
        n = len(locked)
        if n and not any(body_lines[i:i + n] == locked for i in range(len(body_lines) - n + 1)):
            raise DossierError("a locked segment changed: %s" % seg.replace("\n", " / ")[:80])


def owner_note_lines(text, cfg=None):
    """The owner's verbatim lines in the owner notes section (lock comment, blank and placeholder lines removed)."""
    heading = section_roles(cfg)["owner_notes"]
    if not heading:
        return []
    _, _, comment = mark_regexes(cfg)
    got = []
    for name, lines in section_blocks(text, cfg):
        if name != heading:
            continue
        for l in lines:
            l = comment.sub("", l)
            if not is_placeholder_line(l):
                got.append(l.strip())
    return got


def check_owner_notes(body, existing, cfg=None):
    """Every line of the old note's owner notes is still, word for word, in the new note's owner notes section."""
    old = owner_note_lines(existing, cfg)
    if not old:
        return
    have = set(_squash(l) for l in owner_note_lines(body, cfg))
    lost = [l for l in old if _squash(l) not in have]
    if lost:
        raise DossierError("the owner notes section (%s) lost %d line(s); the owner's words are never changed or moved, e.g.: %s"
                           % (section_roles(cfg)["owner_notes"], len(lost), lost[0][:80]))


def owner_marks(text, cfg=None, lock=True):
    """`🗣 <MARKER> <date>` tags -> [(section, MM-DD, line)], machine sections excluded. lock=True reads only the
    locked spellings (old note), False every spelling (new note)."""
    lock_rx, any_rx, _ = mark_regexes(cfg)
    rx = lock_rx if lock else any_rx
    roles = section_roles(cfg)
    machine = set(x for x in (roles["recent"], roles["facts"]) if x)
    out = []
    for name, lines in section_blocks(text, cfg):
        if name in machine:
            continue
        for l in lines:
            for m in rx.finditer(l):
                out.append((name, mark_key(m), l.strip()))
    return out


def check_owner_marks(body, existing, cfg=None):
    """Each section keeps at least as many owner tags per day as the old note had there (wording may change)."""
    old = owner_marks(existing, cfg, lock=True)
    if not old:
        return
    have = {}
    for name, key, _ in owner_marks(body, cfg, lock=False):
        have[(name, key)] = have.get((name, key), 0) + 1
    need = {}
    for name, key, line in old:
        need.setdefault((name, key), []).append(line)
    for (name, key), lines in need.items():
        got = have.get((name, key), 0)
        if got < len(lines):
            raise DossierError("owner mark %s %s lost in section %r (%d in the old note, %d in the new), old line: %s"
                               % (owner_marker(cfg), key, name, len(lines), got, lines[got][:80]))


def count_gaps(body, cfg=None):
    heading = section_roles(cfg)["gaps"]
    if not heading:
        return 0
    m = re.search(r"^%s[^\n]*$(.*?)(?=^## |\Z)" % re.escape(heading), body or "", re.S | re.M)
    return len(re.findall(r"^\s*\d+\.\s", m.group(1), re.M)) if m else 0


def how_to_talk_from(text, limit=160, cfg=None):
    """The next-message line of the talking section (`- **<label>**: …` in its usual spellings), bold removed, at most
    limit characters; None when absent or still ⬜. The board shows it."""
    label = section_roles(cfg)["next_message"]
    if not label:
        return None
    rx = re.compile(r"^\s*[-*]\s*(?:\*\*)?%s(?:\*\*)?\s*(?:[🤔👁🗣]️?\s*)*[:：]\s*(.+?)\s*$" % re.escape(label), re.M)
    m = rx.search(text or "")
    if not m:
        return None
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", m.group(1)).strip()
    if not s or s.startswith("⬜"):
        return None
    return s if len(s) <= limit else s[:limit - 1] + "…"


# ---------------------------------------------------------------- facts section (the machine's section)

def facts_inner(text):
    """Text between the facts delimiters (stripped); None when absent; raises when only one delimiter is there."""
    has_b, has_e = FACTS_BEGIN in text, FACTS_END in text
    if not has_b and not has_e:
        return None
    if has_b != has_e:
        raise DossierError("facts delimiters come in pairs (%s and %s)" % (FACTS_BEGIN, FACTS_END))
    if text.count(FACTS_BEGIN) > 1 or text.count(FACTS_END) > 1:
        raise DossierError("the facts delimiters appear more than once")
    return text.split(FACTS_BEGIN, 1)[1].split(FACTS_END, 1)[0].strip()


# The source line of the facts section carries the day the facts worker pulled (wa_facts, both languages). A note verified
# on a later day differs from the machine's block only in that date: that is not an edit, so it is compared without it.
FACTS_PULLED_RE = re.compile(r"(事实 worker |facts worker pulled )\d{4}-\d{2}-\d{2}")


def facts_for_compare(inner):
    return FACTS_PULLED_RE.sub(r"\1<date>", inner or "")


def check_facts_block(body, facts_block, log=None):
    """The facts section belongs to the machine: when facts_block is not None, the note's delimited text must equal the
    machine's word for word, a machine block must not be left out, and a note may not invent one.
    Returns body: when the only difference is the pull date on the source line, the machine's current block replaces the
    note's (otherwise verifying a note the day after its run refuses it, and an update of that person breaks)."""
    got = facts_inner(body)
    if facts_block is None:
        return body
    want = facts_inner(facts_block or "")
    if got is None:
        if want:
            raise DossierError("the machine supplied a facts section (%d lines) but the note does not carry it verbatim" % len(want.splitlines()))
        return body
    if want is None:
        raise DossierError("the note has a facts section but the machine supplied none (that section is never written by hand)")
    if got != want:
        if facts_for_compare(got) != facts_for_compare(want):
            raise DossierError("the facts section differs from what the machine supplied (%d lines in the note, %d supplied)"
                               % (len(got.splitlines()), len(want.splitlines())))
        if log:
            log("facts section differs only in the pull date: replaced with the machine's current block")
        head, rest = body.split(FACTS_BEGIN, 1)
        body = head + FACTS_BEGIN + "\n" + want + "\n" + FACTS_END + rest.split(FACTS_END, 1)[1]
    return body


# ---------------------------------------------------------------- stage ruling

def stage_model(cfg=None):
    cfg = _cfg(cfg)
    vocab = cfg.get("vocab") if isinstance(cfg.get("vocab"), dict) else {}
    keys = []
    side_keys = []
    has_side_flag = False
    for s in vocab.get("stages") or []:
        k = s.get("key") if isinstance(s, dict) else s
        if isinstance(k, str) and k.strip() and k.strip() not in keys:
            keys.append(k.strip())
            if isinstance(s, dict) and "side" in s:
                has_side_flag = True
                if s.get("side"):
                    side_keys.append(k.strip())
    if not keys:
        keys = list(DEFAULT_STAGE_KEYS)
    pc = _person_cfg(cfg)
    # the side stage: an entry with "side": true (same rule as wa_common.vocab), else the key "parked"
    if has_side_flag:
        parked = side_keys[0] if side_keys else None
    else:
        parked = PARKED if PARKED in keys else None
    order = [k for k in keys if k != parked]
    park_from = [k for k in (vocab.get("park_from") or DEFAULT_PARK_FROM) if k in order]
    paid = [k for k in (pc.get("paid_stages") or DEFAULT_PAID_STAGES) if k in order]
    first = min(order.index(k) for k in paid) if paid else len(order)
    cap = order[first - 1] if 0 < first <= len(order) else None
    rws = [r for r in (pc.get("roles_with_stage") or DEFAULT_ROLES_WITH_STAGE) if isinstance(r, str)]
    return {"order": order, "parked": parked, "park_from": park_from, "paid": paid, "cap": cap, "roles_with_stage": rws}


def enforce_stage(body, roles, ofm, proposed, board, has_payment, today=None, log=None, cfg=None):
    """The writer only proposes a stage. Forward only across the old note, the proposal and the board; parked only from
    park_from; paid stages only with a payment record (has_payment: a floor stage string, or True for the first paid
    stage) or when the old note is already there, otherwise capped just below them. People without a role in
    roles_with_stage carry no stage."""
    sm = stage_model(cfg)
    order, parked, paid = sm["order"], sm["parked"], sm["paid"]
    today = today or dt.date.today().isoformat()
    old = ofm.get("stage") or None
    if old not in order and old != parked:
        old = None
    if not any(r in sm["roles_with_stage"] for r in roles):
        return drop_fm_key(drop_fm_key(body, "stage"), "stage_since")
    cands = [x for x in (old, proposed, board) if x in order]
    if has_payment:
        floor = has_payment if (isinstance(has_payment, str) and has_payment in order) else (paid[0] if paid else None)
        if floor:
            cands.append(floor)
            if old not in paid and log:
                log("payment record found: stage floor %s" % floor)
    new = max(cands, key=order.index) if cands else None
    park_wanted = bool(parked) and parked in (proposed, board)
    if park_wanted and (new is None or new in sm["park_from"]) and (old is None or old in sm["park_from"] or old == parked):
        new = parked
    elif new is None:
        new = parked if (parked and old == parked) else order[0]
    if new in paid and not (old in paid or has_payment):
        below = [x for x in cands if x not in paid] + ([sm["cap"]] if sm["cap"] else [])
        capped = max(below, key=order.index) if below else order[0]
        if log:
            log("stage %s proposed without a payment record: capped at %s" % (new, capped))
        new = capped
    body = set_fm_key(body, "stage", new)
    since = ofm.get("stage_since") if new == old else today
    return set_fm_key(body, "stage_since", since or today)


# ---------------------------------------------------------------- the checks

def finalize(body, jid, existing="", board_stage=None, has_payment=False, log=None, facts_block=None, cfg=None,
             today=None, require_wa_jid=True):
    """A candidate note -> (the note ready to place, info). Raises DossierError on any broken rule.
    facts_block: the machine's facts section for this person ("" = none supplied); None skips that check."""
    cfg = _cfg(cfg)
    today = today or dt.date.today().isoformat()
    # a writer that copied the seed's `<marker>` literally still produces a note the lock and mark checks recognize
    body = marker_subst(normalize_block_lists((body or "").lstrip()), cfg)
    existing = marker_subst(existing or "", cfg)
    if not body.startswith("---"):
        raise DossierError("the note does not start with a --- frontmatter block")
    schema, dpath, derr = person_schema(cfg)
    if derr:
        raise DossierError(derr)
    req_sections, _, roles_map = skeleton(cfg, log)
    allowed_keys = set(schema["required"]) | set(schema["optional"])
    closed, multi = schema["closed"], set(schema["multi"])
    allowed_roles = closed.get("roles") or []
    if not allowed_roles:
        raise DossierError("the doctrine's person family (%s) has no closed roles list" % dpath)
    sm = stage_model(cfg)
    if "stage" in allowed_keys and not any(r in allowed_roles for r in sm["roles_with_stage"]):
        raise DossierError("person.roles_with_stage %s shares no value with the doctrine roles %s, so every stage would be dropped; fix one of them"
                           % (sm["roles_with_stage"], allowed_roles))

    fm, _ = parse_frontmatter(body)
    for k in [k for k in fm if k in HARMLESS_EXTRA and k not in allowed_keys]:
        body = drop_fm_key(body, k)
        fm.pop(k, None)
        if log:
            log("dropped the harmless extra frontmatter key %s" % k)
    bad = [k for k in fm if k not in allowed_keys]
    if bad:
        raise DossierError("frontmatter keys outside the doctrine person family: %s" % bad)
    if fm.get("type") != "person":
        raise DossierError("frontmatter type is not person: %s" % fm.get("type"))
    roles = fm_list(fm.get("roles"))
    if not roles or any(r not in allowed_roles for r in roles):
        raise DossierError("frontmatter roles %s are not in the closed list %s" % (roles, "/".join(allowed_roles)))
    if require_wa_jid and jid and fm.get("wa_jid") != jid:
        raise DossierError("wa_jid was changed: %s (must be %s)" % (fm.get("wa_jid"), jid))
    ofm, _ = parse_frontmatter(existing) if existing else ({}, "")
    if ofm.get("since") and fm.get("since") and fm.get("since") != ofm.get("since"):
        raise DossierError("since was changed: %s to %s" % (ofm.get("since"), fm.get("since")))
    if existing:
        dropped = [r for r in fm_list(ofm.get("roles")) if r in allowed_roles and r not in roles]
        if dropped:
            raise DossierError("roles of the old note were removed (roles may be added, never removed): %s" % dropped)

    if "stage" in allowed_keys:
        body = enforce_stage(body, roles, ofm, fm.get("stage"), board_stage, has_payment, today, log, cfg)
    else:
        body = drop_fm_key(drop_fm_key(body, "stage"), "stage_since")
    if "dossier_updated" in allowed_keys:
        body = set_fm_key(body, "dossier_updated", today)
    body = strip_marker(body)

    if existing:
        missing = [l for l in set(re.findall(r"\[\[[^\]]+\]\]", existing)) if l not in body]
        if missing:
            raise DossierError("links of the old note are gone: %s" % missing[:5])
        if roles_map["notes"]:
            m_old = re.search(r"^%s[^\n]*\n(.*?)(?=^## |\Z)" % re.escape(roles_map["notes"]), existing, re.S | re.M)
            if m_old:
                keep = [l.strip() for l in m_old.group(1).splitlines() if l.strip().startswith(("- ", "### "))]
                lost = [l for l in keep if l not in body and l[:40] not in body]
                if lost:
                    raise DossierError("the notes section (%s) lost %d item(s), e.g.: %s" % (roles_map["notes"], len(lost), lost[0][:80]))
        check_owner_notes(body, existing, cfg)
        check_lock_segments(body, existing, cfg)
        check_owner_marks(body, existing, cfg)
    heads = [_heading_name(l) for l in body.splitlines() if l.startswith("## ")]
    for s in req_sections:
        if not any(h.startswith(s) for h in heads):
            raise DossierError("missing section %r (section names are read from the playbook skeleton)" % s)
    if roles_map["recent"] and (RECENT_BEGIN not in body or RECENT_END not in body):
        raise DossierError("the machine recent block markers are missing (%s ... %s)" % (RECENT_BEGIN, RECENT_END))
    body = check_facts_block(body, facts_block, log)
    if _person_cfg(cfg).get("house_punctuation"):
        body = body.replace("—", ", ")
        body = re.sub(r"\s(?:--|–)\s", ", ", body)

    nfm, _ = parse_frontmatter(body)
    absent = [k for k in schema["required"] if not str(nfm.get(k) or "").strip()]
    if absent:
        raise DossierError("required frontmatter keys are missing or empty: %s" % absent)
    for field, values in closed.items():
        if field in nfm and str(nfm.get(field) or "").strip():
            got = fm_list(nfm[field]) if field in multi else [nfm[field]]
            wrong = [g for g in got if g not in values]
            if wrong:
                raise DossierError("%s value %s is not in the doctrine's closed list %s" % (field, wrong, values))
    return body, {"roles": roles, "stage": nfm.get("stage"), "gaps": count_gaps(body, cfg), "lines": len(body.splitlines())}


def warnings_for(body, cfg=None):
    """Things that do not block placing but deserve a look."""
    roles = section_roles(cfg)
    w = []
    if roles["privacy_hint"] and roles["privacy_hint"] not in body:
        w.append("the fixed privacy line at the end may be missing (no %r)" % roles["privacy_hint"])
    rows = [l for l in body.splitlines() if l.startswith("|") and not l.startswith("|---") and not l.startswith("| |")]
    unmarked = [l for l in rows[1:] if not any(m in l for m in "🗣👁🤔⬜") and len(l) > 12]
    if len(unmarked) > 2:
        w.append("%d table rows carry no source mark, e.g. %s" % (len(unmarked), unmarked[0][:60]))
    n = len(body.splitlines())
    if n < WARN_MIN_LINES:
        w.append("only %d lines (short is right for a thin history; make sure it is not cut off)" % n)
    if n > WARN_MAX_LINES:
        w.append("%d lines, over the playbook's %d" % (n, WARN_MAX_LINES))
    if roles["next_message"] and roles["next_message"] not in body:
        w.append("the talking section has no %r line" % roles["next_message"])
    if roles["gaps"] and count_gaps(body, cfg) == 0:
        w.append("%s is empty; the playbook says it never is" % roles["gaps"])
    return w


def quotes_check(body, sources, n=8):
    """Up to n quoted 「…」 passages (12+ characters) split into fragments of 6+ characters and looked up in the sources;
    a quote hits when 70% of its fragments are found. -> (rows, hits, n)."""
    def norm(s):
        return re.sub(r"[\s,，。、:：;；!！?？\"'`*_+加]", "", s).lower()
    srcn = norm("\n".join(sources))
    quotes = []
    for q in re.findall(r"「([^」]{12,80})」", body):
        if q not in quotes:
            quotes.append(q)
        if len(quotes) >= n:
            break
    rows, hits = [], 0
    for q in quotes:
        frags = [f for f in re.split(r"[,，。、:：;；!！?？\n]", q) if len(norm(f)) >= 6] or [q]
        found = sum(norm(f) in srcn for f in frags)
        ok = found / len(frags) >= 0.7
        hits += ok
        rows.append((ok, q, found, len(frags)))
    return rows, hits, len(quotes)


# ---------------------------------------------------------------- prompt assembly (shared by the daemon and the CLI)

def compose_prompt(cfg, template, name, jid, board=None, reason="", existing_name=None, existing="", facts_block="",
                   pack="", ctx_md="", spec=None, kind="一对一", log=None):
    """Fill the note prompt template: every placeholder, the doctrine's roles, the playbook as {SPEC} (read from the owner
    copy when spec is None), then the existing note, the conversation pack and the second-brain candidates."""
    cfg = _cfg(cfg)
    b = board or {}
    if spec is None:
        spec, _, _, err = playbook_source(cfg, log)
        if spec is None:
            raise DossierError(err)
    spec = marker_subst(spec, cfg)
    template = marker_subst(template, cfg)
    label = owner_label(cfg)
    people = people_dir(cfg)
    small = [
        ("{NAME}", name), ("{JID}", jid), ("{KIND}", kind),
        ("{WORLD}", str(b.get("world") or "-")), ("{STAGE}", str(b.get("stage") or "-")),
        ("{PRIORITY}", str(b.get("priority") or "-")), ("{BOARD_DATE}", str(b.get("updated_at") or "-")[:16]),
        ("{REASON}", reason or "session 手动"), ("{SUMMARY}", str(b.get("summary") or "(无)")),
        ("{EXISTING_NOTE_LINE}", ("有, 档名 `%s`, 全文在下面「现有的档」一节, 里面 %s 写的判断与链接全部保留" % (existing_name, label))
         if existing_name else "没有, 这是开新档"),
        ("{OWNER_LABEL}", label), ("{MARKER}", owner_marker(cfg)),
        ("{PEOPLE_DIR}", (_rel(people, vault_dir(cfg)).rstrip("/") + "/") if people else "(人物房)"),
        ("{FACTS_SOURCE_LABEL}", facts_source_label(cfg)),
    ]
    prompt = template
    for k, v in small:
        prompt = prompt.replace(k, v)
    prompt = fill_doctrine(prompt, cfg)
    prompt = prompt.replace("{FACTS}", (facts_block or "").rstrip() or "(无)")
    prompt = prompt.replace("{SPEC}", spec)
    if pack and len(pack) > PACK_LIMIT:
        pack = "(对话包太长, 只给最后 %d 字)\n\n" % PACK_LIMIT + pack[-PACK_LIMIT:]
    prompt += "\n\n---\n\n# 现有的档\n\n" + (("```markdown\n" + existing + "\n```") if existing else "(无)")
    prompt += "\n\n# 对话包\n\n" + (pack or "(无)") + "\n\n# 第二大脑候选\n\n" + ((ctx_md or "")[:CONTEXT_LIMIT] or "(无)") + "\n"
    return prompt


# ---------------------------------------------------------------- placing a note

@contextlib.contextmanager
def people_lock(cfg=None, timeout=60):
    """One lock for every writer of person notes on this Mac: placement here and the daemon's recent-line appends.
    The lock file is <live>/people-write.lock, created only when the config's live folder already exists; without
    one there is no lock and nothing is created."""
    f = None
    try:
        live = live_path(cfg)
        if live is not None and live.is_dir():
            fd = os.open(str(live / "people-write.lock"), os.O_RDWR | os.O_CREAT, 0o600)
            f = os.fdopen(fd, "r+")
    except Exception:
        f = None
    if f is None:
        yield
        return
    try:
        deadline = time.time() + timeout
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() > deadline:
                    raise DossierError("another writer has held the person notes lock for %ds" % timeout)
                time.sleep(0.1)
        yield
    finally:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        f.close()


def _without_recent(text):
    if RECENT_BEGIN not in text or RECENT_END not in text:
        return text
    head, rest = text.split(RECENT_BEGIN, 1)
    marker_end = rest.find("-->") + 3
    _, tail = rest[marker_end:].split(RECENT_END, 1)
    return head + RECENT_BEGIN + rest[:marker_end] + RECENT_END + tail


def _recent_block(text):
    head, rest = text.split(RECENT_BEGIN, 1)
    marker_end = rest.find("-->") + 3
    block, _ = rest[marker_end:].split(RECENT_END, 1)
    return block


def _with_recent(body, block):
    head, rest = body.split(RECENT_BEGIN, 1)
    marker_end = rest.find("-->") + 3
    _, tail = rest[marker_end:].split(RECENT_END, 1)
    return head + RECENT_BEGIN + rest[:marker_end] + block + RECENT_END + tail


def _write_private(path, text):
    path = Path(path)
    tmp = path.with_name(".%s.tmp-%d" % (path.name, os.getpid()))
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(str(tmp), str(path))


def place_note(cfg, fname, body, existing, backups_dir, reason, who, log=None):
    """Write a finalized note into the people folder, safely:
      - the target is re-read right before writing, under the person notes lock;
      - a target that exists is never written when `existing` is empty (this run thought the person was new);
      - a target whose content is not `existing` is never overwritten, except when only the machine recent block moved
        on meanwhile: then the newer recent lines are carried into the new version;
      - whatever is on disk is backed up first (dossier.backups_keep newest copies per note, default 3);
      - one line goes into paths.filing_log when that is set.
    Returns the target Path; raises DossierError instead of overwriting."""
    cfg = _cfg(cfg)
    people = people_dir(cfg)
    if people is None:
        raise DossierError("paths.people_dir is not set (a relative people_dir also needs paths.vault)")
    if not people.is_dir():
        raise DossierError("people folder not found: %s" % people)
    name = check_note_filename(fname)
    target = people / name
    existing = existing or ""
    backup_file = None
    with people_lock(cfg):
        try:
            current = target.read_text(encoding="utf-8") if target.exists() else None
        except OSError as e:
            raise DossierError("cannot re-read %s before writing: %s" % (target, e))
        if current is None:
            if existing:
                raise DossierError("%s was read when this run started but is gone now (moved or renamed?); not writing" % name)
        else:
            if not existing:
                raise DossierError("%s already exists but this run treated the person as new; refusing to overwrite "
                                   "(match the person to that note by wa_jid or wa_phone, then rewrite it)" % name)
            if current != existing:
                if (RECENT_BEGIN in body and RECENT_END in body and RECENT_BEGIN in current and RECENT_END in current
                        and _without_recent(current) == _without_recent(existing)):
                    body = _with_recent(body, _recent_block(current))
                    if log:
                        log("recent lines were added to %s during the run; the newer lines were carried into the new version" % name)
                else:
                    raise DossierError("%s changed on disk since this run read it; not overwriting (run it again from the current file)" % name)
            live = None if backups_dir else live_path(cfg)
            if not backups_dir and live is None:
                raise DossierError("no backups folder: pass backups_dir or set paths.live_dir (a note is never rewritten without a backup)")
            backups = Path(backups_dir) if backups_dir else live / "dossier-backups"
            try:
                bdir = backups / target.stem
                bdir.mkdir(parents=True, exist_ok=True)
                os.chmod(str(bdir.parent), 0o700)
                os.chmod(str(bdir), 0o700)
                backup_file = bdir / (dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".md")
                _write_private(backup_file, current)
                keep = int((((cfg.get("dossier") or {}) if isinstance(cfg.get("dossier"), dict) else {}).get("backups_keep")) or DEFAULT_BACKUPS_KEEP)
                for old in sorted(bdir.glob("*.md"))[:-max(1, keep)]:
                    old.unlink()
            except (OSError, ValueError) as e:
                raise DossierError("cannot back up %s before writing (%s); not writing" % (name, e))
        try:
            # the daemon runs with umask 077, which would turn a vault note into 0600: set the mode on the open file itself
            # (a new note 0644 like every other vault note, a rewrite keeps the mode the note had)
            if current is None:
                fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                os.fchmod(fd, 0o644)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(body)
            else:
                mode = os.stat(str(target)).st_mode & 0o777
                tmp = target.with_name(".%s.tmp-%d" % (target.name, os.getpid()))
                fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
                os.fchmod(fd, mode)
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(body)
                os.replace(str(tmp), str(target))
        except FileExistsError:
            raise DossierError("%s appeared while this run was writing it; not overwriting" % name)
    flog = filing_log_path(cfg)
    if flog is not None:
        vault = vault_dir(cfg)
        pb = playbook_path(cfg)
        line = "- %s · `%s` %s `%s/` · %s · person note checked by dossier_core.finalize against `%s` · reason: %s · backup: %s\n" % (
            dt.date.today().isoformat(), name, "rewritten in" if current is not None else "new in", _rel(people, vault).rstrip("/"),
            who, _rel(pb, vault) if pb else "the payload seed", re.sub(r"\s+", " ", str(reason or "-")).strip(),
            _rel(backup_file, None) if backup_file else "none (new note)")
        try:
            with flog.open("a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError as e:
            if log:
                log("WARNING: the note was placed but the filing log line could not be written (%s): %s" % (flog, e))
    return target


# ---------------------------------------------------------------- check-live

def check_live(config_path=None, out=print):
    problems = []
    try:
        cfg = load_cfg(config_path)
    except DossierError as e:
        out("FAIL config: %s" % e)
        return 1
    out("config: %s" % (cfg.get("config_path") or config_path or os.environ.get("WA_LIVE_CONFIG") or DEFAULT_CONFIG))
    vault = vault_dir(cfg)
    out("vault: %s%s" % (vault, "" if vault and vault.is_dir() else "  (MISSING)"))
    if not (vault and vault.is_dir()):
        problems.append("vault")
    marker = owner_marker(cfg)
    out("owner: label %r · marker %s · tags `🗣 %s <date>` · lock segments `<!-- %s -->`" % (owner_label(cfg), marker, marker, marker.lower()))
    schema, dpath, err = person_schema(cfg)
    if err:
        out("FAIL doctrine: %s" % err)
        problems.append("doctrine")
    else:
        out("doctrine: %s" % dpath)
        out("  required keys: %s" % ", ".join(schema["required"]))
        out("  optional keys: %s" % ", ".join(schema["optional"]))
        roles = schema["closed"].get("roles") or []
        out("  roles (%d, %d with a meaning): %s" % (len(roles), len(schema["meaning"]), ", ".join(roles)))
        if not roles:
            out("FAIL doctrine: the person family has no roles list")
            problems.append("roles")
        out("  stage list: %s" % ", ".join(schema["closed"].get("stage") or ["(none)"]))
        sm = stage_model(cfg)
        shared = [r for r in sm["roles_with_stage"] if r in roles]
        out("  roles that carry a stage: %s" % (", ".join(shared) or "(none)"))
        if roles and not shared:
            out("FAIL person.roles_with_stage %s shares nothing with the doctrine roles" % sm["roles_with_stage"])
            problems.append("roles_with_stage")
        dstages = schema["closed"].get("stage") or []
        vocab_stages = sm["order"] + ([sm["parked"]] if sm["parked"] else [])
        extra = [s for s in vocab_stages if dstages and s not in dstages]
        if extra:
            out("FAIL vocab stages %s are not in the doctrine's stage list" % extra)
            problems.append("stages")
    notes = []
    try:
        req, opt, roles_map = skeleton(cfg, log=notes.append)
        text, ppath, is_seed, _ = playbook_source(cfg)
        out("playbook: %s%s" % (ppath, "  (PAYLOAD SEED: the owner copy is missing)" if is_seed else ""))
        raw = (_PLAYBOOK_CACHE.get(str(ppath)) or (None, ""))[1]
        if MARKER_PLACEHOLDER_RE.search(raw or ""):
            out("  the playbook writes <marker>; read as %s" % marker)
        out("  required sections (%d): %s" % (len(req), " | ".join(req)))
        out("  optional sections (%d): %s" % (len(opt), " | ".join(opt) or "(none)"))
        out("  section roles: %s" % " · ".join("%s=%r" % (k, v) for k, v in roles_map.items()))
        if is_seed:
            problems.append("playbook owner copy")
    except DossierError as e:
        out("FAIL playbook: %s" % e)
        problems.append("playbook")
    for n in notes:
        out("  " + n)
    pdir = people_dir(cfg)
    if pdir is None or not pdir.is_dir():
        out("FAIL people folder: %s" % (pdir or "paths.people_dir is not set"))
        problems.append("people_dir")
    else:
        files = [p for p in pdir.glob("*.md") if not p.name.startswith("_")]
        with_jid = 0
        for p in files:
            try:
                if parse_frontmatter(p.read_text(encoding="utf-8")[:3000])[0].get("wa_jid"):
                    with_jid += 1
            except OSError:
                pass
        out("people folder: %s · %d notes, %d with wa_jid" % (pdir, len(files), with_jid))
    flog = filing_log_path(cfg)
    out("filing log: %s" % ((str(flog) + ("" if flog.exists() else "  (does not exist yet)")) if flog else "(not set: no filing line is written)"))
    out("check-live: %s" % ("ok" if not problems else "%d problem(s): %s" % (len(problems), ", ".join(problems))))
    return 0 if not problems else 1


# ---------------------------------------------------------------- selftest (fakes in temp dirs only)

FAKE_DOCTRINE = """# Structure doctrine (fake, selftest)

## 8 · Record schema

```yaml
families:
  entity:
    client:          {required: [type, status],
                      optional: [renew_by]}
  person:     {required: [type, roles],
               optional: [roles_remark, stage, stage_since, last_contact, since,
                          wa_jid, wa_phone, crm_id, dossier_updated],
               multi: [roles],
               roles: [lead, member, client, partner, network],
               stage: [new, talking, offered, won, active, done, parked]}
                                          # every person the owner keeps a note on
                                          #   lead         came to an event, has not bought
                                          #   member joined a course or the community
                                          #   client       buys a service
                                          #   partner      shares money or invests together
                                          #   network      pure network: intel, intros,
                                          #                partner of nobody, friends and neighbours
                                          # `stage:` only for lead, member and client
  family:     {required: [type]}
```
"""

FAKE_PLAYBOOK = """---
type: playbook
---

# Playbook: 写一个真人的档 (fake, selftest)

### 骨架 (照这个顺序, 节名照抄)

```
---
(档头)
---

# <名字>

## 1 · 他是谁 (注解)
| 表 |

## 2 · 他的问题
## 3 · 他要的结果 (他自己描述的, 不是我写的)
## 4 · 市场与异议
## 5 · 现在的状态与下一步
## 6 · 怎么跟他讲话
- **下一封怎么开口**: 一句话
## ⬜ 下次见面要问的
## 系统记录 (自动)
(机器给的节; 机器没给就整节不写, 这节可以没有)
## WhatsApp 近况 (自动)
## QX 手记
(旧档多半没有, 这节可以没有)
## 这份档的备注
- 私事提示
```

### 写死的规矩
## 不算节
"""


def _fake_doc(sec1_extra="", notes="<!-- qx -->\n(还没有)", gaps="1. ⬜ 他怎么认识 QX 的?\n2. ⬜ 预算多少?", intro="> 假人。",
              fm_extra="", roles="[lead]", recent="- 2026-03-12 · 一件事", tail="- ⛔ 私事提示。"):
    return ("---\ntype: person\nroles: " + roles + "\nstage: talking\nstage_since: 2026-03-10\nsince: 2026-03-10\nwa_jid: 111@lid\n"
            + fm_extra + "dossier_updated: 2026-03-12\n---\n\n"
            "# 假人\n\n" + intro + "\n\n## 1 · 他是谁\n\n| | |\n|---|---|\n| **名字** | 假人 👁 |\n" + sec1_extra +
            "\n## 2 · 他的问题\n- ⬜\n\n## 3 · 他要的结果\n- ⬜\n\n## 4 · 市场与异议\n- ⬜\n\n## 5 · 现在的状态与下一步\n- ⬜\n\n"
            "## 6 · 怎么跟他讲话\n- **下一封怎么开口**: ⬜\n\n## ⬜ 下次见面要问的\n" + gaps + "\n\n"
            "## WhatsApp 近况 (自动)\n<!-- wa-live:begin -->\n" + recent + "\n<!-- wa-live:end -->\n\n"
            "## QX 手记\n\n" + notes + "\n\n## 这份档的备注\n\n" + tail + "\n")


def _fake_cfg(root):
    vault = root / "vault"
    (vault / "99_Meta").mkdir(parents=True)
    (vault / "Playbooks" / "person-dossier").mkdir(parents=True)
    (vault / "People").mkdir(parents=True)
    (vault / "99_Meta" / "structure-doctrine.md").write_text(FAKE_DOCTRINE, encoding="utf-8")
    (vault / "Playbooks" / "person-dossier" / "person-dossier-playbook.md").write_text(FAKE_PLAYBOOK, encoding="utf-8")
    (vault / "99_Meta" / "filing-log.md").write_text("", encoding="utf-8")
    home = root / "home"
    (home / "live").mkdir(parents=True)
    return {
        "owner_label": "Quinn Xu", "owner_marker": "QX", "my_names": ["Quinn"],
        "paths": {"home": str(home), "live_dir": str(home / "live"), "vault": str(vault), "people_dir": "People",
                  "doctrine": "", "person_playbook": "Playbooks/person-dossier/person-dossier-playbook.md",
                  "filing_log": "99_Meta/filing-log.md"},
        "vocab": {"stages": list(DEFAULT_STAGE_KEYS), "park_from": list(DEFAULT_PARK_FROM)},
        "dossier": {}, "facts": {"source": "csv"}, "person": {},
    }


def selftest():
    import copy
    import io
    import tempfile
    global SEED_PLAYBOOK
    results = []

    def case(name, fn, expect_error=None):
        try:
            fn()
            ok, got = expect_error is None, "passed"
        except DossierError as e:
            ok, got = expect_error is not None and expect_error in str(e), "refused: %s" % e
        results.append(ok)
        print(("  ok    " if ok else "  FAIL  ") + name + ("" if ok else "   (%s)" % got))

    def check(name, ok, detail=""):
        results.append(bool(ok))
        print(("  ok    " if ok else "  FAIL  ") + name + ("" if ok else "   (%s)" % detail))

    saved_seed, saved_cfg_env = SEED_PLAYBOOK, os.environ.pop("WA_LIVE_CONFIG", None)
    with tempfile.TemporaryDirectory(prefix="dossier-core-selftest.") as td:
        root = Path(td)
        SEED_PLAYBOOK = root / "no-seed" / "person-dossier-playbook.md"
        cfg = _fake_cfg(root)
        vault = Path(cfg["paths"]["vault"])
        people = vault / "People"
        T = "2026-03-14"

        def fin(new, old, c=None, **kw):
            return finalize(new, "111@lid", old, cfg=c or cfg, today=T, **kw)

        # 1 to 13: owner words and marks (marker QX)
        base = _fake_doc()
        line1 = "| **怎么认识** | 🗣 QX 2026-03-14: 他其实是朋友 Ken 介绍的 (⚠️ 对话里没提过) |\n"
        note1 = "<!-- qx -->\n- 🗣 QX 2026-03-14「他其实是朋友 Ken 介绍的」→ 融进第 1 节"
        good = _fake_doc(sec1_extra=line1, notes=note1, gaps="1. ⬜ 预算多少?")
        case("1 owner update: placeholder replaced by the owner's line, tag in section 1, checks pass", lambda: fin(good, base))
        case("2 a note with no owner words passes against itself", lambda: fin(base, base))
        case("3 an owner notes line deleted is refused", lambda: fin(_fake_doc(sec1_extra=line1, notes="<!-- qx -->\n(还没有)"), good), "owner notes")
        case("4 the tag removed while the sentence stays is refused",
             lambda: fin(_fake_doc(sec1_extra=line1.replace("🗣 QX 2026-03-14: ", ""), notes=note1), good), "QX 03-14")
        rewritten = _fake_doc(sec1_extra="| **怎么认识** | 朋友 Ken 介绍来的 (🗣qx 3/14 亲口讲; 对话里 ⬜ 没提过) |\n",
                              notes="<!-- QX -->\n*   🗣 QX 2026-03-14「他其实是朋友 Ken   介绍的」→ 融进第 1 节",
                              gaps="1. ⬜ 预算多少?").replace("## 1 · 他是谁", "## 1 · 他是谁 (2026-03-14 补)")
        case("5 a model rewrite keeps the tag (3/14, glued, lower case, * bullet, spaces, heading note) and passes", lambda: fin(rewritten, good))
        case("6 a tag moved to another section is refused",
             lambda: fin(_fake_doc(notes=note1, gaps="1. ⬜ 预算多少?", intro="> 假人。朋友 Ken 介绍的 (🗣 QX 2026-03-14)。"), good), "'## 1 · 他是谁'")
        case("7 two tags on one day in one section, one left, is refused",
             lambda: fin(good, _fake_doc(sec1_extra=line1 + "| **生意** | 🗣 QX 3/14: 卖水 |\n", notes=note1)), "2 in the old note, 1 in the new")
        case("8 a conversation quote with a clock time is not locked",
             lambda: fin(base, _fake_doc(sec1_extra="| **钱** | 🗣 QX 03-12 13:09 说「免费做到好」 |\n")))
        case("9 a lock segment outside the owner notes changed is refused",
             lambda: fin(_fake_doc(intro="> 假人。\n\n<!-- qx -->\nQX 3/13 记: 他不急"), _fake_doc(intro="> 假人。\n\n<!-- qx -->\nQX 3/13 记: 他很急")), "locked segment")
        case("9b a lock segment with only extra spaces and a blank line passes",
             lambda: fin(_fake_doc(intro="> 假人。\n\n<!-- qx -->\n\nQX  3/13 记:  他很急"), _fake_doc(intro="> 假人。\n\n<!-- qx -->\nQX 3/13 记: 他很急")))
        case("10 a markdown rule dropped from the owner notes passes",
             lambda: fin(_fake_doc(notes="<!-- qx -->\n(还没有)"), _fake_doc(notes="<!-- qx -->\n(还没有。主人讲过的写在这里)\n\n---")))
        case("11 the owner's line moved out of the owner notes is refused",
             lambda: fin(good.replace(note1, "<!-- qx -->").replace("- ⛔ 私事提示。", "- ⛔ 私事提示。\n- 🗣 QX 2026-03-14「他其实是朋友 Ken 介绍的」→ 融进第 1 节"), good), "owner notes")
        case("12 the owner notes section removed is refused", lambda: fin(good.replace("## QX 手记\n\n" + note1 + "\n\n", ""), good), "")
        lock_rx, any_rx, _ = mark_regexes(cfg)
        check("13 both date spellings are one day · clock times do not lock · placeholder lines",
              mark_key(lock_rx.search("🗣 QX 2026-03-14")) == mark_key(lock_rx.search("🗣 QX 3/14")) == "03-14"
              and lock_rx.search("🗣 QX 03-12 13:09") is None and any_rx.search("🗣 QX 03-12") is not None
              and lock_rx.search("🗣 QXZ 3/14") is None
              and is_placeholder_line("⬜ (还没有口述)") and is_placeholder_line("(还没有)") and is_placeholder_line("---")
              and not is_placeholder_line("- 🗣 QX 2026-03-14「他其实是朋友 Ken 介绍的」"))
        fake_pb = ("# x\n### 骨架 (照这个顺序)\n\n```\n---\n(档头)\n---\n\n# <名字>\n\n## 1 · 他是谁 (注解)\n| 表 |\n\n## 2 · 新名字\n- a\n"
                   "## 3 · C\n## 4 · D\n## 5 · E\n\n## 系统记录 (自动)\n(机器没给就整节不写, 这节可以没有)\n## 这份档的备注\n```\n\n### 写死的规矩\n## 不算\n")
        r14, o14 = parse_playbook_sections(fake_pb)
        check("14 section names come from the skeleton · optional mark · notes removed · no skeleton gives None",
              r14 == ["## 1 · 他是谁", "## 2 · 新名字", "## 3 · C", "## 4 · D", "## 5 · E", "## 这份档的备注"] and o14 == ["## 系统记录"]
              and parse_playbook_sections("# no skeleton")[0] is None and section_key("## 3 · 他要的结果 (他自己描述的)") == "## 3 · 他要的结果",
              (r14, o14))
        r15, o15 = playbook_sections(cfg)
        check("15 the owner playbook is read through paths.person_playbook",
              r15 is not None and "## 6 · 怎么跟他讲话" in r15 and "## QX 手记" in (o15 or []), (r15, o15))

        # 15b to 15e: the seed's literal <marker> placeholder
        pb_marker = vault / "Playbooks" / "person-dossier" / "marker-placeholder.md"
        pb_marker.write_text(FAKE_PLAYBOOK.replace("## QX 手记", "## <marker> 手记"), encoding="utf-8")
        cfg_mk = copy.deepcopy(cfg)
        cfg_mk["paths"]["person_playbook"] = str(pb_marker)
        r15b, o15b = playbook_sections(cfg_mk)
        check("15b a playbook written with <marker> yields the owner's marker in its section names",
              "## QX 手记" in (o15b or []) and section_roles(cfg_mk)["owner_notes"] == "## QX 手记", (o15b, section_roles(cfg_mk)))
        case("15c with that playbook, a deleted owner notes line is still refused",
             lambda: fin(_fake_doc(sec1_extra=line1, notes="<!-- qx -->\n(还没有)"), good, c=cfg_mk), "owner notes")
        literal = _fake_doc(sec1_extra=line1.replace("🗣 QX", "🗣 <marker>"), notes=note1.replace("<!-- qx -->", "<!-- <marker> -->").replace("🗣 QX", "🗣 <marker>"),
                            gaps="1. ⬜ 预算多少?").replace("## QX 手记", "## <marker> 手记")
        placed_literal, _ = fin(literal, base, c=cfg_mk)
        check("15d a candidate that copied <marker> literally is stored with the real marker",
              "<!-- QX -->" in placed_literal and "🗣 QX 2026-03-14" in placed_literal and "## QX 手记" in placed_literal and "<marker>" not in placed_literal)
        case("15e and the owner's words in it are locked for the next rewrite",
             lambda: fin(_fake_doc(sec1_extra=line1, notes="<!-- qx -->\n(还没有)"), placed_literal, c=cfg_mk), "owner notes")
        seed_src = PAYLOAD / "playbooks" / "person-dossier" / "person-dossier-playbook.md"
        seed_copy = vault / "Playbooks" / "person-dossier" / "seed-copy.md"
        seed_ok = seed_src.is_file()
        if seed_ok:
            seed_copy.write_text(seed_src.read_text(encoding="utf-8"), encoding="utf-8")
        cfg_seed = copy.deepcopy(cfg)
        cfg_seed["paths"]["person_playbook"] = str(seed_copy)
        rs, os_ = playbook_sections(cfg_seed) if seed_ok else (None, None)
        check("15f the payload seed playbook parses to 9 required and 2 optional sections (owner notes as ## QX 手记)",
              seed_ok and rs is not None and len(rs) == 9 and sorted(os_ or []) == sorted(["## 系统记录", "## QX 手记"])
              and rs == ["## 1 · 他是谁", "## 2 · 他的问题", "## 3 · 他要的结果", "## 4 · 市场与异议", "## 5 · 现在的状态与下一步",
                         "## 6 · 怎么跟他讲话", "## ⬜ 下次见面要问的", "## WhatsApp 近况", "## 这份档的备注"],
              "seed %s: %s + %s" % ("found" if seed_ok else "MISSING at %s" % seed_src, rs, os_))
        case("15g a note built on that skeleton passes every check against the seed", lambda: fin(good, base, c=cfg_seed))

        # 16 to 18: section roles cannot be switched off silently
        renamed = FAKE_PLAYBOOK.replace("## 这份档的备注", "## 档案备注")
        pbp = vault / "Playbooks" / "person-dossier" / "renamed.md"
        pbp.write_text(renamed, encoding="utf-8")
        cfg_ren = copy.deepcopy(cfg)
        cfg_ren["paths"]["person_playbook"] = str(pbp)
        case("16 a section renamed in the playbook but not in person.section_roles refuses loudly",
             lambda: fin(base.replace("## 这份档的备注", "## 档案备注"), base.replace("## 这份档的备注", "## 档案备注"), c=cfg_ren), "notes='## 这份档的备注'")
        cfg_ren["person"]["section_roles"] = {"notes": "## 档案备注"}
        old_ren = _fake_doc(tail="- ⛔ 私事提示。\n- 主人记的一条备注").replace("## 这份档的备注", "## 档案备注")
        case("17 with the new name in person.section_roles, a dropped notes item is refused again",
             lambda: fin(_fake_doc().replace("## 这份档的备注", "## 档案备注"), old_ren, c=cfg_ren), "notes section")
        no_owner_pb = FAKE_PLAYBOOK.replace("## QX 手记\n(旧档多半没有, 这节可以没有)\n", "")
        pbp2 = vault / "Playbooks" / "person-dossier" / "no-owner-notes.md"
        pbp2.write_text(no_owner_pb, encoding="utf-8")
        cfg_off = copy.deepcopy(cfg)
        cfg_off["paths"]["person_playbook"] = str(pbp2)
        case("18a a playbook without the owner notes section refuses while the role still names it",
             lambda: fin(base, base, c=cfg_off), "owner_notes")
        cfg_off["person"]["section_roles"] = {"owner_notes": ""}
        case("18b an empty role switches that guard off on purpose", lambda: fin(base, base, c=cfg_off))

        # 19 to 22: doctrine keys, roles, stages, sources
        logs = []
        case("19a keys outside the doctrine are refused", lambda: fin(_fake_doc(fm_extra="status: active\n"), base), "outside the doctrine")
        case("19b a harmless extra key is dropped and logged", lambda: fin(_fake_doc(fm_extra="tags: [x]\n"), base, log=logs.append))
        check("19b log line", any("tags" in l for l in logs), logs)
        case("19c a key only the owner's doctrine allows passes", lambda: fin(_fake_doc(fm_extra="crm_id: A-17\n"), base))
        strict = root / "strict-doctrine.md"
        strict.write_text(FAKE_DOCTRINE.replace("{required: [type, roles],\n               optional: [roles_remark,",
                                                "{required: [type, roles, last_contact],\n               optional: [roles_remark,"), encoding="utf-8")
        cfg_strict = copy.deepcopy(cfg)
        cfg_strict["paths"]["doctrine"] = str(strict)
        case("19d a required key from the doctrine that is missing is refused", lambda: fin(base, base, c=cfg_strict), "last_contact")
        case("20a a role outside the closed list is refused", lambda: fin(_fake_doc(roles="[colleague]"), base), "closed list")
        case("20b a role of the old note removed is refused", lambda: fin(_fake_doc(roles="[lead]"), _fake_doc(roles="[lead, partner]")), "removed")
        net, _ = fin(_fake_doc(roles="[network]"), "")
        check("21a no stage for a person without a stage role", "stage:" not in parse_frontmatter(net)[0] and "stage_since" not in net.split("\n---")[0])
        slog = []
        won, info = fin(_fake_doc().replace("stage: talking", "stage: won"), "", log=slog.append)
        check("21b won without a payment record is capped at offered", info["stage"] == "offered" and any("capped" in l for l in slog), (info, slog))
        paid, info = fin(_fake_doc(), base, has_payment="active")
        check("21c a payment floor lifts the stage and resets stage_since", info["stage"] == "active" and "stage_since: 2026-03-14" in paid, info)
        parked, info = fin(_fake_doc().replace("stage: talking", "stage: parked"), base)
        check("21d parked is allowed from talking and keeps the rule forward only otherwise", info["stage"] == "parked", info)
        back, info = fin(_fake_doc().replace("stage: talking", "stage: new"), _fake_doc().replace("stage: talking", "stage: offered"))
        check("21e the stage never moves back", info["stage"] == "offered", info)
        cfg_rws = copy.deepcopy(cfg)
        cfg_rws["person"]["roles_with_stage"] = ["student"]
        case("21f roles_with_stage sharing nothing with the doctrine refuses loudly", lambda: fin(base, base, c=cfg_rws), "roles_with_stage")
        cfg_nodoc = copy.deepcopy(cfg)
        cfg_nodoc["paths"]["doctrine"] = str(root / "missing.md")
        case("22a an unreadable doctrine refuses", lambda: fin(base, base, c=cfg_nodoc), "doctrine not found")
        cfg_nopb = copy.deepcopy(cfg)
        cfg_nopb["paths"]["person_playbook"] = str(root / "missing-playbook.md")
        case("22b no owner playbook and no seed refuses", lambda: fin(base, base, c=cfg_nopb), "playbook not found")
        SEED_PLAYBOOK = root / "seed" / "person-dossier-playbook.md"
        SEED_PLAYBOOK.parent.mkdir()
        SEED_PLAYBOOK.write_text(FAKE_PLAYBOOK, encoding="utf-8")
        seed_log = []
        case("22c no owner playbook falls back to the payload seed", lambda: fin(base, base, c=cfg_nopb, log=seed_log.append))
        check("22c the fallback is said out loud", any("payload seed" in l for l in seed_log), seed_log)
        SEED_PLAYBOOK = root / "no-seed" / "person-dossier-playbook.md"

        # 23 facts section, 24 punctuation
        facts = "## 系统记录 (自动)\n" + FACTS_BEGIN + "\n- 👁 2026-03-01 · 付款: 课程\n" + FACTS_END + "\n"
        with_facts = base.replace("## WhatsApp 近况", facts + "\n## WhatsApp 近况")
        case("23a facts supplied but missing from the note is refused", lambda: fin(base, base, facts_block=facts), "does not carry")
        case("23b a facts section nobody supplied is refused", lambda: fin(with_facts, base, facts_block=""), "supplied none")
        case("23c an edited facts section is refused", lambda: fin(with_facts.replace("课程", "别的"), base, facts_block=facts), "differs")
        case("23d the supplied facts section verbatim passes", lambda: fin(with_facts, base, facts_block=facts))
        for pulled in ("事实 worker %s 拉", "facts worker pulled %s"):
            f_old = facts.replace("付款: 课程\n", "付款: 课程\n- 👁 来源: sheet, " + pulled % "2026-03-01" + "\n")
            f_new = f_old.replace(pulled % "2026-03-01", pulled % "2026-03-02")
            note_old = base.replace("## WhatsApp 近况", f_old + "\n## WhatsApp 近况")
            out23 = check_facts_block(note_old, f_new)
            check("23e a facts section that differs only in the pull date (%s) passes and takes the machine's date" % pulled.split()[0],
                  f_new != f_old and facts_inner(out23) == facts_inner(f_new)
                  and out23.split(FACTS_BEGIN, 1)[0] == note_old.split(FACTS_BEGIN, 1)[0]
                  and out23.split(FACTS_END, 1)[1] == note_old.split(FACTS_END, 1)[1])
            case("23f a pull date plus a changed fact is still refused (%s)" % pulled.split()[0],
                 lambda f_new=f_new, note_old=note_old: check_facts_block(note_old, f_new.replace("课程", "别的")), "differs")
        dash = _fake_doc(intro="> 假人—朋友。")
        kept, _ = fin(dash, base)
        cfg_p = copy.deepcopy(cfg)
        cfg_p["person"]["house_punctuation"] = True
        fixed, _ = fin(dash, base, c=cfg_p)
        check("24 house punctuation is off by default and replaces the em dash when on", "—" in kept and "—" not in fixed)
        case("25a a changed wa_jid is refused", lambda: fin(_fake_doc().replace("111@lid", "222@lid"), base), "wa_jid")
        case("25b a changed since is refused", lambda: fin(_fake_doc().replace("since: 2026-03-10\nwa_jid", "since: 2026-03-11\nwa_jid"), base), "since")

        # 26 placement safety
        body_new, _ = fin(base, "")
        old_umask = os.umask(0o077)   # the daemon's umask: a vault note must still land 0644
        try:
            target = place_note(cfg, "Tan Mei Ling.md", body_new, "", None, "selftest new", "selftest")
        finally:
            os.umask(old_umask)
        flog = (vault / "99_Meta" / "filing-log.md").read_text(encoding="utf-8")
        check("26a a new note is written and logged once", target.exists() and flog.count("Tan Mei Ling.md") == 1 and "new in `People/`" in flog, flog)
        check("26a a new note is 0644 like the rest of the vault, even under the daemon's umask 077",
              (os.stat(str(target)).st_mode & 0o777) == 0o644, oct(os.stat(str(target)).st_mode & 0o777))
        check("26a no user name leaks into vault text: a path under the home folder is written with ~",
              _rel(os.path.join(os.path.expanduser("~"), "x", "y.md"), None) == "~/x/y.md" and _rel(vault / "People" / "a.md", vault) == "People/a.md")
        before = target.read_text(encoding="utf-8")
        case("26b an existing target with empty `existing` is refused", lambda: place_note(cfg, "Tan Mei Ling.md", good, "", None, "x", "selftest"), "already exists")
        check("26b the file is untouched", target.read_text(encoding="utf-8") == before)
        target.write_text(before.replace("## 2 · 他的问题", "## 2 · 他的问题\n- 主人刚在编辑器里加的一行"), encoding="utf-8")
        edited = target.read_text(encoding="utf-8")
        case("26c a note edited on disk since the run read it is refused", lambda: place_note(cfg, "Tan Mei Ling.md", good, before, None, "x", "selftest"), "changed on disk")
        check("26c the owner's edit survives", target.read_text(encoding="utf-8") == edited)
        target.write_text(before, encoding="utf-8")
        newer = before.replace("<!-- wa-live:begin -->\n", "<!-- wa-live:begin -->\n- 2026-03-14 · 刚追加的一行\n")
        target.write_text(newer, encoding="utf-8")
        plog = []
        cand, _ = fin(good, before)
        place_note(cfg, "Tan Mei Ling.md", cand, before, None, "carry", "selftest", log=plog.append)
        placed = target.read_text(encoding="utf-8")
        bdir = live_path(cfg) / "dossier-backups" / "Tan Mei Ling"
        backs = sorted(bdir.glob("*.md"))
        check("26d only the recent block moved on: newer lines carried in, owner update placed, disk version backed up",
              "刚追加的一行" in placed and "朋友 Ken" in placed and backs and backs[-1].read_text(encoding="utf-8") == newer and plog, (plog, len(backs)))
        for i in range(4):
            cur = target.read_text(encoding="utf-8")
            place_note(cfg, "Tan Mei Ling.md", cur.replace("一件事", "一件事%d" % i), cur, None, "keep", "selftest")
        check("26e backups keep the newest three", len(list(bdir.glob("*.md"))) == 3 and oct(os.stat(str(bdir)).st_mode & 0o777) == "0o700")
        okn = all(check_note_filename(n) for n in ("Tan Mei Ling.md", "陈大文.md", "Zoë-Ng_2.md", "สมชาย.md", "A. B.md"))
        bad_names = ["../x.md", "a/b.md", "a\\b.md", ".hidden.md", "x.txt", ".md", "a..b.md", "semi;colon.md", "tab\tname.md"]
        refused = 0
        for n in bad_names:
            try:
                check_note_filename(n)
            except DossierError:
                refused += 1
        check("26f file names: any script, digits, spaces, dot, dash, underscore pass; separators, .., hidden, odd characters refused",
              okn and refused == len(bad_names), refused)
        case("26g a note the run read that has since vanished is refused",
             lambda: place_note(cfg, "Gone Person.md", good, before, None, "x", "selftest"), "gone now")
        cfg_nolive = copy.deepcopy(cfg)
        cfg_nolive["paths"].pop("live_dir", None)
        cfg_nolive["paths"]["home"] = str(root / "not-created-home")
        cur = target.read_text(encoding="utf-8")
        case("26h a rewrite with no live folder in the config and no backups_dir is refused",
             lambda: place_note(cfg_nolive, "Tan Mei Ling.md", cur, cur, None, "x", "selftest"), "no backups folder")
        with people_lock(cfg_nolive):
            pass
        check("26h nothing is created outside the config's own folders (no default home, no lock file)",
              not (root / "not-created-home").exists() and target.read_text(encoding="utf-8") == cur)

        # 27 helpers that read the same names
        check("27 count_gaps, how_to_talk_from and warnings_for read the configured names",
              count_gaps(base, cfg) == 2 and how_to_talk_from(base.replace("**: ⬜", "**: 先答价钱, 中文为主"), cfg=cfg) == "先答价钱, 中文为主"
              and warnings_for(base.replace("- ⛔ 私事提示。", "- 其他"), cfg)[0].startswith("the fixed privacy line"))
        cfg_m = copy.deepcopy(cfg)
        cfg_m["owner_marker"] = ""
        check("28 an empty owner_marker falls back to the owner_label initials", owner_marker(cfg_m) == "QX")
        tpl = ("{OWNER_LABEL} {PEOPLE_DIR} {FACTS_SOURCE_LABEL} {MARKER} {NAME} {JID} {KIND} {WORLD} {STAGE} {PRIORITY} "
               "{BOARD_DATE} {REASON} {SUMMARY} {EXISTING_NOTE_LINE} {FACTS} {ROLES} {SPEC}")
        prompt = compose_prompt(cfg, tpl, "Tan Mei Ling", "111@lid", board={"world": "work"}, existing_name="Tan Mei Ling.md",
                                existing=base, facts_block="", pack="pack text", ctx_md="ctx text")
        check("29 compose_prompt fills every placeholder and appends note, pack and candidates",
              not re.search(r"\{[A-Z_]+\}", prompt) and "Quinn Xu People/ CSV QX" in prompt
              and "- `network`: pure network: intel, intros, partner of nobody, friends and neighbours" in prompt
              and "- `member`: joined a course or the community" in prompt and "- `partner`: shares money or invests together" in prompt
              and "# 对话包\n\npack text" in prompt, prompt[:300])
        schema = parse_person_family(FAKE_DOCTRINE)
        check("30 the doctrine reader handles a multi-line flow family, grouped families and wrapped role meanings",
              schema["required"] == ["type", "roles"] and "crm_id" in schema["optional"] and schema["multi"] == ["roles"]
              and schema["closed"]["stage"][-1] == "parked" and len(schema["meaning"]) == 5
              and parse_person_family("families:\n  family: {required: [type]}\n") is None)
        cfile = root / "home" / "live" / "config.json"
        cfile.write_text(json.dumps(cfg), encoding="utf-8")
        real_wc = globals()["_wa_common"]
        globals()["_wa_common"] = lambda: None
        buf = io.StringIO()
        try:
            code_ok = check_live(str(cfile), out=lambda s: buf.write(s + "\n"))
            cfg_bad = copy.deepcopy(cfg)
            cfg_bad["paths"]["person_playbook"] = str(root / "missing-playbook.md")
            cfile2 = root / "bad-config.json"
            cfile2.write_text(json.dumps(cfg_bad), encoding="utf-8")
            code_bad = check_live(str(cfile2), out=lambda s: None)
            cfile3 = root / "seed-config.json"
            cfile3.write_text(json.dumps(cfg_seed), encoding="utf-8")
            seed_buf = io.StringIO()
            code_seed = check_live(str(cfile3), out=lambda s: seed_buf.write(s + "\n"))
        finally:
            globals()["_wa_common"] = real_wc
        check("31 --check-live passes on a sound setup and fails on a missing playbook", code_ok == 0 and code_bad == 1, buf.getvalue()[-400:])
        seed_report = seed_buf.getvalue()
        check("31b --check-live on the seed copy reports 9 required and 2 optional sections and the <marker> reading",
              code_seed == 0 and "required sections (9)" in seed_report and "optional sections (2)" in seed_report
              and "## QX 手记" in seed_report and "writes <marker>" in seed_report, seed_report[-600:])
    SEED_PLAYBOOK = saved_seed
    if saved_cfg_env is not None:
        os.environ["WA_LIVE_CONFIG"] = saved_cfg_env
    print("dossier_core selftest: %d/%d ok" % (sum(results), len(results)))
    return 0 if all(results) else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        return selftest()
    if "--check-live" in argv:
        cfgp = None
        if "--config" in argv:
            i = argv.index("--config")
            cfgp = argv[i + 1] if i + 1 < len(argv) else None
        return check_live(cfgp)
    print("usage: dossier_core.py --selftest | --check-live [--config PATH]\n"
          "This is a module: dossier.py and the daemon import it.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
