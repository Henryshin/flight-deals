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
"""


def _e(x):
    return html.escape(str(x))


def _page(inner):
    return (f"<!doctype html><meta charset='utf-8'><style>{_BASE_CSS}</style>"
            f"<div id='card'>{inner}</div>")


# --- 카드 1: 가격 요약 (히어로 숫자) ----------------------------------------

def price_card_html(mat):
    cell, best = mat["cell"], mat["best"]
    w = mat["window"]
    deal = cell.get("deal_pct")
    typical = cell.get("typical")

    badge = ""
    if deal is not None and typical:
        badge = (f"<div class='badge'>▼ 평소 {D.krw(typical)}보다 "
                 f"{deal:.0f}% 낮아요</div>")

    stops = "직항" if str(best.get("stops")) == "0" else "경유"
    nights = best.get("nights")
    trip = f"{nights}박 {nights + 1}일" if isinstance(nights, int) else ""
    leave = best.get("leave_days")
    bonus = best.get("bonus_days")
    leave_txt = f"휴가 {leave}일" if isinstance(leave, int) else "-"
    if isinstance(bonus, int) and bonus:
        leave_txt += f" · 여행 중 쉬는 날 {bonus}일"

    rows = [
        ("가는 날", f"{best['depart_date']} ({best.get('depart_weekday','')})"),
        ("오는 날", f"{best['return_date']} ({best.get('return_weekday','')})"),
        ("일정", f"{trip} · {stops}" if trip else stops),
        ("항공사", best.get("airline") or "미상"),
        ("시각", f"{best.get('dep_time','')} 출발 → {best.get('arr_time','')} 도착"),
        ("휴가 계산", leave_txt),
    ]
    grid = "".join(
        f"<div><div class='k'>{_e(k)}</div><div class='v'>{_e(v)}</div></div>"
        for k, v in rows
    )
    asof = D.kst_str(mat["data_generated_at"])
    return _page(
        f"<div class='eyebrow'>{_e(mat['flag'])} {_e(w['label'])} "
        f"{_e(w['start'])}~{_e(w['end'])}</div>"
        f"<div class='route'>{_e(mat['label'])}</div>"
        f"<div class='hero'>{D.krw(cell['min_price'])[:-1]}<small>원</small></div>"
        f"{badge}<div class='rule'></div><div class='grid'>{grid}</div>"
        f"<div class='foot'>왕복 최저가 · {_e(asof)} 기준 · "
        f"구글 항공권 수집값이라 실제 결제가와 다를 수 있어요</div>"
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
        ("price", price_card_html(mat)),
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
