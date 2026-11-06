"""Builds data/feedback_score.csv (one row per date) from data/clarity_daily.csv."""
import csv, collections

SRC, OUT = "data/clarity_daily.csv", "data/feedback_score.csv"

def pick(rows, metric_sub, field_sub=None):
    for r in rows:
        if r["source"] == "clarity" and r["dimension"] == "all" and metric_sub in r["metric"].lower() \
           and (field_sub is None or field_sub in r["field"].lower()):
            return r["value"]
    return None

def ev(rows, name):
    return sum(r["value"] for r in rows if r["source"] == "goatcounter" and r["field"] == name)

def clamp(x): return max(0.0, min(100.0, x))

def main():
    by = collections.defaultdict(list)
    for r in csv.DictReader(open(SRC)):
        r["value"] = float(r["value"]); by[r["date"]].append(r)
    out = []
    for d, rows in sorted(by.items()):
        sessions = pick(rows, "traffic", "totalsessioncount") or 0
        scroll = pick(rows, "scroll", "average") or pick(rows, "scroll")
        eng_s = pick(rows, "engagement", "active") or pick(rows, "engagement", "time") or pick(rows, "engagement")
        friction_n = sum(pick(rows, m, "subtotal") or pick(rows, m, "sessionsCount".lower()) or 0
                         for m in ("rageclick", "deadclick", "errorclick", "quickback"))
        eng = None if scroll is None or eng_s is None else (clamp(scroll) + clamp(eng_s / 120 * 100)) / 2   # 120 s active time = 100
        success = clamp(ev(rows, "task_success") / sessions * 100) if sessions and any(r["source"] == "goatcounter" for r in rows) else None
        friction = clamp(100 - friction_n / sessions * 100 * 5) if sessions else None                          # 20% friction sessions = 0
        parts = [(0.4, eng), (0.4, success), (0.2, friction)]
        have = [(w, v) for w, v in parts if v is not None]
        score = sum(w * v for w, v in have) / sum(w for w, _ in have) if have else None
        out.append([d, int(sessions), eng, success, friction, score])
    with open(OUT, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["date", "sessions", "engagement", "task_success", "friction_score", "feedback_score"])
        for r in out: w.writerow([r[0], r[1]] + ["" if v is None else round(v, 1) for v in r[2:]])
    print(f"{len(out)} days written")

if __name__ == "__main__":
    main()
