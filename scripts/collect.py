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

from collector.google_flights_crawler import CrawlerSessionError, PriceCrawlerSession
from holidays import date_range_candidates, get_holiday_windows

ROOT = Path(__file__).parent.parent
ROUTES_FILE = ROOT / "data" / "routes.json"
PRICES_FILE = ROOT / "data" / "prices.csv"
DEFAULT_TRIP_LENGTH_DAYS = 3
NEW_HEADER = [
    "origin", "destination", "depart_date", "return_date", "price",
    "is_holiday_window", "collected_at", "dep_time", "arr_time", "stops",
]
OLD_HEADER = NEW_HEADER[:7]


def build_date_candidates(trip_length_days=DEFAULT_TRIP_LENGTH_DAYS):
    """해당 노선의 여행 길이(min_nights)로 (출발, 귀국, 연휴여부) 후보 생성."""
    windows = get_holiday_windows()
    candidates = []
    for w in windows:
        for depart, return_ in date_range_candidates(w, trip_length_days):
            candidates.append((depart, return_, True))

    if not candidates:
        today = date.today()
        for offset in (14, 30, 45):
            d = today + timedelta(days=offset)
            candidates.append((d, d + timedelta(days=trip_length_days), False))
    return candidates


def migrate_prices_file():
    """구(7열) prices.csv를 신(10열) 헤더로 1회 재작성하고 기존 행을 빈 3열로 패딩.

    이미 신 헤더면 아무 것도 하지 않음. 파일이 없으면 아무 것도 하지 않음.
    """
    if not PRICES_FILE.exists() or PRICES_FILE.stat().st_size == 0:
        return
    with open(PRICES_FILE, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if not rows:
        return
    header = rows[0]
    if header == NEW_HEADER:
        return
    if header != OLD_HEADER:
        return  # 알 수 없는 헤더는 건드리지 않음
    with open(PRICES_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(NEW_HEADER)
        for row in rows[1:]:
            writer.writerow(row + ["", "", ""])


def fetch_price_with_retry(session, *args, **kwargs):
    """개별 쿼리 수행. 브라우저 세션이 죽었으면 한 번 재시작해 재시도하고,
    그래도 죽으면 해당 쿼리는 건너뜀(None)."""
    try:
        return session.fetch_lowest_price(*args, **kwargs)
    except CrawlerSessionError as e:
        print(f"  브라우저 세션 오류({e}) -> 세션 재시작 후 재시도")
        session.restart()
        try:
            return session.fetch_lowest_price(*args, **kwargs)
        except CrawlerSessionError as e2:
            print(f"  세션 재시작 후에도 실패({e2}) -> 이번 쿼리 건너뜀")
            return None


def main():
    routes = json.loads(ROUTES_FILE.read_text(encoding="utf-8"))
    collected_at = date.today().isoformat()

    rows = []
    # 실행 전체가 브라우저 하나를 재사용 (쿼리마다 크로미움 기동 비용을 내지 않음).
    with PriceCrawlerSession() as session:
        for route in routes:
            trip_length = route.get("min_nights", DEFAULT_TRIP_LENGTH_DAYS)
            candidates = build_date_candidates(trip_length)
            max_stops = route.get("max_stops")
            for depart, return_, is_holiday in candidates:
                result = fetch_price_with_retry(
                    session,
                    route["origin"], route["destination"], depart, return_,
                    origin_city=route.get("origin_city"), dest_city=route.get("destination_city"),
                    max_stops=max_stops,
                )
                if result is None:
                    print(f"  {route['origin']}->{route['destination']} {depart}~{return_}: 실패")
                    continue
                print(f"  {route['origin']}->{route['destination']} {depart}~{return_}: {result['price']}원")
                rows.append([
                    route["origin"], route["destination"],
                    depart.isoformat(), return_.isoformat(),
                    result["price"], int(is_holiday), collected_at,
                    result.get("dep_time", ""), result.get("arr_time", ""),
                    result.get("stops", ""),
                ])

    if not rows:
        print("수집된 가격 없음")
        return

    # 구 헤더 파일이면 먼저 신 헤더로 마이그레이션해 csv.DictReader 일관성 유지.
    migrate_prices_file()
    write_header = not PRICES_FILE.exists() or PRICES_FILE.stat().st_size == 0
    with open(PRICES_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(NEW_HEADER)
        writer.writerows(rows)

    print(f"{len(rows)}건 저장 완료")


if __name__ == "__main__":
    main()
