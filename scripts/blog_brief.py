"""오늘 쓸 만한 글감 브리핑을 만든다.

이 스크립트는 **글을 쓰지 않는다.** 사실만 정리해서 내놓는다.
글은 Claude Code 세션에서 사람이 명령할 때 그때그때 쓴다.

    python scripts/blog_brief.py                       # 오늘의 소재 후보
    python scripts/blog_brief.py --material ICN-KMI    # 한 소재의 상세 자료
    python scripts/blog_brief.py --material ICN-KMI --window 2026-09-24
    python scripts/blog_brief.py --date 2026-09-07 --stdout

기본 실행은 result/_brief/YYYY-MM-DD.md (저장소 밖 excel-flights/result) 에 브리핑을 쓴다 (GitHub Actions 용).
"""
import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from blog import brief as B
from blog import data as D
from blog import store as S

EXIT_OK = 0
EXIT_BROKEN = 1
EXIT_NO_TOPIC = 0  # 소재가 없는 건 고장이 아니다 (워크플로를 빨갛게 만들지 않는다)


def _pct(v):
    return f"{v:.0f}%" if isinstance(v, (int, float)) else "-"


# 이 안쪽이면 '코앞 연휴'. 리드타임이 짧으면 싼 조합이 이미 빠져 있어서
# 후보가 약해진다 — 그럴 때 먼 연휴를 같이 보라고 표를 둘로 나눈다.
NEAR_DAYS = 60
PER_TABLE = 6


def _rows(cands, start_no=1):
    L = [
        "| # | 노선 | 연휴 | 추천 일정 | 최저가 일정 | 같은일정 2위 대비 | 주간변동 | 점수 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, c in enumerate(cands, start_no):
        wow = c["wow"]
        wow_txt = "-"
        if wow:
            arrow = "▼" if wow["pct"] < 0 else ("▲" if wow["pct"] > 0 else "―")
            wow_txt = f"{arrow} {abs(wow['pct']):.0f}%"

        # 점수는 **순서만** 매긴다. 어느 일정으로 쓸지는 사람이 고른다
        # (사용자 지정 2026-09-08). 그래서 추천안과 최저가안을 나란히 보여준다.
        def _opt(price, nights, leave, stops, check=None):
            sched = f"{nights}박 {nights + 1}일" if isinstance(nights, int) else "-"
            lv = f"연차 {leave}일" if isinstance(leave, int) else "연차 -"
            # 직항/경유를 안 적으면 '더 싸고 더 짧은데 왜 추천이 아니지'로 읽힌다.
            # 직항이면 오는 편까지 확인됐는지도 적는다 (D.nonstop_check, 2026-09-22).
            way = check or ("직항" if str(stops) == "0" else "경유")
            return f"{D.krw(price)} · {sched} · **{lv}** · {way}"

        rec = _opt(c.get("price") or c["min_price"], c.get("nights"),
                   c.get("leave_days"), c.get("stops"), c.get("check"))
        alt = c.get("cheapest")
        alt_txt = "―"
        if alt and alt.get("price") != c.get("price"):
            alt_txt = _opt(alt.get("price"), alt.get("nights"),
                           alt.get("leave_days"), alt.get("stops"), alt.get("check"))
        L.append(
            f"| {i} | {c['flag']} {c['dest_ko']} (`{c['monitor_id']}`) "
            f"| {c['window_label']}<br><sub>D-{c['lead_days']}</sub> | {rec}<br>"
            f"<sub>{c['depart_date']}~{c['return_date']}</sub> "
            f"| {alt_txt} | {(_pct(c['deal_pct_pair']) + ' 싸다') if c.get('deal_pct_pair') else '통계 없음'} | {wow_txt} | {c['score']} |"
        )
    return L


def _top(cands, keep):
    """목적지 중복만 걷어내고 상위 N. 표 안에서만 걸러 다른 표를 가리지 않는다."""
    seen, out = set(), []
    for c in sorted(cands, key=lambda c: -c["score"]):
        if c["dest"] in seen:
            continue
        seen.add(c["dest"])
        out.append(c)
        if len(out) >= keep:
            break
    return out


def render_brief(inputs, cands, today, age_hours, per_table=PER_TABLE):
    near = _top([c for c in cands if c["lead_days"] <= NEAR_DAYS], per_table)
    far = _top([c for c in cands if c["lead_days"] > NEAR_DAYS], per_table)
    L = [
        f"# 블로그 소재 브리핑 — {today.isoformat()}",
        "",
        f"- 데이터 기준: {D.kst_str(inputs['meta']['generated_at'])} "
        f"({age_hours:.1f}시간 전)",
        "- **노선과 일정 둘 다 사람이 고른다.** 점수는 순서만 매긴 것이고,",
        "  '추천 일정'은 연차 부담까지 본 제안일 뿐이다. 최저가 일정으로 갈지는 그때 정한다.",
        "- 여기 실린 가격은 전부 신뢰 필터(5만원 하한·관측수·리드타임)를 통과한 값이다.",
        "",
        f"## 코앞 연휴 (D-{NEAR_DAYS} 이내)",
        "",
    ]
    if near:
        L += _rows(near)
        L += ["", "> 리드타임이 짧으면 싼 조합이 이미 빠져 있는 경우가 많다.",
              "> 마땅한 게 없으면 아래 여유 있는 연휴에서 고르는 편이 낫다.", ""]
    else:
        L += ["_해당 없음._", ""]

    L += [f"## 여유 있는 연휴 (D-{NEAR_DAYS}~{B.MAX_LEAD_DAYS})", "",
          f"> D-{B.MAX_LEAD_DAYS} 보다 먼 연휴는 뺐다. 검색 수요가 여행 두어 달 전에 생겨서,",
          "> 지금 쓰면 그때까지 아무도 안 찾는다.", ""]
    L += _rows(far, start_no=len(near) + 1) if far else ["_해당 없음._"]

    first = (near or far or [None])[0]
    L += [
        "",
        "## 다음 단계",
        "",
        "```",
        f"python scripts/blog_brief.py --material {first['monitor_id']} "
        f"--window {first['window_id']}" if first else "(후보 없음)",
        "```",
        "",
        "상세 자료를 읽고 `.claude/skills/blog-post/SKILL.md` 의 문체 규칙에 맞춰",
        "초안을 쓴 뒤 `python scripts/blog_save.py <초안.md>` 로 저장한다.",
        "",
    ]
    return chr(10).join(L)


def render_material(mat):
    c, best = mat["cell"], mat["best"]
    w, hist, cl = mat["window"], mat["history"], mat["climate"]
    dep = date.fromisoformat(best["depart_date"])

    L = [
        f"# 글감 자료: {mat['flag']} {mat['label']} × {w['label']}",
        "",
        "> 아래 숫자만 글에 쓴다. 여기 없는 값은 지어내지 않는다.",
        "",
        "## 핵심",
        "",
        f"- **왕복 최저가: {D.krw(c['min_price'])}** ({D.krw_man(c['min_price'])})",
        # 할인율은 같은 일정 2위(다른 항공사 최저 직항) 대비만 쓴다 (사용자 정의 2026-09-14).
        # 연휴 전체 통상 시세는 다른 날짜가 섞인 참고값이라 '낮다'를 붙이지 않는다.
        (f"- **할인율(같은 일정 직항 2위 {best.get('runnerup_airline') or '항공사 미상'} "
         f"{D.krw(best['runnerup_price'])} 대비): {best['same_itin_deal_pct']:.0f}%**"
         if best.get("same_itin_deal_pct") is not None else
         "- 할인율: **통계 없음** — 같은 일정 직항 목록에서 최저가와 다른 항공사 중 가장 싼 편을 "
         "구글 항공권에서 직접 확인해 머리말 runnerup_price / runnerup_airline 으로 넣기 전에는 "
         "'특가'·'N% 싸다'를 쓰지 않는다"),
        f"- (참고) 이 연휴 전체 관측가 중앙값: {D.krw(c['typical']) if c['typical'] else '-'} — 다른 날짜·박수가 섞인 값이라 할인 근거로 쓰지 않는다",
        f"- 평시(비연휴) 기준가: "
        f"{D.krw(c['offpeak_baseline']) if c['offpeak_baseline'] else '-'}",
        f"- 관측 수: {c['n_obs']}회 · 등급 {c['tier']}",
        "",
        "## 항공편 (예시 포스팅의 '노선' 박스에 해당)",
        "",
        f"- 가는 편: {best['depart_date']}({best.get('depart_weekday','')}) "
        f"{best.get('dep_time','')} 출발 → {best.get('arr_time','')} 도착",
        f"- 오는 편: {best['return_date']}({best.get('return_weekday','')})"
        + (f" {best.get('ret_dep_time')} 출발 → {best.get('ret_arr_time')} 도착"
           f" · {best.get('ret_airline') or '항공사 미상'}" if best.get("ret_dep_time") else " — 시각 미수집"),
        f"- 공항: 가는 편 {best.get('airports') or '미수집'} · 오는 편 {best.get('ret_airports') or '미수집'}",
        f"- 일정: {best.get('nights')}박 {best.get('days')}일 · "
        f"{D.nonstop_check(best) if str(best.get('stops')) == '0' else '경유 ' + str(best.get('stops')) + '회'}",
        f"- 항공사: {best.get('airline') or '미상'}",
        f"- 휴가 {best.get('leave_days')}일 사용 · 여행 중 쉬는 날 "
        f"{best.get('bonus_days')}일",
        f"- 예약 링크: {mat['booking_url']}",
        "",
        f"## 연휴: {w['label']} ({w['start']} ~ {w['end']})",
        "",
        f"- 공휴일: {', '.join(w.get('holiday_dates') or []) or '-'}",
        "",
    ]

    if mat["alternatives"]:
        L += ["## 다른 일정 후보", "",
              "| 출발 | 귀국 | 박수 | 휴가 | 직항 | 항공사 | 가격 |",
              "|---|---|---|---|---|---|---|"]
        for p in mat["alternatives"]:
            L.append(
                f"| {p['depart_date']}({p.get('depart_weekday','')}) "
                f"| {p['return_date']}({p.get('return_weekday','')}) "
                f"| {p.get('nights')}박 | {p.get('leave_days')}일 "
                f"| {'O' if str(p.get('stops')) == '0' else 'X'} "
                f"| {p.get('airline') or '-'} | {D.krw(p['price'])} |"
            )
        L.append("")

    if mat["airlines"]:
        L += ["## 항공사별 가격 (같은 일정 기준 — 비교 표에 그대로 쓸 것)", "",
              "| 항공사 | 운항 | 시각 | 왕복 |", "|---|---|---|---|"]
        for r in mat["airlines"]:
            stops = "직항" if str(r["stops"]) == "0" else f"경유 {r['stops']}회"
            L.append(
                f"| {r['airline'] or '미상'} | {stops} "
                f"| {r['dep_time']} → {r['arr_time']} | {D.krw(r['price'])} |"
            )
        L.append("")
    else:
        L += ["## 항공사별 가격", "",
              "- 같은 일정에서 수집된 항공사가 하나뿐이라 **비교 표는 넣지 않는다.**",
              ""]

    L += ["## 가격 추이", ""]
    if hist["min_30d"]:
        L.append(f"- 최근 30일 최저: {D.krw(hist['min_30d'])} · "
                 f"평균: {D.krw(hist['avg_30d']) if hist['avg_30d'] else '-'}")
    wow = hist["wow"]
    if wow:
        direction = "내렸다" if wow["pct"] < 0 else ("올랐다" if wow["pct"] > 0 else "그대로다")
        L.append(f"- 최근 7일 최저({D.krw(wow['now'])}) vs 그 전 7일"
                 f"({D.krw(wow['prev'])}): **{abs(wow['pct']):.0f}% {direction}**")
    else:
        L.append("- 주간 비교는 관측이 부족해 **'내렸다/올랐다'를 쓰지 않는다.**")
    L.append("")

    if cl:
        L += [
            f"## {dep.month}월의 {mat['dest_ko']} (여행 파트 근거)", "",
            f"- 평균 최고기온 {cl['temp']}℃ · 습도 {cl['humidity']}% · "
            f"**{cl['rain']}**",
            f"- 여행 성격: {' + '.join(cl['concepts'])}",
            "- 기후 문장은 이 값을 벗어나면 안 된다 "
            "(우기인 달에 '건조하다'고 쓰지 않는다).",
            "",
        ]
    else:
        L += ["## 기후", "",
              "- destmeta.js 에 이 목적지가 없다. **날씨 이야기는 쓰지 않는다.**", ""]

    L += [
        "## 고지",
        "",
        f"- 데이터 기준 시각: {D.kst_str(mat['data_generated_at'])}",
        f"- 대시보드: {mat['dashboard_url']}",
        "- 구글 항공권 수집값이며 실시간이 아니다. 글에 반드시 기준 시각과",
        "  '실제 결제가와 다를 수 있다'는 고지를 넣는다.",
        "",
    ]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description="블로그 소재 브리핑")
    ap.add_argument("--date", help="기준 날짜 YYYY-MM-DD (기본: 오늘 KST)")
    ap.add_argument("--material", help="상세 자료를 볼 노선 ID (예: ICN-KMI)")
    ap.add_argument("--window", help="--material 과 함께 쓸 연휴 ID")
    ap.add_argument("--limit", type=int, default=6, help="표 하나당 후보 개수")
    ap.add_argument("--stdout", action="store_true", help="파일로 쓰지 않는다")
    ap.add_argument("--json", action="store_true", help="원시 JSON 출력")
    args = ap.parse_args(argv)

    today = (date.fromisoformat(args.date) if args.date
             else datetime.now(D.KST).date())

    try:
        inputs = D.load_inputs()
    except D.MissingDataError as e:
        print(f"::error::{e}", file=sys.stderr)
        return EXIT_BROKEN

    try:
        age = D.assert_fresh(inputs["meta"])
    except D.StaleDataError as e:
        print(f"::warning::{e}", file=sys.stderr)
        return EXIT_NO_TOPIC

    if args.material:
        wid = args.window
        if not wid:
            cs = [c for c in B.candidates(inputs, today, limit=200)
                  if c["monitor_id"] == args.material]
            if not cs:
                print(f"::warning::{args.material}: 지금 쓸 만한 연휴 후보가 없다",
                      file=sys.stderr)
                return EXIT_NO_TOPIC
            wid = cs[0]["window_id"]
        try:
            mat = B.material(inputs, args.material, wid, today)
        except (KeyError, ValueError) as e:
            print(f"::warning::{e}", file=sys.stderr)
            return EXIT_NO_TOPIC
        print(json.dumps(mat, ensure_ascii=False, indent=1) if args.json
              else render_material(mat))
        return EXIT_OK

    ledger = S.load_ledger()
    # 표를 코앞/여유로 나눠 보여주므로 여기서 자르거나 중복을 걷지 않는다.
    # 목적지 중복은 표 안에서 걸러야 같은 목적지의 먼 연휴 선택지가 안 가려진다.
    cands = B.candidates(
        inputs, today, exclude_dests=S.recent_dests(ledger, today),
        limit=None, dedupe=False,
    )
    if not cands:
        print("::warning::쓸 만한 소재가 없다 (관측 부족 또는 최근 중복)",
              file=sys.stderr)
        return EXIT_NO_TOPIC

    if args.json:
        print(json.dumps(cands, ensure_ascii=False, indent=1))
        return EXIT_OK

    md = render_brief(inputs, cands, today, age, per_table=args.limit)
    print(md)
    if not args.stdout:
        out = S.BRIEF_DIR / f"{today.isoformat()}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        print(f"\n-> {out}", file=sys.stderr)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            Path(summary).write_text(md, encoding="utf-8")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
