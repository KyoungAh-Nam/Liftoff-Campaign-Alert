import os
import time
import requests
from datetime import datetime, timedelta, timezone

# ── 설정 (GitHub Secrets에서 자동으로 읽어옴) ──────────────────
ACCOUNTS = [
    # 어카운트 여러 개면 여기에 추가
    {"name": os.environ["ACCOUNT_1_NAME"], "api_key": os.environ["ACCOUNT_1_KEY"], "api_secret": os.environ["ACCOUNT_1_SECRET"]},
    # {"name": os.environ["ACCOUNT_2_NAME"], "api_key": os.environ["ACCOUNT_2_KEY"], "api_secret": os.environ["ACCOUNT_2_SECRET"]},
]
SLACK_CHANNEL_ID = os.environ["SLACK_CHANNEL_ID"]
SLACK_BOT_TOKEN  = os.environ["SLACK_BOT_TOKEN"]

BASE_URL = "https://data.liftoff.io/api/v1"

# ── 알럿 임계값 ────────────────────────────────────────────────
THRESHOLDS = {
    "ctr_change_pct":     20,   # CTR ±20% change
    "cpi_increase_pct":   20,   # CPI +20% spike
    "install_drop_pct":  -20,   # Installs -20% drop
}

# ── 시간 설정: 오늘 vs 어제 같은 시간대 ──────────────────────
def get_time_ranges():
    now = datetime.now(timezone.utc)
    today_start    = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    yesterday_same  = yesterday_start + (now - today_start)
    fmt = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
    return fmt(today_start), fmt(now), fmt(yesterday_start), fmt(yesterday_same)

# ── Liftoff API 호출 ───────────────────────────────────────────
def create_report(api_key, api_secret, start, end):
    res = requests.post(
        f"{BASE_URL}/reports",
        json={"start_time": start, "end_time": end, "group_by": ["apps", "campaigns"], "format": "json"},
        auth=(api_key, api_secret),
    )
    res.raise_for_status()
    return res.json()["id"]

def poll_report(api_key, api_secret, report_id, timeout=300):
    for _ in range(timeout // 10):
        time.sleep(10)
        res = requests.get(f"{BASE_URL}/reports/{report_id}/status", auth=(api_key, api_secret))
        state = res.json()["state"]
        if state == "completed":
            return True
        if state in ("failed", "cancelled"):
            return False
    return False

def get_report_data(api_key, api_secret, report_id):
    res = requests.get(f"{BASE_URL}/reports/{report_id}/data", auth=(api_key, api_secret))
    res.raise_for_status()
    data = res.json()
    if not data.get("rows"):
        return []
    cols = data["columns"]
    return [dict(zip(cols, row)) for row in data["rows"]]

def get_campaigns(api_key, api_secret):
    res = requests.get(f"{BASE_URL}/campaigns", auth=(api_key, api_secret))
    return {c["id"]: c["name"] for c in res.json()} if res.ok else {}

# ── 알럿 감지 ──────────────────────────────────────────────────
def detect_alerts(today_rows, yest_rows, camp_names, account_name):
    alerts = []
    yest_map = {r["campaign_id"]: r for r in yest_rows}

    for t in today_rows:
        y = yest_map.get(t["campaign_id"])
        if not y:
            continue
        name = camp_names.get(t["campaign_id"], t["campaign_id"])
        chg  = lambda a, b: ((a - b) / b * 100) if b else 0

        ctr_chg     = chg(t.get("ctr", 0),      y.get("ctr", 0))
        cpi_chg     = chg(t.get("cpi", 0),      y.get("cpi", 0))
        install_chg = chg(t.get("installs", 0), y.get("installs", 0))

        if abs(ctr_chg) >= THRESHOLDS["ctr_change_pct"]:
            alerts.append({
                "account": account_name, "campaign": name, "type": "CTR Anomaly",
                "severity": "critical" if abs(ctr_chg) >= 40 else "warning",
                "detail": f"CTR {'▲' if ctr_chg>0 else '▼'} {abs(ctr_chg):.1f}% ({y['ctr']:.3f} → {t['ctr']:.3f})"
            })
        if cpi_chg >= THRESHOLDS["cpi_increase_pct"]:
            alerts.append({
                "account": account_name, "campaign": name, "type": "CPI Spike",
                "severity": "critical" if cpi_chg >= 40 else "warning",
                "detail": f"CPI ▲ {cpi_chg:.1f}% (${y['cpi']:.2f} → ${t['cpi']:.2f})"
            })
        if install_chg <= THRESHOLDS["install_drop_pct"]:
            alerts.append({
                "account": account_name, "campaign": name, "type": "Install Drop",
                "severity": "critical" if install_chg <= -35 else "warning",
                "detail": f"Installs ▼ {abs(install_chg):.1f}% ({int(y['installs'])} → {int(t['installs'])})"
            })
    return alerts

# ── Slack 전송 ─────────────────────────────────────────────────
def send_slack(alerts, account_count):
    if not alerts:
        msg = f"✅ *Liftoff Campaign Alert* | {datetime.now().strftime('%Y-%m-%d %H:%M')} KST\n\nAll campaigns are performing normally 🎉\n> {account_count} account(s) checked"
    else:
        critical = [a for a in alerts if a["severity"] == "critical"]
        warning  = [a for a in alerts if a["severity"] == "warning"]
        lines = []
        for a in alerts:
            icon = "🔴" if a["severity"] == "critical" else "🟡"
            label = "CRITICAL" if a["severity"] == "critical" else "WARNING"
            lines.append(f"{icon} *[{label}] {a['type']}* `{a['account']}` - {a['campaign']}\n   └ {a['detail']}")
        msg = (
            f"🚨 *Liftoff Campaign Alert* | {datetime.now().strftime('%Y-%m-%d %H:%M')} KST\n\n"
            + "\n".join(lines)
            + f"\n\n> 🔴 Critical: {len(critical)}  🟡 Warning: {len(warning)} | {account_count} account(s) checked"
        )

    res = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        json={"channel": SLACK_CHANNEL_ID, "text": msg, "mrkdwn": True},
    )
    print("Slack 전송:", res.json().get("ok"), res.json().get("error", ""))

# ── 메인 ───────────────────────────────────────────────────────
def main():
    today_start, now_str, yest_start, yest_same = get_time_ranges()
    print(f"📊 분석 기간: 오늘 {today_start} ~ {now_str} vs 어제 {yest_start} ~ {yest_same}")

    all_alerts = []

    for acc in ACCOUNTS:
        print(f"\n🔍 {acc['name']} 체크 중...")
        try:
            camp_names = get_campaigns(acc["api_key"], acc["api_secret"])

            # 오늘 리포트
            tid = create_report(acc["api_key"], acc["api_secret"], today_start, now_str)
            # 어제 리포트
            yid = create_report(acc["api_key"], acc["api_secret"], yest_start, yest_same)

            if poll_report(acc["api_key"], acc["api_secret"], tid) and \
               poll_report(acc["api_key"], acc["api_secret"], yid):
                today_rows = get_report_data(acc["api_key"], acc["api_secret"], tid)
                yest_rows  = get_report_data(acc["api_key"], acc["api_secret"], yid)
                alerts = detect_alerts(today_rows, yest_rows, camp_names, acc["name"])
                all_alerts.extend(alerts)
                print(f"  → 알럿 {len(alerts)}개 감지")
            else:
                print(f"  → 리포트 생성 실패")
        except Exception as e:
            print(f"  → 오류: {e}")

    send_slack(all_alerts, len(ACCOUNTS))
    print("\n✅ 완료!")

if __name__ == "__main__":
    main()
