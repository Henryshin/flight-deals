"""Threads 본문·답글 문구. 후보마다 문구 6가지를 만들어 두고, 텔레그램에서 여행지를
고른 뒤 그중 하나를 고른다 (사용자 결정 2026-11-04 — 처음엔 날짜별 훅 순환이었다).

규칙 (analysis/09_threads-launch-kit.md):
- 본문에 링크를 넣지 않는다. 링크는 첫 본인 답글에 UTM 을 달아서.
- 반말, 짧게, 마지막 줄은 질문이나 행동 유도 (답글·저장).
- 숫자는 matrix.json 에서 온 값만. 지어내지 않는다.
"""
from datetime import date

SITE = "excel-flights.xyz"
REPLY_URL = f"{SITE}/?utm_source=threads&utm_medium=reply"

OPTIONS = (
    ("excel", "엑셀 위장"),
    ("price", "가격 강조"),
    ("leave", "연차 계산"),
    ("vs", "간다 vs 안 간다"),
    ("boss", "직장인 공감"),
    ("save", "저장각 메모"),
)


def _md(iso, wd):
    d = date.fromisoformat(iso)
    return f"{d.month}/{d.day}({wd})" if wd else f"{d.month}/{d.day}"


def _man(price):
    return f"{price // 10_000}만 원대"


def _won(price):
    return f"{price:,}원"


def facts_lines(c, today=None):
    today = today or date.today()
    period = f"{_md(c['depart_date'], c['depart_weekday'])}~{_md(c['return_date'], c['return_weekday'])}"
    if date.fromisoformat(c["depart_date"]).year > today.year:
        period = "내년 " + period
    trip = f"{c['nights']}박 {c['days']}일" if c.get("nights") is not None else ""
    leave = f"연차 {c['leave_days']}개" if c["leave_days"] else "연차 0개"
    if c["stops"] == "0":
        direct = "직항" if c["check"].startswith("양방향") else f"직항({c['check']})"
    else:
        direct = "경유"
    return [
        f"{c['flag']} {c['city']} 왕복 {_won(c['price'])}",
        f"{period} {trip} · {leave} ({c['holiday']} 끼고)",
        direct,
    ]


def body(c, key, today=None):
    facts = "\n".join(facts_lines(c, today))
    city, man, L, hol, n = c["city"], _man(c["price"]), c["leave_days"], c["holiday"], c.get("nights")

    def post(head, tail):
        return f"{head}\n\n{facts}\n\n{tail}"

    if key == "excel":
        return post("회사에서 엑셀 보는 척하면서 찾은 항공권ㅋㅋ", f"{hol}에 {city} 어때?")
    if key == "price":
        head = f"{city} 왕복이 {man}"
        if c.get("cheap_pct", 0) >= 10:
            head += f", 평소보다 {round(c['cheap_pct'])}% 쌈"
        return post(head, "이 가격이면 지르는 거 맞지?")
    if key == "leave":
        head = f"연차 {L}개로 {city} {n}박 가능" if L else f"연차 안 쓰고 {city} {n}박 가능"
        return post(head, "너네 연차 몇 개 남았어?")
    if key == "vs":
        return post(f"{hol}에 {city} {man}면\n간다 vs 안 간다", "댓글로 골라줘")
    if key == "boss":
        if L:
            return post(f"팀장님한테 연차 {L}개만 말하면 되는 {city} 일정 찾음", "이 정도면 결재 올려볼 만하지?")
        return post(f"연차 결재 없이 가는 {city} 일정 찾음", "이거 못 참지?")
    if key == "save":
        return post(f"📌 {hol} {city} 항공권 메모", "나중에 찾지 말고 저장해둬. 같이 갈 사람 태그하고")
    raise ValueError(key)


def bodies(c, today=None):
    return [{"key": k, "label": lab, "text": body(c, k, today)} for k, lab in OPTIONS]


def link_reply(collected_kst: str):
    return (f"👉 {REPLY_URL}\n"
            f"연휴 끼워서 연차 덜 쓰는 일정만 모아둔 엑셀이야 (무료·로그인X)\n"
            f"가격은 {collected_kst} 수집 기준이라 바뀔 수 있어")
