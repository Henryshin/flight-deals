"""포스트 저장소와 원장(ledger).

posts/ 아래에 글 한 편이 디렉터리 하나로 들어간다. 워크플로 커밋 경로를 서로
겹치지 않게 유지하는 저장소 관례에 따라, 블로그 관련 산출물은 전부 posts/ 안에
둔다 (collect.yml -> data/, build.yml -> docs/data/, blog -> posts/).
"""
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from blog.data import KST, ROOT

POSTS_DIR = ROOT / "posts"
LEDGER_FILE = POSTS_DIR / "_ledger.json"
BRIEF_DIR = POSTS_DIR / "_brief"

LEDGER_KEEP = 180

# Windows 예약 장치명. 슬러그가 이것과 같으면 디렉터리를 못 만든다.
_WIN_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def slugify(text, fallback="post"):
    """한글 제목 -> ASCII 슬러그.

    Windows 작업 스케줄러가 .bat 로 경로를 다루므로 한글/이모지를 넣지 않는다.
    한글은 ASCII 로 옮길 수단이 없으므로 남는 게 없으면 fallback 을 쓴다.
    """
    s = unicodedata.normalize("NFKD", str(text))
    s = s.encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    s = re.sub(r"-{2,}", "-", s)[:60].strip("-")
    if not s or s in _WIN_RESERVED:
        s = fallback
    return s


def post_slug(day, monitor_id, window_id=None):
    """'2026-09-07-icn-kmi-2026-09-24' 형태의 디렉터리 이름."""
    parts = [day.isoformat(), slugify(monitor_id, "route")]
    if window_id:
        parts.append(slugify(window_id, "window"))
    return "-".join(parts)


def post_dir(slug, posts_dir=None):
    return (Path(posts_dir) if posts_dir else POSTS_DIR) / slug


def load_ledger(path=None):
    p = Path(path) if path else LEDGER_FILE
    if not p.exists():
        return {"version": 1, "posts": []}
    try:
        led = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": 1, "posts": []}
    led.setdefault("version", 1)
    led.setdefault("posts", [])
    return led


def save_ledger(ledger, path=None):
    p = Path(path) if path else LEDGER_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    ledger["updated_at"] = datetime.now(KST).isoformat(timespec="seconds")
    ledger["posts"] = ledger.get("posts", [])[-LEDGER_KEEP:]
    p.write_text(
        json.dumps(ledger, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )


def append_entry(ledger, entry):
    posts = [p for p in ledger.get("posts", []) if p.get("slug") != entry.get("slug")]
    posts.append(entry)
    ledger["posts"] = posts[-LEDGER_KEEP:]
    return ledger


def recent_dests(ledger, today, within_days=21):
    """최근 N일 안에 이미 쓴 목적지 IATA 집합."""
    out = set()
    for p in ledger.get("posts", []):
        try:
            d = datetime.fromisoformat(p["date"]).date()
        except (KeyError, ValueError):
            continue
        if 0 <= (today - d).days < within_days and p.get("dest"):
            out.add(p["dest"])
    return out
