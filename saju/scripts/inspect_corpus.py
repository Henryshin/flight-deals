"""코퍼스 조회 CLI — 자격증명 없이 결과를 눈으로 확인하는 창구.

걸러내기는 빌드가 아니라 '조회 시점'에 한다. 임계값을 바꿔가며 바로 확인할 수 있어야
튜닝이 가능하기 때문이다.

실행 예:
    python saju/scripts/inspect_corpus.py --stats
    python saju/scripts/inspect_corpus.py --label knowledge --limit 5
    python saju/scripts/inspect_corpus.py --group 십신 --min-score 0.5
    python saju/scripts/inspect_corpus.py --term 편관 --full
    python saju/scripts/inspect_corpus.py --duplicates
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

CORPUS_FILE = ROOT / "saju" / "data" / "corpus.jsonl"
STATS_FILE = ROOT / "saju" / "data" / "stats.json"


def read_corpus(path=None):
    path = Path(path) if path else CORPUS_FILE
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


def apply_filters(records, args):
    out = records
    if not args.include_dups:
        out = [r for r in out if r.get("is_canonical")]
    if args.label:
        wanted = {s.strip() for s in args.label.split(",")}
        out = [r for r in out if r.get("label") in wanted]
    if args.group:
        out = [r for r in out if args.group in (r.get("term_groups") or [])]
    if args.term:
        out = [r for r in out if args.term in (r.get("terms") or [])]
    if args.min_score is not None:
        out = [r for r in out if (r.get("quality") or 0) >= args.min_score]
    return sorted(out, key=lambda r: -(r.get("quality") or 0))


def print_record(rec, full=False):
    text = rec.get("text_clean") or ""
    if not full and len(text) > 220:
        text = text[:220] + " …"
    print(f"── [{rec.get('label')}] {rec.get('quality')} · {rec.get('uid')}")
    if rec.get("terms"):
        print(f"   용어: {', '.join(rec['terms'][:12])}")
    if rec.get("promo_flags"):
        print(f"   홍보: {', '.join(rec['promo_flags'])}")
    if rec.get("permalink"):
        print(f"   {rec['permalink']}")
    for line in text.split("\n"):
        print(f"   {line}")
    print()


def main(argv=None):
    ap = argparse.ArgumentParser(description="사주 코퍼스 조회")
    ap.add_argument("--label", help="knowledge,mixed,promo,fortune_feed,question,thin")
    ap.add_argument("--group", help="용어 그룹 (십신/오행/신살 …)")
    ap.add_argument("--term", help="특정 용어를 포함한 글")
    ap.add_argument("--min-score", type=float, help="품질 점수 하한")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--full", action="store_true", help="본문 전체 출력")
    ap.add_argument("--include-dups", action="store_true", help="복제글도 포함")
    ap.add_argument("--duplicates", action="store_true", help="복제 묶음만 표시")
    ap.add_argument("--stats", action="store_true", help="통계 요약")
    args = ap.parse_args(argv)

    records = read_corpus()
    if not records:
        print(f"코퍼스가 비어 있습니다: {CORPUS_FILE}")
        print("  -> python saju/scripts/build_corpus.py 를 먼저 실행하세요.")
        return 0

    if args.stats:
        if STATS_FILE.exists():
            print(STATS_FILE.read_text(encoding="utf-8").rstrip())
        else:
            print("stats.json 이 없습니다. build_corpus.py 를 실행하세요.")
        return 0

    if args.duplicates:
        dups = [r for r in records if not r.get("is_canonical")]
        if not dups:
            print("복제글이 없습니다.")
            return 0
        print(f"복제 {len(dups)}건:")
        for rec in dups[: args.limit]:
            print(f"  {rec.get('uid')}  ->  대표 {rec.get('dup_of')}")
        return 0

    hits = apply_filters(records, args)
    print(f"조건에 맞는 글 {len(hits)}건 (전체 {len(records)}건 중)\n")
    for rec in hits[: args.limit]:
        print_record(rec, full=args.full)
    return 0


if __name__ == "__main__":
    sys.exit(main())
