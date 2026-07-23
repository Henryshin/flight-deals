"""
구글 플라이트(google.com/travel/flights) 검색 결과에서 최저가를 크롤링.

네이버 항공권은 헤드리스 브라우저 접근을 막는 것으로 확인되어(로그인 요구 문구 반환),
구글 플라이트로 데이터 소스를 교체함.

주의:
- 공식 API가 아닌 화면 크롤링이므로 사이트 구조가 바뀌면 셀렉터를 갱신해야 함.
- 과도한 요청은 차단으로 이어질 수 있음 -> 개인용으로 3~6시간 간격 정도만 권장.

성능:
- 쿼리마다 크로미움을 새로 띄우면 실행당 ~88회의 브라우저 기동 비용을 내게 되므로,
  수집 실행 전체가 PriceCrawlerSession 하나(브라우저 1개)를 재사용하도록 함.
  쿼리별로는 공유 컨텍스트에서 새 페이지만 열고 닫음.
"""
import re
from datetime import date

from playwright.sync_api import sync_playwright

PRICE_PATTERN = re.compile(r"₩([0-9][0-9,]{2,})")

# li 안의 "오전/오후 H:MM" 시각. 뒤에 "+1"(익일 도착) 마커가 붙을 수 있음.
TIME_PATTERN = re.compile(r"(오전|오후)\s*([0-9]{1,2}):([0-9]{2})(\+1)?")
STOPS_NONSTOP = "직항"
STOPS_PATTERN = re.compile(r"경유\s*([0-9]+)\s*회")


def _to_24h(ampm: str, hour: int, minute: int) -> str:
    """'오전 12:xx' -> 00:xx, '오후 12:xx' -> 12:xx, '오전 H' -> 0H, '오후 H' -> H+12."""
    if ampm == "오전":
        hour = 0 if hour == 12 else hour
    else:  # 오후
        hour = 12 if hour == 12 else hour + 12
    return f"{hour:02d}:{minute:02d}"


def parse_itinerary(li_text: str):
    """왕복 li 텍스트 하나를 파싱해 {"price","stops","dep_time","arr_time"} 반환.

    ₩ 가격이 없거나 왕복이 아니면 None. 브라우저 없이 단위테스트 가능하도록 순수 함수.
    """
    if ROUND_TRIP_MARKER not in li_text:
        return None
    price_matches = PRICE_PATTERN.findall(li_text)
    if not price_matches:
        return None
    price = int(price_matches[-1].replace(",", ""))

    times = TIME_PATTERN.findall(li_text)  # [(ampm, H, MM, plus1), ...]
    dep_time = ""
    arr_time = ""
    if len(times) >= 1:
        ampm, h, m, _plus = times[0]
        dep_time = _to_24h(ampm, int(h), int(m))
    if len(times) >= 2:
        ampm, h, m, plus = times[1]
        arr_time = _to_24h(ampm, int(h), int(m))
        if plus:
            arr_time += "+1"

    if STOPS_NONSTOP in li_text:
        stops = 0
    else:
        m = STOPS_PATTERN.search(li_text)
        # 직항도 '경유 N회'도 매칭되지 않으면 경유수 미상(None). 0으로 단정하면
        # 파싱 못한 항목이 nonstop 클래스로 오염되므로 '알 수 없음'으로 남긴다.
        stops = int(m.group(1)) if m else None

    return {"price": price, "stops": stops, "dep_time": dep_time, "arr_time": arr_time}

# 결과 목록의 각 항공편은 <li> 안에 "...₩487,681 | 왕복" 형태로 총액이 들어있음.
# body 전체 텍스트를 긁으면 날짜별 가격 캘린더 위젯 등 다른 요소의 가격까지 섞여
# 실제보다 훨씬 낮은 값을 최저가로 잘못 고르는 문제가 있어, 결과 리스트 항목만 대상으로 함.
ROUND_TRIP_MARKER = "왕복"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# 구글 플라이트는 도시명을 인식하므로 IATA 공항 코드를 도시명으로 매핑.
# 새 노선은 routes.json 에 origin_city/destination_city 를 함께 넣으므로,
# 이 표는 도시명이 없는 과거(legacy) 항목을 위한 폴백으로만 쓰임.
AIRPORT_CITY = {
    "ICN": "Seoul",
    "NRT": "Tokyo",
    "KIX": "Osaka",
    "DAD": "Da Nang International Airport",  # "Da Nang"만 쓰면 구글이 '살펴보기' 랜딩으로 빠져 결과 0건
    "DPS": "Bali",
    "ULN": "Ulaanbaatar",
    "PVG": "Shanghai",
    "TPE": "Taipei",
    "KHH": "Kaohsiung",
    "SHI": "Shimojishima",
}


class CrawlerSessionError(RuntimeError):
    """브라우저 세션 자체가 죽어 새 페이지를 열 수 없는 상태.

    호출 측(collect.py)은 이 예외를 잡아 세션을 재시작할 수 있음.
    """


def build_booking_url(
    origin: str,
    destination: str,
    depart: date,
    return_: date,
    origin_city: str | None = None,
    dest_city: str | None = None,
) -> str:
    """해당 노선/날짜로 사용자가 직접 예약을 확인할 수 있는 구글 플라이트 링크.

    도시명 결정 순서: 명시적 인자 -> AIRPORT_CITY 표 -> 공항 코드 그대로.
    """
    origin_city = origin_city or AIRPORT_CITY.get(origin, origin)
    dest_city = dest_city or AIRPORT_CITY.get(destination, destination)
    # 연결어는 반드시 "through"여야 함. Actions 러너에서 실측한 결과(debug-crawl):
    #   - "returning"은 구글 NL 파서가 인식하지 못해 항공검색 홈으로 떨어짐 -> 결과 0건
    #   - "through"는 왕복 검색으로 정상 파싱됨
    # hl=ko&curr=KRW가 없으면 러너 IP 지역에 따라 가격이 USD로 표시되어
    # PRICE_PATTERN(₩)이 아무것도 매칭하지 못하므로 반드시 붙인다.
    query = (
        f"Flights from {origin_city} to {dest_city} "
        f"on {depart.isoformat()} through {return_.isoformat()}"
    )
    return (
        "https://www.google.com/travel/flights/search?q="
        + query.replace(" ", "%20")
        + "&hl=ko&curr=KRW"
    )


class PriceCrawlerSession:
    """수집 실행 전체가 크로미움 브라우저 하나를 재사용하는 세션.

    with PriceCrawlerSession() as session:
        session.fetch_lowest_price(...)  # 쿼리마다 새 페이지만 열고 닫음
    """

    def __init__(self, _playwright_factory=None):
        # _playwright_factory: 테스트에서 sync_playwright 를 가짜로 주입하기 위한 지점.
        self._playwright_factory = _playwright_factory or sync_playwright
        self._playwright = None
        self._browser = None
        self._browser_context = None

    def __enter__(self):
        self._start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._close()
        return False

    def restart(self):
        """죽은 브라우저를 버리고 새로 시작 (CrawlerSessionError 이후 복구용)."""
        self._close()
        self._start()

    def _start(self):
        self._playwright = self._playwright_factory().start()
        try:
            self._browser = self._playwright.chromium.launch(headless=True)
            self._browser_context = self._browser.new_context(
                locale="ko-KR",
                user_agent=USER_AGENT,
            )
        except Exception:
            self._close()
            raise

    def _close(self):
        """에러 이후에도 남은 리소스를 최대한 정리 (부분 실패 무시)."""
        for resource, closer_name in (
            (self._browser_context, "close"),
            (self._browser, "close"),
            (self._playwright, "stop"),
        ):
            if resource is None:
                continue
            try:
                getattr(resource, closer_name)()
            except Exception:
                pass
        self._browser_context = None
        self._browser = None
        self._playwright = None

    def fetch_lowest_price(
        self,
        origin: str,
        destination: str,
        depart: date,
        return_: date,
        timeout_ms=25000,
        origin_city: str | None = None,
        dest_city: str | None = None,
        max_stops=None,
    ):
        """지정한 노선/날짜의 최저가 항공편 정보를 반환. 개별 쿼리 실패 시 None.

        max_stops 이하의 경유수를 가진 왕복편만 후보로 삼고(None이면 전부),
        그중 최저가 항공편의 전체 dict({"price","stops","dep_time","arr_time"})를 반환.

        브라우저가 죽어 새 페이지조차 못 여는 경우엔 CrawlerSessionError 를 던져
        호출 측이 세션을 재시작할 수 있게 함.
        """
        url = build_booking_url(origin, destination, depart, return_, origin_city=origin_city, dest_city=dest_city)

        try:
            page = self._browser_context.new_page()
        except Exception as e:
            raise CrawlerSessionError(f"cannot open new page (browser dead?): {e}") from e

        try:
            page.goto(url, timeout=timeout_ms)
            page.wait_for_timeout(6000)

            itineraries = []
            for li in page.query_selector_all("li"):
                item_text = li.inner_text()
                parsed = parse_itinerary(item_text)
                if parsed is None:
                    continue
                if max_stops is not None and (
                    parsed["stops"] is None or parsed["stops"] > max_stops
                ):
                    # 경유수 미상 항목은 유계 클래스(max_stops!=None)에 넣지 않는다.
                    continue
                itineraries.append(parsed)

            if not itineraries:
                return None
            return min(itineraries, key=lambda it: it["price"])
        except Exception as e:
            print(f"[google_flights_crawler] failed for {origin}->{destination} {depart}~{return_}: {e}")
            return None
        finally:
            try:
                page.close()
            except Exception:
                pass

    def fetch_min_by_stops(
        self,
        origin: str,
        destination: str,
        depart: date,
        return_: date,
        timeout_ms=25000,
        origin_city: str | None = None,
        dest_city: str | None = None,
        max_stops=None,
    ):
        """(노선/날짜)의 '경유수(stops)별 최저가'를 각각 반환.

        한 번의 페이지 로드에서 모든 왕복편을 파싱해 경유수별로 최저가를 고른다.
        반환: {0: {..dict..}, 1: {..dict..}, ...} — 경유수 미상 항목은 제외.
        max_stops 가 None이 아니면 그 이하 경유수만 포함. 결과 없음/실패 시 {}.

        같은 (o,d,날짜)라도 직항 모니터는 stops=0 최저가를, 경유 모니터는 stops<=1
        최저가를 각각 필요로 하므로, 총액 최저가 1건만 저장하면 직항 모니터가 영원히
        빈 채로 남는다. 이를 막기 위해 경유수 클래스별 최저가를 모두 저장한다.
        """
        url = build_booking_url(origin, destination, depart, return_, origin_city=origin_city, dest_city=dest_city)

        try:
            page = self._browser_context.new_page()
        except Exception as e:
            raise CrawlerSessionError(f"cannot open new page (browser dead?): {e}") from e

        try:
            page.goto(url, timeout=timeout_ms)
            page.wait_for_timeout(6000)

            best = {}
            for li in page.query_selector_all("li"):
                parsed = parse_itinerary(li.inner_text())
                if parsed is None or parsed["stops"] is None:
                    continue
                s = parsed["stops"]
                if max_stops is not None and s > max_stops:
                    continue
                if s not in best or parsed["price"] < best[s]["price"]:
                    best[s] = parsed
            return best
        except Exception as e:
            print(f"[google_flights_crawler] failed for {origin}->{destination} {depart}~{return_}: {e}")
            return {}
        finally:
            try:
                page.close()
            except Exception:
                pass


def fetch_lowest_price(
    origin: str,
    destination: str,
    depart: date,
    return_: date,
    timeout_ms=25000,
    origin_city: str | None = None,
    dest_city: str | None = None,
    max_stops=None,
):
    """지정한 노선/날짜의 최저가(원, int)를 반환. 실패 시 None.

    단발 호출용 하위호환 래퍼. 세션 결과 dict에서 ['price']만 추출해 int로 돌려준다.
    여러 쿼리를 돌릴 땐 PriceCrawlerSession 을 직접 써서
    브라우저 기동 비용을 한 번만 내는 것을 권장.
    """
    with PriceCrawlerSession() as session:
        result = session.fetch_lowest_price(
            origin, destination, depart, return_,
            timeout_ms=timeout_ms, origin_city=origin_city, dest_city=dest_city,
            max_stops=max_stops,
        )
        return result["price"] if result else None


if __name__ == "__main__":
    from datetime import timedelta

    d1 = date.today() + timedelta(days=30)
    d2 = d1 + timedelta(days=3)
    price = fetch_lowest_price("ICN", "NRT", d1, d2)
    print("lowest price:", price)
