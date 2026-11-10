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
