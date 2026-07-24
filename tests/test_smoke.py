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
    via = parse_itinerary("오전 7:00 – 오후 11:20 경유 1회 총 ₩610,000 왕복")
    assert via["stops"] == 1
    unknown = parse_itinerary("총 ₩999,999 왕복")
    assert unknown["stops"] is None  # 직항/경유 문구 없으면 미상


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
