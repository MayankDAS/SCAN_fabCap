/* Mock of the parts of the Tableau Embedding API v3 the page uses. */
export const TableauEventType = { FirstInteractive: "firstinteractive", VizLoadError: "vizloaderror", MarkSelectionChanged: "markselectionchanged" };
export const SelectionUpdateType = { Replace: "select-replace", Add: "select-add", Remove: "select-remove" };
export const FilterUpdateType = { All: "all", Replace: "replace", Add: "add", Remove: "remove" };
const CFG = window.__mockCfg || {};
const LOG = (window.__mockLog = window.__mockLog || []);
let SUP = null;
const data = async () => SUP || (SUP = (await (await fetch("data/suppliers.json")).json()).suppliers);
const bare = c => c.replace(/^(ATTR|MIN|MAX)\((.*)\)$/, "$2");

class Worksheet {
  constructor(viz, name, cols, rowsFn) { Object.assign(this, { viz, name, cols, rowsFn, sheetType: "worksheet", sel: [] }); }
  async getSummaryDataAsync() {
    const rows = await this.rowsFn();
    return { columns: this.cols.map(f => ({ fieldName: f })), data: rows.map(r => this.cols.map(f => ({ value: r[f] }))) };
  }
  async selectMarksByValueAsync(sel, type) {
    LOG.push({ op: "select", sheet: this.name, field: sel[0].fieldName, value: sel[0].value, type });
    const col = this.cols.find(c => bare(c) === sel[0].fieldName);
    if (!col) throw new Error("Field not found: " + sel[0].fieldName);
    const want = new Set(sel[0].value.map(String)), rows = await this.rowsFn();
    this.sel = rows.filter(r => want.has(String(r[col])));
    setTimeout(() => this.viz._fire(this), 40);       // Tableau raises the event for programmatic selections too
  }
  async clearSelectedMarksAsync() { LOG.push({ op: "clear", sheet: this.name }); this.sel = []; setTimeout(() => this.viz._fire(this), 40); }
  async applyFilterAsync(field, values, type) { LOG.push({ op: "filter", sheet: this.name, field, value: values, type }); this.filters = { ...(this.filters || {}), [field]: values }; return field; }
  async clearFilterAsync(field) { LOG.push({ op: "unfilter", sheet: this.name, field }); if (this.filters) delete this.filters[field]; return field; }
}

export class TableauViz extends HTMLElement {
  constructor() {
    super();
    const nameCol = CFG.nameCol || "ATTR(supplier_name)", xform = CFG.xform ? new Function("n", CFG.xform) : (n => n);
    const map = new Worksheet(this, CFG.mapName || "Supplier Map", [nameCol, "industry_sector", "Latitude", "Longitude"],
      async () => (await data()).filter((r, i) => !(CFG.dropEvery && i % CFG.dropEvery === 0))
        .map(r => ({ [nameCol]: CFG.useId ? r.id : xform(r.supplier_name), industry_sector: r.industry_sector, Latitude: r.latitude, Longitude: r.longitude }))
        .concat(CFG.extra ? CFG.extra.map(n => ({ [nameCol]: n, industry_sector: "Other" })) : []));
    const sectorCol = CFG.sectorCol || "industry_sector", sx = CFG.sectorXform ? new Function("n", CFG.sectorXform) : (n => n);
    const bars = new Worksheet(this, "Sector Bars", [sectorCol, "CNT(supplier_name)"], async () => {
      const m = {}; for (const r of await data()) m[r.industry_sector] = (m[r.industry_sector] || 0) + 1;
      return Object.entries(m).map(([k, v]) => ({ [sectorCol]: sx(k), "CNT(supplier_name)": v }))
        .concat(CFG.extraSector ? [{ [sectorCol]: CFG.extraSector, "CNT(supplier_name)": 3 }] : []);
    });
    this._sheets = CFG.noKey ? [bars] : [map, bars];
    /* Tier tiles as on the live dashboard: one KPI sheet per tier, plus a "Fab" tile the page has no tier for */
    if (CFG.tiers) for (const [sheet, label] of [["KPI - Tier 1", "Tier 1"], ["KPI - Tier 2", "Tier 2"], ["KPI - Tier 3", "Tier 3"], ["KPI - Support", "Support Organization"], ["KPI - Fab", "Fab"]])
      this._sheets.push(new Worksheet(this, sheet, ["Tier Classification", "AGG(% of Total )", "AGG(Company Count)"], async () => {
        const n = (await data()).filter(r => r.tier_label === label).length || 1;
        return [{ "Tier Classification": label, "AGG(% of Total )": n / 155, "AGG(Company Count)": n }];
      }));
  }
  connectedCallback() {
    if (this._init) return; this._init = true;
    LOG.push({ op: "load", src: this.src, toolbar: this.toolbar, height: this.height });
    this.style.display='block';this.innerHTML = '<div style="height:100%;min-height:200px;background:repeating-linear-gradient(45deg,#e8eef8,#e8eef8 12px,#dfe7f4 12px,#dfe7f4 24px);display:grid;place-items:center;font:14px sans-serif">Mock Tableau dashboard</div>';
    if (CFG.loadError) { setTimeout(() => this.dispatchEvent(new CustomEvent("vizloaderror", { detail: { message: JSON.stringify({ errorCode: "mock-load-error" }) } })), 150); return; }
    if (CFG.neverInteractive) return;
    setTimeout(() => {
      this.workbook = { activeSheet: { sheetType: "dashboard", name: "Industries Explorer", worksheets: this._sheets } };
      this.dispatchEvent(new CustomEvent("firstinteractive"));
    }, CFG.delay || 200);
  }
  _fire(ws) {
    const rows = ws.sel;
    this.dispatchEvent(new CustomEvent("markselectionchanged", { detail: { worksheet: ws, sheet: ws,
      getMarksAsync: async () => ({ data: [{ columns: ws.cols.map(f => ({ fieldName: f })), data: rows.map(r => ws.cols.map(f => ({ value: r[f] }))) }] }) } }));
  }
  /* test hook: a visitor clicks marks */
  async _userClick(sheet, values) {
    const ws = this._sheets.find(w => w.name === sheet), rows = await ws.rowsFn(), col = ws.cols[0];
    ws.sel = rows.filter(r => values.includes(String(r[col])));
    LOG.push({ op: "userclick", sheet, values });
    this._fire(ws);
  }
}
customElements.define("tableau-viz", TableauViz);
