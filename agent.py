"""Verity's verification run: input sheet -> agency lookup -> output sheet + report."""

import json
import os
import re
from datetime import datetime
from pathlib import Path

from . import __version__
from .browser import Browser, agency_for, local_path, lookup
from .compare import alerts_for, compare, conclude, fmt_date
from .excel_io import read_input, write_output
from .parse import parse_license_page
from .schema import COMPARE_FIELDS, MISMATCH, STATUS_REVIEW, STATUS_UNABLE
from .track import ACTIVE, DONE, FAILED, SKIPPED, STEPS, Tracker

_HERE = Path(__file__).parent
OUTPUT_DIR = _HERE / "output"


def _shown(path):
    """Path as shown in logs: relative to where Verity was started, so shared logs carry no user folders."""
    try:
        return os.path.relpath(path)
    except ValueError:
        return Path(path).name


def _slug(text):
    return re.sub(r"[^A-Za-z0-9]+", "_", str(text)).strip("_") or "row"


def _unverified(row, status, reason, candidates=(), **extra):
    comment = reason + (" Candidates: " + " | ".join(candidates) if candidates else "")
    return {"status": status, "comparison": compare(row, {}), "alerts": [], "discrepancies": "",
            "comments": comment, "raw": [], "source": "", "source_url": row.get("agency_url", ""),
            "evidence": "", **extra}


def verify_row(row, i, browser, out_dir, t, timeout_s):
    """Verify one applicant (row index i), reporting each step to tracker t."""
    url = row.get("agency_url", "")
    saved = local_path(url)
    agency = agency_for(url)
    today = datetime.now()
    evidence, defaults, extra = "", {}, {}

    how = f"license {row['license_number']}" if row.get("license_number") else "name only"
    t.step(i, "read", DONE, f"{row['full_name'] or '(no name)'} - {row.get('license_type') or 'license'}, {how}")

    if saved is not None:
        if not saved.is_absolute():
            saved = Path.cwd() / saved
        roots = getattr(t, "local_roots", None)
        if roots is not None and not any(saved.resolve().is_relative_to(Path(r).resolve()) for r in roots):
            reason = "Saved pages outside Verity's own folders cannot be read from the harness."
            t.step(i, "open", FAILED, reason)
            return _unverified(row, STATUS_UNABLE, reason)
        if not saved.exists():
            t.step(i, "open", FAILED, f"Saved page not found: {saved}")
            return _unverified(row, STATUS_UNABLE, f"Saved page not found: {saved}")
        t.step(i, "open", DONE, f"Saved page {saved.name} (offline)")
        html, source, evidence = saved.read_text(encoding="utf-8", errors="replace"), f"Saved page ({saved.name})", saved.name
    elif not url:
        t.step(i, "open", FAILED, "No Agency URL in the input row")
        return _unverified(row, STATUS_UNABLE, "No Agency URL in the input row.")
    elif agency is None:
        reason = "Agency URL is not on Verity's list of authorized agency sites; not searched."
        t.step(i, "open", FAILED, reason)
        return _unverified(row, STATUS_UNABLE, reason)
    else:
        source = agency["agency"]
        if agency.get("governing_agency"):
            defaults["governing_agency"] = agency["governing_agency"]
        stem = out_dir / "evidence" / f"{_slug(row['s_no'])}_{_slug(row['last_name'])}"
        try:
            found = lookup(browser, row, t, i, stem, timeout_s)
        except Exception as exc:
            reason = f"Lookup failed: {str(exc).splitlines()[0]}"
            t.log(reason, i, "error")
            return _unverified(row, STATUS_UNABLE, reason, source=source)
        evidence = found["evidence"]
        if not found["html"]:
            status = STATUS_REVIEW if found["candidates"] else STATUS_UNABLE
            return _unverified(row, status, found["error"], found["candidates"], source=source, evidence=evidence)
        html, url = found["html"], found["url"]
        # The issuing state is the agency's own; a "State" on the page is the licensee's address.
        extra = {**found["fields"], "issuing_state": agency["state"]}

    t.step(i, "parse", ACTIVE)
    parsed = parse_license_page(html, row.get("license_number"))
    verified = {**defaults, **parsed["fields"], **extra}
    if not any(verified.get(k) for k in ("license_number", "full_name", "license_status")):
        reason = ("The agency page was retrieved but no license details could be read from it. "
                  "Check the saved evidence.")
        t.step(i, "parse", FAILED, reason)
        return _unverified(row, STATUS_REVIEW, reason, source=source, evidence=evidence, raw=parsed["raw"])
    t.step(i, "parse", DONE, f"{len(parsed['fields'])} fields read from the source")

    t.step(i, "compare", ACTIVE)
    comparison = compare(row, verified)
    alerts = alerts_for(verified, today.date())
    status, discrepancies, comments = conclude(comparison, alerts, source, fmt_date(today))
    bad = sum(1 for c in comparison if c["result"] == MISMATCH)
    same = sum(1 for c in comparison if c["result"] == "MATCH")
    t.step(i, "compare", DONE, f"{same} match, {bad} mismatch")
    if parsed["notes"]:
        comments += " " + " ".join(parsed["notes"])
    if row.get("client_rules"):
        comments += f" Client rule to apply: {row['client_rules']}"
    return {"status": status, "comparison": comparison, "alerts": alerts, "discrepancies": discrepancies,
            "comments": comments, "raw": parsed["raw"], "source": source, "source_url": url, "evidence": evidence}


def build_report(results, meta, path):
    by_field = []
    for key, label in COMPARE_FIELDS:
        cells = [c for r in results for c in r["comparison"] if c["field"] == key]
        by_field.append({"label": label, **{name: sum(1 for c in cells if c["result"] == name)
                                             for name in ("MATCH", "MISMATCH", "NOT PROVIDED", "NOT ON SOURCE")}})
    data = {"meta": meta, "fields": by_field,
            "results": [{k: v for k, v in r.items() if k != "raw"} for r in results]}
    template = (_HERE / "report_template.html").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    Path(path).write_text(template.replace("/*__VERITY_DATA__*/null", payload), encoding="utf-8")


def run(input_path, out_dir=None, timeout_s=180, tracker=None):
    t = tracker or Tracker()
    input_path = Path(input_path)
    started = datetime.now()
    out_dir = Path(out_dir) if out_dir else OUTPUT_DIR / started.strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = read_input(input_path)
    t.on_start(rows, out_dir)
    t.log(f"Verity {__version__} - {len(rows)} applicant(s) read from {input_path.name}")

    browser = Browser(_HERE / ".browser_profile", t.log)
    results = []
    try:
        for i, row in enumerate(rows):
            t.log(f"[{i + 1}/{len(rows)}] {row['full_name'] or '(no name)'} - "
                  f"{row['license_type']} {row['license_number']}")
            if t.cancelled:
                res = _unverified(row, STATUS_UNABLE, "The run was stopped before this applicant was searched.")
            else:
                res = verify_row(row, i, browser, out_dir, t, timeout_s)
            for key, _ in STEPS[:-1]:
                if t.states.get((i, key)) not in (DONE, FAILED):
                    t.step(i, key, SKIPPED)
            t.step(i, "close", DONE, res["status"])
            res.update(s_no=row["s_no"], applicant=row["full_name"], verified_on=fmt_date(datetime.now()))
            results.append(res)
            t.on_row_result(i, res)
    finally:
        browser.close()

    xlsx, html = out_dir / "verity_output.xlsx", out_dir / "verity_report.html"
    write_output(xlsx, results)
    build_report(results, {"input": input_path.name, "run_at": started.strftime("%m/%d/%Y %H:%M"),
                           "output": xlsx.name, "version": __version__}, html)
    t.log(f"Output workbook : {_shown(xlsx)}")
    t.log(f"Summary report  : {_shown(html)}")
    t.on_finish(xlsx, html, results)
    return xlsx, html, results
