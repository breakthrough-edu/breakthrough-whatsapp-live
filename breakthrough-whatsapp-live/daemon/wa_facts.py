#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wa_facts.py · the facts worker: who paid and who attended, joined to WhatsApp chats and person notes.

Interface 1 of 5 (DESIGN.md §5.1). build_facts(cfg) asks the adapter named by `facts.source` for records, then does
everything that does not depend on the source: group records into people by phone, attach payments (by record link,
else by phone), resolve partner purchases (paid_via), find each person's WhatsApp chat and person note, compute paid,
attended and the mismatch lists, and render the lines for the brain and the section for person notes.
It writes nothing; --write (or the daemon's facts job) writes <live>/facts.json.

Sources (ADAPTERS): none · csv (adapters/facts_csv.py) · lark (adapters/facts_lark.py). Each adapter exposes
  fetch(cfg) -> {"people": [person record], "payments": [payment record], "errors": [str], "warnings": [str],
                 "sources": {table label: rows}, "config": {locators, never credentials}}
  health(cfg) -> (ok, why)      optional, for the digest health line
`errors` means a table or file could not be read: build_facts raises FactsError and the previous facts.json stays in
place (an expired login never wipes yesterday's facts). `warnings` are row level and are kept in facts.json.

Person record (one row about a person in any table):
  table · kind (enrollment | lead | contact) · record_id · name · phone (raw) · date (YYYY-MM-DD) · product · role ·
  via_record (record_id of the person whose purchase covers this one) · attended (True | False | None) ·
  attendance ([[label, True | False | None]], display only) · tag · flags ([[label, tri]]) · external_id ·
  show (False marks an identity only row that is not rendered)
Payment record:
  table · record_id · date · amount · currency · method · product · participants · phone (raw, fallback) · name ·
  person_record (record_id of a person record) · external_id · remark

facts.json:
  written_at (local time with offset) · source · source_label · lang · currency · floor · section_title · sources ·
  config · warnings · people_index_error · stats {people, paid, payers, paid_via_only, has_chat, has_note,
  payments_assigned, payments_unassigned}
  people {key: person}, key = the one to one chat jid when there is a chat, else "phone:<digits>":
    name · names · phone · phones · phone_kind · wa_jid · wa_lid · has_chat · how · people_note (file name or None) ·
    paid · paid_via · paid_items [payment] · attended (True | False | None) · records [person record] · lines (at most 3)
  aliases {every jid spelling and "phone:<digits>": key}
  mismatch {paid_no_chat, unparsable_phone, paid_chat_no_note_queued, paid_chat_stale_no_note}, always all four

Matching: every phone with at least 8 digits is looked up in WhatsApp (both jid spellings) and in person notes;
facts.phone_valid only decides whether a number that matched nothing is listed as unparsable. When the person notes
folder cannot be read, nobody is queued for a note (people_index_error says why) instead of queueing every payer.

stage_floor(facts, jid, cfg=None) is the one floor implementation; the brain run, the nightly lift of board rows and
dossier_core all call it. Own payment and attended gives floor.paid_attended (default active); own payment gives
floor.paid (default won); a partner purchase plus attended gives floor.via_attended (default active); else False.
The floor map, language and labels are stored in facts.json at build time, so every caller renders and floors the
same way with or without a config.

CLI
  wa_facts.py --dry-run [--quiet]      read the real source now and print the summary; writes nothing
  wa_facts.py --write                  read the source and write <live>/facts.json atomically
  wa_facts.py --for-jid JID            lines and section for one chat from facts.json (reads the source when missing)
  wa_facts.py --selftest               fake records, fake WhatsApp databases, temp folders; no network, no real data
  --config P                           live/config.json (default: env WA_LIVE_CONFIG or ~/WhatsApp-Live/live/config.json)
Only --selftest is offline; --dry-run, --write and --for-jid (without facts.json) read the configured source.
"""

import argparse
import copy
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wa_common as W  # noqa: E402
import adapters as A  # noqa: E402

ADAPTERS = {"csv": "adapters.facts_csv", "lark": "adapters.facts_lark"}
SOURCES = ("none",) + tuple(sorted(ADAPTERS))
FACTS_BEGIN = "<!-- facts:begin -->"
FACTS_END = "<!-- facts:end -->"
MISMATCH_KEYS = ("paid_no_chat", "unparsable_phone", "paid_chat_no_note_queued", "paid_chat_stale_no_note")
STAT_KEYS = ("people", "paid", "payers", "paid_via_only", "has_chat", "has_note", "payments_assigned", "payments_unassigned")

T = {
    "zh": {"paid": "付款", "people_n": "%s 人", "attended": "出席", "via": "经 %s 的单", "unset": "未填",
           "no_date": "日期不明", "source": "来源", "pulled": "事实 worker %s 拉", "title": "## 系统记录 (%s, 自动)",
           "title_plain": "## 系统记录 (自动)", "why_empty": "空", "why_digits": "%d 位", "why_nolink": "没链到记录也没电话"},
    "en": {"paid": "Paid", "people_n": "%s people", "attended": "attended", "via": "via %s", "unset": "not set",
           "no_date": "date unknown", "source": "Source", "pulled": "facts worker pulled %s",
           "title": "## System records (%s, automatic)", "title_plain": "## System records (automatic)",
           "why_empty": "empty", "why_digits": "%d digits", "why_nolink": "no linked record and no phone"},
}


class FactsError(RuntimeError):
    pass


def _L(lang):
    return "en" if str(lang or "").lower().startswith("en") else "zh"


def source_label(cfg):
    """Label of the configured facts source ("CSV", "Lark", or facts.label); "" for none."""
    return W.source_label(cfg, "facts")


def section_title(cfg=None, lang=None, label=None):
    L = _L(lang or (W.owner_lang(cfg) if cfg else "zh"))
    lab = label if label is not None else (source_label(cfg) if cfg else "")
    return T[L]["title"] % lab if lab else T[L]["title_plain"]


def load_adapter(source, cfg=None):
    """The implementation behind facts.source: the owner's own <live>/adapters/facts_<source>.py first (it survives kit
    updates), else a shipped adapter. FactsError for a source nobody implements."""
    key = str(source or "").strip().lower()
    if cfg is not None and key and key != "none":
        own = A.owner_module(cfg, "facts_" + key)
        if own is not None:
            return own
    mod = ADAPTERS.get(key)
    if not mod:
        raise FactsError("unknown facts.source %r (use %s, or put your own adapters/facts_%s.py in the live folder)" % (source, ", ".join(SOURCES), key or "<name>"))
    return A.load(mod)


def floor_map(cfg):
    """facts.floor with every stage checked against the vocabulary; a stage the owner does not have means no floor."""
    order = W.vocab(cfg)["stage_order"]
    raw = ((cfg or {}).get("facts") or {}).get("floor")
    raw = raw if isinstance(raw, dict) else W.DEFAULT_FLOOR
    out = {}
    for k in ("paid", "paid_attended", "via_attended"):
        v = str(raw.get(k) or "").strip()
        out[k] = v if v in order else ""
    return out


def source_health(cfg):
    """(ok, why) from the adapter's health(), or None when facts are off or the adapter has no health check."""
    fc = (cfg or {}).get("facts") or {}
    src = str(fc.get("source") or "none").strip().lower()
    if src == "none" or not fc.get("enabled"):
        return None
    try:
        fn = getattr(load_adapter(src, cfg), "health", None)
    except Exception as e:
        return False, str(e)[:120]
    if fn is None:
        return None
    try:
        ok, why = fn(cfg)
        return bool(ok), str(why or "")[:120]
    except Exception as e:
        return False, str(e)[:120]


# ---------------------------------------------------------------- record cleaning (whatever the adapter sent)

def _tri(v):
    return v if (v is None or isinstance(v, bool)) else A.clean_tri(v)


def _pairs(v):
    out = []
    for item in (v if isinstance(v, (list, tuple)) else []):
        if isinstance(item, (list, tuple)) and len(item) == 2 and str(item[0]).strip():
            out.append([str(item[0]).strip(), _tri(item[1])])
    return out


def _clean_person(r):
    return {
        "table": A.clean_text(r.get("table")) or "?",
        "kind": A.clean_text(r.get("kind")) or "contact",
        "record_id": A.clean_text(r.get("record_id")),
        "name": A.clean_name(r.get("name")),
        "date": A.clean_date(r.get("date")),
        "product": A.clean_text(r.get("product")),
        "role": A.clean_text(r.get("role")),
        "via_record": A.clean_text(r.get("via_record")),
        "via": None,
        "attended": _tri(r.get("attended")),
        "attendance": _pairs(r.get("attendance")),
        "tag": A.clean_text(r.get("tag")),
        "flags": _pairs(r.get("flags")),
        "external_id": A.clean_text(r.get("external_id")),
        "show": r.get("show") is not False,
    }


def _clean_payment(x, default_currency):
    return {
        "table": A.clean_text(x.get("table")) or "?",
        "record_id": A.clean_text(x.get("record_id")),
        "date": A.clean_date(x.get("date")),
        "amount": A.clean_amount(x.get("amount")),
        "currency": A.clean_text(x.get("currency")) or (default_currency or None),
        "method": A.clean_text(x.get("method")),
        "product": A.clean_text(x.get("product")),
        "participants": A.clean_int(x.get("participants")),
        "external_id": A.clean_text(x.get("external_id")),
        "remark": A.clean_text(x.get("remark")),
    }


def _why(d, L):
    return T[L]["why_empty"] if not d else T[L]["why_digits"] % len(d)


# ---------------------------------------------------------------- build

def _empty(cfg, now, source, label):
    fc = (cfg or {}).get("facts") or {}
    lang = W.owner_lang(cfg)
    return {"written_at": now.isoformat(timespec="seconds"), "source": source, "source_label": label, "lang": lang,
            "currency": str(fc.get("currency") or ""), "floor": floor_map(cfg), "section_title": section_title(cfg, lang, label),
            "sources": {}, "config": {}, "warnings": [], "people_index_error": None,
            "people": {}, "aliases": {}, "mismatch": dict((k, []) for k in MISMATCH_KEYS),
            "stats": dict((k, 0) for k in STAT_KEYS)}


def _tri_merge(vals):
    return True if True in vals else (False if False in vals else None)


def build_facts(cfg, fetched=None, now=None):
    """facts.json as a dict (see the module docstring). fetched = an adapter result (the selftest passes one);
    otherwise the adapter for facts.source is called. Raises FactsError when the source could not be read."""
    fc = (cfg or {}).get("facts") or {}
    source = str(fc.get("source") or "none").strip().lower()
    label = source_label(cfg)
    now = now or dt.datetime.now().astimezone()
    out = _empty(cfg, now, source, label)
    L = out["lang"]
    if fetched is None:
        if source == "none":
            return out
        fetched = load_adapter(source, cfg).fetch(cfg)
    errors = [str(e) for e in (fetched.get("errors") or [])]
    if errors:
        raise FactsError("facts source %s could not be read: %s" % (label or source, "; ".join(errors)[:600]))
    out["sources"] = dict(fetched.get("sources") or {})
    out["config"] = copy.deepcopy(fetched.get("config") or {})
    out["warnings"] = [str(w) for w in (fetched.get("warnings") or [])][:50]
    recent_days = int(fc.get("recent_days") or 30)
    mismatch = out["mismatch"]

    P = {}
    rec_digits, rec_name = {}, {}

    def person(d):
        if d not in P:
            P[d] = {"names": [], "records": [], "paid_items": [], "paid_via": None, "raw": []}
        return P[d]

    def add_name(p, nm):
        if nm and nm not in p["names"]:
            p["names"].append(nm)

    # 1 · person records, grouped by phone digits
    for r in fetched.get("people") or []:
        if not isinstance(r, dict):
            continue
        rec = _clean_person(r)
        rid = rec["record_id"]
        raw = A.clean_text(r.get("phone"))
        d = W.norm_phone(raw, cfg)
        if rid:
            rec_name[rid] = rec["name"]
            rec_digits[rid] = d
        if not d:
            mismatch["unparsable_phone"].append({"table": rec["table"], "name": rec["name"], "raw": raw,
                                                 "why": _why(d, L), "record_id": rid})
            continue
        p = person(d)
        add_name(p, rec["name"])
        p["records"].append(rec)
        p["raw"].append({"table": rec["table"], "name": rec["name"], "raw": raw, "record_id": rid})

    # 2 · payments: the linked record's person first, the payment's own phone second
    unassigned = 0
    for x in fetched.get("payments") or []:
        if not isinstance(x, dict):
            continue
        pay = _clean_payment(x, out["currency"])
        pr = A.clean_text(x.get("person_record"))
        raw = A.clean_text(x.get("phone"))
        nm = A.clean_name(x.get("name")) or (rec_name.get(pr) if pr else None)
        target, how = None, None
        if pr and rec_digits.get(pr):
            target, how = rec_digits[pr], "link"
        if target is None:
            d = W.norm_phone(raw, cfg)
            if d:
                target, how = d, "phone"
        if target is None:
            unassigned += 1
            mismatch["unparsable_phone"].append({"table": pay["table"], "name": nm, "raw": raw, "why": T[L]["why_nolink"],
                                                 "record_id": pay["record_id"], "amount": pay["amount"], "date": pay["date"]})
            continue
        pay["assigned_by"] = how
        p = person(target)
        add_name(p, nm)
        p["paid_items"].append(pay)
        if how == "phone":
            p["raw"].append({"table": pay["table"], "name": nm, "raw": raw, "record_id": pay["record_id"]})

    # 3 · partner purchases: a record covered by someone who paid gets paid_via
    for d, p in P.items():
        for rec in p["records"]:
            via = rec.get("via_record")
            if not via:
                continue
            rec["via"] = rec_name.get(via)
            md = rec_digits.get(via)
            payer = P.get(md) if md else None
            if payer is not None and payer is not p and payer["paid_items"] and not p["paid_via"]:
                p["paid_via"] = rec_name.get(via) or ("phone:" + md)

    # 4 · WhatsApp chat, person note, key, aliases
    W.invalidate_wa_cache()
    idx = W.people_index(cfg)
    out["people_index_error"] = idx.get("error")
    people_out, aliases = {}, {}
    for d, p in P.items():
        p["paid_items"].sort(key=lambda z: z.get("date") or "")
        p["records"].sort(key=lambda z: z.get("date") or "")
        kind = W.phone_kind(d, cfg)
        r = {"jid": None, "lid": None, "has_chat": False, "how": "unknown"}
        note = None
        if len(d) >= W.MIN_PHONE_DIGITS:
            try:
                r = W.phone_to_jid(d)
            except Exception:
                r = {"jid": None, "lid": None, "has_chat": False, "how": "error"}
            if not idx.get("error"):
                try:
                    note = W.people_note_for(d, idx, cfg=cfg)
                except Exception:
                    note = None
        entry = {
            "name": p["names"][0] if p["names"] else None, "names": list(p["names"]),
            "phone": d, "phones": [d], "phone_kind": kind,
            "wa_jid": r.get("jid"), "wa_lid": r.get("lid"), "has_chat": bool(r.get("has_chat")), "how": r.get("how") or "unknown",
            "people_note": note.name if note is not None else None,
            "paid": bool(p["paid_items"]) or bool(p["paid_via"]), "paid_via": p["paid_via"],
            "paid_items": p["paid_items"], "attended": _tri_merge([x.get("attended") for x in p["records"]]),
            "records": p["records"], "lines": [],
        }
        if kind is None and not entry["has_chat"] and not entry["wa_lid"]:
            for raw in p["raw"]:
                mismatch["unparsable_phone"].append(dict(raw, why=_why(d, L)))
        key = entry["wa_jid"] if (entry["wa_jid"] and entry["has_chat"]) else "phone:" + d
        if key in people_out:
            q = people_out[key]
            q["paid_items"] = sorted(q["paid_items"] + entry["paid_items"], key=lambda z: z.get("date") or "")
            q["records"] = sorted(q["records"] + entry["records"], key=lambda z: z.get("date") or "")
            for n in entry["names"]:
                if n not in q["names"]:
                    q["names"].append(n)
            q["name"] = q["name"] or entry["name"]
            q["paid"] = q["paid"] or entry["paid"]
            q["paid_via"] = q["paid_via"] or entry["paid_via"]
            q["people_note"] = q["people_note"] or entry["people_note"]
            q["attended"] = _tri_merge([q["attended"], entry["attended"]])
            q["phones"].append(d)
            q.setdefault("other_phones", []).append(d)
        else:
            people_out[key] = entry
        for alt in (d + "@s.whatsapp.net", (entry["wa_lid"] + "@lid") if entry["wa_lid"] else None, entry["wa_jid"], "phone:" + d):
            if alt:
                aliases.setdefault(alt, key)

    # 5 · mismatch lists and lines
    for key, p in people_out.items():
        nm = p["name"] or key
        if p["paid_items"] and not p["has_chat"]:
            last = p["paid_items"][-1]
            mismatch["paid_no_chat"].append({"name": nm, "phone": p["phone"], "amount": last.get("amount"),
                                             "currency": last.get("currency"), "date": last.get("date"),
                                             "payments_n": len(p["paid_items"]), "how": p["how"]})
        if p["paid_items"] and p["has_chat"] and not p["people_note"] and not out["people_index_error"]:
            row = {"name": nm, "wa_jid": p["wa_jid"], "phone": p["phone"], "last_payment": p["paid_items"][-1].get("date")}
            try:
                recent = int(W.chat_message_count(p["wa_jid"], recent_days, content_only=True) or 0)
            except Exception:
                recent = 0
            if recent > 0:
                mismatch["paid_chat_no_note_queued"].append(row)
            else:
                row["recent_messages"] = 0
                row["recent_days"] = recent_days
                mismatch["paid_chat_stale_no_note"].append(row)
        p["lines"] = person_lines(p, L)
    for k in MISMATCH_KEYS:
        mismatch[k].sort(key=lambda z: (str(z.get("date") or z.get("last_payment") or ""), str(z.get("name") or "")))

    out["people"] = people_out
    out["aliases"] = aliases
    vals = list(people_out.values())
    out["stats"] = {
        "people": len(vals),
        "paid": sum(1 for p in vals if p["paid"]),
        "payers": sum(1 for p in vals if p["paid_items"]),
        "paid_via_only": sum(1 for p in vals if p["paid_via"] and not p["paid_items"]),
        "has_chat": sum(1 for p in vals if p["has_chat"]),
        "has_note": sum(1 for p in vals if p["people_note"]),
        "payments_assigned": sum(len(p["paid_items"]) for p in vals),
        "payments_unassigned": unassigned,
    }
    return out


# ---------------------------------------------------------------- read and ask

def load_facts(cfg_or_path):
    """facts.json as a dict, or None. Takes a cfg (reads <live>/facts.json) or a path."""
    if isinstance(cfg_or_path, dict):
        path = W.live_dir(cfg_or_path) / "facts.json"
    else:
        path = Path(os.path.expanduser(str(cfg_or_path)))
    got = W.read_json(path, None)
    return got if isinstance(got, dict) else None


def person_for(facts, jid):
    """The person behind a one to one jid (either spelling), a "phone:<digits>" key or bare digits; groups None."""
    if not facts or not jid:
        return None
    j = str(jid).strip()
    if j.endswith("@g.us") or j.endswith("@broadcast"):
        return None
    people = facts.get("people") or {}
    aliases = facts.get("aliases") or {}
    cands = [j]
    if j.isdigit():
        cands += ["phone:" + j, j + "@s.whatsapp.net"]
    for c in cands:
        if c in people:
            return people[c]
        k = aliases.get(c)
        if k and k in people:
            return people[k]
    return None


def _items(p):
    return p.get("paid_items") if p.get("paid_items") is not None else (p.get("payments") or [])


def _attended_flag(p):
    if p.get("attended") is True:
        return True
    return any(isinstance(r, dict) and (r.get("day1") or r.get("day2")) for r in (p.get("roster") or []))


def is_paid(facts, jid):
    p = person_for(facts, jid)
    return bool(p and p.get("paid"))


def stage_floor(facts, jid, cfg=None):
    """The one payment floor: a stage key or False (see the module docstring). Uses the floor stored in facts.json;
    cfg only matters for an old file without one."""
    p = person_for(facts, jid)
    if not p:
        return False
    fl = facts.get("floor") if isinstance(facts.get("floor"), dict) else (floor_map(cfg) if cfg else dict(W.DEFAULT_FLOOR))
    attended = _attended_flag(p)
    if _items(p):
        if attended and fl.get("paid_attended"):
            return fl["paid_attended"]
        return fl.get("paid") or False
    if p.get("paid_via"):
        return (fl.get("via_attended") or False) if attended else False
    return False


# ---------------------------------------------------------------- lines and the person note section

def _mark(v, L):
    return "✓" if v is True else ("✗" if v is False else T[L]["unset"])


def _money(amount, currency):
    if amount is None:
        s = "?"
    elif isinstance(amount, int) or (isinstance(amount, float) and amount.is_integer()):
        s = "{:,}".format(int(amount))
    else:
        s = "{:,.2f}".format(amount)
    return ("%s %s" % (currency, s)) if currency else s


def _pay_text(x, L, with_date=True):
    bits = []
    if x.get("participants") is not None:
        bits.append(T[L]["people_n"] % x["participants"])
    if x.get("product"):
        bits.append(str(x["product"]))
    core = " ".join(b for b in (x.get("method"), _money(x.get("amount"), x.get("currency"))) if b)
    if bits:
        core += " (%s)" % ", ".join(bits)
    return ("%s %s" % (x.get("date") or "?", core)) if with_date else core


def _record_text(x, L, with_date=True):
    head = []
    if with_date and x.get("date"):
        head.append(x["date"])
    for k in ("product", "role"):
        if x.get(k):
            head.append(str(x[k]))
    if x.get("via"):
        head.append("(%s)" % (T[L]["via"] % x["via"]))
    if x.get("tag"):
        head.append("(%s)" % x["tag"])
    s = " ".join(head)
    marks = []
    if x.get("attendance"):
        for lab, v in x["attendance"]:
            if v is not None:
                marks.append("%s %s" % (lab, _mark(v, L)))
    elif x.get("attended") is not None:
        marks.append("%s %s" % (T[L]["attended"], _mark(x["attended"], L)))
    for lab, v in x.get("flags") or []:
        marks.append("%s %s" % (lab, _mark(v, L)))
    if marks:
        s = (s + ", " if s else "") + " ".join(marks)
    return s or "?"


def person_lines(p, lang="zh"):
    """At most three lines: all payments on one line, then one line per record table (first seen order)."""
    L = _L(lang)
    lines = []
    items = _items(p)
    if items:
        lines.append("%s: %s" % (T[L]["paid"], " · ".join(_pay_text(x, L) for x in items)))
    groups = []
    for rec in p.get("records") or []:
        if not isinstance(rec, dict) or rec.get("show") is False:
            continue
        tb = rec.get("table") or "?"
        for g in groups:
            if g[0] == tb:
                g[1].append(rec)
                break
        else:
            groups.append((tb, [rec]))
    for tb, recs in groups:
        lines.append("%s: %s" % (tb, " · ".join(_record_text(r, L) for r in recs)))
    return lines[:3]


def facts_lines(facts, jid):
    """The brain's system record lines for this chat (at most 3); [] for groups and unknown people."""
    p = person_for(facts, jid)
    if not p:
        return []
    if isinstance(p.get("lines"), list) and (p["lines"] or not (_items(p) or p.get("records"))):
        return [str(x) for x in p["lines"]][:3]
    return person_lines(p, facts.get("lang") or "zh")


def facts_section(facts, jid, cfg=None):
    """The person note's system records section: title, the facts:begin and facts:end markers, one 👁 line per
    payment or record in date order, and the source line. "" when there is nothing (the section is then not written).
    Rendered only from facts.json, so the daemon and dossier.py produce the same text."""
    p = person_for(facts, jid)
    if not p:
        return ""
    L = _L(facts.get("lang") or (W.owner_lang(cfg) if cfg else "zh"))
    items, tables = [], []
    for x in _items(p):
        items.append((x.get("date") or "", "%s: %s" % (T[L]["paid"], _pay_text(x, L, with_date=False))))
        if x.get("table") and x["table"] not in tables:
            tables.append(x["table"])
    for r in p.get("records") or []:
        if not isinstance(r, dict) or r.get("show") is False:
            continue
        items.append((r.get("date") or "", "%s: %s" % (r.get("table") or "?", _record_text(r, L, with_date=False))))
        if r.get("table") and r["table"] not in tables:
            tables.append(r["table"])
    if not items:
        return ""
    items.sort(key=lambda t: t[0])
    body = ["- 👁 %s · %s" % (d or T[L]["no_date"], t) for d, t in items]
    label = facts.get("source_label") if facts.get("source_label") is not None else (source_label(cfg) if cfg else "")
    src = (label or "?") + ((" (%s)" % " · ".join(tables)) if tables else "")
    body.append("- 👁 %s: %s, %s" % (T[L]["source"], src, T[L]["pulled"] % str(facts.get("written_at") or "")[:10]))
    title = facts.get("section_title") or section_title(cfg, L, label)
    return "\n".join([title, FACTS_BEGIN] + body + [FACTS_END]) + "\n"


def written_date(facts):
    """facts.json written_at as a date, or None."""
    try:
        return dt.date.fromisoformat(str((facts or {}).get("written_at") or "")[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------- CLI

def _print_summary(facts, verbose=True):
    s = facts["stats"]
    srcs = " · ".join("%s %s" % (k, v) for k, v in (facts.get("sources") or {}).items())
    print("source %s (%s): %s" % (facts.get("source"), facts.get("source_label") or "-", srcs or "no tables"))
    print("people=%d · paid=%d (own payment %d, partner purchase only %d) · with chat=%d · with note=%d · "
          "payments assigned %d (unassigned %d)" % (s["people"], s["paid"], s["payers"], s["paid_via_only"], s["has_chat"],
                                                    s["has_note"], s["payments_assigned"], s["payments_unassigned"]))
    if facts.get("people_index_error"):
        print("person notes unreadable, nobody queued for a note: %s" % facts["people_index_error"])
    for k in MISMATCH_KEYS:
        rows = facts["mismatch"].get(k) or []
        print("mismatch.%s: %d" % (k, len(rows)))
        if verbose:
            for x in rows:
                print("  " + json.dumps(x, ensure_ascii=False))
    for w in (facts.get("warnings") or [])[:10]:
        print("warning: %s" % w)


def _selftest():
    check = W._Checks("wa_facts")
    import adapters.facts_csv as FC
    import adapters.facts_lark as FL
    check(FC.selftest() == 0, "adapter facts_csv selftest passes")
    check(FL.selftest() == 0, "adapter facts_lark selftest passes")
    tmp = Path(tempfile.mkdtemp(prefix="wa_facts_selftest."))
    saved = os.environ.get(W.WA_ROOT_ENV)
    try:
        people_dir = tmp / "People"
        people_dir.mkdir()
        (people_dir / "Blake-Chen.md").write_text("---\ntype: person\nwa_jid: \"100000000000002@lid\"\n---\n", encoding="utf-8")
        root = W.fake_wa_root(
            tmp / "wa",
            lid_pairs=[("100000000000001", "60120000001"), ("100000000000002", "60120000002")],
            contacts=[("100000000000005", "+65 9000 0001")],
            chats=["100000000000001@lid", "60120000002@s.whatsapp.net", "60120000008@s.whatsapp.net", "447700900001@s.whatsapp.net"],
            messages=[("100000000000001@lid", 2, 0), ("60120000008@s.whatsapp.net", 1, 6), ("60120000008@s.whatsapp.net", 45, 0),
                      ("447700900001@s.whatsapp.net", 3, 0)])
        os.environ[W.WA_ROOT_ENV] = str(root)
        live = tmp / "home" / "live"
        live.mkdir(parents=True)
        cfg = W.apply_defaults({"facts": {"enabled": True, "source": "csv", "label": "Sales sheet", "currency": "MYR",
                                          "phone_default_cc": "60", "phone_valid": ["60:11-12", "65:10"]},
                                "paths": {"people_dir": str(people_dir)}}, live / "config.json")
        people = [
            {"table": "Roster", "kind": "enrollment", "record_id": "r-a1", "name": "Alex Rivera ", "phone": "+60 12-000 0001",
             "date": "2026-07-18", "product": "Workshop August", "role": "Member", "attended": True,
             "attendance": [["Day 1", True], ["Day 2", True]]},
            {"table": "Roster", "kind": "enrollment", "record_id": "r-a2", "name": "Alex Rivera", "phone": "012-000 0001",
             "date": "2026-09-02", "product": "Workshop September", "role": "Member", "attendance": [["Day 1", None], ["Day 2", None]]},
            {"table": "Roster", "kind": "enrollment", "record_id": "r-b", "name": "Blake Chen", "phone": "0120000002",
             "date": "2026-07-19", "product": "Workshop August", "role": "Guest", "via_record": "r-a1", "attended": True,
             "attendance": [["Day 1", True], ["Day 2", False]]},
            {"table": "Roster", "record_id": "r-c", "name": "Casey Ng", "phone": "+6010000000001", "date": "2026-07-21"},
            {"table": "Roster", "record_id": "r-d", "name": "Dana Park", "phone": None, "date": "2026-08-21", "via_record": "r-a1"},
            {"table": "Roster", "record_id": "r-e", "name": "Evan Tan", "phone": "+65 9000 0001", "date": "2026-08-23"},
            {"table": "Roster", "record_id": "r-f", "name": "Farah Lee", "phone": "017-000 0006", "date": "2026-09-11"},
            {"table": "Signups", "kind": "lead", "record_id": "l-1", "name": "Alex Rivera", "phone": "012-000 0001", "date": "2026-09-03",
             "tag": "referral", "flags": [["joined group", True], ["came", True], ["bought", False]]},
            {"table": "Signups", "kind": "lead", "record_id": "l-2", "name": "Alex Rivera", "phone": "+60120000001", "date": "2026-08-03",
             "tag": "first contact", "flags": [["joined group", None], ["came", False], ["bought", True]]},
            {"table": "Signups", "kind": "lead", "record_id": "l-3", "name": "Gina Ho", "phone": "12345678", "date": "2026-09-05"},
            {"table": "Roster", "record_id": "r-h", "name": "Hana Yu", "phone": "+60 12-000 0008", "date": "2026-06-01", "show": False},
            {"table": "Signups", "kind": "lead", "record_id": "l-4", "name": "Ivan Koh", "phone": "+44 7700 900001", "date": "2026-09-06"},
        ]
        payments = [
            {"table": "Payments", "record_id": "t1", "date": "2026-07-31", "amount": "2,788", "method": "Bank Transfer", "participants": 4,
             "product": "Workshop August", "person_record": "r-a1", "phone": "+60120000001"},
            {"table": "Payments", "record_id": "t2", "date": "2026-09-01", "amount": "1,988.00", "method": "Card", "participants": 1,
             "product": "Workshop September", "person_record": "r-a2"},
            {"table": "Payments", "record_id": "t3", "date": "2026-09-11", "amount": "3188", "method": "Card", "person_record": "r-f"},
            {"table": "Payments", "record_id": "t4", "date": "2026-09-12", "amount": "800", "method": "Card", "person_record": None,
             "phone": "+65 9000 0001", "currency": "SGD"},
            {"table": "Payments", "record_id": "t5", "date": "2026-09-13", "amount": "99", "method": "Card", "person_record": "missing"},
            {"table": "Payments", "record_id": "t6", "date": "2026-06-02", "amount": "500", "method": "Card", "person_record": "r-h"},
        ]
        fetched = {"people": people, "payments": payments, "errors": [], "warnings": ["one row was odd"],
                   "sources": {"Roster": 8, "Signups": 4, "Payments": 6}, "config": {"paths": ["x.csv"]}}
        f = build_facts(cfg, fetched=copy.deepcopy(fetched), now=dt.datetime(2026, 9, 15, 3, 0, 12).astimezone())
        P = f["people"]
        check(sorted(P) == sorted(["100000000000001@lid", "60120000002@s.whatsapp.net", "phone:6010000000001", "phone:6590000001",
                                   "phone:60170000006", "phone:12345678", "60120000008@s.whatsapp.net", "447700900001@s.whatsapp.net"]),
              "keys: chat jid when there is a chat (LID or phone spelling), else phone:<digits>")
        a = P["100000000000001@lid"]
        check(a["names"] == ["Alex Rivera"] and a["phone"] == "60120000001" and a["how"] == "lid-chat", "Alex: names merged, LID chat found")
        check([x["date"] for x in a["paid_items"]] == ["2026-07-31", "2026-09-01"] and a["paid_items"][1]["amount"] == 1988
              and a["paid_items"][0]["assigned_by"] == "link", "all payments kept in date order, amounts cleaned, assigned by link")
        check([x["date"] for x in a["records"]] == ["2026-07-18", "2026-08-03", "2026-09-02", "2026-09-03"] and a["attended"] is True,
              "records from two tables in date order; attended")
        b = P["60120000002@s.whatsapp.net"]
        check(b["paid_via"] == "Alex Rivera" and b["paid"] and not b["paid_items"] and b["people_note"] == "Blake-Chen.md",
              "Blake: partner purchase, note found through the LID spelling of the phone chat")
        e = P["phone:6590000001"]
        check(e["paid_items"][0]["assigned_by"] == "phone" and e["wa_lid"] == "100000000000005" and e["how"] == "known-no-chat"
              and e["paid_items"][0]["currency"] == "SGD", "Evan: unlinked payment assigned by its phone, known LID, own currency kept")
        i = P["447700900001@s.whatsapp.net"]
        check(i["phone_kind"] is None and i["has_chat"], "fix: a number outside phone_valid still matches its WhatsApp chat")
        up = dict(((x["table"], x["name"]), x["why"]) for x in f["mismatch"]["unparsable_phone"])
        check(up == {("Roster", "Casey Ng"): "13 位", ("Roster", "Dana Park"): "空", ("Signups", "Gina Ho"): "8 位",
                     ("Payments", None): "没链到记录也没电话"}, "unparsable: 13 digits, empty, 8 digits, payment without link or phone; Ivan not listed")
        check(sorted(x["name"] for x in f["mismatch"]["paid_no_chat"]) == ["Evan Tan", "Farah Lee"], "paid_no_chat: Evan and Farah")
        check([x["name"] for x in f["mismatch"]["paid_chat_no_note_queued"]] == ["Alex Rivera"]
              and f["mismatch"]["paid_chat_no_note_queued"][0]["wa_jid"] == "100000000000001@lid", "queued for a note: Alex only")
        check([x["name"] for x in f["mismatch"]["paid_chat_stale_no_note"]] == ["Hana Yu"], "Hana: paid with a chat but no content in 30 days, not queued")
        check(f["stats"]["payments_assigned"] == 5 and f["stats"]["payments_unassigned"] == 1 and f["stats"]["paid_via_only"] == 1,
              "stats: five assigned, one unassigned, one partner purchase only")
        check(person_for(f, "60120000001@s.whatsapp.net") is a and person_for(f, "60120000001") is a and person_for(f, "120000000000000001@g.us") is None,
              "person_for: other jid spelling, bare digits, groups")

        ls = facts_lines(f, "60120000001@s.whatsapp.net")
        check(ls[0] == "付款: 2026-07-31 Bank Transfer MYR 2,788 (4 人, Workshop August) · 2026-09-01 Card MYR 1,988 (1 人, Workshop September)",
              "line 1: payments")
        check(ls[1] == "Roster: 2026-07-18 Workshop August Member, Day 1 ✓ Day 2 ✓ · 2026-09-02 Workshop September Member", "line 2: roster table")
        check(ls[2] == "Signups: 2026-08-03 (first contact), joined group 未填 came ✗ bought ✓ · 2026-09-03 (referral), joined group ✓ came ✓ bought ✗",
              "line 3: signups with three state flags")
        check(facts_lines(f, "60120000002@s.whatsapp.net") == ["Roster: 2026-07-19 Workshop August Guest (经 Alex Rivera 的单), Day 1 ✓ Day 2 ✗"],
              "partner purchase shown on the record line")
        check(facts_lines(f, "60120000008@s.whatsapp.net") == ["付款: 2026-06-02 Card MYR 500"], "identity only record not rendered")
        check(stage_floor(f, "100000000000001@lid") == "active" and stage_floor(f, "60120000002@s.whatsapp.net") == "active"
              and stage_floor(f, "phone:6590000001") == "won" and stage_floor(f, "447700900001@s.whatsapp.net") is False
              and stage_floor(f, "120000000000000001@g.us") is False, "stage_floor: paid and attended, partner and attended, paid, lead only, group")
        check(stage_floor(f, "60120000002@s.whatsapp.net", cfg) == stage_floor(f, "60120000002@s.whatsapp.net"), "stage_floor same with or without cfg")
        no_won = W.apply_defaults({"vocab": {"stages": ["new", "talking", "active", "done"]}}, None)
        check(floor_map(no_won) == {"paid": "", "paid_attended": "active", "via_attended": "active"}, "floor_map drops stages the vocabulary lacks")

        sec = facts_section(f, "100000000000001@lid")
        body = sec.split(FACTS_BEGIN)[1].split(FACTS_END)[0].strip().splitlines()
        check(sec.startswith("## 系统记录 (Sales sheet, 自动)\n" + FACTS_BEGIN + "\n") and sec.rstrip().endswith(FACTS_END),
              "section: title uses the source label, markers in place")
        check(len(body) == 7 and body[0] == "- 👁 2026-07-18 · Roster: Workshop August Member, Day 1 ✓ Day 2 ✓"
              and body[1].startswith("- 👁 2026-07-31 · 付款: Bank Transfer MYR 2,788")
              and body[-1] == "- 👁 来源: Sales sheet (Payments · Roster · Signups), 事实 worker 2026-09-15 拉", "section: dated items and source line")
        check(chr(0x2014) not in sec and " -- " not in sec and "Lark" not in sec, "section: no em dash, no double hyphen, no hardcoded source name")
        check(facts_section(f, "120000000000000001@g.us") == "" and facts_section(f, "nobody@lid") == "", "section empty for groups and unknown")
        path = W.write_json_atomic(live / "facts.json", f)
        g = load_facts(cfg)
        check(g == json.loads(path.read_text(encoding="utf-8")) and facts_section(g, "100000000000001@lid") == sec
              and stage_floor(g, "phone:6590000001") == "won", "facts.json round trip renders the same section and floor")
        check(sorted(g) == sorted(["written_at", "source", "source_label", "lang", "currency", "floor", "section_title", "sources",
                                   "config", "warnings", "people_index_error", "people", "aliases", "mismatch", "stats"])
              and sorted(g["mismatch"]) == sorted(MISMATCH_KEYS), "facts.json top level keys fixed, all four mismatch lists present")
        check(written_date(g) == dt.date(2026, 9, 15), "written_date")

        en = W.apply_defaults({"board": {"lang": "en"}, "facts": {"source": "csv", "phone_default_cc": "60"}, "paths": {"people_dir": str(people_dir)}}, live / "config.json")
        fe = build_facts(en, fetched=copy.deepcopy(fetched))
        check(facts_lines(fe, "60120000002@s.whatsapp.net") == ["Roster: 2026-07-19 Workshop August Guest (via Alex Rivera), Day 1 ✓ Day 2 ✗"]
              and facts_section(fe, "100000000000001@lid").startswith("## System records (CSV, automatic)"), "English lines and title")

        broken = W.apply_defaults({"facts": {"source": "csv", "phone_default_cc": "60"}, "paths": {"people_dir": str(tmp / "no-people")}}, live / "config.json")
        fb = build_facts(broken, fetched=copy.deepcopy(fetched))
        check(bool(fb["people_index_error"]) and fb["mismatch"]["paid_chat_no_note_queued"] == [] and fb["stats"]["has_note"] == 0,
              "fix: unreadable person notes folder queues nobody and says why")
        try:
            build_facts(cfg, fetched={"people": [], "payments": [], "errors": ["Payments: user token expired"]})
            check(False, "source errors raise")
        except FactsError as ex:
            check("token expired" in str(ex), "source errors raise FactsError (facts.json is not overwritten)")
        none_cfg = W.apply_defaults({}, live / "config.json")
        fn = build_facts(none_cfg)
        check(fn["source"] == "none" and fn["people"] == {} and sorted(fn["mismatch"]) == sorted(MISMATCH_KEYS), "source none gives an empty facts shape")
        try:
            load_adapter("sheets")
            check(False, "unknown source")
        except FactsError:
            check(True, "unknown source refused")

        csv_path = tmp / "sales.csv"
        csv_path.write_text("Phone,Name,Paid,Amount,Date,Plan,Came\n+60 12-000 0001,Alex Rivera,yes,1200,2026-09-01,Coaching,yes\n"
                            "+65 9000 0001,Evan Tan,no,,2026-09-02,Coaching,\n", encoding="utf-8")
        ccfg = W.apply_defaults({"facts": {"enabled": True, "source": "csv", "phone_default_cc": "60",
                                           "csv": {"path": str(csv_path), "columns": {"phone": "Phone", "name": "Name", "paid": "Paid",
                                                                                      "amount": "Amount", "date": "Date", "product": "Plan",
                                                                                      "attended": "Came"}}},
                                 "paths": {"people_dir": str(people_dir)}}, live / "config.json")
        fc = build_facts(ccfg)
        check(stage_floor(fc, "100000000000001@lid") == "active" and facts_lines(fc, "100000000000001@lid")[0] == "付款: 2026-09-01 1,200 (Coaching)"
              and source_health(ccfg)[0] is True, "end to end: local CSV through the adapter to the floor and lines")
    finally:
        if saved is None:
            os.environ.pop(W.WA_ROOT_ENV, None)
        else:
            os.environ[W.WA_ROOT_ENV] = saved
        W.invalidate_wa_cache()
        shutil.rmtree(str(tmp), ignore_errors=True)
    src = Path(__file__).read_text(encoding="utf-8")
    check(chr(0x2014) not in src and chr(0x2013) not in src, "no em dash or en dash in this file")
    return check.done()


def main(argv=None):
    ap = argparse.ArgumentParser(description="facts worker: configured source to live/facts.json")
    ap.add_argument("--config", help="live/config.json (default: env WA_LIVE_CONFIG or ~/WhatsApp-Live/live/config.json)")
    ap.add_argument("--dry-run", action="store_true", help="read the source now, print the summary, write nothing")
    ap.add_argument("--write", action="store_true", help="read the source and write <live>/facts.json")
    ap.add_argument("--for-jid", help="lines and section for one chat (from facts.json; reads the source when it is missing)")
    ap.add_argument("--selftest", action="store_true", help="offline checks with fakes")
    ap.add_argument("--quiet", action="store_true", help="with --dry-run or --write: numbers only")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    try:
        cfg = W.load_config(a.config)
    except (FileNotFoundError, ValueError) as e:
        print(e)
        return 2
    if a.for_jid:
        facts = load_facts(cfg)
        if not facts:
            print("(no facts.json yet, reading the source now; nothing is written)")
            try:
                facts = build_facts(cfg)
            except FactsError as e:
                print("facts: %s" % e)
                return 1
        ls = facts_lines(facts, a.for_jid)
        print("--- facts_lines(%s): %d" % (a.for_jid, len(ls)))
        for line in ls:
            print(line)
        if not ls:
            print("(no system records)")
        print("--- facts_section:")
        print(facts_section(facts, a.for_jid, cfg) or "(empty: the section is not written)")
        print("--- stage_floor: %s" % (stage_floor(facts, a.for_jid, cfg) or "none"))
        return 0
    if not (a.dry_run or a.write):
        ap.print_help()
        return 2
    try:
        facts = build_facts(cfg)
    except FactsError as e:
        print("facts: %s" % e)
        return 1
    _print_summary(facts, verbose=not a.quiet)
    if a.write:
        path = W.write_json_atomic(W.live_dir(cfg) / "facts.json", facts)
        print("wrote %s (%d people)" % (path, len(facts["people"])))
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
