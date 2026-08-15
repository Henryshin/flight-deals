"""롤링 7일 쿼터 원장.

Threads(7일 500쿼리)와 Instagram(7일 고유 해시태그 30개) 모두 '롤링 윈도우'
한도라, 런이 끝나도 남는 영속 원장이 없으면 한도를 넘길 수밖에 없다.

data/collect_status.json 처럼 매 런 통째로 다시 쓰는 방식(merge=ours)은 여기선
쓰면 안 된다. 동시 런에서 한쪽 기록이 통째로 사라지면 한도를 초과해 앱이 제재를
받는다. 그래서 append-only JSONL + .gitattributes 의 merge=union 으로 둔다.
(prices.csv 와 같은 전략. 기록이 겹쳐 남을지언정 사라지지는 않는다.)

플랫폼별로 '무엇을 세는지'가 다르다는 점이 핵심이다.
- threads   : 호출 1건 = 1쿼리 소비. 실패한 호출도 Meta 는 카운트하므로 같이 센다.
- instagram : 호출 수가 아니라 윈도우 내 '고유 해시태그 수'가 한도.
              => 이미 쓴 해시태그를 다시 조회하는 건 공짜다. 이걸 '호출당 과금'으로
                 잘못 구현하면 주당 태그 4개밖에 못 본다고 착각하게 된다.

원장은 어디까지나 '추정치'이고 진실은 API 쪽에 있다. 그래서 API 가 쿼터 오류를
주면 hard_block 을 걸어 카운터와 무관하게 호출을 막는다.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
LEDGER_FILE = ROOT / "saju" / "data" / "quota.jsonl"

WINDOW_DAYS = 7
HARD_BLOCK_HOURS = 24

KIND_CALL = "call"
KIND_HARD_BLOCK = "hard_block"

# unit="call"       -> 항목 개수를 센다
# unit="unique_key" -> 윈도우 내 서로 다른 key 개수를 센다
LIMITS = {
    "threads": {"limit": 500, "unit": "call"},
    "instagram": {"limit": 30, "unit": "unique_key"},
    "sample": {"limit": 10 ** 9, "unit": "call"},  # 로컬 픽스처는 사실상 무제한
}


def _now(now=None):
    return now or datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_at(value):
    try:
        dt = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _read_all(path):
    """원장 전체를 읽는다 (윈도우 필터 없이). 깨진 줄은 조용히 건너뛴다."""
    path = Path(path) if path else LEDGER_FILE
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def load_entries(platform=None, path=None, now=None, kind=KIND_CALL):
    """윈도우 안에 남아있는 항목만 반환."""
    cutoff = _now(now) - timedelta(days=WINDOW_DAYS)
    out = []
    for rec in _read_all(path):
        if kind and rec.get("kind", KIND_CALL) != kind:
            continue
        at = _parse_at(rec.get("at"))
        if at is None or at < cutoff:
            continue
        if platform and rec.get("platform") != platform:
            continue
        out.append(rec)
    return out


def usage(platform, path=None, now=None):
    """현재 윈도우에서 소비된 양 (플랫폼 단위 규칙에 따라)."""
    entries = load_entries(platform, path=path, now=now)
    unit = LIMITS.get(platform, {}).get("unit", "call")
    if unit == "unique_key":
        return len({e.get("key") for e in entries if e.get("key")})
    return len(entries)


def remaining(platform, path=None, now=None):
    limit = LIMITS.get(platform, {}).get("limit", 0)
    return max(0, limit - usage(platform, path=path, now=now))


def hard_blocked_until(platform, path=None, now=None):
    """아직 유효한 hard_block 의 해제 시각 (없으면 None)."""
    current = _now(now)
    latest = None
    for rec in _read_all(path):
        if rec.get("kind") != KIND_HARD_BLOCK or rec.get("platform") != platform:
            continue
        until = _parse_at(rec.get("until"))
        if until and until > current and (latest is None or until > latest):
            latest = until
    return latest


def mark_hard_block(platform, detail="", path=None, now=None, hours=HARD_BLOCK_HOURS):
    """API 가 쿼터 초과를 알려온 순간 호출. 카운터와 무관하게 호출을 막는다."""
    path = Path(path) if path else LEDGER_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    until = _now(now) + timedelta(hours=hours)
    rec = {
        "at": _iso(_now(now)),
        "kind": KIND_HARD_BLOCK,
        "platform": platform,
        "until": _iso(until),
        "detail": detail,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def has_budget(platform, key, path=None, now=None):
    """이 key 로 지금 호출해도 한도를 넘지 않는지. (가능여부, 한국어 사유) 반환."""
    conf = LIMITS.get(platform)
    if conf is None:
        return False, f"알 수 없는 플랫폼: {platform}"

    blocked = hard_blocked_until(platform, path=path, now=now)
    if blocked:
        return False, f"API 쿼터 초과로 차단 중 (해제 예정 {_iso(blocked)})"

    entries = load_entries(platform, path=path, now=now)
    if conf["unit"] == "unique_key":
        used = {e.get("key") for e in entries if e.get("key")}
        if key in used:
            # 이번 윈도우에 이미 쓴 해시태그 — 다시 조회해도 고유 개수가 안 늘어난다.
            return True, ""
        if len(used) >= conf["limit"]:
            return False, f"고유 해시태그 {conf['limit']}개 소진 (7일 롤링)"
        return True, ""

    if len(entries) >= conf["limit"]:
        return False, f"쿼리 {conf['limit']}건 소진 (7일 롤링)"
    return True, ""


def record(platform, key, path=None, now=None):
    """호출 1건을 원장에 append.

    실패한 호출도 반드시 기록한다 — Meta 는 대부분의 한도에서 실패 호출도 세기 때문에,
    성공만 세는 낙관적 원장은 실제 예산을 넘겨 하드 리젝을 부른다.
    """
    path = Path(path) if path else LEDGER_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "at": _iso(_now(now)),
        "kind": KIND_CALL,
        "platform": platform,
        "key": key,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def prune(path=None, now=None, dry_run=False):
    """윈도우 밖 항목을 제거해 파일이 무한히 자라지 않게 한다.

    scripts/prune_prices.py 와 같은 관례. 유효한 hard_block 은 윈도우와 무관하게 남긴다.
    남은 항목 수를 반환.
    """
    path = Path(path) if path else LEDGER_FILE
    if not path.exists():
        return 0
    current = _now(now)
    cutoff = current - timedelta(days=WINDOW_DAYS)
    kept = []
    for rec in _read_all(path):
        if rec.get("kind") == KIND_HARD_BLOCK:
            until = _parse_at(rec.get("until"))
            if until and until > current:
                kept.append(rec)
            continue
        at = _parse_at(rec.get("at"))
        if at and at >= cutoff:
            kept.append(rec)
    if dry_run:
        return len(kept)
    with open(path, "w", encoding="utf-8") as f:
        for rec in kept:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(kept)
