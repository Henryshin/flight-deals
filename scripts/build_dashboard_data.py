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
    """동일 기준(stops 클래스) 부분집합을 우선하되, MIN_HISTORY_POINTS 미만이면 전체로 폴백."""
    subset = stops_class_rows(rows, max_stops)
    if len(subset) >= MIN_HISTORY_POINTS:
        return subset
    return list(rows)


def current_rows(rows, max_stops):
    """현재값/최신 선택용: stops 클래스 부분집합이 비어있지 않으면 그것만 사용.

    baseline_rows 의 전체-폴백은 avg/baseline 집계에만 쓰고, '현재/최신' 행 선택에는
    쓰지 않는다. 그렇지 않으면 클래스 밖(예: nonstop-only 노선의 경유편) 항공편이
    현재값으로 잡혀 '동일 기준' 불변식을 깨고 가짜 특가를 만들 수 있다.
    부분집합이 비면(전부 stops 미상) 전체로 폴백한다.
    """
    subset = stops_class_rows(rows, max_stops)
    return subset if subset else list(rows)


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
        route_prices = by_route.get(key, [])
        sample_count = len(route_prices)
        last_collected_at = max((r["collected_at"] for r in route_prices), default=None)
        max_stops = route.get("max_stops")

        # 수집일(collected_at) 단위로 묶어서 노선 레벨 스냅샷을 계산.
        # 각 수집일 안에서는 stops 클래스에 맞는 행을 우선(부족하면 전체 폴백)해 최저가 선택.
        by_day = defaultdict(list)
        for r in route_prices:
            by_day[r["collected_at"]].append(r)
        days_sorted = sorted(by_day)

        def day_min_row(day_rows):
            candidates = current_rows(day_rows, max_stops)
            return min(candidates, key=lambda r: r["price"])

        latest_row = None
        status_prev_price = None
        if days_sorted:
            latest_row = day_min_row(by_day[days_sorted[-1]])
            if len(days_sorted) >= 2:
                status_prev_price = day_min_row(by_day[days_sorted[-2]])["price"]

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
            history[f"{route['origin']}-{route['destination']}"] = []
            for day in days_sorted:
                day_rows = current_rows(by_day[day], max_stops)
                history[f"{route['origin']}-{route['destination']}"].append({
                    "t": day,
                    "min": min(r["price"] for r in day_rows),
                    "avg": round(sum(r["price"] for r in day_rows) / len(day_rows)),
                    "n": len(day_rows),
                })

        if len(recent) < MIN_HISTORY_POINTS:
            continue
        # 동일 기준(stops 클래스) 평균가. 부족하면 전체로 폴백.
        avg_price = sum(r["price"] for r in recent_base) / len(recent_base)

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
            earlier = [o for o in obs if o["collected_at"] < r["collected_at"]]
            prev_price = earlier[-1]["price"] if earlier else None
            discount = (avg_price - r["price"]) / avg_price
            if discount >= DEAL_THRESHOLD:
                d1 = date.fromisoformat(depart_date)
                d2 = date.fromisoformat(return_date)
                nights = (d2 - d1).days
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
