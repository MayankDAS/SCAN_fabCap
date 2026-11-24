# SCAN_fabCap

This is a public URL placeholder for Capability statement widget used in SCAN dashboard

Open this URL in any browser - https://mayankdas.github.io/SCAN_fabCap/

# Workflow Runs

##workflow - clarity, goatcounter
  Autoruns twice a day 6:00am EST, 2pm EST. Hits 2 API calls per run. Max day limit 10(clarity)

##metric.html - encrypted by secret

Metrics dashboard: https://mayankdas.github.io/SCAN_fabCap/metrics.html

# Supplier data

`index.html` holds no supplier records. It fetches `data/suppliers.json` at
load, which `scripts/build_dashboard_data.py` generates from the master Excel
workbook in the same run that writes the Tableau workbooks in `tableau/`. That
is what keeps this page and the Tableau viz on one source.

The master workbook is deliberately **not** in this repo. It carries columns
that are withheld from publication (contact emails, revenue bands,
certification status, match scores) and this repo is public. `.gitignore`
blocks it.

## To update the data

1. Edit the master workbook. It is the only file edited by hand.
2. `python scripts/build_dashboard_data.py --source <master>.xlsx --out data/suppliers.json --tableau tableau`
3. Read `build_report.txt`, commit `data/suppliers.json` and the `tableau/` files.
4. Refresh and republish the Tableau Public workbook in the same sitting.
   Tableau Public extracts on publish and does not refresh an Excel source on
   its own, so skipping this is how the two dashboards drift apart.

`data_version` is stamped into `data/suppliers.json`, into the `Data Version`
column of both Tableau workbooks, and into the page footer. Matching footers
mean matching data.

## Capability mapping

The cutaway lights fab zones from 18 fixed capability keys. The workbook
describes suppliers in free text, so `scripts/make_capability_map.py` proposes
a phrase to key mapping and writes `data/capability_map.xlsx` for review. Put
corrections in its `Approved Key` column, which overrides the suggestion, then
paste the sheet into the master workbook as a tab named `Capability_Map`.

Rerun with `--existing data/capability_map.xlsx` to keep approvals when new
phrases appear. Suppliers with no key still appear in search and lists; they
just do not light a zone.

# Export: PDF, email and share

When a company, industry sector, capability or fab zone is selected, the details panel shows **Download PDF** and **Email** buttons (plus **Share** on devices that can share files, such as most phones).

* **Download PDF** saves the list on screen: same companies, same sort order, same "only suppliers with a capability statement" filter. Lists get a summary table and then one detail block per company; a single company gets its profile. Only the public fields the page already shows are printed; contact email stays withheld.
* **Email** saves the PDF and opens a draft in the visitor's mail app with the list, a link back to this exact view and a reminder to attach the saved PDF. A static page cannot send email itself and a mailto link cannot carry an attachment, so attaching is the visitor's step.
* **Share** hands the PDF to the device's share menu (Mail, Messages, Teams and so on).
* Every PDF and email links back with a deep link: `?company=<id>`, `?sector=<name>`, `?capability=<key>` or `?zone=<key>`.
* The PDF libraries (jsPDF 4.2.1 and jsPDF AutoTable 5.0.9, MIT licensed) live in `assets/vendor/` and load only on the first export.
* Clarity events: `pdf_download`, `email_share`, `share_sheet`, with tag `export_kind`.
* Tests: `tests/export/export.test.mjs` (37 checks, headless Chrome plus pypdf).

# Tier tiles: list companies by tier

Clicking a tier tile in the Tableau dashboard (the KPI sheets: Tier 1, Tier 2, Tier 3, Support) lists that tier's suppliers in the details panel, with the usual sort, statement filter and PDF or email export.

* Names in the tier list open the company and highlight it on the map; the tier tile stays selected and a **Back to Tier N suppliers** button returns to the list.
* Clicking the same tile again resets the details panel. Picking another tile replaces the list and clears the previous tile.
* The tile is found by its `Tier Classification` field on any sheet that has no company field, so no Tableau change is needed. Tile labels match the page tier names (`Tier 1`, `Tier 2`, `Tier 3`, `Support Organization`).
* The **Fab** tile lists the fabs themselves (Tier Classification `Fab`, published as tier 5). A tile label the supplier list has no tier for shows a short note instead of a list. Unclassified suppliers have no tile.
* Deep link: `?tier=2` or `?tier=Support%20Organization` opens the list and highlights its tile.
* Clarity events: `tableau_tier_click` (tag `tableau_tier`), `tableau_tier_clear`.
* Tests: `tests/tableau_link/tier.test.mjs` (33 checks; the Fab tile lists the fabs).

# One step data update (Google Sheet)

Edit the **master Google Sheet** only. Then press **Actions > Update data from the master Google Sheet > Run workflow** on GitHub, or wait for the nightly run at about 2 AM Eastern.

The workflow (`.github/workflows/sync-data.yml`, script `scripts/sync_google.py`):
1. downloads the master through a Google service account,
2. runs `build_dashboard_data.py` (cleaning, privacy filter, capability mapping); if the build finds an error, nothing is published and GitHub emails the failure,
3. commits `data/suppliers.json` and `tableau/*.xlsx` to main, so the HTML dashboard updates within minutes,
4. rewrites the **published Google Sheet** (tabs `Suppliers` and `Capabilities`, public fields only). Tableau Public refreshes from it once a day, or at once with **Request update** on the viz page.

If nothing changed for any supplier, the run stops without committing or publishing. The master may hold a `Capability_Map` tab; if not, `data/capability_map.xlsx` from the repo is used.

Secrets (Settings > Secrets and variables > Actions): `GOOGLE_SERVICE_ACCOUNT_JSON`, `MASTER_SHEET_ID`, `PUBLISHED_SHEET_ID`.

Tableau must connect to the **published** sheet, never the master: anyone viewing a Tableau Public dashboard can download its data.
