"""가격 카드 PNG 렌더링.

matplotlib 없이, 이미 의존성인 Playwright 로 HTML 을 스크린샷한다.
스파크라인은 파이썬에서 좌표를 계산한 인라인 SVG 라 차트 라이브러리도 필요 없다.

색
--
단일 시리즈이므로 범례가 없고(제목이 시리즈를 가리킨다), 추이선은 시퀀셜 기본
색상인 블루 한 가지만 쓴다. 등락 배지는 상태색인데, 적록 대비는 색맹에서 구분이
안 되므로 **항상 화살표 글리프 + 한글 라벨과 함께** 나간다 (색만으로 의미를
전달하지 않는다). 두 상태색이 한 카드에 동시에 등장하는 경우는 없다.
"""
import datetime as _dt
import html
import json
import os
from datetime import date

from blog import data as D

CARD_WIDTH_PX = 860
DEVICE_SCALE = 2

# 검증된 라이트 팔레트 (dataviz 레퍼런스 인스턴스)
SURFACE = "#fcfcfb"
BORDER = "#e8e7e3"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
TEXT_MUTED = "#8a8880"
SERIES = "#2a78d6"        # 시퀀셜 기본 hue
SERIES_SOFT = "#cde2fb"   # 같은 램프의 옅은 단계 (영역 채움)
STATUS_GOOD = "#0ca30c"
STATUS_BAD = "#d03b3b"

# 엑셀 룩(대시보드 excel-flights.xyz 와 같은 값). 일정 시트 카드에만 쓴다.
XL_GRID = "#d0d7de"
XL_SHEET = "#ffffff"
XL_TEXT = "#1f2328"
XL_MUTED = "#6e7781"
XL_FONT = "Calibri,'Malgun Gothic','Noto Sans KR',Arial,sans-serif"
XL_MONO = "Consolas,'D2Coding',monospace"

FONT_STACK = (
    "'Noto Sans KR','Noto Sans CJK KR','Apple SD Gothic Neo',"
    "'Malgun Gothic',system-ui,sans-serif"
)

_BASE_CSS = f"""
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#fff;font-family:{FONT_STACK};-webkit-font-smoothing:antialiased}}
#card{{width:{CARD_WIDTH_PX}px;background:{SURFACE};border:1px solid {BORDER};
  border-radius:16px;padding:34px 38px;color:{TEXT_PRIMARY}}}
.eyebrow{{font-size:15px;color:{TEXT_SECONDARY};letter-spacing:.01em}}
.route{{font-size:27px;font-weight:700;margin-top:6px;letter-spacing:-.01em}}
.hero{{font-size:60px;font-weight:700;line-height:1.05;margin-top:20px;
  letter-spacing:-.02em}}
.hero small{{font-size:26px;font-weight:600;margin-left:4px}}
.badge{{display:inline-block;margin-top:14px;padding:7px 14px;border-radius:999px;
  font-size:16px;font-weight:600;background:#eaf6ea;color:{STATUS_GOOD}}}
.badge.up{{background:#fbeaea;color:{STATUS_BAD}}}
.rule{{height:1px;background:{BORDER};margin:26px 0 22px}}
.grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:16px 34px}}
.k{{font-size:14px;color:{TEXT_MUTED};margin-bottom:3px}}
.v{{font-size:19px;font-weight:600}}
.foot{{margin-top:24px;font-size:13px;color:{TEXT_MUTED};line-height:1.6}}
table{{width:100%;border-collapse:collapse;margin-top:6px}}
th,td{{padding:13px 12px;text-align:left;font-size:17px;
  border-bottom:1px solid {BORDER}}}
th{{font-size:14px;color:{TEXT_MUTED};font-weight:600}}
td.num{{font-variant-numeric:tabular-nums;font-weight:600;text-align:right}}
th.num{{text-align:right}}
tr:last-child td{{border-bottom:none}}
.best td{{color:{SERIES}}}
.title{{font-size:20px;font-weight:700}}
.sub{{font-size:14px;color:{TEXT_MUTED};margin-top:4px}}
.axis{{font-size:13px;color:{TEXT_MUTED};font-variant-numeric:tabular-nums}}
/* 일정 시트: 어느 날이 공휴일·주말·연차인지 한눈에.
   대시보드(excel-flights.xyz)와 같은 스프레드시트 룩을 쓴다 — 같은 브랜드로 읽히고,
   '표로 따져본 결과'라는 이 블로그의 성격에도 맞는다. */
.xl{{margin-top:20px;font-family:{XL_FONT};color:{XL_TEXT}}}
table.xl-sheet{{width:100%;border-collapse:collapse;table-layout:fixed}}
table.xl-sheet th,table.xl-sheet td{{border:1px solid {XL_GRID};text-align:center;
  padding:7px 2px;font-size:15px;overflow:hidden;white-space:nowrap}}
.xl-rowhead{{width:56px;background:#eaf3ee;color:#14532d;font-weight:700;
  font-size:14px}}
td.xl-day{{font-weight:700;font-variant-numeric:tabular-nums;font-size:17px;
  padding:10px 2px !important}}
td.xl-day .dw{{font-weight:600;font-size:14px;opacity:.7;margin-left:5px}}
td.xl-kind{{font-weight:700;font-size:15px;padding:9px 2px !important}}
.xl-legend{{display:flex;gap:20px;margin-top:16px;font-size:15px;
  color:{TEXT_SECONDARY};flex-wrap:wrap;align-items:center}}
.xl-legend .lg{{display:flex;align-items:center;gap:7px}}
.xl-legend i{{width:14px;height:14px;display:inline-block;border:1px solid {XL_GRID}}}
"""


def _e(x):
    return html.escape(str(x))


def _page(inner):
    return (f"<!doctype html><meta charset='utf-8'><style>{_BASE_CSS}</style>"
            f"<div id='card'>{inner}</div>")


# --- 카드 1: 가격 요약 (히어로 숫자) ----------------------------------------

def _airlines(best):
    """가는편/오는편 항공사. 왕복이므로 둘 다 적는다 (사용자 지정 2026-09-09).

    최저가는 두 편의 항공사가 다른 '다구간 항공권'인 경우가 흔해서, 한쪽만 적으면
    사실과 달라진다. 같으면 한 번만 쓴다.
    """
    go = (best.get("airline") or "미상").strip()
    back = (best.get("ret_airline") or "").strip()
    if not back:
        return f"{go}/미상"
    return go if go == back else f"{go}/{back}"


def price_card_html(mat):
    cell, best = mat["cell"], mat["best"]
    w = mat["window"]
    typical = cell.get("typical")
    # 히어로 숫자는 **주인공 일정의 값**이다. 셀 최저가를 쓰면 연차 부담을 보고
    # 다른 일정을 고른 경우(brief.pick_pair) 본문·일정카드와 어긋난다.
    hero_price = best.get("price") or cell["min_price"]
    # 배지는 **같은 일정 2위(다른 항공사 최저 직항) 대비**만 보여준다 (사용자 정의 2026-09-14).
    # 연휴 전체 중앙값(typical) 대비는 다른 날짜 가격이 섞여 할인이라고 부를 수 없고,
    # 나머지 직항 중앙값 대비(9/13)는 숫자가 너무 커 보인다고 반려됐다.
    ru = best.get("runnerup_price")
    ru_airline = (best.get("runnerup_airline") or "").strip()
    badge = ""
    if ru and hero_price and str(best.get("stops")) == "0":
        deal = (ru - hero_price) / ru * 100.0
        if deal >= 0.5:
            who = f"{ru_airline} " if ru_airline else ""
            badge = (f"<div class='badge'>▼ 같은 날짜 직항 2위 {who}"
                     f"{D.krw(ru)}보다 {deal:.0f}% 낮아요</div>")

    stops = "직항" if str(best.get("stops")) == "0" else "경유"
    nights = best.get("nights")
    trip = f"{nights}박 {nights + 1}일" if isinstance(nights, int) else ""
    leave = best.get("leave_days")
    bonus = best.get("bonus_days")
    leave_txt = f"휴가 {leave}일" if isinstance(leave, int) else "-"
    if isinstance(bonus, int) and bonus:
        leave_txt += f" · 여행 중 쉬는 날 {bonus}일"

    # 날짜와 시각을 한 줄로 합친다. 예전에는 '시각' 행 하나에 **가는 편만** 넣어서
    # 오는 편이 몇 시인지 카드만 봐서는 알 수 없었다 (사용자 지적 2026-09-09).
    def leg(date_, dow, dep, arr):
        line = f"{date_} ({dow})" if dow else str(date_)
        if dep and arr:
            line += f" {dep} → {arr}"
        return line

    # 일정·휴가 계산은 위 스트립과 범례가 이미 말해 주므로 행에서 뺀다
    # (카드 두 장을 합치면서 중복 제거, 2026-09-09).
    rows = [
        ("가는 편", leg(best["depart_date"], best.get("depart_weekday", ""),
                      best.get("dep_time"), best.get("arr_time"))),
        ("오는 편", leg(best["return_date"], best.get("return_weekday", ""),
                      best.get("ret_dep_time"), best.get("ret_arr_time"))),
        ("항공사", _airlines(best)),
    ]
    grid = "".join(
        f"<div><div class='k'>{_e(k)}</div><div class='v'>{_e(v)}</div></div>"
        for k, v in rows
    )
    asof = D.kst_str(mat["data_generated_at"])
    # 머리말 price 로 실조회값을 넣었으면 수집 시각이 아니라 **조회 시각**이 기준이다.
    # 안 고치면 카드가 "10:35 수집값"이라고 말하면서 오후에 확인한 가격을 보여준다.
    checked = best.get("price_checked_at") or ""
    # 일정 스트립(구 schedule 카드)을 여기 얹는다. 두 장이던 것을 한 장으로 합쳤고
    # **공휴일 박스를 먼저** 보여 준다 (사용자 지정 2026-09-09).
    strip = ""
    try:
        strip = _strip_html(_dt.date.fromisoformat(best["depart_date"]),
                            _dt.date.fromisoformat(best["return_date"]))
    except (KeyError, TypeError, ValueError):
        pass
    head = f"{_e(mat['flag'])} {_e(w['label'])} {_e(w['start'])}~{_e(w['end'])}"
    if trip:
        head += f" · {trip} · {stops}"
    return _page(
        f"<div class='eyebrow'>{head}</div>"
        f"<div class='route'>{_e(mat['label'])}</div>"
        f"<div class='hero'>{D.krw(hero_price)[:-1]}<small>원</small></div>"
        f"{badge}{strip}"
        f"<div class='rule'></div><div class='grid'>{grid}</div>"
        f"<div class='foot'>왕복 최저가 · {_e(checked or asof)} 기준 · "
        f"{'구글 항공권에서 직접 조회한 값' if checked else '구글 항공권 수집값'}"
        f"이라 실제 결제가와 다를 수 있어요</div>"
    )


# --- 카드 2: 30일 추이 스파크라인 -------------------------------------------

def _sparkline_svg(points, width=784, height=150, pad=14):
    """단일 시리즈 라인 + 마지막 점 강조. 좌표는 파이썬에서 계산한다."""
    vals = [p["min"] for p in points]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    n = len(points)
    inner_w = width - pad * 2
    inner_h = height - pad * 2

    def xy(i, v):
        x = pad + (inner_w * i / (n - 1) if n > 1 else inner_w / 2)
        y = pad + inner_h - (v - lo) / span * inner_h
        return round(x, 1), round(y, 1)

    coords = [xy(i, v) for i, v in enumerate(vals)]
    line = " ".join(f"{x},{y}" for x, y in coords)
    area = (f"{coords[0][0]},{height - pad} " + line +
            f" {coords[-1][0]},{height - pad}")
    lx, ly = coords[-1]
    # 최저점도 표시 — 독자가 실제로 궁금해하는 지점
    mi = vals.index(lo)
    mx, my = coords[mi]

    return (
        f"<svg width='{width}' height='{height}' viewBox='0 0 {width} {height}' "
        f"role='img' aria-label='최근 최저가 추이'>"
        f"<polygon points='{area}' fill='{SERIES_SOFT}' opacity='.55'/>"
        f"<polyline points='{line}' fill='none' stroke='{SERIES}' "
        f"stroke-width='2' stroke-linejoin='round' stroke-linecap='round'/>"
        f"<circle cx='{mx}' cy='{my}' r='5' fill='{SURFACE}' stroke='{SERIES}' "
        f"stroke-width='2'/>"
        f"<circle cx='{lx}' cy='{ly}' r='5.5' fill='{SERIES}' stroke='{SURFACE}' "
        f"stroke-width='2'/>"
        f"</svg>"
    )


def trend_card_html(mat, series):
    """series: history.json 의 한 노선 시계열 (clean_history 통과분).

    배지는 본문이 인용하는 것과 **같은 지표**(최근 7일 최저 vs 그 전 7일 최저)를
    쓴다. 카드가 '처음 대비 끝'을 따로 계산하면 같은 글 안에 서로 다른 하락률이
    두 개 실려서 독자가 어느 쪽을 믿어야 할지 모르게 된다.
    """
    pts = series[-30:]
    if len(pts) < D.MIN_HISTORY_POINTS:
        return None
    vals = [p["min"] for p in pts]
    lowest, highest, last = min(vals), max(vals), vals[-1]

    wow = (mat.get("history") or {}).get("wow")
    if not wow:
        badge = ("<div class='badge' style='background:#f0efec;color:" +
                 TEXT_SECONDARY + "'>주간 비교는 관측이 부족해요</div>")
    elif wow["pct"] < 0:
        badge = (f"<div class='badge'>▼ 지난주보다 {D.krw(abs(wow['diff']))} "
                 f"내림 ({abs(wow['pct']):.0f}%)</div>")
    elif wow["pct"] > 0:
        badge = (f"<div class='badge up'>▲ 지난주보다 {D.krw(wow['diff'])} "
                 f"오름 ({wow['pct']:.0f}%)</div>")
    else:
        badge = "<div class='badge'>― 지난주와 같아요</div>"

    return _page(
        f"<div class='title'>{_e(mat['dest_ko'])} 왕복 최저가, 최근 "
        f"{len(pts)}일</div>"
        f"<div class='sub'>{_e(pts[0]['t'])} ~ {_e(pts[-1]['t'])} · "
        f"하루 한 점 · 그날 수집한 모든 일정 중 최저가</div>"
        f"{badge}{_sparkline_svg(pts)}"
        f"<div class='grid' style='grid-template-columns:repeat(3,1fr)'>"
        f"<div><div class='k'>기간 최저</div>"
        f"<div class='v'>{D.krw(lowest)}</div></div>"
        f"<div><div class='k'>기간 최고</div>"
        f"<div class='v'>{D.krw(highest)}</div></div>"
        f"<div><div class='k'>가장 최근</div>"
        f"<div class='v'>{D.krw(last)}</div></div></div>"
    )


# --- 카드 3: 항공사별 비교 ---------------------------------------------------

def airlines_card_html(mat):
    rows = mat.get("airlines") or []
    if len(rows) < 2:
        return None
    best = mat["best"]
    head = (f"<tr><th>항공사</th><th>운항</th><th>시각</th>"
            f"<th class='num'>왕복</th></tr>")
    body = []
    for i, r in enumerate(rows):
        stops = "직항" if str(r["stops"]) == "0" else f"경유 {r['stops']}회"
        cls = " class='best'" if i == 0 else ""
        body.append(
            f"<tr{cls}><td>{_e(r['airline'] or '미상')}</td><td>{_e(stops)}</td>"
            f"<td>{_e(r['dep_time'])} → {_e(r['arr_time'])}</td>"
            f"<td class='num'>{D.krw(r['price'])}</td></tr>"
        )
    return _page(
        f"<div class='title'>{_e(mat['dest_ko'])} 항공사별 가격</div>"
        f"<div class='sub'>{_e(best['depart_date'])} ~ {_e(best['return_date'])} "
        f"같은 일정 기준</div>"
        f"<table><thead>{head}</thead><tbody>{''.join(body)}</tbody></table>"
        f"<div class='foot'>{_e(D.kst_str(mat['data_generated_at']))} 기준 "
        f"수집값이에요</div>"
    )


# --- 카드 4: 일정 스트립 (어느 날이 공휴일·주말·연차인지) ---------------------

# 대시보드(docs/index.html 의 tstrip)와 같은 색 언어를 쓴다. 거기서는 수백 행에
# 끼워 넣느라 9px 정사각이었지만, 블로그는 카드 한 장이 통째로 쓰이므로 날짜와
# 라벨을 넣을 만큼 키운다.
DAY_KINDS = {
    "hol":   ("공휴일", "#e5484d", "#fdecea", "#b3261e"),
    "wknd":  ("주말",   "#6aa3e0", "#eaf1fb", "#0b5cab"),
    "leave": ("연차",   "#b6bcc2", "#f3f4f5", "#52514e"),
}
_DOW = ["월", "화", "수", "목", "금", "토", "일"]


def _strip_html(dep, ret):
    """일정 스트립 **한 행**. 칸마다 날짜+요일이 들어가고 색이 종류를 말한다.

    예전에는 '날짜'와 '구분' 두 행이었는데 표가 무겁다는 지적을 받아 한 행으로 합쳤다
    (2026-09-09). 종류는 색으로만 구분하고 아래 범례가 뜻을 설명한다.
    """
    days = _trip_days(dep, ret)
    count = {k: sum(1 for _, kk in days if kk == k) for k in DAY_KINDS}
    cells = []
    for d, kind in days:
        _label, solid, fill, ink = DAY_KINDS[kind]
        # 날짜는 온전히 쓴다 (사용자 지정 2026-09-09). 앞 칸에만 월을 붙이면
        # 스크롤하다 중간부터 본 사람은 몇 월인지 알 수 없다.
        num = f"{d.month}/{d.day}"
        cells.append(
            f"<td class='xl-day' style='background:{fill};color:{ink};"
            f"border-bottom:3px solid {solid}'>{_e(num)}"
            f"<span class='dw'>{_DOW[d.weekday()]}</span></td>")
    legend = "".join(
        f"<span class='lg'><i style='background:{DAY_KINDS[k][1]}'></i>"
        f"{DAY_KINDS[k][0]} {count[k]}일</span>"
        for k in ("hol", "wknd", "leave") if count[k])
    return (f"<div class='xl'><table class='xl-sheet'><tbody><tr>"
            f"{''.join(cells)}</tr></tbody></table></div>"
            f"<div class='xl-legend'>{legend}</div>")


def _trip_days(depart, ret):
    """[(date, kind)] — 공휴일이 주말보다 우선한다 (개천절이 토요일인 해가 있다)."""
    import holidays as HD
    hol = HD._all_holidays()
    out, d = [], depart
    while d <= ret and len(out) < 40:
        if d in hol:
            kind = "hol"
        elif d.weekday() >= 5:
            kind = "wknd"
        else:
            kind = "leave"
        out.append((d, kind))
        d += _dt.timedelta(days=1)
    return out


def schedule_card_html(mat):
    """일정 시트 — 어느 날이 공휴일·주말·연차인지 한 칸씩 보여준다.

    대시보드의 tstrip(9px 정사각)과 같은 판정을 쓰되, 블로그는 카드 한 장이
    통째로 쓰이므로 날짜·요일·구분을 다 적는다. 룩은 대시보드와 같은 스프레드시트
    형태로 맞췄다 (사용자 지정 2026-09-08).
    """
    best = mat["best"]
    try:
        dep = _dt.date.fromisoformat(best["depart_date"])
        ret = _dt.date.fromisoformat(best["return_date"])
    except (KeyError, TypeError, ValueError):
        return None
    if ret < dep:
        return None

    days = _trip_days(dep, ret)
    n = len(days)
    count = {k: sum(1 for _, kk in days if kk == k) for k in DAY_KINDS}

    # 칸이 좁아지면 '구분' 글자가 넘친다. 그때는 한 글자로 줄인다.
    short = n > 10
    row_day, row_kind = [], []
    for d, kind in days:
        label, solid, fill, ink = DAY_KINDS[kind]
        if short:
            label = label[0]                    # 공/주/연
        # 달이 바뀌면 날짜만으로는 헷갈린다. 첫 칸과 매월 1일에 월을 붙인다.
        num = f"{d.month}/{d.day}" if (d == dep or d.day == 1) else str(d.day)
        row_day.append(
            f"<td class='xl-day' style='background:{fill};color:{ink}'>{_e(num)}"
            f"<span class='dw'>{_DOW[d.weekday()]}</span></td>")
        row_kind.append(
            f"<td class='xl-kind' style='background:{solid};color:#fff'>{label}</td>")

    legend = "".join(
        f"<span class='lg'><i style='background:{DAY_KINDS[k][1]}'></i>"
        f"{DAY_KINDS[k][0]} {count[k]}일</span>"
        for k in ("hol", "wknd", "leave") if count[k]
    )
    w = mat["window"]
    nights = best.get("nights")
    trip = f"{nights}박 {nights + 1}일" if isinstance(nights, int) else f"{n}일"
    return _page(
        f"<div class='eyebrow'>{_e(mat['flag'])} {_e(w['label'])} · {_e(trip)}</div>"
        f"<div class='route'>{dep.month}월 {dep.day}일({_DOW[dep.weekday()]})"
        f" → {ret.month}월 {ret.day}일({_DOW[ret.weekday()]})</div>"
        f"<div class='xl'>"
        f"<table class='xl-sheet'><tbody>"
        f"<tr><th class='xl-rowhead'>날짜</th>{''.join(row_day)}</tr>"
        f"<tr><th class='xl-rowhead'>구분</th>{''.join(row_kind)}</tr>"
        f"</tbody></table></div>"
        f"<div class='xl-legend'>{legend}</div>"
        f"<div class='foot'>회색 칸만 연차를 쓰면 돼요. "
        f"빨강은 공휴일, 파랑은 주말이라 원래 쉬는 날이에요</div>"
    )


# --- 렌더링 ------------------------------------------------------------------

def _launch_kwargs():
    """CI 는 playwright install 로 맞는 빌드를 받지만, 크로미움이 미리 깔린
    환경에서는 BLOG_CHROMIUM_PATH 로 실행 파일을 직접 지정할 수 있다."""
    path = os.environ.get("BLOG_CHROMIUM_PATH")
    return {"executable_path": path} if path else {}


def render_html_to_png(html_list, scale=DEVICE_SCALE):
    """HTML 문자열들을 PNG bytes 로. 전부 성공한 뒤에야 호출자가 파일을 쓴다."""
    from playwright.sync_api import sync_playwright

    out = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(**_launch_kwargs())
        try:
            ctx = browser.new_context(
                viewport={"width": CARD_WIDTH_PX + 40, "height": 600},
                device_scale_factor=scale, locale="ko-KR",
            )
            page = ctx.new_page()
            for doc in html_list:
                page.set_content(doc, wait_until="load")
                page.wait_for_timeout(120)  # 웹폰트 반영 여유
                out.append(page.locator("#card").screenshot())
            ctx.close()
        finally:
            browser.close()
    return out


def build_cards(mat, history_series):
    """(이름, HTML) 목록. None 인 카드는 자동으로 빠진다."""
    specs = [
        ("price", price_card_html(mat)),   # 일정 스트립이 이 안에 들어간다
        ("trend", trend_card_html(mat, D.clean_history(history_series))),
        ("airlines", airlines_card_html(mat)),
    ]
    return [(n, h) for n, h in specs if h]


def selftest():
    """한글 폰트가 없으면 카드가 조용히 □ 로 렌더된다. 시끄럽게 실패시킨다."""
    from playwright.sync_api import sync_playwright

    probe = _page("<div class='route'>한글 폰트 확인 미야자키</div>")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(**_launch_kwargs())
        try:
            page = browser.new_page()
            page.set_content(probe, wait_until="load")
            ok = page.evaluate(
                "() => document.fonts.check('700 27px \"Noto Sans KR\"')"
                " || document.fonts.check('700 27px \"Noto Sans CJK KR\"')"
            )
            width = page.locator(".route").evaluate("el => el.scrollWidth")
        finally:
            browser.close()
    if not ok:
        print("::error::한글 폰트(Noto Sans KR/CJK KR)가 없다. "
              "카드가 □ 로 렌더된다. apt-get install -y fonts-noto-cjk", flush=True)
        return 1
    print(f"폰트 OK (샘플 폭 {width}px)")
    return 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    print(json.dumps({"usage": "python -m blog.imagecard --selftest"}))
