"""Threads 아침 후보 고르기 (매일 07:30 텔레그램으로 3개).

블로그 브리핑(brief.py)과 다른 점 — 2026-11-04 사용자 결정:
- 출발까지 기간 상한 없음 (브리핑은 120일 컷). 대신 14일 안 출발은 뺀다.
- 숙박 일수는 **선호**일 뿐 하드 필터가 아니다:
  아시아 근거리는 4~5박이 좋고 9박은 너무 길다, 유럽·미주·대양주·발리는 5박 이상.
- 싼 가격 자체도 훅이 된다 ("아싸리 싼 걸 보여주자").
- 3개는 서로 다른 권역에서 고른다.

일정 하나 = (노선, 연휴, 연차 일수 L) 에서 **연차 L 개짜리 중 최저가**.
이렇게 정의해야 사이트를 연휴=W, 연차 범위=L~L, 휴일가치=0 으로 맞췄을 때
화면에 정확히 같은 일정이 뜬다 (캡처와 본문이 어긋나지 않는다).
"""
from datetime import date, datetime, timedelta

from blog import data as D
from blog import brief as B

MIN_LEAD_DAYS = 14          # 이보다 가까운 출발은 올려 봐야 못 간다
RECENT_POST_DAYS = 14       # 이 기간 안에 올린 목적지는 다시 안 올린다

W_NIGHTS = 14.0             # 숙박 선호 가산
W_CHEAP = 0.35              # 통상가 대비 할인 % 당 가산 (싼 게 훅)

# 권역. 3개 후보는 서로 다른 권역에서 뽑는다.
REGION = {
    "일본": "일본",
    "대만": "중화권", "홍콩": "중화권", "마카오": "중화권", "중국": "중화권", "몽골": "중화권",
    "베트남": "동남아", "태국": "동남아", "필리핀": "동남아", "말레이시아": "동남아",
    "싱가포르": "동남아", "인도네시아": "동남아", "캄보디아": "동남아",
    "괌": "대양주", "북마리아나제도": "대양주", "호주": "대양주", "뉴질랜드": "대양주",
    "미국": "미주", "캐나다": "미주",
    "카자흐스탄": "유럽", "튀르키예": "유럽",
}
LONG_HAUL_REGIONS = {"유럽", "미주"}
LONG_HAUL_DESTS = {"DPS", "SYD", "MEL", "BNE", "AKL"}   # 발리·호주·뉴질랜드


def region_of(route):
    return REGION.get(route.get("country", ""), "유럽")


def is_long_haul(route):
    return region_of(route) in LONG_HAUL_REGIONS or route["destination"] in LONG_HAUL_DESTS


def nights_pref(route, nights):
    """0~1. 하드 컷이 아니라 점수."""
    if not isinstance(nights, int):
        return 0.3
    if is_long_haul(route):
        if 5 <= nights <= 9:
            return 1.0
        if nights == 4 or 10 <= nights <= 12:
            return 0.5
        return 0.1
    # 아시아 근거리
    if 4 <= nights <= 5:
        return 1.0
    if nights in (3, 6):
        return 0.6
    if nights == 7:
        return 0.2
    return 0.0          # 2박 이하, 8박 이상 — "근교 9박은 너무 길다"


def cheap_pct(pair, cell):
    typ = cell.get("typical")
    if not typ or not pair.get("price"):
        return 0.0
    return max(0.0, (1 - pair["price"] / typ) * 100)


def score(pair, cell, route, today):
    return (B.pair_score(pair, cell, None, today)
            + W_NIGHTS * nights_pref(route, pair.get("nights"))
            + W_CHEAP * cheap_pct(pair, cell))


def leave_options(pairs):
    """연차 일수별 최저가 일정 (pairs 는 가격순)."""
    out = {}
    for p in pairs:
        L = p.get("leave_days")
        if isinstance(L, int) and L not in out:
            out[L] = p
    return out


def candidates(inputs, today=None, recent_dests=(), n=3):
    today = today or datetime.now(D.KST).date()
    matrix = inputs["matrix"]
    routes_by_id = inputs["routes_by_id"]
    recent = set(recent_dests)

    best_by_dest = {}
    for w in D.upcoming_windows(matrix, today):
        for mid, cell in D.usable_cells(matrix, w["id"], today=today):
            route = routes_by_id.get(mid)
            if not route or route["destination"] in recent:
                continue
            pairs = D.usable_pairs(cell, today=today, min_lead_days=MIN_LEAD_DAYS)
            for L, p in leave_options(pairs).items():
                s = score(p, cell, route, today)
                cur = best_by_dest.get(route["destination"])
                if cur is None or s > cur["score"]:
                    best_by_dest[route["destination"]] = {
                        "monitor_id": mid, "route": route, "window": w,
                        "cell": cell, "pair": p, "leave_days": L,
                        "score": round(s, 1),
                    }

    picked, used_regions = [], set()
    for c in sorted(best_by_dest.values(), key=lambda c: -c["score"]):
        reg = region_of(c["route"])
        if reg in used_regions:
            continue
        used_regions.add(reg)
        picked.append(c)
        if len(picked) == n:
            break
    return picked


def summarize(c, routes_by_id):
    """텔레그램·템플릿·캡처가 함께 쓰는 평평한 dict."""
    p, w, route = c["pair"], c["window"], c["route"]
    return {
        "monitor_id": c["monitor_id"],
        "origin": route["origin"],
        "dest": route["destination"],
        "city": D.dest_label(c["monitor_id"], routes_by_id),
        "country": route.get("country", ""),
        "flag": route.get("flag", ""),
        "region": region_of(route),
        "window_id": w["id"],
        "holiday": w["label"],
        "price": p["price"],
        "typical": c["cell"].get("typical"),
        "cheap_pct": round(cheap_pct(p, c["cell"]), 1),
        "depart_date": p["depart_date"],
        "return_date": p["return_date"],
        "depart_weekday": p.get("depart_weekday", ""),
        "return_weekday": p.get("return_weekday", ""),
        "nights": p.get("nights"),
        "days": p.get("days"),
        "leave_days": c["leave_days"],
        "stops": str(p.get("stops")),
        "check": D.nonstop_check(p),
        "airline": p.get("airline") or "",
        "score": c["score"],
    }
