"""
GitHub Actions에서 실행: 등록된 노선 x (연휴 날짜 후보 + 평시 기준가 후보)를 크롤링해
data/prices.csv에 append. 로컬 SQLite 대신 git에 커밋되는 CSV가 곧 가격 이력 DB 역할을 함.

운영 원칙:
- stale-first: 한 번도 수집 안 된 노선부터, 그 다음 가장 오래된 노선 순으로 돈다.
- 시간 예산(TIME_BUDGET_MIN)을 넘기면 새 쿼리를 시작하지 않는다. 잘린 노선은
  다음 런에서 stale-first 순서에 의해 자동으로 앞에 온다.
- 노선(o,d) 그룹 하나가 끝날 때마다 CSV에 바로 append -> 중간에 죽어도 부분 보존.
- 노선별 수집 상태/실패 사유를 data/collect_status.json 에 기록해 대시보드가
  '빈 칸'의 이유(차단/결과 없음/파싱 실패/대기)를 보여줄 수 있게 한다.
"""
import csv
import json
import os
import sys
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from collector.google_flights_crawler import CrawlerSessionError, PriceCrawlerSession
from holidays import date_range_candidates, get_holiday_windows

ROOT = Path(__file__).parent.parent
ROUTES_FILE = ROOT / "data" / "routes.json"
PRICES_FILE = ROOT / "data" / "prices.csv"
STATUS_FILE = ROOT / "data" / "collect_status.json"
DEFAULT_TRIP_LENGTH_DAYS = 3

# 노선당 연휴 날짜쌍 후보 상한. 윈도우 간 라운드로빈으로 뽑으므로 상한에 걸려도
# 모든 연휴가 최소 몇 개씩은 커버된다. 18 = 연휴 6개에 ~3쌍씩 -> 통상 시세(중앙값)를
# 잡을 표본이 하루 만에 tier B, 이튿날 tier A 로 차오른다. (45 모니터 x ~18 = 런당 ~810 로드)
MAX_PAIRS_PER_ROUTE = int(os.environ.get("MAX_PAIRS_PER_ROUTE", "18"))
# 이 시간(분)을 넘기면 새 쿼리를 시작하지 않음. 4시간 크론에 맞춘 기본값.
TIME_BUDGET_MIN = float(os.environ.get("TIME_BUDGET_MIN", "170"))
# 평시(비연휴) 기준가 후보는 하루 1회만 수집 (연휴 가성비 지표의 분모).
BASELINE_REFRESH_HOURS = 20
# 연속 이 횟수만큼 차단/동의 페이지가 나오면 런을 조기 중단 (예산 낭비 방지).
BLOCKED_ABORT_STREAK = 5

NEW_HEADER = [
    "origin", "destination", "depart_date", "return_date", "price",
    "is_holiday_window", "collected_at", "dep_time", "arr_time", "stops",
    "window_id",
]
# 과거 스키마들: 7열(초기) -> 10열(dep/arr/stops 추가) -> 11열(window_id 추가)
LEGACY_HEADERS = [NEW_HEADER[:7], NEW_HEADER[:10]]


def build_date_candidates(min_nights=DEFAULT_TRIP_LENGTH_DAYS, max_pairs=None, today=None):
    """해당 노선의 (출발, 귀국, 연휴여부, 연휴id) 후보 생성.

    - 연휴 구간이 min_nights 이상이면: 구간 안에서 min_nights..구간길이 박수 전부.
    - 연휴 구간이 min_nights 보다 짧으면(장거리 노선 등): 연휴 전체를 '포함'하는
      min_nights..min_nights+2 박 일정을 생성. (기존엔 '구간 안에 들어가는' 일정만
      만들어 min_nights=6 노선은 후보 0개 -> 영영 수집되지 않는 버그가 있었다.
      연차를 붙여 연휴를 늘려 쓰는 실제 사용 패턴과도 이 쪽이 맞다.)
    - 윈도우 간 라운드로빈으로 max_pairs 개까지만 (가까운 연휴 우선, 짧은 일정 우선).
    """
    if max_pairs is None:
        max_pairs = MAX_PAIRS_PER_ROUTE
    today = today or date.today()
    per_window = []
    for w in sorted(get_holiday_windows(), key=lambda w: w["start"]):
        window_len = (w["end"] - w["start"]).days
        pairs = []
        if window_len >= min_nights:
            for length in range(min_nights, window_len + 1):
                for depart, return_ in date_range_candidates(w, length):
                    if depart > today:
                        pairs.append((depart, return_))
        else:
            for length in range(min_nights, min_nights + 3):
                cur = w["end"] - timedelta(days=length)  # 귀국일이 구간 끝 이후가 되도록
                while cur <= w["start"]:                 # 출발일이 구간 시작 이전이 되도록
                    if cur > today:
                        pairs.append((cur, cur + timedelta(days=length)))
                    cur += timedelta(days=1)
        if pairs:
            per_window.append((w["id"], pairs))

    candidates, seen = [], set()
    idx = 0
    while len(candidates) < max_pairs:
        progressed = False
        for window_id, pairs in per_window:
            if idx >= len(pairs):
                continue
            progressed = True
            depart, return_ = pairs[idx]
            if (depart, return_) in seen:
                continue
            seen.add((depart, return_))
            candidates.append((depart, return_, True, window_id))
            if len(candidates) >= max_pairs:
                break
        if not progressed:
            break
        idx += 1
    return candidates


def build_baseline_candidates(min_nights=DEFAULT_TRIP_LENGTH_DAYS, today=None):
    """평시(비연휴) 기준가용 날짜쌍 2개: 약 4주/9주 뒤 화요일 출발, min_nights 박.

    연휴 가성비(할증률) 지표의 분모가 되는 '평범한 주' 가격. 연휴 윈도우와
    ±3일 이내로 겹치면 한 주씩 뒤로 민다.
    """
    today = today or date.today()
    windows = get_holiday_windows()

    def near_holiday(d1, d2):
        for w in windows:
            if d1 <= w["end"] + timedelta(days=3) and d2 >= w["start"] - timedelta(days=3):
                return True
        return False

    out = []
    for weeks in (4, 9):
        base = today + timedelta(weeks=weeks)
        depart = base + timedelta(days=(1 - base.weekday()) % 7)  # 다음 화요일
        for _ in range(8):  # 최대 8주 밀며 연휴 회피
            return_ = depart + timedelta(days=min_nights)
            if not near_holiday(depart, return_):
                if (depart, return_) not in out:
                    out.append((depart, return_))
                break
            depart += timedelta(days=7)
    return out


def migrate_prices_file():
    """구(7열/10열) prices.csv를 신(11열) 헤더로 1회 재작성하고 기존 행을 빈 열로 패딩.

    이미 신 헤더면 아무 것도 하지 않음. 파일이 없으면 아무 것도 하지 않음.
    """
    if not PRICES_FILE.exists() or PRICES_FILE.stat().st_size == 0:
        return
    with open(PRICES_FILE, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if not rows:
        return
    header = rows[0]
    if header == NEW_HEADER:
        return
    if header not in LEGACY_HEADERS:
        return  # 알 수 없는 헤더는 건드리지 않음
    pad = len(NEW_HEADER) - len(header)
    with open(PRICES_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(NEW_HEADER)
        for row in rows[1:]:
            writer.writerow(row + [""] * pad)


def append_rows(rows):
    """노선 그룹 하나 분량을 즉시 CSV에 append (중간 크래시에도 부분 보존)."""
    if not rows:
        return
    write_header = not PRICES_FILE.exists() or PRICES_FILE.stat().st_size == 0
    with open(PRICES_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(NEW_HEADER)
        writer.writerows(rows)


def load_collection_meta():
    """prices.csv 1회 스캔: (o,d)별 마지막 수집시각 / 마지막 평시(baseline) 수집시각.

    stale-first 정렬과 '평시 기준가는 하루 1회만' 게이트에 쓴다.
    """
    last_any, last_baseline = {}, {}
    if not PRICES_FILE.exists():
        return last_any, last_baseline
    with open(PRICES_FILE, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (row.get("origin"), row.get("destination"))
            ts = row.get("collected_at") or ""
            if ts > last_any.get(key, ""):
                last_any[key] = ts
            if row.get("is_holiday_window") == "0" and ts > last_baseline.get(key, ""):
                last_baseline[key] = ts
    return last_any, last_baseline


def load_status_attempts():
    """collect_status.json 에서 (o,d)별 마지막 '시도' 시각.

    prices.csv 는 성공한 수집만 기록하므로, 계속 실패하는 노선(결과 없음/차단 등)이
    stale-first 정렬에서 매 런 최우선 순위를 영구 점유하지 않도록 시도 시각도 반영한다.
    """
    if not STATUS_FILE.exists():
        return {}
    try:
        data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    routes = data.get("routes")
    if not isinstance(routes, dict):
        return {}
    out = {}
    for group_key, entry in routes.items():
        if isinstance(entry, dict) and entry.get("last_attempt_at"):
            out[group_key] = entry["last_attempt_at"]
    return out


def hours_since(ts):
    """ISO 시각 문자열(날짜만도 허용) 이후 경과 시간. 파싱 불가면 None."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600


def fetch_result_with_retry(session, *args, **kwargs):
    """typed 수집 결과를 반환. 세션이 죽으면 한 번 재시작해 재시도하고,
    그래도 죽으면 error 결과 반환."""
    try:
        return session.fetch_result(*args, **kwargs)
    except CrawlerSessionError as e:
        print(f"  브라우저 세션 오류({e}) -> 세션 재시작 후 재시도")
        session.restart()
        try:
            return session.fetch_result(*args, **kwargs)
        except CrawlerSessionError as e2:
            print(f"  세션 재시작 후에도 실패({e2}) -> 이번 쿼리 건너뜀")
            return {"status": "error", "by_stops": {}, "detail": f"세션 재시작 실패: {e2}"}


def group_routes(routes):
    """route 엔트리들을 (origin,destination)별로 묶어 크롤 스펙을 만든다.

    같은 (o,d)는 여러 모니터(stops 정책/박수)로 등록될 수 있으나 crawl은 한 번만 한다.
    - nights_variants: 그룹 내 '고유' min_nights 오름차순 목록. 최소값 하나만 쓰면
      연휴보다 긴 박수(예: 7박) 모니터의 후보가 아예 생성되지 않아 그 모니터가
      영구 미수집되므로, 값별로 후보를 만들어 합친다.
    - max_stops : 그룹 내 가장 넓은 정책. 하나라도 None이면 None(=전체),
                  아니면 int들의 최대값.
    반환: [(origin, destination, origin_city, dest_city, nights_variants, max_stops), ...]
    """
    groups = {}
    order = []
    for route in routes:
        key = (route["origin"], route["destination"])
        if key not in groups:
            groups[key] = {
                "origin": route["origin"],
                "destination": route["destination"],
                "origin_city": route.get("origin_city"),
                "dest_city": route.get("destination_city"),
                "nights_variants": {route.get("min_nights", DEFAULT_TRIP_LENGTH_DAYS)},
                "max_stops": route.get("max_stops"),
                "max_stops_any_none": route.get("max_stops") is None,
            }
            order.append(key)
        else:
            g = groups[key]
            g["nights_variants"].add(route.get("min_nights", DEFAULT_TRIP_LENGTH_DAYS))
            ms = route.get("max_stops")
            if ms is None:
                g["max_stops_any_none"] = True
            elif not g["max_stops_any_none"]:
                g["max_stops"] = ms if g["max_stops"] is None else max(g["max_stops"], ms)

    specs = []
    for key in order:
        g = groups[key]
        max_stops = None if g["max_stops_any_none"] else g["max_stops"]
        specs.append((
            g["origin"], g["destination"], g["origin_city"], g["dest_city"],
            sorted(g["nights_variants"]), max_stops,
        ))
    return specs


def filter_routes_only(routes, only):
    """COLLECT_ONLY="ICN-DPS" 처럼 특정 노선(origin-destination)만 남긴다.

    신규 노선 등록 시 그 노선만 즉시 1회 수집하기 위한 경로. 빈 값이면 전체.
    형식이 맞지 않거나 매칭이 없으면 전체를 그대로 반환(안전).
    """
    only = (only or "").strip()
    if not only:
        return routes
    parts = only.split("-")
    if len(parts) != 2:
        print(f"COLLECT_ONLY 형식 오류('{only}') -> 전체 수집")
        return routes
    o, d = parts[0].strip().upper(), parts[1].strip().upper()
    subset = [r for r in routes if r["origin"] == o and r["destination"] == d]
    if not subset:
        print(f"COLLECT_ONLY={only} 매칭 노선 없음 -> 전체 수집")
        return routes
    print(f"COLLECT_ONLY={only} -> {len(subset)}개 모니터만 수집")
    return subset


def write_collect_status(updates):
    """노선별 수집 상태를 data/collect_status.json 에 병합 기록.

    파일 전체를 다시 쓰되 이번 런에서 다루지 않은 노선의 기존 상태는 유지한다
    (COLLECT_ONLY 타겟 런이 다른 노선 상태를 지우지 않도록).
    """
    data = {}
    if STATUS_FILE.exists():
        try:
            data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            data = {}
    routes_map = data.get("routes") if isinstance(data.get("routes"), dict) else {}
    routes_map.update(updates)
    out = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "routes": routes_map,
    }
    STATUS_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def summarize_group(statuses: Counter, n_rows: int, last_detail: str, now_iso: str):
    """노선 그룹 하나의 쿼리 결과들을 상태 엔트리 하나로 요약."""
    attempts = sum(statuses.values())
    if n_rows > 0:
        status, detail = "ok", ""
    elif attempts == 0:
        status, detail = "error", "시도된 쿼리 없음"
    elif statuses.get("ok"):
        # 쿼리는 성공했지만 저장할 행이 없음 (조건 내 항공편 없음)
        status, detail = "no_flights", last_detail or "조건에 맞는 왕복 결과 없음"
    else:
        failures = Counter({k: v for k, v in statuses.items() if k != "ok"})
        status = failures.most_common(1)[0][0]
        detail = last_detail
    return {
        "status": status,
        "detail": (detail or "")[:300],
        "attempts": attempts,
        "ok": statuses.get("ok", 0),
        "rows": n_rows,
        "last_attempt_at": now_iso,
    }


def write_step_summary(status_updates, skipped):
    """GitHub Actions 실행 요약(GITHUB_STEP_SUMMARY)에 노선별 결과 표를 남긴다."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = ["## 수집 결과", "", "| 노선 | 상태 | 성공/시도 | 저장 행 |", "|---|---|---|---|"]
    for key, s in sorted(status_updates.items()):
        lines.append(f"| {key} | {s['status']} | {s.get('ok', 0)}/{s.get('attempts', 0)} | {s.get('rows', 0)} |")
    if skipped:
        lines += ["", f"예산/차단으로 건너뜀: {', '.join(sorted(skipped))}"]
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass


def main():
    routes = json.loads(ROUTES_FILE.read_text(encoding="utf-8"))
    routes = filter_routes_only(routes, os.environ.get("COLLECT_ONLY"))

    # 같은 (origin,destination)를 여러 모니터가 공유하므로 crawl은 (o,d)당 한 번만.
    specs = group_routes(routes)

    # stale-first: 한 번도 시도 안 된 노선 먼저, 그 다음 오래된 순.
    # (예산에 잘린 노선이 다음 런에서 자동으로 앞에 오는 로테이션 효과.
    #  '시도' 시각도 반영해 계속 실패하는 노선이 앞자리를 영구 점유하지 않게 한다.)
    last_any, last_baseline = load_collection_meta()
    attempts = load_status_attempts()
    specs.sort(key=lambda s: max(
        last_any.get((s[0], s[1]), ""),
        attempts.get(f"{s[0]}-{s[1]}", ""),
    ))

    # 구 헤더 파일이면 먼저 신 헤더로 마이그레이션해 csv.DictReader 일관성 유지.
    migrate_prices_file()

    deadline = time.monotonic() + TIME_BUDGET_MIN * 60
    status_updates = {}
    skipped = set()
    total_rows = 0
    blocked_streak = 0
    aborted_blocked = False

    # 실행 전체가 브라우저 하나를 재사용 (쿼리마다 크로미움 기동 비용을 내지 않음).
    with PriceCrawlerSession() as session:
        for origin, destination, origin_city, dest_city, nights_variants, max_stops in specs:
            group_key = f"{origin}-{destination}"
            now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
            if aborted_blocked:
                skipped.add(group_key)
                status_updates[group_key] = {
                    "status": "skipped_blocked", "detail": "연속 차단 감지로 런 조기 중단",
                    "attempts": 0, "ok": 0, "rows": 0, "last_attempt_at": now_iso,
                }
                continue
            if time.monotonic() > deadline:
                skipped.add(group_key)
                status_updates[group_key] = {
                    "status": "skipped_budget", "detail": "시간 예산 초과로 이번 런에서 건너뜀",
                    "attempts": 0, "ok": 0, "rows": 0, "last_attempt_at": now_iso,
                }
                continue

            # 그룹 내 '고유 min_nights'별 후보의 합집합 (같은 날짜쌍은 한 번만).
            candidates = []
            seen_pairs = set()
            for mn in nights_variants:
                for cand in build_date_candidates(mn):
                    pair = (cand[0], cand[1])
                    if pair not in seen_pairs:
                        seen_pairs.add(pair)
                        candidates.append(cand)
            # 평시 기준가는 하루 1회만 (연휴 가성비 지표의 분모). 박수 변형별로 수집해
            # 각 모니터가 자기 박수(±1)의 기준가를 갖게 한다.
            h = hours_since(last_baseline.get((origin, destination), ""))
            if h is None or h >= BASELINE_REFRESH_HOURS:
                for mn in nights_variants:
                    for d, r in build_baseline_candidates(mn):
                        if (d, r) not in seen_pairs:
                            seen_pairs.add((d, r))
                            candidates.append((d, r, False, ""))

            rows = []
            statuses = Counter()
            last_detail = ""
            try:
                for depart, return_, is_holiday, window_id in candidates:
                    if time.monotonic() > deadline:
                        last_detail = "시간 예산 초과로 노선 일부만 수집"
                        break
                    result = fetch_result_with_retry(
                        session,
                        origin, destination, depart, return_,
                        origin_city=origin_city, dest_city=dest_city,
                        max_stops=max_stops,
                    )
                    st = result["status"]
                    statuses[st] += 1
                    if st in ("blocked", "consent"):
                        blocked_streak += 1
                        if blocked_streak >= BLOCKED_ABORT_STREAK:
                            aborted_blocked = True
                            last_detail = result.get("detail") or "연속 차단 감지"
                            print(f"연속 {BLOCKED_ABORT_STREAK}회 차단 감지 -> 런 조기 중단")
                            break
                    else:
                        blocked_streak = 0
                    if st != "ok":
                        last_detail = result.get("detail") or st
                        print(f"  {origin}->{destination} {depart}~{return_}: {st} ({last_detail})")

                    by_stops = result["by_stops"]
                    if not by_stops:
                        continue
                    collected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    prices_txt = ", ".join(f"{s}경유 {it['price']}원" for s, it in sorted(by_stops.items()))
                    print(f"  {origin}->{destination} {depart}~{return_}: {prices_txt}")
                    # 경유수 클래스별 최저가를 각각 한 행씩 저장 (직항/경유 모니터가 각자 필터).
                    for _stops, it in sorted(by_stops.items()):
                        rows.append([
                            origin, destination,
                            depart.isoformat(), return_.isoformat(),
                            it["price"], int(is_holiday), collected_at,
                            it.get("dep_time", ""), it.get("arr_time", ""),
                            it.get("stops", ""),
                            window_id,
                        ])
            except Exception as e:  # noqa: BLE001 - 한 노선의 예기치 못한 크래시(세션 재기동
                # 실패 등)가 남은 노선 수집과 상태 기록 전체를 유실시키지 않도록 격리.
                statuses["error"] += 1
                last_detail = f"{type(e).__name__}: {e}"
                print(f"  {group_key}: 예기치 못한 오류로 노선 건너뜀 ({last_detail})")

            # 노선 그룹 단위로 즉시 저장 -> 런이 중간에 죽어도 여기까지는 보존.
            append_rows(rows)
            total_rows += len(rows)
            status_updates[group_key] = summarize_group(
                statuses, len(rows), last_detail,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )

    write_collect_status(status_updates)
    write_step_summary(status_updates, skipped)

    if skipped:
        print(f"예산/차단으로 건너뜀: {', '.join(sorted(skipped))}")
    print(f"{total_rows}건 저장 완료")


if __name__ == "__main__":
    main()
