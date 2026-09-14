#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wa_calendar.py · the calendar destination interface behind the board's "add to calendar" button.

One rule, one function (the daemon, the CLI and the board all call these, nobody recomputes them):
- validate_hint(hint)            is the brain's `calendar_hint` legal; returns a tidy dict or None (tolerant: the brain writes loosely)
- hint_key(hint)                 short key of a hint (date plus time only), used to tell "already created"
- compose_description(...)       the event description: the owner's note, the conversation name, a wa.me link
- create_event(cfg, ...)         create the event at `calendar.destination`; returns {ok, url, event_id, error}
- ics_file(cfg, key)             where an `ics` event file lives (the board serves GET /calendar/<key>.ics from it)
- local_tz_name(cfg) / localize(...)   the owner's timezone, shared by every implementation

Destinations (`calendar.destination`):
- none   no calendar; the board hides the button; create_event refuses
- ics    writes `<live>/calendar/<key>.ics` (RFC 5545), the board opens /calendar/<key>.ics, which Apple Calendar,
         Google Calendar and Outlook all import; idempotent by key          (adapters/calendar_ics.py)
- lark   calendar events create through lark-cli into `calendar.lark.calendar_id`   (adapters/calendar_lark.py)

Contract: create_event(cfg, title, date, time, duration_min=60, place="", note="", dry_run=False, runner=None,
idempotency_key=None) -> {"ok": bool, "url": str, "event_id": str, "error": str} (implementations may add keys such as
"cmd" or "already"). ok=true always carries a non empty event_id unless dry_run; ok=false always carries a readable
error; it never raises. `note` is the finished plain text description (call compose_description first).
The board form path is strict: a time the owner typed but that cannot be read is an error, never a silent all day event.

Python 3.9 standard library only (the daemon runs on /usr/bin/python3). Never calls a model. --selftest and --dry-run
never create anything; --probe only reads.
"""

import argparse
import datetime as dt
import hashlib
import importlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wa_common as W  # noqa: E402

DESTINATIONS = ("none", "ics", "lark")
_IMPLEMENTATIONS = {"ics": "adapters.calendar_ics", "lark": "adapters.calendar_lark"}

TITLE_MAX, PLACE_MAX, WHO_MAX = 80, 120, 80
DUR_DEFAULT, DUR_MIN, DUR_MAX = 60, 15, 600
REMINDER_MIN = 5
DESC_MAX = 2000
KEY_MAX = 64
_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


# ---------------------------------------------------------------- small helpers

def _one_line(s, limit):
    s = re.sub(r"\s+", " ", str(s if s is not None else "")).strip()
    return s[:limit]


def _norm_date(raw):
    """Date string -> ('YYYY-MM-DD', time found after the date or ''), or (None, '').
    Tolerates no zero padding (2026-9-5), slashes or dots (2026/9/5), a time after the date (2026-09-05T10:00).
    Dates without a year (14/9, 9-14) are refused: guessing the year creates the event a year off."""
    if raw is None or isinstance(raw, bool):
        return None, ""
    s = str(raw).strip()
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[T\s]+(.*))?$", s)
    if not m:
        return None, ""
    try:
        d = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None, ""
    return d.isoformat(), (m.group(4) or "").strip()


def _norm_time(raw):
    """Clock time -> 'HH:MM' or ''. Tolerates 9:00 · 0900 · 900 · 09:00:00 · 3pm · 3:30 PM · 15.30 · a full width colon.
    Anything unreadable becomes '' (the hint survives as an all day event)."""
    if raw is None or isinstance(raw, bool):
        return ""
    s = str(raw).strip().lower().replace("：", ":")
    if not s:
        return ""
    m = re.match(r"^(\d{1,2}:\d{2}):\d{2}", s)      # 09:00:00 or 10:30:00+08:00 keeps hours and minutes
    if m:
        s = m.group(1)
    ampm = ""
    m = re.match(r"^(.*?)\s*(am|pm|a\.m\.|p\.m\.)$", s)
    if m:
        s, ampm = m.group(1).strip(), m.group(2)[0]
    h = mi = None
    m = re.match(r"^(\d{1,2})[:.](\d{2})$", s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
    else:
        m = re.match(r"^(\d{3,4})$", s)
        if m:
            h, mi = int(s[:-2]), int(s[-2:])
        else:
            m = re.match(r"^(\d{1,2})$", s)
            if m and ampm:
                h, mi = int(s), 0
    if h is None:
        return ""
    if ampm:
        if not 1 <= h <= 12:
            return ""
        h = (0 if h == 12 else h) + (12 if ampm == "p" else 0)
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return ""
    return "%02d:%02d" % (h, mi)


def _norm_duration(raw):
    """Duration -> whole minutes clamped to 15..600. Tolerates 90 · "90" · "90 min" · 90.0; unreadable gives 60."""
    if raw is None or isinstance(raw, bool):
        return DUR_DEFAULT
    if isinstance(raw, (int, float)):
        n = int(raw)
    else:
        m = re.match(r"^\s*(\d+(?:\.\d+)?)", str(raw))
        if not m:
            return DUR_DEFAULT
        n = int(float(m.group(1)))
    if n <= 0:
        return DUR_DEFAULT
    return max(DUR_MIN, min(DUR_MAX, n))


# ---------------------------------------------------------------- rule: is a hint legal

def validate_hint(hint):
    """The brain's `calendar_hint` -> legal dict or None. The only judge: normalize() uses it, the board form passes it too.
    Legal = {title: non empty, <=80; date: 'YYYY-MM-DD' (required); time: 'HH:MM' or ''; duration_min: 15..600 (default 60);
    place: <=120; who: <=80}; extra keys are dropped. Accepts a dict or a JSON string (read back from sqlite);
    null, an empty object or a non object give None. A time carried in the date field is used when time is empty."""
    raw = hint
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return None
    if not isinstance(raw, dict) or not raw:
        return None
    title = _one_line(raw.get("title"), TITLE_MAX)
    if not title:
        return None
    date, time_in_date = _norm_date(raw.get("date"))
    if not date:
        return None
    clock = _norm_time(raw.get("time"))
    if not clock and time_in_date:
        clock = _norm_time(time_in_date)
    return {
        "title": title,
        "date": date,
        "time": clock,
        "duration_min": _norm_duration(raw.get("duration_min")),
        "place": _one_line(raw.get("place"), PLACE_MAX),
        "who": _one_line(raw.get("who"), WHO_MAX),
    }


def hint_key(hint):
    """12 hex chars from date plus time. The brain rewrites title, place and duration across runs, so they are left out
    (otherwise the button lights up again for the same appointment); a new date or time is a new appointment.
    An illegal hint gives ''."""
    h = validate_hint(hint)
    if not h:
        return ""
    base = "%s|%s" % (h["date"], h["time"])
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- rule: what the description looks like

def compose_description(note, name="", phone=""):
    """Plain text description: the note the owner typed · a blank line · "WhatsApp: <conversation name>" · a wa.me link
    (only when there is a phone; groups have none)."""
    parts = []
    note = str(note or "").strip()
    if note:
        parts.append(note)
    tail = []
    if name:
        tail.append("WhatsApp: %s" % _one_line(name, 80))
    digits = re.sub(r"\D", "", str(phone or ""))
    if digits:
        tail.append("https://wa.me/%s" % digits)
    if tail:
        parts.append("\n".join(tail))
    return "\n\n".join(parts)[:DESC_MAX]


# ---------------------------------------------------------------- rule: the owner's timezone

def local_tz_name(cfg=None):
    """IANA name of the owner's timezone: `calendar.timezone` when set, else env TZ when it looks like a zone name,
    else the zone macOS points /etc/localtime at. '' when none can be found (callers then use the system offset)."""
    name = str((((cfg or {}).get("calendar") or {}).get("timezone")) or "").strip()
    if name:
        return name
    tz = str(os.environ.get("TZ") or "").strip().lstrip(":")
    if tz and "/" in tz and not tz.startswith("/"):
        return tz
    try:
        real = os.path.realpath("/etc/localtime")
        i = real.find("zoneinfo/")
        if i >= 0:
            return real[i + len("zoneinfo/"):]
    except Exception:
        pass
    return ""


def localize(date, clock, cfg=None):
    """('YYYY-MM-DD', 'HH:MM') -> timezone aware datetime in the owner's timezone (zoneinfo when the zone name resolves,
    otherwise the system local offset for that date)."""
    y, mo, d = [int(x) for x in str(date).split("-")]
    hh, mm = [int(x) for x in str(clock).split(":")]
    name = local_tz_name(cfg)
    if name:
        try:
            from zoneinfo import ZoneInfo
            return dt.datetime(y, mo, d, hh, mm, tzinfo=ZoneInfo(name))
        except Exception:
            pass
    return dt.datetime(y, mo, d, hh, mm).astimezone()


def safe_key(key):
    """Idempotency key -> file and URL safe key (letters, digits, dash, underscore; at most 64). Deterministic."""
    k = re.sub(r"[^A-Za-z0-9_-]+", "-", str(key or "")).strip("-")[:KEY_MAX]
    return k


def event_spec(hint, note="", cfg=None, idempotency_key=None):
    """Legal hint -> what every implementation needs: title, date, time, duration_min, place, description, all_day,
    start and end (aware datetimes in the owner's timezone, None for all day), tz_name, key (safe, never empty),
    idempotency_key (the caller's, possibly None)."""
    spec = dict(hint)
    spec["description"] = str(note or "")[:DESC_MAX]
    spec["all_day"] = not hint.get("time")
    spec["tz_name"] = local_tz_name(cfg)
    spec["start"] = spec["end"] = None
    if not spec["all_day"]:
        start = localize(hint["date"], hint["time"], cfg)
        spec["start"] = start
        spec["end"] = start + dt.timedelta(minutes=int(hint["duration_min"]))
    key = safe_key(idempotency_key) if idempotency_key else ""
    if not key:
        base = "%s|%s|%s" % (hint["date"], hint["time"], hint["title"])
        key = "ev-" + hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]
    spec["key"] = key
    spec["idempotency_key"] = str(idempotency_key)[:KEY_MAX] if idempotency_key else None
    spec["reminder_min"] = REMINDER_MIN
    return spec


# ---------------------------------------------------------------- dispatch

def destination(cfg):
    return str((((cfg or {}).get("calendar") or {}).get("destination")) or "none").strip().lower()


def implementation(name, cfg=None):
    """The module behind a destination: the owner's own <live>/adapters/calendar_<name>.py first (it survives kit
    updates), else the shipped ics or lark. Raises ValueError for anything else."""
    if cfg is not None and name and name != "none":
        import adapters as A
        own = A.owner_module(cfg, "calendar_" + name)
        if own is not None:
            return own
    mod = _IMPLEMENTATIONS.get(name)
    if not mod:
        raise ValueError("unknown calendar destination: %s (use one of %s, or your own adapters/calendar_%s.py in the live folder)" % (name, ", ".join(DESTINATIONS), name))
    return importlib.import_module(mod)


def ics_file(cfg, key):
    """Path of the ics file for a key (under <live>/calendar/), or None when the key is not a safe key.
    The file may not exist yet; the caller checks. A trailing .ics is tolerated."""
    k = str(key or "")
    if k.endswith(".ics"):
        k = k[:-4]
    if not _KEY_RE.match(k):
        return None
    return Path(W.live_dir(cfg)) / "calendar" / ("%s.ics" % k)


def create_event(cfg, title, date, time, duration_min=60, place="", note="", dry_run=False, runner=None, idempotency_key=None):
    """Create one event at the configured destination. Returns {"ok", "url", "event_id", "error"} (plus "cmd" for lark,
    "already" for ics). Never raises.
    - none: refuses with "calendar destination is none".
    - The arguments go through validate_hint once more (the owner may have broken the date in the board form).
    - `time` is what the owner typed: non empty but unreadable is an error, never a silent all day event.
    - dry_run: nothing is created; ok=True with empty url and event_id.
    - runner(cmd, timeout) -> (returncode, stdout, stderr) replaces subprocess for external tools (selftest)."""
    res = {"ok": False, "url": "", "event_id": "", "error": ""}
    dest = destination(cfg)
    if dest == "none":
        res["error"] = "calendar destination is none"
        return res
    if dest not in DESTINATIONS:
        try:
            implementation(dest, cfg)
        except Exception as e:
            res["error"] = str(e)
            return res
    hint = validate_hint({"title": title, "date": date, "time": time, "duration_min": duration_min, "place": place})
    if not hint:
        res["error"] = "title is empty or the date is not YYYY-MM-DD"
        return res
    if str(time if time is not None else "").strip() and not hint["time"]:
        res["error"] = "cannot read the time: %s (write 15:00, or leave it empty for an all day event)" % _one_line(time, 20)
        return res
    try:
        spec = event_spec(hint, note, cfg, idempotency_key)
        out = implementation(dest, cfg).create(cfg, spec, dry_run=bool(dry_run), runner=runner)
    except Exception as e:
        res["error"] = "calendar %s failed: %s" % (dest, str(e)[:300])
        return res
    for k in ("ok", "url", "event_id", "error"):
        out.setdefault(k, res[k])
    out["ok"] = bool(out["ok"])
    return out


# ---------------------------------------------------------------- CLI: --probe and --dry-run (real config)

def probe(cfg):
    dest = destination(cfg)
    print("calendar.destination: %s · owner timezone: %s" % (dest, local_tz_name(cfg) or "(system offset)"))
    if dest == "none":
        print("nothing to probe: the board hides the calendar button")
        return 0
    try:
        impl = implementation(dest, cfg)
    except ValueError as e:
        print(str(e))
        return 1
    fn = getattr(impl, "probe", None)
    if fn is None:
        print("%s has no probe(cfg); nothing to show" % dest)
        return 0
    return fn(cfg)


def dry_run_real(cfg):
    dest = destination(cfg)
    if dest == "none":
        print("calendar.destination is none: nothing to dry run")
        return 0
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    note = compose_description("dry run, nothing is created", "Calendar Dry Run", "")
    ok_all = True
    for label, clock, key in (("timed", "15:00", "dryrun-timed"), ("all day", "", "dryrun-allday")):
        r = create_event(cfg, "calendar dry run (%s)" % label, tomorrow, clock, 90, "Test place", note,
                         dry_run=True, idempotency_key=key)
        ok_all = ok_all and r["ok"]
        print("[%s] ok=%s error=%s" % (label, r["ok"], r["error"] or "none"))
        for line in r.get("preview") or []:
            print("  " + line)
    return 0 if ok_all else 1


# ---------------------------------------------------------------- selftest

def selftest_cfg(tmp, calendar, extra=None):
    """A real cfg for selftests: writes <tmp>/live/config.json with every path inside tmp, loads it through
    wa_common.load_config, and refuses (RuntimeError) when the live dir resolves outside tmp."""
    tmp = Path(tmp)
    live = tmp / "live"
    live.mkdir(parents=True, exist_ok=True)
    raw = {"version": 1, "paths": {"home": str(tmp), "live_dir": str(live)}, "calendar": calendar,
           "brain": {"cwd": str(tmp / "brain-cwd")}}
    raw.update(extra or {})
    p = live / "config.json"
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    cfg = W.load_config(str(p))
    got = Path(W.live_dir(cfg)).resolve()
    if not str(got).startswith(str(tmp.resolve())):
        raise RuntimeError("selftest refuses to run: live dir resolved to %s, outside the temp folder" % got)
    return cfg


def _selftest():
    import shutil
    import tempfile
    fails, n = [], [0]

    def check(name, cond):
        n[0] += 1
        print(("  ok   " if cond else "  FAIL ") + name)
        if not cond:
            fails.append(name)

    # validate_hint: the shapes a model really writes
    base = {"title": "coffee with a client about the quote", "date": "2026-09-20", "time": "15:00", "duration_min": 60,
            "place": "Test Cafe", "who": "Sam"}
    check("a tidy hint passes unchanged", validate_hint(base) == base)
    h = validate_hint(dict(base, duration_min="90"))
    check("string duration '90' -> 90", h and h["duration_min"] == 90)
    h = validate_hint(dict(base, duration_min="90 min"))
    check("'90 min' -> 90", h and h["duration_min"] == 90)
    h = validate_hint(dict(base, duration_min=5))
    check("duration 5 clamps to 15", h and h["duration_min"] == 15)
    h = validate_hint(dict(base, duration_min=None))
    check("duration null -> 60", h and h["duration_min"] == 60)
    h = validate_hint(dict(base, time="9:00"))
    check("'9:00' -> '09:00'", h and h["time"] == "09:00")
    h = validate_hint(dict(base, time="0930"))
    check("'0930' -> '09:30'", h and h["time"] == "09:30")
    h = validate_hint(dict(base, time="3pm"))
    check("'3pm' -> '15:00'", h and h["time"] == "15:00")
    h = validate_hint(dict(base, time="10：15"))
    check("full width colon '10：15' -> '10:15'", h and h["time"] == "10:15")
    h = validate_hint(dict(base, time=None))
    check("time null -> '' and the hint survives", h and h["time"] == "")
    h = validate_hint(dict(base, time="下午"))
    check("unreadable time -> '' (the hint is not dropped)", h and h["time"] == "")
    h = validate_hint(dict(base, time="25:00"))
    check("time 25:00 -> ''", h and h["time"] == "")
    h = validate_hint(dict(base, date="2026-9-5"))
    check("date '2026-9-5' -> '2026-09-05'", h and h["date"] == "2026-09-05")
    h = validate_hint(dict(base, date="2026-09-20T10:30:00+08:00", time=""))
    check("time carried in the date field is used when time is empty", h and h["date"] == "2026-09-20" and h["time"] == "10:30")
    check("date '14/9' -> None", validate_hint(dict(base, date="14/9")) is None)
    check("date 2026-02-30 -> None", validate_hint(dict(base, date="2026-02-30")) is None)
    check("missing date -> None", validate_hint({k: v for k, v in base.items() if k != "date"}) is None)
    check("missing title -> None", validate_hint({k: v for k, v in base.items() if k != "title"}) is None)
    check("blank title -> None", validate_hint(dict(base, title="   ")) is None)
    check("null -> None", validate_hint(None) is None)
    check("empty object -> None", validate_hint({}) is None)
    h = validate_hint(dict(base, extra="x", attendees=["a"], confidence=0.9))
    check("extra keys dropped", h and set(h.keys()) == {"title", "date", "time", "duration_min", "place", "who"})
    h = validate_hint(json.dumps(dict(base, title="  x" * 50), ensure_ascii=False))
    check("JSON string accepted, title cut to 80", h and len(h["title"]) == 80)
    model_like = '{"title":"venue walkthrough","date":"2026-9-25","time":"10:00 AM","duration_min":"120","place":null,"who":"Sam"}'
    check("model shaped JSON string (place null · '10:00 AM' · string duration)",
          validate_hint(model_like) == {"title": "venue walkthrough", "date": "2026-09-25", "time": "10:00",
                                        "duration_min": 120, "place": "", "who": "Sam"})

    # hint_key
    k1 = hint_key(base)
    check("hint_key is 12 chars", len(k1) == 12)
    check("hint_key ignores place and duration", hint_key(dict(base, place="elsewhere", duration_min=90)) == k1)
    check("hint_key ignores a rewritten title", hint_key(dict(base, title="another wording")) == k1)
    check("hint_key changes with the time", hint_key(dict(base, time="16:00")) != k1)
    check("hint_key of an illegal hint is ''", hint_key({"title": ""}) == "")

    # compose_description
    d = compose_description("bring the quote", "Test Contact", "+1 555 0100")
    check("description has note · conversation name · wa.me", "bring the quote" in d and "WhatsApp: Test Contact" in d
          and "https://wa.me/15550100" in d)
    check("a group without phone gets no wa.me", "wa.me" not in compose_description("", "Test Group", ""))
    check("description is cut to 2000", len(compose_description("x" * 3000, "n", "")) == DESC_MAX)

    # timezone
    cfg_tz = {"calendar": {"timezone": "Asia/Tokyo"}}
    check("local_tz_name prefers calendar.timezone", local_tz_name(cfg_tz) == "Asia/Tokyo")
    st = localize("2026-09-20", "15:00", cfg_tz)
    check("localize 15:00 Asia/Tokyo = 06:00 UTC", st.astimezone(dt.timezone.utc).hour == 6 and st.utcoffset() == dt.timedelta(hours=9))
    st2 = localize("2026-09-20", "15:00", {"calendar": {"timezone": "Not/AZone"}})
    check("an unknown zone name falls back to the system offset", st2.tzinfo is not None and st2.hour == 15)
    check("local_tz_name without config finds a zone or ''", isinstance(local_tz_name({}), str))

    # keys and ics_file (cfg loaded through wa_common.load_config from a temp config)
    tmp = Path(tempfile.mkdtemp(prefix="wa_calendar_selftest_"))
    try:
        cfg_base = selftest_cfg(tmp, {"destination": "ics", "timezone": "Asia/Tokyo"})
        live = Path(W.live_dir(cfg_base))
        check("wa_common.load_config keeps calendar.destination", destination(cfg_base) == "ics")
        check("safe_key keeps uuid shapes", safe_key("6f1c2b0e-3a4d-5e6f-8a9b-0c1d2e3f4a5b") == "6f1c2b0e-3a4d-5e6f-8a9b-0c1d2e3f4a5b")
        check("safe_key strips path characters", "/" not in safe_key("../../etc/passwd") and ".." not in safe_key("../../etc/passwd"))
        check("ics_file refuses traversal", ics_file(cfg_base, "../x") is None and ics_file(cfg_base, "a/b") is None
              and ics_file(cfg_base, "") is None)
        p = ics_file(cfg_base, "abc-123.ics")
        check("ics_file maps a key to <live>/calendar/<key>.ics", p is not None and p == live / "calendar" / "abc-123.ics")

        # dispatch: none, unknown, strict form
        r = create_event({"calendar": {"destination": "none"}}, "x", "2026-09-20", "10:00")
        check("destination none refuses with the contract error",
              r == {"ok": False, "url": "", "event_id": "", "error": "calendar destination is none"})
        r = create_event({}, "x", "2026-09-20", "10:00")
        check("no calendar section means none", r["ok"] is False and r["error"] == "calendar destination is none")
        r = create_event({"calendar": {"destination": "outlook"}}, "x", "2026-09-20", "10:00")
        check("unknown destination is an error", r["ok"] is False and "unknown calendar destination" in r["error"])
        r = create_event(cfg_base, "x", "20/9", "10:00")
        check("form with a broken date is an error", r["ok"] is False and "YYYY-MM-DD" in r["error"])
        r = create_event(cfg_base, "x", "2026-09-20", "下午三点")
        check("form with an unreadable typed time is an error, not a silent all day event",
              r["ok"] is False and r["error"].startswith("cannot read the time") and not (live / "calendar").exists())
        spec = event_spec(validate_hint(base), "d", cfg_base, None)
        check("event_spec derives a safe key when no idempotency key is given",
              bool(_KEY_RE.match(spec["key"])) and spec["idempotency_key"] is None and spec["all_day"] is False)

        # every shipped implementation against fakes (they share this check and this temp folder)
        implementation("ics")._selftest(check, tmp)
        implementation("lark")._selftest(check, tmp)
    finally:
        shutil.rmtree(str(tmp), ignore_errors=True)
    print("selftest: %d/%d passed" % (n[0] - len(fails), n[0]))
    return 1 if fails else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="WhatsApp Live calendar destination: calendar_hint rules and create_event (none, ics or lark)")
    ap.add_argument("--config", default=None, help="live config.json (default: env WA_LIVE_CONFIG or ~/WhatsApp-Live/live/config.json)")
    ap.add_argument("--selftest", action="store_true", help="fake runner and temp folder only; creates nothing")
    ap.add_argument("--probe", action="store_true", help="read only: show the destination and, for an API calendar, list its calendars")
    ap.add_argument("--dry-run", action="store_true", help="real config: build a timed and an all day request, create nothing")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if a.probe or a.dry_run:
        try:
            cfg = W.load_config(a.config)
        except (OSError, ValueError) as e:
            print("config: %s" % e)
            return 2
        return probe(cfg) if a.probe else dry_run_real(cfg)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
