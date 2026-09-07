"""posts/ 의 최신 글을 네이버 블로그 **임시저장**까지 올린다.

사용자님 Windows PC 에서 돈다. GitHub Actions 에서는 돌릴 수 없다 — 네이버가
해외/데이터센터 IP 로그인을 기기 인증으로 막기 때문이다.

    python publisher\\naver_draft.py --login      # 최초 1회, 직접 로그인
    python publisher\\naver_draft.py --dry-run    # 네이버 접속 없이 대상 확인
    python publisher\\naver_draft.py              # 매일 실행 (작업 스케줄러)
    python publisher\\naver_draft.py --keep-open  # 실패해도 브라우저 유지

**발행하지 않는다.** 저장(임시저장)까지만 하고, 발행 버튼은 사람이 누른다.
assert_not_publish() 가 '발행/공개/게시' 가 붙은 버튼 클릭을 코드 차원에서 막는다.
"""
import argparse
import json
import re
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# 파일명이 naver_selectors 인 이유: publisher/selectors.py 로 두면 표준 라이브러리의
# selectors 모듈을 가려서 subprocess import 가 깨진다 (sys.path 맨 앞에 이 디렉터리를
# 넣기 때문). 이름을 되돌리지 말 것.
import naver_selectors as SEL

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DEFAULT_CONFIG = {
    "blog_id": "",
    "profile_dir": "profile",
    "state_file": "state.json",
    "posts_dir": "../posts",
    "diagnostics_dir": "diagnostics",
    "slow_mo_ms": 120,
    "timeout_ms": 30000,
    "with_tags": False,
}

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_NEEDS_LOGIN = 2

WRITE_URL = "https://blog.naver.com/{blog_id}?Redirect=Write"
LOGIN_URL = "https://nid.naver.com/nidlogin.login"


class PublishBlocked(RuntimeError):
    """발행 버튼을 누르려 했다. 절대 일어나면 안 된다."""


class StepFailed(RuntimeError):
    """어느 단계에서 무엇을 못 찾았는지 분명히 알린다."""


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# --- 설정 / 상태 -------------------------------------------------------------

def load_config():
    cfg = dict(DEFAULT_CONFIG)
    for name in ("config.json", "config.local.json"):
        p = HERE / name
        if p.exists():
            cfg.update(json.loads(p.read_text(encoding="utf-8")))
    if not cfg["blog_id"]:
        raise SystemExit(
            "publisher/config.json 에 blog_id 를 넣어야 한다.\n"
            "  config.example.json 을 복사해서 쓴다."
        )
    return cfg


def state_path(cfg):
    return HERE / cfg["state_file"]


def load_state(cfg):
    p = state_path(cfg)
    if not p.exists():
        return {"version": 1, "last_slug": "", "history": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": 1, "last_slug": "", "history": []}


def save_state(cfg, state):
    state["history"] = state.get("history", [])[-60:]
    state_path(cfg).write_text(
        json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )


def find_latest_post(cfg):
    """가장 최신 글 **하나만** 고른다.

    디렉터리 이름의 날짜 접두사로 고른다 — mtime 은 git checkout 이 전부
    현재 시각으로 만들어 버려서 쓸 수 없다.
    PC 가 며칠 꺼져 있었어도 밀린 글을 몰아 올리지 않는다. 이틀 지난 가격 글은
    어차피 틀린 글이다.
    """
    posts = (HERE / cfg["posts_dir"]).resolve()
    if not posts.exists():
        return None
    cands = [
        d for d in posts.iterdir()
        if d.is_dir() and not d.name.startswith("_")
        and re.match(r"^\d{4}-\d{2}-\d{2}-", d.name)
        and (d / "post.json").exists()
    ]
    if not cands:
        return None
    return max(cands, key=lambda d: d.name)


# --- 안전장치 ----------------------------------------------------------------

def assert_not_publish(locator, label=""):
    """접근성 이름에 발행/공개/게시가 들어간 요소는 절대 클릭하지 않는다."""
    try:
        text = (locator.inner_text(timeout=1500) or "").strip()
    except Exception:
        text = ""
    for bad in SEL.FORBIDDEN_CLICK_TEXT:
        if bad in text:
            raise PublishBlocked(
                f"'{bad}' 가 붙은 버튼을 누르려 했다 ({label}: {text!r}). "
                "이 스크립트는 임시저장까지만 한다."
            )
    return locator


def first_visible(scope, candidates, label, timeout=6000):
    """후보 셀렉터를 차례로 시도해 처음 보이는 것을 돌려준다."""
    for sel in candidates:
        loc = scope.locator(sel).first
        try:
            loc.wait_for(state="visible", timeout=timeout)
            return loc
        except Exception:
            continue
    raise StepFailed(
        f"{label}: 후보 셀렉터를 하나도 못 찾았다 -> {candidates}\n"
        "  publisher/naver_selectors.py 를 실제 DOM 에 맞게 고칠 것."
    )


# --- 브라우저 ----------------------------------------------------------------

def open_context(pw, cfg, headless=False):
    profile = HERE / cfg["profile_dir"]
    profile.mkdir(parents=True, exist_ok=True)
    kwargs = dict(
        user_data_dir=str(profile),
        headless=headless,           # 헤드리스는 탐지된다. 기본은 헤드풀.
        locale="ko-KR",
        timezone_id="Asia/Seoul",
        viewport={"width": 1440, "height": 960},
        args=["--disable-blink-features=AutomationControlled"],
        slow_mo=cfg["slow_mo_ms"],
        # 본문을 서식째 넣으려면 클립보드 쓰기 권한이 필요하다
        permissions=["clipboard-read", "clipboard-write"],
    )
    try:
        return pw.chromium.launch_persistent_context(channel="chrome", **kwargs)
    except Exception:
        # 실제 Chrome 이 없으면 번들 크로미움으로 떨어진다
        return pw.chromium.launch_persistent_context(**kwargs)


def editor_frame(page, cfg):
    page.wait_for_selector(SEL.EDITOR_FRAME, timeout=cfg["timeout_ms"])
    frame = page.frame_locator(SEL.EDITOR_FRAME)
    for sel in SEL.LOGGED_IN_HINT:
        try:
            frame.locator(sel).first.wait_for(state="visible", timeout=8000)
            return frame
        except Exception:
            continue
    raise StepFailed(
        "에디터 본문 영역을 못 찾았다. 로그인이 풀렸거나 DOM 이 바뀌었다.\n"
        "  python publisher/naver_draft.py --login 으로 다시 로그인해 볼 것."
    )


def dismiss_popups(page, frame):
    """시작 팝업은 DOM 레이어일 수도, 네이티브 confirm 일 수도 있어 둘 다 대비한다."""
    for sel in SEL.POPUP_DISMISS + SEL.HELP_CLOSE:
        try:
            loc = frame.locator(sel).first
            if loc.is_visible(timeout=1200):
                assert_not_publish(loc, "팝업 닫기").click(timeout=3000)
                log(f"  팝업 닫음: {sel}")
                page.wait_for_timeout(400)
        except Exception:
            continue


# --- 입력 --------------------------------------------------------------------

def paste_html(page, frame, chunk_html, chunk_text, cfg):
    """클립보드에 text/html 을 실어 Ctrl+V. 서식을 살리는 유일한 방법이다.

    실패하면 평문 입력으로 떨어진다 (서식은 잃되 글은 남는다).
    """
    body = first_visible(frame, SEL.BODY, "본문 영역", cfg["timeout_ms"])
    body.click()
    try:
        page.evaluate(
            """async ([h, t]) => {
                await navigator.clipboard.write([new ClipboardItem({
                    'text/html':  new Blob([h], {type: 'text/html'}),
                    'text/plain': new Blob([t], {type: 'text/plain'}),
                })]);
            }""",
            [chunk_html, chunk_text],
        )
        page.keyboard.press("Control+V")
        page.wait_for_timeout(500)
        return True
    except Exception as e:
        log(f"  클립보드 붙여넣기 실패 -> 평문 입력으로 대체 ({e})")
        for line in chunk_text.split("\n"):
            page.keyboard.insert_text(line)
            page.keyboard.press("Enter")
        return False


def upload_image(page, frame, path, cfg):
    before = frame.locator(SEL.IMAGE_COMPONENT).count()
    btn = first_visible(frame, SEL.IMAGE_BUTTON, "사진 버튼", cfg["timeout_ms"])
    with page.expect_file_chooser(timeout=cfg["timeout_ms"]) as fc:
        assert_not_publish(btn, "사진 버튼").click()
    fc.value.set_files(str(path))
    # pstatic 업로드가 끝나 컴포넌트가 늘어날 때까지 기다린다
    for _ in range(120):
        if frame.locator(SEL.IMAGE_COMPONENT).count() > before:
            page.wait_for_timeout(600)
            return
        page.wait_for_timeout(500)
    raise StepFailed(f"이미지 업로드가 끝나지 않았다: {path.name}")


def insert_blocks(page, frame, post, post_dir, cfg):
    """블록을 순서대로 넣는다. 이미지는 클립보드로 못 넣어서 업로드로 처리한다."""
    from html import escape

    for i, b in enumerate(post["blocks"], 1):
        if b["type"] == "html":
            text = _strip_tags(b["content"])
            log(f"  [{i}/{len(post['blocks'])}] 본문 조각 {len(b['content'])}자")
            paste_html(page, frame, b["content"], text, cfg)
        elif b["type"] == "card":
            f = post_dir / b.get("file", f"images/card-{b['card']}.png")
            if not f.exists():
                raise StepFailed(f"카드 이미지가 없다: {f}")
            log(f"  [{i}/{len(post['blocks'])}] 카드 업로드 {f.name}")
            upload_image(page, frame, f, cfg)
        elif b["type"] == "photo":
            note = f"[여기에 사진: {b['hint']}]"
            log(f"  [{i}/{len(post['blocks'])}] 사진 자리 표시")
            paste_html(page, frame,
                       f"<p><strong>{escape(note)}</strong></p>", note, cfg)
        page.keyboard.press("Control+End")


def _strip_tags(html_str):
    t = re.sub(r"<br\s*/?>", "\n", html_str)
    t = re.sub(r"</(p|h3|li|tr|blockquote)>", "\n", t)
    t = re.sub(r"<[^>]+>", "", t)
    from html import unescape
    return unescape(t).strip()


def save_draft(page, frame, cfg):
    """임시저장. 발행 버튼과 헷갈리지 않도록 텍스트를 두 번 확인한다."""
    btn = first_visible(frame, SEL.SAVE, "저장 버튼", cfg["timeout_ms"])
    label = (btn.inner_text(timeout=2000) or "").strip()
    for bad in SEL.FORBIDDEN_CLICK_TEXT:
        if bad in label:
            raise PublishBlocked(f"저장 버튼인 줄 알았는데 '{label}' 이다. 중단한다.")
    if "저장" not in label:
        raise StepFailed(f"저장 버튼 문구가 예상과 다르다: {label!r}")
    btn.click()
    page.wait_for_timeout(1500)
    for sel in SEL.SAVE_CONFIRM:
        try:
            if frame.locator(sel).first.is_visible(timeout=2500):
                log(f"  임시저장 확인: {sel}")
                return True
        except Exception:
            continue
    log("  경고: 임시저장 확인 표시를 못 찾았다. 네이버에서 직접 확인할 것.")
    return False


# --- 진단 --------------------------------------------------------------------

def dump_diagnostics(page, cfg, label):
    d = HERE / cfg["diagnostics_dir"] / f"{datetime.now():%Y%m%d-%H%M%S}-{label}"
    d.mkdir(parents=True, exist_ok=True)
    try:
        page.screenshot(path=str(d / "page.png"), full_page=True)
    except Exception:
        pass
    try:
        (d / "page.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass
    (d / "traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
    log(f"진단 자료: {d}")
    return d


# --- 모드 --------------------------------------------------------------------

def do_login(cfg):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        ctx = open_context(pw, cfg)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(LOGIN_URL)
        print()
        print("=" * 58)
        print(" 브라우저에서 직접 로그인하세요 (2단계 인증 포함).")
        print(" 아이디/비밀번호는 이 스크립트에 저장되지 않습니다.")
        print(" 로그인이 끝나면 여기서 Enter 를 누르세요.")
        print("=" * 58)
        input()
        page.goto(WRITE_URL.format(blog_id=cfg["blog_id"]))
        try:
            editor_frame(page, cfg)
            log("로그인 확인됨. 세션이 프로필에 저장되었다.")
            code = EXIT_OK
        except StepFailed as e:
            log(f"로그인 확인 실패: {e}")
            code = EXIT_NEEDS_LOGIN
        ctx.close()
    return code


def do_draft(cfg, args):
    from playwright.sync_api import sync_playwright

    post_dir = find_latest_post(cfg)
    if not post_dir:
        log("posts/ 에 올릴 글이 없다.")
        return EXIT_OK

    post = json.loads((post_dir / "post.json").read_text(encoding="utf-8"))
    state = load_state(cfg)
    if post["slug"] == state.get("last_slug") and not args.force:
        log(f"이미 처리한 글이다: {post['slug']} (--force 로 다시 올릴 수 있다)")
        return EXIT_OK

    log(f"대상: {post['slug']}")
    log(f"  제목: {post['title']}")
    log(f"  블록 {len(post['blocks'])}개 · 사진 자리 {len(post.get('photo_hints', []))}곳")
    if args.dry_run:
        log("--dry-run 이므로 네이버에 접속하지 않는다.")
        for h in post.get("photo_hints", []):
            log(f"  사진 자리: {h}")
        return EXIT_OK

    with sync_playwright() as pw:
        ctx = open_context(pw, cfg)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.on("dialog", lambda d: d.dismiss())   # 네이티브 confirm 대비
        try:
            page.goto(WRITE_URL.format(blog_id=cfg["blog_id"]),
                      wait_until="domcontentloaded")
            frame = editor_frame(page, cfg)
            dismiss_popups(page, frame)

            log("제목 입력")
            title = first_visible(frame, SEL.TITLE, "제목 입력란", cfg["timeout_ms"])
            title.click()
            page.keyboard.type(post["title"], delay=18)
            page.keyboard.press("Tab")

            log("본문 입력")
            insert_blocks(page, frame, post, post_dir, cfg)

            if cfg.get("with_tags") or args.with_tags:
                log("경고: 태그 입력은 발행 패널을 열어야 해서 기본적으로 하지 않는다.")
            log(f"태그(직접 입력하세요): {' '.join(post.get('tags', []))}")

            log("임시저장")
            save_draft(page, frame, cfg)

            state["last_slug"] = post["slug"]
            state.setdefault("history", []).append({
                "slug": post["slug"],
                "at": datetime.now().isoformat(timespec="seconds"),
                "result": "ok",
            })
            save_state(cfg, state)
            log("완료. 네이버 [글쓰기 > 저장된 글] 에서 확인 후 직접 발행하세요.")
            code = EXIT_OK
        except PublishBlocked as e:
            log(f"중단: {e}")
            dump_diagnostics(page, cfg, "publish-blocked")
            code = EXIT_FAIL
        except Exception as e:
            log(f"실패: {type(e).__name__}: {e}")
            dump_diagnostics(page, cfg, "error")
            code = EXIT_FAIL
        finally:
            if args.keep_open:
                log("--keep-open: 브라우저를 열어 둔다. Enter 를 누르면 닫는다.")
                input()
            ctx.close()
    return code


def git_pull():
    try:
        out = subprocess.run(
            ["git", "pull", "--ff-only", "origin", "main"],
            cwd=ROOT, capture_output=True, text=True, timeout=120,
        )
        log(f"git pull: {(out.stdout or out.stderr).strip().splitlines()[-1:]}")
    except Exception as e:
        log(f"git pull 실패(무시하고 진행): {e}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="네이버 블로그 임시저장")
    ap.add_argument("--login", action="store_true", help="최초 1회 수동 로그인")
    ap.add_argument("--dry-run", action="store_true", help="네이버 접속 없이 대상만 확인")
    ap.add_argument("--keep-open", action="store_true", help="끝나도 브라우저 유지")
    ap.add_argument("--no-pull", action="store_true", help="git pull 생략")
    ap.add_argument("--with-tags", action="store_true", help="태그도 입력 시도(비권장)")
    ap.add_argument("--force", action="store_true", help="이미 올린 글도 다시 올린다")
    args = ap.parse_args(argv)

    cfg = load_config()
    if args.login:
        return do_login(cfg)
    if not args.no_pull and not args.dry_run:
        git_pull()
    return do_draft(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
