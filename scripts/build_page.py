"""Embeds the metrics CSVs into src/metrics.src.html and writes build/metrics_plain.html (never committed)."""
import json, os, sys

def js_str(text):
    return json.dumps(text).replace("</", "<\\/")

def main():
    src = open("src/metrics.src.html", encoding="utf8").read()
    read = lambda p: open(p, encoding="utf8").read() if os.path.exists(p) else "date,sessions,engagement,task_success,friction_score,feedback_score\n"
    score = read("data/feedback_score.csv")
    daily = read("data/clarity_daily.csv") if os.path.exists("data/clarity_daily.csv") else "date,source,dimension,metric,field,value\n"
    inject = f"<script>window.__SCORE_CSV__={js_str(score)};window.__DAILY_CSV__={js_str(daily)};</script>\n"
    marker = "<script>\n(function(){"
    if src.count(marker) != 1:
        sys.exit("marker for data injection not found")
    os.makedirs("build", exist_ok=True)
    open("build/metrics_plain.html", "w", encoding="utf8").write(src.replace(marker, inject + marker))
    print("wrote build/metrics_plain.html")

if __name__ == "__main__":
    main()
