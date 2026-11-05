"""매일 07:30 KST: Threads 후보 3개를 골라 캡처하고 텔레그램으로 보낸다.

    python scripts/threads_brief.py                 # 후보 + 캡처 + 텔레그램 전송
    python scripts/threads_brief.py --no-send       # 전송 없이 파일만
    python scripts/threads_brief.py --date 2026-11-05

산출물: docs/threads/<날짜>/  (GitHub Pages 로 공개 → Worker·homepc 에이전트가 URL 로 가져간다)
  cover.png, 1.png~3.png, candidates.json
버튼 처리는 cloudflare-worker/worker.js 의 /telegram 이 하고, Threads 웹 임시저장은
homepc 에서 도는 threads-agent/threads_web_agent.py 가 한다. 게시는 사용자가 앱에서 직접.
사용자가 고쳐서 게시한 형식은 Worker 의 threads:style 에 쌓여 미리보기·임시저장에 반영된다.

비밀값: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (환경변수, 없으면 저장소 루트 .env)
"""
import argparse
import functools
import http.server
import json
import os
import sys
import threading
import traceback
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blog import data as D                      # noqa: E402
from blog import threads_pick as P              # noqa: E402
from blog import threads_templates as T         # noqa: E402

ROOT = D.ROOT
PUBLIC_BASE = "https://excel-flights.xyz"
PROXY_URL = "https://flight-register.klplmfp.workers.dev"
CIRCLED = "①②③④⑤"

# 보는 사람 대부분이 폰이라 모바일 레이아웃(700px 이하, 카드형)으로 찍는다 (사용자 지적 2026-11-04).
VIEWPORT = {"width": 412, "height": 515}      # 4:5
SCALE = 1080 / 412                            # → 1080x1350


# --- 설정 ------------------------------------------------------------------

def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def recent_dests():
    """최근 14일에 올린 목적지 (Worker KV 가 원장). 실패하면 빈 목록 + 경고."""
    try:
        with urllib.request.urlopen(f"{PROXY_URL}/threads/recent", timeout=15) as r:
            data = json.load(r)
    except Exception as e:  # Worker 미배포 등
        return [], f"최근 게시 목록을 못 읽음 ({e.__class__.__name__}) — 중복 제외 없이 골랐어요"
    note = data.get("token_note") or ""
    warn = f"Threads 토큰: {note}" if ("실패" in note or "없음" in note) else None
    return data.get("dests", []), warn


# --- 텔레그램 ----------------------------------------------------------------

def tg(method, fields=None, files=None):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    url = f"https://api.telegram.org/bot{token}/{method}"
    if files:
        boundary = uuid.uuid4().hex
        body = b""
        for k, v in (fields or {}).items():
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n"
                     f"{v}\r\n").encode()
        for k, path in files.items():
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; "
                     f"filename=\"{Path(path).name}\"\r\nContent-Type: image/png\r\n\r\n").encode()
            body += Path(path).read_bytes() + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(url, body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
    else:
        req = urllib.request.Request(url, urllib.parse.urlencode(fields or {}).encode())
    with urllib.request.urlopen(req, timeout=60) as r:
        res = json.load(r)
    if not res.get("ok"):
        raise RuntimeError(f"telegram {method}: {res}")
    return res["result"]


def kb(rows):
    return json.dumps({"inline_keyboard": rows}, ensure_ascii=False)


# --- 캡처 ------------------------------------------------------------------

HIDE_CSS = """
#chat-section, .chat-fab, #live-counter { display: none !important; }
/* 주인공 카드: 노란 채움 + 초록 테두리 + 살짝 키움. 나머지는 흐리게 (곁다리) */
#gv-board tr { transition: none !important; }
#gv-board tr:not(.tb-focus) { opacity: .45; }
tr.tb-focus { outline: 3px solid #217346; outline-offset: -2px; transform: scale(1.03);
              box-shadow: 0 6px 18px rgba(0,0,0,.18); position: relative; z-index: 2; }
tr.tb-focus { background: #fff2cc !important; }
tr.tb-focus > td { background-color: transparent !important; }
#gv-board tr:not(:has(.price-link)) { display: none !important; }  /* '범위에 연휴 없음' 회색 줄 */
"""

FOCUS_JS = """
([wid, L, od]) => {
  state.gvWindows = new Set([wid]);
  state.minLeave = L; state.maxLeave = L; state.dayValue = 0;
  renderGvBoard();
  // 화면의 연차 입력칸도 실제 적용값과 맞춘다 (안 맞추면 '5개 이하'로 보인다)
  const mn = document.getElementById('min-leave'), mx = document.getElementById('max-leave');
  if (mn) mn.value = L; if (mx) mx.value = L;
  const hint = document.getElementById('max-leave-hint');
  if (hint) hint.textContent = `· 적용됨 — 연차 ${L}개 일정만 추천`;
  const rows = [...document.querySelectorAll('#gv-board tr')];
  const row = rows.find(tr => {
    const s = tr.querySelector('.c-name .cell-sub');
    return s && s.textContent.trim() === od;
  });
  if (!row) return null;
  row.classList.add('tb-focus');
  const h2 = document.getElementById('gv-h2');
  // 주인공 카드를 화면 가운데보다 살짝 아래에 — 위로는 연휴·정렬 설정이, 아래로는 다른 카드가 보이게
  const r = row.getBoundingClientRect();
  const center = r.top + scrollY + r.height / 2;
  scrollTo(0, Math.max(0, center - innerHeight * 0.6));
  const price = row.querySelector('.c-price');
  const period = row.querySelector('.period-cell');
  return { price: price && price.textContent.trim(), period: period && period.textContent.trim() };
}
"""


def serve_docs():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass
    handler = functools.partial(Quiet, directory=str(ROOT / "docs"))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/"


def capture(cands, outdir):
    """cover.png + 후보별 n.png. 사이트가 띄운 가격을 돌려준다 (본문과 대조용)."""
    from playwright.sync_api import sync_playwright
    srv, url = serve_docs()
    shown = []
    try:
        with sync_playwright() as pw:
            br = pw.chromium.launch()
            page = br.new_page(viewport=VIEWPORT, device_scale_factor=SCALE, locale="ko-KR")
            page.goto(url, wait_until="networkidle")
            page.wait_for_selector("#gv-board .price-link", timeout=30_000)
            page.add_style_tag(content=HIDE_CSS)
            page.wait_for_timeout(500)
            page.screenshot(path=str(outdir / "cover.png"))
            for i, c in enumerate(cands, 1):
                od = f"{c['origin']}→{c['dest']}"
                got = page.evaluate(FOCUS_JS, [c["window_id"], c["leave_days"], od])
                page.wait_for_timeout(300)
                page.screenshot(path=str(outdir / f"{i}.png"))
                shown.append(got)
            br.close()
    finally:
        srv.shutdown()
    return shown


def price_matches(c, got):
    if not got or not got.get("price"):
        return False
    digits = "".join(ch for ch in got["price"] if ch.isdigit())
    return digits == str(c["price"])


# --- 본체 ------------------------------------------------------------------

def build(day, send=True, do_capture=True):
    inputs = D.load_inputs()
    age = D.assert_fresh(inputs["meta"], max_hours=36)
    recent, warn = recent_dests()
    picked = P.candidates(inputs, today=day, recent_dests=recent)
    if not picked:
        raise RuntimeError("조건에 맞는 후보가 없음")
    cands = [P.summarize(c, inputs["routes_by_id"]) for c in picked]

    collected = D.kst_str(inputs["meta"]["generated_at"]).split("년 ", 1)[1]
    outdir = ROOT / "docs" / "threads" / day.isoformat()
    outdir.mkdir(parents=True, exist_ok=True)
    base = f"{PUBLIC_BASE}/threads/{day.isoformat()}"

    shown = capture(cands, outdir) if do_capture else [None] * len(cands)
    for i, (c, got) in enumerate(zip(cands, shown), 1):
        c["texts"] = T.bodies(c, day)          # 문구 6가지. 여행지를 고르면 Worker 가 보여준다
        c["reply"] = T.link_reply(collected)
        c["images"] = [f"{base}/cover.png", f"{base}/{i}.png"]
        c["site_shown"] = got
        c["capture_ok"] = price_matches(c, got) if do_capture else None

    doc = {
        "date": day.isoformat(),
        "generated_at": datetime.now(D.KST).isoformat(timespec="seconds"),
        "data_generated_at": inputs["meta"]["generated_at"],
        "data_age_hours": round(age, 1),
        "warn": warn,
        "candidates": cands,
    }
    (outdir / "candidates.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    if send:
        send_telegram(doc, outdir, warn)
    return doc, outdir


def wait_public(day, timeout=600):
    """Pages 배포가 끝나 Threads 가 이미지를 가져갈 수 있을 때까지 기다린다."""
    import time
    url = f"{PUBLIC_BASE}/threads/{day.isoformat()}/candidates.json?t="
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url + str(int(time.time())), timeout=15) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(15)
    raise RuntimeError(f"{timeout}초 안에 GitHub Pages 에 후보 파일이 안 올라옴")


def send_telegram(doc, outdir, warn=None):
    chat = os.environ["TELEGRAM_CHAT_ID"]
    d = date.fromisoformat(doc["date"])
    head = (f"☀️ {d.month}/{d.day} Threads 후보 {len(doc['candidates'])}개\n"
            "첫 장은 공통으로 이 엑셀 표지예요. 여행지를 고르면 문구 6가지를 보여줄게요.")
    if warn:
        head += f"\n⚠️ {warn}"
    tg("sendPhoto", {"chat_id": chat, "caption": head}, {"photo": outdir / "cover.png"})
    for i, c in enumerate(doc["candidates"], 1):
        mark = CIRCLED[i - 1]
        note = "" if c["capture_ok"] is not False else "\n⚠️ 캡처 속 가격이 본문과 달라요 — 올리기 전에 확인"
        facts = "\n".join(T.facts_lines(c, d))
        cap = f"{mark} {c['region']} · 점수 {c['score']}{note}\n─────\n{facts}"
        tg("sendPhoto", {
            "chat_id": chat, "caption": cap[:1024],
            "reply_markup": kb([[{"text": f"{mark} 이 여행지로 → 문구 고르기",
                                  "callback_data": f"tp:{doc['date']}:{i}"}]]),
        }, {"photo": outdir / f"{i}.png"})
    tg("sendMessage", {
        "chat_id": chat,
        "text": "여행지 → 문구 → 미리보기 → [📝 임시저장] 순서예요.\n"
                "링크는 이어지는 글로 같이 임시저장돼요. 게시는 Threads 앱 임시저장본에서.",
        "reply_markup": kb([[{"text": "🙅 오늘은 패스", "callback_data": f"tp:{doc['date']}:pass"}]]),
        "disable_web_page_preview": "true",
    })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--no-send", action="store_true")
    ap.add_argument("--no-capture", action="store_true")
    ap.add_argument("--send-only", action="store_true",
                    help="이미 만든 docs/threads/<날짜>/ 를 Pages 공개 확인 후 전송 (Actions 2단계)")
    a = ap.parse_args()
    load_env()
    day = date.fromisoformat(a.date) if a.date else datetime.now(D.KST).date()
    try:
        if a.send_only:
            outdir = ROOT / "docs" / "threads" / day.isoformat()
            doc = json.loads((outdir / "candidates.json").read_text(encoding="utf-8"))
            wait_public(day)
            send_telegram(doc, outdir, doc.get("warn"))
        else:
            doc, outdir = build(day, send=not a.no_send, do_capture=not a.no_capture)
    except Exception as e:
        traceback.print_exc()
        if not a.no_send and os.environ.get("TELEGRAM_BOT_TOKEN"):
            try:
                tg("sendMessage", {"chat_id": os.environ["TELEGRAM_CHAT_ID"],
                                   "text": f"❌ {day} Threads 후보 생성 실패\n{e.__class__.__name__}: {e}"})
            except Exception:
                traceback.print_exc()
        sys.exit(1)
    for i, c in enumerate(doc["candidates"], 1):
        print(f"{i}. {c['city']} {c['price']:,} {c['depart_date']}~{c['return_date']} "
              f"{c['nights']}박 연차{c['leave_days']} [{c['region']}] score={c['score']} capture_ok={c['capture_ok']}")
    print("out:", outdir)


if __name__ == "__main__":
    main()
