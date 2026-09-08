# 항공권 특가 대시보드 (GitHub 자동 운영)

연휴를 낀 항공권 특가를 GitHub Actions가 4시간마다 자동 수집하고, GitHub Pages로 대시보드를 공개합니다.
로컬 PC를 켜둘 필요 없이 GitHub에서 알아서 돌아갑니다.

## 구조

```
data/routes.json      # 관심 노선 등록 (여기에 추가/삭제)
data/prices.csv        # 가격 이력 (Actions가 자동으로 append & 커밋, merge=union)
data/collect_status.json  # 노선별 수집 상태/실패 사유 (collect.py가 기록)
scripts/collect.py      # 크롤링 실행 → prices.csv에 append (연휴 + 평시 기준가)
scripts/build_dashboard_data.py  # prices.csv → docs/data/*.json 생성 (할증률 matrix 포함)
docs/index.html        # GitHub Pages가 서빙하는 대시보드
.github/workflows/collect.yml   # 4시간마다 자동 수집하는 cron (data/ 만 커밋)
.github/workflows/build.yml     # data/** push 시 docs/data/*.json 재생성·커밋
tests/test_smoke.py    # 브라우저 없이 도는 스모크 테스트 (python tests/test_smoke.py)

blog/                  # 블로그 글감 추출·검증·카드 렌더 (표준 라이브러리 + playwright)
scripts/blog_brief.py  # 오늘 쓸 만한 소재 브리핑 / 소재별 상세 자료
scripts/blog_save.py   # 세션에서 쓴 초안 → posts/ 저장 + 가격 카드 PNG
posts/                 # 생성된 글과 브리핑 (blog-brief.yml 이 커밋하는 유일한 경로)
publisher/README.md    # 발행은 별도 검증 도구가 담당 — 여기 자동화 코드 없음
.claude/skills/blog-post/  # 글을 쓸 때 따르는 문체·구조 규칙
.github/workflows/blog-brief.yml  # 매일 07:00 KST 소재 브리핑 (posts/ 만 커밋)
```

수집과 빌드가 분리되어 있어서:
- 노선 등록/삭제는 다음 수집을 기다리지 않고 1분 내 대시보드에 반영됩니다.
- 수십 분짜리 수집 런이 생성 파일(docs/data) 때문에 rebase 충돌로 죽는 일이 없습니다
  (prices.csv 는 `.gitattributes` 의 `merge=union` 으로 동시 런과도 자동 병합).

### 연휴 가성비 (할증률)

수집기는 연휴 날짜 외에 노선당 하루 1회 **평시(비연휴) 기준가**도 수집합니다.
`build_dashboard_data.py` 가 연휴 윈도우별로 `할증률 = 연휴 최저가 / 평시 기준가 중앙값` 을
계산해 `docs/data/matrix.json` 으로 내보내고, 대시보드의 "연휴 가성비 보드"가
연휴를 골라 할증률 낮은 순으로 여행지를 랭킹합니다. 기준가 표본이 3개 이상 쌓여야
정식(티어 A) 지표로 표시되며, 그 전에는 "기준가 수집 중"으로 나옵니다.

### 두 개의 보드 (같은 matrix.json, 축만 반대)

`matrix.json` 은 `cells[연휴id][모니터id]` 형태의 2차원 표라, 어느 축으로도 읽을 수 있습니다.

| 보드 | 질문 | 읽는 방향 |
| --- | --- | --- |
| **연휴 특가 보드** | "이 연휴엔 어디가 싼가" | 연휴 고정 → 여행지 랭킹 |
| **여행지별 연휴 보드** | "이 여행지는 어느 연휴에 가야 싼가" | 여행지 고정 → 연휴 나열 |

여행지별 연휴 보드는 여행지 행을 클릭하면 그 아래로 연휴별 최저가가 **싼 순서대로**
펼쳐지고, 오른쪽 막대로 연휴 간 가격 차이를 비교할 수 있습니다. 노선 관리(수집 상태,
목표가, 박수/경유 수정 ✎, 삭제 ✕)도 이 보드에 그대로 들어 있습니다 — 모니터가 둘 이상인
노선(예: 직항 전용 + 경유 허용)은 펼친 패널 안에서 모니터별로 다룹니다.
두 보드 모두 프론트엔드에서 같은 `matrix.json` 을 읽으므로 수집·빌드 파이프라인은 동일합니다.

### 휴일 가치 (비용-편익 추천)

최저가만 좇으면 '목금토일 연휴에 화~월'처럼 연차 효율이 나쁜 일정이 추천되는 문제가
있어, 일정마다 **연차 소모 수**와 **덤 휴일**(여행 기간 안에서 연차 없이 얻는 날 수 —
주말·공휴일)을 계산합니다. 덤 휴일은 반드시 여행 기간 **안**에서만 셉니다. 출발 전/귀국
후에 집에서 보내는 인접 주말은 그 여행과 무관하므로 세지 않습니다.

- 수집기(`collect.py`)는 연휴 구간 안 후보 외에, 출발/귀국을 인접 주말 경계까지 밀어
  같은 연차로 여행 기간 안의 덤 휴일이 더 많은 **앵커 후보**도 크롤합니다 (성탄절~신정
  브릿지 포함). 후보 총수가 상한을 넘으므로 날짜 기반 회전으로 며칠에 걸쳐 전부 순환
  수집됩니다.
- 빌드(`build_dashboard_data.py`)는 셀마다 (가격↑, 덤휴일↑) **파레토 프런티어** 일정
  목록(`pairs`, 최대 6개)을 내보냅니다. 덤 휴일 = 여행일 − 연차.
- 대시보드 필터의 **휴일 가치**(하루 = n만원, 기본 10)를 설정하면
  `실질 비용 = 가격 − 가치 × 덤휴일` 이 최소인 일정을 추천합니다. 0이면 항상 최저가.
  재빌드 없이 브라우저에서 즉시 재계산되며 값은 브라우저에 저장됩니다.
- 휴일 가치는 **어느 연휴를 갈지**에도 적용됩니다. 두 보드의 대표 연휴를 실질비용이
  가장 낮은 연휴로 고르고(특가 보드는 티어를 먼저 보아 '수집 중' 칸이 대표가 되는 것은
  막습니다), 여행지별 보드 펼침 패널에서 `실질 최적`(파란 배지)과 `최저`(초록 배지)를
  따로 표시합니다. 가치가 0이면 예전과 같이 '가장 싼 연휴'가 대표입니다.

**연차 범위** (최소 ~ 최대. 기본 상한 5개, 각각 비우면 그쪽 제한 없음)

실질 비용 공식은 덤 휴일만 편익으로 보고 **연차 소모에는 페널티가 없습니다.** 덤 휴일은
여행이 길어지면 기계적으로 늘어나므로(주말 하나 낄 때마다 +2), 한도가 없으면 '연차 9개
쓰는 14박'처럼 연차를 태우는 일정이 추천으로 올라옵니다. 연차 상한은 그 예산 제약입니다.
반대쪽인 **하한**은 "이왕 가는 김에 최소 N개는 쓰겠다"는 쪽 — 하한이 없으면 연차 1개짜리
3박 일정만 잔뜩 추천되어, 긴 휴가를 노리는 경우에는 쓸 수 없는 목록이 됩니다.

- 범위를 벗어난 일정은 추천 후보에서 제외합니다 (`eligiblePairs`). 최저가 열은
  `cell.min_price`(= `pairs[0]`)와 묶여 있어 그대로 두고, 추천만 범위 안에서 고릅니다.
- 대표로 보여줄 일정을 고르는 `displayPair` 도 **위아래 양쪽**을 봅니다. 상한만 보던
  때는 하한이 없어 문제가 없었지만, 하한이 생긴 뒤로 그대로 두면 '연차 1개짜리 최저가'가
  하한을 무시하고 대표 자리에 남아, 바로 아래 '대신 추천'만 범위를 지키는 모순이 납니다.
- 범위 안에 후보가 하나도 없으면 `연차 N~M개 범위에서 갈 수 있는 연휴가 없습니다` 로
  알립니다 — 갈 수 없는 일정을 추천처럼 두지 않기 위함입니다.
- 최소 > 최대로 뒤집어 입력하면 두 값을 서로 바꿔 넣습니다(그대로 두면 어떤 일정도
  통과하지 못해 화면이 통째로 비어 버립니다).
- 상한이 낮을수록 후보가 급격히 줄어듭니다(실측: 상한 5 → 561/562 셀에 후보 있음,
  상한 4 → 453/562, 상한 3 → 251/562). 장거리 노선은 최소 박수 때문에 연차 4~5개가
  기본이라, 기본값을 5로 두고 필요하면 사용자가 조입니다.

## 노선 추가/삭제

`data/routes.json`을 수정해서 커밋 & 푸시하면 다음 수집 주기(최대 4시간 내)부터 반영됩니다.

```json
{"origin": "ICN", "destination": "BKK", "label": "인천->방콕"}
```

- `origin`/`destination`은 IATA 공항 코드
- 구글 플라이트 크롤러는 도시명을 인식하므로, 새 공항 코드를 쓰려면 `collector/google_flights_crawler.py`의 `AIRPORT_CITY` 표에도 추가해야 함

## 네이버 블로그 자동 포스팅

수집한 가격 데이터로 네이버 블로그 **글을 만드는 데까지**가 이 저장소의 범위입니다.
네이버에 올리는 일은 검증된 별도 도구가 하고, 발행 버튼은 사람이 누릅니다.

### 자동화되는 것 / 사람이 하는 것

```
[GitHub Actions]                    [Claude Code 세션]         [PC — 별도 검증된 도구]

blog-brief.yml (매일 07:00 KST)
  └ scripts/blog_brief.py
      └ posts/_brief/YYYY-MM-DD.md ──▶ "오늘 이거 써줘"
         (오늘 쓸 만한 소재 8건)         └ 데이터 읽고 글 작성
                                         └ scripts/blog_save.py
                                             └ posts/<날짜>-<노선>-<연휴>/
                                                                 │
                                                                 ▼
                                                    98.네이버블로그자동화_v1.0
                                                      login.py (사람이 로그인)
                                                      post.py  (제목·본문·태그)
                                                                 │
                                             사람: 사진 넣고 · 읽어보고 · 발행
```

글은 매번 세션에서 새로 씁니다. 템플릿을 기계적으로 채우지 않습니다 —
문체·구조 규칙은 `.claude/skills/blog-post/SKILL.md` 에 있습니다.

### 발행 자동화는 이 저장소에 없습니다

네이버 블로그 글쓰기 API 는 2020-05-06 종료됐고, 공식 자동 발행 경로가 없습니다.
남은 방법은 SmartEditor 브라우저 자동화뿐인데, 그건 **이미 검증된 별도 도구**
(`98.네이버블로그자동화_v1.0/`)가 담당합니다. 여기서 다시 만들지 마세요 —
`publisher/README.md` 에 이유와 연결 방법이 있습니다.

이 저장소는 `posts/<슬러그>/post.json` 까지만 책임집니다.

계정 정보는 저장소·GitHub Secrets 어디에도 두지 않습니다.

### 쓰는 법

```bash
python scripts/blog_brief.py --stdout                        # 오늘의 소재 후보
python scripts/blog_brief.py --material ICN-TAK --window 2026-09-24   # 상세 자료
python scripts/blog_save.py draft.md --dry-run               # 검증 + 미리보기
python scripts/blog_save.py draft.md                         # 저장 + 카드 렌더
```

### 가격 신뢰성 가드레일

`blog/data.py` 가 **글에 써도 되는 값만** 통과시킵니다. 이게 없으면 안 됩니다 —
`data/prices.csv` 에는 크롤러의 `PRICE_PATTERN` 이 덜 렌더된 페이지에서 `₩333`
같은 값을 잡아 넣은 행이 15개 있고, 그 결과 `docs/data/matrix.json` 에
**"밀라노 왕복 755원 (tier A, 할증률 99.9%)"** 셀이 실제로 들어 있습니다.
가드레일 없이 "최저가" 글을 쓰면 1위가 밀라노 755원이 됩니다.

| 상수 | 값 | 뜻 |
|---|---|---|
| `PRICE_FLOOR_KRW` | 50,000 | ICN 출발 국제선 왕복 절대 하한 |
| `MIN_PLAUSIBLE_RATIO` | 0.25 | `min_price / typical` 하한 (실제 최고 특가가 0.47) |
| `MAX_PLAUSIBLE_DEAL_PCT` | 75 | 이보다 큰 할증률은 아티팩트 |
| `MAX_DATA_AGE_HOURS` | 30 | 데이터가 오래되면 글을 쓰지 않는다 |
| `MIN_CELL_OBS` / `MIN_CELL_PAIRS` | 6 / 2 | 관측이 얇은 셀은 제외 |
| `MIN_LEAD_DAYS` | 7 | 출발이 코앞인 일정은 제외 |

거기에 `scripts/blog_save.py` 가 **본문에 등장하는 금액을 자료와 대조**해서,
근거 없는 숫자가 하나라도 있으면 저장을 거부합니다. 주간 관측이 부족한데
"내렸다"고 쓰거나, 기후 자료가 없는데 날씨를 쓰는 것도 막습니다.

> 이 가드레일은 블로그 파이프라인 안에서만 동작합니다. 대시보드(`docs/index.html`)
> 에는 아직 하한선 검사가 없어서 755원 셀이 그대로 보입니다. 상류 수정은 별도 작업.

### 사진

여행 사진은 저작권 때문에 자동으로 넣지 않습니다. 초안에 `[[PHOTO: 설명]]` 자리만
잡아 두고, 임시저장된 글에 사용자가 직접 사진을 얹은 뒤 발행합니다. 가격·추이·
항공사 비교는 `blog/imagecard.py` 가 PNG 카드로 만들어 자동 삽입합니다.

## 우선순위 노선 (수집 다양성 조정)

`data/priority_routes.json`에 `["ICN-KHH", "ICN-HAN"]` 처럼 origin-destination 배열을 적어두면, 그 노선들은 연휴당 날짜쌍 후보 상한이 `PRIORITY_BOOST`(기본 2)배로 늘어나 같은 총 크롤 예산 안에서도 더 다양한 일정 후보(특히 덤휴일이 큰 앵커 후보)를 확보합니다. 하루짜리 연휴처럼 원래 후보가 1개뿐이던 노선도 이걸로 여러 개를 만들어, 휴일 가치/연차 상한 설정이 실제로 고를 게 생기게 됩니다.

- 실사용 인기도(즐겨찾기/조회수) 집계는 서버(Cloudflare Worker) 쪽 작업이 필요해 아직 없음 — 지금은 관리자가 직접 고르는 방식
- 파일이 없거나 비어 있으면 기존과 동일하게(부스트 없이) 동작
- `PRIORITY_BOOST` 환경변수로 배율 조정 가능 (`collect.yml`의 `env:`에 추가)

## 공항 데이터베이스 (`docs/airports.js`)

`docs/airports.js`의 공항 목록은 대시보드에서 국기 이모지·도시명 표시(`findAirport`)에 씁니다. 목록은 두 부분으로 구성됩니다.

- **상단 큐레이션 목록**: 한국인 여행자 기준 인기순으로 한글 도시/공항명을 손으로 관리 (검색 우선순위 상단)
- **`GENERATED_EXT` 블록**: 전 세계 정기 IATA 공항 전체를 자동 생성 (시모지시마 등 소규모·지방 공항 포함)

`GENERATED_EXT` 블록은 직접 수정하지 말고 생성 스크립트로 갱신합니다.

```bash
pip install airportsdata pycountry
python scripts/build_airports.py
```

큐레이션 목록에 새 공항을 예쁜 한글명으로 추가하고 싶으면 `airports.js` 상단(마커 위)에 `a(...)` 한 줄을 넣으면 됩니다. 같은 IATA가 큐레이션에 있으면 생성 스크립트가 자동으로 중복을 건너뜁니다.

## 로컬 테스트

```powershell
pip install -r requirements.txt
playwright install chromium
python scripts/collect.py
python scripts/build_dashboard_data.py
```

`docs/index.html`을 브라우저로 직접 열거나 `python -m http.server` 로 로컬 확인 가능.

블로그 쪽:

```bash
python tests/test_smoke.py                    # 신규 블로그 테스트 포함 전체
python scripts/blog_brief.py --stdout         # 소재 후보
python -m blog.imagecard --selftest           # 한글 폰트 확인 (없으면 카드가 □)
```

카드 렌더에는 한글 폰트가 필요합니다. 러너/컨테이너에 없으면
`apt-get install -y fonts-noto-cjk`. 크로미움이 이미 깔린 환경에서는
`BLOG_CHROMIUM_PATH` 로 실행 파일을 직접 지정할 수 있습니다.

## GitHub Pages 설정

저장소 Settings → Pages → Source를 `main` 브랜치의 `/docs` 폴더로 지정.

## 주의사항

- 구글 플라이트 화면 크롤링이므로 사이트 구조가 바뀌면 `collector/google_flights_crawler.py`의 파싱 로직을 갱신해야 함
- GitHub Actions 무료 크레딧은 public 저장소 기준 무제한이지만, 실제 실행 간격은 GitHub의 스케줄 지연으로 정확히 4시간이 아닐 수 있음
- 가격 이력은 `data/prices.csv`에 쌓이지만, 매 수집 직후 `scripts/prune_prices.py`가 계산에 안 쓰이는 90일 초과분(`PRUNE_RETENTION_DAYS`)을 자동으로 잘라내 무한정 커지지 않음
- **가격 하한선 검사가 크롤러·빌더에는 아직 없음.** `PRICE_PATTERN`이 덜 렌더된 페이지에서 `₩333` 같은 값을 잡아 `prices.csv`에 들어온 행이 15개 있고, 그게 `matrix.json`·`routes_status.json`까지 흘러가 대시보드에 "밀라노 왕복 755원"으로 보임. 블로그 파이프라인은 자체 필터(`blog/data.py`)로 막지만, 상류 수정은 별도 작업 필요
- 네이버 발행 자동화 코드를 이 저장소에 만들지 말 것. 실물 SmartEditor 로 검증된 도구가 이미 따로 있고, 비슷한 걸 하나 더 두면 둘 다 썩는다 (`publisher/README.md`)
