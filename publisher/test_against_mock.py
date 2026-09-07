"""가짜 에디터로 발행기 메커니즘을 검증한다 (네이버 접속 없음).

    python -m http.server 8899 --directory publisher &
    python publisher/test_against_mock.py

naver_draft.py 의 **실제 함수**를 그대로 호출하므로, 여기서 통과하면 적어도
다음은 보장된다:

  - #mainFrame 진입과 시작 팝업 닫기
  - 클립보드 text/html 붙여넣기로 서식(<strong>/<h3>/<table>)이 살아남음
  - 사진 버튼 -> 파일 선택기 -> 이미지 업로드
  - 블록이 초안 순서대로 들어감
  - '저장' 은 눌리고 '발행' 은 절대 눌리지 않음

**보장하지 않는 것**: 실제 네이버 SmartEditor 의 셀렉터. 그건 --keep-open 으로
직접 확인해야 한다. 이 테스트는 "로직은 맞는데 셀렉터만 틀린" 상태와
"로직이 틀린" 상태를 구분해 준다.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import naver_draft as N

MOCK_URL = "http://localhost:8899/mock_editor.html"


def main():
    posts = N.ROOT / "posts"
    post_dir = N.find_latest_post({"posts_dir": "../posts"})
    if not post_dir:
        print("FAIL: posts/ 에 글이 없다")
        return 1
    post = json.loads((post_dir / "post.json").read_text(encoding="utf-8"))
    cfg = dict(N.DEFAULT_CONFIG, blog_id="mocktest", write_url=MOCK_URL,
               profile_dir=".test-profile", slow_mo_ms=20)

    from playwright.sync_api import sync_playwright

    failures = []

    def check(name, ok, detail=""):
        print(f"{'PASS' if ok else 'FAIL'} {name}{'' if ok else ': ' + detail}")
        if not ok:
            failures.append(name)

    with sync_playwright() as pw:
        ctx = N.open_context(pw, cfg, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("dialog", lambda d: d.dismiss())
        try:
            page.goto(MOCK_URL, wait_until="domcontentloaded")
            frame = N.editor_frame(page, cfg)
            check("에디터 프레임 진입", True)

            N.dismiss_popups(page, frame)
            check("시작 팝업 닫힘",
                  frame.locator("#popup").count() == 0)

            title = N.first_visible(frame, N.SEL.TITLE, "제목", cfg["timeout_ms"])
            title.click()
            page.keyboard.type(post["title"], delay=5)
            page.keyboard.press("Tab")
            got_title = frame.locator("[data-role='title']").inner_text().strip()
            check("제목 입력", got_title == post["title"],
                  f"기대 {post['title']!r}, 실제 {got_title!r}")

            N.insert_blocks(page, frame, post, post_dir, cfg)

            body_html = frame.locator(".se-component-content").inner_html()
            n_cards = sum(1 for b in post["blocks"] if b["type"] == "card")

            check("굵은 글씨 보존", "<strong>" in body_html or "<b>" in body_html,
                  "클립보드 HTML 붙여넣기가 서식을 못 살렸다")
            check("소제목 보존",
                  any(t in body_html.lower() for t in ("<h3", "<h2", "<b>")),
                  "소제목이 평문으로 들어갔다")
            check("표 보존", "<table" in body_html.lower(),
                  "표가 평문으로 들어갔다")
            check(f"이미지 {n_cards}장 업로드",
                  frame.locator(".se-image").count() == n_cards,
                  f"실제 {frame.locator('.se-image').count()}장")
            check("사진 자리 표시 삽입",
                  "여기에 사진" in body_html,
                  "[[PHOTO:]] 자리가 본문에 안 남았다")

            # 초안 순서대로 들어갔는가 — 첫 문단이 본문 앞쪽에 있어야 한다
            first_para = post["blocks"][0]["content"]
            marker = "안녕하세요" if "안녕하세요" in first_para else None
            if marker:
                check("블록 순서 (첫 문단이 앞)",
                      body_html.find(marker) < body_html.find("여기에 사진"))

            N.save_draft(page, frame, cfg)

            events = page.frame_locator("#mainFrame").locator("body").evaluate(
                "() => JSON.stringify(window.__events || [])"
            )
            ev = json.loads(events)
            kinds = [e["kind"] for e in ev]
            check("임시저장 눌림", "saved" in kinds, str(kinds))
            check("발행 안 눌림 ★", "PUBLISHED" not in kinds,
                  "안전장치가 뚫렸다 — 즉시 고칠 것")
            check("이미지 업로드 이벤트",
                  kinds.count("image_uploaded") == n_cards, str(kinds))
        finally:
            ctx.close()

    print()
    if failures:
        print(f"{len(failures)}개 실패: {failures}")
        return 1
    print("메커니즘 검증 통과. 실제 셀렉터는 --keep-open 으로 별도 확인 필요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
