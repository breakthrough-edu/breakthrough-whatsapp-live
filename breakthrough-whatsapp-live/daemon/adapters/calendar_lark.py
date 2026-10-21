#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
adapters/calendar_lark.py · calendar destination `lark`: lark-cli `calendar events create` into one chosen calendar.

Behind wa_calendar.create_event (which validates the form and builds the spec). Config: `calendar.lark.calendar_id`
(required; empty means refuse without calling lark-cli), `bin.lark_cli`, `bin.node`, `calendar.timezone` (optional).

Known pits (lark-cli 1.0.89):
- The `+event-create` and `+calendar-list` shortcuts do not exist; `calendar +create` forces a video meeting and has no
  place and no all day, so this uses the full API `calendar events create --params {calendar_id} --data {...}`.
- Timed events: unix timestamps plus the owner's IANA zone, a reminder `reminder_min` minutes before. All day events:
  start and end are the same date. Place goes to location.name. No video meeting.
- `--idempotency-key` makes Lark create at most one event per key (per app and calendar), so a double click or two
  tabs create one event; `--dry-run` passes the key too, so the exact real command shape is what gets dry run.
- stdout may start with progress text before the JSON; parse from the first `{`.
- launchd has no node on PATH: the runner puts the directory of `bin.node` in front of PATH.
- The user token expires; an expired login shows up as ok=false with Lark's message.

Python 3.9 standard library only. Never calls a model.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

_DAEMON = Path(__file__).resolve().parent.parent
if str(_DAEMON) not in sys.path:
    sys.path.insert(0, str(_DAEMON))
import wa_common as W  # noqa: E402

NAME = "lark"
DESC_MAX = 2000
WRITABLE_ROLES = ("owner", "writer")


def calendar_id(cfg):
    return str(((((cfg or {}).get("calendar") or {}).get("lark") or {}).get("calendar_id")) or "").strip()


# ---------------------------------------------------------------- shared lark-cli plumbing (second_source_lark imports these)

def cli_path(cfg):
    """lark-cli executable: bin.lark_cli through wa_common.resolve_bin, else lark-cli on PATH, else ''."""
    p = ""
    try:
        p = W.resolve_bin(cfg, "lark_cli") or ""
    except Exception:
        p = ""
    return str(p or shutil.which("lark-cli") or "")


def lark_env(cfg):
    """The daemon's subprocess environment with the folder of bin.node in front of PATH (launchd has no node on PATH,
    and lark-cli is a node program)."""
    try:
        env = dict(W.subprocess_env(cfg))
    except Exception:
        env = dict(os.environ)
    try:
        node = W.resolve_bin(cfg, "node") or ""
    except Exception:
        node = ""
    if node:
        env["PATH"] = str(Path(str(node)).parent) + os.pathsep + env.get("PATH", "/usr/bin:/bin")
    return env


def parse_json_out(out):
    """lark-cli stdout may print progress lines ([page 1] fetching...) before the JSON: try each line that starts with
    '{', then the first '{' anywhere. None when nothing parses."""
    s = out or ""
    for i in [m.start() for m in re.finditer(r"(?m)^\{", s)]:
        try:
            return json.loads(s[i:])
        except Exception:
            continue
    i = s.find("{")
    if i >= 0:
        try:
            return json.loads(s[i:])
        except Exception:
            return None
    return None


def default_runner(cfg):
    """runner(cmd, timeout) -> (returncode, stdout, stderr), with lark_env(cfg)."""
    env = lark_env(cfg)

    def run(cmd, timeout=60):
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return r.returncode, r.stdout or "", r.stderr or ""
    return run


def build_event_body(spec):
    """spec -> the --data object of `calendar events create`."""
    body = {"summary": spec["title"]}
    if not spec.get("all_day"):
        tz = str(spec.get("tz_name") or "") or "UTC"
        body["start_time"] = {"timestamp": str(int(spec["start"].timestamp())), "timezone": tz}
        body["end_time"] = {"timestamp": str(int(spec["end"].timestamp())), "timezone": tz}
        if spec.get("reminder_min"):
            body["reminders"] = [{"minutes": int(spec["reminder_min"])}]
    else:
        body["start_time"] = {"date": spec["date"]}
        body["end_time"] = {"date": spec["date"]}
    if spec.get("place"):
        body["location"] = {"name": spec["place"]}
    if spec.get("description"):
        body["description"] = str(spec["description"])[:DESC_MAX]
    return body


def create(cfg, spec, dry_run=False, runner=None):
    """Create one event in calendar.lark.calendar_id. Returns {ok, url, event_id, error, cmd} (dry run adds preview)."""
    res = {"ok": False, "url": "", "event_id": "", "error": "", "cmd": []}
    cal = calendar_id(cfg)
    if not cal:
        res["error"] = "calendar.lark.calendar_id is empty (run wa_calendar.py --probe, pick a calendar, set it)"
        return res
    cli = cli_path(cfg)
    if not cli and runner is None:
        res["error"] = "lark-cli not found (set bin.lark_cli)"
        return res
    body = build_event_body(spec)
    cmd = [cli or "lark-cli", "calendar", "events", "create", "--as", "user",
           "--params", json.dumps({"calendar_id": cal}, ensure_ascii=False),
           "--data", json.dumps(body, ensure_ascii=False),
           "--format", "json"]
    if spec.get("idempotency_key"):
        cmd += ["--idempotency-key", str(spec["idempotency_key"])[:64]]
    if dry_run:
        cmd.append("--dry-run")
    res["cmd"] = cmd
    run = runner or default_runner(cfg)
    try:
        rc, out, err = run(cmd, 60)
    except Exception as e:
        res["error"] = "lark-cli could not run: %s" % str(e)[:200]
        return res
    j = parse_json_out(out)
    if not isinstance(j, dict):
        res["error"] = "lark-cli returned no JSON (rc=%s): %s" % (rc, (err or out or "")[-300:])
        return res
    if not j.get("ok"):
        e = j.get("error")
        msg = (e.get("message") or json.dumps(e, ensure_ascii=False)) if isinstance(e, dict) else (e or json.dumps(j, ensure_ascii=False))
        res["error"] = "lark-cli ok=false: %s" % str(msg)[:300]
        return res
    if dry_run or j.get("dry_run"):
        res["ok"] = True
        res["preview"] = ["params: %s" % cmd[cmd.index("--params") + 1], "data:   %s" % cmd[cmd.index("--data") + 1],
                          "idempotency key: %s" % (spec.get("idempotency_key") or "(none)")]
        return res
    ev = ((j.get("data") or {}).get("event")) or {}
    res["event_id"] = str(ev.get("event_id") or "")
    res["url"] = str(ev.get("app_link") or "")
    if not res["event_id"]:
        res["error"] = "lark-cli ok but no event_id: %s" % json.dumps(j, ensure_ascii=False)[:300]
        return res
    res["ok"] = True
    return res


# ---------------------------------------------------------------- probe (read only)

def list_calendars(cfg, runner=None):
    """`lark-cli calendar calendars list --as user` (read only). Returns (calendars, error)."""
    cmd = [cli_path(cfg) or "lark-cli", "calendar", "calendars", "list", "--as", "user", "--page-size", "500", "--format", "json"]
    run = runner or default_runner(cfg)
    try:
        rc, out, err = run(cmd, 60)
    except Exception as e:
        return [], "lark-cli could not run: %s" % str(e)[:200]
    j = parse_json_out(out)
    if not isinstance(j, dict) or not j.get("ok"):
        return [], "lark-cli failed (rc=%s): %s" % (rc, (err or out or "")[-300:])
    return list(((j.get("data") or {}).get("calendar_list")) or []), ""


def suggest_calendar(calendars):
    """The calendar to suggest without asking: only when exactly one primary calendar is writable. Else None."""
    hits = [c for c in calendars or [] if str(c.get("type") or "") == "primary" and str(c.get("role") or "") in WRITABLE_ROLES]
    return hits[0] if len(hits) == 1 else None


def probe(cfg, runner=None):
    cals, err = list_calendars(cfg, runner)
    if err:
        print(err)
        return 1
    current = calendar_id(cfg)
    for c in cals:
        cid = str(c.get("calendar_id") or "")
        mark = "current" if cid and cid == current else ("writable" if str(c.get("role") or "") in WRITABLE_ROLES else "")
        print("%-8s | %-24s | alias %-24s | %-8s %-10s | %s" % (mark, str(c.get("summary") or "")[:24], str(c.get("summary_alias") or "")[:24],
                                                             c.get("type") or "", c.get("role") or "", cid))
    print("")
    pick = suggest_calendar(cals)
    if pick:
        print("suggested config (only one writable primary calendar; nothing is written):")
        print(json.dumps({"calendar": {"destination": "lark", "lark": {"calendar_id": pick.get("calendar_id")}}}, ensure_ascii=False))
    else:
        print("pick the calendar that should receive events and set calendar.lark.calendar_id to its id")
    return 0


# ---------------------------------------------------------------- selftest

def _selftest(check=None, tmp=None):
    import datetime as dt
    import tempfile
    import wa_calendar as C
    own = check is None
    fails, n = [], [0]
    if own:
        def check(name, cond):
            n[0] += 1
            print(("  ok   " if cond else "  FAIL ") + name)
            if not cond:
                fails.append(name)
    made = tmp is None
    base = Path(tempfile.mkdtemp(prefix="calendar_lark_selftest_")) if made else Path(tmp) / "lark"
    try:
        cfg = C.selftest_cfg(base, {"destination": "lark", "timezone": "Asia/Tokyo", "lark": {"calendar_id": "cal_test_0001"}})
        calls = []

        def fake_ok(cmd, timeout=60):
            calls.append(cmd)
            return 0, "[progress] creating...\n" + json.dumps({"ok": True, "identity": "user", "data": {"event": {
                "event_id": "ev_test_1", "app_link": "https://calendar.invalid/event/ev_test_1"}}}), ""

        r = C.create_event(cfg, "coffee about the quote", "2026-09-20", "15:00", 90, "Test Cafe", "note", runner=fake_ok,
                           idempotency_key="k-0001")
        cmd = calls[-1] if calls else []
        check("lark: fake create returns ok + url + event_id", r["ok"] and r["event_id"] == "ev_test_1" and r["url"].startswith("https://"))
        check("lark: command is calendar events create --as user", cmd[1:6] == ["calendar", "events", "create", "--as", "user"])
        params = json.loads(cmd[cmd.index("--params") + 1]) if "--params" in cmd else {}
        data = json.loads(cmd[cmd.index("--data") + 1]) if "--data" in cmd else {}
        check("lark: calendar id from calendar.lark.calendar_id", params.get("calendar_id") == "cal_test_0001")
        check("lark: owner's zone name in start_time", (data.get("start_time") or {}).get("timezone") == "Asia/Tokyo")
        start_exp = int(dt.datetime(2026, 9, 20, 6, 0, tzinfo=dt.timezone.utc).timestamp())
        check("lark: 15:00 Asia/Tokyo timestamp is 06:00 UTC", (data.get("start_time") or {}).get("timestamp") == str(start_exp))
        check("lark: 90 minutes -> end = start + 5400", (data.get("end_time") or {}).get("timestamp") == str(start_exp + 5400))
        check("lark: place, description, 5 minute reminder, no video meeting",
              (data.get("location") or {}).get("name") == "Test Cafe" and data.get("description") == "note"
              and data.get("reminders") == [{"minutes": 5}] and "vchat" not in data)
        check("lark: idempotency key passed, no --dry-run",
              "--idempotency-key" in cmd and cmd[cmd.index("--idempotency-key") + 1] == "k-0001" and "--dry-run" not in cmd)

        r = C.create_event(cfg, "venue walkthrough", "2026-9-25", "", runner=fake_ok)
        data = json.loads(calls[-1][calls[-1].index("--data") + 1])
        check("lark: no time -> all day (same start and end date, no timestamp, no reminder)",
              r["ok"] and data.get("start_time") == {"date": "2026-09-25"} and data.get("end_time") == {"date": "2026-09-25"}
              and "reminders" not in data)
        check("lark: without an idempotency key the flag is not passed", "--idempotency-key" not in calls[-1])

        dry = []
        r = C.create_event(cfg, "x", "2026-09-20", "10:00", dry_run=True, idempotency_key="k-dry",
                           runner=lambda c, t=60: (dry.append(c) or (0, json.dumps({"ok": True, "dry_run": True, "data": {"api": []}}), "")))
        check("lark: dry run passes --dry-run and the idempotency key too; ok with empty url",
              r["ok"] and r["url"] == "" and dry and dry[-1][-1] == "--dry-run" and "k-dry" in dry[-1] and r.get("preview"))

        n_before = len(calls)
        cfg_empty = C.selftest_cfg(base / "empty", {"destination": "lark", "lark": {"calendar_id": ""}})
        r = C.create_event(cfg_empty, "x", "2026-09-20", "10:00", runner=fake_ok)
        check("lark: empty calendar_id refuses and never calls lark-cli",
              (not r["ok"]) and "calendar_id" in r["error"] and len(calls) == n_before)
        r = C.create_event(cfg, "x", "2026-09-20", "10:00",
                           runner=lambda c, t=60: (1, json.dumps({"ok": False, "error": {"type": "auth", "message": "token expired"}}), ""))
        check("lark: ok=false carries Lark's message", not r["ok"] and "token expired" in r["error"])
        r = C.create_event(cfg, "x", "2026-09-20", "10:00", runner=lambda c, t=60: (127, "", "node: not found"))
        check("lark: no JSON carries stderr", not r["ok"] and "node: not found" in r["error"])

        def boom(c, t=60):
            raise OSError("spawn failed")
        r = C.create_event(cfg, "x", "2026-09-20", "10:00", runner=boom)
        check("lark: a runner that raises becomes an error, never an exception", not r["ok"] and "spawn failed" in r["error"])
        r = C.create_event(cfg, "x", "2026-09-20", "10:00", runner=lambda c, t=60: (0, json.dumps({"ok": True, "data": {"event": {}}}), ""))
        check("lark: ok without event_id is an error", not r["ok"] and "event_id" in r["error"])

        cals = [{"calendar_id": "cal_a", "summary": "Main", "type": "primary", "role": "owner"},
                {"calendar_id": "cal_b", "summary": "Holidays", "type": "shared", "role": "reader"}]
        check("lark: suggest the single writable primary calendar", (suggest_calendar(cals) or {}).get("calendar_id") == "cal_a")
        check("lark: no suggestion when two primaries are writable",
              suggest_calendar(cals + [{"calendar_id": "cal_c", "type": "primary", "role": "writer"}]) is None)
        got, err = list_calendars(cfg, runner=lambda c, t=60: (0, json.dumps({"ok": True, "data": {"calendar_list": cals}}), ""))
        check("lark: list_calendars reads data.calendar_list", err == "" and len(got) == 2)
    finally:
        if made:
            shutil.rmtree(str(base), ignore_errors=True)
    if own:
        print("selftest: %d/%d passed" % (n[0] - len(fails), n[0]))
        return 1 if fails else 0
    return True


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(_selftest())
    print("usage: calendar_lark.py --selftest   (normally used through wa_calendar.py)")
    sys.exit(0)
