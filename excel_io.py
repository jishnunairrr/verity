"""Read the input workbook and write the output workbook."""

from datetime import date, datetime

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .compare import fmt_date
from .schema import (COMPARE_FIELDS, INPUT_COLUMNS, MATCH, MISMATCH, STATUS_ALERT, STATUS_CLEAR,
                     STATUS_DISCREPANCY, header_key)

_HEAD_FILL = PatternFill("solid", fgColor="1F2A44")
_HEAD_FONT = Font(bold=True, color="FFFFFF")
_FILLS = {
    "good": PatternFill("solid", fgColor="D9F2E3"),
    "bad": PatternFill("solid", fgColor="F8D7DA"),
    "warn": PatternFill("solid", fgColor="FFF1CC"),
}
_DATE_KEYS = ("dob", "expiration_date", "original_issue_date")


def _cell_text(key, value):
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return fmt_date(value)
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    return fmt_date(text) if key in _DATE_KEYS else text


def read_input(path):
    ws = load_workbook(path, data_only=True).active
    rows = list(ws.iter_rows(values_only=True))
    head_idx = next((i for i, r in enumerate(rows) if sum(1 for c in r if header_key(c)) >= 3), None)
    if head_idx is None:
        raise ValueError("Could not find a header row. Run 'python -m verity init' for a template.")
    keys = [header_key(c) for c in rows[head_idx]]

    out = []
    for n, raw in enumerate(rows[head_idx + 1:], 1):
        row = {k: _cell_text(k, v) for k, v in zip(keys, raw) if k}
        if not any(v for k, v in row.items() if k != "s_no"):
            continue
        full = row.get("full_name", "")
        if full and not (row.get("first_name") or row.get("last_name")):
            if "," in full:
                last, first = [p.strip() for p in full.split(",", 1)]
            else:
                parts = full.split()
                first, last = " ".join(parts[:-1]), parts[-1]
            given = first.split()
            if len(given) > 1 and not row.get("middle_name"):  # "John E" -> first John, middle E
                first, row["middle_name"] = given[0], " ".join(given[1:])
            row["first_name"], row["last_name"] = first, last
        row["full_name"] = " ".join(p for p in (row.get("first_name"), row.get("middle_name"), row.get("last_name")) if p)
        row["s_no"] = row.get("s_no") or str(n)
        for key, _, _ in INPUT_COLUMNS:
            row.setdefault(key, "")
        out.append(row)
    return out


def _sheet(wb, title, headers, first=False):
    ws = wb.active if first else wb.create_sheet()
    ws.title = title
    ws.append(headers)
    for cell in ws[1]:
        cell.fill, cell.font = _HEAD_FILL, _HEAD_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"
    return ws


def _autosize(ws, cap=60):
    for i, col in enumerate(ws.columns, 1):
        width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
        ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 10), cap)


def write_template(path):
    wb = Workbook()
    ws = _sheet(wb, "Input", [h for _, h, _ in INPUT_COLUMNS], first=True)
    ws.append(["1", "JANE", "", "DOE", "", "Registered Nurse License", "Texas Board of Nursing", "000000", "TX",
               "1/31/2027", "Active", "6/15/2015", "No", "Multistate",
               "https://txbn.boardsofnursing.org/licenselookup", "Replace this example row"])
    guide = _sheet(wb, "Guide", ["Column", "What to enter"])
    for _, header, hint in INPUT_COLUMNS:
        guide.append([header, hint])
    _autosize(ws)
    _autosize(guide)
    wb.save(path)


def write_output(path, results):
    wb = Workbook()
    fields = [(k, label) for k, label in COMPARE_FIELDS]

    out = _sheet(wb, "Output", ["S. No", "Applicant"] + [label for _, label in fields] +
                 ["Source", "Search Status", "Discrepancies", "Alerts", "Comments", "Verified On", "Evidence"],
                 first=True)
    for r in results:
        by_field = {c["field"]: c for c in r["comparison"]}
        out.append([r["s_no"], r["applicant"]] + [by_field[k]["verified"] if k in by_field else "" for k, _ in fields] +
                   [r["source"], r["status"], r["discrepancies"], " ".join(r["alerts"]), r["comments"],
                    r["verified_on"], r["evidence"]])
        line = out.max_row
        for col, (k, _) in enumerate(fields, 3):
            if by_field.get(k, {}).get("result") == MISMATCH:
                out.cell(line, col).fill = _FILLS["bad"]
        tone = "good" if r["status"] == STATUS_CLEAR else "bad" if r["status"] == STATUS_DISCREPANCY else "warn"
        out.cell(line, len(fields) + 4).fill = _FILLS[tone]

    comp = _sheet(wb, "Comparison", ["S. No", "Applicant", "Field", "Input (claimed)", "Verified (source)",
                                     "Result", "Note"])
    for r in results:
        for c in r["comparison"]:
            comp.append([r["s_no"], r["applicant"], c["label"], c["input"], c["verified"], c["result"], c["note"]])
            tone = "good" if c["result"] == MATCH else "bad" if c["result"] == MISMATCH else None
            if tone:
                comp.cell(comp.max_row, 6).fill = _FILLS[tone]

    raw = _sheet(wb, "Source Data", ["S. No", "Applicant", "Label on source", "Value on source"])
    for r in results:
        for label, value in r["raw"]:
            raw.append([r["s_no"], r["applicant"], label, value])

    for ws in (out, comp, raw):
        _autosize(ws)
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
    wb.save(path)
