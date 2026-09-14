#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
adapters/facts_lark.py · facts source "lark": tables in a Lark (Feishu) Base, read with lark-cli.

Every table id and field name lives in config (facts.lark); nothing about anyone's base is in this file.

  "lark": {
    "people_tables": [ {
        "label": "Roster",                    shown as the line label to the brain and in person notes
        "base": "<base token>", "table": "<table id>",
        "kind": "enrollment",                 enrollment | lead | contact (informational)
        "fields": {
          "name": "<field>", "phone": "<field>", "date": "<field>", "product": "<field>",
          "role": "<field>",                  free text such as a tier
          "via_link": "<field>",              link to the record (in any people table) whose purchase covers this person
          "attended": "<field>" | ["<field>", ...] | {"Day 1": "<field>", "Day 2": "<field>"},
          "tag": "<field>",                   a category shown in brackets
          "flags": {"<label>": "<field>" | {"field": "<field>", "empty_is_false": true}},
          "external_id": "<field>"
        } } ],
    "payment_tables": [ {
        "label": "Payments", "base": "<base token>", "table": "<table id>", "currency": "MYR",
        "fields": {
          "date": "<field>", "amount": "<field>", "currency": "<field>", "method": "<field>", "product": "<field>",
          "participants": "<field>", "remark": "<field>", "external_id": "<field>", "name": "<field>",
          "person_link": "<field>",           link to a record in one of the people tables (the payer)
          "phone": "<field>"                  used only when the link is empty or points nowhere
        } } ]
  }

Reads `lark-cli base +record-list --base-token B --table-id T --limit 200 --offset N --format json` as the logged in
lark-cli user, page by page until has_more is false (at most 20000 rows per table). lark-cli comes from
bin.lark_cli; when it is a node script it runs with bin.node, because launchd's PATH has no node.
A table that cannot be read (login expired, no permission, wrong id) is an error: wa_facts then keeps the previous
facts.json instead of writing a partial one.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import wa_common as W  # noqa: E402
import adapters as A  # noqa: E402

PAGE = 200
MAX_ROWS = 20000
TIMEOUT = 90
PEOPLE_FIELDS = ("name", "phone", "date", "product", "role", "via_link", "attended", "tag", "flags", "external_id")
PAYMENT_FIELDS = ("date", "amount", "currency", "method", "product", "participants", "remark", "external_id", "name",
                  "person_link", "phone")


class LarkReadError(RuntimeError):
    pass


def _is_node_script(path):
    try:
        with open(os.path.realpath(path), "rb") as f:
            first = f.readline(200)
        return first.startswith(b"#!") and b"node" in first
    except OSError:
        return False


def default_runner(cfg):
    """runner(args, timeout) -> (returncode, stdout, stderr) for lark-cli, with the daemon's environment."""
    cli = W.resolve_bin(cfg, "lark_cli")
    node = W.resolve_bin(cfg, "node")
    env = W.subprocess_env(cfg)

    def run(args, timeout=TIMEOUT):
        if not cli:
            return 127, "", "lark-cli not found (set bin.lark_cli)"
        cmd = ([node, cli] if (node and _is_node_script(cli)) else [cli]) + list(args)
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return 124, "", "lark-cli timed out after %ds" % timeout
        except OSError as e:
            return 127, "", str(e)
        return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")
    return run


def json_from_output(out):
    """lark-cli sometimes prints a progress line before the JSON; parse from the first line that starts with {."""
    s = out or ""
    for m in re.finditer(r"(?m)^\{", s):
        try:
            return json.loads(s[m.start():])
        except ValueError:
            continue
    i = s.find("{")
    if i >= 0:
        try:
            return json.loads(s[i:])
        except ValueError:
            return None
    return None


def records(base, table, runner):
    """(rows as {field: value}, record ids) for one table, all pages. Raises LarkReadError with a readable reason."""
    rows, ids, fields, offset = [], [], None, 0
    while True:
        args = ["base", "+record-list", "--base-token", base, "--table-id", table,
                "--limit", str(PAGE), "--offset", str(offset), "--format", "json"]
        rc, out, err = runner(args, TIMEOUT)
        j = json_from_output(out)
        if not isinstance(j, dict):
            raise LarkReadError("lark-cli returned no JSON (exit %s): %s" % (rc, (err or out or "").strip()[-200:]))
        if not j.get("ok", False):
            raise LarkReadError("lark-cli answered ok=false: %s" % json.dumps(j.get("error") or j.get("msg") or j, ensure_ascii=False)[:200])
        d = j.get("data") or {}
        fields = fields or d.get("fields") or []
        page = d.get("data") or []
        for row in page:
            rows.append(dict(zip(fields, row)) if isinstance(row, list) else (row if isinstance(row, dict) else {}))
        got_ids = d.get("record_id_list") or []
        ids.extend(list(got_ids) + [None] * (len(page) - len(got_ids)))
        if not d.get("has_more") or not page:
            break
        offset += len(page)
        if offset >= MAX_ROWS:
            break
    return rows, ids


def _attended(spec, row):
    """(attended tri, attendance [[label, tri]]) from a string, list or dict field spec."""
    if not spec:
        return None, []
    if isinstance(spec, str):
        return A.clean_tri(row.get(spec)), []
    pairs = []
    if isinstance(spec, dict):
        pairs = [[str(lab), A.clean_tri(row.get(field))] for lab, field in spec.items()]
    elif isinstance(spec, (list, tuple)):
        pairs = [[str(field), A.clean_tri(row.get(field))] for field in spec]
    vals = [v for _, v in pairs]
    tri = True if True in vals else (False if False in vals else None)
    return tri, pairs if isinstance(spec, dict) else []


def _flags(spec, row):
    out = []
    for lab, f in (spec or {}).items():
        if isinstance(f, dict):
            out.append([str(lab), A.clean_tri(row.get(f.get("field")), empty_is_false=bool(f.get("empty_is_false")))])
        else:
            out.append([str(lab), A.clean_tri(row.get(f))])
    return out


def _table_ok(t, kind, i):
    if not isinstance(t, dict):
        return "facts.lark.%s[%d] is not an object" % (kind, i)
    if not str(t.get("base") or "").strip() or not str(t.get("table") or "").strip():
        return "facts.lark.%s[%d] needs base and table" % (kind, i)
    if kind == "people_tables" and not ((t.get("fields") or {}).get("phone")):
        return "facts.lark.people_tables[%d] needs fields.phone" % i
    return None


def fetch(cfg, runner=None):
    """The facts adapter contract: {"people", "payments", "errors", "warnings", "sources", "config"}."""
    fc = (cfg or {}).get("facts") or {}
    lc = fc.get("lark") or {}
    out = {"people": [], "payments": [], "errors": [], "warnings": [], "sources": {}, "config": {"people_tables": [], "payment_tables": []}}
    pts, pays = lc.get("people_tables") or [], lc.get("payment_tables") or []
    if not pts and not pays:
        out["errors"].append("facts.lark has no people_tables and no payment_tables")
        return out
    run = runner or default_runner(cfg)
    default_currency = str(fc.get("currency") or "") or None
    for kind, tables in (("people_tables", pts), ("payment_tables", pays)):
        allowed = PEOPLE_FIELDS if kind == "people_tables" else PAYMENT_FIELDS
        for i, t in enumerate(tables):
            bad = _table_ok(t, kind, i)
            if bad:
                out["errors"].append(bad)
                continue
            label = str(t.get("label") or t.get("table"))
            f = t.get("fields") or {}
            extra = sorted(k for k in f if k not in allowed)
            if extra:
                out["warnings"].append("%s: unknown fields ignored: %s" % (label, ", ".join(extra)))
            out["config"][kind].append({"label": label, "base": t["base"], "table": t["table"]})
            try:
                rows, ids = records(str(t["base"]), str(t["table"]), run)
            except LarkReadError as e:
                out["errors"].append("%s: %s" % (label, e))
                continue
            out["sources"][label] = len(rows)
            for rid, row in zip(ids, rows):
                if kind == "people_tables":
                    att, attendance = _attended(f.get("attended"), row)
                    links = A.clean_links(row.get(f.get("via_link"))) if f.get("via_link") else []
                    out["people"].append({
                        "table": label, "kind": str(t.get("kind") or "contact"), "record_id": rid,
                        "name": A.clean_name(row.get(f.get("name"))) if f.get("name") else None,
                        "phone": A.clean_text(row.get(f.get("phone"))),
                        "date": A.clean_date(row.get(f.get("date"))) if f.get("date") else None,
                        "product": " / ".join(A.clean_list(row.get(f.get("product")))) or None if f.get("product") else None,
                        "role": A.clean_first(row.get(f.get("role"))) if f.get("role") else None,
                        "via_record": links[0] if links else None,
                        "attended": att, "attendance": attendance,
                        "tag": A.clean_first(row.get(f.get("tag"))) if f.get("tag") else None,
                        "flags": _flags(f.get("flags"), row),
                        "external_id": A.clean_first(row.get(f.get("external_id"))) if f.get("external_id") else None,
                        "show": True,
                    })
                else:
                    links = A.clean_links(row.get(f.get("person_link"))) if f.get("person_link") else []
                    cur = A.clean_first(row.get(f.get("currency"))) if f.get("currency") else None
                    out["payments"].append({
                        "table": label, "record_id": rid,
                        "date": A.clean_date(row.get(f.get("date"))) if f.get("date") else None,
                        "amount": A.clean_amount(row.get(f.get("amount"))) if f.get("amount") else None,
                        "currency": cur or (str(t.get("currency") or "") or None) or default_currency,
                        "method": A.clean_first(row.get(f.get("method"))) if f.get("method") else None,
                        "product": ", ".join(A.clean_list(row.get(f.get("product")))) or None if f.get("product") else None,
                        "participants": A.clean_int(row.get(f.get("participants"))) if f.get("participants") else None,
                        "phone": A.clean_text(row.get(f.get("phone"))) if f.get("phone") else None,
                        "name": A.clean_name(row.get(f.get("name"))) if f.get("name") else None,
                        "person_record": links[0] if links else None,
                        "external_id": A.clean_first(row.get(f.get("external_id"))) if f.get("external_id") else None,
                        "remark": A.clean_text(row.get(f.get("remark"))) if f.get("remark") else None,
                    })
    return out


def health(cfg, runner=None):
    """(ok, why) from `lark-cli auth status`: is the user identity available."""
    run = runner or default_runner(cfg)
    rc, out, err = run(["auth", "status"], 30)
    j = json_from_output(out) or {}
    u = (j.get("identities") or {}).get("user") or {}
    if not j:
        return False, ("lark-cli auth status gave no JSON: %s" % (err or out or "").strip()[-100:])[:120]
    return bool(u.get("available")), str(u.get("message") or u.get("status") or ("user login ok" if u.get("available") else "user login missing"))[:120]


# ---------------------------------------------------------------- selftest

def selftest():
    check = W._Checks("facts_lark")
    cfg = {"facts": {"source": "lark", "currency": "MYR", "lark": {
        "people_tables": [{"label": "Roster", "base": "baseFAKE1", "table": "tblFAKEroster", "kind": "enrollment",
                           "fields": {"name": "Name", "phone": "Mobile", "date": "Created", "product": "Cohort", "role": "Tier",
                                      "via_link": "Covered By", "attended": {"Day 1": "Day 1 Present", "Day 2": "Day 2 Present"},
                                      "flags": {"joined group": "Joined", "came": {"field": "Came Link", "empty_is_false": True}},
                                      "tag": "Source", "external_id": "CRM Id", "shoe_size": "Shoe"}}],
        "payment_tables": [{"label": "Payments", "base": "baseFAKE1", "table": "tblFAKEpay", "currency": "SGD",
                            "fields": {"date": "Paid At", "amount": "Amount", "method": "Method", "participants": "Seats",
                                       "person_link": "Payer", "phone": "Payer Phone", "product": "Cohort"}}]}}}
    roster_fields = ["Name", "Mobile", "Created", "Cohort", "Tier", "Covered By", "Day 1 Present", "Day 2 Present", "Joined", "Came Link", "Source", "CRM Id"]
    roster_p1 = [["Alex Rivera", "+60 12-000 0001", 1784390400000, ["Workshop August"], "Member", None, True, True, True, [{"id": "recX"}], ["referral"], "crm-1"],
                 [[{"text": "Blake "}, {"text": "Chen"}], "0120000002", "2026-07-19T00:00:00.000+08:00", ["Workshop August"], None, [{"id": "recA1"}], True, False, None, None, "null", None]]
    roster_p2 = [["Casey Ng", "", None, None, None, None, None, None, False, [], None, None]]
    pay_fields = ["Paid At", "Amount", "Method", "Seats", "Payer", "Payer Phone", "Cohort"]
    pay_rows = [["2026-07-31T00:00:00.000+08:00", "2,788", ["Bank Transfer"], 4, [{"id": "recA1"}], "+60120000001", ["August"]],
                [1788192000000, "800", ["Card"], None, None, "+65 9000 0001", None]]
    calls = []

    def runner(args, timeout):
        calls.append(list(args))
        if args[:2] == ["auth", "status"]:
            return 0, '{"identities": {"user": {"available": true, "message": "user ready"}}}', ""
        table, offset = args[args.index("--table-id") + 1], int(args[args.index("--offset") + 1])
        if table == "tblFAKEroster" and offset == 0:
            data = {"fields": roster_fields, "data": roster_p1, "record_id_list": ["recA1", "recB"], "has_more": True}
        elif table == "tblFAKEroster":
            data = {"fields": roster_fields, "data": roster_p2, "record_id_list": ["recC"], "has_more": False}
        elif table == "tblFAKEpay":
            data = {"fields": pay_fields, "data": pay_rows, "record_id_list": ["recP1", "recP2"], "has_more": False}
        else:
            return 1, "", "no such table"
        return 0, "[page 1] fetching...\n" + json.dumps({"ok": True, "data": data}), ""
    got = fetch(cfg, runner=runner)
    check(not got["errors"] and got["sources"] == {"Roster": 3, "Payments": 2}, "two tables read, roster over two pages")
    offsets = [c[c.index("--offset") + 1] for c in calls if "--table-id" in c and "tblFAKEroster" in c]
    check(offsets == ["0", "2"], "pagination follows has_more with the right offsets")
    a, b, c = got["people"]
    check(a["record_id"] == "recA1" and a["name"] == "Alex Rivera" and a["product"] == "Workshop August" and a["role"] == "Member",
          "person fields mapped from config names")
    check(a["attended"] is True and a["attendance"] == [["Day 1", True], ["Day 2", True]] and a["flags"] == [["joined group", True], ["came", True]],
          "attendance pairs and flags, link flag counts as true")
    check(a["date"] is not None and re.fullmatch(r"\d{4}-\d{2}-\d{2}", a["date"]) is not None, "millisecond timestamp becomes a date")
    check(b["name"] == "Blake Chen" and b["via_record"] == "recA1" and b["attended"] is True and b["tag"] is None and b["flags"][1] == ["came", False],
          "rich text name, via link, empty_is_false flag, string null tag")
    check(c["phone"] is None and c["attended"] is None, "empty phone and empty attendance stay empty")
    p1, p2 = got["payments"]
    check(p1["amount"] == 2788 and p1["person_record"] == "recA1" and p1["method"] == "Bank Transfer" and p1["participants"] == 4
          and p1["currency"] == "SGD", "payment mapped, table currency wins over facts.currency")
    check(p2["person_record"] is None and p2["phone"] == "+65 9000 0001" and p2["date"] is not None, "payment without link keeps the phone fallback")
    check(any("shoe_size" in w for w in got["warnings"]) and got["config"]["people_tables"][0]["table"] == "tblFAKEroster",
          "unknown field warned, locators echoed")

    def failing(args, timeout):
        return 0, json.dumps({"ok": False, "error": {"message": "user token expired"}}), ""
    bad = fetch(cfg, runner=failing)
    check(len(bad["errors"]) == 2 and "token expired" in bad["errors"][0], "ok=false from lark-cli is an error per table")
    check(fetch({"facts": {"lark": {}}}, runner=runner)["errors"] == ["facts.lark has no people_tables and no payment_tables"],
          "no tables configured is an error")
    check("needs base and table" in fetch({"facts": {"lark": {"payment_tables": [{"label": "x"}]}}}, runner=runner)["errors"][0],
          "a table without base or table id is an error")
    check(health(cfg, runner=runner) == (True, "user ready"), "health reads auth status")
    check(health(cfg, runner=lambda a, t: (1, "", "not logged in"))[0] is False, "health without JSON is not ok")
    return check.done()


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(selftest())
    print("usage: facts_lark.py --selftest   (the adapter runs through wa_facts.py)")
    sys.exit(0)
