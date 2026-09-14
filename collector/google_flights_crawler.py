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

실패 분류:
- 예전엔 모든 실패가 조용히 None/{} 로 삼켜져 '차단', '결과 없음', '파싱 실패'가
  구분되지 않았다. fetch_result() 는 typed 결과(status/detail)를 돌려주어
  수집 스크립트가 노선별 실패 사유를 기록/노출할 수 있게 한다.
"""
import re
from datetime import date

PRICE_PATTERN = re.compile(r"₩([0-9][0-9,]{2,})")

# li 안의 "오전/오후 H:MM" 시각. 뒤에 "+1"(익일 도착) 마커가 붙을 수 있음.
TIME_PATTERN = re.compile(r"(오전|오후)\s*([0-9]{1,2}):([0-9]{2})(\+1)?")
STOPS_NONSTOP = "직항"
STOPS_PATTERN = re.compile(r"경유\s*([0-9]+)\s*회")

# 결과 li 텍스트에서 항공사명을 뽑기 위한 사전. 등록 노선(한국 출발) 기준 주요 항공사.
# 검색은 '텍스트에 이 이름이 있으면 매칭'이라 레이아웃 변경에 강하다.
# 별칭(영문/약칭)은 CANON 으로 정식 한글명에 합친다.
AIRLINES = [
    "대한항공", "아시아나항공", "진에어", "제주항공", "티웨이항공", "티웨이",
    "에어부산", "에어서울", "이스타항공", "에어프레미아", "에어로케이",
    "일본항공", "전일본공수", "ANA", "JAL", "피치항공", "피치", "집에어",
    "스타플라이어", "스카이마크",
    "중국동방항공", "중국국제항공", "중국남방항공", "상하이항공", "하이난항공",
    "캐세이퍼시픽", "캐세이", "홍콩항공", "그레이터베이항공", "중화항공",
    "에바항공", "스타룩스항공", "스타룩스", "타이거에어",
    "베트남항공", "비엣젯", "뱀부항공", "타이항공", "에어아시아",
    "싱가포르항공", "스쿠트", "스쿳항공", "스쿳", "말레이시아항공", "바틱에어",
    # 아래는 2026-09-09 에 구글 화면에서 직접 확인한 표기다. 사전에 없어서
    # '미상'으로 빠지고 있었다 (최근 2주 관측의 15%, 타이페이 51% · 홍콩 59%).
    # **구글이 쓰는 철자를 그대로** 넣어야 한다 — '스쿠트'만 있어서 '스쿳항공'을 놓쳤다.
    "홍콩 익스프레스항공", "홍콩익스프레스", "심천항공",
    "필리핀항공", "세부퍼시픽", "가루다인도네시아", "라이온에어", "젯스타",
    "몽골항공", "미아트", "에어아스타나",
    "에미레이트", "카타르항공", "에티하드", "터키항공", "루프트한자",
    "에어프랑스", "KLM", "핀에어", "영국항공", "폴란드항공", "유나이티드항공",
    "델타항공", "아메리칸항공", "에어캐나다", "하와이안항공",
]
AIRLINE_CANON = {
    # 구글은 "스쿳항공"으로 쓰지만 블로그 표기는 "스쿠트항공"으로 통일한다.
    "스쿳항공": "스쿠트항공", "스쿳": "스쿠트항공", "스쿠트": "스쿠트항공",
    "홍콩익스프레스": "홍콩 익스프레스항공",
    "티웨이": "티웨이항공", "ANA": "전일본공수", "JAL": "일본항공",
    "피치": "피치항공", "캐세이": "캐세이퍼시픽", "스타룩스": "스타룩스항공",
    "미아트": "몽골항공", "에미레이트": "에미레이트항공", "에티하드": "에티하드항공",
}
# 긴 이름을 먼저 찾도록 정렬 (예: '타이에어아시아' 안의 '에어아시아' 오매칭 완화).
_AIRLINES_BY_LEN = sorted(set(AIRLINES), key=len, reverse=True)


def extract_airline(li_text: str) -> str:
    """li 텍스트에서 항공사명 하나(가장 먼저 등장하는 = 보통 가는편)를 뽑는다.

    못 찾으면 빈 문자열. 순수 함수(단위테스트 가능).
    """
    best = None  # (등장위치, -이름길이, 이름)
    for name in _AIRLINES_BY_LEN:
        idx = li_text.find(name)
        if idx >= 0 and (best is None or (idx, -len(name)) < best[:2]):
            best = (idx, -len(name), name)
    if best is None:
        return ""
    return AIRLINE_CANON.get(best[2], best[2])

# 결과 목록의 각 항공편은 <li> 안에 "...₩487,681 | 왕복" 형태로 총액이 들어있음.
# body 전체 텍스트를 긁으면 날짜별 가격 캘린더 위젯 등 다른 요소의 가격까지 섞여
# 실제보다 훨씬 낮은 값을 최저가로 잘못 고르는 문제가 있어, 결과 리스트 항목만 대상으로 함.
ROUND_TRIP_MARKER = "왕복"
# 가는 편과 오는 편 항공사가 다른 조합권 표기.
MULTI_CARRIER_MARKER = "다구간 항공권"

# 결과 li 가 렌더될 때까지 기다리는 셀렉터 (₩ 가격이 붙은 리스트 항목).
RESULTS_SELECTOR = 'li:has-text("₩")'
RESULTS_TIMEOUT_MS = 20000
SETTLE_MS = 1500  # 셀렉터 등장 후 나머지 항목이 붙을 짧은 여유

# ---- 오는 편 ----
# 구글 항공권 왕복 검색의 첫 화면은 **가는 편 후보만** 늘어놓고 거기에 왕복 총액을
# 붙인다. 오는 편은 가는 편을 하나 클릭해야 나오는 두 번째 화면에 있다
# (2026-09-08 실측). 그래서 오는 편 시각·항공사를 얻으려면 쿼리당 화면 전환이
# 한 번 더 필요하다. 기록할 항목(by_stops 에 남은 것)에 대해서만 들어간다.
RETURN_LIST_MARKERS = ("도착 항공편", "복편")
RETURN_TIMEOUT_MS = 15000

# ---- 실패 분류 상태값 (fetch_result 의 status) ----
STATUS_OK = "ok"                 # 파싱 성공 (by_stops 가 비어있을 수는 있음)
STATUS_NO_FLIGHTS = "no_flights" # 구글이 '결과 없음'을 명시
STATUS_PARSE_ZERO = "parse_zero" # 페이지는 떴는데 왕복/₩ 항목 파싱 0건 (구조 변경/통화 의심)
STATUS_BLOCKED = "blocked"       # 비정상 트래픽/캡차 감지
STATUS_CONSENT = "consent"       # 동의(consent) 페이지에서 벗어나지 못함
STATUS_TIMEOUT = "timeout"       # 페이지 로드 타임아웃
STATUS_ERROR = "error"           # 그 외 예외

CONSENT_BUTTON_SELECTORS = (
    'button:has-text("모두 동의")',
    'button:has-text("Accept all")',
    "#L2AGLb",  # 구글 동의 페이지의 'Accept all' 버튼 id
)
BLOCKED_MARKERS = ("비정상적인 트래픽", "unusual traffic", "recaptcha", "로봇이 아닙니다")
NO_RESULT_MARKERS = (
    "일치하는 항공편이 없습니다",
    "검색 결과가 없습니다",
    "표시할 항공편이 없습니다",
    "No results",
)

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
    "ATH": "Athens, Greece",  # "Athens"만 쓰면 미국 조지아주 Athens와 모호 -> 결과 0건
}


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

    return {
        "price": price, "stops": stops, "dep_time": dep_time, "arr_time": arr_time,
        "airline": extract_airline(li_text),
        # '다구간 항공권' = 가는 편과 오는 편의 항공사가 다른 조합권. 최저가가 이런
        # 경우가 흔한데(2026-09-08 실측: 하노이·나트랑 최저가 둘 다), 가는 편
        # 항공사만 보고 "OO항공 직항 왕복"이라고 쓰면 사실과 달라진다.
        "multi_carrier": MULTI_CARRIER_MARKER in li_text,
    }


def same_itinerary_stats(itineraries):
    """같은 일정(한 번의 검색 결과) 안의 **직항** 항공권 통계. 브라우저 없이 테스트 가능한 순수 함수.

    할인율 정의 (사용자 확정 2026-09-13):
        같은 출발·귀국일의 직항끼리만 비교해서, 이 항공권 가격을
        **나머지 직항 항공권들의 중앙값**과 비교한다. 경유편은 넣지 않는다.

    크롤러는 원래 경유수별 최저가 1건만 남기고 나머지를 버렸다. 그러면 이 비교를
    나중에 할 방법이 없어서, 버리기 전에 중앙값만 요약해 둔다.

    반환: {"nonstop_n": 직항 항공권 수, "nonstop_others_median": 최저가를 뺀 나머지 중앙값 or None}
    같은 편이 li 두 개로 잡혀서(바깥 li 와 안쪽 li) 중복을 뺀다. 키에서 도착시각은 뺀다 —
    안쪽 li 쪽은 도착시각이 출발시각으로 잘못 읽혀(2026-09-13 다낭 실측: 11편이 22편으로 셈)
    같은 편이 다른 편처럼 보인다. (항공사, 출발시각, 가격)으로 묶는다.
    """
    seen = set()
    prices = []
    for it in itineraries or []:
        if it.get("stops") != 0:
            continue
        key = (it.get("airline"), it.get("dep_time"), it.get("price"))
        if key in seen:
            continue
        seen.add(key)
        prices.append(it["price"])
    prices.sort()
    others = prices[1:]
    med = None
    if others:
        mid = len(others) // 2
        med = others[mid] if len(others) % 2 else round((others[mid - 1] + others[mid]) / 2)
    return {"nonstop_n": len(prices), "nonstop_others_median": med}


def classify_no_results(page_url: str, body_text: str, li_count: int, saw_price: bool):
    """결과 li 파싱이 0건일 때 페이지 내용으로 실패 사유를 분류. (순수 함수)"""
    text = body_text or ""
    url = page_url or ""
    if "consent." in url:
        return STATUS_CONSENT, "동의(consent) 페이지에서 벗어나지 못함"
    low = text.lower()
    for marker in BLOCKED_MARKERS:
        if marker.lower() in low:
            return STATUS_BLOCKED, f"차단 의심 문구 감지: '{marker}'"
    for marker in NO_RESULT_MARKERS:
        if marker.lower() in low:
            return STATUS_NO_FLIGHTS, "구글이 '결과 없음'을 표시"
    if li_count == 0:
        # 리스트 자체가 렌더되지 않음 -> 파서 문제가 아니라 로드 실패(느린 페이지/빈 응답).
        # parse_zero 로 분류하면 '사이트 구조 변경'으로 오도된다.
        return STATUS_TIMEOUT, "결과 리스트가 렌더되지 않음 (느린 로드/빈 응답)"
    if not saw_price:
        return STATUS_PARSE_ZERO, f"li {li_count}개 중 ₩ 가격 없음 (통화/로케일 또는 파서 확인)"
    return STATUS_PARSE_ZERO, f"li {li_count}개, 왕복 항목 파싱 0건 (사이트 구조 변경?)"


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


def _default_playwright_factory():
    # build_dashboard_data.py 등 크롤링하지 않는 소비자가 이 모듈을 import 할 때
    # playwright 설치를 요구하지 않도록, 실제 세션 시작 시점에만 lazy import 한다.
    from playwright.sync_api import sync_playwright

    return sync_playwright()


class PriceCrawlerSession:
    """수집 실행 전체가 크로미움 브라우저 하나를 재사용하는 세션.

    with PriceCrawlerSession() as session:
        session.fetch_result(...)  # 쿼리마다 새 페이지만 열고 닫음
    """

    def __init__(self, _playwright_factory=None):
        # _playwright_factory: 테스트에서 sync_playwright 를 가짜로 주입하기 위한 지점.
        self._playwright_factory = _playwright_factory or _default_playwright_factory
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

    def _dismiss_consent(self, page):
        """구글 동의(consent) 페이지로 넘어갔으면 '모두 동의'를 한 번 눌러본다."""
        if "consent." not in page.url:
            return
        for sel in CONSENT_BUTTON_SELECTORS:
            try:
                page.click(sel, timeout=3000)
                page.wait_for_load_state("domcontentloaded", timeout=10000)
                return
            except Exception:
                continue

    def _scan_page(self, page, url, timeout_ms):
        """페이지를 열고 결과 li 를 파싱. (status, itineraries, detail) 반환."""
        page.goto(url, timeout=timeout_ms)
        self._dismiss_consent(page)

        # 고정 sleep 대신 결과 항목이 나타날 때까지만 대기 -> 쿼리당 수 초 단축,
        # 늦게 뜨는 페이지도 타임아웃 한도까지 기다려줌.
        try:
            page.wait_for_selector(RESULTS_SELECTOR, timeout=RESULTS_TIMEOUT_MS)
            page.wait_for_timeout(SETTLE_MS)
        except Exception:
            pass  # 아래에서 파싱 0건이면 페이지 내용으로 사유를 분류

        itineraries = []
        li_count = 0
        saw_price = False
        for idx, li in enumerate(page.query_selector_all("li")):
            li_count += 1
            item_text = li.inner_text()
            if "₩" in item_text:
                saw_price = True
            parsed = parse_itinerary(item_text)
            if parsed is not None:
                # 오는 편을 받으려면 이 li 를 다시 찾아 눌러야 한다 (_scan_return).
                parsed["_li_index"] = idx
                itineraries.append(parsed)

        if itineraries:
            return STATUS_OK, itineraries, ""

        body_text = ""
        try:
            body_text = page.inner_text("body")
        except Exception:
            pass
        status, detail = classify_no_results(page.url, body_text, li_count, saw_price)
        return status, [], detail

    def _on_return_screen(self, page):
        try:
            body = page.inner_text("body")
        except Exception:
            return False
        return any(m in body for m in RETURN_LIST_MARKERS)

    def _scan_return(self, page, li_index, timeout_ms=RETURN_TIMEOUT_MS):
        """가는 편 하나를 눌러 오는 편 목록을 읽는다. 실패하면 None.

        **오는 편 시각은 '그 총액이 성립하는 조합'에 딸린 값이다.** 가는 편만 같고
        오는 편이 다르면 총액이 달라지므로, 여기서 고르는 것은 오는 편 목록의
        최저가 = 그 가는 편과 짝지어 첫 화면에 표시된 총액을 만드는 조합이다.

        실패해도 예외를 올리지 않는다 — 가는 편 데이터는 이미 확보돼 있고, 오는 편
        때문에 수집 전체를 잃는 것이 훨씬 손해다.
        """
        lis = page.query_selector_all("li")
        if li_index >= len(lis):
            return None
        target = lis[li_index]
        try:
            # li 자체는 클릭 대상이 아닐 수 있다. 안쪽 링크/버튼을 먼저 찾는다.
            clickable = target.query_selector("a, [role='link'], button") or target
            clickable.click(timeout=timeout_ms)
            page.wait_for_selector(RESULTS_SELECTOR, timeout=timeout_ms)
            page.wait_for_timeout(SETTLE_MS)
        except Exception:
            return None
        if not self._on_return_screen(page):
            return None       # 화면이 안 넘어갔다. 가는 편 목록을 오는 편으로 오인하지 않는다

        rets = []
        for li in page.query_selector_all("li"):
            parsed = parse_itinerary(li.inner_text())
            if parsed is not None:
                rets.append(parsed)
        if not rets:
            return None
        best = min(rets, key=lambda it: it["price"])
        return {
            "ret_dep_time": best.get("dep_time", ""),
            "ret_arr_time": best.get("arr_time", ""),
            "ret_airline": best.get("airline", ""),
            "ret_price": best.get("price"),
            "multi_carrier": bool(best.get("multi_carrier")),
        }

    def _attach_returns(self, page, best, timeout_ms):
        """by_stops 에 남은 항목마다 오는 편을 붙인다. 실패는 조용히 건너뛴다.

        가는 편을 누르면 화면이 오는 편 목록으로 넘어가므로, 두 번째 항목부터는
        뒤로 가서 목록을 복구한 뒤 누른다.

        **검증**: 첫 화면의 가는 편 행에 붙은 총액은 '그 가는 편으로 낼 수 있는 최저
        총액'이라, 오는 편 목록의 최저가와 같아야 한다. 다르면 엉뚱한 행을 눌렀다는
        뜻이므로 그 오는 편은 버린다 (틀린 시각을 쓰느니 없는 게 낫다).
        """
        for i, s in enumerate(sorted(best)):
            it = best[s]
            if i:
                try:
                    page.go_back(timeout=timeout_ms)
                    page.wait_for_selector(RESULTS_SELECTOR, timeout=timeout_ms)
                    page.wait_for_timeout(SETTLE_MS)
                except Exception:
                    return
            got = self._scan_return(page, it.get("_li_index", -1))
            if not got:
                continue
            if got.pop("ret_price", None) != it["price"]:
                continue
            it.update(got)

    def fetch_result(
        self,
        origin: str,
        destination: str,
        depart: date,
        return_: date,
        timeout_ms=25000,
        origin_city: str | None = None,
        dest_city: str | None = None,
        max_stops=None,
        with_return=True,
    ):
        """typed 수집 결과 반환:
        {"status": str, "by_stops": {stops: itinerary_dict}, "detail": str}

        by_stops 는 경유수 클래스별 최저가 (경유수 미상 항목 제외, max_stops 이하만).
        같은 (o,d,날짜)라도 직항 모니터는 stops=0 최저가를, 경유 모니터는 stops<=1
        최저가를 각각 필요로 하므로, 총액 최저가 1건이 아니라 클래스별 최저가를 모두 담는다.

        브라우저가 죽어 새 페이지조차 못 여는 경우엔 CrawlerSessionError 를 던져
        호출 측이 세션을 재시작할 수 있게 함. 그 외 실패는 status/detail 로 반환.
        """
        url = build_booking_url(origin, destination, depart, return_, origin_city=origin_city, dest_city=dest_city)

        try:
            page = self._browser_context.new_page()
        except Exception as e:
            raise CrawlerSessionError(f"cannot open new page (browser dead?): {e}") from e

        best = {}
        try:
            status, itineraries, detail = self._scan_page(page, url, timeout_ms)
            for parsed in itineraries:
                s = parsed["stops"]
                if s is None:
                    continue  # 경유수 미상 항목은 클래스 오염 방지를 위해 제외
                if max_stops is not None and s > max_stops:
                    continue
                if s not in best or parsed["price"] < best[s]["price"]:
                    best[s] = parsed
            # 같은 일정 직항 비교용 통계는 직항 최저가 행에 붙인다 (할인율 정의 2026-09-13).
            if 0 in best:
                best[0].update(same_itinerary_stats(itineraries))
            # 오는 편은 **기록에 남을 항목에 대해서만** 받는다 (보통 1~2건).
            # 후보 전부에 대해 받으면 쿼리 비용이 몇 배가 된다.
            if with_return and status == STATUS_OK and best:
                self._attach_returns(page, best, timeout_ms)
        except Exception as e:
            kind = STATUS_TIMEOUT if "Timeout" in type(e).__name__ else STATUS_ERROR
            detail = f"{type(e).__name__}: {e}"
            print(f"[google_flights_crawler] {kind} for {origin}->{destination} {depart}~{return_}: {detail}")
            return {"status": kind, "by_stops": {}, "detail": detail[:300]}
        finally:
            try:
                page.close()
            except Exception:
                pass

        if status == STATUS_OK and not best and itineraries:
            detail = "왕복 항목은 있으나 조건(경유수) 내 항목 없음"
        return {"status": status, "by_stops": best, "detail": detail}

    def fetch_min_by_stops(self, *args, **kwargs):
        """(하위호환) 경유수별 최저가 dict 만 반환. 실패/결과 없음 시 {}."""
        return self.fetch_result(*args, **kwargs)["by_stops"]

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
        """
        result = self.fetch_result(
            origin, destination, depart, return_,
            timeout_ms=timeout_ms, origin_city=origin_city, dest_city=dest_city,
            max_stops=max_stops,
        )
        candidates = list(result["by_stops"].values())
        if not candidates:
            return None
        return min(candidates, key=lambda it: it["price"])


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
