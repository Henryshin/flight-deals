"""
data/prices.csv + data/routes.json (+ data/collect_status.json) 을 읽어
docs/data/deals.json, routes_status.json, history.json, matrix.json 등을 생성
(GitHub Pages가 읽는 정적 파일).

지표 구분:
- deals.json: '이 노선의 최근 연휴 시세 평균' 대비 하락(변동성 신호). 노선 내부 비교용.
- matrix.json: 연휴 윈도우별 '그 연휴의 통상 시세 대비 현재 최저가 특가율'.
  통상 시세보다 싼 표(프로모션/숨은 특가)가 남아있는 곳을 찾는 지표 (연휴 특가 보드의 데이터).
"""
import csv
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).parent.parent))

from collector.google_flights_crawler import build_booking_url
from holidays import KOREAN_HOLIDAYS, get_holiday_windows

ROOT = Path(__file__).parent.parent
ROUTES_FILE = ROOT / "data" / "routes.json"
PRICES_FILE = ROOT / "data" / "prices.csv"
STATUS_FILE = ROOT / "data" / "collect_status.json"
OUT_DIR = ROOT / "docs" / "data"

DEAL_THRESHOLD = 0.15
MIN_HISTORY_POINTS = 3
LOOKBACK_DAYS = 30
# 평시(비연휴) 기준가 룩백. 기준가는 천천히 변하므로 연휴 시세보다 길게 잡는다.
LOOKBACK_BASELINE_DAYS = 60
# 할증률 신뢰도 티어: A = 기준가 표본 3+ & 윈도우 날짜쌍 2+, B = 기준가 1~2, C = 기준가 없음
TIER_A_MIN_BASELINE = 3
TIER_A_MIN_WINDOW = 2

WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]
HOLIDAY_DATES = {date.fromisoformat(d) for days in KOREAN_HOLIDAYS.values() for d in days}


def count_leave_days(depart: date, return_: date) -> int:
    """여행 기간 중 평일이면서 공휴일이 아닌 날 수 (= 연차를 써야 하는 날 수)."""
    n = 0
    d = depart
    while d <= return_:
        if d.weekday() < 5 and d not in HOLIDAY_DATES:
            n += 1
        d += timedelta(days=1)
    return n


def _day(row):
    """collected_at 의 날짜 부분 (구 스키마는 날짜만, 신 스키마는 UTC 타임스탬프)."""
    return (row.get("collected_at") or "")[:10]


def load_prices():
    if not PRICES_FILE.exists():
        return []
    with open(PRICES_FILE, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    # 같은 날 여러 런이 재수집한 동일 (노선, 날짜쌍, stops) 관측은 마지막 것만 유지.
    # (하루 ~6회 크론이 같은 후보를 다시 긁으므로, dedup 없이는 표본수/평균이 부풀려짐)
    dedup = {}
    for row in rows:
        key = (
            row["origin"], row["destination"],
            row["depart_date"], row["return_date"],
            row.get("stops") or "", _day(row),
        )
        prev = dedup.get(key)
        if prev is None or (row.get("collected_at") or "") >= (prev.get("collected_at") or ""):
            dedup[key] = row
    return list(dedup.values())


def load_collect_status():
    """collect.py 가 남긴 노선별 수집 상태 ("O-D" -> {status, detail, ...})."""
    if not STATUS_FILE.exists():
        return {}
    try:
        data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    routes = data.get("routes")
    return routes if isinstance(routes, dict) else {}


def route_key(r):
    return (r["origin"], r["destination"])


def monitor_id(route):
    """모니터(route 엔트리) 식별자. id가 있으면 그것, 없으면 "O-D".

    프론트엔드 rk(r) = r.id || (r.origin + '-' + r.destination) 와 반드시 일치해야 한다.
    """
    return route.get("id") or f"{route['origin']}-{route['destination']}"


def _row_nights(row):
    """행의 여행 박수(= 귀국일 - 출발일)."""
    return (date.fromisoformat(row["return_date"]) - date.fromisoformat(row["depart_date"])).days


def _stops_val(row):
    """행의 stops 정수값 반환. 미상('' / 없음)이면 None."""
    s = row.get("stops", "")
    if s is None or s == "":
        return None
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


def stops_class_rows(rows, max_stops):
    """route의 stops 클래스에 맞는 행만 반환.

    max_stops가 None이면 전체. 아니면 stops를 아는(!='') 행 중 int(stops)<=max_stops.
    """
    if max_stops is None:
        return list(rows)
    out = []
    for r in rows:
        sv = _stops_val(r)
        if sv is not None and sv <= max_stops:
            out.append(r)
    return out


def baseline_rows(rows, max_stops):
    """동일 기준(stops 클래스) 부분집합을 우선. 표본이 부족하면 폴백하되,
    '직항 모니터가 경유편 가격을 기준값/30일 평균으로 끌어오는' 클래스 누수를 막는다.

    - 클래스 부분집합이 MIN_HISTORY_POINTS 이상이면 그대로 사용.
    - 부족하면: 남은 행이 '전부 stops 미상'(legacy)일 때만 전체로 폴백하고,
      stops 를 아는 클래스 밖 행(예: 직항 모니터의 경유편)이 있으면 폴백하지 않는다
      (current_rows 와 동일한 불변식).
    """
    subset = stops_class_rows(rows, max_stops)
    if len(subset) >= MIN_HISTORY_POINTS:
        return subset
    if max_stops is None:
        return list(rows)
    any_known = any(_stops_val(r) is not None for r in rows)
    if not any_known:
        return list(rows)  # 전부 미상(legacy) -> 초기 데이터에서도 특가 보이도록 폴백
    return subset  # 클래스 밖(경유편)은 절대 끌어오지 않음


def current_rows(rows, max_stops):
    """현재값/최신 선택용: stops 클래스 부분집합이 비어있지 않으면 그것만 사용.

    baseline_rows 의 전체-폴백은 avg/baseline 집계에만 쓰고, '현재/최신' 행 선택에는
    쓰지 않는다. 그렇지 않으면 클래스 밖(예: nonstop-only 노선의 경유편) 항공편이
    현재값으로 잡혀 '동일 기준' 불변식을 깨고 가짜 특가를 만들 수 있다.
    부분집합이 '전부 stops 미상'이라 비었을 때만 전체로 폴백한다. 제외된 행 중
    stops 를 아는 행이 하나라도 있으면(즉 클래스 밖 항공편) 빈 집합을 그대로 돌려주어
    '직항 모니터가 경유편을 세지 않는다'는 불변식을 지킨다.
    """
    subset = stops_class_rows(rows, max_stops)
    if subset:
        return subset
    if max_stops is None:
        return list(rows)
    any_known = any(_stops_val(r) is not None for r in rows)
    return list(rows) if not any_known else []


def _row_in_window(row, window, known_window_ids):
    """행이 해당 연휴 윈도우 소속인지.

    - window_id 가 현재 유효한 윈도우를 가리키면 정확 일치로만 판정.
    - window_id 가 없거나(legacy) 더 이상 유효하지 않으면(공휴일표 수정 등)
      날짜 겹침으로 폴백해 행이 고아가 되지 않게 한다.
    """
    wid = row.get("window_id") or ""
    if wid:
        if wid == window["id"]:
            return True
        if wid in known_window_ids:
            return False  # 다른 (유효한) 윈도우 소속
    d1 = date.fromisoformat(row["depart_date"])
    d2 = date.fromisoformat(row["return_date"])
    return d1 <= window["end"] and d2 >= window["start"]


def _latest_min(obs):
    """관측 목록에서 '가장 최근 시각' 클러스터의 최저가 행.

    크롤러는 쿼리 1회에 경유수 클래스별(직항/경유) 행을 같은 collected_at 으로
    여러 개 저장하므로, 단순 obs[-1] 은 같은 시각의 더 싼 클래스 행을 임의로
    제쳐 '현재값'이 과대 표시될 수 있다.
    """
    latest_ts = max(o["collected_at"] for o in obs)
    cluster = [o for o in obs if o["collected_at"] == latest_ts]
    return min(cluster, key=lambda o: o["price"])


def build_matrix_cell(route_prices, offpeak_rows, max_stops, window, route,
                      today, lookback_start, baseline_lookback_start,
                      known_window_ids=frozenset()):
    """모니터 x 연휴윈도우 한 칸: 그 연휴의 '통상 시세' 대비 현재 최저가의 특가율.

    - 통상가(typical): 이 연휴에 이 노선으로 관측된 항공권들의 중앙값
      (미래 출발 + stops 클래스 + 30일 신선도). '이 연휴에 가면 보통 이 정도' 시세.
    - 최저가(min_price): 지금 예약 가능한 가장 싼 항공권(날짜쌍별 최신 최저가 중 최소).
    - 특가율 deal_pct = (통상가 - 최저가) / 통상가.
      통상 시세보다 이만큼 싼 표(프로모션/덜 알려진 날짜)가 남아있다는 신호.
    - 평시(offpeak) 기준가는 참고용 툴팁으로만 남긴다(순위 기준 아님).
    """
    in_window = [r for r in route_prices if _row_in_window(r, window, known_window_ids)]
    by_pair = defaultdict(list)
    for r in in_window:
        by_pair[(r["depart_date"], r["return_date"])].append(r)

    pool = []               # 통상가 계산용: 이 연휴의 (최근) 관측 가격 전부
    current_by_pair = {}    # 날짜쌍별 '지금 예약 가능한' 최신 최저가
    for (depart_date, return_date), all_obs in by_pair.items():
        if date.fromisoformat(depart_date) < today:
            continue
        obs = current_rows(all_obs, max_stops)
        recent = [o for o in obs if datetime.fromisoformat(o["collected_at"]).date() >= lookback_start]
        if not recent:
            continue
        pool.extend(o["price"] for o in recent)
        current_by_pair[(depart_date, return_date)] = _latest_min(recent)

    if not current_by_pair:
        return None

    best = min(current_by_pair.values(), key=lambda r: r["price"])
    window_min = best["price"]
    typical = round(median(pool))
    n_obs = len(pool)
    n_pairs = len(current_by_pair)
    deal_pct = round((typical - window_min) / typical * 100, 1) if typical > 0 else 0.0

    # 신뢰도 티어: 통상가를 믿으려면 관측이 충분해야 한다.
    if n_obs >= 6 and n_pairs >= 2:
        tier = "A"
    elif n_obs >= 3:
        tier = "B"
    else:
        tier = "C"  # 표본 부족 -> 통상가/특가율 신뢰 불가, 최저가만 노출

    # 참고용: 평시(비연휴) 대비 할증률 (툴팁 표시용, 순위엔 미사용)
    base_pool = [
        r for r in stops_class_rows(offpeak_rows, max_stops)
        if datetime.fromisoformat(r["collected_at"]).date() >= baseline_lookback_start
    ]
    nights = _row_nights(best)
    tight = [r for r in base_pool if abs(_row_nights(r) - nights) <= 1]
    base = tight or base_pool
    offpeak_baseline = round(median(r["price"] for r in base)) if base else None
    offpeak_ratio = round(window_min / offpeak_baseline, 3) if offpeak_baseline else None

    d1 = date.fromisoformat(best["depart_date"])
    d2 = date.fromisoformat(best["return_date"])
    return {
        "min_price": window_min,
        "typical": typical,
        "deal_pct": deal_pct,
        "tier": tier,
        "n_obs": n_obs,
        "n_pairs": n_pairs,
        "offpeak_baseline": offpeak_baseline,
        "offpeak_ratio": offpeak_ratio,
        "best": {
            "depart_date": best["depart_date"],
            "return_date": best["return_date"],
            "depart_weekday": WEEKDAY_KO[d1.weekday()],
            "return_weekday": WEEKDAY_KO[d2.weekday()],
            "nights": nights,
            "days": nights + 1,
            "leave_days": count_leave_days(d1, d2),
            "stops": best.get("stops", ""),
            "dep_time": best.get("dep_time", ""),
            "arr_time": best.get("arr_time", ""),
            "booking_url": build_booking_url(
                route["origin"], route["destination"], d1, d2,
                origin_city=route.get("origin_city"),
                dest_city=route.get("destination_city"),
            ),
        },
    }


def main():
    routes = json.loads(ROUTES_FILE.read_text(encoding="utf-8"))
    prices = load_prices()
    collect_status = load_collect_status()
    today = date.today()
    lookback_start = today - timedelta(days=LOOKBACK_DAYS)
    baseline_lookback_start = today - timedelta(days=LOOKBACK_BASELINE_DAYS)

    # 연휴 윈도우 (id/label 포함, 진행 중~조회 기간 내만 반환됨)
    windows = get_holiday_windows()
    known_window_ids = frozenset(w["id"] for w in windows)

    by_route = defaultdict(list)          # 연휴 시세 행 (deals/status/history/matrix 분자)
    offpeak_by_route = defaultdict(list)  # 평시 기준가 행 (matrix 분모 전용)
    last_seen_by_route = {}               # 노선별 마지막 수집시각 (연휴+평시 모두 포함)
    for row in prices:
        row["price"] = int(row["price"])
        row["is_holiday_window"] = bool(int(row["is_holiday_window"]))
        # 구 스키마(7열/10열) 행은 이 키들이 없을 수 있음 -> 빈 문자열로 정규화.
        for k in ("dep_time", "arr_time", "stops", "window_id"):
            row.setdefault(k, "")
            if row.get(k) is None:
                row[k] = ""
        key = (row["origin"], row["destination"])
        ts = row.get("collected_at") or ""
        if ts > last_seen_by_route.get(key, ""):
            last_seen_by_route[key] = ts
        if row["is_holiday_window"]:
            by_route[key].append(row)
        else:
            offpeak_by_route[key].append(row)

    routes_status = []
    deals = []
    history = {}
    matrix_cells = {w["id"]: {} for w in windows}

    for route in routes:
        key = route_key(route)
        mid = monitor_id(route)
        min_nights = route.get("min_nights", 0)
        # 이 모니터의 모집단: 같은 (o,d) 행 중, 이 모니터의 최소 박수 이상인 날짜쌍만.
        # (stops 클래스 필터는 아래 baseline/current 헬퍼가 max_stops로 처리)
        route_prices = [r for r in by_route.get(key, []) if _row_nights(r) >= min_nights]
        max_stops = route.get("max_stops")
        # sample_count 는 이 모니터의 stops 클래스 모집단 크기(경유 모니터와 직항 모니터가
        # 같은 (o,d)를 공유해도 각자의 실제 표본 수를 보고하도록).
        sample_count = len(stops_class_rows(route_prices, max_stops))
        last_collected_at = last_seen_by_route.get(key)

        # 수집일 단위로 묶어서 노선 레벨 스냅샷을 계산.
        # 각 수집일 안에서는 stops 클래스에 맞는 행을 우선(부족하면 전체 폴백)해 최저가 선택.
        by_day = defaultdict(list)
        for r in route_prices:
            by_day[_day(r)].append(r)
        days_sorted = sorted(by_day)

        def day_min_row(day_rows):
            candidates = current_rows(day_rows, max_stops)
            if not candidates:
                return None
            return min(candidates, key=lambda r: r["price"])

        latest_row = None
        status_prev_price = None
        if days_sorted:
            latest_row = day_min_row(by_day[days_sorted[-1]])
            if len(days_sorted) >= 2:
                prev_row = day_min_row(by_day[days_sorted[-2]])
                status_prev_price = prev_row["price"] if prev_row else None

        recent = [r for r in route_prices if datetime.fromisoformat(r["collected_at"]).date() >= lookback_start]
        recent_base = baseline_rows(recent, max_stops)
        min_price_30d = min((r["price"] for r in recent_base), default=None)
        avg_price_30d = round(sum(r["price"] for r in recent_base) / len(recent_base)) if recent_base else None

        # 직항 전용(max_stops==0) 모니터인데 직항 관측치가 한 번도 안 잡힌 경우.
        # 수집은 build 전에 돌므로, 이 조건은 '수집을 시도했으나 직항편이 없다'를 의미한다
        # (신규 등록 직후 낙관적 항목은 프론트에서 no_direct 없이 '대기'로 표시).
        no_direct = max_stops == 0 and sample_count == 0
        cs = collect_status.get(f"{route['origin']}-{route['destination']}") or {}
        routes_status.append({
            **route,
            "sample_count": sample_count,
            "no_direct": no_direct,
            "last_collected_at": last_collected_at,
            "latest_price": latest_row["price"] if latest_row else None,
            "latest_depart_date": latest_row["depart_date"] if latest_row else None,
            "latest_return_date": latest_row["return_date"] if latest_row else None,
            "dep_time": latest_row["dep_time"] if latest_row else "",
            "arr_time": latest_row["arr_time"] if latest_row else "",
            "stops": latest_row["stops"] if latest_row else "",
            "prev_price": status_prev_price,
            "min_price_30d": min_price_30d,
            "avg_price_30d": avg_price_30d,
            # 수집 파이프라인 상태 (빈 칸의 '이유'를 대시보드가 보여주기 위함)
            "collect_status": cs.get("status"),
            "collect_status_detail": cs.get("detail", ""),
            "last_attempt_at": cs.get("last_attempt_at"),
        })

        # history.json: 수집일별 min/avg/n (t 오름차순)
        if days_sorted:
            # 차트의 min/avg 도 status/deal 과 같은 '동일 기준'(stops 클래스) 모집단을 따르도록,
            # 각 수집일에서 stops 클래스 행만 집계(그 날 클래스 행이 없으면 전체 폴백).
            history[mid] = []
            for day in days_sorted:
                day_rows = current_rows(by_day[day], max_stops)
                if not day_rows:
                    continue
                history[mid].append({
                    "t": day,
                    "min": min(r["price"] for r in day_rows),
                    "avg": round(sum(r["price"] for r in day_rows) / len(day_rows)),
                    "n": len(day_rows),
                })

        # 연휴 가성비 매트릭스: 이 모니터의 윈도우별 칸 계산 (연휴 행이 있어야 의미 있음)
        offpeak = offpeak_by_route.get(key, [])
        for w in windows:
            cell = build_matrix_cell(
                route_prices, offpeak, max_stops, w, route,
                today, lookback_start, baseline_lookback_start,
                known_window_ids=known_window_ids,
            )
            if cell:
                matrix_cells[w["id"]][mid] = cell

        if len(recent) < MIN_HISTORY_POINTS:
            continue

        # 동일 기준: 같은 박수(nights) + stops 클래스끼리 평균가를 낸다.
        # 박수가 다르면 가격대가 크게 달라 하나의 평균으로 비교하면 짧은 일정이
        # 항상 싸 보이는 착시가 생기므로, 박수별로 기준값을 분리한다.
        recent_by_nights = defaultdict(list)
        for r in recent:
            recent_by_nights[_row_nights(r)].append(r)

        def avg_for_nights(n):
            grp = recent_by_nights.get(n, [])
            if len(grp) < MIN_HISTORY_POINTS:
                return None  # 같은 박수 표본이 부족하면 특가 판정 보류(가짜 특가 방지)
            base = baseline_rows(grp, max_stops)  # 같은 박수 안에서 stops 클래스 우선(부족 시 폴백)
            return sum(x["price"] for x in base) / len(base)

        # 날짜쌍별 관측치: 최신 관측치와 그 직전(더 이른 collected_at) 관측치.
        # 현재값/직전값 선택도 stops 클래스로 제한(부족하면 전체 폴백).
        by_date = defaultdict(list)
        for r in route_prices:
            by_date[(r["depart_date"], r["return_date"])].append(r)

        for (depart_date, return_date), all_obs in by_date.items():
            # 이미 지나간 출발일은 특가로 노출하지 않음 (CSV는 append-only라 과거 행이 계속 남음)
            if date.fromisoformat(depart_date) < today:
                continue
            # 현재값은 반드시 노선의 stops 클래스 안에서 고른다(전체 폴백 금지).
            # 클래스에 해당 날짜쌍 관측치가 하나도 없으면 이 날짜쌍은 건너뛴다.
            obs = current_rows(all_obs, max_stops)
            if not obs:
                continue
            r = _latest_min(obs)
            # 최신 관측치가 30일 기준 구간보다 오래됐으면 '현재값'으로 쓸 수 없음
            if datetime.fromisoformat(r["collected_at"]).date() < lookback_start:
                continue
            d1 = date.fromisoformat(depart_date)
            d2 = date.fromisoformat(return_date)
            nights = (d2 - d1).days
            # 같은 박수 기준값이 없으면(표본 부족) 이 날짜쌍은 특가 판정 보류
            avg_price = avg_for_nights(nights)
            if avg_price is None:
                continue
            earlier = [o for o in obs if o["collected_at"] < r["collected_at"]]
            prev_price = _latest_min(earlier)["price"] if earlier else None
            discount = (avg_price - r["price"]) / avg_price
            if discount >= DEAL_THRESHOLD:
                deals.append({
                    "route": route,
                    "depart_date": depart_date,
                    "return_date": return_date,
                    "depart_weekday": WEEKDAY_KO[d1.weekday()],
                    "return_weekday": WEEKDAY_KO[d2.weekday()],
                    "nights": nights,
                    "days": nights + 1,
                    "leave_days": count_leave_days(d1, d2),
                    "current_price": r["price"],
                    "prev_price": prev_price,
                    "avg_price": round(avg_price),
                    "dep_time": r.get("dep_time", ""),
                    "arr_time": r.get("arr_time", ""),
                    "stops": r.get("stops", ""),
                    "discount_pct": round(discount * 100, 1),
                    "is_holiday_window": r["is_holiday_window"],
                    "booking_url": build_booking_url(
                        route["origin"], route["destination"], d1, d2,
                        origin_city=route.get("origin_city"),
                        dest_city=route.get("destination_city"),
                    ),
                })

    # 노선별로 묶어서 보여줄 수 있도록 노선 -> 할인율 내림차순으로 정렬
    deals.sort(key=lambda d: (d["route"]["origin"], d["route"]["destination"], -d["discount_pct"]))

    windows_out = [
        {
            "id": w["id"],
            "label": w["label"],
            "start": w["start"].isoformat(),
            "end": w["end"].isoformat(),
            "holiday_dates": [d.isoformat() for d in w["holiday_dates"]],
        }
        for w in windows
    ]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "deals.json").write_text(json.dumps(deals, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "routes.json").write_text(json.dumps(routes, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "routes_status.json").write_text(json.dumps(routes_status, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "history.json").write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "matrix.json").write_text(
        json.dumps({"windows": windows_out, "cells": matrix_cells}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (OUT_DIR / "meta.json").write_text(
        json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # holidays.json: 구(배열) 대신 {holidays, windows} 객체. 프론트는 두 형식 모두 수용.
    all_holidays = sorted(d.isoformat() for d in HOLIDAY_DATES)
    (OUT_DIR / "holidays.json").write_text(
        json.dumps({"holidays": all_holidays, "windows": windows_out}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    n_cells = sum(len(v) for v in matrix_cells.values())
    print(f"deals: {len(deals)}, routes: {len(routes_status)}, matrix cells: {n_cells}")


if __name__ == "__main__":
    main()
