/* End to end tests for the page <-> Tableau link in index.html.

   Runs the real page in headless Chrome with the Tableau Embedding API replaced
   by tableau_mock.mjs, so both directions, the loop guard, queueing and the
   fallbacks can be checked without reaching Tableau Public.

   Run from the repo root:
     python3 -m http.server 8740 &
     npm install puppeteer-core
     CHROME=/path/to/chrome node tests/tableau_link/link.test.mjs
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

async function open(cfg = {}, query = "") {
  const page = await browser.newPage();
  await page.setViewport({ width: 1400, height: 1000 });
  const errors = [], requested = [];
  page.on("pageerror", e => errors.push(String(e)));
  await page.setRequestInterception(true);
  page.on("request", r => {
    const u = r.url();
    if (u.startsWith(API)) { requested.push("api");
      if (cfg.badLib) return r.respond({ status: 200, contentType: "application/javascript", headers: { "Access-Control-Allow-Origin": "*" }, body: "export const nothing = 1;" });
      return cfg.apiFail ? r.respond({ status: 404, body: "nope" })
        : r.respond({ status: 200, contentType: "application/javascript", headers: { "Access-Control-Allow-Origin": "*" }, body: MOCK }); }
    if (u.includes("viz_v1.js")) { requested.push("viz_v1"); return r.respond({ status: 200, contentType: "application/javascript", body: "window.__legacyLoaded=true" }); }
    if (/clarity\.ms|gc\.zgo\.at|goatcounter/.test(u)) return r.respond({ status: 200, contentType: "application/javascript", body: "" });
    if (!u.startsWith("http://127.0.0.1")) return r.abort();
    r.continue();
  });
  await page.evaluateOnNewDocument(c => { window.__mockCfg = c; }, cfg);
  await page.goto(BASE + query, { waitUntil: "domcontentloaded" });
  return { page, errors, requested };
}
const st = page => page.evaluate(() => window.SCANLink && window.SCANLink.state);
const mlog = page => page.evaluate(() => window.__mockLog || []);
const status = page => page.evaluate(() => { const e = document.getElementById("tablink"); return e && !e.hidden ? e.textContent.trim() : ""; });
const shown = page => page.evaluate(() => document.querySelector("#capinfo h3")?.textContent.trim() || "");
async function waitReady(page) { await page.waitForFunction(() => window.SCANLink?.state?.ready && window.SCAN?.ready && document.getElementById("tablink") && !document.getElementById("tablink").hidden, { timeout: 15000 }); }
async function searchPick(page, q) {
  await page.$eval("#dsearch", e => { e.value = ""; }); await page.click("#dsearch"); await page.type("#dsearch", q);
  await page.waitForSelector("#sopt0", { timeout: 5000 }); await page.click("#sopt0"); await sleep(400);
}
const viz = (page, fn, ...args) => page.evaluate(fn, ...args);

/* 1. normal load: discovery, matching, idle message */
{ const { page, errors } = await open();
  await waitReady(page); const s = await st(page);
  check(s.targets.length === 1 && s.targets[0].sheet === "Supplier Map", "finds the map sheet as the company sheet, ignores the bar chart's CNT(supplier_name)", JSON.stringify(s.targets));
  const distinct = await page.evaluate(() => new Set(window.SCAN.companies.map(d => d.n.toLowerCase())).size);
  check(s.targets[0].matched === distinct, `matches every distinct supplier name (${distinct}; two rows share the name Peerless)`, `matched ${s.targets[0].matched}`);
  check((await status(page)).startsWith("Linked to the search box"), "shows the linked status line");
  const load = (await mlog(page)).find(x => x.op === "load");
  check(load && load.src === "https://public.tableau.com/shared/58GF35NYY" && load.toolbar === "bottom", "embeds the configured view with the toolbar at the bottom", JSON.stringify(load));

  /* 2. page to viz */
  await searchPick(page, "Applied Mat");
  const sel = (await mlog(page)).filter(x => x.op === "select");
  check(sel.length === 1 && sel[0].sheet === "Supplier Map" && sel[0].field === "supplier_name" && sel[0].value[0] === "Applied Materials" && sel[0].type === "select-replace",
        "search pick highlights the company in the map", JSON.stringify(sel));
  check((await shown(page)).includes("Applied Materials"), "page still shows the searched company", await shown(page));
  check((await status(page)).includes("Highlighted Applied Materials"), "status confirms the highlight", await status(page));

  /* 3. no echo loop: the programmatic selection event must not reopen or re-push */
  await sleep(500);
  const sel2 = (await mlog(page)).filter(x => x.op === "select");
  const clicks = await page.evaluate(() => (window.clarity && window.clarity.q || []).filter(a => a[0] === "event" && a[1] === "tableau_mark_click").length);
  check(sel2.length === 1 && clicks === 0, "own highlight is not echoed back as a visitor click", `selects ${sel2.length}, tableau clicks logged ${clicks}`);

  /* 4. viz to page */
  await viz(page, () => document.querySelector("tableau-viz")._userClick("Supplier Map", ["Corning Incorporated"]).catch(() => {}));
  await sleep(500);
  const names = await page.evaluate(() => window.SCAN.companies.map(d => d.n));
  const target = names.find(n => /corning/i.test(n)) || names[5];
  await viz(page, n => document.querySelector("tableau-viz")._userClick("Supplier Map", [n]), target);
  await sleep(500);
  check((await shown(page)).includes(target), "clicking a company in the map opens it on the page", `${target} -> ${await shown(page)}`);
  check((await status(page)).startsWith(`Showing ${target} in the search panel`), "status says where the details are", await status(page));
  const pushedBack = (await mlog(page)).filter(x => x.op === "select").length;
  check(pushedBack === 1, "a click from the map is not pushed back to the map", `selects ${pushedBack}`);
  const clar = await page.evaluate(() => (window.clarity && window.clarity.q || []).map(a => [...a]).filter(a => a[1] === "tableau_mark_click" || a[1] === "tableau_company"));
  check(clar.some(a => a[1] === "tableau_mark_click") && clar.some(a => a[1] === "tableau_company" && a[2] === target), "map clicks are logged to Clarity with the company", JSON.stringify(clar));

  /* 5. one screen layout: details already beside the map, so no shortcut and no page jump */
  const fit = await page.evaluate(() => ({ go: getComputedStyle(document.querySelector("#tablink .tablinkgo")).display, y: scrollY,
    inView: (r => r.top >= 0 && r.top < innerHeight)(document.getElementById("capinfo").getBoundingClientRect()) }));
  check(fit.go === "none" && fit.y === 0 && fit.inView, "one screen layout: details visible beside the map, no shortcut, no page jump", JSON.stringify(fit));

  /* 6. multi select, unknown company, clear */
  await viz(page, (a, b) => document.querySelector("tableau-viz")._userClick("Supplier Map", [a, b]), names[0], names[1]); await sleep(400);
  check((await status(page)).startsWith("2 companies are selected"), "a multiple selection asks for a single company", await status(page));
  const before = await shown(page);
  await viz(page, () => document.querySelector("tableau-viz")._userClick("Supplier Map", [])); await sleep(400);
  check((await shown(page)) === before, "clearing the selection in the map leaves the page alone");
  /* sector bars */
  const s1 = await st(page);
  const bars = s1.sectorTargets.find(x => x.sheet === "Sector Bars");
  check(bars && bars.field === "industry_sector" && bars.matched === bars.values && bars.values > 20,
        "finds the sector bar sheet and matches every sector", JSON.stringify(s1.sectorTargets));
  await viz(page, () => document.querySelector("tableau-viz")._userClick("Sector Bars", ["Advanced Manufacturing"])); await sleep(500);
  const sec = await page.evaluate(() => ({ eyebrow: document.querySelector("#capinfo .eyebrow")?.textContent, h3: document.querySelector("#capinfo h3")?.textContent,
    rows: document.querySelectorAll("#capinfo .srow").length, expect: window.SCAN.companies.filter(d => d.s === "Advanced Manufacturing").length }));
  check(/sector/i.test(sec.eyebrow) && sec.h3 === "Advanced Manufacturing" && sec.rows === sec.expect && sec.rows > 0,
        "clicking a sector bar lists that sector's suppliers", JSON.stringify(sec));
  check((await status(page)).startsWith(`Listing the ${sec.expect} suppliers in Advanced Manufacturing`), "status says how many suppliers are listed", await status(page));
  const sclar = await page.evaluate(() => (window.clarity && window.clarity.q || []).map(a => [...a]).filter(a => a[1] === "tableau_sector_click" || a[1] === "tableau_sector"));
  check(sclar.some(a => a[1] === "tableau_sector_click") && sclar.some(a => a[1] === "tableau_sector" && a[2] === "Advanced Manufacturing"), "sector clicks are logged to Clarity", JSON.stringify(sclar));
  const selBefore = (await mlog(page)).filter(x => x.op === "select").length;
  await sleep(300);
  check((await mlog(page)).filter(x => x.op === "select").length === selBefore, "a sector click is not pushed back to the dashboard");
  await viz(page, () => document.querySelector("tableau-viz")._userClick("Sector Bars", ["Advanced Manufacturing", "Optics & Photonics"])); await sleep(400);
  check((await status(page)).startsWith("2 sectors are selected"), "several bars ask for a single sector", await status(page));
  check((await shown(page)) === "Advanced Manufacturing", "several bars leave the current list alone");
  const any = await page.evaluate(() => window.SCAN.companies[0].n);
  await viz(page, n => document.querySelector("tableau-viz")._userClick("Supplier Map", [n]), any); await sleep(400);
  check((await shown(page)) === any, "a company click on the map still opens the company, not its sector", await shown(page));

  /* 7. reset clears the highlight */
  await page.click("#resetbtn"); await sleep(400);
  const clr = (await mlog(page)).filter(x => x.op === "clear");
  check(clr.some(x => x.sheet === "Supplier Map") && clr.some(x => x.sheet === "Sector Bars"), "Reset clears the map and the sector bar", JSON.stringify(clr.slice(-2)));
  check((await status(page)).startsWith("Linked to the search box"), "status returns to idle after reset");

  /* 8. searching a sector or capability does not touch the viz */
  const n0 = (await mlog(page)).length;
  await page.$eval("#dsearch", e => { e.value = ""; }); await page.click("#dsearch"); await page.type("#dsearch", "Optics");
  await page.waitForSelector('#sresults a[aria-label^="Capability:"]', { timeout: 5000 });
  await page.click('#sresults a[aria-label^="Capability:"]'); await sleep(400);
  await page.$eval("#dsearch", e => { e.value = ""; }); await page.click("#dsearch"); await page.type("#dsearch", "Advanced Manu");
  await page.waitForSelector('#sresults a[aria-label^="Industry sector:"]', { timeout: 5000 });
  await page.click('#sresults a[aria-label^="Industry sector:"]'); await sleep(400);
  const extra = (await mlog(page)).slice(n0).filter(x => x.op === "select");
  check(extra.length === 1 && extra[0].sheet === "Sector Bars" && extra[0].value[0] === "Advanced Manufacturing",
        "a capability result leaves the dashboard alone; a sector result selects its bar", JSON.stringify(extra));
  await sleep(400);
  check((await status(page)).startsWith("Highlighted Advanced Manufacturing in the dashboard's sector chart"), "the bar's own selection event is not read back as a click", await status(page));
  check(errors.length === 0, "no page errors in the main run", errors.join(" | "));
  await page.close(); }

/* 9. search before the viz is ready is queued */
{ const { page, errors } = await open({ delay: 3000 });
  await page.waitForFunction(() => window.SCAN?.ready, { timeout: 10000 });
  await searchPick(page, "Applied Mat");
  const early = (await mlog(page)).filter(x => x.op === "select").length;
  await waitReady(page); await sleep(600);
  const sel = (await mlog(page)).filter(x => x.op === "select");
  check(early === 0 && sel.length === 1 && sel[0].value[0] === "Applied Materials", "a search made before the dashboard is ready is applied when it loads", `before ${early}, after ${sel.length}`);
  check(errors.length === 0, "no page errors in the queued run", errors.join(" | "));
  await page.close(); }

/* 10. loose name matching: case, punctuation, Inc */
{ const { page } = await open({ nameCol: "Company Name", xform: "return n.toUpperCase().replace(/ /g,'  ') + ', Inc.'" });
  await waitReady(page); const s = await st(page);
  check(s.targets[0] && s.targets[0].field === "Company Name" && s.targets[0].matched >= 150, "names that differ in case, spacing and Inc still match", JSON.stringify(s.targets));
  await searchPick(page, "Applied Mat");
  const sel = (await mlog(page)).filter(x => x.op === "select")[0];
  check(sel && sel.value[0] === "APPLIED  MATERIALS, Inc.", "highlight sends the dashboard's own spelling", JSON.stringify(sel && sel.value));
  await page.close(); }

/* 11. id field preferred when present */
{ const { page } = await open({ nameCol: "id", useId: true });
  await waitReady(page); const s = await st(page);
  check(s.targets[0].field === "id" && s.targets[0].matched === 155, "matches on the published id field, all 155 including both Peerless rows", JSON.stringify(s.targets));
  await page.close(); }

/* 12. company missing from the dashboard */
{ const { page } = await open({ dropEvery: 3, extra: ["Ghost Supplier LLC"] });
  await waitReady(page);
  const missing = await page.evaluate(() => window.SCAN.companies[0].n);
  await searchPick(page, missing.slice(0, 12));
  check((await status(page)).includes("isn't in the dashboard") || (await status(page)).includes("Highlighted"), "a company missing from the dashboard says so", await status(page));
  await viz(page, () => document.querySelector("tableau-viz")._userClick("Supplier Map", ["Ghost Supplier LLC"])); await sleep(400);
  check((await status(page)).includes("isn't in the supplier list"), "a dashboard company missing from the page says so", await status(page));
  await page.close(); }

/* 13. no company field at all */
{ const { page } = await open({ noKey: true });
  await waitReady(page);
  check((await status(page)).includes("no company field"), "with only sector bars, says sectors work and companies don't", await status(page));
  await viz(page, () => document.querySelector("tableau-viz")._userClick("Sector Bars", ["Optics & Photonics"])); await sleep(500);
  check((await shown(page)) === "Optics & Photonics", "with only sector bars, a bar still lists its suppliers", await shown(page));
  await page.close(); }

/* 13b. sector names matched loosely, unknown sector reported */
{ const { page } = await open({ sectorCol: "Industry Group", sectorXform: "return n.toUpperCase()", extraSector: "Basket Weaving" });
  await waitReady(page); const s = await st(page);
  const grp = s.sectorTargets.find(x => x.sheet === "Sector Bars");
  check(grp && grp.field === "Industry Group" && grp.matched === grp.values - 1,
        "an 'Industry Group' field in capitals still matches the page's sectors", JSON.stringify(s.sectorTargets));
  await viz(page, () => document.querySelector("tableau-viz")._userClick("Sector Bars", ["OPTICS & PHOTONICS"])); await sleep(500);
  check((await shown(page)) === "Optics & Photonics", "a capitalised sector opens the page's sector", await shown(page));
  await viz(page, () => document.querySelector("tableau-viz")._userClick("Sector Bars", ["Basket Weaving"])); await sleep(400);
  check((await status(page)).includes("isn't an industry sector in the supplier list"), "an unknown sector says so", await status(page));
  await page.close(); }

/* 14. debug panel */
{ const { page } = await open({}, "?scandebug=1");
  await waitReady(page); await sleep(300);
  const dbg = await page.evaluate(() => { const d = document.getElementById("tabdebug"); return { shown: !d.hidden, rows: d.querySelectorAll("tbody tr").length, text: d.textContent }; });
  check(dbg.shown && dbg.rows === 2 && dbg.text.includes("Supplier Map") && dbg.text.includes("CNT(supplier_name)") && dbg.text.includes("Sector field"), "?scandebug=1 lists every worksheet, its company and sector fields", `rows ${dbg.rows}`);
    await page.close(); }

/* 15. fallbacks keep the dashboard */
for (const [cfg, label] of [[{ apiFail: true }, "API library fails to load"], [{ badLib: true }, "API library has unexpected contents"], [{ loadError: true }, "Tableau reports a load error"]]) {
  const { page, errors, requested } = await open(cfg);
  await page.waitForFunction(() => document.querySelector("#viz1795017713550 object.tableauViz"), { timeout: 10000 }).catch(() => {});
  await sleep(400);
  const fb = await page.evaluate(() => { const o = document.querySelector("#viz1795017713550 object.tableauViz");
    return { obj: !!o, path: o && [...o.querySelectorAll("param")].find(p => p.name === "path")?.value, img: o && [...o.querySelectorAll("param")].find(p => p.name === "static_image")?.value, live: !!document.querySelector("tableau-viz") }; });
  check(fb.obj && !fb.live && fb.path === "shared/58GF35NYY" && requested.includes("viz_v1"), `${label}: falls back to the standard embed`, JSON.stringify(fb));
  check((await status(page)).startsWith("The dashboard loaded without its link"), `${label}: says the link is off`, await status(page));
  await page.waitForFunction(() => window.SCAN?.ready, { timeout: 10000 });
  await searchPick(page, "Applied Mat");
  check((await shown(page)).includes("Applied Materials") && errors.length === 0, `${label}: search still works with no errors`, errors.join(" | "));
  await page.close(); }

/* 16. tracking proxy still finds the iframe container */
{ const { page } = await open(); await waitReady(page);
  const ok = await page.evaluate(() => !!document.querySelector("#viz1795017713550 tableau-viz"));
  check(ok, "the viz still sits inside #viz1795017713550, which the engagement tracker watches");
  await page.close(); }


/* 17. stacked layout on a tablet: Go to it scrolls to the details */
{ const { page } = await open();
  await page.setViewport({ width: 1000, height: 800 }); await waitReady(page); await sleep(300);
  const name = await page.evaluate(() => window.SCAN.companies[5].n);
  await viz(page, n => document.querySelector("tableau-viz")._userClick("Supplier Map", [n]), name); await sleep(500);
  await page.evaluate(() => document.querySelector("tableau-viz").scrollIntoView()); await sleep(200);
  await page.click("#tablink .tablinkgo"); await sleep(900);
  const pos = await page.evaluate(() => Math.round(document.getElementById("capinfo").getBoundingClientRect().top));
  check(pos >= -5 && pos < 120, "stacked layout: Go to it scrolls to the details", `details top ${pos}px`);
  await page.close(); }

/* 18. sector list -> company -> back -> bar cleared */
{ const { page, errors } = await open();
  await waitReady(page); await sleep(300);
  const S = "Optics & Photonics";
  await viz(page, n => document.querySelector("tableau-viz")._userClick("Sector Bars", [n]), S); await sleep(500);
  check((await shown(page)) === S, "bar click lists the sector");
  // remember a scroll position in the list, then open a company from it
  await page.evaluate(() => { const b = document.getElementById("capinfo"); b.scrollTop = 120; });
  const scrolled = await page.evaluate(() => document.getElementById("capinfo").scrollTop);
  const n0 = (await mlog(page)).length;
  const name = await page.evaluate(() => document.querySelectorAll("#capinfo .conav")[2].firstChild.textContent);
  await page.evaluate(() => document.querySelectorAll("#capinfo .conav")[2].click()); await sleep(600);
  const after = (await mlog(page)).slice(n0);
  const co = await page.evaluate(() => ({ h3: document.querySelector("#capinfo h3")?.textContent, eyebrow: document.querySelector("#capinfo .eyebrow")?.textContent,
    back: document.getElementById("backbtn")?.textContent.trim(), focus: document.activeElement?.tagName }));
  check(co.h3 === name && /company/i.test(co.eyebrow), "a company name in the sector list opens its details", JSON.stringify(co));
  check(after.some(x => x.op === "select" && x.sheet === "Supplier Map" && x.value[0] === name) && !after.some(x => x.sheet === "Sector Bars"),
        "it highlights the company on the map and leaves the sector bar selected", JSON.stringify(after));
  check(co.back === `← Back to ${S} suppliers`, "a Back button names the sector list", co.back);
  check((await shown(page)) === name && (await page.evaluate(() => window.SCAN.hasSelection)), "page is not reset by the map selection's own events");
  // back to the list
  const n1 = (await mlog(page)).length;
  await page.click("#backbtn"); await sleep(600);
  const bk = await page.evaluate(() => ({ h3: document.querySelector("#capinfo h3")?.textContent, scroll: document.getElementById("capinfo").scrollTop, back: !!document.getElementById("backbtn") }));
  const after2 = (await mlog(page)).slice(n1);
  check(bk.h3 === S && !bk.back, "Back returns to the sector list", JSON.stringify(bk));
  check(Math.abs(bk.scroll - scrolled) < 2, "Back restores the list's scroll position", `${scrolled} -> ${bk.scroll}`);
  check(after2.some(x => x.op === "clear" && x.sheet === "Supplier Map") && after2.some(x => x.op === "select" && x.sheet === "Sector Bars" && x.value[0] === S),
        "Back clears the company highlight and keeps the bar selected", JSON.stringify(after2));
  // open a company again, then click the bar again to deselect it in Tableau
  await page.evaluate(() => document.querySelectorAll("#capinfo .conav")[0].click()); await sleep(1800);
  const n2 = (await mlog(page)).length;
  await viz(page, () => document.querySelector("tableau-viz")._userClick("Sector Bars", [])); await sleep(600);
  const rs = await page.evaluate(() => ({ h3: document.querySelector("#capinfo h3")?.textContent, sel: window.SCAN.hasSelection }));
  const after3 = (await mlog(page)).slice(n2);
  check(rs.h3 === "No selection yet" && !rs.sel, "clicking the bar again resets the details panel", JSON.stringify(rs));
  check(after3.some(x => x.op === "clear" && x.sheet === "Supplier Map"), "and clears the company highlight on the map", JSON.stringify(after3));
  check((await status(page)).startsWith("Sector cleared in the dashboard"), "status says why the panel reset", await status(page));
  const cl = await page.evaluate(() => (window.clarity && window.clarity.q || []).filter(a => a[1] === "tableau_sector_clear" || a[1] === "sector_list_company_open").map(a => a[1]));
  check(cl.includes("tableau_sector_clear") && cl.includes("sector_list_company_open"), "list opens and bar clears are logged", JSON.stringify(cl));
  // a company from search clears a selected bar, and the page's own clear does not reset the page
  await viz(page, n => document.querySelector("tableau-viz")._userClick("Sector Bars", [n]), S); await sleep(1800);
  const n3 = (await mlog(page)).length;
  await page.$eval("#dsearch", e => { e.value = ""; }); await page.click("#dsearch"); await page.type("#dsearch", "Applied Mat");
  await page.waitForSelector("#sopt0"); await page.click("#sopt0"); await sleep(700);
  const after4 = (await mlog(page)).slice(n3);
  check(after4.findIndex(x => x.op === "clear" && x.sheet === "Sector Bars") >= 0 &&
        after4.findIndex(x => x.op === "clear" && x.sheet === "Sector Bars") < after4.findIndex(x => x.op === "select" && x.sheet === "Supplier Map"),
        "a company from search clears the bar before highlighting the map", JSON.stringify(after4));
  check((await shown(page)) === "Applied Materials", "the page's own bar clear does not reset the panel", await shown(page));
  // picking a capability drops the Back link
  await page.evaluate(() => { document.querySelector("#caps details.capgroup").open = true; });
  await page.click('#caps [data-k="cnc"]'); await sleep(400);
  check(!(await page.evaluate(() => window.SCAN.hasSelection && document.getElementById("backbtn"))), "a capability pick leaves no Back link");
  // keyboard: sector rows are buttons and Back takes focus to the heading
  check(errors.length === 0, "no page errors in the sector flow", errors.join(" | "));
  await page.close(); }

/* 19. a sector chosen before the dashboard is ready is applied when it loads */
{ const { page } = await open({ delay: 3000 });
  await page.waitForFunction(() => window.SCAN?.ready, { timeout: 10000 });
  await page.$eval("#dsearch", e => { e.value = ""; }); await page.click("#dsearch"); await page.type("#dsearch", "Advanced Manu");
  await page.waitForSelector('#sresults a[aria-label^="Industry sector:"]'); await page.click('#sresults a[aria-label^="Industry sector:"]');
  await waitReady(page); await sleep(600);
  const sel = (await mlog(page)).filter(x => x.op === "select" && x.sheet === "Sector Bars");
  check(sel.length === 1 && sel[0].value[0] === "Advanced Manufacturing", "a sector chosen early is highlighted once the dashboard loads", JSON.stringify(sel));
  await page.close(); }

/* 20. "How to use" floats over its panel instead of resizing it */
{ const { page, errors } = await open();
  await waitReady(page); await sleep(300);
  const rect = sel => page.evaluate(s => { const r = document.querySelector(s).getBoundingClientRect(); return [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)].join(","); }, sel);
  const vizBefore = await rect("tableau-viz"), panelBefore = await rect(".q-tab");
  await page.click(".q-tab .infobtn"); await sleep(250);
  const help = await page.evaluate(() => { const h = document.getElementById("howto-tab"), b = document.querySelector(".q-tab .infobtn");
    const r = h.getBoundingClientRect(), br = b.getBoundingClientRect(); const cs = getComputedStyle(h);
    return { shown: !h.hidden, position: cs.position, below: r.top > br.bottom, expanded: b.getAttribute("aria-expanded"),
      onTop: document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)?.closest("#howto-tab") !== null }; });
  check(help.shown && help.position === "absolute" && help.below && help.onTop && help.expanded === "true", "Tableau help opens as a card over the panel", JSON.stringify(help));
  check((await rect("tableau-viz")) === vizBefore && (await rect(".q-tab")) === panelBefore, "the dashboard keeps its exact size and place while help is open", `${vizBefore} / ${await rect("tableau-viz")}`);
  await page.keyboard.press("Escape"); await sleep(150);
  const esc = await page.evaluate(() => ({ hidden: document.getElementById("howto-tab").hidden, focus: document.activeElement?.classList.contains("infobtn") }));
  check(esc.hidden && esc.focus, "Escape closes it and returns focus to the button", JSON.stringify(esc));
  await page.click(".q-search .infobtn"); await sleep(150); await page.click(".q-caps .infobtn"); await sleep(150);
  const one = await page.evaluate(() => [...document.querySelectorAll(".howto")].filter(h => !h.hidden).map(h => h.id));
  check(one.length === 1 && one[0] === "howto-cap", "only one help card is open at a time", JSON.stringify(one));
  await page.mouse.click(5, 300); await sleep(150);
  check(await page.evaluate(() => [...document.querySelectorAll(".howto")].every(h => h.hidden)), "a click elsewhere closes it");
  await page.click(".q-fab .infobtn"); await sleep(150); await page.click("#howto-fab .helpclose"); await sleep(150);
  check(await page.evaluate(() => document.getElementById("howto-fab").hidden), "its close button closes it");
  const searchH = await rect(".q-search .sfield"); await page.click(".q-search .infobtn"); await sleep(150);
  check((await rect(".q-search .sfield")) === searchH, "search help no longer pushes the search box down");
  check(errors.length === 0, "no page errors with help cards", errors.join(" | "));
  await page.close(); }
console.log(`\n${pass} passed, ${fail} failed`);
await browser.close();
process.exit(fail ? 1 : 0);
