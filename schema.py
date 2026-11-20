"""Column definitions shared by the Excel reader/writer, comparator and report."""

import re

# Input sheet columns, in template order: (key, header, hint shown in the template)
INPUT_COLUMNS = [
    ("s_no", "S. No", "Row id"),
    ("first_name", "First Name", "Applicant first name"),
    ("middle_name", "Middle Name", "Optional middle name or initial"),
    ("last_name", "Last Name", "Applicant last name"),
    ("dob", "DOB", "Optional. Compared only if the agency shows it"),
    ("license_type", "License Type", "e.g. Registered Nurse License"),
    ("governing_agency", "Governing Agency", "Agency that issued the license"),
    ("license_number", "License Number", ""),
    ("issuing_state", "Issuing State", "e.g. TX"),
    ("expiration_date", "Expiration Date", "m/d/yyyy"),
    ("license_status", "License Status", "Exactly as claimed"),
    ("original_issue_date", "Original Issuing Date", "m/d/yyyy"),
    ("disciplinary_actions", "Disciplinary Actions", "Yes / No"),
    ("multi_state_privilege", "Multi State Privilege", "Multistate / Single State"),
    ("agency_url", "Agency URL", "Authorized agency lookup page"),
    ("client_rules", "Client Rules", "Optional client specific guideline"),
]

# Fields compared between the input sheet and the agency record: (key, label)
COMPARE_FIELDS = [
    ("full_name", "Full Name"),
    ("dob", "DOB"),
    ("license_type", "License Type"),
    ("governing_agency", "Governing Agency"),
    ("license_number", "License Number"),
    ("issuing_state", "Issuing State"),
    ("expiration_date", "Expiration Date"),
    ("license_status", "License Status"),
    ("original_issue_date", "Original Issuing Date"),
    ("disciplinary_actions", "Disciplinary Actions"),
    ("multi_state_privilege", "Multi State Privilege"),
]

MATCH = "MATCH"
MISMATCH = "MISMATCH"
NOT_PROVIDED = "NOT PROVIDED"      # nothing in the input sheet to compare against
NOT_ON_SOURCE = "NOT ON SOURCE"    # agency page did not show this field

STATUS_CLEAR = "Verified - Clear"
STATUS_ALERT = "Verified - With Alerts"
STATUS_DISCREPANCY = "Verified - Discrepancy Found"
STATUS_REVIEW = "Needs Review"
STATUS_UNABLE = "Unable to Verify"

_ALIASES = {
    "s_no": ["sno", "serialno", "no", "id"],
    "first_name": ["firstname", "applicantfirstname"],
    "middle_name": ["middlename", "middleinitial", "middle", "mi"],
    "last_name": ["lastname", "applicantlastname", "surname"],
    "full_name": ["fullname", "applicantname", "name", "applicant"],
    "dob": ["dob", "dateofbirth", "birthdate"],
    "license_type": ["licensetype", "typeoflicense", "boardofnursinglicensetype", "type"],
    "governing_agency": ["governingagency", "agency", "issuingagency", "board"],
    "license_number": ["licensenumber", "licenseno", "licensenum", "license", "applicationlicenseendorsement"],
    "issuing_state": ["issuingstate", "state"],
    "expiration_date": ["expirationdate", "expirydate", "expiration", "expdate"],
    "license_status": ["licensestatus", "status"],
    "original_issue_date": ["originalissuingdate", "originalissuedate", "issuedate", "issuingdate", "dateissued"],
    "disciplinary_actions": ["disciplinaryactions", "disciplinaryaction", "discipline", "boardaction"],
    "multi_state_privilege": ["multistateprivilege", "multistate", "compact", "compactstatus", "compactmultistateeligible"],
    "agency_url": ["agencyurl", "url", "sourceurl", "lookupurl", "link"],
    "client_rules": ["clientrules", "clientguidelines", "clientrule", "rules", "notes"],
}
HEADER_TO_KEY = {alias: key for key, aliases in _ALIASES.items() for alias in aliases}


def header_key(header):
    """Map a spreadsheet header (any spacing/casing) to a schema key, or None."""
    return HEADER_TO_KEY.get(re.sub(r"[^a-z0-9]", "", str(header or "").lower()))
