"""
data/prices.csv 가지치기: 계산에 더 이상 쓰이지 않는 오래된 관측치를 잘라내고,
가격 단위가 틀린 노이즈 행(USD 금액이 ₩ 로 들어온 507원 류)을 제거한다.

prices.csv는 append-only라 그냥 두면 무한정 커진다. build_dashboard_data.py가
실제로 쓰는 최대 lookback은 LOOKBACK_BASELINE_DAYS(60일, matrix.json의 평시
기준가)이므로, 그보다 넉넉한 PRUNE_RETENTION_DAYS(90일)보다 오래된 관측치는
어떤 계산에도 안 쓰인다. (history.json 차트는 lookback 제한 없이 남아있는
관측치를 전부 보여주므로, 너무 짧게 자르면 차트만 얇아진다 — 그래서 계산에
필요한 60일보다 여유를 둔 90일로 잡는다.)

노이즈 행: 2026-08-03~10-11 에 54행이 들어왔다 (ICN-ULN 2027-02-05 출발 507원 등). 판정 기준은
collector.google_flights_crawler.price_floor (노선 중앙값의 10% 와 30,000원 중 높은 쪽) 로
크롤러의 수집 시 검사와 같다. 제거한 행은 전부 stdout 에 찍는다 (Actions 로그에 남음).

실행: python scripts/prune_prices.py [--dry-run]

주의: prices.csv 는 .gitattributes 에서 merge=union 이라, 다른 커밋(동시 수집
런)이 이 스크립트가 지운 행을 다시 갖고 있으면 rebase 시 union 병합으로 되살아날
수 있다. 그래서 이 스크립트는 별도 워크플로/스케줄이 아니라, collect.yml 안에서
매 수집 직후·커밋 직전에 같은 잡(job) 안에서만 실행한다 (동시 쓰기 경합 자체를
피함).
"""
import csv
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from collector.google_flights_crawler import split_implausible_rows
from scripts.build_dashboard_data import LOOKBACK_BASELINE_DAYS

ROOT = Path(__file__).parent.parent
PRICES_FILE = ROOT / "data" / "prices.csv"

PRUNE_RETENTION_DAYS = 90
assert PRUNE_RETENTION_DAYS >= LOOKBACK_BASELINE_DAYS, (
    "보관 기간이 build_dashboard_data.py 의 lookback보다 짧으면 "
    "계산에 쓰이는 데이터가 삭제될 수 있습니다."
)


def _day(row):
    """collected_at 의 날짜 부분 (구 스키마는 날짜만, 신 스키마는 UTC 타임스탬프)."""
    return (row.get("collected_at") or "")[:10]


def prune(dry_run=False, today=None):
    if not PRICES_FILE.exists():
        print("prices.csv 없음 - 건너뜀")
        return
    today = today or date.today()
    cutoff = (today - timedelta(days=PRUNE_RETENTION_DAYS)).isoformat()

    with open(PRICES_FILE, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    if fieldnames is None:
        print("prices.csv 비어있음 - 건너뜀")
        return

    kept = [r for r in rows if _day(r) >= cutoff]
    removed_old = len(rows) - len(kept)
    print(f"전체 {len(rows)}행 중 {cutoff} 이전 관측치 {removed_old}행 제거 -> {len(kept)}행 남음")

    kept, noise = split_implausible_rows(kept)
    print(f"가격 단위 의심(노선 하한 아래) 행 {len(noise)}행 제거 -> {len(kept)}행 남음")
    for r in noise:
        print(f"  - {r.get('origin')}-{r.get('destination')} {r.get('depart_date')}~{r.get('return_date')} "
              f"stops={r.get('stops')} price={r.get('price')} collected_at={r.get('collected_at')} "
              f"airline={r.get('airline')}")
    removed = removed_old + len(noise)

    if removed == 0:
        return
    if dry_run:
        print("(--dry-run: 파일은 변경하지 않음)")
        return

    with open(PRICES_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept)


if __name__ == "__main__":
    prune(dry_run="--dry-run" in sys.argv)
