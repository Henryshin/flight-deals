"""블로그 글감용 데이터 로딩 + 신뢰성 가드레일.

대시보드가 이미 만들어 둔 docs/data/*.json 을 읽어, **글에 써도 되는 값만**
걸러서 넘긴다. 표준 라이브러리만 사용한다 (build_dashboard_data.py 와 같은 방침).

왜 가드레일이 여기 있는가
-------------------------
크롤러(collector/google_flights_crawler.py)의 PRICE_PATTERN 은 ``₩([0-9][0-9,]{2,})``
라서, 페이지가 덜 렌더된 순간에 ``₩333`` 같은 값을 진짜 가격으로 잡는다. 실제로
data/prices.csv 에 5만원 미만 행이 15개 들어와 있고, 그 결과 docs/data/matrix.json
에 "밀라노 왕복 755원 (tier A, 할증률 99.9%)" 같은 셀이 만들어져 있다.

저장소 어디에도 하한선 검사가 없으므로, 이 값을 그대로 믿고 글을 쓰면 첫 포스팅부터
틀린 가격이 나간다. 상류(크롤러/빌더) 수정과 별개로 블로그 파이프라인은 자체
방어선을 가진다.
"""
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS_DATA = ROOT / "docs" / "data"

KST = timezone(timedelta(hours=9))

# --- 가드레일 상수 -----------------------------------------------------------

# ICN 출발 국제선 왕복의 절대 하한. 이보다 싼 값은 통화/렌더 아티팩트다.
PRICE_FLOOR_KRW = 50_000
# min_price / typical 의 하한. 실제 최고 수준 특가인 ICN-NRT 추석 셀이
# 291,300 / 615,582 = 0.47 이므로, 0.25 는 진짜 특가를 죽이지 않으면서
# 아티팩트(755/1,293,800 = 0.0006)는 확실히 잡는다.
MIN_PLAUSIBLE_RATIO = 0.25
MAX_PLAUSIBLE_DEAL_PCT = 75.0

# 데이터가 이보다 오래되면 글을 쓰지 않는다. 수집은 4시간 주기이므로 여유 있는 값.
MAX_DATA_AGE_HOURS = 30

# 셀 신뢰도 하한
MIN_CELL_OBS = 6
MIN_CELL_PAIRS = 2
# 시계열 비교에 필요한 최소 관측 수 (build_dashboard_data.MIN_HISTORY_POINTS 와 동일)
MIN_HISTORY_POINTS = 3

# 출발이 코앞인 일정은 글로 쓸 실익이 없다.
MIN_LEAD_DAYS = 7


class StaleDataError(Exception):
    """docs/data 가 너무 오래돼서 글을 쓸 수 없음."""


class MissingDataError(Exception):
    """입력 파일이 없거나 깨졌음 (진짜 고장)."""


# --- 로딩 -------------------------------------------------------------------

_DESTMETA_RE = re.compile(
    r"d\('([A-Z]{3})',\s*'([^']+)',\s*\[([^\]]*)\],\s*\[([^\]]*)\],\s*'([dmw]+)'\)"
)


def parse_destmeta(js_path=None):
    """docs/destmeta.js 를 파싱해 {IATA: {...}} 로 돌려준다.

    JS 파일이라 정규식으로 읽는다. tests/test_smoke.py 의
    test_destmeta_covers_all_destinations 가 쓰는 것과 같은 형태의 패턴이며,
    두 파서가 어긋나지 않도록 대조 테스트를 둔다.
    """
    path = Path(js_path) if js_path else ROOT / "docs" / "destmeta.js"
    text = path.read_text(encoding="utf-8")
    out = {}
    for m in _DESTMETA_RE.finditer(text):
        iata, concepts, temps, hums, rain = m.groups()
        out[iata] = {
            "concepts": concepts.split("+"),
            "t": [int(x) for x in temps.replace(" ", "").split(",") if x],
            "h": [int(x) for x in hums.replace(" ", "").split(",") if x],
            "r": rain,
        }
    return out


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise MissingDataError(f"필수 입력 파일이 없다: {path}") from e
    except json.JSONDecodeError as e:
        raise MissingDataError(f"JSON 파싱 실패: {path} ({e})") from e


def load_inputs(docs_data=None, root=None):
    """블로그 글감에 필요한 모든 입력을 한 번에 읽는다."""
    dd = Path(docs_data) if docs_data else DOCS_DATA
    rt = Path(root) if root else ROOT
    routes = _read_json(rt / "data" / "routes.json")
    return {
        "matrix": _read_json(dd / "matrix.json"),
        "routes_status": _read_json(dd / "routes_status.json"),
        "history": _read_json(dd / "history.json"),
        "holidays": _read_json(dd / "holidays.json"),
        "meta": _read_json(dd / "meta.json"),
        "routes": routes,
        "routes_by_id": {monitor_id(r): r for r in routes},
        "destmeta": parse_destmeta(rt / "docs" / "destmeta.js"),
    }


def monitor_id(route):
    """모니터 식별자. scripts/build_dashboard_data.py 의 동명 함수와 같은 규칙."""
    return route.get("id") or f"{route['origin']}-{route['destination']}"


# --- 신선도 -----------------------------------------------------------------

def data_age_hours(meta, now=None):
    now = now or datetime.now(timezone.utc)
    gen = datetime.fromisoformat(meta["generated_at"])
    if gen.tzinfo is None:
        gen = gen.replace(tzinfo=timezone.utc)
    return (now - gen).total_seconds() / 3600.0


def assert_fresh(meta, now=None, max_hours=MAX_DATA_AGE_HOURS):
    age = data_age_hours(meta, now)
    if age > max_hours:
        raise StaleDataError(
            f"docs/data 가 {age:.1f}시간 전 데이터다 (허용 {max_hours}시간). "
            "수집 워크플로를 먼저 확인할 것."
        )
    return age


# --- 가격 타당성 -------------------------------------------------------------

def is_plausible_price(price, typical=None):
    """이 가격을 글에 써도 되는가.

    >>> is_plausible_price(291300, typical=615582)   # 실제 추석 특가
    True
    >>> is_plausible_price(755, typical=1293800)     # 통화 아티팩트
    False
    """
    if not isinstance(price, (int, float)) or price <= 0:
        return False
    if price < PRICE_FLOOR_KRW:
        return False
    if typical and typical > 0 and price / typical < MIN_PLAUSIBLE_RATIO:
        return False
    return True


def is_usable_cell(cell, today=None, min_lead_days=MIN_LEAD_DAYS):
    """matrix 셀 하나를 글감으로 써도 되는가."""
    if not cell:
        return False
    if not is_plausible_price(cell.get("min_price"), cell.get("typical")):
        return False
    if (cell.get("n_obs") or 0) < MIN_CELL_OBS:
        return False
    if (cell.get("n_pairs") or 0) < MIN_CELL_PAIRS:
        return False
    deal = cell.get("deal_pct")
    if deal is not None and deal > MAX_PLAUSIBLE_DEAL_PCT:
        return False
    return bool(usable_pairs(cell, today=today, min_lead_days=min_lead_days))


def usable_pairs(cell, today=None, min_lead_days=MIN_LEAD_DAYS):
    """셀 안에서 실제로 글에 쓸 수 있는 일정만 남긴다."""
    today = today or datetime.now(KST).date()
    cutoff = today + timedelta(days=min_lead_days)
    typical = cell.get("typical")
    out = []
    for p in cell.get("pairs") or []:
        if not is_plausible_price(p.get("price"), typical):
            continue
        try:
            dep = date.fromisoformat(p["depart_date"])
        except (KeyError, ValueError):
            continue
        if dep < cutoff:
            continue
        out.append(p)
    out.sort(key=lambda p: p["price"])
    return out


def usable_cells(matrix, window_id, today=None):
    """한 연휴 윈도우에서 쓸 만한 (monitor_id, cell) 목록을 싼 순으로."""
    cells = (matrix.get("cells") or {}).get(window_id) or {}
    out = [(mid, c) for mid, c in cells.items() if is_usable_cell(c, today=today)]
    out.sort(key=lambda t: t[1]["min_price"])
    return out


def upcoming_windows(matrix, today=None, limit=None):
    """아직 안 지난 연휴 윈도우만, 가까운 순으로."""
    today = today or datetime.now(KST).date()
    out = []
    for w in matrix.get("windows") or []:
        try:
            end = date.fromisoformat(w["end"])
        except (KeyError, ValueError):
            continue
        if end >= today:
            out.append(w)
    out.sort(key=lambda w: w["start"])
    return out[:limit] if limit else out


def window_by_id(matrix, window_id):
    for w in matrix.get("windows") or []:
        if w.get("id") == window_id:
            return w
    return None


# --- 시계열 -----------------------------------------------------------------

def clean_history(series):
    """history.json 의 한 노선 시계열에서 아티팩트 포인트를 제거."""
    return [
        p for p in (series or [])
        if is_plausible_price(p.get("min")) and p.get("t")
    ]


def wow_change(series, today=None):
    """최근 7일 최저가 vs 그 이전 7일 최저가.

    pairs[].prev_price 를 쓰면 안 된다 — build_dashboard_data.load_prices() 가
    하루 단위로 dedup 하므로 그건 '전날' 값이지 '전주' 값이 아니다.
    """
    today = today or datetime.now(KST).date()
    pts = clean_history(series)
    recent, prior = [], []
    for p in pts:
        try:
            d = date.fromisoformat(p["t"])
        except ValueError:
            continue
        age = (today - d).days
        if 0 <= age <= 6:
            recent.append(p["min"])
        elif 7 <= age <= 13:
            prior.append(p["min"])
    if len(recent) < MIN_HISTORY_POINTS or len(prior) < MIN_HISTORY_POINTS:
        return None
    now_min, was_min = min(recent), min(prior)
    if was_min <= 0:
        return None
    return {
        "now": now_min,
        "prev": was_min,
        "diff": now_min - was_min,
        "pct": round((now_min - was_min) / was_min * 100, 1),
        "n_recent": len(recent),
        "n_prior": len(prior),
    }


# --- 기후 -------------------------------------------------------------------

_RAIN_LABEL = {"d": "건기", "m": "간절기", "w": "우기"}


def climate_for(iata, month, destmeta):
    """그 도시의 그 달 기후. 감성 문장도 이 값을 벗어나면 안 된다."""
    meta = (destmeta or {}).get(iata)
    if not meta:
        return None
    i = month - 1
    if not (0 <= i < 12) or len(meta["t"]) < 12 or len(meta["r"]) < 12:
        return None
    return {
        "temp": meta["t"][i],
        "humidity": meta["h"][i] if len(meta["h"]) >= 12 else None,
        "rain_code": meta["r"][i],
        "rain": _RAIN_LABEL.get(meta["r"][i], ""),
        "concepts": meta["concepts"],
    }


# --- 표기 -------------------------------------------------------------------

def krw(n):
    """291300 -> '291,300원'"""
    return f"{int(n):,}원"


def krw_man(n):
    """291300 -> '29만원대'  (블로그 제목·요약에 쓰는 표기)"""
    man = int(n) // 10_000
    return f"{man}만원대"


def kst_str(iso_utc):
    """'2026-09-07T01:32:53+00:00' -> '2026년 9월 7일 10:32' (KST)"""
    dt = datetime.fromisoformat(iso_utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    k = dt.astimezone(KST)
    return f"{k.year}년 {k.month}월 {k.day}일 {k:%H:%M}"


def dest_label(mid, routes_by_id):
    """'ICN-KMI' -> '미야자키' (label 의 화살표 뒷부분)"""
    r = routes_by_id.get(mid)
    if not r:
        return mid
    label = r.get("label") or mid
    return label.split("→")[-1].strip() if "→" in label else label
