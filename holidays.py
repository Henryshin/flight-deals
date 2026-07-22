"""
한국 공휴일 기반 연휴 구간 계산.

공공데이터포털 API는 서비스키 발급이 필요해, 우선 정적 공휴일 목록으로 시작.
주의: get_holiday_windows()가 기본 180일 앞까지 내다보므로, '다음 해' 목록은
연말이 아니라 늦어도 전년도 6월(= 연말 - lookahead_days)까지 채워져 있어야 한다.
음력 기반 공휴일(설날/부처님오신날/추석)과 대체공휴일도 반드시 포함할 것.
"""
import sys
from datetime import date, timedelta

KOREAN_HOLIDAYS = {
    2026: [
        "2026-01-01",  # 신정
        "2026-02-16", "2026-02-17", "2026-02-18",  # 설날 연휴
        "2026-03-01",  # 삼일절
        "2026-05-05",  # 어린이날
        "2026-05-24",  # 부처님오신날 (대체공휴일 가능성 있음, 확정 시 갱신)
        "2026-06-06",  # 현충일
        "2026-08-15",  # 광복절
        "2026-09-24", "2026-09-25", "2026-09-26",  # 추석 연휴
        "2026-10-03",  # 개천절
        "2026-10-09",  # 한글날
        "2026-12-25",  # 성탄절
    ],
    2027: [
        "2027-01-01",  # 신정
        "2027-02-06", "2027-02-07", "2027-02-08",  # 설날 연휴 (토~월)
        "2027-02-09",  # 설날 대체공휴일 (설날 당일이 일요일)
        "2027-03-01",  # 삼일절
        "2027-05-05",  # 어린이날
        "2027-05-13",  # 부처님오신날
        "2027-06-06",  # 현충일 (일요일이지만 대체공휴일 미적용 대상)
        "2027-08-15",  # 광복절
        "2027-08-16",  # 광복절 대체공휴일 (일요일)
        "2027-09-14", "2027-09-15", "2027-09-16",  # 추석 연휴 (화~목)
        "2027-10-03",  # 개천절
        "2027-10-04",  # 개천절 대체공휴일 (일요일)
        "2027-10-09",  # 한글날
        "2027-10-11",  # 한글날 대체공휴일 (토요일)
        "2027-12-25",  # 성탄절
        "2027-12-27",  # 성탄절 대체공휴일 (토요일)
    ],
}


def _all_holidays():
    dates = set()
    for year, days in KOREAN_HOLIDAYS.items():
        for d in days:
            dates.add(date.fromisoformat(d))
    return dates


def get_holiday_windows(bridge_days=1, lookahead_days=180):
    """
    각 공휴일이 포함된 '연휴 구간'을 계산.
    주말과 이어지는 공휴일은 자동으로 묶고, 앞뒤로 bridge_days 만큼 여행일을 더 붙여준다.
    반환: [{"start": date, "end": date, "holiday_dates": [date, ...]}, ...]
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
    holidays = [d for d in holidays if today <= d <= horizon]

    off_days = set(holidays)
    for d in list(off_days):
        pass  # 주말 연결은 아래에서 확장 시 처리

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

        travel_start = start - timedelta(days=bridge_days)
        travel_end = end + timedelta(days=bridge_days)
        windows.append({
            "start": travel_start,
            "end": travel_end,
            "holiday_dates": block_holidays or [h],
        })

    return windows


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
        print(w["start"], "~", w["end"], "| holidays:", w["holiday_dates"])
