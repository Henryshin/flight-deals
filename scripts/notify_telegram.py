"""
docs/data/deals.json ('변동 항목' = 최근 평균 대비 DEAL_THRESHOLD 이상 하락한 특가)을
읽어 텔레그램으로 알린다. build.yml이 대시보드 데이터를 만든 직후 실행한다.

환경변수:
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  - 하나라도 없으면 조용히 건너뜀 (선택 기능)
  TELEGRAM_MIN_DISCOUNT_PCT             - 이 값(%) 이상인 특가만 알림 (기본값은 deals.json
                                           자체 기준인 15와 동일해, 지정하지 않으면 deals.json
                                           에 뜨는 모든 항목을 알린다)

data/telegram_notified.json 에 이미 알린 항목의 할인율을 기록해 중복 알림을 막는다.
같은 날짜쌍이라도 할인율이 RENOTIFY_MIN_DELTA_PCT 이상 더 떨어지면 다시 알리고,
더 이상 특가 기준을 충족하지 않으면 기록에서 지워 나중에 재하락 시 다시 알릴 수 있게 한다.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.parent
DEALS_FILE = ROOT / "docs" / "data" / "deals.json"
STATE_FILE = ROOT / "data" / "telegram_notified.json"

MIN_DISCOUNT_PCT = float(os.environ.get("TELEGRAM_MIN_DISCOUNT_PCT", "15"))
RENOTIFY_MIN_DELTA_PCT = 5.0


def deal_key(d):
    r = d["route"]
    return f"{r['origin']}-{r['destination']}-{d['depart_date']}-{d['return_date']}-{d.get('stops', '')}"


def should_notify(deal, prev_state):
    """이미 이 항목을 알린 적이 있고, 그 이후로 충분히 더 싸지지 않았으면 건너뛴다."""
    if prev_state is None:
        return True
    return deal["discount_pct"] >= prev_state["discount_pct"] + RENOTIFY_MIN_DELTA_PCT


def format_message(d):
    r = d["route"]
    label = r.get("label") or f"{r['origin']}->{r['destination']}"
    return (
        f"✈️ 특가 항공권: {label}\n"
        f"{d['depart_date']}({d['depart_weekday']}) → {d['return_date']}({d['return_weekday']}), "
        f"{d['nights']}박 {d['days']}일 (연차 {d['leave_days']}개)\n"
        f"현재가 {d['current_price']:,}원 - 최근 평균 {d['avg_price']:,}원 대비 {d['discount_pct']}% ↓\n"
        f"{d.get('airline') or '항공사 미상'} · {d.get('stops') if d.get('stops') not in (None, '') else '직항/경유 미상'}\n"
        f"{d['booking_url']}"
    )


def send_message(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        return True
    except urllib.error.URLError as e:
        print(f"텔레그램 전송 실패: {e}", file=sys.stderr)
        return False


def load_state():
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID 미설정 - 텔레그램 알림 건너뜀")
        return

    if not DEALS_FILE.exists():
        print("deals.json 없음 - 건너뜀")
        return
    deals = json.loads(DEALS_FILE.read_text(encoding="utf-8"))

    state = load_state()
    current_keys = set()
    sent = 0
    for d in deals:
        if d["discount_pct"] < MIN_DISCOUNT_PCT:
            continue
        key = deal_key(d)
        current_keys.add(key)
        if not should_notify(d, state.get(key)):
            continue
        if send_message(token, chat_id, format_message(d)):
            state[key] = {"discount_pct": d["discount_pct"], "current_price": d["current_price"]}
            sent += 1

    for key in list(state.keys()):
        if key not in current_keys:
            del state[key]  # 더 이상 특가가 아님 - 재하락 시 다시 알리도록 기록 제거

    save_state(state)
    print(f"특가 {len(deals)}건 중 {sent}건 알림 전송")


if __name__ == "__main__":
    main()
