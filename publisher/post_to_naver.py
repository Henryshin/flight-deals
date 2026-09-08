"""posts/<슬러그>/post.json 을 네이버 블로그 에디터에 채운다 (CDP 연결).

    # 1. 사용자 터미널에서 크롬을 원격 디버깅 포트로 띄우고 직접 로그인
    #    (98.네이버블로그자동화_v1.0/login.py 가 하는 일)
    # 2. 그 다음
    python publisher/post_to_naver.py posts/2026-09-08-icn-bki-2026-10-05

제목·본문·이미지까지만 채우고 **멈춘다.** 저장도 발행도 하지 않는다.
검토·태그·발행은 사람 몫이다.

검증 상태 (2026-09-08)
----------------------
**실물 SmartEditor 로는 한 번도 돌려보지 않았다.** 원격 컨테이너에서는
naver.com 이 네트워크 정책상 막혀 있고 CDP 엔드포인트도 닿지 않는다.

가짜 에디터로 확인한 것: CDP 연결 · page.frame(name=...) · 복구 대화상자 닫기 ·
제목 삽입 후 대조 · 파일 선택기 이미지 업로드.

가짜 에디터가 **실제로 잡아낸 버그** (고쳐서 반영함):
  트리플클릭 + ArrowRight 로 커서를 옮기면 문단이 통째로 사라지고 순서가
  뒤집힌다. 트리플클릭은 '선택' 연산이라, collapse 되기 전에 insert_text 가
  들어가면 선택된 문단을 덮어쓴다. -> Selection API 로 끝점에 collapse (caret_to_end).

**아직 못 가른 것**: 마지막 실행에서 insert_paragraphs 의 대조가 걸렸는데,
진짜 버그인지 가짜 에디터가 실물과 달라서인지 구분할 수 없었다. 가짜 쪽은
contenteditable 이 문단마다 .se-text-paragraph 를 만들지 않아 커서 동작이 다르다.
=> 실물에서 한 번 돌려보고 판단할 것. 로그가 어디서 멈추는지가 답을 준다.

왜 CDP 인가
-----------
Claude Code 세션은 headed 브라우저를 직접 못 띄운다 (chromium.launch(headless=False)
-> spawn UNKNOWN). 그래서 프로세스를 새로 띄우는 대신, 사용자가 이미 띄워 둔
크롬에 connect_over_cdp 로 **붙기만** 한다.

CDP 엔드포인트는 브라우저가 떠 있는 그 컴퓨터의 localhost 다. 원격 세션에서는
닿지 않으므로, 이 스크립트는 크롬이 떠 있는 PC 에서 돌려야 한다.

절대 하지 말 것
---------------
- browser.close() — 연결 해제가 아니라 사용자 크롬을 진짜로 닫는다. with 블록만
  빠져나오면 된다.
- '발행'/'저장' 클릭 — 이 스크립트는 채우기만 한다.
"""
import argparse
import json
import re
import sys
import time
from html import unescape
from pathlib import Path

CDP_URL = "http://localhost:9222"
WRITE_URL = "https://blog.naver.com/{blog_id}?Redirect=Write"

# 에디터는 iframe 안이다. 최상위 page 에서 찾으면 전부 빈 결과가 나온다.
FRAME_NAME = "mainFrame"

TITLE_INNER = ".se-title-text .se-text-paragraph"
BODY_CONTAINER = ".se-component-content"
BODY_INNER = ".se-component-content .se-text-paragraph"
IMAGE_BUTTON = ".se-image-toolbar-button"
IMAGE_COMPONENT = ".se-image"
# text=취소 는 화면에 안 보이는 스크린리더용 span 을 먼저 잡아 타임아웃된다.
RECOVERY_CANCEL = ".se-popup-alert-confirm button:has-text('취소')"

FOCUS_SETTLE_MS = 400


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


class Failed(RuntimeError):
    pass


# --- 텍스트 ------------------------------------------------------------------

def html_to_paragraphs(html):
    """블록 HTML -> 문단 문자열 목록.

    표는 SmartEditor 에 평문으로 넣으면 읽기 어려워지므로 ' | ' 구분으로 편다.
    (가격 표는 어차피 PNG 카드로도 들어간다.)
    """
    s = html
    s = re.sub(r"</(p|h3|li|tr|blockquote)>", "\n", s)
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"</t[dh]>\s*<t[dh][^>]*>", " | ", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = unescape(s)
    return [ln.strip() for ln in s.split("\n") if ln.strip()]


# --- 커서 --------------------------------------------------------------------

def caret_to_end(frame):
    """본문 맨 끝(이미지 뒤 포함)으로 커서를 옮긴다.

    End 키는 문단 끝이 아니라 **화면 줄 끝**으로 가므로 쓰면 안 된다.
    트리플클릭 + ArrowRight 도 쓰지 않는다 — 트리플클릭은 문단을 '선택'하는
    연산이라, collapse 되기 전에 insert_text 가 들어가면 그 문단을 통째로
    덮어쓴다. 실제로 그렇게 문단이 사라지고 순서가 뒤집히는 것을 확인했다.

    Selection API 로 콘텐츠 끝에 caret 을 collapse 시키는 것이 유일하게
    비파괴적이다. 선택 영역이 남지 않는다.
    """
    frame.evaluate(
        """(sel) => {
            const el = document.querySelector(sel);
            if (!el) return false;
            el.focus();
            const r = document.createRange();
            r.selectNodeContents(el);
            r.collapse(false);          // false = 끝점
            const s = getSelection();
            s.removeAllRanges();
            s.addRange(r);
            return true;
        }""",
        BODY_CONTAINER,
    )


# --- 입력 --------------------------------------------------------------------

def insert_paragraphs(frame, page, lines, verify=True):
    """새 문단을 넣는다.

    반드시 insert_text (CDP Input.insertText) 를 쓴다. type() 으로 실제 키를 치면
    "1. 회사 개요" 처럼 숫자+마침표로 시작하는 줄에서 자동 번호매기기가 발동해
    그 줄부터 끝까지 순서 목록이 되어 버린다.

    insert_text 는 실패해도 **예외를 던지지 않는다.** 넣고 나서 읽어 대조하지
    않으면 조용한 실패가 그대로 발행까지 간다.
    """
    for i, line in enumerate(lines):
        page.keyboard.insert_text(line)
        if i < len(lines) - 1:
            page.keyboard.press("Enter")
    page.wait_for_timeout(150)
    if verify and lines:
        probe = lines[-1][:20]
        body = frame.locator(BODY_CONTAINER).inner_text()
        if probe and probe not in body:
            raise Failed(f"본문 삽입이 반영되지 않았다: {probe!r}")


def set_title(frame, page, title):
    """제목 입력. 바깥 div 를 클릭하면 포커스가 안 잡혀 insert_text 가 증발한다."""
    inner = frame.locator(TITLE_INNER).first
    inner.wait_for(state="visible", timeout=15000)
    inner.click()
    page.wait_for_timeout(FOCUS_SETTLE_MS)
    page.keyboard.insert_text(title)
    page.wait_for_timeout(200)

    got = frame.locator(".se-title-text").inner_text().strip()
    if title.strip() not in got:
        raise Failed(
            f"제목이 들어가지 않았다. 기대 {title!r}, 실제 {got!r}\n"
            "  포커스 타이밍 문제다. 다시 실행하면 대개 된다."
        )
    log(f"  제목 확인됨: {got[:40]}")


def upload_image(frame, page, path):
    caret_to_end(frame)
    before = frame.locator(IMAGE_COMPONENT).count()
    with page.expect_file_chooser(timeout=15000) as fc:
        frame.click(IMAGE_BUTTON)
    fc.value.set_files([str(Path(path).resolve())])
    for _ in range(60):
        if frame.locator(IMAGE_COMPONENT).count() > before:
            page.wait_for_timeout(600)
            return
        page.wait_for_timeout(500)
    raise Failed(f"이미지 업로드가 끝나지 않았다: {path}")


def dismiss_recovery(frame, page):
    try:
        btn = frame.locator(RECOVERY_CANCEL).first
        if btn.is_visible(timeout=2500):
            btn.click()
            log("  복구 대화상자 닫음")
            page.wait_for_timeout(400)
    except Exception:
        pass


# --- 본체 --------------------------------------------------------------------

def fill_post(page, post, post_dir):
    frame = page.frame(name=FRAME_NAME)
    if frame is None:
        raise Failed(
            f"iframe '{FRAME_NAME}' 을 못 찾았다. 글쓰기 화면이 맞는지 확인할 것.\n"
            f"  현재 URL: {page.url}"
        )
    dismiss_recovery(frame, page)

    log("제목 입력")
    set_title(frame, page, post["title"])

    log("본문 입력")
    frame.locator(BODY_INNER).first.click()
    page.wait_for_timeout(FOCUS_SETTLE_MS)

    blocks = post["blocks"]
    for i, b in enumerate(blocks, 1):
        head = f"  [{i}/{len(blocks)}]"
        if b["type"] == "html":
            lines = html_to_paragraphs(b["content"])
            log(f"{head} 문단 {len(lines)}개")
            insert_paragraphs(frame, page, lines)
        elif b["type"] == "card":
            f = post_dir / b.get("file", f"images/card-{b['card']}.png")
            if not f.exists():
                raise Failed(f"카드 이미지가 없다: {f}")
            log(f"{head} 카드 업로드 {f.name}")
            upload_image(frame, page, f)
            # 업로드 후 에디터가 포커스를 옮기므로 여기서만 커서를 되돌린다
            caret_to_end(frame)
        elif b["type"] == "photo":
            log(f"{head} 사진 자리")
            insert_paragraphs(frame, page, [f"[여기에 사진: {b['hint']}]"])
        # 텍스트 삽입 뒤에는 caret 이 이미 끝에 있다. 건드리지 않는다.
        if i < len(blocks):
            page.keyboard.press("Enter")

    verify_whole(frame, post, post_dir)


def verify_whole(frame, post, post_dir):
    """전부 넣고 나서 통째로 대조한다.

    조각마다 확인해도 '순서가 뒤집혔다'나 '중간 문단이 통째로 사라졌다'는
    안 잡힌다. 실제로 커서를 잘못 옮겨 그렇게 깨지는 것을 확인했으므로,
    마지막에 원문 문단이 모두 살아 있는지와 순서가 맞는지를 본다.
    """
    body = frame.locator(BODY_CONTAINER).inner_text()
    want = []
    for b in post["blocks"]:
        if b["type"] == "html":
            want += html_to_paragraphs(b["content"])
        elif b["type"] == "photo":
            want.append(f"[여기에 사진: {b['hint']}]")

    missing = [w for w in want if w not in body]
    if missing:
        raise Failed(
            f"본문 {len(missing)}개 문단이 들어가지 않았다. 예: {missing[:3]}"
        )

    pos, prev, out_of_order = 0, None, []
    for w in want:
        at = body.find(w, pos)
        if at < 0:                      # 앞쪽에만 있으면 순서가 뒤집힌 것
            out_of_order.append(w)
        else:
            pos, prev = at + len(w), w
    if out_of_order:
        raise Failed(
            f"문단 순서가 원문과 다르다. 예: {out_of_order[:3]}"
        )

    n_img = sum(1 for b in post["blocks"] if b["type"] == "card")
    got_img = frame.locator(IMAGE_COMPONENT).count()
    if got_img < n_img:
        raise Failed(f"이미지가 {n_img}장이어야 하는데 {got_img}장이다")

    log(f"  검증 통과: 문단 {len(want)}개 · 이미지 {got_img}장 · 순서 일치")


def main(argv=None):
    ap = argparse.ArgumentParser(description="post.json 을 네이버 에디터에 채운다")
    ap.add_argument("post_dir", help="posts/<슬러그> 경로")
    ap.add_argument("--cdp", default=CDP_URL, help=f"CDP 엔드포인트 (기본 {CDP_URL})")
    ap.add_argument("--blog-id", help="글쓰기 페이지가 안 열려 있을 때 직접 연다")
    ap.add_argument("--match", default="blog.naver.com",
                    help="채울 탭을 고를 URL 부분 문자열 (기본 blog.naver.com)")
    args = ap.parse_args(argv)

    post_dir = Path(args.post_dir).resolve()
    pj = post_dir / "post.json"
    if not pj.exists():
        print(f"post.json 이 없다: {pj}", file=sys.stderr)
        return 1
    post = json.loads(pj.read_text(encoding="utf-8"))

    from playwright.sync_api import sync_playwright

    log(f"CDP 연결 {args.cdp}")
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp(args.cdp)
        except Exception as e:
            print(
                f"\nCDP 연결 실패: {e}\n"
                "  크롬이 --remote-debugging-port=9222 로 떠 있어야 한다.\n"
                "  (login.py 를 먼저 실행하고, 그 창을 닫지 말 것)\n"
                "  이 스크립트는 크롬이 떠 있는 그 PC 에서 돌려야 한다.",
                file=sys.stderr,
            )
            return 1

        ctx = browser.contexts[0]
        page = next((p for p in ctx.pages if args.match in p.url), None)
        if page is None:
            if not args.blog_id:
                print("글쓰기 페이지가 안 열려 있다. --blog-id 를 주거나 직접 열 것.",
                      file=sys.stderr)
                return 1
            page = ctx.new_page()
            page.goto(WRITE_URL.format(blog_id=args.blog_id),
                      wait_until="domcontentloaded")
        page.bring_to_front()
        log(f"대상 탭: {page.url}")

        try:
            fill_post(page, post, post_dir)
        except Failed as e:
            print(f"\n중단: {e}", file=sys.stderr)
            return 1
        # browser.close() 를 부르면 사용자 크롬이 진짜로 닫힌다. 부르지 않는다.

    print()
    log("채우기 완료. 저장도 발행도 하지 않았다.")
    print(f"  태그(직접 입력): {' '.join(post.get('tags', []))}")
    for h in post.get("photo_hints", []):
        print(f"  사진 자리: {h}")
    print("  사진을 넣고 한 번 읽어본 뒤 직접 발행하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
