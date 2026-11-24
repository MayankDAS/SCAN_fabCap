#!/usr/bin/env python3
"""
One step data update for the SCAN dashboard.

Reads the private master Google Sheet, runs the normal build
(build_dashboard_data.py: cleaning, privacy filter, capability mapping), then

  1. writes data/suppliers.json and tableau/*.xlsx in the repo, which the
     GitHub workflow commits so the HTML dashboard updates, and
  2. writes the approved public fields to the "published" Google Sheet, which
     Tableau Public reads and refreshes from once a day.

Only the published sheet ever reaches Tableau, so withheld columns (contact
email, revenue, certifications, match scores) stay in the private master.

Environment (set as GitHub secrets):
  GOOGLE_SERVICE_ACCOUNT_JSON  the service account key, whole JSON text
  MASTER_SHEET_ID              id of the private master sheet
  PUBLISHED_SHEET_ID           id of the sheet Tableau Public connects to

Local test without Google:
  python scripts/sync_google.py --local-xlsx Tech_Hub_Company_Listing.xlsx --no-publish
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import openpyxl
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
JSON_OUT = ROOT / "data" / "suppliers.json"
TABLEAU_DIR = ROOT / "tableau"
REPO_MAP = ROOT / "data" / "capability_map.xlsx"
MAP_SHEET = "Capability_Map"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly",
          "https://www.googleapis.com/auth/spreadsheets"]

# Column names in the published sheet. They follow the master's own headers so
# Tableau fields keep familiar names (and the page's link finds "Supplier Name"
# and "Tier Classification").
SUPPLIER_COLUMNS = {
    "id": "Supplier ID", "supplier_name": "Supplier Name", "tier": "Tier Level",
    "tier_label": "Tier Classification", "address": "Address", "city": "City",
    "county": "County", "state": "State", "zip_code": "ZIP Code",
    "supplier_type": "Supplier Type", "industry_sector": "Industry Sector",
    "primary_capability": "Primary Capability", "secondary_capability": "Secondary Capabilities",
    "capability_keys": "Fab Capability Keys", "website": "Website", "website_display": "Website Display",
    "contact_name": "Contact Name", "google_maps_url": "Google Maps URL", "latitude": "Latitude",
    "longitude": "Longitude", "capability_statement_url": "Capability Statement URL",
    "Data Version": "Data Version",
}
CAPABILITY_COLUMNS = {
    "id": "Supplier ID", "supplier_name": "Supplier Name", "tier": "Tier Level",
    "tier_label": "Tier Classification", "industry_sector": "Industry Sector",
    "capability": "Capability", "capability_rank": "Capability Rank",
    "capability_key": "Fab Capability Key", "fab_capability": "Fab Capability",
}


def say(msg: str) -> None:
    print(msg, flush=True)


def stop(msg: str) -> None:
    print(f"\nERROR  {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


# ---------- Google ----------
def google_session():
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        stop("GOOGLE_SERVICE_ACCOUNT_JSON is not set.")
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        stop("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON. Paste the whole key file into the secret.")
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    say(f"  signed in as {info.get('client_email')}")
    return AuthorizedSession(creds)


def check(resp, what: str):
    if resp.status_code == 404:
        stop(f"{what}: not found. Check the sheet id and that the sheet is shared with the service account email.")
    if resp.status_code == 403:
        stop(f"{what}: permission denied. Share the sheet with the service account email "
             f"(Viewer for the master, Editor for the published sheet) and enable the Drive and Sheets APIs.")
    if not resp.ok:
        stop(f"{what}: HTTP {resp.status_code} {resp.text[:300]}")
    return resp


def download_master(session, file_id: str, dest: Path) -> None:
    meta = check(session.get(f"https://www.googleapis.com/drive/v3/files/{file_id}",
                             params={"fields": "name,mimeType,modifiedTime", "supportsAllDrives": "true"}),
                 "Master sheet").json()
    say(f"  master: {meta.get('name')}  (last edited {meta.get('modifiedTime')})")
    if meta.get("mimeType") == "application/vnd.google-apps.spreadsheet":
        resp = session.get(f"https://www.googleapis.com/drive/v3/files/{file_id}/export",
                           params={"mimeType": XLSX_MIME})
    else:  # an .xlsx uploaded to Drive without converting
        resp = session.get(f"https://www.googleapis.com/drive/v3/files/{file_id}",
                           params={"alt": "media", "supportsAllDrives": "true"})
    dest.write_bytes(check(resp, "Downloading the master").content)


def cell(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    if hasattr(v, "item"):          # numpy scalar
        v = v.item()
    return v


def publish(session, sheet_id: str, tabs: dict[str, pd.DataFrame]) -> None:
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}"
    info = check(session.get(base, params={"fields": "properties.title,sheets.properties.title"}), "Published sheet").json()
    have = {s["properties"]["title"] for s in info.get("sheets", [])}
    add = [{"addSheet": {"properties": {"title": t}}} for t in tabs if t not in have]
    if add:
        check(session.post(f"{base}:batchUpdate", json={"requests": add}), "Adding tabs")
    for title, frame in tabs.items():
        rng = f"'{title}'"
        check(session.post(f"{base}/values/{rng}:clear", json={}), f"Clearing {title}")
        values = [list(frame.columns)] + [[cell(v) for v in row] for row in frame.itertuples(index=False)]
        check(session.put(f"{base}/values/{rng}!A1", params={"valueInputOption": "RAW"},
                          json={"values": values}), f"Writing {title}")
        say(f"  published sheet: {title}  {len(frame)} rows")


# ---------- build ----------
def add_repo_map_if_missing(xlsx: Path) -> None:
    wb = openpyxl.load_workbook(xlsx)
    if any(n.lower().replace(" ", "_") == MAP_SHEET.lower() for n in wb.sheetnames):
        say("  capability map: from the master's own Capability_Map tab")
        return
    if not REPO_MAP.exists():
        say("  capability map: none found; zones will not light")
        return
    src = openpyxl.load_workbook(REPO_MAP, read_only=True).worksheets[0]
    ws = wb.create_sheet(MAP_SHEET)
    for row in src.iter_rows(values_only=True):
        ws.append(row)
    wb.save(xlsx)
    say("  capability map: master has no Capability_Map tab, using data/capability_map.xlsx from the repo")


def suppliers_only(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("suppliers")
    except (OSError, ValueError):
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--local-xlsx", help="use this workbook instead of downloading the master")
    ap.add_argument("--no-publish", action="store_true", help="do not write the published Google Sheet")
    ap.add_argument("--force", action="store_true", help="publish even if the supplier data did not change")
    args = ap.parse_args()

    session = None
    if not args.local_xlsx or not args.no_publish:
        say("Google")
        session = google_session()

    before = suppliers_only(JSON_OUT)
    with tempfile.TemporaryDirectory() as tmp:
        xlsx = Path(tmp) / "master.xlsx"
        say("\nMaster")
        if args.local_xlsx:
            xlsx.write_bytes(Path(args.local_xlsx).read_bytes())
            say(f"  local file {args.local_xlsx}")
        else:
            download_master(session, os.environ.get("MASTER_SHEET_ID", "").strip() or stop("MASTER_SHEET_ID is not set."), xlsx)
        add_repo_map_if_missing(xlsx)

        say("\nBuild")
        run = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_dashboard_data.py"),
                              "--source", str(xlsx), "--out", str(JSON_OUT), "--tableau", str(TABLEAU_DIR)],
                             cwd=ROOT)
        if run.returncode != 0:
            stop("the build found a problem (see above), so nothing was published.")

    after = suppliers_only(JSON_OUT)
    changed = before != after
    if not changed and not args.force:
        # Re-exporting the sheet changes its bytes and so the data version, even
        # when no supplier changed. Put the old files back so nothing is committed.
        subprocess.run(["git", "checkout", "--", "data/suppliers.json", "tableau"], cwd=ROOT, check=False)
        say("\nNo supplier changes since the last update. Nothing to publish.")
        Path(os.environ.get("GITHUB_OUTPUT", os.devnull)).open("a").write("changed=false\n")
        return

    say(f"\nSupplier data changed: {len(before or [])} rows before, {len(after or [])} now.")
    if not args.no_publish:
        sheet_id = os.environ.get("PUBLISHED_SHEET_ID", "").strip() or stop("PUBLISHED_SHEET_ID is not set.")
        say("\nPublished sheet")
        publish(session, sheet_id, {
            "Suppliers": pd.read_excel(TABLEAU_DIR / "suppliers.xlsx", dtype={"zip_code": str}).rename(columns=SUPPLIER_COLUMNS),
            "Capabilities": pd.read_excel(TABLEAU_DIR / "supplier_capabilities.xlsx").rename(columns=CAPABILITY_COLUMNS),
        })
    Path(os.environ.get("GITHUB_OUTPUT", os.devnull)).open("a").write("changed=true\n")
    say("\nDone. The workflow commits the new page data; Tableau Public picks up the published sheet on its next daily refresh.")


if __name__ == "__main__":
    main()
