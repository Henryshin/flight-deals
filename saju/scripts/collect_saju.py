"""사주 게시물 수집 오케스트레이션 -> saju/data/posts.jsonl (append-only).

scripts/collect.py 의 구조를 따른다.
- 쿼리 하나가 끝날 때마다 바로 append -> 중간에 죽어도 부분 보존
- 쿼리별 상태/실패 사유를 collect_status.json 에 기록 (대시보드/사람이 원인 파악)
- 호출 '전에' 쿼터를 확인하고, 실패한 호출도 원장에 기록

여기서는 정규화·태깅·품질 판정을 하지 않는다. 원본만 그대로 쌓아두고 파생은
build_corpus.py 가 전담한다. 쿼터가 7일 500쿼리라 재수집이 사실상 불가능하므로,
용어 사전이나 품질 임계값을 고쳤을 때 '원본에서 다시 유도'할 수 있어야 하기 때문이다.

실행:
    python saju/scripts/collect_saju.py --collector sample
    python saju/scripts/collect_saju.py --collector threads --limit 10
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from saju.collectors import STATUS_OK, STATUS_QUOTA, get_collector  # noqa: E402
from saju.scripts import quota  # noqa: E402

DATA_DIR = ROOT / "saju" / "data"
POSTS_FILE = DATA_DIR / "posts.jsonl"
KEYWORDS_FILE = DATA_DIR / "keywords.json"
STATUS_FILE = DATA_DIR / "collect_status.json"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_queries(only=None):
    """keywords.json 에서 수집 대상 검색어 목록. priority 오름차순."""
    if not KEYWORDS_FILE.exists():
        return []
    try:
        data = json.loads(KEYWORDS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"keywords.json 읽기 실패: {e}")
        return []
    queries = data.get("queries") or []
    if only:
        queries = [q for q in queries if q.get("value") == only]
    return sorted(queries, key=lambda q: (q.get("priority", 99), q.get("value", "")))


def existing_uids():
    """이미 저장된 uid 집합. merge=union 으로 같은 줄이 두 번 들어갔어도 흡수한다."""
    uids = set()
    if not POSTS_FILE.exists():
        return uids
    with open(POSTS_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                uids.add(json.loads(line).get("uid"))
            except ValueError:
                continue
    uids.discard(None)
    return uids


def append_posts(records):
    """레코드를 JSONL 로 append. 반드시 한 줄에 하나 (union merge 안전성의 근거)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(POSTS_FILE, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")


def to_record(post, collected_at):
    """RawPost -> posts.jsonl 레코드 (수집 시점의 사실만)."""
    return {
        "uid": post.uid,
        "platform": post.platform,
        "post_id": post.post_id,
        "permalink": post.permalink,
        "author": post.author,
        "posted_at": post.posted_at,
        "collected_at": collected_at,
        "query": post.query,
        "query_mode": post.query_mode,
        "media_type": post.media_type,
        "text_raw": post.text,
        "api_fields_missing": post.api_fields_missing,
    }


def write_collect_status(updates):
    """쿼리별 수집 상태를 병합 기록.

    이번 런에서 다루지 않은 쿼리의 기존 상태는 보존한다 (--only 런이 나머지를
    지우지 않도록). scripts/collect.py 의 write_collect_status 와 같은 계약.
    """
    data = {}
    if STATUS_FILE.exists():
        try:
            data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            data = {}
    queries = data.get("queries") if isinstance(data.get("queries"), dict) else {}
    queries.update(updates)
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATUS_FILE.write_text(
        json.dumps(
            {"updated_at": now_iso(), "queries": queries},
            ensure_ascii=False, indent=2, sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )


def main(argv=None):
    ap = argparse.ArgumentParser(description="사주 게시물 수집")
    ap.add_argument("--collector", default="sample", help="sample | threads | instagram")
    ap.add_argument("--limit", type=int, default=25, help="검색 1회당 가져올 최대 건수")
    ap.add_argument("--max-queries", type=int, default=20, help="이번 런의 최대 검색 횟수")
    ap.add_argument("--only", help="이 검색어 하나만 수집")
    ap.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않고 결과만 출력")
    args = ap.parse_args(argv)

    try:
        collector = get_collector(args.collector)
    except KeyError as e:
        print(e)
        return 2

    ok, why = collector.available()
    if not ok:
        # 앱 심사 전에는 이게 정상 경로다. 크래시가 아니라 안내 후 정상 종료해야
        # cron 이 실패로 물들지 않는다.
        print(f"[{collector.name}] 사용 불가: {why}")
        print("  -> 토큰을 설정하거나 --collector sample 로 픽스처를 사용하세요.")
        if not args.dry_run:
            write_collect_status({
                f"{collector.name}:*": {
                    "status": "auth", "detail": why, "checked_at": now_iso(),
                }
            })
        return 0

    queries = load_queries(only=args.only)
    if not queries:
        print("수집할 검색어가 없습니다 (saju/data/keywords.json 확인).")
        return 0

    seen = existing_uids()
    print(f"[{collector.name}] 검색어 {len(queries)}개, 기존 적재 {len(seen)}건")

    updates = {}
    total_new = 0
    calls = 0

    for q in queries:
        if calls >= args.max_queries:
            print(f"  이번 런 검색 한도({args.max_queries}) 도달 — 나머지는 다음 런에서")
            break

        value = q.get("value") or ""
        mode = q.get("mode") or "keyword"
        if mode not in collector.modes:
            continue

        qkey = collector.quota_key(value, mode)
        allowed, reason = quota.has_budget(collector.platform, qkey)
        if not allowed:
            print(f"  [{value}] 쿼터: {reason}")
            updates[f"{collector.name}:{value}"] = {
                "status": STATUS_QUOTA, "detail": reason, "checked_at": now_iso(),
            }
            continue

        result = collector.search(value, mode=mode, limit=args.limit)
        calls += 1
        if not args.dry_run:
            # 실패한 호출도 기록한다 — Meta 는 대부분의 한도에서 실패도 카운트한다.
            quota.record(collector.platform, result.quota_key or qkey)

        collected_at = now_iso()
        fresh = [p for p in result.posts if p.uid not in seen]
        for p in fresh:
            seen.add(p.uid)

        if result.status == STATUS_OK and fresh and not args.dry_run:
            append_posts([to_record(p, collected_at) for p in fresh])
        total_new += len(fresh)

        updates[f"{collector.name}:{value}"] = {
            "status": result.status,
            "detail": result.detail,
            "found": len(result.posts),
            "new": len(fresh),
            "checked_at": collected_at,
        }
        print(f"  [{value}] {result.status} 조회 {len(result.posts)}건 / 신규 {len(fresh)}건"
              + (f" — {result.detail}" if result.detail else ""))

        if result.status == STATUS_QUOTA and not args.dry_run:
            # API 가 직접 쿼터 초과를 알려온 경우, 카운터와 무관하게 잠시 차단한다.
            quota.mark_hard_block(collector.platform, result.detail)

    if not args.dry_run:
        write_collect_status(updates)
        quota.prune()

    used = quota.usage(collector.platform)
    left = quota.remaining(collector.platform)
    print(f"완료: 검색 {calls}회, 신규 {total_new}건 (쿼터 {used} 사용 / {left} 남음)")
    if args.dry_run:
        print("  (--dry-run: 파일을 쓰지 않았습니다)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
