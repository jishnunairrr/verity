# Verity

Verity is a professional license verification (PLV) agent. It reads applicants
from an Excel sheet, looks each one up on the governing agency's own site,
writes what the agency shows to an output workbook, and builds a summary report
of where the claimed details and the agency record agree or differ.

## Use

    pip install -r verity/requirements.txt
    python -m verity serve                  # harness UI: run and monitor the agent
    python -m verity init my_batch.xlsx     # blank input template
    python -m verity run my_batch.xlsx      # verify every row
    python -m verity demo                   # try it on fictional sample pages

Each run writes to `verity/output/<timestamp>/`:

| File | Contents |
| --- | --- |
| `verity_output.xlsx` | `Output` (verified values, search status, comments), `Comparison` (field by field), `Source Data` (everything read from the agency page) |
| `verity_report.html` | Summary UI: totals, mismatches by field, per-applicant side-by-side comparison |
| `evidence/` | Screenshot and saved HTML of the agency page for each applicant |

## The harness

`python -m verity serve` opens http://127.0.0.1:8765 (local only). From there you
can upload or pick an input sheet, start and stop a run, and watch the agent:

- **Agent track** - every applicant moves through the same eight steps (read
  input row, open agency site, enter search, security check, open license
  record, read record, compare with input, close search), each shown as
  pending, active, waiting for you, done, failed or skipped, with its detail.
- **Live view** - the latest picture of the agent's browser.
- **Activity log** - a timestamped line for everything the agent does.
- **Results** - input versus source for each applicant, plus links to the
  summary report and the output workbook.
- **Past runs** - every finished run is kept with its full track (`run.json`).

## Letting other people use the harness

By default the harness is reachable only from this machine. To share it, start
it with an access code; everyone must then sign in before they can see or run
anything, upload a sheet or download a result.

    python -m verity serve --access-code auto     # prints a generated code

- **From this PC** - `python -m verity share` starts the harness with an access
  code (kept in `verity/.access_code`) and opens a public
  `https://...trycloudflare.com` link through Cloudflare's `cloudflared`. Chrome
  runs on this PC, so every agency works; the link is live only while that
  command is running, and it changes each time.
- **On a server** - `VERITY_HEADLESS=1 python -m verity serve --host 0.0.0.0`
  (set `VERITY_ACCESS_CODE`; one is generated if you do not). The browser runs
  without a window, so agencies with a security check cannot be searched.

Sheets uploaded through the harness can only use agency URLs; saved pages are
limited to Verity's own samples.

## Input columns

S. No, First Name, Last Name, DOB, License Type, Governing Agency, License
Number, Issuing State, Expiration Date, License Status, Original Issuing Date,
Disciplinary Actions, Multi State Privilege, Agency URL, Client Rules.

Blank cells are reported as "not provided" rather than as a mismatch.

## How a row is verified

1. The Agency URL must be an agency Verity knows (`AGENCIES` in `browser.py`);
   anything else is closed as *Unable to Verify* instead of being searched.
2. Search by license number, falling back to first and last name.
3. A result is accepted only if its license number or full name matches the
   applicant. Several unconfirmed candidates close as *Needs Review*.
4. The license page is parsed and compared with the input, field by field.
5. Expired licenses, non-active statuses and disciplinary actions are raised
   as alerts even when they match what the applicant claimed.

Search statuses: Verified - Clear, Verified - With Alerts, Verified -
Discrepancy Found, Needs Review, Unable to Verify.

## The security check

Agency sites protect the search with a "Verify you are human" box. Verity does
not bypass it. It opens a visible browser window, fills in the search, and
waits (default 180 s, `--timeout`) for you to tick the box, then continues.
The browser profile is kept in `verity/.browser_profile/`, so the check is
normally needed far less than once per row.

## Agencies

| Agency site | Covers | Security check |
| --- | --- | --- |
| `txbn.boardsofnursing.org` | Texas Board of Nursing | Yes - operator ticks the box |
| `elicense.ohio.gov` | Ohio eLicense: the 24 State of Ohio boards on the site (Medical, Nursing, Pharmacy, ODA Auctioneers, ...) | No |

On Ohio eLicense the governing agency is read from the record's "Board".

## Adding an agency

Add the host to `AGENCIES` in `browser.py` and, for a new kind of site, an
entry in `_SITES` saying how to reach the search form, fill it and submit it.
The page parser (`parse.py`) is layout-agnostic and keys on field labels, so it
usually needs only new label keywords.

A saved agency page can be re-verified offline by putting its file path in the
Agency URL column.
