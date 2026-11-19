#!/usr/bin/env python3
"""
check_published_data.py  —  validation of everything this repo publishes

Checks the files GitHub Pages and Tableau Public serve, not the master
workbook (which is never committed). Run it from the repo root after every
data rebuild and before every merge to main:

    python3 tests/check_published_data.py

Exit code 0 means no failures. Warnings are printed but do not fail the run.
Requires: pandas, openpyxl
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SUPPLIERS = ROOT / "data" / "suppliers.json"
TABLEAU_SUPPLIERS = ROOT / "tableau" / "suppliers.xlsx"
TABLEAU_CAPS = ROOT / "tableau" / "supplier_capabilities.xlsx"
PAGE = ROOT / "index.html"

# Fields that must never be published, by published name or by source column name.
WITHHELD = {"contact_email", "contact_phone", "supplier_id", "employee_count", "revenue_range",
            "certification_status", "iso_9001", "as9100", "itar", "cmmc", "match_score",
            "match_category", "naics_code", "updated_date"}
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", re.I)

results: list[tuple[str, str, str]] = []   # (status, id, message)


def check(cid: str, ok: bool, message: str, warn_only: bool = False) -> None:
    results.append(("PASS" if ok else ("WARN" if warn_only else "FAIL"), cid, message))


def emails_in_xlsx(path: Path) -> set[str]:
    found: set[str] = set()
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if name.endswith(".xml"):
                found |= set(EMAIL.findall(z.read(name).decode("utf8", "ignore")))
    return found


def main() -> int:
    # ---------- D1 to D12: data/suppliers.json ----------
    try:
        data = json.loads(SUPPLIERS.read_text(encoding="utf8"))
    except Exception as e:  # noqa: BLE001
        check("D1", False, f"data/suppliers.json does not load: {e}")
        return report()
    meta, rows = data.get("meta", {}), data.get("suppliers", [])
    check("D1", all(k in meta for k in ("data_version", "source_sha256", "row_count", "tier_names", "capability_names")),
          "suppliers.json has meta with data_version, source_sha256, row_count, tier_names and capability_names")
    check("D2", meta.get("row_count") == len(rows) and len(rows) > 0, f"row_count {meta.get('row_count')} equals the {len(rows)} records")
    ids = [r.get("id") for r in rows]
    check("D3", len(ids) == len(set(ids)) and all(ids), "every record has a unique id")
    keys = {k for r in rows for k in r}
    leaked = sorted(k for k in keys if k.lower() in WITHHELD)
    check("D4", not leaked, f"no withheld field in any record{': found ' + str(leaked) if leaked else ''}")
    raw = SUPPLIERS.read_text(encoding="utf8")
    mails = sorted(set(EMAIL.findall(raw)))
    check("D5", not mails, f"no email address anywhere in suppliers.json{': found ' + str(mails[:3]) if mails else ''}")
    check("D6", all(r.get("supplier_name") for r in rows), "every record has a supplier_name")
    bad_xy = [r["supplier_name"] for r in rows if r.get("latitude") is not None and not (-90 <= r["latitude"] <= 90 and -180 <= r["longitude"] <= 180)]
    no_xy = [r["supplier_name"] for r in rows if r.get("latitude") is None or r.get("longitude") is None]
    check("D7", not bad_xy, f"coordinates in range{': ' + str(bad_xy[:3]) if bad_xy else ''}")
    check("D7b", not no_xy, f"{len(no_xy)} record(s) have no coordinates and will not plot {no_xy[:3]}", warn_only=True)
    tiers = meta.get("tier_names", {})
    bad_tier = [r["supplier_name"] for r in rows if str(r.get("tier")) not in tiers or tiers[str(r.get("tier"))] != r.get("tier_label")]
    check("D8", not bad_tier, f"tier and tier_label agree with meta.tier_names{': ' + str(bad_tier[:3]) if bad_tier else ''}")
    valid = set(meta.get("capability_names", {}))
    bad_caps = sorted({k for r in rows for k in r.get("capability_keys", []) if k not in valid})
    check("D9", not bad_caps, f"every capability key is one of the {len(valid)} known keys{': ' + str(bad_caps) if bad_caps else ''}")
    insecure = [(r["supplier_name"], f) for r in rows for f in ("website", "google_maps_url", "capability_statement_url")
                if r.get(f) and not str(r[f]).startswith("https://")]
    check("D10", not insecure, f"website, map and statement links are https{': ' + str(insecure[:3]) if insecure else ''}")
    bad_zip = [r["supplier_name"] for r in rows if r.get("zip_code") and not re.fullmatch(r"\d{5}(-\d{4})?", r["zip_code"])]
    check("D11", not bad_zip, f"ZIP codes are 5 digits, leading zeros kept{': ' + str(bad_zip[:3]) if bad_zip else ''}")
    mapped = sum(1 for r in rows if r.get("capability_keys"))
    check("D12", mapped / len(rows) >= 0.7, f"{mapped} of {len(rows)} suppliers carry a capability key (expect at least 70%)", warn_only=True)

    # ---------- T1 to T5: Tableau workbooks built in the same run ----------
    for path, cid in ((TABLEAU_SUPPLIERS, "T1"), (TABLEAU_CAPS, "T2")):
        check(cid, path.exists(), f"{path.relative_to(ROOT)} exists")
    if TABLEAU_SUPPLIERS.exists():
        t = pd.read_excel(TABLEAU_SUPPLIERS)
        check("T3", len(t) == len(rows) and set(t["id"]) == set(ids), f"tableau/suppliers.xlsx has the same {len(rows)} ids as suppliers.json ({len(t)} rows)")
        versions = set(t["Data Version"].astype(str)) if "Data Version" in t else set()
        check("T4", versions == {str(meta.get("data_version"))}, f"its Data Version column is {sorted(versions)}, page data is {meta.get('data_version')}")
        leakedt = sorted(c for c in t.columns if c.lower() in WITHHELD)
        check("T5", not leakedt and not emails_in_xlsx(TABLEAU_SUPPLIERS), "no withheld column or email address in tableau/suppliers.xlsx")
    if TABLEAU_CAPS.exists():
        c = pd.read_excel(TABLEAU_CAPS)
        check("T6", set(c["id"]) <= set(ids) and not emails_in_xlsx(TABLEAU_CAPS), "tableau/supplier_capabilities.xlsx ids all exist and it holds no email address")

    # ---------- R1 to R2: repository hygiene ----------
    try:
        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
        masters = [f for f in tracked if re.search(r"Company_Listing.*\.xlsx$", f)]
        check("R1", not masters, f"the master workbook is not committed{': ' + str(masters) if masters else ''}")
    except Exception:  # noqa: BLE001
        check("R1", True, "git not available, master workbook check skipped", warn_only=True)
    check("R2", "Tech_Hub_Company_Listing.xlsx" in (ROOT / ".gitignore").read_text(encoding="utf8"), ".gitignore blocks the master workbook")

    # ---------- P1 to P5: the page ----------
    page = PAGE.read_text(encoding="utf8")
    m = re.search(r'const DATA_URL = "([^"]+)"', page)
    check("P1", bool(m) and (ROOT / m.group(1)).exists(), f"index.html reads {m.group(1) if m else '(no DATA_URL)'} and that file exists")
    check("P2", "const CO = [{" not in page, "index.html holds no inline supplier records")
    sponsor = re.findall(r'class="[^"]*sponsor[^"]*"|\.sponsor[a-z-]*\b', page)
    check("P3", not sponsor, f"no class name contains 'sponsor' (ad blockers hide them){': ' + str(sponsor[:3]) if sponsor else ''}")
    check("P4", 'id="viz1795017713550"' in page, "the Tableau container keeps id viz1795017713550, which the engagement tracker watches")
    gc = page.count("gc.zgo.at/count.js")
    check("P5", gc == 1, f"GoatCounter is loaded {gc} time(s); expect exactly 1 or page views may double count", warn_only=True)

    return report()


def report() -> int:
    width = max(len(r[1]) for r in results) if results else 3
    for status, cid, message in results:
        print(f"{status:4}  {cid:<{width}}  {message}")
    fails = sum(1 for r in results if r[0] == "FAIL")
    warns = sum(1 for r in results if r[0] == "WARN")
    print(f"\n{len(results) - fails - warns} passed, {warns} warning(s), {fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
