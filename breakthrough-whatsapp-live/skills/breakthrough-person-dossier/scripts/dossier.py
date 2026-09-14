#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dossier.py · write, check and place a person note (breakthrough-whatsapp-live, companion skill breakthrough-person-dossier)

Any Claude Code session writes or rewrites a person note through the same pipeline the daemon uses. What a note says
is ruled by the owner's playbook (paths.person_playbook; read its folder door first) and its frontmatter by the owner's
doctrine; this script does the mechanical part and every check lives in dossier_core.py.

  prepare     --name NAME [--jid JID] [--days 30] [--out DIR] [--reason TEXT] [--owner-input TEXT]
              Pull the conversation pack (wa_sweep; voice goes into the shared transcript cache), the second-brain
              candidates (wa_context), the board's judgment and the facts section, and write prompt.md for this
              session to follow. --owner-input takes words the owner just said about this person ("update <name>'s
              note: ..."): they open the prompt as the highest source and are kept in owner-input.md for the quote check.
  verify      --name NAME --candidate FILE [--jid JID] [--existing FILE] [--pack FILE] [--show-quotes]
              Every check plus the quote check; writes nothing. --existing checks against another file as the old
              note (a rehearsal of the next rewrite). The quote check prints its count only; --show-quotes prints
              each quote (other people's words) with found or not found.
  place       --name NAME --candidate FILE [--jid JID] [--pack FILE] [--reason TEXT] [--force] [--show-quotes]
              Verify, then place: re-read the note on disk, back it up, write it, one filing-log line. A low quote hit
              rate refuses until a human has looked (--show-quotes) and adds --force.
  quotes      --candidate FILE --pack FILE    only the quote check
  check-live                                  what the checks will read from the owner's doctrine and playbook
  selftest                                    temp dirs and fakes only (also --selftest)

Every command takes --config PATH (default $WA_LIVE_CONFIG, else ~/WhatsApp-Live/live/config.json).
--name is the note's file name in the people folder without .md; a new note needs --jid.
⛔ Nothing here sends a message. Only `place` writes into the vault.
"""

import argparse
import contextlib
import datetime as dt
import io
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import dossier_core as C  # noqa: E402

SKILL_DIR = HERE.parent
PROMPT = SKILL_DIR / "assets" / "dossier-prompt.md"
OWNER_INPUT_FILE = "owner-input.md"
WHO = "skill breakthrough-person-dossier (session)"
QUOTE_MIN = 0.8
SWEEP_TIMEOUT = 1800
CONTEXT_TIMEOUT = 600
_WF = {"loaded": False, "mod": None, "error": None}


def die(msg):
    sys.stderr.write("dossier.py: %s\n" % msg)
    sys.exit(2)


def warn(msg):
    print("  ⚠ %s" % msg)


def wf():
    """daemon/wa_facts.py of this payload (facts section and payment floor), or None."""
    if not _WF["loaded"]:
        _WF["loaded"] = True
        d = C.PAYLOAD / "daemon"
        try:
            if (d / "wa_facts.py").is_file():
                if str(d) not in sys.path:
                    sys.path.append(str(d))
                import wa_facts  # noqa: E402
                _WF["mod"] = wa_facts
            else:
                _WF["error"] = "wa_facts.py is not in %s" % d
        except Exception as e:
            _WF["error"] = str(e)
    return _WF["mod"]


def load(a):
    try:
        return C.load_cfg(getattr(a, "config", None))
    except C.DossierError as e:
        die(str(e))


def note_path(cfg, name):
    people = C.people_dir(cfg)
    if people is None:
        die("paths.people_dir is not set (a relative people_dir also needs paths.vault)")
    try:
        return people / C.check_note_filename((name or "").strip() + ".md")
    except C.DossierError as e:
        die(str(e))


def jid_for(cfg, name, jid):
    if jid:
        return jid
    p = note_path(cfg, name)
    if not p.exists():
        die("there is no %s in %s; a new note needs --jid" % (p.name, p.parent))
    fm, _ = C.parse_frontmatter(p.read_text(encoding="utf-8")[:3000])
    if not fm.get("wa_jid"):
        die("%s has no wa_jid in its frontmatter; pass --jid" % p.name)
    return fm["wa_jid"]


def jid_forms(jid):
    wc = C._wa_common()
    if wc is not None and hasattr(wc, "jid_forms"):
        try:
            forms = wc.jid_forms(jid)
            if forms:
                return [jid] + sorted(f for f in forms if f != jid)
        except Exception:
            pass
    return [jid]


def board(cfg, jid):
    """The board's row for this person (world, stage, priority, summary, updated_at), read only; {} when absent."""
    live = C.live_path(cfg)
    db = live / "state.sqlite" if live is not None else None
    if db is None or not db.exists():
        return {}
    try:
        con = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        try:
            for j in jid_forms(jid):
                r = con.execute("SELECT world, stage, priority, summary, updated_at FROM conversations WHERE jid=?", (j,)).fetchone()
                if r:
                    return dict(r)
        finally:
            con.close()
    except Exception:
        return {}
    return {}


def _call(fn, *args):
    try:
        return fn(*args)
    except TypeError:
        return fn(*args[:-1])


def facts_for(cfg, jid):
    """-> (facts or None, facts section text, payment floor). The one floor rule and the one section renderer live in
    wa_facts; nothing is computed here."""
    mod = wf()
    if mod is None:
        live = C.live_path(cfg)
        if live is not None and (live / "facts.json").exists():
            warn("facts.json exists but wa_facts cannot be imported (%s): the facts section and the payment floor are not used" % _WF["error"])
        return None, "", False
    try:
        facts = mod.load_facts(cfg)
    except Exception as e:
        warn("facts.json could not be read: %s" % e)
        return None, "", False
    if not facts:
        return None, "", False
    try:
        return facts, _call(mod.facts_section, facts, jid, cfg) or "", _call(mod.stage_floor, facts, jid, cfg) or False
    except Exception as e:
        warn("wa_facts failed for %s: %s" % (jid, e))
        return None, "", False


def reader_dir(cfg):
    rd = C._paths(cfg).get("reader_dir")
    cands = []
    if rd and "<" not in str(rd):
        p = Path(os.path.expanduser(str(rd)))
        cands += [p, p / "skills" / "breakthrough-whatsapp-read" / "scripts"]
    cands.append(SKILL_DIR.parent / "breakthrough-whatsapp-read" / "scripts")
    for c in cands:
        if (c / "wa_sweep.py").is_file() and (c / "wa_context.py").is_file():
            return c
    die("reader scripts not found (paths.reader_dir, then this payload's skills/breakthrough-whatsapp-read/scripts)")


def python_bin(cfg):
    p = str(((cfg.get("bin") or {}) if isinstance(cfg.get("bin"), dict) else {}).get("python") or "")
    return p if p and os.path.isfile(p) and os.access(p, os.X_OK) else sys.executable


def reader_env(cfg):
    env = None
    wc = C._wa_common()
    if wc is not None and hasattr(wc, "subprocess_env"):
        try:
            env = wc.subprocess_env(cfg)
        except Exception:
            env = None
    if env is None:
        env = dict(os.environ)
        parts = [p for p in (env.get("PATH") or "").split(os.pathsep) if p]
        env["PATH"] = os.pathsep.join(parts + [d for d in ("/opt/homebrew/bin", "/usr/local/bin") if d not in parts])
    wm = str(((cfg.get("brain") or {}) if isinstance(cfg.get("brain"), dict) else {}).get("whisper_model") or "")
    if wm and os.path.exists(os.path.expanduser(wm)):
        env["WA_WHISPER_MODEL"] = os.path.expanduser(wm)
    elif env.get("WA_WHISPER_MODEL") and not os.path.exists(env["WA_WHISPER_MODEL"]):
        env.pop("WA_WHISPER_MODEL", None)   # only a model that exists is passed on
    return env


def _private_dir(p):
    p = Path(p)
    new = not p.exists()
    p.mkdir(parents=True, exist_ok=True)
    if new:
        try:
            os.chmod(str(p), 0o700)
        except OSError:
            pass
    return p


def _write_private(path, text):
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


def reader_workdir(cfg, jid):
    """The reader's own work dir for this person, with the shared transcript cache linked in (so the board shows every
    transcript) and the reader config copied fresh from <home>/config.json. paths.home comes from the config only."""
    home = C._expand(C._paths(cfg).get("home"), cfg)
    if home is None:
        die("paths.home is not set in the config")
    work = _private_dir(home / "runs-work" / ("dossier-cli-" + re.sub(r"[^A-Za-z0-9._-]", "_", jid)))
    shared = _private_dir(home / "transcripts-cache")
    link = work / "transcripts-cache"
    if not link.is_symlink():
        if link.exists():
            link.rename(work / ("transcripts-cache.old-%d" % int(time.time())))
        link.symlink_to(shared)
    src = home / "config.json"
    if src.is_file():
        shutil.copyfile(str(src), str(work / "config.json"))
        os.chmod(str(work / "config.json"), 0o600)
        return work, src
    warn("no reader config at %s (run wa_setup.py sync-reader-config): the reader falls back to its defaults" % src)
    return work, None


def owner_input_block(cfg, text, today=None):
    """Words the owner said about this person -> the section that opens the prompt; "" when there are none.
    The words are kept verbatim (only outer whitespace trimmed)."""
    text = (text or "").strip()
    if not text:
        return ""
    today = today or dt.date.today().isoformat()
    label = C.owner_label(cfg)
    tag = "🗣 %s %s" % (C.owner_marker(cfg), today)
    roles = C.section_roles(cfg)
    notes = roles["owner_notes"] or "主人手记"
    gaps = roles["gaps"] or "下次见面要问的"
    quoted = "\n".join("> " + l if l.strip() else ">" for l in text.splitlines())
    return ("## %s 这次讲的 (最高来源)\n\n"
            "日期: %s · 标记写法: `%s`\n\n"
            "原话 (照录):\n\n%s\n\n"
            "这次不是整份重写, 是把上面这段话**融进现有的档**。三条规矩:\n\n"
            "1. **%s 讲的是最高来源。** 跟对话包、第二大脑候选、分诊摘要冲突时以 %s 为准, 并把两边都写出来 (例「%s 说 X; ⚠️ 对话里他 MM-DD 说 Y」)。\n"
            "2. **只动跟这段话有关的格。** 融进骨架里该去的节 (节名照规格「骨架」那段), 每处标 `%s`; 「%s」那节留一行原话当出处; ⛔ 不准趁机重写别的节。\n"
            "3. **⬜ 被答了就改成事实, 不留 ⬜。** 格里的 ⬜ 换成带 `%s` 的事实; 「%s」里被答掉的那题删掉, 后面的重新编号。\n\n"
            "---\n\n") % (label, today, tag, quoted, label, label, tag, tag, notes, tag, gaps)


def _door_hint(cfg):
    pb = C.playbook_path(cfg)
    return str(pb.parent) if pb else "the person dossier playbook folder"


def cmd_prepare(a):
    cfg = load(a)
    jid = jid_for(cfg, a.name, a.jid)
    note = note_path(cfg, a.name)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    live = C.live_path(cfg)
    if not a.out and live is None:
        die("the config names no live folder (paths.live_dir); pass --out")
    out = _private_dir(Path(a.out).expanduser() if a.out else
                       live / "dossier-cli" / ("%s-%s" % (stamp, re.sub(r"[/\\:]", "_", a.name.strip()))))
    rd, py, env = reader_dir(cfg), python_bin(cfg), reader_env(cfg)
    work, reader_cfg = reader_workdir(cfg, jid)
    sweep_json = out / "sweep.json"
    print("-> wa_sweep, %d days (a first read with many voice notes takes a while) ..." % a.days, flush=True)
    try:
        r = subprocess.run([py, str(rd / "wa_sweep.py"), "--work-dir", str(work), "--only-jid", jid, "--days", str(a.days),
                            "--out", str(sweep_json)], capture_output=True, text=True, env=env, timeout=SWEEP_TIMEOUT)
    except subprocess.TimeoutExpired:
        die("wa_sweep timed out after %ds" % SWEEP_TIMEOUT)
    if r.returncode != 0 or not sweep_json.with_suffix(".md").exists():
        die("wa_sweep failed (exit %d): %s" % (r.returncode, (r.stderr or r.stdout)[-600:].strip()))
    pack = sweep_json.with_suffix(".md").read_text(encoding="utf-8")
    cargs = [py, str(rd / "wa_context.py"), "--sweep", str(sweep_json), "--out", str(out / "context.json")]
    if reader_cfg:
        cargs += ["--config", str(reader_cfg)]
    vault = C.vault_dir(cfg)
    if vault:
        cargs += ["--vault", str(vault)]
    try:
        rc = subprocess.run(cargs, capture_output=True, text=True, env=env, timeout=CONTEXT_TIMEOUT)
        if rc.returncode == 0 and (out / "context.md").exists():
            ctx_md = (out / "context.md").read_text(encoding="utf-8")
        else:
            ctx_md = "(第二大脑查找失败: exit %d, %s)" % (rc.returncode, (rc.stderr or rc.stdout)[-300:].strip())
    except Exception as e:
        ctx_md = "(第二大脑查找失败: %s)" % e
    existing = note.read_text(encoding="utf-8") if note.exists() else ""
    facts, fb, _ = facts_for(cfg, jid)
    try:
        tpl = PROMPT.read_text(encoding="utf-8")
        prompt = C.compose_prompt(cfg, tpl, a.name, jid, board=board(cfg, jid), reason=a.reason,
                                  existing_name=note.name if note.exists() else None, existing=existing, facts_block=fb,
                                  pack=pack, ctx_md=ctx_md, log=warn)
    except (OSError, C.DossierError) as e:
        die("cannot assemble the prompt: %s" % e)
    oi = owner_input_block(cfg, a.owner_input)
    if oi:
        prompt = oi + prompt
        _write_private(out / OWNER_INPUT_FILE, a.owner_input.strip() + "\n")
    _write_private(out / "prompt.md", prompt)
    _write_private(out / "pack.md", pack)
    if fb:
        _write_private(out / "facts.md", fb)
    if oi:
        print("✓ the prompt opens with what the owner said (%d characters); the words are also in %s" % (len(oi), OWNER_INPUT_FILE))
    print("✓ materials in %s\n  prompt.md (%d characters) · pack.md · context.md · facts section: %s\n"
          "next: in this session read prompt.md whole, write the note to %s/candidate.md (with the Write tool), then\n"
          "  %s %s place --config %s --name %s --candidate %s/candidate.md --reason \"...\"" % (
              out, len(prompt), ("facts.md, %d lines, placed verbatim" % len(fb.splitlines())) if fb else "none (not written)",
              out, python_bin(cfg), Path(__file__).resolve(), cfg.get("config_path") or "<config>", shlex.quote(a.name), out))
    return 0


def _verify(a, do_place):
    cfg = load(a)
    jid = jid_for(cfg, a.name, a.jid)
    try:
        cand = Path(a.candidate).expanduser().read_text(encoding="utf-8")
    except OSError as e:
        die("cannot read the candidate: %s" % e)
    header_name, cand = C.parse_file_header(cand)
    note = note_path(cfg, a.name)
    if header_name and header_name != note.name:
        print("  · the candidate's first line names %s; the note is placed as %s (--name)" % (header_name, note.name))
    existing = note.read_text(encoding="utf-8") if note.exists() else ""
    if getattr(a, "existing", None):
        existing = Path(a.existing).expanduser().read_text(encoding="utf-8")
        print("  · checking against %s as the old note (not the people folder copy)" % a.existing)
    facts, fb, floor = facts_for(cfg, jid)
    try:
        body, info = C.finalize(cand, jid, existing, board(cfg, jid).get("stage"), floor, log=lambda s: print("  ·", s),
                                facts_block=(fb if facts is not None else None), cfg=cfg)
    except C.DossierError as e:
        print("✗ FAIL:", e)
        return 1
    print("✓ checks passed (frontmatter, links, notes, lock segments, owner notes, owner marks, sections) · roles=%s stage=%s · %d lines · %d open questions"
          % (info["roles"], info["stage"], info["lines"], info["gaps"]))
    for w in C.warnings_for(body, cfg):
        print("  ⚠", w)
    sources = []
    pack = Path(a.pack).expanduser() if a.pack else Path(a.candidate).expanduser().parent / "pack.md"
    if pack.exists():
        sources.append(pack.read_text(encoding="utf-8"))
    sources.append(existing)
    oi = Path(a.candidate).expanduser().parent / OWNER_INPUT_FILE
    if oi.exists():
        sources.append(oi.read_text(encoding="utf-8"))
    rows, hits, n = C.quotes_check(body, sources)
    if getattr(a, "show_quotes", False):   # other people's own words: printed only when asked for, never by default
        for ok, q, f, t in rows:
            print(("  ✓ " if ok else "  ✗ ") + q[:60] + ("" if ok else "   (%d/%d fragments found)" % (f, t)))
    low = bool(n) and hits / n < QUOTE_MIN
    print("  %d quotes checked, %d found%s" % (n, hits, " (LOW: a human looks before placing)" if low else ""))
    if not do_place:
        return 0
    if low and not a.force:
        print("✗ low quote hit rate: run it again with --show-quotes to see the quotes not found, look, then add --force")
        return 1
    live = C.live_path(cfg)
    try:
        target = C.place_note(cfg, note.name, body, existing, (live / "dossier-backups") if live is not None else None,
                              a.reason or "session, by hand", WHO, log=lambda s: print("  ·", s))
    except C.DossierError as e:
        print("✗ FAIL:", e)
        return 1
    print("→ placed %s\n  next: add one row to Recent runs in the playbook folder's door (%s)" % (target, _door_hint(cfg)))
    return 0


def cmd_quotes(a):
    body = Path(a.candidate).expanduser().read_text(encoding="utf-8")
    rows, hits, n = C.quotes_check(body, [Path(a.pack).expanduser().read_text(encoding="utf-8")])
    for ok, q, f, t in rows:
        print(("  ✓ " if ok else "  ✗ ") + q[:60] + ("" if ok else "   (%d/%d)" % (f, t)))
    print("%d quotes checked, %d found" % (n, hits))
    return 0 if not n or hits / n >= QUOTE_MIN else 1


# ---------------------------------------------------------------- selftest (fakes in temp dirs only)

FAKE_SWEEP = r'''import argparse, json
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("--work-dir"); p.add_argument("--only-jid"); p.add_argument("--days"); p.add_argument("--out")
a = p.parse_args()
out = Path(a.out)
out.write_text(json.dumps({"chats": [{"jid": a.only_jid, "kind": "direct", "name": "Tan Mei Ling", "participants": []}]}))
out.with_suffix(".md").write_text("# WhatsApp 全窗阅读包\n\n- 09:00 Tan Mei Ling: 我想报名周六的课, 可以先分期付款吗\n")
print(json.dumps({"ok": True}))
'''
FAKE_CONTEXT = r'''import argparse, json
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("--sweep"); p.add_argument("--out"); p.add_argument("--config"); p.add_argument("--vault")
a = p.parse_args()
out = Path(a.out)
out.write_text(json.dumps({"ok": True, "config": a.config, "vault": a.vault}))
out.with_suffix(".md").write_text("# 第二大脑查找\n\n- (零命中)\n")
'''


def selftest():
    res = []

    def chk(name, ok, detail=""):
        res.append(bool(ok))
        print(("  ok    " if ok else "  FAIL  ") + name + ("" if ok else "   (%s)" % detail))

    def run(argv):
        buf = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                code = main(argv)
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else 1
        return code, buf.getvalue()

    saved_env = os.environ.pop("WA_LIVE_CONFIG", None)
    saved_seed = C.SEED_PLAYBOOK
    _WF.update(loaded=True, mod=None, error="selftest")
    try:
        with tempfile.TemporaryDirectory(prefix="dossier-cli-selftest.") as td:
            root = Path(td)
            C.SEED_PLAYBOOK = root / "no-seed.md"
            cfg = C._fake_cfg(root)
            chk("1 no owner words (None, empty, blank) gives an empty block, so the prompt is unchanged",
                owner_input_block(cfg, None) == "" and owner_input_block(cfg, "") == "" and owner_input_block(cfg, "  \n ") == "")
            b = owner_input_block(cfg, "  他其实是朋友 Ken 介绍的\n\n不是网上看到的  ", today="2026-03-14")
            chk("2 the heading is the first line and names the owner", b.splitlines()[0] == "## Quinn Xu 这次讲的 (最高来源)", b.splitlines()[0])
            chk("3 the words are quoted verbatim (lines, inner spaces, outer whitespace trimmed)", "> 他其实是朋友 Ken 介绍的\n>\n> 不是网上看到的\n" in b)
            chk("4 date and tag spelling", "日期: 2026-03-14" in b and "`🗣 QX 2026-03-14`" in b)
            chk("5 the three rules and the configured section names are there",
                all(k in b for k in ("最高来源。", "只动跟这段话有关的格", "⬜ 被答了就改成事实", "「## QX 手记」", "「## ⬜ 下次见面要问的」")))
            chk("6 house writing rule: no em dash, no en dash, no spaced double hyphen", "—" not in b and "–" not in b and " -- " not in b)
            chk("7 the tag is one the checks lock on", C.mark_regexes(cfg)[0].search(b) is not None)

            reader = root / "reader"
            reader.mkdir()
            (reader / "wa_sweep.py").write_text(FAKE_SWEEP, encoding="utf-8")
            (reader / "wa_context.py").write_text(FAKE_CONTEXT, encoding="utf-8")
            cfg["paths"]["reader_dir"] = str(reader)
            home = Path(cfg["paths"]["home"])
            (home / "config.json").write_text(json.dumps({"my_names": ["Quinn"]}), encoding="utf-8")
            cfile = home / "live" / "config.json"
            cfile.write_text(json.dumps(cfg), encoding="utf-8")
            draft = root / "draft"
            code, out = run(["prepare", "--config", str(cfile), "--name", "Tan Mei Ling", "--jid", "111@lid", "--out", str(draft),
                             "--owner-input", "她是朋友介绍的"])
            prompt = (draft / "prompt.md").read_text(encoding="utf-8") if (draft / "prompt.md").exists() else ""
            chk("8 prepare assembles prompt.md with every placeholder filled (template from assets)",
                code == 0 and prompt and not re.search(r"\{[A-Z_]+\}", prompt) and "Quinn Xu 私人 WhatsApp" in prompt
                and "# 对话包" in prompt and "分期付款" in prompt and prompt.startswith("## Quinn Xu 这次讲的"), out[-500:])
            work = home / "runs-work" / "dossier-cli-111_lid"
            ctx = json.loads((draft / "context.json").read_text(encoding="utf-8")) if (draft / "context.json").exists() else {}
            chk("9 the reader runs in its own work dir with the shared transcript cache and a fresh reader config",
                (work / "transcripts-cache").is_symlink() and os.path.realpath(str(work / "transcripts-cache")) == os.path.realpath(str(home / "transcripts-cache"))
                and (work / "config.json").exists() and ctx.get("config") == str(home / "config.json") and ctx.get("vault") == cfg["paths"]["vault"], ctx)
            chk("10 owner-input.md keeps the words for the quote check", (draft / OWNER_INPUT_FILE).read_text(encoding="utf-8") == "她是朋友介绍的\n")

            cand = C._fake_doc(sec1_extra="| **想要** | 🗣 (03-12 09:00)「我想报名周六的课, 可以先分期付款吗」 |\n")
            (draft / "candidate.md").write_text("<!-- file: Tan Mei Ling.md -->\n" + cand, encoding="utf-8")
            people = Path(cfg["paths"]["vault"]) / "People"
            code, out = run(["verify", "--config", str(cfile), "--name", "Tan Mei Ling", "--jid", "111@lid", "--candidate", str(draft / "candidate.md")])
            chk("11 verify passes a sound candidate and writes nothing", code == 0 and "✓ checks passed" in out and not list(people.glob("*.md")), out[-400:])
            chk("11b verify prints no quote text by default", "我想报名周六的课" not in out and "1 quotes checked, 1 found" in out, out[-300:])
            code_q, out_q = run(["verify", "--config", str(cfile), "--name", "Tan Mei Ling", "--jid", "111@lid", "--candidate", str(draft / "candidate.md"), "--show-quotes"])
            chk("11c verify --show-quotes prints each quote", code_q == 0 and "✓ 我想报名周六的课" in out_q, out_q[-300:])
            code, out = run(["place", "--config", str(cfile), "--name", "Tan Mei Ling", "--jid", "111@lid", "--candidate", str(draft / "candidate.md"), "--reason", "selftest"])
            placed = people / "Tan Mei Ling.md"
            flog = (Path(cfg["paths"]["vault"]) / "99_Meta" / "filing-log.md").read_text(encoding="utf-8")
            chk("12 place writes the note (file name with spaces) and one filing-log line", code == 0 and placed.exists() and flog.count("Tan Mei Ling.md") == 1, out[-400:])
            before = placed.read_text(encoding="utf-8")
            (draft / "bad.md").write_text(cand.replace("roles: [lead]", "roles: [colleague]"), encoding="utf-8")
            code, out = run(["place", "--config", str(cfile), "--name", "Tan Mei Ling", "--candidate", str(draft / "bad.md")])
            chk("13 a candidate that breaks a rule is refused and the note stays as it was", code == 1 and "FAIL" in out and placed.read_text(encoding="utf-8") == before, out[-300:])
            (draft / "made-up.md").write_text(cand.replace("我想报名周六的课, 可以先分期付款吗", "我明天一定会把全部学费转过来给你的"), encoding="utf-8")
            code, out = run(["place", "--config", str(cfile), "--name", "Tan Mei Ling", "--candidate", str(draft / "made-up.md")])
            chk("14 a quote not found in the sources blocks place without --force", code == 1 and "LOW" in out and placed.read_text(encoding="utf-8") == before, out[-300:])
            code, out = run(["quotes", "--candidate", str(draft / "candidate.md"), "--pack", str(draft / "pack.md")])
            chk("15 quotes alone finds the real quote in the pack", code == 0 and "1 quotes checked, 1 found" in out, out[-200:])
            code, out = run(["verify", "--config", str(cfile), "--name", "Nobody", "--candidate", str(draft / "candidate.md")])
            chk("16 a new note without --jid stops with a clear message", code == 2 and "needs --jid" in out, out[-200:])
    finally:
        C.SEED_PLAYBOOK = saved_seed
        _WF.update(loaded=False, mod=None, error=None)
        if saved_env is not None:
            os.environ["WA_LIVE_CONFIG"] = saved_env
    print("dossier.py selftest: %d/%d ok" % (sum(res), len(res)))
    return 0 if all(res) else 1


def build_parser():
    ap = argparse.ArgumentParser(prog="dossier.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--config", default=None, help="live config.json (default $WA_LIVE_CONFIG, else ~/WhatsApp-Live/live/config.json)")
        return p

    p = common(sub.add_parser("prepare", help="assemble prompt.md for this session to write the note"))
    p.add_argument("--name", required=True, help="note file name in the people folder, without .md")
    p.add_argument("--jid", help="WhatsApp jid (required for a new note)")
    p.add_argument("--days", type=int, default=30, help="days of conversation to read (default 30)")
    p.add_argument("--out", help="draft folder (default <live>/dossier-cli/<time>-<name>)")
    p.add_argument("--reason", help="why this note is written now (goes into the prompt and the filing line)")
    p.add_argument("--owner-input", help="words the owner said about this person; they open the prompt as the highest source")
    p = common(sub.add_parser("verify", help="run every check on a candidate; writes nothing"))
    p.add_argument("--name", required=True)
    p.add_argument("--jid")
    p.add_argument("--candidate", required=True)
    p.add_argument("--existing", help="check against this file as the old note (default: the people folder copy)")
    p.add_argument("--pack", help="conversation pack for the quote check (default pack.md beside the candidate)")
    p.add_argument("--show-quotes", action="store_true", help="print each quote with found or not found (other people's words)")
    p = common(sub.add_parser("place", help="verify, back up, write into the people folder, one filing-log line"))
    p.add_argument("--name", required=True)
    p.add_argument("--jid")
    p.add_argument("--candidate", required=True)
    p.add_argument("--pack")
    p.add_argument("--reason")
    p.add_argument("--force", action="store_true", help="place despite a low quote hit rate, after a human look")
    p.add_argument("--show-quotes", action="store_true", help="print each quote with found or not found (other people's words)")
    p = common(sub.add_parser("quotes", help="only the quote check"))
    p.add_argument("--candidate", required=True)
    p.add_argument("--pack", required=True)
    common(sub.add_parser("check-live", help="report what the checks read from the owner's doctrine and playbook"))
    common(sub.add_parser("selftest", help="temp dirs and fakes only"))
    return ap


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        return selftest()
    ap = build_parser()
    a = ap.parse_args(argv)
    os.umask(0o077)   # what this writes holds other people's messages: owner only, like the daemon's own files
    if a.cmd == "prepare":
        return cmd_prepare(a)
    if a.cmd == "verify":
        return _verify(a, False)
    if a.cmd == "place":
        return _verify(a, True)
    if a.cmd == "quotes":
        return cmd_quotes(a)
    if a.cmd == "check-live":
        return C.check_live(a.config)
    if a.cmd == "selftest":
        return selftest()
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main() or 0)
