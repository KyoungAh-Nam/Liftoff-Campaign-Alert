"""
Liftoff Daily Performance Report → Slack
-----------------------------------------
매일 자동으로 Liftoff API에서 어제 데이터를 가져와
캠페인 / 크리에이티브 / 소스앱 분석 결과를 Slack에 전송합니다.

Required GitHub Secrets:
  ACCOUNT_1_NAME     - 계정 이름 (ex: "Actionfit iOS")
  ACCOUNT_1_KEY      - Liftoff API Key
  ACCOUNT_1_SECRET   - Liftoff API Secret
  SLACK_CHANNEL_ID   - Slack 채널 ID (ex: C012AB3CD)
  SLACK_BOT_TOKEN    - Slack Bot Token (xoxb-...)
"""

import os
import csv
import io
import time
import requests
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

# ── Config ─────────────────────────────────────────────────────
ACCOUNTS = [
    {
        "name":       os.environ["ACCOUNT_1_NAME"],
        "api_key":    os.environ["ACCOUNT_1_KEY"],
        "api_secret": os.environ["ACCOUNT_1_SECRET"],
    },
    # 계정 추가 시 아래 주석 해제 + GitHub Secrets에 추가
    # {
    #     "name":       os.environ["ACCOUNT_2_NAME"],
    #     "api_key":    os.environ["ACCOUNT_2_KEY"],
    #     "api_secret": os.environ["ACCOUNT_2_SECRET"],
    # },
]

SLACK_CHANNEL_ID = os.environ["SLACK_CHANNEL_ID"]
SLACK_BOT_TOKEN  = os.environ["SLACK_BOT_TOKEN"]
BASE_URL         = "https://data.liftoff.io/api/v1"

# D7 ROAS 기준 (cohort_window=7)
COHORT_WINDOW = 7

# ROAS 기준 임계값 (%)
ROAS_GOOD    = 8.0   # 이상: Scale Up
ROAS_WARNING = 4.0   # 이상: Monitor
# 미만: Reduce


# ── 날짜 범위: 어제 (KST 기준) ────────────────────────────────
def get_yesterday():
    yesterday = datetime.now(KST) - timedelta(days=1)
    return yesterday.strftime("%Y-%m-%d")


# ── Liftoff API 함수들 ─────────────────────────────────────────

def get_events(api_key, api_secret):
    """계정의 이벤트 목록 조회 (IAP/Purchase 이벤트 ID 획득용)"""
    resp = requests.get(
        f"{BASE_URL}/events",
        auth=(api_key, api_secret),
        timeout=30,
    )
    if resp.status_code == 200:
        return resp.json()
    print(f"[WARN] 이벤트 조회 실패: {resp.status_code}")
    return []


def create_report(api_key, api_secret, group_by, date_str, event_ids=None):
    """
    Liftoff 리포트 생성 요청 (비동기) → report_id 반환

    Liftoff API는 동기가 아님:
      1. POST /reports  → report_id 받음
      2. GET  /reports/{id}/status  폴링
      3. GET  /reports/{id}/data  다운로드
    """
    payload = {
        "start_time":        date_str,
        "end_time":          date_str,
        "group_by":          group_by,
        "cohort_window":     COHORT_WINDOW,
        "timezone":          "Asia/Seoul",
        "format":            "csv",
        "remove_zero_rows":  True,
    }
    if event_ids:
        payload["event_ids"] = event_ids

    resp = requests.post(
        f"{BASE_URL}/reports",
        json=payload,
        auth=(api_key, api_secret),
        headers={"Content-Type": "application/json"},
        timeout=30,
    )

    if resp.status_code != 200:
        print(f"[ERROR] 리포트 생성 실패 ({group_by}): {resp.status_code} - {resp.text[:300]}")
        return None

    report_id = resp.json().get("id")
    print(f"[INFO] 리포트 생성됨 → ID: {report_id} | group_by: {group_by}")
    return report_id


def wait_for_report(api_key, api_secret, report_id, max_wait_sec=600, interval_sec=30):
    """리포트 완료될 때까지 폴링 (최대 10분 대기)"""
    elapsed = 0
    while elapsed < max_wait_sec:
        time.sleep(interval_sec)
        elapsed += interval_sec

        resp = requests.get(
            f"{BASE_URL}/reports/{report_id}/status",
            auth=(api_key, api_secret),
            timeout=30,
        )
        state = resp.json().get("state", "unknown")
        print(f"[INFO] 리포트 {report_id} 상태: {state} ({elapsed}초 경과)")

        if state == "completed":
            return True
        if state in ("failed", "cancelled"):
            print(f"[ERROR] 리포트 {report_id} 실패: {state}")
            return False

    print(f"[ERROR] 리포트 {report_id} 타임아웃 ({max_wait_sec}초 초과)")
    return False


def download_report(api_key, api_secret, report_id):
    """완료된 리포트 CSV 다운로드 → row 딕셔너리 리스트 반환"""
    resp = requests.get(
        f"{BASE_URL}/reports/{report_id}/data",
        auth=(api_key, api_secret),
        timeout=60,
    )
    if resp.status_code != 200:
        print(f"[ERROR] 리포트 다운로드 실패: {resp.status_code}")
        return []

    reader = csv.DictReader(io.StringIO(resp.text))
    rows = list(reader)
    print(f"[INFO] 리포트 {report_id} 다운로드 완료 → {len(rows)}행")
    return rows


def fetch_all_reports(api_key, api_secret, date_str, event_ids):
    """
    3종 리포트를 병렬 생성 후 순차 폴링/다운로드
    Returns: (general_rows, creative_rows, publisher_rows)
    """
    report_configs = [
        ("General",   ["apps", "campaigns"]),
        ("Creative",  ["apps", "campaigns", "creatives"]),
        ("Publisher", ["apps", "campaigns", "publisher"]),
    ]

    # 1단계: 3개 리포트 동시 생성 요청
    report_ids = {}
    for name, group_by in report_configs:
        rid = create_report(api_key, api_secret, group_by, date_str, event_ids)
        if rid:
            report_ids[name] = rid
        time.sleep(2)  # 생성 요청 간 짧은 간격

    # 2단계: 각 리포트 완료 대기 후 다운로드
    results = {}
    for name, rid in report_ids.items():
        success = wait_for_report(api_key, api_secret, rid)
        if success:
            results[name] = download_report(api_key, api_secret, rid)
        else:
            results[name] = []
        time.sleep(1)

    return (
        results.get("General",   []),
        results.get("Creative",  []),
        results.get("Publisher", []),
    )


# ── 분석 함수들 ────────────────────────────────────────────────

def safe_float(val, default=0.0):
    try:
        return float(val) if val not in (None, "", "N/A", "-") else default
    except (ValueError, TypeError):
        return default


def calc_roas(revenue, spend):
    s = safe_float(spend)
    return (safe_float(revenue) / s * 100) if s > 0 else 0.0


def find_revenue_col(row):
    """
    API 응답에서 revenue 컬럼명을 동적으로 찾음
    (이벤트 이름이 컬럼이 되기 때문에 계정마다 다를 수 있음)
    """
    revenue_keywords = ["revenue", "purchase", "payment", "iap", "transaction"]
    for key in row.keys():
        if any(kw in key.lower() for kw in revenue_keywords):
            return key
    return None


def aggregate_rows(rows, key_fields):
    """지정 key_fields 기준으로 숫자 컬럼 합산"""
    result = {}
    for row in rows:
        key = tuple(row.get(f, "") for f in key_fields)
        if key not in result:
            result[key] = {}
        for col, val in row.items():
            if col not in key_fields:
                try:
                    result[key][col] = result[key].get(col, 0.0) + float(val)
                except (ValueError, TypeError):
                    if col not in result[key]:
                        result[key][col] = val
    return result


# ── 분석 & Slack 메시지 생성 ──────────────────────────────────

def build_slack_message(general_rows, creative_rows, publisher_rows, account_name, date_str):
    lines = []

    # ── 헤더 ──────────────────────────────────────────────────
    lines.append(f"*📊 Liftoff Daily Analysis — {account_name}*")
    lines.append(f"_Date: {date_str} | D{COHORT_WINDOW} ROAS 기준_")
    lines.append("─" * 40)

    # ── 전체 요약 ──────────────────────────────────────────────
    total_spend    = sum(safe_float(r.get("spend", 0))       for r in general_rows)
    total_installs = sum(safe_float(r.get("installs", 0))    for r in general_rows)
    total_imps     = sum(safe_float(r.get("impressions", 0)) for r in general_rows)

    # revenue 컬럼 탐색
    rev_col = find_revenue_col(general_rows[0]) if general_rows else None
    total_revenue = sum(safe_float(r.get(rev_col, 0)) for r in general_rows) if rev_col else 0.0
    overall_roas  = calc_roas(total_revenue, total_spend)
    overall_cpi   = total_spend / total_installs if total_installs > 0 else 0.0

    lines.append(
        f"*전체 요약* | Spend: *${total_spend:,.0f}* | "
        f"Revenue: *${total_revenue:,.2f}* | "
        f"ROAS: *{overall_roas:.2f}%* | "
        f"Installs: *{total_installs:,.0f}* | "
        f"CPI: *${overall_cpi:.2f}*"
    )
    lines.append("")

    # ── 캠페인 레벨 ────────────────────────────────────────────
    lines.append("*캠페인 레벨*")
    camp_data = aggregate_rows(general_rows, ["campaign_id", "app_id"])

    scale_up, monitor, reduce = [], [], []
    for (camp_id, app_id), v in camp_data.items():
        spend    = v.get("spend", 0.0)
        revenue  = v.get(rev_col, 0.0) if rev_col else 0.0
        installs = v.get("installs", 0.0)
        if spend < 10:
            continue
        r   = calc_roas(revenue, spend)
        cpi = spend / installs if installs > 0 else 0.0
        pct = spend / total_spend * 100 if total_spend > 0 else 0.0
        entry = f"• *{camp_id}* — ROAS {r:.2f}%, CPI ${cpi:.2f}, Installs {installs:,.0f}, Spend ${spend:,.0f} ({pct:.1f}%)"

        if r >= ROAS_GOOD:
            scale_up.append((r, entry))
        elif r >= ROAS_WARNING:
            monitor.append((r, entry))
        else:
            reduce.append((r, entry))

    if scale_up:
        lines.append(":large_green_circle: *Scale Up*")
        for _, e in sorted(scale_up, reverse=True):
            lines.append(e)
    if monitor:
        lines.append(":large_yellow_circle: *Monitor / Hold*")
        for _, e in sorted(monitor, reverse=True):
            lines.append(e)
    if reduce:
        lines.append(":red_circle: *Reduce / Review*")
        for _, e in sorted(reduce):
            lines.append(e)
    lines.append("")

    # ── 크리에이티브 레벨 ──────────────────────────────────────
    lines.append("*크리에이티브 레벨*")
    cr_data = aggregate_rows(creative_rows, ["creative_id"])
    cr_rev_col = find_revenue_col(creative_rows[0]) if creative_rows else rev_col

    cr_sig = [
        (cid, v) for (cid,), v in cr_data.items()
        if v.get("spend", 0) > 50
    ]
    cr_sorted = sorted(
        cr_sig,
        key=lambda x: calc_roas(x[1].get(cr_rev_col, 0) if cr_rev_col else 0, x[1].get("spend", 0)),
        reverse=True,
    )

    lines.append(":trophy: *Top 5 (ROAS 기준)*")
    for cid, v in cr_sorted[:5]:
        r        = calc_roas(v.get(cr_rev_col, 0) if cr_rev_col else 0, v.get("spend", 0))
        installs = v.get("installs", 0)
        spend    = v.get("spend", 0)
        lines.append(f"• `{str(cid)[:45]}` — ROAS {r:.2f}%, Installs {installs:,.0f}, Spend ${spend:,.0f}")

    # 최하위 크리에이티브
    cr_worst = [x for x in cr_sorted if x[1].get("spend", 0) > 300]
    if cr_worst:
        lines.append(":warning: *예산 낭비 크리에이티브 (spend > $300)*")
        for cid, v in cr_worst[-3:]:
            r     = calc_roas(v.get(cr_rev_col, 0) if cr_rev_col else 0, v.get("spend", 0))
            spend = v.get("spend", 0)
            pct   = spend / total_spend * 100 if total_spend > 0 else 0.0
            lines.append(f"• `{str(cid)[:45]}` — ROAS {r:.2f}%, Spend ${spend:,.0f} ({pct:.1f}%)")
    lines.append("")

    # ── 소스앱 레벨 ────────────────────────────────────────────
    lines.append("*소스앱 레벨*")
    pub_data = aggregate_rows(publisher_rows, ["publisher_app_store_id", "publisher_name"])
    pub_rev_col = find_revenue_col(publisher_rows[0]) if publisher_rows else rev_col

    pub_sig = [
        ((sid, sname), v)
        for (sid, sname), v in pub_data.items()
        if v.get("spend", 0) > 100 and sid
    ]
    pub_sorted = sorted(
        pub_sig,
        key=lambda x: calc_roas(x[1].get(pub_rev_col, 0) if pub_rev_col else 0, x[1].get("spend", 0)),
        reverse=True,
    )

    lines.append(":trophy: *Top 5 소스앱*")
    for (sid, sname), v in pub_sorted[:5]:
        r        = calc_roas(v.get(pub_rev_col, 0) if pub_rev_col else 0, v.get("spend", 0))
        installs = v.get("installs", 0)
        spend    = v.get("spend", 0)
        label    = sname if sname else sid
        lines.append(f"• *{label}* — ROAS {r:.2f}%, Installs {installs:,.0f}, Spend ${spend:,.0f}")

    if pub_sorted:
        lines.append(":no_entry: *블랙리스트 후보 (spend > $200)*")
        pub_worst = [(k, v) for k, v in pub_sorted if v.get("spend", 0) > 200]
        for (sid, sname), v in pub_worst[-3:]:
            r     = calc_roas(v.get(pub_rev_col, 0) if pub_rev_col else 0, v.get("spend", 0))
            spend = v.get("spend", 0)
            label = sname if sname else sid
            lines.append(f"• *{label}* (`{sid}`) — ROAS {r:.2f}%, Spend ${spend:,.0f}")

    lines.append("")
    lines.append(f"_Generated by Cowork · {datetime.now(KST).strftime('%Y-%m-%d %H:%M')} KST_")

    return "\n".join(lines)


# ── Slack 전송 ──────────────────────────────────────────────────

def send_to_slack(text):
    url     = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        "Content-Type":  "application/json; charset=utf-8",
    }
    payload = {
        "channel":      SLACK_CHANNEL_ID,
        "text":         text,
        "mrkdwn":       True,
        "unfurl_links": False,
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    data = resp.json()
    if not data.get("ok"):
        print(f"[ERROR] Slack 전송 실패: {data.get('error')}")
        raise RuntimeError(f"Slack error: {data.get('error')}")
    print(f"[OK] Slack 전송 완료 → {SLACK_CHANNEL_ID}")


# ── Main ────────────────────────────────────────────────────────

def main():
    date_str = get_yesterday()
    print(f"[INFO] 분석 날짜: {date_str}")

    for account in ACCOUNTS:
        name       = account["name"]
        api_key    = account["api_key"]
        api_secret = account["api_secret"]
        print(f"\n[INFO] ── 계정 처리 중: {name} ──")

        try:
            # 이벤트 목록 조회 (revenue 컬럼 확보용)
            events    = get_events(api_key, api_secret)
            event_ids = [e["id"] for e in events] if events else None
            if event_ids:
                print(f"[INFO] 이벤트 {len(event_ids)}개 감지: {[e['name'] for e in events]}")
            else:
                print("[WARN] 이벤트 없음 — revenue 데이터 없이 진행")

            # 3종 리포트 생성 & 다운로드
            general_rows, creative_rows, publisher_rows = fetch_all_reports(
                api_key, api_secret, date_str, event_ids
            )

            if not general_rows:
                msg = f":warning: *{name}* — {date_str} 데이터 없음. API 설정 또는 날짜 확인 필요."
                print(f"[WARN] {msg}")
                send_to_slack(msg)
                continue

            # 분석 & Slack 전송
            message = build_slack_message(
                general_rows, creative_rows, publisher_rows, name, date_str
            )
            send_to_slack(message)

        except Exception as e:
            error_msg = f":x: *{name}* 리포트 처리 중 오류: `{e}`"
            print(f"[ERROR] {error_msg}")
            try:
                send_to_slack(error_msg)
            except Exception:
                pass

        time.sleep(2)  # 계정 간 간격

    print("\n[DONE] 모든 계정 처리 완료.")


if __name__ == "__main__":
    main()
