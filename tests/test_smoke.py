"""
브라우저 없이 도는 스모크 테스트 (의존성 없음).

실행: python tests/test_smoke.py
- 크롤러의 순수 함수(파싱/실패 분류)
- 연휴 날짜 후보 생성 (min_nights > 연휴 길이 케이스 포함)
- 평시 기준가 후보의 연휴 회피
- 빌드 스크립트의 dedup / 할증률 계산
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from collector.google_flights_crawler import (
    STATUS_BLOCKED, STATUS_CONSENT, STATUS_NO_FLIGHTS, STATUS_PARSE_ZERO, STATUS_TIMEOUT,
    classify_no_results, parse_itinerary,
)
from holidays import get_holiday_windows
from scripts.collect import build_baseline_candidates, build_date_candidates, group_routes


def test_parse_itinerary():
    li = "오전 9:05 – 오후 1:30+1 대한항공 직항 총 ₩487,681 왕복"
    p = parse_itinerary(li)
    assert p is not None
    assert p["price"] == 487681
    assert p["stops"] == 0
    assert p["dep_time"] == "09:05"
    assert p["arr_time"] == "13:30+1"

    assert parse_itinerary("₩300,000 편도 특가") is None  # 왕복 아님
    assert parse_itinerary("왕복 일정 안내") is None  # 가격 없음
    assert p["airline"] == "대한항공"
    via = parse_itinerary("오전 7:00 – 오후 11:20 경유 1회 총 ₩610,000 왕복")
    assert via["stops"] == 1
    unknown = parse_itinerary("총 ₩999,999 왕복")
    assert unknown["stops"] is None  # 직항/경유 문구 없으면 미상


def test_extract_airline():
    from collector.google_flights_crawler import extract_airline
    assert extract_airline("오전 8:00 대한항공 직항 ₩487,681 왕복") == "대한항공"
    assert extract_airline("... 티웨이 ... 왕복") == "티웨이항공"   # 별칭 -> 정식명
    assert extract_airline("... ANA ... 왕복") == "전일본공수"
    assert extract_airline("항공사 정보 없음") == ""
    # 가는편(먼저 등장) 항공사를 택함
    assert extract_airline("진에어 ... 제주항공 ...") == "진에어"


def test_classify_no_results():
    s, _ = classify_no_results("https://consent.google.com/x", "", 0, False)
    assert s == STATUS_CONSENT
    s, _ = classify_no_results("https://google.com/travel", "비정상적인 트래픽이 감지되었습니다", 3, False)
    assert s == STATUS_BLOCKED
    s, _ = classify_no_results("https://google.com/travel", "일치하는 항공편이 없습니다", 5, False)
    assert s == STATUS_NO_FLIGHTS
    s, d = classify_no_results("https://google.com/travel", "그냥 이상한 페이지", 40, False)
    assert s == STATUS_PARSE_ZERO and "₩" in d  # 통화 힌트
    s, _ = classify_no_results("https://google.com/travel", "가격은 있는데 왕복 파싱 실패 ₩", 40, True)
    assert s == STATUS_PARSE_ZERO
    # 리스트 자체가 안 렌더된 페이지(li 0개)는 '구조 변경'이 아니라 로드 실패로 분류
    s, _ = classify_no_results("https://google.com/travel", "빈 페이지", 0, False)
    assert s == STATUS_TIMEOUT


def test_date_candidates_short_window_unlocked():
    """min_nights 가 연휴 길이보다 커도(장거리 노선) 후보가 생성되어야 한다."""
    today = date(2026, 7, 23)
    for mn in (3, 5, 6, 7):
        cands = build_date_candidates(mn, today=today)
        assert cands, f"min_nights={mn}: 후보 0개"
        for depart, return_, is_holiday, window_id in cands:
            assert depart > today
            assert (return_ - depart).days >= mn
            assert is_holiday and window_id
    # 캡 준수
    assert len(build_date_candidates(3, max_pairs=5, today=today)) == 5


def test_priority_routes_boost_candidates():
    """PRIORITY_FILE 에 있는 노선은 후보 상한이 부스트되고, 파일이 없으면 빈 집합(무동작)이어야 한다."""
    import json
    import tempfile
    from scripts import collect

    tmp = Path(tempfile.mktemp(suffix=".json"))
    orig = collect.PRIORITY_FILE
    try:
        collect.PRIORITY_FILE = tmp
        assert collect.load_priority_routes() == set()  # 파일 없음 -> 무동작

        tmp.write_text(json.dumps(["icn-khh", " icn-han ", ""]), encoding="utf-8")
        assert collect.load_priority_routes() == {"ICN-KHH", "ICN-HAN"}
    finally:
        collect.PRIORITY_FILE = orig
        tmp.unlink(missing_ok=True)

    today = date(2026, 7, 23)
    base = build_date_candidates(3, max_pairs=collect.MAX_PAIRS_PER_ROUTE, today=today)
    boosted = build_date_candidates(
        3, max_pairs=int(collect.MAX_PAIRS_PER_ROUTE * collect.PRIORITY_BOOST), today=today
    )
    assert len(boosted) > len(base), "부스트된 상한이 더 많은 후보를 내야 한다"


def test_candidates_overlap_their_window():
    """후보 일정은 반드시 자기 연휴 윈도우와 겹쳐야 한다."""
    today = date(2026, 7, 23)
    windows = {w["id"]: w for w in get_holiday_windows()}
    for depart, return_, _hol, wid in build_date_candidates(6, today=today):
        w = windows[wid]
        assert depart <= w["end"] and return_ >= w["start"], (depart, return_, wid)


def test_baseline_avoids_holidays():
    today = date(2026, 7, 23)
    windows = get_holiday_windows()
    for depart, return_ in build_baseline_candidates(3, today=today):
        for w in windows:
            near = depart <= w["end"] + timedelta(days=3) and return_ >= w["start"] - timedelta(days=3)
            assert not near, f"평시 후보 {depart}~{return_} 가 연휴 {w['id']} 와 근접"


def test_build_matrix_deal():
    """특가율 = 그 연휴 통상 시세(중앙값) 대비 현재 최저가가 얼마나 싼지."""
    from scripts.build_dashboard_data import build_matrix_cell

    w = {"id": "2026-09-24", "start": date(2026, 9, 23), "end": date(2026, 9, 28)}
    today = date(2026, 7, 23)
    route = {"origin": "ICN", "destination": "NRT"}
    D1, D2 = "2026-07-21T01:00:00+00:00", "2026-07-22T01:00:00+00:00"
    mk = lambda dd, rd, price, hol, ts, stops="0": {
        "origin": "ICN", "destination": "NRT", "depart_date": dd, "return_date": rd,
        "price": price, "is_holiday_window": hol, "collected_at": ts,
        "dep_time": "", "arr_time": "", "stops": stops, "window_id": "2026-09-24" if hol else "",
    }
    # 3개 날짜쌍 x 2일 관측 = 6건. 각 쌍의 최신(D2) 최저가 = 500/460/510k, 최저 460k.
    holiday_rows = [
        mk("2026-09-23", "2026-09-26", 550000, True, D1),
        mk("2026-09-23", "2026-09-26", 500000, True, D2),
        mk("2026-09-24", "2026-09-27", 480000, True, D1),
        mk("2026-09-24", "2026-09-27", 460000, True, D2),
        mk("2026-09-25", "2026-09-28", 520000, True, D1),
        mk("2026-09-25", "2026-09-28", 510000, True, D2),
    ]
    offpeak = [
        mk("2026-08-25", "2026-08-28", 400000, False, D2),
        mk("2026-10-20", "2026-10-23", 380000, False, D2),
        mk("2026-11-03", "2026-11-06", 420000, False, D2),
    ]
    cell = build_matrix_cell(
        holiday_rows, offpeak, 1, w, route, today,
        today - timedelta(days=30), today - timedelta(days=60),
    )
    assert cell is not None
    assert cell["min_price"] == 460000
    assert cell["typical"] == 505000        # median([460,480,500,510,520,550]k)
    assert abs(cell["deal_pct"] - 8.9) < 0.1  # (505-460)/505
    assert cell["tier"] == "A"               # 관측 6건, 날짜쌍 3개
    assert cell["offpeak_baseline"] == 400000  # 참고용 평시 중앙값
    assert abs(cell["offpeak_ratio"] - 1.15) < 0.001

    # 관측이 적으면(1건) tier C — 통상 시세 신뢰 불가
    cell_c = build_matrix_cell(
        [mk("2026-09-23", "2026-09-26", 500000, True, D2)], [], 1, w, route, today,
        today - timedelta(days=30), today - timedelta(days=60),
    )
    assert cell_c["tier"] == "C" and cell_c["min_price"] == 500000


def test_matrix_pairs_keep_all_candidates_with_prev_price():
    """셀은 후보를 미리 추리지 않고 전부 싣고, 날짜쌍별 직전 관측가를 함께 준다.

    거르는 일은 프론트(연차 상한/직항·경유)가 사용자 선택을 받은 뒤에 하므로,
    빌드 단계에서 줄이면 필터가 찾을 대상 자체가 사라진다. 예전 (가격, 덤휴일)
    파레토 프런티어는 '가장 싼 일정이 덤 휴일도 최대'인 칸에서 나머지를 전부
    버려, 연차를 적게 쓰는 짧은 일정이 필터에 걸리지 않는 회귀가 있었다.
    """
    from scripts.build_dashboard_data import build_matrix_cell

    w = {"id": "2026-10-05", "start": date(2026, 10, 3), "end": date(2026, 10, 12)}
    today = date(2026, 8, 5)
    route = {"origin": "ICN", "destination": "KHH"}
    D1, D2 = "2026-08-03T01:00:00+00:00", "2026-08-04T01:00:00+00:00"
    mk = lambda dd, rd, price, ts: {
        "origin": "ICN", "destination": "KHH", "depart_date": dd, "return_date": rd,
        "price": price, "is_holiday_window": True, "collected_at": ts,
        "dep_time": "", "arr_time": "", "stops": "0", "window_id": "2026-10-05",
    }
    rows = [
        # 최저가이면서 덤 휴일도 최대인 일정 — 옛 로직에선 이 하나만 남았다
        mk("2026-10-03", "2026-10-12", 380000, D1),
        mk("2026-10-03", "2026-10-12", 370830, D2),
        # 더 비싸고 덤 휴일도 적지만, 연차를 1개만 쓰는 일정
        mk("2026-10-03", "2026-10-06", 398730, D2),
        # 관측이 한 번뿐인 일정 -> prev_price 는 None
        mk("2026-10-04", "2026-10-08", 413011, D2),
    ]
    cell = build_matrix_cell(
        rows, [], 1, w, route, today,
        today - timedelta(days=30), today - timedelta(days=60),
    )
    pairs = {(p["depart_date"], p["return_date"]): p for p in cell["pairs"]}
    assert len(pairs) == 3, f"후보 3개가 다 남아야 하는데 {len(pairs)}개"

    # 연차 1개짜리 일정이 살아남아야 프론트의 연차 상한 필터가 찾을 수 있다
    short = pairs[("2026-10-03", "2026-10-06")]
    assert short["leave_days"] == 1

    # pairs[0] 은 여전히 최저가 (min_price/best/특가율 기준이 흔들리면 안 됨)
    assert cell["pairs"][0]["price"] == cell["min_price"] == 370830
    assert cell["best"]["return_date"] == "2026-10-12"

    # 직전 관측가: 두 번 관측된 일정만 값이 있고, 한 번뿐이면 None
    assert pairs[("2026-10-03", "2026-10-12")]["prev_price"] == 380000
    assert pairs[("2026-10-04", "2026-10-08")]["prev_price"] is None


def test_group_routes_nights_variants():
    """같은 (o,d)에 박수가 다른 모니터가 있으면 값별 후보가 모두 크롤되어야 한다."""
    routes = [
        {"origin": "ICN", "destination": "NRT", "min_nights": 3, "max_stops": 1},
        {"origin": "ICN", "destination": "NRT", "min_nights": 7, "max_stops": 0, "id": "ICN-NRT-d"},
    ]
    specs = group_routes(routes)
    assert len(specs) == 1
    assert specs[0][4] == [3, 7]  # nights_variants
    assert specs[0][5] == 1       # max_stops 는 가장 넓은 정책


def test_latest_min_same_timestamp_cluster():
    """같은 시각에 저장된 클래스별(직항/경유) 행 중 최저가를 '현재값'으로 골라야 한다."""
    from scripts.build_dashboard_data import _latest_min
    rows = [
        {"collected_at": "2026-07-23T01:00:00+00:00", "price": 500000},
        {"collected_at": "2026-07-23T01:00:00+00:00", "price": 460000},
        {"collected_at": "2026-07-22T01:00:00+00:00", "price": 300000},
    ]
    picked = _latest_min(rows)
    assert picked["price"] == 460000  # 과거의 30만원도, 같은 시각의 50만원도 아님


def test_window_id_stable_during_window():
    """연휴가 진행 중이어도(첫 공휴일이 지나도) 윈도우 id 가 유지되어야 한다.

    today 필터를 블록 구성 '전'에 적용하던 버그: 2026-09-25 시점에 추석 id 가
    2026-09-24 -> 2026-09-25 로 밀리며 window_id 태깅 행이 matrix 에서 고아가 됐다.
    """
    import holidays as hol

    class FakeDate(date):
        @classmethod
        def today(cls):
            return date(2026, 9, 25)

    orig = hol.date
    hol.date = FakeDate
    try:
        ids = {w["id"]: w for w in hol.get_holiday_windows()}
    finally:
        hol.date = orig
    assert "2026-09-24" in ids, f"추석 id 가 유지되어야 함: {sorted(ids)}"
    w = ids["2026-09-24"]
    assert w["label"] == "추석"


def test_prune_prices_removes_only_old_rows():
    """가지치기: PRUNE_RETENTION_DAYS보다 오래된 관측치만 지우고, 최근 행은 그대로 둔다."""
    import csv
    import tempfile

    from scripts import prune_prices

    today = date(2026, 7, 27)
    old_day = (today - timedelta(days=prune_prices.PRUNE_RETENTION_DAYS + 5)).isoformat()
    recent_day = (today - timedelta(days=5)).isoformat()
    fieldnames = [
        "origin", "destination", "depart_date", "return_date", "price",
        "is_holiday_window", "collected_at", "dep_time", "arr_time", "stops",
        "window_id", "airline",
    ]
    mk = lambda day, price: {
        "origin": "ICN", "destination": "NRT", "depart_date": "2026-09-23", "return_date": "2026-09-26",
        "price": price, "is_holiday_window": "1", "collected_at": f"{day}T01:00:00+00:00",
        "dep_time": "", "arr_time": "", "stops": "0", "window_id": "2026-09-24", "airline": "",
    }
    rows = [mk(old_day, "500000"), mk(recent_day, "480000")]

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        tmp_path = Path(f.name)

    orig_file = prune_prices.PRICES_FILE
    prune_prices.PRICES_FILE = tmp_path
    try:
        # dry-run은 오래된 행이 있어도 파일을 바꾸지 않아야 한다
        prune_prices.prune(dry_run=True, today=today)
        with open(tmp_path, encoding="utf-8", newline="") as f:
            assert len(list(csv.DictReader(f))) == 2, "dry-run인데 파일이 바뀜"

        prune_prices.prune(today=today)
        with open(tmp_path, encoding="utf-8", newline="") as f:
            kept = list(csv.DictReader(f))
        assert len(kept) == 1, f"오래된 행만 제거되고 1행 남아야 하는데 {len(kept)}행: {kept}"
        assert kept[0]["price"] == "480000"
    finally:
        prune_prices.PRICES_FILE = orig_file
        tmp_path.unlink(missing_ok=True)


def test_destmeta_covers_all_destinations():
    """routes.json 의 모든 목적지가 destmeta.js 에 등록되어 있어야 한다 (컨셉 필터/기후 배지)."""
    import json
    import re

    root = Path(__file__).parent.parent
    routes = json.loads((root / "data" / "routes.json").read_text(encoding="utf-8"))
    dests = {r["destination"] for r in routes}
    js = (root / "docs" / "destmeta.js").read_text(encoding="utf-8")
    entries = dict(re.findall(r"d\('([A-Z]{3})',\s*'([^']+)'", js))
    missing = dests - set(entries)
    assert not missing, f"destmeta.js 에 없는 목적지: {sorted(missing)}"
    allowed = {"휴양", "도시", "대자연"}
    for iata, concepts in entries.items():
        tags = set(concepts.split("+"))
        assert tags and tags <= allowed, f"{iata}: 잘못된 컨셉 태그 {tags - allowed}"
    # 강수 시즌 문자열은 반드시 12자(1~12월)
    for m in re.finditer(r"d\('([A-Z]{3})'[^)]*'([dmw]+)'\)", js):
        assert len(m.group(2)) == 12, f"{m.group(1)}: r 문자열이 {len(m.group(2))}자 (12자여야 함)"


# =============================================================================
# 블로그 파이프라인 (blog/, scripts/blog_*.py)
# =============================================================================

def _cell(min_price=277300, typical=585400, n_obs=41, n_pairs=5, deal=52.6,
          depart="2026-11-01", ret="2026-11-05", price=None, **extra):
    """손으로 만든 matrix 셀. build_matrix_cell 의 출력 모양을 흉내낸다."""
    pair = {
        "price": price if price is not None else min_price,
        "prev_price": min_price, "depart_date": depart, "return_date": ret,
        "depart_weekday": "토", "return_weekday": "수", "nights": 4, "days": 5,
        "leave_days": 2, "bonus_days": 1, "stops": "0", "airline": "에어서울",
        "dep_time": "08:45", "arr_time": "10:30", "booking_url": "https://x/",
    }
    cell = {
        "min_price": min_price, "typical": typical, "deal_pct": deal,
        "tier": "A", "n_obs": n_obs, "n_pairs": n_pairs,
        "offpeak_baseline": 509250, "offpeak_ratio": 0.54,
        "pairs": [pair], "best": pair,
    }
    cell.update(extra)
    return cell


def test_blog_rejects_implausible_prices():
    """실제 버그의 회귀 테스트.

    data/prices.csv 에 ₩333 같은 통화/렌더 아티팩트가 15행 들어와 있어서
    matrix.json 에 '밀라노 왕복 755원 (tier A, 할증률 99.9%)' 셀이 만들어져 있다.
    저장소 상류에는 하한선 검사가 없으므로 블로그 쪽에서 반드시 걸러야 한다.
    """
    from blog import data as D

    assert D.is_plausible_price(291300, typical=615582)   # 실제 추석 특가
    assert D.is_plausible_price(277300, typical=585400)
    assert not D.is_plausible_price(755, typical=1293800)  # 밀라노 아티팩트
    assert not D.is_plausible_price(1161, typical=598600)  # 울란바토르 아티팩트
    assert not D.is_plausible_price(337)
    assert not D.is_plausible_price(0)
    assert not D.is_plausible_price(None)
    # 하한선 자체
    assert not D.is_plausible_price(D.PRICE_FLOOR_KRW - 1)
    assert D.is_plausible_price(D.PRICE_FLOOR_KRW + 1)

    today = date(2026, 10, 1)
    assert D.is_usable_cell(_cell(), today=today)
    assert not D.is_usable_cell(_cell(min_price=755, typical=1293800, price=755),
                                today=today)


def test_blog_thin_data_guardrail():
    """관측이 얇거나 출발이 코앞이면 글감으로 쓰지 않는다."""
    from blog import data as D

    today = date(2026, 10, 1)
    assert not D.is_usable_cell(_cell(n_obs=2), today=today)
    assert not D.is_usable_cell(_cell(n_pairs=1), today=today)
    assert not D.is_usable_cell(_cell(deal=99.9), today=today)   # 말이 안 되는 할증률
    # 출발이 리드타임 안쪽이면 그 일정은 빠지고, 남는 게 없으면 셀도 못 쓴다
    soon = _cell(depart="2026-10-03", ret="2026-10-07")
    assert D.usable_pairs(soon, today=today) == []
    assert not D.is_usable_cell(soon, today=today)


def test_blog_stale_data_guardrail():
    from datetime import datetime, timezone
    from blog import data as D

    now = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    fresh = {"generated_at": "2026-09-07T06:00:00+00:00"}
    stale = {"generated_at": "2026-09-05T06:00:00+00:00"}
    assert D.assert_fresh(fresh, now) < D.MAX_DATA_AGE_HOURS
    try:
        D.assert_fresh(stale, now)
        raise AssertionError("오래된 데이터인데 통과했다")
    except D.StaleDataError:
        pass


def test_blog_wow_needs_enough_points():
    """주간 비교는 양쪽에 관측이 충분할 때만. 아니면 '내렸다'를 쓸 근거가 없다."""
    from blog import data as D

    today = date(2026, 9, 14)

    def series(days_prices):
        return [{"t": (today - timedelta(days=d)).isoformat(), "min": p}
                for d, p in days_prices]

    assert D.wow_change(series([(0, 300000), (1, 310000)]), today) is None
    full = series([(0, 300000), (2, 305000), (4, 310000),
                   (7, 400000), (9, 410000), (11, 420000)])
    w = D.wow_change(full, today)
    assert w["now"] == 300000 and w["prev"] == 400000
    assert w["pct"] == -25.0
    # 아티팩트 포인트는 계산에서 빠진다
    dirty = full + [{"t": today.isoformat(), "min": 755}]
    assert D.wow_change(dirty, today)["now"] == 300000


def test_blog_climate_matches_destmeta():
    """감성 문장도 destmeta 값을 벗어나면 안 되므로, 파서가 정확해야 한다."""
    from blog import data as D

    dm = D.parse_destmeta()
    # KMI: 'ddmmwwwwmmdd' -> 1월 건기, 7월 우기, 12월 건기
    assert D.climate_for("KMI", 1, dm)["rain"] == "건기"
    assert D.climate_for("KMI", 7, dm)["rain"] == "우기"
    assert D.climate_for("KMI", 12, dm)["rain"] == "건기"
    assert D.climate_for("KMI", 12, dm)["temp"] == 14
    assert D.climate_for("없는공항", 5, dm) is None


def test_blog_destmeta_parser_matches_smoke_regex():
    """blog/data.py 의 파서와 이 파일의 기존 정규식이 같은 목적지를 봐야 한다."""
    import re
    from blog import data as D

    root = Path(__file__).parent.parent
    js = (root / "docs" / "destmeta.js").read_text(encoding="utf-8")
    legacy = set(re.findall(r"d\('([A-Z]{3})',\s*'[^']+'", js))
    assert set(D.parse_destmeta()) == legacy


def test_blog_slug_is_filesystem_safe():
    import re as _re
    from blog import store as S

    assert S.slugify("인천→도쿄 🇯🇵", "route") == "route"   # 한글은 ASCII 로 못 옮긴다
    assert S.slugify("ICN-KMI") == "icn-kmi"
    assert S.slugify("2026-09-24") == "2026-09-24"
    assert S.slugify("con") == "post"                       # Windows 예약 장치명
    assert S.slugify("  ") == "post"
    slug = S.post_slug(date(2026, 9, 7), "ICN-KMI", "2026-09-24")
    assert slug == "2026-09-07-icn-kmi-2026-09-24"
    assert _re.match(r"^[a-z0-9][a-z0-9-]{0,80}$", slug)
    assert not set(slug) & set('<>:"/\\|?*')


def test_blog_ledger_roundtrip():
    import json
    import tempfile
    from blog import store as S

    tmp = Path(tempfile.mktemp(suffix=".json"))
    orig = S.LEDGER_FILE
    try:
        S.LEDGER_FILE = tmp
        assert S.load_ledger() == {"version": 1, "posts": []}   # 파일 없음
        led = S.load_ledger()
        for i in range(S.LEDGER_KEEP + 5):
            S.append_entry(led, {"slug": f"s{i}", "date": "2026-09-07", "dest": "KMI"})
        S.save_ledger(led)
        again = S.load_ledger()
        assert len(again["posts"]) == S.LEDGER_KEEP     # 오래된 것부터 잘린다
        assert again["posts"][-1]["slug"] == f"s{S.LEDGER_KEEP + 4}"
        assert "updated_at" in again

        today = date(2026, 9, 7)
        recent = {"posts": [
            {"date": "2026-09-05", "dest": "KMI"},
            {"date": "2026-08-01", "dest": "NRT"},
        ]}
        assert S.recent_dests(recent, today, 21) == {"KMI"}
    finally:
        S.LEDGER_FILE = orig
        tmp.unlink(missing_ok=True)


def test_blog_render_parses_draft():
    from blog import render as R

    draft = (
        "---\n"
        "title: 추석 다카마쓰 항공권\n"
        "tags: [다카마쓰항공권, 다카마쓰여행]\n"
        "monitor_id: ICN-TAK\n"
        "window_id: 2026-09-24\n"
        "---\n\n"
        "첫 줄이에요.\n둘째 줄이에요.\n\n"
        "## 소제목\n\n"
        "**볼드**와 [링크](https://example.com/a) 예요.\n\n"
        "> 가는 편: 08:45\n> 오는 편: 12:30\n\n"
        "- 하나\n- 둘\n\n"
        "| 항공사 | 가격 |\n|---|---|\n| 에어서울 | 277,300원 |\n\n"
        "[[CARD:price]]\n\n"
        "[[PHOTO: 리츠린 공원]]\n"
    )
    meta, body = R.parse_front_matter(draft)
    assert meta["title"] == "추석 다카마쓰 항공권"
    assert meta["tags"] == ["다카마쓰항공권", "다카마쓰여행"]
    assert meta["monitor_id"] == "ICN-TAK"

    blocks = R.parse_body(body)
    kinds = [b["type"] for b in blocks]
    assert kinds == ["html", "card", "photo"], kinds
    h = blocks[0]["content"]
    assert "<p>첫 줄이에요.<br>둘째 줄이에요.</p>" in h
    assert "<h3>소제목</h3>" in h
    assert "<strong>볼드</strong>" in h
    assert '<a href="https://example.com/a">링크</a>' in h
    assert "<blockquote>가는 편: 08:45<br>오는 편: 12:30</blockquote>" in h
    assert "<ul><li>하나</li><li>둘</li></ul>" in h
    assert "<th>항공사</th>" in h and "<td>277,300원</td>" in h
    assert "|---|" not in h                       # 표 구분선은 행이 아니다
    assert blocks[1]["card"] == "price"
    assert blocks[2]["hint"] == "리츠린 공원"


def test_blog_render_uses_allowed_tags_only():
    """SmartEditor 가 지우는 태그·속성이 본문에 들어가면 안 된다."""
    from blog import render as R

    body = ("<script>alert(1)</script> 와 <b onclick='x'>주입</b> 시도\n\n"
            "## 제목 & 기호\n\n[[PHOTO: x]]\n")
    blocks = R.parse_body(body)
    assert R.used_tags(blocks) <= R.ALLOWED_TAGS, R.used_tags(blocks)
    joined = "".join(b.get("content", "") for b in blocks)
    # 태그로 살아남은 게 없어야 한다. "onclick" 이라는 글자 자체는 이스케이프된
    # 본문 텍스트로 남는 게 정상이므로, 여는 꺾쇠가 붙은 형태만 본다.
    assert "<script" not in joined
    assert "<b " not in joined and "<b>" not in joined
    assert "&lt;script&gt;" in joined              # 이스케이프되어 텍스트로 남는다
    assert "&amp;" in joined


def test_blog_front_matter_requires_delimiters():
    from blog import render as R

    for bad in ("제목만 있는 글", "---\ntitle: x\n본문"):
        try:
            R.parse_front_matter(bad)
            raise AssertionError(f"머리말이 잘못됐는데 통과했다: {bad!r}")
        except R.DraftError:
            pass


def test_blog_validate_rejects_unbacked_price():
    """본문 금액은 자료에 있는 값(또는 값들의 차이)이어야 한다."""
    import importlib
    from blog import render as R

    bs = importlib.import_module("scripts.blog_save")
    mat = {
        "cell": _cell(), "best": _cell()["best"], "alternatives": [],
        "airlines": [{"price": 529500, "airline": "ANA", "stops": "1",
                      "dep_time": "19:55", "arr_time": "21:25"}],
        "history": {"min_30d": 262300, "avg_30d": None, "wow": {"now": 1, "prev": 2}},
        "climate": {"rain": "우기", "temp": 28, "humidity": 73, "concepts": ["도시"]},
    }
    ok = bs.known_prices(mat)
    assert 277300 in ok and 585400 in ok and 529500 in ok
    assert (585400 - 277300) in ok            # 차이도 글에 자주 쓴다
    assert 999999 not in ok

    meta = {"title": "제목", "monitor_id": "ICN-TAK", "window_id": "2026-09-24",
            "tags": ["a", "b", "c"]}
    blocks = R.parse_body("본문 277,300원 이에요.\n\n[[PHOTO: x]]\n")
    assert bs.validate(meta, blocks, "본문 277,300원 이에요.", mat) == []

    errs = bs.validate(meta, blocks, "본문 999,999원 이에요.", mat)
    assert any("자료에 없는 금액" in e for e in errs), errs


def test_blog_validate_rejects_hype_and_missing_photo():
    import importlib
    from blog import render as R

    bs = importlib.import_module("scripts.blog_save")
    mat = {
        "cell": _cell(), "best": _cell()["best"], "alternatives": [], "airlines": [],
        "history": {"min_30d": None, "avg_30d": None, "wow": None},
        "climate": None,
    }
    meta = {"title": "제목", "monitor_id": "ICN-TAK", "window_id": "2026-09-24",
            "tags": ["a", "b", "c"]}

    errs = bs.validate(meta, R.parse_body("역대급 특가예요\n\n[[PHOTO: x]]\n"),
                       "역대급 특가예요", mat)
    assert any("과장 표현" in e for e in errs), errs

    errs = bs.validate(meta, R.parse_body("담백한 글이에요\n"), "담백한 글이에요", mat)
    assert any("PHOTO" in e for e in errs), errs

    # 주간 관측이 없는데 '내렸다'를 쓰면 막는다
    errs = bs.validate(meta, R.parse_body("가격이 내렸어요\n\n[[PHOTO: x]]\n"),
                       "가격이 내렸어요", mat)
    assert any("내렸다" in e for e in errs), errs

    # 기후 자료가 없는데 날씨를 쓰면 막는다
    errs = bs.validate(meta, R.parse_body("건기라 좋아요\n\n[[PHOTO: x]]\n"),
                       "건기라 좋아요", mat)
    assert any("기후" in e for e in errs), errs

    # 태그 개수/형식
    bad = dict(meta, tags=["a b", "#c"])
    errs = bs.validate(bad, R.parse_body("글\n\n[[PHOTO: x]]\n"), "글", mat)
    assert any("태그" in e for e in errs), errs


def test_blog_airline_compare_uses_one_snapshot():
    """여러 날의 최저가를 섞으면 표가 본문 최저가보다 싸져서 독자가 혼란스럽다."""
    import tempfile
    from blog import brief as B

    root = Path(tempfile.mkdtemp())
    (root / "data").mkdir()
    (root / "data" / "prices.csv").write_text(
        "origin,destination,depart_date,return_date,price,is_holiday_window,"
        "collected_at,dep_time,arr_time,stops,window_id,airline\r\n"
        # 지난주(더 쌌던) 수집 — 표에 들어오면 안 된다
        "ICN,TAK,2026-11-01,2026-11-05,262300,1,2026-09-01T00:00:00+00:00,"
        "08:45,10:30,0,2026-11-01,에어서울\r\n"
        # 최신 수집
        "ICN,TAK,2026-11-01,2026-11-05,277300,1,2026-09-06T00:00:00+00:00,"
        "08:45,10:30,0,2026-11-01,에어서울\r\n"
        "ICN,TAK,2026-11-01,2026-11-05,529500,1,2026-09-06T00:00:00+00:00,"
        "19:55,21:25,1,2026-11-01,전일본공수\r\n"
        # 아티팩트는 아예 안 보인다
        "ICN,TAK,2026-11-01,2026-11-05,333,1,2026-09-06T00:00:00+00:00,"
        "08:45,10:30,0,2026-11-01,진에어\r\n"
        # 다른 일정은 비교 대상이 아니다
        "ICN,TAK,2026-11-02,2026-11-06,199000,1,2026-09-06T00:00:00+00:00,"
        "08:45,10:30,0,2026-11-01,진에어\r\n",
        encoding="utf-8",
    )
    rows = B.airline_compare(root, "ICN", "TAK", "2026-11-01", "2026-11-05",
                             today=date(2026, 9, 7))
    assert [r["airline"] for r in rows] == ["에어서울", "전일본공수"]
    assert rows[0]["price"] == 277300          # 지난주의 262,300 이 아니다
    assert all(r["collected_at"] == "2026-09-06T00:00:00+00:00" for r in rows)


def test_blog_posts_match_ledger():
    """posts/ 의 글과 원장이 어긋나지 않아야 한다 (실제 파일 정합성)."""
    import json
    from blog import store as S

    if not S.POSTS_DIR.exists():
        return
    dirs = {d.name for d in S.POSTS_DIR.iterdir()
            if d.is_dir() and not d.name.startswith("_")}
    led = {p["slug"] for p in S.load_ledger().get("posts", [])}
    assert dirs <= led, f"원장에 없는 글: {sorted(dirs - led)}"
    for d in dirs:
        post = json.loads((S.POSTS_DIR / d / "post.json").read_text(encoding="utf-8"))
        assert post["slug"] == d
        assert post["title"] and post["blocks"]
        for b in post["blocks"]:
            if b["type"] == "card":
                assert (S.POSTS_DIR / d / b["file"]).exists(), f"{d}: {b['file']} 없음"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    if failed:
        sys.exit(1)
    print(f"{len(tests)}개 테스트 통과")


if __name__ == "__main__":
    main()
