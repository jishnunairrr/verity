"""Agency lookups.

Verity drives a normal, visible browser window. Some agency sites protect their
search with a security check ("Verify you are human"); Verity never tries to
get around it - it pauses and waits for the operator to tick the box, then
carries on. The browser profile is kept between rows so the check is usually
only needed once per run.
"""

import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from .compare import canon_type
from .track import ACTIVE, DONE, FAILED, WAITING

# Agencies Verity knows how to search. Verification must come from the
# authorized agency site, so any other host is refused rather than guessed at.
#   agency            name recorded as the verification source
#   governing_agency  set when the whole site is one agency; otherwise read from the page (its "Board")
#   name_selector     where the license page shows the licensee's name, when it has no label
AGENCIES = {
    "txbn.boardsofnursing.org": {"agency": "Texas Board of Nursing", "governing_agency": "Texas Board of Nursing",
                                 "state": "TX", "kind": "boardsofnursing"},
    "elicense.ohio.gov": {"agency": "Ohio eLicense (State of Ohio)", "state": "OH", "kind": "ohio_elicense",
                          "name_selector": "form[id$='lcPageDetail'] h2"},
}

# On a server there is no screen: run the browser without a window. Agencies that need a person
# to pass a security check cannot be searched in this mode.
HEADLESS = os.environ.get("VERITY_HEADLESS", "").lower() in ("1", "true", "yes")

_SECURITY_JS = """() => {
  const box = document.querySelector('#turnstileContainer, .cf-turnstile');
  const token = document.querySelector('[name="cf-turnstile-response"]');
  if (!box && !token) return true;
  return !!(token && token.value);
}"""

_RESULT_LINKS = "a[href*='Report' i], a[href*='Detail' i]"

_CANDIDATES_JS = """els => els.map(a => ({
  href: a.href || '',
  text: ((a.closest('tr') || a.closest('li') || a.parentElement).innerText || '').replace(/\\s+/g, ' ').trim()
}))"""


def agency_for(url):
    return AGENCIES.get((urlparse(url).hostname or "").lower())


def local_path(url):
    """A saved page (file:// URL or a path to .html) can be re-verified offline."""
    text = str(url or "").strip()
    if text.lower().startswith("file://"):
        text = re.sub(r"^file:/+", "", text, flags=re.I)
        return Path(text if re.match(r"^[A-Za-z]:", text) else "/" + text)
    if text.lower().endswith((".html", ".htm")) and not re.match(r"^https?://", text, re.I):
        return Path(text)
    return None


class Browser:
    """Lazily started, visible, persistent browser session."""

    def __init__(self, profile_dir, log):
        self.profile_dir, self.log = Path(profile_dir).resolve(), log
        self._pw = self._ctx = self._page = None

    @property
    def page(self):
        if self._page is None:
            from playwright.sync_api import sync_playwright
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            self._pw = sync_playwright().start()
            last_error = None
            for channel in ("chrome", "msedge", None):
                try:
                    self._ctx = self._pw.chromium.launch_persistent_context(
                        str(self.profile_dir), headless=HEADLESS, channel=channel, timeout=30000,
                        viewport={"width": 1280, "height": 900})
                    break
                except Exception as exc:  # try the next installed browser
                    last_error = exc
            if self._ctx is None:
                raise RuntimeError(f"Could not start a browser: {last_error}")
            self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        return self._page

    def close(self):
        for closer in (getattr(self._ctx, "close", None), getattr(self._pw, "stop", None)):
            try:
                closer and closer()
            except Exception:
                pass


def _hide_cookie_banner(page):
    """Decline optional cookies when offered; otherwise just move the banner out of the way."""
    try:
        reject = page.locator("#onetrust-reject-all-handler")
        if reject.count() and reject.first.is_visible():
            reject.first.click()
            return
    except Exception:
        pass
    try:
        page.add_style_tag(content="#onetrust-consent-sdk{display:none!important}")
    except Exception:
        pass


def _wait_security(page, t, i, timeout_s):
    """Wait for the operator to pass the security check. Returns an error message or None."""
    try:
        page.wait_for_selector('[name="cf-turnstile-response"]', state="attached", timeout=6000)
    except Exception:
        pass
    deadline, told = time.time() + timeout_s, False
    while time.time() < deadline:
        if t.cancelled:
            return "The run was stopped."
        if HEADLESS and told and time.time() > deadline - timeout_s + 10:
            return ("This agency asks a person to pass a security check, which is not possible when Verity "
                    "runs on a server without a screen.")
        if page.is_closed():
            return "The browser window was closed before the security check was completed."
        try:
            if page.locator(_RESULT_LINKS).count() or page.evaluate(_SECURITY_JS):
                t.step(i, "security", DONE, "Passed" if told else "Not required")
                return None
        except Exception:
            pass  # the page is navigating (for example the operator pressed Search); look again
        if not told:
            t.step(i, "security", WAITING,
                   f"Tick 'Verify you are human' in the Verity browser window (waiting up to {timeout_s}s)")
            told = True
        t.shot(page, min_gap=2)
        time.sleep(1)
    return f"The security check was not completed within {timeout_s} seconds."


def _select_type(page, selector, license_type):
    if not license_type:
        return
    want = canon_type(license_type)
    options = page.eval_on_selector_all(selector + " option", "els => els.map(e => e.textContent.trim())")
    for opt in options:
        if opt and canon_type(opt) == want:
            page.select_option(selector, label=opt)
            return


def _norm(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


# ---- site kinds: how to reach the search form, fill it and submit it ---------------------------

def _bon_fill(page, row, mode):
    if mode == "license":
        page.locator("a:has-text('Search by License Number'):visible").first.click()
        page.fill("#LicenseSearch_LicenseSearchInput_LicenseNumber", row["license_number"])
        _select_type(page, "#LicenseSearch_LicenseSearchInput_LicenseTypeId", row.get("license_type"))
    else:
        page.fill("#LicenseSearch_NameSearchInput_LastName", row["last_name"])
        page.fill("#LicenseSearch_NameSearchInput_FirstName", row["first_name"])
        _select_type(page, "#LicenseSearch_NameSearchInput_LicenseTypeId", row.get("license_type"))


def _ohio_enter(page):
    # The site opens on an intro page with a "continue" button; wait until either it or the
    # search form has actually rendered before deciding which one we are on.
    start = "input[type=submit][value='continue']"
    form = "[id$=':licenseNumber']"
    page.wait_for_selector(f"{start}, {form}", state="visible", timeout=45000)
    if page.locator(start).count() and page.locator(start).first.is_visible():
        page.locator(start).first.click()
    page.wait_for_selector(form, state="visible", timeout=45000)


def _stem(text):
    return {w.rstrip("s") for w in re.findall(r"[a-z]+", str(text or "").lower())} - {"of", "the", "and"}


def _ohio_select_board(page, board):
    """Narrow a name search to the applicant's board when the input names one unambiguously."""
    want = _stem(board)
    if not want:
        return
    options = page.eval_on_selector_all("[id$=':board'] option", "els => els.map(e => e.textContent.trim())")
    hits = [o for o in options if want <= _stem(o)]
    if len(hits) == 1:
        page.select_option("[id$=':board']", label=hits[0])
        page.wait_for_timeout(1500)  # the form refreshes its license types


def _ohio_fill(page, row, mode):
    if mode == "license":
        page.fill("[id$=':licenseNumber']", row["license_number"])
    else:
        _ohio_select_board(page, row.get("governing_agency"))
        page.fill("[id$=':lastName']", row["last_name"])
        page.fill("[id$=':firstName']", row["first_name"])


_SITES = {
    "boardsofnursing": {"enter": lambda page: None, "fill": _bon_fill, "security": True,
                        "submit": lambda page: page.click("#btnSearch"), "empty": ".validation-summary-errors"},
    "ohio_elicense": {"enter": _ohio_enter, "fill": _ohio_fill, "security": False,
                      "submit": lambda page: page.locator("input.searchButton:visible").first.click(),
                      "empty": "td.dataTables_empty"},
}


def _search(page, row, mode, site, t, i, timeout_s):
    t.step(i, "open", ACTIVE, row["agency_url"])
    page.goto(row["agency_url"], wait_until="domcontentloaded")
    _hide_cookie_banner(page)
    site["enter"](page)
    t.step(i, "open", DONE, page.url)
    t.shot(page)

    what = f"License number {row['license_number']}" if mode == "license" else \
        f"Name {row['last_name']}, {row['first_name']}"
    t.step(i, "search", ACTIVE, what)
    site["fill"](page, row, mode)
    t.step(i, "search", DONE, what)
    t.shot(page)

    if site["security"]:
        error = _wait_security(page, t, i, timeout_s)
        if error:
            t.step(i, "security", FAILED, error)
            return None, error
    else:
        t.step(i, "security", DONE, "Not required by this agency")
    t.step(i, "record", ACTIVE, "Submitting search")
    if not page.locator(_RESULT_LINKS).count():  # unless the operator already pressed Search
        site["submit"](page)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    try:
        page.wait_for_selector(f"{_RESULT_LINKS}, {site['empty']}", timeout=15000)
    except Exception:
        pass
    t.shot(page)
    return page.eval_on_selector_all(_RESULT_LINKS, _CANDIDATES_JS), None


def _pick(candidates, row):
    """Confirm a result belongs to the applicant: license number first, then full name."""
    lic = _norm(row.get("license_number"))
    if lic:
        hits = [c for c in candidates if lic in _norm(c["text"])]
        if hits:
            return hits[0], None
    first, last = _norm(row.get("first_name")), _norm(row.get("last_name"))
    hits = [c for c in candidates if first and last and first in _norm(c["text"]) and last in _norm(c["text"])]
    if len(hits) == 1:
        return hits[0], None
    if len(candidates) == 1 and not lic and not (first and last):
        return candidates[0], None
    return None, hits or candidates


def lookup(browser, row, t, i, evidence_stem, timeout_s=180):
    """Search the agency for one applicant (row index i), reporting each step to tracker t.

    Returns {"html", "url", "error", "candidates", "evidence", "fields"}; "html" is the
    license page when a single record was confirmed, "fields" anything read
    directly from it that the label parser cannot see.
    """
    out = {"html": None, "url": row["agency_url"], "error": None, "candidates": [], "evidence": "", "fields": {}}
    agency = agency_for(row["agency_url"])
    site = _SITES[agency["kind"]]
    modes = (["license"] if row.get("license_number") else []) +             (["name"] if row.get("first_name") and row.get("last_name") else [])
    if not modes:
        out["error"] = "Input has neither a license number nor a full name to search with."
        t.step(i, "search", FAILED, out["error"])
        return out
    t.step(i, "open", ACTIVE, "Starting browser")
    page = browser.page

    for mode in modes:
        candidates, error = _search(page, row, mode, site, t, i, timeout_s)
        if error:
            out["error"] = error
            break
        if not candidates:
            t.log(f"No results when searching by {mode}", i)
            continue
        chosen, others = _pick(candidates, row)
        if chosen is None:
            out["candidates"] = [c["text"] for c in others][:10]
            out["error"] = f"{len(others)} possible records found; none could be confirmed as the applicant."
            continue
        if chosen["href"] and not chosen["href"].lower().startswith("javascript"):
            page.goto(chosen["href"], wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        out.update(html=page.content(), url=page.url, error=None, candidates=[])
        if agency.get("name_selector"):
            try:
                name = page.locator(agency["name_selector"]).first.inner_text(timeout=3000)
                out["fields"]["full_name"] = re.sub(r"\s+", " ", name.replace("\xa0", " ")).strip()
            except Exception:
                pass
        t.step(i, "record", DONE, chosen["text"][:120])
        break
    else:
        if not out["error"]:
            # No result links: keep the page so a reviewer can see what the agency returned.
            out["error"] = "No matching record was returned by the agency."
    if out["error"] and not out["html"] and "security" not in out["error"].lower() and "stopped" not in out["error"]:
        t.step(i, "record", FAILED, out["error"])

    try:
        stem = Path(evidence_stem)
        stem.parent.mkdir(parents=True, exist_ok=True)
        stem.with_suffix(".html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(stem.with_suffix(".png")), full_page=True)
        out["evidence"] = stem.with_suffix(".png").name
        t.shot(page)
    except Exception:
        pass
    time.sleep(2)  # be gentle with the agency site between applicants
    return out
