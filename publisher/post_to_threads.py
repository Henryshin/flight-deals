# -*- coding: utf-8 -*-
"""
result/<날짜>_<도시>/threads.md 를 Threads 작성창에 채운다. **게시 버튼은 누르지 않는다.**

    python publisher\\post_to_threads.py ..\\result\\<날짜>_<도시>
    python publisher\\post_to_threads.py ..\\result\\<날짜>_<도시> --dry-run   # 원고·가격만 확인

순서
----
1. threads.md 를 읽는다 — 머리말 + `## 본문` / `## 답글 1` / `## 답글 2` ...
2. 가격이 살아있는지 구글 항공권으로 다시 조회한다 (collector 의 크롤러 재사용).
   원고 가격과 1% 넘게 다르면 **브라우저를 건드리지 않고 멈춘다.**
   - 올랐다 → 이 글은 올리지 않는다 (가격 살아있는 것만 올린다, 2026-09-21 결정)
   - 내렸다 → 원고 숫자가 틀리므로 고친 뒤 다시 돌린다
3. 사용자 크롬(CDP 9222)에 붙어 새 탭에서 threads.com 작성창을 열고
   본문 + 이미지, '스레드에 추가' 로 답글들을 채운다.
4. 멈춘다. 읽어 보고 게시는 사람이 누른다.

하지 말 것
----------
- browser.close() — 연결 해제가 아니라 사용자 크롬을 진짜로 닫는다.
- '게시' 클릭 — 이 스크립트는 채우기만 한다.
"""
import argparse
import io
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if sys.stdout.encoding is None or sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

CDP_URL = "http://127.0.0.1:9222"
HOME_URL = "https://www.threads.com/"
PRICE_TOLERANCE = 0.01
MAX_CHARS = 500  # Threads 한 게시물 글자 수 제한


class Failed(RuntimeError):
    pass


def log(msg):
    print(f"[threads] {msg}", flush=True)


# ---------------------------------------------------------------- 원고 읽기

def split_front(text):
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    front = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            v = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
        else:
            v = v.strip("'\"")
        front[k.strip()] = v
    return front, m.group(2)


def load_threads_md(post_dir, name="threads.md"):
    p = post_dir / name
    if not p.exists():
        raise Failed(f"{p} 가 없다")
    front, body = split_front(p.read_text(encoding="utf-8"))
    parts = re.split(r"^## +(.+)$", body, flags=re.M)
    sections = []
    for name, text in zip(parts[1::2], parts[2::2]):
        text = text.strip()
        if text:
            sections.append((name.strip(), text))
    if not sections or sections[0][0] != "본문":
        raise Failed("threads.md 첫 섹션이 '## 본문' 이어야 한다")
    for name, text in sections:
        if len(text) > MAX_CHARS:
            raise Failed(f"'{name}' 이 {len(text)}자 — Threads 제한 {MAX_CHARS}자를 넘는다")
    images = [post_dir / i for i in front.get("images", [])]
    missing = [str(i) for i in images if not i.exists()]
    if missing:
        raise Failed("이미지가 없다: " + ", ".join(missing))
    return front, sections, images


def load_source_front(post_dir, front):
    src = post_dir / front.get("source", f"{post_dir.name}.md")
    if not src.exists():
        raise Failed(f"블로그 원고 {src} 가 없다 — 가격 조회 조건을 알 수 없다")
    sf, body = split_front(src.read_text(encoding="utf-8"))
    return sf, body


# ---------------------------------------------------------------- 가격 재조회

def check_price(front, src_front, src_body):
    price = int(front.get("price") or src_front["price"])
    origin, dest = src_front["monitor_id"].split("-")
    d1, d2 = (date.fromisoformat(x) for x in src_front["itinerary"].split(":"))
    nonstop = "직항" in src_front.get("title", "") or "직항" in src_body[:1500]
    log(f"가격 재조회 {origin}->{dest} {d1}~{d2} {'직항' if nonstop else '경유 포함'} (원고 {price:,}원)")

    from collector.google_flights_crawler import PriceCrawlerSession
    with PriceCrawlerSession() as s:
        r = s.fetch_result(origin, dest, d1, d2, max_stops=0 if nonstop else None,
                           with_return=False)
    if not r["by_stops"]:
        raise Failed(f"가격 조회 실패({r['status']}: {r['detail']}) — 확인 못 한 가격으로 올리지 않는다")
    best = min(r["by_stops"].values(), key=lambda it: it["price"])
    now = best["price"]
    diff = (now - price) / price
    log(f"지금 {now:,}원 ({best.get('airline', '?')} {best.get('dep_time', '')}) · 원고 대비 {diff:+.1%}")
    if abs(diff) <= PRICE_TOLERANCE:
        return now
    if now > price:
        raise Failed(f"가격이 올랐다 ({price:,} → {now:,}원). 이 글은 올리지 않는다")
    raise Failed(f"가격이 내렸다 ({price:,} → {now:,}원). threads.md 숫자를 고친 뒤 다시 돌릴 것")


# ---------------------------------------------------------------- 작성창 채우기

COMPOSE_OPENERS = [
    "text=새로운 소식이 있나요?",
    "text=What's new?",
    "[aria-label='만들기']",
    "[aria-label='Create']",
]
ADD_TO_THREAD = ["text=스레드에 추가", "text=Add to thread"]
POST_BUTTON_TEXTS = ("게시", "Post")


def first_visible(page, selectors, timeout=4000):
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            loc.wait_for(state="visible", timeout=timeout)
            return loc
        except Exception:
            continue
    return None


def type_multiline(page, text):
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line:
            page.keyboard.insert_text(line)
        if i < len(lines) - 1:
            page.keyboard.press("Shift+Enter")


def textboxes(dialog):
    return dialog.locator("[contenteditable='true']")


def fill_compose(page, sections, images):
    page.goto(HOME_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    if page.locator("a[href*='/login']").count() and not first_visible(page, COMPOSE_OPENERS, 2000):
        raise Failed("Threads 에 로그인돼 있지 않다 — 이 크롬에서 직접 로그인한 뒤 다시 돌릴 것")

    opener = first_visible(page, COMPOSE_OPENERS)
    if opener is None:
        raise Failed("작성창 여는 버튼을 못 찾았다")
    opener.click()
    dialog = page.locator("[role='dialog']").last
    dialog.wait_for(state="visible", timeout=8000)

    for idx, (name, text) in enumerate(sections):
        if idx > 0:
            add = first_visible(dialog, ADD_TO_THREAD)
            if add is None:
                raise Failed(f"'{name}' 을 넣을 '스레드에 추가' 를 못 찾았다")
            add.click()
            page.wait_for_timeout(600)
        boxes = textboxes(dialog)
        if boxes.count() < idx + 1:
            raise Failed(f"{idx + 1}번째 입력칸이 안 생겼다 (지금 {boxes.count()}개)")
        box = boxes.nth(idx)
        box.click()
        type_multiline(page, text)
        page.wait_for_timeout(400)
        log(f"{name} 입력 ({len(text)}자)")

        if idx == 0 and images:
            file_input = dialog.locator("input[type='file']").first
            file_input.set_input_files([str(i) for i in images])
            page.wait_for_timeout(2500)
            log(f"본문 이미지 {len(images)}장 첨부")

    got = textboxes(dialog).count()
    if got != len(sections):
        raise Failed(f"입력칸이 {got}개 — 원고 섹션 {len(sections)}개와 다르다. 화면을 확인할 것")
    return dialog


def open_minimized_page(browser):
    """사용자 창과 따로 **최소화된 새 창**을 만들고 그 탭을 돌려준다.

    사용자가 직접 클릭해 열기 전까지 최소화를 유지한다(2026-09-21 지시).
    bring_to_front·창 복원은 하지 않는다. background=True 라 포커스도 뺏지 않는다.
    """
    ctx = browser.contexts[0]
    cdp = browser.new_browser_cdp_session()
    before = set(ctx.pages)
    with ctx.expect_page(timeout=10000) as info:
        target = cdp.send("Target.createTarget",
                          {"url": "about:blank", "newWindow": True, "background": True})
    win = cdp.send("Browser.getWindowForTarget", {"targetId": target["targetId"]})
    cdp.send("Browser.setWindowBounds",
             {"windowId": win["windowId"], "bounds": {"windowState": "minimized"}})
    page = info.value
    if page in before:
        raise Failed("새 창의 탭을 못 찾았다")
    log(f"최소화된 새 창에서 작업 (windowId {win['windowId']}, "
        f"재사용: --target-id {target['targetId']})")
    return page


def target_id(page):
    return page.context.new_cdp_session(page).send("Target.getTargetInfo")["targetInfo"]["targetId"]


def find_page(browser, tid):
    """targetId 로만 탭을 고른다. URL 로 고르면 사용자 탭이 섞인다(2026-09-21 사고)."""
    for p in browser.contexts[0].pages:
        if target_id(p) == tid:
            if p.locator("[role='dialog'] [contenteditable='true']").count():
                raise Failed("그 창에 작성 중인 글이 열려 있다 — 덮어쓰지 않는다")
            log(f"내 창 재사용 (targetId {tid})")
            return p
    raise Failed(f"targetId {tid} 탭이 없다")


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description="threads.md 를 Threads 작성창에 채운다 (게시는 사람이)")
    ap.add_argument("post_dir", help="result/<날짜>_<도시> 폴더 경로")
    ap.add_argument("--cdp", default=CDP_URL)
    ap.add_argument("--file", default="threads.md", help="글 폴더 안의 원고 파일 (비교안: threads_B.md 등)")
    ap.add_argument("--target-id", help="이미 열어 둔 내 최소화 창의 탭을 재사용 (새 창을 만들지 않음)")
    ap.add_argument("--dry-run", action="store_true", help="원고 확인·가격 재조회까지만")
    ap.add_argument("--skip-price-check", action="store_true",
                    help="가격 재조회를 건너뛴다 (방금 따로 확인했을 때만)")
    args = ap.parse_args(argv)
    post_dir = Path(args.post_dir).resolve()

    try:
        front, sections, images = load_threads_md(post_dir, args.file)
        log(f"원고: {' / '.join(f'{n} {len(t)}자' for n, t in sections)} · 이미지 {len(images)}장")
        if not args.skip_price_check:
            src_front, src_body = load_source_front(post_dir, front)
            check_price(front, src_front, src_body)
    except Failed as e:
        print(f"\n중단: {e}", file=sys.stderr)
        return 2
    if args.dry_run:
        log("--dry-run: 브라우저는 건드리지 않았다")
        return 0

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp(args.cdp)
        except Exception as e:
            print(f"\nCDP 연결 실패: {e}\n  login.py 로 크롬을 먼저 띄울 것", file=sys.stderr)
            return 1
        try:
            page = (find_page(browser, args.target_id) if args.target_id
                    else open_minimized_page(browser))
        except Failed as e:
            print(f"\n중단: {e}", file=sys.stderr)
            return 1
        try:
            fill_compose(page, sections, images)
        except Failed as e:
            print(f"\n중단: {e}", file=sys.stderr)
            return 1
        # browser.close() 를 부르지 않는다 — 사용자 크롬이 닫힌다.

    print()
    log("채우기 완료. 게시하지 않았다.")
    if front.get("topic"):
        print(f"  주제 태그(직접 고르세요): {front['topic']}")
    print("  창은 최소화돼 있다. 작업표시줄에서 열어 본문·답글·이미지를 읽어 본 뒤 '게시' 를 직접 누르세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
