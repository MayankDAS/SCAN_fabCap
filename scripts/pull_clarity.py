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
    r = requests.get(f"https://{site}.goatcounter.com/api/v0/stats/hits", params={"start": date, "end": date, "limit": 100},
                     headers={"Authorization": f"Bearer {tok}"}, timeout=60)
    r.raise_for_status()
    return [[date, "goatcounter", "all", "event", h["path"].removeprefix("ev/"), float(h["count"])]
            for h in r.json().get("hits", []) if h.get("event")]

def main():
    now = dt.datetime.now(dt.timezone.utc)
    date = now.date().isoformat()  # each run stores a trailing 24 hour snapshot under the run date, replaced by later runs
    with_device = now.hour >= 20 or os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
    rows = clarity(date, with_device) + goatcounter(date)
    os.makedirs("data", exist_ok=True)
    groups = {(r[1], r[2] == "all") for r in rows}
    kept = []
    if os.path.exists(OUT):  # a re run replaces the same date and kind of rows instead of duplicating them
        with open(OUT, newline="") as f:
            kept = [r for r in csv.reader(f)][1:]
        kept = [r for r in kept if r and not (r[0] == date and (r[1], r[2] == "all") in groups)]
    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        w.writerows(kept)
        w.writerows(rows)
    print(f"{len(rows)} rows for {date} (device breakdown: {with_device})")

if __name__ == "__main__":
    sys.exit(main())
