/* Checks the Tableau panel size and the layout request sent to Tableau Public.

   Run from the repo root:
     python3 -m http.server 8740 &
     CHROME=/path/to/chrome node tests/tableau_link/size.test.mjs
*/
import puppeteer from "puppeteer-core";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
const API = "https://public.tableau.com/javascripts/api/tableau.embedding.3.latest.min.js";
const MOCK = fs.readFileSync(fileURLToPath(new URL("./tableau_mock.mjs", import.meta.url)), "utf8");
const BASE = process.env.BASE || "http://127.0.0.1:8740/index.html";
const browser = await puppeteer.launch({ executablePath: process.env.CHROME || "/usr/bin/google-chrome", headless: "new", args: ["--no-sandbox", "--disable-gpu"] });
let pass = 0, fail = 0;
const check = (ok, label, extra = "") => { ok ? pass++ : fail++; console.log(`${ok ? "PASS" : "FAIL"}  ${label}${extra ? "  | " + extra : ""}`); };
async function measure(width, height) {
  const page = await browser.newPage();
  await page.setViewport({ width, height });
  await page.setRequestInterception(true);
  page.on("request", r => { const u = r.url();
    if (u.startsWith(API)) return r.respond({ status: 200, contentType: "application/javascript", headers: { "Access-Control-Allow-Origin": "*" }, body: MOCK });
    if (!u.startsWith("http://127.0.0.1")) return r.abort(); r.continue(); });
  await page.goto(BASE, { waitUntil: "domcontentloaded" });
  await page.waitForFunction(() => document.querySelector("tableau-viz"), { timeout: 15000 });
  await new Promise(r => setTimeout(r, 600));
  const m = await page.evaluate(() => { const v = document.querySelector("tableau-viz"), b = v.getBoundingClientRect(), s = document.querySelector(".q-search").getBoundingClientRect();
    return { w: Math.round(b.width), h: Math.round(b.height), device: v.getAttribute("device"), search: Math.round(s.width) }; });
  await page.close(); return m;
}
for (const [w, h] of [[1100, 700], [1280, 720], [1440, 900], [1920, 1080]]) {
  const m = await measure(w, h);
  check(m.device === "desktop", `${w}x${h}: asks Tableau for the desktop layout`, JSON.stringify(m));
  check(m.w >= 660, `${w}x${h}: Tableau frame is at least 660 px wide`, `${m.w} px`);
  check(m.h >= 500, `${w}x${h}: Tableau frame is at least 500 px tall`, `${m.h} px`);
  check(m.search >= 320, `${w}x${h}: search panel keeps at least 320 px`, `${m.search} px`);
}
{ const m = await measure(900, 1000); check(m.device === "desktop" && m.w >= 800, "900 px stacked layout: full width desktop frame", JSON.stringify(m)); }
{ const m = await measure(390, 844); check(m.device === null, "phone: Tableau keeps its phone layout", JSON.stringify(m)); }
await browser.close();
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
