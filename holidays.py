"""
한국 공휴일 기반 연휴 구간 계산.

공공데이터포털 API는 서비스키 발급이 필요해, 우선 정적 공휴일 목록으로 시작.
주의: get_holiday_windows()가 기본 180일 앞까지 내다보므로, '다음 해' 목록은
연말이 아니라 늦어도 전년도 6월(= 연말 - lookahead_days)까지 채워져 있어야 한다.
음력 기반 공휴일(설날/부처님오신날/추석)과 대체공휴일도 반드시 포함할 것.
"""
import sys
from datetime import date, timedelta

# {연도: {"YYYY-MM-DD": 이름}}. dict 를 순회하면 날짜 문자열(key)이 나오므로
# 기존의 "for d in days" 소비자(빌드 스크립트 등)와 호환된다.
KOREAN_HOLIDAYS = {
    2026: {
        "2026-01-01": "신정",
        "2026-02-16": "설날", "2026-02-17": "설날", "2026-02-18": "설날",  # 설날 연휴
        "2026-03-01": "삼일절",  # 일요일
        "2026-03-02": "삼일절",  # 대체공휴일 (월)
        "2026-05-05": "어린이날",
        "2026-05-24": "부처님오신날",  # 일요일
        "2026-05-25": "부처님오신날",  # 대체공휴일 (월)
        "2026-06-06": "현충일",  # 토요일이지만 대체공휴일 미적용 대상
        "2026-08-15": "광복절",  # 토요일
        "2026-08-17": "광복절",  # 대체공휴일 (월)
        "2026-09-24": "추석", "2026-09-25": "추석", "2026-09-26": "추석",  # 목~토 (토 겹침 대체 미적용)
        "2026-10-03": "개천절",  # 토요일
        "2026-10-05": "개천절",  # 대체공휴일 (월)
        "2026-10-09": "한글날",
        "2026-12-25": "성탄절",
    },
    2027: {
        "2027-01-01": "신정",
        "2027-02-06": "설날", "2027-02-07": "설날", "2027-02-08": "설날",  # 토~월
        "2027-02-09": "설날",  # 대체공휴일 (설날 당일이 일요일)
        "2027-03-01": "삼일절",
        "2027-05-05": "어린이날",
        "2027-05-13": "부처님오신날",
        "2027-06-06": "현충일",  # 일요일이지만 대체공휴일 미적용 대상
        "2027-08-15": "광복절",
        "2027-08-16": "광복절",  # 대체공휴일 (일요일)
        "2027-09-14": "추석", "2027-09-15": "추석", "2027-09-16": "추석",  # 화~목
        "2027-10-03": "개천절",
        "2027-10-04": "개천절",  # 대체공휴일 (일요일)
        "2027-10-09": "한글날",
        "2027-10-11": "한글날",  # 대체공휴일 (토요일)
        "2027-12-25": "성탄절",
        "2027-12-27": "성탄절",  # 대체공휴일 (토요일)
    },
}


def _all_holidays():
    dates = set()
    for year, days in KOREAN_HOLIDAYS.items():
        for d in days:
            dates.add(date.fromisoformat(d))
    return dates


def holiday_name(d: date) -> str:
    """해당 날짜 공휴일의 이름 (대체공휴일은 원 공휴일 이름). 미등록이면 '공휴일'."""
    return KOREAN_HOLIDAYS.get(d.year, {}).get(d.isoformat(), "공휴일")


def get_holiday_windows(bridge_days=1, lookahead_days=180):
    """
    각 공휴일이 포함된 '연휴 구간'을 계산.
    주말과 이어지는 공휴일은 자동으로 묶고, 앞뒤로 bridge_days 만큼 여행일을 더 붙여준다.
    반환: [{"id": str, "label": str, "start": date, "end": date,
            "holiday_dates": [date, ...]}, ...]
    - id: 구간의 첫 공휴일 날짜(ISO). 조회 시점이 달라져도 같은 연휴면 같은 id.
    - label: 구간에 포함된 공휴일 이름들 (예: "추석", "개천절").
    """
    holidays = sorted(_all_holidays())
    today = date.today()
    horizon = today + timedelta(days=lookahead_days)
    last_year = max(KOREAN_HOLIDAYS)
    if horizon.year > last_year:
        print(
            f"[holidays] 경고: 조회 구간이 {horizon}까지인데 KOREAN_HOLIDAYS는 "
            f"{last_year}년까지만 있습니다. {horizon.year}년 공휴일을 추가하세요.",
            file=sys.stderr,
        )

    # 블록(연휴 구간)은 '전체' 공휴일 목록으로 먼저 만들고 마지막에 기간으로 거른다.
    # today/horizon 으로 먼저 거르면 연휴 진행 중이거나 호라이즌 경계에 걸린 블록의
    # 구성(첫 공휴일)이 조회 시점마다 바뀌어 id 가 흔들리고, prices.csv 에
    # window_id 로 태깅해 둔 행이 matrix 집계에서 고아가 된다.
    off_days = set(holidays)

    def is_off(d):
        return d in off_days or d.weekday() >= 5  # 5=토, 6=일

    windows = []
    visited = set()
    for h in holidays:
        if h in visited:
            continue
        start = h
        while is_off(start - timedelta(days=1)):
            start -= timedelta(days=1)
        end = h
        while is_off(end + timedelta(days=1)):
            end += timedelta(days=1)

        cur = start
        block_holidays = []
        while cur <= end:
            visited.add(cur)
            if cur in off_days and cur.weekday() < 5:
                block_holidays.append(cur)
            cur += timedelta(days=1)

        block = block_holidays or [h]
        names = []
        for hd in block:
            n = holiday_name(hd)
            if n not in names:
                names.append(n)

        travel_start = start - timedelta(days=bridge_days)
        travel_end = end + timedelta(days=bridge_days)
        windows.append({
            "id": block[0].isoformat(),
            "label": "·".join(names),
            "start": travel_start,
            "end": travel_end,
            "holiday_dates": block,
        })

    # 진행 중인 연휴(end 가 아직 안 지남)도 포함해 조회 기간으로 필터.
    # 과거 출발일 후보는 소비자(collect/build)가 각자 걸러낸다.
    return [w for w in windows if w["end"] >= today and w["start"] <= horizon]


def date_range_candidates(window, trip_length_days=3):
    """연휴 구간 안에서 가능한 (출발일, 귀국일) 조합 생성."""
    candidates = []
    cur = window["start"]
    while cur + timedelta(days=trip_length_days) <= window["end"]:
        candidates.append((cur, cur + timedelta(days=trip_length_days)))
        cur += timedelta(days=1)
    return candidates


if __name__ == "__main__":
    for w in get_holiday_windows():
        print(w["id"], w["label"], "|", w["start"], "~", w["end"], "| holidays:", w["holiday_dates"])
