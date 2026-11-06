"""Daily pull of Clarity (and optional GoatCounter event) data into data/clarity_daily.csv (long format)."""
import csv, datetime as dt, os, sys, requests

CLARITY_URL = "https://www.clarity.ms/export-data/api/v1/project-live-insights"
OUT = "data/clarity_daily.csv"
FIELDS = ["date", "source", "dimension", "metric", "field", "value"]

def flatten(payload, date, dim):
    rows = []
    for block in payload or []:
        metric = block.get("metricName", "")
        for item in block.get("information", []):
            label = next((str(item[k]) for k in ("Device", "OS", "Browser", "Source", "Medium", "Country") if k in item), "all")
            for k, v in item.items():
                try:
                    rows.append([date, "clarity", label if dim else "all", metric, k, float(v)])
                except (TypeError, ValueError):
                    pass
    return rows

def clarity(date, with_device):
    tok = os.environ["CLARITY_TOKEN"].strip().strip('"\'')
    if tok.lower().startswith("bearer "):
        tok = tok[7:].strip()
    print(f"Clarity token length: {len(tok)} (JWT tokens are long, hundreds of characters)")
    h = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    rows = []
    calls = [({"numOfDays": "1"}, False)]
    if with_device:  # keeps total calls per day under Clarity's limit of 10
        calls.append(({"numOfDays": "1", "dimension1": "Device"}, True))
    for params, dim in calls:
        r = requests.get(CLARITY_URL, params=params, headers=h, timeout=60)
        if not r.ok:
            sys.exit(f"Clarity API error {r.status_code}: {r.text[:300]}")
        rows += flatten(r.json(), date, dim)
    return rows

def goatcounter(date):
    tok, site = os.environ.get("GOATCOUNTER_TOKEN"), os.environ.get("GOATCOUNTER_SITE", "fabscan")
    if not tok:
        return []
    r = requests.get(f"https://{site}.goatcounter.com/api/v0/stats/hits", params={"start": f"{date}T00:00:00Z", "end": f"{date}T23:59:59Z", "limit": 100},
                     headers={"Authorization": f"Bearer {tok}"}, timeout=60)
    r.raise_for_status()
    rows = [[date, "goatcounter", "all", "event", h["path"].removeprefix("ev/"), float(h["count"])]
            for h in r.json().get("hits", []) if h.get("event")]
    t = requests.get(f"https://{site}.goatcounter.com/api/v0/stats/total", params={"start": f"{date}T00:00:00Z", "end": f"{date}T23:59:59Z"},
                     headers={"Authorization": f"Bearer {tok}"}, timeout=60)
    t.raise_for_status()
    tj = t.json()
    wk = requests.get(f"https://{site}.goatcounter.com/api/v0/stats/total", params={"start": f"{date}T00:00:00Z"[:0] + (dt.date.fromisoformat(date) - dt.timedelta(days=30)).isoformat() + "T00:00:00Z", "end": f"{date}T23:59:59Z"},
                      headers={"Authorization": f"Bearer {tok}"}, timeout=60)
    print("GoatCounter last 30 days (diagnostic):", {k: v for k, v in wk.json().items() if k.startswith("total")} if wk.ok else wk.status_code)
    print("GoatCounter total response:", {k: tj[k] for k in tj if k.startswith("total")})
    for field, key in (("visitors", "total"), ("events", "total_events")):
        if isinstance(tj.get(key), (int, float)):
            rows.append([date, "goatcounter", "all", "total", field, float(tj[key])])
    return rows

def main():
    now = dt.datetime.now(dt.timezone.utc)
    date = now.date().isoformat()  # each run stores a trailing 24 hour snapshot under the run date, replaced by later runs
    with_device = True  # 2 calls per run, 2 runs a day, well under Clarity's limit of 10 per day
    err = None
    try:
        rows = clarity(date, with_device)
    except SystemExit as e:  # keep going so GoatCounter data is still saved when Clarity is rate limited
        err, rows = e, []
        print(f"Clarity skipped: {e}")
    yday = (now.date() - dt.timedelta(days=1)).isoformat()  # GoatCounter has no daily call limit, so refresh yesterday too
    rows += goatcounter(yday) + goatcounter(date)
    os.makedirs("data", exist_ok=True)
    groups = {(r[0], r[1], r[2] == "all") for r in rows}
    kept = []
    if os.path.exists(OUT):  # a re run replaces the same date and kind of rows instead of duplicating them
        with open(OUT, newline="") as f:
            kept = [r for r in csv.reader(f)][1:]
        kept = [r for r in kept if r and not ((r[0], r[1], r[2] == "all") in groups)]
    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        w.writerows(kept)
        w.writerows(rows)
    print(f"{len(rows)} rows for {date} (device breakdown: {with_device})")
    if err is not None:
        sys.exit(err.code)

if __name__ == "__main__":
    sys.exit(main())
