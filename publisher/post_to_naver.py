"""posts/<슬러그>/post.json 을 네이버 블로그 에디터에 채운다 (CDP 연결).

    # 1. 사용자 터미널에서 크롬을 원격 디버깅 포트로 띄우고 직접 로그인
    #    (98.네이버블로그자동화_v1.0/login.py 가 하는 일)
    # 2. 그 다음
    python publisher/post_to_naver.py posts/2026-09-08-icn-bki-2026-10-05

제목·본문·이미지까지만 채우고 **멈춘다.** 저장도 발행도 하지 않는다.
검토·태그·발행은 사람 몫이다.

검증 상태 (2026-09-08)
----------------------
**실물 SmartEditor 에서 통과했다.** 사용자 크롬(CDP 9222)의 blog.naver.com 글쓰기
화면에서 제목 · 문단 78개 · 카드 이미지 2장을 채웠고, 라이브 DOM 을 컴포넌트
순서대로 덤프해 원문과 대조했다 — 항목 80개 전부 일치, 이미지도 제자리(14 · 45).

첫 실물 실행에서 잡은 것. 전부 가짜 에디터로는 안 보이던 것들이다.

1. 탭 선택이 로그인 페이지를 집었다. 네이버 로그인 URL 의 url= 파라미터에
   'blog.naver.com' 이 그대로 들어 있어서 URL 부분일치가 거기 먼저 걸린다.
   -> find_editor_page: mainFrame 안에 제목칸이 있는 탭만 고른다.

2. BODY_CONTAINER = '.se-component-content' 가 틀렸다. 이 클래스는 **제목
   컴포넌트에도 있어서** 빈 글에서도 2개가 잡힌다. querySelector 의 첫 매치가
   제목이라 캐럿이 제목칸으로 가고, locator(...).inner_text() 는 매치가 여러 개라
   strict mode 위반으로 죽는다. -> 본문은 '.se-component.se-text' 로 스코프한다.

3. Selection API 로 캐럿을 옮기는 방식은 실물에서 동작하지 않는다. 본문 문단에는
   contenteditable 조상이 아예 없고(closest('[contenteditable=true]') -> null),
   에디터는 숨은 input buffer 로 입력을 받아 자기 캐럿 모델을 따로 굴린다.
   <p>.focus() 는 무효라 document.getSelection() 을 옮겨도 에디터가 안 따라온다.
   -> 검증된 도구(lib.py focus_body_tail)와 같이 '마지막 문단 클릭'으로 바꿨다.
   이 함수를 부르는 시점의 마지막 문단은 항상 비어 있어서 클릭만으로 캐럿이
   끝에 놓인다. 비어 있지 않으면 문장 중간을 찍을 수 있으므로 그 자리에서 멈춘다.
   트리플클릭도 End 키도 여전히 쓰지 않는다.

4. **줄이 조용히 사라진다.** insert_text 직후 Enter 를 바로 치면 에디터가 아직
   입력을 처리하는 중이라 줄이 통째로 씹힌다. 30줄 블록에서 5줄, 18줄 블록에서
   5줄이 유실됐는데, 블록 단위 대조는 마지막 줄만 확인해서 그대로 통과했다.
   맨 끝 verify_whole 에서야 걸렸다. -> 줄마다 마지막 문단을 읽어 대조하고,
   비어 있으면 다시 넣는다. Enter 도 같은 방식으로 확인한다(씹히면 한 번 더).

5. 빈 문단에는 '.se-placeholder' span 이 실제 DOM 노드로 들어 있다
   ('글감과 함께 나의 일상을 기록해보세요!'). 그냥 읽으면 본문 텍스트로 섞인다.
   -> 대조 전에 걷어낸다.

한 번은 팝업(.se-popup-alert)이 툴바를 덮어 사진 버튼 클릭이 30초 재시도하다
죽은 적이 있다. 재현되지 않았고 원인은 못 밝혔다. describe_popup() 이 다음에는
그 팝업의 문구와 버튼을 에러에 실어 주므로, 또 나면 그 로그를 보고 판단할 것.

링크
----
본문에 거는 링크는 **post.json 의 booking_url 하나뿐이다.** 그 조건이 조회되는
구글 항공권 화면이고, 글에서 제일 중요한 링크다. 맨 끝 BOOKING_LINK_TEXT 문구에 건다.

원문 HTML 안의 <a>(자체 사이트 링크 등)는 **평문으로 두는 것이 맞다.** 사용자가
2026-09-08 에 그렇게 결정했다. 태그가 지워져서 생긴 사고가 아니라 의도된 동작이니
"링크가 빠졌다"고 보고 되살리지 말 것. 무엇이 평문으로 갔는지는 로그에 남는다.

사진
----
photo 블록은 photo-credits.json 이 있으면 실제 사진을 넣고, 없으면 예전처럼
'[여기에 사진: ...]' 자리 표시만 넣는다. 그 파일은 publisher/fetch_photos.py 가
위키미디어 커먼즈에서 라이선스를 확인하고 만든다.

**photos.json 은 있는데 아직 안 받았으면 이 스크립트가 알아서 받는다.** 따로
fetch_photos.py 를 돌릴 필요가 없다 (--no-fetch 로 끌 수 있다). 받는 것은 에디터를
건드리기 전에 끝낸다 — 받다가 실패하면 반쯤 채워진 글이 남는 것보다 아무것도 안 한
상태로 죽는 게 낫다. 사진을 실제로 넣었을 때는
글 끝에 저작자·라이선스 출처 문단을 붙인다 — CC BY / BY-SA 의 표시 의무이므로
사람이 지우면 안 된다.

정렬
----
본문은 **기본이 가운데정렬**이다(--align 으로 바꾼다, keep 은 손대지 않음).
문단마다 걸지 않고 다 채운 뒤 전체 선택해서 한 번만 누른다 — 정렬은 '현재 서식'
으로 이어지는 성질이 있어서 중간에 걸면 이후 입력·이미지 삽입과 계속 얽힌다.
누른 뒤에는 선택을 반드시 푼다. 전체 선택이 남아 있으면 다음 입력이 문서를
통째로 덮어쓴다.

이어서 실행할 때는 --fresh 를 준다. 글쓰기 화면을 다시 열고 복구 대화상자를 닫아
빈 글에서 시작한다(쓰던 내용은 버려진다). --fresh 없이 돌리면 assert_blank 가
빈 글이 아닌 것을 보고 덮어쓰지 않고 멈춘다.

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
import io
import json
import re
import sys
import time
from html import unescape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_photos import credit_lines, fetch as fetch_commons_photos

# Windows 콘솔(cp949)에서 한글 로그가 깨지거나 UnicodeEncodeError 로 죽는다.
if sys.stdout.encoding is None or sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

# localhost 는 IPv6(::1) 로 먼저 붙으려다 실패할 수 있어 127.0.0.1 로 고정한다.
CDP_URL = "http://127.0.0.1:9222"
WRITE_URL = "https://blog.naver.com/{blog_id}?Redirect=Write"

# 에디터는 iframe 안이다. 최상위 page 에서 찾으면 전부 빈 결과가 나온다.
FRAME_NAME = "mainFrame"

TITLE_TEXT = ".se-title-text"
TITLE_INNER = ".se-title-text .se-text-paragraph"
# 본문은 반드시 .se-component.se-text 로 스코프한다. .se-component-content 는
# **제목 컴포넌트에도 있어서** 빈 글에서도 2개가 잡힌다. 그걸 쓰면 (1) querySelector
# 첫 매치가 제목이라 커서가 제목칸으로 가고 (2) locator 는 매치가 여러 개라
# strict mode 위반으로 죽는다. 실물 에디터에서 확인했다 (2026-09-08).
BODY_COMPONENT = ".se-component.se-text"
BODY_INNER = ".se-component.se-text .se-text-paragraph"
IMAGE_BUTTON = ".se-image-toolbar-button"
IMAGE_COMPONENT = ".se-component.se-image"
HELP_CLOSE = ".se-help-panel-close-button"
# 정렬 드롭다운을 여는 버튼은 **현재 정렬 상태에 따라 자기 클래스가 바뀐다**
# (se-align-left-toolbar-button 등). 상태를 안 타는 부모로 스코프한다.
ALIGN_DROPDOWN = ".se-toolbar-item-align .se-property-toolbar-drop-down-button"
ALIGN_OPTION = {
    "left": ".se-toolbar-option-align-left-button",
    "center": ".se-toolbar-option-align-center-button",
    "right": ".se-toolbar-option-align-right-button",
    "justify": ".se-toolbar-option-align-justify-button",
}
ALIGN_DEFAULT = "center"
# 글자 크기. 드롭다운 옵션 순서가 곧 이 목록이다.
FONT_SIZE_BUTTON = ".se-font-size-code-toolbar-button"
FONT_SIZE_OPTION = ".se-toolbar-option-text-button"
FONT_SIZES = [11, 13, 15, 16, 19, 24, 28, 30, 34, 38]
# 출처 문단은 본문과 달리 왼쪽 · 최소 크기로 둔다 (사용자 지정, 2026-09-08).
CREDIT_ALIGN = "left"
CREDIT_PT = 11
# 발행 설정 패널. **패널을 여는 버튼과 진짜 발행 버튼이 둘 다 '발행' 이다.**
# 텍스트로 고르면 안 되고 클래스로 갈라야 한다. confirm_btn 은 절대 누르지 않는다.
PUBLISH_PANEL_BUTTON = "button[class*='publish_btn']"
PUBLISH_CONFIRM_BUTTON = "button[class*='confirm_btn']"   # 누르지 말 것
TAG_INPUT = 'input[placeholder*="태그"]'
TAG_ITEMS = "[class*='tag_textarea'] [class*='tag__']"
# 링크. '링크 추가'(se-oglink) 는 카드 컴포넌트를 넣는 것이고, 글자에 링크를
# 거는 것은 '링크 입력'(se-link) 쪽이다.
LINK_BUTTON = ".se-link-toolbar-button"
LINK_INPUT = "input.se-custom-layer-link-input"
LINK_APPLY = ".se-custom-layer-link-apply-button"
# 글감 검색 결과 패널. 열려 있으면 본문 클릭을 전부 가로챈다. Escape 로는 안 닫힌다.
MATERIAL_PANEL = ".se-popup-unified-result-flayer--visible"
MATERIAL_CLOSE = ".se-flayer-unified-result-close"
# 예약 링크를 거는 문구. post.json 의 booking_url 로 연결한다.
BOOKING_LINK_TEXT = "구글 항공권에서 이 조건 그대로 보기"
# text=취소 는 화면에 안 보이는 스크린리더용 span 을 먼저 잡아 타임아웃된다.
RECOVERY_CANCEL = ".se-popup-alert-confirm button:has-text('취소')"

FOCUS_SETTLE_MS = 400
# insert_text 직후 Enter 를 바로 치면 에디터가 입력을 아직 처리 중이라 줄이
# 통째로 씹힌다. 실측: settle 없이 30줄 블록에서 5줄, 18줄 블록에서 5줄 유실
# (2026-09-08). 대기만으로는 못 믿으므로 줄마다 읽어 대조하고 재시도한다.
INSERT_SETTLE_MS = 80
ENTER_SETTLE_MS = 80


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

def body_text(frame):
    """본문 문단만 이어붙여 돌려준다 (제목·이미지 캡션 제외).

    locator(...).inner_text() 를 쓰면 매치가 여러 개일 때 strict mode 로 죽으므로
    evaluate 로 직접 모은다.
    """
    return chr(10).join(body_paragraphs(frame))


def body_paragraphs(frame):
    """본문 문단 텍스트 목록. 플레이스홀더 span 은 뺀다.

    빈 문단에는 `.se-placeholder` span 이 실제 DOM 노드로 들어 있어서
    ('글감과 함께 나의 일상을 기록해보세요!') 그냥 읽으면 본문 텍스트로 섞인다.
    """
    return frame.evaluate(
        """(sel) => Array.from(document.querySelectorAll(sel)).map(el => {
               const c = el.cloneNode(true);
               c.querySelectorAll('.se-placeholder, .__se_placeholder')
                .forEach(n => n.remove());
               return c.textContent.trim();
           })""",
        BODY_INNER,
    )


def last_body_paragraph(frame):
    """마지막 본문 문단의 텍스트. 줄 단위 대조용이라 전체를 훑지 않는다."""
    return frame.evaluate(
        """(sel) => {
               const ps = document.querySelectorAll(sel);
               if (!ps.length) return "";
               const c = ps[ps.length - 1].cloneNode(true);
               c.querySelectorAll('.se-placeholder, .__se_placeholder')
                .forEach(n => n.remove());
               return c.textContent;
           }""",
        BODY_INNER,
    )


def norm(s):
    """대조용 공백 정규화. 에디터가 공백을 NBSP 로 바꾸는 경우가 있다."""
    return " ".join(s.replace(chr(0xA0), " ").split())


def caret_to_end(frame, page):
    """본문 마지막 문단을 클릭해 캐럿을 그 끝에 둔다.

    End 키는 문단 끝이 아니라 **화면 줄 끝**으로 가므로 쓰지 않는다.
    트리플클릭도 쓰지 않는다 — 문단을 '선택'하는 연산이라 collapse 되기 전에
    insert_text 가 들어가면 그 문단을 통째로 덮어쓴다.

    Selection API 로 collapse 하는 방법도 **실물에서는 안 된다.** 본문 문단에는
    contenteditable 조상이 아예 없고(실측: closest('[contenteditable=true]') -> null),
    SmartEditor 는 숨은 input buffer 로 입력을 받아 자기 캐럿 모델을 따로 굴린다.
    <p>.focus() 는 무효라 document.getSelection() 을 옮겨도 에디터가 안 따라온다.

    남는 것은 검증된 도구가 쓰는 방식 — 마지막 문단을 그냥 클릭하는 것이다.
    이 스크립트가 이 함수를 부르는 시점(이미지 앞뒤)의 마지막 본문 문단은 항상
    비어 있다. 직전에 Enter 를 쳤거나, 이미지 삽입 후 에디터가 새 빈 문단을
    만들기 때문이다. 빈 문단은 어디를 눌러도 캐럿이 끝에 놓인다.

    비어 있지 않으면 클릭 지점이 문장 중간이 될 수 있으므로 그 자리에서 멈춘다.
    """
    paras = body_paragraphs(frame)
    if not paras:
        raise Failed("본문 문단을 찾지 못해 커서를 옮기지 못했다")
    if paras[-1]:
        raise Failed(
            "마지막 본문 문단이 비어 있지 않다: " + repr(paras[-1][:30]) + ". "
            "여기를 클릭하면 캐럿이 문장 중간에 놓인다. 흐름이 예상과 다르므로 멈춘다."
        )
    last = frame.locator(BODY_INNER).last
    last.scroll_into_view_if_needed()
    page.wait_for_timeout(150)
    last.click()
    page.wait_for_timeout(250)


# --- 입력 --------------------------------------------------------------------

def insert_paragraphs(frame, page, lines, verify=True):
    """새 문단을 넣는다. **한 줄마다 읽어서 대조하고, 씹히면 다시 넣는다.**

    반드시 insert_text (CDP Input.insertText) 를 쓴다. type() 으로 실제 키를 치면
    "1. 회사 개요" 처럼 숫자+마침표로 시작하는 줄에서 자동 번호매기기가 발동해
    그 줄부터 끝까지 순서 목록이 되어 버린다.

    insert_text 는 실패해도 **예외를 던지지 않는다.** 예전에는 블록의 마지막 줄만
    대조했는데, 그러면 블록 중간에서 사라진 줄을 못 잡는다 — 실물 첫 실행에서
    30줄 블록과 18줄 블록이 각각 5줄씩 조용히 유실됐다(2026-09-08). 블록 단위
    대조는 통과했고 맨 끝 verify_whole 에서야 걸렸다. 그래서 줄 단위로 내린다.

    Enter 도 같이 씹힌다. Enter 뒤 마지막 문단이 비어 있지 않으면 새 문단이 안
    생긴 것이므로 한 번 더 친다.
    """
    for i, line in enumerate(lines):
        for attempt in range(3):
            page.keyboard.insert_text(line)
            page.wait_for_timeout(INSERT_SETTLE_MS)
            got = last_body_paragraph(frame)
            if norm(got) == norm(line):
                break
            if norm(got) == "":
                continue            # 통째로 씹혔다. 그대로 다시 넣는다.
            raise Failed(
                "문단이 예상과 다르게 들어갔다. 중복 입력 위험이 있어 멈춘다."
                + " 기대=" + repr(line[:40]) + " 실제=" + repr(got[:40])
            )
        else:
            raise Failed("문단을 세 번 넣었는데 반영되지 않았다: " + repr(line[:40]))

        if i < len(lines) - 1:
            page.keyboard.press("Enter")
            page.wait_for_timeout(ENTER_SETTLE_MS)
            if norm(last_body_paragraph(frame)) != "":
                page.keyboard.press("Enter")   # Enter 가 씹혔다
                page.wait_for_timeout(ENTER_SETTLE_MS)


def set_title(frame, page, title):
    """제목 입력. 바깥 div 를 클릭하면 포커스가 안 잡혀 insert_text 가 증발한다."""
    inner = frame.locator(TITLE_INNER).first
    inner.wait_for(state="visible", timeout=15000)
    inner.click()
    page.wait_for_timeout(FOCUS_SETTLE_MS)
    page.keyboard.insert_text(title)
    page.wait_for_timeout(200)

    got = frame.locator(TITLE_TEXT).inner_text().strip()
    if title.strip() not in got:
        raise Failed(
            f"제목이 들어가지 않았다. 기대 {title!r}, 실제 {got!r}\n"
            "  포커스 타이밍 문제다. 다시 실행하면 대개 된다."
        )
    log(f"  제목 확인됨: {got[:40]}")


def upload_image(frame, page, path):
    caret_to_end(frame, page)
    pop = describe_popup(frame)
    if pop:
        raise Failed("사진 버튼을 누르기 전에 팝업이 떠 있다." + pop)
    before = frame.locator(IMAGE_COMPONENT).count()
    try:
        with page.expect_file_chooser(timeout=15000) as fc:
            frame.click(IMAGE_BUTTON, timeout=10000)
    except Exception as e:
        raise Failed(
            "사진 버튼 클릭이 실패했다: " + str(e).strip().splitlines()[0]
            + describe_popup(frame)
        )
    fc.value.set_files([str(Path(path).resolve())])
    for _ in range(60):
        if frame.locator(IMAGE_COMPONENT).count() > before:
            page.wait_for_timeout(600)
            return
        page.wait_for_timeout(500)
    raise Failed(f"이미지 업로드가 끝나지 않았다: {path}")


def visible_popup(frame):
    """화면에 떠 있는 SmartEditor 팝업을 읽는다. 없으면 None.

    팝업이 떠 있으면 툴바 클릭이 전부 가로채여 Playwright 가 30초 재시도하다
    죽는데, 에러에는 '무엇이 가로챘는지'만 나오고 팝업이 뭐라고 하는지는
    안 나온다. 그래서 직접 읽어 메시지에 실어 준다.
    """
    return frame.evaluate(
        """() => {
            for (const el of document.querySelectorAll('.se-popup')) {
                const shown = !!(el.offsetWidth || el.offsetHeight
                                 || el.getClientRects().length);
                if (!shown) continue;
                return {
                    name: el.getAttribute('data-name'),
                    text: (el.innerText || '').trim().slice(0, 300),
                    buttons: Array.from(el.querySelectorAll('button'))
                        .filter(b => b.offsetWidth || b.offsetHeight)
                        .map(b => (b.innerText || '').trim().slice(0, 30)),
                };
            }
            return null;
        }"""
    )


def describe_popup(frame):
    pop = visible_popup(frame)
    if not pop:
        return ""
    return (" 떠 있는 팝업[" + str(pop.get("name")) + "] "
            + repr(pop.get("text")) + " 버튼=" + repr(pop.get("buttons")))


def dismiss_recovery(frame, page):
    """'작성 중인 글이 있습니다' 를 취소로 닫는다. 없으면 조용히 지나간다."""
    try:
        frame.click(RECOVERY_CANCEL, timeout=3000)
        log("  복구 대화상자 닫음")
        page.wait_for_timeout(400)
    except Exception:
        pass


def close_help_panel(frame, page):
    try:
        frame.click(HELP_CLOSE, timeout=2000)
        page.wait_for_timeout(200)
    except Exception:
        pass


def assert_blank(frame):
    """빈 글인지 확인한다.

    복구 대화상자를 못 닫았으면 이전 초안 위에 그대로 덮어써진다. 검증된 도구가
    같은 이유로 이 확인을 본문 입력 전에 강제한다. 빈 글 기준(실측): 제목이
    플레이스홀더 '제목', 본문 문단 1개, 이미지 0개.
    """
    title = frame.locator(TITLE_TEXT).inner_text().strip()
    n_para = frame.locator(BODY_INNER).count()
    n_img = frame.locator(IMAGE_COMPONENT).count()
    if n_img != 0 or n_para > 3 or title not in ("제목", ""):
        raise Failed(
            f"빈 글이 아니다 (제목={title[:20]!r}, 본문 문단={n_para}, 이미지={n_img}). "
            "복구 대화상자가 안 닫혔거나 이미 쓰던 글이다. 덮어쓰지 않고 멈춘다."
        )
    log(f"  빈 글 확인: 문단 {n_para}개 · 이미지 {n_img}장")


def set_body_align(frame, page, align):
    """본문 전체를 한 번에 정렬한다. 기본은 가운데.

    문단마다 거는 대신 **다 채운 뒤 전체 선택해서 한 번만** 누른다. 정렬은 글자
    크기처럼 '현재 서식'으로 이어지는 성질이 있어서, 중간에 걸면 이후 입력과
    이미지 삽입에 계속 얽힌다. 마지막에 한 번이면 그 성질을 걱정할 일이 없다.

    전체 선택을 남겨 두면 다음 입력이 문서를 통째로 덮어쓰므로 반드시 푼다.
    """
    if align == "keep":
        return
    frame.locator(BODY_INNER).last.click()
    page.wait_for_timeout(FOCUS_SETTLE_MS)
    page.keyboard.press("Control+a")
    page.wait_for_timeout(300)
    frame.click(ALIGN_DROPDOWN)
    page.wait_for_timeout(300)
    frame.click(ALIGN_OPTION[align])
    page.wait_for_timeout(500)
    page.keyboard.press("ArrowRight")      # 선택 해제. 남겨 두면 위험하다.
    page.wait_for_timeout(200)

    want = "se-text-paragraph-align-" + align
    bad = frame.evaluate(
        """(a) => Array.from(document.querySelectorAll(
               '.se-component.se-text .se-text-paragraph'))
             .filter(p => !p.classList.contains(a)).length""",
        want,
    )
    if bad:
        raise Failed(f"정렬이 문단 {bad}개에 적용되지 않았다 (기대 {align})")
    log(f"  본문 정렬 {align} 적용 확인")


def set_font_size(frame, page, pt):
    """선택 영역의 글자 크기를 바꾼다.

    캐럿이 제목칸에 있으면 드롭다운이 제목용 3종만 띄운다. 그 상태로 본문 기준
    인덱스를 누르면 엉뚱한 크기가 잡히거나 타임아웃난다 — 옵션 개수를 세서 다르면
    조용히 넘어가지 않고 멈춘다.
    """
    frame.click(FONT_SIZE_BUTTON)
    page.wait_for_timeout(300)
    opts = frame.locator(FONT_SIZE_OPTION)
    if opts.count() != len(FONT_SIZES):
        page.keyboard.press("Escape")
        raise Failed(
            f"글자 크기 옵션이 {opts.count()}개다 (본문 기준 {len(FONT_SIZES)}개). "
            "캐럿이 본문에 있지 않다."
        )
    opts.nth(FONT_SIZES.index(pt)).click()
    page.wait_for_timeout(300)


def insert_credits(frame, page, lines):
    """글 끝에 사진 출처를 붙인다. 본문(가운데)과 달리 왼쪽 · 최소 크기다.

    **이미 넣은 문단을 골라 서식을 바꾸는 방식은 쓰지 않는다.** 그러려면 첫 줄
    맨 앞부터 마지막 줄 맨 끝까지 정확히 선택해야 하는데(글자 크기는 문단이
    아니라 글자 단위라 부분 선택이면 일부만 작아진다), 마지막 줄의 오른쪽 끝은
    오른쪽 사이드바(.se-sidebar-interaction)에 덮여 클릭이 가로채인다.
    실물에서 그렇게 죽었다 (2026-09-08).

    대신 본문 정렬을 끝낸 뒤 **빈 문단을 하나 만들고 거기에 서식을 먼저 걸어**
    놓고 쓴다. 정렬·글자 크기는 '현재 서식'으로 이어지므로 그 뒤에 넣는 줄이
    전부 그 서식으로 들어간다. 선택할 일이 없다.
    """
    log(f"  사진 출처 {len(lines)}줄 ({CREDIT_ALIGN} · {CREDIT_PT}pt)")
    page.keyboard.press("Enter")
    page.wait_for_timeout(ENTER_SETTLE_MS)
    frame.click(ALIGN_DROPDOWN)
    page.wait_for_timeout(300)
    frame.click(ALIGN_OPTION[CREDIT_ALIGN])
    page.wait_for_timeout(300)
    set_font_size(frame, page, CREDIT_PT)
    insert_paragraphs(frame, page, lines)

    bad = frame.evaluate(
        """([n, a, fs]) => {
             const ps = Array.from(document.querySelectorAll(
                 '.se-component.se-text .se-text-paragraph')).slice(-n);
             return ps.filter(p => !p.classList.contains(a)
                 || !p.querySelector('span[class*="' + fs + '"]')).length;
           }""",
        [len(lines), "se-text-paragraph-align-" + CREDIT_ALIGN, "se-fs%d" % CREDIT_PT],
    )
    if bad:
        raise Failed(f"출처 서식이 {bad}줄에 적용되지 않았다")
    log(f"  출처 서식 확인: {CREDIT_ALIGN} · {CREDIT_PT}pt")


def close_material_panel(frame, page):
    """글감 검색 결과 패널을 닫는다. Escape 는 듣지 않고 전용 닫기 버튼만 먹는다."""
    for _ in range(3):
        if not frame.evaluate("(s) => !!document.querySelector(s)", MATERIAL_PANEL):
            return
        btn = frame.locator(MATERIAL_CLOSE).first
        if btn.count() == 0 or not btn.is_visible():
            return
        btn.click()
        page.wait_for_timeout(700)


def apply_link(frame, page, text, url):
    """`text` 와 정확히 같은 문단 전체에 링크를 건다.

    여기서는 트리플클릭이 **맞는 용법**이다. 선택에 링크를 거는 것이고 뒤에
    insert_text 를 하지 않으므로 선택을 덮어쓸 위험이 없다. Shift+Home 으로는
    선택이 잡히지 않았다 (실물, 2026-09-08).

    확인할 때 <a> 를 찾으면 안 된다. **에디터 안에서 링크는 <a> 가 아니라
    span.se-link 의 data-href 로 들어간다.** <a> 는 발행된 뒤에야 생긴다.
    이걸 몰라서 '안 걸렸다'고 오판하고 여러 번 다시 걸었다.
    """
    close_material_panel(frame, page)
    idx = frame.evaluate(
        """([sel, t]) => Array.from(document.querySelectorAll(sel))
                          .findIndex(p => p.textContent.trim() === t)""",
        [BODY_INNER, text],
    )
    if idx < 0:
        raise Failed(f"링크를 걸 문단을 못 찾았다: {text!r}")
    target = frame.locator(BODY_INNER).nth(idx)
    target.scroll_into_view_if_needed()
    page.wait_for_timeout(300)
    target.click(click_count=3)
    page.wait_for_timeout(500)

    box = frame.locator(LINK_INPUT).first
    if box.count() == 0 or not box.is_visible():
        frame.click(LINK_BUTTON)
        page.wait_for_timeout(1200)
        box = frame.locator(LINK_INPUT).first
    if not box.is_visible():
        raise Failed("링크 입력칸이 열리지 않았다")
    box.click()
    page.wait_for_timeout(250)
    box.fill("")
    page.keyboard.type(url, delay=3)
    page.wait_for_timeout(500)

    def applied():
        return frame.evaluate(
            """([t, u]) => Array.from(document.querySelectorAll('span.se-link'))
                 .some(s => s.textContent.trim() === t
                            && (s.getAttribute('data-href') || '').includes(u))""",
            [text, url.split("?")[0]],
        )

    # 확정 버튼은 오른쪽 사이드바 패널 헤더에 가려 클릭이 가로채이는 경우가 있다.
    # Enter 로 먼저 시도하고, 그래도 안 되면 히트테스트를 건너뛰는 JS 클릭으로 누른다.
    page.keyboard.press("Enter")
    page.wait_for_timeout(1500)
    close_material_panel(frame, page)
    if not applied():
        frame.evaluate(
            """(sel) => { const b = document.querySelector(sel); if (b) b.click(); }""",
            LINK_APPLY,
        )
        page.wait_for_timeout(1800)
        close_material_panel(frame, page)
    if not applied():
        raise Failed(f"링크가 걸리지 않았다: {text!r} -> {url[:60]}")
    log(f"  링크 확인: {text} -> {url[:52]}...")


def fill_tags(frame, page, tags):
    """발행 설정 패널을 열고 태그를 넣는다. **발행 버튼은 누르지 않는다.**

    패널을 여는 버튼(publish_btn)과 진짜 발행 버튼(confirm_btn)이 둘 다 '발행'
    이라고 쓰여 있다. 텍스트로 찾으면 발행이 눌린다 — 반드시 클래스로 가른다.
    """
    if not tags:
        return
    opener = frame.locator(PUBLISH_PANEL_BUTTON).first
    if opener.count() == 0 or not opener.is_visible():
        raise Failed("발행 설정 패널을 여는 버튼을 못 찾았다")
    opener.click()
    page.wait_for_timeout(1800)

    box = frame.locator(TAG_INPUT).first
    if box.count() == 0 or not box.is_visible():
        raise Failed("태그 입력칸을 못 찾았다")
    box.click()
    page.wait_for_timeout(400)
    for t in tags:
        page.keyboard.type(t, delay=12)
        page.wait_for_timeout(200)
        page.keyboard.press("Enter")
        page.wait_for_timeout(400)

    got = frame.evaluate(
        """(sel) => Array.from(document.querySelectorAll(sel))
                        .map(e => (e.getAttribute('aria-label') || '').trim())
                        .filter(Boolean)""",
        TAG_ITEMS,
    )
    missing = [t for t in tags if t not in got]
    if missing:
        raise Failed(f"태그가 들어가지 않았다: {missing} (현재 {got})")
    log(f"  태그 {len(tags)}개 입력 확인: {' '.join(got)}")


def ensure_photos(post_dir, allow_fetch=True):
    """photos.json 은 있는데 사진이 아직 없으면 받아 둔다.

    **에디터를 건드리기 전에** 부른다. 받다가 실패하면 반쯤 채워진 글이 남는 것보다
    아무것도 안 한 상태로 죽는 편이 낫다.

    photos.json 자체는 사람이(또는 Claude 가) 커먼즈에서 후보를 보고 고른 결과다.
    그건 자동화 대상이 아니다 — 여기서는 이미 고른 것을 받아 오기만 한다.
    """
    if not (post_dir / "photos.json").exists():
        return
    credits = post_dir / "photo-credits.json"
    need = True
    if credits.exists():
        try:
            manifest = json.loads(credits.read_text(encoding="utf-8"))
            need = any(not (post_dir / m["file"]).exists() for m in manifest)
        except Exception:
            need = True
    if not need:
        return
    if not allow_fetch:
        log("  사진이 아직 없다. --no-fetch 라 받지 않는다 (자리 표시만 들어간다)")
        return
    log("사진을 커먼즈에서 받는다 (photos.json 은 있는데 아직 안 받았다)")
    fetch_commons_photos(post_dir)


def load_photos(post_dir):
    """photo-credits.json 을 읽어 (hint -> 파일 절대경로, 매니페스트) 를 돌려준다.

    이 파일이 없으면 사진 없이 예전처럼 '[여기에 사진: ...]' 자리 표시만 넣는다.
    publisher/fetch_photos.py 가 커먼즈에서 라이선스를 확인하고 만든다.
    """
    f = post_dir / "photo-credits.json"
    if not f.exists():
        return {}, []
    manifest = json.loads(f.read_text(encoding="utf-8"))
    found = {}
    for m in manifest:
        path = post_dir / m["file"]
        if path.exists():
            found[m["hint"]] = path
    return found, manifest


# --- 본체 --------------------------------------------------------------------

def fill_post(page, post, post_dir, align=ALIGN_DEFAULT, tags=True):
    frame = page.frame(name=FRAME_NAME)
    if frame is None:
        raise Failed(
            f"iframe '{FRAME_NAME}' 을 못 찾았다. 글쓰기 화면이 맞는지 확인할 것.\n"
            f"  현재 URL: {page.url}"
        )
    dismiss_recovery(frame, page)
    close_help_panel(frame, page)
    assert_blank(frame)

    log("제목 입력")
    set_title(frame, page, post["title"])

    photos, manifest = load_photos(post_dir)
    if photos:
        log(f"  사진 {len(photos)}장 확인 (photo-credits.json)")

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
            # 원문 <a> 는 평문으로 둔다 — 의도된 동작이다(docstring '링크' 참조).
            # 그래도 무엇이 그렇게 됐는지는 보이게 남긴다.
            for href in re.findall(r'<a[^>]+href="([^"]+)"', b["content"]):
                log(f"{head} 원문 <a> 는 평문으로 둠(의도): {href[:55]}")
        elif b["type"] == "card":
            f = post_dir / b.get("file", f"images/card-{b['card']}.png")
            if not f.exists():
                raise Failed(f"카드 이미지가 없다: {f}")
            log(f"{head} 카드 업로드 {f.name}")
            upload_image(frame, page, f)
            # 업로드 후 에디터가 포커스를 옮기므로 여기서만 커서를 되돌린다
            caret_to_end(frame, page)
        elif b["type"] == "photo":
            f = photos.get(b["hint"])
            if f is None:
                log(f"{head} 사진 자리 (파일 없음)")
                insert_paragraphs(frame, page, [f"[여기에 사진: {b['hint']}]"])
            else:
                log(f"{head} 사진 업로드 {f.name}")
                upload_image(frame, page, f)
                caret_to_end(frame, page)
        # 텍스트 삽입 뒤에는 caret 이 이미 끝에 있다. 건드리지 않는다.
        if i < len(blocks):
            page.keyboard.press("Enter")

    booking = post.get("booking_url")
    if booking:
        page.keyboard.press("Enter")
        page.wait_for_timeout(ENTER_SETTLE_MS)
        log("  예약 링크 한 줄")
        insert_paragraphs(frame, page, [BOOKING_LINK_TEXT])
        apply_link(frame, page, BOOKING_LINK_TEXT, booking)

    used = [m for m in manifest if m["hint"] in photos]
    # 본문 정렬을 먼저 끝내고 그 뒤에 출처를 붙인다. 순서를 바꾸면 전체 선택
    # 가운데정렬이 출처 서식까지 덮어쓴다.
    set_body_align(frame, page, align)
    if used:
        # CC BY / BY-SA 는 저작자 표시가 의무다. 사진을 실제로 넣었을 때만 붙인다.
        insert_credits(frame, page, credit_lines(used))
    verify_whole(frame, post, photos, used)
    # 태그는 맨 마지막에. 발행 설정 패널이 열리면 본문을 가려서 대조를 못 한다.
    if tags:
        fill_tags(frame, page, post.get("tags") or [])
    return photos, used


def verify_whole(frame, post, photos, used):
    # 주의: verify_whole 은 post 를 읽어 booking 줄까지 기대 목록에 넣는다.
    """전부 넣고 나서 통째로 대조한다.

    조각마다 확인해도 '순서가 뒤집혔다'나 '중간 문단이 통째로 사라졌다'는
    안 잡힌다. 실제로 커서를 잘못 옮겨 그렇게 깨지는 것을 확인했으므로,
    마지막에 원문 문단이 모두 살아 있는지와 순서가 맞는지를 본다.
    """
    body = body_text(frame)
    want = []
    for b in post["blocks"]:
        if b["type"] == "html":
            want += html_to_paragraphs(b["content"])
        elif b["type"] == "photo" and b["hint"] not in photos:
            want.append(f"[여기에 사진: {b['hint']}]")
    if post.get("booking_url"):
        want.append(BOOKING_LINK_TEXT)
    if used:
        want += credit_lines(used)

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

    n_img = sum(1 for b in post["blocks"] if b["type"] == "card") + len(used)
    got_img = frame.locator(IMAGE_COMPONENT).count()
    if got_img < n_img:
        raise Failed(f"이미지가 {n_img}장이어야 하는데 {got_img}장이다")

    log(f"  검증 통과: 문단 {len(want)}개 · 이미지 {got_img}장 · 순서 일치")


def find_editor_page(ctx, match):
    """에디터가 실제로 살아 있는 탭을 고른다.

    URL 부분일치로 고르면 안 된다. 네이버 로그인 페이지의 url= 파라미터에
    'blog.naver.com' 이 그대로 들어 있어서 로그인 탭이 먼저 잡힌다 —
    실물에서 그렇게 mainFrame 을 못 찾고 죽었다 (2026-09-08).

    제목칸(.se-title-text)만 보면 안 된다. **발행된 글을 읽는 화면에도 같은
    클래스가 그대로 있다.** 편집 가능한 화면에만 있는 툴바까지 봐야 가른다.
    """
    for p in ctx.pages:
        if match and match not in p.url:
            continue
        f = p.frame(name=FRAME_NAME)
        if f is None:
            continue
        try:
            if f.locator(TITLE_TEXT).count() > 0 and f.locator(IMAGE_BUTTON).count() > 0:
                return p
        except Exception:
            continue
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="post.json 을 네이버 에디터에 채운다")
    ap.add_argument("post_dir", help="posts/<슬러그> 경로")
    ap.add_argument("--cdp", default=CDP_URL, help=f"CDP 엔드포인트 (기본 {CDP_URL})")
    ap.add_argument("--blog-id", help="글쓰기 페이지가 안 열려 있을 때 직접 연다")
    ap.add_argument("--match", default="blog.naver.com",
                    help="채울 탭을 고를 URL 부분 문자열 (기본 blog.naver.com)")
    ap.add_argument("--align", default=ALIGN_DEFAULT,
                    choices=["center", "left", "right", "justify", "keep"],
                    help=f"본문 정렬 (기본 {ALIGN_DEFAULT}). keep 은 손대지 않는다")
    ap.add_argument("--no-fetch", action="store_true",
                    help="사진을 자동으로 받지 않는다 (photos.json 이 있어도)")
    ap.add_argument("--no-tags", action="store_true",
                    help="태그를 넣지 않는다 (발행 설정 패널을 열지 않음)")
    ap.add_argument("--fresh", action="store_true",
                    help="채우기 전에 글쓰기 화면을 다시 열어 빈 글로 만든다 "
                         "(쓰던 내용은 버려진다)")
    args = ap.parse_args(argv)

    post_dir = Path(args.post_dir).resolve()
    pj = post_dir / "post.json"
    if not pj.exists():
        print(f"post.json 이 없다: {pj}", file=sys.stderr)
        return 1
    post = json.loads(pj.read_text(encoding="utf-8"))

    # 브라우저를 건드리기 전에 사진부터 확보한다.
    ensure_photos(post_dir, allow_fetch=not args.no_fetch)

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
        page = find_editor_page(ctx, args.match)
        if page is None:
            if not args.blog_id:
                print("에디터가 열린 탭이 없다. 글쓰기 화면을 열거나 --blog-id 를 줄 것.", file=sys.stderr)
                print("  열린 탭: " + ", ".join(p.url[:70] for p in ctx.pages),
                      file=sys.stderr)
                return 1
            page = ctx.new_page()
            page.goto(WRITE_URL.format(blog_id=args.blog_id),
                      wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
        page.bring_to_front()
        log(f"대상 탭: {page.url}")

        if args.fresh:
            from urllib.parse import urlsplit
            blog_id = args.blog_id or urlsplit(page.url).path.strip("/")
            if not blog_id:
                print("--fresh 인데 블로그 ID 를 알아낼 수 없다. --blog-id 를 줄 것.",
                      file=sys.stderr)
                return 1
            # 찾은 탭이 글쓰기 화면이 아니면(예: 발행글 수정 중) 거기서 이동하면
            # 사용자가 하던 작업이 날아간다. 그럴 땐 새 탭에서 연다.
            if "Redirect=Write" not in page.url:
                log("찾은 탭이 글쓰기 화면이 아니다. 새 탭에서 연다.")
                page = ctx.new_page()
            log(f"글쓰기 화면 새로 열기: {blog_id}")
            page.goto(WRITE_URL.format(blog_id=blog_id), wait_until="load")
            page.wait_for_timeout(2500)
            page.bring_to_front()

        try:
            photos, used = fill_post(page, post, post_dir, args.align,
                                     tags=not args.no_tags)
        except Failed as e:
            print(f"\n중단: {e}", file=sys.stderr)
            return 1
        # browser.close() 를 부르면 사용자 크롬이 진짜로 닫힌다. 부르지 않는다.

    print()
    log("채우기 완료. 저장도 발행도 하지 않았다.")
    tag_list = " ".join(post.get("tags", []))
    if args.no_tags:
        print(f"  태그(직접 입력하세요): {tag_list}")
    else:
        print(f"  태그 입력됨: {tag_list}")
    if used:
        print(f"  사진 {len(used)}장을 넣고 글 끝에 출처를 붙였다.")
        print("  출처 문단은 지우지 말 것 — CC BY / BY-SA 의 저작자 표시 의무다.")
    empty = [h for h in post.get("photo_hints", []) if h not in photos]
    for h in empty:
        print(f"  아직 빈 사진 자리: {h}")
    if empty:
        print("  남은 자리를 채우고 한 번 읽어본 뒤 직접 발행하세요.")
    else:
        print("  한 번 읽어본 뒤 예약 시각을 잡고 직접 발행하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
