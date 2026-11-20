"""Compare what the applicant claimed (input sheet) with what the agency shows."""

import re
from datetime import date, datetime

from .schema import (COMPARE_FIELDS, MATCH, MISMATCH, NOT_ON_SOURCE, NOT_PROVIDED, STATUS_ALERT,
                     STATUS_CLEAR, STATUS_DISCREPANCY)

_DATE_FORMATS = ("%m/%d/%Y", "%m-%d-%Y", "%Y-%m-%d", "%m/%d/%y", "%d-%b-%Y", "%d %b %Y", "%b %d, %Y",
                 "%B %d, %Y", "%d %B %Y", "%Y/%m/%d", "%m.%d.%Y")

_TYPE_SYNONYMS = {
    "rn": ("rn", "registered nurse", "registered nurse rn", "registered professional nurse"),
    "lvn/lpn": ("lvn", "lpn", "lvn/lpn", "lpn/lvn", "licensed vocational nurse", "licensed practical nurse",
                "vocational nurse", "practical nurse"),
    "aprn": ("aprn", "advanced practice registered nurse", "advanced practice nurse"),
}
_STATE_NAMES = {"texas": "TX", "california": "CA", "florida": "FL", "new york": "NY", "arizona": "AZ"}


def parse_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    text = re.sub(r"\s+\d{1,2}:\d{2}.*$", "", text)  # drop a trailing time
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def fmt_date(value):
    """m/d/yyyy when the value is a recognisable date, otherwise the text as-is."""
    d = parse_date(value)
    return f"{d.month}/{d.day}/{d.year}" if d else str(value or "").strip()


def _alnum(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _tokens(value):
    return set(re.findall(r"[a-z]+", str(value or "").lower()))


def canon_type(value):
    text = re.sub(r"[^a-z/ ]", " ", str(value or "").lower())
    text = re.sub(r"\b(license|licence|permit|certificate)\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for canon, names in _TYPE_SYNONYMS.items():
        if text in names:
            return canon
    return text


def canon_discipline(value):
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if text in ("n", "no", "none", "na", "n/a", "false", "clear") or re.match(r"^(no|none)\b", text):
        return "no"
    return "yes"


def canon_multistate(value):
    text = re.sub(r"[^a-z ]", " ", str(value or "").lower())
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    if "single" in text or text in ("no", "n", "none", "non compact", "not compact"):
        return "single state"
    if "multi" in text or "compact" in text or text in ("yes", "y"):
        return "multistate"
    return text


def canon_state(value):
    text = str(value or "").strip()
    return _STATE_NAMES.get(text.lower(), text.upper())


def _same(field, claimed, verified):
    """Return (is_match, note)."""
    if field == "full_name":
        want, got = _tokens(claimed), _tokens(verified)

        def present(tok):  # a middle initial on either side matches the full middle name
            return tok in got or any(len(g) == 1 and tok.startswith(g) or len(tok) == 1 and g.startswith(tok)
                                     for g in got)
        if want and all(present(t) for t in want):
            extra = {g for g in got if not any(g == t or len(g) == 1 and t.startswith(g) or
                                               len(t) == 1 and g.startswith(t) for t in want)}
            return True, ("Source shows additional name parts: " + " ".join(sorted(extra)).upper()) if extra else ""
        return False, ""
    if field in ("expiration_date", "original_issue_date", "dob"):
        a, b = parse_date(claimed), parse_date(verified)
        if a and b:
            return a == b, ""
        return _alnum(claimed) == _alnum(verified), "Date could not be parsed; compared as text"
    if field == "license_number":
        return _alnum(claimed).lstrip("0") == _alnum(verified).lstrip("0"), ""
    if field == "license_type":
        a, b = canon_type(claimed), canon_type(verified)
        return bool(a and b) and (a == b or a in b or b in a), ""
    if field == "disciplinary_actions":
        return canon_discipline(claimed) == canon_discipline(verified), ""
    if field == "multi_state_privilege":
        return canon_multistate(claimed) == canon_multistate(verified), ""
    if field == "issuing_state":
        return canon_state(claimed) == canon_state(verified), ""
    if field == "governing_agency":
        a, b = _tokens(claimed) - {"of", "the", "state"}, _tokens(verified) - {"of", "the", "state"}
        return bool(a and b) and (a <= b or b <= a), ""
    return _alnum(claimed) == _alnum(verified), ""


def _display(field, value):
    return fmt_date(value) if field in ("expiration_date", "original_issue_date", "dob") else str(value or "").strip()


def compare(claimed, verified):
    """Field-by-field comparison. Both arguments are dicts keyed by schema key."""
    rows = []
    for field, label in COMPARE_FIELDS:
        a, b = _display(field, claimed.get(field)), _display(field, verified.get(field))
        note = ""
        if not b:
            result = NOT_ON_SOURCE
        elif not a:
            result = NOT_PROVIDED
        else:
            ok, note = _same(field, a, b)
            result = MATCH if ok else MISMATCH
        rows.append({"field": field, "label": label, "input": a, "verified": b, "result": result, "note": note})
    return rows


def alerts_for(verified, today=None):
    """Facts on the agency record a reviewer must see even when everything matches."""
    today = today or date.today()
    alerts = []
    exp = parse_date(verified.get("expiration_date"))
    if exp and exp < today:
        alerts.append(f"License expired on {fmt_date(exp)}.")
    status = str(verified.get("license_status") or "").strip()
    low = status.lower()
    if status and (not re.search(r"\b(active|current)\b", low) or re.search(r"\bnot (active|current)\b", low)):
        alerts.append(f"License status on source is '{status}'.")
    if canon_discipline(verified.get("disciplinary_actions")) == "yes":
        alerts.append(f"Disciplinary action reported by source: {verified.get('disciplinary_actions')}.")
    return alerts


def conclude(rows, alerts, source, verified_on):
    """Overall search status plus a closing comment for the record."""
    mismatches = [r for r in rows if r["result"] == MISMATCH]
    missing = [r["label"] for r in rows if r["result"] == NOT_ON_SOURCE and r["input"]]
    if mismatches:
        status = STATUS_DISCREPANCY
    elif alerts:
        status = STATUS_ALERT
    else:
        status = STATUS_CLEAR

    parts = [f"Verified through {source} on {verified_on}."]
    if mismatches:
        parts.append("Discrepancies: " + "; ".join(
            f"{r['label']} - provided '{r['input']}', source shows '{r['verified']}'" for r in mismatches) + ".")
    else:
        parts.append("All details provided match the source.")
    if missing:
        parts.append("Not shown by source: " + ", ".join(missing) + ".")
    parts.extend(alerts)
    discrepancies = "; ".join(f"{r['label']}: {r['input']} vs {r['verified']}" for r in mismatches)
    return status, discrepancies, " ".join(parts)
