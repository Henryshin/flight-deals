"""homepc Threads 웹 에이전트 — 텔레그램에서 고른 글을 Threads 웹에 '임시저장'한다.

흐름: 텔레그램 [📝 Threads 임시저장] → Cloudflare Worker 가 KV 에 작업을 넣음
      → 이 스크립트가 20초마다 /threads/job 을 확인 → 웹에서 글쓰기 창을 열어
         본문 + 이미지 2장 + 이어지는 글(링크)을 넣고 닫을 때 '저장' → /threads/job/done

Threads API 에는 임시저장이 없어서 웹을 쓴다 (사용자 결정 2026-11-05).
로그인은 전용 크롬 프로필(%LOCALAPPDATA%/afterwork-threads-profile)에 저장돼 있다.
로그인이 풀리면 threads_login.py 를 다시 실행.

실행: pythonw threads_web_agent.py          (상주, 로그온 시 작업 스케줄러가 켬)
      python  threads_web_agent.py --once   (작업 하나만 확인)
      python  threads_web_agent.py --test <candidates.json URL> <후보> <문구>   (직접 임시저장)
"""
import difflib
import json
import os
import re
import sys
import tempfile
import time
import traceback
import urllib.request
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
PROFILE = Path(os.environ.get("LOCALAPPDATA", ".")) / "afterwork-threads-profile"
LOG = HERE / "agent.log"
POLL_SEC = 20
UA = "afterwork-threads-agent/1.0"


def load_env():
    env = {}
    f = HERE / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


ENV = load_env()
WORKER = ENV.get("WORKER_URL", "https://flight-register.klplmfp.workers.dev")
KEY = ENV.get("THREADS_AGENT_KEY", "")


def log(*a):
    line = datetime.now().strftime("%m-%d %H:%M:%S ") + " ".join(str(x) for x in a)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        if LOG.stat().st_size > 2_000_000:
            LOG.write_text(line + "\n", encoding="utf-8")
    except OSError:
        pass
    if sys.stdout:
        try:
            print(line, flush=True)
        except Exception:
            pass


def http(method, path, body=None):
    req = urllib.request.Request(
        WORKER + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-Agent-Key": KEY, "User-Agent": UA, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def download(urls, folder):
    paths = []
    for i, u in enumerate(urls):
        p = Path(folder) / f"{i}.png"
        req = urllib.request.Request(f"{u}?t={int(time.time())}", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            p.write_bytes(r.read())
        paths.append(str(p))
    return paths


def click_visible(page, label):
    """같은 이름의 숨은 요소가 여럿 있어서, 화면에 보이는 것만 누른다."""
    page.locator(f'span:text-is("{label}") >> visible=true').last.click(timeout=8000)


def type_lines(page, text):
    """줄바꿈은 Enter 로, 글자는 insert_text 로 (이모지·한글 안전)."""
    for i, line in enumerate(text.split("\n")):
        if i:
            page.keyboard.press("Enter")
        if line:
            page.keyboard.insert_text(line)


def save_draft(job, headless=True):
    with tempfile.TemporaryDirectory() as tmp:
        files = download(job["images"], tmp)
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(PROFILE), channel="chrome", headless=headless,
                viewport={"width": 1280, "height": 900}, locale="ko-KR",
            )
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                page.goto("https://www.threads.com/", wait_until="domcontentloaded", timeout=60000)
                opener = page.get_by_text("새로운 소식을 공유해보세요").first
                try:
                    opener.wait_for(timeout=20000)
                except Exception:
                    if "login" in page.url:
                        raise RuntimeError("Threads 로그인이 풀렸어요 — homepc 에서 threads_login.py 실행 필요")
                    raise RuntimeError("글쓰기 버튼을 못 찾았어요 (화면이 바뀌었을 수 있음)")
                opener.click()
                dlg = page.locator("[role=dialog]").first
                dlg.wait_for(timeout=15000)
                page.wait_for_timeout(1000)

                boxes = dlg.locator("[role=textbox][contenteditable=true]")
                boxes.first.click()
                type_lines(page, job["text"])

                dlg.locator("input[type=file]").first.set_input_files(files)
                # 미리보기 이미지가 다 붙을 때까지
                for _ in range(30):
                    if dlg.locator("img[src^='blob:'], img[src^='data:']").count() >= len(files):
                        break
                    page.wait_for_timeout(500)
                else:
                    raise RuntimeError("이미지 첨부가 끝나지 않았어요")

                if job.get("image_spoiler"):
                    # 이미지마다 [⋯] → 스포일러로 표시
                    for i in range(len(files)):
                        dlg.get_by_role("button", name="첨부 파일 옵션").nth(i).click()
                        click_visible(page, "스포일러로 표시")
                        page.wait_for_timeout(500)

                if job.get("reply"):
                    dlg.get_by_text("스레드에 추가", exact=True).first.click()
                    page.wait_for_timeout(800)
                    if boxes.count() < 2:
                        raise RuntimeError("이어지는 글 칸이 안 생겼어요")
                    boxes.nth(1).click()
                    type_lines(page, job["reply"])
                    if job.get("reply_link_spoiler"):
                        # 첫 줄(링크)만 선택 → 떠 있는 [스포일러로 표시]
                        page.keyboard.press("Control+Home")
                        page.keyboard.press("Shift+End")
                        page.wait_for_timeout(600)
                        click_visible(page, "스포일러로 표시")
                        page.wait_for_timeout(500)
                        page.keyboard.press("End")

                page.wait_for_timeout(1000)
                page.screenshot(path=str(HERE / "last_compose.png"))

                # 닫기 → "임시 저장하시겠어요?" → 저장
                dlg.get_by_text("취소", exact=True).first.click()
                page.get_by_text("임시 저장하시겠어요?").wait_for(timeout=10000)
                # dry: 화면 확인용 — 저장하지 않고 버린다
                page.get_by_role("button", name="저장 안 함" if job.get("dry") else "저장", exact=True).click()
                page.get_by_text("임시 저장하시겠어요?").wait_for(state="hidden", timeout=15000)
                page.wait_for_timeout(2500)   # 서버 저장 여유
                page.screenshot(path=str(HERE / "last_saved.png"))
            except Exception:
                try:
                    page.screenshot(path=str(HERE / "last_error.png"))
                except Exception:
                    pass
                raise
            finally:
                ctx.close()


# --- 루프백: 게시된 글과 임시저장한 글 비교 ------------------------------------
# 사용자는 임시저장본을 고쳐서 게시한다. 게시된 글을 찾아 무엇을 고쳤는지 뽑아
# Worker(/threads/learn)의 threads:style 에 보내면 다음 글부터 그대로 반영된다.
#  - 지운 줄      → drop_line_patterns (도시·연휴·숫자는 일반화)
#  - 고친 줄      → replace [{city}/{hol} 자리표시자 쓴 원문, 바꾼 문장] (숫자가 바뀐 줄은 보고만)
#  - 새로 넣은 줄 → 보고만 (어디에 넣을지 모호)
#  - 이미지 장수·스포일러, 이어지는 글(링크) 문구 → 그대로 반영

DRAFTS = HERE / "drafts.jsonl"
LEARNED = HERE / "learned.jsonl"
HANDLE = ENV.get("THREADS_HANDLE", "made_by.henry")
WATCH_EVERY = 15 * 60
WATCH_DAYS = 3

POSTS_JS = """() => [...document.querySelectorAll('div[data-pressable-container=true]')].map(c => ({
  user: (c.querySelector('a[href^="/@"]') || {getAttribute: () => null}).getAttribute('href'),
  imgs: c.querySelectorAll('picture img, img[alt^="Photo"]').length,
  text: c.innerText }))"""


def jsonl(path):
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def append_jsonl(path, obj):
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def pending_drafts():
    done = {x["draft_id"] for x in jsonl(LEARNED)}
    cut = time.time() - WATCH_DAYS * 86400
    return [d for d in jsonl(DRAFTS) if d["id"] not in done and d.get("saved_at", 0) > cut]


def post_lines(text):
    """'아이디\\n시간\\n본문…\\n1\\n/\\n2\\n…' 에서 본문 줄만."""
    lines = text.split("\n")[2:]
    body = []
    for i, l in enumerate(lines):
        s = l.strip()
        if s.isdigit() and i + 1 < len(lines) and lines[i + 1].strip() == "/":
            break
        if s in ("스포일러", "인기순", "모든 답글", "활동 보기"):
            break
        body.append(s)
    return [l for l in body if l]


def read_post(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    mine = [c for c in page.evaluate(POSTS_JS) if c["user"] == f"/@{HANDLE}"]
    if not mine:
        return None
    main = mine[0]
    reply = mine[1] if len(mine) > 1 else None
    return {
        "url": url, "body": post_lines(main["text"]), "imgs": main["imgs"],
        "spoiler": "\n스포일러\n" in main["text"],
        "reply": post_lines(reply["text"]) if reply else [],
    }


def generalize(line, d):
    g = line
    if d.get("city"):
        g = g.replace(d["city"], "{city}")
    if d.get("holiday"):
        g = g.replace(d["holiday"], "{hol}")
    if d.get("collected"):
        g = g.replace(d["collected"], "{md}")
    return g


def line_pattern(line, d):
    g = re.escape(generalize(line, d))
    g = g.replace(r"\{city\}", ".+").replace(r"\{hol\}", ".+").replace(r"\{md\}", ".+")
    return "^" + re.sub(r"\d+", r"\\d+", g) + "$"


def diff_post(d, post):
    patch, rep = {"drop_line_patterns": [], "replace": []}, []
    dl = [l.strip() for l in d["text"].split("\n") if l.strip()]
    pl = post["body"]
    sm = difflib.SequenceMatcher(None, dl, pl)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        olds, news = dl[i1:i2], pl[j1:j2]
        pairs = []
        if tag == "replace":
            for a in olds:
                best = max(news, key=lambda b: difflib.SequenceMatcher(None, a, b).ratio(), default=None)
                if best and difflib.SequenceMatcher(None, a, best).ratio() > 0.45:
                    pairs.append((a, best))
            paired_old = {a for a, _ in pairs}
            paired_new = {b for _, b in pairs}
            olds = [a for a in olds if a not in paired_old]
            news = [b for b in news if b not in paired_new]
        for a, b in pairs:
            ga, gb = generalize(a, d), generalize(b, d)
            if re.findall(r"\d+", ga) != re.findall(r"\d+", gb):
                rep.append(f"✏️ 숫자 수정(자동 반영 안 함): {a} → {b}")
            else:
                patch["replace"].append([ga, gb])
                rep.append(f"✏️ 수정: {a} → {b}")
        for a in olds:
            patch["drop_line_patterns"].append(line_pattern(a, d))
            rep.append(f"🗑 삭제: {a}")
        for b in news:
            rep.append(f"➕ 추가(자동 반영 안 함): {b}")

    n_draft = len(d.get("images") or [])
    if post["imgs"] and post["imgs"] != n_draft:
        patch["image_mode"] = "card" if post["imgs"] == 1 else "cover+card"
        rep.append(f"🖼 이미지 {n_draft}장 → {post['imgs']}장")
    if post["imgs"] and post["spoiler"] != bool(d.get("image_spoiler")):
        patch["image_spoiler"] = post["spoiler"]
        rep.append(f"🖼 이미지 스포일러 {'켬' if post['spoiler'] else '끔'}")

    if post["reply"]:
        tpl = "\n".join("{url}" if "excel-flights.xyz" in l else generalize(l, d) for l in post["reply"])
        old = "\n".join("{url}" if "excel-flights.xyz" in l else generalize(l.strip(), d)
                        for l in d.get("reply", "").split("\n") if l.strip())
        if tpl != old:
            patch["reply_template"] = tpl
            rep.append("💬 이어지는 글 문구 변경:\n" + "\n".join(post["reply"]))
    patch = {k: v for k, v in patch.items() if v not in ([], None)}
    return patch, rep


def watch_posts():
    pend = pending_drafts()
    if not pend:
        return
    seen = {x.get("post_url") for x in jsonl(LEARNED)}
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(PROFILE), channel="chrome", headless=True,
            viewport={"width": 1280, "height": 1400}, locale="ko-KR")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(f"https://www.threads.com/@{HANDLE}", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(5000)
            links = page.eval_on_selector_all(
                "a[href*='/post/']", "els => [...new Set(els.map(e => e.href.split('/media')[0]))].slice(0, 6)")
            for url in links:
                if url in seen or not pend:
                    continue
                post = read_post(page, url)
                if not post:
                    continue
                body = "\n".join(post["body"])
                for d in pend:
                    price = f"{d['price']:,}" if d.get("price") else ""
                    if d.get("city") and d["city"] in body and (not price or price in body):
                        patch, rep = diff_post(d, post)
                        report = (f"{d['city']} · {d.get('label', '')}\n{url}\n\n" +
                                  ("\n".join(rep) if rep else "고친 곳 없음 — 그대로 게시"))
                        http("POST", "/threads/learn", {"patch": patch, "report": report})
                        append_jsonl(LEARNED, {"draft_id": d["id"], "post_url": url, "patch": patch,
                                               "report": rep, "at": time.time()})
                        log("학습", d["id"], url, json.dumps(patch, ensure_ascii=False))
                        pend.remove(d)
                        break
        finally:
            ctx.close()


def handle(job):
    log("작업", job["id"], job.get("city"), job.get("label"))
    try:
        try:
            save_draft(job, headless=True)
        except Exception as e:
            log("headless 실패 → 창 띄워서 재시도:", e)
            save_draft(job, headless=False)
        log("임시저장 완료", job["id"])
        if job["id"] != "test":
            append_jsonl(DRAFTS, {**job, "saved_at": time.time()})
        return {"id": job["id"], "ok": True}
    except Exception as e:
        log("실패", job["id"], repr(e), traceback.format_exc()[-800:])
        return {"id": job["id"], "ok": False, "error": str(e)}


def poll_once():
    j = http("GET", "/threads/job").get("job")
    if not j:
        return False
    res = handle(j)
    http("POST", "/threads/job/done", res)
    return True


def main():
    if "--test" in sys.argv:
        i = sys.argv.index("--test")
        url, pick, opt = sys.argv[i + 1], int(sys.argv[i + 2]), int(sys.argv[i + 3])
        doc = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA})).read())
        c = doc["candidates"][pick - 1]
        job = {"id": "test", "city": c["city"], "label": c["texts"][opt - 1]["label"],
               "text": c["texts"][opt - 1]["text"], "reply": c["reply"], "images": c["images"]}
        print(handle(job))
        return
    if not KEY:
        log("THREADS_AGENT_KEY 가 .env 에 없음")
        sys.exit(1)
    if "--once" in sys.argv:
        print("처리함" if poll_once() else "작업 없음")
        return
    if "--watch" in sys.argv:
        watch_posts()
        return
    log("에이전트 시작", WORKER)
    last_watch = 0
    while True:
        try:
            poll_once()
        except Exception as e:
            log("폴링 오류:", repr(e))
            time.sleep(60)
        if time.time() - last_watch > WATCH_EVERY:
            last_watch = time.time()
            try:
                watch_posts()
            except Exception as e:
                log("게시글 확인 오류:", repr(e))
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
