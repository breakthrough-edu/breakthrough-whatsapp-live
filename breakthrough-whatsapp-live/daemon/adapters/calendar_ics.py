#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
adapters/calendar_ics.py · calendar destination `ics`: one RFC 5545 file per event at <live>/calendar/<key>.ics.

The board opens /calendar/<key>.ics (the daemon serves it as text/calendar); Apple Calendar, Google Calendar and Outlook
all import it. Behind wa_calendar.create_event, which validates the form and builds the spec this module receives.

- UID comes from the idempotency key; the same key returns the same url and never rewrites the file (first writer wins,
  a double click or two tabs produce one file).
- Timed events carry the owner's local wall clock with TZID=<zone> and a VTIMEZONE holding the zone's offset on that
  date; when no zone name is known (or the event spans an offset change) the times are written in UTC (Z), which every
  client shows at the right instant. All day events use VALUE=DATE with DTEND on the next day (exclusive end).
- Lines end with CRLF, long lines are folded at 75 octets without splitting a character, text values are escaped.
- Timed events get a display alarm `reminder_min` minutes before. No video meeting, no attendees.
- Folder 0700, file 0600 (the description can carry a name and a wa.me link).

Python 3.9 standard library only.
"""

import datetime as dt
import os
import re
import sys
import tempfile
from pathlib import Path

_DAEMON = Path(__file__).resolve().parent.parent
if str(_DAEMON) not in sys.path:
    sys.path.insert(0, str(_DAEMON))
import wa_common as W  # noqa: E402

NAME = "ics"
PRODID = "-//whatsapp-live//calendar 1.0//EN"
UID_DOMAIN = "whatsapp-live.local"
FOLD_OCTETS = 75


# ---------------------------------------------------------------- RFC 5545 text

def escape_text(s):
    """TEXT value escaping (RFC 5545 3.3.11): backslash, semicolon, comma, newline."""
    s = str(s if s is not None else "").replace("\r\n", "\n").replace("\r", "\n")
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fold_line(line):
    """Fold one content line so every physical line is at most 75 octets (continuations start with one space)."""
    if len(line.encode("utf-8")) <= FOLD_OCTETS:
        return line
    out, cur, used = [], "", 0
    for ch in line:
        size = len(ch.encode("utf-8"))
        if used + size > FOLD_OCTETS:
            out.append(cur)
            cur, used = " " + ch, 1 + size
        else:
            cur += ch
            used += size
    out.append(cur)
    return "\r\n".join(out)


def unfold(text):
    """Inverse of folding, for readers and the selftest."""
    return re.sub(r"\r\n[ \t]", "", text)


def _utc(d):
    return d.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _offset(td):
    total = int(td.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return "%s%02d%02d" % (sign, total // 3600, (total % 3600) // 60)


def _zone_block(spec):
    """VTIMEZONE lines for a timed event, or None when the zone name is unknown or unreliable."""
    name = str(spec.get("tz_name") or "")
    st, en = spec.get("start"), spec.get("end")
    if not name or st is None or st.utcoffset() is None:
        return None
    if getattr(st.tzinfo, "key", None) != name:        # localize fell back to the system offset
        return None
    if en is not None and en.utcoffset() != st.utcoffset():
        return None
    off = _offset(st.utcoffset())
    return ["BEGIN:VTIMEZONE", "TZID:" + name, "BEGIN:STANDARD", "DTSTART:19700101T000000",
            "TZOFFSETFROM:" + off, "TZOFFSETTO:" + off, "TZNAME:" + escape_text(st.tzname() or off),
            "END:STANDARD", "END:VTIMEZONE"]


def render_ics(spec, now=None):
    """spec (from wa_calendar.event_spec) -> the whole VCALENDAR text with CRLF line ends."""
    now = now or dt.datetime.now(dt.timezone.utc)
    zone = None if spec.get("all_day") else _zone_block(spec)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:" + PRODID, "CALSCALE:GREGORIAN", "METHOD:PUBLISH"]
    if zone:
        lines += zone
    lines += ["BEGIN:VEVENT", "UID:%s@%s" % (spec["key"], UID_DOMAIN), "DTSTAMP:" + _utc(now)]
    if spec.get("all_day"):
        d = dt.date.fromisoformat(spec["date"])
        lines += ["DTSTART;VALUE=DATE:" + d.strftime("%Y%m%d"),
                  "DTEND;VALUE=DATE:" + (d + dt.timedelta(days=1)).strftime("%Y%m%d")]
    elif zone:
        tzid = spec["tz_name"]
        lines += ["DTSTART;TZID=%s:%s" % (tzid, spec["start"].strftime("%Y%m%dT%H%M%S")),
                  "DTEND;TZID=%s:%s" % (tzid, spec["end"].strftime("%Y%m%dT%H%M%S"))]
    else:
        lines += ["DTSTART:" + _utc(spec["start"]), "DTEND:" + _utc(spec["end"])]
    lines.append("SUMMARY:" + escape_text(spec["title"]))
    if spec.get("place"):
        lines.append("LOCATION:" + escape_text(spec["place"]))
    if spec.get("description"):
        lines.append("DESCRIPTION:" + escape_text(spec["description"]))
    if not spec.get("all_day") and spec.get("reminder_min"):
        lines += ["BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:" + escape_text(spec["title"]),
                  "TRIGGER:-PT%dM" % int(spec["reminder_min"]), "END:VALARM"]
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(fold_line(x) for x in lines) + "\r\n"


# ---------------------------------------------------------------- create

def create(cfg, spec, dry_run=False, runner=None):
    """Write <live>/calendar/<key>.ics once. Returns {ok, url, event_id, error, already, path} (dry run adds preview)."""
    from wa_calendar import ics_file
    res = {"ok": False, "url": "", "event_id": "", "error": ""}
    path = ics_file(cfg, spec.get("key"))
    if path is None:
        res["error"] = "unsafe event key: %r" % spec.get("key")
        return res
    text = render_ics(spec)
    if dry_run:
        res["ok"] = True
        res["preview"] = ["would write %s" % path] + [x for x in text.split("\r\n") if x]
        return res
    url = "/calendar/%s.ics" % path.stem
    already = path.exists()
    if not already:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(str(path.parent), 0o700)
        except OSError:
            pass
        fd, tmp = tempfile.mkstemp(prefix=".%s." % path.name, dir=str(path.parent))
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(text.encode("utf-8"))
            os.chmod(tmp, 0o600)
            try:
                os.link(tmp, str(path))            # atomic and refuses to replace: the first writer wins
            except FileExistsError:
                already = True
            except OSError:
                if path.exists():
                    already = True
                else:
                    os.replace(tmp, str(path))
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    res.update(ok=True, url=url, event_id=path.stem, already=bool(already), path=str(path))
    return res


def probe(cfg, runner=None):
    from wa_calendar import local_tz_name
    folder = Path(W.live_dir(cfg)) / "calendar"
    n = len(list(folder.glob("*.ics"))) if folder.exists() else 0
    print("ics files go to %s (%d so far); the board opens /calendar/<key>.ics" % (folder, n))
    print("timed events use the zone %s" % (local_tz_name(cfg) or "(system offset, written in UTC)"))
    return 0


# ---------------------------------------------------------------- selftest

def _selftest(check=None, tmp=None):
    import shutil
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
    base = Path(tempfile.mkdtemp(prefix="calendar_ics_selftest_")) if made else Path(tmp) / "ics"
    try:
        cfg = C.selftest_cfg(base, {"destination": "ics", "timezone": "Asia/Tokyo"})
        live = Path(W.live_dir(cfg))
        key = "6f1c2b0e-3a4d-5e6f-8a9b-0c1d2e3f4a5b"
        note = C.compose_description("bring the printed quote; parking, level 2", "Test Contact", "+1 555 0100")
        r = C.create_event(cfg, "coffee, about the quote", "2026-09-20", "15:00", 90, "Test Cafe; corner", note, idempotency_key=key)
        path = live / "calendar" / ("%s.ics" % key)
        check("ics: ok with url /calendar/<key>.ics and event_id = key",
              r["ok"] and r["url"] == "/calendar/%s.ics" % key and r["event_id"] == key and r["error"] == "")
        check("ics: file written under <live>/calendar/", path.exists() and C.ics_file(cfg, key) == path)
        check("ics: file mode 0600 and folder 0700", path.exists() and (path.stat().st_mode & 0o777) == 0o600
              and (path.parent.stat().st_mode & 0o777) == 0o700)
        raw = path.read_bytes() if path.exists() else b""
        check("ics: every line ends with CRLF", raw.count(b"\n") == raw.count(b"\r\n") and raw.endswith(b"\r\n"))
        check("ics: no physical line over 75 octets", all(len(x) <= 75 for x in raw.split(b"\r\n")))
        text = unfold(raw.decode("utf-8"))
        lines = [x for x in text.split("\r\n") if x]
        check("ics: VCALENDAR wraps everything, VERSION 2.0 and PRODID present",
              lines[:1] == ["BEGIN:VCALENDAR"] and lines[-1:] == ["END:VCALENDAR"] and "VERSION:2.0" in lines
              and any(x.startswith("PRODID:") for x in lines))
        check("ics: UID from the idempotency key", "UID:%s@%s" % (key, UID_DOMAIN) in lines)
        check("ics: DTSTAMP in UTC", any(re.match(r"^DTSTAMP:\d{8}T\d{6}Z$", x) for x in lines))
        check("ics: VTIMEZONE for the owner's zone with its offset", "TZID:Asia/Tokyo" in lines and "TZOFFSETTO:+0900" in lines)
        check("ics: timed event in local wall clock with TZID, 90 minutes long",
              "DTSTART;TZID=Asia/Tokyo:20260920T150000" in lines and "DTEND;TZID=Asia/Tokyo:20260920T163000" in lines)
        check("ics: text escaped (comma, semicolon, newline)",
              "SUMMARY:coffee\\, about the quote" in lines and "LOCATION:Test Cafe\\; corner" in lines
              and any(x.startswith("DESCRIPTION:bring the printed quote\\; parking\\, level 2\\n\\nWhatsApp: Test Contact\\n") for x in lines))
        check("ics: five minute display alarm on a timed event", "TRIGGER:-PT5M" in lines and "ACTION:DISPLAY" in lines)
        before = raw
        r2 = C.create_event(cfg, "a rewritten title", "2026-09-20", "15:00", 60, "", "", idempotency_key=key)
        check("ics: same key returns the same url and does not rewrite the file",
              r2["ok"] and r2["url"] == r["url"] and r2.get("already") is True and path.read_bytes() == before)

        r3 = C.create_event(cfg, "venue walkthrough", "2026-9-25", "", idempotency_key="allday-1")
        t3 = unfold((live / "calendar" / "allday-1.ics").read_text(encoding="utf-8")) if r3["ok"] else ""
        check("ics: all day event uses VALUE=DATE with an exclusive next day end, no alarm, no zone",
              r3["ok"] and "DTSTART;VALUE=DATE:20260925" in t3 and "DTEND;VALUE=DATE:20260926" in t3
              and "VALARM" not in t3 and "TZID" not in t3)

        long_note = "\u957f\u5907\u6ce8" * 60 + " end"
        r4 = C.create_event(cfg, "\u8ddf\u5ba2\u6237\u559d\u5496\u5561\u8c08\u62a5\u4ef7", "2026-09-21", "09:30", 30, "", long_note,
                            idempotency_key="fold-1")
        b4 = (live / "calendar" / "fold-1.ics").read_bytes() if r4["ok"] else b""
        check("ics: long CJK text folds on character boundaries and unfolds back",
              r4["ok"] and len(b4.split(b"\r\n")) > 12 and all(len(x) <= 75 for x in b4.split(b"\r\n"))
              and ("DESCRIPTION:" + escape_text(long_note)) in unfold(b4.decode("utf-8")))

        cfg_utc = C.selftest_cfg(base / "utc", {"destination": "ics", "timezone": "Not/AZone"})
        r5 = C.create_event(cfg_utc, "call", "2026-09-22", "10:00", 60, "", "", idempotency_key="utc-1")
        t5 = (Path(W.live_dir(cfg_utc)) / "calendar" / "utc-1.ics").read_text(encoding="utf-8") if r5["ok"] else ""
        exp = dt.datetime(2026, 9, 22, 10, 0).astimezone().astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        check("ics: unknown zone name falls back to UTC times (system offset), no VTIMEZONE",
              r5["ok"] and ("DTSTART:%s" % exp) in t5 and "VTIMEZONE" not in t5)

        r6 = C.create_event(cfg, "dry", "2026-09-23", "11:00", 60, "", "", dry_run=True, idempotency_key="dry-1")
        check("ics: dry run is ok, returns a preview, writes nothing",
              r6["ok"] and r6["url"] == "" and r6["event_id"] == "" and r6.get("preview")
              and not (live / "calendar" / "dry-1.ics").exists())
        r7 = C.create_event(cfg, "odd key", "2026-09-24", "", idempotency_key="../../escape me")
        check("ics: an odd idempotency key is made safe and stays inside <live>/calendar/",
              r7["ok"] and r7["event_id"] == "escape-me" and (live / "calendar" / "escape-me.ics").exists())
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
    print("usage: calendar_ics.py --selftest   (normally used through wa_calendar.py)")
    sys.exit(0)
