"""Turn an agency license page (HTML) into normalised license fields.

The parser is deliberately layout-agnostic: it harvests every label/value pair
it can find (two-cell table rows, header tables, <dl>, <label> blocks and
"Label: value" text) and then maps labels onto our fields by keyword. Every
harvested pair is also kept verbatim so nothing the agency showed is lost.
"""

import re

from bs4 import BeautifulSoup

# Ordered: the first rule whose keyword appears in the label wins.
_RULES = [
    ("disciplinary_actions", ("disciplin", "board action", "board order", "adverse action"), ()),
    ("multi_state_privilege", ("compact", "multistate", "multi-state", "multi state", "privilege to practice"), ()),
    ("expiration_date", ("expir",), ()),
    ("original_issue_date", ("original issue", "issue date", "date issued", "issued", "original licens",
                             "date of licensure", "original date"), ()),
    ("license_number", ("license number", "license no", "license #", "lic number", "license num", "permit number"), ()),
    ("license_status", ("license status", "status"), ("sub",)),
    ("license_type", ("license type", "licence type", "type of license", "credential type", "license/permit type"), ()),
    ("dob", ("date of birth", "birth"), ()),
    ("first_name", ("first name",), ()),
    ("last_name", ("last name",), ()),
    ("full_name", ("name",), ("first", "last", "middle", "maiden", "other", "school", "program", "employer", "board")),
    ("issuing_state", ("state",), ("status", "statement")),
]
_EXACT = {"type": "license_type", "number": "license_number", "licensee": "full_name",
          "board": "governing_agency", "board name": "governing_agency", "agency": "governing_agency",
          "licensing board": "governing_agency", "governing agency": "governing_agency"}


def _clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def _txt(el):
    return _clean(el.get_text(" ", strip=True))


def field_for_label(label):
    lab = _clean(label).rstrip(":").strip().lower()
    if not lab or len(lab) > 60:
        return None
    if lab in _EXACT:
        return _EXACT[lab]
    for field, include, exclude in _RULES:
        if any(k in lab for k in include) and not any(k in lab for k in exclude):
            return field
    return None


def map_pairs(pairs):
    """First non-empty value per field wins."""
    out = {}
    for label, value in pairs:
        field = field_for_label(label)
        value = _clean(value)
        if field and value and len(value) <= 300 and field not in out:
            out[field] = value
    return out


def _next_value(el):
    """Text that follows a label element: its next sibling, else its parent's.

    When the label has a value element beside it that is simply empty, the
    answer is "" - never the next label's block.
    """
    for node in (el, el.parent):
        if node is None:
            continue
        had_element = False
        for sib in node.next_siblings:
            had_element = had_element or not isinstance(sib, str)
            text = _clean(sib if isinstance(sib, str) else sib.get_text(" ", strip=True))
            if text:
                return text
        if had_element:
            return ""
    return ""


def extract(html):
    """Return (pairs, records): loose label/value pairs and header-table rows."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()

    pairs, records = [], []

    for table in soup.find_all("table"):
        header = None
        for tr in table.find_all("tr"):
            if tr.find_parent("table") is not table:
                continue
            cells = tr.find_all(["th", "td"], recursive=False)
            texts = [_txt(c) for c in cells]
            if not any(texts):
                continue
            if len(cells) >= 2 and all(c.name == "th" for c in cells):
                header = texts
            elif header and len(texts) == len(header):
                records.append(dict(zip(header, texts)))
            elif len(texts) % 2 == 0:
                pairs.extend(zip(texts[0::2], texts[1::2]))

    for dl in soup.find_all("dl"):
        pairs.extend((_txt(dt), _txt(dd)) for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")))

    for el in soup.find_all(["label", "strong", "b", "span", "div", "p", "h4", "h5"]):
        text = _txt(el)
        if not text or len(text) > 60:
            continue
        if el.name == "label" or text.endswith(":"):
            value = _next_value(el)
            if value and value != text:
                pairs.append((text, value))

    lines = [_clean(line) for line in soup.get_text("\n").splitlines()]
    lines = [line for line in lines if line]
    for i, line in enumerate(lines):
        m = re.match(r"^([A-Za-z][A-Za-z /#.'()\-]{1,50}):\s*(\S.*)$", line)
        if m:
            pairs.append((m.group(1), m.group(2)))
        elif line.endswith(":") and len(line) <= 50 and i + 1 < len(lines) and not lines[i + 1].endswith(":"):
            pairs.append((line, lines[i + 1]))

    seen, unique = set(), []
    for label, value in pairs:
        key = (_clean(label).rstrip(":").strip(), _clean(value))
        if key[0] and key[1] and len(key[0]) <= 60 and len(key[1]) <= 300 and key not in seen:
            seen.add(key)
            unique.append(key)
    return unique, records


def _norm_lic(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper()).lstrip("0")


def parse_license_page(html, want_license=None):
    """Parse a license page. Returns {"fields", "raw", "notes"}."""
    pairs, records = extract(html)
    page = map_pairs(pairs)
    mapped = [map_pairs(r.items()) for r in records]
    mapped = [m for m in mapped if m]
    notes = []

    chosen, exact = None, False
    if want_license:
        for m in mapped:
            if _norm_lic(m.get("license_number")) == _norm_lic(want_license):
                chosen, exact = m, True
                break
    if chosen is None and mapped:
        licensed = [m for m in mapped if m.get("license_number")] or mapped
        chosen = licensed[0]
        if len(licensed) > 1:
            notes.append(f"Source lists {len(licensed)} license rows; the first was used.")

    if chosen is None:
        fields = dict(page)
    elif exact:
        fields = {**page, **chosen}
    else:
        fields = {**chosen, **page}

    if not fields.get("full_name") and (fields.get("first_name") or fields.get("last_name")):
        fields["full_name"] = _clean(f"{fields.get('first_name', '')} {fields.get('last_name', '')}")

    raw = list(pairs)
    for n, rec in enumerate(records, 1):
        raw.extend((f"[row {n}] {k}", v) for k, v in rec.items() if k and v)
    return {"fields": fields, "raw": raw, "notes": notes}
