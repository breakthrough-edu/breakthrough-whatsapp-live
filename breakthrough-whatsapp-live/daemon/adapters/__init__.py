# -*- coding: utf-8 -*-
"""
adapters · the shipped implementations behind the daemon's interfaces (DESIGN.md §5).

Each interface module owns a small registry and imports its implementation lazily by module name, so a broken or
missing adapter only stops that one block:

  facts source        wa_facts.py          adapters.facts_csv · adapters.facts_lark
  second source       wa_second_source.py  adapters.second_source_lark
  calendar            wa_calendar.py       adapters.calendar_ics · adapters.calendar_lark

`load(module_name)` imports one of them. The value cleaners below are shared by every adapter that reads rows from
somebody else's system (a CSV export, a Lark Base, a spreadsheet): records come back in many shapes (empty strings,
the string "null", millisecond timestamps, "1,250.00", link arrays), and each cleaner turns one of those into a single
predictable shape. Python 3.9 standard library only.
"""

import datetime as dt
import importlib
import importlib.util
import os
import re
from pathlib import Path

__all__ = ["load", "owner_dir", "owner_module","clean_null", "clean_text", "clean_name", "clean_list", "clean_first", "clean_links",
           "clean_tri", "clean_date", "clean_amount", "clean_int", "TRUE_WORDS", "FALSE_WORDS"]


def owner_dir(cfg):
    """<live>/adapters: where an owner keeps implementations they wrote themselves. It lives outside the payload, so
    `npx skills update` never replaces it. None when the config has no live folder."""
    if not isinstance(cfg, dict):
        return None
    live = (cfg.get("paths") or {}).get("live_dir") if isinstance(cfg.get("paths"), dict) else None
    live = live or cfg.get("live_dir")
    return Path(os.path.expanduser(str(live))) / "adapters" if live else None


def owner_module(cfg, short_name):
    """The owner's own adapter <live>/adapters/<short_name>.py (for example facts_airtable.py), imported from its file, or
    None when there is no such file. An owner file with the same name as a shipped adapter wins over the shipped one."""
    d = owner_dir(cfg)
    if d is None:
        return None
    f = d / ("%s.py" % short_name)
    if not f.is_file():
        return None
    spec = importlib.util.spec_from_file_location("owner_adapters.%s" % short_name, str(f))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load(module_name, cfg=None):
    """Import an adapter module by name ("adapters.facts_csv" or just "facts_csv"). With cfg, the owner's own file of the
    same name under <live>/adapters/ is used first. Raises ImportError with the name."""
    short = module_name[len("adapters."):] if module_name.startswith("adapters.") else module_name
    if cfg is not None:
        mod = owner_module(cfg, short)
        if mod is not None:
            return mod
    return importlib.import_module("adapters." + short)


# Words a yes/no column may hold. Anything else is "unknown" (None), never silently True.
TRUE_WORDS = {"true", "yes", "y", "1", "paid", "✓", "✔", "☑", "x", "是", "有", "对", "已付", "已付款", "已缴", "已缴费",
              "succeeded", "success", "successful", "complete", "completed", "attended", "present", "done", "ok", "checked"}
FALSE_WORDS = {"false", "no", "n", "0", "unpaid", "✗", "✘", "☐", "否", "没有", "无", "未付", "未付款", "未缴", "failed",
               "refunded", "cancelled", "canceled", "absent", "pending", "unchecked", "declined", "void"}


def clean_null(v):
    """Empty in any spelling becomes None: None, "", "null", "none", "nil", an empty list or dict."""
    if v is None:
        return None
    if isinstance(v, str) and v.strip().lower() in ("", "null", "none", "nil", "undefined"):
        return None
    if isinstance(v, (list, tuple, dict)) and not v:
        return None
    return v


def clean_text(v):
    """Any value to one line of text or None. Rich text segments ([{"text": ...}]) are joined; lists joined with ", "."""
    v = clean_null(v)
    if v is None:
        return None
    if isinstance(v, dict):
        v = v.get("text") or v.get("name") or v.get("value") or v.get("id")
        return clean_text(v)
    if isinstance(v, (list, tuple)):
        if all(isinstance(x, dict) and "text" in x for x in v):
            joined = "".join(str(x.get("text") or "") for x in v)
            return re.sub(r"\s+", " ", joined).strip() or None
        parts = []
        for x in v:
            t = clean_text(x)
            if t:
                parts.append(t)
        return ", ".join(parts) if parts else None
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = re.sub(r"\s+", " ", str(v)).strip()
    return s or None


def clean_name(v):
    return clean_text(v)


def clean_list(v):
    """list, single value or empty to a list of non empty strings."""
    v = clean_null(v)
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        out = []
        for x in v:
            t = clean_text(x)
            if t:
                out.append(t)
        return out
    t = clean_text(v)
    return [t] if t else []


def clean_first(v):
    xs = clean_list(v)
    return xs[0] if xs else None


def clean_links(v):
    """A link column ([{"id": "rec1"}, {"record_ids": ["rec2"]}] or ["rec3"]) to a list of record ids."""
    v = clean_null(v)
    if v is None:
        return []
    if not isinstance(v, (list, tuple)):
        v = [v]
    out = []
    for x in v:
        if isinstance(x, dict):
            if x.get("id"):
                out.append(str(x["id"]))
            for rid in x.get("record_ids") or []:
                if rid:
                    out.append(str(rid))
        elif isinstance(x, str) and x.strip():
            out.append(x.strip())
    return out


def clean_tri(v, empty_is_false=False):
    """Three states: True, False, or None (not filled or not readable). A non empty link list counts as True."""
    v = clean_null(v)
    if v is None:
        return False if empty_is_false else None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, (list, tuple)):
        return len(v) > 0
    if isinstance(v, dict):
        return True
    s = str(v).strip().lower()
    if s in TRUE_WORDS:
        return True
    if s in FALSE_WORDS:
        return False
    return None


_MONTH_FORMATS = ("%b %d, %Y", "%B %d, %Y", "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y", "%d-%b-%Y", "%d %b, %Y")


def _valid_date(y, m, d):
    try:
        return dt.date(int(y), int(m), int(d)).isoformat()
    except (TypeError, ValueError):
        return None


def clean_date(v, day_first=True):
    """A date in many spellings to "YYYY-MM-DD", or None when it cannot be read.
    Accepts ISO strings with or without time, YYYY/MM/DD, D/M/YYYY (day_first) or M/D/YYYY, "Jul 31, 2026",
    and epoch timestamps in seconds or milliseconds (Lark returns milliseconds)."""
    v = clean_null(v)
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        x = float(v)
        if x > 1e11:
            x = x / 1000.0
        if x < 1e8:
            return None
        try:
            return dt.datetime.fromtimestamp(x).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    s = str(v).strip()
    if re.fullmatch(r"\d{10}(\d{3})?", s):
        return clean_date(int(s), day_first)
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        return _valid_date(m.group(1), m.group(2), m.group(3))
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if a > 12:
            return _valid_date(y, b, a)
        if b > 12:
            return _valid_date(y, a, b)
        return _valid_date(y, b, a) if day_first else _valid_date(y, a, b)
    head = re.split(r"\s+\d{1,2}:\d{2}", s)[0].strip()
    for fmt in _MONTH_FORMATS:
        try:
            return dt.datetime.strptime(head, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def clean_amount(v):
    """An amount to int (when whole) or float, or None. "RM 1,250.00" is 1250, "1.250,50" is 1250.5, "(20)" is -20."""
    v = clean_null(v)
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(v) if float(v).is_integer() else v
    if isinstance(v, (list, tuple)):
        return clean_amount(v[0]) if v else None
    s = str(v).strip()
    neg = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    s = re.sub(r"[^\d.,]", "", s)
    if not s or not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    if s.count(".") > 1:
        head, _, tail = s.rpartition(".")
        s = head.replace(".", "") + "." + tail
    try:
        f = float(s)
    except ValueError:
        return None
    if neg:
        f = -f
    return int(f) if f.is_integer() else f


def clean_int(v):
    a = clean_amount(v)
    if a is None:
        return None
    try:
        return int(a)
    except (TypeError, ValueError):
        return None


def _selftest():
    fails = []

    def check(cond, msg):
        print(("  ok   " if cond else "  FAIL ") + msg)
        if not cond:
            fails.append(msg)
    check(clean_null("null") is None and clean_null([]) is None and clean_null(0) == 0, "clean_null")
    check(clean_text([{"text": "Alex "}, {"text": "Rivera"}]) == "Alex Rivera" and clean_text(["a", None, "b"]) == "a, b"
          and clean_text(3.0) == "3", "clean_text: rich text, lists, whole floats")
    check(clean_list("x") == ["x"] and clean_first([None, "y"]) == "y" and clean_links([{"id": "r1"}, {"record_ids": ["r2"]}, "r3"]) == ["r1", "r2", "r3"],
          "clean_list, clean_first, clean_links")
    check(clean_tri("Yes") is True and clean_tri("unpaid") is False and clean_tri("maybe") is None and clean_tri(None, empty_is_false=True) is False
          and clean_tri([{"id": "x"}]) is True, "clean_tri three states")
    check(clean_date("31/07/2026") == "2026-07-31" and clean_date("03/04/2026") == "2026-04-03" and clean_date("03/04/2026", day_first=False) == "2026-03-04"
          and clean_date("Jul 31, 2026") == "2026-07-31" and clean_date("2026-02-30") is None and clean_date("soon") is None, "clean_date")
    check(clean_amount("RM 1,250.00") == 1250 and clean_amount("1.250,50") == 1250.5 and clean_amount("(20)") == -20
          and clean_amount("12,5") == 12.5 and clean_amount(True) is None and clean_int("4 seats") == 4, "clean_amount and clean_int")
    print("adapters selftest %s (%d failed)" % ("PASS" if not fails else "FAIL", len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(_selftest() if "--selftest" in _sys.argv[1:] else 0)
