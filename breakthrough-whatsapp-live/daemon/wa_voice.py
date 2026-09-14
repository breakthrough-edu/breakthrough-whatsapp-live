#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wa_voice.py · the voice loop: the brain's draft and what the owner actually sent become a pair, and the pairs of one
world are fed back to the brain as writing examples.

Flow:
- The doorbell sees a message the owner sent (the outbound path of Doorbell.consider) -> on_outbound(...).
  First close this jid's stale pair; with an open pair, add this message (plain text only) to `sent` and close at 3;
  with no open pair, open one only when should_open passes, and this message becomes the first one.
- The daemon calls close_stale(...) about once a minute without a jid: every pair open for over 30 minutes closes.
- Closing a pair appends one line to `<live>/voice-pairs.jsonl`: {jid, world, run_id, draft, sent, opened_at, closed_at,
  closed: true}; a pair with nothing in `sent` writes no line.
- Brain prompt section 8: render_examples(examples_for(pairs_path, world)).

State lives in the `meta` table of state.sqlite (own connection, timeout 10), two key kinds only:
- `voice_open:<jid>`       the open pair (JSON)
- `voice_last_run:<jid>`   the run id that last opened a pair; a run's draft opens at most one pair

Rules:
- Plain text means wa_common.TEXT_TYPES (the text entries of the one CONTENT_TYPES table).
- A pair holds only the brain's draft and what the owner sent: never the other person's words, never their name.
- Examples never cross worlds: a pair from one world only feeds that same world (world keys come from the owner's vocab).
- Only those two meta key kinds are written; conversations, runs and inbound_log are read only.
- Times are local naive ISO strings to the second (same as the daemon's now_iso), compared with datetime.fromisoformat.
  on_outbound judges by the message's own msg_at (a daemon catching up after downtime still uses real times), and only
  falls back to now when msg_at cannot be read.

Python 3.9 standard library only (the daemon runs on /usr/bin/python3).
"""

import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys
import tempfile
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wa_common as W  # noqa: E402

TEXT_TYPES = tuple(W.TEXT_TYPES)  # the plain text entries of the one CONTENT_TYPES table
MAX_SENT = 3                     # a pair keeps at most 3 sent messages
PAIR_WINDOW_MIN = 30             # a pair closes 30 minutes after it opened
RUN_FRESH_HOURS = 24             # the draft's run must have finished within 24 hours before the outbound message
EXAMPLES_N = 12                  # how many pairs section 8 gets
CLIP = 300                       # draft and sent are each clipped to this many characters
NO_EXAMPLES = "(还没有范本)"
SEP = " ⏎ "                      # joins several sent messages


def now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


def _parse(ts):
    """Local naive ISO string -> datetime; unreadable gives None. Aware values are converted to local, then made naive."""
    if isinstance(ts, dt.datetime):
        d = ts
    else:
        s = str(ts or "").strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            d = dt.datetime.fromisoformat(s.replace(" ", "T", 1) if "T" not in s else s)
        except ValueError:
            return None
    if d.tzinfo is not None:
        d = d.astimezone().replace(tzinfo=None)
    return d


def _k_open(jid):
    return "voice_open:%s" % jid


def _k_last(jid):
    return "voice_last_run:%s" % jid


def _connect(state_db):
    conn = sqlite3.connect(str(state_db), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    return conn


def _get_meta(conn, key):
    r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r[0] if r else None


def _set_meta(conn, key, value):
    conn.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def _load_open(conn, jid):
    raw = _get_meta(conn, _k_open(jid))
    if not raw:
        return None
    try:
        p = json.loads(raw)
        return p if isinstance(p, dict) else None
    except Exception:
        return None


# ---------------------------------------------------------------- rules (one rule, one function)

def is_text(mtype, text):
    """Is an outbound message plain text: mtype in (0, 7, 41) and non blank text. A numeric string mtype is accepted."""
    try:
        m = int(str(mtype).strip())
    except (TypeError, ValueError):
        return False
    return m in TEXT_TYPES and bool(str(text or "").strip())


def brain_draft(conn, run_id, fallback):
    """The draft the brain itself wrote in that run (runs.output draft), not conversations.draft: words the owner edited
    on the board are not a "draft". Falls back when output is unreadable, has no draft, or the column is missing."""
    try:
        r = conn.execute("SELECT output FROM runs WHERE id=?", (run_id,)).fetchone()
        d = json.loads(r[0]).get("draft") if (r and r[0]) else None
        return d if str(d or "").strip() else fallback
    except Exception:
        return fallback


def should_open(conn, jid, now):
    """Should this outbound message open a new pair. All five must hold:
    1) conversations.draft is non blank;
    2) world is non empty (no world means the brain never judged it: the owner typed that draft on the board);
    3) the last_run_id run has ok=1 and finished within the 24 hours before now (a run that finished after the
       message does not count: the owner had not seen that draft);
    4) voice_last_run:<jid> != last_run_id (one pair per run);
    5) not draft_overtaken (the owner had already replied while that run was going, so the draft landed stale)."""
    c = conn.execute("SELECT draft, world, last_run_id FROM conversations WHERE jid=?", (jid,)).fetchone()
    if c is None:
        return False
    draft, world, run_id = c[0], c[1], c[2]
    if not str(draft or "").strip() or not str(world or "").strip() or run_id is None:
        return False
    r = conn.execute("SELECT ok, finished_at, started_at FROM runs WHERE id=?", (run_id,)).fetchone()
    if r is None or str(r[0]) != "1":
        return False
    fin, ref = _parse(r[1]), _parse(now)
    if fin is None or ref is None:
        return False
    age = (ref - fin).total_seconds()
    if age < 0 or age > RUN_FRESH_HOURS * 3600:
        return False
    if str(_get_meta(conn, _k_last(jid)) or "") == str(run_id):
        return False
    return not draft_overtaken(conn, jid, r[2], now)


def draft_overtaken(conn, jid, run_started_at, now):
    """Was the draft overtaken: after that run started and strictly before now, inbound_log already has an outbound
    message for this jid (the brain was running for some seconds, the owner replied, the draft landed anyway). That
    draft was not what the owner sent, and a later message is not a reply to it either.
    No inbound_log or an unreadable started_at counts as not overtaken (read only, no table is created)."""
    st, ref = _parse(run_started_at), _parse(now)
    if st is None or ref is None:
        return False
    try:
        rows = conn.execute("SELECT msg_at FROM inbound_log WHERE jid=? AND decision LIKE 'outbound%'", (jid,)).fetchall()
    except sqlite3.OperationalError:
        return False
    for (m,) in rows:
        d = _parse(m)
        if d is not None and st <= d < ref:
            return True
    return False


def is_stale(pair, now):
    """Has an open pair gone stale: more than 30 minutes since opened_at. Unreadable opened_at is stale too
    (so a pair can never stay open forever)."""
    o, ref = _parse((pair or {}).get("opened_at")), _parse(now)
    if o is None or ref is None:
        return True
    return (ref - o).total_seconds() > PAIR_WINDOW_MIN * 60


# ---------------------------------------------------------------- actions

def open_pair(conn, jid, world, run_id, draft, opened_at):
    """Open a pair: write voice_open:<jid> (empty sent) and voice_last_run:<jid>, commit; return the pair.
    The caller adds the first message with add_sent."""
    pair = {"jid": jid, "world": world, "run_id": run_id, "draft": draft, "sent": [], "opened_at": opened_at}
    _set_meta(conn, _k_open(jid), json.dumps(pair, ensure_ascii=False))
    _set_meta(conn, _k_last(jid), run_id)
    conn.commit()
    return pair


def add_sent(conn, jid, text):
    """Add a sent message to the open pair; return how many it holds now (0 without an open pair; full pairs stay at 3)."""
    pair = _load_open(conn, jid)
    if pair is None:
        return 0
    sent = pair.get("sent") if isinstance(pair.get("sent"), list) else []
    if len(sent) < MAX_SENT:
        sent.append(str(text))
        pair["sent"] = sent
        _set_meta(conn, _k_open(jid), json.dumps(pair, ensure_ascii=False))
        conn.commit()
    return len(sent)


def _append_jsonl(pairs_path, obj):
    """Append one line, fsync. The file holds the owner's own messages, so it is created 0600."""
    p = Path(pairs_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def close_pair(conn, pairs_path, jid, closed_at):
    """Close a pair: append one jsonl line when sent is non empty, then delete voice_open:<jid> (voice_last_run stays).
    True = a line was written; False = no open pair or nothing was sent (the key is deleted either way).
    The line is written before the key is deleted: a crash in between rewrites one line at most, and examples_for
    deduplicates by (jid, run_id)."""
    pair = _load_open(conn, jid)
    raw = _get_meta(conn, _k_open(jid))
    if pair is None and raw is None:
        return False
    wrote = False
    if pair is not None and [s for s in (pair.get("sent") or []) if str(s).strip()]:
        out = {"jid": pair.get("jid") or jid, "world": pair.get("world"), "run_id": pair.get("run_id"),
               "draft": pair.get("draft") or "", "sent": list(pair.get("sent") or []),
               "opened_at": pair.get("opened_at"), "closed_at": closed_at, "closed": True}
        _append_jsonl(pairs_path, out)
        wrote = True
    conn.execute("DELETE FROM meta WHERE key=?", (_k_open(jid),))
    conn.commit()
    return wrote


def close_stale(state_db, pairs_path, now=None, jid=None):
    """Close stale pairs (open for over 30 minutes). With jid, look at that one only. Returns how many closed
    (including pairs that had nothing sent and wrote no line)."""
    now = now or now_iso()
    conn = _connect(state_db)
    try:
        return _close_stale_conn(conn, pairs_path, now, jid)
    finally:
        conn.close()


def _close_stale_conn(conn, pairs_path, now, jid=None):
    if jid is not None:
        keys = [_k_open(jid)]
    else:
        keys = [r[0] for r in conn.execute("SELECT key FROM meta WHERE key LIKE 'voice_open:%'").fetchall()]
    n = 0
    for k in keys:
        raw = _get_meta(conn, k)
        if raw is None:
            continue
        j = k[len("voice_open:"):]
        try:
            pair = json.loads(raw)
        except Exception:
            pair = None
        if pair is None or is_stale(pair, now):
            close_pair(conn, pairs_path, j, now)
            n += 1
    return n


def on_outbound(state_db, pairs_path, jid, text, mtype, msg_at, now=None):
    """Called when the doorbell sees a message the owner sent. Returns "opened" | "added" | "closed" | None.
    Judged at msg_at (now only when msg_at is unreadable):
    1) close this jid's stale pair first;
    2) with an open pair: plain text goes into sent (anything else returns None); at 3 the pair closes ("closed"),
       otherwise "added";
    3) with no open pair: open one only when should_open passes; plain text becomes the first message ("opened");
       otherwise None.
    The daemon must log the message in inbound_log before calling this (draft_overtaken reads inbound_log)."""
    ref = msg_at if _parse(msg_at) is not None else (now or now_iso())
    conn = _connect(state_db)
    try:
        _close_stale_conn(conn, pairs_path, ref, jid=jid)
        pair = _load_open(conn, jid)
        if pair is not None:
            # a new draft landed inside the 30 minute window (last_run_id changed): close the old pair first and judge
            # this message against the new run, otherwise a reply to the new draft would be paired with the old one
            cur = conn.execute("SELECT last_run_id FROM conversations WHERE jid=?", (jid,)).fetchone()
            if cur is not None and cur[0] is not None and str(cur[0]) != str(pair.get("run_id")):
                close_pair(conn, pairs_path, jid, ref)
                pair = None
        if pair is not None:
            if not is_text(mtype, text):
                return None
            n = add_sent(conn, jid, text)
            if n >= MAX_SENT:
                close_pair(conn, pairs_path, jid, ref)
                return "closed"
            return "added"
        if not should_open(conn, jid, ref):
            return None
        c = conn.execute("SELECT draft, world, last_run_id FROM conversations WHERE jid=?", (jid,)).fetchone()
        open_pair(conn, jid, c[1], c[2], brain_draft(conn, c[2], c[0]), ref)
        if is_text(mtype, text):
            add_sent(conn, jid, text)
        return "opened"
    finally:
        conn.close()


# ---------------------------------------------------------------- feeding examples

def read_pairs(pairs_path):
    """Every legal jsonl line (bad lines and non objects skipped), in file order = closing order. Missing file gives []."""
    out = []
    try:
        with open(str(pairs_path), "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if isinstance(o, dict):
                    out.append(o)
    except (IOError, OSError):
        return []
    return out


def _truthy(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes")


def examples_for(pairs_path, world, n=EXAMPLES_N):
    """The latest n closed pairs of the same world with something sent. Order: oldest first, newest last (closest to
    where the brain continues writing). World match ignores surrounding blanks and case; an empty world gives [].
    Repeated (jid, run_id) lines keep the last one."""
    w = str(world or "").strip().lower()
    if not w:
        return []
    keep = {}
    order = []
    for o in read_pairs(pairs_path):
        if str(o.get("world") or "").strip().lower() != w or not _truthy(o.get("closed")):
            continue
        sent = o.get("sent")
        if isinstance(sent, str):
            sent = [sent]
        if not isinstance(sent, list) or not [s for s in sent if str(s).strip()]:
            continue
        o = dict(o)
        o["sent"] = [str(s) for s in sent if str(s).strip()]
        key = (str(o.get("jid")), str(o.get("run_id")))
        if key in keep:
            order.remove(key)
        keep[key] = o
        order.append(key)
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = EXAMPLES_N
    return [keep[k] for k in order[-n:]] if n > 0 else []


def _flat(s, limit=CLIP):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= limit else s[:limit] + "…"


def render_examples(pairs):
    """Body of brain prompt section 8 (the daemon writes the heading). No pairs gives NO_EXAMPLES.
    Two lines per pair: `- 草稿: …` and `  实发: …` (several messages joined with ⏎); each clipped to 300, one line each."""
    lines = []
    for p in pairs or []:
        sent = p.get("sent") or []
        if isinstance(sent, str):
            sent = [sent]
        sent = [re.sub(r"\s+", " ", str(s)).strip() for s in sent if str(s).strip()]
        if not sent:
            continue
        lines.append("- 草稿: %s" % (_flat(p.get("draft")) or "(空)"))
        lines.append("  实发: %s" % _flat(SEP.join(sent)))
    return "\n".join(lines) if lines else NO_EXAMPLES


# ---------------------------------------------------------------- read only look at the real state

def _ro(path):
    return sqlite3.connect("file:%s?mode=ro" % urllib.parse.quote(str(path)), uri=True, timeout=5)


def probe_open_candidates(state_db, now=None):
    """Read only: which conversations would open a pair if the owner sent a message right now.
    Returns [(jid, world, run_id, finished_at)]."""
    now = now or now_iso()
    conn = _ro(state_db)
    try:
        out = []
        for r in conn.execute("SELECT jid, world, last_run_id FROM conversations").fetchall():
            if should_open(conn, r[0], now):
                fin = conn.execute("SELECT finished_at FROM runs WHERE id=?", (r[2],)).fetchone()
                out.append((r[0], r[1], r[2], fin[0] if fin else None))
        return out
    finally:
        conn.close()


def show(state_db, pairs_path, world=None):
    pairs = read_pairs(pairs_path)
    if not Path(pairs_path).exists():
        print("voice-pairs.jsonl: not yet (%s)" % pairs_path)
    else:
        print("voice-pairs.jsonl: %d legal lines (%s)" % (len(pairs), pairs_path))
        by = {}
        for o in pairs:
            by[str(o.get("world"))] = by.get(str(o.get("world")), 0) + 1
        for w in sorted(by):
            print("  %s: %d" % (w, by[w]))
    if world:
        ex = examples_for(pairs_path, world)
        print("section 8 would get (world=%s, %d pairs):" % (world, len(ex)))
        print(render_examples(ex))
    if Path(state_db).exists():
        conn = _ro(state_db)
        try:
            rows = conn.execute("SELECT key, value FROM meta WHERE key LIKE 'voice_%'").fetchall()
        finally:
            conn.close()
        opens = [r for r in rows if r[0].startswith("voice_open:")]
        print("open pairs: %d · voice_last_run keys: %d" % (len(opens), len(rows) - len(opens)))
        for k, v in opens:
            try:
                p = json.loads(v)
                print("  %s · world=%s run=%s · opened %s · %d sent" % (k[len("voice_open:"):], p.get("world"), p.get("run_id"),
                                                                        p.get("opened_at"), len(p.get("sent") or [])))
            except Exception:
                print("  %s · (unreadable JSON)" % k)
        cands = probe_open_candidates(state_db)
        print("conversations that would open a pair if the owner sent a message now: %d" % len(cands))
        for jid, w, rid, fin in cands:
            print("  %s · world=%s · run #%s finished %s" % (jid, w, rid, fin))
    else:
        print("state.sqlite: missing (%s)" % state_db)


# ---------------------------------------------------------------- selftest

def _selftest():
    tmp = Path(tempfile.mkdtemp(prefix="wa_voice_selftest_"))
    db = tmp / "state.sqlite"
    jl = tmp / "voice-pairs.jsonl"
    results = []

    def check(ok, msg):
        results.append((bool(ok), msg))
        print(("  ok   " if ok else "  FAIL ") + msg)

    conn = sqlite3.connect(str(db))
    conn.executescript("""
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE conversations(jid TEXT PRIMARY KEY, name TEXT, kind TEXT, draft TEXT, world TEXT, last_run_id INTEGER);
    CREATE TABLE runs(id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, jid TEXT, ok INTEGER);
    CREATE TABLE inbound_log(pk INTEGER PRIMARY KEY, seen_at TEXT, msg_at TEXT, jid TEXT, name TEXT, kind TEXT, mtype INTEGER, decision TEXT, policy TEXT);
    """)
    conn.commit()
    T0 = dt.datetime(2026, 9, 14, 10, 0, 0)

    def at(minutes):
        return (T0 + dt.timedelta(minutes=minutes)).isoformat(timespec="seconds")

    def setup(jid, draft, world, run_fin_min, ok=1):
        cur = conn.execute("INSERT INTO runs(started_at, finished_at, jid, ok) VALUES(?,?,?,?)", (at(run_fin_min - 1), at(run_fin_min), jid, ok))
        conn.execute("INSERT OR REPLACE INTO conversations(jid, name, kind, draft, world, last_run_id) VALUES(?,?,?,?,?,?)",
                     (jid, "test contact", "direct", draft, world, cur.lastrowid))
        conn.commit()
        return cur.lastrowid

    def lines_for(jid):
        return [o for o in read_pairs(jl) if o.get("jid") == jid]

    def open_key(jid):
        return _get_meta(conn, _k_open(jid))

    print("case 1 · a draft, one outbound message, close_stale at 31 minutes -> one jsonl line with 1 sent")
    j1 = "test-a01@s.whatsapp.net"
    setup(j1, "好的, 我 [日期] 发你", "work", -10)
    check(on_outbound(db, jl, j1, "ok 我明天发你哦", 0, at(0)) == "opened", "first message returns opened")
    check(close_stale(db, jl, now=at(20)) == 0, "not closed at 20 minutes")
    check(close_stale(db, jl, now=at(31)) == 1, "one closed at 31 minutes")
    L = lines_for(j1)
    check(len(L) == 1 and L[0]["sent"] == ["ok 我明天发你哦"] and L[0]["closed"] is True and L[0]["closed_at"] == at(31), "one jsonl line, 1 sent, closed true")
    check(set(L[0].keys()) == {"jid", "world", "run_id", "draft", "sent", "opened_at", "closed_at", "closed"},
          "a line has exactly the eight contract keys (no other person's words, no name)")
    check(open_key(j1) is None, "voice_open key deleted")
    check((jl.stat().st_mode & 0o777) == 0o600, "voice-pairs.jsonl is created 0600")

    print("case 2 · two outbound messages -> added; the closed pair has 2 sent")
    j2 = "test-a02@s.whatsapp.net"
    setup(j2, "Hi, 10 月场是 [日期]", "work", -5)
    r = [on_outbound(db, jl, j2, "Hi there", 0, at(0)), on_outbound(db, jl, j2, "10 月场是 18 号\n你看看", 7, at(3))]
    check(r == ["opened", "added"], "returns opened, added (got %s)" % r)
    close_stale(db, jl, now=at(40))
    L = lines_for(j2)
    check(len(L) == 1 and L[0]["sent"] == ["Hi there", "10 月场是 18 号\n你看看"], "2 sent (mtype 7 counts as text)")

    print("case 3 · four outbound messages -> closes at the third, the fourth neither joins nor reopens")
    j3 = "test-a03@s.whatsapp.net"
    rid3 = setup(j3, "收到, 我查了回你", "side", -60)
    r = [on_outbound(db, jl, j3, "a", 0, at(0)), on_outbound(db, jl, j3, "b", 0, at(1)),
         on_outbound(db, jl, j3, "c", "41", at(2)), on_outbound(db, jl, j3, "d", 0, at(3))]
    check(r == ["opened", "added", "closed", None], "returns opened, added, closed, None (got %s)" % r)
    L = lines_for(j3)
    check(len(L) == 1 and L[0]["sent"] == ["a", "b", "c"] and L[0]["run_id"] == rid3, "one line with only the first 3 (string mtype \"41\" accepted)")
    check(open_key(j3) is None and _get_meta(conn, _k_last(j3)) == str(rid3), "no open pair, voice_last_run remembers the run")

    print("case 4 · a message after 31 minutes does not count: the old pair closes, the same run does not reopen")
    j4 = "test-a04@s.whatsapp.net"
    setup(j4, "明天 call?", "personal", -2)
    check(on_outbound(db, jl, j4, "好啊", 0, at(0)) == "opened", "first message opened")
    check(on_outbound(db, jl, j4, "几点?", 0, at(31)) is None, "the message at 31 minutes returns None")
    L = lines_for(j4)
    check(len(L) == 1 and L[0]["sent"] == ["好啊"] and L[0]["closed_at"] == at(31), "the old pair closed at 31 minutes with only the first message")
    check(open_key(j4) is None, "no new pair opened")

    print("case 5 · no draft, no pair (empty string, blanks, NULL)")
    for i, d in enumerate(("", "   \n ", None)):
        j = "test-b0%d@s.whatsapp.net" % i
        setup(j, d, "work", -1)
        check(on_outbound(db, jl, j, "hello", 0, at(0)) is None and open_key(j) is None, "draft=%r does not open" % (d,))

    print("case 6 · media outbound never joins sent; a pair with nothing sent writes no line")
    j6 = "test-a06@s.whatsapp.net"
    setup(j6, "我发你海报", "work", -3)
    check(on_outbound(db, jl, j6, "", 1, at(0)) == "opened", "an image opens the pair (sent empty)")
    check(on_outbound(db, jl, j6, "", 3, at(1)) is None, "a voice note returns None")
    check(on_outbound(db, jl, j6, "  ", 0, at(2)) is None, "blank text does not join either")
    check(close_stale(db, jl, now=at(45)) == 1 and lines_for(j6) == [], "one closed but no jsonl line")
    j6b = "test-a07@s.whatsapp.net"
    setup(j6b, "我发你海报", "work", -3)
    on_outbound(db, jl, j6b, "", 1, at(0))
    on_outbound(db, jl, j6b, "海报在上面", 0, at(2))
    close_stale(db, jl, now=at(45))
    check([o["sent"] for o in lines_for(j6b)] == [["海报在上面"]], "image then text: the text joins sent")

    print("case 7 · world NULL does not open (that draft was typed by the owner on the board)")
    j7 = "test-a08@s.whatsapp.net"
    setup(j7, "我自己打的草稿", None, -1)
    check(on_outbound(db, jl, j7, "hi", 0, at(0)) is None, "world NULL does not open")

    print("case 8 · a run over 24 hours old, a failed run, a run finishing after the message: none open")
    j8 = "test-a09@s.whatsapp.net"
    setup(j8, "旧草稿", "side", -(24 * 60 + 1))
    check(on_outbound(db, jl, j8, "hi", 0, at(0)) is None, "a run 24 hours and 1 minute old does not open")
    j8b = "test-a10@s.whatsapp.net"
    setup(j8b, "草稿", "side", -5, ok=0)
    check(on_outbound(db, jl, j8b, "hi", 0, at(0)) is None, "an ok=0 run does not open")
    j8c = "test-a11@s.whatsapp.net"
    setup(j8c, "草稿", "side", 2)
    check(on_outbound(db, jl, j8c, "hi", 0, at(0)) is None, "a run finishing 2 minutes after the message does not open")
    j8d = "test-a12@s.whatsapp.net"
    setup(j8d, "草稿", "side", -(23 * 60 + 59))
    check(on_outbound(db, jl, j8d, "hi", 0, at(0)) == "opened", "a run 23 hours 59 minutes old opens")
    close_stale(db, jl, now=at(60))
    j8e = "test-a13@s.whatsapp.net"
    setup(j8e, "草稿 (他跑的时候已经回了)", "side", -3)       # run started at(-4), finished at(-3)
    conn.execute("INSERT INTO inbound_log(pk, msg_at, jid, mtype, decision) VALUES(900, ?, ?, 0, 'outbound:replied')", (at(-3.5), j8e))
    conn.execute("INSERT INTO inbound_log(pk, msg_at, jid, mtype, decision) VALUES(901, ?, ?, 0, 'outbound:replied')", (at(120), j8e))
    conn.commit()
    check(on_outbound(db, jl, j8e, "晚点再讲", 0, at(120)) is None,
          "the owner replied while the run was going (overtaken): a message two hours later does not open (its own log row does not count)")
    j8f = "test-a14@s.whatsapp.net"
    setup(j8f, "草稿", "side", -3)
    conn.execute("INSERT INTO inbound_log(pk, msg_at, jid, mtype, decision) VALUES(902, ?, ?, 0, 'outbound:replied')", (at(0), j8f))
    conn.commit()
    check(on_outbound(db, jl, j8f, "hi", 0, at(0)) == "opened", "the daemon logs inbound first, then calls on_outbound: the message itself is not an overtake")
    close_stale(db, jl, now=at(60))

    print("case 9 · a new run can open again; examples_for never mixes worlds; bad lines are skipped")
    setup(j4, "新草稿: 7 点可以吗", "personal", 100)
    check(on_outbound(db, jl, j4, "7 点 ok", 0, at(110)) == "opened", "a new run's draft opens a new pair")
    close_stale(db, jl, now=at(200))
    with open(str(jl), "a", encoding="utf-8") as f:
        f.write("{this line is broken\n")
        f.write("[1, 2]\n\n")
        f.write(json.dumps({"jid": "x", "world": "personal", "run_id": 9, "draft": "d", "sent": [], "closed": True}) + "\n")
        f.write(json.dumps({"jid": "y", "world": "personal", "run_id": 9, "draft": "still open", "sent": ["s"], "closed": False}) + "\n")
        f.write('{"jid": "z", "world": " Personal ", "run_id": "10", "draft": "model shaped line", "sent": "single string", "closed": "true"}\n')
    per = examples_for(jl, "personal")
    check([o["jid"] for o in per] == [j4, j4, "z"], "personal gets its 3 pairs, oldest first (got %s)" % [o["jid"] for o in per])
    check(all(str(o["world"]).strip().lower() == "personal" for o in per), "no other world mixed in")
    wk = examples_for(jl, "work")
    check([o["jid"] for o in wk] == [j1, j2, j6b], "work has 3 pairs (empty pairs and other worlds excluded; got %s)" % [o["jid"] for o in wk])
    check([o["jid"] for o in examples_for(jl, "side")] == [j3, j8d, j8f], "side has 3 pairs")
    check(examples_for(jl, "") == [] and examples_for(jl, None) == [], "empty world gives []")
    check(len(examples_for(jl, "work", n=2)) == 2 and examples_for(jl, "work", n=2)[-1]["jid"] == j6b, "n=2 gives the latest 2, newest last")
    _append_jsonl(jl, lines_for(j1)[0])
    check([o["jid"] for o in examples_for(jl, "work")].count(j1) == 1, "a rewritten (jid, run_id) line counts once")

    print("case 10 · render_examples")
    check(render_examples([]) == NO_EXAMPLES and render_examples(examples_for(tmp / "nope.jsonl", "personal")) == NO_EXAMPLES, "no pairs prints the empty marker")
    txt = render_examples(examples_for(jl, "work"))
    check("- 草稿: Hi, 10 月场是 [日期]\n  实发: Hi there ⏎ 10 月场是 18 号 你看看" in txt, "several messages joined with ⏎, newlines flattened")
    long = render_examples([{"draft": "长" * 400, "sent": ["短" * 200, "短" * 200]}])
    ls = long.split("\n")
    check(len(ls) == 2 and len(ls[0]) == len("- 草稿: ") + CLIP + 1 and len(ls[1]) == len("  实发: ") + CLIP + 1 and ls[0].endswith("…"),
          "each clipped to 300 plus …")
    check(render_examples([{"draft": "x", "sent": []}]) == NO_EXAMPLES, "a pair with nothing sent is not drawn")

    print("case 11 · close_stale without a jid closes every stale pair, with a jid only that one")
    ja, jb = "test-c01@s.whatsapp.net", "test-c02@s.whatsapp.net"
    setup(ja, "A 草稿", "other", 290)
    setup(jb, "B 草稿", "other", 290)
    on_outbound(db, jl, ja, "A1", 0, at(300))
    on_outbound(db, jl, jb, "B1", 0, at(310))
    check(close_stale(db, jl, now=at(335), jid=jb) == 0, "with jid=B: B is not stale, stays open")
    check(close_stale(db, jl, now=at(335)) == 1 and open_key(ja) is None and open_key(jb) is not None, "without jid: only the stale A closes")
    check(close_stale(db, jl, now=at(400)) == 1, "B closes once stale")
    tables = sorted(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall())
    check(tables == ["conversations", "inbound_log", "meta", "runs", "sqlite_sequence"], "no other table created")
    check(all(k.startswith("voice_last_run:") for (k,) in conn.execute("SELECT key FROM meta").fetchall()), "at the end meta holds only voice_last_run keys")

    check(TEXT_TYPES == (0, 7, 41) and all(t in W.CONTENT_TYPES for t in TEXT_TYPES),
          "TEXT_TYPES comes from wa_common and is part of CONTENT_TYPES (the one table)")

    conn.close()
    import shutil
    shutil.rmtree(str(tmp), ignore_errors=True)
    good = sum(1 for ok, _ in results if ok)
    print("selftest: %d/%d passed" % (good, len(results)))
    return 0 if good == len(results) else 1


# ---------------------------------------------------------------- CLI

def _default_paths(config_path=None):
    """(state_db, pairs_path) from the owner's config (state_db and voice.pairs); next to the config file when it
    cannot be read."""
    try:
        cfg = W.load_config(config_path)
        live = Path(W.live_dir(cfg))
        return (Path(cfg.get("state_db") or live / "state.sqlite"),
                Path(((cfg.get("voice") or {}).get("pairs")) or live / "voice-pairs.jsonl"))
    except Exception:
        live = Path(config_path).expanduser().parent if config_path else Path(W.DEFAULT_CONFIG).expanduser().parent
        return live / "state.sqlite", live / "voice-pairs.jsonl"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Voice loop: pair the brain's draft with what the owner sent, feed same world pairs back as examples.")
    ap.add_argument("--selftest", action="store_true", help="temp database and temp jsonl only; never touches real files")
    ap.add_argument("--show", action="store_true", help="read only: pair counts, open pairs, conversations that would open a pair now")
    ap.add_argument("--world", default=None, help="with --show: print what section 8 would get for this world")
    ap.add_argument("--config", default=None, help="live config.json used for the default --db and --pairs")
    ap.add_argument("--db", default=None, help="state.sqlite (default: <live>/state.sqlite)")
    ap.add_argument("--pairs", default=None, help="voice-pairs.jsonl (default: <live>/voice-pairs.jsonl)")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if a.show:
        db, pairs = a.db, a.pairs
        if not db or not pairs:
            d_db, d_pairs = _default_paths(a.config)
            db, pairs = db or str(d_db), pairs or str(d_pairs)
        show(db, pairs, world=a.world)
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
