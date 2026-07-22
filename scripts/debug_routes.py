"""DAD(다낭)/TPE(타이페이)만 수집 0건인 원인 진단.

각 노선에 대해 도시명/대기시간 변형별로:
  - 최종 URL, 타이틀, li 개수, '왕복' li 개수, ₩ 등장 횟수
  - '왕복' li 중 ₩ 가격이 실제로 잡히는 첫 항목 샘플
을 출력해 (도시명 인식 실패 vs 렌더링 타이밍 vs 결과 없음)을 구분한다.
"""
import re
from urllib.parse import quote

from playwright.sync_api import sync_playwright

PRICE_PATTERN = re.compile(r"₩([0-9][0-9,]{2,})")
D1, D2 = "2026-09-23", "2026-09-26"  # 추석 연휴 근처, 성공 노선과 동일 날짜대

def q(s):
    return "https://www.google.com/travel/flights/search?q=" + quote(s) + "&hl=ko&curr=KRW"

# (라벨, 목적지 도시명) — 성공 노선(오사카)도 대조군으로 포함
CASES = [
    ("KIX_control_Osaka", "Osaka"),
    ("DAD_DaNang", "Da Nang"),
    ("DAD_Danang", "Danang"),
    ("DAD_airport", "Da Nang International Airport"),
    ("TPE_Taipei", "Taipei"),
    ("TPE_Taoyuan", "Taoyuan"),
    ("TPE_airport", "Taipei Taoyuan"),
]

def scan(page):
    lis = page.query_selector_all("li")
    rt = 0
    priced = []
    for li in lis:
        t = li.inner_text() or ""
        if "왕복" not in t:
            continue
        rt += 1
        m = PRICE_PATTERN.findall(t)
        if m:
            priced.append(t.replace("\n", " | ")[:120])
    return len(lis), rt, priced

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(
        locale="ko-KR",
        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    )
    for label, city in CASES:
        url = q(f"Flights from Seoul to {city} on {D1} through {D2}")
        page = ctx.new_page()
        try:
            page.goto(url, timeout=30000)
            # 6초(현재값) 시점과 12초 시점을 각각 측정 -> 타이밍 문제인지 확인
            page.wait_for_timeout(6000)
            n6, rt6, pr6 = scan(page)
            page.wait_for_timeout(6000)
            n12, rt12, pr12 = scan(page)
            print(f"===== {label} (city={city!r})")
            print(f"  title: {page.title()!r}")
            print(f"  @6s:  li={n6} 왕복={rt6} priced={len(pr6)}")
            print(f"  @12s: li={n12} 왕복={rt12} priced={len(pr12)}")
            if pr12:
                print(f"  first_priced: {pr12[0]}")
            elif rt12 == 0:
                body = page.inner_text("body")
                print(f"  NO_ROUNDTRIP. body_head: {body[:250].replace(chr(10),' | ')}")
        except Exception as e:
            print(f"===== {label}: EXCEPTION {type(e).__name__}: {e}")
        finally:
            page.close()
    browser.close()
