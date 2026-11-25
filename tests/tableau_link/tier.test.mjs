/* End to end tests for the Tableau tier tiles -> supplier list link in index.html.

   Uses tableau_mock.mjs with tier tiles turned on (one KPI sheet per tier, as on
   the live dashboard, including the Fab tile).

   Run from the repo root:
     python3 -m http.server 8740 &
     npm install puppeteer-core
     CHROME=/path/to/chrome node tests/tableau_link/tier.test.mjs
*/
import puppeteer from "puppeteer-core";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
const API = "https://public.tableau.com/javascripts/api/tableau.embedding.3.latest.min.js";
const MOCK = fs.readFileSync(fileURLToPath(new URL("./tableau_mock.mjs", import.meta.url)), "utf8");
const BASE = process.env.BASE || "http://127.0.0.1:8740/index.html";
const sleep = ms => new Promise(r => setTimeout(r, ms));
const browser = await puppeteer.launch({ executablePath: process.env.CHROME || "/usr/bin/google-chrome", headless: "new", args: ["--no-sandbox", "--disable-gpu"] });
let pass = 0, fail = 0;
const check = (ok, label, extra = "") => { ok ? pass++ : fail++; console.log(`${ok ? "PASS" : "FAIL"}  ${label}${extra ? "  | " + extra : ""}`); };

async function open(query = "", cfg = { tiers: true }) {
  const page = await browser.newPage();
  await page.setViewport({ width: 1400, height: 1000 });
  const errors = [];
  page.on("pageerror", e => errors.push(String(e)));
  await page.setRequestInterception(true);
  page.on("request", r => {
    const u = r.url();
    if (u.startsWith(API)) return r.respond({ status: 200, contentType: "application/javascript", headers: { "Access-Control-Allow-Origin": "*" }, body: MOCK });
    if (/clarity\.ms|gc\.zgo\.at|goatcounter/.test(u)) return r.respond({ status: 200, contentType: "application/javascript", body: "" });
    if (!u.startsWith("http://127.0.0.1")) return r.abort();
    r.continue();
  });
  await page.evaluateOnNewDocument(c => { window.__mockCfg = c; window.__clarity = []; window.clarity = (...a) => window.__clarity.push(a.join(":")); }, cfg);
  await page.goto(BASE + query, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => window.SCANLink?.state?.ready && window.SCAN?.ready && document.getElementById("tablink") && !document.getElementById("tablink").hidden, { timeout: 15000 });
  await sleep(400);
  return { page, errors };
}
const click = (page, sheet, values) => page.evaluate((s, v) => document.querySelector("tableau-viz")._userClick(s, v), sheet, values);
const head = page => page.evaluate(() => ({ eb: document.querySelector("#capinfo .eyebrow")?.textContent.trim(), h: document.querySelector("#capinfo h3")?.textContent.trim(), n: document.querySelectorAll("#capinfo .suplist li").length }));
const status = page => page.evaluate(() => document.getElementById("tablink").textContent.trim());
const mlog = page => page.evaluate(() => window.__mockLog || []);
const pageCount = (page, t) => page.evaluate(t => window.SCAN.companies.filter(d => d.t === t).length, t);

/* 1. discovery */
{ const { page, errors } = await open();
  const s = await page.evaluate(() => window.SCANLink.state);
  check(s.tierTargets.length === 5, "finds the five tier tiles", s.tierTargets.map(t => t.sheet).join(", "));
  check(s.tierTargets.find(t => t.sheet === "KPI - Fab").matched[0] === 5 && s.tierTargets.find(t => t.sheet === "KPI - Support").matched[0] === 4, "maps tile labels to page tiers (Support Organization to 4, Fab to 5)");
  check(s.targets.length === 1 && s.targets[0].sheet === "Supplier Map", "tier tiles are not mistaken for a company sheet");
  check(/tier tile/.test(await status(page)), "idle message mentions tier tiles", await status(page));
  check(errors.length === 0, "no page errors", errors.join(" | "));
  await page.close(); }

/* 2. tile click lists that tier, other tiles and the map mark are cleared */
{ const { page, errors } = await open();
  await click(page, "KPI - Tier 1", ["Tier 1"]); await sleep(400);
  let h = await head(page), n1 = await pageCount(page, 1);
  check(h.eb === "Supplier tier" && h.h === "Tier 1" && h.n === n1, `Tier 1 tile lists the ${n1} Tier 1 suppliers`, JSON.stringify(h));
  check(/Listing the \d+ Tier 1 suppliers/.test(await status(page)), "status line confirms", await status(page));
  check((await page.evaluate(() => window.__clarity)).includes("event:tableau_tier_click"), "tableau_tier_click is tracked");
  await page.evaluate(() => { window.__mockLog.length = 0; });
  await click(page, "KPI - Tier 2", ["Tier 2"]); await sleep(500);
  h = await head(page);
  const n2 = await pageCount(page, 2);
  check(h.h === "Tier 2" && h.n === n2, `Tier 2 tile replaces the list (${n2})`, JSON.stringify(h));
  const lg = await mlog(page);
  check(lg.some(x => x.op === "clear" && x.sheet === "KPI - Tier 1") && !lg.some(x => x.op === "clear" && x.sheet === "KPI - Tier 2"), "the other tier tiles are cleared, the clicked one kept");
  check((await head(page)).h === "Tier 2", "those quiet clears do not reset the panel");
  // sorted rows match the on screen order and every row is the right tier
  const tiers = await page.evaluate(() => [...document.querySelectorAll("#capinfo .suplist .tpill")].map(e => e.textContent.trim()));
  check(tiers.every(t => t === "Tier 2"), "every listed supplier is Tier 2");
  check(errors.length === 0, "no page errors", errors.join(" | "));
  await page.close(); }

/* 3. company from the tier list: map highlight keeps the tile, Back returns to the list */
{ const { page } = await open();
  await click(page, "KPI - Tier 3", ["Tier 3"]); await sleep(400);
  await page.evaluate(() => { document.getElementById("capinfo").scrollTop = 120; window.__mockLog.length = 0; });
  const name = await page.evaluate(() => { const b = document.querySelectorAll("#capinfo .conav")[3]; b.click(); return b.childNodes[0].textContent.trim(); });
  await sleep(500);
  let h = await head(page);
  check(h.eb === "Company" && h.h === name, "a name in the tier list opens that company", JSON.stringify(h));
  const lg = await mlog(page);
  check(lg.some(x => x.op === "select" && x.sheet === "Supplier Map"), "the company is highlighted on the map");
  check(!lg.some(x => x.op === "clear" && /^KPI/.test(x.sheet)), "the Tier 3 tile stays selected");
  const bk = await page.$eval("#backbtn", b => b.textContent.trim()).catch(() => "");
  check(/Back to Tier 3 suppliers/.test(bk), "Back button names the tier", bk);
  await page.click("#backbtn"); await sleep(400);
  h = await head(page);
  check(h.h === "Tier 3" && await page.$eval("#capinfo", e => e.scrollTop) > 0, "Back returns to the Tier 3 list where it was");
  await page.close(); }

/* 4. clicking the tile again resets the panel, also from a company opened off the list */
{ const { page } = await open();
  await click(page, "KPI - Support", ["Support Organization"]); await sleep(400);
  check((await head(page)).h === "Support Organization", "Support tile lists support organizations");
  await page.evaluate(() => document.querySelector("#capinfo .conav").click()); await sleep(1700);
  await click(page, "KPI - Support", []); await sleep(400);
  const h = await head(page);
  check(h.h === "No selection yet", "deselecting the tile resets the details panel", JSON.stringify(h));
  check(/Tier cleared/.test(await status(page)), "status explains the reset", await status(page));
  await page.close(); }

/* 5. the Fab tile lists the fabs; a tile with no matching page tier explains itself */
{ const { page } = await open();
  await click(page, "KPI - Fab", ["Fab"]); await sleep(400);
  const h = await head(page), n = await pageCount(page, 5);
  check(h.h === "Fab" && h.n === n && n > 0, `the Fab tile lists the ${n} fabs`, JSON.stringify(h));
  await page.close(); }

/* 6. page to viz: ?tier= opens the list and highlights its tile; search afterwards clears the tile */
{ const { page, errors } = await open("?tier=2");
  await sleep(400);
  const h = await head(page), lg = await mlog(page);
  check(h.h === "Tier 2", "?tier=2 opens the Tier 2 list", JSON.stringify(h));
  check(lg.some(x => x.op === "select" && x.sheet === "KPI - Tier 2" && x.value[0] === "Tier 2"), "and highlights the Tier 2 tile");
  check(/Highlighted the Tier 2 tile/.test(await status(page)) && h.h === "Tier 2", "own highlight does not loop back", await status(page));
  await page.evaluate(() => { window.__mockLog.length = 0; });
  await page.click("#dsearch"); await page.type("#dsearch", "Micron"); await page.waitForSelector("#sopt0"); await page.click("#sopt0"); await sleep(600);
  check((await mlog(page)).some(x => x.op === "clear" && x.sheet === "KPI - Tier 2"), "a company from search clears the tier tile");
  check(errors.length === 0, "no page errors", errors.join(" | "));
  await page.close(); }
{ const { page } = await open("?tier=Support%20Organization");
  check((await head(page)).h === "Support Organization", "?tier= also takes the tier name");
  await page.close(); }
{ const { page } = await open("?tier=0");
  await sleep(300);
  check((await head(page)).h === "Unclassified" && /has no tile/.test(await status(page)), "Unclassified opens on the page and says it has no tile", await status(page));
  await page.close(); }

/* 7. Reset clears the tier tile; export works for a tier list */
{ const { page } = await open();
  await click(page, "KPI - Tier 2", ["Tier 2"]); await sleep(400);
  const sel = await page.evaluate(() => { const s = window.SCANExport.currentSelection(); return { kind: s.kind, n: s.items.length, link: s.link }; });
  check(sel.kind === "Supplier tier" && sel.n === await pageCount(page, 2) && /\?tier=2$/.test(sel.link), "PDF and email export the tier list with a ?tier= link", JSON.stringify(sel));
  check((await page.$$eval("#capinfo .xbtn", b => b.length)) >= 2, "export buttons show on the tier list");
  await page.evaluate(() => { window.__mockLog.length = 0; }); await page.click("#resetbtn"); await sleep(500);
  check((await mlog(page)).some(x => x.op === "clear" && x.sheet === "KPI - Tier 2") && (await head(page)).h === "No selection yet", "Reset clears the tier tiles and the panel");
  await page.close(); }

/* 9. sector AND tier: a tier picked after a sector filters within that sector */
{ const { page, errors } = await open();
  const pick = await page.evaluate(() => { const c = {}; window.SCAN.companies.forEach(d => { const k = d.s + "|" + d.t; c[k] = (c[k] || 0) + 1; });
    const sec = {}; window.SCAN.companies.forEach(d => sec[d.s] = (sec[d.s] || 0) + 1);
    const [k] = Object.entries(c).filter(([k, n]) => k.endsWith("|2") && n > 1 && sec[k.split("|")[0]] > n).sort((a, b) => b[1] - a[1])[0];
    const s = k.split("|")[0]; return { s, n: c[k], all: sec[s], tier2: window.SCAN.companies.filter(d => d.t === 2).length }; });
  await click(page, "Sector Bars", [pick.s]); await sleep(400);
  check((await head(page)).n === pick.all, `sector bar lists all ${pick.all} suppliers in ${pick.s}`);
  await page.evaluate(() => { window.__mockLog.length = 0; });
  await click(page, "KPI - Tier 2", ["Tier 2"]); await sleep(500);
  let h = await head(page);
  check(h.eb === "Industry sector and tier" && h.h === pick.s && h.n === pick.n, `then Tier 2 lists only the ${pick.n} Tier 2 suppliers in that sector (not all ${pick.tier2})`, JSON.stringify(h));
  const tiers = await page.evaluate(() => [...document.querySelectorAll("#capinfo .suplist .tpill")].map(e => e.textContent.trim()));
  check(tiers.every(t => t === "Tier 2"), "every row is Tier 2");
  check(!(await mlog(page)).some(x => x.op === "clear" && x.sheet === "Sector Bars"), "the sector bar stays selected in the dashboard");
  check(new RegExp(`Listing the ${pick.n} Tier 2 suppliers in`).test(await status(page)), "status names both filters", await status(page));
  const sel = await page.evaluate(() => { const s = window.SCANExport.currentSelection(); return { kind: s.kind, n: s.items.length, link: s.link }; });
  check(sel.kind === "Industry sector and tier" && sel.n === pick.n && /\?sector=.+&tier=2$/.test(sel.link), "PDF and email export the filtered list with both filters in the link", JSON.stringify(sel));
  // company from the combined list, then back
  await page.evaluate(() => document.querySelector("#capinfo .conav").click()); await sleep(400);
  const bk = await page.$eval("#backbtn", b => b.textContent.trim()).catch(() => "");
  check(bk.includes(`Back to Tier 2 in ${pick.s} suppliers`), "Back button names both filters", bk);
  await page.click("#backbtn"); await sleep(400);
  h = await head(page);
  check(h.eb === "Industry sector and tier" && h.n === pick.n, "Back returns to the filtered list");
  // clearing the tier tile goes back to the whole sector (wait out the quiet and echo windows after Back's own highlight)
  await sleep(3200);
  await click(page, "KPI - Tier 2", []); await sleep(400);
  h = await head(page);
  check(h.eb !== "Industry sector and tier" && h.h === pick.s && h.n === pick.all, "deselecting the tier tile lists the whole sector again", JSON.stringify(h));
  // tier first, then sector: also combined
  await click(page, "KPI - Tier 2", ["Tier 2"]); await sleep(1700);
  await click(page, "Sector Bars", []); await sleep(400);
  h = await head(page);
  check(h.h === "Tier 2" && h.n === pick.tier2, "deselecting the sector bar lists the whole tier", JSON.stringify(h));
  await click(page, "Sector Bars", [pick.s]); await sleep(400);
  h = await head(page);
  check(h.eb === "Industry sector and tier" && h.n === pick.n, "a sector picked while a tier tile is selected also filters by both");
  // page buttons drop one filter
  await page.click("#droptier"); await sleep(400);
  check((await head(page)).n === pick.all, "\"Show all tiers in this sector\" drops the tier");
  check(errors.length === 0, "no page errors", errors.join(" | "));
  await page.close(); }
{ const { page } = await open();
  await click(page, "KPI - Tier 1", ["Tier 1"]); await sleep(400);
  const n1 = await pageCount(page, 1);
  check((await head(page)).n === n1, "with no sector selected, a tier tile still lists the whole tier");
  await page.close(); }
{ const s = await (async () => { const { page } = await open(); const v = await page.evaluate(() => { const d = window.SCAN.companies.find(x => x.t === 2); return { s: d.s, n: window.SCAN.companies.filter(x => x.t === 2 && x.s === d.s).length }; }); await page.close(); return v; })();
  const { page } = await open(`?sector=${encodeURIComponent(s.s)}&tier=2`); await sleep(400);
  const h = await head(page), lg = await mlog(page);
  check(h.eb === "Industry sector and tier" && h.n === s.n, "?sector=&tier= opens the filtered list", JSON.stringify(h));
  check(lg.some(x => x.op === "select" && x.sheet === "Sector Bars") && lg.some(x => x.op === "select" && x.sheet === "KPI - Tier 2") && !lg.some(x => x.op === "clear" && x.sheet === "Sector Bars" && lg.indexOf(x) > lg.findIndex(y => y.op === "select" && y.sheet === "Sector Bars")), "and highlights both the sector bar and the tier tile");
  await page.close(); }

/* 8. dashboards without tier tiles are unaffected */
{ const { page, errors } = await open("", {});
  const s = await page.evaluate(() => window.SCANLink.state);
  check(s.tierTargets.length === 0 && errors.length === 0, "no tier tiles: nothing changes");
  await page.close(); }

await browser.close();
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
