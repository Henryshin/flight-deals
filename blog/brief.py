"""오늘 쓸 만한 글감을 골라 브리핑으로 만든다.

이 모듈은 **글을 쓰지 않는다.** 사실만 모아서 넘긴다.
글은 Claude Code 세션에서 사람이 명령할 때 그때그때 쓴다
(.claude/skills/blog-post/SKILL.md 참고).

글 한 편 = 노선 하나 × 연휴 하나. 예시 포스팅(미야자키 편)과 같은 형태다.
"""
import csv
from datetime import date, datetime, timedelta

from blog import data as D

# 후보 점수 가중치
W_DEAL = 1.0          # 할증률 (평시 대비 얼마나 싼가)
W_NONSTOP = 12.0      # 직항 가산 — 직항 글이 훨씬 잘 읽힌다
W_LEAD = 6.0          # 출발까지 45~150일이면 예약 실익이 큼
W_DROP = 0.6          # 최근 하락폭
# 연차 부담. "연차를 아껴 쓰고 싶은 직장인" 블로그라 짧게 끝나는 일정의 가치가 크다.
# 이게 없으면 긴 일정이 유리해서(기간이 길수록 그 안에 싼 조합이 걸릴 확률이 높다)
# 상위 후보가 죄다 연차 4~8일짜리로 나온다 — 2026-09-08 실측, 사용자 지적.
# 16 은 실측으로 고른 값이다 (2026-09-08). 10 이면 약해서 1위가 연차 4일짜리로
# 남고, 22 이상이면 후보가 전부 연차 1일로 납작해져 긴 일정 선택지가 사라진다.
W_LEAVE = 16.0

# 항공사 비교 표를 만들 때 prices.csv 에서 훑을 최근 일수
AIRLINE_LOOKBACK_DAYS = 14

# 출발까지 이보다 멀면 후보에서 뺀다. 8개월 뒤 여행 글은 지금 써 봐야 검색 수요가
# 아직 없다 — 5월 연휴 수요는 3~4월에 생긴다 (SEO 처방 2026-09-10). 2026-09-09 에
# 부처님오신날(D-245) 타이페이를 썼는데 그 글이 이 함정에 걸린 사례다.
MAX_LEAD_DAYS = 120


def _lead_score(today, depart_iso):
    try:
        lead = (date.fromisoformat(depart_iso) - today).days
    except (TypeError, ValueError):
        return 0.0
    if lead < D.MIN_LEAD_DAYS:
        return 0.0
    if 45 <= lead <= 150:
        return 1.0
    if lead < 45:
        return lead / 45.0
    return max(0.0, 1.0 - (lead - 150) / 400.0)


def _leave_score(leave_days):
    """연차 1일 이하면 만점, 늘수록 깎아 6일부터 0. 모르면 중립값."""
    if not isinstance(leave_days, int):
        return 0.5
    return max(0.0, min(1.0, 1.0 - (leave_days - 1) / 5.0))


def _pair_deal_pct(pair, cell):
    """그 일정의 할인율 = 같은 일정 나머지 직항 중앙값 대비 (사용자 정의 2026-09-13).

    예전엔 연휴 전체 관측가 중앙값(typical) 대비였다. 그건 3박·9박 가격과 지난 30일
    가격이 섞여서 "같은 날 다른 항공권보다 싼가"가 아니었고, 그 숫자로 다낭을 올렸다가
    "결코 특가가 아니다"는 지적을 받았다. 통계가 없는 일정(9/13 이전 수집분)은 0 —
    옛 정의로 대신 채우지 않는다. 채우면 틀린 근거가 다시 순위에 섞인다.
    """
    v = pair.get("same_itin_deal_pct")
    return float(v) if isinstance(v, (int, float)) else 0.0


def pair_score(pair, cell, drop, today):
    return (
        W_DEAL * _pair_deal_pct(pair, cell)
        + (W_NONSTOP if str(pair.get("stops")) == "0" else 0.0)
        + W_LEAD * _lead_score(today, pair.get("depart_date"))
        + W_LEAVE * _leave_score(pair.get("leave_days"))
        + W_DROP * (-drop["pct"] if drop and drop["pct"] < 0 else 0.0)
    )


def pick_pair(pairs, cell, today, drop=None, prefer="auto"):
    """글에 실을 일정 하나를 고른다. 브리핑과 본문·카드가 **같은 것을 봐야** 한다.

    prefer:
      "auto"      가격과 연차 부담을 함께 본 점수 (기본)
      "cheapest"  최저가 — 2026-09-08 이전 동작
      "min_leave" 연차가 가장 적은 것, 동률이면 싼 것
      "2026-10-01:2026-10-07" 출발:귀국 직접 지정
    """
    if not pairs:
        return None
    if prefer in (None, "", "auto"):
        return max(pairs, key=lambda p: pair_score(p, cell, drop, today))
    if prefer == "cheapest":
        return pairs[0]
    if prefer == "min_leave":
        return min(pairs, key=lambda p: (p.get("leave_days", 99), p["price"]))
    if ":" in prefer:
        dep, ret = prefer.split(":", 1)
        for p in pairs:
            if p.get("depart_date") == dep and p.get("return_date") == ret:
                return p
        raise ValueError(f"자료에 없는 일정이다: {prefer}")
    raise ValueError(f"모르는 itinerary 값이다: {prefer}")


def candidates(inputs, today=None, exclude_dests=(), limit=8,
               window_limit=12, dedupe=True):
    """오늘 쓸 만한 (노선 × 연휴) 후보를 점수순으로.

    exclude_dests: 최근에 이미 쓴 목적지 IATA 집합 (원장에서 넘김)
    limit: None 이면 자르지 않는다
    window_limit: 훑을 연휴 창 수. 예전엔 4였는데 그러면 **코앞 연휴만** 보게 된다
        — 리드타임이 짧아 싼 조합이 이미 빠진 구간이라 후보가 약해진다.
        2026-09-08 실측: 데이터가 있는 창이 10개인데 4개만 보고 있었고, 설날(D-150)
        이후는 통째로 빠져 있었다.
    dedupe: 목적지당 최고점 하나만 남길지. 연휴별로 나눠 보여줄 땐 False 로 끈다
        (같은 목적지의 먼 연휴 선택지가 가려지면 안 된다).
    """
    today = today or datetime.now(D.KST).date()
    matrix = inputs["matrix"]
    routes_by_id = inputs["routes_by_id"]
    history = inputs["history"]
    exclude = set(exclude_dests)

    out = []
    for w in D.upcoming_windows(matrix, today, limit=window_limit):
        if (date.fromisoformat(w["start"]) - today).days > MAX_LEAD_DAYS:
            continue          # 수요가 아직 없는 연휴. 검색 유입이 안 난다
        for mid, cell in D.usable_cells(matrix, w["id"], today=today):
            route = routes_by_id.get(mid)
            if not route or route["destination"] in exclude:
                continue
            pairs = D.usable_pairs(cell, today=today)
            if not pairs:
                continue
            drop = D.wow_change(history.get(mid), today)
            best = pick_pair(pairs, cell, today, drop)
            nonstop = str(best.get("stops")) == "0"
            score = pair_score(best, cell, drop, today)
            out.append({
                "monitor_id": mid,
                "window_id": w["id"],
                "window_label": w["label"],
                "window_start": w["start"],
                "lead_days": (date.fromisoformat(w["start"]) - today).days,
                "dest": route["destination"],
                "dest_ko": D.dest_label(mid, routes_by_id),
                "country": route.get("country", ""),
                "flag": route.get("flag", ""),
                "min_price": cell["min_price"],
                # 실을 일정 기준 값. 최저가 일정과 다를 수 있다 (연차 부담 반영).
                "price": best.get("price"),
                "leave_days": best.get("leave_days"),
                "nights": best.get("nights"),
                # 최종 선택은 사람이 하므로 비교할 다른 안도 같이 넘긴다.
                "stops": best.get("stops"),
                # 할인율은 **실을 일정 기준**이다. 셀 deal_pct 는 최저가 기준이라
                # 주인공이 최저가가 아닐 때 어긋난다.
                "deal_pct_pair": _pair_deal_pct(best, cell),
                "cheapest": {k: pairs[0].get(k)
                             for k in ("price", "nights", "leave_days", "stops",
                                       "depart_date", "return_date")},
                "min_leave": {k: min(pairs, key=lambda p: (p.get("leave_days", 99),
                                                           p["price"])).get(k)
                              for k in ("price", "nights", "leave_days", "stops",
                                        "depart_date", "return_date")},
                "typical": cell.get("typical"),
                "deal_pct": cell.get("deal_pct"),
                "nonstop": nonstop,
                "airline": best.get("airline") or "",
                "depart_date": best.get("depart_date"),
                "return_date": best.get("return_date"),
                "n_obs": cell.get("n_obs"),
                "wow": drop,
                "score": round(score, 1),
            })

    if dedupe:
        # 같은 목적지가 여러 연휴에 걸려도 가장 점수 높은 하나만 남긴다
        best_by_dest = {}
        for c in sorted(out, key=lambda c: -c["score"]):
            best_by_dest.setdefault(c["dest"], c)
        out = list(best_by_dest.values())
    ranked = sorted(out, key=lambda c: -c["score"])
    return ranked if limit is None else ranked[:limit]


# --- 개별 소재 상세 자료 -----------------------------------------------------

def _iter_price_rows(root, origin, dest, since):
    """data/prices.csv 에서 한 노선의 최근 행만 훑는다.

    matrix 의 pairs 는 파레토 가지치기를 거쳐서 같은 날짜에 항공사가 하나뿐이다.
    '항공사별 가격비교' 표를 만들려면 원본 CSV 를 봐야 한다.
    """
    path = root / "data" / "prices.csv"
    needle = f"{origin},{dest},"
    with path.open(encoding="utf-8", newline="") as f:
        # csv.writer 가 CRLF 로 쓰므로 필드명 끝의 \r 을 반드시 털어낸다
        header = [c.strip() for c in f.readline().strip().split(",")]
        idx = {name: i for i, name in enumerate(header)}
        for line in f:
            if not line.startswith(needle):
                continue
            row = next(csv.reader([line.strip()]))
            if len(row) < len(header):
                continue
            try:
                collected = row[idx["collected_at"]][:10]
                if collected < since:
                    continue
                price = int(row[idx["price"]])
            except (KeyError, ValueError):
                continue
            if not D.is_plausible_price(price):
                continue
            yield {
                "depart_date": row[idx["depart_date"]],
                "return_date": row[idx["return_date"]],
                "price": price,
                "collected_at": row[idx["collected_at"]],
                "dep_time": row[idx["dep_time"]],
                "arr_time": row[idx["arr_time"]],
                "stops": row[idx["stops"]],
                "airline": row[idx["airline"]],
            }


def airline_compare(root, origin, dest, depart, ret, today=None, lookback=AIRLINE_LOOKBACK_DAYS):
    """같은 일정·같은 수집 시점의 항공사별 가격.

    예시 포스팅의 '항공사별 가격비교' 표에 해당한다. 두 가지를 반드시 맞춘다:

    1. depart/return 이 정확히 같은 행만 본다.
    2. **가장 최근 수집분 하나만** 본다. 여러 날의 최저가를 섞으면 표의 값이
       본문의 '현재 최저가'보다 낮게 나와서 (지난주에 더 쌌으니까) 독자가
       "표가 더 싼데 왜 최저가가 저거지?" 하게 된다.
    """
    today = today or datetime.now(D.KST).date()
    since = (today - timedelta(days=lookback)).isoformat()
    rows = [
        r for r in _iter_price_rows(root, origin, dest, since)
        if r["depart_date"] == depart and r["return_date"] == ret
    ]
    if not rows:
        return []
    latest = max(r["collected_at"] for r in rows)
    by_airline = {}
    for r in rows:
        if r["collected_at"] != latest:
            continue
        name = r["airline"] or "항공사 미상"
        cur = by_airline.get(name)
        if cur is None or r["price"] < cur["price"]:
            by_airline[name] = r
    return sorted(by_airline.values(), key=lambda r: r["price"])


def material(inputs, monitor_id, window_id, today=None, root=None,
             itinerary="auto"):
    """글 한 편에 필요한 사실을 전부 모은다. 여기 없는 숫자는 글에 쓰지 않는다.

    itinerary 는 어느 일정을 주인공으로 삼을지다 (pick_pair 참고). 카드도 이걸
    따라가므로 본문과 카드가 어긋나지 않는다. 초안 머리말의 `itinerary:` 로 넘긴다.
    """
    today = today or datetime.now(D.KST).date()
    root = root or D.ROOT
    matrix = inputs["matrix"]
    route = inputs["routes_by_id"].get(monitor_id)
    if not route:
        raise KeyError(f"모르는 노선: {monitor_id}")
    window = D.window_by_id(matrix, window_id)
    if not window:
        raise KeyError(f"모르는 연휴: {window_id}")
    cell = (matrix.get("cells") or {}).get(window_id, {}).get(monitor_id)
    if not D.is_usable_cell(cell, today=today):
        raise ValueError(
            f"{monitor_id} × {window_id}: 관측이 부족하거나 가격이 신뢰 범위를 벗어남"
        )

    pairs = D.usable_pairs(cell, today=today)
    best = pick_pair(pairs, cell, today,
                     D.wow_change(inputs["history"].get(monitor_id), today),
                     itinerary)
    dep = date.fromisoformat(best["depart_date"])
    status = next(
        (r for r in inputs["routes_status"] if D.monitor_id(r) == monitor_id), {}
    )

    return {
        "monitor_id": monitor_id,
        "origin": route["origin"],
        "dest": route["destination"],
        "dest_ko": D.dest_label(monitor_id, inputs["routes_by_id"]),
        "label": route.get("label", monitor_id),
        "country": route.get("country", ""),
        "flag": route.get("flag", ""),
        "max_stops": route.get("max_stops"),
        "window": window,
        "cell": {
            "min_price": cell["min_price"],
            "typical": cell.get("typical"),
            "deal_pct": cell.get("deal_pct"),
            "tier": cell.get("tier"),
            "n_obs": cell.get("n_obs"),
            "offpeak_baseline": cell.get("offpeak_baseline"),
            "offpeak_ratio": cell.get("offpeak_ratio"),
        },
        "best": best,
        # 주인공 일정을 뺀 나머지. best 가 더는 pairs[0] 이라는 보장이 없다.
        "alternatives": [p for p in pairs if p is not best][:5],
        "airlines": airline_compare(
            root, route["origin"], route["destination"],
            best["depart_date"], best["return_date"], today=today,
        ),
        "history": {
            "min_30d": status.get("min_price_30d"),
            "avg_30d": status.get("avg_price_30d"),
            "wow": D.wow_change(inputs["history"].get(monitor_id), today),
        },
        "climate": D.climate_for(route["destination"], dep.month, inputs["destmeta"]),
        "collected_at": status.get("last_collected_at"),
        "data_generated_at": inputs["meta"]["generated_at"],
        "booking_url": best.get("booking_url"),
        "dashboard_url": "https://excel-flights.xyz/",
    }
