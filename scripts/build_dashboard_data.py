"""
data/prices.csv + data/routes.json 을 읽어
docs/data/deals.json, docs/data/routes_status.json, docs/data/history.json 을 생성
(GitHub Pages가 읽는 정적 파일).
"""
import csv
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from collector.google_flights_crawler import build_booking_url
from holidays import KOREAN_HOLIDAYS

ROOT = Path(__file__).parent.parent
ROUTES_FILE = ROOT / "data" / "routes.json"
PRICES_FILE = ROOT / "data" / "prices.csv"
OUT_DIR = ROOT / "docs" / "data"

DEAL_THRESHOLD = 0.15
MIN_HISTORY_POINTS = 3
LOOKBACK_DAYS = 30

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


def load_prices():
    if not PRICES_FILE.exists():
        return []
    with open(PRICES_FILE, encoding="utf-8") as f:
        return list(csv.DictReader(f))


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


def main():
    routes = json.loads(ROUTES_FILE.read_text(encoding="utf-8"))
    prices = load_prices()
    today = date.today()
    lookback_start = today - timedelta(days=LOOKBACK_DAYS)

    by_route = defaultdict(list)
    for row in prices:
        row["price"] = int(row["price"])
        row["is_holiday_window"] = bool(int(row["is_holiday_window"]))
        # 구 스키마(7열) 행은 이 키들이 없을 수 있음 -> 빈 문자열로 정규화.
        row.setdefault("dep_time", "")
        row.setdefault("arr_time", "")
        row.setdefault("stops", "")
        if row.get("dep_time") is None:
            row["dep_time"] = ""
        if row.get("arr_time") is None:
            row["arr_time"] = ""
        if row.get("stops") is None:
            row["stops"] = ""
        by_route[(row["origin"], row["destination"])].append(row)

    routes_status = []
    deals = []
    history = {}

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
        last_collected_at = max((r["collected_at"] for r in route_prices), default=None)

        # 수집일(collected_at) 단위로 묶어서 노선 레벨 스냅샷을 계산.
        # 각 수집일 안에서는 stops 클래스에 맞는 행을 우선(부족하면 전체 폴백)해 최저가 선택.
        by_day = defaultdict(list)
        for r in route_prices:
            by_day[r["collected_at"]].append(r)
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

        routes_status.append({
            **route,
            "sample_count": sample_count,
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
            obs.sort(key=lambda r: r["collected_at"])
            r = obs[-1]
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
            prev_price = earlier[-1]["price"] if earlier else None
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

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "deals.json").write_text(json.dumps(deals, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "routes.json").write_text(json.dumps(routes, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "routes_status.json").write_text(json.dumps(routes_status, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "history.json").write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "meta.json").write_text(
        json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    all_holidays = sorted(d.isoformat() for d in HOLIDAY_DATES)
    (OUT_DIR / "holidays.json").write_text(
        json.dumps(all_holidays, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"deals: {len(deals)}, routes: {len(routes_status)}")


if __name__ == "__main__":
    main()
