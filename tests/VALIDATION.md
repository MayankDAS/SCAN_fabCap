# SCAN Dashboard validation tests

Every check this dashboard needs before a change goes live, with how to run it and what a pass looks like.
The dashboard is `index.html` on GitHub Pages, fed by `data/suppliers.json`, beside an embedded Tableau Public dashboard fed by `tableau/*.xlsx`. All three are built from one master workbook that is never committed.

Last verified: 19 Nov 2026, against `main` at `f1b6a6a` and data version `20261110-f09cfd72`.

## When to run what

| Trigger | Run |
|---|---|
| Any change to `index.html` | A (automated page tests), V, B, K, then G after merging |
| Any data rebuild from the master workbook | S, D (automated data checks), C, then G |
| New capability phrases in the workbook, or a review of the mapping | M |
| Tableau workbook republished on Tableau Public | L and C |
| Before every merge to `main` | A and D must pass with no failures |
| After every merge to `main` | G, then L on the live site |
| Monthly | L, C and T, even with no changes, to catch drift in Tableau or the data |

## How to run the automated suites

```bash
# from the repo root
python3 tests/check_published_data.py          # D: data and privacy checks, about 2 seconds

python3 -m http.server 8740 &                  # A: page tests need the page served over http
npm install puppeteer-core
CHROME="/path/to/chrome" node tests/tableau_link/link.test.mjs
```

Both print one line per check and exit with code 1 if anything fails. Suite A replaces the Tableau Embedding API with `tests/tableau_link/tableau_mock.mjs`, so it runs offline and does **not** prove the live dashboard works; L does that.

---

## A. Automated page tests: 77 checks

`tests/tableau_link/link.test.mjs`, run in headless Chrome. Expected: **77 passed, 0 failed.** The names below are the exact lines the suite prints.

**Finding companies and sectors in the dashboard**
- finds the map sheet as the company sheet, ignores the bar chart's CNT(supplier_name)
- matches every distinct supplier name (154; two rows share the name Peerless)
- matches on the published id field, all 155 including both Peerless rows
- names that differ in case, spacing and Inc still match
- highlight sends the dashboard's own spelling
- finds the sector bar sheet and matches every sector
- an 'Industry Group' field in capitals still matches the page's sectors
- with only sector bars, says sectors work and companies don't
- with only sector bars, a bar still lists its suppliers
- ?scandebug=1 lists every worksheet, its company and sector fields

**Page to dashboard**
- embeds the configured view with the toolbar at the bottom
- shows the linked status line
- search pick highlights the company in the map
- page still shows the searched company
- status confirms the highlight
- a capability result leaves the dashboard alone; a sector result selects its bar
- a company from search clears the bar before highlighting the map
- a search made before the dashboard is ready is applied when it loads
- a sector chosen early is highlighted once the dashboard loads
- a company missing from the dashboard says so
- Reset clears the map and the sector bar
- status returns to idle after reset

**Dashboard to page**
- clicking a company in the map opens it on the page
- status says where the details are
- a multiple selection asks for a single company
- clearing the selection in the map leaves the page alone
- a dashboard company missing from the page says so
- clicking a sector bar lists that sector's suppliers
- bar click lists the sector
- status says how many suppliers are listed
- several bars ask for a single sector
- several bars leave the current list alone
- a company click on the map still opens the company, not its sector
- a capitalised sector opens the page's sector
- an unknown sector says so

**Sector list, company and Back**
- a company name in the sector list opens its details
- it highlights the company on the map and leaves the sector bar selected
- a Back button names the sector list
- Back returns to the sector list
- Back restores the list's scroll position
- Back clears the company highlight and keeps the bar selected
- clicking the bar again resets the details panel
- and clears the company highlight on the map
- status says why the panel reset
- a capability pick leaves no Back link

**No feedback loops** (the page must not read its own selections back as visitor clicks)
- own highlight is not echoed back as a visitor click
- a click from the map is not pushed back to the map
- a sector click is not pushed back to the dashboard
- the bar's own selection event is not read back as a click
- page is not reset by the map selection's own events
- the page's own bar clear does not reset the panel

**Fallback: the dashboard must never disappear**
- API library fails to load: falls back to the standard embed
- API library fails to load: says the link is off
- API library fails to load: search still works with no errors
- API library has unexpected contents: falls back to the standard embed
- API library has unexpected contents: says the link is off
- API library has unexpected contents: search still works with no errors
- Tableau reports a load error: falls back to the standard embed
- Tableau reports a load error: says the link is off
- Tableau reports a load error: search still works with no errors

**Layout**
- one screen layout: details visible beside the map, no shortcut, no page jump
- stacked layout: Go to it scrolls to the details
- the viz still sits inside #viz1795017713550, which the engagement tracker watches

**How to use cards**
- Tableau help opens as a card over the panel
- the dashboard keeps its exact size and place while help is open
- Escape closes it and returns focus to the button
- only one help card is open at a time
- a click elsewhere closes it
- its close button closes it
- search help no longer pushes the search box down

**Tracking**
- map clicks are logged to Clarity with the company
- sector clicks are logged to Clarity
- list opens and bar clears are logged

**Errors**
- no page errors in the main run
- no page errors in the queued run
- no page errors in the sector flow
- no page errors with help cards

---

## D. Automated data and privacy checks: 26 checks

`tests/check_published_data.py`. Expected: **0 failed.** Warnings are reported but do not fail the run.

| ID | Check | Fails when |
|---|---|---|
| D1 | `suppliers.json` loads and its meta has data_version, source_sha256, row_count, tier_names, capability_names | the build was interrupted or edited by hand |
| D2 | meta.row_count equals the number of records | records were added or removed by hand |
| D3 | every record has a unique id | ids collided |
| D4 | no withheld field in any record (contact_email, phone, supplier id, revenue, certifications, match scores, NAICS, update date) | a publication switch was flipped or the field map changed |
| D5 | no email address anywhere in `suppliers.json` | an address slipped into any field |
| D6 | every record has a supplier name | a blank row was published |
| D7 | coordinates within range; D7b warns on records without coordinates | a geocode is wrong; D7b: a supplier will not plot |
| D8 | tier and tier label agree with meta.tier_names | tier mapping drifted |
| D9 | every capability key is one of the 18 known keys | the mapping sheet has a typo |
| D10 | website, map and statement links are https | a bare host or http link was published |
| D11 | ZIP codes are 5 digits with leading zeros kept | Excel stripped a leading zero |
| D12 | at least 70% of suppliers carry a capability key (warning) | the mapping sheet is missing or stale |
| T1, T2 | both Tableau workbooks exist | the build skipped them |
| T3 | `tableau/suppliers.xlsx` has the same ids as `suppliers.json` | the two outputs come from different builds |
| T4 | its Data Version column equals the page's data_version | Tableau files are stale |
| T5, T6 | no withheld column or email address in either Tableau workbook | private data reached Tableau |
| R1 | the master workbook is not committed | someone added it to git |
| R2 | `.gitignore` blocks the master workbook | the guard was removed |
| P1 | `index.html` reads `data/suppliers.json` and that file exists | the data path changed on one side only |
| P2 | `index.html` holds no inline supplier records | old hardcoded data came back |
| P3 | no class name contains "sponsor" | ad blockers would hide that element, as they did the sponsor logos on 19 Nov 2026 |
| P4 | the Tableau container keeps id `viz1795017713550` | the engagement tracker loses the dashboard |
| P5 | GoatCounter is loaded exactly once (warning) | page views may double count |

To prove the suite still catches problems, plant one (an email in a record, an old Data Version in the Tableau file) in a scratch copy of the repo and confirm it fails with exit code 1.

---

## S. Data build: `scripts/build_dashboard_data.py`

Run against the master workbook:

```bash
python3 scripts/build_dashboard_data.py --source Tech_Hub_Company_Listing.xlsx --out data/suppliers.json --tableau tableau
```

**Always pass `--out data/suppliers.json`.** The script's default is still `docs/data/data.json`, which the page does not read; a build without `--out` leaves the live data unchanged. Check P1 then passes against the old file, so watch the build output for the path it wrote.

**Stops the build** (nothing written):
| ID | Condition |
|---|---|
| S1 | source workbook not found |
| S2 | the Capability_Map sheet has no phrase column |
| S3 | a supplier's coordinates are out of range |
| S4 | a withheld field reached the published schema |
| S5 | two records ended up with the same id |
| S6 | any warning while `--strict` is set |

**Reported as warnings** (read every one in `build_report.txt`):
| ID | Warning | Action |
|---|---|---|
| S7 | no Capability_Map sheet | paste it from `data/capability_map.xlsx`, or the cutaway lights nothing |
| S8 | mapping rows naming an unknown key, or one phrase mapped two ways | fix the Approved Key column |
| S9 | the mapping sheet does not list some phrases | rerun step M |
| S10 | columns missing from the workbook | a column was renamed in the master |
| S11 | ZIP leading zeros restored | expected; confirm the ZIPs |
| S12 | stored Google Maps URLs point away from the coordinates | the coordinates win; fix the URL in the master |
| S13 | rows without coordinates | they will not plot |
| S14 | blank Tier Classification | they publish as Unclassified |
| S15 | row count differs from 155 | expected when suppliers are added; update `EXPECTED_ROWS` |
| S16 | duplicate supplier names | confirm they are different businesses (today: two named Peerless) |
| S17 | blank required fields | fill them in the master |
| S18 | capability statement links on buffalo.box.com | confirm they open without a UB sign in |
| S19 | industry sectors with a single supplier | consider merging sectors before they become filters |

Expected today: 155 rows published, 123 with capability keys, and only these known warnings: S11 (EPTAM, THORLABS), S12 (one map URL), S14 (16 Unclassified), S16 (Peerless), S17 (one row blank in sector and primary capability), S18 (32 statements), S19 (11 single supplier sectors). No failures.

---

## M. Capability mapping: `scripts/make_capability_map.py`

| ID | Check | Pass |
|---|---|---|
| M1 | Rerun with `--existing data/capability_map.xlsx` after new phrases appear | approvals carried forward; the count is printed |
| M2 | Review every `low` row, then `medium` | the Approved Key column holds a valid key or stays blank on purpose |
| M3 | Coverage after the build | at least 70% of suppliers carry a key (D12); today 123 of 155 |
| M4 | Unmapped suppliers | only support organizations and non fab services (food, logistics, legal and so on) |

---

## L. Live dashboard, manual: Tableau link

Open the live page with diagnostics: `https://mayankdas.github.io/SCAN_fabCap/?scandebug=1`. Suite A cannot test this; it uses a stand in for Tableau.

| ID | Check | Expected today |
|---|---|---|
| L1 | Diagnostics panel State | **linked**. "standard embed (no link)" means a fallback; the panel gives the reason |
| L2 | Company sheet and field | Companies by Locations, Supplier Name, 137 of 137 matched |
| L3 | Sector sheet and field | Companies by Industry, Industry Group, 8 of 9 matched (Other has no page sector; see open issues) |
| L4 | Search a company and choose it | it highlights on the Tableau map; the status line confirms |
| L5 | Click one company on the map | its details open in the search panel |
| L6 | Click one industry bar | that sector's suppliers list in the search panel |
| L7 | In that list, click a company name | details open with Back to <sector> suppliers, and the map highlights it |
| L8 | Back | the list returns at the same scroll position |
| L9 | Click the selected bar again | the details panel resets to No selection yet and the map highlight clears |
| L10 | Search a sector and choose it | its bar is selected in Tableau |
| L11 | Reset button | page, map highlight and bar selection all clear |
| L12 | Status line under the dashboard | says what happened after each step, never stale |

---

## C. Consistency between Tableau and the page

| ID | Check | Expected |
|---|---|---|
| C1 | Tableau's company count against `meta.row_count` | equal. **Fails today: 137 against 155** |
| C2 | Per sector, a bar's count against the list a click produces | equal. **Fails today**, for example Advanced Manufacturing 46 against 50 |
| C3 | The Tableau data source | `tableau/suppliers.xlsx` from the latest build, extract refreshed, workbook republished |
| C4 | Tableau's Data Version against the page footer's data version | identical |

C1 and C2 pass only once the Tableau workbook reads `tableau/suppliers.xlsx` and is republished after each build. Tableau Public does not refresh an Excel source on its own.

---

## V. Layout and visual, manual

Check at 1920×1080, 1440×900, 1366×768, a tablet and a phone, in light and dark themes.

| ID | Check | Expected |
|---|---|---|
| V1 | Grid | hero on top; search and details beside Tableau; fab perspective beside capability groups |
| V2 | Tableau row height | about two thirds of the window at 1100px wide and up (647px at 1920×1080) |
| V3 | Cutaway | original proportions (1962×1466, ratio 1.338); numbered markers sit on their zones |
| V4 | Each panel | scrolls on its own; picking something never jumps the page unless the details heading is off screen |
| V5 | Under 1100px wide | panels stack in the sketch's order |
| V6 | Hero | title, both logos visible and linked, theme button not overlapping the logos |
| V7 | How to use cards | float over their panel; the panel behind does not move |
| V8 | Company details | Capability statement directly after Website, wherever present |
| V9 | Empty details panel | No selection yet, then: Companies, industry sectors and capability details appear here upon selection. |
| V10 | Dark theme | every panel, card and logo readable |

---

## B. Browsers and environment, manual

| ID | Environment | Why |
|---|---|---|
| B1 | Chrome | most visitors |
| B2 | Safari on Mac and iPhone | different layout engine |
| B3 | Firefox | different layout engine |
| B4 | **Any browser with an ad blocker on** | ad blockers hid both sponsor logos on 19 Nov 2026; automated tests run without one |
| B5 | A narrow phone | stacked layout, help cards fit the screen |
| B6 | Page opened from disk (file://) | shows a clear message to serve it over http instead of a blank panel |

---

## K. Accessibility, manual

| ID | Check | Expected |
|---|---|---|
| K1 | Tab through the page | every control reachable in a sensible order with a visible focus ring |
| K2 | Enter and Space | open capabilities, zones, companies, Back and How to use |
| K3 | Escape | closes a help card and returns focus to its button |
| K4 | Screen reader | logos announce their organization and that they open a new tab; status line announces changes |
| K5 | Reduced motion setting | no smooth scrolling or animated transitions |
| K6 | 200% browser zoom | nothing clipped or overlapping |

---

## T. Measurement, manual

Confirm in Microsoft Clarity after using the live page (events can take a few hours to appear).

| ID | Events and tags | Fired by |
|---|---|---|
| T1 | `tableau_engaged` | the first click into the Tableau dashboard (focus moves into its frame inside `#viz1795017713550`) |
| T2 | `tableau_mark_click` with tag `tableau_company` | a company click in Tableau |
| T3 | `tableau_sector_click` with tag `tableau_sector`; `tableau_sector_clear` | a bar click; deselecting the bar |
| T4 | `search_result_open` with tag `last_search_type` | choosing a search result |
| T5 | `company_detail_view` (expanding a supplier row or opening one from a sector list), `sector_list_company_open`, `statement_open`, `website_link_click` | company navigation and outbound links |
| T6 | `capability_select` with tag `last_capability`, `zone_select` with tag `last_zone`, `view_cut` / `view_list`, `reset`, `about_open`, `theme_toggle`, `task_success` | the rest of the page |
| T7 | GoatCounter | one page view per visit (see P5) |
| T8 | Daily visitor metrics workflow | last run succeeded in the repo's Actions tab |

---

## G. Deployment, manual

| ID | Check | Expected |
|---|---|---|
| G1 | Actions tab: pages build and deployment for the merge commit | completed, success, usually within 2 minutes |
| G2 | Live page footer | the new data version |
| G3 | Hard reload of the live page | the new version, not a cached one |
| G4 | Branch previews | raw.githack.com caches for a few minutes; a commit pinned address is always fresh |

---

## Open issues found by these checks

| Issue | Check | Fix |
|---|---|---|
| Tableau reads 137 companies, the page 155 | C1, C2 | point the Tableau workbook at `tableau/suppliers.xlsx`, refresh, republish |
| The Other bar lists nothing | L3 | map Other to the small sectors it groups, or rename the group in Tableau |
| GoatCounter loads twice | P5 | remove the duplicate script tag |
| The build's default output path is not the page's data path | S | change the default to `data/suppliers.json` |
| County blank for 68 of 155 suppliers | S17 | fill in the master workbook |
| 32 capability statements on buffalo.box.com may need a UB sign in | S18 | confirm public access or host them elsewhere |
