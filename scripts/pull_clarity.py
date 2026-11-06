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

def clarity(date):
    tok = os.environ["CLARITY_TOKEN"]
    h = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    rows = []
    for params, dim in (({"numOfDays": "1"}, False), ({"numOfDays": "1", "dimension1": "Device"}, True)):  # 2 of 10 daily calls
        r = requests.get(CLARITY_URL, params=params, headers=h, timeout=60)
        r.raise_for_status()
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
    date = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).date().isoformat()  # Clarity "last 24h" is UTC
    rows = clarity(date) + goatcounter(date)
    exists = os.path.exists(OUT)
    os.makedirs("data", exist_ok=True)
    with open(OUT, "a", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(FIELDS)
        w.writerows(rows)
    print(f"{len(rows)} rows for {date}")

if __name__ == "__main__":
    sys.exit(main())
