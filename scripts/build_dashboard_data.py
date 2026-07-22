"""
data/prices.csv + data/routes.json 을 읽어
docs/data/deals.json, docs/data/routes_status.json 을 생성 (GitHub Pages가 읽는 정적 파일).
"""
import csv
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
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


def main():
    routes = json.loads(ROUTES_FILE.read_text(encoding="utf-8"))
    prices = load_prices()
    today = date.today()
    lookback_start = today - timedelta(days=LOOKBACK_DAYS)

    by_route = defaultdict(list)
    for row in prices:
        row["price"] = int(row["price"])
        row["is_holiday_window"] = bool(int(row["is_holiday_window"]))
        by_route[(row["origin"], row["destination"])].append(row)

    routes_status = []
    deals = []

    for route in routes:
        key = route_key(route)
        route_prices = by_route.get(key, [])
        sample_count = len(route_prices)
        last_collected_at = max((r["collected_at"] for r in route_prices), default=None)
        latest = max(route_prices, key=lambda r: r["collected_at"], default=None)

        routes_status.append({
            **route,
            "sample_count": sample_count,
            "last_collected_at": last_collected_at,
            "latest_price": latest["price"] if latest else None,
            "latest_depart_date": latest["depart_date"] if latest else None,
            "latest_return_date": latest["return_date"] if latest else None,
        })

        recent = [r for r in route_prices if datetime.fromisoformat(r["collected_at"]).date() >= lookback_start]
        if len(recent) < MIN_HISTORY_POINTS:
            continue
        avg_price = sum(r["price"] for r in recent) / len(recent)

        by_date = {}
        for r in route_prices:
            dkey = (r["depart_date"], r["return_date"])
            if dkey not in by_date or r["collected_at"] > by_date[dkey]["collected_at"]:
                by_date[dkey] = r

        for (depart_date, return_date), r in by_date.items():
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
                    "avg_price": round(avg_price),
                    "discount_pct": round(discount * 100, 1),
                    "is_holiday_window": r["is_holiday_window"],
                    "booking_url": build_booking_url(route["origin"], route["destination"], d1, d2),
                })

    deals.sort(key=lambda d: d["discount_pct"], reverse=True)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "deals.json").write_text(json.dumps(deals, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "routes_status.json").write_text(json.dumps(routes_status, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "meta.json").write_text(
        json.dumps({"generated_at": datetime.now().isoformat()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    all_holidays = sorted(d.isoformat() for d in HOLIDAY_DATES)
    (OUT_DIR / "holidays.json").write_text(
        json.dumps(all_holidays, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"deals: {len(deals)}, routes: {len(routes_status)}")


if __name__ == "__main__":
    main()
