"""GitHub Actions 러너 환경에서 구글 플라이트 URL 문구별로 결과 리스트가 뜨는지 비교 진단.

각 변형에 대해: 최종 URL(리다이렉트 여부), 페이지 타이틀, li 개수, '왕복' 포함 li 개수,
가격(₩) 등장 횟수, 컨센트/봇차단 마커, 첫 결과 텍스트 샘플을 출력한다.
"""
from urllib.parse import quote

from playwright.sync_api import sync_playwright

D1, D2 = "2026-09-23", "2026-09-26"

def q(s):
    return "https://www.google.com/travel/flights/search?q=" + quote(s)

VARIANTS = {
    "v1_returning": q(f"Flights from Seoul to Tokyo on {D1} returning {D2}"),
    "v2_through": q(f"Flights from Seoul to Tokyo on {D1} through {D2}"),
    "v3_roundtrip_through": q(f"round trip flights from Seoul to Tokyo on {D1} through {D2}"),
    "v4_korean": q(f"서울 도쿄 왕복 항공권 {D1} 출발 {D2} 도착"),
    "v5_through_hl": q(f"Flights from Seoul to Tokyo on {D1} through {D2}") + "&hl=ko&curr=KRW",
    "v6_returning_hl": q(f"Flights from Seoul to Tokyo on {D1} returning {D2}") + "&hl=ko&curr=KRW",
}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    for name, url in VARIANTS.items():
        page = browser.new_page(
            locale="ko-KR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        )
        try:
            page.goto(url, timeout=30000)
            page.wait_for_timeout(7000)
            body = page.inner_text("body")
            lis = page.query_selector_all("li")
            rt_texts = []
            for li in lis:
                t = li.inner_text() or ""
                if "왕복" in t or "round trip" in t.lower():
                    rt_texts.append(t)
            print(f"===== {name}")
            print(f"  final_url: {page.url[:160]}")
            print(f"  title: {page.title()!r}")
            print(f"  li_total={len(lis)} li_roundtrip={len(rt_texts)} won_count={body.count(chr(8361))}")
            print(f"  markers: 왕복={'왕복' in body} 편도={'편도' in body} oneway={'one way' in body.lower()} "
                  f"consent={'consent' in page.url or '동의' in body[:2000]} "
                  f"captcha={'unusual traffic' in body.lower() or 'CAPTCHA' in body}")
            if rt_texts:
                print("  first_rt: " + rt_texts[0].replace("\n", " | ")[:220])
            else:
                print("  body_head: " + body[:400].replace("\n", " | "))
        except Exception as e:
            print(f"===== {name}: EXCEPTION {type(e).__name__}: {e}")
        finally:
            page.close()
    browser.close()
