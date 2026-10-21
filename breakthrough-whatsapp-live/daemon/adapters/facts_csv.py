#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
adapters/facts_csv.py · facts source "csv": a local CSV file, or a published https:// CSV link.

Covers anything that can export or publish a CSV: a Google Sheets "publish to the web" CSV link, an Airtable, Notion
or GoHighLevel export saved to disk, a payment processor's export.

Config (facts.csv):
  path       a local path (~ allowed) or an https:// URL; a list of them is also accepted (rows are concatenated)
  columns    standard field → the header of that column in your CSV (case and surrounding spaces ignored).
             Map only what your file has; look at a real header first, never map from memory.
               phone       required. Any spelling; a leading 0 gets facts.phone_default_cc
               name        the person's name
               paid        a paid flag: yes/no, true/false, 1/0, paid/unpaid, succeeded/failed, ✓/✗ (other values are
                           reported as warnings and count as not paid)
               amount      the amount; without a paid column, an amount above 0 counts as paid
               currency    the currency of the amount (else facts.currency)
               method      payment method
               date        payment or sign up date: ISO, YYYY/MM/DD, D/M/YYYY, "Jul 31, 2026", epoch seconds or ms
               product     what was bought or joined
               attended    attended flag, three states: yes, no, empty (unknown)
               record_id   a unique id per row (else "<label>#<line number>")
  day_first  true (default) reads 03/04/2026 as 3 April; false reads it as March 4
Label: facts.label (else "CSV") names the rows in the brain's lines and in person notes.

Every row becomes one person record. A paid row also becomes one payment linked to that record; a paid row with no
attended value is identity only (show false), so the brain is not told the same purchase twice.
A URL is fetched only when fetch() runs (the nightly facts job, wa_facts.py --dry-run or --write), never at import,
never by health(), never in --selftest (which feeds text through a fake reader).
"""

import csv
import datetime as dt
import io
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import wa_common as W  # noqa: E402
import adapters as A  # noqa: E402

FIELDS = ("phone", "name", "paid", "amount", "currency", "method", "date", "product", "attended", "record_id")
MAX_BYTES = 20 * 1024 * 1024
TIMEOUT = 30


def _label(cfg):
    return W.source_label(cfg, "facts") or "CSV"


def display_source(src):
    """What logs and facts.json may show about a source: a local path as is, a URL only as scheme and host
    (a published link is private enough to keep out of files that get backed up)."""
    s = str(src or "")
    if s.lower().startswith(("https://", "http://")):
        u = urllib.parse.urlsplit(s)
        return "%s://%s/…" % (u.scheme, u.hostname or "?")
    return s


def _http_get(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": "whatsapp-live-facts/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(MAX_BYTES + 1)
    except (urllib.error.URLError, OSError) as e:
        if "CERTIFICATE" not in str(e).upper() and "SSL" not in str(e).upper():
            raise
        conf = 'url = "%s"\n' % url.replace("\\", "\\\\").replace('"', '\\"')
        r = subprocess.run(["/usr/bin/curl", "-sSL", "--fail", "--max-time", str(int(timeout)), "-K", "-"],
                           input=conf.encode("utf-8"), capture_output=True, timeout=timeout + 10)
        if r.returncode != 0:
            raise RuntimeError("curl exit %d: %s" % (r.returncode, r.stderr.decode("utf-8", "replace")[-200:]))
        data = r.stdout
    if len(data) > MAX_BYTES:
        raise RuntimeError("larger than %d MB" % (MAX_BYTES // (1024 * 1024)))
    return data


def _decode(data):
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def read_source(src):
    """Bytes of one source decoded to text. https:// is fetched; http:// is refused; anything else is a local file."""
    s = str(src).strip()
    low = s.lower()
    if low.startswith("https://"):
        return _decode(_http_get(s))
    if low.startswith("http://"):
        raise RuntimeError("only https:// links are accepted")
    p = Path(os.path.expanduser(s))
    return _decode(p.read_bytes())


def parse_csv(text):
    """(header, rows as dicts). Delimiter sniffed among comma, semicolon and tab; a byte order mark and blank rows dropped."""
    text = (text or "").lstrip(chr(0xFEFF))
    sample = text[:4096]
    dialect = csv.excel
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        pass
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    header = [h for h in (reader.fieldnames or []) if h is not None]
    if len(header) <= 1 and "," in (text.split("\n", 1)[0] if text else "") and dialect is not csv.excel:
        reader = csv.DictReader(io.StringIO(text), dialect=csv.excel)
        header = [h for h in (reader.fieldnames or []) if h is not None]
    rows = []
    for r in reader:
        if any(isinstance(v, str) and v.strip() for v in r.values()):
            rows.append(r)
    return header, rows


def fetch(cfg, reader=None):
    """The facts adapter contract: {"people", "payments", "errors", "warnings", "sources", "config"}.
    reader(src) -> text replaces read_source (the selftest uses it; nothing else should)."""
    fc = (cfg or {}).get("facts") or {}
    cc = fc.get("csv") or {}
    out = {"people": [], "payments": [], "errors": [], "warnings": [], "sources": {}, "config": {}}
    paths = cc.get("path")
    if isinstance(paths, str):
        paths = [paths] if paths.strip() else []
    paths = [str(x) for x in (paths or []) if str(x).strip()]
    cols = dict((str(k).strip(), str(v).strip()) for k, v in (cc.get("columns") or {}).items() if str(v or "").strip())
    day_first = bool(cc.get("day_first", True))
    label = _label(cfg)
    out["config"] = {"paths": [display_source(p) for p in paths], "columns": cols}
    if not paths:
        out["errors"].append("facts.csv.path is not set")
    if "phone" not in cols:
        out["errors"].append("facts.csv.columns.phone is not set (map it to the header of your phone column)")
    unknown = sorted(k for k in cols if k not in FIELDS)
    if unknown:
        out["warnings"].append("facts.csv.columns has unknown fields ignored: %s (known: %s)" % (", ".join(unknown), ", ".join(FIELDS)))
    if out["errors"]:
        return out
    read = reader or read_source
    odd_paid = {}
    for i, src in enumerate(paths):
        shown = display_source(src)
        tag = label if len(paths) == 1 else "%s %d" % (label, i + 1)
        try:
            text = read(src)
        except Exception as e:
            out["errors"].append("cannot read %s: %s" % (shown, str(e)[:200]))
            continue
        header, rows = parse_csv(text)
        hmap = dict((h.strip().lower(), h) for h in header)
        missing = [f for f, col in cols.items() if f in FIELDS and col.lower() not in hmap]
        if missing:
            out["errors"].append("%s: header has no column for %s (header: %s)" % (
                shown, ", ".join("%s=%r" % (f, cols[f]) for f in missing), ", ".join(header)[:300]))
            continue
        out["sources"][tag] = len(rows)

        for n, row in enumerate(rows, start=2):
            def get(field):
                if field not in cols:
                    return None
                return row.get(hmap[cols[field].lower()])
            rid = A.clean_text(get("record_id")) or "%s#%d" % (tag, n)
            amount = A.clean_amount(get("amount"))
            if "paid" in cols:
                raw_paid = get("paid")
                paid = A.clean_tri(raw_paid)
                if paid is None and A.clean_null(raw_paid) is not None:
                    odd_paid[str(raw_paid).strip()] = odd_paid.get(str(raw_paid).strip(), 0) + 1
                paid = bool(paid)
            else:
                paid = amount is not None and amount > 0
            attended = A.clean_tri(get("attended")) if "attended" in cols else None
            date = A.clean_date(get("date"), day_first=day_first)
            product = A.clean_text(get("product"))
            out["people"].append({
                "table": tag, "kind": "contact", "record_id": rid, "name": A.clean_name(get("name")),
                "phone": A.clean_text(get("phone")), "date": date, "product": product, "role": None, "via_record": None,
                "attended": attended, "attendance": [], "tag": None, "flags": [], "external_id": A.clean_text(get("record_id")),
                "show": (not paid) or attended is not None,
            })
            if paid:
                out["payments"].append({
                    "table": tag, "record_id": rid, "date": date, "amount": amount,
                    "currency": A.clean_text(get("currency")) or (str(fc.get("currency") or "") or None),
                    "method": A.clean_text(get("method")), "product": product, "participants": None,
                    "phone": A.clean_text(get("phone")), "name": A.clean_name(get("name")), "person_record": rid,
                    "external_id": A.clean_text(get("record_id")), "remark": None,
                })
    if odd_paid:
        out["warnings"].append("paid column values not understood, counted as not paid: %s" % ", ".join(
            "%r x%d" % (k, v) for k, v in sorted(odd_paid.items())[:10]))
    return out


def health(cfg):
    """(ok, why) for the digest health line; never fetches a URL."""
    cc = (((cfg or {}).get("facts") or {}).get("csv") or {})
    paths = cc.get("path")
    if isinstance(paths, str):
        paths = [paths] if paths.strip() else []
    paths = [str(x) for x in (paths or []) if str(x).strip()]
    if not paths:
        return False, "facts.csv.path is not set"
    notes = []
    for src in paths:
        if src.lower().startswith("https://"):
            notes.append("link set")
            continue
        p = Path(os.path.expanduser(src))
        try:
            st = p.stat()
        except OSError:
            return False, "file not found: %s" % p
        if not os.access(str(p), os.R_OK):
            return False, "file not readable: %s" % p
        notes.append("%.0f KB, changed %s" % (st.st_size / 1024.0, dt.datetime.fromtimestamp(st.st_mtime).strftime("%m-%d %H:%M")))
    return True, "; ".join(notes)


# ---------------------------------------------------------------- selftest

def selftest():
    check = W._Checks("facts_csv")
    cfg = {"facts": {"source": "csv", "currency": "MYR", "label": "Sales sheet",
                     "csv": {"path": "~/exports/sales.csv", "day_first": True,
                             "columns": {"name": "Full Name", "phone": "WhatsApp", "paid": "Status", "amount": "Amount",
                                         "date": "Paid On", "product": "Plan", "attended": "Showed Up", "record_id": "Row ID",
                                         "colour": "Favourite"}}}}
    text = ("\ufeffRow ID,Full Name,WhatsApp,Status,Amount,Paid On,Plan,Showed Up\n"
            "r1,Alex Rivera,+60 12-000 0001,paid,\"RM 1,250.00\",31/07/2026,Workshop,yes\n"
            "r2,Blake Chen,012-000 0002,succeeded,1460,2026-09-01T10:00:00+08:00,Coaching,\n"
            "r3,Casey Ng,,unpaid,0,03/04/2026,Workshop,no\n"
            ",,,,,,,\n"
            "r4,Dana Park,+65 9000 0001,maybe,100,Jul 31 2026,Workshop,\n")
    seen = []

    def fake_reader(src):
        seen.append(src)
        return text
    got = fetch(cfg, reader=fake_reader)
    check(not got["errors"] and got["sources"] == {"Sales sheet": 4}, "four data rows read, blank row dropped, label from facts.label")
    check(len(got["people"]) == 4 and len(got["payments"]) == 2, "every row a person record, two paid rows become payments")
    p1, pay1 = got["people"][0], got["payments"][0]
    check(p1["phone"] == "+60 12-000 0001" and p1["date"] == "2026-07-31" and p1["attended"] is True and p1["show"] is True,
          "row 1: raw phone kept, D/M/YYYY date, attended, shown because attendance is known")
    check(pay1["amount"] == 1250 and pay1["currency"] == "MYR" and pay1["person_record"] == "r1", "payment amount cleaned, currency default, linked to its row")
    check(got["people"][1]["show"] is False and got["payments"][1]["date"] == "2026-09-01", "paid row without attendance is identity only")
    check(got["people"][2]["attended"] is False and got["people"][2]["date"] == "2026-04-03", "unpaid row kept as a person, day_first date")
    check(got["people"][3]["date"] == "2026-07-31" and any("maybe" in w for w in got["warnings"])
          and any("colour" in w for w in got["warnings"]), "month name date; odd paid value and unknown column reported as warnings")
    check(seen == ["~/exports/sales.csv"] and got["config"]["paths"] == ["~/exports/sales.csv"], "reader called once with the configured path")

    semi = "Phone;Amount\n+60 12-000 0003;50\n+60 12-000 0004;0\n"
    got2 = fetch({"facts": {"csv": {"path": "x.csv", "columns": {"phone": "phone", "amount": "AMOUNT"}}}}, reader=lambda s: semi)
    check(not got2["errors"] and len(got2["payments"]) == 1 and got2["people"][0]["table"] == "CSV",
          "semicolon file, case insensitive headers, amount above 0 means paid, default label CSV")
    bad = fetch({"facts": {"csv": {"path": "x.csv", "columns": {"phone": "Mobile"}}}}, reader=lambda s: "Phone,Name\n1,2\n")
    check(bad["errors"] and "Mobile" in bad["errors"][0], "a mapped column missing from the header is an error")
    check(fetch({"facts": {"csv": {"path": "", "columns": {}}}})["errors"][:2] ==
          ["facts.csv.path is not set", "facts.csv.columns.phone is not set (map it to the header of your phone column)"],
          "unset path and phone mapping are errors")

    def failing(src):
        raise IOError("permission denied")
    check("cannot read" in fetch({"facts": {"csv": {"path": "x.csv", "columns": {"phone": "Phone"}}}}, reader=failing)["errors"][0],
          "an unreadable file is an error")
    url = "https://docs.example.com/spreadsheets/d/e/SECRETID/pub?output=csv"
    got3 = fetch({"facts": {"csv": {"path": url, "columns": {"phone": "Phone"}}}}, reader=lambda s: "Phone\n+60 12-000 0005\n")
    check(got3["config"]["paths"] == ["https://docs.example.com/…"] and "SECRETID" not in str(got3), "a URL is shown only as scheme and host")
    try:
        read_source("http://example.com/x.csv")
        check(False, "http refused")
    except RuntimeError:
        check(True, "plain http links are refused")
    check(health({"facts": {"csv": {"path": url}}}) == (True, "link set"), "health never fetches a link")
    check(health({"facts": {"csv": {"path": "/nonexistent/folder/x.csv"}}})[0] is False, "health reports a missing file")
    return check.done()


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest())
    print("usage: facts_csv.py --selftest   (the adapter runs through wa_facts.py)")
    sys.exit(0)
