#!/usr/bin/env python3
"""
build_dashboard_data.py  —  Fab Supplier SCAN single source build step

The master workbook Tech_Hub_Company_Listing.xlsx is the only file you edit by
hand. One run of this script regenerates every downstream artifact from it:

    docs/data/data.json          for the GitHub Pages HTML dashboard
    tableau/suppliers.xlsx       for Tableau Public
    build_report.txt             what was written, dropped, cleaned, flagged

Both outputs come from the same read of the same bytes in the same run, so they
cannot drift apart. The SHA256 of the master is stamped into all three, which
lets you prove which snapshot is live.

Source sheet: "Scraped capability statements" (155 rows). It is the only tab
that carries all 18 published fields. The other supplier tabs lack the Google
Maps and capability statement URL columns, and "Scraped prospects to keep" uses
an unrelated D and B schema.

Usage
    python build_dashboard_data.py --inspect        list sheets and columns
    python build_dashboard_data.py                  build with defaults
    python build_dashboard_data.py --strict         treat warnings as fatal

Requires: pandas, openpyxl
    pip install pandas openpyxl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd


# ===========================================================================
# 1.  CONFIGURATION  —  the only block you should need to edit
# ===========================================================================

DEFAULT_SOURCE = "Tech_Hub_Company_Listing.xlsx"
DEFAULT_JSON_OUT = "docs/data/data.json"
DEFAULT_TABLEAU_DIR = "tableau"

# Sheet to publish. Matching ignores case, spaces, underscores and punctuation.
SOURCE_SHEET_ALIASES = [
    "Scraped capability statements",
    "Capability Statements",
    "Suppliers",
]

# THE PUBLICATION GATE.
# Excel header on the left, published JSON key on the right. Any column not
# listed here never leaves the workbook. Both GitHub Pages and Tableau Public
# are world readable, so treat this mapping as a publication decision.
FIELD_MAP = {
    "Supplier_Name":                 "supplier_name",
    "Address":                       "address",
    "City":                          "city",
    "County":                        "county",
    "State":                         "state",
    "ZIP Code":                      "zip_code",
    "Tier Classification":           "tier_label",
    "Supplier_Type":                 "supplier_type",
    "Industry_Sector":               "industry_sector",
    "Primary_Capability":            "primary_capability",
    "Secondary_Capabilities":        "secondary_capability",
    "Website":                       "website",
    "Contact_Name":                  "contact_name",
    "Contact_Email":                 "contact_email",
    "Google My Maps Location URL":   "google_maps_url",
    "Latitude":                      "latitude",
    "Longitude":                     "longitude",
    "Capability statement URL":      "capability_statement_url",
}

# Columns that must never reach an output. Everything outside FIELD_MAP is
# dropped anyway; these are named so the leak check can abort loudly.
BLOCKED_COLUMNS = [
    "Supplier_ID", "Employee_Count", "Revenue_Range", "Certification_Status",
    "ISO_9001", "AS9100", "ITAR", "CMMC", "Any_Other_Certification",
    "Markets_Served", "NAICS_Code", "Materials_Worked",
    "Processes", "Equipment_List", "Production_Specialization",
    "Match_Score", "Match_Category", "Contact_Phone",
]

# Tier Classification text mapped to the numeric tier the page sorts and colors
# by. 0 is the unclassified bucket, 4 the support organizations.
TIER_LEVELS = {
    "tier 1": 1, "tier1": 1, "1": 1,
    "tier 2": 2, "tier2": 2, "2": 2,
    "tier 3": 3, "tier3": 3, "3": 3,
    "support organization": 4, "support": 4, "support org": 4,
}
TIER_NAMES = {0: "Unclassified", 1: "Tier 1", 2: "Tier 2", 3: "Tier 3", 4: "Support Organization"}

# The capability mapping sheet, which turns free text capability phrases into
# the fixed keys the fab cutaway lights zones from. Produced by
# make_capability_map.py, reviewed by hand, then pasted into the master.
CAPABILITY_MAP_SHEET_ALIASES = ["Capability_Map", "Capability Map", "Capability Mapping"]
CAPABILITY_MAP_COLUMNS = {
    "phrase": ["Capability Phrase", "Phrase", "Capability"],
    "suggested": ["Suggested Key", "Suggested"],
    "approved": ["Approved Key", "Approved", "Final Key"],
}
# The 18 keys the fab cutaway understands. Keep in step with CAPS on the page.
CAPABILITY_NAMES = {
    "hepa":  "HEPA/ULPA Filtration & Fan Filter Units",
    "iot":   "Environmental & IoT Sensor Mesh",
    "amhs":  "Automated Material Handling (AMHS/OHB)",
    "dep":   "Semiconductor Deposition Equipment Manufacturing",
    "litho": "Lithography, Etch & Metrology Equipment",
    "cnc":   "Precision CNC Machining",
    "sens":  "Pressure & Specialized Sensor Manufacturing",
    "vac":   "Vacuum Pumps & Exhaust Abatement",
    "gas":   "Gas & Chemical Delivery Systems",
    "chem":  "Specialty Chemicals & Industrial Gases",
    "rf":    "RF & Plasma Power Subsystems",
    "vib":   "Vibration Isolation & Motion Control",
    "upw":   "Ultrapure Water Treatment (Filtration & RO)",
    "pump":  "Pumps & Fluid Distribution",
    "opt":   "Precision Optics & Photonics",
    "mat":   "Advanced Materials & Substrates",
    "elec":  "Electronics, Power & Interconnect",
    "batt":  "Backup Power, Batteries & Hydrogen",
}
VALID_CAPABILITY_KEYS = set(CAPABILITY_NAMES)

# Per field publication switches. A field set to False is omitted from the
# published schema entirely, in both outputs, without touching the master.
PUBLISH = {
    "contact_name": True,
    "contact_email": False,            # withheld: a public page would be scraped
    "capability_statement_url": True,  # buffalo.box.com links, may require sign in
}

# Cells whose text means "no value". Case insensitive, compared after trimming.
NULL_TOKENS = {
    "", "########", "n/a", "na", "none", "not listed", "not available",
    "unknown", "tbd", "-", "--", "nan", "null",
}

NUMERIC_FIELDS = ["latitude", "longitude"]

# Semicolon delimited cells that also become arrays. Commas are NOT delimiters:
# "Cutting, Grinding, Polishing Optical Materials" is one phrase, not three.
LIST_FIELDS = {"supplier_type": ";/", "primary_capability": ";", "secondary_capability": ";"}

STATE_FIXES = {"new york": "NY", "ny": "NY"}

EXPECTED_ROWS = 155       # warning only, so the dataset can grow. None to skip.
EXPECTED_STATE = "NY"     # rows outside this state are reported, not removed.

JSON_FIELD_ORDER = [
    "id", "supplier_name", "tier", "tier_label", "address", "city", "county",
    "state", "zip_code", "supplier_type", "supplier_type_list", "industry_sector",
    "primary_capability", "primary_capability_list",
    "secondary_capability", "secondary_capability_list",
    "capability_keys", "website", "website_display", "contact_name", "contact_email",
    "google_maps_url", "latitude", "longitude", "capability_statement_url",
]

# The fields that actually reach an output, after the PUBLISH switches.
PUBLISHED_FIELDS = [key for key in JSON_FIELD_ORDER if PUBLISH.get(key, True)]
WITHHELD_FIELDS = [key for key in JSON_FIELD_ORDER if not PUBLISH.get(key, True)]


# ===========================================================================
# 2.  HELPERS
# ===========================================================================

PROBLEMS: list[str] = []


def norm(value) -> str:
    """Collapse a sheet or column name to a comparison key."""
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def warn(message: str) -> None:
    PROBLEMS.append(message)
    print(f"  WARN  {message}")


def note(message: str) -> None:
    print(f"  ok    {message}")


def fail(message: str) -> None:
    print(f"\nERROR  {message}", file=sys.stderr)
    sys.exit(1)


def blank(value) -> bool:
    """True for anything that should become null."""
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    if isinstance(value, (datetime, date)):
        return False
    if pd.isna(value) if not isinstance(value, (str, list)) else False:
        return True
    return str(value).strip().lower() in NULL_TOKENS


def text(value) -> str | None:
    """Trim, collapse inner whitespace, normalize unicode, blanks to None."""
    if blank(value):
        return None
    cleaned = unicodedata.normalize("NFKC", str(value)).replace(" ", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ;,")
    return cleaned or None


def phrase_key(value) -> str:
    """
    The lookup key for a capability phrase. make_capability_map.py normalizes
    phrases the same way, so the two sides cannot drift: collapse whitespace,
    strip trailing punctuation, lowercase.
    """
    return " ".join(str(value).split()).strip(" ,;.").lower()


def slug(value: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", str(value).lower())).strip("-")


# ===========================================================================
# 3.  FIELD TRANSFORMS
# ===========================================================================

def parse_zip(value) -> str | None:
    """Excel stores ZIPs as integers, which eats the leading zero of 0xxxx."""
    if blank(value):
        return None
    raw = str(value).strip()
    if raw.endswith(".0"):
        raw = raw[:-2]
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    if len(digits) == 9:                       # ZIP+4 arrived unseparated
        return f"{digits[:5]}-{digits[5:]}"
    return digits.zfill(5)[:5]


def parse_website(value) -> tuple[str | None, str | None]:
    """Most values arrive as bare hosts, which do not work as an href."""
    host = text(value)
    if not host:
        return None, None
    host = host.rstrip("/")
    display = re.sub(r"^https?://", "", host, flags=re.I)
    href = host if re.match(r"^https?://", host, flags=re.I) else f"https://{host}"
    return href, display


def parse_state(value) -> str | None:
    cleaned = text(value)
    if not cleaned:
        return None
    return STATE_FIXES.get(cleaned.lower(), cleaned.upper() if len(cleaned) == 2 else cleaned)


def split_list(value, delimiters: str) -> list[str]:
    cleaned = text(value)
    if not cleaned:
        return []
    parts = re.split(f"[{re.escape(delimiters)}]", cleaned)
    seen, out = set(), []
    for part in parts:
        item = part.strip(" ;,/")
        if item and item.lower() not in seen:
            seen.add(item.lower())
            out.append(item)
    return out


def parse_tier(value) -> tuple[int, str]:
    """
    Tier Classification is free text and incomplete: Tier 1/2/3, plus
    Support Organization, plus blanks. Returns (level, label).
    """
    cleaned = text(value)
    if not cleaned:
        return 0, TIER_NAMES[0]
    level = TIER_LEVELS.get(cleaned.lower())
    if level is None:
        found = re.search(r"\b([123])\b", cleaned)
        level = int(found.group(1)) if found else 0
    return level, TIER_NAMES[level] if level in TIER_NAMES else cleaned


def load_capability_map(workbook: pd.ExcelFile) -> tuple[dict[str, str], set[str]]:
    """
    Read the reviewed phrase to key mapping. Approved Key wins over
    Suggested Key.

    Returns (mapping, known_phrases). known_phrases holds every phrase the
    sheet lists, including the ones deliberately left without a key, so the
    build can tell "intentionally unmapped" from "missing from the sheet".
    """
    actual = {norm(name): name for name in workbook.sheet_names}
    sheet = next((actual[norm(a)] for a in CAPABILITY_MAP_SHEET_ALIASES if norm(a) in actual), None)
    if sheet is None:
        warn(f"no capability mapping sheet found (looked for {CAPABILITY_MAP_SHEET_ALIASES}). "
             f"Every supplier will publish an empty capability_keys list, so the fab cutaway "
             f"will light no zones. Run make_capability_map.py and paste its sheet into the master.")
        return {}, set()

    frame = workbook.parse(sheet).dropna(how="all")
    cols = {norm(c): c for c in frame.columns}
    resolved = {}
    for role, aliases in CAPABILITY_MAP_COLUMNS.items():
        resolved[role] = next((cols[norm(a)] for a in aliases if norm(a) in cols), None)
    if resolved["phrase"] is None:
        fail(f"the {sheet} sheet has no phrase column. Looked for {CAPABILITY_MAP_COLUMNS['phrase']}.")

    mapping: dict[str, str] = {}
    known: set[str] = set()
    bad_keys, conflicts = [], []
    for row in frame.to_dict("records"):
        phrase = text(row.get(resolved["phrase"]))
        if not phrase:
            continue
        lookup = phrase_key(phrase)
        known.add(lookup)

        approved = text(row.get(resolved["approved"])) if resolved["approved"] else None
        suggested = text(row.get(resolved["suggested"])) if resolved["suggested"] else None
        key = (approved or suggested or "").lower()
        if not key:
            continue
        if key not in VALID_CAPABILITY_KEYS:
            bad_keys.append(f"{phrase!r} -> {key!r}")
            continue
        if lookup in mapping and mapping[lookup] != key:
            conflicts.append(f"{phrase!r}: {mapping[lookup]} vs {key}")
        mapping[lookup] = key

    if bad_keys:
        warn(f"{len(bad_keys)} mapping row(s) name a key that is not one of the 18 valid keys and "
             f"were ignored: {bad_keys[:3]}")
    if conflicts:
        warn(f"{len(conflicts)} phrase(s) appear more than once in the mapping sheet with different "
             f"keys; the last row won: {conflicts[:3]}")
    note(f"capability map: {len(known)} phrase(s) listed, {len(mapping)} carry a key, "
         f"from sheet {sheet}")
    return mapping, known


def maps_url(given, lat, lng) -> tuple[str | None, bool]:
    """
    Returns (url, coords_disagree). The stored URL is just ?q=lat,lng, so it is
    derivable. Where it is missing we generate it; where it is present and
    disagrees with the coordinate columns, we flag it and trust the columns.
    """
    url = text(given)
    if lat is None or lng is None:
        return url, False
    generated = f"https://www.google.com/maps?q={lat},{lng}"
    if not url:
        return generated, False
    found = re.search(r"q=(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)", url)
    if found:
        off = abs(float(found.group(1)) - lat) > 0.001 or abs(float(found.group(2)) - lng) > 0.001
        if off:
            return generated, True
    return url, False


# ===========================================================================
# 4.  LOAD AND CLEAN
# ===========================================================================

def resolve_sheet(workbook: pd.ExcelFile) -> str:
    actual = {norm(name): name for name in workbook.sheet_names}
    for alias in SOURCE_SHEET_ALIASES:
        if norm(alias) in actual:
            return actual[norm(alias)]
    fail(
        f"source sheet not found. Looked for {SOURCE_SHEET_ALIASES}. "
        f"Workbook contains: {workbook.sheet_names}"
    )


def resolve_columns(frame: pd.DataFrame) -> dict[str, str]:
    """Map Excel column to published key, tolerating trailing spaces and case."""
    actual = {norm(col): col for col in frame.columns}
    mapping, missing = {}, []
    for header, key in FIELD_MAP.items():
        source = actual.get(norm(header))
        if source is None:
            missing.append(header)
        else:
            mapping[source] = key
    if missing:
        warn(f"columns not found in the workbook and omitted: {missing}")
    return mapping


def build_records(
    frame: pd.DataFrame,
    mapping: dict[str, str],
    capability_map: dict[str, str],
    known_phrases: set[str],
) -> list[dict]:
    counters = {
        "website_fixed": 0, "maps_generated": 0, "maps_disagree": [],
        "coords_missing": [], "state_other": [], "zip_padded": [],
        "tier_unclassified": [], "no_capability_keys": [], "phrases_unmapped": set(),
    }
    records = []

    for _, row in frame.iterrows():
        raw = {key: row[col] for col, key in mapping.items()}
        rec: dict = {}

        rec["supplier_name"] = text(raw.get("supplier_name"))
        if not rec["supplier_name"]:
            continue                            # a row with no supplier is not a supplier

        for key in ("address", "city", "county", "industry_sector", "contact_name"):
            rec[key] = text(raw.get(key))

        rec["state"] = parse_state(raw.get("state"))
        if rec["state"] and EXPECTED_STATE and rec["state"] != EXPECTED_STATE:
            counters["state_other"].append(f"{rec['supplier_name']} ({rec['state']})")

        original_zip = raw.get("zip_code")
        rec["zip_code"] = parse_zip(original_zip)
        if rec["zip_code"] and not str(original_zip).strip().startswith(rec["zip_code"][:1]):
            counters["zip_padded"].append(f"{rec['supplier_name']} -> {rec['zip_code']}")

        rec["tier"], rec["tier_label"] = parse_tier(raw.get("tier_label"))
        if rec["tier"] == 0:
            counters["tier_unclassified"].append(rec["supplier_name"])

        for key, delims in LIST_FIELDS.items():
            rec[key] = text(raw.get(key))
            rec[f"{key}_list"] = split_list(raw.get(key), delims)

        # Free text capability phrases to the fixed keys the fab cutaway uses.
        phrases = rec["primary_capability_list"] + rec["secondary_capability_list"]
        keys = []
        for phrase in phrases:
            lookup = phrase_key(phrase)
            found = capability_map.get(lookup)
            if found:
                if found not in keys:
                    keys.append(found)
            elif known_phrases and lookup not in known_phrases:
                # Listed in the sheet with no key means deliberately unmapped.
                # Absent from the sheet means the sheet is out of date.
                counters["phrases_unmapped"].add(phrase)
        rec["capability_keys"] = keys
        if not keys:
            counters["no_capability_keys"].append(rec["supplier_name"])

        href, display = parse_website(raw.get("website"))
        rec["website"], rec["website_display"] = href, display
        if href and display and not str(raw.get("website", "")).strip().lower().startswith("http"):
            counters["website_fixed"] += 1

        email = text(raw.get("contact_email"))
        rec["contact_email"] = email if email and "@" in email else None
        if email and "@" not in email and PUBLISH.get("contact_email", True):
            warn(f"{rec['supplier_name']}: Contact_Email is not an address ({email!r}), withheld")

        for key in NUMERIC_FIELDS:
            value = pd.to_numeric(raw.get(key), errors="coerce")
            rec[key] = None if pd.isna(value) else round(float(value), 6)
        if rec["latitude"] is None or rec["longitude"] is None:
            counters["coords_missing"].append(rec["supplier_name"])
        else:
            if not (-90 <= rec["latitude"] <= 90) or not (-180 <= rec["longitude"] <= 180):
                fail(f"{rec['supplier_name']}: coordinates out of range "
                     f"({rec['latitude']}, {rec['longitude']})")

        url, disagree = maps_url(raw.get("google_maps_url"), rec["latitude"], rec["longitude"])
        rec["google_maps_url"] = url
        if disagree:
            counters["maps_disagree"].append(rec["supplier_name"])
        elif url and not text(raw.get("google_maps_url")):
            counters["maps_generated"] += 1

        rec["capability_statement_url"] = text(raw.get("capability_statement_url"))

        rec["id"] = f"{slug(rec['supplier_name'])}-{rec['zip_code'] or 'nozip'}"

        records.append({k: rec.get(k) for k in PUBLISHED_FIELDS})

    # Stable unique ids even when two suppliers share a name and a ZIP.
    counts: dict[str, int] = {}
    for rec in records:
        counts[rec["id"]] = counts.get(rec["id"], 0) + 1
        if counts[rec["id"]] > 1:
            rec["id"] = f"{rec['id']}-{counts[rec['id']]}"

    report_counters(counters, len(records))
    return records


def report_counters(counters: dict, total: int) -> None:
    print("\nCleaning")

    if counters["website_fixed"]:
        note(f"website: prefixed https:// on {counters['website_fixed']} bare host(s)")
    if counters["zip_padded"]:
        warn(f"zip_code: {len(counters['zip_padded'])} ZIP(s) had a lost leading zero restored: "
             f"{counters['zip_padded'][:3]}")
    if counters["maps_generated"]:
        note(f"google_maps_url: generated from coordinates for {counters['maps_generated']} row(s)")
    if counters["maps_disagree"]:
        warn(f"google_maps_url: {len(counters['maps_disagree'])} stored URL(s) point somewhere other "
             f"than the Latitude/Longitude columns. The columns were trusted and the URL rebuilt: "
             f"{counters['maps_disagree'][:5]}")
    if counters["coords_missing"]:
        warn(f"{len(counters['coords_missing'])} row(s) have no coordinates and will not plot: "
             f"{counters['coords_missing'][:5]}")
    if counters["state_other"]:
        note(f"{len(counters['state_other'])} row(s) outside {EXPECTED_STATE}: "
             f"{counters['state_other'][:5]}")
    if counters["tier_unclassified"]:
        warn(f"tier: {len(counters['tier_unclassified'])} of {total} rows have a blank "
             f"Tier Classification and publish as Unclassified: "
             f"{counters['tier_unclassified'][:5]}")
    if counters["phrases_unmapped"]:
        warn(f"the capability mapping sheet does not list {len(counters['phrases_unmapped'])} "
             f"phrase(s) that appear in the data, so those contribute no key. Rerun "
             f"make_capability_map.py with --existing to keep your approvals and pick up the new "
             f"phrases. Examples: {sorted(counters['phrases_unmapped'])[:3]}")
    covered = total - len(counters["no_capability_keys"])
    if counters["no_capability_keys"]:
        note(f"capability keys: {covered} of {total} suppliers ({covered / total * 100:.0f}%) carry "
             f"at least one key and appear in the fab cutaway; {len(counters['no_capability_keys'])} "
             f"carry none and will not light a zone")


# ===========================================================================
# 5.  VALIDATION
# ===========================================================================

def validate(records: list[dict], frame: pd.DataFrame, mapping: dict[str, str]) -> None:
    print("\nValidating")

    if EXPECTED_ROWS is not None and len(records) != EXPECTED_ROWS:
        warn(f"{len(records)} rows published, expected {EXPECTED_ROWS}. Confirm this is intentional.")
    else:
        note(f"{len(records)} rows published")

    # Hard privacy gate. Must never trip, which is why it aborts.
    blocked = {norm(b) for b in BLOCKED_COLUMNS}
    leaked = [k for k in PUBLISHED_FIELDS if norm(k) in blocked]
    leaked += [k for k in WITHHELD_FIELDS if any(k in r for r in records)]
    if leaked:
        fail(f"withheld field(s) {leaked} reached the published schema. Build aborted.")
    unmapped = [c for c in frame.columns if c not in mapping]
    note(f"privacy gate clear: {len(mapping)} column(s) mapped, {len(unmapped)} columns withheld")
    if WITHHELD_FIELDS:
        note(f"fields withheld by switch and absent from every record: {WITHHELD_FIELDS}")

    names = [r["supplier_name"].strip().lower() for r in records]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        warn(f"duplicate supplier_name value(s): {dupes}. Each still got a unique id, but confirm "
             f"these are distinct businesses rather than a double entry.")

    ids = [r["id"] for r in records]
    if len(set(ids)) != len(ids):
        fail("id collision after disambiguation, which should be impossible. Build aborted.")
    note("every row has a unique id")

    for key in ("supplier_name", "industry_sector", "primary_capability", "latitude"):
        missing = sum(1 for r in records if r.get(key) in (None, [], ""))
        if missing:
            warn(f"{key}: {missing} row(s) blank")

    if PUBLISH["capability_statement_url"]:
        hosts = {
            re.sub(r"^https?://([^/]+).*", r"\1", r["capability_statement_url"])
            for r in records if r.get("capability_statement_url")
        }
        present = sum(1 for r in records if r.get("capability_statement_url"))
        if hosts:
            warn(f"capability_statement_url: {present} of {len(records)} rows populated, hosted on "
                 f"{sorted(hosts)}. Confirm these links open without a sign in before publishing, "
                 f"or the dashboard will show links that most visitors cannot follow.")

    # Facet hygiene: a filter with many one member categories is unusable.
    sectors: dict[str, int] = {}
    for r in records:
        if r["industry_sector"]:
            sectors[r["industry_sector"]] = sectors.get(r["industry_sector"], 0) + 1
    singletons = sorted(s for s, n in sectors.items() if n == 1)
    if len(singletons) > 5:
        warn(f"industry_sector has {len(sectors)} distinct values, {len(singletons)} of them with a "
             f"single supplier. Consider consolidating in the master before building a filter: "
             f"{singletons[:8]}")

    if PUBLISH["contact_email"]:
        emails = sum(1 for r in records if r.get("contact_email"))
        warn(f"contact_email: {emails} address(es) will be published on a world readable page and "
             f"will be scraped. Set PUBLISH['contact_email'] = False to withhold, or serve a "
             f"contact form instead.")
    else:
        note("contact_email withheld. contact_name is still published; set its switch to False "
             "as well if named individuals should not appear either.")


# ===========================================================================
# 6.  BUILD
# ===========================================================================

def inspect(source: Path) -> None:
    workbook = pd.ExcelFile(source)
    print(f"\n{source.name}  —  {len(workbook.sheet_names)} sheet(s)\n")
    for name in workbook.sheet_names:
        frame = workbook.parse(name, nrows=5)
        print(f"  [{name}]  {len(frame.columns)} columns")
        for col in frame.columns:
            print(f"      - {col}")
        print()


def build(source: Path, json_out: Path, tableau_dir: Path, strict: bool) -> None:
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    workbook = pd.ExcelFile(source)
    sheet = resolve_sheet(workbook)

    print(f"\nReading {source}")
    print(f"  sheet  {sheet}")
    print(f"  sha256 {digest[:16]}")

    frame = workbook.parse(sheet).dropna(how="all")
    mapping = resolve_columns(frame)
    capability_map, known_phrases = load_capability_map(workbook)
    records = build_records(frame, mapping, capability_map, known_phrases)
    validate(records, frame, mapping)

    if strict and PROBLEMS:
        fail(f"{len(PROBLEMS)} warning(s) with --strict enabled. Nothing was written.")

    stamp = datetime.now(timezone.utc)
    data_version = f"{stamp:%Y%m%d}-{digest[:8]}"

    withheld = sorted(k for k, v in PUBLISH.items() if not v)
    payload = {
        "meta": {
            "data_version": data_version,
            "generated_utc": stamp.isoformat(timespec="seconds"),
            "source_file": source.name,
            "source_sheet": sheet,
            "source_sha256": digest,
            "row_count": len(records),
            "field_map": FIELD_MAP,
            "fields_withheld": withheld,
            "tier_names": {str(k): v for k, v in TIER_NAMES.items()},
            "capability_names": CAPABILITY_NAMES,
            "capability_map_rows": len(capability_map),
            "suppliers_with_capability_keys": sum(1 for r in records if r["capability_keys"]),
            "warning_count": len(PROBLEMS),
            "disclaimer": (
                "Supplier records were compiled by web scraping and have not been fully "
                "validated. Verify any field before outreach or publication."
            ),
        },
        "suppliers": records,
        "facets": {
            "industry_sector": sorted({r["industry_sector"] for r in records if r["industry_sector"]}),
            "supplier_type": sorted({t for r in records for t in r["supplier_type_list"]}),
            "county": sorted({r["county"] for r in records if r["county"]}),
            "state": sorted({r["state"] for r in records if r["state"]}),
            "tier": [
                {"level": level, "label": TIER_NAMES[level],
                 "count": sum(1 for r in records if r["tier"] == level)}
                for level in sorted(TIER_NAMES)
                if any(r["tier"] == level for r in records)
            ],
            "capability_keys": sorted({k for r in records for k in r["capability_keys"]}),
        },
    }

    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {json_out}  ({json_out.stat().st_size / 1024:.0f} KB)")

    # Tableau gets the same records, so the two dashboards cannot disagree.
    tableau_dir.mkdir(parents=True, exist_ok=True)
    flat = pd.DataFrame([
        {
            key: ("; ".join(value) if isinstance(value, list) else value)
            for key, value in record.items()
            if not key.endswith("_list")
        }
        for record in records
    ])
    flat["Data Version"] = data_version
    tableau_path = tableau_dir / "suppliers.xlsx"
    flat.to_excel(tableau_path, index=False, sheet_name="Suppliers")
    print(f"Wrote {tableau_path}  ({len(flat)} rows)")

    # Long format companion so Tableau can count by capability without parsing.
    long_rows = [
        {"id": r["id"], "supplier_name": r["supplier_name"], "tier": r["tier"],
         "tier_label": r["tier_label"], "industry_sector": r["industry_sector"],
         "capability": phrase, "capability_rank": rank,
         "capability_key": capability_map.get(phrase_key(phrase), ""),
         "fab_capability": CAPABILITY_NAMES.get(capability_map.get(phrase_key(phrase), ""), "")}
        for r in records
        for rank, phrase in enumerate(
            r["primary_capability_list"] + r["secondary_capability_list"], 1
        )
    ]
    long_path = tableau_dir / "supplier_capabilities.xlsx"
    pd.DataFrame(long_rows).to_excel(long_path, index=False, sheet_name="Capabilities")
    print(f"Wrote {long_path}  ({len(long_rows)} rows)")

    report = [
        "Fab Supplier SCAN build report",
        f"generated_utc   {payload['meta']['generated_utc']}",
        f"data_version    {data_version}",
        f"source_file     {source.name}",
        f"source_sheet    {sheet}",
        f"source_sha256   {digest}",
        f"rows published  {len(records)}",
        "",
        f"fields published ({len(FIELD_MAP)})",
        *[f"  {h:32} -> {k}" for h, k in FIELD_MAP.items()],
        "",
        f"fields withheld by switch: {', '.join(withheld) if withheld else 'none'}",
        f"columns withheld entirely: {len([c for c in frame.columns if c not in mapping])}",
        "",
        f"warnings ({len(PROBLEMS)})",
        *([f"  - {w}" for w in PROBLEMS] if PROBLEMS else ["  none"]),
    ]
    Path("build_report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("Wrote build_report.txt")

    print(f"\nDone. data_version = {data_version}  warnings = {len(PROBLEMS)}")
    print("Next: commit the JSON, then refresh and republish Tableau Public in the same sitting.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build dashboard data from the master Excel workbook.")
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="path to the master .xlsx")
    parser.add_argument("--out", default=DEFAULT_JSON_OUT, help="path for data.json")
    parser.add_argument("--tableau", default=DEFAULT_TABLEAU_DIR, help="directory for Tableau .xlsx files")
    parser.add_argument("--inspect", action="store_true", help="list sheets and columns, then exit")
    parser.add_argument("--strict", action="store_true", help="treat warnings as fatal")
    args = parser.parse_args()

    source = Path(args.source)
    if not source.exists():
        fail(f"source workbook not found: {source.resolve()}")

    if args.inspect:
        inspect(source)
        return

    build(source, Path(args.out), Path(args.tableau), args.strict)


if __name__ == "__main__":
    main()
