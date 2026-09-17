"""포스트 저장소와 원장(ledger).

글 한 편 = 저장소 **밖** `Blog_flight/result/<날짜>_<도시>/` 디렉터리 하나.
원고(`<날짜>_<도시>.md`)와 발행용 파일(post.json·body·카드·사진)이 같은 폴더에 있고,
사람이 고치는 파일은 원고 하나뿐이다 (사용자 지정 2026-09-17 — 저장소 안 사본과
result 사본이 따로 있어 고친 원고가 발행에 반영되지 않을 뻔했다).
BLOG_RESULT_DIR 환경변수로 위치를 바꿀 수 있다 (CI 등).
"""
import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from blog.data import KST, ROOT

POSTS_DIR = Path(os.environ.get("BLOG_RESULT_DIR") or ROOT.parent / "result")
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


_BAD_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def city_name(dest_ko):
    """'타이페이(타오위안)' -> '타이페이'. 폴더 이름에 못 쓰는 글자와 공백을 뺀다."""
    s = re.sub(r"\(.*?\)", "", str(dest_ko or ""))
    s = _BAD_PATH_CHARS.sub("", s).replace(" ", "").strip(".")
    return s or "post"


def post_folder(day, dest_ko):
    """'2026-09-17_마카오' 형태의 디렉터리 이름. 원고 파일 이름도 이것 + '.md'."""
    return f"{day.isoformat()}_{city_name(dest_ko)}"


def source_sha256(path):
    """원고 파일 해시. post.json 에 남겨 두고 발행기가 원고 변경을 알아챈다."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def is_post_dir(d):
    return Path(d).is_dir() and (Path(d) / "post.json").exists()


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
