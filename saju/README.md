# 사주 지식 코퍼스 (`saju/`)

인스타그램·스레드의 사주 게시물을 모아 **검색·RAG에 쓸 수 있는 지식 코퍼스**로 만드는
파이프라인. 항공권 대시보드와는 독립적으로 동작하며, 서로의 데이터·워크플로를 건드리지 않는다.

의존성은 추가하지 않았다 — 표준 라이브러리(`urllib`, `json`, `hashlib`, `unicodedata`)만 쓴다.
`requirements.txt` 는 그대로 `playwright` 한 줄이다.

---

## 지금 상태: 스캐폴딩 완료, 실수집 대기

Meta API 토큰이 아직 없다. 그래서 **정규화 → 용어 태깅 → 품질 판정 → 중복 표시 →
코퍼스 빌드** 전 구간을 픽스처로 완결해 두고, 실제 수집기(Threads/Instagram)는
같은 인터페이스로 작성만 해 두었다. 토큰이 생기면 `--collector` 만 바꾸면 된다.

실수집기도 `_transport` 주입으로 **토큰 없이 필드 매핑과 오류 분류가 테스트되고 있다.**

```bash
python saju/tests/test_saju.py                          # 32개 테스트 (네트워크·자격증명 불필요)
python saju/scripts/collect_saju.py --collector sample  # 픽스처 -> data/posts.jsonl
python saju/scripts/build_corpus.py                     # -> data/corpus.jsonl + stats.json
python saju/scripts/inspect_corpus.py --label knowledge --limit 5
python saju/scripts/inspect_corpus.py --stats
python saju/scripts/inspect_corpus.py --duplicates
```

토큰 없이 `--collector threads` 를 돌리면 크래시가 아니라 안내 후 정상 종료(exit 0)한다.
cron 이 빨간불로 물들지 않게 하기 위한 것이다.

---

## ⚠️ 원문은 커밋하지 않는다

이 레포는 **public** 이고 GitHub Pages 도 붙어 있다. 타인의 게시물 본문을 그대로 커밋하면
제3자 저작물을 공개 재배포하는 것이 된다. 그래서 기본값을 이렇게 잡았다.

| 파일 | 커밋 | 이유 |
|---|:---:|---|
| `data/posts.jsonl` (원문) | ✗ | `.gitignore`. 게시물 본문 |
| `data/corpus.jsonl` (원문+분석) | ✗ | `.gitignore`. 게시물 본문 포함 |
| `data/stats.json` | ✓ | 집계만, 본문 없음 |
| `data/quota.jsonl` | ✓ | 런 사이에 롤링 한도를 이어 세려면 필수 |
| `data/collect_status.json` | ✓ | 실패 원인 추적 |
| `data/hashtag_ids.json` | ✓ | 태그 ID 캐시 — 있으면 IG 1단계 호출을 아낀다 |

원문을 함께 관리하려면 레포를 private 으로 돌린 뒤 `.gitignore` 의 해당 두 줄을 지우면 된다.
`.gitattributes` 에는 그때를 대비한 union merge 설정이 이미 들어 있다.

---

## 데이터 접근의 실제 제약

| 플랫폼 | 엔드포인트 | 한도 | 선결 조건 |
|---|---|---|---|
| Threads | `/keyword_search` (`search_mode=TAG` 지원) | **7일 롤링 500쿼리** | `threads_keyword_search` 앱 심사 |
| Instagram | `ig_hashtag_search` → `recent_media` | **7일 롤링 고유 해시태그 30개** | 비즈니스/크리에이터 계정 + 앱 심사 |

직접 스크래핑은 Meta ToS 위반이라 설계에서 제외했다.

**두 플랫폼의 과금 단위가 다르다는 점이 이 코드의 핵심 함정이다.**
Threads 는 *호출 1건 = 1쿼리*, Instagram 은 *호출 수가 아니라 윈도우 내 고유 해시태그 수*가
한도다. 즉 IG 는 **이미 쓴 태그를 다시 조회하는 게 공짜**다. 이걸 호출당 과금으로 잘못 구현하면
"주당 태그 4개밖에 못 본다"고 착각하게 된다. 실제로는 핵심 태그 ~20개를 고정해두고
**매일** 돌리는 게 맞다 — `recent_media` 가 최근 24시간만 돌려주기 때문에 더 느리게 돌면 누락된다.

`data/quota.jsonl` 이 이 규칙을 플랫폼별로 나눠 관리한다. API 가 쿼터 초과를 직접 알려오면
카운터와 무관하게 24시간 `hard_block` 을 건다 — 원장은 추정치고 진실은 API 쪽에 있기 때문이다.

---

## 구조

```
saju/
├── collectors/          수집기 (교체 가능)
│   ├── base.py          STATUS_*, RawPost, CollectResult, Collector
│   ├── _http.py         urllib + Meta 오류코드 -> STATUS_* 분류
│   ├── sample.py        픽스처 재생 (자격증명 불필요)
│   ├── threads.py       /keyword_search
│   └── instagram.py     ig_hashtag_search -> recent_media (2단계 + ID 캐시)
├── scripts/
│   ├── collect_saju.py  오케스트레이션 -> posts.jsonl
│   ├── normalize.py     NFC·제로폭·이모지·해시태그·홍보문구 (순수 함수)
│   ├── terms.py         사주 용어 사전 + 문맥 기반 태깅
│   ├── quality.py       설명력 점수 + 라벨
│   ├── dedup.py         중복 '표시' (삭제 아님)
│   ├── quota.py         롤링 7일 원장
│   ├── build_corpus.py  posts.jsonl -> corpus.jsonl + stats.json
│   └── inspect_corpus.py  조회 CLI
├── fixtures/            샘플 게시물 (라벨 정답 포함)
├── data/                keywords.json(검색어 설정) + 산출물
└── tests/test_saju.py   손수 만든 러너 (pytest 미사용)
```

원본(`posts.jsonl`)과 파생(`corpus.jsonl`)을 나눈 것은 취향이 아니라 **쿼터 때문**이다.
7일 500쿼리로는 재수집이 사실상 불가능하므로, 용어 사전이나 품질 임계값을 고쳤을 때
원본에서 다시 유도할 수 있어야 한다. `collect_saju.py` 는 분석을 일절 하지 않는다.

---

## 설계에서 조심한 것들

**한글 NFC 정규화.** macOS/iOS 발 한글은 NFD(자모 분리)로 도착하는 경우가 있다. 눈으로는
같아 보여도 `"정관" in text` 가 조용히 False 가 되어 용어 사전 전체가 그 글을 놓친다.
파이프라인 맨 앞에서 NFC 로 모은다.

**1글자 용어 오탐.** 오행의 목/화/토/금/수는 목요일·금요일·수업처럼 일상 한국어에 흔한
음절이다. 단순 부분문자열 매칭이면 모든 한국어 글이 사주 글로 태깅된다. 그래서 사전을
둘로 나눴다 — 다글자 용어는 직접 매칭, 1글자는 문맥 정규식(`목 기운`, `목일간`, `오행 … 목`)을
만족해야만 인정한다. 십신 `상관` 도 `상관없다/상관관계` 를 배제한 뒤에 잡는다.

**제로폭 문자.** 홍보 계정은 `상​담​문​의` 처럼 제로폭 문자를 끼워 필터를 피한다.
홍보 문구 탐지 '전에' 제거해야 한다.

**본문 중간의 해시태그.** 한국 사주 글은 `#정관 이 강하면…` 처럼 해시태그를 그냥 명사로 쓴다.
말미 태그 블록만 걷어내고, 문장 속 태그는 `#` 기호만 떼어 낱말을 살린다.

**라벨이 필요한 이유.** '사주'로 검색하면 지식도 광고도 아닌 것이 대량으로 딸려온다 —
`제 사주 좀 봐주세요`(질문글)와 `오늘의 띠별 운세`(자동 생성 피드)다. 점수 하나로는 애매한
중간값으로 뭉개지므로 라벨을 따로 둔다: `knowledge / mixed / promo / fortune_feed / question / thin`.

**`mixed` 를 버리지 않는 이유.** 한국 사주 글에서 가장 흔한 형태가 *제대로 된 십신 해설 +
말미에 상담 문의* 다. 광고를 이분법으로 자르면 실제 지식의 상당수가 함께 날아간다.
홍보 '줄'만 걷어내고 해설은 남긴다.

**mixed/promo 판정은 홍보 감점 이전 점수로 한다.** 최종 점수로 판정하면 감점이 두 번
반영되어("광고가 붙었으니 감점" → "점수가 낮으니 광고") 멀쩡한 해설글이 광고로 분류된다.
그래서 `content_score`(홍보 감점 전)를 따로 계산해 레코드에 남긴다.

**걸러내기는 빌드가 아니라 조회 시점에.** `corpus.jsonl` 에는 광고글도 전부 들어간다.
첫 임계값은 반드시 틀리고, 다시 맞추려면 걸러진 쪽도 남아 있어야 한다. 광고글은 분류기
정밀도를 잴 유일한 음성 표본이기도 하다.

**중복은 표시만.** 삭제하지 않고 `dup_of` / `is_canonical` 을 붙인다. 대표 선정은
*가장 먼저 올라온 글, 동률이면 uid 사전순* 으로 **결정적**이다 — 입력 순서에 따라 대표가
바뀌면 재빌드마다 `corpus.jsonl` 이 흔들려 워크플로가 의미 없는 커밋을 남긴다.
`stats.json` 에 타임스탬프를 넣지 않은 것도 같은 이유다.

**`STATUS_PERMISSION` 과 `STATUS_AUTH` 는 다르다.** "토큰은 멀쩡한데 스코프 미승인"은
앱 심사 통과 전의 **정상 상태**다. 이걸 토큰 고장으로 뭉뚱그리면 엉뚱한 곳을 보게 된다.

---

## 앱 심사 통과 후 할 일

1. Meta 개발자 앱 생성 → Threads 제품 추가 → `threads_keyword_search` 권한 심사 제출
   (스크린캐스트 기반 심사다. "개인 코퍼스 구축"은 약한 제출 사유라 반려 가능성을 염두에 둘 것)
2. Instagram: 비즈니스/크리에이터 계정 전환 → Facebook 페이지 연결 →
   `instagram_basic` + `instagram_manage_insights` (해시태그 검색은 insights 권한을 요구한다)
3. 토큰을 GitHub Actions secrets 로: `THREADS_ACCESS_TOKEN`, `IG_ACCESS_TOKEN`, `IG_USER_ID`
   — **장기 토큰도 60일이면 만료된다.** 조용히 만료되면 모든 런이 `STATUS_AUTH` 가 되면서
   몇 주치 수집을 잃는다. 갱신 일정을 따로 잡아둘 것.
4. 대량 실행 전에 픽스처 대조: `--collector threads --max-queries 1 --dry-run` 으로 실제 응답을
   받아 `tests/test_saju.py` 의 가짜 payload 와 다른 점을 맞춘다. 여기서 픽스처가 추측이 아니게 된다.
5. 예산 설정: Threads 500/7일 ≈ 하루 71건 → `--max-queries 40` 정도로 여유를 둔다.
   Instagram 은 핵심 해시태그를 30개 아래로 고정하고 **매일** 돌린다.
6. `.github/workflows/saju.yml` 추가. 기존 `collect.yml`(4시간 cron)과 스케줄이 겹치지 않게 하고,
   동시 실행을 막아야 한다(`concurrency`) — 쿼터 원장이 겹쳐 쓰이면 한도 계산이 흔들린다.

---

## 알려진 한계

- **Instagram 경로는 가성비가 낮다.** 작성자 정보를 주지 않고, `recent_media` 는 24시간만
  커버하며, 주당 고유 해시태그 30개 제한이 있다. 지식 코퍼스 목적에선 Threads 쪽이 훨씬 유용하다.
- **소셜 게시물은 지식 소스로서 한계가 뚜렷하다.** 짧은 단편이고 출처가 없으며 민간 속설이
  섞여 있다. 이걸 근거로 RAG 답변을 만들면 신뢰도 문제가 생긴다. 7일 500쿼리 예산이면
  수개월 모아도 규모가 작다. *체계적인 사주 지식* 이 목표라면 서적·고전 원문이 더 나은 소스다.
  다만 **"사람들이 실제로 사주를 어떻게 이야기하는가"** 를 담는 것이 목적이라면 이 파이프라인이
  정확히 맞다. 후자라면 지금 구조 그대로 쓰면 되고, 전자라면 같은
  정규화→태깅→품질→중복 파이프라인에 고전 텍스트 수집기를 하나 더 붙이는 편이 낫다.
- **근접 중복은 아직 못 잡는다.** 본문이 같고 장식만 다른 복붙은 잡지만(정규화 후 해싱),
  문장 하나가 삽입된 변형은 별개로 남는다. 필요해지면 simhash 를 추가할 지점이다.
