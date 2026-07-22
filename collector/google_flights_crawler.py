"""
구글 플라이트(google.com/travel/flights) 검색 결과에서 최저가를 크롤링.

네이버 항공권은 헤드리스 브라우저 접근을 막는 것으로 확인되어(로그인 요구 문구 반환),
구글 플라이트로 데이터 소스를 교체함.

주의:
- 공식 API가 아닌 화면 크롤링이므로 사이트 구조가 바뀌면 셀렉터를 갱신해야 함.
- 과도한 요청은 차단으로 이어질 수 있음 -> 개인용으로 3~6시간 간격 정도만 권장.
"""
import re
from datetime import date

from playwright.sync_api import sync_playwright

PRICE_PATTERN = re.compile(r"₩([0-9][0-9,]{2,})")

# 구글 플라이트는 도시명을 인식하므로 IATA 공항 코드를 도시명으로 매핑.
# routes.json 에 새 노선을 추가하면 이 표에도 도시명을 추가해야 함.
AIRPORT_CITY = {
    "ICN": "Seoul",
    "NRT": "Tokyo",
    "KIX": "Osaka",
    "DAD": "Da Nang",
    "DPS": "Bali",
}


def build_booking_url(origin: str, destination: str, depart: date, return_: date) -> str:
    """해당 노선/날짜로 사용자가 직접 예약을 확인할 수 있는 구글 플라이트 링크."""
    origin_city = AIRPORT_CITY.get(origin, origin)
    dest_city = AIRPORT_CITY.get(destination, destination)
    query = (
        f"Flights from {origin_city} to {dest_city} "
        f"on {depart.isoformat()} through {return_.isoformat()}"
    )
    return "https://www.google.com/travel/flights/search?q=" + query.replace(" ", "%20")


def fetch_lowest_price(origin: str, destination: str, depart: date, return_: date, timeout_ms=25000):
    """지정한 노선/날짜의 최저가(원)를 반환. 실패 시 None."""
    url = build_booking_url(origin, destination, depart, return_)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            locale="ko-KR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        )
        try:
            page.goto(url, timeout=timeout_ms)
            page.wait_for_timeout(6000)
            text = page.inner_text("body")
            prices = [int(m.replace(",", "")) for m in PRICE_PATTERN.findall(text)]
            if not prices:
                return None
            return min(prices)
        except Exception as e:
            print(f"[google_flights_crawler] failed for {origin}->{destination} {depart}~{return_}: {e}")
            return None
        finally:
            browser.close()


if __name__ == "__main__":
    from datetime import timedelta

    d1 = date.today() + timedelta(days=30)
    d2 = d1 + timedelta(days=3)
    price = fetch_lowest_price("ICN", "NRT", d1, d2)
    print("lowest price:", price)
