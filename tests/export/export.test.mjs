/* End to end tests for the PDF download, email and share buttons in index.html.

   Runs the real page in headless Chrome (Tableau mocked, analytics blocked),
   clicks the export buttons, saves the PDFs and reads them back with pypdf to
   check that they hold exactly the companies shown on screen.

   Run from the repo root:
     python3 -m http.server 8740 &
     npm install puppeteer-core          (and: pip install pypdf)
     CHROME=/path/to/chrome node tests/export/export.test.mjs
*/
import puppeteer from "puppeteer-core";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
const API = "https://public.tableau.com/javascripts/api/tableau.embedding.3.latest.min.js";
const MOCK = fs.readFileSync(fileURLToPath(new URL("../tableau_link/tableau_mock.mjs", import.meta.url)), "utf8");
const BASE = process.env.BASE || "http://127.0.0.1:8740/index.html";
const OUT = fs.mkdtempSync(path.join(os.tmpdir(), "scan-export-"));
const sleep = ms => new Promise(r => setTimeout(r, ms));
const browser = await puppeteer.launch({ executablePath: process.env.CHROME || "/usr/bin/google-chrome", headless: "new", args: ["--no-sandbox", "--disable-gpu"] });
let pass = 0, fail = 0;
const check = (ok, label, extra = "") => { ok ? pass++ : fail++; console.log(`${ok ? "PASS" : "FAIL"}  ${label}${extra ? "  | " + extra : ""}`); };

async function open(query = "", { share = false, width = 1400 } = {}) {
  const page = await browser.newPage();
  await page.setViewport({ width, height: 1000 });
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
  await page.evaluateOnNewDocument(share => {
    window.__mailto = []; window.__shared = []; window.__clarity = [];
    window.clarity = (...a) => window.__clarity.push(a.join(":"));
    const click = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function () { if (this.href.startsWith("mailto:")) { window.__mailto.push(this.href); return; } return click.call(this); };
    if (share) {
      navigator.canShare = d => !!(d && d.files && d.files.length);
      navigator.share = async d => { window.__shared.push({ n: d.files.length, name: d.files[0].name, type: d.files[0].type, size: d.files[0].size, text: d.text }); };
    } else { try { delete Navigator.prototype.canShare; } catch (e) {} navigator.canShare = undefined; }
  }, share);
  const dir = fs.mkdtempSync(path.join(OUT, "dl-"));
  const cdp = await page.createCDPSession();
  await cdp.send("Browser.setDownloadBehavior", { behavior: "allow", downloadPath: dir });
  await page.goto(BASE + query, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => window.SCAN && window.SCAN.ready, { timeout: 15000 });
  await sleep(300);
  return { page, errors, dir };
}
async function waitFile(dir, ms = 10000) {
  const t = Date.now();
  while (Date.now() - t < ms) { const f = fs.readdirSync(dir).filter(n => n.endsWith(".pdf")); if (f.length) { await sleep(200); return path.join(dir, f[0]); } await sleep(150); }
  return null;
}
function pdfInfo(file) {
  const py = `import sys,json,pypdf
r=pypdf.PdfReader(sys.argv[1]); t="\\n".join(p.extract_text() for p in r.pages)
links=[a.get_object().get("/A",{}).get("/URI") for p in r.pages for a in (p.get("/Annots") or [])]
print(json.dumps({"pages":len(r.pages),"text":t,"title":(r.metadata or {}).get("/Title"),"links":[l for l in links if l]}))`;
  return JSON.parse(execFileSync("python3", ["-I", "-c", py, file], { encoding: "utf8" }));
}
const onScreen = page => page.evaluate(() => [...document.querySelectorAll("#capinfo .suplist .cobtn")].map(b => b.childNodes[b.classList.contains("conav") ? 0 : 1].textContent.trim()));
const status = page => page.$eval("#capinfo .xstatus", e => e.textContent.trim()).catch(() => "");
const squash = s => s.replace(/\s+/g, " ");

/* 1. no selection: no export bar */
{ const { page, errors } = await open();
  check(!(await page.$("#capinfo .xbar")), "no export buttons before anything is selected");
  check((await page.$$eval("#capinfo .xbtn", b => b.length)) === 0 && !(await page.$("script[src*='jspdf']")), "PDF library is not loaded up front");
  check(errors.length === 0, "no page errors on load", errors.join(" | "));
  await page.close(); }

/* 2. sector list: PDF holds the same companies in the same order */
{ const { page, errors, dir } = await open();
  const sector = await page.evaluate(() => { const c = {}; window.SCAN.companies.forEach(d => c[d.s] = (c[d.s] || 0) + 1); return Object.entries(c).sort((a, b) => b[1] - a[1])[0][0]; });
  await page.evaluate(s => window.SCAN.openSector(s), sector); await sleep(300);
  const labels = await page.$$eval("#capinfo .xbtn", b => b.map(x => x.textContent.trim()));
  check(labels.join("|") === "Download PDF|Email", "sector view shows Download PDF and Email (no Share where the browser cannot share files)", labels.join("|"));
  const names = await onScreen(page);
  await page.click('#capinfo .xbtn[data-x="pdf"]');
  const f = await waitFile(dir);
  check(!!f, "Download PDF saves a file", f || "none");
  if (f) {
    check(/^SCAN_industry_sector_[a-z0-9_]+_\d{8}\.pdf$/.test(path.basename(f)), "file name says what it holds and when", path.basename(f));
    const p = pdfInfo(f), txt = squash(p.text);
    check(txt.includes("Supply Chain Activation Network (SCAN) Dashboard 2.0") && txt.includes(squash(sector)), "PDF has the dashboard title and the sector name");
    check(txt.includes(`${names.length} companies`), `PDF states the count (${names.length})`);
    const missing = names.filter(n => !txt.includes(squash(n).slice(0, 18)));
    check(missing.length === 0, "every company on screen is in the PDF", missing.join(", "));
    const pos = names.map(n => txt.indexOf(`. ${squash(n).slice(0, 18)}`)).filter(i => i >= 0);
    check(pos.length === names.length && pos.every((v, i) => !i || v > pos[i - 1]), "details appear in the on screen sort order");
    check(/Page 1 of \d+/.test(txt), "pages are numbered", `${p.pages} pages`);
    check(p.links.some(l => l.includes("?sector=")), "PDF links back to this exact view");
    check(!/contact_email|@[a-z0-9.]+\.(com|org|edu)/i.test(txt), "no email addresses are printed (contact email stays withheld)");
    fs.copyFileSync(f, path.join(OUT, "sample_sector.pdf"));
  }
  check((await status(page)).startsWith("PDF saved as"), "status line confirms the save", await status(page));
  check((await page.evaluate(() => window.__clarity)).some(x => x === "event:pdf_download"), "pdf_download event is tracked");
  check(errors.length === 0, "no page errors", errors.join(" | "));
  await page.close(); }

/* 3. statement filter and sort carry into the PDF */
{ const { page, dir } = await open();
  await page.evaluate(() => { const c = {}; window.SCAN.companies.filter(d => d.statement).forEach(d => c[d.s] = (c[d.s] || 0) + 1); window.SCAN.openSector(Object.entries(c).sort((a, b) => b[1] - a[1])[0][0]); }); await sleep(300);
  await page.click("#stmtonly"); await sleep(250);
  await page.select("#supsort", "name"); await sleep(250);
  const names = await onScreen(page);
  const sel = await page.evaluate(() => { const s = window.SCANExport.currentSelection(); return { n: s.items.length, names: s.items.map(d => d.n), filtered: s.filtered, total: s.total }; });
  check(sel.n > 1 && sel.n < sel.total && sel.n === names.length && sel.names.join("|") === names.join("|"), "export list equals the filtered, name sorted list on screen", `${sel.n} vs ${names.length}`);
  await page.click('#capinfo .xbtn[data-x="pdf"]');
  const f = await waitFile(dir);
  if (f) { const txt = squash(pdfInfo(f).text);
    check(txt.includes("Showing only suppliers with a capability statement") && txt.includes("Sorted by name"), "PDF notes the filter and the sort"); }
  else check(false, "filtered PDF saved");
  await page.close(); }

/* 4. single company */
{ const { page, dir } = await open();
  const d = await page.evaluate(() => { const d = window.SCAN.companies.find(x => x.w && x.statement && x.prim.length); window.SCAN.openCompany(d.id); return { n: d.n, w: d.w, sid: d.sid, st: d.statement }; });
  await sleep(300);
  await page.click('#capinfo .xbtn[data-x="pdf"]');
  const f = await waitFile(dir);
  if (f) { const p = pdfInfo(f), txt = squash(p.text);
    check(txt.includes(squash(d.n)) && txt.includes("Website") && txt.includes("Capability statement") && txt.includes("Fab capability"), "company PDF carries the profile fields");
    check(!txt.includes("Summary"), "company PDF skips the list summary table");
    check(p.links.includes(d.st) && p.links.some(l => l.includes(`?company=${encodeURIComponent(d.sid)}`)), "statement and view links are clickable in the PDF");
    check(/^SCAN_company_/.test(path.basename(f)), "company file name", path.basename(f));
    fs.copyFileSync(f, path.join(OUT, "sample_company.pdf")); }
  else check(false, "company PDF saved");
  await page.close(); }

/* 5. Email: saves the PDF and opens a mail draft */
{ const { page, dir } = await open();
  const cap = await page.evaluate(() => { const b = [...document.querySelectorAll(".cap")].sort((a, b) => +b.querySelector(".n").textContent - +a.querySelector(".n").textContent)[0]; b.click(); return b.dataset.k; });
  await sleep(300);
  const n = (await onScreen(page)).length;
  await page.click('#capinfo .xbtn[data-x="mail"]');
  const f = await waitFile(dir);
  await sleep(200);
  const m = await page.evaluate(() => window.__mailto);
  check(!!f && m.length === 1, "Email saves the PDF and opens one mail draft", `${!!f} ${m.length}`);
  if (m.length) { const u = new URL(m[0]), subj = u.searchParams.get("subject") || decodeURIComponent((m[0].match(/subject=([^&]*)/) || [])[1] || ""), body = decodeURIComponent((m[0].match(/body=([^&]*)/) || [])[1] || "");
    check(subj.startsWith("SCAN supplier list:") && subj.includes(`(${n} companies)`), "draft subject names the list and count", subj);
    check(body.includes(`?capability=${cap}`) && body.includes(".pdf"), "draft body links to the view and names the PDF to attach");
    check(m[0].length < 2000, "mail link stays under 2000 characters so mail apps accept it", String(m[0].length)); }
  check(/Attach it to the email draft/.test(await status(page)), "status explains the attachment step", await status(page));
  check((await page.evaluate(() => window.__clarity)).some(x => x === "event:email_share"), "email_share event is tracked");
  await page.close(); }

/* 6. Share sheet where the browser supports sharing files */
{ const { page } = await open("", { share: true });
  await page.evaluate(() => document.querySelector(".zbtn").click()); await sleep(300);
  const labels = await page.$$eval("#capinfo .xbtn", b => b.map(x => x.textContent.trim()));
  check(labels.includes("Share"), "Share button appears when the browser can share files", labels.join("|"));
  await page.click('#capinfo .xbtn[data-x="share"]');
  await page.waitForFunction(() => window.__shared.length, { timeout: 10000 }).catch(() => {});
  const s = await page.evaluate(() => window.__shared);
  check(s.length === 1 && s[0].type === "application/pdf" && s[0].size > 2000 && /^SCAN_fab_zone_/.test(s[0].name), "Share hands one PDF file to the share sheet", JSON.stringify(s));
  await page.close(); }

/* 7. deep links open the matching view */
{ const sid = await (async () => { const { page } = await open(); const v = await page.evaluate(() => window.SCAN.companies[5].sid); await page.close(); return v; })();
  for (const [q, want] of [[`?company=${encodeURIComponent(sid)}`, "Company"], ["?sector=Semiconductor", "Industry sector"], ["?capability=hepa", "Capability"], ["?zone=ro", "Fab zone"], ["?zone=nope", "Suppliers"]]) {
    const { page, errors } = await open(q);
    const eb = await page.$eval("#capinfo .eyebrow", e => e.textContent.trim()).catch(() => "");
    check(eb.startsWith(want) && errors.length === 0, `deep link ${q} opens ${want}`, eb);
    await page.close(); }
  const { page } = await open("?scandebug=1&capability=litho");
  check((await page.$eval("#capinfo .eyebrow", e => e.textContent.trim())) === "Capability", "deep link works alongside ?scandebug=1");
  await page.close(); }

/* 8. narrow screen: buttons fit and do not overflow the panel */
{ const { page } = await open("?capability=hepa", { width: 390 });
  const fit = await page.evaluate(() => { const bar = document.querySelector("#capinfo .xbar"), box = document.querySelector("#capinfo"); return bar && [...bar.querySelectorAll(".xbtn")].every(b => b.getBoundingClientRect().right <= box.getBoundingClientRect().right + 1); });
  check(fit, "export buttons fit on a phone width screen");
  await page.screenshot({ path: path.join(OUT, "phone.png") });
  await page.close(); }
{ const { page } = await open("?sector=Semiconductor");
  const el = await page.$(".q-search"); await el.screenshot({ path: path.join(OUT, "desktop.png") });
  await page.close(); }

await browser.close();
console.log(`\n${pass} passed, ${fail} failed. Samples and screenshots: ${OUT}`);
process.exit(fail ? 1 : 0);
