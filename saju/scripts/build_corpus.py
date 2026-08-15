"""posts.jsonl -> corpus.jsonl + stats.json (파생물 전량 재생성).

scripts/build_dashboard_data.py 와 같은 위치의 스크립트다 — 원본은 건드리지 않고
파생만 다시 만든다. 용어 사전이나 품질 임계값을 고치면 이 스크립트만 다시 돌리면 된다.

corpus.jsonl 에는 저품질/광고 글도 '전부' 들어간다. 걸러내는 건 조회 시점
(inspect_corpus.py 의 --label/--min-score)이다. 이유:
- 쿼터(7일 500쿼리)상 지운 데이터는 되살릴 수 없다.
- 첫 임계값은 반드시 틀린다. 다시 맞추려면 걸러진 쪽도 남아 있어야 한다.
- 광고 글은 분류기 정밀도를 재는 유일한 음성 표본이다.

출력은 결정적이어야 한다 (uid 정렬, 타임스탬프 없음). 안 그러면 재빌드마다
파일이 흔들려 워크플로가 의미 없는 커밋을 남긴다.

실행: python saju/scripts/build_corpus.py
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from saju.scripts.dedup import dedupe_by_uid, mark_duplicates  # noqa: E402
from saju.scripts.normalize import content_hash, normalize  # noqa: E402
from saju.scripts.quality import (  # noqa: E402
    KNOWLEDGE_MIN, content_score, label_post, score_post,
)
from saju.scripts.terms import tag_terms  # noqa: E402

DATA_DIR = ROOT / "saju" / "data"
POSTS_FILE = DATA_DIR / "posts.jsonl"
CORPUS_FILE = DATA_DIR / "corpus.jsonl"
STATS_FILE = DATA_DIR / "stats.json"

# 사전/점수 로직이 바뀌면 올린다. 어떤 레코드를 다시 유도해야 하는지 판단하는 근거.
TERMS_VERSION = 1
QUALITY_VERSION = 1


def read_posts(path=None):
    path = Path(path) if path else POSTS_FILE
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


def analyze(rec):
    """원본 레코드 하나 -> 분석 필드가 붙은 코퍼스 레코드."""
    norm = normalize(rec.get("text_raw") or "")
    text_clean = norm["text_clean"]
    terms, groups = tag_terms(text_clean)
    score = score_post(text_clean, terms, groups, norm["promo_flags"], norm["hashtags"])
    content = content_score(text_clean, terms, groups, norm["hashtags"])
    label = label_post(
        text_clean, score, norm["promo_flags"], rec.get("media_type"), content=content,
    )

    out = dict(rec)
    out.update({
        "text_clean": text_clean,
        "char_len_raw": len(rec.get("text_raw") or ""),
        "char_len_clean": len(text_clean),
        "hashtags": norm["hashtags"],
        "promo_flags": norm["promo_flags"],
        "content_hash": content_hash(text_clean),
        "terms": terms,
        "term_groups": groups,
        "term_count": len(terms),
        "quality": score,
        "content_score": content,   # 홍보 감점 이전 — 임계값 튜닝용으로 남긴다
        "label": label,
        "terms_version": TERMS_VERSION,
        "quality_version": QUALITY_VERSION,
    })
    return out


def build_stats(records):
    """결정적 통계 (타임스탬프 없음 — 재빌드 시 diff 가 나지 않게)."""
    canonical = [r for r in records if r.get("is_canonical")]
    term_counter = Counter()
    group_counter = Counter()
    for r in canonical:
        term_counter.update(r.get("terms") or [])
        group_counter.update(r.get("term_groups") or [])

    return {
        "total": len(records),
        "canonical": len(canonical),
        "duplicates": len(records) - len(canonical),
        "by_label": dict(sorted(Counter(r.get("label") for r in canonical).items())),
        "by_platform": dict(sorted(Counter(r.get("platform") for r in records).items())),
        "by_term_group": dict(sorted(group_counter.items())),
        # most_common() 은 동점일 때 삽입 순서로 자른다 -> 입력 순서에 따라 결과가
        # 달라진다. 빈도 내림차순 + 용어 사전순으로 명시 정렬해 결정성을 보장한다.
        "top_terms": dict(
            sorted(term_counter.items(), key=lambda kv: (-kv[1], kv[0]))[:30]
        ),
        "knowledge_threshold": KNOWLEDGE_MIN,
        "above_threshold": sum(
            1 for r in canonical if (r.get("quality") or 0) >= KNOWLEDGE_MIN
        ),
        "terms_version": TERMS_VERSION,
        "quality_version": QUALITY_VERSION,
    }


def main(argv=None):
    posts = read_posts()
    if not posts:
        print(f"원본이 비어 있습니다: {POSTS_FILE}")
        print("  -> python saju/scripts/collect_saju.py --collector sample 을 먼저 실행하세요.")
        return 0

    raw_count = len(posts)
    posts = dedupe_by_uid(posts)
    analyzed = [analyze(r) for r in posts]
    records = mark_duplicates(analyzed)
    # 결정적 출력 — uid 정렬
    records.sort(key=lambda r: r.get("uid") or "")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(CORPUS_FILE, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")

    stats = build_stats(records)
    STATS_FILE.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    dropped = raw_count - len(posts)
    print(f"원본 {raw_count}행 (uid 중복 {dropped}행 제거) -> 코퍼스 {len(records)}건")
    print(f"  대표 {stats['canonical']}건 / 복제 {stats['duplicates']}건")
    print(f"  라벨: {stats['by_label']}")
    print(f"  임계값 {KNOWLEDGE_MIN} 이상: {stats['above_threshold']}건")
    print(f"  -> {CORPUS_FILE.relative_to(ROOT)}, {STATS_FILE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
