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

# 항공사 비교 표를 만들 때 prices.csv 에서 훑을 최근 일수
AIRLINE_LOOKBACK_DAYS = 14


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


def candidates(inputs, today=None, exclude_dests=(), limit=8, window_limit=4):
    """오늘 쓸 만한 (노선 × 연휴) 후보를 점수순으로.

    exclude_dests: 최근에 이미 쓴 목적지 IATA 집합 (원장에서 넘김)
    """
    today = today or datetime.now(D.KST).date()
    matrix = inputs["matrix"]
    routes_by_id = inputs["routes_by_id"]
    history = inputs["history"]
    exclude = set(exclude_dests)

    out = []
    for w in D.upcoming_windows(matrix, today, limit=window_limit):
        for mid, cell in D.usable_cells(matrix, w["id"], today=today):
            route = routes_by_id.get(mid)
            if not route or route["destination"] in exclude:
                continue
            pairs = D.usable_pairs(cell, today=today)
            if not pairs:
                continue
            best = pairs[0]
            nonstop = str(best.get("stops")) == "0"
            drop = D.wow_change(history.get(mid), today)
            score = (
                W_DEAL * (cell.get("deal_pct") or 0)
                + (W_NONSTOP if nonstop else 0.0)
                + W_LEAD * _lead_score(today, best.get("depart_date"))
                + W_DROP * (-drop["pct"] if drop and drop["pct"] < 0 else 0.0)
            )
            out.append({
                "monitor_id": mid,
                "window_id": w["id"],
                "window_label": w["label"],
                "dest": route["destination"],
                "dest_ko": D.dest_label(mid, routes_by_id),
                "country": route.get("country", ""),
                "flag": route.get("flag", ""),
                "min_price": cell["min_price"],
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

    # 같은 목적지가 여러 연휴에 걸려도 가장 점수 높은 하나만 남긴다
    best_by_dest = {}
    for c in sorted(out, key=lambda c: -c["score"]):
        best_by_dest.setdefault(c["dest"], c)
    ranked = sorted(best_by_dest.values(), key=lambda c: -c["score"])
    return ranked[:limit]


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


def material(inputs, monitor_id, window_id, today=None, root=None):
    """글 한 편에 필요한 사실을 전부 모은다. 여기 없는 숫자는 글에 쓰지 않는다."""
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
    best = pairs[0]
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
        "alternatives": pairs[1:6],
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
