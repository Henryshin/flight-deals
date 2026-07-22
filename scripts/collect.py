"""
GitHub Actions에서 실행: 등록된 노선 x 연휴 날짜 후보를 크롤링해 data/prices.csv에 append.
로컬 SQLite 대신 git에 커밋되는 CSV가 곧 가격 이력 DB 역할을 함.
"""
import csv
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from collector.google_flights_crawler import fetch_lowest_price
from holidays import date_range_candidates, get_holiday_windows

ROOT = Path(__file__).parent.parent
ROUTES_FILE = ROOT / "data" / "routes.json"
PRICES_FILE = ROOT / "data" / "prices.csv"
TRIP_LENGTH_DAYS = 3


def build_date_candidates():
    windows = get_holiday_windows()
    candidates = []
    for w in windows:
        for depart, return_ in date_range_candidates(w, TRIP_LENGTH_DAYS):
            candidates.append((depart, return_, True))

    if not candidates:
        today = date.today()
        for offset in (14, 30, 45):
            d = today + timedelta(days=offset)
            candidates.append((d, d + timedelta(days=TRIP_LENGTH_DAYS), False))
    return candidates


def main():
    routes = json.loads(ROUTES_FILE.read_text(encoding="utf-8"))
    candidates = build_date_candidates()
    collected_at = date.today().isoformat()

    rows = []
    for route in routes:
        for depart, return_, is_holiday in candidates:
            price = fetch_lowest_price(route["origin"], route["destination"], depart, return_)
            if price is None:
                print(f"  {route['origin']}->{route['destination']} {depart}~{return_}: 실패")
                continue
            print(f"  {route['origin']}->{route['destination']} {depart}~{return_}: {price}원")
            rows.append([
                route["origin"], route["destination"],
                depart.isoformat(), return_.isoformat(),
                price, int(is_holiday), collected_at,
            ])

    if not rows:
        print("수집된 가격 없음")
        return

    write_header = not PRICES_FILE.exists() or PRICES_FILE.stat().st_size == 0
    with open(PRICES_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["origin", "destination", "depart_date", "return_date", "price", "is_holiday_window", "collected_at"])
        writer.writerows(rows)

    print(f"{len(rows)}건 저장 완료")


if __name__ == "__main__":
    main()
