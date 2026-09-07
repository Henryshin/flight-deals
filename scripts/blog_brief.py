"""오늘 쓸 만한 글감 브리핑을 만든다.

이 스크립트는 **글을 쓰지 않는다.** 사실만 정리해서 내놓는다.
글은 Claude Code 세션에서 사람이 명령할 때 그때그때 쓴다.

    python scripts/blog_brief.py                       # 오늘의 소재 후보
    python scripts/blog_brief.py --material ICN-KMI    # 한 소재의 상세 자료
    python scripts/blog_brief.py --material ICN-KMI --window 2026-09-24
    python scripts/blog_brief.py --date 2026-09-07 --stdout

기본 실행은 posts/_brief/YYYY-MM-DD.md 에 브리핑을 쓴다 (GitHub Actions 용).
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


def render_brief(inputs, cands, today, age_hours):
    L = [
        f"# 블로그 소재 브리핑 — {today.isoformat()}",
        "",
        f"- 데이터 기준: {D.kst_str(inputs['meta']['generated_at'])} "
        f"({age_hours:.1f}시간 전)",
        f"- 후보 {len(cands)}건. 아래 중 하나를 골라 세션에서 "
        "`/blog-post <노선ID>` 로 글을 쓴다.",
        "- 여기 실린 가격은 전부 신뢰 필터(5만원 하한·관측수·리드타임)를 통과한 값이다.",
        "",
        "| # | 노선 | 연휴 | 최저가 | 평시대비 | 직항 | 일정 | 주간변동 | 점수 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for i, c in enumerate(cands, 1):
        wow = c["wow"]
        wow_txt = "-"
        if wow:
            arrow = "▼" if wow["pct"] < 0 else ("▲" if wow["pct"] > 0 else "―")
            wow_txt = f"{arrow} {abs(wow['pct']):.0f}%"
        L.append(
            f"| {i} | {c['flag']} {c['dest_ko']} (`{c['monitor_id']}`) "
            f"| {c['window_label']} | {D.krw(c['min_price'])} "
            f"| {_pct(c['deal_pct'])} 싸다 | {'직항' if c['nonstop'] else '경유'} "
            f"| {c['depart_date']}~{c['return_date']} | {wow_txt} | {c['score']} |"
        )
    L += [
        "",
        "## 다음 단계",
        "",
        "```",
        f"python scripts/blog_brief.py --material {cands[0]['monitor_id']} "
        f"--window {cands[0]['window_id']}",
        "```",
        "",
        "상세 자료를 읽고 `.claude/skills/blog-post/SKILL.md` 의 문체 규칙에 맞춰",
        "초안을 쓴 뒤 `python scripts/blog_save.py <초안.md>` 로 저장한다.",
        "",
    ]
    return "\n".join(L)


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
        f"- 이 연휴의 통상 시세: {D.krw(c['typical']) if c['typical'] else '-'}"
        + (f" → **{c['deal_pct']:.0f}% 낮다**" if c["deal_pct"] is not None else ""),
        f"- 평시(비연휴) 기준가: "
        f"{D.krw(c['offpeak_baseline']) if c['offpeak_baseline'] else '-'}",
        f"- 관측 수: {c['n_obs']}회 · 등급 {c['tier']}",
        "",
        "## 항공편 (예시 포스팅의 '노선' 박스에 해당)",
        "",
        f"- 가는 편: {best['depart_date']}({best.get('depart_weekday','')}) "
        f"{best.get('dep_time','')} 출발 → {best.get('arr_time','')} 도착",
        f"- 오는 편: {best['return_date']}({best.get('return_weekday','')})",
        f"- 일정: {best.get('nights')}박 {best.get('days')}일 · "
        f"{'직항' if str(best.get('stops')) == '0' else '경유 ' + str(best.get('stops')) + '회'}",
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
    ap.add_argument("--limit", type=int, default=8, help="후보 개수")
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
    cands = B.candidates(
        inputs, today, exclude_dests=S.recent_dests(ledger, today), limit=args.limit
    )
    if not cands:
        print("::warning::쓸 만한 소재가 없다 (관측 부족 또는 최근 중복)",
              file=sys.stderr)
        return EXIT_NO_TOPIC

    if args.json:
        print(json.dumps(cands, ensure_ascii=False, indent=1))
        return EXIT_OK

    md = render_brief(inputs, cands, today, age)
    print(md)
    if not args.stdout:
        out = S.BRIEF_DIR / f"{today.isoformat()}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        print(f"\n-> {out.relative_to(D.ROOT)}", file=sys.stderr)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            Path(summary).write_text(md, encoding="utf-8")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
